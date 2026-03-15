package rename

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"bangumi_auto_rename/internal/ai"
	"bangumi_auto_rename/internal/config"
	"bangumi_auto_rename/internal/logger"
)

// ─────────────────────────────────────────────────────────────────────────────
// Task & result types
// ─────────────────────────────────────────────────────────────────────────────

// TaskOptions carries per-task overrides that may differ from the global config.
type TaskOptions struct {
	IsAnime         *bool
	IsMovie         *bool
	UseAI           bool
	CusName         string // user-supplied search name override
	CusSeasonID     *int   // force a specific season number
	CusTMDBID       string // force a specific TMDB ID
	CusOffset       int    // episode number offset
	ConfigOverrides map[string]interface{}
}

// TaskStatus represents the processing state of a single file task.
type TaskStatus string

const (
	StatusPending    TaskStatus = "pending"
	StatusProcessing TaskStatus = "processing"
	StatusSuccess    TaskStatus = "success"
	StatusFailed     TaskStatus = "failed"
	StatusIgnored    TaskStatus = "ignored"
)

// TaskRecord is the persisted record for one processed file.
type TaskRecord struct {
	UUID string `json:"uuid"`
	Path string `json:"path"`
	// TargetPath is kept for backwards-compatibility with Python-generated records
	// (which used a singular "target_path" string).  New records use TargetPaths.
	TargetPath  string     `json:"target_path,omitempty"`
	Name        string     `json:"name"`
	Status      TaskStatus `json:"status"`
	ErrMsg      string     `json:"error,omitempty"`
	IsAnime     *bool      `json:"is_anime"`
	IsMovie     *bool      `json:"is_movie"`
	SeasonID    *int       `json:"season_id"`
	EpisodeID   int        `json:"episode_id"`
	TMDBID      string     `json:"tmdb_id,omitempty"`
	UseAI       bool       `json:"use_ai"`
	Offset      int        `json:"episode_offset"`
	TargetPaths []string   `json:"target_paths,omitempty"`
	ProcessedAt string     `json:"processed_at,omitempty"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Processor
// ─────────────────────────────────────────────────────────────────────────────

// Processor orchestrates the full rename pipeline for a single media file.
type Processor struct {
	cfg  *config.Manager
	tmdb *TMDBClient
	ai   *ai.Client
	log  *logger.Logger
}

// NewProcessor creates a Processor using the global config manager.
func NewProcessor(cfg *config.Manager) *Processor {
	c := cfg.GetConfig()
	return &Processor{
		cfg:  cfg,
		tmdb: NewTMDBClient(c.APIKey, c.TitleLanguages, c.OverviewLanguages),
		ai:   ai.New(cfg),
		log:  logger.Get(),
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Public entry point
// ─────────────────────────────────────────────────────────────────────────────

// Process is the main entry point.  It determines media type, queries TMDB,
// builds the target path and performs the file operation (hardlink / copy /
// move / symlink).
//
// Returns a TaskRecord describing the outcome.
func (p *Processor) Process(srcPath string, opts TaskOptions, uuid string) *TaskRecord {
	record := &TaskRecord{
		UUID:        uuid,
		Path:        srcPath,
		Status:      StatusProcessing,
		IsAnime:     opts.IsAnime,
		IsMovie:     opts.IsMovie,
		UseAI:       opts.UseAI,
		Offset:      opts.CusOffset,
		TMDBID:      opts.CusTMDBID,
		ProcessedAt: time.Now().Format("2006-01-02 15:04:05"),
	}
	if opts.CusSeasonID != nil {
		record.SeasonID = opts.CusSeasonID
	}

	p.log.Info("[处理] 开始处理: %s", srcPath)

	if err := p.process(srcPath, opts, record); err != nil {
		p.log.Error("[处理] 失败: %s — %v", srcPath, err)
		record.Status = StatusFailed
		record.ErrMsg = err.Error()
	}

	return record
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal pipeline
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) process(srcPath string, opts TaskOptions, rec *TaskRecord) error {
	cfg := p.cfg.GetConfig()

	if opts.CusName != "" {
		p.log.Info("[处理] 使用任务覆盖标题: %q", opts.CusName)
	}
	if opts.CusTMDBID != "" {
		p.log.Info("[处理] 使用强制 TMDB ID: %s", opts.CusTMDBID)
	} else {
		p.log.Info("[处理] 本次未指定强制 TMDB ID，将走正常搜索流程")
	}

	// Pick up embedded TMDB ID from path if present.
	if opts.CusTMDBID == "" {
		if embeddedID := extractTMDBIDFromPath(srcPath); embeddedID != "" {
			opts.CusTMDBID = embeddedID
			rec.TMDBID = embeddedID
			p.log.Info("[处理] 使用路径内 TMDB ID: %s", embeddedID)
		}
	}

	// If TMDB ID is provided, infer movie vs TV by querying TMDB directly.
	if opts.CusTMDBID != "" {
		if id, err := strconv.Atoi(opts.CusTMDBID); err == nil && id > 0 {
			if movie, err := p.tmdb.GetMovieDetail(id, "external_ids"); err == nil && movie != nil {
				b := true
				opts.IsMovie = &b
			} else if tv, err := p.tmdb.GetTVDetail(id, "external_ids"); err == nil && tv != nil {
				b := false
				opts.IsMovie = &b
			} else if opts.IsMovie == nil && opts.IsAnime == nil {
				// Fallback heuristic when TMDB lookup fails
				stem := strings.TrimSuffix(filepath.Base(srcPath), filepath.Ext(srcPath))
				ep := ExtractEpisode(stem)
				isMovieGuess := true
				if ep.Found {
					isMovieGuess = false
				} else {
					if _, ok := ExtractSeason(stem); ok {
						isMovieGuess = false
					} else {
						parent := filepath.Base(filepath.Dir(srcPath))
						if _, ok := ExtractSeason(parent); ok || IsSeasonName(parent) {
							isMovieGuess = false
						}
					}
				}
				opts.IsMovie = &isMovieGuess
			}
		}
	}

	// ── 1. Determine media type ──────────────────────────────────────────────
	isAnime, isMovie := p.detectMediaType(srcPath, opts)
	// If media type wasn't explicitly set, use episode markers to bias to TV.
	if opts.IsMovie == nil {
		stem := strings.TrimSuffix(filepath.Base(srcPath), filepath.Ext(srcPath))
		if ep := ExtractEpisode(stem); ep.Found {
			isMovie = false
		}
	}
	rec.IsAnime = &isAnime
	rec.IsMovie = &isMovie

	// ── 2. Choose search query ────────────────────────────────────────────────
	searchName, year := p.chooseSearchQuery(srcPath, opts, rec)
	if strings.TrimSpace(opts.CusName) != "" {
		rec.Name = searchName
	}
	p.log.Info("[处理] 搜索词: %q  年份: %d  动画: %v  电影: %v", searchName, year, isAnime, isMovie)

	// ── 3. Determine target root directory ────────────────────────────────────
	targetRoot := p.targetRootDir(cfg, isAnime, isMovie)
	if targetRoot == "" {
		return fmt.Errorf("目标目录未配置 (is_anime=%v, is_movie=%v)", isAnime, isMovie)
	}

	// ── 4. Look up TMDB ───────────────────────────────────────────────────────
	var tmdbResult interface{} // *TMDBTVDetail or *TMDBMovieDetail
	var tmdbID int
	var tmdbTitle string
	var tmdbYear int

	if opts.CusTMDBID != "" {
		id, err := strconv.Atoi(opts.CusTMDBID)
		if err == nil {
			tmdbID = id
			p.log.Info("[处理] 解析强制 TMDB ID 成功: %d", tmdbID)
		} else {
			p.log.Warn("[处理] 强制 TMDB ID 无法解析为数字: %q", opts.CusTMDBID)
		}
	} else {
		p.log.Info("[处理] 未携带强制 TMDB ID，后续将依赖搜索结果")
	}

	if isMovie {
		movie, err := p.resolveMovie(searchName, year, tmdbID)
		if err != nil {
			return fmt.Errorf("TMDB电影查询失败: %w", err)
		}
		if movie == nil {
			return fmt.Errorf("TMDB未找到电影: %q", searchName)
		}
		tmdbID = movie.ID
		tmdbTitle = preferredMovieTitle(movie)
		if len(movie.ReleaseDate) >= 4 {
			tmdbYear, _ = strconv.Atoi(movie.ReleaseDate[:4])
		}
		tmdbResult = movie
		rec.TMDBID = strconv.Itoa(tmdbID)
		p.log.Info("[处理] 匹配电影: %s (%d) [tmdb:%d]", tmdbTitle, tmdbYear, tmdbID)
	} else {
		tv, seasonNum, err := p.resolveTV(srcPath, searchName, year, tmdbID, opts)
		if err != nil {
			return fmt.Errorf("TMDB剧集查询失败: %w", err)
		}
		if tv == nil {
			p.log.Info("[处理] TV 未命中，尝试电影兜底: %q", searchName)
			movie, movieErr := p.resolveMovie(searchName, year, tmdbID)
			if movieErr != nil {
				return fmt.Errorf("TMDB电影兜底查询失败: %w", movieErr)
			}
			if movie == nil {
				return fmt.Errorf("TMDB未找到剧集: %q", searchName)
			}

			isMovie = true
			rec.IsMovie = &isMovie
			rec.SeasonID = nil

			tmdbID = movie.ID
			tmdbTitle = preferredMovieTitle(movie)
			if len(movie.ReleaseDate) >= 4 {
				tmdbYear, _ = strconv.Atoi(movie.ReleaseDate[:4])
			}
			tmdbResult = movie
			rec.TMDBID = strconv.Itoa(tmdbID)
			targetRoot = p.targetRootDir(cfg, isAnime, isMovie)
			if targetRoot == "" {
				return fmt.Errorf("目标目录未配置 (is_anime=%v, is_movie=%v)", isAnime, isMovie)
			}
			p.log.Info("[处理] TV 自动识别未命中，改为匹配电影: %s (%d) [tmdb:%d]", tmdbTitle, tmdbYear, tmdbID)
		} else {
			tmdbID = tv.ID
			tmdbTitle = preferredTVTitle(tv)
			if len(tv.FirstAirDate) >= 4 {
				tmdbYear, _ = strconv.Atoi(tv.FirstAirDate[:4])
			}
			tmdbResult = tv
			rec.TMDBID = strconv.Itoa(tmdbID)
			rec.SeasonID = &seasonNum
			p.log.Info("[处理] 匹配剧集: %s (%d) 第%d季 [tmdb:%d]", tmdbTitle, tmdbYear, seasonNum, tmdbID)
		}
	}

	// Back-fill the display name with the TMDB-identified title when no custom
	// name was provided — otherwise the task list would show "—".
	if strings.TrimSpace(rec.Name) == "" && tmdbTitle != "" {
		rec.Name = tmdbTitle
	}

	// ── 5. Build rename mapping ────────────────────────────────────────────────
	renameMap, episodeNum, err := p.buildRenameMap(srcPath, opts, cfg, isAnime, isMovie, tmdbTitle, tmdbYear, tmdbID, tmdbResult, targetRoot)
	if err != nil {
		return fmt.Errorf("构建重命名映射失败: %w", err)
	}
	if len(renameMap) == 0 {
		return fmt.Errorf("无有效文件映射")
	}
	rec.EpisodeID = episodeNum

	// ── 6. Apply file operations ──────────────────────────────────────────────
	targets, err := p.applyFileOps(renameMap, cfg, opts.ConfigOverrides)
	if err != nil {
		return err
	}

	for _, t := range targets {
		rec.TargetPaths = append(rec.TargetPaths, t)
	}

	// ── 7. Scrape metadata (optional) ─────────────────────────────────────────
	scrape := cfg.ScrapeMetadata
	if v, ok := opts.ConfigOverrides["scrape_metadata"]; ok {
		if b, ok := v.(bool); ok {
			scrape = b
		}
	}
	if scrape {
		scraper := NewScraper(cfg.APIKey, cfg.ScrapeImageTypes)
		if isMovie {
			if movie, ok := tmdbResult.(*TMDBMovieDetail); ok && len(targets) > 0 {
				scraper.ScrapeMovie(targets[0], movie)
			}
		} else {
			if tv, ok := tmdbResult.(*TMDBTVDetail); ok && len(targets) > 0 && rec.SeasonID != nil {
				workDir := filepath.Dir(filepath.Dir(targets[0]))
				scraper.ScrapeTV(workDir, tv)

				seasonNum := *rec.SeasonID
				seasonDir := filepath.Dir(targets[0])

				// 获取完整的季信息（包含集数列表）
				seasonDetail, err := p.tmdb.GetSeasonDetail(tv.ID, seasonNum)
				if err != nil {
					p.log.Warn("[刮削] 获取第 %d 季详情失败: %v", seasonNum, err)
				} else {
					// 更新 TV 详情中的季信息
					for i := range tv.Seasons {
						if tv.Seasons[i].SeasonNumber == seasonNum {
							tv.Seasons[i].Episodes = seasonDetail.Episodes
							break
						}
					}
				}

				scraper.ScrapeSeason(workDir, seasonNum, tv, seasonDir)

				// 刮削每一集（只处理视频文件，使用正确集数）
				if seasonDetail != nil && len(seasonDetail.Episodes) > 0 {
					for _, targetFile := range targets {
						if IsVideoFile(targetFile) {
							scraper.ScrapeEpisode(targetFile, tv, seasonNum, episodeNum)
						}
					}
				}
			}
		}
	}

	rec.Status = StatusSuccess
	p.log.Info("[处理] 完成: %s → %v", srcPath, targets)
	return nil
}

func extractTMDBIDFromPath(path string) string {
	if id := ExtractTMDBID(path); id != "" {
		return id
	}
	parts := strings.Split(path, string(filepath.Separator))
	for _, p := range parts {
		if id := ExtractTMDBID(p); id != "" {
			return id
		}
	}
	return ""
}

// ─────────────────────────────────────────────────────────────────────────────
// Media-type detection
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) detectMediaType(srcPath string, opts TaskOptions) (isAnime bool, isMovie bool) {
	if opts.IsMovie != nil {
		isMovie = *opts.IsMovie
	}
	if opts.IsAnime != nil {
		isAnime = *opts.IsAnime
	}
	// If unspecified, infer TV when episode markers exist.
	stem := strings.TrimSuffix(filepath.Base(srcPath), filepath.Ext(srcPath))
	parent := filepath.Base(filepath.Dir(srcPath))
	hasEpisodeMarkers := false
	if ep := ExtractEpisode(stem); ep.Found {
		hasEpisodeMarkers = true
	} else if _, ok := ExtractSeason(stem); ok {
		hasEpisodeMarkers = true
	} else if _, ok := ExtractSeason(parent); ok || IsSeasonName(parent) {
		hasEpisodeMarkers = true
	}
	if hasEpisodeMarkers {
		isMovie = false
	}
	return isAnime, isMovie
}

// ─────────────────────────────────────────────────────────────────────────────
// Search-query selection
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) chooseSearchQuery(srcPath string, opts TaskOptions, rec *TaskRecord) (name string, year int) {
	if strings.TrimSpace(opts.CusName) != "" {
		name, year = ParseSearchName(opts.CusName)
		name = strings.Join(strings.Fields(name), " ")
		return name, year
	}

	// Try to derive name from path
	stem := strings.TrimSuffix(filepath.Base(srcPath), filepath.Ext(srcPath))

	// If the stem looks like a pure episode marker, try the parent directory
	if IsWeakFilename(stem) {
		parent := filepath.Base(filepath.Dir(srcPath))
		if IsSeasonName(parent) {
			parent = filepath.Base(filepath.Dir(filepath.Dir(srcPath)))
		}
		stem = parent
	}

	// Parse filename into a clean search name (Python-compatible)
	cleaned, year := ParseSearchName(stem)
	name = strings.TrimSpace(cleaned)

	// If name is still weak or empty, fall back to parent / grandparent
	if name == "" || IsWeakFilename(name) || len([]rune(name)) < 2 {
		parent := filepath.Dir(srcPath)
		if parent != "." && parent != string(filepath.Separator) {
			pname := filepath.Base(parent)
			if IsSeasonName(pname) {
				grand := filepath.Dir(parent)
				if grand != "." && grand != string(filepath.Separator) {
					pname = filepath.Base(grand)
				}
			}
			fallbackName, fallbackYear := ParseSearchName(pname)
			if fallbackName != "" {
				name = fallbackName
			}
			if year == 0 && fallbackYear > 0 {
				year = fallbackYear
			}
		}
	}

	name = strings.TrimSpace(strings.NewReplacer("-", " ", "_", " ").Replace(name))
	name = strings.Join(strings.Fields(name), " ")

	return name, year
}

// ─────────────────────────────────────────────────────────────────────────────
// Target directory selection
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) targetRootDir(cfg config.Config, isAnime, isMovie bool) string {
	switch {
	case isAnime && isMovie:
		if cfg.AnimeMoviePath != "" {
			return cfg.AnimeMoviePath
		}
		if cfg.AnimePath != "" {
			return cfg.AnimePath
		}
		return cfg.MoviePath
	case isAnime:
		if cfg.AnimePath != "" {
			return cfg.AnimePath
		}
		return cfg.BangumiPath
	case isMovie:
		return cfg.MoviePath
	default:
		return cfg.BangumiPath
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// TMDB resolution helpers
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) resolveMovie(name string, year, forceID int) (*TMDBMovieDetail, error) {
	if forceID > 0 {
		return p.tmdb.GetMovieDetail(forceID, "credits,external_ids,release_dates,images")
	}

	results, err := p.tmdb.SearchMovie(name, year)
	if err != nil {
		return nil, err
	}
	if len(results) == 0 {
		// Retry without year
		if year > 0 {
			results, err = p.tmdb.SearchMovie(name, 0)
			if err != nil || len(results) == 0 {
				return nil, err
			}
		} else {
			return nil, nil
		}
	}

	// Pick best using heuristics (and AI if available)
	best := SelectBestMovie(results, name, year)
	if p.ai.IsAvailable() && len(results) > 1 {
		candidates := resultsToMaps(results, true)
		idx := p.ai.SelectBestTMDBMatch(name, year, candidates, true)
		if idx >= 0 && idx < len(results) {
			best = &results[idx]
		}
	}
	if best == nil {
		return nil, nil
	}

	detail, err := p.tmdb.GetMovieDetail(best.ID, "credits,external_ids,release_dates,images")
	if err != nil {
		return nil, err
	}
	if detail == nil {
		return nil, nil
	}
	if year > 0 && len(detail.ReleaseDate) >= 4 {
		if y, err := strconv.Atoi(detail.ReleaseDate[:4]); err == nil {
			if y > 0 && absInt(y-year) > 1 {
				return nil, nil
			}
		}
	}
	return detail, nil
}

func absInt(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

func (p *Processor) resolveTV(srcPath, name string, year, forceID int, opts TaskOptions) (*TMDBTVDetail, int, error) {
	p.log.Info("[处理] resolveTV: name=%q year=%d forceID=%d cusName=%q cusTMDBID=%q", name, year, forceID, opts.CusName, opts.CusTMDBID)

	seasonNum := 1
	if opts.CusSeasonID != nil && *opts.CusSeasonID > 0 {
		seasonNum = *opts.CusSeasonID
		p.log.Info("[处理] 使用强制季号: %d", seasonNum)
	} else {
		// Try to detect season from path
		dir := filepath.Dir(srcPath)
		if s, ok := ExtractSeason(filepath.Base(dir)); ok {
			seasonNum = s
		} else if s, ok := ExtractSeason(name); ok {
			seasonNum = s
		}
	}

	var detail *TMDBTVDetail
	var err error

	if forceID > 0 {
		p.log.Info("[处理] 跳过 TV 搜索，直接使用强制 TMDB ID 获取详情: %d", forceID)
		detail, err = p.tmdb.GetTVDetail(forceID, "credits,external_ids,content_ratings,images")
		if err != nil {
			return nil, seasonNum, err
		}
	} else {
		p.log.Info("[处理] 未指定强制 TMDB ID，开始搜索 TV: %q", name)
		// ── API search ───────────────────────────────────────────────────────
		// SearchTV internally tries zh-CN+year, zh-CN+0, en-US+year, en-US+0
		// and returns on the first non-empty page, so a single call is enough.
		results, searchErr := p.tmdb.SearchTV(name, year)
		if searchErr != nil {
			return nil, seasonNum, searchErr
		}
		p.log.Info("[处理] TV API 搜索返回 %d 条结果", len(results))

		if year > 0 && len(results) > 0 {
			results = filterResultsByYear(results, year, false)
			p.log.Info("[处理] 年份过滤后剩余 %d 条 TV 结果", len(results))
		}

		// ── Web-scraping fallback (mirrors Python's _search_tmdb_web) ────────
		// Two trigger conditions (matching Python's behaviour):
		//   1. API returned zero results.
		//   2. API returned results, but filterResultsByYear found no year-
		//      appropriate candidates and silently fell back to the original
		//      list (e.g. only returning old shows like "K" from 2012 when we
		//      need a 2025 show).  In that case len(results)>0 but none of the
		//      candidates are anywhere near the target year, so we must not
		//      accept them without first trying the web search.
		needsWebFallback := len(results) == 0 ||
			(year > 0 && len(results) > 0 && !hasYearAppropriateResult(results, year, false, 3))
		p.log.Info("[处理] TV 网页兜底判定: needsWebFallback=%v", needsWebFallback)

		if needsWebFallback {
			p.log.Info("[处理] API搜索未找到合适年份结果 %q，尝试网页兜底搜索...", name)
			if webResults, webErr := p.tmdb.SearchTMDBWeb(name, "tv"); webErr == nil && len(webResults) > 0 {
				p.log.Info("[处理] 网页搜索返回 %d 条结果", len(webResults))
				filtered := webResults
				if year > 0 {
					filtered = filterResultsByYear(webResults, year, false)
					if len(filtered) == 0 {
						filtered = webResults // keep unfiltered web results rather than nothing
					}
				}
				results = filtered
			} else if webErr != nil {
				p.log.Warn("[处理] 网页兜底搜索失败: %v", webErr)
				// results unchanged — proceed with whatever the API gave us
			}
			// If web scraping also returned nothing, results stays as-is
			// (either the API's stale list or empty); the AI fallback below
			// will handle the empty case.
		}

		// ── AI fallback ──────────────────────────────────────────────────────
		if len(results) == 0 {
			if p.ai.IsAvailable() {
				p.log.Info("[处理] TMDB未找到 %q，尝试AI推断...", name)
				ctx := map[string]interface{}{
					"folder_name": name,
					"full_path":   srcPath,
					"file_names":  []string{filepath.Base(srcPath)},
				}
				meta := p.ai.AnalyzeMetadata(ctx)
				if meta != nil && meta.Name != "" {
					results, _ = p.tmdb.SearchTV(meta.Name, meta.Year)
					if len(results) == 0 && meta.Year > 0 {
						results, _ = p.tmdb.SearchTV(meta.Name, 0)
					}
				}
			}
			if len(results) == 0 {
				return nil, seasonNum, nil
			}
		}

		best := SelectBestTV(results, name, year)
		if p.ai.IsAvailable() && len(results) > 1 {
			candidates := resultsToMaps(results, false)
			idx := p.ai.SelectBestTMDBMatch(name, year, candidates, false)
			if idx >= 0 && idx < len(results) {
				best = &results[idx]
			}
		}
		if best == nil {
			return nil, seasonNum, nil
		}

		p.log.Info("[处理] TV 最佳匹配候选: id=%d name=%q first_air_date=%q", best.ID, best.Name, best.FirstAirDate)
		detail, err = p.tmdb.GetTVDetail(best.ID, "credits,external_ids,content_ratings,images")
		if err != nil {
			return nil, seasonNum, err
		}
	}

	// ── Year sanity check ────────────────────────────────────────────────────
	// Python never hard-rejects on year; it only adjusts scores.  We use a
	// generous ±3-year window so that:
	//   • Multi-season shows (S1 aired years ago) are not rejected.
	//   • Shows whose TMDB first_air_date differs slightly from the filename
	//     year (e.g. cross-year premiere) are not dropped.
	if detail != nil && year > 0 && len(detail.FirstAirDate) >= 4 {
		if y, err := strconv.Atoi(detail.FirstAirDate[:4]); err == nil {
			if y > 0 && absInt(y-year) > 3 {
				p.log.Warn("[处理] 年份差异过大 (TMDB=%d, 文件=%d)，跳过: %s", y, year, detail.Name)
				return nil, seasonNum, nil
			}
		}
	}

	return detail, seasonNum, nil
}

// hasYearAppropriateResult reports whether any result in the slice has an
// air/release date within ±tolerance years of the target year.
// isMovie selects ReleaseDate vs FirstAirDate.
func hasYearAppropriateResult(results []TMDBSearchResult, year int, isMovie bool, tolerance int) bool {
	if year <= 0 {
		return len(results) > 0
	}
	for _, r := range results {
		dateStr := r.FirstAirDate
		if isMovie {
			dateStr = r.ReleaseDate
		}
		if len(dateStr) < 4 {
			continue
		}
		var y int
		fmt.Sscanf(dateStr[:4], "%d", &y)
		if y > 0 && absInt(y-year) <= tolerance {
			return true
		}
	}
	return false
}

func filterResultsByYear(results []TMDBSearchResult, year int, isMovie bool) []TMDBSearchResult {
	if year <= 0 {
		return results
	}
	var out []TMDBSearchResult
	for _, r := range results {
		dateStr := r.FirstAirDate
		if isMovie {
			dateStr = r.ReleaseDate
		}
		if len(dateStr) < 4 {
			continue
		}
		var y int
		fmt.Sscanf(dateStr[:4], "%d", &y)
		if y > 0 && absInt(y-year) <= 1 {
			out = append(out, r)
		}
	}
	if len(out) > 0 {
		return out
	}
	return results
}

// ─────────────────────────────────────────────────────────────────────────────
// Rename-map construction
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) buildRenameMap(
	srcPath string,
	opts TaskOptions,
	cfg config.Config,
	isAnime, isMovie bool,
	tmdbTitle string,
	tmdbYear int,
	tmdbID int,
	tmdbResult interface{},
	targetRoot string,
) (map[string]string, int, error) {
	result := make(map[string]string)

	ext := strings.ToLower(filepath.Ext(srcPath))
	stem := strings.TrimSuffix(filepath.Base(srcPath), filepath.Ext(srcPath))

	mediaInfo := ExtractMediaInfo(filepath.Base(srcPath))
	videoFormat := mediaInfo.Resolution

	if isMovie {
		format := cfg.MovieRenameFormat
		if v, ok := opts.ConfigOverrides["movie_rename_format"]; ok {
			if s, ok := v.(string); ok && s != "" {
				format = s
			}
		}

		ctx := BuildRenderContext(tmdbTitle, "", tmdbYear, 0, 0, "", videoFormat, ext, strconv.Itoa(tmdbID), mediaInfo)
		relPath := RenderTemplate(format, ctx)

		// 应用二级分类
		category := GetCategoryFolder(tmdbResult, isMovie, isAnime, cfg)
		var target string
		if category != "" {
			target = filepath.Join(targetRoot, category, filepath.FromSlash(relPath))
		} else {
			target = filepath.Join(targetRoot, filepath.FromSlash(relPath))
		}
		result[srcPath] = target

		// Accompanying subtitle / NFO files
		p.addAccompanyingFiles(srcPath, filepath.Dir(target), stem, filepath.Base(target[:len(target)-len(ext)]), result)

		return result, 0, nil
	}

	// TV show
	ep := ExtractEpisode(stem)
	episode := ep.Episode + opts.CusOffset
	if episode < 0 {
		episode = 0
	}

	season := 1
	if opts.CusSeasonID != nil && *opts.CusSeasonID > 0 {
		season = *opts.CusSeasonID
	} else if ep.Season > 0 {
		season = ep.Season
	} else if tv, ok := tmdbResult.(*TMDBTVDetail); ok {
		// Detect season from path
		dir := filepath.Dir(srcPath)
		if s, ok := ExtractSeason(filepath.Base(dir)); ok {
			season = s
		} else if s, ok := ExtractSeason(tv.Name); ok {
			season = s
		}
	}

	format := cfg.TVRenameFormat
	if v, ok := opts.ConfigOverrides["tv_rename_format"]; ok {
		if s, ok := v.(string); ok && s != "" {
			format = s
		}
	}

	ctx := BuildRenderContext(tmdbTitle, "", tmdbYear, season, episode, "", videoFormat, ext, strconv.Itoa(tmdbID), mediaInfo)
	relPath := RenderTemplate(format, ctx)

	// 应用二级分类
	category := GetCategoryFolder(tmdbResult, isMovie, isAnime, cfg)
	var target string
	if category != "" {
		target = filepath.Join(targetRoot, category, filepath.FromSlash(relPath))
	} else {
		target = filepath.Join(targetRoot, filepath.FromSlash(relPath))
	}
	result[srcPath] = target

	p.addAccompanyingFiles(srcPath, filepath.Dir(target), stem, filepath.Base(target[:len(target)-len(ext)]), result)

	return result, episode, nil
}

// addAccompanyingFiles appends subtitle / NFO files that share the same stem
// as the video into the rename map.
func (p *Processor) addAccompanyingFiles(srcPath, targetDir, srcStem, targetStem string, out map[string]string) {
	dir := filepath.Dir(srcPath)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		ext := strings.ToLower(filepath.Ext(name))
		base := strings.TrimSuffix(name, filepath.Ext(name))

		if base != srcStem {
			continue
		}
		if !SubtitleSuffix[ext] && ext != ".nfo" {
			continue
		}

		src := filepath.Join(dir, name)
		dst := filepath.Join(targetDir, targetStem+ext)
		out[src] = dst
	}
}

func preferredTVTitle(tv *TMDBTVDetail) string {
	if tv == nil {
		return ""
	}

	name := strings.TrimSpace(tv.Name)
	if name != "" {
		return name
	}

	return strings.TrimSpace(tv.OriginalName)
}

func preferredMovieTitle(movie *TMDBMovieDetail) string {
	if movie == nil {
		return ""
	}

	title := strings.TrimSpace(movie.Title)
	if title != "" {
		return title
	}

	return strings.TrimSpace(movie.OriginalTitle)
}

// ─────────────────────────────────────────────────────────────────────────────
// File operations
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) applyFileOps(renameMap map[string]string, cfg config.Config, overrides map[string]interface{}) ([]string, error) {
	mode := cfg.Mode
	if v, ok := overrides["mode"]; ok {
		if s, ok := v.(string); ok && s != "" {
			mode = s
		}
	}
	overwriteMode := cfg.OverwriteMode
	if v, ok := overrides["overwrite_mode"]; ok {
		if s, ok := v.(string); ok && s != "" {
			overwriteMode = s
		}
	}

	var targets []string

	for src, dst := range renameMap {
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return targets, fmt.Errorf("创建目录失败 %s: %w", filepath.Dir(dst), err)
		}

		// Handle existing target
		if _, err := os.Stat(dst); err == nil {
			switch overwriteMode {
			case "从不覆盖":
				p.log.Warn("[文件操作] 跳过已存在文件: %s", dst)
				targets = append(targets, dst)
				continue
			case "总是覆盖":
				p.log.Info("[文件操作] 覆盖已存在文件: %s", dst)
				_ = os.Remove(dst)
			case "保留最新":
				srcStat, e1 := os.Stat(src)
				dstStat, e2 := os.Stat(dst)
				if e1 == nil && e2 == nil && srcStat.ModTime().After(dstStat.ModTime()) {
					p.log.Info("[文件操作] 源文件更新，覆盖: %s", dst)
					_ = os.Remove(dst)
				} else {
					p.log.Warn("[文件操作] 目标文件较新，跳过: %s", dst)
					targets = append(targets, dst)
					continue
				}
			}
		}

		var opErr error
		switch mode {
		case "硬链接", "链接":
			opErr = os.Link(src, dst)
			if opErr != nil {
				p.log.Warn("[文件操作] 硬链接失败，回退软链接: %v", opErr)
				opErr = os.Symlink(src, dst)
			}
		case "软链接":
			opErr = os.Symlink(src, dst)
		case "复制":
			opErr = copyFile(src, dst)
		case "剪切":
			opErr = os.Rename(src, dst)
			if opErr != nil {
				// cross-device move fallback
				opErr = copyFile(src, dst)
				if opErr == nil {
					_ = os.Remove(src)
				}
			}
		default:
			// Default to hardlink
			opErr = os.Link(src, dst)
			if opErr != nil {
				opErr = os.Symlink(src, dst)
			}
		}

		if opErr != nil {
			p.log.Error("[文件操作] %s → %s 失败: %v", src, dst, opErr)
			return targets, fmt.Errorf("文件操作失败 (%s): %w", mode, opErr)
		}

		p.log.Info("[文件操作] %s → %s [%s]", filepath.Base(src), filepath.Base(dst), mode)
		targets = append(targets, dst)
	}

	return targets, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Utility helpers
// ─────────────────────────────────────────────────────────────────────────────

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	buf := make([]byte, 4*1024*1024) // 4 MB buffer
	for {
		n, err := in.Read(buf)
		if n > 0 {
			if _, werr := out.Write(buf[:n]); werr != nil {
				return werr
			}
		}
		if err != nil {
			if err.Error() == "EOF" {
				break
			}
			return err
		}
	}
	return out.Sync()
}

// resultsToMaps converts TMDBSearchResult slice to []map[string]interface{}
// for passing to the AI client.
func resultsToMaps(results []TMDBSearchResult, isMovie bool) []map[string]interface{} {
	out := make([]map[string]interface{}, len(results))
	for i, r := range results {
		m := map[string]interface{}{
			"id":       r.ID,
			"overview": r.Overview,
		}
		if isMovie {
			m["title"] = r.Title
			m["original_title"] = r.OriginalTitle
			m["release_date"] = r.ReleaseDate
		} else {
			m["name"] = r.Name
			m["original_name"] = r.OriginalName
			m["first_air_date"] = r.FirstAirDate
		}
		out[i] = m
	}
	return out
}
