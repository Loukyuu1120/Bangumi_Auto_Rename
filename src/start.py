import sys
from pathlib import Path
from nicegui import app, run

sys.path.append(str(Path(__file__).parents[2]))

from .logger import logger  # noqa: E402
from .web import ui  # noqa: E402 # type: ignore
from .monitor.monitor_manager import monitor_manager


async def startup_monitor():
    logger.info("正在后台启动监控服务...")
    try:
        await monitor_manager.start_from_config()
        logger.info("监控服务启动流程已在后台完成")
    except Exception as e:
        logger.error(f"监控服务启动失败: {e}")


if __name__ in {"__main__", "__mp_main__"}:
    logger.info("程序启动中...")

    # 注册启动任务
    app.on_startup(startup_monitor)

    ui.run(
        host="0.0.0.0",
        port=5999,
        storage_secret="KEY233WuyiDay",
        title="番剧自动重命名",
        favicon="🍭",
        log_config=None,
        reload=False,
        uvicorn_reload_excludes="*.log",
    )
