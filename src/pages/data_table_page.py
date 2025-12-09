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
        self.all_rows = []
        self.filter_text = ''
        self.filter_status = '全部'
        self.filter_season = None
        self.table = None
        self.selected_rows = []

        # 引用 UI 元素以便直接更新文本
        self.selection_label = None

        # --- 分页参数 ---
        self.page = 1
        self.page_size = 100
        self.total_items = 0

    def load_data(self):
        """加载数据并应用当前的过滤器"""
        rows = []
        if not TASK_PATH.exists():
            self.all_rows = []
            self.filter_data()
            return

        sorted_files = sorted(
            TASK_PATH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
        )
        for index, i in enumerate(sorted_files):
            # 忽略非 json 文件
            if i.suffix.lower() != '.json':
                continue

            try:
                task_data = get_task(i.stem)

                # 如果文件存在但内容为空或解析后为None
                if not task_data:
                    raise ValueError("文件内容为空")

                if task_data.get('error'):
                    status = '失败'
                    error_msg = task_data['error']
                else:
                    status = '成功'
                    error_msg = ''

                source_path = task_data.get('path', '')
                target_path = task_data.get('target_path', '')
                tmdb_id = task_data.get('tmdb_id', '')

                ai_used = task_data.get('use_ai', False)

                rows.append(
                    {
                        'id': index,
                        'uuid': task_data.get('uuid', i.stem),
                        'path': source_path,
                        'target_path': target_path,
                        'name': task_data.get('name', '未识别'),
                        'season': task_data.get('season_id', ''),
                        'status': status,
                        'error_msg': error_msg,
                        'is_anime': task_data.get('is_anime', False),
                        'is_movie': task_data.get('is_movie', False),
                        'ai_used': ai_used,
                        'tmdb_id': tmdb_id,
                        'episode_offset': task_data.get('episode_offset', 0),
                        'value': '操作',
                    }
                )
            except Exception as e:
                logger.error(f"Error loading task {i}: {e}")
                rows.append({
                    'id': index,
                    'uuid': i.stem,
                    'path': '文件损坏或格式错误',
                    'target_path': '',
                    'name': f'无法读取的任务 ({i.name})',
                    'season': '',
                    'status': '失败',
                    'error_msg': f"文件读取错误: {str(e)}。请尝试删除此记录。",
                    'is_anime': False,
                    'is_movie': False,
                    'ai_used': False,
                    'tmdb_id': '',
                    'episode_offset': 0,
                    'value': '操作',
                })

        self.all_rows = rows
        self.filter_data()

    def get_filtered_rows(self):
        """纯计算：根据当前条件返回过滤后的数据列表（不分页）"""
        filtered = []

        # 预处理条件
        txt = str(self.filter_text).lower().strip() if self.filter_text else ''
        status = self.filter_status
        season = str(self.filter_season).strip() if (
                self.filter_season is not None and str(self.filter_season).strip()) else ''

        for row in self.all_rows:
            # 1. 文本
            if txt:
                r_name = str(row.get('name') or '').lower()
                r_path = str(row.get('path') or '').lower()
                if txt not in r_name and txt not in r_path:
                    continue

            # 2. 状态
            if status != '全部' and row.get('status') != status:
                continue

            # 3. 季号
            if season:
                r_season = str(row.get('season')).strip() if row.get('season') is not None else ''
                if r_season != season:
                    continue

            filtered.append(row)

        self.total_items = len(filtered)
        return filtered

    def get_current_page_data(self):
        """获取当前页的数据切片"""
        filtered = self.get_filtered_rows()

        # 简单的越界保护
        max_page = math.ceil(self.total_items / self.page_size) if self.page_size > 0 else 1
        if self.page > max_page and max_page > 0:
            self.page = max_page

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

    def do_refresh(self):
        """点击刷新按钮 -> 重绘整个表格区域"""
        self.load_data()
        refresh_table_view.refresh()

    def batch_retry_click(self):
        if not self.selected_rows:
            notify('请先勾选需要重试的任务')
            return
        BatchEditDialog(self.selected_rows, self.execute_batch_process).open()

    async def execute_batch_process(self, rows: List[Dict], settings: Dict):
        if not rows:
            notify("没有需要处理的任务")
            return

        count = 0
        success_count = 0

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

        notify('正在后台进行批量处理，请稍候...')

        for row in rows:
            try:
                path = Path(row['path'])
                uuid = row['uuid']

                task_data = get_task(uuid)
                if task_data:
                    name = task_data.get('name')
                    is_anime_orig = task_data.get('is_anime')
                    is_movie_orig = task_data.get('is_movie')
                    ai_used_orig = task_data.get('use_ai')
                    tmdb_id_orig = task_data.get('tmdb_id')
                    season_id_orig = task_data.get('season_id')
                    offset_orig = task_data.get('episode_offset')
                else:
                    name = row.get('name')
                    is_anime_orig = row.get('is_anime')
                    is_movie_orig = row.get('is_movie')
                    ai_used_orig = row.get('ai_used')
                    tmdb_id_orig = row.get('tmdb_id')
                    season_id_orig = row.get('season')
                    offset_orig = row.get('episode_offset')

                tmdb_id = batch_tmdb_id if batch_tmdb_id is not None else tmdb_id_orig
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

                result = await run.io_bound(
                    Rename().process,
                    path,
                    _is_anime=is_anime,
                    _is_movie=is_movie,
                    _tuuid=uuid,
                    cus_name=name,
                    cus_season_id=season_id,
                    cus_tmdb_id=tmdb_id,
                    cus_offset=offset,
                    use_ai=use_ai
                )

                if result is True:
                    success_count += 1
                else:
                    logger.error(f"Batch process failed for {row.get('name')}: {result}")

            except Exception as e:
                import traceback
                logger.error(f"Batch process error for {row.get('uuid')}: {e}")
                traceback.print_exc()

            count += 1

        notify(f'处理完成: 成功 {success_count}/{count}')
        self.selected_rows = []
        ui.timer(1.0, self.do_refresh, once=True)

    def delete_by_uuid(self, uuid: str):
        path1 = TASK_PATH / f'{uuid}.json'
        path2 = RECORD_PATH / f'{uuid}.json'

        if path1.exists():
            path1.unlink()
        if path2.exists():
            path2.unlink()

        self.all_rows = [row for row in self.all_rows if row['uuid'] != uuid]

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
        self.selected_rows = []
        # 清空 UI 状态
        self.update_selection_label()
        if self.table and self.table.selected:
            self.table.selected.clear()
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
            'bg-red-1 text-red-8': props.value === '失败'
        }">
            <div class="flex items-center justify-center">
                <q-icon :name="props.value === '成功' ? 'check_circle' : 'error'" size="xs" class="q-mr-xs"/>
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
    manager.load_data()

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


async def handle_edit(ev: GenericEventArguments):
    arg = ev.args
    uuid = arg['row']['uuid']
    await edit_page(uuid)
    manager.refresh_table()


async def handle_retry(ev: GenericEventArguments, is_batch: bool = False):
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    task_data = get_task(uuid)
    if not task_data:
        task_data = row_data

    path = task_data.get('path')
    name = task_data.get('name')
    is_anime = task_data.get('is_anime')
    is_movie = task_data.get('is_movie')
    season_id = task_data.get('season_id')
    tmdb_id = task_data.get('tmdb_id')
    offset = task_data.get('episode_offset')
    use_ai = task_data.get('use_ai')

    if not tmdb_id: tmdb_id = None
    if not offset:
        offset = None
    else:
        offset = int(offset)

    try:
        if not is_batch:
            notify('正在后台重试任务...')

        await run.io_bound(
            Rename().process,
            Path(path),
            _is_anime=is_anime,
            _is_movie=is_movie,
            _tuuid=uuid,
            cus_name=name,
            cus_season_id=season_id,
            cus_tmdb_id=tmdb_id,
            cus_offset=offset,
            use_ai=use_ai,
        )
        if not is_batch:
            notify('任务处理完成')
    except Exception as e:
        import traceback
        error_msg = f'重试失败: {str(e)}'
        logger.error(error_msg)
        traceback.print_exc()
        if not is_batch:
            notify(error_msg)

    if not is_batch:
        manager.refresh_table()


def handle_delete(ev: GenericEventArguments, is_notify: bool = True):
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    manager.delete_by_uuid(uuid)

    if is_notify:
        notify('删除任务记录成功!')
    manager.refresh_table()
