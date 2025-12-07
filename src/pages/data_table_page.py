from pathlib import Path
from typing import Any, Dict, List, Optional

from nicegui import ui
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

    def _handle_confirm(self):
        self.close()
        final_settings = {
            k: v
            for k, v in self.settings.items()
            if v is not None and v != '保持原样' and v != ''
        }
        self.callback(self.selected_rows, final_settings)


class TableManager:
    def __init__(self):
        self.all_rows = []
        self.filter_text = ''
        self.filter_status = '全部'
        self.filter_season = ''
        self.table = None
        # 【修改点1】手动维护一个选中列表，比依赖 table.selected 更可靠
        self.current_selection = []

    def load_data(self):
        rows = []
        if not TASK_PATH.exists():
            return []

        sorted_files = sorted(
            TASK_PATH.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
        )
        for index, i in enumerate(sorted_files):
            try:
                task_data = get_task(i.stem)
                if not task_data:
                    continue

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
        self.all_rows = rows
        self.filter_data()

    def filter_data(self):
        if not self.table:
            return

        filtered_rows = []

        txt_target = self.filter_text.lower().strip() if self.filter_text else ''
        status_target = self.filter_status if self.filter_status else '全部'
        season_target = str(self.filter_season).strip() if self.filter_season else ''

        for row in self.all_rows:
            # 1) 文本过滤
            if txt_target:
                r_name = str(row.get('name') or '').lower()
                r_path = str(row.get('path') or '').lower()
                if txt_target not in r_name and txt_target not in r_path:
                    continue

            # 2) 状态过滤
            if status_target != '全部':
                r_status = row.get('status')
                if r_status != status_target:
                    continue

            # 3) 季度过滤
            if season_target:
                r_season = str(row.get('season') or '').strip()
                if r_season != season_target:
                    continue

            filtered_rows.append(row)

        self.table.rows = filtered_rows
        # 刷新数据时，清空当前选中，避免数据不一致
        self.current_selection = []
        if self.table.selected:
            self.table.selected.clear()
        self.table.update()

    # 【修改点2】增加选择事件处理函数
    def handle_selection(self, e):
        """当表格勾选发生变化时触发"""
        # e.args['rows'] 包含了所有当前被选中的行数据
        self.current_selection = e.args.get('rows', [])

    def batch_retry_click(self):
        """点击批量重试按钮 -> 打开弹窗"""
        # 【修改点3】使用手动维护的 current_selection
        rows = self.current_selection

        if not rows:
            notify('请先勾选需要重试的任务')
            return

        BatchEditDialog(rows, self.execute_batch_process).open()

    def execute_batch_process(self, rows: List[Dict], settings: Dict):
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

        for row in rows:
            try:
                path = Path(row['path'])
                uuid = row['uuid']
                name = row.get('name')

                tmdb_id = batch_tmdb_id if batch_tmdb_id is not None else row.get('tmdb_id')
                season_id = int(batch_season_id) if batch_season_id else row.get('season')
                offset = int(batch_offset) if batch_offset else row.get('episode_offset')
                if offset is None: offset = 0

                if batch_is_anime_text:
                    is_anime = get_bool_from_text(batch_is_anime_text)
                else:
                    is_anime = row.get('is_anime')

                if batch_is_movie_text:
                    is_movie = get_bool_from_text(batch_is_movie_text)
                else:
                    is_movie = row.get('is_movie')

                if batch_use_ai_text:
                    use_ai = get_bool_from_text(batch_use_ai_text)
                else:
                    use_ai = row.get('ai_used')

                result = Rename().process(
                    path,
                    _is_anime=is_anime,
                    _is_movie=is_movie,
                    _tuuid=uuid,
                    cus_name=name,
                    cus_season_id=season_id,
                    cus_tmdb_id=tmdb_id,
                    cus_offset=offset,
                    use_ai=use_ai,
                )

                if result is True:
                    success_count += 1
                else:
                    logger.error(f"Batch process failed for {row.get('name')}: {result}")

            except Exception as e:
                import traceback
                logger.error(f"Batch process error for {row['uuid']}: {e}")
                traceback.print_exc()

            count += 1

        notify(f'处理完成: 成功 {success_count}/{count}')
        # 延迟刷新以等待UI响应
        ui.timer(1.0, self.refresh_table, once=True)

    def batch_delete(self):
        """批量删除"""
        # 【修改点3】使用手动维护的 current_selection
        rows = self.current_selection

        if not rows:
            notify('请先勾选需要删除的任务')
            return

        count = 0
        for row in rows:
            handle_delete(
                GenericEventArguments(
                    sender=self.table,
                    client=None,
                    args={'row': row},
                ),
                is_notify=False,
            )
            count += 1

        notify(f'已删除 {count} 个任务记录')
        self.refresh_table()

    def refresh_table(self):
        self.current_selection = []
        if self.table and self.table.selected is not None:
            self.table.selected.clear()
            self.table.update()
        self.load_data()


manager = TableManager()


@ui.refreshable
def create_table():
    def on_text_change(e):
        manager.filter_text = e.value
        manager.filter_data()

    def on_season_change(e):
        manager.filter_season = e.value
        manager.filter_data()

    def on_status_change(e):
        manager.filter_status = e.value
        manager.filter_data()

    with ui.row().classes('w-full items-center q-mb-md justify-between'):
        with ui.row().classes('items-center'):
            # 搜索框
            RedInput(
                label='搜索 剧名/路径',
                value=manager.filter_text,
                on_change=on_text_change,
            ).props('dense outlined clearable debounce=300').classes('w-64 q-mr-md')

            # 季号框
            RedInput(
                label='季号',
                value=manager.filter_season,
                on_change=on_season_change,
            ).props('dense outlined clearable type=number debounce=300').classes('w-24')

            # 状态选择
            RedSelect(
                options=['全部', '成功', '失败'],
                value=manager.filter_status,
                label='状态',
                on_change=on_status_change,
            ).props('dense outlined').classes('w-32')

        with ui.row().classes('items-center'):
            RedButton('刷新', on_click=manager.refresh_table).props(
                'outline icon=refresh'
            ).classes('q-mr-sm')
            ui.separator().props('vertical').classes('q-mx-sm')
            RedButton('批量重试', on_click=manager.batch_retry_click).props(
                'color=green-6 icon=replay'
            ).classes('q-mr-sm')
            RedButton('批量删除', on_click=manager.batch_delete).props(
                'color=red-6 icon=delete'
            )

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
        ui.table(columns=columns, rows=[], selection='multiple', row_key='uuid')
        .classes('w-full h-full rounded')
        .style('max-height: 80vh; border-radius: 10px; separator: cell')
    )

    # 【修改点4】绑定选择事件，确保后端能实时获取选中状态
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
        ''',
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
        ''',
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
        """,
    )

    manager.table.on('retry', lambda ev: handle_retry(ev))
    manager.table.on('edit', lambda ev: handle_edit(ev))
    manager.table.on('del', lambda ev: handle_delete(ev))

    manager.load_data()


async def handle_edit(ev: GenericEventArguments):
    arg = ev.args
    uuid = arg['row']['uuid']
    await edit_page(uuid)
    manager.refresh_table()


def handle_retry(ev: GenericEventArguments, is_batch: bool = False):
    arg = ev.args
    row_data = arg['row']
    path = row_data['path']
    is_anime = row_data['is_anime']
    is_movie = row_data['is_movie']
    uuid = row_data['uuid']
    name = row_data.get('name')
    season_id = row_data.get('season')

    tmdb_id = row_data.get('tmdb_id')
    offset = row_data.get('episode_offset')
    use_ai = row_data.get('ai_used')

    if not tmdb_id: tmdb_id = None
    if not offset:
        offset = None
    else:
        offset = int(offset)

    try:
        Rename().process(
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
            notify('任务已重新提交')
    except Exception as e:
        notify(f'重试失败: {str(e)}')

    if not is_batch:
        manager.refresh_table()


def handle_delete(ev: GenericEventArguments, is_notify: bool = True):
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    path1 = TASK_PATH / f'{uuid}.json'
    path2 = RECORD_PATH / f'{uuid}.json'

    if path1.exists():
        path1.unlink()
    if path2.exists():
        path2.unlink()

    if is_notify:
        notify('删除任务记录成功!')
        manager.refresh_table()
