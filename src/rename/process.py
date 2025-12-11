import re
import json
import uuid
import time
import types
import threading
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Union, Optional

from jikanpy import Jikan
from nicegui import app

from .trans import Trans
from ..logger import logger
from .get_info import Search
from ..utils.path import TASK_PATH
from .ai_processor import AIProcessor
from ..config.config_manager import cm
from ..ai.models import AIAnalysisResult
from .utils import S0_TAG, EXTRA_TAG, IGNORE_DIR, VIDEO_SUFFIX, IGNORE_SUFFIX
from .scraper import Scraper
from .cleaner import (
    remove_tag,
    to_sim_max,
    remove_code,
    remove_season,
    divide_by_year,
    extract_number,
    extract_season,
    extract_tmdb_id,
    remove_episode,
    extract_base_num,
    match_and_extract,
    remove_similar_part,
    find_unique_parts_in_videos,
    clean_noise,
    parse_filename,
    get_render_context,
    render_path_template,
    is_weak_filename,
    is_season_name,
)

jikan = Jikan()


class Rename:
    _lock = threading.Lock()
    _dir_cache = {}
    _ai_mapping_cache = {}
    _processed_paths = set()
    _processing_paths = set()
    _tmdb_search_cache: Dict[tuple, tuple] = {}
    _logger_patched = False

    def __init__(self):
        self.BANGUMI_PATH = Path(cm.get_config('bangumi_path'))
        self.MOVIE_PATH = Path(cm.get_config('movie_path'))
        self.ANIME_PATH = Path(cm.get_config('anime_path'))
        self.ANIME_MOVIE_PATH = Path(cm.get_config('anime_movie_path'))

        self.ANIME_MOVIE_PATH.mkdir(parents=True, exist_ok=True)
        self.MOVIE_PATH.mkdir(parents=True, exist_ok=True)
        self.ANIME_PATH.mkdir(parents=True, exist_ok=True)
        self.BANGUMI_PATH.mkdir(parents=True, exist_ok=True)
        self.search = Search()
        self.ai_processor = AIProcessor()
        self.scraper = Scraper()

        self.R = {}
        self.scraped_seasons = set()

        self._patch_logger_safe()

    def _patch_logger_safe(self):
        """修复 NiceGUI 日志并发问题"""
        if Rename._logger_patched or app is None:
            return

        with Rename._lock:
            if Rename._logger_patched:
                return

            try:
                for handler in logger.handlers:
                    if hasattr(handler, 'log_element') and not getattr(handler, '_is_patched', False):
                        def safe_emit(h_self, record):
                            try:
                                msg = h_self.format(record)
                                # 使用 call_from_background 确保 UI 更新在主线程
                                try:
                                    app.call_from_background(h_self.log_element.push, msg)
                                except Exception:
                                    pass
                            except Exception:
                                pass  # 忽略错误防止循环

                        handler.emit = types.MethodType(safe_emit, handler)
                        handler._is_patched = True
                Rename._logger_patched = True
            except Exception:
                pass

    def _get_category_folder(self, info: Dict, is_movie: bool, is_anime: bool) -> str:
        if not info:
            return "未分类"

        genres = info.get('genres', [])
        genre_ids = [g.get('id') for g in genres]
        original_language = info.get('original_language', '').lower()

        countries = []
        if 'origin_country' in info:
            countries = info.get('origin_country', [])
        elif 'production_countries' in info:
            countries = [c.get('iso_3166_1') for c in info.get('production_countries', [])]

        def is_chinese_region():
            return original_language in ['zh', 'cn', 'bo', 'za'] or \
                any(c in ['CN', 'HK', 'TW'] for c in countries)

        def is_jap_kor_region():
            return original_language in ['ja', 'ko'] or \
                any(c in ['JP', 'KR'] for c in countries)

        if is_movie:
            if 16 in genre_ids or is_anime:
                return "动画电影"
            if is_chinese_region():
                return "华语电影"
            return "外语电影"

        if 10762 in genre_ids:
            return "儿童"
        if 99 in genre_ids:
            return "纪录片"
        if 10764 in genre_ids or 10767 in genre_ids:
            return "综艺"
        if is_anime or 16 in genre_ids:
            if is_chinese_region():
                return "国漫"
            return "日漫"
        if is_chinese_region():
            return "国产剧"
        if is_jap_kor_region():
            return "日韩剧"
        return "欧美剧"

    def _process_accompanying_files(self, source_video_path: Path, target_video_path: Path):
        """
        处理伴随文件（字幕等）
        source_video_path: 原视频路径 (例如 /downloads/Movie.mp4)
        target_video_path: 新视频路径 (例如 /data/Movie/Movie.mp4)
        """
        try:
            # 1. 获取允许的后缀列表
            allowed_exts = cm.get_config('subtitle_extensions') or ['.ass', '.srt', '.sub']

            # 2. 获取源目录和视频的文件名主干
            source_dir = source_video_path.parent
            video_stem = source_video_path.stem  # "Movie"

            # 3. 遍历源目录寻找匹配文件
            for sibling in source_dir.iterdir():
                if sibling == source_video_path:
                    continue
                if sibling.is_dir():
                    continue

                # 检查后缀是否在允许列表中
                if sibling.suffix.lower() not in allowed_exts:
                    continue

                # 检查文件名是否以视频名开头
                # 视频: S01E01.mp4 (stem: S01E01)
                # 字幕: S01E01.zh.ass
                if sibling.name.startswith(video_stem):
                    # 提取剩余的后缀部分 (包括语言标记)
                    # S01E01.zh.ass - S01E01 = .zh.ass
                    suffix_part = sibling.name[len(video_stem):]

                    # 构造新的目标路径
                    # 目标视频: .../Show/S01/NewName.mp4
                    # 新字幕: .../Show/S01/NewName.zh.ass
                    new_sub_name = target_video_path.stem + suffix_part
                    target_sub_path = target_video_path.parent / new_sub_name

                    # 加入重命名队列
                    self.R[sibling] = target_sub_path
                    logger.debug(f"[伴随文件] {sibling.name} -> {target_sub_path.name}")

        except Exception as e:
            logger.warning(f"[伴随文件] 处理出错: {e}")

    def get_season_id(
            self,
            tv_info: Dict,
            work_path: Path,
            path: Path,
            titles: Optional[List[Dict]],
    ):
        season_id = 1
        path_name = path.name
        all_similaritys: List[Dict] = []

        int_rtpath_name = extract_season(path_name)
        logger.debug(f'[处理任务] 提取标题季号:{int_rtpath_name}')

        matched_via_tmdb = False

        for season in tv_info['seasons']:
            info_season_id = season['season_number']

            if info_season_id == int_rtpath_name:
                season_id = int_rtpath_name
                matched_via_tmdb = True
                break

            sname: str = season['name']

            if not (sname.strip().startswith('Season') and '1' in sname):
                if sname in path.name:
                    sname_list = sname.split(' ')
                    path_name_list = path.stem.split(' ')
                    if len(sname_list) == len(path_name_list):
                        season_id = info_season_id
                        matched_via_tmdb = True
                        break

                if titles:
                    for title in titles:
                        similaritys = {}
                        ename = title['title']
                        clean_path_name = path_name.replace(ename, '')
                        similarity = SequenceMatcher(
                            None, sname, remove_tag(clean_path_name)
                        ).ratio()
                        similaritys[similarity] = info_season_id
                        all_similaritys.append(similaritys)

        if not matched_via_tmdb:
            if int_rtpath_name > 0:
                logger.info(f'[处理任务] TMDB未包含S{int_rtpath_name}，但文件名明确标识，强制使用文件名季号。')
                season_id = int_rtpath_name
            elif all_similaritys:
                season_id = to_sim_max(all_similaritys)

        logger.info(f'[处理任务] 最终识别季号：{season_id}')

        return season_id

    def process_sub(
            self,
            itme_path_main_name: str,
            item_repeat: Optional[List[str]],
            item_path: Path,
            work_path: Path,
            season_id: int,
            info: Optional[Dict] = None,
            cus_offset: Optional[int] = None,
            cus_season_id: Optional[int] = None,
    ):
        time.sleep(0.005)

        item_name = item_path.name
        if item_repeat:
            item_name_remove = remove_similar_part(item_repeat, item_path.stem)
        else:
            item_name_remove = item_path.stem

        item_name_l = item_name_remove.lower()
        item_suffix = item_path.suffix.lower()

        n_item_name_l = item_name.replace(itme_path_main_name, '').lower()

        enable_scrape = cm.get_config('scrape_metadata')

        for ignore_dir in IGNORE_DIR:
            if ignore_dir in item_path.name:
                return

        for ignore_tag in IGNORE_SUFFIX:
            if ignore_tag in item_suffix:
                return

        tv_rename_format = cm.get_config('tv_rename_format')
        ep = 0
        _idata = match_and_extract(item_name)
        if _idata:
            if cus_season_id is None:
                season_id = int(_idata[0])
            ep = _idata[1]
        else:
            p = r'[a-zA-Z0-9]'
            for ex in EXTRA_TAG:
                if re.search(rf'(?<!{p}){ex.lower()}(?!{p})', n_item_name_l):
                    t = work_path / 'extra'
                    target_file = t / item_name # 提取变量
                    self.R[item_path] = target_file
                    self._process_accompanying_files(item_path, target_file)
                    logger.info(f'[处理任务] 处理完成{item_name} (Extra)')
                    return

            for s0 in S0_TAG:
                if re.search(rf'(?<!{p}){s0.lower()}(?!{p})', item_name_l) or \
                        re.search(rf'(?<!{p}){s0.lower()}[\d]{{1,3}}(?!{p})', item_name_l):
                    t = work_path / 'Season0'
                    target_file = t / item_name # 提取变量
                    self.R[item_path] = target_file
                    self._process_accompanying_files(item_path, target_file)
                    logger.info(f'[处理任务] 处理完成{item_name} (Season0)')
                    return

            _item_name = remove_code(remove_season(item_name_l))
            epp = extract_base_num(_item_name)
            if epp is not None:
                ep = int(epp)
            elif _item_name.isdigit():
                ep = int(_item_name)

        if cus_offset is not None and cus_offset != 0:
            ep = ep + cus_offset

        if tv_rename_format and info and ep > 0:
            ctx = get_render_context(item_path, info, int(season_id), int(ep))
            rel_path = render_path_template(tv_rename_format, ctx)

            if rel_path:
                path_str = str(rel_path).replace("\\", "/")
                path_str = re.sub(r'\.{2,}', '.', path_str)
                p = Path(path_str)

                if work_path.name.lower() == p.parts[0].lower():
                    if len(p.parts) > 1:
                        final_rel_path = Path(*p.parts[1:])
                        target_file = work_path / final_rel_path
                    else:
                        target_file = work_path / p.name
                else:
                    if len(p.parts) > 1:
                        target_file = work_path.parent / p
                    else:
                        target_file = work_path / p

                self.R[item_path] = target_file
                logger.info(f'[自定义格式] 目标路径: {target_file}')
                self._process_accompanying_files(item_path, target_file)

                if enable_scrape:
                    try:
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        logger.warning(f"[目录创建] 预创建目录失败: {e}")

                    if season_id not in self.scraped_seasons:
                        try:
                            real_season_dir = target_file.parent
                            real_show_dir = real_season_dir.parent
                            if real_show_dir.name == work_path.parent.name:
                                real_show_dir = real_season_dir

                            self.scraper.scrape_season(
                                work_path=real_show_dir,
                                season_number=season_id,
                                info=info,
                                season_dir=real_season_dir
                            )
                            self.scraped_seasons.add(season_id)
                        except Exception as e:
                            logger.warning(f"[刮削警告] 自定义模板季刮削失败: {e}")

                    try:
                        self.scraper.scrape_episode(target_file, info, int(season_id), int(ep))
                    except:
                        pass

                return

        t = work_path / f'Season{int(season_id)}'

        if enable_scrape and info:
            if season_id not in self.scraped_seasons:
                try:
                    self.scraper.scrape_season(work_path, season_id, info)
                except Exception as e:
                    logger.warning(f"[刮削警告] 无法获取第 {season_id} 季元数据: {e}")
                finally:
                    self.scraped_seasons.add(season_id)

        ep_str = f'0{ep}' if ep < 10 else ep
        s = f'0{int(season_id)}'
        ss = s if season_id < 10 else int(season_id)
        t.mkdir(parents=True, exist_ok=True)
        ft = f'S{ss}E{ep_str}'
        target_file = t / f'{ft} - {item_name}'
        self.R[item_path] = target_file
        self._process_accompanying_files(item_path, target_file)

        if enable_scrape and info and ep > 0:
            try:
                self.scraper.scrape_episode(
                    t / f'{ft} - {item_name}',
                    info,
                    int(season_id),
                    int(ep)
                )
            except Exception:
                pass

        logger.info(f'[处理任务] 处理完成{item_name}')

    def process(
            self,
            path: Path,
            _is_anime: Optional[bool] = None,
            _is_movie: Optional[bool] = None,
            _tuuid: Optional[str] = None,
            cus_name: Optional[str] = None,
            cus_season_id: Optional[int] = None,
            cus_tmdb_id: Optional[str] = None,
            cus_offset: Optional[int] = None,
            use_ai: Optional[bool] = None,
            _ai_attempted: bool = False,
            _scoped_cache: Optional[Dict] = None,
    ):
        time.sleep(0.01)

        if cus_tmdb_id and str(cus_tmdb_id).lower() in ['none', 'null', 'n/a', '']:
            cus_tmdb_id = None

        initial_context = {
            'is_anime': _is_anime,
            'is_movie': _is_movie,
            'cus_name': cus_name,
            'cus_season_id': cus_season_id,
            'cus_tmdb_id': cus_tmdb_id,
            'cus_offset': cus_offset,
            'use_ai': use_ai,
            'ai_attempted': _ai_attempted
        }

        if path.is_file():
            if path.suffix.lower() not in VIDEO_SUFFIX:
                logger.debug(f"[跳过] 文件后缀不在允许列表中: {path.name}")
                return False

            return self._process(path, _uuid=_tuuid, **initial_context)

        stack = [(path, _tuuid, initial_context)]
        final_result = True

        while stack:
            time.sleep(0.005)

            curr_path, curr_uuid, ctx = stack.pop()

            if curr_path.name.startswith(('.', '@', '$RECYCLE')):
                continue

            if curr_path.is_file():
                if curr_path.suffix.lower() in VIDEO_SUFFIX:
                    res = self._process(curr_path, _uuid=curr_uuid, **ctx)
                    if isinstance(res, str):
                        final_result = res
                    continue

            if curr_path.is_dir():
                has_video_files = False
                for sub_path in curr_path.iterdir():
                    if sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX:
                        has_video_files = True
                        break

                dir_processed_successfully = False
                child_context = ctx.copy()

                if has_video_files:
                    use_uuid = curr_uuid if curr_uuid else str(uuid.uuid4())
                    res = self._process(curr_path, _uuid=use_uuid, **ctx)

                    if res is True:
                        dir_processed_successfully = True
                    elif res == "SKIP_DIR_IS_MOVIE":
                        logger.info(
                            f"[合集识别] 目录 '{curr_path.name}' 被判定为电影/合集。放弃目录重命名，转为以 [文件名] 为准分别处理子文件。")
                        dir_processed_successfully = False

                        child_context['is_movie'] = True
                        child_context['is_anime'] = False
                        child_context['cus_name'] = None
                        child_context['cus_tmdb_id'] = None
                        child_context['ai_attempted'] = False

                        error_task_file = TASK_PATH / f"{use_uuid}.json"
                        if error_task_file.exists():
                            try:
                                error_task_file.unlink()
                            except:
                                pass

                    else:
                        logger.warning(f"[降级处理] 目录 [{curr_path.name}] 整体识别失败，转为尝试单独识别内部文件...")
                        error_task_file = TASK_PATH / f"{use_uuid}.json"
                        if error_task_file.exists():
                            try:
                                error_task_file.unlink()
                            except:
                                pass

                if not dir_processed_successfully:
                    logger.info(f"[循环扫描] 进入子目录: {curr_path}")
                    try:
                        children = []
                        for sub_path in curr_path.iterdir():
                            if sub_path.is_dir() or (sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX):
                                children.append((sub_path, None, child_context))
                        stack.extend(children)
                    except Exception as e:
                        logger.error(f"遍历目录出错 {curr_path}: {e}")

        return final_result

    def check_task_type(
            self,
            _uuid: str,
            rtpath_name: str,
            year: int,
            path: Path,
            is_anime: Optional[bool] = None,
            is_movie: Optional[bool] = None,
    ) -> Union[Tuple[str, Dict, bool, bool], str]:
        time.sleep(0.005)

        norm_name = rtpath_name.strip().lower()
        filename = path.name

        logger.info(f"[搜索参数] 准备搜索: Name='{rtpath_name}', Year={year}, Is_Movie={is_movie}, Is_Anime={is_anime}")

        with Rename._lock:
            cached_res = None
            if is_movie:
                cache_key = (norm_name, year, "movie")
                if cache_key in Rename._tmdb_search_cache:
                    cached_res = Rename._tmdb_search_cache[cache_key]

        if is_movie:
            logger.info(f"[处理任务] 上下文强制指定为电影类型，使用文件名 '{rtpath_name}' 进行搜索...")

            if cached_res:
                s2_name, s2_info = cached_res
                logger.debug(f"[TMDB缓存] 命中电影缓存: {s2_name}")
            else:
                logger.debug(f"[TMDB搜索] 正在搜索电影: 关键词='{rtpath_name}', 年份={year}")
                s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)

            if s2_name and s2_info and year > 0:
                release_date = s2_info.get('release_date', '')
                tmdb_year = int(release_date.split('-')[0]) if release_date else 0
                if abs(tmdb_year - year) > 1:
                    ratio = SequenceMatcher(None, rtpath_name.lower(), s2_name.lower()).ratio()
                    ratio_origin = SequenceMatcher(
                        None, rtpath_name.lower(),
                        s2_info.get('original_title', '').lower()
                    ).ratio()
                    if ratio < 0.5 and ratio_origin < 0.5:
                        logger.warning(
                            f"[搜索校验] TMDB结果 '{s2_name}'({tmdb_year}) 与文件名 '{rtpath_name}'({year}) 差异过大，丢弃。"
                        )
                        s2_name, s2_info = None, None

            if not s2_name and year != 0:
                logger.debug(f"[TMDB搜索] 电影带年份搜索失败，尝试去除年份: 关键词='{rtpath_name}'")
                s2_name, s2_info = self.search.get_movie_info(rtpath_name, 0)

            if s2_name:
                with Rename._lock:
                    Rename._tmdb_search_cache[(norm_name, year, "movie")] = (s2_name, s2_info)

            if not s2_name:
                return f'[TMDB] 未搜索到电影信息 (强制Movie模式), 文件名: {rtpath_name}'

            return s2_name, s2_info, (is_anime or False), True

        # 1. 搜索电视剧信息
        has_episode_pattern = extract_base_num(filename)
        if has_episode_pattern:
            logger.info('[处理任务] 检测到 SxxExx 格式，锁定为电视剧模式，跳过电影搜索')
            tv_cache_key = (norm_name, year, "tv")
            with Rename._lock:
                if tv_cache_key in Rename._tmdb_search_cache:
                    s1_name, s1_info = Rename._tmdb_search_cache[tv_cache_key]
                    if s1_name:
                        logger.debug(f"[TMDB缓存] 命中电视剧缓存: {s1_name}")
                else:
                    s1_name, s1_info = None, None

            if not s1_name:
                logger.debug(f"[TMDB搜索] 正在搜索TV (SxxExx模式): 关键词='{rtpath_name}', 年份={year}")
                s1_name, s1_info = self.search.get_tv_info(rtpath_name, year)

                # 尝试去除年份重试
                if not s1_name and year != 0:
                    logger.debug(f"[TMDB搜索] TV带年份搜索失败，尝试去除年份: 关键词='{rtpath_name}'")
                    s1_name, s1_info = self.search.get_tv_info(rtpath_name, 0)

                if s1_name:
                    with Rename._lock:
                        Rename._tmdb_search_cache[tv_cache_key] = (s1_name, s1_info)

            if not s1_name or not s1_info:
                return f'[TMDB] 未搜索到电视剧信息 (检测到SxxExx), 搜索词: {rtpath_name}'

            logger.info(f'[处理任务] 搜索到的电视剧名称: {s1_name}')

            if is_anime is None:
                is_anime = any(g['name'].lower() in ['animation', 'anime'] for g in s1_info.get('genres', []))

            return s1_name, s1_info, is_anime, False
        logger.info('[处理任务] 未传入任务类型且无明确SxxExx特征，开始双向搜索判断！')
        pos = 0
        tv_cache_key = (norm_name, year, "tv")
        logger.debug(f"[TMDB搜索] 双向搜索-尝试TV: 关键词='{rtpath_name}', 年份={year}")
        with Rename._lock:
            s1_name, s1_info = Rename._tmdb_search_cache.get(tv_cache_key, (None, None))

        if not s1_name:
            s1_name, s1_info = self.search.get_tv_info(rtpath_name, year)
            if not s1_name and year != 0:
                s1_name, s1_info = self.search.get_tv_info(rtpath_name, 0)
            if s1_name:
                with Rename._lock:
                    Rename._tmdb_search_cache[tv_cache_key] = (s1_name, s1_info)

        if s1_name:
            logger.info(f'[处理任务] 搜索到的电视剧名称: {s1_name}')

        # 2. 搜索电影信息
        mv_cache_key = (norm_name, year, "movie")
        logger.debug(f"[TMDB搜索] 双向搜索-尝试Movie: 关键词='{rtpath_name}', 年份={year}")
        with Rename._lock:
            s2_name, s2_info = Rename._tmdb_search_cache.get(mv_cache_key, (None, None))

        if not s2_name:
            s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)
            if not s2_name and year != 0:
                s2_name, s2_info = self.search.get_movie_info(rtpath_name, 0)
            if s2_name:
                with Rename._lock:
                    Rename._tmdb_search_cache[mv_cache_key] = (s2_name, s2_info)

        if s2_name:
            logger.info(f'[处理任务] 搜索到的电影名称: {s2_name}')

        season_id = extract_season(rtpath_name)

        if s1_name:
            pos += 1
            if year > 0 and s1_info:
                first_air_date = s1_info.get('first_air_date', '')
                if first_air_date:
                    try:
                        tv_year = int(first_air_date.split('-')[0])
                        if abs(tv_year - year) > 1:
                            pos -= 1.5
                    except:
                        pass

        if s2_name:
            pos -= 1
            if year > 0 and s2_info:
                release_date = s2_info.get('release_date', '')
                if release_date:
                    try:
                        mv_year = int(release_date.split('-')[0])
                        if abs(mv_year - year) <= 1:
                            pos -= 1.5
                    except:
                        pass

        for parent in path.parents:
            pname = parent.name.lower()
            if is_season_name(pname):
                pos += 1
                break

        try:
            dir_to_check = path.parent if path.is_file() else path
            if dir_to_check.is_dir():
                video_files = [
                    p for p in dir_to_check.iterdir()
                    if p.is_file() and p.suffix.lower() in VIDEO_SUFFIX
                ]
                if len(video_files) >= 3:
                    processed_names = []
                    for vf in video_files:
                        base = vf.stem.lower()
                        base = remove_tag(base)
                        base = remove_episode(base)
                        base = remove_season(base)
                        processed_names.append((vf.name, base))

                    similar_pairs = 0
                    n = len(processed_names)
                    for i in range(n):
                        for j in range(i + 1, n):
                            name_i, base_i = processed_names[i]
                            name_j, base_j = processed_names[j]
                            if not base_i or not base_j:
                                continue
                            ratio = SequenceMatcher(None, base_i, base_j).ratio()
                            if 0.8 < ratio < 0.999:
                                similar_pairs += 1

                    if similar_pairs >= 3:
                        if season_id > 0:
                            pos += 1
                        else:
                            pos += 0.2
        except Exception as e:
            logger.warning(f"[处理任务] 检查相似视频文件时出错: {e}")

        if season_id == -1:
            pos -= 0.6
            if path.is_file():
                pos -= 0.5
            else:
                pos += 0.6
        if path.is_file():
            pos += 0.5

        if path.is_dir():
            if len([i for i in path.iterdir() if i.is_file()]) > 6:
                pos += 0.4
            else:
                pos -= 0.4

        if pos > 0 or (is_movie is not None and not is_movie):
            logger.debug(f'[处理任务] 该文件可能为电视剧！(得分: {pos})')
            is_movie = False
            info = s1_info
            name = s1_name
            if not name or not info:
                return f'[TMDB] 未搜索到电视剧信息, 跳过{rtpath_name}'

            if is_anime is None:
                is_anime = any(g['name'].lower() in ['animation', 'anime']
                               for g in info.get('genres', []))
        else:
            logger.debug(f'[处理任务] 该文件可能为电影！(得分: {pos})')
            is_movie = True
            info = s2_info
            name = s2_name
            if not name or not info:
                return f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}'

            if is_anime is None:
                is_anime = any(g['name'].lower() in ['animation', 'anime']
                               for g in info.get('genres', []))

        return name, info, is_anime, is_movie

    def _attempt_ai_recovery(self, path, _uuid, is_anime, cus_offset, cus_season_id, use_ai, check_skip_dir=False,
                             hint_name=None):
        if self.ai_processor.ai_client.is_available():
            try:
                if hint_name:
                    context_hint = hint_name
                else:
                    parts = path.parts
                    if len(parts) >= 3:
                        context_hint = f"{parts[-3]}-{parts[-2]}-{parts[-1]}"
                    elif len(parts) >= 2:
                        context_hint = f"{parts[-2]}-{parts[-1]}"
                    else:
                        context_hint = path.name
            except:
                context_hint = path.name

            logger.info(f"[AI处理] 正在请求AI分析: {context_hint}")

            time.sleep(0.01)

            ai_meta = self.ai_processor.analyze_search_metadata(path, context_hint=context_hint)

            if ai_meta:
                ai_name = ai_meta.get('name')
                ai_tmdb_id = ai_meta.get('tmdb_id')
                if ai_tmdb_id:
                    if str(ai_tmdb_id).strip().lower() in ['none', 'null', 'n/a', '']:
                        ai_tmdb_id = None
                invalid_names = ['未知', 'Unknown', 'None', 'Null', 'TBA', '未识别到官方名称', '待定']
                if not ai_name or any(inv.lower() in ai_name.lower() for inv in invalid_names):
                    logger.warning(f"[AI处理] AI返回了无效名称 '{ai_name}'，视为失败，不进行搜索。")
                    return None

                detected_year = None
                if ai_name:
                    year_match = re.search(r'[\(\[\s](\d{4})[\)\]]?$', ai_name)
                    if year_match:
                        extracted_year = int(year_match.group(1))
                        if 1900 < extracted_year < 2100:
                            detected_year = extracted_year
                        ai_name = ai_name[:year_match.start()].strip()
                        logger.info(f"[AI处理] 从名称中分离年份: Name='{ai_name}', Year={detected_year}")
                if detected_year and ai_name:
                    ai_name = f"{ai_name} ({detected_year})"
                ai_is_movie = ai_meta.get('is_movie', False)
                logger.info(
                    f"[AI处理] AI推断成功: Name={ai_name}, TMDB_ID={ai_tmdb_id}, Movie={ai_is_movie}"
                )

                cache_key = str(path.parent.absolute())
                with Rename._lock:
                    Rename._dir_cache[cache_key] = {
                        'tmdb_id': str(ai_tmdb_id) if ai_tmdb_id else None,
                        'name': ai_name,
                        'is_anime': is_anime,
                        'is_movie': ai_is_movie
                    }
                    Rename._processing_paths.discard(str(path.resolve()))

                logger.info(f"[缓存写入] AI元数据推断结果已缓存至: {path.parent.name}")
                if check_skip_dir and path.is_dir() and ai_is_movie:
                    return "SKIP_DIR_IS_MOVIE"

                return self.process(
                    path,
                    _is_anime=is_anime,
                    _is_movie=ai_is_movie,
                    _tuuid=_uuid,
                    cus_name=ai_name,
                    cus_tmdb_id=ai_tmdb_id,
                    cus_offset=cus_offset,
                    cus_season_id=cus_season_id,
                    use_ai=use_ai,
                    _ai_attempted=True
                )
            else:
                logger.warning("[AI处理] AI执行完毕，但未能推断出有效元数据")
        else:
            logger.warning("[AI处理] AI Client不可用，无法进行补救")
        return None

    def _process(
            self,
            path: Path,
            is_anime: Optional[bool] = None,
            is_movie: Optional[bool] = None,
            _uuid: Optional[str] = None,
            cus_name: Optional[str] = None,
            cus_season_id: Optional[int] = None,
            cus_tmdb_id: Optional[str] = None,
            cus_offset: Optional[int] = None,
            use_ai: Optional[bool] = None,
            ai_attempted: bool = False,
            _scoped_cache: Optional[Dict] = None,
    ):
        if not _uuid:
            _uuid = str(uuid.uuid4())

        time.sleep(0.01)

        abs_str = str(path.resolve())

        with Rename._lock:
            if abs_str in Rename._processed_paths:
                logger.info(f"[跳过] 文件已在之前的任务中处理完毕: {path.name}")
                return True
            if abs_str in Rename._processing_paths:
                logger.info(f"[跳过] 文件当前正在处理中: {path.name}")
                return True
            Rename._processing_paths.add(abs_str)

        try:
            self.scraped_seasons = set()
            self.R = {}

            if not self.search.TMDB_KEY:
                return self.error_reply(_uuid, '无TMDB Key', path)

            enable_scrape = cm.get_config('scrape_metadata')
            global_use_ai = bool(cm.get_config('use_ai'))
            effective_use_ai = use_ai if use_ai is not None else global_use_ai
            enable_secondary = cm.get_config('secondary_classification')

            logger.debug(f'[处理任务] 开始处理{path.name}')

            rtpath_name, year, detected_season, detected_episode = parse_filename(path.name)
            if cus_season_id is None and detected_season:
                cus_season_id = detected_season
            if not cus_tmdb_id:
                extracted_id = extract_tmdb_id(path.name)

                if not extracted_id:
                    extracted_id = extract_tmdb_id(path.parent.name)

                if not extracted_id and path.parent != path.root:
                    pname = path.parent.name
                    if is_season_name(remove_tag(pname).lower().strip()):
                        if path.parent.parent != path.root:
                            extracted_id = extract_tmdb_id(path.parent.parent.name)

                if extracted_id:
                    logger.info(f"[路径解析] 从路径中提取到 TMDB ID: {extracted_id}")
                    cus_tmdb_id = extracted_id

            INVALID_NAMES = ['未知', 'unknown', 'none', 'null', 'tba', '未识别到官方名称', '待定']
            name_check = re.sub(r'[\W_]+', '', rtpath_name.replace(path.suffix, "") if path.suffix else rtpath_name)
            is_weak = is_weak_filename(name_check)

            if is_weak:
                logger.info(f"[智能判断] 文件名 '{path.name}' 判定为弱文件名，强制作为剧集(TV)处理。")
                is_movie = False

            cache_key = str(path.parent.absolute())

            from_ai_cache = False

            cached_data = None
            if _scoped_cache is not None:
                if cache_key in _scoped_cache:
                    cached_data = _scoped_cache[cache_key]
            else:
                with Rename._lock:
                    if cache_key in Rename._dir_cache:
                        cached_data = Rename._dir_cache[cache_key]

            if is_weak and not cus_tmdb_id and not cus_name:
                if cached_data:
                    c = cached_data
                    cached_is_movie = c.get('is_movie', False)

                    if not cached_is_movie:
                        logger.info(f"[目录缓存] 命中电视剧缓存: {c.get('name')}")
                        cus_tmdb_id, cus_name = c.get('tmdb_id'), c.get('name')
                        if is_anime is None:
                            is_anime = c.get('is_anime')
                        rtpath_name = cus_name
                        is_movie = False
                        from_ai_cache = True
                    else:
                        logger.info(f"[目录缓存] 缓存为电影，不使用缓存")
                else:
                    # 缓存没命中，必须手动溯源
                    final_search_name = ""

                    if path.parent != path.root:
                        pname = path.parent.name
                        pname_cleaned = remove_tag(pname).lower().strip()

                        # 判断父目录是否为季号
                        if is_season_name(pname_cleaned):
                            # 是季号，找爷爷
                            if path.parent.parent != path.root:
                                grandparent_name = path.parent.parent.name
                                final_search_name, year, _, _ = parse_filename(grandparent_name)
                                logger.info(f"[溯源] 识别到季目录 '{pname}'，使用祖父目录: {final_search_name}")
                            else:
                                logger.warning(f"[溯源] 无法向上回溯，被迫使用季号目录: {pname}")
                                final_search_name = pname
                        else:
                            # 不是季号，直接用父目录
                            final_search_name, year, _, _ = parse_filename(pname)
                            logger.info(f"[溯源] 使用父目录: {final_search_name}")

                    if final_search_name:
                        rtpath_name = final_search_name

                    if any(inv in rtpath_name.lower() for inv in INVALID_NAMES) and not cus_tmdb_id:
                        rtpath_name = ""
                        if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIX:
                            return

            if cus_name:
                rtpath_name = cus_name
                new_name, new_year, _, _ = parse_filename(cus_name)
                if new_year > 0:
                    rtpath_name, year = new_name, new_year

            if cus_tmdb_id:
                try:
                    name, info, is_anime, is_movie = self.search.get_info_by_tmdb_id(
                        tmdb_id=int(cus_tmdb_id),
                        is_movie_hint=is_movie
                    )
                    # ID 获取成功，信任它并写入缓存
                    if not is_movie and path.parent != path.root:
                        new_cache_data = {
                            'tmdb_id': str(info['id']),
                            'name': name,
                            'is_anime': is_anime,
                            'is_movie': is_movie
                        }
                        if _scoped_cache is not None:
                            _scoped_cache[cache_key] = new_cache_data
                        else:
                            with Rename._lock:
                                Rename._dir_cache[cache_key] = new_cache_data
                except Exception as e:
                    # ID 获取失败 (404等)，视为脏数据，重置 ID 并回退到下方搜索逻辑
                    logger.warning(f"[ID失效] ID {cus_tmdb_id} 查询失败: {e}，判定为脏数据，将使用文件名搜索...")
                    cus_tmdb_id = None

            if not cus_tmdb_id:
                search_candidates = []
                search_candidates.append(rtpath_name)

                try:
                    name_no_brackets = re.sub(r'[\[【\(（].*?[\]】\)）]', '', rtpath_name)
                    name_no_brackets = re.sub(r'\s+', ' ', name_no_brackets).strip()

                    if name_no_brackets and name_no_brackets != rtpath_name:
                        if len(name_no_brackets) >= 2:
                            search_candidates.append(name_no_brackets)
                            logger.debug(f"[搜索增强] 添加去括号关键词: '{name_no_brackets}'")
                except Exception:
                    pass
                if ' - ' in rtpath_name:
                    parts = rtpath_name.split(' - ')
                    for part in parts:
                        clean_part = part.replace('.', ' ').strip()
                        if clean_part and clean_part not in search_candidates:
                            search_candidates.append(clean_part)
                    if rtpath_name not in search_candidates:
                        search_candidates.append(rtpath_name)
                else:
                    search_candidates.append(rtpath_name)

                task_res = None
                last_error = "未搜索到结果"

                for idx, candidate_name in enumerate(search_candidates):
                    time.sleep(0.01)
                    if idx > 0:
                        logger.info(f"[搜索重试] 尝试第 {idx + 1} 个关键词: {candidate_name}")

                    res = self.check_task_type(_uuid, candidate_name, year, path, is_anime, is_movie)

                    if not isinstance(res, str):
                        task_res = res
                        logger.info(f"[搜索成功] 命中关键词: {candidate_name}")
                        break
                    else:
                        last_error = res
                        logger.debug(f"[搜索尝试] 关键词 '{candidate_name}' 无结果")

                if not task_res:
                    if from_ai_cache:
                        logger.warning(f"[AI缓存] 名称 '{rtpath_name}' 无法在TMDB搜索，但来自AI缓存，跳过AI重试")
                        return self.error_reply(_uuid, last_error, path)

                    if effective_use_ai and not ai_attempted:
                        hint_for_ai = rtpath_name
                        if is_weak and rtpath_name not in path.name:
                            hint_for_ai = f"{rtpath_name} {path.name}"
                        elif is_weak:
                            hint_for_ai = path.name
                        ai_res = self._attempt_ai_recovery(
                            path, _uuid, is_anime, cus_offset, cus_season_id,
                            effective_use_ai, check_skip_dir=True,
                            hint_name=hint_for_ai
                        )
                        if ai_res:
                            return ai_res
                    return self.error_reply(_uuid, last_error, path)

                name, info, is_anime, is_movie = task_res

            if info and 'id' in info and path.parent != path.root and not is_movie:
                new_cache_data = {
                    'tmdb_id': str(info['id']),
                    'name': name,
                    'is_anime': is_anime,
                    'is_movie': is_movie
                }
                if _scoped_cache is not None:
                    _scoped_cache[cache_key] = new_cache_data
                else:
                    with Rename._lock:
                        Rename._dir_cache[cache_key] = new_cache_data

            work_path = None
            season_id = 0
            target_file = None
            if is_movie:
                _WORK_PATH = self.ANIME_MOVIE_PATH if is_anime else self.MOVIE_PATH
                if not name:
                    return self.error_reply(_uuid, "未找到电影信息", path)

                first_year = info.get('release_date', '0000').split('-')[0]
                movie_rename_format = cm.get_config('movie_rename_format')

                use_template_ok = False
                if movie_rename_format:
                    ctx = get_render_context(path, info)
                    rel_path = render_path_template(movie_rename_format, ctx)
                    if rel_path:
                        path_str = str(rel_path).replace("\\", "/")
                        path_str = re.sub(r'\.{2,}', '.', path_str)
                        rel_path = Path(path_str)

                        if enable_secondary:
                            category = self._get_category_folder(info, True, is_anime)
                            target_file = _WORK_PATH / category / rel_path
                        else:
                            target_file = _WORK_PATH / rel_path

                        self.R[path] = target_file
                        logger.info(f'[自定义格式] 目标路径: {target_file}')
                        use_template_ok = True
                        work_path = target_file.parent
                        season_id = 0

                if not use_template_ok:
                    if enable_secondary:
                        category = self._get_category_folder(info, True, is_anime)
                        work_path = _WORK_PATH / category / f'{name} ({first_year})'
                    else:
                        work_path = _WORK_PATH / f'{name} ({first_year})'

                    work_path.mkdir(parents=True, exist_ok=True)
                    target_file = work_path / f'{name} - {path.name}'
                    self.R[path] = target_file
                    season_id = 0

                if enable_scrape:
                    self.scraper.scrape_movie(target_file, info)

            else:
                if is_anime:
                    _WORK_PATH = self.ANIME_PATH
                else:
                    _WORK_PATH = self.BANGUMI_PATH

                if not name:
                    return self.error_reply(_uuid, "未找到剧集信息", path)

                if enable_scrape:
                    info = self.search.fill_season_info(info)
                first_year = info.get('first_air_date', '0000').split('-')[0]

                root_folder_name = f'{name} ({first_year})'

                tv_rename_format = cm.get_config('tv_rename_format')
                if tv_rename_format:
                    try:
                        dummy_ctx = get_render_context(path, info, 1, 1)
                        dummy_res = render_path_template(tv_rename_format, dummy_ctx)
                        if dummy_res and len(dummy_res.parts) > 1:
                            root_folder_name = dummy_res.parts[0]
                            logger.debug(f"[路径计算] 根据模板识别到剧集根目录: {root_folder_name}")
                    except Exception as e:
                        logger.warning(f"[路径计算] 模板预计算失败，将使用默认目录名: {e}")

                if enable_secondary:
                    category = self._get_category_folder(info, False, is_anime)
                    work_path = _WORK_PATH / category / root_folder_name
                else:
                    work_path = _WORK_PATH / root_folder_name

                try:
                    work_path.mkdir(parents=True, exist_ok=True)
                except:
                    pass

                season_id = self.get_season_id(info, work_path, path, [{'title': name}])
                if cus_season_id:
                    season_id = int(cus_season_id)

                if enable_scrape:
                    self.scraper.scrape_tv_show(work_path, info)

                self._process_traditional(
                    path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                )

            final_tmdb_id = cus_tmdb_id if cus_tmdb_id else (str(info['id']) if info else None)
            display_path = str(list(self.R.values())[0]) if self.R else str(work_path)

            trans_result = Trans(self.R, _uuid).trans_file()

            if trans_result is True:
                with Rename._lock:
                    for k in list(self.R.keys()):
                        Rename._processed_paths.add(str(k.absolute()))

                self.R = {}

            if isinstance(trans_result, str):
                return self.error_reply(_uuid, trans_result, path)

            task_data = {
                "path": str(path), "target_path": display_path, "is_anime": is_anime, "is_movie": is_movie,
                "name": name, "season_id": season_id, "uuid": str(_uuid), "error": None, "use_ai": effective_use_ai,
                "tmdb_id": final_tmdb_id
            }
            with open(TASK_PATH / f"{_uuid}.json", "w", encoding="UTF-8") as f:
                json.dump(task_data, f, indent=4, ensure_ascii=False)
            return True
        finally:
            with Rename._lock:
                Rename._processing_paths.discard(abs_str)

    def _process_traditional(
            self, path: Path, rtpath_name: str, work_path: Path, season_id: int, info: Optional[Dict] = None,
            cus_offset: Optional[int] = None, cus_season_id: Optional[int] = None,
    ):
        time.sleep(0.01)
        if path.is_file():
            logger.info(f"[处理任务] 开始对 [单文件] {path.name}处理")
            self.process_sub(
                rtpath_name,
                None,
                path,
                work_path,
                season_id,
                info,
                cus_offset,
                cus_season_id,
            )
        else:
            logger.info(f"[处理任务] 开始对 [文件夹] {path.name}处理")
            repeat = find_unique_parts_in_videos(path)
            for item_path in path.iterdir():
                if item_path.is_dir():
                    repeat_2 = find_unique_parts_in_videos(item_path)
                    for sub_item in item_path.iterdir():
                        self.process_sub(
                            rtpath_name,
                            repeat_2,
                            sub_item,
                            work_path,
                            season_id,
                            info,
                            cus_offset,
                            cus_season_id,
                        )
                else:
                    self.process_sub(
                        rtpath_name,
                        repeat,
                        item_path,
                        work_path,
                        season_id,
                        info,
                        cus_offset,
                        cus_season_id,
                    )

    def error_reply(
            self,
            _uuid: str,
            error: str,
            path: Path,
            is_anime: Optional[bool] = None,
            is_movie: Optional[bool] = None,
            name: Optional[str] = None,
            season_id: Optional[int] = None,
            use_ai: Optional[bool] = None,
    ):
        logger.error(f"[任务失败] {error}")
        task_path = TASK_PATH / f'{_uuid}.json'
        task_data = {
            'path': str(path),
            'is_anime': is_anime,
            'is_movie': is_movie,
            'name': name,
            'season_id': season_id,
            'uuid': str(_uuid),
            'error': error,
            'use_ai': use_ai,
        }
        with open(task_path, 'w', encoding='UTF-8') as file:
            json.dump(task_data, file, indent=4, ensure_ascii=False)
        return error
