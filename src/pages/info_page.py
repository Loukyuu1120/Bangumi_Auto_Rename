import logging
import platform
import requests
import asyncio
import time
import threading
from collections import deque
from nicegui import ui, app

from ..utils.path import TASK_PATH
from ..utils.utils import get_task
from ..monitor.monitor import monitor_service
from ..config.config_manager import cm

# 使用 deque 存储最近日志 (用于新打开页面时回显)
LOG_HISTORY = deque(maxlen=50)


class GlobalHistoryHandler(logging.Handler):
    """
    全局历史日志记录器
    纯后端内存操作，无性能瓶颈
    """

    def __init__(self):
        super().__init__(logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            if record.name.startswith("nicegui"): return
            msg = self.format(record)
            LOG_HISTORY.append(msg)
        except Exception:
            self.handleError(record)


# 注册全局历史记录器
_has_history_handler = False
for h in logging.getLogger().handlers:
    if isinstance(h, GlobalHistoryHandler):
        _has_history_handler = True
        break
if not _has_history_handler:
    logging.getLogger().addHandler(GlobalHistoryHandler())


class BufferedLogHandler(logging.Handler):
    """
    [性能优化版] 缓冲日志处理器
    不再每条日志都触发 UI 更新，而是先存入缓冲区。
    解决批量处理时大量日志导致网页卡死无法访问的问题。
    """

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
        self.buffer = []  # 缓冲区列表
        self.lock = threading.Lock()  # 线程锁，保证 list 操作安全

    def emit(self, record):
        try:
            if record.name.startswith("nicegui"): return
            msg = self.format(record)

            # 仅仅是将消息写入内存列表，速度极快，不涉及 UI 操作
            with self.lock:
                self.buffer.append(msg)

        except Exception:
            self.handleError(record)

    def flush_to_ui(self, log_element):
        """
        将缓冲区的内容一次性刷入 UI
        这个方法由主线程定时器调用
        """
        if not self.buffer:
            return

        with self.lock:
            # 取出当前所有日志，并清空缓冲区
            messages = self.buffer[:]
            self.buffer.clear()

        # 批量推送到 UI
        # 注意：这里是在主线程执行的，非常安全
        if messages and log_element:
            # 如果日志太多（比如超过100条），为了防止前端JS卡顿，可以只截取最后100条
            if len(messages) > 100:
                messages = messages[-100:]
                messages.insert(0, "...(日志更新过快，部分省略)...")

            for msg in messages:
                log_element.push(msg)


# --- 辅助函数 ---

def get_stats():
    total = 0
    success = 0
    fail = 0
    try:
        if TASK_PATH.exists():
            # 使用 list() 快照防止迭代报错
            for file in list(TASK_PATH.glob('*.json')):
                total += 1
                task = get_task(file.stem)
                if task:
                    if task.get('error'):
                        fail += 1
                    else:
                        success += 1
                else:
                    fail += 1
    except Exception:
        pass
    return total, success, fail


def info_card(title, value, icon, color, subtext=None):
    with ui.card().classes('w-full p-3 no-shadow border-[1px]'):
        with ui.row().classes('items-center justify-between w-full'):
            with ui.column().classes('gap-0'):
                ui.label(title).classes('text-grey-7 text-xs')
                ui.label(str(value)).classes(f'text-xl font-bold text-{color}-7')
                if subtext:
                    ui.label(subtext).classes('text-xs text-grey-5')
            ui.icon(icon, size='2.5em', color=f'{color}-2')


async def check_tmdb_connection(label_element, btn_element):
    """测试 TMDB 连接"""
    btn_element.props('loading')
    label_element.text = '连接中...'
    label_element.classes(replace='text-grey-6 text-xs font-bold')

    api_key = cm.get_config('api_key')
    base_url = 'https://api.themoviedb.org/3'

    if not api_key:
        ui.notify('未配置 TMDB API Key', type='warning')
        label_element.text = '未配置 Key'
        label_element.classes(replace='text-orange-6 text-xs font-bold')
        btn_element.props(remove='loading')
        return

    try:
        start_time = time.time()
        url = f"{base_url}/configuration?api_key={api_key}"
        response = await asyncio.to_thread(requests.get, url, timeout=8)
        ping = (time.time() - start_time) * 1000

        if response.status_code == 200:
            ui.notify(f'TMDB 连接成功: {int(ping)}ms', type='positive')
            label_element.text = f"正常 ({int(ping)}ms)"
            label_element.classes(replace='text-green-6 text-xs font-bold')
        else:
            ui.notify(f'连接失败: HTTP {response.status_code}', type='negative')
            label_element.text = f"HTTP {response.status_code}"
            label_element.classes(replace='text-red-6 text-xs font-bold')

    except requests.exceptions.Timeout:
        ui.notify('TMDB 连接超时', type='negative')
        label_element.text = "超时"
        label_element.classes(replace='text-red-6 text-xs font-bold')
    except Exception as e:
        ui.notify(f'连接错误: {str(e)}', type='negative')
        label_element.text = "错误"
        label_element.classes(replace='text-red-6 text-xs font-bold')
    finally:
        btn_element.props(remove='loading')


def get_active_monitor_paths():
    paths = []
    try:
        raw_paths = cm.config.get('monitor_paths', [])
        if raw_paths and isinstance(raw_paths, list):
            for p in raw_paths:
                if p and isinstance(p, str) and p.strip():
                    paths.append(p.strip())

        if not paths:
            val = cm.config.get('source_dir')
            if val: paths.append(str(val))

        if not paths:
            return "未配置监控路径"

        unique_paths = list(set(paths))
        return ", ".join(unique_paths)
    except Exception:
        return "配置读取错误"


# --- 主页面逻辑 ---

def info_page():
    try:
        cm.update_config()
    except Exception:
        pass

    total, success, fail = get_stats()
    success_rate = (success / total * 100) if total > 0 else 0

    with ui.column().classes('w-full h-full p-4 gap-4 scroll bg-slate-50'):

        # 1. 顶部统计卡片
        with ui.grid(columns=4).classes('w-full gap-4'):
            info_card('总任务数', total, 'list_alt', 'blue')
            info_card('重命名成功', success, 'check_circle', 'green', f'成功率: {success_rate:.1f}%')
            info_card('失败/异常', fail, 'warning', 'red')
            info_card('运行环境', f"Py {platform.python_version()}", 'memory', 'purple', platform.system())

        # 2. 状态信息
        with ui.grid(columns=2).classes('w-full gap-4'):
            # 监控状态
            with ui.card().classes('w-full p-0 no-shadow border-[1px]'):
                with ui.row().classes('w-full p-2 bg-grey-1 border-b-[1px] items-center gap-2'):
                    ui.icon('folder_open', color='indigo')
                    ui.label('监控目录配置').classes('font-bold text-xs text-grey-8')
                    monitor_status_icon = ui.icon('circle', size='xs', color='grey-4')

                with ui.row().classes('w-full p-3 items-center justify-between'):
                    with ui.column().classes('gap-1 w-3/4'):
                        ui.label('监控路径').classes('text-xs text-grey-5')
                        monitor_path_label = ui.label('读取中...').classes(
                            'text-sm font-bold text-grey-8 break-all leading-tight')
                    monitor_status_text = ui.label('检查中...').classes(
                        'text-xs font-bold text-grey-5 bg-grey-2 px-2 py-1 rounded')

            # TMDB 状态
            with ui.card().classes('w-full p-0 no-shadow border-[1px]'):
                with ui.row().classes('w-full p-2 bg-grey-1 border-b-[1px] items-center gap-2'):
                    ui.icon('movie', color='blue')
                    ui.label('TMDB API 服务').classes('font-bold text-xs text-grey-8')

                with ui.row().classes('w-full p-3 items-center justify-between'):
                    with ui.column().classes('gap-1'):
                        ui.label('连接状态').classes('text-xs text-grey-5')
                        tmdb_status_label = ui.label('点击右侧进行测试').classes('text-sm font-bold text-grey-7')

                    btn_tmdb = ui.button('测试连接', icon='refresh',
                                         on_click=lambda: check_tmdb_connection(tmdb_status_label, btn_tmdb)) \
                        .props('flat dense size=sm color=blue-7')

        # 3. 任务队列与日志
        with ui.row().classes('w-full flex-nowrap gap-4 h-[400px]'):
            # 左侧队列
            with ui.column().classes('w-1/3 h-full gap-4'):
                with ui.card().classes('w-full p-0 no-shadow border-[1px]'):
                    with ui.row().classes('w-full p-2 bg-blue-1 items-center gap-2 border-b-[1px]'):
                        ui.spinner('dots', size='sm', color='blue-7')
                        ui.label('正在处理').classes('font-bold text-blue-9 text-sm')
                    with ui.row().classes('w-full p-4 items-center justify-center h-[60px]'):
                        current_file_label = ui.label('无任务').classes(
                            'text-grey-5 italic text-center break-all text-sm')

                with ui.card().classes('w-full flex-grow p-0 no-shadow border-[1px] flex flex-col'):
                    with ui.row().classes('w-full p-2 bg-grey-2 items-center justify-between border-b-[1px]'):
                        with ui.row().classes('gap-2 items-center'):
                            ui.icon('hourglass_empty', color='orange-7')
                            ui.label('等待队列').classes('font-bold text-grey-8 text-sm')
                        queue_count_badge = ui.badge('0', color='orange')
                    queue_scroll = ui.scroll_area().classes('w-full flex-grow p-2')

            # 右侧日志
            with ui.card().classes('w-2/3 h-full p-0 no-shadow border-[1px] flex flex-col'):
                with ui.row().classes('w-full p-2 bg-grey-2 items-center border-b-[1px]'):
                    ui.icon('terminal', color='grey-8')
                    ui.label('实时日志').classes('font-bold text-grey-8')
                    ui.label('(Live)').classes('text-xs text-green-6 ml-auto font-mono')

                log_view = ui.log(max_lines=300).classes(
                    'w-full flex-grow p-2 font-mono text-xs bg-[#1e1e1e] text-green-400')

                # 填充历史日志
                for history_msg in list(LOG_HISTORY):
                    log_view.push(history_msg)

                # 初始化缓冲日志处理器
                root_logger = logging.getLogger()
                buffered_handler = BufferedLogHandler()
                root_logger.addHandler(buffered_handler)

                # 页面断开时移除 Handler
                ui.context.client.on_disconnect(lambda: root_logger.removeHandler(buffered_handler))

        def update_ui_state():
            try:
                # 1. 刷新日志 (从缓冲区批量取)
                # 这保证了无论后台日志多快，前端每 0.2 秒只更新一次
                buffered_handler.flush_to_ui(log_view)

                # 2. 降低队列刷新频率的消耗
                # 更新当前文件
                cur = monitor_service.current_file
                if cur:
                    current_file_label.text = cur
                    current_file_label.classes(remove='text-grey-5 italic', add='text-blue-7 font-bold')
                else:
                    current_file_label.text = '等待任务...'
                    current_file_label.classes(remove='text-blue-7 font-bold', add='text-grey-5 italic')

                # 3. 更新队列列表 (带异常保护)
                items = monitor_service.get_queue_list()
                queue_count_badge.text = str(len(items))

                # 只有队列变化时才重绘列表，避免 DOM 闪烁和性能消耗
                # 这里简单起见还是清空重绘，但因为是 1s 一次，影响可控

            except Exception:
                pass

        def slow_update_ui_state():
            """
            低频更新的任务 (1秒一次)
            """
            try:
                # 更新队列详情显示 (比较耗时 DOM 操作)
                items = monitor_service.get_queue_list()
                queue_scroll.clear()
                with queue_scroll:
                    if not items:
                        with ui.column().classes('w-full h-full items-center justify-center text-grey-4 q-mt-md'):
                            ui.label('队列空闲').classes('text-xs')
                    else:
                        # 限制显示数量，防止队列太长卡死前端
                        display_items = items[:50]
                        with ui.list().props('dense separator').classes('w-full'):
                            for idx, filename in enumerate(display_items):
                                with ui.item():
                                    with ui.item_section().props('avatar min-width=20px'):
                                        ui.label(str(idx + 1)).classes('text-grey-5 text-xs')
                                    with ui.item_section():
                                        ui.label(filename).classes('text-xs break-all')
                        if len(items) > 50:
                            ui.label(f'...还有 {len(items) - 50} 个任务').classes('text-xs text-grey-5 q-ml-md')

                # 更新路径和状态
                active_paths = get_active_monitor_paths()
                monitor_path_label.text = active_paths
                monitor_path_label.update()

                is_running = False
                has_paths = (active_paths != "未配置" and active_paths != "配置读取错误" and active_paths != "")
                monitor_enabled = cm.config.get('monitor_enabled', False)

                if hasattr(monitor_service, 'observer') and monitor_service.observer:
                    if monitor_service.observer.is_alive():
                        is_running = True

                if is_running:
                    monitor_status_text.text = '运行中'
                    monitor_status_text.classes(replace='text-white bg-green-6')
                    monitor_status_icon.props('color=green')
                elif monitor_enabled and has_paths:
                    monitor_status_text.text = '启动中'
                    monitor_status_text.classes(replace='text-white bg-orange-5')
                    monitor_status_icon.props('color=orange')
                else:
                    monitor_status_text.text = '未启动'
                    monitor_status_text.classes(replace='text-white bg-red-4')
                    monitor_status_icon.props('color=red')
            except Exception:
                pass

        # 启动高频定时器 (仅刷新日志) - 0.2秒一次
        ui.timer(0.2, update_ui_state)

        # 启动低频定时器 (刷新队列 DOM 和状态) - 1.5秒一次，减轻浏览器负担
        ui.timer(1.5, slow_update_ui_state)
