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
)

jikan = Jikan()


class Rename:
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
                        if title['type'] in ['Default', 'Synonym', 'English', 'French']:
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
                season_id = _idata[0]
            ep = _idata[1]

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
    ):
        if path.is_file():
            return self._process(
                path, _is_anime, _is_movie, _tuuid, cus_name,
                cus_season_id, cus_tmdb_id, cus_offset, use_ai
            )

        if path.is_dir():
            has_video_files = False
            for sub_path in path.iterdir():
                if sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX:
                    has_video_files = True
                    break

            if has_video_files:
                current_uuid = _tuuid if _tuuid else str(uuid.uuid4())
                result = self._process(
                    path, _is_anime, _is_movie, current_uuid, cus_name,
                    cus_season_id, cus_tmdb_id, cus_offset, use_ai
                )

                if result is True:
                    return True

                logger.warning(f"[降级处理] 目录 [{path.name}] 整体识别失败，转为尝试单独识别内部文件...")

                error_task_file = TASK_PATH / f"{current_uuid}.json"
                if error_task_file.exists():
                    try:
                        error_task_file.unlink()
                    except Exception:
                        pass

            all_success = True
            logger.info(f"[递归扫描] 进入目录: {path}")

            for sub_path in path.iterdir():
                if sub_path.name.startswith(('.', '@', '$RECYCLE')):
                    continue

                if sub_path.is_dir() or (sub_path.is_file() and sub_path.suffix.lower() in VIDEO_SUFFIX):
                    sub_result = self.process(
                        sub_path, _is_anime, _is_movie, None, cus_name,
                        cus_season_id, cus_tmdb_id, cus_offset, use_ai
                    )
                    if isinstance(sub_result, str):
                        all_success = sub_result

            return True if all_success is True else all_success

    def check_task_type(
            self,
            _uuid: str,
            rtpath_name: str,
            year: int,
            path: Path,
            is_anime: Optional[bool] = None,
            is_movie: Optional[bool] = None,
    ) -> Union[Tuple[str, Dict, bool, bool], str]:
        # ... (check_task_type 逻辑保持原样，主要用于自动识别) ...
        # 注意：如果有 cus_tmdb_id，其实这个函数在 _process 里会被跳过或覆盖结果

        season_id = 1
        pos = 0
        logger.info('[处理任务] 未传入任务类型，开始判断该文件是否为电影！')

        s1_name, s1_info = self.search.get_tv_info(rtpath_name, year)
        logger.info(f'[处理任务] 搜索到的电视剧名称: {s1_name}')
        if not s1_name and year != 0:
            s1_name, s1_info = self.search.get_tv_info(rtpath_name, 0)
            logger.info(f'[处理任务] 未搜索到结果, 删除year后重试: {s1_name}')

        s2_name, s2_info = self.search.get_movie_info(rtpath_name, year)
        logger.info(f'[处理任务] 搜索到的电影名称: {s2_name}')

        if not s2_name and year != 0:
            s2_name, s2_info = self.search.get_movie_info(
                rtpath_name,
                year,
            )
            logger.info(f'[处理任务] 未搜索到结果, 删除year后重试: {s2_name}')

        season_id = extract_season(rtpath_name)

        if s1_name:
            pos += 1
        elif s2_name:
            pos -= 1

        filename = path.name
        if re.search(r"S\d{1,2}E\d{1,3}", filename, re.IGNORECASE):
            pos += 2

        for parent in path.parents:
            pname = parent.name.lower()
            if re.search(r"season\s*\d+", pname) or re.match(r"s\d{1,2}", pname):
                pos += 1
                break

        try:
            dir_to_check = path.parent if path.is_file() else path
            if dir_to_check.is_dir():
                video_files = [p for p in dir_to_check.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIX]
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
                            if not base_i or not base_j: continue
                            ratio = SequenceMatcher(None, base_i, base_j).ratio()
                            if 0.8 < ratio < 0.999: similar_pairs += 1
                    if similar_pairs >= 3:
                        pos += 1
        except Exception as e:
            logger.warning(f"[处理任务] 检查相似视频文件时出错: {e}")

        if season_id == -1:
            pos -= 0.6
            if path.is_file(): pos -= 0.5
        else:
            pos += 0.6
            if path.is_file(): pos += 0.5
        if path.is_dir():
            if len([i for i in path.iterdir() if i.is_file()]) > 6:
                pos += 0.4
            else:
                pos -= 0.4

        if pos > 0 or (is_movie is not None and not is_movie):
            logger.info('[处理任务] 该文件可能为电视剧！')
            is_movie = False
            info = s1_info
            name = s1_name
            if not info: return f'[TMDB] 未搜索到电视剧信息, 跳过{rtpath_name}'
            if is_anime is None:
                for g in info['genres']:
                    if g['name'].lower() in ['animation', 'anime']:
                        is_anime = True
                        break
                else:
                    is_anime = False
        else:
            logger.info('[处理任务] 该文件可能为电影！')
            is_movie = True
            info = s2_info
            name = s2_name
            if not info: return self.error_reply(_uuid, f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}', path, is_anime)
            if is_anime is None:
                for g in info['genres']:
                    if g['name'].lower() in ['animation', 'anime']:
                        is_anime = True
                        break
                else:
                    is_anime = False
        return name, info, is_anime, is_movie

    def _process(
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
    ):
        if _tuuid:
            _uuid = _tuuid
        else:
            _uuid = str(uuid.uuid4())

        self.scraped_seasons = set()

        if not self.search.TMDB_KEY:
            return self.error_reply(_uuid, '你还没有配置TMDB的Key！...', path, _is_anime, _is_movie)

        enable_scrape = cm.get_config('scrape_metadata')

        logger.info(f'[处理任务] 开始处理{path.name}')

        year = 0
        rtpath_name = remove_tag(path.name)
        if not rtpath_name: rtpath_name = remove_tag(path.name, True)
        path_atri = re.split(r'[\s-]+', rtpath_name)
        if len(path_atri) > 3: rtpath_name = ' '.join(path_atri)
        if rtpath_name.count('.') >= 3:
            rtpath_name = ' '.join(rtpath_name.split('.'))
            rtpath_name, year = divide_by_year(rtpath_name)
        rtpath_name = remove_season(rtpath_name)
        rtpath_name = remove_episode(rtpath_name)
        rtpath_name = rtpath_name.strip('!')

        if path.is_file() and path.suffix.lower() not in VIDEO_SUFFIX:
            return

        if cus_name: rtpath_name = cus_name

        if cus_tmdb_id:
            logger.info(f"[处理任务] 检测到指定TMDB ID: {cus_tmdb_id}，跳过搜索")
            try:
                # 这里完全交给 Search 来处理 TMDB
                name, info, is_anime, is_movie = self.search.get_info_by_tmdb_id(
                    tmdb_id=int(cus_tmdb_id),
                    is_movie_hint=_is_movie,  # 可以带上调用方的「倾向」
                )

                # 如果外部显式指定 is_anime / is_movie，就覆盖掉自动判断
                if _is_anime is not None:
                    is_anime = _is_anime
                if _is_movie is not None:
                    is_movie = _is_movie

                logger.info(f"[处理任务] 指定ID获取成功: {name} ({'电影' if is_movie else '剧集'})")

            except Exception as e:
                import traceback
                error_str = f'指定TMDB ID查询失败: {e}'
                logger.error(error_str)
                logger.error(traceback.format_exc())
                return self.error_reply(_uuid, error_str, path, _is_anime, _is_movie)
        else:
            # 正常自动识别流程
            task_type = self.check_task_type(_uuid, rtpath_name, year, path, _is_anime, _is_movie)
            if isinstance(task_type, str):
                return self.error_reply(_uuid, task_type, path, _is_anime, _is_movie)
            name, info, is_anime, is_movie = task_type
        # =======================================================

        global_use_ai = bool(cm.get_config('use_ai'))
        effective_use_ai = use_ai if use_ai is not None else global_use_ai
        ai_available = self.ai_processor.ai_client.is_available()
        if is_movie:
            if not name:
                return self.error_reply(_uuid, f'[TMDB] 未搜索到电影信息, 跳过{rtpath_name}', path, is_anime, is_movie)

            if is_anime:
                _WORK_PATH = self.ANIME_MOVIE_PATH
            else:
                _WORK_PATH = self.MOVIE_PATH

            first_data = info.get('release_date', '0000-00-00')
            first_year = first_data.split('-')[0]
            work_path = _WORK_PATH / f'{name} ({first_year})'
            work_path.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                target_file = work_path / f'{name} - {path.name}'
                self.R[path] = target_file
                if enable_scrape:
                    self.scraper.scrape_movie(target_file, info)
            else:
                for item_path in path.iterdir():
                    item_name = item_path.name
                    target_file = work_path / f'{name} - {item_name}'
                    self.R[item_path] = target_file
                    if enable_scrape and item_path.suffix.lower() in VIDEO_SUFFIX:
                        self.scraper.scrape_movie(target_file, info)
            season_id = 0
        else:
            if is_anime:
                if not name:
                    logger.info('[处理任务] TMDB未搜索到!转为MyAnimeList搜索！')
                    try:
                        search_result = jikan.search('anime', rtpath_name, page=1)
                        data = search_result['data'][0]  # 简化逻辑
                        titles = data['titles']
                        logger.info((f'[处理任务] MyAnimeList识别结果: {titles}'))
                    except:
                        titles = None
                else:
                    titles = None
                _WORK_PATH = self.ANIME_PATH
            else:
                titles = [{'type': 'Default', 'title': name}]
                _WORK_PATH = self.BANGUMI_PATH

            if not name:
                return self.error_reply(_uuid, f'[TMDB] 未搜索到剧集信息, 跳过{rtpath_name}', path, is_anime, is_movie)

            if enable_scrape:
                logger.info("[处理任务] 正在补全季度信息以进行刮削...")
                info = self.search.fill_season_info(info)

            first_data = info.get('first_air_date', '0000-00-00')
            first_year = first_data.split('-')[0]
            work_path = _WORK_PATH / f'{name} ({first_year})'

            initial_season_id = self.get_season_id(info, work_path, path, titles)
            season_id = initial_season_id

            if cus_season_id:
                season_id = int(cus_season_id)
                logger.info(f"[处理任务] 强制使用用户指定季号: {season_id} (自动识别为: {initial_season_id}，已忽略)")

            if cus_season_id:
                season_id = int(cus_season_id)

            if enable_scrape:
                self.scraper.scrape_tv_show(work_path, info)

            if is_anime and effective_use_ai and ai_available:
                logger.info("[处理任务] 启用AI分析动漫文件映射")
                tv_info = self.search.fill_season_info(info)
                ai_result = self.ai_processor.analyze_anime_files(path, tv_info)

                confidence_threshold = cm.get_config("ai_confidence_threshold")
                should_use_ai = False
                if ai_result:
                    if confidence_threshold == "High" and ai_result.confidence == "High":
                        should_use_ai = True
                    elif confidence_threshold == "Medium" and ai_result.confidence in ["High", "Medium"]:
                        should_use_ai = True
                    elif confidence_threshold == "Low":
                        should_use_ai = True

                if should_use_ai and ai_result:
                    logger.info("[处理任务] 使用AI分析结果进行文件映射")
                    self.R = self.ai_processor.apply_ai_mapping(
                        ai_result=ai_result,
                        base_path=path,
                        work_path=work_path,
                    )
                    if not self.R:
                        logger.warning("[处理任务] AI未返回有效映射，回退到传统方法处理")
                        self._process_traditional(
                            path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                        )
                else:
                    logger.info("[处理任务] AI置信度不足或AI结果无效，使用传统方法处理")
                    self._process_traditional(
                        path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                    )
            else:
                if is_anime and not ai_available:
                    logger.info("[处理任务] AI客户端不可用，使用传统方法处理")
                elif is_anime and not effective_use_ai:
                    logger.info("[处理任务] 已显式禁用AI，使用传统方法处理")
                self._process_traditional(
                    path, rtpath_name, work_path, season_id, info, cus_offset, cus_season_id
                )

        final_tmdb_id = cus_tmdb_id
        if not final_tmdb_id and info and 'id' in info:
            final_tmdb_id = info['id']
        if self.R:
            try:
                first_dest = list(self.R.values())[0]
                display_target_path = str(first_dest)
            except:
                display_target_path = str(work_path)
        else:
            display_target_path = str(work_path)
        task_path = TASK_PATH / f"{_uuid}.json"
        task_data = {
            "path": str(path),
            "target_path": display_target_path,
            "is_anime": is_anime,
            "is_movie": is_movie,
            "name": name,
            "season_id": season_id,
            "uuid": str(_uuid),
            "error": None,
            "use_ai": bool(is_anime and effective_use_ai and ai_available),
            "tmdb_id": final_tmdb_id,
            "episode_offset": cus_offset,
        }
        trans_result = Trans(self.R, _uuid).trans_file()
        self.R = {}
        if isinstance(trans_result, str):
            return self.error_reply(_uuid, trans_result, path, is_anime, is_movie, name, season_id)
        with open(task_path, "w", encoding="UTF-8") as file:
            json.dump(task_data, file, indent=4, ensure_ascii=False)
        return True

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
    ):
        task_path = TASK_PATH / f'{_uuid}.json'
        task_data = {
            'path': str(path),
            'is_anime': is_anime,
            'is_movie': is_movie,
            'name': name,
            'season_id': season_id,
            'uuid': str(_uuid),
            'error': error,
        }
        with open(task_path, 'w', encoding='UTF-8') as file:
            json.dump(task_data, file, indent=4, ensure_ascii=False)
        return error
