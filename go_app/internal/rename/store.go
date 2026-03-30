package rename

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
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
	cache map[string]*TaskRecord
}

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
		taskDir:   taskDir,
		recordDir: recordDir,
		cache:     make(map[string]*TaskRecord),
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
	defer s.mu.RUnlock()

	rec := s.cache[uuid]
	if rec == nil {
		return nil
	}
	cp := *rec
	return &cp
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
	s.mu.RLock()
	defer s.mu.RUnlock()

	list := make([]*TaskRecord, 0, len(s.cache))
	for _, rec := range s.cache {
		cp := *rec
		list = append(list, &cp)
	}

	sort.SliceStable(list, func(i, j int) bool {
		if list[i].ProcessedAt != list[j].ProcessedAt {
			return list[i].ProcessedAt > list[j].ProcessedAt
		}
		return list[i].UUID > list[j].UUID
	})
	return list
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
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.cache)
}

// CountByStatus returns a map of status → count.
func (s *Store) CountByStatus() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()

	m := make(map[string]int)
	for _, rec := range s.cache {
		m[string(rec.Status)]++
	}
	return m
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
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		// Only load ordinary files. This avoids blocking on special files
		// such as FIFOs, sockets, or device nodes that may exist on some NAS
		// filesystems or after manual recovery operations.
		if !info.Mode().IsRegular() {
			continue
		}
		uuid := strings.TrimSuffix(entry.Name(), ".json")
		path := filepath.Join(s.taskDir, entry.Name())

		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var rec TaskRecord
		if err := json.Unmarshal(data, &rec); err != nil {
			continue
		}
		if rec.UUID == "" {
			rec.UUID = uuid
		}

		// ── Migrate Python-format records ────────────────────────────────
		// Python stored a single "target_path" string; normalise to slice.
		if rec.TargetPath != "" && len(rec.TargetPaths) == 0 {
			rec.TargetPaths = []string{rec.TargetPath}
		}

		// Python records had no "status" field.  Infer it from available data.
		if rec.Status == "" {
			if len(rec.TargetPaths) > 0 || rec.TargetPath != "" {
				rec.Status = StatusSuccess
			} else {
				rec.Status = StatusFailed
			}
		}

		// Python records had no "processed_at" field.
		// Fall back to the JSON file's modification time.
		if rec.ProcessedAt == "" {
			rec.ProcessedAt = info.ModTime().Format(time.RFC3339[:19])
		}
		// ─────────────────────────────────────────────────────────────────

		s.cache[uuid] = &rec
	}
	return nil
}

// writeAtomic writes data to path atomically via a temporary file.
func writeAtomic(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
