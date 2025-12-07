from typing import Any, Dict, List, Union, Literal, Optional

from nicegui import ui
from nicegui.context import context
from nicegui.events import (
    Handler,
    ClickEventArguments,
    ValueChangeEventArguments,
)

from .color import MAINC

ARG_MAP = {
    'close_button': 'closeBtn',
    'multi_line': 'multiLine',
}


def notify(
    message: Any,
    *,
    position: Literal[
        'top-left',
        'top-right',
        'bottom-left',
        'bottom-right',
        'top',
        'bottom',
        'left',
        'right',
        'center',
    ] = 'top',
    close_button: Union[bool, str] = False,
    type: Optional[
        Literal[  # pylint: disable=redefined-builtin
            'positive',
            'negative',
            'warning',
            'info',
            'ongoing',
        ]
    ] = 'warning',
    color: Optional[str] = None,
    multi_line: bool = False,
    **kwargs: Any,
) -> None:
    options = {
        ARG_MAP.get(key, key): value
        for key, value in locals().items()
        if key != 'kwargs' and value is not None
    }
    options['message'] = str(message)
    options.update(kwargs)
    client = context.client
    client.outbox.enqueue_message('notify', options, client.id)


class RedDropDownButton(ui.dropdown_button):
    def __init__(
        self,
        text: str = '',
        *,
        value: bool = False,
        on_value_change: Optional[Handler[ValueChangeEventArguments]] = None,
        on_click: Optional[Handler[ClickEventArguments]] = None,
        icon: Optional[str] = None,
        auto_close: Optional[bool] = False,
        split: Optional[bool] = False,
    ):
        super().__init__(
            text,
            value=value,
            on_value_change=on_value_change,
            on_click=on_click,
            color=MAINC,
            icon=icon,
            auto_close=auto_close,
            split=split,
        )
        self._props['rounded'] = True


class RedButton(ui.button):
    def __init__(self, text: str, on_click=None, icon=None):
        super().__init__(text, on_click=on_click, icon=icon, color=MAINC)
        self._props['rounded'] = True


class RedToogle(ui.toggle):
    def __init__(
        self,
        options: Union[List, Dict],
        *,
        value: Any = None,
        on_change: Optional[Handler[ValueChangeEventArguments]] = None,
        clearable: bool = False,
    ):
        super().__init__(
            options,
            value=value,
            on_change=on_change,
            clearable=clearable,
        )
        self._props['toggle-color'] = MAINC
        self._props['no-wrap'] = True
        self._props['stack'] = True
        self._props['spread'] = False


class RedInput(ui.input):
    def __init__(
        self,
        label: Optional[str] = None,
        *,
        placeholder: Optional[str] = None,
        value: str = '',
        password: bool = False,
        password_toggle_button: bool = False,
        on_change: Optional[Handler[ValueChangeEventArguments]] = None,
        autocomplete: Optional[List[str]] = None,
        validation: Dict[str, Any] = {},
    ):
        super().__init__(
            label=label,
            placeholder=placeholder,
            value=value,
            password=password,
            password_toggle_button=password_toggle_button,
            on_change=on_change,
            autocomplete=autocomplete,
            validation=validation,
        )
        self.props(f'color={MAINC}')  # 统一使用主题色
        self.props('rounded outlined dense')  # 默认圆角、边框、紧凑模式


class RedSelect(ui.select):
    def __init__(
        self,
        options: Union[List, Dict],
        *,
        label: Optional[str] = None,
        value: Any = None,
        on_change: Optional[Handler[ValueChangeEventArguments]] = None,
        with_input: bool = False,
        multiple: bool = False,
        clearable: bool = False,
    ):
        super().__init__(
            options=options,
            label=label,
            value=value,
            on_change=on_change,
            with_input=with_input,
            multiple=multiple,
            clearable=clearable,
        )
        self.props(f'color={MAINC}')
        self.props('rounded outlined dense options-dense')
