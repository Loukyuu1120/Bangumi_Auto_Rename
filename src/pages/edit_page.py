from pathlib import Path
from typing import Optional
from types import SimpleNamespace

from nicegui import ui

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

        # 初始化默认值
        if 'use_ai' not in task_data:
            task_data['use_ai'] = True
        if 'episode_offset' not in task_data:
            task_data['episode_offset'] = 0
        if 'tmdb_id' not in task_data:
            task_data['tmdb_id'] = ''

        self.data = SimpleNamespace(**task_data)
        with self, ui.card().style(_s).classes('flex'):
            ui.label('编辑任务').style('font-size: 20px; font-weight: bold')
            ui.separator()

            # 基本信息
            ui.label('基本信息').style(
                'font-size: 16px; font-weight: bold; margin-top: 10px;'
            )
            # 在这里添加了 tmdb_id 和 episode_offset
            basic_fields = ['is_anime', 'name', 'season_id', 'tmdb_id', 'episode_offset', 'is_movie']

            for key in basic_fields:
                # 即使 JSON 里没有，我们也显示输入框（因为上面已经做了默认值初始化）
                self._create_field_row(key, getattr(self.data, key, None))

            ui.separator().style('margin: 20px 0;')

            # AI设置
            ui.label('AI设置').style(
                'font-size: 16px; font-weight: bold; margin-top: 10px;'
            )
            self._create_field_row('use_ai', task_data.get('use_ai', True))

            ui.separator()

            with ui.row(wrap=False).classes('w-full justify-end'):
                RedButton('取消', on_click=self.close).props('outline')
                RedButton('确认修改并重新处理', on_click=self._handle_ok)

    def _create_field_row(self, key: str, value):
        with ui.column(wrap=False).classes('flex no-wrap w-full'):
            with ui.row(wrap=False).classes('flex justify-space-between w-full'):
                with ui.row(wrap=False, align_items='baseline') as row:
                    row.classes('flex w-full')
                    # 配置标签
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
                        # 处理输入框类型
                        input_props = 'filled dense'
                        val = getattr(self.data, key)

                        # 数字类型处理
                        if key in ['episode_offset', 'season_id']:
                            input_type = 'number'
                        else:
                            input_type = 'text'

                        ui.input(
                            value=val,
                            on_change=lambda e, c=key: self._change(c, e.value),
                        ).props(f'{input_props}').props(f'type={input_type}').style(
                            'flex-grow: 2'
                        ).bind_value(
                            self.data, key
                        )

    def _change(self, key: str, value) -> None:
        # 类型转换
        if key == 'episode_offset':
            try:
                value = int(value) if value else 0
            except ValueError:
                value = 0
        elif key == 'season_id':
            try:
                value = int(value) if value else 1
            except ValueError:
                value = 1

        setattr(self.data, key, value)

    def _handle_ok(self):
        # 保存所有字段到 JSON
        write_task(self.uuid, self.data.__dict__)
        logger.info(f'[任务] 任务{self.uuid}已修改为： {self.data.__dict__}')
        notify('修改成功！重新开始识别！')
        self.close()

        # 根据use_ai设置决定是否使用AI
        use_ai = getattr(self.data, 'use_ai', True)
        if not use_ai:
            from ..config.config_manager import cm
            original_ai_enabled = cm.get_config('ai_enabled')
            cm.set_config('ai_enabled', False)

        try:
            # 获取新增的参数
            tmdb_id = getattr(self.data, 'tmdb_id', None)
            offset = getattr(self.data, 'episode_offset', 0)

            # 处理空字符串的情况
            if not tmdb_id: tmdb_id = None
            if not offset:
                offset = 0
            else:
                offset = int(offset)

            # 调用 Rename 进程
            Rename().process(
                Path(getattr(self.data, 'path')),
                _is_anime=text_to_value(getattr(self.data, 'is_anime')),
                _is_movie=text_to_value(getattr(self.data, 'is_movie')),
                _tuuid=getattr(self.data, 'uuid'),
                cus_name=getattr(self.data, 'name'),
                cus_season_id=getattr(self.data, 'season_id'),
                # 新增参数传递
                cus_tmdb_id=tmdb_id,
                cus_offset=offset
            )
        finally:
            if not use_ai:
                cm.set_config('ai_enabled', original_ai_enabled)


async def edit_page(uuid: str) -> None:
    await EditPage(uuid)
