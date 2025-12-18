import re
import time
import requests
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

import tmdbsimple as tmdb

from ..logger import logger
from ..config.config_manager import cm
from .cleaner import is_chinese_percentage_sufficient


class Search:
    def __init__(self) -> None:
        self.TMDB_KEY = cm.get_config("api_key")
        tmdb.API_KEY = self.TMDB_KEY
        self._ai_processor = None

        # Key: query_year, Value: (name, info)
        self._tv_cache: Dict[str, Any] = {}
        self._movie_cache: Dict[str, Any] = {}
        # Key: tv_id_season_num, Value: season_info
        self._season_cache: Dict[str, Any] = {}
        self._max_cache_size = 200

    def _safe_add_to_cache(self, cache_dict: Dict, key: str, value: Any):
        """辅助方法：添加缓存并控制大小"""
        if len(cache_dict) >= self._max_cache_size:
            try:
                first_key = next(iter(cache_dict))
                del cache_dict[first_key]
            except StopIteration:
                pass
        cache_dict[key] = value

    def _get_ai_processor(self):
        """延迟加载AI处理器，避免循环导入"""
        if self._ai_processor is None:
            try:
                from .ai_processor import AIProcessor
                self._ai_processor = AIProcessor()
            except Exception as e:
                logger.debug(f"[AI辅助] AI处理器加载失败: {e}")
        return self._ai_processor

    def _search_tmdb_web(self, query: str, target_type: str = "tv", language: str = "zh-CN") -> List[Dict]:
        """
        爬取TMDB网页搜索结果，支持带 Slug 的 URL (如 /tv/123-name)，返回候选列表
        """
        base_url = "https://www.themoviedb.org"
        params = {"language": language, "query": query}
        url = base_url + "/search?" + urlencode(params)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }

        results = []
        try:
            logger.info(f"[TMDB Web] 正在执行网页搜索: {query} (Type: {target_type})")
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")

            # 遍历所有搜索结果卡片
            for card in soup.select("div.card"):
                # 1. 提取链接
                a_el = card.select_one("a.result")
                # 有些布局可能不同，尝试备选方案
                if not a_el:
                    a_el = card.select_one("div.details div.title a")

                if not a_el: continue

                href = a_el.get("href", "")

                # 核心修正：匹配 /tv/数字 或 /movie/数字 (忽略后面的 slug)
                match = re.search(rf"/{target_type}/(\d+)", href)

                if match:
                    tmdb_id = int(match.group(1))

                    # 2. 提取标题
                    title_el = card.select_one("div.details div.title h2")
                    if title_el:
                        name = title_el.get_text(strip=True)
                    else:
                        name = a_el.get_text(strip=True)

                    # 3. 提取年份
                    date_el = card.select_one("span.release_date")
                    date_str = ""
                    if date_el:
                        date_text = date_el.get_text(strip=True)
                        year_match = re.search(r'\d{4}', date_text)
                        if year_match:
                            date_str = f"{year_match.group(0)}-01-01"

                    # 4. 提取原名 (Web结果通常不直接显示原名，暂时复用 name 或留空)
                    original_name = name

                    results.append({
                        "id": tmdb_id,
                        "name": name,
                        "title": name,
                        "original_name": original_name,
                        "original_title": original_name,
                        "first_air_date": date_str,
                        "release_date": date_str
                    })

            logger.info(f"[TMDB Web] 抓取到 {len(results)} 个潜在结果")
            return results

        except Exception as e:
            logger.warning(f"[TMDB Web] 网页搜索异常: {e}")
            return []

    def _select_best_result(
            self,
            query: str,
            year: int,
            results: List[Dict],
            is_movie: bool,
            season_number: Optional[int] = None
    ) -> int:
        """
        [混合模式] 选择最佳结果：
        1. 优先使用规则评分（支持季号年份偏移逻辑）。
        2. 如果规则评分判定为“高置信度”匹配(>=80分)，直接返回规则结果。
        3. 如果规则评分信心不足，且 AI 可用，则调用 AI 辅助。
        """
        if not results:
            return 0
        if len(results) == 1:
            return 0

        # --- 阶段一：规则打分 ---
        best_idx = 0
        max_score = -1.0

        def clean_str(s):
            return re.sub(r'[^\w\u4e00-\u9fa5]', '', str(s)).lower()

        clean_query = clean_str(query)

        for idx, res in enumerate(results):
            score = 0.0

            # 1. 名称相似度 (基础分 60)
            name = res.get('title') if is_movie else res.get('name')
            original_name = res.get('original_title') if is_movie else res.get('original_name')
            if not original_name: original_name = name

            ratio_name = SequenceMatcher(None, clean_query, clean_str(name)).ratio()
            ratio_origin = SequenceMatcher(None, clean_query, clean_str(original_name)).ratio()

            name_score = max(ratio_name, ratio_origin)

            # 完全匹配奖励
            if clean_query == clean_str(name):
                name_score = 1.0

            score += name_score * 60

            # 2. 年份逻辑 (基础分 40)
            date_str = res.get('release_date') if is_movie else res.get('first_air_date')
            res_year = 0
            if date_str:
                try:
                    res_year = int(str(date_str).split('-')[0])
                except:
                    pass

            if year > 0 and res_year > 0:
                year_diff = year - res_year

                if is_movie:
                    if abs(year_diff) <= 1:
                        score += 40
                    elif abs(year_diff) <= 2:
                        score += 20
                    else:
                        score -= 20
                else:
                    # TV 剧集季号逻辑
                    if season_number and season_number > 1:
                        # S2+: 只要首播年份早于或等于文件年份即可 (2004 <= 2016)
                        # 允许一定的容错 (-1)
                        if year_diff >= 0:
                            score += 40
                        elif year_diff >= -1:
                            score += 20
                        else:
                            score -= 100  # 严重错误：S5早于S1首播
                    else:
                        # S1: 必须接近
                        if abs(year_diff) <= 2:
                            score += 40
                        elif abs(year_diff) <= 5:
                            score += 10
                        else:
                            score -= 20
            else:
                # 缺少年份信息，给予中等分数避免误杀
                score += 20

            # logger.debug(f"Candidate: {name} ({res_year}), Score: {score}")

            if score > max_score:
                max_score = score
                best_idx = idx

        logger.debug(f"[选片] 规则计算最高分: {max_score:.2f} (候选: {results[best_idx].get('name' or 'title')})")

        # --- 阶段二：决策 ---
        # 阈值判定：如果分数超过 80 分，说明名字和年份都非常匹配，直接信赖规则
        if max_score >= 80:
            return best_idx

        # --- 阶段三：AI 介入 ---
        # 分数低，且结果数量适中，尝试 AI
        if 2 <= len(results) <= 10:
            ai_processor = self._get_ai_processor()
            if ai_processor and ai_processor.ai_client.is_available():
                logger.info(f"[选片] 规则评分信心不足(Max={max_score:.1f})，调用 AI 进行判决...")
                try:
                    ai_idx = ai_processor.select_best_tmdb_result(
                        query=query,
                        year=year,
                        results=results,
                        is_movie=is_movie
                    )
                    if ai_idx is not None and 0 <= ai_idx < len(results):
                        logger.info(f"[选片] AI 选择了: {results[ai_idx].get('name' or 'title')}")
                        return ai_idx
                except Exception as e:
                    logger.warning(f"[选片] AI 辅助选择失败: {e}，回退到规则结果")

        return best_idx

    def get_movie_info(self, query: str, year: int):
        cache_key = f"{query}_{year}"
        if cache_key in self._movie_cache:
            return self._movie_cache[cache_key]

        for i in range(3):
            try:
                search = tmdb.Search()
                search.movie(
                    query=query,
                    language="zh-CN",
                    year=year if year != 0 else None,
                )

                candidates = []
                source = "api"

                # 1. 获取 API 结果
                if search.results:
                    candidates = search.results

                # 2. 如果 API 无结果，且是第一次尝试，尝试 Web 兜底
                if not candidates and i == 0:
                    web_results = self._search_tmdb_web(query, target_type="movie")
                    if web_results:
                        candidates = web_results
                        source = "web"

                # 3. 筛选结果
                if candidates:
                    selected_idx = self._select_best_result(
                        query=query,
                        year=year,
                        results=candidates,
                        is_movie=True
                    )
                    target = candidates[selected_idx]

                    # 获取详情
                    movie = tmdb.Movies(target["id"])
                    info = movie.info(
                        language="zh-CN",
                        append_to_response="credits,external_ids,release_dates"
                    )
                    info["logo_path"] = self._get_logos(movie, "movie")

                    name = info.get("title", target.get("name"))  # 优先用详情里的title
                    logger.info(f"[TMDB] 选中最佳电影结果 (Source: {source}): {name} (ID: {target['id']})")

                    self._safe_add_to_cache(self._movie_cache, cache_key, (name, info))
                    return name, info

                self._safe_add_to_cache(self._movie_cache, cache_key, ("", None))
                return "", None

            except Exception as e:
                logger.warning(f"[TMDB] 搜索电影 '{query}' 失败，第 {i + 1} 次重试: {e}")
                time.sleep(2)
        return "", None

    def get_tv_info(self, query: str, year: int, season_number: Optional[int] = None):
        """
        搜索 TV 信息，支持 Web 兜底和季号辅助判断
        """
        cache_key = f"{query}_{year}"
        if cache_key in self._tv_cache:
            return self._tv_cache[cache_key]

        for i in range(3):
            try:
                q = query
                for _ in range(3):
                    search = tmdb.Search()
                    # API 搜索
                    search.tv(
                        query=q,
                        language="zh-CN",
                        first_air_date_year=year if year != 0 else None,
                    )

                    candidates = []
                    source = "api"

                    # 1. 获取 API 结果
                    if search.results:
                        candidates = search.results

                    # 2. API 无结果时的 Web 兜底逻辑
                    # 触发条件: 第一次循环 AND (未指定年份 OR 是多季剧集)
                    # S2+ 时忽略 API 年份限制导致的空结果，转而通过 Web 搜素获取列表并校验
                    elif _ == 0 and (year == 0 or (season_number and season_number > 1)):
                        web_results = self._search_tmdb_web(q, target_type="tv")
                        if web_results:
                            candidates = web_results
                            source = "web"

                    # 3. 筛选结果
                    if candidates:
                        best_idx = self._select_best_result(
                            query=q,
                            year=year,
                            results=candidates,
                            is_movie=False,
                            season_number=season_number
                        )

                        target = candidates[best_idx]
                        tmdb_id = target["id"]

                        logger.info(f"[TMDB] 选中最佳TV结果 (Source: {source}): {target.get('name')} (ID: {tmdb_id})")

                        tv = tmdb.TV(tmdb_id)
                        info = tv.info(
                            language="zh-CN",
                            append_to_response="credits,external_ids,content_ratings"
                        )
                        info["logo_path"] = self._get_logos(tv, "tv")

                        self._safe_add_to_cache(self._tv_cache, cache_key, (info["name"], info))
                        return info["name"], info

                    # 重试逻辑：去除非中文字符
                    else:
                        if is_chinese_percentage_sufficient(q):
                            q = re.sub(r"[a-zA-Z]", "", q)

                self._safe_add_to_cache(self._tv_cache, cache_key, ("", None))
                return "", None

            except Exception as e:
                logger.warning(
                    f"[TMDB] 搜索剧集 '{query}' 失败，第 {i + 1} 次重试: {e}"
                )
                time.sleep(2)
        return "", None

    def _fetch_tv_with_retry(
            self,
            tmdb_id: int,
            retries: int = 3,
            delay: float = 1.0,
    ) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for i in range(retries):
            try:
                tv = tmdb.TV(tmdb_id)
                info = tv.info(
                    language="zh-CN",
                    append_to_response="credits,external_ids,content_ratings"
                )
                return info
            except Exception as e:
                last_err = e
                logger.warning(
                    f"[TMDB] 第 {i + 1}/{retries} 次按剧集查询 ID={tmdb_id} 失败: {e}"
                )
                if i < retries - 1:
                    time.sleep(delay)
        raise RuntimeError(f"按剧集查询 TMDB ID={tmdb_id} 多次失败: {last_err}")

    def _fetch_movie_with_retry(
            self,
            tmdb_id: int,
            retries: int = 3,
            delay: float = 1.0,
    ) -> Dict[str, Any]:
        last_err: Optional[Exception] = None
        for i in range(retries):
            try:
                movie = tmdb.Movies(tmdb_id)
                info = movie.info(
                    language="zh-CN",
                    append_to_response="credits,external_ids,release_dates"
                )
                return info
            except Exception as e:
                last_err = e
                logger.warning(
                    f"[TMDB] 第 {i + 1}/{retries} 次按电影查询 ID={tmdb_id} 失败: {e}"
                )
                if i < retries - 1:
                    time.sleep(delay)
        raise RuntimeError(f"按电影查询 TMDB ID={tmdb_id} 多次失败: {last_err}")

    def get_info_by_tmdb_id(
            self,
            tmdb_id: int,
            is_movie_hint: Optional[bool] = None,
            retries: int = 3,
            delay: float = 1.0,
    ) -> Tuple[str, Optional[Dict[str, Any]], bool, bool]:
        """
        根据 TMDB ID 获取信息。
        返回: (name, info, is_anime, is_movie)
        """
        if not self.TMDB_KEY:
            raise RuntimeError("TMDB API Key 未配置")

        tmdb_id = int(tmdb_id)

        if is_movie_hint is True:
            # 只查电影
            info = self._fetch_movie_with_retry(
                tmdb_id, retries=retries, delay=delay
            )
            is_movie = True

        elif is_movie_hint is False:
            # 只查剧集
            info = self._fetch_tv_with_retry(
                tmdb_id, retries=retries, delay=delay
            )
            is_movie = False
            info = self.fill_season_info(info)

        else:
            # 未指定类型：先 TV，再 Movie（都带重试）
            try:
                info = self._fetch_tv_with_retry(
                    tmdb_id, retries=retries, delay=delay
                )
                is_movie = False
                info = self.fill_season_info(info)
            except Exception as e_tv:
                logger.warning(
                    f"[TMDB] 按剧集查询 ID={tmdb_id} 多次失败: {e_tv}，尝试按电影查询..."
                )
                info = self._fetch_movie_with_retry(
                    tmdb_id, retries=retries, delay=delay
                )
                is_movie = True

        if not info:
            raise RuntimeError(f"TMDB 未返回 ID={tmdb_id} 的信息")

        # 取名称
        name = info.get("name") if not is_movie else info.get("title") or ""

        # 判断是否为动画
        is_anime = False
        for g in info.get("genres", []):
            if g["name"].lower() in ["animation", "anime"]:
                is_anime = True
                break

        return name, info, is_anime, is_movie

    def get_season_info(
            self, tv_id: int, season_number: int
    ) -> Optional[Dict[str, Any]]:
        cache_key = f"{tv_id}_{season_number}"
        if cache_key in self._season_cache:
            return self._season_cache[cache_key]

        for i in range(3):
            try:
                season = tmdb.TV_Seasons(tv_id, season_number)
                season_info = season.info(language="zh-CN")

                if not season_info:
                    logger.warning(
                        f"[季度信息] 未获取到Season {season_number}的信息"
                    )
                    return None

                filtered_season = {
                    "air_date": season_info.get("air_date"),
                    "episode_count": season_info.get("episode_count", 0),
                    "id": season_info.get("id"),
                    "name": season_info.get("name", ""),
                    "overview": season_info.get("overview", ""),
                    "season_number": season_info.get(
                        "season_number", season_number
                    ),
                    "poster_path": season_info.get("poster_path"),
                    "episodes": [],
                }

                episodes: List[Dict] = season_info.get("episodes", [])
                for episode in episodes:
                    filtered_episode = {
                        "air_date": episode.get("air_date"),
                        "episode_number": episode.get("episode_number"),
                        "episode_type": episode.get(
                            "episode_type", "regular"
                        ),
                        "name": episode.get("name", ""),
                        "overview": episode.get("overview", ""),
                        "runtime": episode.get("runtime"),
                        "season_number": episode.get(
                            "season_number", season_number
                        ),
                        "still_path": episode.get("still_path"),
                        "vote_average": episode.get("vote_average"),
                        "id": episode.get("id"),
                    }
                    filtered_season["episodes"].append(filtered_episode)

                logger.info(
                    f'[季度信息] 获取Season {season_number}信息成功，包含{len(filtered_season["episodes"])}集'
                )

                self._safe_add_to_cache(self._season_cache, cache_key, filtered_season)
                return filtered_season

            except Exception as e:
                # 404 表示TMDB确实没有这一季，不需要重试
                if "404" in str(e):
                    self._safe_add_to_cache(self._season_cache, cache_key, None)
                    return None

                logger.warning(
                    f"[季度信息] 获取Season {season_number}信息失败，重试第{i + 1}次: {str(e)}"
                )
                time.sleep(2)
        return None

    def get_tv_info_with_seasons(
            self, query: str, year: int
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        name, tv_info = self.get_tv_info(query, year)
        if not name or not tv_info:
            return name, tv_info
        tv_info = self.fill_season_info(tv_info)
        return name, tv_info

    def fill_season_info(self, tv_info: Dict[str, Any]) -> Dict[str, Any]:
        if not tv_info or "id" not in tv_info:
            return tv_info

        tv_id = tv_info["id"]
        seasons = tv_info.get("seasons", [])

        for season in seasons:
            season_number = season.get("season_number")
            if season_number is None:
                continue

            detailed_season = self.get_season_info(tv_id, season_number)
            if detailed_season:
                season.update(detailed_season)

        return tv_info

    def _get_logos(self, tmdb_obj, obj_type: str = "tv"):
        try:
            images = tmdb_obj.images(include_image_language="zh,en,null")
            logos = images.get("logos", [])
            if logos:
                zh_logos = [
                    l["file_path"] for l in logos if l["iso_639_1"] == "zh"
                ]
                if zh_logos:
                    return zh_logos[0]
                return logos[0]["file_path"]
        except Exception:
            pass
        return None
