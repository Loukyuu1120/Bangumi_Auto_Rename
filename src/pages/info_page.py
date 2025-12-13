import platform
import time
import asyncio
import requests
from nicegui import ui

from ..utils.path import TASK_PATH
from ..utils.utils import get_task
from ..monitor.monitor import monitor_service
from ..config.config_manager import cm
from ..logger import ui_log_history


class SystemMonitorPage:
    def __init__(self):
        """初始化页面组件引用"""
        self.monitor_path_label = None
        self.monitor_status_text = None
        self.monitor_status_icon = None
        self.tmdb_status_label = None
        self.current_file_label = None
        self.queue_count_badge = None
        self.queue_scroll = None
        self.log_scroll = None
        self.log_container = None

        # 缓存
        self._last_queue_signature = None
        self._last_paths_signature = None

        # 构建 UI
        self.build_ui()

        # 启动定时器
        ui.timer(2.0, self.update_status_indicators)
        ui.timer(1.0, self.update_queue_display)
        ui.timer(0.1, self.refresh_log_view, once=True)

    # --- 静态辅助方法 ---
    @staticmethod
    def get_stats():
        total = 0
        success = 0
        fail = 0
        try:
            if TASK_PATH.exists():
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

    @staticmethod
    def get_active_monitor_paths():
        """
        获取当前配置的监控路径列表
        兼容：
        1. ['/path/a', '/path/b'] (旧格式)
        2. [{'path': '/path/a', ...}, {'path': '/path/b', ...}] (新格式)
        """
        paths = []
        try:
            raw_paths = cm.config.get('monitor_paths', [])
            if raw_paths and isinstance(raw_paths, list):
                for p in raw_paths:
                    # 情况1: 旧格式字符串
                    if isinstance(p, str) and p.strip():
                        paths.append(p.strip())
                    # 情况2: 新格式字典
                    elif isinstance(p, dict) and p.get('path'):
                        path_str = p.get('path')
                        if isinstance(path_str, str) and path_str.strip():
                            paths.append(path_str.strip())

            if not paths:
                return []

            unique_paths = sorted(list(set(paths)))
            return unique_paths
        except Exception:
            return []

    # --- UI 构建组件 ---
    def info_card(self, title, value, icon, color, subtext=None):
        with ui.card().classes('w-full p-3 no-shadow border-[1px]'):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.column().classes('gap-0'):
                    ui.label(title).classes('text-grey-7 text-xs')
                    ui.label(str(value)).classes(f'text-xl font-bold text-{color}-7')
                    if subtext: ui.label(subtext).classes('text-xs text-grey-5')
                ui.icon(icon, size='2.5em', color=f'{color}-2')

    # --- 核心 UI 构建 ---
    def build_ui(self):
        try:
            cm.update_config()
        except Exception:
            pass

        total, success, fail = self.get_stats()
        success_rate = (success / total * 100) if total > 0 else 0

        with ui.column().classes('w-full h-full p-4 gap-4 scroll bg-slate-50'):
            # 1. 顶部统计卡片
            with ui.grid(columns=4).classes('w-full gap-4'):
                self.info_card('总任务数', total, 'list_alt', 'blue')
                self.info_card('重命名成功', success, 'check_circle', 'green', f'成功率: {success_rate:.1f}%')
                self.info_card('失败/异常', fail, 'warning', 'red')
                self.info_card('运行环境', f"Py {platform.python_version()}", 'memory', 'purple', platform.system())

            # 2. 状态信息
            with ui.grid(columns=2).classes('w-full gap-4'):
                # 监控卡片
                with ui.card().classes('w-full p-0 no-shadow border-[1px] flex flex-col'):
                    with ui.row().classes('w-full p-2 bg-grey-1 border-b-[1px] items-center gap-2'):
                        ui.icon('folder_open', color='indigo')
                        ui.label('监控目录配置').classes('font-bold text-xs text-grey-8')
                        self.monitor_status_icon = ui.icon('circle', size='xs', color='grey-4')

                    # 使用 flex-grow 让内容区填满，并处理溢出
                    with ui.row().classes('w-full p-3 items-start justify-between flex-grow'):
                        with ui.column().classes('w-3/4 gap-1'):
                            ui.label('监控路径列表').classes('text-xs text-grey-5')
                            # 使用 white-space: pre-wrap 支持换行，限制高度并允许滚动
                            self.monitor_path_label = ui.label('读取中...').style(
                                'white-space: pre-wrap; max-height: 80px; overflow-y: auto; display: block; width: 100%;'
                            ).classes('text-sm font-bold text-grey-8 leading-tight')

                        with ui.column().classes('items-end gap-1'):
                            self.monitor_status_text = ui.label('检查中...').classes(
                                'text-xs font-bold text-grey-5 bg-grey-2 px-2 py-1 rounded')
                            # 显示模式 (Inotify/Polling)
                            monitor_mode = cm.config.get('monitor_mode', 'fast')
                            mode_text = "高效模式" if monitor_mode == 'fast' else "兼容模式"
                            ui.label(mode_text).classes('text-[10px] text-grey-4')

                # TMDB 卡片
                with ui.card().classes('w-full p-0 no-shadow border-[1px]'):
                    with ui.row().classes('w-full p-2 bg-grey-1 border-b-[1px] items-center gap-2'):
                        ui.icon('movie', color='blue')
                        ui.label('TMDB API 服务').classes('font-bold text-xs text-grey-8')
                    with ui.row().classes('w-full p-3 items-center justify-between'):
                        with ui.column().classes('gap-1'):
                            ui.label('连接状态').classes('text-xs text-grey-5')
                            self.tmdb_status_label = ui.label('点击右侧进行测试').classes(
                                'text-sm font-bold text-grey-7')

                        # 传入 self.tmdb_status_label 和 按钮本身
                        btn_tmdb = ui.button('测试连接', icon='refresh',
                                             on_click=lambda: self.check_tmdb_connection(btn_tmdb)) \
                            .props('flat dense size=sm color=blue-7')

            # 3. 任务队列与日志
            with ui.row().classes('w-full flex-nowrap gap-4 h-[400px]'):
                # --- 左侧：队列 ---
                with ui.column().classes('w-1/3 h-full gap-4'):
                    with ui.card().classes('w-full p-0 no-shadow border-[1px]'):
                        with ui.row().classes('w-full p-2 bg-blue-1 items-center gap-2 border-b-[1px]'):
                            ui.spinner('dots', size='sm', color='blue-7')
                            ui.label('正在处理').classes('font-bold text-blue-9 text-sm')
                        with ui.row().classes('w-full p-4 items-center justify-center h-[60px]'):
                            self.current_file_label = ui.label('无任务').classes(
                                'text-grey-5 italic text-center break-all text-sm line-clamp-2')

                    with ui.card().classes('w-full flex-grow p-0 no-shadow border-[1px] flex flex-col'):
                        with ui.row().classes('w-full p-2 bg-grey-2 items-center justify-between border-b-[1px]'):
                            with ui.row().classes('gap-2 items-center'):
                                ui.icon('hourglass_empty', color='orange-7')
                                ui.label('等待队列').classes('font-bold text-grey-8 text-sm')
                                self.queue_count_badge = ui.badge('0', color='orange')
                            self.queue_scroll = ui.scroll_area().classes('w-full flex-grow p-2')

                # --- 右侧：日志 ---
                with ui.card().classes('w-2/3 h-full p-0 no-shadow border-[1px] flex flex-col'):
                    with ui.row().classes('w-full p-2 bg-grey-2 items-center justify-between border-b-[1px]'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('terminal', color='grey-8')
                            ui.label('系统日志').classes('font-bold text-grey-8')

                        with ui.row().classes('items-center gap-2'):
                            ui.button('清空', icon='delete_sweep', on_click=self.clear_log_view).props(
                                'flat dense size=sm color=grey-7')
                            ui.button('刷新', icon='refresh', on_click=self.refresh_log_view).props(
                                'unelevated dense size=sm color=blue-7')

                    self.log_scroll = ui.scroll_area().classes('w-full flex-grow bg-[#1e1e1e] p-2')
                    with self.log_scroll:
                        self.log_container = ui.column().classes('w-full gap-0.5')

    async def check_tmdb_connection(self, btn_element):
        """测试 TMDB 连接"""
        btn_element.props('loading')
        self.tmdb_status_label.text = '连接中...'
        self.tmdb_status_label.classes(replace='text-grey-6 text-xs font-bold')

        api_key = cm.get_config('api_key')
        base_url = 'https://api.themoviedb.org/3'

        if not api_key:
            ui.notify('未配置 TMDB API Key', type='warning')
            self.tmdb_status_label.text = '未配置 Key'
            self.tmdb_status_label.classes(replace='text-orange-6 text-xs font-bold')
            btn_element.props(remove='loading')
            return

        try:
            start_time = time.time()
            url = f"{base_url}/configuration?api_key={api_key}"
            response = await asyncio.to_thread(requests.get, url, timeout=8)
            ping = (time.time() - start_time) * 1000

            if response.status_code == 200:
                ui.notify(f'TMDB 连接成功: {int(ping)}ms', type='positive')
                self.tmdb_status_label.text = f"正常 ({int(ping)}ms)"
                self.tmdb_status_label.classes(replace='text-green-6 text-xs font-bold')
            else:
                ui.notify(f'连接失败: HTTP {response.status_code}', type='negative')
                self.tmdb_status_label.text = f"HTTP {response.status_code}"
                self.tmdb_status_label.classes(replace='text-red-6 text-xs font-bold')
        except Exception as e:
            ui.notify(f'连接错误: {str(e)}', type='negative')
            self.tmdb_status_label.text = "错误"
            self.tmdb_status_label.classes(replace='text-red-6 text-xs font-bold')
        finally:
            btn_element.props(remove='loading')

    def clear_log_view(self):
        if self.log_container:
            self.log_container.clear()
        ui_log_history.clear()
        ui.notify('日志已清空', position='top')

    async def refresh_log_view(self):
        """刷新日志视图 (Async)"""
        if not self.log_container: return

        self.log_container.clear()
        logs = list(ui_log_history)

        with self.log_container:
            for msg in logs:
                color_class = 'text-green-400'
                u_msg = msg.upper()
                if 'DEBUG' in u_msg:
                    color_class = 'text-gray-400'
                elif 'WARN' in u_msg:
                    color_class = 'text-orange-400'
                elif 'ERROR' in u_msg or 'CRITICAL' in u_msg:
                    color_class = 'text-red-500 font-bold'

                ui.label(msg).classes(f'text-xs font-mono {color_class} break-all leading-tight')

        await asyncio.sleep(0.1)

        if self.log_scroll:
            self.log_scroll.scroll_to(percent=1.0)

    def update_status_indicators(self):
        try:
            # 1. 更新监控路径显示
            paths_list = self.get_active_monitor_paths()

            # 使用签名检测是否有变化，减少重绘（虽然这里只是更新text）
            current_paths_sig = str(paths_list)
            if self.monitor_path_label and current_paths_sig != self._last_paths_signature:
                if not paths_list:
                    self.monitor_path_label.text = "未配置监控路径"
                    self.monitor_path_label.classes(replace='text-sm italic text-orange-5')
                else:
                    # 将列表转换为换行符分隔的字符串，配合 white-space: pre-wrap 显示
                    display_text = "\n".join([f"• {p}" for p in paths_list])
                    self.monitor_path_label.text = display_text
                    self.monitor_path_label.classes(replace='text-xs font-mono text-grey-8 leading-tight')
                self._last_paths_signature = current_paths_sig

            # 2. 更新运行状态
            is_running = False
            monitor_enabled = cm.config.get('monitor_enabled', False)

            if hasattr(monitor_service, 'observer') and monitor_service.observer:
                if monitor_service.observer.is_alive(): is_running = True

            if self.monitor_status_text and self.monitor_status_icon:
                if is_running:
                    self.monitor_status_text.text = '运行中'
                    self.monitor_status_text.classes(replace='text-white bg-green-6 px-2 py-1 rounded shadow-sm')
                    self.monitor_status_icon.props('color=green')
                elif monitor_enabled and paths_list:
                    self.monitor_status_text.text = '启动中'
                    self.monitor_status_text.classes(replace='text-white bg-orange-5 px-2 py-1 rounded')
                    self.monitor_status_icon.props('color=orange')
                elif not monitor_enabled:
                    self.monitor_status_text.text = '已禁用'
                    self.monitor_status_text.classes(replace='text-white bg-grey-5 px-2 py-1 rounded')
                    self.monitor_status_icon.props('color=grey')
                else:
                    self.monitor_status_text.text = '未启动'
                    self.monitor_status_text.classes(replace='text-white bg-red-4 px-2 py-1 rounded')
                    self.monitor_status_icon.props('color=red')
        except Exception:
            pass

    def update_queue_display(self):
        try:
            # 1. 更新当前任务
            cur = monitor_service.current_file
            if self.current_file_label:
                if cur:
                    self.current_file_label.text = cur
                    self.current_file_label.classes(remove='text-grey-5 italic', add='text-blue-7 font-bold')
                else:
                    self.current_file_label.text = '等待任务...'
                    self.current_file_label.classes(remove='text-blue-7 font-bold', add='text-grey-5 italic')

            # 2. 更新列表
            items = monitor_service.get_queue_list()
            if self.queue_count_badge:
                self.queue_count_badge.text = str(len(items))

            # 简单的差异检测优化
            current_signature = f"{len(items)}_{items[0] if items else ''}"
            if current_signature == self._last_queue_signature:
                return
            self._last_queue_signature = current_signature

            if self.queue_scroll:
                self.queue_scroll.clear()
                with self.queue_scroll:
                    if not items:
                        with ui.column().classes('w-full h-full items-center justify-center text-grey-4 q-mt-md'):
                            ui.label('队列空闲').classes('text-xs')
                    else:
                        display_items = items[:50]
                        with ui.list().props('dense separator').classes('w-full'):
                            for idx, filename in enumerate(display_items):
                                with ui.item():
                                    with ui.item_section().props('avatar min-width=20px'):
                                        ui.label(str(idx + 1)).classes('text-grey-5 text-xs')
                                    with ui.item_section():
                                        ui.label(filename).classes('text-xs break-all')
                        if len(items) > 50:
                            ui.label(f'...还有 {len(items) - 50} 个任务').classes('text-xs text-grey-5 q-ml-md q-my-sm')
        except Exception:
            pass


def info_page():
    """
    外部调用的入口函数。
    实例化 SystemMonitorPage 类，它会自动在当前上下文构建 UI。
    """
    SystemMonitorPage()
