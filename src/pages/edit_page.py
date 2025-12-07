from pathlib import Path
from typing import Optional
from types import SimpleNamespace

from nicegui import ui, run

from ..logger import logger
from ..rename.process import Rename
from ..utils.utils import get_task, write_task
from ..element.red import RedButton, RedToogle, notify

TASK_MAP = {
    'is_anime': '是否为动画',
    'name': '剧集名称',
    'season_id': '季度',
    'is_movie': '是否为电影',
    'use_ai': '使用AI识别',
    'episode_offset': '集数偏移(Offset)',
    'tmdb_id': '指定 TMDB ID',
}


def value_to_text(value: Optional[bool]) -> str:
    if value is None:
        return '自动'
    elif value:
        return '是'
    else:
        return '否'


def text_to_value(text: str) -> Optional[bool]:
    if text == '是':
        return True
    elif text == '否':
        return False
    elif text == '自动':
        return None
    else:
        return None


class EditPage(ui.dialog):

    def __init__(self, uuid: str) -> None:
        super().__init__()
        self.uuid = uuid

        _s = 'width: 50%; flex-wrap: nowrap; max-height: 80vh; overflow-y: auto;'
        task_data = get_task(uuid)
        if task_data is None:
            return notify('任务数据不存在！')

        if 'use_ai' not in task_data or task_data['use_ai'] is None:
            task_data['use_ai'] = False  # 默认关闭，由用户决定
        if 'episode_offset' not in task_data:
            task_data['episode_offset'] = 0
        if 'tmdb_id' not in task_data or task_data['tmdb_id'] == '':
            task_data['tmdb_id'] = ''
        if 'season_id' in task_data:
            if task_data['season_id'] is False or task_data['season_id'] == '':
                task_data['season_id'] = None  # 空值设为 None
            elif task_data['season_id'] is not None:
                try:
                    task_data['season_id'] = int(task_data['season_id'])
                except:
                    task_data['season_id'] = None

        self.data = SimpleNamespace(**task_data)
        with self, ui.card().style(_s).classes('flex'):
            ui.label('编辑任务').style('font-size: 20px; font-weight: bold')
            ui.separator()

            # 基本信息
            ui.label('基本信息').style(
                'font-size: 16px; font-weight: bold; margin-top: 10px;'
            )
            basic_fields = ['is_anime', 'name', 'season_id', 'tmdb_id', 'episode_offset', 'is_movie']

            for key in basic_fields:
                self._create_field_row(key, getattr(self.data, key, None))

            ui.separator().style('margin: 20px 0;')

            # AI设置
            ui.label('AI设置').style(
                'font-size: 16px; font-weight: bold; margin-top: 10px;'
            )
            self._create_field_row('use_ai', task_data.get('use_ai', False))

            ui.separator()

            with ui.row(wrap=False).classes('w-full justify-end'):
                RedButton('取消', on_click=self.close).props('outline')
                RedButton('确认修改并重新处理', on_click=self._handle_ok)

    def _create_field_row(self, key: str, value):
        with ui.column(wrap=False).classes('flex no-wrap w-full'):
            with ui.row(wrap=False).classes('flex justify-space-between w-full'):
                with ui.row(wrap=False, align_items='baseline') as row:
                    row.classes('flex w-full')
                    label = TASK_MAP.get(key, key)
                    ui.label(label).style('min-width: 120px')

                    if key in ['is_anime', 'is_movie']:
                        tg = RedToogle(
                            ['是', '否', '自动'],
                            value=value_to_text(value),
                            on_change=lambda e, c=key: self._change(c, e.value),
                        )
                        tg.style('font-size: 10px')
                        tg.classes('flex no-wrap w-full')
                    elif key == 'use_ai':
                        tg = RedToogle(
                            ['启用', '禁用'],
                            value='启用' if value else '禁用',
                            on_change=lambda e, c=key: self._change(
                                c, e.value == '启用'
                            ),
                        )
                        tg.style('font-size: 10px')
                        tg.classes('flex no-wrap w-full')
                    else:
                        val = getattr(self.data, key)

                        if key == 'season_id':
                            # 如果是 None 或空，显示空字符串而不是 "None"
                            display_val = '' if val is None else str(val)
                        elif key == 'episode_offset':
                            display_val = str(val) if val is not None else '0'
                        else:
                            display_val = val if val is not None else ''

                        input_props = 'filled dense'
                        if key in ['episode_offset', 'season_id']:
                            input_type = 'number'
                        else:
                            input_type = 'text'

                        ui.input(
                            value=display_val,
                            on_change=lambda e, c=key: self._change(c, e.value),
                        ).props(f'{input_props} type={input_type}').style(
                            'flex-grow: 2'
                        )

    def _change(self, key: str, value) -> None:
        """处理字段变化"""
        if key == 'episode_offset':
            try:
                value = int(value) if value else 0
            except ValueError:
                value = 0
        elif key == 'season_id':
            try:
                # 空值保持为 None，不要转为 1
                value = int(value) if value and str(value).strip() else None
            except ValueError:
                value = None
        elif key == 'tmdb_id':
            # 保持字符串，但清理空值
            value = str(value).strip() if value else None
        elif key == 'name':
            # 保持字符串，但清理空值
            value = str(value).strip() if value else None
        elif key in ['is_anime', 'is_movie']:
            # 通过 toggle 传来的是文本
            value = text_to_value(value)
        elif key == 'use_ai':
            # 已经是布尔值
            pass

        setattr(self.data, key, value)

    async def _handle_ok(self):
        """保存并重新处理"""
        save_data = self.data.__dict__.copy()

        # 清理空字符串
        if save_data.get('tmdb_id') == '':
            save_data['tmdb_id'] = None
        if save_data.get('name') == '':
            save_data['name'] = None

        # 确保数字类型正确
        if 'episode_offset' in save_data and save_data['episode_offset'] is None:
            save_data['episode_offset'] = 0

        # 确保 use_ai 是布尔值
        if 'use_ai' in save_data:
            save_data['use_ai'] = bool(save_data['use_ai'])

        # 保存到文件
        write_task(self.uuid, save_data)
        logger.info(f'[任务] 任务{self.uuid}已修改为： {save_data}')
        notify('修改成功！重新开始识别！')
        self.close()
        path = Path(save_data.get('path'))
        is_anime = text_to_value(save_data.get('is_anime'))
        is_movie = text_to_value(save_data.get('is_movie'))
        uuid = save_data.get('uuid')
        name = save_data.get('name')
        season_id = save_data.get('season_id')
        tmdb_id = save_data.get('tmdb_id')
        offset = save_data.get('episode_offset', 0)
        use_ai = save_data.get('use_ai', False)  # ← 关键：读取 use_ai

        # 类型转换
        if offset is None:
            offset = 0
        else:
            offset = int(offset)

        if season_id is not None:
            try:
                season_id = int(season_id)
            except:
                season_id = None

        try:
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
                use_ai=use_ai,
            )

            if result is True:
                notify('处理成功！')
            else:
                notify(f'处理失败: {result}')

        except Exception as e:
            import traceback
            error_msg = f'处理失败: {str(e)}'
            logger.error(error_msg)
            traceback.print_exc()
            notify(error_msg)


async def edit_page(uuid: str) -> None:
    await EditPage(uuid)
