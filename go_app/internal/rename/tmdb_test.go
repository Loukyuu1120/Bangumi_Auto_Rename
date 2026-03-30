package rename

import "testing"

func TestPickChinesePreferredTitleUsesChineseCandidateForChineseMedia(t *testing.T) {
	got := pickChinesePreferredTitle(
		"Look Up There's Starlight",
		"Look Up There's Starlight",
		"zh",
		[]string{"CN"},
		[]tmdbAltTitleItem{
			{Title: "Look Up There's Starlight", Lang: "en-US"},
			{Title: "陪你逐风飞翔", Lang: "zh-CN"},
		},
	)

	if got != "陪你逐风飞翔" {
		t.Fatalf("expected Chinese title, got %q", got)
	}
}

func TestPickChinesePreferredTitlePrefersSimplifiedChineseOverTraditional(t *testing.T) {
	got := pickChinesePreferredTitle(
		"Toujima Tanzaburou wa Kamen Rider ni Naritai",
		"Toujima Tanzaburou wa Kamen Rider ni Naritai",
		"ja",
		[]string{"JP"},
		[]tmdbAltTitleItem{
			{Title: "東島丹三郎想成為假面騎士", Lang: "zh-TW"},
			{Title: "东岛丹三郎想成为假面骑士", Lang: "zh-CN"},
		},
	)

	if got != "东岛丹三郎想成为假面骑士" {
		t.Fatalf("expected simplified Chinese title, got %q", got)
	}
}

func TestPickChinesePreferredTitleFallsBackToOriginalChinese(t *testing.T) {
	got := pickChinesePreferredTitle(
		"Looking Up",
		"银河补习班",
		"zh",
		[]string{"CN"},
		nil,
	)

	if got != "银河补习班" {
		t.Fatalf("expected original Chinese title, got %q", got)
	}
}

func TestPickChinesePreferredTitleUsesChineseCandidateForNonChineseMedia(t *testing.T) {
	got := pickChinesePreferredTitle(
		"Interstellar",
		"Interstellar",
		"en",
		[]string{"US"},
		[]tmdbAltTitleItem{
			{Title: "星际穿越", Lang: "zh-CN"},
		},
	)

	if got != "星际穿越" {
		t.Fatalf("expected Chinese title for non-Chinese media, got %q", got)
	}
}

func TestHasChineseTranslationAcceptsTraditionalChineseTitle(t *testing.T) {
	got := hasChineseTranslation(
		[]tmdbAltTitleItem{
			{Title: "東島丹三郎想成為假面騎士", Lang: "zh-TW"},
		},
		nil,
	)

	if !got {
		t.Fatal("expected zh-TW title to count as Chinese translation")
	}
}

func TestHasChineseTranslationRejectsEnglishOnly(t *testing.T) {
	got := hasChineseTranslation(
		[]tmdbAltTitleItem{
			{Title: "Toujima Tanzaburou wa Kamen Rider ni Naritai", Lang: "ja-JP"},
			{Title: "Tojima Tanzaburo Wants to Be a Kamen Rider", Lang: "en-US"},
		},
		nil,
	)

	if got {
		t.Fatal("expected non-Chinese titles to be ignored")
	}
}
