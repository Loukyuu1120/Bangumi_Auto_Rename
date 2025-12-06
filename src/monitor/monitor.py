import time
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ..rename.process import Rename

logger = logging.getLogger(__name__)


class MonitorEventHandler(FileSystemEventHandler):
    def __init__(self, rename_processor: Rename, exclude_dirs: list[str]):
        super().__init__()
        self.rename_processor = rename_processor
        self.exclude_dirs = exclude_dirs

    def on_created(self, event):
        if event.is_directory:
            return
        if any(
            exclude_dir in Path(event.src_path).parts
            for exclude_dir in self.exclude_dirs
        ):
            logger.info(f"[监控] 忽略创建事件：{event.src_path} (在排除目录中)")
            return
        logger.info(f"[监控] 检测到新文件创建: {event.src_path}")
        self.rename_processor.process(Path(event.src_path))


def start_monitoring(path_to_monitor: Path, exclude_dirs: list[str]):
    event_handler = MonitorEventHandler(Rename(), exclude_dirs)
    observer = Observer()
    observer.schedule(event_handler, path_to_monitor, recursive=True)
    observer.start()
    logger.info(f"[监控] 开始监控目录: {path_to_monitor}, 排除目录: {exclude_dirs}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
