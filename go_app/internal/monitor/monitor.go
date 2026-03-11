package monitor

import (
	"context"
	"encoding/json"
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

	// stable-file detection: path → last-seen size
	pendingFiles  map[string]int64
	pendingFileMu sync.Mutex

	// fsnotify watcher (nil when monitoring is disabled)
	watcher     *fsnotify.Watcher
	watchedDirs []PathConfig
	excludeDirs []string

	// channel to feed items to the worker
	workCh chan QueueItem

	// pause / resume
	paused bool

	// total items expected in the current batch (for progress reporting)
	batchTotal int

	ctx    context.Context
	cancel context.CancelFunc

	// ensures Init is only run once
	once sync.Once
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
			pendingFiles: make(map[string]int64),
			workCh:       make(chan QueueItem, 512),
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

// StartWatchers starts fsnotify watchers for the given path configs.
// It stops any previously running watchers first.
func (s *Service) StartWatchers(paths []PathConfig, excludeDirs []string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Stop existing watcher
	if s.watcher != nil {
		_ = s.watcher.Close()
		s.watcher = nil
	}

	s.watchedDirs = paths
	s.excludeDirs = excludeDirs

	if len(paths) == 0 {
		s.log.Info("[监控] 无监控目录配置，跳过启动")
		return nil
	}

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
		s.log.Info("[监控] 开始监控: %s", pc.Path)
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
	id := uuid.New().String()
	item := QueueItem{
		UUID:    id,
		Path:    path,
		Options: opts,
		AddedAt: time.Now(),
	}

	s.mu.Lock()
	s.queue = append(s.queue, item)
	s.mu.Unlock()

	// Non-blocking send to worker
	select {
	case s.workCh <- item:
	default:
		// Channel full – item is still in queue slice; worker will drain it
	}

	s.log.Info("[任务] 已加入队列: %s", path)
	return id
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
		case s.workCh <- item:
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
// fsnotify event loop
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
			if !rename.IsVideoFile(event.Name) {
				continue
			}
			if s.shouldExclude(event.Name) {
				continue
			}

			// Determine which path config this file belongs to
			var opts TaskOptions
			for watchedPath, pc := range pathMap {
				if strings.HasPrefix(filepath.Clean(event.Name), watchedPath) {
					// Apply path-level is_anime if configured
					if v, ok := pc.Extras["is_anime"]; ok {
						b, _ := v.(bool)
						opts.IsAnime = &b
					}
					break
				}
			}

			// Queue for stability check (don't process files still being written)
			s.pendingFileMu.Lock()
			info, err := os.Stat(event.Name)
			if err == nil {
				s.pendingFiles[event.Name] = info.Size()
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

// shouldExclude reports whether a path matches any of the configured exclude patterns.
func (s *Service) shouldExclude(path string) bool {
	for _, excl := range s.excludeDirs {
		if strings.Contains(path, excl) {
			return true
		}
	}
	base := filepath.Base(path)
	return strings.HasPrefix(base, ".")
}

// ─────────────────────────────────────────────────────────────────────────────
// Stability checker – waits for files to stop growing before queuing them
// ─────────────────────────────────────────────────────────────────────────────

func (s *Service) stabilityChecker() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	stable := make(map[string]int64)  // path → size at last stable check
	checked := make(map[string]int64) // path → size at previous tick

	for {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
			s.pendingFileMu.Lock()
			current := make(map[string]int64, len(s.pendingFiles))
			for k, v := range s.pendingFiles {
				current[k] = v
			}
			s.pendingFileMu.Unlock()

			for path, size := range current {
				prev, existed := checked[path]
				if !existed {
					checked[path] = size
					continue
				}
				if size == prev {
					// Size hasn't changed – file is stable
					if _, alreadyQueued := stable[path]; !alreadyQueued {
						stable[path] = size
						s.log.Info("[监控] 文件就绪，加入队列: %s", path)
						s.AddTask(path, TaskOptions{})
					}

					// Remove from pending
					s.pendingFileMu.Lock()
					delete(s.pendingFiles, path)
					s.pendingFileMu.Unlock()
					delete(checked, path)
				} else {
					checked[path] = size
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

		case item := <-s.workCh:
			// Respect pause state – busy-wait with backoff
			for s.IsPaused() {
				select {
				case <-s.ctx.Done():
					return
				case <-time.After(2 * time.Second):
				}
			}

			s.processItem(item)

			// Remove from queue slice
			s.mu.Lock()
			for i, q := range s.queue {
				if q.UUID == item.UUID {
					s.queue = append(s.queue[:i], s.queue[i+1:]...)
					break
				}
			}
			s.mu.Unlock()
		}
	}
}

func (s *Service) processItem(item QueueItem) {
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
