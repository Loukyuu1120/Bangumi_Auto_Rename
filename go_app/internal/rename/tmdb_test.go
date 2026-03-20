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

func TestPickChinesePreferredTitleSkipsNonChineseMedia(t *testing.T) {
	got := pickChinesePreferredTitle(
		"Interstellar",
		"Interstellar",
		"en",
		[]string{"US"},
		[]tmdbAltTitleItem{
			{Title: "星际穿越", Lang: "zh-CN"},
		},
	)

	if got != "" {
		t.Fatalf("expected empty result for non-Chinese media, got %q", got)
	}
}
