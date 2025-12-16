from pathlib import Path
from typing import Any, Dict, List, Optional
import math

from nicegui import ui, run
from nicegui.events import GenericEventArguments

from ..element.red import notify, RedButton, RedInput, RedSelect, RedToogle
from .edit_page import edit_page
from ..utils.utils import get_task
from ..rename.process import Rename
from ..utils.path import TASK_PATH, RECORD_PATH
from ..logger import logger
from ..monitor.monitor import monitor_service


class BatchEditDialog(ui.dialog):
    def __init__(self, selected_rows: List[Dict], on_confirm_callback):
        super().__init__()
        self.selected_rows = selected_rows
        self.callback = on_confirm_callback

        # 用于存储批量设置的值
        self.settings = {
            'tmdb_id': None,
            'season_id': None,
            'episode_offset': None,
            'is_anime': None,
            'is_movie': None,
            'use_ai': None,
        }

        with self, ui.card().style('width: 500px; max-width: 90vw'):
            ui.label(f'批量设置 ({len(selected_rows)} 个任务)').classes('text-h6 q-mb-md')
            ui.label('提示: 留空或不选择表示保持原任务的设置').classes('text-caption text-grey q-mb-md')

            with ui.column().classes('w-full q-gutter-y-sm'):
                # 1. TMDB ID
                RedInput(
                    label='指定 TMDB ID (选填)',
                    on_change=lambda e: self.settings.update({'tmdb_id': e.value})
                ).props('clearable').classes('w-full')

                # 2. 季号 & 偏移量
                with ui.row().classes('w-full'):
                    RedInput(
                        label='强制季号 (Season ID)',
                        on_change=lambda e: self.settings.update({'season_id': e.value})
                    ).props('type=number clearable').classes('col q-mr-sm')

                    RedInput(
                        label='集数偏移 (Offset)',
                        on_change=lambda e: self.settings.update({'episode_offset': e.value})
                    ).props('type=number clearable').classes('col')

                ui.separator().classes('q-my-sm')

                # 3. 类型开关
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('是否为动漫')
                    RedToogle(
                        ['保持原样', '是', '否', '自动'],
                        value='保持原样',
                        on_change=lambda e: self.settings.update({'is_anime': e.value})
                    ).props('dense')

                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('是否为电影')
                    RedToogle(
                        ['保持原样', '是', '否', '自动'],
                        value='保持原样',
                        on_change=lambda e: self.settings.update({'is_movie': e.value})
                    ).props('dense')

                with ui.row().classes('items-center justify-between w-full'):
                    ui.label('AI 识别')
                    RedToogle(
                        ['保持原样', '启用', '禁用'],
                        value='保持原样',
                        on_change=lambda e: self.settings.update({'use_ai': e.value})
                    ).props('dense')

            # 按钮区
            with ui.row().classes('w-full justify-end q-mt-lg'):
                RedButton('取消', on_click=self.close).props('outline color=grey')
                RedButton('确认并重试', on_click=self._handle_confirm).classes('q-ml-sm')

    async def _handle_confirm(self):
        self.close()
        final_settings = {
            k: v
            for k, v in self.settings.items()
            if v is not None and v != '保持原样' and v != ''
        }
        await self.callback(self.selected_rows, final_settings)


class TableManager:
    def __init__(self):
        self.cache: Dict[str, Dict] = {}
        self.file_list: List[Path] = []

        self.filter_text = ''
        self.filter_status = '全部'
        self.filter_season = None
        self.table = None
        self.selected_rows = []
        self.selection_label = None

        self.page = 1
        self.page_size = 100
        self.total_items = 0

        self.is_fully_loaded = False # 标记是否已将所有文件载入缓存

    def _scan_files_sync(self):
        if not TASK_PATH.exists():
            return []
        # 这里的 glob 和 stat 是最耗时的
        return sorted(
            TASK_PATH.glob('*.json'),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

    def _read_task_file(self, file_path: Path) -> Optional[Dict]:
        """读取单个文件并格式化，优先使用缓存"""
        uuid = file_path.stem
        if uuid in self.cache:
            return self.cache[uuid]

        try:
            task_data = get_task(uuid)
            if not task_data: return None

            status = '失败' if task_data.get('error') else '成功'
            error_msg = task_data.get('error', '')

            row = {
                'id': uuid, # 用于 key
                'uuid': uuid,
                'path': task_data.get('path', ''),
                'target_path': task_data.get('target_path', ''),
                'name': task_data.get('name', '未识别'),
                'season': task_data.get('season_id', ''),
                'status': status,
                'error_msg': error_msg,
                'is_anime': task_data.get('is_anime', False),
                'is_movie': task_data.get('is_movie', False),
                'ai_used': task_data.get('use_ai', False),
                'tmdb_id': task_data.get('tmdb_id', ''),
                'episode_offset': task_data.get('episode_offset', 0),
                'value': '操作',
                '_mtime': file_path.stat().st_mtime
            }
            self.cache[uuid] = row
            return row
        except Exception:
            return None

    async def load_data(self):
        """异步加载数据，不阻塞主线程"""
        try:
            self.file_list = await run.io_bound(self._scan_files_sync)
            self.is_fully_loaded = False
            self.total_items = len(self.file_list)
        except Exception as e:
            logger.error(f"File list error: {e}")
            self.file_list = []
            self.total_items = 0

    def get_current_page_data(self) -> List[Dict]:
        """获取当前页数据"""

        # 判断是否有过滤条件
        has_filter = (
                bool(self.filter_text) or
                self.filter_status != '全部' or
                bool(self.filter_season)
        )

        if not has_filter:
            # === 模式 A：无过滤 (高性能后端分页) ===
            self.total_items = len(self.file_list)

            # 计算切片
            start = (self.page - 1) * self.page_size
            end = start + self.page_size

            # 越界修正
            if start >= self.total_items:
                start = 0
                end = self.page_size
                self.page = 1

            target_files = self.file_list[start:end]
            rows = []
            for f in target_files:
                r = self._read_task_file(f)
                if r: rows.append(r)

            return rows

        else:
            # === 模式 B：有过滤 (需加载数据后内存过滤) ===

            # 1. 懒加载：如果还没全量加载，现在加载
            if not self.is_fully_loaded:
                # 这里的性能瓶颈：首次搜索时需要读取所有文件
                # 10000 个文件可能需要 2-5 秒
                notify('正在加载所有记录以进行搜索，请稍候...', type='info')
                for f in self.file_list:
                    if f.stem not in self.cache:
                        self._read_task_file(f)
                self.is_fully_loaded = True

            # 2. 内存过滤
            filtered = []
            txt = self.filter_text.lower().strip()
            status = self.filter_status
            season = str(self.filter_season).strip() if self.filter_season else ''

            # 使用 list(self.cache.values()) 可能乱序，最好重新按时间排序
            # 优化：如果 self.file_list 是有序的，按这个顺序去 cache 取比较好
            for f in self.file_list:
                row = self.cache.get(f.stem)
                if not row: continue

                # 文本过滤
                if txt and (txt not in str(row['name']).lower() and txt not in str(row['path']).lower()):
                    continue
                # 状态过滤
                if status != '全部' and row['status'] != status:
                    continue
                # 季号过滤
                if season and str(row['season']).strip() != season:
                    continue

                filtered.append(row)

            self.total_items = len(filtered)

            # 3. 内存分页
            start = (self.page - 1) * self.page_size
            end = start + self.page_size
            return filtered[start:end]

    def filter_data(self):
        refresh_table_view.refresh()

    def update_selection_label(self):
        """更新界面左下角的选中计数"""
        if self.selection_label:
            count = len(self.selected_rows)
            self.selection_label.text = f'已选中: {count}'

    def handle_selection(self, e):
        """处理 Quasar 的选择逻辑"""
        args = e.args

        # 情况1: Quasar 增量更新 (大多数情况)
        if isinstance(args, dict) and 'rows' in args:
            changed_rows = args['rows']
            is_added = args.get('added', True)
            existing_uuids = set(r['uuid'] for r in self.selected_rows)

            if is_added:
                for row in changed_rows:
                    if row['uuid'] not in existing_uuids:
                        self.selected_rows.append(row)
                        existing_uuids.add(row['uuid'])
            else:
                uuids_to_remove = set(r['uuid'] for r in changed_rows)
                self.selected_rows = [
                    r for r in self.selected_rows
                    if r['uuid'] not in uuids_to_remove
                ]

        # 情况2: 全量更新
        elif isinstance(args, list):
            self.selected_rows = args

        # 实时更新左下角文字
        self.update_selection_label()

    async def do_refresh(self):
        try:
            notify('正在刷新列表...')
            await self.load_data()
            refresh_table_view.refresh()
        except Exception:
            pass

    def batch_retry_click(self):
        if not self.selected_rows:
            notify('请先勾选需要重试的任务')
            return
        BatchEditDialog(self.selected_rows, self.execute_batch_process).open()

    async def execute_batch_process(self, rows: List[Dict], settings: Dict):
        if not rows:
            notify("没有需要处理的任务")
            return

        def get_bool_from_text(text):
            if text == '是' or text == '启用': return True
            if text == '否' or text == '禁用': return False
            return None

        batch_tmdb_id = settings.get('tmdb_id')
        batch_season_id = settings.get('season_id')
        batch_offset = settings.get('episode_offset')
        batch_is_anime_text = settings.get('is_anime')
        batch_is_movie_text = settings.get('is_movie')
        batch_use_ai_text = settings.get('use_ai')

        # 注册计数器，让监控页面能看到数量变化
        monitor_service.register_batch_count(len(rows))

        notify(f'已将 {len(rows)} 个任务加入后台队列')

        for row in rows:
            try:
                uuid = row['uuid']
                # 优先读取最新的 task 文件，如果没有则用表格里的旧数据
                task_data = get_task(uuid) or row

                path_str = task_data.get('path')
                if not path_str: continue

                path = Path(path_str)

                # 原有参数
                is_anime_orig = task_data.get('is_anime')
                is_movie_orig = task_data.get('is_movie')
                ai_used_orig = task_data.get('use_ai')
                season_id_orig = task_data.get('season_id')
                offset_orig = task_data.get('episode_offset')

                # 合并逻辑：如果有批量设置则使用，否则使用原参数
                tmdb_id = batch_tmdb_id  # 批量没填就是 None

                season_id = int(batch_season_id) if batch_season_id else season_id_orig
                offset = int(batch_offset) if batch_offset else offset_orig
                if offset is None: offset = 0

                if batch_is_anime_text:
                    is_anime = get_bool_from_text(batch_is_anime_text)
                else:
                    is_anime = is_anime_orig

                if batch_is_movie_text:
                    is_movie = get_bool_from_text(batch_is_movie_text)
                else:
                    is_movie = is_movie_orig

                if batch_use_ai_text:
                    use_ai = get_bool_from_text(batch_use_ai_text)
                else:
                    use_ai = ai_used_orig

                options = {
                    'is_anime': is_anime,
                    'is_movie': is_movie,
                    'use_ai': use_ai,
                    'cus_season_id': season_id,
                    'cus_tmdb_id': tmdb_id,
                    'cus_offset': offset,
                    '_tuuid': uuid,  # 传入 UUID，保证覆盖原来的任务记录
                    '_ai_attempted': False  # 重置 AI 尝试状态
                }

                # 加入队列
                monitor_service.add_manual_task(path, options)

                # [视觉反馈] 临时修改缓存状态，让用户感觉反应很快
                if uuid in self.cache:
                    self.cache[uuid]['status'] = '排队中'
                    self.cache[uuid]['error_msg'] = '等待后台处理...'

            except Exception as e:
                logger.error(f"Batch submit error for {row.get('uuid')}: {e}")

        self.selected_rows = []
        try:
            await self.do_refresh()
        except Exception:
            pass

    def delete_by_uuid(self, uuid: str):
        path1 = TASK_PATH / f'{uuid}.json'
        path2 = RECORD_PATH / f'{uuid}.json'

        if path1.exists(): path1.unlink()
        if path2.exists(): path2.unlink()

        if uuid in self.cache:
            del self.cache[uuid]

        self.file_list = [f for f in self.file_list if f.stem != uuid]
        self.total_items = max(0, self.total_items - 1)

    def batch_delete(self):
        rows = list(self.selected_rows)
        if not rows:
            notify('请先勾选需要删除的任务')
            return

        for row in rows:
            self.delete_by_uuid(row['uuid'])

        notify(f'已删除 {len(rows)} 个任务记录')
        self.refresh_table()

    def refresh_table(self):
        """刷新逻辑：重新扫描文件列表（检测外部变动），清空状态"""
        self.selected_rows = []
        if self.table and self.table.selected:
            self.table.selected.clear()
        self.update_selection_label()

        self.cache.clear()
        self.is_fully_loaded = False

        self.load_data()
        refresh_table_view.refresh()


manager = TableManager()


@ui.refreshable
def refresh_table_view():
    rows_page = manager.get_current_page_data()
    total_pages = math.ceil(manager.total_items / manager.page_size) if manager.page_size > 0 else 1

    columns: List[Dict[str, Any]] = [
        {'name': 'name', 'label': '剧集信息 / 原文件路径', 'field': 'name', 'sortable': True, 'align': 'left'},
        {'name': 'season', 'label': '季度', 'field': 'season', 'sortable': True, 'align': 'center'},
        {'name': 'status', 'label': '状态', 'field': 'status', 'sortable': True, 'align': 'center'},
        {'name': 'tmdb_id', 'label': 'TMDB ID', 'field': 'tmdb_id', 'sortable': True, 'align': 'center'},
        {'name': 'episode_offset', 'label': 'Offset', 'field': 'episode_offset', 'sortable': True, 'align': 'center'},
        {'name': 'ai_used', 'label': 'AI', 'field': 'ai_used', 'sortable': True, 'align': 'center'},
        {'name': 'value', 'label': '操作', 'field': 'value', 'align': 'center'},
    ]

    manager.table = (
        ui.table(columns=columns, rows=rows_page, selection='multiple', row_key='uuid')
        .classes('w-full h-full rounded')
        .style('max-height: 75vh; border-radius: 10px; separator: cell')
        .props('hide-bottom')
    )

    manager.table.on('selection', manager.handle_selection)

    manager.table.add_slot(
        'body-cell-name',
        '''
        <q-td :props="props">
            <div class="column">
                <div class="text-subtitle2 text-weight-bold">{{ props.row.name }}</div>
                <div class="text-caption text-grey-7" style="font-size: 0.75rem; word-break: break-all; line-height: 1.1;">
                    <q-icon name="folder_open" size="xs" class="q-mr-xs"/>
                    <span class="text-grey-8">源:</span> {{ props.row.path }}
                </div>
                <div v-if="props.row.target_path" class="text-caption text-green-8 q-mt-xs" style="font-size: 0.75rem; word-break: break-all; line-height: 1.1;">
                    <q-icon name="output" size="xs" class="q-mr-xs"/>
                    <span class="text-green-9">至:</span> {{ props.row.target_path }}
                </div>
            </div>
        </q-td>
        '''
    )
    manager.table.add_slot(
        'body-cell-status',
        '''
        <q-td :props="props" :class="{
            'bg-green-1 text-green-8': props.value === '成功',
            'bg-red-1 text-red-8': props.value === '失败',
            'bg-blue-1 text-blue-8': props.value === '排队中'
        }">
            <div class="flex items-center justify-center">
                <q-icon :name="props.value === '成功' ? 'check_circle' : (props.value === '排队中' ? 'hourglass_empty' : 'error')" size="xs" class="q-mr-xs"/>
                {{ props.value }}
                <q-tooltip v-if="props.row.error_msg" content-style="font-size: 14px">{{ props.row.error_msg }}</q-tooltip>
            </div>
        </q-td>
        '''
    )
    manager.table.add_slot(
        'body-cell-value',
        """
        <q-td :props="props">
            <div class="row justify-center no-wrap">
                <q-btn flat dense round icon="replay" color="green" @click.stop="$parent.$emit('retry', props)">
                    <q-tooltip>重试</q-tooltip>
                </q-btn>
                <q-btn flat dense round icon="edit" color="blue" @click.stop="$parent.$emit('edit', props)">
                    <q-tooltip>编辑</q-tooltip>
                </q-btn>
                <q-btn flat dense round icon="delete" color="red" @click.stop="$parent.$emit('del', props)">
                    <q-tooltip>删除</q-tooltip>
                </q-btn>
            </div>
        </q-td>
        """
    )

    manager.table.on('retry', lambda ev: handle_retry(ev))
    manager.table.on('edit', lambda ev: handle_edit(ev))
    manager.table.on('del', lambda ev: handle_delete(ev))

    # --- 自定义底部工具栏 ---
    with ui.row().classes('w-full justify-between items-center q-mt-sm q-px-sm'):
        # 左侧：显示选中数量
        # 将这个 Label 赋值给 manager，以便在 selection 事件中动态更新
        manager.selection_label = ui.label(f'已选中: {len(manager.selected_rows)}').classes(
            'text-subtitle2 text-primary font-bold')

        # 右侧：分页控制区域 (总数、每页数量、翻页器)
        with ui.row().classes('items-center q-gutter-x-sm'):
            ui.label(f'总计: {manager.total_items}').classes('text-grey-7 q-mr-md')

            def on_page_size_change(e):
                manager.page_size = e.value
                manager.page = 1
                manager.selected_rows = []
                manager.update_selection_label()  # 清空后更新 Label
                refresh_table_view.refresh()

            def on_page_change(e):
                manager.page = e.value
                manager.selected_rows = []
                manager.update_selection_label()  # 清空后更新 Label
                refresh_table_view.refresh()

            if total_pages > 1:
                ui.pagination(
                    min=1,
                    max=total_pages,
                    value=manager.page,
                    on_change=on_page_change
                ).props('boundary-numbers direction-links input')

            RedSelect(
                options=[100, 200, 500, 1000],
                value=manager.page_size,
                on_change=on_page_size_change,
                label='每页'
            ).props('dense outlined options-dense').classes('w-24')


def create_table():
    def on_text_change(e):
        manager.filter_text = e.value
        manager.page = 1
        refresh_table_view.refresh()

    def on_season_change(e):
        manager.filter_season = e.value
        manager.page = 1
        refresh_table_view.refresh()

    def on_status_change(e):
        manager.filter_status = e.value
        manager.page = 1
        refresh_table_view.refresh()

    with ui.row().classes('w-full items-center q-mb-md justify-between'):
        with ui.row().classes('items-center'):
            RedInput(
                label='搜索 剧名/路径',
                value=manager.filter_text,
                on_change=on_text_change
            ).props('dense outlined clearable debounce=300').classes('w-64 q-mr-md')

            RedInput(
                label='季号',
                value=manager.filter_season,
                on_change=on_season_change
            ).props('dense outlined clearable type=number debounce=300').classes('w-24')

            RedSelect(
                options=['全部', '成功', '失败'],
                value=manager.filter_status,
                label='状态',
                on_change=on_status_change,
            ).props('dense outlined').classes('w-32')

        with ui.row().classes('items-center'):
            RedButton('刷新', on_click=manager.do_refresh).props('outline icon=refresh').classes('q-mr-sm')
            ui.separator().props('vertical').classes('q-mx-sm')
            RedButton('批量重试', on_click=manager.batch_retry_click).props('color=green-6 icon=replay').classes(
                'q-mr-sm')
            RedButton('批量删除', on_click=manager.batch_delete).props('color=red-6 icon=delete')

    refresh_table_view()
    async def init_data():
        try:
            ui.notify('正在加载任务列表...', type='info', position='top')
            await manager.load_data()
            refresh_table_view.refresh()
        except Exception:
            pass


    ui.timer(0, init_data, once=True)


async def handle_edit(ev: GenericEventArguments):
    arg = ev.args
    uuid = arg['row']['uuid']
    await edit_page(uuid)
    manager.refresh_table()


async def handle_retry(ev: GenericEventArguments, is_batch: bool = False):
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    # 读取最新配置
    task_data = get_task(uuid)
    if not task_data:
        task_data = row_data

    path_str = task_data.get('path')
    if not path_str:
        notify('路径无效', type='negative')
        return

    path = Path(path_str)

    # 提取参数
    options = {
        'is_anime': task_data.get('is_anime'),
        'is_movie': task_data.get('is_movie'),
        'use_ai': task_data.get('use_ai'),
        'cus_season_id': task_data.get('season_id'),
        'cus_tmdb_id': task_data.get('tmdb_id'),
        'cus_offset': int(task_data.get('episode_offset', 0) or 0),
        '_tuuid': uuid,
        '_ai_attempted': False
    }

    try:
        if not is_batch:
            # 加入队列
            monitor_service.add_manual_task(path, options, increment_counter=True)

            # 更新缓存状态
            if uuid in manager.cache:
                manager.cache[uuid]['status'] = '排队中'
                manager.cache[uuid]['error_msg'] = '已加入处理队列'

            notify('已加入后台处理队列')
            refresh_table_view.refresh()

    except Exception as e:
        logger.error(f'Retry failed: {e}')
        if not is_batch:
            notify(f'加入队列失败: {e}', type='negative')


def handle_delete(ev: GenericEventArguments, is_notify: bool = True):
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    manager.delete_by_uuid(uuid)

    if is_notify:
        notify('删除任务记录成功!')
    manager.refresh_table()
