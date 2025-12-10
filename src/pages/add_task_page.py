import re
import os
from pathlib import Path
from typing import Optional, Sequence, List, Dict, Any, Tuple

from nicegui import ui, run

from ..logger import logger
from ..monitor.monitor import monitor_service
from ..pages.data_table_page import refresh_table_view, manager
from ..element.red import RedButton, RedToogle, RedInput, notify
from ..component.local_file_picker import local_file_picker
from ..rename.utils import VIDEO_SUFFIX


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
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('是否为动画类型').classes('text-subtitle1')
                    RedToogle(
                        ['是', '否'],
                        value='是',
                        on_change=lambda e: self._set_anime(e.value)
                    ).props('dense')

                RedInput(
                    label='排除路径/文件名关键词 (支持正则)',
                    placeholder='例如: sample|feature|.nfo|Thumbs.db',
                    on_change=lambda e: self._set_exclude(e.value)
                ).props('clearable outlined').classes('w-full').tooltip('匹配到的文件将被忽略')

            ui.separator().classes('q-mt-lg q-mb-sm')

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
        return None


def _should_ignore(file_path: Path, pattern: Optional[re.Pattern]) -> bool:
    if file_path.name.startswith('.'):
        return True
    if pattern:
        if pattern.search(file_path.as_posix()):
            return True
    return False


# --- 遍历和添加操作 ---
def _process_files_in_thread(
        paths: Sequence[str],
        is_anime: bool,
        exclude_pattern: Optional[re.Pattern]
) -> Tuple[int, int]:
    """
    这个函数将在单独的线程中运行，不会阻塞 UI
    """
    count_added = 0
    count_ignored = 0

    logger.info(f"[手动任务] 开始后台扫描路径: {len(paths)} 个目标")

    for p_str in paths:
        path_obj = Path(p_str)
        if not path_obj.exists():
            continue

        files_iterator = []

        # 情况1: 用户直接选了一个文件
        if path_obj.is_file():
            if path_obj.suffix.lower() in VIDEO_SUFFIX:
                files_iterator = [path_obj]
            else:
                logger.warning(f"[手动任务] 跳过不支持的文件类型: {path_obj.name}")
                count_ignored += 1
                continue

        # 情况2: 用户选了一个文件夹
        elif path_obj.is_dir():
            for root, _, files in os.walk(path_obj):
                for file in files:
                    files_iterator.append(Path(root) / file)

        # 遍历收集到的文件列表
        for f_path in files_iterator:
            # 1. 再次检查后缀（针对文件夹遍历的情况）
            if f_path.suffix.lower() not in VIDEO_SUFFIX:
                continue

            # 2. 检查正则排除
            if _should_ignore(f_path, exclude_pattern):
                logger.debug(f"[手动任务] 排除: {f_path.name}")
                count_ignored += 1
                continue

            # 3. 加入队列
            monitor_service.add_manual_task(f_path, {'is_anime': is_anime})
            count_added += 1

    return count_added, count_ignored


async def pick_file() -> None:
    # 1. 选择文件/文件夹
    paths: Optional[Sequence[str]] = await local_file_picker('/', multiple=True)

    # 2. 检查取消
    if not paths:
        notify('已取消选择', type='warning')
        return

    # 3. 配置
    config_result: Dict[str, Any] = await TaskConfigDialog()
    if not config_result:
        notify('已取消配置', type='warning')
        return

    is_anime = config_result['is_anime']
    exclude_str = config_result['exclude_keywords']
    exclude_pattern = _compile_regex(exclude_str)

    # 4. 提示开始
    notify('正在后台扫描并添加任务，请留意日志...', type='info', timeout=3000)

    try:
        count_added, count_ignored = await run.io_bound(
            _process_files_in_thread,
            paths,
            is_anime,
            exclude_pattern
        )

        msg = f'已将 {count_added} 个文件加入后台队列'
        if count_ignored > 0:
            msg += f' (忽略 {count_ignored} 个)'

        if count_added > 0:
            notify(msg, type='positive', timeout=5000)
            notify('任务正在后台处理，处理完成后会自动出现在列表中', type='info', timeout=5000)
        else:
            notify(msg, type='warning', timeout=5000)

        logger.info(f"[手动任务] 批量添加完成. Added: {count_added}, Ignored: {count_ignored}")

    except Exception as e:
        logger.error(f"[手动任务] 批量处理出错: {e}")
        notify(f'添加任务出错: {e}', type='negative')
