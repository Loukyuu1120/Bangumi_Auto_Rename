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
	UUID        string     `json:"uuid"`
	Path        string     `json:"path"`
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
		tmdb: NewTMDBClient(c.APIKey),
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

	// ── 1. Determine media type ──────────────────────────────────────────────
	isAnime, isMovie := p.detectMediaType(srcPath, opts)
	rec.IsAnime = &isAnime
	rec.IsMovie = &isMovie

	// ── 2. Choose search query ────────────────────────────────────────────────
	searchName, year := p.chooseSearchQuery(srcPath, opts, rec)
	rec.Name = searchName
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
		}
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
		tmdbTitle = movie.Title
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
			return fmt.Errorf("TMDB未找到剧集: %q", searchName)
		}
		tmdbID = tv.ID
		tmdbTitle = tv.Name
		if len(tv.FirstAirDate) >= 4 {
			tmdbYear, _ = strconv.Atoi(tv.FirstAirDate[:4])
		}
		tmdbResult = tv
		rec.TMDBID = strconv.Itoa(tmdbID)
		rec.SeasonID = &seasonNum
		p.log.Info("[处理] 匹配剧集: %s (%d) 第%d季 [tmdb:%d]", tmdbTitle, tmdbYear, seasonNum, tmdbID)
	}

	// ── 5. Build rename mapping ────────────────────────────────────────────────
	renameMap, err := p.buildRenameMap(srcPath, opts, cfg, isAnime, isMovie, tmdbTitle, tmdbYear, tmdbID, tmdbResult, targetRoot)
	if err != nil {
		return fmt.Errorf("构建重命名映射失败: %w", err)
	}
	if len(renameMap) == 0 {
		return fmt.Errorf("无有效文件映射")
	}

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
			if tv, ok := tmdbResult.(*TMDBTVDetail); ok && len(targets) > 0 {
				workDir := filepath.Dir(filepath.Dir(targets[0]))
				scraper.ScrapeTV(workDir, tv)
				if rec.SeasonID != nil {
					seasonDir := filepath.Dir(targets[0])
					scraper.ScrapeSeason(workDir, *rec.SeasonID, tv, seasonDir)
					for _, ep := range tv.Seasons {
						if ep.SeasonNumber == *rec.SeasonID {
							for i, epFile := range targets {
								if i < len(ep.Episodes) {
									scraper.ScrapeEpisode(epFile, tv, *rec.SeasonID, ep.Episodes[i].EpisodeNumber)
								}
							}
							break
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
	return isAnime, isMovie
}

// ─────────────────────────────────────────────────────────────────────────────
// Search-query selection
// ─────────────────────────────────────────────────────────────────────────────

func (p *Processor) chooseSearchQuery(srcPath string, opts TaskOptions, rec *TaskRecord) (name string, year int) {
	if opts.CusName != "" {
		name, year = DivideByYear(opts.CusName)
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

	// Clean noise from the stem
	cleaned := CleanNoise(stem)

	// Check for embedded TMDB ID
	if embeddedID := ExtractTMDBID(srcPath); embeddedID != "" && opts.CusTMDBID == "" {
		opts.CusTMDBID = embeddedID
		rec.TMDBID = embeddedID
	}

	name, year = DivideByYear(cleaned)
	name = strings.TrimSpace(name)

	// Remove season markers from the name
	for _, p := range seasonPatterns {
		name = p.ReplaceAllString(name, "")
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

	return p.tmdb.GetMovieDetail(best.ID, "credits,external_ids,release_dates,images")
}

func (p *Processor) resolveTV(srcPath, name string, year, forceID int, opts TaskOptions) (*TMDBTVDetail, int, error) {
	seasonNum := 1
	if opts.CusSeasonID != nil && *opts.CusSeasonID > 0 {
		seasonNum = *opts.CusSeasonID
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
		detail, err = p.tmdb.GetTVDetail(forceID, "credits,external_ids,content_ratings,images")
		if err != nil {
			return nil, seasonNum, err
		}
	} else {
		results, searchErr := p.tmdb.SearchTV(name, year)
		if searchErr != nil {
			return nil, seasonNum, searchErr
		}
		if len(results) == 0 && year > 0 {
			results, searchErr = p.tmdb.SearchTV(name, 0)
			if searchErr != nil {
				return nil, seasonNum, searchErr
			}
		}
		if len(results) == 0 {
			// AI fallback
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

		detail, err = p.tmdb.GetTVDetail(best.ID, "credits,external_ids,content_ratings,images")
		if err != nil {
			return nil, seasonNum, err
		}
	}

	return detail, seasonNum, nil
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
) (map[string]string, error) {
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

		ctx := BuildRenderContext(tmdbTitle, "", tmdbYear, 0, 0, "", videoFormat, ext, strconv.Itoa(tmdbID))
		relPath := RenderTemplate(format, ctx)
		target := filepath.Join(targetRoot, filepath.FromSlash(relPath))
		result[srcPath] = target

		// Accompanying subtitle / NFO files
		p.addAccompanyingFiles(srcPath, filepath.Dir(target), stem, filepath.Base(target[:len(target)-len(ext)]), result)

		return result, nil
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

	ctx := BuildRenderContext(tmdbTitle, "", tmdbYear, season, episode, "", videoFormat, ext, strconv.Itoa(tmdbID))
	relPath := RenderTemplate(format, ctx)
	target := filepath.Join(targetRoot, filepath.FromSlash(relPath))
	result[srcPath] = target

	p.addAccompanyingFiles(srcPath, filepath.Dir(target), stem, filepath.Base(target[:len(target)-len(ext)]), result)

	return result, nil
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
