import re
import difflib
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from ..logger import logger
from .utils import (
    NUM_MAP,
    ROMA_MAP,
    cn_num,
    keywords,
    code_partten,
    season_partten,
    episode_partten,
    bracket_patterns,
)


def _clean_title_case_insensitive(title: str):
    # 将关键词和标题转换为小写进行匹配
    lower_keywords = [kw.lower() for kw in keywords]
    j = '|'.join(re.escape(kw) for kw in lower_keywords)
    keyword_regex = re.compile(j)

    # 遍历所有括号类型
    for pattern in bracket_patterns:
        # 查找所有匹配的括号内容
        matches = re.findall(pattern, title)  # 保留原始大小写内容
        for match in matches:
            # 转为小写进行匹配
            if keyword_regex.search(match.lower()):
                title = title.replace(match, '')  # 删除原始大小写内容

    # 返回清理后的标题
    return title.strip()[1:-1]


def chinese_to_number(chinese_numeral):
    chinese_digits = {
        '零': 0,
        '一': 1,
        '二': 2,
        '三': 3,
        '四': 4,
        '五': 5,
        '六': 6,
        '七': 7,
        '八': 8,
        '九': 9,
    }
    if chinese_numeral in chinese_digits:
        return chinese_digits[chinese_numeral]
    return None


def clean_noise(text: str) -> str:
    """
    清洗文件名中的常见垃圾词（分辨率、编码、扩展名等）
    """
    # 1. 去除扩展名
    text = re.sub(r'\.(strm|mp4|mkv|avi|mov|iso|ts)$', '', text, flags=re.IGNORECASE)

    # 2. 去除分辨率、编码等关键词 (基于 keywords 列表，或者手动补充)
    noise_patterns = [
        r'1080[pP]', r'720[pP]', r'2160[pP]', r'4[kK]',
        r'WebRip', r'BluRay', r'HEVC', r'AVC', r'AAC', r'H\.?26[45]',
        r'AC3', r'DTS', r'TrueHD', r'Atmos', r'HDR', r'Remux',
        r'-',  # 孤立的连字符
    ]

    for pat in noise_patterns:
        text = re.sub(pat, ' ', text, flags=re.IGNORECASE)

    # 3. 清理多余空格
    return re.sub(r'\s+', ' ', text).strip()


def remove_tag(title: str, skip=False):
    '''
    该步骤将带括号的文件名中，包含【指定关键词】的【任意括号】内容删除。

    [LoliHouse] Shangri / 香格里拉 [WebRip 1080p HEVC-10bit AAC]【简繁内封字幕】

    将会变为

    Shangri / 香格里拉

    如果指定`skip=True`，则会保留第二个匹配的括号，将会变为

    Shangri / 香格里拉 [WebRip 1080p HEVC-10bit AAC]

    当指定文件夹名字是下面类型的，会很有用

    [LoliHouse] [Shangri / 香格里拉] [WebRip 1080p HEVC-10bit AAC]【简繁内封字幕】
    '''
    s = title
    if skip:
        # 创建一个字典来追踪每种括号的匹配次数
        counts = {pattern: 0 for pattern in bracket_patterns}

        # 定义替换函数，追踪匹配次数并决定是否保留第二个匹配
        def replace_match(pattern, match):
            counts[pattern] += 1
            # 保留每种括号的第二个匹配，否则去除
            if counts[pattern] == 2:
                return match.group(0)
            else:
                return ''

        # 对每个模式应用相应的匹配逻辑
        for pattern in bracket_patterns:
            s = re.sub(pattern, lambda m: replace_match(pattern, m), s)
    else:
        # 不启用跳过规则，正常删除所有匹配项
        for pattern in bracket_patterns:
            s = re.sub(pattern, '', s)

    remove_tag_s = s.strip()
    logger.debug(f'[移除标签工具] {remove_tag_s}')
    if not remove_tag_s:
        s = _clean_title_case_insensitive(title)

    return s.strip()


def divide_by_year(filename: str) -> Tuple[str, int]:
    """
    增强版：分离年份并处理英文点号分隔的标题

    示例：
    - "Sword.and.Beloved.2025.S01E23" -> ("Sword and Beloved", 2025)
    - "香格里拉.2022" -> ("香格里拉", 2022)
    - "The.Last.of.Us.2023" -> ("The Last of Us", 2023)
    """
    original = filename

    # 1. 先用正则提取年份
    year_pattern = re.compile(r'(?:[\(\[\.\s]|^)(19\d{2}|20[0-3]\d)(?:[\)\]\.\s]|$)', re.IGNORECASE)
    match = year_pattern.search(filename)

    year = 0
    title_part = filename

    if match:
        year_str = match.group(1)
        year = int(year_str)
        start, end = match.span()
        title_part = filename[:start]

        # 如果年份前没有内容，取年份后的内容
        if not title_part.strip():
            rest = filename[end:]
            title_part = re.sub(r'^[\)\]\.\s\-]+', '', rest).strip()

    # 2. 检测是否为英文点号分隔格式
    # 条件：包含点号 且 没有中文字符
    is_english_dotted = '.' in title_part and not re.search(r'[\u4e00-\u9fff]', title_part)

    if is_english_dotted:
        logger.debug(f'[年份分离] 检测到英文点号分隔: {title_part}')

        # 移除季集信息（避免干扰）
        title_part = re.sub(r'\.S\d{1,2}E\d{1,3}.*$', '', title_part, flags=re.IGNORECASE)
        title_part = re.sub(r'\.S\d{1,2}\..*$', '', title_part, flags=re.IGNORECASE)

        # 移除分辨率等技术标签
        tech_tags = [
            r'\.2160p', r'\.1080p', r'\.720p', r'\.4K', r'\.UHD',
            r'\.WEB-?DL', r'\.WEBRip', r'\.BluRay', r'\.BDRip',
            r'\.HEVC', r'\.x26[45]', r'\.H\.26[45]',
            r'\.DDP', r'\.AAC', r'\.AC3', r'\.DTS',
            r'\.HDR', r'\.SDR', r'\.\d+fps',
            r'\.-[A-Z][a-zA-Z]+$'  # 组名如 -HiveWeb
        ]
        for tag in tech_tags:
            title_part = re.sub(tag, '', title_part, flags=re.IGNORECASE)

        # 转换点号和下划线为空格
        title_part = title_part.replace('.', ' ').replace('_', ' ')

        # 清理多余空格
        title_part = re.sub(r'\s+', ' ', title_part).strip()

        logger.debug(f'[年份分离] 英文标题清洗后: {title_part}')
    else:
        # 原有逻辑：清理标题末尾的残留符号
        title_part = re.sub(r'[\(\[\.\s\-]+$', '', title_part).strip()

    # 3. 最终清理
    clean_title = title_part.strip('.-_[](){} ')

    logger.debug(f'[年份分离] {original} -> 标题: {clean_title}, 年份: {year}')

    return clean_title, year


def parse_filename(filename: str) -> Tuple[str, int, Optional[int], Optional[int]]:
    """
    完整解析文件名，提取标题、年份、季号、集号

    示例：
    - "Sword.and.Beloved.2025.S01E23.2160p.WEB-DL.HEVC.strm"
      -> ("Sword and Beloved", 2025, 1, 23)
    - "[LoliHouse] 香格里拉.2022.S01E01.1080p.mkv"
      -> ("香格里拉", 2022, 1, 1)
    - "The.Last.of.Us.S01.1080p.mkv"
      -> ("The Last of Us", 0, 1, None)

    返回：(标题, 年份, 季号, 集号)
    """
    original = filename
    name = filename

    # 1. 先移除标签（利用已有的 remove_tag）
    name = remove_tag(name)

    # 2. 移除文件扩展名
    name = re.sub(r'\.(mkv|mp4|avi|mov|strm|ts|iso)$', '', name, flags=re.IGNORECASE)

    # 3. 提取季集信息
    season_num = None
    episode_num = None

    # 匹配 S01E23 格式
    se_match = re.search(r'[.\s\-_]S(\d{1,2})E(\d{1,3})', name, re.IGNORECASE)
    if se_match:
        season_num = int(se_match.group(1))
        episode_num = int(se_match.group(2))
        name = name[:se_match.start()]  # 移除季集及之后内容
        logger.debug(f'[文件名解析] 提取到 S{season_num}E{episode_num}')
    else:
        # 匹配 S01 格式
        s_match = re.search(r'[.\s\-_]S(\d{1,2})(?:[.\s\-_]|$)', name, re.IGNORECASE)
        if s_match:
            season_num = int(s_match.group(1))
            name = name[:s_match.start()]
            logger.debug(f'[文件名解析] 提取到 S{season_num}')

    # 4. 提取年份
    year = 0
    year_match = re.search(r'[.\s\-_\(]([12][90]\d{2})(?:[.\s\-_\)]|$)', name)
    if year_match:
        year = int(year_match.group(1))
        name = name[:year_match.start()]
        logger.debug(f'[文件名解析] 提取到年份: {year}')

    # 5. 移除技术标签（从第一个技术关键词开始截断）
    tech_pattern = r'[.\s\-_](2160p|1080p|720p|480p|4K|UHD|WEB-?DL|WEBRip|BluRay|BDRip|DVDRip|HDTV|HEVC|x26[45]|H\.26[45])'
    tech_match = re.search(tech_pattern, name, re.IGNORECASE)
    if tech_match:
        name = name[:tech_match.start()]
        logger.debug(f'[文件名解析] 移除技术标签后: {name}')

    # 6. 判断是否为英文点号分隔格式
    is_english_dotted = '.' in name and not re.search(r'[\u4e00-\u9fff]', name)

    if is_english_dotted:
        # 转换分隔符为空格
        name = name.replace('.', ' ').replace('_', ' ').replace('-', ' ')
    else:
        # 中文或其他情况，只替换下划线
        name = name.replace('_', ' ')

    # 7. 清理多余空格和首尾空白
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.strip('.-_[](){} ')

    logger.info(
        f'[文件名解析] "{original}" -> '
        f'标题:"{name}", 年份:{year}, S{season_num}E{episode_num}'
    )

    return name, year, season_num, episode_num

def remove_season(s: str):
    '''
    该步骤将文件名中, 类似季度的内容剔除

    Shangri / 香格里拉.S01E01

    将会变为

    Shangri / 香格里拉.E01
    '''
    for p in season_partten:
        s = re.sub(p, '', s)
    return s.strip()


def remove_episode(s: str):
    '''
    该步骤将文件名中, 类似剧集的内容剔除
    Shangri / 香格里拉.E01
    将会变为
    Shangri / 香格里拉.
    '''
    for p in episode_partten:
        s = re.sub(p, '', s)
    return s.strip()


def is_chinese_percentage_sufficient(text: str):
    '''
    用于判断字符串中 中文字符的比例是否至少占 25%
    '''
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
    chinese_chars = chinese_pattern.findall(text)
    total_chars = len(text)
    chinese_char_count = len(chinese_chars)
    if total_chars > 0:
        return chinese_char_count / total_chars >= 0.25
    else:
        return False


def extract_season(text: str):
    '''
    用于提取字符串中的 季 信息
    '''

    # 匹配 第1季, 第二季 等
    match = re.search(r'第([\d一二三四五六七八九零]{1,2})(季|部分|部)', text)
    if match:
        season_str = match.group(1)
        if season_str.isdigit():
            return int(season_str)
        else:
            # 中文数字转换为阿拉伯数字
            season_number = 0
            for char in season_str:
                _a = chinese_to_number(char)
                if _a is not None:
                    season_number += _a
            return season_number

    for p in season_partten:
        match = re.search(p, text)
        if match:
            if p == r'(First|Second|Third|Fourth|Fifth) Season':
                return NUM_MAP.get(match.group(1), 1)
            elif p == r'第([\d一二三四五六七八九零]{1,2})(季|部分|部)':
                continue
            elif p == r' (I{2,3})' or p == r' (I{1,3}V)' or p == r' (VI{2,3})':
                return ROMA_MAP.get(match.group(1), 1)
            else:
                if match:
                    return int(match.group(1))

    # 未找到匹配项
    return -1


def find_common_substrings_in_all(
    filenames: List[str], min_length: int = 3
) -> List[str]:
    common_substrings: List[str] = []

    # 取第一个文件名作为初始比较基础
    base_string = filenames[0]

    for filename in filenames[1:]:
        matcher = difflib.SequenceMatcher(None, base_string, filename)
        blocks = matcher.get_matching_blocks()

        # 每次匹配相似块，保留长度大于min_length的部分
        for match in blocks:
            if match.size > min_length:
                A = match.a
                B = match.size
                substring = base_string[A : A + B]  # noqa: E203
                if substring not in common_substrings:
                    common_substrings.append(substring)

    # 只保留在所有文件中都存在的相似部分
    final_common_substrings: List[str] = []
    for substring in common_substrings:
        if all(substring in filename for filename in filenames):
            final_common_substrings.append(substring)

    logger.debug(f'【相似部分】：{final_common_substrings}')
    return final_common_substrings


def find_unique_parts_in_videos(directory: Path):
    '''
    用于提取某个路径中所有文件的公共相似部分
    '''

    video_ext = ['.mp4', '.mkv', '.avi', '.mov', '.flv']
    files: List[Path] = [
        file for file in directory.iterdir() if file.suffix in video_ext
    ]
    filenames: List[str] = [file.stem for file in files]

    if len(filenames) < 2:
        return None

    # 找出所有文件的公共相似部分
    common_parts = find_common_substrings_in_all(filenames)

    return common_parts


def remove_similar_part(common_parts: List[str], filename: str):
    for common_part in common_parts:
        if len(common_part) > 3:  # 确保只移除长度大于3的部分
            pattern = re.escape(common_part)
            filename = re.sub(pattern, '', filename).strip()
    logger.debug(f'【移除相似部分】：{filename}')
    return filename


def remove_code(s: str) -> str:
    for p in code_partten:
        s = re.sub(p, '', s)
    return s


def extract_base_num(filename: str) -> Optional[float]:
    match = re.search(r'S\d+E(\d+)', filename)
    if match:
        return float(match.group(1))
    else:
        return None


def match_and_extract(input_string: str):
    '''
    用于提取字符串中的 季和集 信息
    S01E01
    '''

    pattern = re.compile(r'S(\d+)E(\d+)')
    match = pattern.search(input_string)

    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        return season, episode
    else:
        return None


def extract_number(filename: str) -> Optional[float]:
    match = re.search(
        r'(\d+\.?\d+|[零一二三四五六七八九十百千万]+\.?[\.零一二三四五六七八九十百千万]+)',
        filename,
    )
    if match:
        r = match.group(1)
        if r.isdigit():
            return int(r)
        else:
            return chinese_to_arabic(r)
    else:
        return None


def to_sim_max(all_similaritys: List[Dict[float, int]]):
    max_key = float('-inf')
    max_value = 1
    for similaritys in all_similaritys:
        _max_key = float('-inf')
        _max_value = 1

        for key, value in similaritys.items():
            if key > _max_key:
                _max_key = key
                _max_value = value

        if _max_key > max_key:
            max_key = _max_key
            max_value = _max_value

    if max_key > 0.6:
        season_id = max_value
    else:
        season_id = 1

    return season_id


def chinese_to_arabic(cn: str) -> int:
    unit = 0
    ldig = []

    for cndig in reversed(cn):
        if cndig in cn_num:
            num = cn_num[cndig]
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
    if unit == 10:  # 处理个位为0的情况，如 '十'
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
