import platform
from pathlib import Path
from typing import Optional

from nicegui import ui, events

from ..config.config_manager import cm
from ..element.red import RedButton, RedToogle


class local_file_picker(ui.dialog):

    def __init__(
        self,
        directory: str,
        *,
        upper_limit: Optional[str] = None,
        multiple: bool = False,
        show_hidden_files: bool = False,
    ) -> None:
        """这是一个简单的文件选择器，允许您从运行 NiceGUI 的本地文件系统中选择文件。

        参数：
            directory: 起始目录。
            upper_limit: 限制的最高目录（None 表示无限制，默认值为与起始目录相同）。
            multiple: 是否允许选择多个文件。
            show_hidden_files: 是否显示隐藏文件。
        """
        super().__init__()

        self.path = Path(directory).expanduser()
        if upper_limit is None:
            self.upper_limit = None
        else:
            self.upper_limit = Path(
                directory if upper_limit == ... else upper_limit
            ).expanduser()
        self.show_hidden_files = show_hidden_files

        _s = 'flex-wrap: nowrap; min-width: 400px; max-width: 730px'
        self.classes('flex w-full').style(_s)
        with (
            self,
            ui.card().classes('flex w-full').style(_s),
        ):
            with ui.column(align_items='center') as c1:
                c1.classes('flex w-full')
                c1.style('flex-wrap: nowrap; flex-shrink: 0; flex-basis: auto')
                with ui.row().classes('w-full nowrap') as r1:
                    r1.style('flex-wrap: nowrap')
                    self.add_drives_toggle()
                    rs = 'multiple' if multiple else 'single'
                    self.grid = (
                        ui.aggrid(
                            {
                                'columnDefs': [
                                    {
                                        'field': 'name',
                                        'headerName': 'File',
                                    }
                                ],
                                'rowSelection': rs,
                            },
                            html_columns=[0],
                        )
                        .classes('w-1/2')
                        .style(_s)
                        .on('cellDoubleClicked', self.handle_double_click)
                    )
                with ui.row().classes('w-full justify-end'):
                    RedButton('取消', on_click=self.close).props('outline')
                    RedButton('确定', on_click=self._handle_ok)
        self.update_grid()

    def add_drives_toggle(self):
        if platform.system() == 'Windows':
            import win32api
            drives = win32api.GetLogicalDriveStrings().split('\000')[:-1]
            self.drives_toggle = RedToogle(
                drives, value=drives[0], on_change=self.update_drive
            )
        elif platform.system() == 'Darwin':  # macOS
            import os
            drives = [
                drive
                for drive in os.listdir('/Volumes')
                if os.path.isdir(os.path.join('/Volumes', drive))
            ]
            drives = [os.path.join('/Volumes', d) for d in drives]
            if not drives:
                drives = ['/']

            self.drives_toggle = RedToogle(
                drives, value=drives[0], on_change=lambda e: self.update_drive(None)
            )

        elif platform.system() == 'Linux':
            import os
            docker_config = cm.get_config('docker_mnt')

            drives = []
            if isinstance(docker_config, list):
                drives = [d for d in docker_config if os.path.isdir(d)]
            elif isinstance(docker_config, str) and docker_config and os.path.isdir(docker_config):
                try:
                    # 获取子目录并拼凑成完整路径
                    sub_dirs = [
                        os.path.join(docker_config, d)
                        for d in os.listdir(docker_config)
                        if os.path.isdir(os.path.join(docker_config, d))
                    ]
                    drives = sub_dirs
                except Exception:
                    pass

            # 兜底：如果没有配置或路径无效，默认显示根目录
            if not drives:
                drives = ['/']

            # 创建切换按钮
            self.drives_toggle = RedToogle(drives, value=drives[0])
            self.drives_toggle.on_value_change(
                lambda e: self.update_drive(None),
            )
        self.drives_toggle.classes(add="column", remove="row inline")

    def update_drive(self, main: Optional[str] = None):
        if main is None:
            path_str = self.drives_toggle.value
        else:
            path_str = f'{main}/{self.drives_toggle.value}'
        self.path = Path(path_str).expanduser()  # type: ignore
        self.update_grid()

    def _get_safe_mtime(self, p: Path) -> float:
        try:
            # 尝试获取修改时间
            return p.stat().st_mtime
        except (FileNotFoundError, PermissionError, OSError):
            # 如果文件是损坏的软链接、无权限或者是诡异的系统文件
            # 返回 0.0，让它排在列表最后
            return 0.0

    def update_grid(self) -> None:
        # 1. 安全地获取文件列表 (glob 也有可能因为权限报错)
        try:
            paths = list(self.path.glob('*'))
        except Exception as e:
            paths = []

        # 2. 使用辅助方法进行排序，防止崩溃
        # paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        paths.sort(key=self._get_safe_mtime, reverse=True)

        if not self.show_hidden_files:
            paths = [p for p in paths if not p.name.startswith('.')]

        # 3. 继续后续排序
        paths.sort(key=lambda p: p.name.lower())
        paths.sort(key=lambda p: not p.is_dir())
        self.grid.options['rowData'] = [
            {
                'name': f'📁 {p.name}' if p.is_dir() else p.name,
                'path': str(p),
            }
            for p in paths
        ]
        if (self.upper_limit is None and self.path != self.path.parent) or (
                self.upper_limit is not None and self.path != self.upper_limit
        ):
            self.grid.options['rowData'].insert(
                0,
                {
                    'name': '📁 <strong>..</strong>',
                    'path': str(self.path.parent),
                },
            )
        self.grid.update()

    def handle_double_click(self, e: events.GenericEventArguments) -> None:
        self.path = Path(e.args['data']['path'])
        if self.path.is_dir():
            self.update_grid()
        else:
            self.submit([str(self.path)])

    async def _handle_ok(self):
        rows = await self.grid.get_selected_rows()
        if not rows:
            self.submit([str(self.path)])
        else:
            self.submit([r['path'] for r in rows])
