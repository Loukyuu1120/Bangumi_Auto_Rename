import time
import os
import re
import platform
import gc
import threading
import json
from pathlib import Path
from threading import Event, Thread
from queue import PriorityQueue, Empty
from typing import Dict, Any
from itertools import count

from nicegui import run
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from ..rename.cleaner import is_video_file
from ..utils.path import TASK_PATH
from ..rename.process import Rename
from ..config.config_manager import cm
from ..logger import logger


class MonitorEventHandler(FileSystemEventHandler):
    def __init__(self, service_instance, exclude_dirs: list[str]):
        super().__init__()
        self.service = service_instance
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
        if os.path.basename(file_path_str).startswith("."):
            return True
        if not is_video_file(file_path_str):
            return True
        for pattern in self.exclude_patterns:
            if pattern.search(file_path_str):
                return True
        return False

    def on_created(self, event):
        if event.is_directory:
            return
        if self._add_to_queue(event.src_path, "捕获新建"):
            pass

    def on_moved(self, event):
        if event.is_directory:
            return
        if self._add_to_queue(event.dest_path, "捕获移动"):
            pass

    def _add_to_queue(self, path_str, action_name):
        if self._should_ignore(path_str):
            return False

        path_obj = Path(path_str)
        logger.info(f"[监控] {action_name}: {path_obj.name} -> 加入队列 (系统优先)")
        with self.service._count_lock:
            self.service.logical_pending_count += 1

        seq = next(self.service._counter)
        self.service.task_queue.put((self.service.PRIORITY_SYSTEM, seq, (path_obj, {})))
        return True


class MonitorService:
    """
    智能单例模式监控服务
    """

    _instance = None
    PRIORITY_SYSTEM = 1
    PRIORITY_MANUAL = 10

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MonitorService, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return
        # 使用 PriorityQueue 替代普通 Queue
        self.task_queue = PriorityQueue(maxsize=0)

        self._counter = count()

        self.logical_pending_count = 0
        self._count_lock = threading.Lock()
        self._paused = False
        self.stop_event = Event()
        self.observer = None
        self.worker_thread = None
        self.is_running = False
        self.current_file: str | None = None
        self.path_map: Dict[Path, Dict] = {}
        self.QUEUE_FILE = TASK_PATH / "saved_queue.json"

        self.last_cache_clear_time = time.time()
        self.CACHE_CLEAR_INTERVAL = 86400  # 24小时

        self.initialized = True

    @property
    def is_paused_state(self) -> bool:
        return self._paused

    def pause_processing(self):
        self._paused = True
        logger.info("[控制] 任务处理已暂停 (新文件将进入队列等待)")

    def resume_processing(self):
        self._paused = False
        logger.info("[控制] 任务处理已恢复")

    def clear_pending_tasks(self):
        # 1. 暂停防止写入冲突
        was_paused = self._paused
        self._paused = True

        # 2. 彻底清空队列
        try:
            with self.task_queue.mutex:
                self.task_queue.queue.clear()
        except Exception:
            while not self.task_queue.empty():
                try:
                    self.task_queue.get_nowait()
                    self.task_queue.task_done()
                except Empty:
                    break

        # 3. 重置计数
        with self._count_lock:
            self.logical_pending_count = 0

        # 4. 强制垃圾回收
        gc.collect()

        if not was_paused:
            self._paused = False

        logger.warning("[控制] 待处理队列已强制清空，内存已清理")

    def has_saved_queue(self) -> bool:
        return self.QUEUE_FILE.exists()

    def save_queue_to_disk(self) -> int:
        """保存并清空队列"""

        if self.task_queue.empty():
            return 0

        logger.info("[任务保存] 正在保存未完成的任务...")
        self._paused = True
        saved_items = []

        # 临时列表用于保存取出的数据，以便后续如果要继续运行可以放回
        temp_items = []

        # 1. 提取所有任务
        while True:
            try:
                item_tuple = self.task_queue.get_nowait()
                priority, seq, (path_obj, options) = item_tuple

                saved_items.append({
                    "path": str(path_obj),
                    "options": options,
                    "priority": priority  # 保存优先级
                })
                temp_items.append(item_tuple)
                self.task_queue.task_done()
            except Empty:
                break

        # 2. 写入磁盘
        if saved_items:
            try:
                with open(self.QUEUE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(saved_items, f, indent=4, ensure_ascii=False)
                logger.info(f"[任务保存] 已将 {len(saved_items)} 个任务保存到磁盘")
            except Exception as e:
                logger.error(f"[任务保存] 保存失败: {e}")

        # 3. 重置计数
        with self._count_lock:
            self.logical_pending_count = 0

        del saved_items
        del temp_items
        gc.collect()

        return 0

    def load_queue_from_disk(self) -> int:
        if not self.QUEUE_FILE.exists():
            return 0

        count_loaded = 0
        try:
            with open(self.QUEUE_FILE, 'r', encoding='utf-8') as f:
                saved_items = json.load(f)

            if isinstance(saved_items, list):
                for entry in saved_items:
                    path_str = entry.get("path")
                    options = entry.get("options", {})
                    priority = entry.get("priority", self.PRIORITY_MANUAL)

                    if path_str:
                        path_obj = Path(path_str)
                        if path_obj.exists():
                            seq = next(self._counter)
                            self.task_queue.put((priority, seq, (path_obj, options)))
                            count_loaded += 1

            with self._count_lock:
                self.logical_pending_count += count_loaded

            self.QUEUE_FILE.unlink()  # 加载成功后删除存档
            logger.info(f"[任务恢复] 成功恢复 {count_loaded} 个任务")

        except Exception as e:
            logger.error(f"[任务恢复] 读取失败: {e}")

        return count_loaded

    @staticmethod
    def count_directory_files(directory: Path, max_check: int = 10000) -> int:
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

    @staticmethod
    def check_system_limits() -> dict:
        limits = {"max_user_watches": 8192}
        if platform.system() != "Linux":
            return limits
        try:
            with open("/proc/sys/fs/inotify/max_user_watches", "r") as f:
                limits["max_user_watches"] = int(f.read().strip())
        except Exception:
            pass
        return limits

    def __choose_observer(self):
        system = platform.system()
        observers_to_try = []

        if system == "Linux":
            observers_to_try = [("InotifyObserver", "watchdog.observers.inotify")]
        elif system == "Darwin":
            observers_to_try = [("FSEventsObserver", "watchdog.observers.fsevents")]
        elif system == "Windows":
            observers_to_try = [
                ("WindowsApiObserver", "watchdog.observers.read_directory_changes")
            ]

        for class_name, module_name in observers_to_try:
            try:
                module = __import__(module_name, fromlist=[class_name])
                ObserverClass = getattr(module, class_name)
                test_obs = ObserverClass()
                test_obs.stop()
                return ObserverClass
            except Exception as e:
                logger.debug(f"无法使用 {class_name}: {e}")

        return None

    async def start(self, path_configs: Dict[Path, Dict], exclude_dirs: list[str]):
        """启动监控"""
        if self.is_running:
            logger.warning("[监控] 服务已经在运行中")
            return

        # 启动时尝试加载上次保存的任务
        self.load_queue_from_disk()

        def _start_sync():
            self.stop_event.clear()
            self.path_map = path_configs
            paths_to_monitor = list(path_configs.keys())

            if not self.worker_thread or not self.worker_thread.is_alive():
                self.worker_thread = Thread(
                    target=self._process_worker, daemon=True, name="RenameWorker"
                )
                self.worker_thread.start()

            use_polling = False
            total_files = 0
            for path in paths_to_monitor:
                if path.exists():
                    total_files += self.count_directory_files(path)

            limits = self.check_system_limits()
            max_watches = limits["max_user_watches"]

            if platform.system() == "Linux" and total_files > max_watches * 0.8:
                logger.warning(
                    f"[监控] 文件数量({total_files}) 接近系统限制({max_watches})，强制使用轮询模式"
                )
                use_polling = True

            if cm.get_config("monitor_mode") == "compatibility":
                use_polling = True

            ObserverClass = None
            if not use_polling:
                ObserverClass = self.__choose_observer()
                if not ObserverClass:
                    logger.info("[监控] 高效模式不可用，回退到轮询模式")
                    use_polling = True

            if use_polling or ObserverClass is None:
                self.observer = PollingObserver(timeout=2)
                mode_name = "兼容模式(轮询)"
            else:
                try:
                    self.observer = ObserverClass()
                    mode_name = "高效模式(原生)"
                except Exception as e:
                    logger.error(f"[监控] 实例化原生Observer失败: {e}，回退到轮询")
                    self.observer = PollingObserver(timeout=2)
                    mode_name = "兼容模式(轮询)"

            event_handler = MonitorEventHandler(self, exclude_dirs)
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
                    logger.info(
                        f"[监控] 服务已启动，模式: [{mode_name}]，监控 {monitored_count} 个目录"
                    )
                except Exception as e:
                    logger.error(f"[监控] 启动失败: {e}")
                    if "轮询" not in mode_name:
                        logger.warning("[监控] 尝试紧急切换到轮询模式...")
                        try:
                            self.observer = PollingObserver(timeout=3)
                            for path in paths_to_monitor:
                                if path.exists() and path.is_dir():
                                    self.observer.schedule(
                                        event_handler, str(path), recursive=True
                                    )
                            self.observer.start()
                            self.is_running = True
                            logger.info("[监控] 紧急切换成功，当前运行于: [兼容模式]")
                        except Exception as e2:
                            logger.error(f"[监控] 紧急切换也失败了: {e2}")
            else:
                logger.warning("[监控] 没有有效的监控目录，服务未启动监听")

        await run.io_bound(_start_sync)
        logger.info("[监控] 启动流程已在后台完成")

    def _scheduled_cache_clear(self):
        try:
            count_cleared = Rename.clear_processed_cache()
            logger.info(f"[定时任务] 自动清理了 {count_cleared} 条路径缓存记录")
        except Exception as e:
            logger.error(f"[定时任务] 缓存清理失败: {e}")

    def stop(self):
        """停止监控服务和处理线程，并保存队列"""
        logger.info("[监控] 正在接收停止指令...")

        # 停止时自动保存队列
        try:
            self.save_queue_to_disk()
        except Exception as e:
            logger.error(f"[监控] 自动保存队列失败: {e}")

        if self.observer:
            if self.observer.is_alive():
                self.observer.stop()
                self.observer.join()
            self.observer = None
            logger.info("[监控] 目录监听器已停止")

        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.worker_thread.join()
            self.worker_thread = None
            logger.info("[监控] 处理线程已停止")

        self.is_running = False
        logger.info("[监控] 服务已完全停止")

    def get_queue_list(self) -> list[str]:
        return [
            p[2][0].name  # 提取 path.name (index 2 is the data tuple)
            for p in list(self.task_queue.queue)
        ]

    def register_batch_count(self, count_val: int):
        with self._count_lock:
            self.logical_pending_count += count_val
        logger.info(f"[计数器] 批量注册任务: +{count_val}, 当前待处理: {self.logical_pending_count}")

    def add_manual_task(self, path: Path, options: Dict[str, Any] = None, increment_counter=False):
        if options is None:
            options = {}

        if not self.worker_thread or not self.worker_thread.is_alive():
            logger.warning("[监控] 处理线程未运行，尝试启动...")
            self.stop_event.clear()
            self.worker_thread = Thread(
                target=self._process_worker, daemon=True, name="RenameWorker"
            )
            self.worker_thread.start()
            self.is_running = True

        logger.debug(f"[手动任务] 添加: {path.name} 参数: {options}")
        if increment_counter:
            with self._count_lock:
                self.logical_pending_count += 1

        seq = next(self._counter)
        self.task_queue.put((self.PRIORITY_MANUAL, seq, (path, options)))

    def _process_worker(self):
        logger.info("[处理线程] 启动成功，等待任务...")
        rename_processor = Rename()
        logger.debug("[处理线程] Rename 处理器已初始化")

        processed_count = 0

        while not self.stop_event.is_set():
            if self._paused:
                time.sleep(1)
                continue

            current_time = time.time()
            if current_time - self.last_cache_clear_time > self.CACHE_CLEAR_INTERVAL:
                self._scheduled_cache_clear()
                self.last_cache_clear_time = current_time

            try:
                # PriorityQueue 会自动按优先级弹出 (priority 越小越先出)
                queue_item = self.task_queue.get(timeout=1)
            except Empty:
                continue

            priority, seq, (file_path, options) = queue_item

            try:
                if not self._wait_for_file_ready(file_path):
                    logger.warning(f"[跳过] 文件无法读取或已消失: {file_path}")
                    self.task_queue.task_done()
                    continue

                self.current_file = file_path.name
                custom_config = {}

                if self.path_map:
                    max_len = 0
                    for root_path, cfg in self.path_map.items():
                        try:
                            if file_path.is_relative_to(root_path):
                                if len(str(root_path)) > max_len:
                                    max_len = len(str(root_path))
                                    custom_config = cfg
                        except Exception:
                            pass

                rename_kwargs = {}
                manual_overrides = options.get('config_overrides', {})
                final_overrides = custom_config.copy()
                if 'path' in final_overrides: del final_overrides['path']
                final_overrides.update(manual_overrides)
                final_overrides = {k: v for k, v in final_overrides.items() if v is not None and v != ""}

                use_ai = bool(cm.get_config("ai_enabled"))
                if "use_ai" in options:
                    use_ai = options["use_ai"]
                if 'is_anime' in options:
                    rename_kwargs['_is_anime'] = options.pop('is_anime')
                if 'is_movie' in options:
                    rename_kwargs['_is_movie'] = options.pop('is_movie')

                rename_kwargs.update(options)
                rename_kwargs.pop("use_ai", None)
                rename_kwargs.pop("config_overrides", None)
                rename_kwargs['config_overrides'] = final_overrides

                logger.info(
                    f"[开始处理] {file_path.name} | Prio: {priority} | AI: {use_ai}"
                )

                rename_processor.process(file_path, use_ai=use_ai, **rename_kwargs)

                with self._count_lock:
                    if self.logical_pending_count > 0:
                        self.logical_pending_count -= 1

                processed_count += 1
                if processed_count % 50 == 0:
                    gc.collect()

            except Exception as e:
                logger.error(f"[处理异常] {file_path.name}: {e}")
            finally:
                self.current_file = None
                self.task_queue.task_done()

    def _wait_for_file_ready(
            self, file_path: Path, timeout=10, check_interval=1.0
    ) -> bool:
        if not file_path.exists():
            return False
        start_time = time.time()
        last_size = -1
        while time.time() - start_time < timeout:
            if self.stop_event.is_set():
                return False
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


monitor_service = MonitorService()
