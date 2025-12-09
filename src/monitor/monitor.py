import time
import os
import re
import platform
from pathlib import Path
from threading import Event, Thread
from queue import Queue, Empty
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver
from ..rename.process import Rename
from ..config.config_manager import cm

from ..logger import logger

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
    智能单例模式监控服务
    支持自动切换 原生事件驱动(高效) / 轮询模式(兼容)
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
        self.current_file: str | None = None
        self.initialized = True

    # --- 核心辅助方法：统计文件数 ---
    @staticmethod
    def count_directory_files(directory: Path, max_check: int = 10000) -> int:
        """统计目录下的文件数量（用于检测是否超过系统限制）"""
        try:
            count = 0
            for root, dirs, files in os.walk(str(directory)):
                count += len(files)
                if count > max_check:
                    return count
            return count
        except Exception as err:
            logger.debug(f"统计目录文件数量失败: {err}")
            return 0

    # --- 检查系统限制 (Linux) ---
    @staticmethod
    def check_system_limits() -> dict:
        """检查 Linux 系统 inotify 限制"""
        limits = {'max_user_watches': 8192}  # 默认值
        if platform.system() != 'Linux':
            return limits

        try:
            with open('/proc/sys/fs/inotify/max_user_watches', 'r') as f:
                limits['max_user_watches'] = int(f.read().strip())
        except Exception:
            pass
        return limits

    # --- 动态加载 Observer ---
    def __choose_observer(self):
        """尝试加载最高效的 Observer，失败则返回 None"""
        system = platform.system()
        observers_to_try = []

        if system == 'Linux':
            observers_to_try = [('InotifyObserver', 'watchdog.observers.inotify')]
        elif system == 'Darwin':  # macOS
            observers_to_try = [('FSEventsObserver', 'watchdog.observers.fsevents')]
        elif system == 'Windows':
            observers_to_try = [('WindowsApiObserver', 'watchdog.observers.read_directory_changes')]

        for class_name, module_name in observers_to_try:
            try:
                module = __import__(module_name, fromlist=[class_name])
                ObserverClass = getattr(module, class_name)
                # 尝试实例化测试一下
                test_obs = ObserverClass()
                test_obs.stop()
                return ObserverClass
            except Exception as e:
                logger.debug(f"无法使用 {class_name}: {e}")

        return None

    def start(self, paths_to_monitor: list[Path], exclude_dirs: list[str]):
        if self.is_running:
            logger.warning("[监控] 服务已经在运行中")
            return

        self.stop_event.clear()

        # 1. 启动消费者线程 (文件处理)
        self.worker_thread = Thread(
            target=self._process_worker,
            daemon=True,
            name="RenameWorker"
        )
        self.worker_thread.start()

        # 2. 智能选择监控模式
        use_polling = False
        total_files = 0

        # 统计文件总量并检查系统限制
        for path in paths_to_monitor:
            if path.exists():
                total_files += self.count_directory_files(path)

        limits = self.check_system_limits()
        max_watches = limits['max_user_watches']

        # 决策逻辑：如果文件数超过限制的 80%，强制使用轮询，避免系统报错崩溃
        if platform.system() == 'Linux' and total_files > max_watches * 0.8:
            logger.warning(f"[监控] 文件数量({total_files}) 接近系统限制({max_watches})，强制使用轮询模式")
            use_polling = True

        # TODO: 用户也可以在 config 中强制开启兼容模式(如果有这个配置项)
        # TODO: if cm.get_config('monitor_mode') == 'compatibility': use_polling = True

        # 3. 实例化 Observer
        ObserverClass = None
        if not use_polling:
            ObserverClass = self.__choose_observer()
            if not ObserverClass:
                logger.info("[监控] 高效模式不可用，回退到轮询模式")
                use_polling = True

        if use_polling or ObserverClass is None:
            # 轮询模式：需要 timeout 参数来降低 CPU 占用
            self.observer = PollingObserver(timeout=2)
            mode_name = "兼容模式(轮询)"
        else:
            # 原生模式：通常不需要 timeout
            self.observer = ObserverClass()
            mode_name = "高效模式(原生)"

        # 4. 添加监控路径
        event_handler = MonitorEventHandler(self.task_queue, exclude_dirs)
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
                logger.info(f"[监控] 服务已启动，模式: [{mode_name}]，监控 {monitored_count} 个目录")
            except Exception as e:
                logger.error(f"[监控] 启动失败: {e}")
                # 如果是原生模式启动失败(比如inotify满了)，尝试紧急降级到轮询
                if not use_polling:
                    logger.warning("[监控] 尝试紧急切换到轮询模式...")
                    try:
                        self.observer = PollingObserver(timeout=3)
                        for path in paths_to_monitor:
                            if path.exists() and path.is_dir():
                                self.observer.schedule(event_handler, str(path), recursive=True)
                        self.observer.start()
                        self.is_running = True
                        logger.info("[监控] 紧急切换成功，当前运行于: [兼容模式]")
                    except Exception as e2:
                        logger.error(f"[监控] 紧急切换也失败了: {e2}")

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
