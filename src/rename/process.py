import re
import json
import uuid
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Union, Optional

from jikanpy import Jikan

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
    remove_episode,
    extract_base_num,
    match_and_extract,
    remove_similar_part,
    find_unique_parts_in_videos,
    clean_noise,
)

jikan = Jikan()


class Rename:
    _dir_cache = {}
    _ai_mapping_cache = {}
    _processed_paths = set()
    _processing_paths = set()
    _tmdb_search_cache: Dict[tuple, tuple] = {}

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

    def _get_category_folder(self, info: Dict, is_movie: bool, is_anime: bool) -> str:
        """
        根据元数据判断二级分类文件夹名称
        规则：
        - 电影：动画电影、华语电影、外语电影
        - 剧集：儿童、纪录片、综艺、国漫、日漫、国产剧、日韩剧、欧美剧、未分类
        """
        if not info:
            return "未分类"

        # 1. 提取基础数据
        genres = info.get('genres', [])
        genre_ids = [g.get('id') for g in genres]
        original_language = info.get('original_language', '').lower()

        # 提取国家代码 (电影和剧集的字段不同)
        countries = []
        if 'origin_country' in info:
            # 剧集通常是 ['US', 'GB']
            countries = info.get('origin_country', [])
        elif 'production_countries' in info:
            # 电影通常是 [{'iso_3166_1': 'US', ...}]
            countries = [c.get('iso_3166_1') for c in info.get('production_countries', [])]

        # 辅助判断函数
        def is_chinese_region():
            return original_language in ['zh', 'cn', 'bo', 'za'] or \
                any(c in ['CN', 'HK', 'TW'] for c in countries)

        def is_jap_kor_region():
            return original_language in ['ja', 'ko'] or \
                any(c in ['JP', 'KR'] for c in countries)

        # ================= 电影分类逻辑 =================
        if is_movie:
            # 1. 动画电影 (Genre ID 16 = Animation)
            if 16 in genre_ids or is_anime:
                return "动画电影"

            # 2. 华语电影
            if is_chinese_region():
                return "华语电影"

            # 3. 外语电影 (其余所有)
            return "外语电影"

        # ================= 剧集分类逻辑 =================

        # 1. 儿童 (Genre ID 10762 = Kids)
        if 10762 in genre_ids:
            return "儿童"

        # 2. 纪录片 (Genre ID 99 = Documentary)
        if 99 in genre_ids:
            return "纪录片"

        # 3. 综艺 (Genre ID 10764 = Reality, 10767 = Talk)
        if 10764 in genre_ids or 10767 in genre_ids:
            return "综艺"

        # 4. 动漫 (国漫/日漫)
        # 注意：这里假设所有非国漫的动漫都归入“日漫”或者你需要更细分。
        # 按照你的需求列表，只有“国漫”和“日漫”。
        if is_anime or 16 in genre_ids:
            if is_chinese_region():
                return "国漫"
            # 默认其他动漫都归为日漫（包含欧美动漫，除非你想把欧美动漫归入欧美剧或者单开）
            # 如果需要严格区分日本动漫，可以加 is_jap_kor_region() 判断
            return "日漫"

            # 5. 真人剧集 (国产/日韩/欧美)
        if is_chinese_region():
            return "国产剧"

        if is_jap_kor_region():
            return "日韩剧"

        # 默认为欧美剧 (包括美国、英国、欧洲、拉美等)
        return "欧美剧"

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

        # 提前提取文件名中的季号 (例如 "S02")
        int_rtpath_name = extract_season(path_name)
        logger.info(f'[处理任务] 提取标题季号:{int_rtpath_name}')

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

        _idata = match_and_extract(item_name)
        if _idata:
            if cus_season_id is None:
                season_id = int(_idata[0])
            ep = _idata[1]

            if cus_offset is not None and cus_offset != 0:
                ep = ep + cus_offset

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
            return

        p = r'[a-zA-Z0-9]'
        for ex in EXTRA_TAG:
            if re.search(rf'(?<!{p}){ex.lower()}(?!{p})', n_item_name_l):
                t = work_path / 'extra'
                self.R[item_path] = t / item_name
                logger.info(f'[处理任务] 处理完成{item_name}')
                return

        for s0 in S0_TAG:
            if re.search(rf'(?<!{p}){s0.lower()}(?!{p})', item_name_l) or \
                    re.search(rf'(?<!{p}){s0.lower()}[\d]{{1,3}}(?!{p})', item_name_l):
                t = work_path / 'Season0'
                self.R[item_path] = t / item_name
                logger.info(f'[处理任务] 处理完成{item_name}')
                return

        _item_name = remove_code(remove_season(item_name_l))
        epp = extract_base_num(_item_name)
        if epp is None:
            ep = extract_number(_item_name)
        else:
            ep = int(epp)

        if ep is None:
            if _item_name.isdigit():
                ep = int(_item_name)
            else:
                ep = 0
        else:
            ep = int(ep)

        if cus_offset is not None and cus_offset != 0:
            ep = ep + cus_offset

        t = work_path / f'Season{season_id}'

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
    ):
        # 构造上下文配置字典，用于在递归中传递
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
            return self._process(path, _uuid=_tuuid, **initial_context)

        # 栈结构：(Path, UUID, Context_Dict)
        stack = [(path, _tuuid, initial_context)]
        final_result = True

        while stack:
            curr_path, curr_uuid, ctx = stack.pop()

            if curr_path.name.startswith(('.', '@', '$RECYCLE')):
                continue

            # --- 处理文件 ---
            if curr_path.is_file():
                if curr_path.suffix.lower() in VIDEO_SUFFIX:
                    # 使用当前栈带来的上下文进行处理
                    res = self._process(curr_path, _uuid=curr_uuid, **ctx)
                    if isinstance(res, str):
                        final_result = res
                continue

            # --- 处理目录 ---
            if curr_path.is_dir():
                has_video_files = False
                for sub_path in curr_path.iterdir():
                    if sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX:
                        has_video_files = True
                        break

                dir_processed_successfully = False

                # 准备子文件的上下文（默认复制当前上下文）
                child_context = ctx.copy()

                # 只有当目录内有视频时，才尝试对目录本身进行整体识别
                if has_video_files:
                    use_uuid = curr_uuid if curr_uuid else str(uuid.uuid4())

                    # 尝试处理整个目录
                    res = self._process(curr_path, _uuid=use_uuid, **ctx)

                    if res is True:
                        dir_processed_successfully = True
                    elif res == "SKIP_DIR_IS_MOVIE":
                        # 目录被判定为电影（合集）
                        logger.info(
                            f"[合集识别] 目录 '{curr_path.name}' 被判定为电影/合集。放弃目录重命名，转为以 [文件名] 为准分别处理子文件。")
                        dir_processed_successfully = False

                        # 强制更新子文件上下文
                        # 1. 标记为电影
                        child_context['is_movie'] = True
                        child_context['is_anime'] = False
                        # 2. 彻底清除父级带来的名称和ID，迫使子文件必须使用自己的文件名进行搜索
                        child_context['cus_name'] = None
                        child_context['cus_tmdb_id'] = None
                        # 3. 清除AI尝试标记，允许子文件在必要时自己调用AI（针对它自己的文件名）
                        child_context['ai_attempted'] = False

                        # 清理目录的错误任务记录
                        error_task_file = TASK_PATH / f"{use_uuid}.json"
                        if error_task_file.exists():
                            try:
                                error_task_file.unlink()
                            except:
                                pass

                    else:
                        # 普通失败（如剧集识别失败），也进入子文件处理
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
                                # 将子文件加入栈，携带修正后的 child_context
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
        """
        判断任务类型（电影/电视剧）
        注意：此方法不处理缓存，只负责类型判断和 TMDB 搜索
        """
        norm_name = rtpath_name.strip().lower()

        # 如果上下文强制指定为电影
        if is_movie:
            logger.info(f"[处理任务] 上下文强制指定为电影类型，使用文件名 '{rtpath_name}' 进行搜索...")

            cache_key = (norm_name, year, "movie")
            if cache_key in Rename._tmdb_search_cache:
                s2_name, s2_info = Rename._tmdb_search_cache[cache_key]
            else:
                s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)

                # 年份校验
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
                    logger.info(f"[搜索重试] 去除年份后重试搜索: {rtpath_name}")
                    s2_name, s2_info = self.search.get_movie_info(rtpath_name, 0)

                if s2_name:
                    Rename._tmdb_search_cache[cache_key] = (s2_name, s2_info)

            if not s2_name:
                return f'[TMDB] 未搜索到电影信息 (强制Movie模式), 文件名: {rtpath_name}'

            return s2_name, s2_info, (is_anime or False), True

        # ------- 自动判断模式 -------
        pos = 0
        logger.info('[处理任务] 未传入任务类型，开始判断该文件是否为电影！')

        # 1. 搜索电视剧信息
        tv_cache_key = (norm_name, year, "tv")
        if tv_cache_key in Rename._tmdb_search_cache:
            s1_name, s1_info = Rename._tmdb_search_cache[tv_cache_key]
        else:
            s1_name, s1_info = self.search.get_tv_info(rtpath_name, year)
            if not s1_name and year != 0:
                s1_name, s1_info = self.search.get_tv_info(rtpath_name, 0)
            if s1_name:
                Rename._tmdb_search_cache[tv_cache_key] = (s1_name, s1_info)

        if s1_name:
            logger.info(f'[处理任务] 搜索到的电视剧名称: {s1_name}')

        # 2. 搜索电影信息
        mv_cache_key = (norm_name, year, "movie")
        if mv_cache_key in Rename._tmdb_search_cache:
            s2_name, s2_info = Rename._tmdb_search_cache[mv_cache_key]
        else:
            s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)
            if not s2_name and year != 0:
                s2_name, s2_info = self.search.get_movie_info(rtpath_name, 0)
            if s2_name:
                Rename._tmdb_search_cache[mv_cache_key] = (s2_name, s2_info)

        if s2_name:
            logger.info(f'[处理任务] 搜索到的电影名称: {s2_name}')

        # 3. 快速判断：检测到 SxxExx 格式直接判定为电视剧
        filename = path.name
        has_episode_pattern = bool(re.search(r"S\d{1,2}E\d{1,3}", filename, re.IGNORECASE))

        if has_episode_pattern:
            logger.info('[处理任务] 检测到 SxxExx 格式，直接判定为电视剧')
            is_movie = False
            info = s1_info
            name = s1_name
            if not name or not info:
                return f'[TMDB] 未搜索到电视剧信息, 跳过{rtpath_name}'

            if is_anime is None:
                is_anime = any(g['name'].lower() in ['animation', 'anime']
                               for g in info.get('genres', []))

            return name, info, is_anime, is_movie

        # 4. 提取季号用于评分
        season_id = extract_season(rtpath_name)

        # 5. 评分逻辑
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

        # 检查父目录是否有季号标识
        for parent in path.parents:
            pname = parent.name.lower()
            if re.search(r"season\s*\d+", pname) or re.match(r"s\d{1,2}", pname):
                pos += 1
                break

        # 检查目录内相似文件
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

        # 季号判断
        if season_id == -1:
            pos -= 0.6
            if path.is_file():
                pos -= 0.5
        else:
            pos += 0.6
            if path.is_file():
                pos += 0.5

        # 目录文件数量判断
        if path.is_dir():
            if len([i for i in path.iterdir() if i.is_file()]) > 6:
                pos += 0.4
            else:
                pos -= 0.4

        # 6. 最终判定
        if pos > 0 or (is_movie is not None and not is_movie):
            logger.info(f'[处理任务] 该文件可能为电视剧！(得分: {pos})')
            is_movie = False
            info = s1_info
            name = s1_name
            if not name or not info:
                return f'[TMDB] 未搜索到电视剧信息, 跳过{rtpath_name}'

            if is_anime is None:
                is_anime = any(g['name'].lower() in ['animation', 'anime']
                               for g in info.get('genres', []))
        else:
            logger.info(f'[处理任务] 该文件可能为电影！(得分: {pos})')
            is_movie = True
            info = s2_info
            name = s2_name
            if not name or not info:
                return f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}'

            if is_anime is None:
                is_anime = any(g['name'].lower() in ['animation', 'anime']
                               for g in info.get('genres', []))

        return name, info, is_anime, is_movie

    def _attempt_ai_recovery(
            self,
            path: Path,
            _uuid: str,
            is_anime: Optional[bool],
            cus_offset: Optional[int],
            cus_season_id: Optional[int],
            use_ai: bool,
            check_skip_dir: bool = False
    ):
        """尝试使用AI进行元数据分析补救"""
        if self.ai_processor.ai_client.is_available():
            try:
                # 获取路径的最后3级
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

            ai_meta = self.ai_processor.analyze_search_metadata(path)

            if ai_meta:
                ai_name = ai_meta.get('name')
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
                ai_tmdb_id = ai_meta.get('tmdb_id')
                ai_is_movie = ai_meta.get('is_movie', False)
                logger.info(
                    f"[AI处理] AI推断成功: Name={ai_name}, TMDB_ID={ai_tmdb_id}, Movie={ai_is_movie}"
                )
                cache_key = str(path.parent.absolute())
                Rename._dir_cache[cache_key] = {
                    'tmdb_id': str(ai_tmdb_id) if ai_tmdb_id else None,
                    'name': ai_name,
                    'is_anime': is_anime,  # 继承当前的动漫标记
                    'is_movie': ai_is_movie
                }
                logger.info(f"[缓存写入] AI元数据推断结果已缓存至: {path.parent.name}")
                if check_skip_dir and path.is_dir() and ai_is_movie:
                    return "SKIP_DIR_IS_MOVIE"

                abs_str = str(path.resolve())
                Rename._processing_paths.discard(abs_str)

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
    ):
        if not _uuid:
            _uuid = str(uuid.uuid4())

        abs_str = str(path.resolve())
        if abs_str in Rename._processed_paths:
            logger.info(f"[跳过] 文件已在之前的任务中处理完毕: {path.name}")
            return True
        if abs_str in Rename._processing_paths:
            logger.info(f"[跳过] 文件当前正在处理中: {path.name}")
            return True

        Rename._processing_paths.add(abs_str)
        try:
            self.scraped_seasons = set()

            if not self.search.TMDB_KEY:
                return self.error_reply(_uuid, '无TMDB Key', path)

            enable_scrape = cm.get_config('scrape_metadata')
            global_use_ai = bool(cm.get_config('use_ai'))
            effective_use_ai = use_ai if use_ai is not None else global_use_ai
            enable_secondary = cm.get_config('secondary_classification')

            logger.info(f'[处理任务] 开始处理{path.name}')

            # --- 1. 基础清洗 ---
            rtpath_name = remove_tag(path.name)
            if not rtpath_name:
                rtpath_name = remove_tag(path.name, True)
            rtpath_name, year = divide_by_year(rtpath_name)
            rtpath_name = remove_season(remove_episode(rtpath_name)).strip('!')

            # --- 2. 检查是否为弱文件名 ---
            INVALID_NAMES = ['未知', 'unknown', 'none', 'null', 'tba', '未识别到官方名称', '待定']
            name_check = re.sub(r'[\W_]+', '', rtpath_name.replace(path.suffix, "") if path.suffix else rtpath_name)
            is_weak = (not name_check) or (name_check.isdigit()) or (len(name_check) < 2) or (
                    rtpath_name.lower() in INVALID_NAMES)

            if is_weak:
                logger.info(f"[智能判断] 文件名 '{path.name}' 判定为弱文件名，强制作为剧集(TV)处理。")
                is_movie = False

            # --- 3. 目录缓存逻辑（仅用于电视剧） ---
            cache_key = str(path.parent.absolute())

            # 先快速判断：如果文件名有 SxxExx 格式，预判为电视剧
            filename = path.name
            has_episode_pattern = bool(re.search(r"S\d{1,2}E\d{1,3}", filename, re.IGNORECASE))

            # 如果是弱文件名 或 明确的剧集格式，尝试使用目录缓存
            if (is_weak or has_episode_pattern) and not cus_tmdb_id and not cus_name:
                if cache_key in Rename._dir_cache:
                    c = Rename._dir_cache[cache_key]
                    cached_is_movie = c.get('is_movie', False)

                    # 只有缓存的也是电视剧，才使用缓存
                    if not cached_is_movie:
                        logger.info(f"[目录缓存] 命中电视剧缓存: {c.get('name')}")
                        cus_tmdb_id, cus_name = c.get('tmdb_id'), c.get('name')
                        if is_anime is None:
                            is_anime = c.get('is_anime')
                        rtpath_name = cus_name
                        is_movie = False  # 强制为电视剧
                    else:
                        logger.info(f"[目录缓存] 缓存为电影，不使用缓存")
                else:
                    # 没有缓存，进行溯源
                    if is_weak and path.parent != path.root:
                        pname = path.parent.name
                        if re.match(r'^(season|series|s)\s*\d*$',
                                    remove_tag(pname).lower().strip()) and path.parent.parent != path.root:
                            rtpath_name, year = divide_by_year(remove_tag(path.parent.parent.name))
                            logger.info(f"[溯源] 使用祖父目录: {rtpath_name}")
                        else:
                            rtpath_name, year = divide_by_year(remove_tag(pname))
                            logger.info(f"[溯源] 使用父目录: {rtpath_name}")

            if any(inv in rtpath_name.lower() for inv in INVALID_NAMES) and not cus_tmdb_id:
                rtpath_name = ""
            if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIX:
                return

            if cus_name:
                rtpath_name = cus_name
                new_name, new_year = divide_by_year(rtpath_name)
                if new_year > 0:
                    rtpath_name, year = new_name, new_year

            # --- 4. 搜索 TMDB ---
            if cus_tmdb_id:
                try:
                    name, info, is_anime, is_movie = self.search.get_info_by_tmdb_id(
                        tmdb_id=int(cus_tmdb_id),
                        is_movie_hint=is_movie
                    )
                    # 只有电视剧才写入目录缓存
                    if not is_movie and path.parent != path.root:
                        Rename._dir_cache[cache_key] = {
                            'tmdb_id': str(info['id']),
                            'name': name,
                            'is_anime': is_anime,
                            'is_movie': is_movie
                        }
                        logger.info(f"[目录缓存] 已缓存电视剧: {name}")
                except Exception as e:
                    return self.error_reply(_uuid, str(e), path)
            else:
                task_res = self.check_task_type(_uuid, rtpath_name, year, path, is_anime, is_movie)
                if isinstance(task_res, str):
                    if effective_use_ai and not ai_attempted:
                        ai_res = self._attempt_ai_recovery(
                            path, _uuid, is_anime, cus_offset, cus_season_id,
                            effective_use_ai, check_skip_dir=True
                        )
                        if ai_res:
                            return ai_res
                    return self.error_reply(_uuid, task_res, path)

                name, info, is_anime, is_movie = task_res

                # 只有电视剧才写入目录缓存
                if info and 'id' in info and path.parent != path.root and not is_movie:
                    Rename._dir_cache[cache_key] = {
                        'tmdb_id': str(info['id']),
                        'name': name,
                        'is_anime': is_anime,
                        'is_movie': is_movie
                    }
                    logger.info(f"[目录缓存] 已缓存电视剧: {name}")

            # --- 5. 后续处理逻辑保持不变 ---
            ai_available = self.ai_processor.ai_client.is_available()

            if is_movie:
                # 电影处理逻辑（不使用目录缓存）
                if not name:
                    return self.error_reply(_uuid, "未找到电影信息", path)

                _WORK_PATH = self.ANIME_MOVIE_PATH if is_anime else self.MOVIE_PATH
                first_year = info.get('release_date', '0000').split('-')[0]

                if enable_secondary:
                    category = self._get_category_folder(info, True, is_anime)
                    work_path = _WORK_PATH / category / f'{name} ({first_year})'
                else:
                    work_path = _WORK_PATH / f'{name} ({first_year})'

                work_path.mkdir(parents=True, exist_ok=True)
                target_file = work_path / f'{name} - {path.name}'
                self.R[path] = target_file

                if enable_scrape:
                    self.scraper.scrape_movie(target_file, info)
                season_id = 0

            else:
                # 电视剧处理逻辑（使用目录缓存）
                if is_anime:
                    _WORK_PATH = self.ANIME_PATH
                else:
                    _WORK_PATH = self.BANGUMI_PATH

                if not name:
                    return self.error_reply(_uuid, "未找到剧集信息", path)

                if enable_scrape:
                    info = self.search.fill_season_info(info)
                first_year = info.get('first_air_date', '0000').split('-')[0]

                if enable_secondary:
                    category = self._get_category_folder(info, False, is_anime)
                    work_path = _WORK_PATH / category / f'{name} ({first_year})'
                else:
                    work_path = _WORK_PATH / f'{name} ({first_year})'

                season_id = self.get_season_id(info, work_path, path, [{'title': name}])
                if cus_season_id:
                    season_id = int(cus_season_id)

                use_ai_logic = effective_use_ai and ai_available and (is_anime or is_weak)

                if use_ai_logic:
                    # AI 处理逻辑...
                    tv_info = self.search.fill_season_info(info)
                    ai_cache_key = str(path.parent.absolute())

                    if ai_cache_key in Rename._ai_mapping_cache:
                        logger.info(f"[缓存命中] 使用目录AI映射: {ai_cache_key}")
                        ai_result = Rename._ai_mapping_cache[ai_cache_key]
                    else:
                        logger.info("[处理任务] 启用AI批量分析目录...")
                        ai_result: AIAnalysisResult | None = (
                            self.ai_processor.analyze_anime_files(path, tv_info)
                        )
                        if ai_result:
                            Rename._ai_mapping_cache[ai_cache_key] = ai_result

                    should_use = False
                    if ai_result:
                        conf = cm.get_config("ai_confidence_threshold")
                        if conf == "Low" or \
                                (conf == "Medium" and ai_result.confidence in ["Medium", "High"]) or \
                                (conf == "High" and ai_result.confidence == "High"):
                            should_use = True

                    if should_use:
                        logger.info("[AI处理] 应用批量文件映射...")
                        self.R = self.ai_processor.apply_ai_mapping(ai_result, path, work_path)

                        if not self.R:
                            logger.warning("[AI处理] 映射为空，回退传统模式")
                            if enable_scrape:
                                self.scraper.scrape_tv_show(work_path, info)
                            self._process_traditional(
                                path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                            )
                        else:
                            if enable_scrape:
                                self.scraper.scrape_tv_show(work_path, info)
                                logger.info(f"[批量刮削] 正在处理 {len(self.R)} 个文件...")
                                for src, dest in self.R.items():
                                    Rename._processed_paths.add(str(src.absolute()))
                                    try:
                                        match = re.search(r'S(\d+)E(\d+)', dest.name, re.IGNORECASE)
                                        if match:
                                            self.scraper.scrape_episode(
                                                dest, info, int(match.group(1)), int(match.group(2))
                                            )
                                    except Exception as e:
                                        logger.debug(f"刮削单集失败: {e}")
                    else:
                        logger.info("[AI处理] 置信度不足，回退传统模式")
                        if enable_scrape:
                            self.scraper.scrape_tv_show(work_path, info)
                        self._process_traditional(
                            path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                        )
                else:
                    # 传统模式
                    if enable_scrape:
                        self.scraper.scrape_tv_show(work_path, info)
                    self._process_traditional(
                        path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                    )

            # --- 6. 提交移动任务 ---
            final_tmdb_id = cus_tmdb_id if cus_tmdb_id else (str(info['id']) if info else None)
            display_path = str(list(self.R.values())[0]) if self.R else str(work_path)

            trans_result = Trans(self.R, _uuid).trans_file()

            if trans_result is True:
                for k in self.R.keys():
                    Rename._processed_paths.add(str(k.absolute()))

            self.R = {}

            if isinstance(trans_result, str):
                return self.error_reply(_uuid, trans_result, path)

            # 写入任务记录
            task_data = {
                "path": str(path), "target_path": display_path, "is_anime": is_anime, "is_movie": is_movie,
                "name": name, "season_id": season_id, "uuid": str(_uuid), "error": None, "use_ai": effective_use_ai,
                "tmdb_id": final_tmdb_id
            }
            with open(TASK_PATH / f"{_uuid}.json", "w", encoding="UTF-8") as f:
                json.dump(task_data, f, indent=4, ensure_ascii=False)
            return True
        finally:
            Rename._processing_paths.discard(abs_str)

    def _process_traditional(
            self, path: Path, rtpath_name: str, work_path: Path, season_id: int, info: Optional[Dict] = None,
            cus_offset: Optional[int] = None, cus_season_id: Optional[int] = None,
    ):
        """传统处理方式"""
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
