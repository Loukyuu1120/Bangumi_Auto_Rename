package rename

import "testing"

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
