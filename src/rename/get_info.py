import re
import time
import requests  # 新增
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin
from bs4 import BeautifulSoup  # 新增

import tmdbsimple as tmdb

from ..logger import logger
from ..config.config_manager import cm
from .cleaner import is_chinese_percentage_sufficient

# Key: query_year, Value: (name, info)
GLOBAL_TV_CACHE: Dict[str, Any] = {}
GLOBAL_MOVIE_CACHE: Dict[str, Any] = {}
# Key: tv_id_season_num, Value: season_info
GLOBAL_SEASON_CACHE: Dict[str, Any] = {}


class Search:
    def __init__(self) -> None:
        self.TMDB_KEY = cm.get_config("api_key")
        tmdb.API_KEY = self.TMDB_KEY
        self._ai_processor = None

    def _get_ai_processor(self):
        """延迟加载AI处理器，避免循环导入"""
        if self._ai_processor is None:
            try:
                from .ai_processor import AIProcessor
                self._ai_processor = AIProcessor()
            except Exception as e:
                logger.debug(f"[AI辅助] AI处理器加载失败: {e}")
        return self._ai_processor

    def _search_tmdb_web(self, query: str, target_type: str = "tv", language: str = "zh-CN") -> Optional[int]:
        """
        通过爬取TMDB网页搜索结果获取ID (当API搜不到时的兜底方案)
        target_type: 'tv' 或 'movie'
        返回: tmdb_id (int) or None
        """
        base_url = "https://www.themoviedb.org"
        params = {
            "language": language,
            "query": query
        }
        url = base_url + "/search?" + urlencode(params)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }

        try:
            logger.info(f"[TMDB Web] 正在尝试网页搜索兜底: {query} (Type: {target_type})")
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"[TMDB Web] 网页搜索返回状态码: {resp.status_code}")
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # 遍历搜索结果卡片
            for card in soup.select("div.card.v4.tight"):
                # 1. 获取链接解析ID和类型
                a_el = card.select_one("a.result")
                if not a_el:
                    continue
                href = a_el.get("href", "")  # 形如 /tv/288306?language=zh-CN

                try:
                    path = href.split("?")[0]  # /tv/288306
                    parts = path.strip("/").split("/")  # ["tv", "288306"]

                    if len(parts) >= 2:
                        media_type = parts[0]  # tv / movie
                        tmdb_id_str = parts[1]

                        # 如果类型匹配，直接返回第一个结果的ID (通常第一个就是最匹配的)
                        if media_type == target_type and tmdb_id_str.isdigit():
                            tmdb_id = int(tmdb_id_str)

                            # 获取一下标题用于日志记录
                            title_el = card.select_one("div.title a h2")
                            title = title_el.get_text(strip=True) if title_el else "Unknown"

                            logger.info(f"[TMDB Web] 网页搜索命中: {title} (ID: {tmdb_id})")
                            return tmdb_id
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"[TMDB Web] 网页搜索发生异常: {e}")

        return None


    def _select_best_result(
            self,
            query: str,
            year: int,
            results: List[Dict],
            is_movie: bool
    ) -> int:
        """从多个TMDB结果中选择最佳匹配"""
        if not results or len(results) == 1:
            return 0

        # 尝试使用AI辅助选择（2-10个结果时）
        if 2 <= len(results) <= 10:
            ai_processor = self._get_ai_processor()
            if ai_processor:
                ai_selected_idx = ai_processor.select_best_tmdb_result(
                    query=query,
                    year=year,
                    results=results,
                    is_movie=is_movie
                )
                if ai_selected_idx is not None:
                    return ai_selected_idx

        # 默认返回第一个结果
        return 0

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
        if cache_key in GLOBAL_SEASON_CACHE:
            return GLOBAL_SEASON_CACHE[cache_key]

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

                GLOBAL_SEASON_CACHE[cache_key] = filtered_season
                return filtered_season

            except Exception as e:
                # 404 表示TMDB确实没有这一季，不需要重试
                if "404" in str(e):
                    GLOBAL_SEASON_CACHE[cache_key] = None
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

    def get_movie_info(self, query: str, year: int):
        cache_key = f"{query}_{year}"
        if cache_key in GLOBAL_MOVIE_CACHE:
            return GLOBAL_MOVIE_CACHE[cache_key]

        for i in range(3):
            try:
                search = tmdb.Search()
                search.movie(
                    query=query,
                    language="zh-CN",
                    year=year if year != 0 else None,
                )

                if search.results:
                    # 使用AI辅助选择最佳结果
                    selected_idx = self._select_best_result(
                        query=query,
                        year=year,
                        results=search.results,
                        is_movie=True
                    )

                    target = search.results[selected_idx]
                    name = target["title"]
                    movie = tmdb.Movies(target["id"])
                    info = movie.info(language="zh-CN")
                    info["logo_path"] = self._get_logos(movie, "movie")

                    GLOBAL_MOVIE_CACHE[cache_key] = (name, info)
                    return name, info

                # --- API 搜不到，尝试网页搜兜底 ---
                elif i == 0:
                    web_id = self._search_tmdb_web(query, target_type="movie")
                    if web_id:
                        try:
                            movie = tmdb.Movies(web_id)
                            info = movie.info(
                                language="zh-CN",
                                append_to_response="credits,external_ids,release_dates"
                            )
                            name = info["title"]
                            info["logo_path"] = self._get_logos(movie, "movie")
                            GLOBAL_MOVIE_CACHE[cache_key] = (name, info)
                            return name, info
                        except Exception as e_web:
                            logger.error(f"[TMDB Web] 兜底ID获取元数据失败: {e_web}")

                GLOBAL_MOVIE_CACHE[cache_key] = ("", None)
                return "", None

            except Exception as e:
                logger.warning(
                    f"[TMDB] 搜索电影 '{query}' 失败，第 {i + 1} 次重试: {e}"
                )
                time.sleep(2)
        return "", None

    def get_tv_info(self, query: str, year: int):
        cache_key = f"{query}_{year}"
        if cache_key in GLOBAL_TV_CACHE:
            return GLOBAL_TV_CACHE[cache_key]

        for i in range(3):
            try:
                q = query
                # 尝试3次API搜索 (可能在内部做一些字符清理)
                for _ in range(3):
                    search = tmdb.Search()
                    search.tv(
                        query=q,
                        language="zh-CN",
                        first_air_date_year=year if year != 0 else None,
                    )

                    if search.results:
                        # 使用AI辅助选择最佳结果
                        selected_idx = self._select_best_result(
                            query=q,
                            year=year,
                            results=search.results,
                            is_movie=False
                        )

                        target = search.results[selected_idx]
                        name = target["name"]
                        tv = tmdb.TV(target["id"])
                        info = tv.info(language="zh-CN")
                        info["logo_path"] = self._get_logos(tv, "tv")

                        GLOBAL_TV_CACHE[cache_key] = (name, info)
                        return name, info

                    if _ == 0:
                        web_id = self._search_tmdb_web(q, target_type="tv")
                        if web_id:
                            try:
                                tv = tmdb.TV(web_id)
                                info = tv.info(
                                    language="zh-CN",
                                    append_to_response="credits,external_ids,content_ratings"
                                )
                                name = info["name"]
                                info["logo_path"] = self._get_logos(tv, "tv")
                                GLOBAL_TV_CACHE[cache_key] = (name, info)
                                return name, info
                            except Exception as e_web:
                                logger.error(f"[TMDB Web] 兜底ID获取元数据失败: {e_web}")

                    # 之前的重试逻辑：去除非中文字符再试
                    else:
                        if is_chinese_percentage_sufficient(q):
                            q = re.sub(r"[a-zA-Z]", "", q)

                GLOBAL_TV_CACHE[cache_key] = ("", None)
                return "", None

            except Exception as e:
                logger.warning(
                    f"[TMDB] 搜索剧集 '{query}' 失败，第 {i + 1} 次重试: {e}"
                )
                time.sleep(2)
        return "", None
