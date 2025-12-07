from __future__ import annotations

import threading
from pathlib import Path
from typing import List

from .monitor import start_monitoring
from ..config.config_manager import cm
from ..logger import logger


class MonitorManager:
    """负责按配置启动 / 停止 / 重启所有监控线程的管理器"""

    def __init__(self) -> None:
        self._threads: List[threading.Thread] = []
        self._stop_events: List[threading.Event] = []
        self._lock = threading.Lock()

    def _stop_all(self) -> None:
        """停止当前所有监控线程"""
        with self._lock:
            if not self._threads:
                return

            logger.info("[监控管理器] 正在停止所有监控线程...")
            for ev in self._stop_events:
                ev.set()

            for t in self._threads:
                t.join(timeout=5)

            self._threads.clear()
            self._stop_events.clear()
            logger.info("[监控管理器] 所有监控线程已停止")

    def start_from_config(self) -> None:
        """根据当前配置启动监控（仅在程序启动时调用一次）"""
        if not cm.get_config("monitor_enabled"):
            logger.info("[监控管理器] 监控未启用，跳过启动")
            return

        monitor_paths_conf = cm.get_config("monitor_paths") or []
        exclude_dirs = cm.get_config("monitor_exclude_dirs") or []

        # 兼容一下旧配置是字符串的情况
        if isinstance(monitor_paths_conf, str):
            try:
                import json

                monitor_paths_conf = json.loads(monitor_paths_conf)
            except Exception:
                monitor_paths_conf = []

        if isinstance(exclude_dirs, str):
            try:
                import json

                exclude_dirs = json.loads(exclude_dirs)
            except Exception:
                exclude_dirs = []

        if not monitor_paths_conf:
            logger.warning("[监控管理器] monitor_paths 为空，未启动任何监控")
            return

        with self._lock:
            for path_str in monitor_paths_conf:
                if not path_str:
                    continue

                path = Path(path_str)
                if not (path.exists() and path.is_dir()):
                    logger.warning(
                        f"[监控管理器] 监控目录 {path} 不存在或不是目录，跳过"
                    )
                    continue

                stop_event = threading.Event()
                t = threading.Thread(
                    target=start_monitoring,
                    args=(path, exclude_dirs, stop_event),
                    daemon=True,
                )
                t.start()

                self._threads.append(t)
                self._stop_events.append(stop_event)
                logger.info(f"[监控管理器] 已为目录 {path} 启动监控线程")

    def restart_from_config(self) -> None:
        """根据当前配置重启监控（保存配置后调用）"""
        logger.info("[监控管理器] 正在根据新配置重启监控...")
        self._stop_all()
        self.start_from_config()


# 提供一个全局单例
monitor_manager = MonitorManager()
