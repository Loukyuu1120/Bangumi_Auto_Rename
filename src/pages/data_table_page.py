import time
import gc
import platform
import ctypes
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from nicegui import ui, run
from nicegui.events import GenericEventArguments

from ..element.red import notify, RedButton, RedInput, RedSelect, RedToogle
from .edit_page import edit_page
from ..utils.utils import get_task
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

        self.is_fully_loaded = False
        self.last_access_time = time.time()
        self.CACHE_TTL = 300  # 缓存存活时间：300秒 (5分钟) 无操作则清理

    def keep_alive(self):
        """更新最后访问时间"""
        self.last_access_time = time.time()

    async def check_expiration(self):
        """
        检查缓存是否过期，如果过期则清空并释放内存。
        该方法由 UI 定时器调用。
        """
        # 如果缓存和文件列表都已经为空，说明已经清理过了，直接返回，避免重复日志
        if not self.cache:
            return

        # 计算当前空闲了多久
        idle_duration = time.time() - self.last_access_time
        # 只有空闲时间超过设定值 (300秒) 才清理
        if idle_duration > self.CACHE_TTL:
            logger.info(f"[任务列表] 页面闲置超时 ({int(idle_duration)}s > {self.CACHE_TTL}s)，自动清理内存缓存...")
            # 清空数据引用
            self.cache.clear()
            self.is_fully_loaded = False
            await run.io_bound(gc.collect)
            if platform.system() == 'Linux':
                try:
                    libc = ctypes.CDLL("libc.so.6")
                    libc.malloc_trim.argtypes = [ctypes.c_int]
                    libc.malloc_trim.restype = ctypes.c_int
                    await run.io_bound(lambda: libc.malloc_trim(0))
                except Exception:
                    pass

            logger.info("[任务列表] 内存清理完成，资源已释放")

    def _scan_files_sync(self):
        if not TASK_PATH.exists():
            return []
        return sorted(
            TASK_PATH.glob('*.json'),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

    def _read_task_file(self, file_path: Path) -> Optional[Dict]:
        """读取单个文件并格式化"""
        self.keep_alive()  # 只要读取文件，就算活跃

        uuid = file_path.stem
        if uuid in self.cache:
            return self.cache[uuid]

        try:
            task_data = get_task(uuid)
            if not task_data: return None

            status = '失败' if task_data.get('error') else '成功'
            error_msg = task_data.get('error', '')
            ts = task_data.get('timestamp')
            try:
                if not ts:
                    ts = file_path.stat().st_mtime
                time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(ts)))
            except Exception:
                time_str = "Unknown Time"

            row = {
                'id': uuid,
                'uuid': uuid,
                'path': task_data.get('path', ''),
                'target_path': task_data.get('target_path', ''),
                'name': task_data.get('name', '未识别'),
                'season': task_data.get('season_id', ''),
                'status': status,
                'error_msg': error_msg,
                'time': time_str,
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

    @staticmethod
    def _perform_physical_delete(uuids: List[str]):
        """在后台线程批量删除文件，避免阻塞 UI"""
        for uuid in uuids:
            try:
                path1 = TASK_PATH / f'{uuid}.json'
                path2 = RECORD_PATH / f'{uuid}.json'
                if path1.exists(): path1.unlink()
                if path2.exists(): path2.unlink()
            except Exception:
                pass

    async def load_data(self):
        self.keep_alive()
        try:
            # 如果文件列表已经被清理了，重新加载
            if not self.file_list:
                self.file_list = await run.io_bound(self._scan_files_sync)
            else:
                # 简单的检查是否有变动 (可选)
                pass

            self.total_items = len(self.file_list)
        except Exception as e:
            logger.error(f"File list error: {e}")
            self.file_list = []
            self.total_items = 0

    def get_current_page_data(self) -> List[Dict]:
        self.keep_alive()  # 翻页算活跃

        try:
            # 1. 预处理过滤条件
            # 使用 (self.filter_text or '') 防止 NoneType 错误
            txt = (self.filter_text or '').lower().strip()
            status = self.filter_status
            season = str(self.filter_season).strip() if self.filter_season else ''

            has_filter = (
                    bool(txt) or
                    status != '全部' or
                    bool(season)
            )

            if not has_filter:
                # === 模式 A：无过滤 ===
                if not self.file_list and self.total_items == 0:
                    return []

                self.total_items = len(self.file_list)  # 确保总数正确

                start = (self.page - 1) * self.page_size
                end = start + self.page_size

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
                # === 模式 B：有过滤 (需加载数据) ===

                # 1. 懒加载：如果还没全量加载，现在加载
                if not self.is_fully_loaded:
                    notify('正在加载所有记录以进行搜索，请稍候...', type='info')
                    for f in self.file_list:
                        if f.stem not in self.cache:
                            self._read_task_file(f)
                    self.is_fully_loaded = True

                # 2. 内存过滤
                filtered = []

                # 直接遍历 file_list 保证顺序
                for f in self.file_list:
                    row = self.cache.get(f.stem)
                    if not row: continue

                    # 名字/路径搜索
                    if txt:
                        name_match = str(row.get('name', '')).lower()
                        path_match = str(row.get('path', '')).lower()
                        if txt not in name_match and txt not in path_match:
                            continue

                    # 状态过滤
                    if status != '全部' and row.get('status') != status:
                        continue

                    # 季号过滤
                    if season and str(row.get('season', '')).strip() != season:
                        continue

                    filtered.append(row)

                self.total_items = len(filtered)

                start = (self.page - 1) * self.page_size
                end = start + self.page_size
                return filtered[start:end]

        except Exception as e:
            logger.error(f"获取分页数据出错: {e}")
            return []

    def batch_retry_click(self):
        self.keep_alive()
        if not self.selected_rows:
            notify('请先勾选需要重试的任务')
            return
        BatchEditDialog(self.selected_rows, self.execute_batch_process).open()

    async def execute_batch_process(self, rows: List[Dict], settings: Dict):
        self.keep_alive()
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

        monitor_service.register_batch_count(len(rows))
        notify(f'已将 {len(rows)} 个任务加入后台队列')

        for row in rows:
            try:
                uuid = row['uuid']
                task_data = get_task(uuid) or row
                path_str = task_data.get('path')
                if not path_str: continue
                path = Path(path_str)

                is_anime_orig = task_data.get('is_anime')
                is_movie_orig = task_data.get('is_movie')
                ai_used_orig = task_data.get('use_ai')
                season_id_orig = task_data.get('season_id')
                offset_orig = task_data.get('episode_offset')

                tmdb_id = batch_tmdb_id
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
                    '_tuuid': uuid,
                    '_ai_attempted': False
                }

                monitor_service.add_manual_task(path, options)

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

    def _cleanup_dirs_after_delete(self, dirs: set[Path]):
        """删除文件后：清理空目录（只要目录里没有视频文件就删）"""
        from src.rename.utils import VIDEO_SUFFIX

        def has_video_files(directory: Path) -> bool:
            try:
                for item in directory.rglob('*'):
                    if item.is_file() and item.suffix.lower() in VIDEO_SUFFIX:
                        return True
            except Exception:
                pass
            return False

        def force_cleanup_dir(directory: Path):
            try:
                for item in directory.iterdir():
                    if item.is_file():
                        try:
                            item.unlink()
                        except Exception:
                            pass
                    elif item.is_dir():
                        force_cleanup_dir(item)

                directory.rmdir()
                logger.info(f'[删除任务] 已清理无视频目录: {directory}')
            except Exception as e:
                logger.warning(f'[删除任务] 清理目录失败 {directory}: {e}')

        # 从深到浅删，避免父目录先删失败
        for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                if not d.exists() or not d.is_dir():
                    continue
                if not has_video_files(d):
                    force_cleanup_dir(d)
            except Exception:
                pass

    def _delete_files_from_record_sync(self, uuid: str, delete_transferred: bool, delete_source: bool):
        """
        根据 uuid 删除目标文件/源文件/记录文件。用于在后台线程调用。
        """
        import json

        record_file = RECORD_PATH / f'{uuid}.json'
        task_file = TASK_PATH / f'{uuid}.json'

        dirs_to_check: set[Path] = set()

        # 1) 删除目标文件
        mapping = None
        if delete_transferred and record_file.exists():
            try:
                with record_file.open('r', encoding='utf-8') as f:
                    mapping = json.load(f)  # {source_str: target_str}
            except Exception:
                mapping = None

            if isinstance(mapping, dict):
                for _, tgt_str in mapping.items():
                    try:
                        t = Path(tgt_str)
                    except Exception:
                        continue

                    parent = t.parent
                    for _ in range(3):
                        if parent.exists() and parent.is_dir():
                            dirs_to_check.add(parent)

                        if parent.parent == parent:
                            break
                        parent = parent.parent

                    # 1) 删除目标文件本体
                    try:
                        if t.exists() and t.is_file():
                            t.unlink()
                    except Exception:
                        pass

                    # 2) 删除同前缀元数据（字幕/图片/nfo等）
                    prefix = t.stem
                    try:
                        if parent.exists() and parent.is_dir():
                            for child in parent.iterdir():
                                if not child.is_file():
                                    continue
                                if child == t:
                                    continue
                                if child.stem.startswith(prefix):
                                    try:
                                        child.unlink()
                                    except Exception:
                                        pass
                    except Exception:
                        pass

        # 目标删除完成后：清理目录
        if delete_transferred and dirs_to_check:
            try:
                self._cleanup_dirs_after_delete(dirs_to_check)
            except Exception:
                pass

        # 2) 删除源文件
        if delete_source:
            maybe_paths = set()

            # 从 task_file 获取源路径
            try:
                if task_file.exists():
                    with task_file.open('r', encoding='utf-8') as f:
                        td = json.load(f)
                    p = td.get('path')
                    if p:
                        maybe_paths.add(p)
            except Exception:
                pass

            # 从 mapping keys 获取源路径
            try:
                if isinstance(mapping, dict):
                    for src_str in mapping.keys():
                        if src_str:
                            maybe_paths.add(src_str)
                elif record_file.exists():
                    # mapping 之前没读到，这里再读一次
                    with record_file.open('r', encoding='utf-8') as f:
                        _m = json.load(f)
                    if isinstance(_m, dict):
                        for src_str in _m.keys():
                            if src_str:
                                maybe_paths.add(src_str)
            except Exception:
                pass

            # 删除源文件
            for pstr in maybe_paths:
                try:
                    p = Path(pstr)
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception:
                    pass

        # 3) 删除任务记录文件
        try:
            if task_file.exists():
                task_file.unlink()
        except Exception:
            pass

        try:
            if record_file.exists():
                record_file.unlink()
        except Exception:
            pass

    def delete_by_uuid(self, uuid: str, delete_transferred: bool = False, delete_source: bool = False):
        """单条删除（可选择是否删除转移后的文件或原文件）"""
        self.keep_alive()

        # 后台同步删除实际文件与记录
        try:
            # 在后台线程执行物理删除
            run.io_bound(self._delete_files_from_record_sync, uuid, delete_transferred, delete_source)
        except Exception:
            # 如果 run.io_bound 不可用（同步上下文），仍然直接调用
            try:
                self._delete_files_from_record_sync(uuid, delete_transferred, delete_source)
            except Exception:
                pass

        # 内存清理
        if uuid in self.cache:
            del self.cache[uuid]

        # 列表清理
        self.file_list = [f for f in self.file_list if f.stem != uuid]
        self.total_items = max(0, self.total_items - 1)

    async def batch_delete(self):
        """批量删除：先弹窗选择是否删除文件/源文件，再后台删除并更新内存"""
        self.keep_alive()

        rows = list(self.selected_rows)
        if not rows:
            notify('请先勾选需要删除的任务')
            return

        # 弹窗选择
        from nicegui import ui as _ui

        result = {
            'confirmed': False,
            'del_trans': False,
            'del_src': False,
        }

        dialog = _ui.dialog()
        with dialog:
            with _ui.card().style('width: 420px; max-width: 90vw'):
                _ui.label(f'确认删除 {len(rows)} 条记录吗？').classes('text-h6 q-mb-md')
                _ui.label('选择是否同时删除文件（若文件不存在会被忽略）').classes(
                'text-caption text-grey q-mb-sm'
                )

                del_target_checkbox = _ui.checkbox('删除已转移的目标文件').props('dense')
                del_source_checkbox = _ui.checkbox('删除源文件（转移前）').props('dense')

                with _ui.row().classes('w-full justify-end q-mt-md'):
                    _ui.button('取消', on_click=lambda e: dialog.close()).props('outline color=grey')

                    def do_confirm(e):
                        result['confirmed'] = True
                        result['del_trans'] = del_target_checkbox.value
                        result['del_src'] = del_source_checkbox.value
                        dialog.close()

                    _ui.button('确认删除', on_click=do_confirm).classes('q-ml-sm').props('color=red')

        dialog.open()

        await dialog

        if not result['confirmed']:
            notify('已取消删除')
            return

        notify(
            f'正在后台删除 {len(rows)} 个任务（同时删除目标文件: {result["del_trans"]}, '
            f'删除源文件: {result["del_src"]}）',
            type='info',
        )

        uuids_to_delete = [r['uuid'] for r in rows]
        uuids_set = set(uuids_to_delete)

        # 在后台删除实际文件（io_bound）
        await run.io_bound(
            lambda: [
                self._delete_files_from_record_sync(
                    u,
                    result['del_trans'],
                    result['del_src'],
                )
                for u in uuids_to_delete
            ]
        )

        # 内存更新
        for uuid in uuids_to_delete:
            self.cache.pop(uuid, None)

        self.file_list = [f for f in self.file_list if f.stem not in uuids_set]
        self.total_items = len(self.file_list)
        self.selected_rows = []
        self.update_selection_label()

        notify(f'成功删除 {len(rows)} 个任务')
        refresh_table_view.refresh()

    async def refresh_table(self):
        self.keep_alive()
        self.selected_rows = []
        if self.table and self.table.selected:
            self.table.selected.clear()
        self.update_selection_label()

        self.file_list = []
        self.cache.clear()
        self.is_fully_loaded = False

        await self.load_data()
        refresh_table_view.refresh()

    def update_selection_label(self):
        if self.selection_label:
            count = len(self.selected_rows)
            self.selection_label.text = f'已选中: {count}'

    def handle_selection(self, e):
        self.keep_alive()
        args = e.args
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
        elif isinstance(args, list):
            self.selected_rows = args
        self.update_selection_label()

    async def do_refresh(self):
        self.keep_alive()
        try:
            notify('正在刷新列表...')
            self.file_list = []
            self.cache.clear()
            self.is_fully_loaded = False
            await self.load_data()
            refresh_table_view.refresh()
        except Exception:
            pass


manager = TableManager()


@ui.refreshable
def refresh_table_view():
    rows_page = manager.get_current_page_data()
    total_pages = math.ceil(manager.total_items / manager.page_size) if manager.page_size > 0 else 1

    columns: List[Dict[str, Any]] = [
        {'name': 'name', 'label': '剧集信息 / 原文件路径', 'field': 'name', 'sortable': True, 'align': 'left'},
        {'name': 'time', 'label': '处理时间', 'field': 'time', 'sortable': True, 'align': 'left', 'classes': 'text-grey-7', 'style': 'width: 160px'},
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

    with ui.row().classes('w-full justify-between items-center q-mt-sm q-px-sm'):
        manager.selection_label = ui.label(f'已选中: {len(manager.selected_rows)}').classes(
            'text-subtitle2 text-primary font-bold')

        with ui.row().classes('items-center q-gutter-x-sm'):
            ui.label(f'总计: {manager.total_items}').classes('text-grey-7 q-mr-md')

            def on_page_size_change(e):
                manager.keep_alive()  # 操作UI算活跃
                manager.page_size = e.value
                manager.page = 1
                manager.selected_rows = []
                manager.update_selection_label()
                refresh_table_view.refresh()

            def on_page_change(e):
                manager.keep_alive()  # 操作UI算活跃
                manager.page = e.value
                manager.selected_rows = []
                manager.update_selection_label()
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
    manager.keep_alive()
    def on_text_change(e):
        manager.keep_alive()
        manager.filter_text = e.value
        manager.page = 1
        refresh_table_view.refresh()

    def on_season_change(e):
        manager.keep_alive()
        manager.filter_season = e.value
        manager.page = 1
        refresh_table_view.refresh()

    def on_status_change(e):
        manager.keep_alive()
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

    # 启动后台检查定时器：每60秒检查一次是否过期
    ui.timer(60.0, manager.check_expiration)

    async def init_data():
        try:
            ui.notify('正在加载任务列表...', type='info', position='top')
            await manager.load_data()
            refresh_table_view.refresh()
        except Exception:
            pass

    ui.timer(0, init_data, once=True)


async def handle_edit(ev: GenericEventArguments):
    manager.keep_alive()
    arg = ev.args
    uuid = arg['row']['uuid']
    await edit_page(uuid)
    await manager.refresh_table()


async def handle_retry(ev: GenericEventArguments, is_batch: bool = False):
    manager.keep_alive()
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

    try:
        raw_offset = task_data.get('episode_offset', 0)
        try:
            safe_offset = int(raw_offset) if raw_offset is not None else 0
        except (ValueError, TypeError):
            safe_offset = 0

        options = {
            'is_anime': task_data.get('is_anime'),
            'is_movie': task_data.get('is_movie'),
            'use_ai': task_data.get('use_ai'),
            'cus_season_id': task_data.get('season_id'),
            'cus_tmdb_id': task_data.get('tmdb_id'),
            'cus_offset': safe_offset,
            '_tuuid': uuid,
            '_ai_attempted': False
        }

        if not is_batch:
            monitor_service.add_manual_task(path, options, increment_counter=True)
            if uuid in manager.cache:
                manager.cache[uuid]['status'] = '排队中'
                manager.cache[uuid]['error_msg'] = '已加入处理队列'
            notify('已加入后台处理队列')
            refresh_table_view.refresh()

    except Exception as e:
        logger.error(f'Retry failed: {e}')
        if not is_batch:
            notify(f'加入队列失败: {e}', type='negative')


async def handle_delete(ev: GenericEventArguments, is_notify: bool = True):
    manager.keep_alive()
    arg = ev.args
    row_data = arg['row']
    uuid = row_data['uuid']

    from nicegui import ui as _ui

    # 用一个 future 来接收结果
    result = {'confirmed': False, 'del_trans': False, 'del_src': False}

    dialog = _ui.dialog()
    with dialog:
        with _ui.card().style('width: 420px; max-width: 90vw'):
            _ui.label('确认删除该任务记录吗？').classes('text-h6 q-mb-md')
            _ui.label('可选择是否同时删除源文件/目标文件（不存在会被忽略）').classes(
                'text-caption text-grey q-mb-sm'
            )

            del_target_checkbox = _ui.checkbox('删除已转移的目标文件').props('dense')
            del_source_checkbox = _ui.checkbox('删除源文件（转移前）').props('dense')

            with _ui.row().classes('w-full justify-end q-mt-md'):
                _ui.button('取消', on_click=dialog.close).props('outline color=grey')

                def do_confirm():
                    result['confirmed'] = True
                    result['del_trans'] = bool(del_target_checkbox.value)
                    result['del_src'] = bool(del_source_checkbox.value)
                    dialog.close()

                _ui.button('确认删除', on_click=do_confirm).classes('q-ml-sm').props('color=red')

    dialog.open()

    await dialog

    if not result['confirmed']:
        if is_notify:
            notify('已取消删除')
        return

    if is_notify:
        notify(
            f'正在后台删除任务（删除目标文件: {result["del_trans"]}, 删除源文件: {result["del_src"]}）',
            type='info'
        )

    manager.delete_by_uuid(
        uuid,
        delete_transferred=result['del_trans'],
        delete_source=result['del_src'],
    )

    manager.selected_rows = [r for r in manager.selected_rows if r.get('uuid') != uuid]
    manager.update_selection_label()

    if is_notify:
        notify('删除成功!')

    refresh_table_view.refresh()

