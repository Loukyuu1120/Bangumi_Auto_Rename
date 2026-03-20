package ai

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

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

type tokenEvent struct {
	at     time.Time
	tokens int
}

type requestRateLimiter struct {
	mu             sync.Mutex
	requests       []time.Time
	tokenEvents    []tokenEvent
	cooldownUntil  time.Time
	cooldownReason string
}

var globalRateLimiter requestRateLimiter

type aiRateLimitError struct {
	status     int
	message    string
	retryAfter time.Duration
}

func (e *aiRateLimitError) Error() string {
	if e == nil {
		return ""
	}
	if e.retryAfter > 0 {
		return fmt.Sprintf("ai upstream rate limited (%d), retry after %.1fs: %s", e.status, e.retryAfter.Seconds(), e.message)
	}
	return fmt.Sprintf("ai upstream rate limited (%d): %s", e.status, e.message)
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

	prompt := fmt.Sprintf(`你是一个专业的媒体元数据分析专家。请分析以下文件路径信息，提取用于 TMDB 搜索的核心元数据。

主要分析名称:
%s

完整路径参考 (包含潜在的ID或父级目录信息):
%s

包含的文件:
%s

请返回纯JSON（不含Markdown标记），格式：
{"name":"...","year":0,"is_movie":false,"tmdb_id":"","confidence":"High|Medium|Low"}

请严格遵守以下步骤进行推断：

1. 提取 TMDB ID（最高优先级）：
   - 仔细检查完整路径参考和文件名
   - 查找 {tmdb-xxxx}、[tmdbid=xxxx]、tmdb:xxxx、tmdb=xxxx 等标记
   - 如果发现，优先填入 tmdb_id，这比推断名字更准确

2. 清洗官方名称（Official Name）：
   - 从主要分析名称中提取核心标题
   - 必须移除：版本修饰词（如“新编集版”“重制版”“Director's Cut”）、制作组信息、分辨率、语种、片源、编码、音频格式等噪音
   - 保留原名：如果是中文名，保留中文；如果是英文，保留英文；如果是日文，保留日文
   - 绝对禁止将中文标题翻译成英文，也不要凭空意译标题
   - 如果标题里混有季号、集号、SxxExx、分辨率、WEB-DL、BluRay、x264、x265、HEVC、AAC、DDP、Atmos 等技术信息，必须剔除

3. 确定年份：
   - 优先查找圆括号中的年份，如 (2024)
   - 如果主要分析名称里没有，再去完整路径参考中查找
   - 只有在高度确定时才填写 year，否则填 0

4. 判断类型：
   - 如果明显是单文件电影、剧场版、Movie、Film，倾向 is_movie=true
   - 如果包含季号、SxxExx、第X季、多集文件、Season 目录，倾向 is_movie=false
   - 无法确定时优先按剧集处理，即 is_movie=false

5. 置信度：
   - 信息非常明确时返回 High
   - 有一定推断但基本可信时返回 Medium
   - 依据不足时返回 Low

只输出纯 JSON，不要输出解释、不要输出 Markdown、不要输出代码块。`, folderName, fullPath, filesStr.String())

	systemPrompt := "你是一个严格的媒体文件元数据提取器。你的任务是从文件路径中提取用于数据库搜索的标准名称、年份和ID。禁止意译标题，必须移除噪音词。你只输出纯 JSON 格式的结果，不要包含 Markdown 标记。"
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
	c.waitForRateLimit(system, user)

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
	return doJSONRequestWithRetry(client, req, 2, parseOpenAIResponse)
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
	return doJSONRequestWithRetry(client, req, 2, parseGeminiResponse)
}

// --- helpers ---

func firstChars(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func (c *Client) waitForRateLimit(system, user string) {
	cfg := c.cfg.GetConfig()
	rpm := cfg.AIRateLimitRPM
	tpm := cfg.AIRateLimitTPM
	globalRateLimiter.wait(rpm, tpm, estimatePromptTokens(system, user))
}

func (l *requestRateLimiter) wait(rpm, tpm, tokens int) {
	for {
		l.mu.Lock()
		now := time.Now()
		l.pruneLocked(now)

		waitFor := time.Duration(0)
		if d := l.cooldownWaitLocked(now); d > waitFor {
			waitFor = d
		}
		if rpm > 0 && len(l.requests) >= rpm {
			if d := time.Until(l.requests[0].Add(time.Minute)); d > waitFor {
				waitFor = d
			}
		}
		if tpm > 0 && tokens > 0 {
			if d := l.tokenWaitLocked(now, tpm, tokens); d > waitFor {
				waitFor = d
			}
		}

		if waitFor <= 0 {
			l.requests = append(l.requests, now)
			if tokens > 0 {
				l.tokenEvents = append(l.tokenEvents, tokenEvent{at: now, tokens: tokens})
			}
			l.mu.Unlock()
			return
		}
		l.mu.Unlock()

		logger.Warn("[AI限速] 请求进入等待队列，%.1f 秒后继续", waitFor.Seconds())
		time.Sleep(waitFor)
	}
}

func (l *requestRateLimiter) pruneLocked(now time.Time) {
	cutoff := now.Add(-time.Minute)
	reqIdx := 0
	for reqIdx < len(l.requests) && l.requests[reqIdx].Before(cutoff) {
		reqIdx++
	}
	if reqIdx > 0 {
		l.requests = append([]time.Time(nil), l.requests[reqIdx:]...)
	}

	tokenIdx := 0
	for tokenIdx < len(l.tokenEvents) && l.tokenEvents[tokenIdx].at.Before(cutoff) {
		tokenIdx++
	}
	if tokenIdx > 0 {
		l.tokenEvents = append([]tokenEvent(nil), l.tokenEvents[tokenIdx:]...)
	}
}

func (l *requestRateLimiter) cooldownWaitLocked(now time.Time) time.Duration {
	if l.cooldownUntil.IsZero() {
		return 0
	}
	if !now.Before(l.cooldownUntil) {
		l.cooldownUntil = time.Time{}
		l.cooldownReason = ""
		return 0
	}
	return time.Until(l.cooldownUntil)
}

func (l *requestRateLimiter) tokenWaitLocked(now time.Time, limit, nextTokens int) time.Duration {
	total := nextTokens
	for _, event := range l.tokenEvents {
		total += event.tokens
	}
	if total <= limit {
		return 0
	}

	overflow := total - limit
	released := 0
	for _, event := range l.tokenEvents {
		released += event.tokens
		releaseAt := event.at.Add(time.Minute)
		if released >= overflow {
			if d := time.Until(releaseAt); d > 0 {
				return d
			}
			return 0
		}
	}

	return time.Second
}

func estimatePromptTokens(system, user string) int {
	runes := utf8.RuneCountInString(system) + utf8.RuneCountInString(user)
	estimated := runes/3 + 256
	if estimated < 256 {
		return 256
	}
	return estimated
}

func doJSONRequestWithRetry(client *http.Client, req *http.Request, retries int, parser func(int, []byte) (string, error)) (string, error) {
	var lastErr error
	for attempt := 0; attempt <= retries; attempt++ {
		reqCopy := req.Clone(req.Context())
		if req.GetBody != nil {
			body, err := req.GetBody()
			if err != nil {
				return "", err
			}
			reqCopy.Body = body
		}

		resp, err := client.Do(reqCopy)
		if err != nil {
			lastErr = err
		} else {
			data, readErr := io.ReadAll(resp.Body)
			resp.Body.Close()
			if readErr != nil {
				lastErr = readErr
			} else {
				content, parseErr := parser(resp.StatusCode, data)
				if parseErr == nil {
					return content, nil
				}
				lastErr = parseErr
				if rateLimitErr, ok := parseErr.(*aiRateLimitError); ok {
					globalRateLimiter.applyUpstreamCooldown(rateLimitErr.retryAfter, rateLimitErr.message)
					if attempt == retries {
						return "", parseErr
					}
					continue
				}
				if !shouldRetryStatus(resp.StatusCode) || attempt == retries {
					return "", parseErr
				}
				time.Sleep(retryDelay(resp.Header.Get("Retry-After"), attempt))
				continue
			}
		}

		if attempt == retries {
			break
		}
		time.Sleep(retryDelay("", attempt))
	}
	return "", lastErr
}

func parseOpenAIResponse(statusCode int, data []byte) (string, error) {
	body := strings.TrimSpace(string(data))
	if statusCode < 200 || statusCode >= 300 {
		if isRateLimitResponse(statusCode, body) {
			return "", &aiRateLimitError{
				status:     statusCode,
				message:    summarizeBody(body),
				retryAfter: inferRateLimitDelay(statusCode, body),
			}
		}
		return "", fmt.Errorf("openai status %d: %s", statusCode, summarizeBody(body))
	}

	var oResp openAIResponse
	if err := json.Unmarshal(data, &oResp); err == nil {
		if oResp.Error != nil {
			return "", fmt.Errorf("openai error: %s", oResp.Error.Message)
		}
		if len(oResp.Choices) == 0 {
			return "", fmt.Errorf("openai: empty choices")
		}
		return oResp.Choices[0].Message.Content, nil
	}

	var stringBody string
	if err := json.Unmarshal(data, &stringBody); err == nil && strings.TrimSpace(stringBody) != "" {
		return "", fmt.Errorf("openai returned string body: %s", summarizeBody(stringBody))
	}
	return "", fmt.Errorf("parsing openai response failed: %s", summarizeBody(body))
}

func parseGeminiResponse(statusCode int, data []byte) (string, error) {
	body := strings.TrimSpace(string(data))
	if statusCode < 200 || statusCode >= 300 {
		if isRateLimitResponse(statusCode, body) {
			return "", &aiRateLimitError{
				status:     statusCode,
				message:    summarizeBody(body),
				retryAfter: inferRateLimitDelay(statusCode, body),
			}
		}
		return "", fmt.Errorf("gemini status %d: %s", statusCode, summarizeBody(body))
	}

	var gResp geminiResponse
	if err := json.Unmarshal(data, &gResp); err != nil {
		return "", fmt.Errorf("parsing gemini response failed: %s", summarizeBody(body))
	}
	if gResp.Error != nil {
		return "", fmt.Errorf("gemini error: %s", gResp.Error.Message)
	}
	if len(gResp.Candidates) == 0 || len(gResp.Candidates[0].Content.Parts) == 0 {
		return "", fmt.Errorf("gemini: empty response")
	}
	return gResp.Candidates[0].Content.Parts[0].Text, nil
}

func shouldRetryStatus(statusCode int) bool {
	return statusCode == http.StatusTooManyRequests || statusCode >= 500
}

func retryDelay(retryAfter string, attempt int) time.Duration {
	if retryAfter != "" {
		if seconds, err := strconv.Atoi(strings.TrimSpace(retryAfter)); err == nil && seconds > 0 {
			return time.Duration(seconds) * time.Second
		}
		if ts, err := http.ParseTime(retryAfter); err == nil {
			if d := time.Until(ts); d > 0 {
				return d
			}
		}
	}
	return time.Duration(attempt+1) * 2 * time.Second
}

func summarizeBody(body string) string {
	body = strings.TrimSpace(body)
	if body == "" {
		return "empty body"
	}
	if len(body) > 240 {
		return body[:240] + "..."
	}
	return body
}

func (l *requestRateLimiter) applyUpstreamCooldown(delay time.Duration, reason string) {
	if delay <= 0 {
		delay = time.Minute
	}
	l.mu.Lock()
	defer l.mu.Unlock()

	until := time.Now().Add(delay)
	if until.After(l.cooldownUntil) {
		l.cooldownUntil = until
		l.cooldownReason = reason
		logger.Warn("[AI限速] 上游触发限速，后续请求排队等待 %.1f 秒: %s", delay.Seconds(), reason)
	}
}

func isRateLimitResponse(statusCode int, body string) bool {
	lower := strings.ToLower(body)
	if statusCode == http.StatusTooManyRequests {
		return true
	}
	if statusCode != http.StatusForbidden {
		return false
	}
	return strings.Contains(lower, "rpm limit exceeded") ||
		strings.Contains(lower, "rate limit") ||
		strings.Contains(lower, "requests per min") ||
		strings.Contains(lower, "too many requests")
}

func inferRateLimitDelay(statusCode int, body string) time.Duration {
	lower := strings.ToLower(body)
	if statusCode == http.StatusForbidden && strings.Contains(lower, "rpm limit exceeded") {
		return time.Minute
	}
	return 15 * time.Second
}
