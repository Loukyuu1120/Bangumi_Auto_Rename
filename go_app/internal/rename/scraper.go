package rename

import (
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Scraper
// ─────────────────────────────────────────────────────────────────────────────

// Scraper downloads images and writes NFO sidecar files for Kodi/Jellyfin/Emby.
type Scraper struct {
	apiKey       string
	allowedTypes map[string]bool
	imageBase    string
	http         *http.Client
}

// NewScraper creates a Scraper with the given TMDB API key and allowed image
// type list (e.g. ["poster", "backdrop", "logo"]).
func NewScraper(apiKey string, allowedTypes []string) *Scraper {
	allowed := make(map[string]bool, len(allowedTypes))
	for _, t := range allowedTypes {
		allowed[strings.ToLower(t)] = true
	}
	return &Scraper{
		apiKey:       apiKey,
		allowedTypes: allowed,
		imageBase:    "https://image.tmdb.org/t/p/original",
		http:         &http.Client{Timeout: 30 * time.Second},
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Public entry points
// ─────────────────────────────────────────────────────────────────────────────

// ScrapeMovie writes movie.nfo and downloads poster/fanart for a movie file.
// savePath is the final target video file path; NFO and images are placed
// alongside it.
func (s *Scraper) ScrapeMovie(savePath string, info *TMDBMovieDetail) {
	if info == nil {
		return
	}
	folder := filepath.Dir(savePath)

	// Images
	s.downloadImage(info.PosterPath, filepath.Join(folder, "poster.jpg"), "poster", "w500")
	s.downloadImage(info.BackdropPath, filepath.Join(folder, "fanart.jpg"), "backdrop", "w1280")

	// NFO
	root := &movieNFO{
		Title:         info.Title,
		OriginalTitle: info.OriginalTitle,
		Year:          yearOf(info.ReleaseDate),
		Premiered:     info.ReleaseDate,
		Plot:          info.Overview,
		Outline:       info.Overview,
		Rating:        info.VoteAverage,
		MPAA:          movieCertification(info),
	}
	root.UniqueIDs = append(root.UniqueIDs, uniqueID{Type: "tmdb", Default: "true", Value: fmt.Sprintf("%d", info.ID)})
	if info.ExternalIDs.IMDbID != "" {
		root.UniqueIDs = append(root.UniqueIDs, uniqueID{Type: "imdb", Default: "false", Value: info.ExternalIDs.IMDbID})
	}
	for _, g := range info.Genres {
		root.Genres = append(root.Genres, g.Name)
	}
	root.Actors, root.Directors, root.Writers = extractCredits(info.Credits)

	nfoPath := strings.TrimSuffix(savePath, filepath.Ext(savePath)) + ".nfo"
	writeNFO(root, nfoPath)
}

// ScrapeTV writes tvshow.nfo and downloads show-level poster/fanart.
// workPath is the root show directory (e.g. /media/TV/ShowName (2020)).
func (s *Scraper) ScrapeTV(workPath string, info *TMDBTVDetail) {
	if info == nil {
		return
	}

	// Images
	s.downloadImage(info.PosterPath, filepath.Join(workPath, "poster.jpg"), "poster", "w500")
	s.downloadImage(info.BackdropPath, filepath.Join(workPath, "fanart.jpg"), "backdrop", "w1280")

	// Logo (first available)
	for _, logo := range info.Logos {
		if logo.ISO6391 == "en" || logo.ISO6391 == "" {
			s.downloadImage(logo.FilePath, filepath.Join(workPath, "logo.png"), "logo", "original")
			break
		}
	}

	root := &tvshowNFO{
		Title:         info.Name,
		OriginalTitle: info.OriginalName,
		Year:          yearOf(info.FirstAirDate),
		Premiered:     info.FirstAirDate,
		Plot:          info.Overview,
		Season:        "-1",
		Episode:       "-1",
		Rating:        0,
	}
	root.UniqueIDs = append(root.UniqueIDs, uniqueID{Type: "tmdb", Default: "false", Value: fmt.Sprintf("%d", info.ID)})
	if info.ExternalIDs.IMDbID != "" {
		root.UniqueIDs = append(root.UniqueIDs, uniqueID{Type: "imdb", Default: "true", Value: info.ExternalIDs.IMDbID})
	}
	if info.ExternalIDs.TVDbID > 0 {
		root.UniqueIDs = append(root.UniqueIDs, uniqueID{Type: "tvdb", Default: "false", Value: fmt.Sprintf("%d", info.ExternalIDs.TVDbID)})
	}
	for _, g := range info.Genres {
		root.Genres = append(root.Genres, g.Name)
	}
	root.Actors, root.Directors, root.Writers = extractCredits(info.Credits)

	writeNFO(root, filepath.Join(workPath, "tvshow.nfo"))
}

// ScrapeSeason writes season.nfo and downloads season poster.
// workPath is the show root; seasonNumber is the TMDB season number;
// seasonDir is the actual season directory on disk (may equal workPath).
func (s *Scraper) ScrapeSeason(workPath string, seasonNumber int, info *TMDBTVDetail, seasonDir string) {
	if info == nil {
		return
	}

	var seasonInfo *TMDBSeason
	for i := range info.Seasons {
		if info.Seasons[i].SeasonNumber == seasonNumber {
			seasonInfo = &info.Seasons[i]
			break
		}
	}
	if seasonInfo == nil {
		return
	}

	// Season poster alongside the show root
	posterName := fmt.Sprintf("season%02d-poster.jpg", seasonNumber)
	s.downloadImage(seasonInfo.PosterPath, filepath.Join(workPath, posterName), "poster", "w500")

	// Also write folder.jpg inside the season directory if it differs
	if seasonDir != "" && seasonDir != workPath {
		s.downloadImage(seasonInfo.PosterPath, filepath.Join(seasonDir, "folder.jpg"), "poster", "w500")
	}

	// season.nfo
	targetDir := seasonDir
	if targetDir == "" {
		targetDir = filepath.Join(workPath, fmt.Sprintf("Season%d", seasonNumber))
	}
	if _, err := os.Stat(targetDir); err == nil {
		root := &seasonNFO{
			Title:     seasonInfo.Name,
			Season:    seasonNumber,
			Plot:      seasonInfo.Overview,
			Premiered: seasonInfo.AirDate,
			Year:      yearOf(seasonInfo.AirDate),
		}
		writeNFO(root, filepath.Join(targetDir, "season.nfo"))
	}
}

// ScrapeEpisode writes an episode NFO and downloads the still image.
// videoTarget is the full path to the renamed video file.
func (s *Scraper) ScrapeEpisode(videoTarget string, info *TMDBTVDetail, seasonNumber, episodeNumber int) {
	if info == nil {
		return
	}

	var seasonInfo *TMDBSeason
	for i := range info.Seasons {
		if info.Seasons[i].SeasonNumber == seasonNumber {
			seasonInfo = &info.Seasons[i]
			break
		}
	}
	if seasonInfo == nil {
		return
	}

	var ep *TMDBEpisode
	for i := range seasonInfo.Episodes {
		if seasonInfo.Episodes[i].EpisodeNumber == episodeNumber {
			ep = &seasonInfo.Episodes[i]
			break
		}
	}
	if ep == nil {
		return
	}

	// Thumbnail
	if ep.StillPath != "" {
		thumbPath := strings.TrimSuffix(videoTarget, filepath.Ext(videoTarget)) + "-thumb.jpg"
		s.downloadImage(ep.StillPath, thumbPath, "thumb", "w300")
	}

	// Episode NFO
	root := &episodeNFO{
		Title:   ep.Name,
		Plot:    ep.Overview,
		Season:  seasonNumber,
		Episode: episodeNumber,
		Aired:   ep.AirDate,
		Rating:  ep.VoteAverage,
	}
	root.UniqueIDs = append(root.UniqueIDs, uniqueID{
		Type:    "tmdb",
		Default: "true",
		Value:   fmt.Sprintf("%d", ep.ID),
	})

	nfoPath := strings.TrimSuffix(videoTarget, filepath.Ext(videoTarget)) + ".nfo"
	writeNFO(root, nfoPath)
}

// ─────────────────────────────────────────────────────────────────────────────
// Image download helper
// ─────────────────────────────────────────────────────────────────────────────

func (s *Scraper) downloadImage(suffix, savePath, imageType, size string) {
	if suffix == "" || !s.allowedTypes[imageType] {
		return
	}
	if _, err := os.Stat(savePath); err == nil {
		return // already exists
	}
	if err := os.MkdirAll(filepath.Dir(savePath), 0o755); err != nil {
		return
	}

	url := fmt.Sprintf("https://image.tmdb.org/t/p/%s%s", size, suffix)
	resp, err := s.http.Get(url)
	if err != nil || resp.StatusCode != http.StatusOK {
		if resp != nil {
			resp.Body.Close()
		}
		return
	}
	defer resp.Body.Close()

	f, err := os.Create(savePath)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = io.Copy(f, resp.Body)
}

// ─────────────────────────────────────────────────────────────────────────────
// NFO XML structures
// ─────────────────────────────────────────────────────────────────────────────

type uniqueID struct {
	XMLName xml.Name `xml:"uniqueid"`
	Type    string   `xml:"type,attr"`
	Default string   `xml:"default,attr"`
	Value   string   `xml:",chardata"`
}

type actorXML struct {
	XMLName xml.Name `xml:"actor"`
	Name    string   `xml:"name"`
	Type    string   `xml:"type"`
	Role    string   `xml:"role,omitempty"`
	TMDbID  string   `xml:"tmdbid,omitempty"`
	Thumb   string   `xml:"thumb,omitempty"`
}

type movieNFO struct {
	XMLName       xml.Name   `xml:"movie"`
	Title         string     `xml:"title"`
	OriginalTitle string     `xml:"originaltitle"`
	Year          string     `xml:"year"`
	Premiered     string     `xml:"premiered"`
	Plot          string     `xml:"plot"`
	Outline       string     `xml:"outline"`
	Rating        float64    `xml:"rating"`
	MPAA          string     `xml:"mpaa,omitempty"`
	UniqueIDs     []uniqueID `xml:"uniqueid"`
	Genres        []string   `xml:"genre"`
	Actors        []actorXML
	Directors     []string `xml:"director"`
	Writers       []string `xml:"credits"`
}

type tvshowNFO struct {
	XMLName       xml.Name   `xml:"tvshow"`
	Title         string     `xml:"title"`
	OriginalTitle string     `xml:"originaltitle"`
	Year          string     `xml:"year"`
	Premiered     string     `xml:"premiered"`
	Plot          string     `xml:"plot"`
	Season        string     `xml:"season"`
	Episode       string     `xml:"episode"`
	Rating        float64    `xml:"rating"`
	MPAA          string     `xml:"mpaa,omitempty"`
	UniqueIDs     []uniqueID `xml:"uniqueid"`
	Genres        []string   `xml:"genre"`
	Actors        []actorXML
	Directors     []string `xml:"director"`
	Writers       []string `xml:"credits"`
}

type seasonNFO struct {
	XMLName   xml.Name `xml:"season"`
	Title     string   `xml:"title"`
	Season    int      `xml:"season"`
	Plot      string   `xml:"plot"`
	Premiered string   `xml:"premiered"`
	Year      string   `xml:"year"`
}

type episodeNFO struct {
	XMLName   xml.Name   `xml:"episodedetails"`
	Title     string     `xml:"title"`
	Plot      string     `xml:"plot"`
	Season    int        `xml:"season"`
	Episode   int        `xml:"episode"`
	Aired     string     `xml:"aired"`
	Rating    float64    `xml:"rating"`
	UniqueIDs []uniqueID `xml:"uniqueid"`
}

// ─────────────────────────────────────────────────────────────────────────────
// NFO write helper
// ─────────────────────────────────────────────────────────────────────────────

func writeNFO(v interface{}, path string) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}

	data, err := xml.MarshalIndent(v, "", "  ")
	if err != nil {
		return
	}

	content := []byte(xml.Header + string(data) + "\n")
	_ = os.WriteFile(path, content, 0o644)
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

func yearOf(dateStr string) string {
	if len(dateStr) >= 4 {
		return dateStr[:4]
	}
	return ""
}

func movieCertification(info *TMDBMovieDetail) string {
	for _, country := range info.ReleaseDates.Results {
		if country.ISO31661 == "US" {
			for _, rd := range country.ReleaseDates {
				if rd.Certification != "" {
					return rd.Certification
				}
			}
		}
	}
	return ""
}

func extractCredits(credits TMDBCredits) (actors []actorXML, directors []string, writers []string) {
	limit := 15
	if len(credits.Cast) < limit {
		limit = len(credits.Cast)
	}
	for _, person := range credits.Cast[:limit] {
		thumb := ""
		if person.ProfilePath != "" {
			thumb = "https://image.tmdb.org/t/p/original" + person.ProfilePath
		}
		actors = append(actors, actorXML{
			Name:   person.Name,
			Type:   "Actor",
			Role:   person.Character,
			TMDbID: fmt.Sprintf("%d", person.ID),
			Thumb:  thumb,
		})
	}
	for _, person := range credits.Crew {
		switch strings.ToLower(person.Job) {
		case "director":
			directors = append(directors, person.Name)
		case "writer", "screenplay", "story", "screenwriter":
			writers = append(writers, person.Name)
		}
	}
	return
}
