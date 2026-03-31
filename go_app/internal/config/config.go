package config

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Config holds all application configuration
type Config struct {
	APIKey                  string      `json:"api_key"`
	BangumiPath             string      `json:"bangumi_path"`
	MoviePath               string      `json:"movie_path"`
	AnimePath               string      `json:"anime_path"`
	AnimeMoviePath          string      `json:"anime_movie_path"`
	TVRenameFormat          string      `json:"tv_rename_format"`
	MovieRenameFormat       string      `json:"movie_rename_format"`
	ExcludeDirs             []string    `json:"exclude_dirs"`
	Mode                    string      `json:"mode"`
	OverwriteMode           string      `json:"overwrite_mode"`
	ScrapeMetadata          bool        `json:"scrape_metadata"`
	ScrapeImageTypes        []string    `json:"scrape_image_types"`
	SubtitleExtensions      []string    `json:"subtitle_extensions"`
	SecondaryClassification bool        `json:"secondary_classification"`
	SecondaryRules          interface{} `json:"secondary_rules"`
	DockerMnt               string      `json:"docker_mnt"`
	AIProvider              string      `json:"ai_provider"`
	AIAPIKey                string      `json:"ai_api_key"`
	AIBaseURL               string      `json:"ai_base_url"`
	AIModel                 string      `json:"ai_model"`
	AITemperature           float64     `json:"ai_temperature"`
	AIRateLimitRPM          int         `json:"ai_rate_limit_rpm"`
	AIRateLimitTPM          int         `json:"ai_rate_limit_tpm"`
	GeminiAPIKey            string      `json:"gemini_api_key"`
	GeminiBaseURL           string      `json:"gemini_base_url"`
	GeminiModel             string      `json:"gemini_model"`
	GeminiTemperature       float64     `json:"gemini_temperature"`
	AIEnabled               bool        `json:"ai_enabled"`
	AIConfidenceThreshold   string      `json:"ai_confidence_threshold"`
	OpenAIOutputFormat      string      `json:"openai_output_format"`
	AIAutoSave              bool        `json:"ai_auto_save"`
	LogLevel                string      `json:"log_level"`
	MonitorEnabled          bool        `json:"monitor_enabled"`
	MonitorMode             string      `json:"monitor_mode"`
	MonitorPaths            interface{} `json:"monitor_paths"`
	MonitorExcludeDirs      interface{} `json:"monitor_exclude_dirs"`
	TitleLanguages          []string    `json:"title_languages"`
	OverviewLanguages       []string    `json:"overview_languages"`
	ScrapeLanguage          string      `json:"scrape_language"`
	BatchRetryDefaultName   string      `json:"batch_retry_default_name"`
}

// DefaultConfig returns a Config populated with default values
func DefaultConfig() Config {
	return Config{
		APIKey:                  "",
		BangumiPath:             "",
		MoviePath:               "",
		AnimePath:               "",
		AnimeMoviePath:          "",
		TVRenameFormat:          "{{title}}{% if year %} ({{year}}){% endif %}/Season {{season}}/{{title}} - {{season_episode}}{% if part %}-{{part}}{% endif %}{% if episode %} - 第 {{episode}} 集{% endif %}{{fileExt}}",
		MovieRenameFormat:       "{{title}}{% if year %} ({{year}}){% endif %}/{{title}}{% if year %} ({{year}}){% endif %}{% if part %}-{{part}}{% endif %}{% if videoFormat %} - {{videoFormat}}{% endif %}{{fileExt}}",
		ExcludeDirs:             []string{},
		Mode:                    "硬链接",
		OverwriteMode:           "从不覆盖",
		ScrapeMetadata:          false,
		ScrapeImageTypes:        []string{"poster", "backdrop", "logo"},
		SubtitleExtensions:      []string{".ass", ".srt", ".sub"},
		SecondaryClassification: false,
		SecondaryRules:          map[string]interface{}{},
		DockerMnt:               "/media",
		AIProvider:              "openai",
		AIAPIKey:                "",
		AIBaseURL:               "https://api.openai.com/v1",
		AIModel:                 "gpt-4o-mini",
		AITemperature:           0.1,
		AIRateLimitRPM:          0,
		AIRateLimitTPM:          0,
		GeminiAPIKey:            "",
		GeminiBaseURL:           "https://generativelanguage.googleapis.com",
		GeminiModel:             "gemini-2.5-flash",
		GeminiTemperature:       0.5,
		AIEnabled:               false,
		AIConfidenceThreshold:   "Medium",
		OpenAIOutputFormat:      "function_calling",
		AIAutoSave:              false,
		LogLevel:                "INFO",
		MonitorEnabled:          false,
		MonitorMode:             "compatibility",
		MonitorPaths:            []interface{}{},
		MonitorExcludeDirs:      []interface{}{},
		TitleLanguages:          []string{"zh-CN", "en-US"},
		OverviewLanguages:       []string{"zh-CN", "en-US"},
		ScrapeLanguage:          "zh-CN",
		BatchRetryDefaultName:   "",
	}
}

// Manager manages reading and writing of config.json
type Manager struct {
	mu         sync.RWMutex
	configPath string
	cfg        Config
}

var (
	instance *Manager
	once     sync.Once
)

// Init initialises the singleton config manager with the given data directory.
func Init(dataDir string) (*Manager, error) {
	var initErr error
	once.Do(func() {
		m := &Manager{
			configPath: filepath.Join(dataDir, "config.json"),
		}
		if err := m.load(); err != nil {
			initErr = err
			return
		}
		instance = m
	})
	if initErr != nil {
		return nil, initErr
	}
	return instance, nil
}

// Get returns the singleton manager (must call Init first).
func Get() *Manager {
	return instance
}

// load reads config from disk, filling in defaults for missing keys.
func (m *Manager) load() error {
	defaults := DefaultConfig()

	data, err := os.ReadFile(m.configPath)
	if err != nil {
		if os.IsNotExist(err) {
			// First run: write defaults
			m.cfg = defaults
			return m.write()
		}
		return fmt.Errorf("reading config: %w", err)
	}

	// Unmarshal into a raw map first so we can merge defaults
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		// Corrupt file – reset to defaults
		m.cfg = defaults
		return m.write()
	}

	// Re-marshal defaults as base, then overlay file values
	baseBytes, _ := json.Marshal(defaults)
	var merged map[string]json.RawMessage
	_ = json.Unmarshal(baseBytes, &merged)
	for k, v := range raw {
		merged[k] = v
	}
	mergedBytes, _ := json.Marshal(merged)
	if err := json.Unmarshal(mergedBytes, &m.cfg); err != nil {
		m.cfg = defaults
		return m.write()
	}

	return m.write() // Persist any new default keys
}

// write atomically writes config to disk.
func (m *Manager) write() error {
	dir := filepath.Dir(m.configPath)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}

	data, err := json.MarshalIndent(m.cfg, "", "    ")
	if err != nil {
		return err
	}

	tmp := m.configPath + ".bak"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, m.configPath)
}

// GetConfig returns a copy of the current configuration.
func (m *Manager) GetConfig() Config {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.cfg
}

// SetConfig replaces the current config and persists it.
func (m *Manager) SetConfig(cfg Config) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Normalise URLs
	cfg.AIBaseURL = normalizeURL(cfg.AIBaseURL)
	cfg.GeminiBaseURL = normalizeURL(cfg.GeminiBaseURL)

	m.cfg = cfg
	return m.write()
}

// UpdateField updates a single field by key (JSON field name) and persists.
func (m *Manager) UpdateField(key string, value interface{}) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Round-trip through JSON map for simplicity
	cfgBytes, err := json.Marshal(m.cfg)
	if err != nil {
		return err
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(cfgBytes, &raw); err != nil {
		return err
	}

	if strings.HasSuffix(key, "_base_url") {
		if s, ok := value.(string); ok {
			value = normalizeURL(s)
		}
	}
	raw[key] = value

	merged, err := json.Marshal(raw)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(merged, &m.cfg); err != nil {
		return err
	}
	return m.write()
}

// GetMonitorPaths returns monitor_paths as a slice of MonitorPathConfig.
func (m *Manager) GetMonitorPaths() []MonitorPathConfig {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return parseMonitorPaths(m.cfg.MonitorPaths)
}

// GetMonitorExcludeDirs returns monitor_exclude_dirs as []string.
func (m *Manager) GetMonitorExcludeDirs() []string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return parseStringList(m.cfg.MonitorExcludeDirs)
}

// --- helpers ---

// MonitorPathConfig represents a single monitored path entry.
type MonitorPathConfig struct {
	Path   string                 `json:"path"`
	Mode   string                 `json:"mode,omitempty"`
	Extras map[string]interface{} `json:"-"`
}

func parseMonitorPaths(raw interface{}) []MonitorPathConfig {
	var result []MonitorPathConfig
	if raw == nil {
		return result
	}
	// The JSON value could be []string or []map or a JSON-encoded string
	switch v := raw.(type) {
	case []interface{}:
		for _, item := range v {
			switch entry := item.(type) {
			case string:
				result = append(result, MonitorPathConfig{Path: entry})
			case map[string]interface{}:
				p, _ := entry["path"].(string)
				mode, _ := entry["monitor_mode"].(string)
				result = append(result, MonitorPathConfig{Path: p, Mode: mode, Extras: entry})
			}
		}
	case string:
		// Try JSON decode
		var list []interface{}
		if err := json.Unmarshal([]byte(v), &list); err == nil {
			return parseMonitorPaths(list)
		}
		if v != "" {
			result = append(result, MonitorPathConfig{Path: v})
		}
	}
	return result
}

func parseStringList(raw interface{}) []string {
	var result []string
	if raw == nil {
		return result
	}
	switch v := raw.(type) {
	case []interface{}:
		for _, item := range v {
			if s, ok := item.(string); ok && s != "" {
				result = append(result, s)
			}
		}
	case []string:
		return v
	case string:
		var list []interface{}
		if err := json.Unmarshal([]byte(v), &list); err == nil {
			return parseStringList(list)
		}
	}
	return result
}

func parseDockerMounts(raw string) []string {
	lines := strings.Split(raw, "\n")
	out := make([]string, 0, len(lines))
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		out = append(out, line)
	}
	return out
}

func FirstDockerMount(raw string) string {
	mounts := parseDockerMounts(raw)
	if len(mounts) == 0 {
		return ""
	}
	return mounts[0]
}

func normalizeURL(raw string) string {
	if raw == "" {
		return raw
	}
	raw = strings.TrimRight(raw, "/")
	if !strings.HasPrefix(raw, "http://") && !strings.HasPrefix(raw, "https://") {
		raw = "https://" + raw
	}
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return raw
	}
	return raw
}
