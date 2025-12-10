import os
import shutil
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional, List
from ..logger import logger
from ..config.config_manager import cm


class Scraper:
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0"}
        self.tmdb_image_base = "https://image.tmdb.org/t/p/"
        self.api_key = cm.get_config("api_key")

        self.url_cache: Dict[str, Path] = {}

        # [配置读取] 获取用户配置的允许下载类型
        types = cm.get_config("scrape_image_types")
        self.allowed_types = set(types) if types else set()

    # -------------------------
    #  智能下载/复制图片
    # -------------------------
    def _smart_download(self, url_suffix: Optional[str], save_path: Path, image_type: str, size: str = "original"):
        """
        下载图片，支持去重和类型检查
        """
        if not url_suffix:
            return

        # 1. 类型检查
        if image_type not in self.allowed_types:
            return

        # 2. 检查目录和文件存在性
        save_path.parent.mkdir(parents=True, exist_ok=True)
        if save_path.exists():
            return

        # 3. 构造完整 URL
        full_url = f"{self.tmdb_image_base}{size}{url_suffix}"

        # 4. 缓存检查
        if full_url in self.url_cache:
            existing_path = self.url_cache[full_url]
            if existing_path.exists():
                try:
                    shutil.copy2(existing_path, save_path)
                    logger.info(f"[刮削] 快速复制: {save_path.name} (源自 {existing_path.name})")
                    return
                except Exception as e:
                    logger.warning(f"[刮削] 复制失败，转为重新下载: {e}")

        # 5. 执行网络下载
        try:
            resp = requests.get(full_url, headers=self.headers, timeout=15)
            if resp.status_code == 200:
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                logger.debug(f"[刮削] 下载成功: {save_path.name} [{image_type}]")
                self.url_cache[full_url] = save_path
            else:
                logger.warning(f"[刮削] 下载失败 {resp.status_code}: {full_url}")
        except Exception as e:
            logger.warning(f"[刮削] 下载异常: {e}")

    def _get_tmdb_images(self, tmdb_id: int, media_type: str) -> Dict[str, List[Dict]]:
        if not self.api_key: return {}
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images"
        params = {"api_key": self.api_key, "include_image_language": "zh,cn,null,en"}
        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=10)
            if resp.status_code == 200: return resp.json()
        except Exception:
            pass
        return {}

    def _write_nfo(self, data: Dict[str, Any], save_path: Path, root_tag: str):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        if save_path.exists(): return
        root = ET.Element(root_tag)
        for key, value in data.items():
            if value is None or isinstance(value, list): continue
            elem = ET.SubElement(root, key)
            elem.text = str(value)
        if hasattr(ET, "indent"): ET.indent(root, space="  ")
        try:
            ET.ElementTree(root).write(save_path, encoding="utf-8", xml_declaration=True)
        except Exception:
            pass

    # -------------------------
    #  电影刮削
    # -------------------------
    def scrape_movie(self, save_path: Path, info: Dict[str, Any]):
        if not info: return
        self.url_cache.clear()
        tmdb_id = info.get("id")
        folder = save_path.parent
        nfo_path = folder / "movie.nfo"
        if not nfo_path.exists():
            nfo_path = folder / f"{save_path.stem}.nfo"

        images_data = self._get_tmdb_images(tmdb_id, "movie")
        posters = images_data.get("posters", [])
        backdrops = images_data.get("backdrops", [])
        logos = images_data.get("logos", [])

        # 下载各类图片
        p_url = posters[0]['file_path'] if posters else info.get("poster_path")
        self._smart_download(p_url, folder / "poster.jpg", image_type="poster")

        bd_url_1 = backdrops[0]['file_path'] if backdrops else info.get("backdrop_path")
        self._smart_download(bd_url_1, folder / "backdrop.jpg", image_type="backdrop")
        self._smart_download(bd_url_1, folder / "fanart.jpg", image_type="backdrop")

        bd_url_2 = backdrops[1]['file_path'] if len(backdrops) > 1 else bd_url_1
        self._smart_download(bd_url_2, folder / "background.jpg", image_type="background")

        bd_url_3 = backdrops[2]['file_path'] if len(backdrops) > 2 else bd_url_1
        self._smart_download(bd_url_3, folder / "thumb.jpg", image_type="thumb", size="w500")

        self._smart_download(bd_url_1, folder / "banner.jpg", image_type="banner", size="w780")

        if logos:
            l_url = logos[0]['file_path']
            self._smart_download(l_url, folder / "logo.png", image_type="logo")
            self._smart_download(l_url, folder / "clearart.png", image_type="clearart")
        elif info.get("logo_path"):
            self._smart_download(info.get("logo_path"), folder / "logo.png", image_type="logo")

        nfo_data = {
            "title": info.get("title"), "originaltitle": info.get("original_title"),
            "plot": info.get("overview"), "year": info.get("release_date", "")[:4],
            "tmdbid": info.get("id"), "rating": info.get("vote_average"), "id": info.get("id"),
        }
        self._write_nfo(nfo_data, nfo_path, "movie")

    # -------------------------
    #  电视剧刮削
    # -------------------------
    def scrape_tv_show(self, work_path: Path, info: Dict[str, Any]):
        if not info: return
        self.url_cache.clear()
        tmdb_id = info.get("id")
        images_data = self._get_tmdb_images(tmdb_id, "tv")
        posters = images_data.get("posters", [])
        backdrops = images_data.get("backdrops", [])
        logos = images_data.get("logos", [])

        p_url = posters[0]['file_path'] if posters else info.get("poster_path")
        self._smart_download(p_url, work_path / "poster.jpg", image_type="poster")

        bd_url_1 = backdrops[0]['file_path'] if backdrops else info.get("backdrop_path")
        self._smart_download(bd_url_1, work_path / "backdrop.jpg", image_type="backdrop")
        self._smart_download(bd_url_1, work_path / "fanart.jpg", image_type="backdrop")

        bd_url_2 = backdrops[1]['file_path'] if len(backdrops) > 1 else bd_url_1
        self._smart_download(bd_url_2, work_path / "background.jpg", image_type="background")

        bd_url_3 = backdrops[2]['file_path'] if len(backdrops) > 2 else bd_url_1
        self._smart_download(bd_url_3, work_path / "thumb.jpg", image_type="thumb", size="w500")

        self._smart_download(bd_url_1, work_path / "banner.jpg", image_type="banner", size="w780")

        if logos:
            self._smart_download(logos[0]['file_path'], work_path / "logo.png", image_type="logo")
            self._smart_download(logos[0]['file_path'], work_path / "clearart.png", image_type="clearart")

        nfo_data = {
            "title": info.get("name"), "originaltitle": info.get("original_name"),
            "plot": info.get("overview"), "userrating": info.get("vote_average"),
            "premiered": info.get("first_air_date"), "tmdbid": info.get("id"),
            "studio": (info.get("networks") or [{}])[0].get("name", ""),
            "genre": " / ".join(g["name"] for g in info.get("genres", [])),
        }
        self._write_nfo(nfo_data, work_path / "tvshow.nfo", "tvshow")

    # -------------------------
    #  单季刮削
    # -------------------------
    def scrape_season(self, work_path: Path, season_number: int, info: Dict[str, Any], season_dir: Path = None):
        """
        :param work_path: 剧集根目录，用于存放 seasonXX-poster.jpg
        :param season_number: 季号
        :param info: TMDB信息
        :param season_dir: 真实的季文件夹路径 (如果使用了自定义模板，这里会传入真实路径)
        """
        season_info = next((s for s in info.get("seasons", []) if s.get("season_number") == season_number), None)
        if not season_info: return

        poster = season_info.get("poster_path")
        if poster:
            # 1. 标准季海报：放在剧集根目录下，命名为 seasonXX-poster.jpg
            # 这是 Kodi/Emby 识别多季海报的标准方式，与子文件夹命名无关。
            self._smart_download(
                poster,
                work_path / f"season{season_number:02d}-poster.jpg",
                image_type="poster"
            )

        # 2. 季 NFO：放在真实的季文件夹内
        # 如果没有传入 season_dir (未使用自定义模板)，则默认假设为 Season X
        target_dir = season_dir if season_dir else (work_path / f"Season{season_number}")

        # 确保目录存在（如果是自定义模板，目录通常已经由 Trans 创建好了，但这里兜底一下）
        if not target_dir.exists():
            # 只有在默认模式下才尝试创建默认目录，自定义模式下如果目录不存在则跳过nfo
            if not season_dir:
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except:
                    pass

        if target_dir.exists():
            nfo_data = {
                "title": season_info.get("name", f"Season {season_number}"),
                "season": season_number, "plot": season_info.get("overview"),
                "premiered": season_info.get("air_date"), "id": season_info.get("id"),
            }
            self._write_nfo(nfo_data, target_dir / "season.nfo", "season")

    # -------------------------
    #  单集刮削
    # -------------------------
    def scrape_episode(self, video_target: Path, info: Dict[str, Any], season_number: int, episode_num: int):
        season_info = next((s for s in info.get("seasons", []) if s.get("season_number") == season_number), None)
        if not season_info: return
        episodes = season_info.get("episodes") or []
        ep = next((e for e in episodes if e.get("episode_number") == episode_num), None)
        if not ep: return

        # 生成 NFO
        nfo_data = {
            "title": ep.get("name"), "plot": ep.get("overview"),
            "season": season_number, "episode": episode_num,
            "aired": ep.get("air_date"), "rating": ep.get("vote_average"), "id": ep.get("id"),
        }
        self._write_nfo(nfo_data, video_target.with_suffix(".nfo"), "episodedetails")

        # 生成缩略图 (与视频同名)
        still = ep.get("still_path")
        if still:
            # 单集截图使用 "thumb" 类型
            self._smart_download(
                still,
                video_target.with_suffix(".jpg"),
                image_type="thumb"
            )
