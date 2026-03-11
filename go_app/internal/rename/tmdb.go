package rename

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
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
	apiKey string
	http   *http.Client
}

// NewTMDBClient creates a client with the given API key.
func NewTMDBClient(apiKey string) *TMDBClient {
	return &TMDBClient{
		apiKey: apiKey,
		http:   &http.Client{Timeout: 15 * time.Second},
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Search helpers
// ─────────────────────────────────────────────────────────────────────────────

// SearchTV searches TMDB for TV shows matching query and optional year.
func (c *TMDBClient) SearchTV(query string, year int) ([]TMDBSearchResult, error) {
	params := url.Values{}
	params.Set("query", query)
	params.Set("language", "zh-CN")
	if year > 0 {
		params.Set("first_air_date_year", fmt.Sprintf("%d", year))
	}

	var resp TMDBSearchResponse
	if err := c.get("/search/tv", params, &resp); err != nil {
		return nil, err
	}
	return resp.Results, nil
}

// SearchMovie searches TMDB for movies matching query and optional year.
func (c *TMDBClient) SearchMovie(query string, year int) ([]TMDBSearchResult, error) {
	params := url.Values{}
	params.Set("query", query)
	params.Set("language", "zh-CN")
	if year > 0 {
		params.Set("year", fmt.Sprintf("%d", year))
	}

	var resp TMDBSearchResponse
	if err := c.get("/search/movie", params, &resp); err != nil {
		return nil, err
	}
	return resp.Results, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Detail helpers
// ─────────────────────────────────────────────────────────────────────────────

// GetTVDetail fetches full TV show details including all seasons.
// appendToResponse is passed as append_to_response (e.g. "credits,external_ids,content_ratings,images").
func (c *TMDBClient) GetTVDetail(id int, appendToResponse string) (*TMDBTVDetail, error) {
	params := url.Values{}
	params.Set("language", "zh-CN")
	if appendToResponse != "" {
		params.Set("append_to_response", appendToResponse)
	}
	params.Set("include_image_language", "zh,cn,null,en")

	var detail TMDBTVDetail
	if err := c.get(fmt.Sprintf("/tv/%d", id), params, &detail); err != nil {
		return nil, err
	}
	return &detail, nil
}

// GetMovieDetail fetches full movie details.
func (c *TMDBClient) GetMovieDetail(id int, appendToResponse string) (*TMDBMovieDetail, error) {
	params := url.Values{}
	params.Set("language", "zh-CN")
	if appendToResponse != "" {
		params.Set("append_to_response", appendToResponse)
	}
	params.Set("include_image_language", "zh,cn,null,en")

	var detail TMDBMovieDetail
	if err := c.get(fmt.Sprintf("/movie/%d", id), params, &detail); err != nil {
		return nil, err
	}
	return &detail, nil
}

// GetSeasonDetail fetches a single season with full episode list.
func (c *TMDBClient) GetSeasonDetail(tvID, seasonNumber int) (*TMDBSeason, error) {
	params := url.Values{}
	params.Set("language", "zh-CN")

	var season TMDBSeason
	if err := c.get(fmt.Sprintf("/tv/%d/season/%d", tvID, seasonNumber), params, &season); err != nil {
		return nil, err
	}
	return &season, nil
}

// GetTVImages fetches poster/backdrop/logo images for a TV show.
func (c *TMDBClient) GetTVImages(id int) (map[string][]map[string]interface{}, error) {
	params := url.Values{}
	params.Set("include_image_language", "zh,cn,null,en")

	var result map[string][]map[string]interface{}
	if err := c.get(fmt.Sprintf("/tv/%d/images", id), params, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// GetMovieImages fetches poster/backdrop/logo images for a movie.
func (c *TMDBClient) GetMovieImages(id int) (map[string][]map[string]interface{}, error) {
	params := url.Values{}
	params.Set("include_image_language", "zh,cn,null,en")

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
