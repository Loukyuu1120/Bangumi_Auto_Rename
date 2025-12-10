import json
import re
from typing import Dict, List, Optional, Any

from ..logger import logger
from .models import AIAnalysisResult
from ..config.config_manager import cm
from .gemini_client import GeminiClient
from .openai_client import OpenAIClient
from .base_client import BaseAIClient


class AIClient:
    """AI客户端工厂类，根据配置选择合适的AI提供商"""

    def __init__(self):
        self.provider = cm.get_config("ai_provider") or "openai"
        self.enabled = bool(cm.get_config("ai_enabled"))
        self.confidence_threshold = cm.get_config("ai_confidence_threshold")

        # 根据提供商创建相应的客户端
        if self.provider.lower() == "gemini":
            self._client: BaseAIClient = GeminiClient()
        else:  # 默认使用OpenAI
            self._client: BaseAIClient = OpenAIClient()

    def is_available(self) -> bool:
        """检查AI客户端是否可用"""
        return bool(self.enabled and self._client and self._client.is_available())

    def select_best_tmdb_match(
            self,
            query: str,
            year: int,
            candidates: List[Dict],
            is_movie: bool
    ) -> Optional[int]:
        """从多个TMDB搜索结果中选择最佳匹配"""
        if not self.is_available():
            logger.debug("[AI辅助] AI功能未启用，跳过")
            return None

        if not candidates:
            return None

        try:
            # 构建候选信息
            candidates_info = []
            for idx, result in enumerate(candidates):
                if is_movie:
                    title = result.get('title', '')
                    release_date = result.get('release_date', '')
                    overview = result.get('overview', '')[:150]
                    tmdb_year = release_date.split('-')[0] if release_date else 'N/A'
                    original_title = result.get('original_title', '')
                    candidates_info.append({
                        'index': idx,
                        'title': title,
                        'original_title': original_title,
                        'year': tmdb_year,
                        'overview': overview
                    })
                else:
                    name = result.get('name', '')
                    first_air_date = result.get('first_air_date', '')
                    overview = result.get('overview', '')[:150]
                    tmdb_year = first_air_date.split('-')[0] if first_air_date else 'N/A'
                    original_name = result.get('original_name', '')
                    candidates_info.append({
                        'index': idx,
                        'name': name,
                        'original_name': original_name,
                        'year': tmdb_year,
                        'overview': overview
                    })

            # 构建提示词
            media_type = "电影" if is_movie else "电视剧"
            year_hint = f" ({year})" if year > 0 else ""

            user_prompt = f"""你是一个媒体信息匹配专家。现在需要从TMDB搜索结果中选择最匹配的{media_type}。

    搜索关键词: {query}{year_hint}

    候选结果（共{len(candidates_info)}个）:
    """
            for c in candidates_info:
                if is_movie:
                    user_prompt += f"""
    {c['index'] + 1}. 标题: {c['title']}
       原标题: {c['original_title']}
       年份: {c['year']}
       简介: {c['overview']}
    """
                else:
                    user_prompt += f"""
    {c['index'] + 1}. 名称: {c['name']}
       原名称: {c['original_name']}
       年份: {c['year']}
       简介: {c['overview']}
    """

            user_prompt += f"""
    请根据以下规则选择最佳匹配:
    1. 标题相似度（考虑中英文、简繁体、常见别名、数字表示）
    2. 年份匹配度（如果提供了年份，±1年内视为匹配）
    3. 概述内容相关性
    4. 媒体类型正确性
    5. 原标题/原名称的匹配度

    **特别注意**:
    - 数字续集（如"2"、"II"、"第二部"）应准确匹配
    - 如果年份明显不符（相差2年以上），除非其他信息高度匹配，否则降低该候选的优先级

    **输出格式**:
    只返回一个数字，表示最匹配的序号（1-{len(candidates_info)}）。
    如果所有候选都不合适，返回 0。
    不要有任何其他文字。
    """

            system_prompt = "你是一个专业的媒体信息匹配助手。你需要根据用户提供的搜索关键词和TMDB候选结果，选择最匹配的一个。你只输出数字，不输出其他内容。"

            logger.info(f"[AI辅助] 请求AI从{len(candidates_info)}个结果中选择最佳匹配")

            # 根据不同的客户端类型调用对应的API
            response = None

            if self.provider.lower() == "openai":
                # OpenAI 调用方式
                try:
                    from openai import OpenAI
                    client = self._client.client  # 获取底层的 OpenAI 客户端

                    completion = client.chat.completions.create(
                        model=self._client.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.3,
                    )
                    response = completion.choices[0].message.content.strip()

                except Exception as e:
                    logger.error(f"[AI辅助] OpenAI调用失败: {e}")
                    return None

            elif self.provider.lower() == "gemini":
                # Gemini 调用方式
                try:
                    full_prompt = f"{system_prompt}\n\n{user_prompt}"
                    result = self._client.model.generate_content(full_prompt)
                    response = result.text.strip()

                except Exception as e:
                    logger.error(f"[AI辅助] Gemini调用失败: {e}")
                    return None

            if not response:
                logger.warning("[AI辅助] AI无响应")
                return None

            # 解析响应
            match = re.search(r'\d+', response.strip())
            if not match:
                logger.warning(f"[AI辅助] 无法解析AI响应: {response}")
                return None

            selected = int(match.group())

            if selected == 0:
                logger.warning("[AI辅助] AI判断所有结果都不匹配")
                return None

            if not (1 <= selected <= len(candidates_info)):
                logger.warning(f"[AI辅助] AI返回的索引超出范围: {selected}")
                return None

            best_idx = selected - 1
            best = candidates_info[best_idx]

            if is_movie:
                logger.info(
                    f"[AI辅助] ✓ AI选择第{selected}个结果: "
                    f"{best['title']} ({best['year']})"
                )
            else:
                logger.info(
                    f"[AI辅助] ✓ AI选择第{selected}个结果: "
                    f"{best['name']} ({best['year']})"
                )

            return best_idx

        except Exception as e:
            logger.warning(f"[AI辅助] 选择过程出错: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def analyze_metadata(self, context_data: Dict) -> Optional[Dict[str, Any]]:
        """
        分析文件名和上下文以推断元数据（名称、年份、类型）

        Args:
            context_data: 包含 folder_name 和 file_names 的字典

        Returns:
            Dict: 包含 name, year, is_movie, tmdb_id, confidence
        """
        if not self.is_available():
            logger.warning(f"[AI搜索] AI功能未启用或{self.provider}客户端不可用")
            return None

        logger.debug(f"[AI搜索] 使用 {self.provider.upper()} 进行元数据推断")

        # 调用具体客户端的实现
        # 注意：你需要确保 BaseAIClient 及其子类实现了 analyze_metadata 方法
        result = self._client.analyze_metadata(context_data)

        return result

    def analyze_episode_mapping(
            self,
            anime_info: Dict,
            local_files: List[Dict],
    ) -> Optional[AIAnalysisResult]:
        """
        分析本地文件与TMDB剧集的映射关系

        Args:
            anime_info: TMDB动漫信息
            local_files: 本地文件信息列表，包含文件名、路径、时长等

        Returns:
            验证后的AIAnalysisResult对象
        """
        if not self.is_available():
            logger.warning(f"[AI识别] AI功能未启用或{self.provider}客户端不可用")
            return None

        logger.info(f"[AI识别] 使用 {self.provider.upper()} 进行分析")
        result = self._client.analyze_episode_mapping(anime_info, local_files)

        # 统一在此处保存分析数据
        self._client._save_analysis_data(anime_info, local_files, result)

        return result

    @staticmethod
    def build_metadata_prompt(context_data: Dict) -> str:
        """
        构建元数据分析的提示词

        Args:
            context_data: 包含 folder_name 和 file_names

        Returns:
            提示词字符串
        """
        folder_name = context_data.get("folder_name", "Unknown")
        file_names = context_data.get("file_names", [])
        total_files = context_data.get("total_files", len(file_names))

        files_str = "\n".join([f"- {name}" for name in file_names])

        prompt = f"""
请分析以下文件路径信息，请重视()里面的年份信息，推断该媒体对应的官方名称（Official Name）、发行年份和类型（TV或Movie）。

目录名称: 
{folder_name}

包含的文件 (共 {total_files} 个，仅列出部分):
{files_str}

要求：
1. 请提取最准确的官方名称（优先英文名，其次原名；如果英文是拼音，优先中文名）。
2. 如果可以确定，请提供发行年份。
3. 根据文件名特征（如S01E01表示TV，年份+文件名表示Movie）判断是剧集还是电影。
4. 如果文件名中包含明显的TMDB ID信息，请提取。
5. 请给出你对这次推断的置信度 (High/Medium/Low)。

"""
        return prompt

    @staticmethod
    def get_metadata_system_prompt() -> str:
        """
        获取元数据分析的系统提示词
        """
        return (
            "你是一个媒体文件元数据专家。你的任务是根据混乱的文件名和目录名，"
            "精准识别出电影或剧集的标准名称和年份。你只输出JSON格式的结果。"
        )

    @staticmethod
    def build_common_prompt(anime_info: Dict, local_files: List[Dict]) -> str:
        """
        构建通用的分析提示词，不包含JSON格式要求
        """
        # 构建TMDB信息
        tmdb_info = f"""
动漫名称: {anime_info.get('name', '未知')}
首播日期: {anime_info.get('first_air_date', '未知')}
总季数: {anime_info.get('number_of_seasons', 0)}
总集数: {anime_info.get('number_of_episodes', 0)}
"""

        # 构建季度信息
        seasons_info = "TMDB 季度信息：\n"
        seasons_info += json.dumps(anime_info.get("seasons", ""), ensure_ascii=False)

        # 构建本地文件信息
        files_info = "本地文件信息 (路径均为相对路径):\n"
        for i, file_info in enumerate(local_files, 1):
            duration_str = ""
            if file_info.get("duration"):
                duration_str = f" (时长: {file_info['duration']:.1f}分钟)"
            files_info += f"  {file_info['path']}{duration_str}\n"

        prompt = f"""
请分析以下动漫的本地文件与TMDB数据的对应关系：

{tmdb_info}

{seasons_info}

{files_info}

请特别注意以下常见情况：
0. TMDB的第0季通常是特典或OVA集
1. 本地目录可能将多季合并为一个目录，或者相反
2. 本地目录剧集的标号可能会是总集号，而不是TMDB的季集号
3. 本地目录可能会给总集篇标注4.5这样的半集号，而TMDB会将其放在第0季
4. OVA/特典可能被放在正片季度末尾，而tmdb会将其放在第0季
5. 本地目录的不同季度可能仅用名称区分，没有明确季号
6. 剧场版有时被混在TV版中，一般会被tmdb视为特典处理；有时剧场版本身被视为单独的电影，但其特典被tmdb放到tv版第0季

"""
        return prompt

    @staticmethod
    def get_system_prompt() -> str:
        """
        获取通用的系统提示词
        """
        return (
                "你是一个专业的动漫文件重命名助手。你需要分析本地动漫文件与TMDB数据库中剧集信息的对应关系，特别关注动漫BD发布与官方分季的差异。"
                + "请你只输出匹配到的季度和剧集信息，不要输出其他未匹配到tmdb信息的内容。"
        )
