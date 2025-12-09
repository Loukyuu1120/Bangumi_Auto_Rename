from nicegui import ui

from .utils.path import IMAGE
from .utils.utils import unpack_style
from .pages.add_task_page import pick_file
from .pages.config_page import config_page
from .pages.download_log import download_log
from .__version__ import __url__, __version__
from .pages.data_table_page import create_table
from .element.red import RedButton, RedDropDownButton
from .pages.info_page import info_page


def main_page():
    ui.query('.nicegui-content').classes('p-0 gap-0')
    with ui.header(wrap=False, bordered=True, elevated=True) as header:
        header_style = {
            'background-color': 'rgb(214, 77, 77)',
            # 'background-color': '#FFFFFF',
            'height': '50px',
            'align-items': 'center',
            'flex-wrap': 'nowrap',
            'justify-content': 'space-between',
            'margin': '0px',
            'padding': '0px',
        }
        header.style(unpack_style(header_style))
        header.props('rounded')

        with ui.row(wrap=False, align_items='center'):
            ui.image(IMAGE / 'ICON.png').props('fit=contain').style(
                unpack_style(
                    {
                        'width': '100px',
                        'height': '200%',
                        'margin': '0px',
                        'padding': '0px',
                    }
                )
            )
            with ui.row(wrap=False, align_items='baseline'):
                ui.label("番剧自动重命名").style(
                    unpack_style(
                        {
                            'font-size': '14px',
                            'font-weight': 'bold',
                            'min-width': '100px',
                            'margin': '0px',
                            'padding': '0px',
                        }
                    )
                )
                ui.label(f"v{__version__}").style(
                    unpack_style(
                        {
                            'font-size': '10px',
                            'min-width': '100px',
                        }
                    )
                )
        with ui.row(wrap=False, align_items='center') as button_row:
            button_row.classes('gap-2').style('margin-right: 10px;')
            RedButton("➕ 添加任务", on_click=pick_file)
            RedButton("📄 配置", on_click=config_page)
            with RedDropDownButton(
                '🎀 菜单',
                auto_close=True,
            ):
                ui.item(
                    '📁 项目地址',
                    on_click=lambda: ui.navigate.to(
                        target=__url__,
                        new_tab=True,
                    ),
                )
                ui.item(
                    '🚩 下载日志',
                    on_click=download_log,
                )

    with ui.splitter(value=8).classes('flex h-screen w-full gap-0') as sp:
        with sp.before:
            with ui.tabs().props('vertical').classes('w-full').style(
                'flex-wrap: nowrap'
            ) as tabs:
                finish_task_tab = ui.tab(
                    '已完成任务',
                    icon='published_with_changes',
                )
                add_task_tab = ui.tab(
                    '基本信息',
                    icon='info',
                )
        with sp.after:
            with ui.tab_panels(
                tabs,
                value=finish_task_tab,
            ).props(
                'vertical'
            ).classes('w-full h-full'):
                with ui.tab_panel(finish_task_tab).classes('max-w-full'):
                    create_table()
                with ui.tab_panel(add_task_tab).classes('p-0'):
                    info_page()
