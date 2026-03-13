package monitor

import (
	"context"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/google/uuid"

	"bangumi_auto_rename/internal/logger"
	"bangumi_auto_rename/internal/rename"
)

const (
	PrioritySystem = 1
	PriorityManual = 10
)

// extractTMDBIDFromPath extracts TMDB ID from file path using the same logic as in process.go
func extractTMDBIDFromPath(path string) string {
	if id := rename.ExtractTMDBID(path); id != "" {
		return id
	}
	parts := strings.Split(path, string(filepath.Separator))
	for _, p := range parts {
		if id := rename.ExtractTMDBID(p); id != "" {
			return id
		}
	}
	return ""
}

// pollInterval is the interval between directory scans in compatibility (polling) mode.
// Override with the BANGUMI_POLL_INTERVAL environment variable (e.g. "5s", "10s", "30s").
// Values below 1 s are ignored. Default: 5 s.
var pollInterval = func() time.Duration {
	if v := os.Getenv("BANGUMI_POLL_INTERVAL"); v != "" {
		if d, err := time.ParseDuration(v); err == nil && d >= time.Second {
			return d
		}
	}
	return 5 * time.Second
}()

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

// TaskOptions mirrors rename.TaskOptions but is JSON-serialisable so the queue
// can be persisted to disk between restarts.
type TaskOptions struct {
	IsAnime         *bool                  `json:"is_anime,omitempty"`
	IsMovie         *bool                  `json:"is_movie,omitempty"`
	UseAI           bool                   `json:"use_ai"`
	CusName         string                 `json:"cus_name,omitempty"`
	CusSeasonID     *int                   `json:"cus_season_id,omitempty"`
	CusTMDBID       string                 `json:"cus_tmdb_id,omitempty"`
	CusOffset       int                    `json:"cus_offset"`
	ConfigOverrides map[string]interface{} `json:"config_overrides,omitempty"`
}

// toRenameOpts converts the monitor TaskOptions to rename.TaskOptions.
func (o TaskOptions) toRenameOpts() rename.TaskOptions {
	return rename.TaskOptions{
		IsAnime:         o.IsAnime,
		IsMovie:         o.IsMovie,
		UseAI:           o.UseAI,
		CusName:         o.CusName,
		CusSeasonID:     o.CusSeasonID,
		CusTMDBID:       o.CusTMDBID,
		CusOffset:       o.CusOffset,
		ConfigOverrides: o.ConfigOverrides,
	}
}

// QueueItem is a single pending rename job.
type QueueItem struct {
	UUID     string      `json:"uuid"`
	Path     string      `json:"path"`
	Options  TaskOptions `json:"options"`
	AddedAt  time.Time   `json:"added_at"`
	Priority int         `json:"priority"`
	Seq      uint64      `json:"seq"`
}

// PathConfig describes a single monitored directory and its per-path overrides.
type PathConfig struct {
	Path   string                 `json:"path"`
	Extras map[string]interface{} `json:"extras,omitempty"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Service
// ─────────────────────────────────────────────────────────────────────────────

// Service is a singleton that
//   - watches configured directories for new video files,
//   - accepts manually-submitted tasks, and
//   - processes them in a background worker goroutine.
type Service struct {
	mu sync.Mutex

	processor *rename.Processor
	store     *rename.Store
	log       *logger.Logger
	dataDir   string

	// pending work queue (protected by mu)
	queue []QueueItem

	// fsnotify watcher (nil when monitoring is disabled / in polling mode)
	watcher     *fsnotify.Watcher
	watchedDirs []PathConfig
	excludeDirs []string
	// compiled exclude regex patterns (case-insensitive)
	excludePatterns []*regexp.Regexp

	// pollCancel cancels the pollLoop goroutine (compatibility mode)
	pollCancel context.CancelFunc

	// channel to feed items to the worker
	workCh chan struct{}

	// pause / resume
	paused bool

	// logical pending count (mirrors Python monitor_service.logical_pending_count)
	logicalPendingCount int
	countMu             sync.Mutex

	seq uint64

	ctx    context.Context
	cancel context.CancelFunc
}

var (
	globalService *Service
	globalOnce    sync.Once
)

// GetService returns the process-wide singleton Service.
// Call InitService before using.
func GetService() *Service {
	return globalService
}

// InitService creates and starts the global Service singleton.
func InitService(processor *rename.Processor, store *rename.Store, dataDir string) *Service {
	globalOnce.Do(func() {
		ctx, cancel := context.WithCancel(context.Background())
		svc := &Service{
			processor: processor,
			store:     store,
			log:       logger.Get(),
			dataDir:   dataDir,
			workCh:    make(chan struct{}, 512),
			ctx:       ctx,
			cancel:    cancel,
		}
		globalService = svc
		go svc.worker()
	})
	return globalService
}

// ─────────────────────────────────────────────────────────────────────────────
// Start / Stop watchers
// ─────────────────────────────────────────────────────────────────────────────

// StartWatchers starts file watchers for the given path configs.
// mode can be "compatibility" (polling, like Python's PollingObserver) or
// "native" (fsnotify, OS-level events).  It stops any previously running
// watchers first.  If mode is omitted it defaults to "compatibility".
func (s *Service) StartWatchers(paths []PathConfig, excludeDirs []string, mode ...string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Stop existing fsnotify watcher
	if s.watcher != nil {
		_ = s.watcher.Close()
		s.watcher = nil
	}

	// Cancel any running poll loop
	if s.pollCancel != nil {
		s.pollCancel()
		s.pollCancel = nil
	}

	s.watchedDirs = paths
	s.excludeDirs = excludeDirs
	s.excludePatterns = compileExcludePatterns(excludeDirs, s.log)

	if len(paths) == 0 {
		s.log.Info("[监控] 无监控目录配置，跳过启动")
		return nil
	}

	// Determine monitoring mode (mirror Python behavior)
	watchMode := "compatibility"
	if len(mode) > 0 && mode[0] != "" {
		watchMode = strings.ToLower(strings.TrimSpace(mode[0]))
	}

	usePolling := watchMode == "compatibility"
	if !usePolling && runtime.GOOS != "linux" {
		s.log.Info("[监控] 当前系统不支持递归原生监听，回退到轮询模式")
		usePolling = true
	}
	if !usePolling && runtime.GOOS == "linux" {
		limit := getInotifyLimit()
		s.log.Info("[监控] 当前 max_user_watches: %d", limit)
		totalFiles := 0
		for _, pc := range paths {
			totalFiles += countDirectoryFiles(pc.Path, 10000)
		}
		if totalFiles > int(float64(limit)*0.8) {
			s.log.Warn("[监控] 文件数量(%d) 接近系统限制(%d)，强制使用轮询模式", totalFiles, limit)
			usePolling = true
		}
	}

	if usePolling {
		s.log.Info("[监控] 使用兼容模式 (轮询)")
		pollCtx, pollCancel := context.WithCancel(s.ctx)
		s.pollCancel = pollCancel
		go s.pollLoop(pollCtx, paths)
		s.log.Info("[监控] 服务已启动，模式: [兼容模式(轮询)]，监控 %d 个目录", len(paths))
		return nil
	}

	if err := s.startNativeWatchers(paths); err != nil {
		s.log.Warn("[监控] 原生模式启动失败，回退到轮询: %v", err)
		pollCtx, pollCancel := context.WithCancel(s.ctx)
		s.pollCancel = pollCancel
		go s.pollLoop(pollCtx, paths)
	}
	return nil
}

// startNativeWatchers starts fsnotify-based watchers (native mode).
func (s *Service) startNativeWatchers(paths []PathConfig) error {
	w, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}

	monitoredCount := 0
	for _, pc := range paths {
		if _, err := os.Stat(pc.Path); err != nil {
			s.log.Warn("[监控] 路径不存在，跳过: %s", pc.Path)
			continue
		}
		if runtime.GOOS == "linux" {
			_ = filepath.WalkDir(pc.Path, func(path string, d fs.DirEntry, walkErr error) error {
				if walkErr != nil {
					return nil
				}
				if !d.IsDir() {
					return nil
				}
				if s.shouldExclude(path) {
					return filepath.SkipDir
				}
				if addErr := w.Add(path); addErr != nil {
					s.log.Warn("[监控] 添加目录失败 %s: %v", path, addErr)
					return nil
				}
				monitoredCount++
				return nil
			})
			s.log.Info("[监控] 已添加监控目录: %s", pc.Path)
		} else {
			if err := w.Add(pc.Path); err != nil {
				s.log.Warn("[监控] 添加监控失败 %s: %v", pc.Path, err)
				continue
			}
			monitoredCount++
			s.log.Info("[监控] 已添加监控目录: %s", pc.Path)
		}
	}

	if monitoredCount == 0 {
		_ = w.Close()
		s.log.Warn("[监控] 没有有效的监控目录，服务未启动监听")
		return nil
	}

	s.watcher = w
	go s.watchLoop(w, paths)
	s.log.Info("[监控] 服务已启动，模式: [高效模式(原生)]，监控 %d 个目录", monitoredCount)
	return nil
}

// Stop shuts down the service worker and any active watchers.
func (s *Service) Stop() {
	if err := s.SaveQueue(); err != nil {
		s.log.Warn("[监控] 自动保存队列失败: %v", err)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if s.watcher != nil {
		_ = s.watcher.Close()
		s.watcher = nil
	}
	if s.pollCancel != nil {
		s.pollCancel()
		s.pollCancel = nil
	}
	s.cancel()
	s.log.Info("[监控] 服务已停止")
}

// ─────────────────────────────────────────────────────────────────────────────
// Pause / resume
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) Pause() {
	s.mu.Lock()
	s.paused = true
	s.mu.Unlock()
	s.log.Info("[监控] 已暂停处理")
}

func (s *Service) Resume() {
	s.mu.Lock()
	s.paused = false
	s.mu.Unlock()
	s.log.Info("[监控] 已恢复处理")
}

func (s *Service) IsPaused() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.paused
}

// ─────────────────────────────────────────────────────────────────────────────
// Manual task submission
// ─────────────────────────────────────────────────────────────────────────────

// AddTask enqueues a single file for processing with the provided options.
// Returns the UUID assigned to the task.
func (s *Service) AddTask(path string, opts TaskOptions) string {
	return s.AddTaskWithPriority(path, opts, PriorityManual, false)
}

// AddTaskWithID enqueues a task using a caller-supplied UUID so retries/edits
// can reuse the existing task record instead of creating duplicates.
func (s *Service) AddTaskWithID(id, path string, opts TaskOptions, priority int, incrementCounter bool) string {
	if id == "" {
		id = uuid.New().String()
	}
	item := QueueItem{
		UUID:     id,
		Path:     path,
		Options:  opts,
		AddedAt:  time.Now(),
		Priority: priority,
		Seq:      atomic.AddUint64(&s.seq, 1),
	}

	s.enqueue(item, incrementCounter)

	// Non-blocking wake-up
	select {
	case s.workCh <- struct{}{}:
	default:
	}

	s.log.Info("[任务] 已加入队列: %s", path)
	return id
}

// AddTaskWithPriority enqueues a task. When priority is true, it is inserted
// at the front of the queue.
func (s *Service) AddTaskWithPriority(path string, opts TaskOptions, priority int, incrementCounter bool) string {
	return s.AddTaskWithID("", path, opts, priority, incrementCounter)
}

// RegisterBatchCount sets the expected total count for the current batch
// (used for progress display in the UI).
func (s *Service) RegisterBatchCount(n int) {
	s.countMu.Lock()
	s.logicalPendingCount += n
	s.countMu.Unlock()
	s.log.Info("[计数器] 批量注册任务: +%d, 当前待处理: %d", n, s.logicalPendingCount)
}

// ─────────────────────────────────────────────────────────────────────────────
// Queue inspection
// ─────────────────────────────────────────────────────────────────────────────

// QueueLength returns the number of items currently waiting in the queue.
func (s *Service) QueueLength() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.queue)
}

// QueueSnapshot returns a copy of the current queue.
func (s *Service) QueueList() []QueueItem {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]QueueItem, len(s.queue))
	copy(out, s.queue)
	sort.Slice(out, func(i, j int) bool {
		if out[i].Priority != out[j].Priority {
			return out[i].Priority < out[j].Priority
		}
		return out[i].Seq < out[j].Seq
	})
	return out
}

// ClearQueue discards all pending (unprocessed) queue items.
func (s *Service) ClearQueue() {
	s.mu.Lock()
	s.queue = s.queue[:0]
	s.mu.Unlock()
	s.countMu.Lock()
	s.logicalPendingCount = 0
	s.countMu.Unlock()
	s.log.Info("[任务] 队列已清空")
}

func (s *Service) enqueue(item QueueItem, incrementCounter bool) {
	s.mu.Lock()
	s.queue = append(s.queue, item)
	s.mu.Unlock()

	if incrementCounter {
		s.countMu.Lock()
		s.logicalPendingCount++
		s.countMu.Unlock()
	}
}

func (s *Service) decrementLogicalCount() {
	s.countMu.Lock()
	if s.logicalPendingCount > 0 {
		s.logicalPendingCount--
	}
	s.countMu.Unlock()
}

// ─────────────────────────────────────────────────────────────────────────────
// Queue persistence
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) queueFilePath() string {
	return filepath.Join(s.dataDir, "task", "saved_queue.json")
}

// SaveQueue persists the current queue to disk so it survives a restart.
func (s *Service) SaveQueue() error {
	s.mu.Lock()
	if len(s.queue) == 0 {
		s.mu.Unlock()
		return nil
	}
	s.paused = true
	items := make([]QueueItem, len(s.queue))
	copy(items, s.queue)
	s.queue = s.queue[:0]
	s.mu.Unlock()

	entries := make([]map[string]interface{}, 0, len(items))
	for _, item := range items {
		entry := map[string]interface{}{
			"path":     item.Path,
			"priority": item.Priority,
			"options":  taskOptionsToMap(item.Options),
		}
		entries = append(entries, entry)
	}

	data, err := json.MarshalIndent(entries, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.queueFilePath()), 0o755); err != nil {
		return err
	}
	tmp := s.queueFilePath() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, s.queueFilePath()); err != nil {
		return err
	}

	s.countMu.Lock()
	s.logicalPendingCount = 0
	s.countMu.Unlock()

	s.log.Info("[任务保存] 已将 %d 个任务保存到磁盘", len(items))
	return nil
}

// LoadQueue reads a previously persisted queue from disk and re-enqueues items.
// The queue file is deleted after loading.
func (s *Service) LoadQueue() error {
	data, err := os.ReadFile(s.queueFilePath())
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	var entries []map[string]interface{}
	if err := json.Unmarshal(data, &entries); err != nil {
		return err
	}

	countLoaded := 0
	for _, entry := range entries {
		path, _ := entry["path"].(string)
		if path == "" {
			continue
		}
		if _, statErr := os.Stat(path); statErr != nil {
			continue
		}
		priority := PriorityManual
		if p, ok := entry["priority"].(float64); ok {
			priority = int(p)
		}
		opts := TaskOptions{}
		if raw, ok := entry["options"].(map[string]interface{}); ok {
			opts = taskOptionsFromMap(raw)
		}

		item := QueueItem{
			UUID:     uuid.New().String(),
			Path:     path,
			Options:  opts,
			AddedAt:  time.Now(),
			Priority: priority,
			Seq:      atomic.AddUint64(&s.seq, 1),
		}
		s.enqueue(item, false)
		countLoaded++
	}

	_ = os.Remove(s.queueFilePath())
	s.countMu.Lock()
	s.logicalPendingCount += countLoaded
	s.countMu.Unlock()
	s.log.Info("[任务恢复] 成功恢复 %d 个任务", countLoaded)
	return nil
}

// HasSavedQueue reports whether a persisted queue file exists.
func (s *Service) HasSavedQueue() bool {
	_, err := os.Stat(s.queueFilePath())
	return err == nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Polling-based watcher (compatibility mode)
//
// Mirrors Python's PollingObserver: periodically walks all monitored
// directories at unlimited depth, detects new video files, and enqueues them
// with system priority (matching Python's PollingObserver behavior).
// ─────────────────────────────────────────────────────────────────────────────

// pollLoop periodically scans all monitored directories for new video files.
// It maintains a snapshot of known files and only triggers processing for
// files that appear after the initial scan (same behaviour as Python).
func (s *Service) pollLoop(ctx context.Context, paths []PathConfig) {
	// Build path→config lookup
	pathMap := make(map[string]PathConfig, len(paths))
	for _, pc := range paths {
		pathMap[filepath.Clean(pc.Path)] = pc
	}

	// Initial snapshot: record all existing video files so they are not
	// treated as new when the service starts.
	knownFiles := make(map[string]int64) // path → size
	for _, pc := range paths {
		s.scanDir(pc.Path, knownFiles)
		s.log.Info("[监控] 轮询初始化完成: %s (已知文件 %d 个)", pc.Path, len(knownFiles))
	}

	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			currentFiles := make(map[string]int64)
			for _, pc := range paths {
				s.scanDir(pc.Path, currentFiles)
			}

			// Detect new files (present in current scan but not in previous snapshot)
			for filePath, size := range currentFiles {
				if _, existed := knownFiles[filePath]; !existed {
					// New file discovered — determine per-path options
					opts := TaskOptions{}
					cleanName := filepath.Clean(filePath)
					for watchedPath, pc := range pathMap {
						if strings.HasPrefix(cleanName, watchedPath) {
							opts = buildTaskOptions(pc.Extras)
							break
						}
					}

					// Extract TMDB ID from path if present
					if tmdbID := extractTMDBIDFromPath(filePath); tmdbID != "" {
						opts.CusTMDBID = tmdbID
					}
					_ = size
					s.AddTaskWithPriority(filePath, opts, PrioritySystem, true)
					s.log.Info("[监控] 轮询发现新文件: %s -> 加入队列 (系统优先)", filepath.Base(filePath))
				}
			}

			// Update snapshot for next iteration
			knownFiles = currentFiles
		}
	}
}

// scanDir walks dir recursively and adds all non-excluded video files to out.
func (s *Service) scanDir(dir string, out map[string]int64) {
	_ = filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // skip unreadable entries
		}
		if d.IsDir() {
			if s.shouldExclude(path) {
				return filepath.SkipDir
			}
			return nil
		}
		if !rename.IsVideoFile(path) {
			return nil
		}
		if s.shouldExclude(path) {
			return nil
		}
		info, infoErr := d.Info()
		if infoErr != nil {
			return nil
		}
		out[path] = info.Size()
		return nil
	})
}

// ─────────────────────────────────────────────────────────────────────────────
// fsnotify event loop (native mode)
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) watchLoop(w *fsnotify.Watcher, paths []PathConfig) {
	// Build a path→config lookup for quick access
	pathMap := make(map[string]PathConfig, len(paths))
	for _, pc := range paths {
		pathMap[filepath.Clean(pc.Path)] = pc
	}

	for {
		select {
		case <-s.ctx.Done():
			return

		case event, ok := <-w.Events:
			if !ok {
				return
			}
			if event.Op&(fsnotify.Create|fsnotify.Rename) == 0 {
				continue
			}

			// If a new directory was created, add it and its sub-tree to the
			// watcher so that files placed inside it are also detected.
			if info, statErr := os.Stat(event.Name); statErr == nil && info.IsDir() {
				if !s.shouldExclude(event.Name) {
					_ = filepath.WalkDir(event.Name, func(path string, d fs.DirEntry, err error) error {
						if err != nil {
							return nil
						}
						if !d.IsDir() {
							return nil
						}
						if s.shouldExclude(path) {
							return filepath.SkipDir
						}
						if addErr := w.Add(path); addErr != nil {
							s.log.Warn("[监控] 动态添加目录失败 %s: %v", path, addErr)
						}
						return nil
					})
					s.log.Debug("[监控] 动态监控新目录: %s", event.Name)
				}
				continue
			}

			if !rename.IsVideoFile(event.Name) {
				continue
			}
			if s.shouldExclude(event.Name) {
				continue
			}

			// Determine which path config this file belongs to and build opts.
			opts := TaskOptions{}
			cleanName := filepath.Clean(event.Name)
			for watchedPath, pc := range pathMap {
				if strings.HasPrefix(cleanName, watchedPath) {
					opts = buildTaskOptions(pc.Extras)
					break
				}
			}
			// Extract TMDB ID from path if present
			if tmdbID := extractTMDBIDFromPath(event.Name); tmdbID != "" {
				opts.CusTMDBID = tmdbID
			}

			s.AddTaskWithPriority(event.Name, opts, PrioritySystem, true)
			s.log.Info("[监控] 捕获变更: %s -> 加入队列 (系统优先)", filepath.Base(event.Name))

		case err, ok := <-w.Errors:
			if !ok {
				return
			}
			s.log.Warn("[监控] watcher 错误: %v", err)
		}
	}
}

func buildTaskOptions(extras map[string]interface{}) TaskOptions {
	var opts TaskOptions
	if extras == nil {
		return opts
	}
	overrides := map[string]interface{}{}
	for k, v := range extras {
		switch k {
		case "path", "scan_now":
			continue
		case "is_anime":
			if b, ok := v.(bool); ok {
				opts.IsAnime = &b
			} else if s, ok := v.(string); ok {
				b := s == "true" || s == "1"
				opts.IsAnime = &b
			}
		case "is_movie":
			if b, ok := v.(bool); ok {
				opts.IsMovie = &b
			} else if s, ok := v.(string); ok {
				b := s == "true" || s == "1"
				opts.IsMovie = &b
			}
		default:
			if v == nil {
				continue
			}
			if s, ok := v.(string); ok && strings.TrimSpace(s) == "" {
				continue
			}
			overrides[k] = v
		}
	}
	if len(overrides) > 0 {
		opts.ConfigOverrides = overrides
	}
	return opts
}

// ScanNow walks the given paths and enqueues all video files for processing.
// It is used by the config "scan_now" action to mimic the Python behavior.
func (s *Service) ScanNow(paths []PathConfig, excludeDirs []string) int {
	count := 0
	s.UpdateExcludeDirs(excludeDirs)

	for _, pc := range paths {
		root := filepath.Clean(pc.Path)
		if root == "" {
			continue
		}
		info, err := os.Stat(root)
		if err != nil || !info.IsDir() {
			s.log.Warn("[全量扫描] 目录不存在，跳过: %s", root)
			continue
		}

		s.log.Info("[全量扫描] 开始扫描: %s", root)
		opts := buildTaskOptions(pc.Extras)
		_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if d.IsDir() {
				if s.shouldExclude(path) {
					return filepath.SkipDir
				}
				return nil
			}
			if s.shouldExclude(path) {
				return nil
			}
			if !rename.IsVideoFile(path) {
				return nil
			}
			// Extract TMDB ID from path if present
			if tmdbID := extractTMDBIDFromPath(path); tmdbID != "" {
				opts.CusTMDBID = tmdbID
			}
			s.AddTask(path, opts)
			count++
			return nil
		})
	}

	if count > 0 {
		s.log.Info("[全量扫描] 扫描完成，已添加 %d 个任务", count)
	} else {
		s.log.Info("[全量扫描] 扫描完成，未发现新任务")
	}

	return count
}

// shouldExclude reports whether a path matches any of the configured exclude patterns.
func (s *Service) shouldExclude(path string) bool {
	base := filepath.Base(path)
	if strings.HasPrefix(base, ".") {
		return true
	}
	s.mu.Lock()
	patterns := append([]*regexp.Regexp{}, s.excludePatterns...)
	s.mu.Unlock()
	for _, re := range patterns {
		if re.MatchString(path) {
			return true
		}
	}
	return false
}

// UpdateExcludeDirs updates the in-memory exclude list without restarting watchers.
func (s *Service) UpdateExcludeDirs(exclude []string) {
	s.mu.Lock()
	s.excludeDirs = exclude
	s.excludePatterns = compileExcludePatterns(exclude, s.log)
	s.mu.Unlock()
	s.log.Info("[监控] 排除目录已更新 (%d 条)", len(exclude))
}

func compileExcludePatterns(exclude []string, log *logger.Logger) []*regexp.Regexp {
	patterns := make([]*regexp.Regexp, 0, len(exclude))
	for _, patternStr := range exclude {
		if strings.TrimSpace(patternStr) == "" {
			continue
		}
		re, err := regexp.Compile("(?i)" + patternStr)
		if err != nil {
			log.Error("[监控] 排除规则 '%s' 无效: %v", patternStr, err)
			continue
		}
		patterns = append(patterns, re)
	}
	return patterns
}

func countDirectoryFiles(root string, maxCheck int) int {
	count := 0
	_ = filepath.WalkDir(root, func(_ string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if !d.IsDir() {
			count++
			if count > maxCheck {
				return filepath.SkipDir
			}
		}
		return nil
	})
	return count
}

func getInotifyLimit() int {
	data, err := os.ReadFile("/proc/sys/fs/inotify/max_user_watches")
	if err != nil {
		return 8192
	}
	v, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return 8192
	}
	return v
}

func taskOptionsToMap(opts TaskOptions) map[string]interface{} {
	out := map[string]interface{}{}
	if opts.IsAnime != nil {
		out["is_anime"] = *opts.IsAnime
	}
	if opts.IsMovie != nil {
		out["is_movie"] = *opts.IsMovie
	}
	if opts.UseAI {
		out["use_ai"] = opts.UseAI
	}
	if opts.CusName != "" {
		out["cus_name"] = opts.CusName
	}
	if opts.CusSeasonID != nil {
		out["cus_season_id"] = *opts.CusSeasonID
	}
	if opts.CusTMDBID != "" {
		out["cus_tmdb_id"] = opts.CusTMDBID
	}
	if opts.CusOffset != 0 {
		out["cus_offset"] = opts.CusOffset
	}
	if len(opts.ConfigOverrides) > 0 {
		out["config_overrides"] = opts.ConfigOverrides
	}
	return out
}

func taskOptionsFromMap(m map[string]interface{}) TaskOptions {
	var opts TaskOptions
	if m == nil {
		return opts
	}
	if v, ok := m["is_anime"]; ok {
		if b, ok := v.(bool); ok {
			opts.IsAnime = &b
		}
	}
	if v, ok := m["is_movie"]; ok {
		if b, ok := v.(bool); ok {
			opts.IsMovie = &b
		}
	}
	if v, ok := m["use_ai"]; ok {
		if b, ok := v.(bool); ok {
			opts.UseAI = b
		}
	}
	if v, ok := m["cus_name"]; ok {
		if s, ok := v.(string); ok {
			opts.CusName = s
		}
	}
	if v, ok := m["cus_tmdb_id"]; ok {
		if s, ok := v.(string); ok {
			opts.CusTMDBID = s
		}
	}
	if v, ok := m["cus_offset"]; ok {
		if n, ok := v.(float64); ok {
			opts.CusOffset = int(n)
		}
	}
	if v, ok := m["cus_season_id"]; ok {
		if n, ok := v.(float64); ok {
			i := int(n)
			opts.CusSeasonID = &i
		}
	}
	if v, ok := m["config_overrides"]; ok {
		if mm, ok := v.(map[string]interface{}); ok {
			opts.ConfigOverrides = mm
		}
	}
	return opts
}

func (s *Service) waitForFileReady(path string, timeout time.Duration, interval time.Duration) bool {
	if _, err := os.Stat(path); err != nil {
		return false
	}
	start := time.Now()
	lastSize := int64(-1)
	for time.Since(start) < timeout {
		select {
		case <-s.ctx.Done():
			return false
		default:
		}
		info, err := os.Stat(path)
		if err != nil {
			time.Sleep(interval)
			continue
		}
		size := info.Size()
		if size > 0 && size == lastSize {
			return true
		}
		lastSize = size
		time.Sleep(interval)
	}
	s.log.Warn("[等待超时] 尝试强制处理: %s", filepath.Base(path))
	return true
}

func (s *Service) resolvePathExtras(filePath string) map[string]interface{} {
	s.mu.Lock()
	paths := make([]PathConfig, len(s.watchedDirs))
	copy(paths, s.watchedDirs)
	s.mu.Unlock()

	cleanFile := filepath.Clean(filePath)
	bestLen := -1
	var best map[string]interface{}
	for _, pc := range paths {
		root := filepath.Clean(pc.Path)
		if root == "" {
			continue
		}
		if strings.HasPrefix(cleanFile, root) {
			if len(root) > bestLen {
				bestLen = len(root)
				best = pc.Extras
			}
		}
	}
	if best == nil {
		return map[string]interface{}{}
	}
	return best
}

// ─────────────────────────────────────────────────────────────────────────────
// Background worker
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) worker() {
	for {
		select {
		case <-s.ctx.Done():
			return

		case <-s.workCh:
			for {
				item, ok := s.popNext()
				if !ok {
					break
				}
				// Respect pause state – busy-wait with backoff
				for s.IsPaused() {
					select {
					case <-s.ctx.Done():
						return
					case <-time.After(2 * time.Second):
					}
				}
				if !s.waitForFileReady(item.Path, 10*time.Second, 1*time.Second) {
					s.log.Warn("[跳过] 文件无法读取或已消失: %s", item.Path)
					s.decrementLogicalCount()
					continue
				}
				s.processItem(item)
			}
		}
	}
}

func (s *Service) popNext() (QueueItem, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.queue) == 0 {
		return QueueItem{}, false
	}
	bestIdx := 0
	best := s.queue[0]
	for i := 1; i < len(s.queue); i++ {
		cur := s.queue[i]
		if cur.Priority < best.Priority || (cur.Priority == best.Priority && cur.Seq < best.Seq) {
			best = cur
			bestIdx = i
		}
	}
	item := best
	s.queue = append(s.queue[:bestIdx], s.queue[bestIdx+1:]...)
	return item, true
}

func (s *Service) processItem(item QueueItem) {
	if s.shouldExclude(item.Path) {
		s.log.Info("[任务] 已忽略（排除目录命中）[%s]: %s", item.UUID, item.Path)
		rec := &rename.TaskRecord{
			UUID:        item.UUID,
			Path:        item.Path,
			Status:      rename.StatusIgnored,
			ErrMsg:      "排除目录命中",
			IsAnime:     item.Options.IsAnime,
			IsMovie:     item.Options.IsMovie,
			UseAI:       item.Options.UseAI,
			Offset:      item.Options.CusOffset,
			TMDBID:      item.Options.CusTMDBID,
			ProcessedAt: time.Now().Format("2006-01-02 15:04:05"),
		}
		if item.Options.CusSeasonID != nil {
			rec.SeasonID = item.Options.CusSeasonID
		}
		if err := s.store.SaveTask(rec); err != nil {
			s.log.Error("[任务] 保存忽略记录失败 [%s]: %v", item.UUID, err)
		}
		s.decrementLogicalCount()
		return
	}
	opts := item.Options
	customConfig := s.resolvePathExtras(item.Path)
	if opts.IsAnime == nil {
		if v, ok := customConfig["is_anime"]; ok {
			if b, ok := v.(bool); ok {
				opts.IsAnime = &b
			}
		}
	}
	if opts.IsMovie == nil {
		if v, ok := customConfig["is_movie"]; ok {
			if b, ok := v.(bool); ok {
				opts.IsMovie = &b
			}
		}
	}
	finalOverrides := map[string]interface{}{}
	for k, v := range customConfig {
		if k == "path" || k == "scan_now" || k == "is_anime" || k == "is_movie" {
			continue
		}
		if v == nil {
			continue
		}
		if s, ok := v.(string); ok && strings.TrimSpace(s) == "" {
			continue
		}
		finalOverrides[k] = v
	}
	for k, v := range opts.ConfigOverrides {
		if v == nil {
			continue
		}
		if s, ok := v.(string); ok && strings.TrimSpace(s) == "" {
			continue
		}
		finalOverrides[k] = v
	}
	if len(finalOverrides) > 0 {
		opts.ConfigOverrides = finalOverrides
	}

	s.log.Info("[任务] 开始处理 [%s]: %s", item.UUID, item.Path)

	rec := s.processor.Process(item.Path, opts.toRenameOpts(), item.UUID)

	if err := s.store.SaveTask(rec); err != nil {
		s.log.Error("[任务] 保存任务记录失败 [%s]: %v", item.UUID, err)
	}

	if rec.Status == rename.StatusSuccess {
		// Build source→target map from TargetPaths + original source
		fileMap := map[string]string{item.Path: ""}
		if len(rec.TargetPaths) > 0 {
			fileMap[item.Path] = rec.TargetPaths[0]
		}
		if err := s.store.SaveRecord(item.UUID, fileMap); err != nil {
			s.log.Error("[任务] 保存文件记录失败 [%s]: %v", item.UUID, err)
		}
		s.log.Info("[任务] 完成 [%s]", item.UUID)
	} else {
		s.log.Error("[任务] 失败 [%s]: %s", item.UUID, rec.ErrMsg)
	}

	s.decrementLogicalCount()
}
