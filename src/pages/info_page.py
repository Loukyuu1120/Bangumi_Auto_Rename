import logging
import platform
import requests
import asyncio
import time
from collections import deque
from nicegui import ui

from ..utils.path import TASK_PATH
from ..utils.utils import get_task
from ..monitor.monitor import monitor_service
from ..config.config_manager import cm

LOG_HISTORY = deque(maxlen=15)


class GlobalHistoryHandler(logging.Handler):
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


logging.getLogger().addHandler(GlobalHistoryHandler())


class NiceGuiLogHandler(logging.Handler):
    def __init__(self, log_element, level=logging.NOTSET):
        super().__init__(level)
        self.log_element = log_element
        self.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            if record.name.startswith("nicegui"): return
            msg = self.format(record)
            self.log_element.push(msg)
        except Exception:
            self.handleError(record)


# --- 辅助函数 ---

def get_stats():
    total = 0
    success = 0
    fail = 0
    if TASK_PATH.exists():
        for file in TASK_PATH.glob('*.json'):
            total += 1
            task = get_task(file.stem)
            if task:
                if task.get('error'):
                    fail += 1
                else:
                    success += 1
            else:
                fail += 1
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
    """测试 TMDB 连接并更新 UI"""
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
    """
    智能获取当前生效的监控路径。
    支持读取 monitor_paths (列表) 和 movie_path (字符串) 等多种格式。
    """
    paths = []
    # 1. 直接访问 config 字典，避免 get_config 方法的潜在类型转换问题
    #    并强制处理列表类型
    raw_paths = cm.config.get('monitor_paths', [])

    if raw_paths and isinstance(raw_paths, list):
        for p in raw_paths:
            if p and isinstance(p, str) and p.strip():
                paths.append(p.strip())

    # 3. 最后尝试读取旧的 source_dir
    if not paths:
        val = cm.config.get('source_dir')
        if val: paths.append(str(val))

    # 4. 格式化输出
    if not paths:
        # 调试打印：如果还是空，打印一下当前的 config，方便在控制台排查
        print(f"DEBUG: Config is {cm.config}")
        return "未配置监控路径"

    # 去重并用逗号连接
    unique_paths = list(set(paths))
    return ", ".join(unique_paths)


# --- 主页面逻辑 ---

def info_page():
    # 强制重新加载配置
    try:
        cm.update_config()
    except Exception:
        pass

    total, success, fail = get_stats()
    success_rate = (success / total * 100) if total > 0 else 0

    with ui.column().classes('w-full h-full p-4 gap-4 scroll bg-slate-50'):

        # =================================================
        # 第一栏：业务统计
        # =================================================
        with ui.grid(columns=4).classes('w-full gap-4'):
            info_card('总任务数', total, 'list_alt', 'blue')
            info_card('重命名成功', success, 'check_circle', 'green', f'成功率: {success_rate:.1f}%')
            info_card('失败/异常', fail, 'warning', 'red')
            info_card('运行环境', f"Py {platform.python_version()}", 'memory', 'purple', platform.system())

        # =================================================
        # 第二栏：服务与监控配置
        # =================================================
        with ui.grid(columns=2).classes('w-full gap-4'):

            # --- 监控目录状态 ---
            with ui.card().classes('w-full p-0 no-shadow border-[1px]'):
                with ui.row().classes('w-full p-2 bg-grey-1 border-b-[1px] items-center gap-2'):
                    ui.icon('folder_open', color='indigo')
                    ui.label('监控目录配置').classes('font-bold text-xs text-grey-8')
                    monitor_status_icon = ui.icon('circle', size='xs', color='grey-4')

                with ui.row().classes('w-full p-3 items-center justify-between'):
                    with ui.column().classes('gap-1 w-3/4'):
                        ui.label('监控路径').classes('text-xs text-grey-5')

                        # 初始化标签，设置一个唯一的 ID 方便调试（内部逻辑）
                        monitor_path_label = ui.label('读取中...').classes(
                            'text-sm font-bold text-grey-8 break-all leading-tight')

                    monitor_status_text = ui.label('检查中...').classes(
                        'text-xs font-bold text-grey-5 bg-grey-2 px-2 py-1 rounded')

            # --- TMDB 服务状态 ---
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

        # =================================================
        # 第三栏：队列与日志
        # =================================================
        with ui.row().classes('w-full flex-nowrap gap-4 h-[400px]'):

            # --- 左侧：正在处理 & 等待队列 ---
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

            # --- 右侧：实时日志 ---
            with ui.card().classes('w-2/3 h-full p-0 no-shadow border-[1px] flex flex-col'):
                with ui.row().classes('w-full p-2 bg-grey-2 items-center border-b-[1px]'):
                    ui.icon('terminal', color='grey-8')
                    ui.label('实时日志').classes('font-bold text-grey-8')
                    ui.label('(Live)').classes('text-xs text-green-6 ml-auto font-mono')

                log_view = ui.log(max_lines=500).classes(
                    'w-full flex-grow p-2 font-mono text-xs bg-[#1e1e1e] text-green-400')
                for history_msg in LOG_HISTORY:
                    log_view.push(history_msg)

                root_logger = logging.getLogger()
                gui_handler = NiceGuiLogHandler(log_view)
                root_logger.addHandler(gui_handler)
                ui.context.client.on_disconnect(lambda: root_logger.removeHandler(gui_handler))

        # =================================================
        # 定时刷新逻辑
        # =================================================
        def update_ui_state():
            # 1. 更新当前处理文件
            cur = monitor_service.current_file
            if cur:
                current_file_label.text = cur
                current_file_label.classes(remove='text-grey-5 italic', add='text-blue-7 font-bold')
            else:
                current_file_label.text = '等待任务...'
                current_file_label.classes(remove='text-blue-7 font-bold', add='text-grey-5 italic')

            # 2. 更新队列列表
            items = monitor_service.get_queue_list()
            queue_count_badge.text = str(len(items))
            queue_scroll.clear()
            with queue_scroll:
                if not items:
                    with ui.column().classes('w-full h-full items-center justify-center text-grey-4 q-mt-md'):
                        ui.label('队列空闲').classes('text-xs')
                else:
                    with ui.list().props('dense separator').classes('w-full'):
                        for idx, filename in enumerate(items):
                            with ui.item():
                                with ui.item_section().props('avatar min-width=20px'):
                                    ui.label(str(idx + 1)).classes('text-grey-5 text-xs')
                                with ui.item_section():
                                    ui.label(filename).classes('text-xs break-all')

            # 3. 更新监控目录路径
            # 获取路径字符串 (这里一定会调用打印日志的那个函数)
            active_paths = get_active_monitor_paths()

            # 无论是否相等，都强制赋值，防止 UI 状态不同步
            monitor_path_label.text = active_paths
            # 显式调用 update() 确保界面刷新
            monitor_path_label.update()

            # 4. 检查监控服务是否存活
            is_running = False
            # 逻辑：如果配置了路径 并且 (服务在运行 或者 监控开关是开的)
            has_paths = (active_paths != "未配置" and active_paths != "")
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

        # 启动定时器
        ui.timer(1.0, update_ui_state)
