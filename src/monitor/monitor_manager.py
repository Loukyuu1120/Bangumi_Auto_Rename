from __future__ import annotations

import json
from pathlib import Path
from typing import List

from nicegui import run
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
        """根据当前配置启动监控"""
        if not cm.get_config("monitor_enabled"):
            logger.info("[监控管理器] 监控未启用，跳过启动")
            return

        monitor_configs = cm.get_config("monitor_paths") or []
        exclude_dirs_conf = cm.get_config("monitor_exclude_dirs") or []

        # --- 统一格式化为列表 ---
        if isinstance(monitor_configs, str):
            # 兼容旧配置是纯字符串的情况
            try:
                monitor_configs = json.loads(monitor_configs)
            except:
                monitor_configs = [monitor_configs]

        exclude_dirs = self._parse_config_list(exclude_dirs_conf)

        if not monitor_configs:
            logger.warning("[监控管理器] monitor_paths 为空，未启动任何监控")
            return

        # --- 整理路径和配置映射 ---
        # 结构: {Path('/data/tv'): {'tv_format': '...', ...}}
        valid_path_configs = {}

        for item in monitor_configs:
            path_str = ""
            config_data = {}

            if isinstance(item, str):
                path_str = item
            elif isinstance(item, dict):
                path_str = item.get("path", "")
                config_data = item  # 保留整个字典配置

            if not path_str:
                continue

            path_obj = Path(path_str)
            valid_path_configs[path_obj] = config_data

        # --- 调用 Service 统一启动 ---
        # 传入带有配置信息的字典
        monitor_service.start(valid_path_configs, exclude_dirs)

    async def restart_from_config(self) -> None:
        """根据当前配置重启监控（保存配置后调用）"""
        logger.info("[监控管理器] 正在根据新配置重启监控...")
        await run.io_bound(self._do_restart_sync)

    def _do_restart_sync(self) -> None:
        """实际执行停止和启动的同步方法"""
        try:
            self._stop_all()
            self.start_from_config()
        except Exception as e:
            logger.error(f"[监控管理器] 重启过程中发生错误: {e}")

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
