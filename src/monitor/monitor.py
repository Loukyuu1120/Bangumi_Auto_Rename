import re
import time
from pathlib import Path
from threading import Event, Lock
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ..rename.process import Rename
from ..logger import logger
from ..config.config_manager import cm


class MonitorEventHandler(FileSystemEventHandler):
    def __init__(self, rename_processor: Rename, exclude_dirs: list[str]):
        super().__init__()
        self.rename_processor = rename_processor

        self.exclude_patterns = []
        for pattern_str in exclude_dirs:
            if not pattern_str:
                continue
            try:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                self.exclude_patterns.append(pattern)
            except re.error as e:
                logger.error(f"[监控] 配置的排除规则 '{pattern_str}' 是无效的正则表达式: {e}")

    def on_created(self, event):
        if event.is_directory:
            return

        src_path = Path(event.src_path)
        src_path_str = src_path.as_posix()

        # 1. 排除规则
        for pattern in self.exclude_patterns:
            if pattern.search(src_path_str):
                logger.info(f"[监控] 忽略创建事件：{event.src_path} (匹配排除规则: '{pattern.pattern}')")
                return
        logger.info(f"[监控] 检测到新文件创建: {event.src_path}")

        use_ai = bool(cm.get_config("ai_enabled"))
        self.rename_processor.process(src_path, use_ai=use_ai)


def start_monitoring(
    path_to_monitor: Path,
    exclude_dirs: list[str],
    stop_event: Event | None = None,
):
    """
    启动目录监控
    """
    if stop_event is None:
        stop_event = Event()

    event_handler = MonitorEventHandler(Rename(), exclude_dirs)
    observer = Observer()
    observer.schedule(event_handler, path_to_monitor, recursive=True)
    observer.start()
    logger.info(f"[监控] 开始监控目录: {path_to_monitor}, 排除规则(正则): {exclude_dirs}")

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except Exception as e:
        logger.error(f"[监控] 监控目录 {path_to_monitor} 时发生异常: {e}")
    finally:
        observer.stop()
        observer.join()
        logger.info(f"[监控] 已停止监控目录: {path_to_monitor}")
