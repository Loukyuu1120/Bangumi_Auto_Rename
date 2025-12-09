from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .monitor import monitor_service
from ..config.config_manager import cm
from ..logger import logger


class MonitorManager:
    """
    负责从配置中读取参数，并控制 MonitorService 的启停。
    不再直接管理线程，而是委托给 MonitorService 单例。
    """

    def __init__(self) -> None:
        # 这里不再需要维护 threads 列表，因为 Service 内部管理了
        pass

    def _stop_all(self) -> None:
        """停止监控服务"""
        logger.info("[监控管理器] 正在停止监控服务...")
        monitor_service.stop()

    def start_from_config(self) -> None:
        """根据当前配置启动监控（仅在程序启动时调用一次）"""
        if not cm.get_config("monitor_enabled"):
            logger.info("[监控管理器] 监控未启用，跳过启动")
            return

        monitor_paths_conf = cm.get_config("monitor_paths") or []
        exclude_dirs_conf = cm.get_config("monitor_exclude_dirs") or []

        # --- 配置解析 (兼容 JSON 字符串或列表) ---
        monitor_paths_conf = self._parse_config_list(monitor_paths_conf)
        exclude_dirs = self._parse_config_list(exclude_dirs_conf)

        if not monitor_paths_conf:
            logger.warning("[监控管理器] monitor_paths 为空，未启动任何监控")
            return

        # --- 整理有效路径 ---
        valid_paths: List[Path] = []
        for path_str in monitor_paths_conf:
            if not path_str:
                continue
            path = Path(path_str)
            valid_paths.append(path)

        # --- 调用 Service 统一启动 ---
        # 即使只有一个路径，也传给 Service，由 Service 统一管理队列
        monitor_service.start(valid_paths, exclude_dirs)

    def restart_from_config(self) -> None:
        """根据当前配置重启监控（保存配置后调用）"""
        logger.info("[监控管理器] 正在根据新配置重启监控...")
        self._stop_all()
        self.start_from_config()

    def _parse_config_list(self, config_val) -> List[str]:
        """辅助方法：处理可能是字符串也可能是列表的配置项"""
        if isinstance(config_val, list):
            return config_val
        if isinstance(config_val, str):
            try:
                return json.loads(config_val)
            except Exception:
                return []
        return []


# 提供一个全局单例
monitor_manager = MonitorManager()
