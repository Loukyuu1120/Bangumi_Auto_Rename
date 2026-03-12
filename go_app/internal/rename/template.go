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
	WebSource     string
	VideoCodec    string
	AudioCodec    string
	FPS           string
	Channels      string
	ReleaseGroup  string
	Customization string
	Edition       string
	HDR           string
	HDRFormat     string
}

// RenderTemplate processes a Jinja2-like template string using the provided context.
// Supported tags:
//
//	{{variable}}                                       - variable substitution
//	{{variable|replace('a','b')|replace("c","d")}}    - chained filters
//	{% if variable %}...{% endif %}                   - conditional blocks
//	{% if variable %}...{% elif other %}...{% else %}...{% endif %} - nested conditionals
func RenderTemplate(tmpl string, ctx RenderContext) string {
	vars := map[string]string{
		"title":          ctx.Title,
		"originalTitle":  ctx.OriginalTitle,
		"year":           yearStr(ctx.Year),
		"season":         seasonStr(ctx.Season),
		"episode":        episodeStr(ctx.Episode),
		"season_episode": ctx.SeasonEpisode,
		"part":           ctx.Part,
		"episode_str":    ctx.Episode_str,
		"videoFormat":    ctx.VideoFormat,
		"fileExt":        ctx.FileExt,
		"tmdb_id":        ctx.TMDBId,
		"tmdbid":         ctx.TMDBId,
		"webSource":      ctx.WebSource,
		"videoCodec":     ctx.VideoCodec,
		"audioCodec":     ctx.AudioCodec,
		"fps":            ctx.FPS,
		"channels":       ctx.Channels,
		"releaseGroup":   ctx.ReleaseGroup,
		"customization":  ctx.Customization,
		"edition":        ctx.Edition,
		"hdr":            ctx.HDR,
		"hdrFormat":      ctx.HDRFormat,
	}

	result := renderNodes(parseTemplate(tmpl), vars)
	result = cleanupResult(result)
	return result
}

type nodeType int

const (
	nodeText nodeType = iota
	nodeVar
	nodeIf
)

type templateNode struct {
	typ      nodeType
	text     string
	expr     string
	branches []ifBranch
}

type ifBranch struct {
	condition string // empty means else branch
	nodes     []templateNode
}

type parser struct {
	input string
	pos   int
	len   int
}

func parseTemplate(s string) []templateNode {
	p := &parser{
		input: s,
		len:   len(s),
	}
	nodes, _ := p.parseUntil(nil)
	return nodes
}

func (p *parser) parseUntil(stop map[string]bool) ([]templateNode, string) {
	var nodes []templateNode

	for p.pos < p.len {
		switch {
		case strings.HasPrefix(p.input[p.pos:], "{{"):
			expr, ok := p.readVarTag()
			if !ok {
				nodes = append(nodes, templateNode{typ: nodeText, text: p.input[p.pos:]})
				p.pos = p.len
				break
			}
			nodes = append(nodes, templateNode{typ: nodeVar, expr: expr})

		case strings.HasPrefix(p.input[p.pos:], "{%"):
			tag, ok := p.readBlockTag()
			if !ok {
				nodes = append(nodes, templateNode{typ: nodeText, text: p.input[p.pos:]})
				p.pos = p.len
				break
			}

			tagName, tagExpr := splitTag(tag)
			if stop != nil && stop[tagName] {
				return nodes, tag
			}

			if tagName == "if" {
				nodes = append(nodes, p.parseIf(tagExpr))
				continue
			}

			// Unknown standalone block tag: preserve as text.
			nodes = append(nodes, templateNode{typ: nodeText, text: "{% " + tag + " %}"})

		default:
			next := p.nextSpecial()
			nodes = append(nodes, templateNode{typ: nodeText, text: p.input[p.pos:next]})
			p.pos = next
		}
	}

	return nodes, ""
}

func (p *parser) parseIf(firstCond string) templateNode {
	n := templateNode{
		typ: nodeIf,
		branches: []ifBranch{
			{condition: strings.TrimSpace(firstCond)},
		},
	}

	for {
		body, stopTag := p.parseUntil(map[string]bool{
			"elif":  true,
			"else":  true,
			"endif": true,
		})
		n.branches[len(n.branches)-1].nodes = body

		if stopTag == "" {
			break
		}

		tagName, tagExpr := splitTag(stopTag)
		switch tagName {
		case "elif":
			n.branches = append(n.branches, ifBranch{
				condition: strings.TrimSpace(tagExpr),
			})
		case "else":
			elseBody, endTag := p.parseUntil(map[string]bool{
				"endif": true,
			})
			n.branches = append(n.branches, ifBranch{
				condition: "",
				nodes:     elseBody,
			})
			if endTag == "" {
				return n
			}
			return n
		case "endif":
			return n
		default:
			return n
		}
	}

	return n
}

func (p *parser) readVarTag() (string, bool) {
	start := p.pos + 2
	end := strings.Index(p.input[start:], "}}")
	if end < 0 {
		return "", false
	}
	end += start
	expr := strings.TrimSpace(p.input[start:end])
	p.pos = end + 2
	return expr, true
}

func (p *parser) readBlockTag() (string, bool) {
	start := p.pos + 2
	end := strings.Index(p.input[start:], "%}")
	if end < 0 {
		return "", false
	}
	end += start
	expr := strings.TrimSpace(p.input[start:end])
	p.pos = end + 2
	return expr, true
}

func (p *parser) nextSpecial() int {
	iVar := strings.Index(p.input[p.pos:], "{{")
	iBlock := strings.Index(p.input[p.pos:], "{%")

	next := p.len
	if iVar >= 0 {
		next = p.pos + iVar
	}
	if iBlock >= 0 && p.pos+iBlock < next {
		next = p.pos + iBlock
	}
	return next
}

func splitTag(tag string) (name string, expr string) {
	tag = strings.TrimSpace(tag)
	if tag == "" {
		return "", ""
	}
	parts := strings.Fields(tag)
	if len(parts) == 0 {
		return "", ""
	}
	name = parts[0]
	if len(parts) == 1 {
		return name, ""
	}
	expr = strings.TrimSpace(tag[len(name):])
	return name, expr
}

func renderNodes(nodes []templateNode, vars map[string]string) string {
	var sb strings.Builder
	for _, n := range nodes {
		switch n.typ {
		case nodeText:
			sb.WriteString(n.text)
		case nodeVar:
			sb.WriteString(evalVarExpr(n.expr, vars))
		case nodeIf:
			sb.WriteString(renderIfNode(n, vars))
		}
	}
	return sb.String()
}

func renderIfNode(n templateNode, vars map[string]string) string {
	for _, br := range n.branches {
		if br.condition == "" {
			return renderNodes(br.nodes, vars)
		}
		if evalCondition(br.condition, vars) {
			return renderNodes(br.nodes, vars)
		}
	}
	return ""
}

func evalCondition(expr string, vars map[string]string) bool {
	expr = strings.TrimSpace(expr)
	if expr == "" {
		return false
	}

	// Minimal truthy support for current templates:
	// - variable
	// - not variable
	if strings.HasPrefix(expr, "not ") {
		name := strings.TrimSpace(strings.TrimPrefix(expr, "not "))
		return !isTruthy(vars[name])
	}

	return isTruthy(evalVarExpr(expr, vars))
}

// evalVarExpr substitutes a variable and applies chained filters.
func evalVarExpr(expr string, vars map[string]string) string {
	parts := splitPipes(expr)
	if len(parts) == 0 {
		return ""
	}

	key := strings.TrimSpace(parts[0])
	if key == "" {
		return ""
	}

	val := vars[key]
	for _, f := range parts[1:] {
		val = applyFilter(strings.TrimSpace(f), val)
	}
	return val
}

func splitPipes(expr string) []string {
	var parts []string
	var sb strings.Builder
	inSingle := false
	inDouble := false
	escape := false

	for _, r := range expr {
		switch {
		case escape:
			sb.WriteRune(r)
			escape = false
		case r == '\\':
			sb.WriteRune(r)
			escape = true
		case r == '\'' && !inDouble:
			inSingle = !inSingle
			sb.WriteRune(r)
		case r == '"' && !inSingle:
			inDouble = !inDouble
			sb.WriteRune(r)
		case r == '|' && !inSingle && !inDouble:
			parts = append(parts, strings.TrimSpace(sb.String()))
			sb.Reset()
		default:
			sb.WriteRune(r)
		}
	}

	parts = append(parts, strings.TrimSpace(sb.String()))
	return parts
}

func applyFilter(filterExpr, value string) string {
	if filterExpr == "" {
		return value
	}

	name, args := parseFilterCall(filterExpr)
	switch strings.ToLower(name) {
	case "replace":
		if len(args) != 2 {
			return value
		}
		return strings.ReplaceAll(value, args[0], args[1])
	default:
		return value
	}
}

func parseFilterCall(s string) (string, []string) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", nil
	}

	open := strings.IndexByte(s, '(')
	close := strings.LastIndexByte(s, ')')
	if open < 0 || close < open {
		return s, nil
	}

	name := strings.TrimSpace(s[:open])
	argStr := s[open+1 : close]
	return name, parseArgs(argStr)
}

func parseArgs(s string) []string {
	var args []string
	var sb strings.Builder
	inSingle := false
	inDouble := false
	escape := false

	flush := func() {
		arg := strings.TrimSpace(sb.String())
		if unq, ok := unquoteArg(arg); ok {
			args = append(args, unq)
		} else if arg != "" {
			args = append(args, arg)
		} else {
			args = append(args, "")
		}
		sb.Reset()
	}

	for _, r := range s {
		switch {
		case escape:
			sb.WriteRune(r)
			escape = false
		case r == '\\':
			sb.WriteRune(r)
			escape = true
		case r == '\'' && !inDouble:
			inSingle = !inSingle
			sb.WriteRune(r)
		case r == '"' && !inSingle:
			inDouble = !inDouble
			sb.WriteRune(r)
		case r == ',' && !inSingle && !inDouble:
			flush()
		default:
			sb.WriteRune(r)
		}
	}
	flush()

	return args
}

func unquoteArg(s string) (string, bool) {
	s = strings.TrimSpace(s)
	if len(s) >= 2 {
		if s[0] == '\'' && s[len(s)-1] == '\'' {
			return strings.ReplaceAll(s[1:len(s)-1], `\'`, `'`), true
		}
		if s[0] == '"' && s[len(s)-1] == '"' {
			return strings.ReplaceAll(s[1:len(s)-1], `\"`, `"`), true
		}
	}
	return s, false
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
	media MediaInfo,
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

	audioCodec := strings.TrimSpace(media.AudioCodec)
	if audioCodec != "" && media.Channels != "" && !strings.Contains(audioCodec, media.Channels) {
		audioCodec = strings.TrimSpace(audioCodec + "." + media.Channels)
	}

	return RenderContext{
		Title:         SanitizeForPath(title),
		OriginalTitle: SanitizeForPath(originalTitle),
		Year:          year,
		Season:        season,
		Episode:       episode,
		SeasonEpisode: se,
		Part:          firstNonEmpty(part, media.Part),
		Episode_str:   epStr,
		VideoFormat:   firstNonEmpty(videoFormat, media.Resolution),
		FileExt:       fileExt,
		TMDBId:        tmdbID,
		WebSource:     SanitizeForPath(media.Source),
		VideoCodec:    SanitizeForPath(media.VideoCodec),
		AudioCodec:    SanitizeForPath(audioCodec),
		FPS:           SanitizeForPath(media.FPS),
		Channels:      SanitizeForPath(media.Channels),
		ReleaseGroup:  SanitizeForPath(media.Group),
		Customization: SanitizeForPath(media.QualityTag),
		Edition:       "",
		HDR:           SanitizeForPath(media.HDR),
		HDRFormat:     SanitizeForPath(media.HDR),
	}
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}
