import re
import time
from typing import Any, Dict, List, Optional, Tuple

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
                info = tv.info(language="zh-CN")
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
                info = movie.info(language="zh-CN")
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

        is_movie_hint:
            - True  -> 只按电影查，失败直接报错（带重试）
            - False -> 只按剧集查，失败直接报错（带重试）
            - None  -> 先当剧集查（带重试），失败再当电影查（带重试，兜底）
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

        # 某些剧集(如动漫) info里可能seasons为空，需要重新获取
        if not seasons:
            # 这里保留原来逻辑占位，避免死循环
            pass

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
                    target = search.results[0]
                    name = target["title"]
                    movie = tmdb.Movies(target["id"])
                    info = movie.info(language="zh-CN")
                    info["logo_path"] = self._get_logos(movie, "movie")

                    GLOBAL_MOVIE_CACHE[cache_key] = (name, info)
                    return name, info

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
                for _ in range(3):
                    search = tmdb.Search()
                    search.tv(
                        query=q,
                        language="zh-CN",
                        first_air_date_year=year if year != 0 else None,
                    )
                    if search.results:
                        target = search.results[0]
                        name = target["name"]
                        tv = tmdb.TV(target["id"])
                        info = tv.info(language="zh-CN")
                        info["logo_path"] = self._get_logos(tv, "tv")

                        GLOBAL_TV_CACHE[cache_key] = (name, info)
                        return name, info
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