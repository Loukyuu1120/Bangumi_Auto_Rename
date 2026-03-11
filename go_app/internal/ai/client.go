package ai

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"bangumi_auto_rename/internal/config"
	"bangumi_auto_rename/internal/logger"
)

// AnalysisResult is a simplified AI episode-mapping result.
type AnalysisResult struct {
	Confidence  string           `json:"confidence"`
	Reason      string           `json:"reason"`
	FileMapping []EpisodeMapping `json:"file_mapping"`
	ExtraNotes  string           `json:"extra_notes,omitempty"`
}

// EpisodeMapping maps a local file to a TMDB season/episode.
type EpisodeMapping struct {
	FilePath    string `json:"file_path"`
	TMDBSeason  int    `json:"tmdb_season"`
	TMDBEpisode int    `json:"tmdb_episode"`
	EpisodeType string `json:"episode_type"` // regular | special | movie
	Confidence  string `json:"confidence"`
}

// MetadataResult is the AI response for metadata inference.
type MetadataResult struct {
	Name       string `json:"name"`
	Year       int    `json:"year"`
	IsMovie    bool   `json:"is_movie"`
	TMDBId     string `json:"tmdb_id,omitempty"`
	Confidence string `json:"confidence"`
}

// Client wraps either OpenAI-compatible or Gemini REST APIs.
type Client struct {
	cfg *config.Manager
}

// New creates an AI Client backed by the global config manager.
func New(cfg *config.Manager) *Client {
	return &Client{cfg: cfg}
}

// IsAvailable reports whether the AI feature is enabled and configured.
func (c *Client) IsAvailable() bool {
	cfg := c.cfg.GetConfig()
	if !cfg.AIEnabled {
		return false
	}
	switch strings.ToLower(cfg.AIProvider) {
	case "gemini":
		return cfg.GeminiAPIKey != ""
	default:
		return cfg.AIAPIKey != ""
	}
}

// SelectBestTMDBMatch asks the AI to pick the best entry from a list of TMDB
// search results.  Returns the 0-based index of the best match, or -1.
func (c *Client) SelectBestTMDBMatch(query string, year int, candidates []map[string]interface{}, isMovie bool) int {
	if !c.IsAvailable() || len(candidates) == 0 {
		return -1
	}
	if len(candidates) == 1 {
		return 0
	}
	if len(candidates) > 10 {
		return -1
	}

	mediaType := "电视剧"
	if isMovie {
		mediaType = "电影"
	}
	yearHint := ""
	if year > 0 {
		yearHint = fmt.Sprintf(" (%d)", year)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("你是一个媒体信息匹配专家。从TMDB搜索结果中选最匹配的%s。\n搜索词: %s%s\n\n候选结果:\n", mediaType, query, yearHint))
	for i, r := range candidates {
		if isMovie {
			sb.WriteString(fmt.Sprintf("%d. 标题:%v 原标题:%v 年份:%v 简介:%v\n", i+1,
				r["title"], r["original_title"],
				firstChars(fmt.Sprintf("%v", r["release_date"]), 4),
				firstChars(fmt.Sprintf("%v", r["overview"]), 150)))
		} else {
			sb.WriteString(fmt.Sprintf("%d. 名称:%v 原名:%v 年份:%v 简介:%v\n", i+1,
				r["name"], r["original_name"],
				firstChars(fmt.Sprintf("%v", r["first_air_date"]), 4),
				firstChars(fmt.Sprintf("%v", r["overview"]), 150)))
		}
	}
	sb.WriteString(fmt.Sprintf("\n只返回最匹配的序号(1-%d)，不匹配返回0，不要其他文字。", len(candidates)))

	systemPrompt := "你是一个专业的媒体信息匹配助手，只输出数字。"
	resp, err := c.chatComplete(systemPrompt, sb.String(), 0.3)
	if err != nil {
		logger.Warn("[AI辅助] 请求失败: %v", err)
		return -1
	}

	var idx int
	fmt.Sscanf(strings.TrimSpace(resp), "%d", &idx)
	if idx <= 0 || idx > len(candidates) {
		return -1
	}
	return idx - 1
}

// AnalyzeMetadata infers name/year/type from path and file context.
func (c *Client) AnalyzeMetadata(contextData map[string]interface{}) *MetadataResult {
	if !c.IsAvailable() {
		return nil
	}

	folderName, _ := contextData["folder_name"].(string)
	fullPath, _ := contextData["full_path"].(string)
	fileNames, _ := contextData["file_names"].([]string)

	var filesStr strings.Builder
	for _, f := range fileNames {
		filesStr.WriteString("- " + f + "\n")
	}

	prompt := fmt.Sprintf(`你是媒体元数据提取专家。分析以下信息并提取TMDB搜索用的核心元数据。

主要名称: %s
完整路径: %s
包含文件:
%s

请返回纯JSON（不含Markdown标记），格式：
{"name":"...","year":0,"is_movie":false,"tmdb_id":"","confidence":"High|Medium|Low"}

规则：
1. 检查路径中的{tmdb-xxxx}或[tmdbid=xxxx]标记，有则填入tmdb_id
2. 移除版本修饰词、制作组、分辨率等噪音，保留官方标题
3. 禁止翻译标题（中文保留中文，日文保留日文）
4. 年份优先取括号内数字`, folderName, fullPath, filesStr.String())

	systemPrompt := "你是严格的媒体元数据提取器，只输出纯JSON，禁止Markdown。"
	resp, err := c.chatComplete(systemPrompt, prompt, 0.1)
	if err != nil {
		logger.Warn("[AI搜索] 请求失败: %v", err)
		return nil
	}

	// Strip markdown code fences if present
	resp = strings.TrimSpace(resp)
	if strings.HasPrefix(resp, "```") {
		lines := strings.Split(resp, "\n")
		if len(lines) > 2 {
			resp = strings.Join(lines[1:len(lines)-1], "\n")
		}
	}

	var result MetadataResult
	if err := json.Unmarshal([]byte(resp), &result); err != nil {
		logger.Warn("[AI搜索] 解析响应失败: %v\nraw=%s", err, resp)
		return nil
	}
	return &result
}

// chatComplete sends a single user message and returns the assistant reply.
// It routes to the correct backend based on ai_provider config.
func (c *Client) chatComplete(system, user string, temperature float64) (string, error) {
	cfg := c.cfg.GetConfig()
	switch strings.ToLower(cfg.AIProvider) {
	case "gemini":
		return c.geminiComplete(system, user, temperature)
	default:
		return c.openaiComplete(system, user, temperature)
	}
}

// --- OpenAI-compatible implementation ---

type openAIRequest struct {
	Model       string          `json:"model"`
	Messages    []openAIMessage `json:"messages"`
	Temperature float64         `json:"temperature"`
}

type openAIMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type openAIResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

func (c *Client) openaiComplete(system, user string, temperature float64) (string, error) {
	cfg := c.cfg.GetConfig()
	baseURL := strings.TrimRight(cfg.AIBaseURL, "/")
	endpoint := baseURL + "/chat/completions"

	reqBody := openAIRequest{
		Model: cfg.AIModel,
		Messages: []openAIMessage{
			{Role: "system", Content: system},
			{Role: "user", Content: user},
		},
		Temperature: temperature,
	}
	body, _ := json.Marshal(reqBody)

	req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cfg.AIAPIKey)

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("openai request: %w", err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	var oResp openAIResponse
	if err := json.Unmarshal(data, &oResp); err != nil {
		return "", fmt.Errorf("parsing openai response: %w", err)
	}
	if oResp.Error != nil {
		return "", fmt.Errorf("openai error: %s", oResp.Error.Message)
	}
	if len(oResp.Choices) == 0 {
		return "", fmt.Errorf("openai: empty choices")
	}
	return oResp.Choices[0].Message.Content, nil
}

// --- Gemini implementation ---

type geminiRequest struct {
	Contents          []geminiContent      `json:"contents"`
	SystemInstruction *geminiSystemContent `json:"system_instruction,omitempty"`
	GenerationConfig  *geminiGenConfig     `json:"generation_config,omitempty"`
}

type geminiContent struct {
	Parts []geminiPart `json:"parts"`
	Role  string       `json:"role,omitempty"`
}

type geminiSystemContent struct {
	Parts []geminiPart `json:"parts"`
}

type geminiPart struct {
	Text string `json:"text"`
}

type geminiGenConfig struct {
	Temperature float64 `json:"temperature"`
}

type geminiResponse struct {
	Candidates []struct {
		Content struct {
			Parts []struct {
				Text string `json:"text"`
			} `json:"parts"`
		} `json:"content"`
	} `json:"candidates"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

func (c *Client) geminiComplete(system, user string, temperature float64) (string, error) {
	cfg := c.cfg.GetConfig()
	baseURL := strings.TrimRight(cfg.GeminiBaseURL, "/")
	model := cfg.GeminiModel
	apiKey := cfg.GeminiAPIKey
	endpoint := fmt.Sprintf("%s/v1beta/models/%s:generateContent?key=%s", baseURL, model, apiKey)

	reqBody := geminiRequest{
		Contents: []geminiContent{
			{Role: "user", Parts: []geminiPart{{Text: user}}},
		},
		SystemInstruction: &geminiSystemContent{
			Parts: []geminiPart{{Text: system}},
		},
		GenerationConfig: &geminiGenConfig{Temperature: temperature},
	}
	body, _ := json.Marshal(reqBody)

	req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("gemini request: %w", err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	var gResp geminiResponse
	if err := json.Unmarshal(data, &gResp); err != nil {
		return "", fmt.Errorf("parsing gemini response: %w", err)
	}
	if gResp.Error != nil {
		return "", fmt.Errorf("gemini error: %s", gResp.Error.Message)
	}
	if len(gResp.Candidates) == 0 || len(gResp.Candidates[0].Content.Parts) == 0 {
		return "", fmt.Errorf("gemini: empty response")
	}
	return gResp.Candidates[0].Content.Parts[0].Text, nil
}

// --- helpers ---

func firstChars(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
