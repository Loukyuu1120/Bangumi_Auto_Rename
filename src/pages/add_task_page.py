import re
import os
from pathlib import Path
from typing import Optional, Sequence, List, Dict, Any

from nicegui import ui

from ..logger import logger
from ..monitor.monitor import monitor_service
from ..pages.data_table_page import refresh_table_view, manager
from ..element.red import RedButton, RedToogle, RedInput, notify
from ..component.local_file_picker import local_file_picker


class TaskConfigDialog(ui.dialog):
    def __init__(self) -> None:
        super().__init__()

        self.config = {
            'is_anime': True,
            'exclude_keywords': ''
        }

        _card_style = 'width: 500px; max-width: 90vw; max-height: 80vh; overflow-y: auto;'

        with self, ui.card().style(_card_style).classes('flex column'):
            ui.label('任务配置').classes('text-h6 text-weight-bold q-mb-sm')
            ui.separator().classes('q-mb-md')

            with ui.column().classes('w-full q-gutter-y-md'):
                # 1. 类型选择
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('是否为动画类型').classes('text-subtitle1')
                    RedToogle(
                        ['是', '否'],
                        value='是',
                        on_change=lambda e: self._set_anime(e.value)
                    ).props('dense')

                # 2. 排除规则输入
                RedInput(
                    label='排除路径/文件名关键词 (支持正则)',
                    placeholder='例如: sample|feature|.nfo|Thumbs.db',
                    on_change=lambda e: self._set_exclude(e.value)
                ).props('clearable outlined').classes('w-full').tooltip(
                    '匹配到的文件将被忽略，不会加入队列。多个关键词可用 | 分隔')

            ui.separator().classes('q-mt-lg q-mb-sm')

            # 按钮区
            with ui.row().classes('w-full justify-end q-gutter-x-sm'):
                RedButton('取消', on_click=self.close).props('outline color=grey')
                RedButton('确认提交', on_click=self._handle_ok)

    def _set_anime(self, value: str) -> None:
        self.config['is_anime'] = (value == '是')

    def _set_exclude(self, value: str) -> None:
        self.config['exclude_keywords'] = value

    def _handle_ok(self) -> None:
        self.close()
        self.submit(self.config)


def _compile_regex(pattern_str: str) -> Optional[re.Pattern]:
    if not pattern_str or not pattern_str.strip():
        return None
    try:
        return re.compile(pattern_str, re.IGNORECASE)
    except re.error as e:
        logger.error(f"[手动任务] 正则表达式错误 '{pattern_str}': {e}")
        notify(f'正则格式错误: {e}', type='negative')
        return None


def _should_ignore(file_path: Path, pattern: Optional[re.Pattern]) -> bool:
    if file_path.name.startswith('.'):
        return True
    if pattern:
        if pattern.search(file_path.as_posix()):
            return True
    return False


async def pick_file() -> None:
    # 1. 打开文件选择器 (这是一个弹窗)
    paths: Optional[Sequence[str]] = await local_file_picker('~', multiple=True)

    if not paths:
        notify('已取消选择', type='warning')
        return

    # 2. 打开配置弹窗
    config_result: Dict[str, Any] = await TaskConfigDialog()

    if not config_result:
        notify('已取消配置', type='warning')
        return

    is_anime = config_result['is_anime']
    exclude_str = config_result['exclude_keywords']
    exclude_pattern = _compile_regex(exclude_str)

    count_added = 0
    count_ignored = 0

    notify('正在扫描并添加任务...', type='info')

    for p_str in paths:
        path_obj = Path(p_str)

        if not path_obj.exists():
            continue

        files_to_process: List[Path] = []

        if path_obj.is_file():
            files_to_process.append(path_obj)
        elif path_obj.is_dir():
            for root, _, files in os.walk(path_obj):
                for file in files:
                    files_to_process.append(Path(root) / file)

        for f_path in files_to_process:
            if _should_ignore(f_path, exclude_pattern):
                logger.info(f"[手动任务] 排除: {f_path.name} (匹配规则)")
                count_ignored += 1
                continue

            logger.info(f'[手动任务] 加入队列: {f_path.name}')
            monitor_service.add_manual_task(f_path, {'is_anime': is_anime})
            count_added += 1

    msg = f'已添加 {count_added} 个任务'
    if count_ignored > 0:
        msg += f' (已过滤 {count_ignored} 个文件)'

    notify(msg, type='positive' if count_added > 0 else 'warning')

    manager.load_data()
    refresh_table_view.refresh()
