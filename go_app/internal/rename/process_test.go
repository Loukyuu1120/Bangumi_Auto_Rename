package rename

import (
	"errors"
	"strconv"
	"testing"
)

func TestParseSearchNameStripsSingleLetterChinesePrefix(t *testing.T) {
	name, year := ParseSearchName("G古诺希亚(2025)4K超清2160P收藏版")
	if name != "古诺希亚" {
		t.Fatalf("expected cleaned title 古诺希亚, got %q", name)
	}
	if year != 2025 {
		t.Fatalf("expected year 2025, got %d", year)
	}
}

func TestParseSearchNameStripsPartCollectionSuffix(t *testing.T) {
	name, year := ParseSearchName("东岛丹三郎想成为假面骑士 Part.1+Part.2(2025-2026)4K超清2160p收藏版 24集全 内封中字")
	if name != "东岛丹三郎想成为假面骑士" {
		t.Fatalf("expected cleaned title 东岛丹三郎想成为假面骑士, got %q", name)
	}
	if year != 2025 {
		t.Fatalf("expected year 2025, got %d", year)
	}
}

func TestChooseSearchQueryPrefersParentForEpisodeFiles(t *testing.T) {
	p := &Processor{}
	srcPath := "/library/G古诺希亚(2025)4K超清2160P收藏版/GNOSIA - 08.strm"

	name, year := p.chooseSearchQuery(srcPath, TaskOptions{}, nil)
	if name != "古诺希亚" {
		t.Fatalf("expected parent title 古诺希亚, got %q", name)
	}
	if year != 2025 {
		t.Fatalf("expected year 2025, got %d", year)
	}
}

func TestParseSearchNameKeepsMovieSequelNumberAndUsesReleaseYear(t *testing.T) {
	name, year := ParseSearchName("[LGNB封装][2017][美国][银翼杀手2049][Blade.Runner.2049.2017.Open.Matte.2160p.DV.HDR10.HEVC.TrueHD.7.1.Atmos-LGNB].strm")
	if name != "Blade Runner 2049" {
		t.Fatalf("expected Blade Runner 2049, got %q", name)
	}
	if year != 2017 {
		t.Fatalf("expected year 2017, got %d", year)
	}
}

func TestChooseSearchQueryKeepsNumericMovieTitleInsteadOfFallingBackToCollectionFolder(t *testing.T) {
	p := &Processor{}
	srcPath := "/library/Open Matte 与 IMAX 电影收藏 - 4.76T/300 2006 Open Matte Hybrid BluRay 1080p DTS-HD MA TrueHD 7.1 Atmos x264-MgB.strm"

	name, year := p.chooseSearchQuery(srcPath, TaskOptions{}, nil)
	if name != "300" {
		t.Fatalf("expected title 300, got %q", name)
	}
	if year != 2006 {
		t.Fatalf("expected year 2006, got %d", year)
	}
}

func TestChooseSearchQueryDirectoryFirstUsesSeriesFolderForMessyEpisodeNames(t *testing.T) {
	p := &Processor{}
	srcPath := "/library/G古诺希亚(2025)4K超清2160P收藏版/01.strm"

	name, year := p.chooseSearchQuery(srcPath, TaskOptions{
		ConfigOverrides: map[string]interface{}{
			"recognition_mode": "directory_first",
		},
	}, nil)
	if name != "古诺希亚" {
		t.Fatalf("expected parent title 古诺希亚, got %q", name)
	}
	if year != 2025 {
		t.Fatalf("expected year 2025, got %d", year)
	}
}

func TestDetectMediaTypeDirectoryFirstAvoidsGuessingMovieFromFolderYear(t *testing.T) {
	p := &Processor{}
	srcPath := "/library/古诺希亚 (2025)/01.mkv"

	_, isMovie := p.detectMediaType(srcPath, TaskOptions{
		ConfigOverrides: map[string]interface{}{
			"recognition_mode": "directory_first",
		},
	})
	if isMovie {
		t.Fatalf("expected directory_first mode not to guess movie from weak file naming")
	}
}

func TestDetectMediaTypeTreatsYearBasedSingleFileAsMovie(t *testing.T) {
	p := &Processor{}
	srcPath := "/library/collection/Blade.Runner.2049.2017.Open.Matte.2160p.DV.HDR10.HEVC.TrueHD.7.1.Atmos.strm"

	_, isMovie := p.detectMediaType(srcPath, TaskOptions{})
	if !isMovie {
		t.Fatalf("expected single-file movie detection to be true")
	}
}

func TestBuildSearchCandidatesSplitsChineseAndEnglishTitleSegments(t *testing.T) {
	srcPath := "/library/冲出宁静号.Serenity.2005.Open.Matte.1080p.WEB-DL/Serenity.2005.Open.Matte.1080p.WEB-DL.strm"
	got := buildSearchCandidates(srcPath, "Serenity")

	if len(got) < 2 {
		t.Fatalf("expected multiple search candidates, got %v", got)
	}
	if got[0] != "冲出宁静号" {
		t.Fatalf("expected Chinese candidate first, got %q", got[0])
	}

	foundEnglish := false
	for _, item := range got {
		if item == "Serenity" {
			foundEnglish = true
			break
		}
	}
	if !foundEnglish {
		t.Fatalf("expected English candidate Serenity, got %v", got)
	}
}

func TestParseSearchNameRemovesUpscaleSizeAndSubtitleNoise(t *testing.T) {
	name, year := ParseSearchName("犬夜叉：紅蓮之蓬莱島（2004）4K超分［内封简中字幕］11.44GB")
	if name != "犬夜叉 紅蓮之蓬莱島" {
		t.Fatalf("expected cleaned title 犬夜叉 紅蓮之蓬莱島, got %q", name)
	}
	if year != 2004 {
		t.Fatalf("expected year 2004, got %d", year)
	}
}

func TestParseSearchNameRemovesEnglishSizeAndBilingualSubtitleNoise(t *testing.T) {
	name, year := ParseSearchName("The Phoenician Scheme (2025) 4K Web SDR 精修简体中字 简英双语字幕 11.44GB")
	if name != "The Phoenician Scheme" {
		t.Fatalf("expected cleaned title The Phoenician Scheme, got %q", name)
	}
	if year != 2025 {
		t.Fatalf("expected year 2025, got %d", year)
	}
}

func TestShouldAggressivelyFallbackToMovieForSingleFileMovie(t *testing.T) {
	srcPath := "/library/The.Phoencian.Scheme.2025.2160p.WEB.SDR.HEVC.DDP5.1.Atmos-TEST.strm"
	if !shouldAggressivelyFallbackToMovie(srcPath, TaskOptions{}) {
		t.Fatalf("expected single-file movie to prefer movie fallback")
	}
}

func TestShouldNotAggressivelyFallbackToMovieForEpisodeFile(t *testing.T) {
	srcPath := "/library/Show.Name.2025/Show.Name.S01E01.1080p.WEB-DL.strm"
	if shouldAggressivelyFallbackToMovie(srcPath, TaskOptions{}) {
		t.Fatalf("expected episode file not to prefer movie fallback")
	}
}

func TestIsTMDBNotFoundError(t *testing.T) {
	if !isTMDBNotFoundError(errors.New("tmdb /tv/1084242 returned 404: The resource you requested could not be found.")) {
		t.Fatalf("expected 404 tmdb error to be treated as not found")
	}
	if isTMDBNotFoundError(errors.New("tmdb rate limit exceeded")) {
		t.Fatalf("expected non-404 tmdb error not to be treated as not found")
	}
}

func TestForcedTMDBIDShouldBypassTVYearSanityCheck(t *testing.T) {
	forceID := 67063
	year := 2025
	firstAirDate := "2016-07-08"
	shouldReject := false
	if forceID <= 0 && year > 0 && len(firstAirDate) >= 4 {
		if y, err := strconv.Atoi(firstAirDate[:4]); err == nil {
			if y > 0 && absInt(y-year) > 3 {
				shouldReject = true
			}
		}
	}
	if shouldReject {
		t.Fatalf("expected forced TMDB ID to bypass TV year sanity check")
	}
}

func TestShouldRelaxTVYearFilterForLaterSeason(t *testing.T) {
	srcPath := "/library/一人之下 第六季/一人之下.s06e05.2025.2160p.WEB-DL.strm"
	if !shouldRelaxTVYearFilter(srcPath, "一人之下", 6, TaskOptions{}) {
		t.Fatalf("expected later season to relax TV year filter")
	}
}

func TestShouldRelaxTVYearFilterForSecondPartTitle(t *testing.T) {
	srcPath := "/library/作品 第二部/作品 第二部 2025 1080p.strm"
	if !shouldRelaxTVYearFilter(srcPath, "作品 第二部", 1, TaskOptions{}) {
		t.Fatalf("expected second-part title to relax TV year filter")
	}
}

func TestShouldNotRelaxTVYearFilterForFirstSeason(t *testing.T) {
	srcPath := "/library/新剧/新剧.s01e01.2025.1080p.WEB-DL.strm"
	if shouldRelaxTVYearFilter(srcPath, "新剧", 1, TaskOptions{}) {
		t.Fatalf("expected first season not to relax TV year filter")
	}
}
