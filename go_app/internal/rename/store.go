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
func (s *Store) ListTasks() []*TaskRecord {
	s.mu.RLock()
	defer s.mu.RUnlock()

	list := make([]*TaskRecord, 0, len(s.cache))
	for _, rec := range s.cache {
		cp := *rec
		list = append(list, &cp)
	}

	sort.Slice(list, func(i, j int) bool {
		return list[i].ProcessedAt > list[j].ProcessedAt
	})
	return list
}

// ListTasksFiltered returns tasks matching optional text / status / season filters.
// All filter strings are case-insensitive.  Empty strings mean "no filter".
func (s *Store) ListTasksFiltered(text, status string, season *int) []*TaskRecord {
	all := s.ListTasks()
	text = strings.ToLower(strings.TrimSpace(text))
	status = strings.TrimSpace(status)

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
func (s *Store) loadAll() error {
	entries, err := os.ReadDir(s.taskDir)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
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
