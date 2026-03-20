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
