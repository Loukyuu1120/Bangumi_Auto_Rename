import os, re, copy
from typing import Sequence
from types import SimpleNamespace
from pathlib import Path

from nicegui import ui, run

from ..logger import logger, update_log_level_from_config
from ..config.config_manager import CN_MAP, cm
from ..element.red import RedButton, RedToogle
from ..component.local_file_picker import local_file_picker
from ..monitor.monitor_manager import monitor_manager
from ..monitor.monitor import monitor_service
from ..rename.cleaner import is_video_file
from ..rename.utils import DEFAULT_SECONDARY_RULES


class ConfigPage(ui.dialog):

    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(**cm.config)

        _s = "width: 60%; flex-wrap: nowrap; max-height: 80vh; overflow-y: auto;"
        with self, ui.card().style(_s).classes("flex"):
            ui.label("配置").style("font-size: 20px; font-weight: bold")
            ui.separator()

            # 基础配置
            ui.label("基础配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            basic_configs = [
                "api_key",
                "bangumi_path",
                "movie_path",
                "anime_path",
                "anime_movie_path",
                "tv_rename_format",
                "movie_rename_format",
                "mode",
                "overwrite_mode",
                "scrape_metadata",
                "scrape_image_types",
                "subtitle_extensions",
                "secondary_classification",
                "docker_mnt",
                "log_level",
            ]
            for cn in basic_configs:
                self._create_config_row(cn)

            ui.separator().style("margin: 20px 0;")

            # 监控配置
            ui.label("监控配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            ui.label(
                "说明：启用后会监控所配置目录及其子目录中新建的文件，并自动重命名。"
            ).style("font-size: 12px; color: gray;")
            ui.label(
                "排除目录按名称匹配：只要路径中包含这些目录名（如 @Recycle、.Trash），就会被忽略。"
            ).style("font-size: 12px; color: gray; margin-bottom: 5px;")

            monitor_configs = [
                "monitor_enabled",
                "monitor_mode",
                "monitor_paths",
                "monitor_exclude_dirs",
            ]
            for cn in monitor_configs:
                self._create_config_row(cn)

            ui.separator().style("margin: 20px 0;")

            # AI配置
            ui.label("AI识别配置").style(
                "font-size: 16px; font-weight: bold; margin-top: 10px;"
            )
            ai_configs = [
                "ai_enabled",
                "ai_auto_save",
                "ai_provider",
                "ai_confidence_threshold",
                "openai_output_format",  # OpenAI输出格式选择
                "ai_api_key",
                "ai_base_url",
                "ai_model",
                "ai_temperature",
                "gemini_api_key",
                "gemini_base_url",
                "gemini_model",
                "gemini_temperature",
            ]
            for cn in ai_configs:
                self._create_config_row(cn)

            # AI功能测试按钮
            with ui.row(wrap=False).classes("w-full justify-center mt-4 gap-2"):
                RedButton(
                    "🧪 测试AI识别功能", on_click=self._test_ai_recognition
                ).props("outline")
                RedButton("⚙️ 测试OpenAI API功能", on_click=self._test_openai_api).props(
                    "outline"
                )

            ui.separator()

            with ui.row(wrap=False).classes("w-full justify-end"):
                RedButton("取消", on_click=self.close).props("outline")
                RedButton("确认修改", on_click=self._handle_ok)

    def _create_config_row(self, cn: str):
        with ui.column(wrap=False).classes("flex no-wrap w-full"):
            with ui.row(wrap=False).classes("flex justify-space-between w-full"):
                with ui.row(wrap=False, align_items="baseline") as row:
                    row.classes("flex w-full")
                    # 配置标签
                    label = CN_MAP.get(cn, cn)
                    ui.label(label).style("min-width: 150px")

                    if cn == "mode":
                        tg = RedToogle(
                            ["硬链接", "软链接", "复制", "剪切"],
                            value=cm.get_config(cn) if cm.get_config(cn) != "链接" else "硬链接",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")

                    elif cn == "overwrite_mode":
                        tg = RedToogle(
                            ["从不覆盖", "总是覆盖", "保留最新"],
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")

                    elif cn == "scrape_metadata":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")

                    elif cn == "scrape_image_types":
                        options = [
                            "poster", "backdrop", "background",
                            "banner", "logo", "clearart", "thumb", "disc(暂不支持)"
                        ]
                        # 确保获取到的是列表，防止配置为空时报错
                        current_val = cm.get_config(cn)
                        if not isinstance(current_val, list):
                            current_val = []

                        ui.select(
                            options=options,
                            multiple=True,  # 开启多选
                            value=current_val,
                            label="选择要下载的图片类型",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        ).props("use-chips filled").style("flex-grow: 2")

                    elif cn == "subtitle_extensions":
                        # 读取配置，默认为 ['.ass', '.srt']
                        current_exts = cm.get_config(cn)
                        if not current_exts:
                            current_exts = ['.ass', '.srt', '.sub']

                        display_val = ", ".join([e.lstrip('.') for e in current_exts])

                        def _save_sub_exts(value_str):
                            # 字符串转回列表
                            # "ass, srt" -> ['.ass', '.srt']
                            exts = []
                            if value_str:
                                # 支持中文逗号和英文逗号
                                parts = value_str.replace('，', ',').split(',')
                                for p in parts:
                                    clean_p = p.strip()
                                    if clean_p:
                                        if not clean_p.startswith('.'):
                                            clean_p = '.' + clean_p
                                        exts.append(clean_p.lower())
                            self._change(cn, exts)

                        with ui.column().style("flex-grow: 2"):
                            ui.input(
                                value=display_val,
                                placeholder="ass, srt, sub",
                                on_change=lambda e: _save_sub_exts(e.value)
                            ).props("filled dense").style("width: 100%")
                            ui.label("移动视频时，会自动带走同名的这些后缀文件（保留语言标记，如 .zh.ass）").style(
                                "font-size: 10px; color: gray;")

                    elif cn == "secondary_classification":
                        with ui.row().classes("items-center gap-2"):
                            tg = RedToogle(
                                ["启用", "禁用"],
                                value="启用" if cm.get_config(cn) else "禁用",
                                on_change=lambda e, c=cn: self._change(
                                    c, e.value == "启用"
                                ),
                            )
                            tg.style("font-size: 10px")
                            RedButton("🎨 自定义规则", on_click=self._open_secondary_rules_editor).props(
                                "dense flat size=sm")

                    elif cn == "ai_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "ai_confidence_threshold":
                        tg = RedToogle(
                            ["High", "Medium", "Low"],
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "log_level":
                        tg = RedToogle(
                            ["DEBUG", "INFO", "WARNING", "ERROR"],
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "ai_auto_save":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "ai_provider":
                        tg = RedToogle(
                            ["openai", "gemini"],
                            value=cm.get_config(cn) or "openai",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "openai_output_format":
                        tg = RedToogle(
                            [
                                "function_calling",
                                "json_object",
                                "structured_output",
                                "text",
                            ],
                            value=cm.get_config(cn) or "function_calling",
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "monitor_enabled":
                        tg = RedToogle(
                            ["启用", "禁用"],
                            value="启用" if cm.get_config(cn) else "禁用",
                            on_change=lambda e, c=cn: self._change(
                                c, e.value == "启用"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")
                    elif cn == "monitor_mode":
                        # 获取当前值，默认为 fast
                        current_mode = cm.get_config(cn)
                        # 如果配置是 'compatibility' 显示为 '兼容模式'，否则为 '高效模式'
                        ui_value = "兼容模式" if current_mode == "compatibility" else "高效模式"

                        tg = RedToogle(
                            ["高效模式", "兼容模式"],
                            value=ui_value,
                            # 保存时：兼容模式->compatibility, 高效模式->fast
                            on_change=lambda e, c=cn: self._change(
                                c, "compatibility" if e.value == "兼容模式" else "fast"
                            ),
                        )
                        tg.style("font-size: 10px")
                        tg.classes("flex no-wrap w-full")

                        # 添加提示信息 (鼠标悬停显示)
                        with tg:
                            ui.tooltip(
                                "高效模式: 使用系统原生事件(Inotify)，性能好但Docker下可能有数量限制，概率会出现文件缺失。\n"
                                "兼容模式: 使用轮询(Polling)，CPU占用稍高但绝对稳定，适合大批量文件或NFS挂载。"
                            )
                    elif cn == "monitor_paths":
                        # 添加监控目录编辑器
                        with ui.column().style("flex-grow: 2"):
                            self._render_monitor_paths_editor()
                    elif cn == "docker_mnt":
                        # 处理配置：支持从旧的字符串格式自动兼容为列表
                        current_val = cm.get_config(cn)
                        if isinstance(current_val, str) and current_val:
                            current_val = [current_val]
                        elif not isinstance(current_val, list):
                            current_val = []

                        text_value = "\n".join(current_val)

                        ui.textarea(
                            value=text_value,
                            placeholder="Docker环境下映射的路径，每行一个。\n例如：\n/mnt/media\n/data/downloads",
                            on_change=lambda e, c=cn: self._change(
                                c,
                                [line.strip() for line in (e.value or "").splitlines() if line.strip()],
                            ),
                        ).props("filled rows=3").style("flex-grow: 2")
                    elif cn == "monitor_exclude_dirs":
                        current_value = cm.get_config(cn) or []
                        if isinstance(current_value, list):
                            text_value = "\n".join(current_value)
                        else:
                            text_value = str(current_value)

                        ui.textarea(
                            value=text_value,
                            placeholder=(
                                "每行一个要排除的目录名（默认排除隐藏文件，支持正则，不是完整路径），例如：\n"
                                "短剧 -> 只要路径里有“短剧”就忽略\n"
                                "S00 -> 忽略包含 S00 的路径（特典）\n"
                                "\\.m4a$ -> 忽略所有以 .m4a 结尾的文件"
                            ),
                            on_change=lambda e, c=cn: self._change(
                                c,
                                [line.strip() for line in (e.value or "").splitlines() if line.strip()],
                            )
                        ).props("filled").style("flex-grow:2")

                    elif cn in ["tv_rename_format", "movie_rename_format"]:
                        if cn == "tv_rename_format":
                            placeholder = "{{title}}/Season {{season}}/{{title}} - S{{season_00}}E{{episode_00}}"
                            tooltip_text = (
                                "可用变量:\n"
                                "{{title}}: 标题\n"
                                "{{en_title}}: 英文/原名\n"
                                "{{year}}: 年份\n"
                                "{{season}}: 季号(1)\n"
                                "{{season_00}}: 两位季号(01)\n"
                                "{{episode}}: 集号(1)\n"
                                "{{episode_00}}: 两位集号(01)\n"
                                "{{fileExt}}: 扩展名\n"
                                "以及 videoFormat, videoCodec, audioCodec 等技术参数"
                            )
                        else:
                            placeholder = "{{title}} ({{year}})/{{title}} - {{year}}"
                            tooltip_text = (
                                "可用变量:\n"
                                "{{title}}: 标题\n"
                                "{{en_title}}: 英文/原名\n"
                                "{{year}}: 年份\n"
                                "{{tmdbid}}: TMDB ID\n"
                                "{{webSource}}: 来源(BluRay)\n"
                                "{{videoFormat}}: 分辨率(2160p)\n"
                                "{{videoCodec}}: 视频编码(x265)\n"
                                "{{audioCodec}}: 音频编码(AAC)\n"
                                "{{releaseGroup}}: 制作组\n"
                                "{{fileExt}}: 扩展名"
                            )

                        with ui.column().style("flex-grow: 2"):
                            ui.textarea(
                                value=cm.get_config(cn),
                                placeholder=placeholder,
                                on_change=lambda e, c=cn: self._change(c, e.value),
                            ).props("filled rows=2").style("width: 100%")

                            # 添加变量提示说明的小字
                            ui.label("鼠标悬停查看可用变量").style(
                                "font-size: 10px; color: gray; cursor: help").tooltip(tooltip_text)

                    else:
                        ui.input(
                            value=cm.get_config(cn),
                            on_change=lambda e, c=cn: self._change(c, e.value),
                        ).props("filled").props("dense").style(
                            "flex-grow: 2"
                        ).bind_value(
                            self.config, cn
                        )

                    if cn.endswith("path"):
                        RedButton(
                            "选择",
                            on_click=lambda e, c=cn: self.pick(key=c),
                        ).style("min-width: 60px")
                    else:
                        ui.label("").style("min-width: 60px")

    def _open_secondary_rules_editor(self):
        """打开二级分类规则编辑器"""

        # 1. 获取当前规则，如果不存在则使用默认值
        current_rules = cm.get_config("secondary_rules")
        if not current_rules:
            current_rules = copy.deepcopy(DEFAULT_SECONDARY_RULES)

        # 临时绑定到实例以便编辑，不直接修改 cm，直到点击保存
        self.editing_rules = current_rules

        with ui.dialog() as dialog, ui.card().classes("w-[800px] h-[80vh] flex flex-col"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label("🎨 自定义二级分类规则").classes("text-h6")
                ui.icon("help_outline", color="gray").tooltip(
                    "规则按从上到下的顺序匹配。\n"
                    "一旦匹配成功，文件将放入对应的文件夹。\n"
                    "如果所有条件留空，则视为总是匹配（通常作为最后一项兜底）。"
                )

            ui.separator()

            # 常用代码提示
            with ui.expansion("📋 常用代码参考 (点击展开)", icon="info").classes(
                    "w-full text-xs text-gray-500 bg-gray-50"):
                with ui.grid(columns=2).classes("w-full gap-4 p-2"):
                    ui.label(
                        "类型(genre_ids):\n16:动漫, 99:纪录片, 10764:真人秀\n10767:脱口秀, 10762:儿童, 10402:音乐").style(
                        "white-space: pre-wrap")
                    ui.label(
                        "国家或地区(origin_country):\nCN:中国, US:美国, JP:日本, KR:韩国\nGB:英国, HK:香港, TW:台湾").style(
                        "white-space: pre-wrap")

            # 选项卡
            with ui.tabs().classes("w-full text-red-8") as tabs:
                movie_tab = ui.tab("电影策略")
                tv_tab = ui.tab("剧集策略")

            with ui.tab_panels(tabs, value=movie_tab).classes("w-full flex-grow overflow-hidden"):
                # === 电影面板 ===
                with ui.tab_panel(movie_tab).classes("p-0 h-full flex flex-col"):
                    self._render_rule_list_editor("movie")

                # === 剧集面板 ===
                with ui.tab_panel(tv_tab).classes("p-0 h-full flex flex-col"):
                    self._render_rule_list_editor("tv")

            ui.separator()

            with ui.row().classes("w-full justify-end gap-2"):
                RedButton("恢复默认", on_click=lambda: self._reset_rules(dialog)).props("outline color=grey")
                RedButton("取消", on_click=dialog.close).props("outline")
                RedButton("保存规则", on_click=lambda: self._save_rules(dialog))

        dialog.open()

    def _render_rule_list_editor(self, rule_type: str):
        """渲染规则列表（可滚动区域）"""

        # 容器：用于刷新列表
        container = ui.column().classes("w-full flex-grow overflow-y-auto p-2 gap-2")

        def _refresh():
            container.clear()
            rules = self.editing_rules.get(rule_type, [])

            with container:
                for idx, rule in enumerate(rules):
                    with ui.card().classes("w-full p-2 border border-gray-200"):
                        # --- 第一行：标题栏与操作 ---
                        with ui.row().classes("w-full items-center gap-2"):
                            # 序号
                            ui.label(f"#{idx + 1}").classes("text-gray-400 text-xs font-mono w-6")

                            # 文件夹名称输入
                            ui.input(
                                value=rule.get("name", ""),
                                label="分类文件夹名称",
                                placeholder="例如：华语电影",
                                on_change=lambda e, i=idx: self._update_rule(rule_type, i, "name", e.value)
                            ).props("dense outlined").style("width: 200px")

                            ui.space()

                            # 排序按钮
                            if idx > 0:
                                ui.button(icon="arrow_upward",
                                          on_click=lambda i=idx: self._move_rule(rule_type, i, -1, _refresh)).props(
                                    "flat dense round size=sm color=grey")
                            if idx < len(rules) - 1:
                                ui.button(icon="arrow_downward",
                                          on_click=lambda i=idx: self._move_rule(rule_type, i, 1, _refresh)).props(
                                    "flat dense round size=sm color=grey")

                            # 删除按钮
                            ui.button(icon="delete",
                                      on_click=lambda i=idx: self._delete_rule(rule_type, i, _refresh)).props(
                                "flat dense round size=sm color=red")

                        # --- 第二行：条件配置 ---
                        conds = rule.get("conditions", {})

                        with ui.row().classes("w-full gap-2 mt-2 items-center"):
                            ui.label("匹配条件:").classes("text-xs font-bold text-gray-600 mt-2")

                            # 类型 ID
                            ui.input(
                                value=conds.get("genre_ids", ""),
                                label="类型ID (genre_ids)",
                                placeholder="16, 10765",
                                on_change=lambda e, i=idx: self._update_condition(rule_type, i, "genre_ids", e.value)
                            ).props("dense filled").classes("flex-1").tooltip("TMDB的类型ID，多个用逗号分隔")

                            # 国家
                            ui.input(
                                value=conds.get("origin_country", ""),
                                label="国家代码 (country)",
                                placeholder="CN, US",
                                on_change=lambda e, i=idx: self._update_condition(rule_type, i, "origin_country",
                                                                                  e.value)
                            ).props("dense filled").classes("flex-1").tooltip("ISO 3166-1 国家代码")

                        with ui.row().classes("w-full gap-2 items-center"):
                            ui.label("       ").classes("w-[50px]")  # 占位对齐
                            # 语言
                            ui.input(
                                value=conds.get("original_language", ""),
                                label="原始语言 (lang)",
                                placeholder="zh, ja",
                                on_change=lambda e, i=idx: self._update_condition(rule_type, i, "original_language",
                                                                                  e.value)
                            ).props("dense filled").classes("flex-1")

                            # 后缀
                            ui.input(
                                value=conds.get("ext", ""),
                                label="文件后缀 (ext)",
                                placeholder="iso, bdmv",
                                on_change=lambda e, i=idx: self._update_condition(rule_type, i, "ext", e.value)
                            ).props("dense filled").classes("flex-1")

                # 添加按钮
                with ui.row().classes("w-full justify-center mt-2"):
                    RedButton("➕ 添加新规则", on_click=lambda: self._add_rule(rule_type, _refresh)).props(
                        "outline dashed w-full")

        _refresh()

    def _update_rule(self, r_type, idx, key, value):
        self.editing_rules[r_type][idx][key] = value

    def _update_condition(self, r_type, idx, key, value):
        # 确保 conditions 字典存在
        if "conditions" not in self.editing_rules[r_type][idx]:
            self.editing_rules[r_type][idx]["conditions"] = {}

        # 如果值为空，建议删除该键，或者是存为空字符串
        if not value:
            if key in self.editing_rules[r_type][idx]["conditions"]:
                self.editing_rules[r_type][idx]["conditions"][key] = ""
        else:
            self.editing_rules[r_type][idx]["conditions"][key] = value

    def _move_rule(self, r_type, idx, direction, refresh_cb):
        lst = self.editing_rules[r_type]
        new_idx = idx + direction
        if 0 <= new_idx < len(lst):
            lst[idx], lst[new_idx] = lst[new_idx], lst[idx]
            refresh_cb()

    def _delete_rule(self, r_type, idx, refresh_cb):
        self.editing_rules[r_type].pop(idx)
        refresh_cb()

    def _add_rule(self, r_type, refresh_cb):
        # 添加一个空规则
        self.editing_rules[r_type].append({"name": "新分类", "conditions": {}})
        refresh_cb()

    def _reset_rules(self, dialog):
        self.editing_rules = copy.deepcopy(DEFAULT_SECONDARY_RULES)
        dialog.close()
        self._open_secondary_rules_editor()  # 重新打开以刷新界面
        ui.notify("已重置为默认规则，请点击保存生效", type="info")

    def _save_rules(self, dialog):
        # 保存到配置管理器
        cm.set_config("secondary_rules", self.editing_rules)
        # 同时更新 self.config 以便界面其他部分知道（虽然 secondary_rules 不在主界面显示）
        setattr(self.config, "secondary_rules", self.editing_rules)
        logger.info(f"[配置] 二级分类规则已更新")
        ui.notify("✅ 规则保存成功", type="positive")
        dialog.close()

    async def pick(self, *, key: str) -> None:
        result = await local_file_picker("~", multiple=True)
        if isinstance(result, Sequence):
            result = result[0]
        logger.info(f"[配置] {key} 选择了 {result}")
        self._change(key, result)

    def _change(self, key: str, value: str) -> None:
        setattr(self.config, key, value)

    async def _handle_ok(self):
        # 验证URL配置项
        url_configs = ["ai_base_url", "gemini_base_url"]
        for url_config in url_configs:
            if hasattr(self.config, url_config):
                url_value = getattr(self.config, url_config)
                if url_value and not cm.validate_url(url_value):
                    ui.notify(
                        f"❌ {CN_MAP.get(url_config, url_config)} 格式无效",
                        type="negative",
                    )
                    return
        # 提取需要立即扫描的目录配置
        scan_targets = []
        if hasattr(self.config, 'monitor_paths') and isinstance(self.config.monitor_paths, list):
            # 遍历列表查找 scan_now 标记
            for item in self.config.monitor_paths:
                if isinstance(item, dict) and item.get('scan_now') is True:
                    # 创建副本用于扫描任务（避免修改原配置影响保存）
                    target_config = item.copy()
                    # 从配置中移除临时的 scan_now 标记，确保不会被永久保存到 config.json
                    del target_config['scan_now']
                    scan_targets.append(target_config)

                    # 重置当前配置中的标记，防止下次打开时开关还是开着的
                    item['scan_now'] = False

        # 保存所有配置
        for cn in self.config.__dict__:
            cm.set_config(
                cn,
                getattr(self.config, cn),
            )
        config_show = cm.config.copy()
        for key in config_show.keys():
            if "api_key" in key:
                config_show[key] = len(str(config_show[key])) * "*"

        logger.info("[配置] 配置已修改为： {}".format(config_show))
        ui.notify("✅ 配置保存成功", type="positive")
        # 更新运行时日志级别
        try:
            update_log_level_from_config()
            logger.info(f"[配置] 运行时日志级别已更新为 {cm.get_config('log_level')}")
        except Exception as e:
            logger.error(f"[配置] 更新运行时日志级别失败: {e}")

        try:
            await monitor_manager.restart_from_config()
            logger.info("[配置] 已根据新配置重启目录监控")
        except Exception as e:
            logger.error(f"[配置] 重启目录监控失败: {e}")
            ui.notify(f"⚠️ 重启目录监控失败: {e}", type="warning")
        self.close()
        if scan_targets:
            ui.notify(f"🚀 已触发 {len(scan_targets)} 个目录的全量扫描...", type='info')
            try:
                await run.io_bound(self._execute_immediate_scans, scan_targets)
                ui.notify(f"✅ 全量扫描已完成", type='positive')
            except Exception as e:
                logger.error(f"扫描出错: {e}")
                ui.notify(f"扫描出错: {e}", type='negative')

    def _execute_immediate_scans(self, scan_configs: list):
        """
        遍历指定目录，将所有视频文件加入监控队列
        """
        exclude_dirs_conf = cm.get_config("monitor_exclude_dirs") or []
        exclude_patterns = []
        for p in exclude_dirs_conf:
            if p:
                try:
                    exclude_patterns.append(re.compile(p, re.IGNORECASE))
                except:
                    pass

        count = 0
        try:
            for cfg in scan_configs:
                root_path_str = cfg.get('path')
                if not root_path_str: continue

                root_path = Path(root_path_str)
                if not root_path.exists():
                    logger.warning(f"[全量扫描] 目录不存在: {root_path}")
                    continue

                logger.info(f"[全量扫描] 开始扫描: {root_path}")

                task_options = {
                    "config_overrides": cfg
                }

                # 遍历目录
                for root, dirs, files in os.walk(root_path):
                    should_skip_dir = False
                    root_str = str(Path(root)).replace('\\', '/')

                    for pattern in exclude_patterns:
                        if pattern.search(root_str):
                            should_skip_dir = True
                            break

                    if should_skip_dir:
                        dirs[:] = []
                        logger.debug(f"[全量扫描] 跳过排除目录: {root}")
                        continue

                    dirs[:] = [d for d in dirs if not d.startswith('.')]

                    for file in files:
                        if file.startswith('.'): continue

                        file_path = Path(root) / file

                        if is_video_file(file):
                            monitor_service.add_manual_task(file_path, task_options)
                            count += 1

            if count > 0:
                logger.info(f"[全量扫描] 扫描完成，已添加 {count} 个任务到队列")
                # 这里不能直接调用 ui.notify，因为它在后台线程运行
            else:
                logger.info("[全量扫描] 扫描完成，未发现新任务")

        except Exception as e:
            logger.error(f"[全量扫描] 执行出错: {e}")

    def _get_current_ui_config(self) -> dict:
        """获取当前界面的配置（未保存的）"""
        current_config = {}
        ai_config_keys = [
            "ai_enabled",
            "ai_auto_save",
            "ai_provider",
            "ai_confidence_threshold",
            "openai_output_format",
            "ai_api_key",
            "ai_base_url",
            "ai_model",
            "gemini_api_key",
            "gemini_base_url",
            "gemini_model",
            "ai_temperature",
            "gemini_temperature",
        ]
        for key in ai_config_keys:
            # 优先使用界面中的值，如果没有则使用配置文件中的值
            if hasattr(self.config, key):
                current_config[key] = getattr(self.config, key)
            else:
                current_config[key] = cm.get_config(key)
        return current_config

    async def _test_ai_recognition(self):
        """测试AI识别功能（使用当前界面配置）"""
        try:
            ui.notify("🧪 开始测试AI识别功能，请稍候...", type="info")
            current_config = self._get_current_ui_config()

            from ..ai.unified_ai_tester import UnifiedAITester

            tester = UnifiedAITester(current_config)

            import asyncio

            result = await asyncio.get_event_loop().run_in_executor(
                None, tester.test_ai_recognition
            )

            self._show_ai_test_results(result)
        except Exception as e:
            logger.error(f"[配置] AI识别测试失败: {str(e)}")
            ui.notify(f"❌ AI识别测试失败: {str(e)}", type="negative")

    async def _test_openai_api(self):
        """测试OpenAI API功能（使用当前界面配置，测试多种输出格式）"""
        try:
            ui.notify("⚙️ 开始测试OpenAI API功能，请稍候...", type="info")
            current_config = self._get_current_ui_config()

            # 检查基本配置
            if not current_config.get("ai_api_key"):
                ui.notify("❌ 请先配置OpenAI API密钥", type="negative")
                return

            if current_config.get("ai_provider", "openai").lower() != "openai":
                ui.notify("❌ 此测试仅支持OpenAI提供商", type="negative")
                return

            from ..ai.unified_ai_tester import UnifiedAITester

            tester = UnifiedAITester(current_config)

            import asyncio

            results = await asyncio.get_event_loop().run_in_executor(
                None, tester.test_openai_api_formats
            )

            self._show_openai_formats_test_results(results)
        except Exception as e:
            logger.error(f"[配置] OpenAI API测试失败: {str(e)}")
            ui.notify(f"❌ OpenAI API测试失败: {str(e)}", type="negative")

    def _show_ai_test_results(self, result: dict):
        """显示AI识别测试结果"""
        with ui.dialog() as dialog, ui.card().classes("w-[600px]"):
            ui.label("🧪 AI识别功能测试结果").classes("text-h6 mb-4")

            # 配置提示
            ui.label("💡 此测试使用界面中的配置，但不会保存配置").classes(
                "text-sm text-blue mb-4"
            )

            with ui.column().classes("w-full gap-3"):
                # 基本信息 - 根据结果状态显示
                result_status = result.get("result_status", "unknown")

                if result_status == "perfect":
                    status_icon = "✅"
                    status_text = "完全正确"
                    status_color = "text-green"
                elif result_status == "validation_failed":
                    status_icon = "⚠️"
                    status_text = "验证失败"
                    status_color = "text-orange"
                elif result_status == "ai_failed":
                    status_icon = "❌"
                    status_text = "AI失败"
                    status_color = "text-red"
                else:
                    status_icon = "❓"
                    status_text = "未知状态"
                    status_color = "text-gray"

                ui.label(f"{status_icon} 测试状态: {status_text}").classes(
                    f"font-bold {status_color}"
                )

                # 配置信息
                config_used = result.get("config_used", {})
                provider = config_used.get("ai_provider", "unknown")
                ui.label(f"🤖 AI提供商: {provider.upper()}")
                ui.label(f"⏱️ 耗时: {result.get('duration', 0):.2f}秒")

                if provider.lower() == "openai":
                    output_format = config_used.get("openai_output_format", "unknown")
                    ui.label(f"📋 输出格式: {output_format}")

                # AI失败情况：显示错误信息
                if result_status == "ai_failed":
                    ui.separator()
                    ui.label("❌ 错误详情").classes("font-bold text-red")
                    if result.get("error"):
                        ui.label(f"错误信息: {result['error']}").classes("text-red")
                    else:
                        ui.label("AI分析返回None，可能是API调用失败或解析错误").classes(
                            "text-red"
                        )

                # 验证失败和完全正确情况：显示详细结果
                elif result_status in ["validation_failed", "perfect"] and result.get(
                    "validation"
                ):
                    validation = result["validation"]
                    ui.separator()
                    ui.label("📊 分析结果").classes("font-bold")

                    confidence = validation.get("confidence", "None")
                    ui.label(f"🎯 置信度: {confidence}")

                    file_count = validation.get("file_mapping_count", 0)
                    ui.label(f"📁 映射文件数: {file_count}")

                    # 验证详情
                    if "validation_details" in validation:
                        details = validation["validation_details"]
                        if "accuracy" in details:
                            accuracy = details["accuracy"] * 100
                            accuracy_color = (
                                "text-green" if accuracy == 100 else "text-orange"
                            )
                            ui.label(f"✅ 准确率: {accuracy:.1f}%").classes(
                                accuracy_color
                            )

                            matched_count = details.get("matched_count", 0)
                            expected_count = details.get("expected_count", 0)
                            ui.label(f"📈 匹配情况: {matched_count}/{expected_count}")

                            # 显示详细的文件匹配情况
                            missing_files = details.get("missing_files", [])
                            extra_files = details.get("extra_files", [])
                            matched_files = details.get("matched_files", [])

                            if matched_files:
                                ui.label(
                                    f"✅ 正确匹配 ({len(matched_files)}):"
                                ).classes("text-green font-bold")
                                for file_path in matched_files:
                                    ui.label(f"  • {file_path}").classes(
                                        "text-sm text-green"
                                    )

                            if missing_files:
                                ui.label(
                                    f"❌ 遗漏文件 ({len(missing_files)}):"
                                ).classes("text-red font-bold")
                                for file_path in missing_files:
                                    ui.label(f"  • {file_path}").classes(
                                        "text-sm text-red"
                                    )

                            if extra_files:
                                ui.label(f"⚠️ 多余文件 ({len(extra_files)}):").classes(
                                    "text-orange font-bold"
                                )
                                for file_path in extra_files:
                                    ui.label(f"  • {file_path}").classes(
                                        "text-sm text-orange"
                                    )

            # 关闭按钮
            with ui.row().classes("w-full justify-end mt-4"):
                RedButton("关闭", on_click=dialog.close)

        dialog.open()

    def _show_openai_formats_test_results(self, results: dict):
        """显示OpenAI多格式测试结果"""
        with ui.dialog() as dialog, ui.card().classes("w-[700px]"):
            ui.label("⚙️ OpenAI API多格式测试结果").classes("text-h6 mb-4")

            # 配置提示
            ui.label("💡 此测试使用界面中的配置，但不会保存配置").classes(
                "text-sm text-blue mb-4"
            )

            with ui.column().classes("w-full gap-3"):
                # 总体结果
                overall_success = results.get("success", False)
                success_icon = "✅" if overall_success else "❌"
                ui.label(
                    f"{success_icon} 总体状态: {'至少一种格式成功' if overall_success else '所有格式均失败'}"
                ).classes("font-bold")

                if results.get("error"):
                    ui.label(f"❌ 错误信息: {results['error']}").classes("text-red")

                # 推荐格式
                if overall_success:
                    recommended = results.get("recommended_format", "text")
                    ui.label(f"🌟 推荐格式: {recommended}").classes(
                        "text-green font-bold"
                    )

                ui.separator()

                # 各格式详细结果
                format_results = results.get("format_results", [])
                for format_result in format_results:
                    output_format = format_result.get("output_format", "unknown")
                    result_status = format_result.get("result_status", "unknown")

                    # 根据结果状态确定图标和标题
                    if result_status == "perfect":
                        icon = "✅"
                        status_text = "完全正确"
                        status_color = "text-green"
                    elif result_status == "validation_failed":
                        icon = "⚠️"
                        status_text = "验证失败"
                        status_color = "text-orange"
                    elif result_status == "ai_failed":
                        icon = "❌"
                        status_text = "AI失败"
                        status_color = "text-red"
                    else:
                        icon = "❓"
                        status_text = "未知状态"
                        status_color = "text-gray"

                    with ui.expansion(
                        f"{icon} {output_format} - {status_text}", icon="settings"
                    ).classes("w-full"):
                        with ui.column().classes("gap-2 p-2"):
                            ui.label(f"状态: {status_text}").classes(
                                status_color + " font-bold"
                            )
                            ui.label(f"耗时: {format_result.get('duration', 0):.2f}秒")

                            # AI失败情况：显示错误信息
                            if result_status == "ai_failed":
                                if format_result.get("error"):
                                    ui.label(
                                        f"错误详情: {format_result['error']}"
                                    ).classes("text-red")
                                else:
                                    ui.label(
                                        "AI分析返回None，可能是API调用失败或解析错误"
                                    ).classes("text-red")

                            # 验证失败和完全正确情况：显示详细结果
                            elif result_status in [
                                "validation_failed",
                                "perfect",
                            ] and format_result.get("validation"):
                                validation = format_result["validation"]
                                confidence = validation.get("confidence", "None")
                                ui.label(f"置信度: {confidence}")

                                file_count = validation.get("file_mapping_count", 0)
                                ui.label(f"映射文件数: {file_count}")

                                if "validation_details" in validation:
                                    details = validation["validation_details"]
                                    if "accuracy" in details:
                                        accuracy = details["accuracy"] * 100
                                        accuracy_color = (
                                            "text-green"
                                            if accuracy == 100
                                            else "text-orange"
                                        )
                                        ui.label(f"准确率: {accuracy:.1f}%").classes(
                                            accuracy_color
                                        )

                                        matched_count = details.get("matched_count", 0)
                                        expected_count = details.get(
                                            "expected_count", 0
                                        )
                                        ui.label(
                                            f"匹配情况: {matched_count}/{expected_count}"
                                        )

                                        # 显示详细的文件匹配情况
                                        missing_files = details.get("missing_files", [])
                                        extra_files = details.get("extra_files", [])
                                        matched_files = details.get("matched_files", [])

                                        if matched_files:
                                            ui.label(
                                                f"✅ 正确匹配 ({len(matched_files)}):"
                                            ).classes("text-green font-bold")
                                            for file_path in matched_files:
                                                ui.label(f"  • {file_path}").classes(
                                                    "text-sm text-green"
                                                )

                                        if missing_files:
                                            ui.label(
                                                f"❌ 遗漏文件 ({len(missing_files)}):"
                                            ).classes("text-red font-bold")
                                            for file_path in missing_files:
                                                ui.label(f"  • {file_path}").classes(
                                                    "text-sm text-red"
                                                )

                                        if extra_files:
                                            ui.label(
                                                f"⚠️ 多余文件 ({len(extra_files)}):"
                                            ).classes("text-orange font-bold")
                                            for file_path in extra_files:
                                                ui.label(f"  • {file_path}").classes(
                                                    "text-sm text-orange"
                                                )

            # 关闭按钮
            with ui.row().classes("w-full justify-end mt-4"):
                RedButton("关闭", on_click=dialog.close)

        dialog.open()

    def _render_monitor_paths_editor(self):
        """渲染监控路径编辑器"""
        # 获取当前配置，兼容旧的字符串列表格式
        current_data = cm.get_config("monitor_paths") or []

        self.monitor_paths_data = []
        for item in current_data:
            if isinstance(item, str):
                # 兼容旧格式，补全字典
                self.monitor_paths_data.append({
                    "path": item,
                    "tv_format": "",
                    "movie_format": "",
                    "target_tv_dir": "",
                    "target_movie_dir": "",
                    "scan_now": False,
                })
            elif isinstance(item, dict):
                # 补全可能缺少的字段
                item.setdefault("tv_format", "")
                item.setdefault("movie_format", "")
                item.setdefault("target_tv_dir", "")
                item.setdefault("target_movie_dir", "")
                item.setdefault("scan_now", False)
                self.monitor_paths_data.append(item)

        self.path_container = ui.column().classes("w-full gap-2")

        def _refresh_list():
            self.path_container.clear()
            with self.path_container:
                for idx, item in enumerate(self.monitor_paths_data):
                    with ui.card().classes("w-full p-2 border-1 border-gray-200"):
                        with ui.row().classes("w-full items-center gap-2"):
                            # 路径输入框
                            path_input = ui.input(
                                label=f"监控路径 #{idx + 1}",
                                value=item["path"],
                                on_change=lambda e, i=idx: self._update_path_data(i, "path", e.value)
                            ).props("filled dense").style("flex-grow: 1")

                            # 选择文件夹按钮
                            RedButton("📂",
                                      on_click=lambda i=idx, inp=path_input: self._pick_folder_for_item(i, inp)).props(
                                "dense flat")

                            # 删除按钮
                            RedButton("🗑️", on_click=lambda i=idx: _remove_path(i)).props("dense flat color=grey")

                        # 高级配置折叠面板
                        with ui.expansion("监控配置 (点击展开)", icon="settings").classes("w-full text-sm text-gray-600"):
                            with ui.column().classes("w-full gap-2 p-2 bg-gray-50"):
                                with ui.row().classes("w-full items-center justify-between bg-blue-50 p-2 rounded"):
                                    ui.label("🚀 立即操作").classes("font-bold text-blue-800")
                                    ui.switch(
                                        "保存后立即全量扫描此目录",
                                        value=item.get('scan_now', False),
                                        on_change=lambda e, i=idx: self._update_path_data(i, "scan_now", e.value)
                                    ).props("dense color=blue").tooltip("开启后，点击保存配置时会立即遍历该目录并将所有视频加入任务队列")
                                ui.label("💡 留空或不选表示使用全局默认设置").classes("text-xs text-gray-400 mb-1")
                                # === 0. 目标输出目录 (Priority) ===
                                ui.label("📁 目标输出目录 (覆盖全局设置)").classes("font-bold text-xs mt-1")
                                with ui.row().classes("w-full gap-2"):
                                    # TV 目标目录
                                    tv_dir_input = ui.input(
                                        label="📺 电视剧目标目录",
                                        value=item.get("target_tv_dir", ""),
                                        placeholder="留空则使用全局配置",
                                        on_change=lambda e, i=idx: self._update_path_data(i, "target_tv_dir", e.value)
                                    ).props("filled dense").classes("flex-1")
                                    RedButton("📂", on_click=lambda i=idx, inp=tv_dir_input: self._pick_folder_for_item(i, inp)).props("dense flat")

                                    # Movie 目标目录
                                    mv_dir_input = ui.input(
                                        label="🎬 电影目标目录",
                                        value=item.get("target_movie_dir", ""),
                                        placeholder="留空则使用全局配置",
                                        on_change=lambda e, i=idx: self._update_path_data(i, "target_movie_dir", e.value)
                                    ).props("filled dense").classes("flex-1")
                                    RedButton("📂", on_click=lambda i=idx, inp=mv_dir_input: self._pick_folder_for_item(i, inp)).props("dense flat")

                                ui.separator().classes("my-1")
                                # === 1. 重命名模板 ===
                                with ui.row().classes("w-full gap-2"):
                                    ui.input(label="📺 TV模板", value=item.get("tv_rename_format", ""),
                                             on_change=lambda e, i=idx: self._update_path_data(i, "tv_rename_format", e.value)
                                             ).props("filled dense").classes("flex-1")
                                    ui.input(label="🎬 Movie模板", value=item.get("movie_rename_format", ""),
                                             on_change=lambda e, i=idx: self._update_path_data(i, "movie_rename_format", e.value)
                                             ).props("filled dense").classes("flex-1")

                                # === 2. 行为控制 (Mode, Overwrite) ===
                                with ui.row().classes("w-full gap-2"):
                                    # 模式选择
                                    current_mode = item.get("mode") or "默认"
                                    ui.select(
                                        options=["默认", "硬链接", "软链接", "复制", "剪切"],
                                        value=current_mode,
                                        label="重命名模式",
                                        on_change=lambda e, i=idx: self._update_path_data(i, "mode", None if e.value == "默认" else e.value)
                                    ).props("dense outlined").classes("flex-1")

                                    # 覆盖模式
                                    current_ov = item.get("overwrite_mode") or "默认"
                                    ui.select(
                                        options=["默认", "从不覆盖", "总是覆盖", "保留最新"],
                                        value=current_ov,
                                        label="覆盖模式",
                                        on_change=lambda e, i=idx: self._update_path_data(i, "overwrite_mode", None if e.value == "默认" else e.value)
                                    ).props("dense outlined").classes("flex-1")

                                # === 3. 开关控制 (刮削, 二级分类) ===
                                with ui.row().classes("w-full gap-4 items-center"):
                                    # 刮削
                                    meta_val = item.get("scrape_metadata")
                                    meta_label = "默认" if meta_val is None else ("启用" if meta_val else "禁用")
                                    ui.select(
                                        options=["默认", "启用", "禁用"],
                                        value=meta_label,
                                        label="刮削元数据",
                                        on_change=lambda e, i=idx: self._update_path_data(i, "scrape_metadata", True if e.value=="启用" else (False if e.value=="禁用" else None))
                                    ).props("dense outlined").classes("w-32")

                                    # 二级分类
                                    sec_val = item.get("secondary_classification")
                                    sec_label = "默认" if sec_val is None else ("启用" if sec_val else "禁用")
                                    ui.select(
                                        options=["默认", "启用", "禁用"],
                                        value=sec_label,
                                        label="二级分类",
                                        on_change=lambda e, i=idx: self._update_path_data(i, "secondary_classification", True if e.value=="启用" else (False if e.value=="禁用" else None))
                                    ).props("dense outlined").classes("w-32")

                                # === 4. 图片类型 ===
                                image_options = [
                                    "poster", "backdrop", "background",
                                    "banner", "logo", "clearart", "thumb"
                                ]
                                current_img_types = item.get("scrape_image_types")
                                # 如果是 None，NiceGUI select多选模式可能显示为空，我们接受它为空，表示"未设置/Global"
                                # 用户如果想选择，就会覆盖；如果全取消选择，变成空列表，save时存为None（回退Global）

                                ui.select(
                                    options=image_options,
                                    multiple=True,
                                    value=current_img_types,
                                    label="刮削图片类型 (不选则使用全局设置)",
                                    on_change=lambda e, i=idx: self._update_path_data(
                                        i,
                                        "scrape_image_types",
                                        e.value if e.value else None # 如果列表非空则保存列表，空则保存None以使用全局配置
                                    )
                                ).props("use-chips dense outlined").classes("w-full")


        def _add_path():
            self.monitor_paths_data.append({"path": "", "tv_format": "", "movie_format": ""})
            _refresh_list()

        def _remove_path(index):
            if 0 <= index < len(self.monitor_paths_data):
                self.monitor_paths_data.pop(index)
                _refresh_list()

        # 初始渲染
        _refresh_list()

        # 添加按钮
        RedButton("➕ 添加监控目录", on_click=_add_path).props("outline classes=w-full dashed")

    # 辅助方法：更新特定行的数据并同步回配置
    def _update_path_data(self, index, key, value):
        if 0 <= index < len(self.monitor_paths_data):
            self.monitor_paths_data[index][key] = value
            # 实时同步回 self.config，以便保存
            setattr(self.config, "monitor_paths", self.monitor_paths_data)

    # 辅助方法：为特定行选择文件夹
    async def _pick_folder_for_item(self, index, input_element):
        result = await local_file_picker("~", multiple=False)  # 这里改为单选比较安全
        if result:
            if isinstance(result, list): result = result[0]
            input_element.value = result
            self._update_path_data(index, "path", result)


async def config_page() -> None:
    await ConfigPage()
