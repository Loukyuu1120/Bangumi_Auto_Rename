package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"bangumi_auto_rename/internal/api"
	"bangumi_auto_rename/internal/config"
	"bangumi_auto_rename/internal/logger"
	"bangumi_auto_rename/internal/monitor"
	"bangumi_auto_rename/internal/rename"
)

func main() {
	// ── Command-line flags (all have environment-variable fallbacks) ──────────
	dataDir := flag.String(
		"data",
		envOr("DATA_DIR", "/Bangumi_Auto_Rename/data"),
		"directory for config.json, task records, and log files",
	)
	staticDir := flag.String(
		"static",
		envOr("STATIC_DIR", "/app/static"),
		"directory containing the static web UI files (index.html, app.js, …)",
	)
	host := flag.String(
		"host",
		envOr("HOST", "0.0.0.0"),
		"interface address to listen on",
	)
	portStr := flag.String(
		"port",
		envOr("PORT", "5999"),
		"TCP port to listen on",
	)
	flag.Parse()

	// Parse port number
	port, err := strconv.Atoi(*portStr)
	if err != nil || port <= 0 || port > 65535 {
		fmt.Fprintf(os.Stderr, "invalid port %q: must be 1–65535\n", *portStr)
		os.Exit(1)
	}

	// ── Ensure the data directory exists ─────────────────────────────────────
	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "cannot create data directory %q: %v\n", *dataDir, err)
		os.Exit(1)
	}

	// ── Logger (initialise early so all subsequent code can log) ─────────────
	logDir := filepath.Join(*dataDir, "logs")
	if err := logger.Init(logDir, "INFO"); err != nil {
		fmt.Fprintf(os.Stderr, "failed to initialise logger: %v\n", err)
		os.Exit(1)
	}
	log := logger.Get()
	defer log.Close()

	logPath := filepath.Join(logDir, "BAR.log")

	log.Info("============================================")
	log.Info("  番剧自动重命名 (Go 版) 启动中…")
	log.Info("  数据目录 : %s", *dataDir)
	log.Info("  静态目录 : %s", *staticDir)
	log.Info("============================================")

	// ── Configuration ─────────────────────────────────────────────────────────
	cfg, err := config.Init(*dataDir)
	if err != nil {
		log.Error("配置初始化失败: %v", err)
		os.Exit(1)
	}

	// Apply the configured log level now that we have it
	cfgSnap := cfg.GetConfig()
	log.SetLevel(logger.ParseLevel(cfgSnap.LogLevel))
	log.Info("日志级别: %s", cfgSnap.LogLevel)

	// ── Task store ────────────────────────────────────────────────────────────
	store, err := rename.NewStore(*dataDir)
	if err != nil {
		log.Error("任务存储初始化失败: %v", err)
		os.Exit(1)
	}
	log.Info("任务存储已加载，共 %d 条记录", store.Count())

	// ── Rename processor ──────────────────────────────────────────────────────
	processor := rename.NewProcessor(cfg)

	// ── Monitor service (starts background worker & stability-checker goroutines) ──
	svc := monitor.InitService(processor, store, *dataDir)

	// Start file-system watchers if the feature is enabled in config
	if cfgSnap.MonitorEnabled {
		rawPaths := cfg.GetMonitorPaths()
		excludeDirs := cfg.GetMonitorExcludeDirs()

		watchPaths := make([]monitor.PathConfig, 0, len(rawPaths))
		for _, p := range rawPaths {
			watchPaths = append(watchPaths, monitor.PathConfig{
				Path:   p.Path,
				Extras: p.Extras,
			})
		}

		if err := svc.StartWatchers(watchPaths, excludeDirs); err != nil {
			log.Warn("文件监控启动失败: %v", err)
		} else {
			log.Info("文件监控已启动，监控目录数: %d", len(watchPaths))
		}
	} else {
		log.Info("文件监控未启用（可在设置页面中开启）")
	}

	// Restore any queue that was persisted before the last shutdown
	if svc.HasSavedQueue() {
		log.Info("检测到已保存的队列，正在恢复…")
		if err := svc.LoadQueue(); err != nil {
			log.Warn("队列恢复失败: %v", err)
		}
	}

	// ── HTTP server ───────────────────────────────────────────────────────────
	mux := http.NewServeMux()
	api.New(mux, cfg, store, svc, processor, *dataDir, logPath, *staticDir)

	addr := fmt.Sprintf("%s:%d", *host, port)
	server := &http.Server{
		Addr:    addr,
		Handler: mux,
		// Generous timeouts because TMDB / AI calls can be slow
		ReadTimeout:       60 * time.Second,
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      120 * time.Second,
		IdleTimeout:       240 * time.Second,
	}

	// Start listening in the background
	serverErr := make(chan error, 1)
	go func() {
		log.Info("Web UI 已就绪: http://%s", addr)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			serverErr <- err
		}
	}()

	// ── Wait for shutdown signal or fatal server error ─────────────────────
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	select {
	case sig := <-quit:
		log.Info("收到信号 %s，开始优雅退出…", sig)
	case err := <-serverErr:
		log.Error("HTTP 服务器意外退出: %v", err)
	}

	// Persist the in-memory queue so tasks survive a restart
	log.Info("正在保存队列状态…")
	if err := svc.SaveQueue(); err != nil {
		log.Warn("队列保存失败: %v", err)
	}

	// Give in-flight HTTP requests up to 30 s to complete
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutdownCancel()

	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Error("HTTP 服务器关闭超时: %v", err)
	}

	svc.Stop()
	log.Info("服务已安全关闭，再见！")
}

// envOr returns the value of the named environment variable, or fallback when unset / empty.
func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
