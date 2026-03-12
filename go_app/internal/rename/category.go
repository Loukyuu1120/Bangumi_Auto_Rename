package rename

import (
	"encoding/json"
	"fmt"
	"strings"

	"bangumi_auto_rename/internal/config"
	"bangumi_auto_rename/internal/logger"
)

// SecondaryRule 定义二级分类规则
type SecondaryRule struct {
	Name       string                 `json:"name"`
	Conditions map[string]interface{} `json:"conditions"`
}

// SecondaryRules 定义电影和电视剧的二级分类规则集合
type SecondaryRules struct {
	Movie []SecondaryRule `json:"movie"`
	TV    []SecondaryRule `json:"tv"`
}

// GetCategoryFolder 根据 TMDB 信息和配置返回二级分类文件夹名称
func GetCategoryFolder(info interface{}, isMovie, isAnime bool, cfg config.Config) string {
	log := logger.Get()

	// 如果未启用二级分类，返回空字符串
	if !cfg.SecondaryClassification {
		return ""
	}

	// 解析二级分类规则
	var rules SecondaryRules
	if cfg.SecondaryRules != nil {
		// 尝试将 interface{} 转换为 SecondaryRules
		rulesBytes, err := json.Marshal(cfg.SecondaryRules)
		if err == nil {
			if err := json.Unmarshal(rulesBytes, &rules); err != nil {
				log.Warn("[二级分类] 规则解析失败: %v", err)
			}
		}
	}

	// 提取通用信息
	var genreIDs []int
	var genres []TMDBGenre
	var originCountry []string
	var originalLanguage string

	if isMovie {
		if movie, ok := info.(*TMDBMovieDetail); ok {
			genres = movie.Genres
			originalLanguage = movie.OriginalLanguage
			// 电影没有 origin_country，但可以从其他字段推断
		}
	} else {
		if tv, ok := info.(*TMDBTVDetail); ok {
			genres = tv.Genres
			originCountry = tv.OriginCountry
			originalLanguage = tv.OriginalLanguage
		}
	}

	// 提取 genre IDs
	for _, g := range genres {
		genreIDs = append(genreIDs, g.ID)
	}

	// 如果有自定义规则，使用自定义规则
	var ruleList []SecondaryRule
	if isMovie {
		ruleList = rules.Movie
	} else {
		ruleList = rules.TV
	}

	if len(ruleList) > 0 {
		for _, rule := range ruleList {
			if matchRule(rule, genreIDs, originCountry, originalLanguage) {
				log.Info("[二级分类] 匹配自定义规则: %s", rule.Name)
				return rule.Name
			}
		}
		// 如果有规则但都不匹配，返回"其他"
		return "其他"
	}

	// 使用默认分类逻辑
	return getDefaultCategory(genreIDs, originCountry, originalLanguage, isMovie, isAnime)
}

// matchRule 检查是否匹配规则条件
func matchRule(rule SecondaryRule, genreIDs []int, originCountry []string, originalLanguage string) bool {
	conditions := rule.Conditions
	if len(conditions) == 0 {
		return true // 无条件规则，直接匹配
	}

	// 检查 genre_ids
	if genreIDsStr, ok := conditions["genre_ids"].(string); ok && genreIDsStr != "" {
		targetGenres := parseIntList(genreIDsStr)
		if !hasIntersection(targetGenres, genreIDs) {
			return false
		}
	}

	// 检查 origin_country
	if countryStr, ok := conditions["origin_country"].(string); ok && countryStr != "" {
		targetCountries := parseStringList(countryStr)
		upperCountries := make([]string, len(originCountry))
		for i, c := range originCountry {
			upperCountries[i] = strings.ToUpper(c)
		}
		if !hasStringIntersection(targetCountries, upperCountries) {
			return false
		}
	}

	// 检查 original_language
	if langStr, ok := conditions["original_language"].(string); ok && langStr != "" {
		targetLangs := parseStringList(langStr)
		lowerLang := strings.ToLower(originalLanguage)
		found := false
		for _, lang := range targetLangs {
			if strings.ToLower(lang) == lowerLang {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}

	return true
}

// getDefaultCategory 返回默认的分类逻辑
func getDefaultCategory(genreIDs []int, originCountry []string, originalLanguage string, isMovie, isAnime bool) string {
	// 辅助函数：检查是否为中文地区
	isChineseRegion := func() bool {
		lang := strings.ToLower(originalLanguage)
		if lang == "zh" || lang == "cn" || lang == "bo" || lang == "za" {
			return true
		}
		for _, c := range originCountry {
			if c == "CN" || c == "HK" || c == "TW" {
				return true
			}
		}
		return false
	}

	// 辅助函数：检查是否为日韩地区
	isJapKorRegion := func() bool {
		lang := strings.ToLower(originalLanguage)
		if lang == "ja" || lang == "ko" {
			return true
		}
		for _, c := range originCountry {
			if c == "JP" || c == "KR" {
				return true
			}
		}
		return false
	}

	// 辅助函数：检查是否为西方地区
	isWesternRegion := func() bool {
		westernCountries := []string{"US", "CA", "GB", "FR", "DE", "IT", "ES"}
		for _, c := range originCountry {
			for _, w := range westernCountries {
				if c == w {
					return true
				}
			}
		}
		return false
	}

	// 辅助函数：检查是否包含某个 genre ID
	hasGenre := func(id int) bool {
		for _, gid := range genreIDs {
			if gid == id {
				return true
			}
		}
		return false
	}

	// 电影分类
	if isMovie {
		// 16 = Animation
		if hasGenre(16) || isAnime {
			return "动画电影"
		}
		if isChineseRegion() {
			return "华语电影"
		}
		return "外语电影"
	}

	// 电视剧分类
	// 10762 = Kids
	if hasGenre(10762) {
		return "儿童"
	}
	// 99 = Documentary
	if hasGenre(99) {
		return "纪录片"
	}
	// 10764 = Reality, 10767 = Talk
	if hasGenre(10764) || hasGenre(10767) {
		return "综艺"
	}
	// 16 = Animation
	if isAnime || hasGenre(16) {
		if isChineseRegion() {
			return "国漫"
		}
		if isWesternRegion() {
			return "美漫"
		}
		return "日漫"
	}
	if isChineseRegion() {
		return "国产剧"
	}
	if isJapKorRegion() {
		return "日韩剧"
	}
	return "欧美剧"
}

// parseIntList 解析逗号分隔的整数字符串
func parseIntList(s string) []int {
	parts := strings.Split(s, ",")
	result := make([]int, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		var num int
		if _, err := fmt.Sscanf(p, "%d", &num); err == nil {
			result = append(result, num)
		}
	}
	return result
}

// parseStringList 解析逗号分隔的字符串并转为大写
func parseStringList(s string) []string {
	parts := strings.Split(s, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			result = append(result, strings.ToUpper(p))
		}
	}
	return result
}

// hasIntersection 检查两个整数切片是否有交集
func hasIntersection(a, b []int) bool {
	set := make(map[int]bool, len(b))
	for _, v := range b {
		set[v] = true
	}
	for _, v := range a {
		if set[v] {
			return true
		}
	}
	return false
}

// hasStringIntersection 检查两个字符串切片是否有交集
func hasStringIntersection(a, b []string) bool {
	set := make(map[string]bool, len(b))
	for _, v := range b {
		set[v] = true
	}
	for _, v := range a {
		if set[v] {
			return true
		}
	}
	return false
}
