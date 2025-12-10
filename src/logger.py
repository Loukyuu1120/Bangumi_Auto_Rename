import logging
from logging.handlers import TimedRotatingFileHandler
from collections import deque
import structlog
from .utils.path import log_path
from .config.config_manager import cm

ui_log_history = deque(maxlen=200)

class UIHandler(logging.Handler):
    """
    自定义处理器：将日志格式化后存入 deque
    """
    def __init__(self):
        super().__init__()
        # 强制设置格式：[INFO] 12:00:00 | 消息内容
        self.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s | %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            # 过滤掉 nicegui 内部日志，防止无限递归或刷屏
            if record.name.startswith("nicegui") or record.name.startswith("watchfiles"):
                return
            msg = self.format(record)
            ui_log_history.append(msg)
        except Exception:
            self.handleError(record)

# 实例化 UI 处理器
ui_handler = UIHandler()

file_handler = TimedRotatingFileHandler(
    filename=log_path,
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8",
)
file_handler.suffix = "%Y-%m-%d"
file_formatter = structlog.stdlib.ProcessorFormatter(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.stdlib.add_log_level,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ]
)
file_handler.setFormatter(file_formatter)

console_formatter = structlog.stdlib.ProcessorFormatter(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.dev.ConsoleRenderer(),
    ]
)
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)

# 获取日志级别配置
log_level_str = cm.get_config("log_level") or "INFO"
log_level = getattr(logging, log_level_str.upper(), logging.INFO)

console_handler.setLevel(log_level)
file_handler.setLevel(log_level)
ui_handler.setLevel(log_level) # UI 也遵循全局日志级别

logging.basicConfig(
    level=log_level,
    handlers=[file_handler, console_handler, ui_handler],
)

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=False,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
logging.getLogger('niceGUI').propagate = False
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

def update_log_level_from_config() -> None:
    """Update log levels based on config."""
    log_level_str = cm.get_config("log_level") or "INFO"
    level = getattr(logging, log_level_str.upper(), logging.INFO)
    console_handler.setLevel(level)
    file_handler.setLevel(level)
    ui_handler.setLevel(level) # 记得这里也要更新
    logging.getLogger().setLevel(level)
