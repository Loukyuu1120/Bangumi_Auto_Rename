package rename

import (
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

// VideoSuffix lists all recognised video file extensions (lowercase).
var VideoSuffix = map[string]bool{
	".strm": true, ".mp4": true, ".mkv": true, ".avi": true,
	".wmv": true, ".flv": true, ".mov": true, ".mpg": true,
	".mpeg": true, ".m4v": true, ".rm": true, ".rmvb": true,
	".ts": true, ".iso": true, ".m2ts": true,
}

// SubtitleSuffix lists common subtitle extensions.
var SubtitleSuffix = map[string]bool{
	".ass": true, ".srt": true, ".sub": true, ".ssa": true, ".vtt": true,
}

// IsVideoFile reports whether a file path has a recognised video extension.
func IsVideoFile(path string) bool {
	return VideoSuffix[strings.ToLower(filepath.Ext(path))]
}

// ignoreDirs lists directory name segments that should be skipped.
var ignoreDirs = []string{"cd", "scan"}

// extraTags are non-episode special content identifiers.
var extraTags = []string{
	"NCOP", "NCED", "Menu", "Teaser", "IV", "CM", "NC", "OP", "PV", "ED",
	"Advice", "Trailer", "Event", "Fans", "Preview", "Picture Drama",
	"访谈", "预告", "特典", "映像", "花絮", "采访",
}

// s0Tags indicate Season-0 / OVA content.
var s0TagPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bOVA\b`),
	regexp.MustCompile(`(?i)\bOAD\b`),
	regexp.MustCompile(`(?i)\bSpecial\b`),
	regexp.MustCompile(`(?i)\b[Ss][Pp]\b`),
	regexp.MustCompile(`(?i)\b00\b`),
	regexp.MustCompile(`\.5\b`),
	regexp.MustCompile(`(?i)Chaos no Kakera`),
	regexp.MustCompile(`总集篇`),
	regexp.MustCompile(`(?i)\bRecap\b`),
	regexp.MustCompile(`(?i)\bMovie\b`),
}

// keywordsToClean are encoding/release noise words.
var keywordsToClean = []string{
	"1080P", "FLAC", "简繁", "外挂", "MKV", "MP4", "TV", "全集",
	"HEVC", "8bit", "10bit", "720P", "2160P", "4K", "BD", "RIP",
	"DBD-raws", "Remux", "AVC", "H264", "H265", "DTS", "DTS-HD",
	"TrueHD", "Atmos", "HDR", "DV", "Dolby", "AAC", "AC3", "HQ",
	"Web-DL", "BluRay",
	// Streaming platform tags
	"ATVP", "AMZN", "NF", "DSNP", "HMAX", "PCOK", "PMTP",
	"HULU", "CRAV", "STAN", "ITVX", "RED",
	// Additional codecs / formats
	"DDP", "EAC3", "OPUS", "LPCM", "PCM", "MP3",
	"WEBRip", "HDTV", "BDRip", "DVDRip", "WEBDL",
	"内封简繁中字", "内封简繁", "中字",
}

// bracketPattern matches common bracket types and their contents.
var bracketPattern = regexp.MustCompile(`\[.*?\]|【.*?】|《.*?》|<.*?>|\(.*?\)|（.*?）`)

// codePatterns match technical encoding info that is noise in titles.
var codePatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)(2160|1080|720|480|576)[pP]`),
	regexp.MustCompile(`(?i)(x|h)[\W_]?26[45]|hevc|avc|mpeg2|vp9|av1`),
	regexp.MustCompile(`(?i)(dts-?hd|dts|truehd|atmos|ac3|aac|flac|opus|mp3|pcm|ddp|eac3)([\W_]*\d+\.\d)?(audio|ch|channel)?`),
	regexp.MustCompile(`(?i)\b\d{1,2}\.\d(audio|ch|channel|sound)\b`),
	regexp.MustCompile(`(?i)hdr|dv|dolby|10bit|8bit`),
	regexp.MustCompile(`(?i)remux|bluray|web-dl|webrip|hdtv|bdrip|dvdrip`),
	regexp.MustCompile(`(?i)hq|(\d{2,3})\s?fps`),
}

// seasonPatterns attempt to extract a season number from a title.
var seasonPatterns = []*regexp.Regexp{
	regexp.MustCompile(`[Ss]\s*([\d]{1,2})`),
	regexp.MustCompile(`第([0-9一二三四五六七八九零]{1,2})[季部分部]`),
	regexp.MustCompile(`([\d]{1,2})nd Season`),
	regexp.MustCompile(`(?i)Season\s*([\d]{1,2})`),
	regexp.MustCompile(`(?i)Series\s*([\d]{1,2})`),
	regexp.MustCompile(`(?i)(First|Second|Third|Fourth|Fifth) Season`),
	regexp.MustCompile(` (I{2,3})\b`),
	regexp.MustCompile(` (I{1,3}V)\b`),
	regexp.MustCompile(` (VI{2,3})\b`),
}

// episodePatterns attempt to extract episode (and optionally season) numbers.
var episodePatterns = []*regexp.Regexp{
	regexp.MustCompile(`第\s*(\d+)\s*季\s*(\d{1,3})`),      // 第1季06
	regexp.MustCompile(`[Ss]([\d]{1,2})[Ee]([\d]{1,3})`), // S01E06
	regexp.MustCompile(`[Ee]([\d]{1,3})`),                // E06
	regexp.MustCompile(`[Ee][Pp]([\d]{1,3})`),            // EP06
	regexp.MustCompile(`第([0-9一二三四五六七八九零]+)[话集]`),        // 第06集
	regexp.MustCompile(`([\d]{1,3})[Ee]pisode`),
	regexp.MustCompile(`([\d]{1,3})[Ee]ps`),
	regexp.MustCompile(`[\[【]\s*(\d{1,4})\s*(?:[vV]\d)?\s*[\]】]`), // [06] [12v2] 【03】
	regexp.MustCompile(`\[(\d{1,3})\]`),                           // [06]
	regexp.MustCompile(`【(\d{1,3})】`),                             // 【06】
	regexp.MustCompile(` - (\d{1,3})(?:\D|$)`),                    //  - 06
	regexp.MustCompile(`\s(\d{1,3})(?:\.\w{2,4})?$`),              // trailing number
}

var numMap = map[string]int{
	"First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5,
}
var romaMap = map[string]int{
	"II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
}

var cnNum = map[rune]int{
	'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
	'六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
}

// ChineseToNumber converts a Chinese numeral string to an integer.
func ChineseToNumber(s string) int {
	result := 0
	current := 0
	for _, ch := range s {
		if v, ok := cnNum[ch]; ok {
			switch {
			case v == 10:
				if current == 0 {
					current = 1
				}
				result += current * 10
				current = 0
			case v < 10:
				current = v
			}
		}
	}
	result += current
	return result
}

// IsDigit reports whether every rune in s is an ASCII digit.
func IsDigit(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !unicode.IsDigit(r) {
			return false
		}
	}
	return true
}

// RemoveTag strips the first occurrence of tag (case-insensitive) from title.
func RemoveTag(title, tag string) string {
	re := regexp.MustCompile(`(?i)` + regexp.QuoteMeta(tag))
	return strings.TrimSpace(re.ReplaceAllString(title, ""))
}

func cleanTitleCaseInsensitive(title string) string {
	lowerKeywords := make([]string, 0, len(keywordsToClean))
	for _, kw := range keywordsToClean {
		lowerKeywords = append(lowerKeywords, strings.ToLower(kw))
	}
	j := strings.Join(lowerKeywords, "|")
	keywordRe := regexp.MustCompile(j)
	cleaned := title
	for _, pattern := range []string{
		`\[.*?\]`, `【.*?】`, `《.*?》`, `<.*?>`, `\(.*?\)`, `（.*?）`,
	} {
		matches := regexp.MustCompile(pattern).FindAllString(cleaned, -1)
		for _, match := range matches {
			if keywordRe.MatchString(strings.ToLower(match)) {
				cleaned = strings.ReplaceAll(cleaned, match, "")
			}
		}
	}
	cleaned = strings.TrimSpace(cleaned)
	if strings.HasPrefix(cleaned, "[") && strings.HasSuffix(cleaned, "]") && len(cleaned) > 2 {
		return strings.TrimSpace(cleaned[1 : len(cleaned)-1])
	}
	return cleaned
}

// RemoveBracketTags removes bracketed segments. When skip is true, keeps the
// 2nd occurrence of each bracket pattern (Python behavior).
func RemoveBracketTags(title string, skip bool) string {
	s := title
	if skip {
		for _, pattern := range []string{
			`\[.*?\]`, `【.*?】`, `《.*?》`, `<.*?>`, `\(.*?\)`, `（.*?）`,
		} {
			count := 0
			re := regexp.MustCompile(pattern)
			s = re.ReplaceAllStringFunc(s, func(m string) string {
				count++
				if count == 2 {
					return m
				}
				return ""
			})
		}
	} else {
		s = bracketPattern.ReplaceAllString(s, "")
	}
	s = strings.TrimSpace(s)
	if s == "" {
		s = cleanTitleCaseInsensitive(title)
	}
	return strings.TrimSpace(s)
}

// CleanNoise removes common encoding noise words from a title.
func CleanNoise(title string) string {
	// Remove video file extension if present
	if ext := strings.ToLower(filepath.Ext(title)); ext != "" {
		if VideoSuffix[ext] {
			title = strings.TrimSuffix(title, filepath.Ext(title))
		}
	}

	// Remove code patterns
	for _, p := range codePatterns {
		title = p.ReplaceAllString(title, " ")
	}

	// Remove release-group suffixes like "-ABC"
	title = regexp.MustCompile(`-[A-Za-z0-9]+$`).ReplaceAllString(title, "")

	// Remove known noise keywords (case-insensitive)
	for _, kw := range keywordsToClean {
		re := regexp.MustCompile(`(?i)\b` + regexp.QuoteMeta(kw) + `\b`)
		title = re.ReplaceAllString(title, " ")
	}

	// Collapse whitespace and trim
	wsRe := regexp.MustCompile(`\s{2,}`)
	title = wsRe.ReplaceAllString(strings.TrimSpace(title), " ")
	return title
}

// RemoveSeason removes season markers from a string.
func RemoveSeason(s string) string {
	for _, p := range seasonPatterns {
		s = p.ReplaceAllString(s, "")
	}
	return strings.TrimSpace(s)
}

// RemoveEpisode removes episode markers from a string.
func RemoveEpisode(s string) string {
	for _, p := range episodePatterns {
		s = p.ReplaceAllString(s, "")
	}
	return strings.TrimSpace(s)
}

// RemoveCode removes technical code patterns.
func RemoveCode(s string) string {
	for _, p := range codePatterns {
		s = p.ReplaceAllString(s, "")
	}
	return s
}

// ParseSearchName extracts a clean search title and year from a raw filename.
// It mirrors the Python parser: truncate at year/season markers and strip
// trailing technical tags to keep only the show/movie title.
func ParseSearchName(input string) (string, int) {
	name := filepath.Base(input)
	if ext := strings.ToLower(filepath.Ext(name)); ext != "" {
		if VideoSuffix[ext] {
			name = strings.TrimSuffix(name, filepath.Ext(name))
		}
	}

	clean := name
	year := 0
	yearRe := regexp.MustCompile(`(?i)(?:[.\s\-_\(\[（【]|^)([12][90]\d{2})(?:[.\s\-_\)\]）】]|$)`)
	if idxs := yearRe.FindAllStringSubmatchIndex(clean, -1); len(idxs) > 0 {
		// Prefer the last year occurrence (Python behavior in multi-year names)
		idx := idxs[len(idxs)-1]
		yearStr := clean[idx[2]:idx[3]]
		if y, err := strconv.Atoi(yearStr); err == nil {
			year = y
		}
		if idx[2] > 0 {
			clean = clean[:idx[2]]
		}
	}

	// Remove bracket content/tags
	clean = RemoveBracketTags(clean, false)
	// Go RE2 doesn't support lookahead; use a safe fallback to drop "A <CJK>" prefixes.
	clean = regexp.MustCompile(`^[A-Za-z]\s+[\p{Han}]`).ReplaceAllStringFunc(clean, func(s string) string {
		parts := strings.Fields(s)
		if len(parts) >= 2 {
			return parts[len(parts)-1]
		}
		return ""
	})

	// Truncate at season/episode markers
	seasonMarkers := []*regexp.Regexp{
		regexp.MustCompile(`(?i)[.\s\-_]S(\d{1,2})(?:E|EP)(\d{1,3})`),
		regexp.MustCompile(`(?i)[.\s\-_]S(\d{1,2})\s*-\s*S?(\d{1,2})`),
		regexp.MustCompile(`(?i)[.\s\-_]S(\d{1,2})(?:$|[.\s\-_])`),
		regexp.MustCompile(`(?i)[.\s\-_]Season[.\s\-_]*(\d{1,2})`),
		regexp.MustCompile(`(?i)[.\s\-_]?第\s*(\d+|[零一二三四五六七八九十百千万]+)\s*季`),
	}
	cutAt := -1
	for _, re := range seasonMarkers {
		if loc := re.FindStringIndex(clean); loc != nil {
			if cutAt == -1 || loc[0] < cutAt {
				cutAt = loc[0]
			}
		}
	}
	if cutAt >= 0 {
		clean = clean[:cutAt]
	}

	// If no year/season, truncate at resolution or codec markers
	if year == 0 && cutAt < 0 {
		resRe := regexp.MustCompile(`(?i)[.\s\-_](1080p|2160p|720p|480p|576p|4k)`)
		if loc := resRe.FindStringIndex(clean); loc != nil {
			clean = clean[:loc[0]]
		} else {
			codecRe := regexp.MustCompile(`(?i)[.\s\-_]((x|h)\.?26[45]|hevc|avc)`)
			if loc := codecRe.FindStringIndex(clean); loc != nil {
				clean = clean[:loc[0]]
			}
		}
	}

	clean = strings.ReplaceAll(clean, "：", " ")
	clean = strings.ReplaceAll(clean, ":", " ")
	clean = strings.ReplaceAll(clean, "（", " ")
	clean = strings.ReplaceAll(clean, "）", " ")
	clean = strings.NewReplacer(".", " ", "_", " ").Replace(clean)
	clean = regexp.MustCompile(`\s+`).ReplaceAllString(clean, " ")
	clean = strings.Trim(clean, " .-[]()（）")

	return clean, year
}

// ExtractSeason tries to parse a season number from a string.
// Returns (season, found).
func ExtractSeason(s string) (int, bool) {
	s = RemoveCode(s)
	for _, p := range s0TagPatterns {
		if p.MatchString(s) {
			return 0, true
		}
	}
	for i, p := range seasonPatterns {
		m := p.FindStringSubmatch(s)
		if m == nil {
			continue
		}
		switch i {
		case 0, 2, 3, 4: // numeric group
			if n, err := strconv.Atoi(m[1]); err == nil {
				return n, true
			}
		case 1: // Chinese numeral
			n := ChineseToNumber(m[1])
			if n > 0 {
				return n, true
			}
		case 5: // First/Second/…
			if n, ok := numMap[m[1]]; ok {
				return n, true
			}
		case 6, 7, 8: // Roman II, III, IV, VI, VII
			if n, ok := romaMap[strings.TrimSpace(m[1])]; ok {
				return n, true
			}
		}
	}
	return 1, false
}

// EpisodeInfo holds the result of episode extraction.
type EpisodeInfo struct {
	Season  int
	Episode int
	Found   bool
}

// ExtractEpisode tries to extract season and episode numbers from a filename stem.
func ExtractEpisode(stem string) EpisodeInfo {
	// Pattern 0: 第N季M集
	if m := episodePatterns[0].FindStringSubmatch(stem); m != nil {
		s, _ := strconv.Atoi(m[1])
		e, _ := strconv.Atoi(m[2])
		return EpisodeInfo{Season: s, Episode: e, Found: true}
	}
	// Pattern 1: SxxExx
	if m := episodePatterns[1].FindStringSubmatch(stem); m != nil {
		s, _ := strconv.Atoi(m[1])
		e, _ := strconv.Atoi(m[2])
		return EpisodeInfo{Season: s, Episode: e, Found: true}
	}
	// Patterns 2-9: episode only
	for i := 2; i < len(episodePatterns); i++ {
		m := episodePatterns[i].FindStringSubmatch(stem)
		if m == nil {
			continue
		}
		capGroup := m[1]
		// Chinese numerals
		if !IsDigit(capGroup) {
			n := ChineseToNumber(capGroup)
			if n > 0 && isValidEpisodeNumber(n) {
				return EpisodeInfo{Episode: n, Found: true}
			}
			continue
		}
		if n, err := strconv.Atoi(capGroup); err == nil && n > 0 && isValidEpisodeNumber(n) {
			return EpisodeInfo{Episode: n, Found: true}
		}
	}
	return EpisodeInfo{}
}

func isValidEpisodeNumber(n int) bool {
	if n <= 0 {
		return false
	}
	if n == 480 || n == 576 || n == 720 || n == 1080 || n == 2160 || n == 264 || n == 265 {
		return false
	}
	if n > 1900 && n < 2100 {
		return false
	}
	return true
}

// ExtractTMDBID looks for patterns like {tmdb-12345} or [tmdbid=12345] and returns the ID string.
func ExtractTMDBID(s string) string {
	// Matches tmdb=123, [tmdb=123], {tmdb:123}, 【tmdb-123】, tmdbid:123, etc.
	patterns := []*regexp.Regexp{
		regexp.MustCompile(`(?i)[\[\(\\{【［]?\s*tmdb(?:id)?\s*[-:=]\s*(\d+)\s*[\]\)\\}】］]?`),
		regexp.MustCompile(`(?i)\btmdb(?:id)?\s*(\d+)\b`),
	}
	for _, re := range patterns {
		if m := re.FindStringSubmatch(s); m != nil {
			return m[1]
		}
	}
	return ""
}

// IsWeakFilename reports whether a stem looks like a poor search keyword
// (pure episode numbers, very short, all digits, etc.).
func IsWeakFilename(stem string) bool {
	clean := strings.TrimSpace(stem)
	if len(clean) <= 3 {
		return true
	}
	if IsDigit(clean) {
		return true
	}
	if regexp.MustCompile(`(?i)^[Ee](P)?\d+(\s*v\d+)?$`).MatchString(clean) {
		return true
	}
	// Looks purely like SxxExx
	if regexp.MustCompile(`(?i)^[Ss]\d{1,2}[Ee](P)?\d{1,3}$`).MatchString(clean) {
		return true
	}
	if len([]rune(clean)) <= 4 && !containsChinese(clean) {
		return true
	}
	return false
}

func containsChinese(s string) bool {
	for _, r := range s {
		if r >= 0x4E00 && r <= 0x9FFF {
			return true
		}
	}
	return false
}

// IsSeasonName reports whether a directory name looks like a season folder
// (e.g. "Season 1", "S02", "第一季").
func IsSeasonName(name string) bool {
	stem := strings.TrimSpace(name)
	if regexp.MustCompile(`^\d{1,2}$`).MatchString(stem) {
		return true
	}
	if regexp.MustCompile(`(?i)^S\d+$`).MatchString(stem) {
		return true
	}
	if regexp.MustCompile(`(?i)^(S|Season)\s*\d+\s*-\s*(S|Season)?\s*\d+$`).MatchString(stem) {
		return true
	}
	if regexp.MustCompile(`(?i)^(SP|OVA|specials?)$`).MatchString(stem) {
		return true
	}
	if regexp.MustCompile(`(?i)^(Season|S)\s*\d+([ ._-]|$)`).MatchString(stem) {
		return true
	}
	for _, p := range seasonPatterns {
		remain := strings.TrimSpace(p.ReplaceAllString(stem, ""))
		if remain == "" || regexp.MustCompile(`[.\-_\[\]\(\)\s]+`).MatchString(remain) {
			return true
		}
	}
	return false
}

// ParsedFile holds structured metadata extracted from a raw filename.
type ParsedFile struct {
	Title   string
	Year    int
	Season  int
	Episode int
	Part    string // e.g. "Part1"
	IsExtra bool
	IsS0    bool
}

// ParseFilename extracts structured information from a video filename stem.
func ParseFilename(stem string) ParsedFile {
	result := ParsedFile{Season: 1}

	// 1. Check for extra / S0 tags first
	upperStem := strings.ToUpper(stem)
	for _, tag := range extraTags {
		if strings.Contains(upperStem, strings.ToUpper(tag)) {
			result.IsExtra = true
			return result
		}
	}
	for _, p := range s0TagPatterns {
		if p.MatchString(stem) {
			result.IsS0 = true
		}
	}

	// 2. Extract year
	yearRe := regexp.MustCompile(`\((\d{4})\)`)
	if m := yearRe.FindStringSubmatch(stem); m != nil {
		if y, err := strconv.Atoi(m[1]); err == nil && y > 1900 && y < 2100 {
			result.Year = y
			stem = strings.Replace(stem, m[0], "", 1)
		}
	}

	// 3. Extract episode info
	ep := ExtractEpisode(stem)
	if ep.Found {
		result.Episode = ep.Episode
		if ep.Season > 0 {
			result.Season = ep.Season
		}
	}

	// 4. Extract season if not already set by SxxExx
	if ep.Season == 0 {
		if s, ok := ExtractSeason(stem); ok {
			result.Season = s
		}
	}

	// 5. Clean noise and set title
	title := CleanNoise(stem)
	// Remove season/episode markers from title
	title = seasonPatterns[0].ReplaceAllString(title, "")
	for _, p := range episodePatterns {
		title = p.ReplaceAllString(title, "")
	}
	title = strings.NewReplacer("-", " ", "_", " ", ".", " ").Replace(title)
	wsRe := regexp.MustCompile(`\s{2,}`)
	result.Title = strings.TrimSpace(wsRe.ReplaceAllString(title, " "))

	return result
}

// MediaInfo holds extracted video/audio technical metadata strings.
type MediaInfo struct {
	Source     string
	VideoCodec string
	AudioCodec string
	HDR        string
	QualityTag string
	Resolution string
	FPS        string
	Channels   string
	Group      string
	Part       string
}

var mediaMapping = map[string]map[string][]string{
	"source": {
		"Remux":  {"REMUX"},
		"BluRay": {"BLURAY", "BLU-RAY", " BD ", " BD-", ".BD.", "BDMV"},
		"BDRip":  {"BDRIP"},
		"UHD-BD": {"UHD-BD", "UHD BLURAY", "UHD.BLURAY", "ULTRAHD BLURAY", "2160P BLURAY"},
		"WEB-DL": {"WEB-DL", "WEBDL", "WEB DL"},
		"WEBRip": {"WEBRIP", "WEB-RIP", "WEB RIP"},
		"HDTV":   {"HDTV"},
		"DVDRip": {"DVDRIP"},
		"DVD":    {"DVD", "NTSC", "PAL"},
		"HDCam":  {"HDCAM"},
		"CAM":    {" CAM ", ".CAM.", "-CAM"},
		"TS":     {" TELESYNC ", ".TS.", "-TS", "HDTS"},
		"TC":     {" TELECINE ", ".TC.", "-TC"},
		"PDTV":   {"PDTV"},
		"SATRip": {"SATRIP"},
		"TVRip":  {"TVRIP"},
	},
	"video_codec": {
		"x264":  {"X264", "H264", "AVC"},
		"x265":  {"X265", "H265", "HEVC"},
		"MPEG2": {"MPEG2"},
		"AV1":   {"AV1"},
		"VP9":   {"VP9"},
		"VC-1":  {"VC-1", "VC1"},
		"Hi10P": {"HI10P"},
		"10bit": {"10BIT", "YUV420P10", "MAIN10"},
		"8bit":  {"8BIT"},
	},
	"audio_codec": {
		"TrueHD Atmos": {"TRUEHD.ATMOS", "TRUEHD ATMOS", "TRUEHD+ATMOS", "ATMOS TRUEHD"},
		"DDP Atmos":    {"DDP.ATMOS", "DDP ATMOS", "EAC3 ATMOS", "E-AC-3 ATMOS", "DD+.ATMOS", "DD+ ATMOS"},
		"DTS:X":        {"DTS:X", "DTSX"},
		"DTS-HD MA":    {"DTS-HD.MA", "DTS-HD MA", "DTSHD.MA", "DTSHD MA", "DTS-HD", "DTSHD"},
		"DTS-HD HRA":   {"DTS-HRA", "DTS HD HRA", "DTS-HD HRA"},
		"DTS-ES":       {"DTS-ES"},
		"DTS":          {"DTS"},
		"TrueHD":       {"TRUEHD"},
		"DDP":          {"DDP", "EAC3", "E-AC-3", "DD+", "DDPLUS"},
		"AC3":          {"AC3", "DD ", "DD5", "DD2"},
		"AAC":          {"AAC"},
		"FLAC":         {"FLAC"},
		"Opus":         {"OPUS"},
		"MP3":          {"MP3"},
		"PCM":          {"LPCM", "PCM"},
	},
	"hdr": {
		"Dolby Vision": {"DOLBY VISION", "DOVI", "DV"},
		"HDR10+":       {"HDR10+"},
		"HDR10":        {"HDR10"},
		"HLG":          {"HLG"},
		"HDR":          {"HDR"},
	},
	"quality_tag": {
		"Proper":         {"PROPER"},
		"REPACK":         {"REPACK"},
		"RERIP":          {"RERIP"},
		"Hybrid":         {"HYBRID"},
		"Extended":       {"EXTENDED"},
		"Director's Cut": {"DIRECTOR'S CUT", "DIRECTORS CUT", "DC"},
		"Criterion":      {"CRITERION"},
		"Dubbed":         {"DUBBED"},
		"Dual Audio":     {"DUAL AUDIO", "DUALAUDIO"},
		"Multi Audio":    {"MULTI AUDIO", "MULTIAUDIO"},
		"Atmos":          {"ATMOS"},
	},
}

// ExtractMediaInfo parses encoding metadata from a raw filename.
func ExtractMediaInfo(filename string) MediaInfo {
	upper := strings.ToUpper(filename)
	info := MediaInfo{}

	for category, entries := range mediaMapping {
		var detected []string
		for label, tokens := range entries {
			for _, token := range tokens {
				if strings.Contains(upper, strings.ToUpper(token)) {
					detected = append(detected, label)
					break
				}
			}
		}

		if len(detected) == 0 {
			continue
		}

		switch category {
		case "source":
			info.Source = detected[0]
		case "video_codec":
			info.VideoCodec = strings.Join(detected, " ")
		case "audio_codec":
			info.AudioCodec = strings.Join(detected, " ")
		case "hdr":
			info.HDR = strings.Join(detected, " ")
		case "quality_tag":
			info.QualityTag = strings.Join(detected, " ")
		}
	}

	// Resolution
	resRe := regexp.MustCompile(`(?i)(2160|1080|720|576|480)[pPiI]|4K|8K`)
	if m := resRe.FindStringSubmatch(filename); m != nil {
		info.Resolution = strings.ToLower(strings.ReplaceAll(m[0], "I", "i"))
	}

	// FPS
	fpsRe := regexp.MustCompile(`(?i)(\d{2,3})\s?FPS`)
	if m := fpsRe.FindStringSubmatch(filename); m != nil {
		info.FPS = strings.ToLower(m[1] + "fps")
	}

	// Channels
	chanRe := regexp.MustCompile(`(?i)\b([1-9]\.\d)\b`)
	if m := chanRe.FindStringSubmatch(filename); m != nil {
		info.Channels = m[1]
	}

	// Common audio+channel combined forms when plain channel regex misses
	if info.Channels == "" {
		audioChanRe := regexp.MustCompile(`(?i)\b(?:DDP|DD\+|EAC3|E-AC-3|AC3|AAC|DTS(?:[-.: ]?HD)?|TRUEHD|FLAC|OPUS|PCM)[ ._-]*([1-9]\.\d)\b`)
		if m := audioChanRe.FindStringSubmatch(filename); m != nil {
			info.Channels = m[1]
		}
	}

	// Release group
	groupRe := regexp.MustCompile(`-([a-zA-Z0-9_]+)(?:\[.*?\])?(?:\.[a-zA-Z0-9]{2,4})?$`)
	if m := groupRe.FindStringSubmatch(filename); m != nil {
		grp := m[1]
		upperGrp := strings.ToUpper(grp)
		if upperGrp != "DL" && upperGrp != "RIP" && upperGrp != "H264" && upperGrp != "H265" &&
			upperGrp != "HEVC" && upperGrp != "AAC" && upperGrp != "AC3" && upperGrp != "DDP" &&
			upperGrp != "TRUEHD" && upperGrp != "DTS" && upperGrp != "ATMOS" &&
			upperGrp != "MKV" && upperGrp != "MP4" && upperGrp != "STRM" && upperGrp != "ASS" {
			info.Group = grp
		}
	}

	// Part / disc
	partRe := regexp.MustCompile(`(?i)(?:CD|PART|DISC)\s?(\d+)`)
	if m := partRe.FindStringSubmatch(filename); m != nil {
		info.Part = "CD" + m[1]
	}

	return info
}

// SanitizeForPath replaces characters that are invalid in filesystem paths.
func SanitizeForPath(s string) string {
	invalid := `<>:"/\|?*`
	for _, c := range invalid {
		s = strings.ReplaceAll(s, string(c), "")
	}
	// Collapse multiple spaces / dots
	s = regexp.MustCompile(`\.{2,}`).ReplaceAllString(s, ".")
	s = regexp.MustCompile(` {2,}`).ReplaceAllString(s, " ")
	return strings.TrimSpace(s)
}

// IsChinesePercentageSufficient reports whether at least pct% of runes in s
// are CJK unified ideographs.
func IsChinesePercentageSufficient(s string, pct float64) bool {
	if s == "" {
		return false
	}
	total := 0
	chinese := 0
	for _, r := range s {
		total++
		if r >= 0x4E00 && r <= 0x9FFF {
			chinese++
		}
	}
	if total == 0 {
		return false
	}
	return float64(chinese)/float64(total) >= pct/100.0
}

// ToSimplifiedMax is a placeholder for CJK simplification.
// Full conversion requires an external library; here we just pass through.
func ToSimplifiedMax(s string) string {
	return s
}

// DivideByYear splits a title string that might embed a year like "Title (2023)"
// into title and year components.
func DivideByYear(s string) (title string, year int) {
	re := regexp.MustCompile(`^(.*?)\s*\((\d{4})\)\s*$`)
	if m := re.FindStringSubmatch(s); m != nil {
		if y, err := strconv.Atoi(m[2]); err == nil {
			return strings.TrimSpace(m[1]), y
		}
	}
	return strings.TrimSpace(s), 0
}
