import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[2]))

from .logger import logger  # noqa: E402
from .web import ui  # noqa: E402 # type: ignore
from .monitor.monitor_manager import monitor_manager


if __name__ == "__main__":
    logger.info("程序启动中...")

    monitor_manager.start_from_config()

ui.run(
    port=5999,
    storage_secret="KEY233WuyiDay",
    title="番剧自动重命名",
    favicon="🍭",
    log_config=None,
    reload=False,
    uvicorn_reload_excludes="*.log",
)
