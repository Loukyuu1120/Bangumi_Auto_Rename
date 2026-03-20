package rename

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Store manages on-disk task and record JSON files.
//
// Directory layout:
//   data/
//     task/    – one <uuid>.json per task (mutable, user-editable metadata)
//     record/  – one <uuid>.json per processed task (source→target mapping)
// ─────────────────────────────────────────────────────────────────────────────

// Store persists TaskRecord objects to disk.
type Store struct {
	mu        sync.RWMutex
	taskDir   string
	recordDir string
	// in-memory index: uuid → *TaskRecord
	cache         map[string]*TaskRecord
	maxCacheItems int
}

const defaultHotCacheSize = 2000

// NewStore creates a Store rooted at dataDir.
func NewStore(dataDir string) (*Store, error) {
	taskDir := filepath.Join(dataDir, "task")
	recordDir := filepath.Join(dataDir, "record")

	for _, dir := range []string{taskDir, recordDir} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}

	s := &Store{
		taskDir:       taskDir,
		recordDir:     recordDir,
		cache:         make(map[string]*TaskRecord),
		maxCacheItems: resolveHotCacheSize(),
	}

	// Pre-load existing tasks into the cache.
	if err := s.loadAll(); err != nil {
		return nil, err
	}
	return s, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Write operations
// ─────────────────────────────────────────────────────────────────────────────

// SaveTask writes a TaskRecord to the task directory and updates the cache.
func (s *Store) SaveTask(rec *TaskRecord) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if rec.ProcessedAt == "" {
		rec.ProcessedAt = time.Now().Format("2006-01-02 15:04:05")
	}

	data, err := json.MarshalIndent(rec, "", "    ")
	if err != nil {
		return err
	}
	path := filepath.Join(s.taskDir, rec.UUID+".json")
	if err := writeAtomic(path, data); err != nil {
		return err
	}

	// Deep-copy into cache
	cp := *rec
	s.cache[rec.UUID] = &cp
	s.pruneCacheLocked()
	return nil
}

// SaveRecord writes the source→target file mapping for a completed task.
// The record file format is { "source_path": "target_path", ... }.
func (s *Store) SaveRecord(uuid string, fileMap map[string]string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	data, err := json.MarshalIndent(fileMap, "", "    ")
	if err != nil {
		return err
	}
	path := filepath.Join(s.recordDir, uuid+".json")
	return writeAtomic(path, data)
}

// DeleteTask removes the task (and optionally its record) for the given UUID.
func (s *Store) DeleteTask(uuid string, deleteRecord bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	taskPath := filepath.Join(s.taskDir, uuid+".json")
	if err := os.Remove(taskPath); err != nil && !os.IsNotExist(err) {
		return err
	}
	delete(s.cache, uuid)

	if deleteRecord {
		recPath := filepath.Join(s.recordDir, uuid+".json")
		if err := os.Remove(recPath); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

// DeleteTaskFiles removes source/target files for a task UUID and optionally
// cleans up empty directories (no video files). It does not delete the task
// or record JSON files.
func (s *Store) DeleteTaskFiles(uuid string, deleteTarget, deleteSource, cleanupDirs bool) error {
	var mapping map[string]string
	recPath := filepath.Join(s.recordDir, uuid+".json")
	if data, err := os.ReadFile(recPath); err == nil {
		_ = json.Unmarshal(data, &mapping)
	}

	var taskPath string
	if data, err := os.ReadFile(filepath.Join(s.taskDir, uuid+".json")); err == nil {
		var rec TaskRecord
		if jsonErr := json.Unmarshal(data, &rec); jsonErr == nil {
			taskPath = rec.Path
		}
	}

	dirsToCheck := map[string]struct{}{}

	if deleteTarget && len(mapping) > 0 {
		for _, tgt := range mapping {
			if tgt == "" {
				continue
			}
			t := filepath.Clean(tgt)
			parent := filepath.Dir(t)
			// collect up to 3 levels for cleanup consideration
			for i := 0; i < 3; i++ {
				if parent == "." || parent == string(filepath.Separator) {
					break
				}
				dirsToCheck[parent] = struct{}{}
				parent = filepath.Dir(parent)
			}

			// delete target file
			_ = os.Remove(t)

			// delete sibling files with same prefix (subtitles/nfo/images)
			base := strings.TrimSuffix(filepath.Base(t), filepath.Ext(t))
			if entries, err := os.ReadDir(filepath.Dir(t)); err == nil {
				for _, e := range entries {
					if e.IsDir() {
						continue
					}
					name := e.Name()
					if strings.HasPrefix(strings.TrimSuffix(name, filepath.Ext(name)), base) {
						_ = os.Remove(filepath.Join(filepath.Dir(t), name))
					}
				}
			}
		}
	}

	if cleanupDirs && len(dirsToCheck) > 0 {
		for dir := range dirsToCheck {
			if dir == "" || dir == "." || dir == string(filepath.Separator) {
				continue
			}
			if !hasVideoFiles(dir) {
				_ = os.RemoveAll(dir)
			}
		}
	}

	if deleteSource {
		maybe := map[string]struct{}{}
		if taskPath != "" {
			maybe[taskPath] = struct{}{}
		}
		for src := range mapping {
			if src != "" {
				maybe[src] = struct{}{}
			}
		}
		for src := range maybe {
			_ = os.Remove(src)
		}
	}

	return nil
}

func hasVideoFiles(dir string) bool {
	found := false
	_ = filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
		if err != nil || found {
			return nil
		}
		if d.IsDir() {
			if strings.HasPrefix(d.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		ext := strings.ToLower(filepath.Ext(path))
		if VideoSuffix[ext] {
			found = true
		}
		return nil
	})
	return found
}

// ─────────────────────────────────────────────────────────────────────────────
// Read operations
// ─────────────────────────────────────────────────────────────────────────────

// GetTask returns the TaskRecord for the given UUID, or nil if not found.
func (s *Store) GetTask(uuid string) *TaskRecord {
	s.mu.RLock()
	rec := s.cache[uuid]
	s.mu.RUnlock()

	if rec != nil {
		cp := *rec
		return &cp
	}

	return s.loadTaskFromDisk(uuid)
}

// GetRecord returns the source→target file-mapping for a completed task,
// or nil if the record file does not exist.
func (s *Store) GetRecord(uuid string) map[string]string {
	s.mu.RLock()
	defer s.mu.RUnlock()

	path := filepath.Join(s.recordDir, uuid+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var m map[string]string
	if err := json.Unmarshal(data, &m); err != nil {
		return nil
	}
	return m
}

// ListTasks returns all TaskRecords sorted newest-first by ProcessedAt.
// Uses a stable secondary sort on UUID so the order is deterministic even
// when many records share the same (or empty) ProcessedAt value.
func (s *Store) ListTasks() []*TaskRecord {
	return s.readAllTasks()
}

// ListTasksFiltered returns tasks matching optional text / status / season filters.
// All filter strings are case-insensitive.  Empty strings mean "no filter".
func (s *Store) ListTasksFiltered(text, status string, season *int, sortBy string, ascending bool) []*TaskRecord {
	all := s.ListTasks()
	text = strings.ToLower(strings.TrimSpace(text))
	status = strings.TrimSpace(status)
	sortBy = strings.TrimSpace(sortBy)

	var out []*TaskRecord
	for _, r := range all {
		if text != "" {
			combined := strings.ToLower(r.Name + " " + r.Path)
			if !strings.Contains(combined, text) {
				continue
			}
		}
		if status != "" && status != "全部" {
			if string(r.Status) != status {
				continue
			}
		}
		if season != nil {
			if r.SeasonID == nil || *r.SeasonID != *season {
				continue
			}
		}
		out = append(out, r)
	}

	if sortBy == "processed_at" {
		sort.SliceStable(out, func(i, j int) bool {
			if out[i].ProcessedAt == out[j].ProcessedAt {
				if ascending {
					return out[i].UUID < out[j].UUID
				}
				return out[i].UUID > out[j].UUID
			}
			if ascending {
				return out[i].ProcessedAt < out[j].ProcessedAt
			}
			return out[i].ProcessedAt > out[j].ProcessedAt
		})
	}

	return out
}

// Count returns the total number of cached tasks.
func (s *Store) Count() int {
	entries, err := os.ReadDir(s.taskDir)
	if err != nil {
		return 0
	}
	count := 0
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		count++
	}
	return count
}

// CountByStatus returns a map of status → count.
func (s *Store) CountByStatus() map[string]int {
	m := make(map[string]int)
	for _, rec := range s.readAllTasks() {
		m[string(rec.Status)]++
	}
	return m
}

// CacheSize returns the number of hot task records currently kept in memory.
func (s *Store) CacheSize() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.cache)
}

// ReclaimMemory trims the hot task cache and returns the remaining item count.
func (s *Store) ReclaimMemory(keep int) int {
	s.mu.Lock()
	defer s.mu.Unlock()

	if keep < 0 {
		keep = 0
	}
	if keep == 0 {
		s.cache = make(map[string]*TaskRecord)
		return 0
	}
	s.pruneCacheToLocked(keep)
	return len(s.cache)
}

// ─────────────────────────────────────────────────────────────────────────────
// Queue persistence (for the monitor service)
// ─────────────────────────────────────────────────────────────────────────────

// QueueEntry represents a single pending item that was serialised to disk.
type QueueEntry struct {
	Path    string      `json:"path"`
	Options TaskOptions `json:"options"`
	UUID    string      `json:"uuid"`
}

// SaveQueue writes a slice of pending queue entries to data/queue.json.
func (s *Store) SaveQueue(entries []QueueEntry) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	data, err := json.MarshalIndent(entries, "", "    ")
	if err != nil {
		return err
	}
	path := filepath.Join(filepath.Dir(s.taskDir), "queue.json")
	return writeAtomic(path, data)
}

// LoadQueue reads and returns the persisted queue, removing the file afterwards.
func (s *Store) LoadQueue() ([]QueueEntry, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	path := filepath.Join(filepath.Dir(s.taskDir), "queue.json")
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var entries []QueueEntry
	if err := json.Unmarshal(data, &entries); err != nil {
		return nil, err
	}

	// Remove the file once loaded
	_ = os.Remove(path)
	return entries, nil
}

// HasSavedQueue reports whether a persisted queue file exists.
func (s *Store) HasSavedQueue() bool {
	path := filepath.Join(filepath.Dir(s.taskDir), "queue.json")
	_, err := os.Stat(path)
	return err == nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

// loadAll scans taskDir and populates the in-memory cache.
// It also performs an in-memory migration for records produced by the
// Python version of the application, which used a different schema.
func (s *Store) loadAll() error {
	entries, err := os.ReadDir(s.taskDir)
	if err != nil {
		return err
	}
	type preloadEntry struct {
		name    string
		modTime time.Time
	}
	preload := make([]preloadEntry, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		preload = append(preload, preloadEntry{name: entry.Name(), modTime: info.ModTime()})
	}
	sort.Slice(preload, func(i, j int) bool {
		return preload[i].modTime.After(preload[j].modTime)
	})
	if len(preload) > s.maxCacheItems {
		preload = preload[:s.maxCacheItems]
	}
	for _, item := range preload {
		uuid := strings.TrimSuffix(item.name, ".json")
		if rec := s.loadTaskFile(filepath.Join(s.taskDir, item.name), uuid, item.modTime); rec != nil {
			s.cache[uuid] = rec
		}
	}
	return nil
}

func (s *Store) readAllTasks() []*TaskRecord {
	entries, err := os.ReadDir(s.taskDir)
	if err != nil {
		return nil
	}

	list := make([]*TaskRecord, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		uuid := strings.TrimSuffix(entry.Name(), ".json")
		info, _ := entry.Info()
		rec := s.loadTaskFile(filepath.Join(s.taskDir, entry.Name()), uuid, modTimeOrZero(info))
		if rec == nil {
			continue
		}
		list = append(list, rec)
	}

	sort.SliceStable(list, func(i, j int) bool {
		if list[i].ProcessedAt != list[j].ProcessedAt {
			return list[i].ProcessedAt > list[j].ProcessedAt
		}
		return list[i].UUID > list[j].UUID
	})
	return list
}

func (s *Store) loadTaskFromDisk(uuid string) *TaskRecord {
	rec := s.loadTaskFile(filepath.Join(s.taskDir, uuid+".json"), uuid, time.Time{})
	if rec == nil {
		return nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	cp := *rec
	s.cache[uuid] = &cp
	s.pruneCacheLocked()
	out := cp
	return &out
}

func (s *Store) loadTaskFile(path, uuid string, fallbackModTime time.Time) *TaskRecord {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var rec TaskRecord
	if err := json.Unmarshal(data, &rec); err != nil {
		return nil
	}
	if rec.UUID == "" {
		rec.UUID = uuid
	}
	normalizeTaskRecord(&rec, fallbackModTime)
	return &rec
}

func normalizeTaskRecord(rec *TaskRecord, fallbackModTime time.Time) {
	if rec == nil {
		return
	}
	if rec.TargetPath != "" && len(rec.TargetPaths) == 0 {
		rec.TargetPaths = []string{rec.TargetPath}
	}
	if rec.Status == "" {
		if len(rec.TargetPaths) > 0 || rec.TargetPath != "" {
			rec.Status = StatusSuccess
		} else {
			rec.Status = StatusFailed
		}
	}
	if rec.ProcessedAt == "" && !fallbackModTime.IsZero() {
		rec.ProcessedAt = fallbackModTime.Format(time.RFC3339[:19])
	}
}

func (s *Store) pruneCacheLocked() {
	s.pruneCacheToLocked(s.maxCacheItems)
}

func (s *Store) pruneCacheToLocked(limit int) {
	if limit <= 0 || len(s.cache) <= limit {
		return
	}

	type cacheItem struct {
		uuid string
		rec  *TaskRecord
	}
	items := make([]cacheItem, 0, len(s.cache))
	for uuid, rec := range s.cache {
		items = append(items, cacheItem{uuid: uuid, rec: rec})
	}
	sort.Slice(items, func(i, j int) bool {
		pi := processedAtUnix(items[i].rec)
		pj := processedAtUnix(items[j].rec)
		if pi != pj {
			return pi > pj
		}
		return items[i].uuid > items[j].uuid
	})
	for _, item := range items[limit:] {
		delete(s.cache, item.uuid)
	}
}

func processedAtUnix(rec *TaskRecord) int64 {
	if rec == nil {
		return 0
	}
	if rec.ProcessedAt == "" {
		return 0
	}
	layouts := []string{
		"2006-01-02 15:04:05",
		time.RFC3339,
		time.RFC3339[:19],
	}
	for _, layout := range layouts {
		if ts, err := time.Parse(layout, rec.ProcessedAt); err == nil {
			return ts.Unix()
		}
	}
	return 0
}

func modTimeOrZero(info os.FileInfo) time.Time {
	if info == nil {
		return time.Time{}
	}
	return info.ModTime()
}

func resolveHotCacheSize() int {
	raw := strings.TrimSpace(os.Getenv("BAR_TASK_CACHE_SIZE"))
	if raw == "" {
		return defaultHotCacheSize
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return defaultHotCacheSize
	}
	return n
}

// writeAtomic writes data to path atomically via a temporary file.
func writeAtomic(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
