package monitor

import (
	"context"
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/google/uuid"

	"bangumi_auto_rename/internal/logger"
	"bangumi_auto_rename/internal/rename"
)

// maxWatchDepth is the maximum number of directory levels below a configured
// root that will be pre-registered with fsnotify on startup, and the cap used
// when dynamically adding newly-created directories at runtime.
const maxWatchDepth = 4

// maxWatchersPerRoot is the maximum number of sub-directories that will be
// added to fsnotify per monitored root path.  Each watch consumes one OS file
// descriptor; this cap prevents "too many open files" on very large trees.
// Directories beyond the cap are still handled via dynamic detection in
// watchLoop when new sub-directories are created at runtime.
const maxWatchersPerRoot = 300

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
const pollInterval = 3 * time.Second

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
	UUID    string      `json:"uuid"`
	Path    string      `json:"path"`
	Options TaskOptions `json:"options"`
	AddedAt time.Time   `json:"added_at"`
}

// QueueSnapshot is used to persist / restore the queue between process restarts.
type QueueSnapshot struct {
	Items []QueueItem `json:"items"`
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

	// stable-file detection: path → pending entry (size + opts)
	pendingFiles  map[string]pendingEntry
	pendingFileMu sync.Mutex

	// fsnotify watcher (nil when monitoring is disabled / in polling mode)
	watcher     *fsnotify.Watcher
	watchedDirs []PathConfig
	excludeDirs []string

	// pollCancel cancels the pollLoop goroutine (compatibility mode)
	pollCancel context.CancelFunc

	// channel to feed items to the worker
	workCh chan struct{}

	// pause / resume
	paused bool

	// total items expected in the current batch (for progress reporting)
	batchTotal int

	ctx    context.Context
	cancel context.CancelFunc

	// ensures Init is only run once
	once sync.Once
}

// pendingEntry holds the last-seen file size together with the TaskOptions
// that were determined when the file-system event was first observed.
// Keeping opts here ensures that per-directory is_anime overrides are not
// lost between the watchLoop and the stabilityChecker goroutine.
type pendingEntry struct {
	size int64
	opts TaskOptions
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
			processor:    processor,
			store:        store,
			log:          logger.Get(),
			dataDir:      dataDir,
			pendingFiles: make(map[string]pendingEntry),
			workCh:       make(chan struct{}, 512),
			ctx:          ctx,
			cancel:       cancel,
		}
		globalService = svc
		go svc.worker()
		go svc.stabilityChecker()
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

	if len(paths) == 0 {
		s.log.Info("[监控] 无监控目录配置，跳过启动")
		return nil
	}

	// Determine monitoring mode
	watchMode := "compatibility"
	if len(mode) > 0 && mode[0] != "" {
		watchMode = mode[0]
	}

	if watchMode == "native" {
		return s.startNativeWatchers(paths)
	}

	// Default: compatibility (polling) mode – mirrors Python's PollingObserver.
	// This is more reliable as it works at any directory depth and doesn't
	// depend on OS-level inotify/kqueue limits.
	s.log.Info("[监控] 使用兼容模式 (轮询)，与 Python 版行为一致")
	pollCtx, pollCancel := context.WithCancel(s.ctx)
	s.pollCancel = pollCancel
	go s.pollLoop(pollCtx, paths)
	return nil
}

// startNativeWatchers starts fsnotify-based watchers (native mode).
func (s *Service) startNativeWatchers(paths []PathConfig) error {
	w, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}

	for _, pc := range paths {
		if _, err := os.Stat(pc.Path); err != nil {
			s.log.Warn("[监控] 路径不存在，跳过: %s", pc.Path)
			continue
		}
		if err := w.Add(pc.Path); err != nil {
			s.log.Warn("[监控] 添加监控失败 %s: %v", pc.Path, err)
			continue
		}
		s.log.Info("[监控] 开始监控 (原生模式): %s", pc.Path)

		count := 0
		s.addDirsRecursive(w, pc.Path, 0, &count)
		if count > 0 {
			s.log.Info("[监控] 已添加 %d 个子目录: %s", count, pc.Path)
		}
	}

	s.watcher = w
	go s.watchLoop(w, paths)
	return nil
}

// Stop shuts down the service worker and any active watchers.
func (s *Service) Stop() {
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
	return s.AddTaskWithPriority(path, opts, false)
}

// AddTaskWithID enqueues a task using a caller-supplied UUID so retries/edits
// can reuse the existing task record instead of creating duplicates.
func (s *Service) AddTaskWithID(id, path string, opts TaskOptions, priority bool) string {
	if id == "" {
		id = uuid.New().String()
	}
	item := QueueItem{
		UUID:    id,
		Path:    path,
		Options: opts,
		AddedAt: time.Now(),
	}

	s.mu.Lock()
	for i, q := range s.queue {
		if q.UUID == id || q.Path == path {
			s.queue = append(s.queue[:i], s.queue[i+1:]...)
			break
		}
	}
	if priority {
		s.queue = append([]QueueItem{item}, s.queue...)
	} else {
		s.queue = append(s.queue, item)
	}
	s.mu.Unlock()

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
func (s *Service) AddTaskWithPriority(path string, opts TaskOptions, priority bool) string {
	return s.AddTaskWithID("", path, opts, priority)
}

// RegisterBatchCount sets the expected total count for the current batch
// (used for progress display in the UI).
func (s *Service) RegisterBatchCount(n int) {
	s.mu.Lock()
	s.batchTotal = n
	s.mu.Unlock()
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
	return out
}

// ClearQueue discards all pending (unprocessed) queue items.
func (s *Service) ClearQueue() {
	s.mu.Lock()
	s.queue = s.queue[:0]
	s.mu.Unlock()
	s.log.Info("[任务] 队列已清空")
}

// ─────────────────────────────────────────────────────────────────────────────
// Queue persistence
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) queueFilePath() string {
	return filepath.Join(s.dataDir, "queue.json")
}

// SaveQueue persists the current queue to disk so it survives a restart.
func (s *Service) SaveQueue() error {
	s.mu.Lock()
	snapshot := QueueSnapshot{Items: make([]QueueItem, len(s.queue))}
	copy(snapshot.Items, s.queue)
	s.mu.Unlock()

	data, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.queueFilePath() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.queueFilePath())
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

	var snapshot QueueSnapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return err
	}

	_ = os.Remove(s.queueFilePath())

	for _, item := range snapshot.Items {
		s.mu.Lock()
		s.queue = append(s.queue, item)
		s.mu.Unlock()
		select {
		case s.workCh <- struct{}{}:
		default:
		}
	}

	s.log.Info("[任务] 已从磁盘恢复 %d 个队列任务", len(snapshot.Items))
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
// directories at unlimited depth, detects new video files, and feeds them
// through the same pendingFiles → stabilityChecker → AddTask pipeline.
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

					// Add to pending for stability check
					// Extract TMDB ID from path if present
					if tmdbID := extractTMDBIDFromPath(filePath); tmdbID != "" {
						opts.CusTMDBID = tmdbID
					}
					s.pendingFileMu.Lock()
					s.pendingFiles[filePath] = pendingEntry{size: size, opts: opts}
					s.pendingFileMu.Unlock()

					s.log.Debug("[监控] 轮询检测到新文件: %s", filePath)
				} else {
					// Existing file — update size in pendingFiles if it changed
					// (the stabilityChecker uses this to detect when writing stops)
					s.pendingFileMu.Lock()
					if _, isPending := s.pendingFiles[filePath]; isPending {
						pe := s.pendingFiles[filePath]
						pe.size = size
						s.pendingFiles[filePath] = pe
					}
					s.pendingFileMu.Unlock()
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
			if event.Op&(fsnotify.Create|fsnotify.Write|fsnotify.Rename) == 0 {
				continue
			}

			// If a new directory was created, add it and its sub-tree to the
			// watcher so that files placed inside it are also detected.
			if info, statErr := os.Stat(event.Name); statErr == nil && info.IsDir() {
				if !s.shouldExclude(event.Name) {
					if addErr := w.Add(event.Name); addErr == nil {
						s.log.Debug("[监控] 动态监控新目录: %s", event.Name)
						count := 0
						s.addDirsRecursive(w, event.Name, 0, &count)
					} else {
						s.log.Warn("[监控] 动态添加目录失败 %s: %v", event.Name, addErr)
					}
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

			// Queue for stability check (don't process files still being written).
			// Store opts alongside the size so the stabilityChecker can use them.
			s.pendingFileMu.Lock()
			info, err := os.Stat(event.Name)
			if err == nil {
				s.pendingFiles[event.Name] = pendingEntry{size: info.Size(), opts: opts}
			}
			s.pendingFileMu.Unlock()

			s.log.Debug("[监控] 检测到文件变化: %s", event.Name)

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
	if v, ok := extras["is_anime"]; ok {
		if b, ok := v.(bool); ok {
			opts.IsAnime = &b
		}
	}
	if v, ok := extras["is_movie"]; ok {
		if b, ok := v.(bool); ok {
			opts.IsMovie = &b
		}
	}
	if v, ok := extras["tv_rename_format"]; ok {
		if s, ok := v.(string); ok && strings.TrimSpace(s) != "" {
			if opts.ConfigOverrides == nil {
				opts.ConfigOverrides = map[string]interface{}{}
			}
			opts.ConfigOverrides["tv_rename_format"] = s
		}
	}
	if v, ok := extras["movie_rename_format"]; ok {
		if s, ok := v.(string); ok && strings.TrimSpace(s) != "" {
			if opts.ConfigOverrides == nil {
				opts.ConfigOverrides = map[string]interface{}{}
			}
			opts.ConfigOverrides["movie_rename_format"] = s
		}
	}
	if v, ok := extras["mode"]; ok {
		if s, ok := v.(string); ok && strings.TrimSpace(s) != "" {
			if opts.ConfigOverrides == nil {
				opts.ConfigOverrides = map[string]interface{}{}
			}
			opts.ConfigOverrides["mode"] = s
		}
	}
	if v, ok := extras["overwrite_mode"]; ok {
		if s, ok := v.(string); ok && strings.TrimSpace(s) != "" {
			if opts.ConfigOverrides == nil {
				opts.ConfigOverrides = map[string]interface{}{}
			}
			opts.ConfigOverrides["overwrite_mode"] = s
		}
	}
	return opts
}

// ScanNow walks the given paths and enqueues all video files for processing.
// It is used by the config "scan_now" action to mimic the Python behavior.
func (s *Service) ScanNow(paths []PathConfig, excludeDirs []string) int {
	count := 0
	shouldExclude := func(path string) bool {
		for _, excl := range excludeDirs {
			if excl == "" {
				continue
			}
			if strings.Contains(path, excl) {
				return true
			}
		}
		base := filepath.Base(path)
		return strings.HasPrefix(base, ".")
	}

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
				if shouldExclude(path) {
					return filepath.SkipDir
				}
				return nil
			}
			if shouldExclude(path) {
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

// addDirsRecursive adds sub-directories of dir to w, up to maxWatchDepth
// levels deep and maxWatchersPerRoot total (shared via count pointer).
// It opens only one directory at a time with os.ReadDir so that the process
// never accumulates too many open file descriptors simultaneously.
func (s *Service) addDirsRecursive(w *fsnotify.Watcher, dir string, depth int, count *int) {
	if depth >= maxWatchDepth || *count >= maxWatchersPerRoot {
		return
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		if *count >= maxWatchersPerRoot {
			s.log.Warn("[监控] 子目录监控已达上限 %d，其余目录将依靠动态检测", maxWatchersPerRoot)
			return
		}
		subdir := filepath.Join(dir, entry.Name())
		if s.shouldExclude(subdir) {
			continue
		}
		if addErr := w.Add(subdir); addErr != nil {
			s.log.Warn("[监控] 添加子目录监控失败 %s: %v", subdir, addErr)
		} else {
			(*count)++
			s.log.Debug("[监控] 监控子目录: %s", subdir)
		}
		s.addDirsRecursive(w, subdir, depth+1, count)
	}
}

// shouldExclude reports whether a path matches any of the configured exclude patterns.
func (s *Service) shouldExclude(path string) bool {
	s.mu.Lock()
	excl := append([]string{}, s.excludeDirs...)
	s.mu.Unlock()
	lowerPath := strings.ToLower(path)
	for _, excl := range excl {
		if excl == "" {
			continue
		}
		if strings.Contains(lowerPath, strings.ToLower(excl)) {
			return true
		}
	}
	base := filepath.Base(path)
	return strings.HasPrefix(base, ".")
}

// UpdateExcludeDirs updates the in-memory exclude list without restarting watchers.
func (s *Service) UpdateExcludeDirs(exclude []string) {
	s.mu.Lock()
	s.excludeDirs = exclude
	s.mu.Unlock()
	s.log.Info("[监控] 排除目录已更新 (%d 条)", len(exclude))
}

// ─────────────────────────────────────────────────────────────────────────────
// Stability checker – waits for files to stop growing before queuing them
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) stabilityChecker() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	type stableEntry struct {
		size int64
		opts TaskOptions
	}
	stable := make(map[string]stableEntry)   // path → entry at last stable check
	checked := make(map[string]pendingEntry) // path → entry at previous tick

	for {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
			s.pendingFileMu.Lock()
			current := make(map[string]pendingEntry, len(s.pendingFiles))
			for k, v := range s.pendingFiles {
				current[k] = v
			}
			s.pendingFileMu.Unlock()

			for path, entry := range current {
				prev, existed := checked[path]
				if !existed {
					checked[path] = entry
					continue
				}
				if entry.size == prev.size {
					// Size hasn't changed – file is stable
					if _, alreadyQueued := stable[path]; !alreadyQueued {
						stable[path] = stableEntry{size: entry.size, opts: entry.opts}
						s.log.Info("[监控] 文件就绪，加入队列: %s", path)
						// Pass the opts that were captured when the event fired,
						// preserving per-directory overrides (e.g. is_anime).
						s.AddTaskWithPriority(path, entry.opts, true)
					}

					// Remove from pending
					s.pendingFileMu.Lock()
					delete(s.pendingFiles, path)
					s.pendingFileMu.Unlock()
					delete(checked, path)
				} else {
					checked[path] = entry
				}
			}

			// Clean up stable entries that are no longer in pending
			for path := range stable {
				if _, inPending := current[path]; !inPending {
					delete(stable, path)
				}
			}
		}
	}
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
	item := s.queue[0]
	s.queue = s.queue[1:]
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
		return
	}
	s.log.Info("[任务] 开始处理 [%s]: %s", item.UUID, item.Path)

	rec := s.processor.Process(item.Path, item.Options.toRenameOpts(), item.UUID)

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
}
