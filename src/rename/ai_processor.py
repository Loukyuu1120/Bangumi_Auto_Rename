from pathlib import Path
from typing import Dict, List, Optional, Union, Any
import re

from ..logger import logger
from .utils import VIDEO_SUFFIX
from ..ai.client import AIClient
from ..ai.models import AIAnalysisResult
from ..ai.video_analyzer import VideoAnalyzer


class AIProcessor:
    """AI辅助处理器，用于智能分析和重命名"""

    def __init__(self):
        self.ai_client = AIClient()
        self.video_analyzer = VideoAnalyzer()

    def select_best_tmdb_result(
            self,
            query: str,
            year: int,
            results: List[Dict],
            is_movie: bool
    ) -> Optional[int]:
        """
        从多个TMDB搜索结果中选择最佳匹配

        Args:
            query: 搜索关键词
            year: 年份（0表示未知）
            results: TMDB搜索结果列表
            is_movie: 是否为电影

        Returns:
            最佳匹配的索引（0-based），None表示无匹配或AI不可用
        """
        if not self.ai_client.is_available():
            logger.debug("[AI辅助选择] AI功能未启用")
            return None

        if not results:
            logger.debug("[AI辅助选择] 结果列表为空")
            return None

        if len(results) == 1:
            logger.debug("[AI辅助选择] 只有1个结果，无需AI选择")
            return 0

        if len(results) > 10:
            logger.info(f"[AI辅助选择] 结果数量({len(results)})超过10个，不使用AI辅助")
            return None

        logger.info(f"[AI辅助选择] 🤖 TMDB返回{len(results)}个结果，启用AI辅助选择")

        try:
            selected_idx = self.ai_client.select_best_tmdb_match(
                query=query,
                year=year,
                candidates=results,
                is_movie=is_movie
            )

            if selected_idx is not None:
                return selected_idx
            else:
                logger.info("[AI辅助选择] AI未能做出选择")
                return None

        except Exception as e:
            logger.warning(f"[AI辅助选择] AI选择过程出错: {e}")
            return None

    def analyze_search_metadata(self, path: Path) -> Optional[Dict[str, Any]]:
        """
        当常规TMDB搜索失败时，使用AI分析目录和文件名以推断元数据。
        """
        if not self.ai_client.is_available():
            logger.info("[AI搜索] AI功能未启用，跳过智能分析")
            return None

        if path.is_file():
            stem = path.stem
            # 匹配纯数字 或 S01E01 格式
            is_weak_name = stem.isdigit() or \
                           len(stem) < 3 or \
                           re.match(r'(?i)^s\d+e\d+', stem) or \
                           re.match(r'(?i)^\d+(\.\d+)?$', stem)

            if is_weak_name:
                # 如果文件名太弱，必须借用父目录的名字
                parent_name = path.parent.name

                if re.match(r'^(season|series|s)\s*\d*$', parent_name.lower()) or \
                        re.match(r'^(specials?|sp|ova)$', parent_name.lower()):
                    folder_name = path.parent.parent.name
                else:
                    folder_name = parent_name
            else:
                folder_name = stem

            video_files = [path]
        else:
            folder_name = path.name
            video_files = self._collect_video_files(path)

        if not video_files:
            logger.warning("[AI搜索] 未找到视频文件，无法进行AI元数据分析")
            return None

        # 3. 提取文件名用于AI分析
        file_names_context = self._get_file_names_context(video_files)

        # 构造给AI的上下文数据
        context_data = {
            "folder_name": folder_name,
            "file_names": file_names_context,
            "total_files": len(video_files)
        }

        logger.info(f"[AI搜索] 正在请求AI推断元数据: {folder_name} (参考文件数: {len(file_names_context)})")

        try:
            # 调用 AI Client 进行分析
            result = self.ai_client.analyze_metadata(context_data)

            if result:
                name = result.get('name')
                year = result.get('year')
                m_type = "Movie" if result.get('is_movie') else "TV"
                logger.info(f"[AI搜索] AI推断结果: {name} ({year}) - {m_type}")
                return result

        except Exception as e:
            logger.error(f"[AI搜索] AI分析过程中发生错误: {e}")

        return None

    def analyze_anime_files(
            self, path: Path, anime_info: Dict
    ) -> Optional[AIAnalysisResult]:
        """
        使用AI分析动漫文件的映射关系

        Args:
            path: 本地文件路径
            anime_info: TMDB动漫信息

        Returns:
            验证后的AI分析结果
        """
        if not self.ai_client.is_available():
            logger.info("[AI处理] AI功能未启用，跳过AI分析")
            return None

        # 收集视频文件
        video_files = self._collect_video_files(path)
        if not video_files:
            logger.warning("[AI处理] 未找到视频文件")
            return None

        # 分析视频文件
        base_dir = path.parent if path.is_file() else path
        file_analysis = self.video_analyzer.analyze_video_files(base_dir, video_files)

        # 使用AI分析映射关系
        ai_result = self.ai_client.analyze_episode_mapping(anime_info, file_analysis)

        if ai_result:
            logger.info(f"[AI处理] AI分析完成，置信度: {ai_result.confidence}")

            # 记录低置信度结果到单独日志
            if ai_result.confidence == "Low":
                self._log_low_confidence_result(path, ai_result)

        return ai_result

    def apply_ai_mapping(
            self,
            ai_result: AIAnalysisResult | None,
            base_path: Path,
            work_path: Path,
    ) -> Dict[Path, Path]:
        """
        应用AI分析结果生成全新的文件映射，并处理关联文件。

        Args:
            ai_result: 验证后的AI分析结果
            base_path: 媒体文件扫描的根目录
            work_path: 目标工作目录路径

        Returns:
            一个全新的文件映射字典
        """
        if not ai_result or not ai_result.file_mapping:
            logger.info("[AI处理] 无有效AI分析结果，返回空映射")
            return {}

        new_mapping: Dict[Path, Path] = {}
        actual_base_dir = base_path.parent if base_path.is_file() else base_path

        all_local_files = list(actual_base_dir.rglob("*"))

        try:
            if ai_result.season_mapping:
                logger.info("[AI处理] AI季度映射:")
                for season_map in ai_result.season_mapping:
                    logger.info(
                        f"  {season_map.local_group_name} -> TMDB季度: {season_map.maps_to_tmdb_seasons}"
                    )

            for mapping in ai_result.file_mapping:
                relative_path_str = mapping.file_path
                tmdb_season = mapping.tmdb_season
                tmdb_episode = mapping.tmdb_episode
                episode_type = mapping.episode_type
                confidence = mapping.confidence

                source_path = (actual_base_dir / relative_path_str).resolve()

                if not source_path.exists():
                    logger.warning(f"[AI处理] AI返回的文件路径不存在: {source_path}")
                    continue

                # 根据类型确定目标目录
                if episode_type == "special":
                    target_dir = work_path / "Season0"
                elif episode_type == "movie":
                    target_dir = work_path / "Movies"
                else:
                    target_dir = work_path / f"Season{tmdb_season}"

                target_dir.mkdir(parents=True, exist_ok=True)

                # 生成新的文件名
                if episode_type in ["special", "ova"]:
                    new_video_filename = f"S00E{tmdb_episode:02d}{source_path.suffix}"
                elif episode_type == "movie":
                    new_video_filename = source_path.name
                else:
                    new_video_filename = (
                        f"S{tmdb_season:02d}E{tmdb_episode:02d}{source_path.suffix}"
                    )

                # 1. 添加视频文件自身的映射
                target_video_path = target_dir / new_video_filename
                new_mapping[source_path] = target_video_path
                logger.info(
                    f"[AI处理] AI映射: {source_path.name} -> {new_video_filename} "
                    f"(类型: {episode_type}, 置信度: {confidence})"
                )

                # 2. 查找并添加关联文件的映射
                video_filename = source_path.stem
                for other_file in all_local_files:
                    if not other_file.is_file() or other_file == source_path:
                        continue

                    if other_file.name.startswith(f"{video_filename}."):
                        suffix_part = other_file.name[len(video_filename):]
                        new_video_stem = new_video_filename.rsplit(".", 1)[0]
                        new_associated_filename = f"{new_video_stem}{suffix_part}"

                        target_associated_path = target_dir / new_associated_filename
                        new_mapping[other_file] = target_associated_path
                        logger.info(
                            f"[AI处理] 发现并映射关联文件: {other_file.name} -> {new_associated_filename}"
                        )

        except Exception as e:
            logger.error(f"[AI处理] 应用AI映射时发生严重错误: {str(e)}", exc_info=True)
            return {}

        return new_mapping

    def _collect_video_files(self, path: Path) -> List[Path]:
        """
        收集视频文件。
        逻辑：
        无论传入的是文件还是目录，都获取该目录（或父目录）下的所有视频文件。
        这样可以一次性分析整个季度的文件，实现批量处理。
        """
        video_files = []

        # 确定搜索的基准目录
        search_dir = path if path.is_dir() else path.parent

        try:
            # 扫描目录下的所有视频文件
            for item in search_dir.iterdir():
                if item.is_file() and item.suffix.lower() in VIDEO_SUFFIX:
                    video_files.append(item)
        except Exception as e:
            logger.warning(f"[视频搜索] 扫描目录失败: {e}")

        return sorted(video_files)

    def _get_file_names_context(self, video_files: List[Path], limit: int = 6) -> List[str]:
        """提取文件名上下文，如果过多则截断"""
        all_file_names = [f.name for f in video_files]
        if len(all_file_names) > limit:
            half = limit // 2
            return all_file_names[:half] + all_file_names[-half:]
        return all_file_names

    def _log_low_confidence_result(self, path: Path, ai_result: AIAnalysisResult):
        """记录低置信度结果到单独日志"""
        confidence = ai_result.confidence
        reason = ai_result.reason

        logger.warning(
            f"[AI低置信度] 路径: {path} | 置信度: {confidence} | "
            f"理由: {reason} | 映射数量: {len(ai_result.file_mapping)}"
        )

        # 记录季度映射
        if ai_result.season_mapping:
            for season_map in ai_result.season_mapping:
                logger.warning(
                    f"[AI低置信度] 季度映射: {season_map.local_group_name} -> {season_map.maps_to_tmdb_seasons}"
                )

        # 详细记录每个映射的置信度
        for mapping in ai_result.file_mapping:
            if mapping.confidence == "Low":
                logger.warning(
                    f"[AI低置信度文件] {mapping.file_path} -> "
                    f"S{mapping.tmdb_season:02d}E{mapping.tmdb_episode:02d} "
                    f"(类型: {mapping.episode_type}, 置信度: {mapping.confidence})"
                )

        # 记录额外说明
        if ai_result.extra_notes:
            logger.warning(f"[AI低置信度] 额外说明: {ai_result.extra_notes}")
