import os
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional
from ..logger import logger


class Scraper:
    def __init__(self):
        self.base_image_url = "https://image.tmdb.org/t/p/original"
        self.headers = {
            "User-Agent": "Mozilla/5.0"
        }

    def _download_image(self, url_suffix: Optional[str], save_path: Path):
        """通用图片下载函数"""
        if not url_suffix or save_path.exists():
            return

        full_url = f"{self.base_image_url}{url_suffix}"
        try:
            response = requests.get(full_url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"[刮削] 图片下载成功: {save_path.name}")
            else:
                logger.warning(f"[刮削] 图片下载失败 ({response.status_code})")
        except Exception as e:
            logger.warning(f"[刮削] 图片下载异常: {e}")

    def _write_nfo(self, data: Dict[str, Any], save_path: Path, root_tag: str):
        """通用 NFO 生成函数"""
        if save_path.exists():
            return

        root = ET.Element(root_tag)
        for key, value in data.items():
            if value is not None:
                if isinstance(value, list): continue
                elem = ET.SubElement(root, key)
                elem.text = str(value)

        if hasattr(ET, 'indent'):
            ET.indent(root, space="  ")

        try:
            tree = ET.ElementTree(root)
            tree.write(save_path, encoding='utf-8', xml_declaration=True)
            logger.info(f"[刮削] NFO生成成功: {save_path.name}")
        except Exception:
            pass

    def scrape_movie(self, save_path: Path, info: Dict[str, Any]):
        if not info:
            return

        work_dir = save_path.parent
        prefix = save_path.stem
        nfo_path = save_path.with_suffix(".nfo")

        # ---- 图片 ----
        logger.info(str(info.keys()))
        poster = info.get("poster_path")
        backdrop = info.get("backdrop_path")
        logo = info.get("logo_path")

        url = lambda p: f"https://image.tmdb.org/t/p/original{p}" if p else None

        self._download_image(url(poster), work_dir / f"{prefix}-poster.jpg")
        self._download_image(url(backdrop), work_dir / f"{prefix}-background.jpg")
        self._download_image(url(logo), work_dir / f"{prefix}-logo.png")

        # ---- NFO 结构 ----
        nfo_data = {
            "title": info.get("title"),
            "originaltitle": info.get("original_title"),
            "plot": info.get("overview"),
            "year": info.get("release_date", "")[:4],
            "tmdbid": info.get("id"),
        }

        self._write_nfo(nfo_data, nfo_path, "movie")

    def scrape_tv_show(self, work_path: Path, info: Dict[str, Any]):
        # ---- 图片下载 ----
        poster = info.get("poster_path")
        backdrop = info.get("backdrop_path")
        logo = info.get("logo_path")

        url = lambda p: f"https://image.tmdb.org/t/p/original{p}" if p else None

        self._download_image(url(poster), work_path / "poster.jpg")
        self._download_image(url(backdrop), work_path / "background.jpg")
        self._download_image(url(logo), work_path / "logo.png")

        # ---- NFO ----
        nfo_data = {
            "title": info.get('name'),
            "originaltitle": info.get('original_name'),
            "plot": info.get('overview'),
            "userrating": info.get('vote_average'),
            "premiered": info.get('first_air_date'),
            "id": info.get('id'),
            "tmdbid": info.get('id'),
            "studio": info.get('networks')[0]['name'] if info.get('networks') else "",
            "genre": " / ".join([g['name'] for g in info.get('genres', [])]),
        }

        self._write_nfo(nfo_data, work_path / "tvshow.nfo", "tvshow")

    def scrape_season(self, work_path: Path, season_id: int, info: Dict[str, Any]):
        season_info = next((s for s in info.get('seasons', []) if s.get('season_number') == season_id), None)
        if not season_info:
            return

        # 根目录下的 seasonXX-poster.jpg
        poster_suffix = season_info.get('poster_path')
        if poster_suffix:
            self._download_image(poster_suffix, work_path / f"season{season_id:02d}-poster.jpg")

        # 季度文件夹内的 season.nfo
        season_dir = work_path / f"Season{season_id}"
        season_dir.mkdir(parents=True, exist_ok=True)

        nfo_data = {
            "title": season_info.get('name', f'Season {season_id}'),
            "season": season_id,
            "plot": season_info.get('overview'),
            "premiered": season_info.get('air_date'),
            "id": season_info.get('id')
        }
        self._write_nfo(nfo_data, season_dir / "season.nfo", "season")

    def scrape_episode(self, video_target_path: Path, info: Dict[str, Any], season_id: int, episode_num: int):
        season_info = next((s for s in info.get('seasons', []) if s.get('season_number') == season_id), None)
        ep_data = None
        if season_info and 'episodes' in season_info:
            ep_data = next((e for e in season_info['episodes'] if e.get('episode_number') == episode_num), None)

        if not ep_data:
            return

        # 生成 NFO
        nfo_path = video_target_path.with_suffix('.nfo')
        nfo_data = {
            "title": ep_data.get('name'),
            "plot": ep_data.get('overview'),
            "season": season_id,
            "episode": episode_num,
            "aired": ep_data.get('air_date'),
            "rating": ep_data.get('vote_average'),
            "id": ep_data.get('id')
        }
        self._write_nfo(nfo_data, nfo_path, "episodedetails")

        # 下载单集图片
        still_path = ep_data.get('still_path')
        if still_path:
            # 直接生成同名 jpg
            img_path = video_target_path.with_suffix('.jpg')
            self._download_image(still_path, img_path)
