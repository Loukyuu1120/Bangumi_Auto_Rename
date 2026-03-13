package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"bangumi_auto_rename/internal/ai"
	"bangumi_auto_rename/internal/config"
	"bangumi_auto_rename/internal/logger"
	"bangumi_auto_rename/internal/monitor"
	"bangumi_auto_rename/internal/rename"
)

// ─────────────────────────────────────────────────────────────────────────────
// Handler wires up all HTTP routes.
// ─────────────────────────────────────────────────────────────────────────────

// Handler holds shared dependencies for all route handlers.
type Handler struct {
	cfg       *config.Manager
	store     *rename.Store
	svc       *monitor.Service
	processor *rename.Processor
	log       *logger.Logger
	dataDir   string
	logPath   string
	staticDir string
}

// New creates a Handler and registers all routes on mux.
func New(
	mux *http.ServeMux,
	cfg *config.Manager,
	store *rename.Store,
	svc *monitor.Service,
	processor *rename.Processor,
	dataDir string,
	logPath string,
	staticDir string,
) *Handler {
	h := &Handler{
		cfg:       cfg,
		store:     store,
		svc:       svc,
		processor: processor,
		log:       logger.Get(),
		dataDir:   dataDir,
		logPath:   logPath,
		staticDir: staticDir,
	}
	h.registerRoutes(mux)
	return h
}

func (h *Handler) registerRoutes(mux *http.ServeMux) {
	// Serve the single-page application
	mux.Handle("/", http.FileServer(http.Dir(h.staticDir)))

	// ── Task management ──────────────────────────────────────────────────────
	mux.HandleFunc("/api/tasks", h.withCORS(h.handleTasks))
	mux.HandleFunc("/api/tasks/", h.withCORS(h.handleTaskByID))
	mux.HandleFunc("/api/tasks/delete", h.withCORS(h.handleBatchDelete))
	mux.HandleFunc("/api/tasks/retry", h.withCORS(h.handleBatchRetry))

	// ── Manual task submission ────────────────────────────────────────────────
	mux.HandleFunc("/api/submit", h.withCORS(h.handleSubmit))
	// Legacy webhook endpoint (compatible with the Python version)
	mux.HandleFunc("/sendTask", h.withCORS(h.handleSendTask))

	// ── Queue management ─────────────────────────────────────────────────────
	mux.HandleFunc("/api/queue", h.withCORS(h.handleQueue))
	mux.HandleFunc("/api/queue/clear", h.withCORS(h.handleQueueClear))
	mux.HandleFunc("/api/queue/save", h.withCORS(h.handleQueueSave))
	mux.HandleFunc("/api/queue/load", h.withCORS(h.handleQueueLoad))

	// ── Monitor control ──────────────────────────────────────────────────────
	mux.HandleFunc("/api/monitor/pause", h.withCORS(h.handleMonitorPause))
	mux.HandleFunc("/api/monitor/resume", h.withCORS(h.handleMonitorResume))
	mux.HandleFunc("/api/monitor/restart", h.withCORS(h.handleMonitorRestart))
	mux.HandleFunc("/api/monitor/status", h.withCORS(h.handleMonitorStatus))
	mux.HandleFunc("/api/monitor/exclude", h.withCORS(h.handleMonitorExclude))

	// ── Configuration ────────────────────────────────────────────────────────
	mux.HandleFunc("/api/config", h.withCORS(h.handleConfig))

	// ── File-system browser (for add-task dialog) ────────────────────────────
	mux.HandleFunc("/api/files", h.withCORS(h.handleFileBrowser))

	// ── Logs ────────────────────────────────────────────────────────────────
	mux.HandleFunc("/api/logs", h.withCORS(h.handleLogs))
	mux.HandleFunc("/api/logs/download", h.withCORS(h.handleLogDownload))

	// ── System stats ─────────────────────────────────────────────────────────
	mux.HandleFunc("/api/stats", h.withCORS(h.handleStats))

	// ── AI test ──────────────────────────────────────────────────────────────
	mux.HandleFunc("/api/ai/test", h.withCORS(h.handleAITest))
	mux.HandleFunc("/api/tmdb/test", h.withCORS(h.handleTMDBTest))
}

// ─────────────────────────────────────────────────────────────────────────────
// Tasks
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleTasks(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		// Query params: text, status, season, sort_by, sort_order, page, page_size
		text := r.URL.Query().Get("text")
		status := r.URL.Query().Get("status")
		seasonStr := r.URL.Query().Get("season")
		sortBy := strings.TrimSpace(r.URL.Query().Get("sort_by"))
		sortOrder := strings.TrimSpace(r.URL.Query().Get("sort_order"))
		page, _ := strconv.Atoi(r.URL.Query().Get("page"))
		pageSize, _ := strconv.Atoi(r.URL.Query().Get("page_size"))

		if page <= 0 {
			page = 1
		}
		if pageSize <= 0 {
			pageSize = 100
		}

		var season *int
		if seasonStr != "" {
			if s, err := strconv.Atoi(seasonStr); err == nil {
				season = &s
			}
		}

		ascending := strings.EqualFold(sortOrder, "asc")
		all := h.store.ListTasksFiltered(text, status, season, sortBy, ascending)
		total := len(all)

		// Paginate
		start := (page - 1) * pageSize
		end := start + pageSize
		if start > total {
			start = total
		}
		if end > total {
			end = total
		}
		page_data := all[start:end]

		jsonOK(w, map[string]interface{}{
			"total":      total,
			"page":       page,
			"page_size":  pageSize,
			"sort_by":    sortBy,
			"sort_order": sortOrder,
			"items":      page_data,
		})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (h *Handler) handleTaskByID(w http.ResponseWriter, r *http.Request) {
	// URL: /api/tasks/<uuid>
	uuid := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	if uuid == "" {
		http.Error(w, "missing uuid", http.StatusBadRequest)
		return
	}

	switch r.Method {
	case http.MethodGet:
		rec := h.store.GetTask(uuid)
		if rec == nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		jsonOK(w, rec)

	case http.MethodPut:
		// Update task metadata (used by the Edit dialog)
		existing := h.store.GetTask(uuid)
		if existing == nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}

		var body rename.TaskRecord
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
			return
		}

		updated := *existing
		updated.UUID = uuid
		if body.Name != "" {
			updated.Name = body.Name
		}
		updated.IsAnime = body.IsAnime
		updated.IsMovie = body.IsMovie
		updated.UseAI = body.UseAI
		updated.SeasonID = body.SeasonID
		updated.Offset = body.Offset
		updated.TMDBID = body.TMDBID
		if body.Status != "" {
			updated.Status = body.Status
		}
		updated.ErrMsg = ""
		updated.ProcessedAt = body.ProcessedAt

		if err := h.store.SaveTask(&updated); err != nil {
			jsonError(w, "save failed: "+err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, updated)

	case http.MethodDelete:
		deleteFiles := r.URL.Query().Get("delete_files") == "true"
		deleteSource := r.URL.Query().Get("delete_source") == "true"
		cleanupDirs := r.URL.Query().Get("cleanup_dirs") == "true"
		if deleteFiles || deleteSource || cleanupDirs {
			_ = h.store.DeleteTaskFiles(uuid, deleteFiles, deleteSource, cleanupDirs)
		}
		if err := h.store.DeleteTask(uuid, true); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		jsonOK(w, map[string]string{"status": "deleted"})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (h *Handler) handleBatchDelete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		UUIDs        []string `json:"uuids"`
		DeleteFiles  bool     `json:"delete_files"`
		DeleteSource bool     `json:"delete_source"`
		CleanupDirs  bool     `json:"cleanup_dirs"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	deleted := 0
	for _, id := range body.UUIDs {
		if body.DeleteFiles || body.DeleteSource || body.CleanupDirs {
			_ = h.store.DeleteTaskFiles(id, body.DeleteFiles, body.DeleteSource, body.CleanupDirs)
		}
		if err := h.store.DeleteTask(id, true); err == nil {
			deleted++
		}
	}
	jsonOK(w, map[string]int{"deleted": deleted})
}

func (h *Handler) handleBatchRetry(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		UUIDs    []string               `json:"uuids"`
		Settings map[string]interface{} `json:"settings"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	queued := 0
	for _, id := range body.UUIDs {
		rec := h.store.GetTask(id)
		if rec == nil {
			continue
		}

		opts := monitor.TaskOptions{
			IsAnime:     rec.IsAnime,
			IsMovie:     rec.IsMovie,
			UseAI:       rec.UseAI,
			CusName:     rec.Name,
			CusTMDBID:   "",
			CusOffset:   rec.Offset,
			CusSeasonID: rec.SeasonID,
		}
		if strings.TrimSpace(rec.Name) == "" {
			opts.CusName = ""
		}

		// Apply batch settings overrides
		if v, ok := body.Settings["is_anime"]; ok {
			if s, ok := v.(string); ok {
				b := s == "是"
				opts.IsAnime = &b
			}
		}
		if v, ok := body.Settings["is_movie"]; ok {
			if s, ok := v.(string); ok {
				b := s == "是"
				opts.IsMovie = &b
			}
		}
		if v, ok := body.Settings["use_ai"]; ok {
			if s, ok := v.(string); ok {
				opts.UseAI = s == "启用"
			}
		}
		if v, ok := body.Settings["tmdb_id"]; ok {
			if s, ok := v.(string); ok {
				opts.CusTMDBID = strings.TrimSpace(s)
			}
		}
		if v, ok := body.Settings["name"]; ok {
			if s, ok := v.(string); ok {
				opts.CusName = strings.TrimSpace(s)
			}
		}
		if v, ok := body.Settings["clear_name"]; ok {
			if b, ok := v.(bool); ok && b {
				opts.CusName = ""
			}
		}
		if v, ok := body.Settings["season_id"]; ok {
			if s, ok := v.(string); ok && s != "" {
				if n, err := strconv.Atoi(s); err == nil {
					opts.CusSeasonID = &n
				}
			}
		}
		if v, ok := body.Settings["episode_offset"]; ok {
			if s, ok := v.(string); ok && s != "" {
				if n, err := strconv.Atoi(s); err == nil {
					opts.CusOffset = n
				}
			}
		}

		// Optional cleanup before retry
		deleteTarget := false
		deleteSource := false
		cleanupDirs := false
		if v, ok := body.Settings["delete_transferred"]; ok {
			if b, ok := v.(bool); ok {
				deleteTarget = b
			}
		}
		if v, ok := body.Settings["delete_source"]; ok {
			if b, ok := v.(bool); ok {
				deleteSource = b
			}
		}
		if v, ok := body.Settings["cleanup_dirs"]; ok {
			if b, ok := v.(bool); ok {
				cleanupDirs = b
			}
		}
		if deleteTarget || deleteSource || cleanupDirs {
			_ = h.store.DeleteTaskFiles(id, deleteTarget, deleteSource, cleanupDirs)
		}

		h.svc.AddTaskWithID(id, rec.Path, opts, monitor.PriorityManual, true)
		queued++
	}

	jsonOK(w, map[string]int{"queued": queued})
}

// ─────────────────────────────────────────────────────────────────────────────
// Task submission
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleSubmit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		Paths           []string               `json:"paths"`
		IsAnime         *bool                  `json:"is_anime"`
		IsMovie         *bool                  `json:"is_movie"`
		UseAI           bool                   `json:"use_ai"`
		ExcludeKeywords string                 `json:"exclude_keywords"`
		Overrides       map[string]interface{} `json:"overrides"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	added := 0
	ignored := 0
	cfg := h.cfg.GetConfig()

	for _, p := range body.Paths {
		info, err := os.Stat(p)
		if err != nil {
			h.log.Warn("[提交] 路径不存在: %s", p)
			ignored++
			continue
		}

		opts := monitor.TaskOptions{
			IsAnime:         body.IsAnime,
			IsMovie:         body.IsMovie,
			UseAI:           body.UseAI,
			ConfigOverrides: body.Overrides,
		}

		if info.IsDir() {
			// Walk directory
			_ = filepath.Walk(p, func(path string, fi os.FileInfo, err error) error {
				if err != nil || fi.IsDir() {
					return nil
				}
				if !rename.IsVideoFile(path) {
					return nil
				}
				if shouldExcludeByKeywords(path, body.ExcludeKeywords) {
					ignored++
					return nil
				}
				if shouldExcludeByConfig(path, cfg.ExcludeDirs) {
					ignored++
					return nil
				}
				h.svc.AddTask(path, opts)
				added++
				return nil
			})
		} else {
			if !rename.IsVideoFile(p) {
				ignored++
				continue
			}
			if shouldExcludeByKeywords(p, body.ExcludeKeywords) {
				ignored++
				continue
			}
			h.svc.AddTask(p, opts)
			added++
		}
	}

	h.svc.RegisterBatchCount(added)

	jsonOK(w, map[string]interface{}{
		"added":   added,
		"ignored": ignored,
		"message": fmt.Sprintf("已将 %d 个文件加入队列", added),
	})
}

// handleSendTask is the legacy webhook endpoint compatible with the Python version.
// It accepts form-encoded or JSON body with: path, is_anime, no_process, tag
func (h *Handler) handleSendTask(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var (
		path      string
		isAnime   string
		noProcess string
		tag       string
	)

	contentType := r.Header.Get("Content-Type")
	if strings.Contains(contentType, "application/json") {
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			jsonResp(w, http.StatusBadRequest, map[string]interface{}{"code": 400, "data": "invalid JSON"})
			return
		}
		path = body["path"]
		isAnime = body["is_anime"]
		noProcess = body["no_process"]
		tag = body["tag"]
	} else {
		_ = r.ParseForm()
		path = r.FormValue("path")
		isAnime = r.FormValue("is_anime")
		noProcess = r.FormValue("no_process")
		tag = r.FormValue("tag")
	}

	// Decode latin-1 encoded path (compatibility with Python version)
	if path != "" {
		decoded := decodeLatin1(path)
		if decoded != "" {
			path = decoded
		}
	}

	if path == "" {
		jsonResp(w, http.StatusBadRequest, map[string]interface{}{"code": 400, "data": "路径为空！"})
		return
	}

	tagList := splitTags(tag)

	if containsTag(tagList, "no_process") {
		noProcess = "true"
	}

	if noProcess == "true" || noProcess == "1" {
		h.log.Info("[Webhook] 忽略任务: %s", path)
		jsonResp(w, http.StatusAccepted, map[string]interface{}{"code": 202, "data": path + "忽略, 不处理！"})
		return
	}

	if _, err := os.Stat(path); err != nil {
		h.log.Error("[Webhook] 路径不存在: %s", path)
		jsonResp(w, http.StatusNotFound, map[string]interface{}{"code": 404, "data": "路径" + path + "不存在！"})
		return
	}

	aniTags := []string{"动漫", "anime", "动画"}
	movieTags := []string{"电影", "movie", "剧场", "剧场版"}

	var isAnimeBool *bool
	var isMovieBool *bool

	if isAnime != "" {
		b := isAnime == "true" || isAnime == "1"
		isAnimeBool = &b
	} else {
		for _, t := range aniTags {
			if containsTag(tagList, t) {
				b := true
				isAnimeBool = &b
				break
			}
		}
	}

	for _, t := range movieTags {
		if containsTag(tagList, t) {
			b := true
			isMovieBool = &b
			break
		}
	}

	opts := monitor.TaskOptions{
		IsAnime: isAnimeBool,
		IsMovie: isMovieBool,
	}
	h.svc.AddTask(path, opts)

	h.log.Info("[Webhook] 已接收任务: %s", path)
	jsonResp(w, http.StatusOK, map[string]interface{}{"code": 200, "data": "提交任务成功, 具体信息可以查看WebUI！"})
}

// ─────────────────────────────────────────────────────────────────────────────
// Queue management
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleQueue(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	jsonOK(w, map[string]interface{}{
		"length": h.svc.QueueLength(),
		"items":  h.svc.QueueList(),
	})
}

func (h *Handler) handleQueueClear(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.svc.ClearQueue()
	jsonOK(w, map[string]string{"status": "cleared"})
}

func (h *Handler) handleQueueSave(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := h.svc.SaveQueue(); err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]string{"status": "saved"})
}

func (h *Handler) handleQueueLoad(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := h.svc.LoadQueue(); err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]string{"status": "loaded"})
}

// ─────────────────────────────────────────────────────────────────────────────
// Monitor control
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleMonitorPause(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.svc.Pause()
	jsonOK(w, map[string]string{"status": "paused"})
}

func (h *Handler) handleMonitorResume(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	h.svc.Resume()
	jsonOK(w, map[string]string{"status": "resumed"})
}

func (h *Handler) handleMonitorRestart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	cfg := h.cfg.GetConfig()
	if !cfg.MonitorEnabled {
		jsonOK(w, map[string]string{"status": "disabled"})
		return
	}

	rawPaths := h.cfg.GetMonitorPaths()
	excludeDirs := h.cfg.GetMonitorExcludeDirs()

	paths := make([]monitor.PathConfig, 0, len(rawPaths))
	for _, p := range rawPaths {
		paths = append(paths, monitor.PathConfig{
			Path:   p.Path,
			Extras: p.Extras,
		})
	}

	if err := h.svc.StartWatchers(paths, excludeDirs, cfg.MonitorMode); err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]string{"status": "restarted"})
}

func (h *Handler) handleMonitorExclude(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		ExcludeDirs []string `json:"exclude_dirs"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	h.svc.UpdateExcludeDirs(body.ExcludeDirs)
	jsonOK(w, map[string]string{"status": "updated"})
}

func (h *Handler) handleMonitorStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	cfg := h.cfg.GetConfig()
	jsonOK(w, map[string]interface{}{
		"enabled":         cfg.MonitorEnabled,
		"paused":          h.svc.IsPaused(),
		"queue_length":    h.svc.QueueLength(),
		"has_saved_queue": h.svc.HasSavedQueue(),
		"watched_paths":   cfg.MonitorPaths,
	})
}

// ─────────────────────────────────────────────────────────────────────────────
// Configuration
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleConfig(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		jsonOK(w, h.cfg.GetConfig())

	case http.MethodPut, http.MethodPost:
		var raw map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}

		scanTargets := []monitor.PathConfig{}
		if mp, ok := raw["monitor_paths"]; ok {
			cleaned, targets := extractScanTargets(mp)
			raw["monitor_paths"] = cleaned
			scanTargets = targets
		}

		payload, err := json.Marshal(raw)
		if err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		var body config.Config
		if err := json.Unmarshal(payload, &body); err != nil {
			jsonError(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := h.cfg.SetConfig(body); err != nil {
			jsonError(w, err.Error(), http.StatusInternalServerError)
			return
		}
		h.svc.UpdateExcludeDirs(h.cfg.GetMonitorExcludeDirs())

		if len(scanTargets) > 0 {
			exclude := h.cfg.GetMonitorExcludeDirs()
			added := h.svc.ScanNow(scanTargets, exclude)
			if added > 0 {
				h.svc.RegisterBatchCount(added)
			}
		}
		jsonOK(w, map[string]string{"status": "saved"})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func extractScanTargets(raw interface{}) (interface{}, []monitor.PathConfig) {
	if raw == nil {
		return raw, nil
	}

	// Attempt to parse JSON string form
	if s, ok := raw.(string); ok {
		var list []interface{}
		if err := json.Unmarshal([]byte(s), &list); err == nil {
			cleaned, targets := extractScanTargets(list)
			return cleaned, targets
		}
		return raw, nil
	}

	items, ok := raw.([]interface{})
	if !ok {
		return raw, nil
	}

	cleaned := make([]interface{}, 0, len(items))
	var targets []monitor.PathConfig
	for _, item := range items {
		switch entry := item.(type) {
		case string:
			cleaned = append(cleaned, entry)
		case map[string]interface{}:
			path, _ := entry["path"].(string)
			scanNow, _ := entry["scan_now"].(bool)
			delete(entry, "scan_now")
			cleaned = append(cleaned, entry)
			if scanNow && path != "" {
				extras := make(map[string]interface{})
				for k, v := range entry {
					if k == "path" {
						continue
					}
					extras[k] = v
				}
				targets = append(targets, monitor.PathConfig{Path: path, Extras: extras})
			}
		}
	}

	return cleaned, targets
}

// ─────────────────────────────────────────────────────────────────────────────
// File-system browser
// ─────────────────────────────────────────────────────────────────────────────

type fsEntry struct {
	Name  string `json:"name"`
	Path  string `json:"path"`
	IsDir bool   `json:"is_dir"`
}

func (h *Handler) handleFileBrowser(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	dir := r.URL.Query().Get("path")
	if dir == "" {
		// Default to docker mount or root
		cfg := h.cfg.GetConfig()
		if cfg.DockerMnt != "" {
			dir = cfg.DockerMnt
		} else {
			dir = "/"
		}
	}

	// Clean & validate
	dir = filepath.Clean(dir)
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		dir = "/"
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		jsonError(w, err.Error(), http.StatusInternalServerError)
		return
	}

	var result []fsEntry

	// Parent entry
	parent := filepath.Dir(dir)
	if parent != dir {
		result = append(result, fsEntry{
			Name:  "..",
			Path:  parent,
			IsDir: true,
		})
	}

	for _, e := range entries {
		name := e.Name()
		if strings.HasPrefix(name, ".") {
			continue
		}
		result = append(result, fsEntry{
			Name:  name,
			Path:  filepath.Join(dir, name),
			IsDir: e.IsDir(),
		})
	}

	jsonOK(w, map[string]interface{}{
		"current": dir,
		"entries": result,
	})
}

// ─────────────────────────────────────────────────────────────────────────────
// Logs
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleLogs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	n, _ := strconv.Atoi(r.URL.Query().Get("n"))
	if n <= 0 {
		n = 200
	}

	lines := h.log.HistoryFormatted(n)
	jsonOK(w, map[string]interface{}{
		"lines": lines,
		"count": len(lines),
	})
}

func (h *Handler) handleLogDownload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	data, err := os.ReadFile(h.logPath)
	if err != nil {
		http.Error(w, "log file not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="BAR.log"`)
	_, _ = w.Write(data)
}

// ─────────────────────────────────────────────────────────────────────────────
// System stats
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	statusCounts := h.store.CountByStatus()
	jsonOK(w, map[string]interface{}{
		"total_tasks":  h.store.Count(),
		"status":       statusCounts,
		"queue_length": h.svc.QueueLength(),
		"paused":       h.svc.IsPaused(),
		"server_time":  time.Now().Format("2006-01-02 15:04:05"),
	})
}

// ─────────────────────────────────────────────────────────────────────────────
// AI & TMDB test
// ─────────────────────────────────────────────────────────────────────────────

func (h *Handler) handleAITest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if body.Name == "" {
		body.Name = "进击的巨人 (2013)"
	}

	cfg := h.cfg.GetConfig()
	if !cfg.AIEnabled {
		jsonOK(w, map[string]interface{}{"ok": false, "message": "AI未启用，请先在配置中开启"})
		return
	}

	aiClient := ai.New(h.cfg)
	if !aiClient.IsAvailable() {
		jsonOK(w, map[string]interface{}{"ok": false, "message": "AI不可用，请检查 API Key 是否已配置"})
		return
	}

	ctx := map[string]interface{}{
		"folder_name": body.Name,
		"full_path":   body.Name,
		"file_names":  []string{body.Name + ".mkv"},
	}

	start := time.Now()
	meta := aiClient.AnalyzeMetadata(ctx)
	elapsed := time.Since(start).Seconds()

	if meta == nil {
		jsonOK(w, map[string]interface{}{
			"ok":      false,
			"message": fmt.Sprintf("AI 请求失败（耗时 %.1fs），请检查 API Key 和网络连接", elapsed),
		})
		return
	}

	mediaType := "剧集"
	if meta.IsMovie {
		mediaType = "电影"
	}
	message := fmt.Sprintf(
		"✅ AI 连接成功（耗时 %.1fs）\n识别结果：%s（%d）[%s] | 置信度：%s",
		elapsed, meta.Name, meta.Year, mediaType, meta.Confidence,
	)
	jsonOK(w, map[string]interface{}{
		"ok":      true,
		"message": message,
		"result":  meta,
	})
}

func (h *Handler) handleTMDBTest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	cfg := h.cfg.GetConfig()
	if cfg.APIKey == "" {
		jsonOK(w, map[string]interface{}{"ok": false, "message": "TMDB API Key未配置"})
		return
	}

	// Simple connectivity test: search for a known title
	client := rename.NewTMDBClient(cfg.APIKey)
	results, err := client.SearchTV("Breaking Bad", 2008)
	if err != nil {
		jsonOK(w, map[string]interface{}{"ok": false, "message": "TMDB连接失败: " + err.Error()})
		return
	}
	if len(results) == 0 {
		jsonOK(w, map[string]interface{}{"ok": false, "message": "TMDB返回空结果，请检查API Key"})
		return
	}
	jsonOK(w, map[string]interface{}{"ok": true, "message": fmt.Sprintf("TMDB连接正常，找到 %d 个结果", len(results))})
}

// ─────────────────────────────────────────────────────────────────────────────
// Utility helpers
// ─────────────────────────────────────────────────────────────────────────────

// withCORS wraps a handler to add permissive CORS headers for local use.
func (h *Handler) withCORS(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next(w, r)
	}
}

func jsonOK(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_ = json.NewEncoder(w).Encode(v)
}

func jsonError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func jsonResp(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

// shouldExcludeByKeywords reports whether a file path matches any of the
// newline-separated keyword/regex patterns supplied by the user.
func shouldExcludeByKeywords(path, patterns string) bool {
	if patterns == "" {
		return false
	}
	lower := strings.ToLower(path)
	for _, line := range strings.Split(patterns, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if strings.Contains(lower, strings.ToLower(line)) {
			return true
		}
	}
	return false
}

// shouldExcludeByConfig reports whether a file path falls under any of the
// globally configured exclude directories.
func shouldExcludeByConfig(path string, excludeDirs []string) bool {
	for _, excl := range excludeDirs {
		if excl == "" {
			continue
		}
		if strings.Contains(path, excl) {
			return true
		}
	}
	return false
}

// splitTags splits a comma-separated tag string into a lowercase trimmed slice.
func splitTags(tag string) []string {
	var out []string
	for _, t := range strings.Split(tag, ",") {
		t = strings.TrimSpace(strings.ToLower(t))
		if t != "" {
			out = append(out, t)
		}
	}
	return out
}

// containsTag reports whether the tag slice contains the given target (case-insensitive).
func containsTag(tags []string, target string) bool {
	target = strings.ToLower(target)
	for _, t := range tags {
		if t == target {
			return true
		}
	}
	return false
}

// decodeLatin1 attempts to re-interpret a string that was encoded as latin-1
// but contains UTF-8 bytes (a common issue with Python's form parsing).
// Returns the decoded string, or empty string if decoding fails or produces no change.
func decodeLatin1(s string) string {
	b := make([]byte, len(s))
	for i, r := range s {
		if r > 0xFF {
			return "" // contains non-latin1 codepoints – don't recode
		}
		b[i] = byte(r)
	}
	// Check if the byte slice is valid UTF-8 and differs from the original
	decoded := string(b)
	if decoded != s {
		return decoded
	}
	return s
}
