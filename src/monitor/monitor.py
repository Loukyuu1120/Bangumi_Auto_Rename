import time
from pathlib import Path
from threading import Event
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ..rename.process import Rename
from ..logger import logger


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


def start_monitoring(
    path_to_monitor: Path,
    exclude_dirs: list[str],
    stop_event: Event | None = None,
):
    """
    启动目录监控

    Args:
        path_to_monitor: 要监控的目录
        exclude_dirs: 需要排除的目录名列表
        stop_event: 可选的停止事件，如果设置，在外部调用 stop_event.set() 即可停止监控线程
    """
    if stop_event is None:
        stop_event = Event()

    event_handler = MonitorEventHandler(Rename(), exclude_dirs)
    observer = Observer()
    observer.schedule(event_handler, path_to_monitor, recursive=True)
    observer.start()
    logger.info(f"[监控] 开始监控目录: {path_to_monitor}, 排除目录: {exclude_dirs}")
    try:
        # 用 stop_event 控制循环，而不是 while True + KeyboardInterrupt
        while not stop_event.is_set():
            time.sleep(1)
    except Exception as e:
        logger.error(f"[监控] 监控目录 {path_to_monitor} 时发生异常: {e}")
    finally:
        observer.stop()
        observer.join()
        logger.info(f"[监控] 已停止监控目录: {path_to_monitor}")
