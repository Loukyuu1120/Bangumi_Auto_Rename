package rename

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// TMDB data models
// ─────────────────────────────────────────────────────────────────────────────

// TMDBSearchResult holds a single item from a TMDB search response.
type TMDBSearchResult struct {
	ID               int      `json:"id"`
	Title            string   `json:"title"`          // movie
	Name             string   `json:"name"`           // tv
	OriginalTitle    string   `json:"original_title"` // movie
	OriginalName     string   `json:"original_name"`  // tv
	ReleaseDate      string   `json:"release_date"`   // movie
	FirstAirDate     string   `json:"first_air_date"` // tv
	Overview         string   `json:"overview"`
	PosterPath       string   `json:"poster_path"`
	BackdropPath     string   `json:"backdrop_path"`
	MediaType        string   `json:"media_type,omitempty"`
	GenreIDs         []int    `json:"genre_ids"`
	OriginCountry    []string `json:"origin_country"`
	OriginalLanguage string   `json:"original_language"`
}

// DisplayTitle returns whichever title field is populated.
func (r TMDBSearchResult) DisplayTitle() string {
	if r.Title != "" {
		return r.Title
	}
	return r.Name
}

// DisplayYear returns the 4-character year portion of the air/release date.
func (r TMDBSearchResult) DisplayYear() string {
	d := r.ReleaseDate
	if d == "" {
		d = r.FirstAirDate
	}
	if len(d) >= 4 {
		return d[:4]
	}
	return ""
}

// TMDBSearchResponse is the top-level envelope returned by /search/tv and /search/movie.
type TMDBSearchResponse struct {
	Results    []TMDBSearchResult `json:"results"`
	TotalPages int                `json:"total_pages"`
}

// TMDBSeason holds season-level metadata.
type TMDBSeason struct {
	ID           int           `json:"id"`
	SeasonNumber int           `json:"season_number"`
	Name         string        `json:"name"`
	AirDate      string        `json:"air_date"`
	Overview     string        `json:"overview"`
	PosterPath   string        `json:"poster_path"`
	Episodes     []TMDBEpisode `json:"episodes"`
}

// TMDBEpisode holds episode-level metadata.
type TMDBEpisode struct {
	ID            int     `json:"id"`
	Name          string  `json:"name"`
	Overview      string  `json:"overview"`
	EpisodeNumber int     `json:"episode_number"`
	SeasonNumber  int     `json:"season_number"`
	AirDate       string  `json:"air_date"`
	StillPath     string  `json:"still_path"`
	VoteAverage   float64 `json:"vote_average"`
}

// TMDBTVDetail holds full TV show details including all season stubs.
type TMDBTVDetail struct {
	ID               int          `json:"id"`
	Name             string       `json:"name"`
	OriginalName     string       `json:"original_name"`
	Overview         string       `json:"overview"`
	FirstAirDate     string       `json:"first_air_date"`
	NumberOfSeasons  int          `json:"number_of_seasons"`
	NumberOfEpisodes int          `json:"number_of_episodes"`
	Seasons          []TMDBSeason `json:"seasons"`
	Genres           []TMDBGenre  `json:"genres"`
	Networks         []struct {
		Name string `json:"name"`
	} `json:"networks"`
	OriginCountry    []string `json:"origin_country"`
	OriginalLanguage string   `json:"original_language"`
	PosterPath       string   `json:"poster_path"`
	BackdropPath     string   `json:"backdrop_path"`
	ExternalIDs      struct {
		IMDbID string `json:"imdb_id"`
		TVDbID int    `json:"tvdb_id"`
	} `json:"external_ids"`
	ContentRatings struct {
		Results []struct {
			ISO31661 string `json:"iso_3166_1"`
			Rating   string `json:"rating"`
		} `json:"results"`
	} `json:"content_ratings"`
	Credits TMDBCredits `json:"credits"`
	Logos   []struct {
		FilePath string `json:"file_path"`
		ISO6391  string `json:"iso_639_1"`
	} `json:"logos"`
}

// TMDBMovieDetail holds full movie details.
type TMDBMovieDetail struct {
	ID               int         `json:"id"`
	Title            string      `json:"title"`
	OriginalTitle    string      `json:"original_title"`
	Overview         string      `json:"overview"`
	ReleaseDate      string      `json:"release_date"`
	Genres           []TMDBGenre `json:"genres"`
	PosterPath       string      `json:"poster_path"`
	BackdropPath     string      `json:"backdrop_path"`
	OriginalLanguage string      `json:"original_language"`
	VoteAverage      float64     `json:"vote_average"`
	ExternalIDs      struct {
		IMDbID string `json:"imdb_id"`
	} `json:"external_ids"`
	ReleaseDates struct {
		Results []struct {
			ISO31661     string `json:"iso_3166_1"`
			ReleaseDates []struct {
				Certification string `json:"certification"`
			} `json:"release_dates"`
		} `json:"results"`
	} `json:"release_dates"`
	Credits TMDBCredits `json:"credits"`
	Logos   []struct {
		FilePath string `json:"file_path"`
		ISO6391  string `json:"iso_639_1"`
	} `json:"logos"`
}

// TMDBGenre is a genre entry.
type TMDBGenre struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

// TMDBCredits bundles cast and crew.
type TMDBCredits struct {
	Cast []struct {
		ID          int    `json:"id"`
		Name        string `json:"name"`
		Character   string `json:"character"`
		ProfilePath string `json:"profile_path"`
	} `json:"cast"`
	Crew []struct {
		ID   int    `json:"id"`
		Name string `json:"name"`
		Job  string `json:"job"`
	} `json:"crew"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Client
// ─────────────────────────────────────────────────────────────────────────────

const tmdbBase = "https://api.themoviedb.org/3"

// TMDBClient performs authenticated calls to the TMDB v3 REST API.
type TMDBClient struct {
	apiKey            string
	http              *http.Client
	titleLanguages    []string
	overviewLanguages []string
}

// NewTMDBClient creates a client with the given API key.
func NewTMDBClient(apiKey string, titleLanguages, overviewLanguages []string) *TMDBClient {
	return &TMDBClient{
		apiKey:            apiKey,
		http:              &http.Client{Timeout: 15 * time.Second},
		titleLanguages:    normalizeLanguagePreferences(titleLanguages, []string{"zh-CN", "zh-SG", "zh", "en-US", "en"}),
		overviewLanguages: normalizeLanguagePreferences(overviewLanguages, []string{"zh-CN", "zh-SG", "zh", "en-US", "en"}),
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Web-scraping fallback (mirrors Python's _search_tmdb_web)
// ─────────────────────────────────────────────────────────────────────────────

// SearchTMDBWeb scrapes TMDB's public search page as a last-resort fallback
// when the v3 API search returns zero results (e.g. newly-indexed shows whose
// search index hasn't been refreshed yet).  It mirrors Python's
// _search_tmdb_web() helper in get_info.py.
//
// mediaType must be "tv" or "movie".
func (c *TMDBClient) SearchTMDBWeb(query, mediaType string) ([]TMDBSearchResult, error) {
	params := url.Values{}
	params.Set("query", query)
	params.Set("language", c.primaryAPILanguage())
	searchURL := "https://www.themoviedb.org/search?" + params.Encode()

	req, err := http.NewRequest("GET", searchURL, nil)
	if err != nil {
		return nil, fmt.Errorf("tmdb web search: build request: %w", err)
	}
	// Mimic a real browser so TMDB serves the SSR HTML with results.
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	req.Header.Set("Accept-Language", c.acceptLanguageHeader())
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
	req.Header.Set("Referer", "https://www.themoviedb.org/")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("tmdb web search: request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, nil
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("tmdb web search: read body: %w", err)
	}

	return parseTMDBWebHTML(string(body), mediaType), nil
}

// parseTMDBWebHTML extracts TMDBSearchResult entries from TMDB's HTML search
// page.  It uses regexp instead of a full HTML parser so that no extra
// dependency is required.
//
// Strategy (matches Python's BeautifulSoup selectors):
//   - Find every <a> opening tag that has class="result" AND an href containing
//     "/<mediaType>/<id>".  This is equivalent to Python's
//     card.select_one("a.result") inside a "div.card".
//   - For each unique ID, capture the nearest <h2> text as the title and the
//     nearest 4-digit year as the air/release date.
//
// Using class="result" as the gate prevents false positives from navigation
// links, trending banners, or any other /tv/ID href that appears on the page
// outside the actual search-result cards.
func parseTMDBWebHTML(html, mediaType string) []TMDBSearchResult {
	// Step 1: match the complete opening <a …> tag (attributes can be up to
	// ~500 chars; [^>] also matches newlines so multi-line tags are handled).
	aTagRe := regexp.MustCompile(`<a\b[^>]{0,500}>`)

	// Step 2: within that tag, require class="…result…"
	classResultRe := regexp.MustCompile(`\bclass="[^"]*\bresult\b`)

	// Step 3: within that tag, extract the media-type ID from the href
	hrefIDRe := regexp.MustCompile(`href="/` + regexp.QuoteMeta(mediaType) + `/(\d+)`)

	// Title: first <h2> text in the window after the tag
	h2Re := regexp.MustCompile(`<h2[^>]*>\s*([^<]{1,300}?)\s*</h2>`)

	// Year: prefer a span whose class contains "release_date"
	yearRe := regexp.MustCompile(`(?i)release_date[^>]*>([^<]{0,60}?)(\d{4})`)

	// Fallback: any bare 20xx year in the window
	bareYearRe := regexp.MustCompile(`\b(20\d{2})\b`)

	seen := make(map[int]bool)
	var results []TMDBSearchResult

	for _, loc := range aTagRe.FindAllStringIndex(html, -1) {
		tag := html[loc[0]:loc[1]]

		// Must be a "result" link (Python: a.result)
		if !classResultRe.MatchString(tag) {
			continue
		}

		// Must link to the requested media type
		m := hrefIDRe.FindStringSubmatch(tag)
		if m == nil {
			continue
		}

		id := 0
		fmt.Sscanf(m[1], "%d", &id)
		if id == 0 || seen[id] {
			continue
		}
		seen[id] = true

		// Inspect a 2000-char window after the opening tag for title/year.
		windowEnd := loc[1] + 2000
		if windowEnd > len(html) {
			windowEnd = len(html)
		}
		window := html[loc[0]:windowEnd]

		// Title from nearest <h2>
		name := ""
		if tm := h2Re.FindStringSubmatch(window); tm != nil {
			name = unescapeHTMLEntities(strings.TrimSpace(tm[1]))
		}

		// Year: prefer span.release_date, fall back to any bare 20xx year
		dateStr := ""
		if ym := yearRe.FindStringSubmatch(window); ym != nil {
			dateStr = ym[2] + "-01-01"
		} else if ym := bareYearRe.FindStringSubmatch(window); ym != nil {
			dateStr = ym[1] + "-01-01"
		}

		r := TMDBSearchResult{
			ID:           id,
			Name:         name,
			OriginalName: name,
		}
		if mediaType == "tv" {
			r.FirstAirDate = dateStr
		} else {
			r.ReleaseDate = dateStr
		}
		results = append(results, r)

		if len(results) >= 10 {
			break
		}
	}

	return results
}

// unescapeHTMLEntities replaces the most common HTML entities with their
// plain-text equivalents.
func unescapeHTMLEntities(s string) string {
	s = strings.ReplaceAll(s, "&amp;", "&")
	s = strings.ReplaceAll(s, "&lt;", "<")
	s = strings.ReplaceAll(s, "&gt;", ">")
	s = strings.ReplaceAll(s, "&#39;", "'")
	s = strings.ReplaceAll(s, "&apos;", "'")
	s = strings.ReplaceAll(s, "&quot;", `"`)
	s = strings.ReplaceAll(s, "&nbsp;", " ")
	return s
}

// ─────────────────────────────────────────────────────────────────────────────
// Search helpers
// ─────────────────────────────────────────────────────────────────────────────

// SearchTV searches TMDB for TV shows matching query and optional year.
func (c *TMDBClient) SearchTV(query string, year int) ([]TMDBSearchResult, error) {
	type attempt struct {
		lang string
		year int
	}
	var attempts []attempt
	for _, lang := range c.searchLanguages() {
		attempts = append(attempts,
			attempt{lang: lang, year: year},
			attempt{lang: lang, year: 0},
		)
	}

	var lastErr error
	for i, a := range attempts {
		if i > 0 && a.year == attempts[i-1].year && a.lang == attempts[i-1].lang {
			continue
		}
		params := url.Values{}
		params.Set("query", query)
		params.Set("language", a.lang)
		if a.year > 0 {
			params.Set("first_air_date_year", fmt.Sprintf("%d", a.year))
		}
		var resp TMDBSearchResponse
		if err := c.get("/search/tv", params, &resp); err != nil {
			lastErr = err
			continue
		}
		if len(resp.Results) > 0 {
			return resp.Results, nil
		}
	}
	if lastErr != nil {
		return nil, lastErr
	}
	return nil, nil
}

// SearchMovie searches TMDB for movies matching query and optional year.
func (c *TMDBClient) SearchMovie(query string, year int) ([]TMDBSearchResult, error) {
	type attempt struct {
		lang string
		year int
	}
	var attempts []attempt
	for _, lang := range c.searchLanguages() {
		attempts = append(attempts,
			attempt{lang: lang, year: year},
			attempt{lang: lang, year: 0},
		)
	}

	var lastErr error
	for i, a := range attempts {
		if i > 0 && a.year == attempts[i-1].year && a.lang == attempts[i-1].lang {
			continue
		}
		params := url.Values{}
		params.Set("query", query)
		params.Set("language", a.lang)
		if a.year > 0 {
			params.Set("year", fmt.Sprintf("%d", a.year))
		}
		var resp TMDBSearchResponse
		if err := c.get("/search/movie", params, &resp); err != nil {
			lastErr = err
			continue
		}
		if len(resp.Results) > 0 {
			return resp.Results, nil
		}
	}
	if lastErr != nil {
		return nil, lastErr
	}
	return nil, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Detail helpers
// ─────────────────────────────────────────────────────────────────────────────

// GetTVDetail fetches full TV show details including all seasons.
// appendToResponse is passed as append_to_response (e.g. "credits,external_ids,content_ratings,images").
func (c *TMDBClient) GetTVDetail(id int, appendToResponse string) (*TMDBTVDetail, error) {
	params := url.Values{}
	params.Set("language", c.primaryAPILanguage())
	if appendToResponse != "" {
		params.Set("append_to_response", appendToResponse)
	}
	params.Set("include_image_language", c.includeImageLanguageValue())

	var detail TMDBTVDetail
	if err := c.get(fmt.Sprintf("/tv/%d", id), params, &detail); err != nil {
		return nil, err
	}

	if err := c.enrichTVDetailPreferredText(&detail, appendToResponse); err != nil {
		return nil, err
	}

	return &detail, nil
}

// GetMovieDetail fetches full movie details.
func (c *TMDBClient) GetMovieDetail(id int, appendToResponse string) (*TMDBMovieDetail, error) {
	params := url.Values{}
	params.Set("language", c.primaryAPILanguage())
	if appendToResponse != "" {
		params.Set("append_to_response", appendToResponse)
	}
	params.Set("include_image_language", c.includeImageLanguageValue())

	var detail TMDBMovieDetail
	if err := c.get(fmt.Sprintf("/movie/%d", id), params, &detail); err != nil {
		return nil, err
	}

	if err := c.enrichMovieDetailPreferredText(&detail, appendToResponse); err != nil {
		return nil, err
	}

	return &detail, nil
}

// GetSeasonDetail fetches a single season with full episode list.
func (c *TMDBClient) GetSeasonDetail(tvID, seasonNumber int) (*TMDBSeason, error) {
	params := url.Values{}
	params.Set("language", c.primaryAPILanguage())

	var season TMDBSeason
	if err := c.get(fmt.Sprintf("/tv/%d/season/%d", tvID, seasonNumber), params, &season); err != nil {
		return nil, err
	}
	return &season, nil
}

// GetTVImages fetches poster/backdrop/logo images for a TV show.
func (c *TMDBClient) GetTVImages(id int) (map[string][]map[string]interface{}, error) {
	params := url.Values{}
	params.Set("include_image_language", c.includeImageLanguageValue())

	var result map[string][]map[string]interface{}
	if err := c.get(fmt.Sprintf("/tv/%d/images", id), params, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// GetMovieImages fetches poster/backdrop/logo images for a movie.
func (c *TMDBClient) GetMovieImages(id int) (map[string][]map[string]interface{}, error) {
	params := url.Values{}
	params.Set("include_image_language", c.includeImageLanguageValue())

	var result map[string][]map[string]interface{}
	if err := c.get(fmt.Sprintf("/movie/%d/images", id), params, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Best-result selection
// ─────────────────────────────────────────────────────────────────────────────

// SelectBestTV picks the most-likely TV result from a list using simple
// heuristics (title similarity + year proximity).
func SelectBestTV(results []TMDBSearchResult, query string, year int) *TMDBSearchResult {
	if len(results) == 0 {
		return nil
	}
	best := &results[0]
	bestScore := scoreResult(results[0], query, year, false)

	for i := 1; i < len(results); i++ {
		s := scoreResult(results[i], query, year, false)
		if s > bestScore {
			bestScore = s
			best = &results[i]
		}
	}
	return best
}

// SelectBestMovie picks the most-likely movie result from a list.
func SelectBestMovie(results []TMDBSearchResult, query string, year int) *TMDBSearchResult {
	if len(results) == 0 {
		return nil
	}
	best := &results[0]
	bestScore := scoreResult(results[0], query, year, true)

	for i := 1; i < len(results); i++ {
		s := scoreResult(results[i], query, year, true)
		if s > bestScore {
			bestScore = s
			best = &results[i]
		}
	}
	return best
}

// scoreResult computes a numeric score for a search result against a query.
func scoreResult(r TMDBSearchResult, query string, year int, isMovie bool) float64 {
	var score float64

	title := r.Name
	origTitle := r.OriginalName
	dateStr := r.FirstAirDate
	if isMovie {
		title = r.Title
		origTitle = r.OriginalTitle
		dateStr = r.ReleaseDate
	}

	qLower := strings.ToLower(strings.TrimSpace(query))
	tLower := strings.ToLower(strings.TrimSpace(title))
	oLower := strings.ToLower(strings.TrimSpace(origTitle))

	// Exact match
	if tLower == qLower || oLower == qLower {
		score += 100
	} else if strings.Contains(tLower, qLower) || strings.Contains(oLower, qLower) {
		score += 50
	} else {
		// Simple word-overlap score
		qWords := strings.Fields(qLower)
		tWords := strings.Fields(tLower)
		overlap := wordOverlap(qWords, tWords)
		score += float64(overlap) * 10
	}

	// Year bonus / penalty
	if year > 0 && len(dateStr) >= 4 {
		var resultYear int
		fmt.Sscanf(dateStr[:4], "%d", &resultYear)
		diff := resultYear - year
		if diff < 0 {
			diff = -diff
		}
		switch diff {
		case 0:
			score += 20
		case 1:
			score += 5
		default:
			score -= float64(diff) * 5
		}
	}

	return score
}

func wordOverlap(a, b []string) int {
	set := make(map[string]bool, len(b))
	for _, w := range b {
		set[w] = true
	}
	count := 0
	for _, w := range a {
		if set[w] {
			count++
		}
	}
	return count
}

type tmdbAltTitleItem struct {
	Title string
	Lang  string
}

type tmdbTVTranslationsResponse struct {
	Translations []struct {
		ISO6391  string `json:"iso_639_1"`
		ISO31661 string `json:"iso_3166_1"`
		Data     struct {
			Name     string `json:"name"`
			Overview string `json:"overview"`
		} `json:"data"`
	} `json:"translations"`
}

type tmdbMovieTranslationsResponse struct {
	Translations []struct {
		ISO6391  string `json:"iso_639_1"`
		ISO31661 string `json:"iso_3166_1"`
		Data     struct {
			Title    string `json:"title"`
			Overview string `json:"overview"`
		} `json:"data"`
	} `json:"translations"`
}

type tmdbTVAlternativeTitlesResponse struct {
	Results []struct {
		Title    string `json:"title"`
		ISO31661 string `json:"iso_3166_1"`
	} `json:"results"`
}

type tmdbMovieAlternativeTitlesResponse struct {
	Titles []struct {
		Title    string `json:"title"`
		ISO31661 string `json:"iso_3166_1"`
	} `json:"titles"`
}

func preferredLangOrder() []string {
	return []string{"zh-CN", "zh-SG", "zh", "en-US", "en"}
}

func normalizeAPILanguage(tag string) string {
	tag = strings.TrimSpace(strings.ToLower(tag))
	switch {
	case tag == "", tag == "zh":
		return "zh-CN"
	case tag == "zh-cn", tag == "zh-sg":
		return "zh-CN"
	case strings.HasPrefix(tag, "zh-tw"), strings.HasPrefix(tag, "zh-hk"), strings.HasPrefix(tag, "zh-mo"):
		return "zh-TW"
	case strings.HasPrefix(tag, "zh"):
		return "zh-CN"
	case tag == "en", strings.HasPrefix(tag, "en-"):
		return "en-US"
	default:
		return ""
	}
}

func uniqueNonEmptyStrings(items []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(items))
	for _, item := range items {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		key := strings.ToLower(item)
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, item)
	}
	return out
}

func (c *TMDBClient) searchLanguages() []string {
	candidates := []string{}
	for _, lang := range c.titleLanguages {
		if normalized := normalizeAPILanguage(lang); normalized != "" {
			candidates = append(candidates, normalized)
		}
	}
	candidates = append(candidates, "zh-CN", "en-US")
	return uniqueNonEmptyStrings(candidates)
}

func (c *TMDBClient) primaryAPILanguage() string {
	langs := c.searchLanguages()
	if len(langs) == 0 {
		return "zh-CN"
	}
	return langs[0]
}

func (c *TMDBClient) includeImageLanguageValue() string {
	values := []string{}
	for _, lang := range c.titleLanguages {
		lang = strings.TrimSpace(strings.ToLower(lang))
		switch {
		case strings.HasPrefix(lang, "zh"):
			values = append(values, "zh", "cn")
		case strings.HasPrefix(lang, "en"):
			values = append(values, "en")
		}
	}
	values = append(values, "zh", "cn", "null", "en")
	return strings.Join(uniqueNonEmptyStrings(values), ",")
}

func (c *TMDBClient) acceptLanguageHeader() string {
	apiLangs := c.searchLanguages()
	parts := make([]string, 0, len(apiLangs)+2)
	q := 1.0
	for i, lang := range apiLangs {
		if i == 0 {
			parts = append(parts, lang)
		} else {
			parts = append(parts, fmt.Sprintf("%s;q=%.1f", lang, q))
		}
		q -= 0.1
		if q < 0.1 {
			q = 0.1
		}
	}
	parts = append(parts, "zh;q=0.9", "en;q=0.8")
	return strings.Join(uniqueNonEmptyStrings(parts), ",")
}

func normalizeLanguagePreferences(preferences []string, defaults []string) []string {
	if len(preferences) == 0 {
		out := make([]string, len(defaults))
		copy(out, defaults)
		return out
	}

	seen := map[string]bool{}
	out := make([]string, 0, len(preferences)+len(defaults))

	for _, pref := range preferences {
		pref = strings.TrimSpace(pref)
		if pref == "" {
			continue
		}
		normalized := strings.ToLower(pref)
		if seen[normalized] {
			continue
		}
		seen[normalized] = true
		out = append(out, pref)
	}

	for _, pref := range defaults {
		normalized := strings.ToLower(pref)
		if seen[normalized] {
			continue
		}
		seen[normalized] = true
		out = append(out, pref)
	}

	if len(out) == 0 {
		out = append(out, defaults...)
	}

	return out
}

func langRankWithOrder(tag string, order []string) int {
	tag = strings.TrimSpace(strings.ToLower(tag))
	for i, v := range order {
		if strings.ToLower(v) == tag {
			return i
		}
	}
	switch {
	case tag == "zh-cn" || tag == "zh-sg":
		return 0
	case strings.HasPrefix(tag, "zh-tw") || strings.HasPrefix(tag, "zh-hk") || strings.HasPrefix(tag, "zh-mo"):
		return 10 + len(order)
	case strings.HasPrefix(tag, "zh"):
		return 1
	case strings.HasPrefix(tag, "en"):
		return 20 + len(order)
	default:
		return 99 + len(order)
	}
}

func langRank(tag string) int {
	return langRankWithOrder(tag, preferredLangOrder())
}

func normalizeLangTag(lang, region string) string {
	lang = strings.TrimSpace(strings.ToLower(lang))
	region = strings.TrimSpace(strings.ToUpper(region))
	if lang == "" {
		return ""
	}
	if region == "" {
		return lang
	}
	return lang + "-" + region
}

func pickPreferredTitle(current string, candidates []tmdbAltTitleItem, order []string) string {
	best := strings.TrimSpace(current)
	bestRank := 99 + len(order)
	if best != "" {
		bestRank = 98 + len(order)
	}

	for _, item := range candidates {
		title := strings.TrimSpace(item.Title)
		if title == "" {
			continue
		}
		rank := langRankWithOrder(item.Lang, order)
		if rank < bestRank {
			best = title
			bestRank = rank
		}
	}

	return best
}

func pickPreferredOverview(current string, candidates map[string]string, order []string) string {
	best := strings.TrimSpace(current)
	bestRank := 99 + len(order)
	if best != "" {
		bestRank = 98 + len(order)
	}

	keys := make([]string, 0, len(candidates))
	for k := range candidates {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return langRankWithOrder(keys[i], order) < langRankWithOrder(keys[j], order)
	})

	for _, lang := range keys {
		text := strings.TrimSpace(candidates[lang])
		if text == "" {
			continue
		}
		rank := langRankWithOrder(lang, order)
		if rank < bestRank {
			best = text
			bestRank = rank
		}
	}

	return best
}

func containsAppendValue(appendToResponse, target string) bool {
	for _, part := range strings.Split(appendToResponse, ",") {
		if strings.TrimSpace(part) == target {
			return true
		}
	}
	return false
}

func mergeAppendValue(appendToResponse string, values ...string) string {
	seen := map[string]bool{}
	var parts []string

	for _, part := range strings.Split(appendToResponse, ",") {
		part = strings.TrimSpace(part)
		if part == "" || seen[part] {
			continue
		}
		seen[part] = true
		parts = append(parts, part)
	}

	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		parts = append(parts, value)
	}

	return strings.Join(parts, ",")
}

func (c *TMDBClient) enrichTVDetailPreferredText(detail *TMDBTVDetail, appendToResponse string) error {
	if detail == nil {
		return nil
	}

	params := url.Values{}
	params.Set("language", c.primaryAPILanguage())
	params.Set("include_image_language", c.includeImageLanguageValue())

	appendValue := mergeAppendValue(appendToResponse, "translations", "alternative_titles")
	if appendValue != "" {
		params.Set("append_to_response", appendValue)
	}

	var raw struct {
		Name              string                          `json:"name"`
		OriginalName      string                          `json:"original_name"`
		OriginalLanguage  string                          `json:"original_language"`
		Overview          string                          `json:"overview"`
		Translations      tmdbTVTranslationsResponse      `json:"translations"`
		AlternativeTitles tmdbTVAlternativeTitlesResponse `json:"alternative_titles"`
	}
	if err := c.get(fmt.Sprintf("/tv/%d", detail.ID), params, &raw); err != nil {
		return err
	}

	var titles []tmdbAltTitleItem
	overviews := map[string]string{}

	for _, tr := range raw.Translations.Translations {
		langTag := normalizeLangTag(tr.ISO6391, tr.ISO31661)
		if tr.Data.Name != "" {
			titles = append(titles, tmdbAltTitleItem{Title: tr.Data.Name, Lang: langTag})
		}
		if tr.Data.Overview != "" {
			overviews[langTag] = tr.Data.Overview
		}
	}

	for _, alt := range raw.AlternativeTitles.Results {
		if alt.Title == "" {
			continue
		}
		langTag := normalizeLangTag("", alt.ISO31661)
		if strings.EqualFold(alt.ISO31661, "CN") || strings.EqualFold(alt.ISO31661, "SG") {
			langTag = "zh-CN"
		} else if strings.EqualFold(alt.ISO31661, "TW") {
			langTag = "zh-TW"
		} else if strings.EqualFold(alt.ISO31661, "HK") || strings.EqualFold(alt.ISO31661, "MO") {
			langTag = "zh-HK"
		} else if strings.EqualFold(alt.ISO31661, "US") {
			langTag = "en-US"
		}
		titles = append(titles, tmdbAltTitleItem{Title: alt.Title, Lang: langTag})
	}

	if chineseTitle := pickChinesePreferredTitle(detail.Name, raw.OriginalName, raw.OriginalLanguage, detail.OriginCountry, titles); chineseTitle != "" {
		detail.Name = chineseTitle
		detail.Overview = pickPreferredOverview(detail.Overview, overviews, chineseLanguageOrder())
	} else {
		// 只要 TMDB 提供任意中文标题，就优先使用中文，避免日漫等条目落成罗马字标题。
		shouldForceChinese := !isChinese(detail.Name) && hasChineseTranslation(titles, overviews)
		if shouldForceChinese {
			detail.Name = pickPreferredTitle(detail.Name, titles, chineseLanguageOrder())
			detail.Overview = pickPreferredOverview(detail.Overview, overviews, chineseLanguageOrder())
		} else {
			detail.Name = pickPreferredTitle(detail.Name, titles, c.titleLanguages)
			detail.Overview = pickPreferredOverview(detail.Overview, overviews, c.overviewLanguages)
		}
	}

	if !containsAppendValue(appendToResponse, "translations") {
		detail.Logos = filterPreferredLogos(detail.Logos)
	}

	return nil
}

func (c *TMDBClient) enrichMovieDetailPreferredText(detail *TMDBMovieDetail, appendToResponse string) error {
	if detail == nil {
		return nil
	}

	params := url.Values{}
	params.Set("language", c.primaryAPILanguage())
	params.Set("include_image_language", c.includeImageLanguageValue())

	appendValue := mergeAppendValue(appendToResponse, "translations", "alternative_titles")
	if appendValue != "" {
		params.Set("append_to_response", appendValue)
	}

	var raw struct {
		Title             string                             `json:"title"`
		OriginalTitle     string                             `json:"original_title"`
		OriginalLanguage  string                             `json:"original_language"`
		Overview          string                             `json:"overview"`
		Translations      tmdbMovieTranslationsResponse      `json:"translations"`
		AlternativeTitles tmdbMovieAlternativeTitlesResponse `json:"alternative_titles"`
	}
	if err := c.get(fmt.Sprintf("/movie/%d", detail.ID), params, &raw); err != nil {
		return err
	}

	var titles []tmdbAltTitleItem
	overviews := map[string]string{}

	for _, tr := range raw.Translations.Translations {
		langTag := normalizeLangTag(tr.ISO6391, tr.ISO31661)
		if tr.Data.Title != "" {
			titles = append(titles, tmdbAltTitleItem{Title: tr.Data.Title, Lang: langTag})
		}
		if tr.Data.Overview != "" {
			overviews[langTag] = tr.Data.Overview
		}
	}

	for _, alt := range raw.AlternativeTitles.Titles {
		if alt.Title == "" {
			continue
		}
		langTag := normalizeLangTag("", alt.ISO31661)
		if strings.EqualFold(alt.ISO31661, "CN") || strings.EqualFold(alt.ISO31661, "SG") {
			langTag = "zh-CN"
		} else if strings.EqualFold(alt.ISO31661, "TW") {
			langTag = "zh-TW"
		} else if strings.EqualFold(alt.ISO31661, "HK") || strings.EqualFold(alt.ISO31661, "MO") {
			langTag = "zh-HK"
		} else if strings.EqualFold(alt.ISO31661, "US") {
			langTag = "en-US"
		}
		titles = append(titles, tmdbAltTitleItem{Title: alt.Title, Lang: langTag})
	}

	if chineseTitle := pickChinesePreferredTitle(detail.Title, raw.OriginalTitle, raw.OriginalLanguage, nil, titles); chineseTitle != "" {
		detail.Title = chineseTitle
		detail.Overview = pickPreferredOverview(detail.Overview, overviews, chineseLanguageOrder())
	} else {
		// 只要 TMDB 提供任意中文标题，就优先使用中文，避免日漫等条目落成罗马字标题。
		shouldForceChinese := !isChinese(detail.Title) && hasChineseTranslation(titles, overviews)
		if shouldForceChinese {
			detail.Title = pickPreferredTitle(detail.Title, titles, chineseLanguageOrder())
			detail.Overview = pickPreferredOverview(detail.Overview, overviews, chineseLanguageOrder())
		} else {
			detail.Title = pickPreferredTitle(detail.Title, titles, c.titleLanguages)
			detail.Overview = pickPreferredOverview(detail.Overview, overviews, c.overviewLanguages)
		}
	}

	if !containsAppendValue(appendToResponse, "translations") {
		detail.Logos = filterPreferredLogos(detail.Logos)
	}

	return nil
}

func filterPreferredLogos(logos []struct {
	FilePath string `json:"file_path"`
	ISO6391  string `json:"iso_639_1"`
}) []struct {
	FilePath string `json:"file_path"`
	ISO6391  string `json:"iso_639_1"`
} {
	var zh []struct {
		FilePath string `json:"file_path"`
		ISO6391  string `json:"iso_639_1"`
	}
	var en []struct {
		FilePath string `json:"file_path"`
		ISO6391  string `json:"iso_639_1"`
	}
	var empty []struct {
		FilePath string `json:"file_path"`
		ISO6391  string `json:"iso_639_1"`
	}

	for _, logo := range logos {
		lang := strings.ToLower(strings.TrimSpace(logo.ISO6391))
		switch {
		case strings.HasPrefix(lang, "zh"):
			zh = append(zh, logo)
		case strings.HasPrefix(lang, "en"):
			en = append(en, logo)
		case lang == "":
			empty = append(empty, logo)
		}
	}

	if len(zh) > 0 {
		return zh
	}
	if len(en) > 0 {
		return en
	}
	if len(empty) > 0 {
		return empty
	}
	return logos
}

func chineseLanguageOrder() []string {
	return []string{"zh-CN", "zh-SG", "zh", "zh-TW", "zh-HK", "zh-MO"}
}

func isChineseLanguage(lang string) bool {
	lang = strings.ToLower(strings.TrimSpace(lang))
	return lang == "zh" || strings.HasPrefix(lang, "zh-")
}

func isChineseRegion(region string) bool {
	switch strings.ToUpper(strings.TrimSpace(region)) {
	case "CN", "SG", "TW", "HK", "MO":
		return true
	default:
		return false
	}
}

func isChineseMedia(originalLanguage string, originCountries []string) bool {
	if isChineseLanguage(originalLanguage) {
		return true
	}
	for _, country := range originCountries {
		if isChineseRegion(country) {
			return true
		}
	}
	return false
}

func pickChinesePreferredTitle(current, original, originalLanguage string, originCountries []string, candidates []tmdbAltTitleItem) string {
	if isChinese(current) {
		return strings.TrimSpace(current)
	}

	best := ""
	bestRank := 99 + len(chineseLanguageOrder())
	for _, item := range candidates {
		title := strings.TrimSpace(item.Title)
		if title == "" || !isChinese(title) {
			continue
		}
		rank := langRankWithOrder(item.Lang, chineseLanguageOrder())
		if rank < bestRank {
			best = title
			bestRank = rank
		}
	}
	if best != "" {
		return best
	}
	if isChinese(original) && (isChineseLanguage(originalLanguage) || isChineseMedia(originalLanguage, originCountries)) {
		return strings.TrimSpace(original)
	}
	return ""
}

// isEnglishLanguage 判断语言代码是否为英文
func isEnglishLanguage(lang string) bool {
	lang = strings.ToLower(strings.TrimSpace(lang))
	return lang == "en" || strings.HasPrefix(lang, "en-")
}

// isChinese 判断字符串是否包含中文字符
func isChinese(s string) bool {
	for _, r := range s {
		// 中文字符的 Unicode 范围
		if (r >= 0x4E00 && r <= 0x9FFF) || // CJK 统一汉字
			(r >= 0x3400 && r <= 0x4DBF) || // CJK 扩展 A
			(r >= 0x20000 && r <= 0x2A6DF) || // CJK 扩展 B
			(r >= 0x2A700 && r <= 0x2B73F) || // CJK 扩展 C
			(r >= 0x2B740 && r <= 0x2B81F) || // CJK 扩展 D
			(r >= 0x2B820 && r <= 0x2CEAF) || // CJK 扩展 E
			(r >= 0xF900 && r <= 0xFAFF) || // CJK 兼容汉字
			(r >= 0x2F800 && r <= 0x2FA1F) { // CJK 兼容汉字补充
			return true
		}
	}
	return false
}

// hasChineseTranslation 判断是否有任意中文翻译
func hasChineseTranslation(titles []tmdbAltTitleItem, overviews map[string]string) bool {
	order := chineseLanguageOrder()

	for _, t := range titles {
		if strings.TrimSpace(t.Title) == "" {
			continue
		}
		if langRankWithOrder(t.Lang, order) < len(order) {
			return true
		}
	}

	for lang, text := range overviews {
		if strings.TrimSpace(text) == "" {
			continue
		}
		if langRankWithOrder(lang, order) < len(order) {
			return true
		}
	}

	return false
}

// ─────────────────────────────────────────────────────────────────────────────
// Low-level HTTP helper
// ─────────────────────────────────────────────────────────────────────────────

func (c *TMDBClient) get(path string, params url.Values, out interface{}) error {
	if params == nil {
		params = url.Values{}
	}
	params.Set("api_key", c.apiKey)

	endpoint := tmdbBase + path + "?" + params.Encode()

	resp, err := c.http.Get(endpoint)
	if err != nil {
		return fmt.Errorf("tmdb GET %s: %w", path, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("tmdb read body: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		// Try to extract error message
		var errBody struct {
			StatusMessage string `json:"status_message"`
			StatusCode    int    `json:"status_code"`
		}
		_ = json.Unmarshal(body, &errBody)
		return fmt.Errorf("tmdb %s returned %d: %s", path, resp.StatusCode, errBody.StatusMessage)
	}

	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("tmdb decode %s: %w", path, err)
	}
	return nil
}
