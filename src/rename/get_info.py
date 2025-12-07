import re
import time
from typing import Any, Dict, List, Optional

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
        self.TMDB_KEY = cm.get_config('api_key')
        tmdb.API_KEY = self.TMDB_KEY

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
                    logger.warning(f"[季度信息] 未获取到Season {season_number}的信息")
                    return None

                filtered_season = {
                    "air_date": season_info.get("air_date"),
                    "episode_count": season_info.get("episode_count", 0),
                    "id": season_info.get("id"),
                    "name": season_info.get("name", ""),
                    "overview": season_info.get("overview", ""),
                    "season_number": season_info.get("season_number", season_number),
                    "poster_path": season_info.get("poster_path"),
                    "episodes": [],
                }

                episodes: List[Dict] = season_info.get("episodes", [])
                for episode in episodes:
                    filtered_episode = {
                        "air_date": episode.get("air_date"),
                        "episode_number": episode.get("episode_number"),
                        "episode_type": episode.get("episode_type", "regular"),
                        "name": episode.get("name", ""),
                        "overview": episode.get("overview", ""),
                        "runtime": episode.get("runtime"),
                        "season_number": episode.get("season_number", season_number),
                        "still_path": episode.get("still_path"),  # 确保有这个
                        "vote_average": episode.get("vote_average"),
                        "id": episode.get("id")
                    }
                    filtered_season["episodes"].append(filtered_episode)

                logger.info(
                    f'[季度信息] 获取Season {season_number}信息成功，包含{len(filtered_season["episodes"])}集'
                )

                GLOBAL_SEASON_CACHE[cache_key] = filtered_season
                return filtered_season

            except Exception as e:
                # 404 表示TMDB确实没有这一季，不需要重试，直接返回None并缓存结果（避免反复404）
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
    ) -> tuple[str, Optional[Dict[str, Any]]]:
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
            # 这里的递归调用要注意避免死循环，通常 cached get_tv_info 会处理
            pass

        for season in seasons:
            season_number = season.get("season_number")
            if season_number is None:
                continue

            # 获取详细信息（带缓存）
            detailed_season = self.get_season_info(tv_id, season_number)

            if detailed_season:
                season.update(detailed_season)

        return tv_info

    def _get_logos(self, tmdb_obj, obj_type='tv'):
        try:
            images = tmdb_obj.images(include_image_language='zh,en,null')
            logos = images.get('logos', [])
            if logos:
                zh_logos = [l['file_path'] for l in logos if l['iso_639_1'] == 'zh']
                if zh_logos:
                    return zh_logos[0]
                return logos[0]['file_path']
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
                search.movie(query=query, language='zh-CN', year=year if year != 0 else None)
                if search.results:
                    target = search.results[0]
                    name = target['title']
                    movie = tmdb.Movies(target['id'])
                    info = movie.info(language='zh-CN')
                    info['logo_path'] = self._get_logos(movie, 'movie')

                    # 写入缓存
                    GLOBAL_MOVIE_CACHE[cache_key] = (name, info)
                    return name, info

                GLOBAL_MOVIE_CACHE[cache_key] = ('', None)
                return '', None
            except Exception:
                time.sleep(2)
        return '', None

    def get_tv_info(self, query: str, year: int):
        cache_key = f"{query}_{year}"
        if cache_key in GLOBAL_TV_CACHE:
            return GLOBAL_TV_CACHE[cache_key]

        for i in range(3):
            try:
                for _ in range(3):
                    search = tmdb.Search()
                    search.tv(query=query, language='zh-CN', first_air_date_year=year if year != 0 else None)
                    if search.results:
                        target = search.results[0]
                        name = target['name']
                        tv = tmdb.TV(target['id'])
                        info = tv.info(language='zh-CN')
                        info['logo_path'] = self._get_logos(tv, 'tv')

                        # 写入缓存
                        GLOBAL_TV_CACHE[cache_key] = (name, info)
                        return name, info
                    else:
                        if is_chinese_percentage_sufficient(query):
                            query = re.sub(r'[a-zA-Z]', '', query)

                GLOBAL_TV_CACHE[cache_key] = ('', None)
                return '', None
            except Exception:
                time.sleep(2)
        return '', None
