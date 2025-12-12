import shutil
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
from ..logger import logger
from ..config.config_manager import cm


class Scraper:
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0"}
        self.tmdb_image_base_url = "https://image.tmdb.org/t/p/original"
        self.tmdb_image_dl_base = "https://image.tmdb.org/t/p/"
        self.tmdb_person_base = "https://www.themoviedb.org/person/"
        self.api_key = cm.get_config("api_key")

        self.url_cache: Dict[str, Path] = {}
        types = cm.get_config("scrape_image_types")
        self.allowed_types = set(types) if types else set()

    # -------------------------
    #  XML 辅助方法
    # -------------------------
    def _add_e(self, parent: ET.Element, tag: str, text: Any):
        if text is not None:
            s_text = str(text).strip()
            if s_text:
                elem = ET.SubElement(parent, tag)
                elem.text = s_text
                return elem
        return None

    def _add_uniqueid(self, parent: ET.Element, id_type: str, id_value: Any, is_default: bool = False):
        if id_value:
            elem = ET.SubElement(parent, "uniqueid")
            elem.text = str(id_value)
            elem.set("type", id_type)
            if is_default:
                elem.set("default", "true")
            else:
                elem.set("default", "false")

    def _save_nfo(self, root: ET.Element, save_path: Path):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        # if save_path.exists(): return # 覆盖模式

        if hasattr(ET, "indent"):
            ET.indent(root, space="  ")

        try:
            tree = ET.ElementTree(root)
            tree.write(save_path, encoding="utf-8", xml_declaration=True)
            logger.info(f"[NFO生成] 已保存: {save_path.name}")
        except Exception as e:
            logger.warning(f"[NFO生成] 失败 {save_path.name}: {e}")

    # -------------------------
    #  核心元数据处理
    # -------------------------
    def _process_common_info(self, root: ET.Element, info: Dict[str, Any], is_movie: bool):
        tmdb_id = info.get("id")
        ext_ids = info.get("external_ids", {})
        imdb_id = ext_ids.get("imdb_id")
        tvdb_id = ext_ids.get("tvdb_id")

        self._add_e(root, "tmdbid", tmdb_id)
        self._add_uniqueid(root, "tmdb", tmdb_id, is_default=False)

        if tvdb_id:
            self._add_e(root, "tvdbid", tvdb_id)
            self._add_uniqueid(root, "tvdb", tvdb_id)

        if imdb_id:
            self._add_e(root, "imdbid", imdb_id)
            self._add_uniqueid(root, "imdb", imdb_id, is_default=True)

        overview = info.get("overview", "")
        self._add_e(root, "plot", overview)
        self._add_e(root, "outline", overview)

        self._process_credits(root, info)

        for genre in info.get("genres", []):
            self._add_e(root, "genre", genre.get("name"))

        self._add_e(root, "rating", info.get("vote_average"))
        self._add_e(root, "mpaa", self._get_certification(info, is_movie))

    def _process_credits(self, root: ET.Element, info: Dict[str, Any]):
        credits = info.get('credits', {})
        cast = credits.get('cast', [])
        for person in cast[:15]:
            actor_elem = ET.SubElement(root, 'actor')
            self._add_e(actor_elem, 'name', person.get('name'))
            self._add_e(actor_elem, 'type', 'Actor')
            self._add_e(actor_elem, 'role', person.get('character'))
            pid = person.get('id')
            self._add_e(actor_elem, 'tmdbid', pid)
            profile_path = person.get('profile_path')
            if profile_path:
                self._add_e(actor_elem, 'thumb', self.tmdb_image_base_url + profile_path)
            if pid:
                self._add_e(actor_elem, 'profile', f"{self.tmdb_person_base}{pid}")

        crew = credits.get('crew', [])
        for person in crew:
            job = person.get('job', '').lower()
            if job == 'director':
                self._add_e(root, 'director', person.get('name'))
            elif job in ['writer', 'screenplay', 'story', 'screenwriter']:
                self._add_e(root, 'credits', person.get('name'))

    def _get_certification(self, info: Dict[str, Any], is_movie: bool) -> str:
        try:
            if is_movie:
                releases = info.get('release_dates', {}).get('results', [])
                for country in releases:
                    if country.get('iso_3166_1') == "US":
                        for date_item in country.get('release_dates', []):
                            if date_item.get('certification'):
                                return date_item.get('certification')
            else:
                ratings = info.get('content_ratings', {}).get('results', [])
                for item in ratings:
                    if item.get('iso_3166_1') == "US":
                        return item.get('rating')
            return ""
        except:
            return ""

    # -------------------------
    #  图片下载
    # -------------------------
    def _smart_download(self, url_suffix: Optional[str], save_path: Path, image_type: str,
                        size: str = "original", allowed_types_override: Optional[Set[str]] = None):
        """
        :param allowed_types_override: 如果提供，则使用此集合检查；否则使用 self.allowed_types
        """
        # 确定当前的允许列表
        current_allowed = allowed_types_override if allowed_types_override is not None else self.allowed_types

        if not url_suffix or image_type not in current_allowed:
            return

        save_path.parent.mkdir(parents=True, exist_ok=True)
        if save_path.exists(): return

        full_url = f"{self.tmdb_image_dl_base}{size}{url_suffix}"

        if full_url in self.url_cache:
            if self.url_cache[full_url].exists():
                try:
                    shutil.copy2(self.url_cache[full_url], save_path)
                    return
                except:
                    pass

        try:
            resp = requests.get(full_url, headers=self.headers, timeout=15)
            if resp.status_code == 200:
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                self.url_cache[full_url] = save_path
        except Exception:
            pass

    def _get_tmdb_images(self, tmdb_id: int, media_type: str) -> Dict[str, List[Dict]]:
        if not self.api_key: return {}
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images"
        params = {"api_key": self.api_key, "include_image_language": "zh,cn,null,en"}
        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=10)
            if resp.status_code == 200: return resp.json()
        except:
            pass
        return {}

    # -------------------------
    #  辅助：提取 Config Override
    # -------------------------
    def _get_allowed_types(self, overrides: Optional[Dict]) -> Optional[Set[str]]:
        if overrides and 'scrape_image_types' in overrides:
            val = overrides['scrape_image_types']
            if val is not None:
                if isinstance(val, list):
                    return set(val)
                if isinstance(val, str):  # 如果配置里存的是逗号分隔字符串
                    return set([x.strip() for x in val.split(',') if x.strip()])
        return None

    # -------------------------
    #  入口方法
    # -------------------------
    def scrape_movie(self, save_path: Path, info: Dict[str, Any], config_overrides: Optional[Dict] = None):
        if not info: return
        tmdb_id = info.get("id")
        folder = save_path.parent

        allowed = self._get_allowed_types(config_overrides)

        images = self._get_tmdb_images(tmdb_id, "movie")
        p = images.get("posters", [])
        b = images.get("backdrops", [])

        self._smart_download(p[0]['file_path'] if p else info.get("poster_path"), folder / "poster.jpg", "poster",
                             allowed_types_override=allowed)
        self._smart_download(b[0]['file_path'] if b else info.get("backdrop_path"), folder / "fanart.jpg", "backdrop",
                             allowed_types_override=allowed)

        root = ET.Element('movie')
        self._add_e(root, "title", info.get("title"))
        self._add_e(root, "originaltitle", info.get("original_title"))
        self._add_e(root, "year", info.get("release_date", "")[:4])
        self._add_e(root, "premiered", info.get("release_date"))

        self._process_common_info(root, info, is_movie=True)
        self._save_nfo(root, save_path.with_suffix(".nfo"))

    def scrape_tv_show(self, work_path: Path, info: Dict[str, Any], config_overrides: Optional[Dict] = None):
        if not info: return
        tmdb_id = info.get("id")
        allowed = self._get_allowed_types(config_overrides)

        images = self._get_tmdb_images(tmdb_id, "tv")
        p = images.get("posters", [])
        b = images.get("backdrops", [])

        self._smart_download(p[0]['file_path'] if p else info.get("poster_path"), work_path / "poster.jpg", "poster",
                             allowed_types_override=allowed)
        self._smart_download(b[0]['file_path'] if b else info.get("backdrop_path"), work_path / "fanart.jpg",
                             "backdrop", allowed_types_override=allowed)

        root = ET.Element('tvshow')
        self._process_common_info(root, info, is_movie=False)

        self._add_e(root, "title", info.get("name"))
        self._add_e(root, "originaltitle", info.get("original_name"))
        self._add_e(root, "premiered", info.get("first_air_date"))
        self._add_e(root, "year", info.get("first_air_date", "")[:4])
        self._add_e(root, "season", "-1")
        self._add_e(root, "episode", "-1")

        self._save_nfo(root, work_path / "tvshow.nfo")

    def scrape_season(self, work_path: Path, season_number: int, info: Dict[str, Any], season_dir: Path = None,
                      config_overrides: Optional[Dict] = None):
        season_info = next((s for s in info.get("seasons", []) if s.get("season_number") == season_number), None)
        if not season_info: return

        allowed = self._get_allowed_types(config_overrides)
        poster = season_info.get("poster_path")

        self._smart_download(poster, work_path / f"season{season_number:02d}-poster.jpg", "poster",
                             allowed_types_override=allowed)
        if season_dir and season_dir != work_path:
            self._smart_download(poster, season_dir / "folder.jpg", "poster", allowed_types_override=allowed)

        target_dir = season_dir if season_dir else (work_path / f"Season{season_number}")
        if target_dir.exists():
            root = ET.Element('season')
            self._add_e(root, "title", season_info.get("name"))
            self._add_e(root, "season", season_number)
            self._add_e(root, "plot", season_info.get("overview"))
            self._add_e(root, "premiered", season_info.get("air_date"))
            self._add_e(root, "year", (season_info.get("air_date") or "")[:4])
            self._save_nfo(root, target_dir / "season.nfo")

    def scrape_episode(self, video_target: Path, info: Dict[str, Any], season_number: int, episode_num: int,
                       config_overrides: Optional[Dict] = None):
        season_info = next((s for s in info.get("seasons", []) if s.get("season_number") == season_number), None)
        if not season_info: return
        ep = next((e for e in season_info.get("episodes", []) if e.get("episode_number") == episode_num), None)
        if not ep: return

        allowed = self._get_allowed_types(config_overrides)

        if ep.get("still_path"):
            self._smart_download(ep.get("still_path"), video_target.with_name(f"{video_target.stem}-thumb.jpg"),
                                 "thumb", allowed_types_override=allowed)

        root = ET.Element('episodedetails')
        self._add_e(root, "title", ep.get("name"))
        self._add_e(root, "plot", ep.get("overview"))
        self._add_e(root, "season", season_number)
        self._add_e(root, "episode", episode_num)
        self._add_e(root, "aired", ep.get("air_date"))
        self._add_e(root, "rating", ep.get("vote_average"))

        eid = ep.get("id")
        self._add_e(root, "tmdbid", eid)
        self._add_uniqueid(root, "tmdb", eid, is_default=True)

        self._save_nfo(root, video_target.with_suffix(".nfo"))
