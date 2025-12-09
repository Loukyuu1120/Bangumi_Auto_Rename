import time
import os
import re
from pathlib import Path
from threading import Event, Thread
from queue import Queue, Empty
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ..rename.process import Rename
from ..logger import logger
from ..config.config_manager import cm


class MonitorEventHandler(FileSystemEventHandler):
    def __init__(self, task_queue: Queue, exclude_dirs: list[str]):
        super().__init__()
        self.task_queue = task_queue
        self.exclude_patterns = []
        for pattern_str in exclude_dirs:
            if not pattern_str:
                continue
            try:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                self.exclude_patterns.append(pattern)
            except re.error as e:
                logger.error(f"[监控] 排除规则 '{pattern_str}' 无效: {e}")

    def _should_ignore(self, file_path_str: str) -> bool:
        if os.path.basename(file_path_str).startswith('.'):
            return True
        for pattern in self.exclude_patterns:
            if pattern.search(file_path_str):
                return True
        return False

    def on_created(self, event):
        if event.is_directory:
            return
        self._add_to_queue(event.src_path, "捕获新建")

    def on_moved(self, event):
        if event.is_directory:
            return
        self._add_to_queue(event.dest_path, "捕获移动")

    def _add_to_queue(self, path_str, action_name):
        path_obj = Path(path_str)
        if self._should_ignore(path_obj.as_posix()):
            logger.info(f"[监控] 忽略: {path_obj.name} (匹配排除规则)")
            return

        logger.info(f"[监控] {action_name}: {path_obj.name} -> 加入队列")
        self.task_queue.put(path_obj)


class MonitorService:
    """
    单例模式监控服务
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MonitorService, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return
        self.task_queue = Queue()
        self.stop_event = Event()
        self.observer = None
        self.worker_thread = None
        self.rename_processor = Rename()
        self.is_running = False

        # 新增：记录当前正在处理的文件名
        self.current_file: str | None = None

        self.initialized = True

    def start(self, paths_to_monitor: list[Path], exclude_dirs: list[str]):
        if self.is_running:
            logger.warning("[监控] 服务已经在运行中")
            return

        self.stop_event.clear()

        # 1. 启动消费者线程
        self.worker_thread = Thread(
            target=self._process_worker,
            daemon=True,
            name="RenameWorker"
        )
        self.worker_thread.start()

        # 2. 启动 Watchdog 生产者
        event_handler = MonitorEventHandler(self.task_queue, exclude_dirs)
        self.observer = Observer()

        monitored_count = 0
        for path in paths_to_monitor:
            if path.exists() and path.is_dir():
                self.observer.schedule(event_handler, str(path), recursive=True)
                logger.info(f"[监控] 已添加监控目录: {path}")
                monitored_count += 1
            else:
                logger.warning(f"[监控] 目录不存在，跳过: {path}")

        if monitored_count > 0:
            try:
                self.observer.start()
                self.is_running = True
                logger.info(f"[监控] 服务已启动，共监控 {monitored_count} 个目录")
            except Exception as e:
                logger.error(f"[监控] 启动失败: {e}")
        else:
            logger.warning("[监控] 没有有效的监控目录，服务未启动监听")

    def get_queue_list(self) -> list[str]:
        """返回当前队列中的文件名列表，供UI显示"""
        return [p.name for p in list(self.task_queue.queue)]

    def _process_worker(self):
        logger.info("[处理线程] 启动成功，等待任务...")
        while not self.stop_event.is_set():
            try:
                file_path = self.task_queue.get(timeout=1)
            except Empty:
                continue

            try:
                if not self._wait_for_file_ready(file_path):
                    logger.warning(f"[跳过] 文件无法读取或已消失: {file_path}")
                    self.task_queue.task_done()
                    continue

                self.current_file = file_path.name

                use_ai = bool(cm.get_config("ai_enabled"))
                logger.info(f"[开始处理] {file_path.name}")

                self.rename_processor.process(file_path, use_ai=use_ai)

            except Exception as e:
                logger.error(f"[处理异常] {file_path.name}: {e}")
            finally:
                self.current_file = None
                self.task_queue.task_done()

    def _wait_for_file_ready(self, file_path: Path, timeout=10, check_interval=1.0) -> bool:
        if not file_path.exists():
            return False
        start_time = time.time()
        last_size = -1
        while time.time() - start_time < timeout:
            try:
                current_size = file_path.stat().st_size
                if current_size > 0 and current_size == last_size:
                    return True
                last_size = current_size
                time.sleep(check_interval)
            except (FileNotFoundError, PermissionError):
                time.sleep(check_interval)
        logger.warning(f"[等待超时] 尝试强制处理: {file_path.name}")
        return True


# 全局单例实例
monitor_service = MonitorService()
