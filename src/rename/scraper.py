import os
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional
from ..logger import logger


class Scraper:
    def __init__(self):
        self.headers = {"User-Agent": "Mozilla/5.0"}
        self.tmdb_image_base = "https://image.tmdb.org/t/p/original"

    # -------------------------
    #  工具：创建目录 & 下载图片
    # -------------------------
    def _download_image(self, url_suffix: Optional[str], save_path: Path):
        """下载 TMDB 图片，自动创建目录"""
        if not url_suffix:
            return

        save_path.parent.mkdir(parents=True, exist_ok=True)

        if save_path.exists():
            return

        full_url = f"{self.tmdb_image_base}{url_suffix}"

        try:
            resp = requests.get(full_url, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"[刮削] 图片下载失败 {resp.status_code}: {save_path}")
                return

            with open(save_path, "wb") as f:
                f.write(resp.content)

            logger.info(f"[刮削] 图片下载成功: {save_path}")

        except Exception as e:
            logger.warning(f"[刮削] 图片下载异常: {e}")

    # -------------------------
    #  工具：写 NFO
    # -------------------------
    def _write_nfo(self, data: Dict[str, Any], save_path: Path, root_tag: str):
        """生成 NFO，自动创建目录"""
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if save_path.exists():
            return

        root = ET.Element(root_tag)

        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, list):
                continue

            elem = ET.SubElement(root, key)
            elem.text = str(value)

        if hasattr(ET, "indent"):
            ET.indent(root, space="  ")

        try:
            ET.ElementTree(root).write(save_path, encoding="utf-8", xml_declaration=True)
            logger.info(f"[刮削] NFO生成成功: {save_path}")
        except Exception as e:
            logger.warning(f"[刮削] NFO生成失败: {e}")

    # -------------------------
    #  电影刮削
    # -------------------------
    def scrape_movie(self, save_path: Path, info: Dict[str, Any]):
        if not info:
            return

        folder = save_path.parent
        prefix = save_path.stem
        nfo_path = save_path.with_suffix(".nfo")

        # 图片
        self._download_image(info.get("poster_path"),     folder / f"{prefix}-poster.jpg")
        self._download_image(info.get("backdrop_path"),   folder / f"{prefix}-background.jpg")
        self._download_image(info.get("logo_path"),       folder / f"{prefix}-logo.png")

        # NFO
        nfo_data = {
            "title": info.get("title"),
            "originaltitle": info.get("original_title"),
            "plot": info.get("overview"),
            "year": info.get("release_date", "")[:4],
            "tmdbid": info.get("id"),
        }

        self._write_nfo(nfo_data, nfo_path, "movie")

    # -------------------------
    #  电视剧刮削
    # -------------------------
    def scrape_tv_show(self, work_path: Path, info: Dict[str, Any]):
        if not info:
            return

        # 图片
        self._download_image(info.get("poster_path"),    work_path / "poster.jpg")
        self._download_image(info.get("backdrop_path"),  work_path / "background.jpg")
        self._download_image(info.get("logo_path"),      work_path / "logo.png")

        # NFO
        nfo_data = {
            "title": info.get("name"),
            "originaltitle": info.get("original_name"),
            "plot": info.get("overview"),
            "userrating": info.get("vote_average"),
            "premiered": info.get("first_air_date"),
            "tmdbid": info.get("id"),
            "studio": (info.get("networks") or [{}])[0].get("name", ""),
            "genre": " / ".join(g["name"] for g in info.get("genres", [])),
        }

        self._write_nfo(nfo_data, work_path / "tvshow.nfo", "tvshow")

    # -------------------------
    #  单季刮削
    # -------------------------
    def scrape_season(self, work_path: Path, season_number: int, info: Dict[str, Any]):
        season_info = next(
            (s for s in info.get("seasons", []) if s.get("season_number") == season_number),
            None
        )
        if not season_info:
            return

        # seasonXX-poster
        poster = season_info.get("poster_path")
        if poster:
            self._download_image(
                poster,
                work_path / f"season{season_number:02d}-poster.jpg"
            )

        # Season 目录
        season_dir = work_path / f"Season{season_number:02d}"
        season_dir.mkdir(parents=True, exist_ok=True)

        # season.nfo
        nfo_data = {
            "title": season_info.get("name", f"Season {season_number}"),
            "season": season_number,
            "plot": season_info.get("overview"),
            "premiered": season_info.get("air_date"),
            "id": season_info.get("id"),
        }

        self._write_nfo(nfo_data, season_dir / "season.nfo", "season")

    # -------------------------
    #  单集刮削
    # -------------------------
    def scrape_episode(self, video_target: Path, info: Dict[str, Any], season_number: int, episode_num: int):
        season_info = next(
            (s for s in info.get("seasons", []) if s.get("season_number") == season_number),
            None
        )
        if not season_info:
            return

        episodes = season_info.get("episodes") or []
        ep = next((e for e in episodes if e.get("episode_number") == episode_num), None)
        if not ep:
            return

        # episode.nfo
        nfo_data = {
            "title": ep.get("name"),
            "plot": ep.get("overview"),
            "season": season_number,
            "episode": episode_num,
            "aired": ep.get("air_date"),
            "rating": ep.get("vote_average"),
            "id": ep.get("id"),
        }

        self._write_nfo(nfo_data, video_target.with_suffix(".nfo"), "episodedetails")

        # still image
        still = ep.get("still_path")
        if still:
            self._download_image(still, video_target.with_suffix(".jpg"))
