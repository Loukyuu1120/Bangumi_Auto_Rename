package rename

import (
	"fmt"
	"regexp"
	"strings"
)

// RenderContext holds all the variables available when rendering a rename template.
type RenderContext struct {
	Title         string
	OriginalTitle string
	Year          int
	Season        int
	Episode       int
	SeasonEpisode string // e.g. "S01E06"
	Part          string
	Episode_str   string // formatted episode string e.g. "06"
	VideoFormat   string // e.g. "1080p"
	FileExt       string // e.g. ".mkv"
	TMDBId        string
}

// RenderTemplate processes a Jinja2-like template string using the provided context.
// Supported tags:
//
//	{{variable}}           - variable substitution
//	{% if variable %}...{% endif %}  - conditional blocks (truthy check)
//	{% if variable %}...{% else %}...{% endif %}  - if/else blocks
func RenderTemplate(tmpl string, ctx RenderContext) string {
	// Build a variable map from the context struct
	vars := map[string]string{
		"title":          ctx.Title,
		"originalTitle":  ctx.OriginalTitle,
		"year":           yearStr(ctx.Year),
		"season":         seasonStr(ctx.Season),
		"episode":        episodeStr(ctx.Episode),
		"season_episode": ctx.SeasonEpisode,
		"part":           ctx.Part,
		"videoFormat":    ctx.VideoFormat,
		"fileExt":        ctx.FileExt,
		"tmdb_id":        ctx.TMDBId,
	}

	result := tmpl

	// Process {% if var %}...{% else %}...{% endif %} blocks (else branch)
	result = processIfElse(result, vars)

	// Process {% if var %}...{% endif %} blocks (no else)
	result = processIf(result, vars)

	// Replace {{variable}} placeholders
	result = replaceVars(result, vars)

	// Clean up any leftover whitespace artefacts from removed blocks
	result = cleanupResult(result)

	return result
}

// processIfElse handles {% if var %}...{% else %}...{% endif %} constructs.
func processIfElse(s string, vars map[string]string) string {
	// Pattern: {% if VAR %}THEN{% else %}ELSE{% endif %}
	re := regexp.MustCompile(`\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*else\s*%\}(.*?)\{%\s*endif\s*%\}`)
	return re.ReplaceAllStringFunc(s, func(match string) string {
		sub := re.FindStringSubmatch(match)
		if sub == nil {
			return match
		}
		varName := sub[1]
		thenBranch := sub[2]
		elseBranch := sub[3]

		if isTruthy(vars[varName]) {
			return thenBranch
		}
		return elseBranch
	})
}

// processIf handles {% if var %}...{% endif %} constructs (without else).
func processIf(s string, vars map[string]string) string {
	re := regexp.MustCompile(`\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}`)
	return re.ReplaceAllStringFunc(s, func(match string) string {
		sub := re.FindStringSubmatch(match)
		if sub == nil {
			return match
		}
		varName := sub[1]
		body := sub[2]

		if isTruthy(vars[varName]) {
			return body
		}
		return ""
	})
}

// replaceVars substitutes all {{varName}} placeholders.
func replaceVars(s string, vars map[string]string) string {
	re := regexp.MustCompile(`\{\{\s*(\w+)\s*\}\}`)
	return re.ReplaceAllStringFunc(s, func(match string) string {
		sub := re.FindStringSubmatch(match)
		if sub == nil {
			return match
		}
		if val, ok := vars[sub[1]]; ok {
			return val
		}
		// Unknown variable: remove it
		return ""
	})
}

// isTruthy reports whether a string value should be considered "true" in a
// template conditional — i.e. non-empty and not "0".
func isTruthy(s string) bool {
	return s != "" && s != "0"
}

// cleanupResult trims repeated slashes, spaces before separators, and similar
// artefacts that appear when optional blocks are removed.
func cleanupResult(s string) string {
	// Collapse multiple consecutive path separators
	s = regexp.MustCompile(`/{2,}`).ReplaceAllString(s, "/")

	// Remove trailing spaces before a path separator
	s = regexp.MustCompile(` +/`).ReplaceAllString(s, "/")

	// Remove leading/trailing spaces in each path component
	parts := strings.Split(s, "/")
	for i, p := range parts {
		parts[i] = strings.TrimSpace(p)
	}
	s = strings.Join(parts, "/")

	// Remove consecutive spaces
	s = regexp.MustCompile(` {2,}`).ReplaceAllString(s, " ")

	// Remove trailing dots or spaces from directory components (not from the
	// final extension)
	parts = strings.Split(s, "/")
	for i := 0; i < len(parts)-1; i++ {
		parts[i] = strings.TrimRight(parts[i], ". ")
	}
	s = strings.Join(parts, "/")

	return s
}

// ─────────────────────────────────────────────────────────────────────────────
// Formatting helpers
// ─────────────────────────────────────────────────────────────────────────────

func yearStr(y int) string {
	if y <= 0 {
		return ""
	}
	return fmt.Sprintf("%d", y)
}

func seasonStr(s int) string {
	if s <= 0 {
		return "1"
	}
	return fmt.Sprintf("%d", s)
}

func episodeStr(e int) string {
	if e <= 0 {
		return ""
	}
	return fmt.Sprintf("%02d", e)
}

// BuildSeasonEpisode returns the canonical SxxExx string (e.g. "S01E06").
func BuildSeasonEpisode(season, episode int) string {
	return fmt.Sprintf("S%02dE%02d", season, episode)
}

// BuildRenderContext constructs a RenderContext from raw components.
func BuildRenderContext(
	title string,
	originalTitle string,
	year int,
	season int,
	episode int,
	part string,
	videoFormat string,
	fileExt string,
	tmdbID string,
) RenderContext {
	se := ""
	if season > 0 && episode > 0 {
		se = BuildSeasonEpisode(season, episode)
	} else if episode > 0 {
		se = BuildSeasonEpisode(1, episode)
	}

	epStr := ""
	if episode > 0 {
		epStr = fmt.Sprintf("%02d", episode)
	}

	return RenderContext{
		Title:         SanitizeForPath(title),
		OriginalTitle: SanitizeForPath(originalTitle),
		Year:          year,
		Season:        season,
		Episode:       episode,
		SeasonEpisode: se,
		Part:          part,
		Episode_str:   epStr,
		VideoFormat:   videoFormat,
		FileExt:       fileExt,
		TMDBId:        tmdbID,
	}
}
