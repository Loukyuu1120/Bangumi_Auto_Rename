import re
import difflib
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union
from jinja2 import Environment, BaseLoader
from ..logger import logger
from .utils import (
    S0_TAG,
    VIDEO_SUFFIX,
    KEYWORDS_TO_CLEAN,
    BRACKET_PATTERNS,
    CN_NUM,
    SEASON_PATTERNS,
    EPISODE_PATTERNS,
    NUM_MAP,
    ROMA_MAP,
    CODE_PATTERNS,
    MEDIA_MAPPING
)


# ================= 工具函数 =================

def chinese_to_number(chinese_numeral):
    if chinese_numeral in CN_NUM:
        return CN_NUM[chinese_numeral]
    return None


def chinese_to_arabic(cn: str) -> int:
    unit = 0
    ldig = []
    for cndig in reversed(cn):
        if cndig in CN_NUM:
            num = CN_NUM[cndig]
            if num == 10 or num == 100 or num == 1000 or num == 10000:
                if num > unit:
                    unit = num
                    if unit == 10000:
                        ldig.append(unit)
                        unit = 1
                else:
                    unit *= num
            else:
                if unit:
                    num *= unit
                    unit = 0
                ldig.append(num)
    if unit == 10:
        ldig.append(10)
    val, tmp = 0, 0
    for x in reversed(ldig):
        if x == 10000:
            val += tmp * x
            tmp = 0
        else:
            tmp += x
    val += tmp
    return val


def _clean_title_case_insensitive(title: str):
    lower_keywords = [kw.lower() for kw in KEYWORDS_TO_CLEAN]
    j = '|'.join(re.escape(kw) for kw in lower_keywords)
    keyword_regex = re.compile(j)
    for pattern in BRACKET_PATTERNS:
        matches = re.findall(pattern, title)
        for match in matches:
            if keyword_regex.search(match.lower()):
                title = title.replace(match, '')
    return title.strip()[1:-1] if title.strip().startswith('[') and title.strip().endswith(']') else title.strip()


def remove_tag(title: str, skip=False):
    s = title
    if skip:
        counts = {pattern: 0 for pattern in BRACKET_PATTERNS}

        def replace_match(pattern, match):
            counts[pattern] += 1
            return match.group(0) if counts[pattern] == 2 else ''

        for pattern in BRACKET_PATTERNS:
            s = re.sub(pattern, lambda m: replace_match(pattern, m), s)
    else:
        for pattern in BRACKET_PATTERNS:
            s = re.sub(pattern, '', s)

    s = s.strip()
    if not s:
        s = _clean_title_case_insensitive(title)
    return s.strip()


def clean_noise(text: str) -> str:
    """清洗标题中的噪音"""
    # 动态生成扩展名正则
    ext_pattern = r'\.(' + '|'.join([ext.lstrip('.') for ext in VIDEO_SUFFIX]) + r')$'
    text = re.sub(ext_pattern, '', text, flags=re.IGNORECASE)

    # 移除技术参数
    for pat in CODE_PATTERNS:
        text = re.sub(pat, ' ', text, flags=re.IGNORECASE)
    # 移除组名 (-404, -Wiki 等)
    text = re.sub(r'-[a-zA-Z0-9]+$', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def remove_season(s: str):
    for p in SEASON_PATTERNS:
        s = re.sub(p, '', s, flags=re.IGNORECASE)
    return s.strip()


def remove_episode(s: str):
    for p in EPISODE_PATTERNS:
        s = re.sub(p, '', s, flags=re.IGNORECASE)
    return s.strip()


def remove_code(s: str) -> str:
    for p in CODE_PATTERNS:
        s = re.sub(p, '', s, flags=re.IGNORECASE)
    return s


# ================= 核心识别逻辑 =================

def extract_season(text: str) -> int:
    """
    从文本中提取季号
    返回: 季号(int), 未找到返回 -1
    """
    text = remove_code(text)
    for s0_pattern in S0_TAG:
        if re.search(s0_pattern, text, re.IGNORECASE):
            return 0
    match = re.search(r'第([\d一二三四五六七八九零]{1,2})(季|部分|部)', text)
    if match:
        season_str = match.group(1)
        if season_str.isdigit():
            return int(season_str)
        else:
            return chinese_to_arabic(season_str)

    for p in SEASON_PATTERNS:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            try:
                if 'Season' in p:
                    if match.group(1) in NUM_MAP:
                        return NUM_MAP[match.group(1)]
                    return int(match.group(1))
                elif 'I' in p or 'V' in p:
                    return ROMA_MAP.get(match.group(1), 1)
                else:
                    return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return -1


def match_and_extract(input_string: str) -> Optional[Tuple[int, int]]:
    """
    提取季和集
    返回: (season, episode) 或者 None
    """
    # 0. 中文季号+纯数字集号
    # 匹配 "第1季12" 或 "第1季 12"
    cn_season_num_pattern = re.search(r'第\s*(\d+)\s*季\s*(\d+)(?!\d)', input_string)
    if cn_season_num_pattern:
        season = int(cn_season_num_pattern.group(1))
        episode = int(cn_season_num_pattern.group(2))
        return season, episode
    cn_end_digit_match = re.search(r'[\u4e00-\u9fa5](\d{1,3})$', input_string.strip())
    if cn_end_digit_match:
        ep_val = int(cn_end_digit_match.group(1))
        season = extract_season(input_string)
        if season <= 0: season = 1
        return season, ep_val

    # 1. 标准 S01E01 格式
    pattern = re.compile(r'(?i)S(\d+)(?:E|EP)(\d+)')
    match = pattern.search(input_string)
    if match:
        return int(match.group(1)), int(match.group(2))

    # 2. 中文 第x集 格式
    cn_pattern = re.search(r'第\s*(\d+|[零一二三四五六七八九十百千万]+)\s*[集话]', input_string)
    if cn_pattern:
        ep_str = cn_pattern.group(1)
        episode = int(ep_str) if ep_str.isdigit() else chinese_to_arabic(ep_str)
        season = extract_season(input_string)
        if season <= 0: season = 1
        if episode > 0:
            return season, episode

    # 3. 动漫方括号格式 [01], [12v2], 【03】
    brackets = re.findall(r'[\[【](\d{1,4})(?:[vV]\d)?[\]】]', input_string)
    valid_eps = []
    for val in brackets:
        num = int(val)
        if num not in [480, 576, 720, 1080, 2160, 264, 265] and not (1900 < num < 2100):
            valid_eps.append(num)

    if valid_eps:
        episode = valid_eps[0]
        season = extract_season(input_string)
        if season <= 0: season = 1
        return season, episode

    # 4. 纯集数格式 .EP19, E19, EP19 (没有 S 前缀的情况)
    pure_ep_match = re.search(r'(?i)(?:^|[.\s\-_\[\(\[])E(P)?(\d{1,4})(?:[vV]\d)?(?:$|[.\s\-_\]\)\]])', input_string)
    if pure_ep_match:
        episode = int(pure_ep_match.group(2))
        if not (1900 < episode < 2100) and episode not in [480, 720, 1080, 2160]:
            season = extract_season(input_string)
            if season <= 0:
                season = 1
            return season, episode

    # 5. 空格+纯数字结尾 (解决: ...时间 06.strm)
    season_found = extract_season(input_string)
    if season_found > 0:
        end_num_match = re.search(r'\s(\d{1,3})(?:\.\w{2,4})?$', input_string)
        if end_num_match:
            ep_val = int(end_num_match.group(1))
            if ep_val < 1900:  # 排除年份
                return season_found, ep_val

    # 去除两端空格后，如果整个字符串就是 1-4 位数字
    clean_str = input_string.strip()
    if re.fullmatch(r'\d{1,4}', clean_str):
        episode = int(clean_str)
        if not (1900 < episode < 2100):
            season = extract_season(input_string)
            if season <= 0: season = 1
            return season, episode

    return None


def extract_base_num(filename: str) -> Optional[float]:
    """
    提取基础集数，用于 process_sub 的兜底逻辑
    """
    # 1. SxxExx 格式
    match = re.search(r'(?i)S\d+(?:E|EP)(\d+)', filename)
    if match:
        return float(match.group(1))

    # 2. 动漫方括号格式 [01]
    brackets = re.findall(r'[\[【](\d{1,4})(?:[vV]\d)?[\]】]', filename)
    for val in brackets:
        num = float(val)
        if num not in [480, 576, 720, 1080, 2160, 264, 265] and not (1900 < num < 2100):
            return num

    # 3. 纯集数格式 .EP19 或 EP19
    match_ep = re.search(r'(?i)(?:^|[.\s\-_])E(P)?(\d{1,4})(?:$|[.\s\-_])', filename)
    if match_ep:
        num = float(match_ep.group(2))
        if not (1900 < num < 2100):
            return num

    clean_str = filename.strip()
    if re.fullmatch(r'\d{1,4}', clean_str):
        num = float(clean_str)
        if not (1900 < num < 2100):
            return num

    return None


def extract_number(filename: str) -> Optional[float]:
    match = re.search(r'(\d+|[零一二三四五六七八九十百千万]+)', filename)
    if match:
        r = match.group(1)
        return int(r) if r.isdigit() else chinese_to_arabic(r)
    return None


def extract_tmdb_id(text: str) -> Optional[str]:
    """
    从字符串中提取 TMDB ID
    """
    if not text:
        return None

    patterns = [
        r'\{tmdb[-_]?(\d+)\}',  # {tmdb-12345}
        r'｛tmdb[-_]?(\d+)｝',  # 全角
        r'\[tmdbid=(\d+)\]',  # [tmdbid=12345]
        r'［tmdbid=(\d+)］',  # 全角
        r'(?:^|[.\s\-_\[\(\｛])tmdb[-_]?(\d+)(?:$|[.\s\-_\]\)\｝])',  # tmdb-12345
        r'\{tmdbid[-_]?(\d+)\}',
        r'｛tmdbid[-_]?(\d+)｝'
    ]

    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def is_weak_filename(filename: str) -> bool:
    """
    判断是否为弱文件名（不包含标题，只有集数信息）
    """
    stem = Path(filename).stem.upper()
    if re.fullmatch(r'\d+', stem): return True
    if re.fullmatch(r'(?i)E(P)?\d+(\s*v\d+)?', stem): return True
    if re.fullmatch(r'(?i)S\d+E(P)?\d+', stem): return True
    if len(stem) <= 4 and not re.search(r'[\u4e00-\u9fff]', stem): return True
    return False


def is_season_name(filename: str) -> bool:
    """
    判断目录名是否为纯季号目录
    """
    stem = Path(filename).stem.strip()
    if re.fullmatch(r'\d{1,2}', stem): return True
    if re.fullmatch(r'S\d+', stem, re.IGNORECASE): return True
    if re.fullmatch(r'(S|Season\s*)\d+\s*-\s*(S|Season\s*)?\d+', stem, re.IGNORECASE): return True
    if re.fullmatch(r'(SP|OVA|specials?)', stem, re.IGNORECASE): return True
    if re.match(r'(?i)^(Season|S)\s*\d+([ ._-]|$)', stem): return True
    for p in SEASON_PATTERNS:
        remain = re.sub(p, '', stem, flags=re.IGNORECASE).strip()
        if not remain or re.fullmatch(r'[.\-_\[\]\(\)\s]+', remain): return True
    return False


def is_video_file(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix in VIDEO_SUFFIX

# ================= 复杂文件名解析 =================

def parse_filename(filename: str) -> Tuple[str, int, Optional[int], Optional[int]]:
    """
    解析文件名：标题, 年份, 季, 集
    """
    original = filename

    # 1. 移除扩展名
    ext_list = [ext.lstrip('.') for ext in VIDEO_SUFFIX]
    ext_regex = r'\.(' + '|'.join(ext_list) + r')$'
    clean_original = re.sub(ext_regex, '', original, flags=re.IGNORECASE)

    year = 0
    # 2. 提取年份
    year_pattern = r'(?:[.\s\-_\(\[\（\【]|^)([12][90]\d{2})(?:[.\s\-_\)\]\）\】]|$)'
    year_match = re.search(year_pattern, clean_original)

    title_part = clean_original
    if year_match:
        year = int(year_match.group(1))
        #以年份为界，强制截断后面所有内容
        title_part = clean_original[:year_match.start()]

    # 3. 对截断后的标题进行 Tag 清洗
    title_part = remove_tag(title_part)

    # 匹配规则：行首 + 单个字母 + 空格 + 中文
    title_part = re.sub(r'^[a-zA-Z]\s+(?=[\u4e00-\u9fa5])', '', title_part)

    # 4. 提取季集信息
    season_num = None
    episode_num = None
    name = title_part

    # Pattern A: S01E01
    se_match = re.search(r'[.\s\-_]S(\d{1,2})(?:E|EP)(\d{1,3})', name, re.IGNORECASE)
    # Pattern B: S01-S02
    s_range_match = re.search(r'[.\s\-_]S(\d{1,2})\s*-\s*S?(\d{1,2})', name, re.IGNORECASE)
    # Pattern C: S01
    s_only_match = re.search(r'[.\s\-_]S(\d{1,2})(?:$|[.\s\-_])', name, re.IGNORECASE)

    # Pattern Season X
    season_word_match = re.search(r'[.\s\-_]Season[.\s\-_]*(\d{1,2})', name, re.IGNORECASE)

    # Pattern 第X季
    cn_season_match = re.search(r'[.\s\-_]?第\s*(\d+|[零一二三四五六七八九十百千万]+)\s*季', name)

    matches = []
    if se_match: matches.append((se_match.start(), se_match, 'se'))
    if s_range_match: matches.append((s_range_match.start(), s_range_match, 'range'))
    if s_only_match: matches.append((s_only_match.start(), s_only_match, 's_only'))
    if season_word_match: matches.append((season_word_match.start(), season_word_match, 'season_word'))
    if cn_season_match: matches.append((cn_season_match.start(), cn_season_match, 'cn_season'))

    matches.sort(key=lambda x: x[0])

    if matches:
        first_match = matches[0]
        match_obj = first_match[1]
        m_type = first_match[2]

        if first_match[0] < len(title_part):
            title_part = title_part[:first_match[0]]

        if m_type == 'se':
            season_num = int(match_obj.group(1))
            episode_num = int(match_obj.group(2))
        elif m_type == 'range':
            season_num = int(match_obj.group(1))
        elif m_type == 's_only':
            season_num = int(match_obj.group(1))
        elif m_type == 'season_word':
            season_num = int(match_obj.group(1))
        elif m_type == 'cn_season':
            val = match_obj.group(1)
            season_num = int(val) if val.isdigit() else chinese_to_arabic(val)

    # 尝试提取集数 (如果还没找到)
    if episode_num is None:
        pass

    # 5. 兜底清洗 (移除分辨率等技术参数)
    if year == 0 and season_num is None:
        res_match = re.search(r'[.\s\-_](1080[PpIi]|4[Kk]|2160[Pp]|720[Pp])', title_part, re.IGNORECASE)
        if res_match:
            title_part = title_part[:res_match.start()]
        else:
            codec_match = re.search(r'[.\s\-_]([xXhH]\.?26[45]|HEVC|AVC)', title_part, re.IGNORECASE)
            if codec_match:
                title_part = title_part[:codec_match.start()]

    # 6. 标准化清洗
    title_part = title_part.replace('：', ' ').replace(':', ' ')
    title_part = title_part.replace('.', ' ').replace('_', ' ')
    title_part = re.sub(r'\s+', ' ', title_part).strip(' .-[]()')

    return title_part, year, season_num, episode_num


def extract_media_info(filename: str) -> Dict[str, str]:
    """
    基于 utils.MEDIA_MAPPING 提取详细技术信息
    """
    info = {
        "source": "", "video_codec": "", "audio_codec": "",
        "resolution": "", "hdr": "", "fps": "", "channels": "",
        "group": "", "part": ""
    }

    upper_name = filename.upper()

    for category, mapping in MEDIA_MAPPING.items():
        detected = []
        for standard_name, keywords_list in mapping.items():
            for kw in keywords_list:
                if kw.upper() in upper_name:
                    detected.append(standard_name)
                    break

        if detected:
            if category == 'source':
                info['source'] = detected[0]
            elif category == 'video_codec':
                info['video_codec'] = ' '.join(detected)
            elif category == 'audio_codec':
                info['audio_codec'] = ' '.join(detected)
            elif category == 'hdr':
                info['hdr'] = ' '.join(detected)
            elif category == 'quality_tag':
                if info['video_codec']:
                    info['video_codec'] += ' ' + ' '.join(detected)
                else:
                    info['video_codec'] = ' '.join(detected)

    # 提取分辨率
    res_match = re.search(r'(2160[Pp]|4[Kk]|1080[PpIi]|720[Pp]|480[Pp]|576[Pp])', filename)
    if res_match:
        info['resolution'] = res_match.group(1).lower()

    # 提取 FPS
    fps_match = re.search(r'(\d{2,3})\s?FPS', upper_name)
    if fps_match:
        info['fps'] = f"{fps_match.group(1)}fps"

    # 提取声道
    chan_match = re.search(r'([257]\.[01])', filename)
    if chan_match:
        info['channels'] = chan_match.group(1)

    # 提取制作组
    group_match = re.search(r'-([a-zA-Z0-9_]+)(?:\[.*?\])?(?:\.[a-zA-Z0-9]{2,4})?$', filename)
    if group_match:
        grp = group_match.group(1)
        if grp.upper() not in ['DL', 'RIP', 'H264', 'H265', 'HEVC', 'AAC', 'MKV', 'MP4', 'STRM', 'ASS']:
            info['group'] = grp

    # 提取分片
    part_match = re.search(r'(?:CD|PART|DISC)\s?(\d+)', upper_name)
    if part_match:
        info['part'] = f"CD{part_match.group(1)}"

    return info


# ================= 模板渲染相关 =================

def sanitize_variable(text: str) -> str:
    if not text: return ""
    text = str(text).replace('/', ' ').replace('\\', ' ')
    text = text.replace(':', '：').replace('?', '？').replace('*', '') \
        .replace('"', '').replace('<', '').replace('>', '').replace('|', '')
    return ' '.join(text.split())


def get_render_context(path: Path, info: Dict, season: int = None, episode: int = None) -> Dict:
    # 1. 首先从文件名提取技术参数
    tech = extract_media_info(path.name)

    # 2. 从父目录提取作为补充
    parent_tech = extract_media_info(path.parent.name)

    # 3. 如果父目录是纯季号目录 (如 "Season 1")，往往没写分辨率，尝试去祖父目录找
    grandparent_tech = {}
    if path.parent != path.root and is_season_name(path.parent.name) and path.parent.parent != path.root:
        grandparent_tech = extract_media_info(path.parent.parent.name)

    # 4. 智能合并元数据 (优先级: 文件本身 > 父目录 > 祖父目录)
    for key in tech.keys():
        if not tech[key]:
            if parent_tech.get(key):
                tech[key] = parent_tech[key]
            elif grandparent_tech.get(key):
                tech[key] = grandparent_tech[key]

    raw_title = info.get('name') or info.get('title', '')
    raw_original_title = info.get('original_name') or info.get('original_title', '')

    title = clean_noise(raw_title)
    original_title = clean_noise(raw_original_title)

    date_str = info.get('release_date') or info.get('first_air_date') or '0000'
    year = date_str.split('-')[0]
    if year == '0000': year = ''

    context = {
        'title': sanitize_variable(title),
        'en_title': sanitize_variable(original_title),
        'original_title': sanitize_variable(original_title),
        'year': year,
        'tmdbid': str(info.get('id', '')),
        'fileExt': path.suffix,

        # 技术参数
        'webSource': sanitize_variable(tech['source']),
        'source': sanitize_variable(tech['source']),
        'videoFormat': sanitize_variable(tech['resolution']),
        'resolution': sanitize_variable(tech['resolution']),
        'videoCodec': sanitize_variable(tech['video_codec']),
        'audioCodec': sanitize_variable(tech['audio_codec']),
        'releaseGroup': sanitize_variable(tech['group']),
        'part': sanitize_variable(tech['part']),
        'hdr': sanitize_variable(tech['hdr']),
        'fps': sanitize_variable(tech['fps']),
        'channels': sanitize_variable(tech['channels']),
    }

    if season is not None:
        context['season'] = season
        context['season_00'] = f"{season:02d}"
    if episode is not None:
        context['episode'] = episode
        context['episode_00'] = f"{episode:02d}"
    if season is not None and episode is not None:
        context['season_episode'] = f"S{season:02d}E{episode:02d}"
    else:
        context['season_episode'] = ""

    return context


def render_path_template(template_str: str, context: Dict) -> Optional[Path]:
    try:
        env = Environment(loader=BaseLoader(), trim_blocks=True, lstrip_blocks=True)
        template = env.from_string(template_str)
        result = template.render(**context)
        result = result.replace(':', '：').replace('?', '？').replace('*', '') \
            .replace('"', '').replace('<', '').replace('>', '').replace('|', '')
        return Path(result.strip())
    except Exception as e:
        logger.error(f"[模板渲染错误] {e}")
        return None


# ================= OLD =================

def divide_by_year(filename: str) -> Tuple[str, int]:
    t, y, _, _ = parse_filename(filename)
    return t, y


def to_sim_max(all_similaritys: List[Dict[float, int]]):
    max_key = float('-inf')
    max_value = 1
    for similaritys in all_similaritys:
        for key, value in similaritys.items():
            if key > max_key:
                max_key = key
                max_value = value
    return max_value if max_key > 0.6 else 1


def find_unique_parts_in_videos(directory: Path):
    files = [f for f in directory.iterdir() if f.suffix.lower() in VIDEO_SUFFIX]
    filenames = [f.stem for f in files]
    if len(filenames) < 2: return None

    base = filenames[0]
    matcher = difflib.SequenceMatcher(None, base, filenames[1])
    match = matcher.find_longest_match(0, len(base), 0, len(filenames[1]))
    if match.size > 3:
        return [base[match.a: match.a + match.size]]
    return None


def remove_similar_part(common_parts: List[str], filename: str):
    if not common_parts: return filename
    for part in common_parts:
        if len(part) > 3:
            filename = filename.replace(part, '')
    return filename.strip()


def is_chinese_percentage_sufficient(text: str, threshold: float = 0.5) -> bool:
    """
    判断字符串中中文字符比例是否超过阈值。
    用于搜索时决定是否剔除干扰的英文字符。
    """
    if not text:
        return False
    zh_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    return (zh_count / len(text)) > threshold
