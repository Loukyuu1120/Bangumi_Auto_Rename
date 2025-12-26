IGNORE_DIR = ['cd', 'scan']
IGNORE_SUFFIX = ['.rar', '.zip', '.7z', '.webp', '.jpg', '.png', '.nfo', '.txt', '.xml']

EXTRA_TAG = [
    'NCOP', 'NCED', 'Menu', 'Teaser', 'IV', 'CM', 'NC', 'OP', 'PV', 'ED',
    'Advice', 'Trailer', 'Event', 'Fans', '访谈', 'Preview', 'Picture Drama',
    '预告', '特典', '映像',
]
S0_TAG = [
    r'OVA', r'OAD', r'Special', r'sp', r'SP', r'00', r'\.5', r'Chaos no Kakera'
]
VIDEO_SUFFIX = [
    '.strm', '.mp4', '.mkv', '.avi', '.wmv', '.flv', '.mov', '.mpg', '.mpeg',
    '.m4v', '.rm', '.rmvb', '.ts', '.iso', '.m2ts'
]
KEYWORDS_TO_CLEAN = [
    '01', '1080P', 'FLAC', '简繁', '外挂', 'MKV', 'MP4', 'TV', '全集',
    'HEVC', '8bit', '10bit', '720P', '2160P', '4K', 'BD', 'RIP', 'DBD-raws',
    'Remux', 'AVC', 'H264', 'H265', 'DTS', 'DTS-HD', 'TrueHD', 'Atmos',
    'HDR', 'DV', 'Dolby', 'AAC', 'AC3', 'HQ', 'Web-DL', 'BluRay'
]

# ================= 媒体信息提取映射表 =================
MEDIA_MAPPING = {
    'source': {
        'Remux': ['REMUX'],
        'BluRay': ['BLURAY', ' BD ', ' BD-', '.BD.'],
        'BDRip': ['BDRIP'],
        'UHD-BD': ['UHD-BD', 'UHD BluRay'],
        'WEB-DL': ['WEB-DL', 'WEBDL'],
        'WEBRip': ['WEBRIP'],
        'HDTV': ['HDTV'],
        'DVD': ['DVD', 'NTSC', 'PAL'],
    },
    'video_codec': {
        'x264': ['x264', 'H264', 'AVC'],
        'x265': ['x265', 'H265', 'HEVC'],
        'MPEG2': ['MPEG2'],
        'VC-1': ['VC-1'],
        'AV1': ['AV1'],
        'VP9': ['VP9'],
    },
    'audio_codec': {
        'DTS-HD MA': ['DTS-HD', 'DTSHD'],
        'DTS': ['DTS'],
        'TrueHD': ['TRUEHD'],
        'Atmos': ['ATMOS'],
        'AC3': ['AC3', 'DDP', 'EAC3'],
        'AAC': ['AAC'],
        'FLAC': ['FLAC'],
        'Opus': ['OPUS'],
        'MP3': ['MP3'],
        'PCM': ['LPCM', 'PCM'],
    },
    'hdr': {
        'Dolby Vision': ['DV', 'Dolby Vision'],
        'HDR10+': ['HDR10+'],
        'HDR': ['HDR'],
    },
    'quality_tag': {
        'HQ': ['HQ'],
        '10bit': ['10bit', '10-bit'],
        '8bit': ['8bit', '8-bit'],
    }
}

# ================= 正则模式 =================

BRACKET_PATTERNS = [
    r'\[.*?\]',
    r'【.*?】',
    r'《.*?》',
    r'<.*?>',
    r'\(.*?\)',
    r'（.*?）',
]

CN_NUM = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    '百': 100, '千': 1000, '万': 10000,
}
SEASON_PATTERNS = [
    r'S\s*([\d]{1,2})',
    r'第([\d一二三四五六七八九零]{1,2})(季|部分|部)',
    r'([\d]{1,2})nd Season',
    r'Season\s*([\d]{1,2})',
    r'Series\s*([\d]{1,2})',
    r'(First|Second|Third|Fourth|Fifth) Season',
    r' (I{2,3})', r' (I{1,3}V)', r' (VI{2,3})',
]
EPISODE_PATTERNS = [
    r'第\s*(\d+)\s*季\s*(\d+)(?!\d)',  # 匹配：第1季06
    r'[Ss]([\d]{1,2})[Ee]([\d]{1,3})', # 匹配：S01E06

    r'[Ee]([\d]{1,3})',           # E06
    r'[Ee][Pp]([\d]{1,3})',       # EP06
    r'第([\d一二三四五六七八九零]+)[话集]', # 第06集, 第一集
    r'([\d]{1,3})[Ee]pisode',
    r'([\d]{1,3})[Ee]ps',
    r'\[(\d{1,3})\]',             # [06]
    r'【(\d{1,3})】',             # 【06】
    r' - (\d{1,3})(?:\D|$)',      #  - 06 (空格-空格 数字)

    r'\s(\d{1,3})(?:\.\w{2,4})?$',
]

NUM_MAP = {'First': 1, 'Second': 2, 'Third': 3, 'Fourth': 4, 'Fifth': 5}
ROMA_MAP = {'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7}

CODE_PATTERNS = [
    r'(2160|1080|720|480|576)[pP]',
    r'x264|x265|h264|h265|hevc|avc|mpeg2|vp9|av1',
    r'(?i)(dts-?hd|dts|truehd|atmos|ac3|aac|flac|opus|mp3|pcm)(\W*\d+\.\d)?(audio|ch|channel)?',
    r'(?i)\b\d{1,2}\.\d(audio|ch|channel|sound)\b',
    r'hdr|dv|dolby|10bit|8bit',
    r'remux|bluray|web-dl|webrip|hdtv|bdrip|dvdrip',
    r'hq|(\d{2,3})\s?fps',
]
DEFAULT_SECONDARY_RULES = {
    "movie": [
        {"name": "演唱会", "conditions": {"genre_ids": "10402"}},
        {"name": "蓝光原盘", "conditions": {"ext": "iso"}},
        {"name": "纪录片电影", "conditions": {"genre_ids": "99"}},
        {"name": "动漫电影", "conditions": {"genre_ids": "16", "origin_country": "JP"}},
        {"name": "动画电影", "conditions": {"genre_ids": "16"}},
        {"name": "华语电影", "conditions": {"original_language": "zh,cn,bo,za"}},
        {"name": "外语电影", "conditions": {}}  # 兜底
    ],
    "tv": [
        {"name": "儿童", "conditions": {"genre_ids": "10762"}},
        {"name": "国漫", "conditions": {"genre_ids": "16", "origin_country": "CN,TW,HK"}},
        {"name": "日番", "conditions": {"genre_ids": "16", "origin_country": "JP"}},
        {"name": "美漫", "conditions": {"genre_ids": "16", "origin_country": "US,CA,GB,FR,DE"}},
        {"name": "纪录片剧集", "conditions": {"genre_ids": "99"}},
        {"name": "综艺", "conditions": {"genre_ids": "10764,10767"}},
        {"name": "国产剧", "conditions": {"origin_country": "CN,TW,HK,SG"}},
        {"name": "欧美剧", "conditions": {"origin_country": "US,FR,GB,DE,ES,IT,NL,PT,RU,UK"}},
        {"name": "日韩剧", "conditions": {"origin_country": "JP,KP,KR,TH,IN,SG"}},
        {"name": "其它", "conditions": {}} # 兜底
    ]
}
