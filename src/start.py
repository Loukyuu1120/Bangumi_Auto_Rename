import sys
from pathlib import Path
import json
import threading

sys.path.append(str(Path(__file__).parents[2]))
# __package__ = 'Bangumi_Auto_Rename.src'

from .logger import logger  # noqa: E402
from .web import ui  # noqa: E402 # type: ignore
from .monitor.monitor import start_monitoring
from .config.config_manager import cm

if __name__ == "__main__":
    logger.info("程序启动中...")

    # 启动监控
    if cm.get_config("monitor_enabled"):
        monitor_paths_conf = cm.get_config("monitor_paths") or []
        exclude_dirs = cm.get_config("monitor_exclude_dirs") or []

        # 兼容旧配置：如果存成了字符串，就尝试按 JSON 解析
        if isinstance(monitor_paths_conf, str):
            try:
                monitor_paths_conf = json.loads(monitor_paths_conf)
            except Exception:
                monitor_paths_conf = []

        if isinstance(exclude_dirs, str):
            try:
                exclude_dirs = json.loads(exclude_dirs)
            except Exception:
                exclude_dirs = []

        for path_str in monitor_paths_conf:
            path = Path(path_str)
            if path.exists() and path.is_dir():
                monitor_thread = threading.Thread(
                    target=start_monitoring, args=(path, exclude_dirs)
                )
                monitor_thread.daemon = True
                monitor_thread.start()
                logger.info(f"[主程序] 已为目录 {path} 启动监控线程")
            else:
                logger.warning(f"[主程序] 监控目录 {path} 不存在或不是一个目录，跳过")

ui.run(
    port=5999,
    storage_secret="KEY233WuyiDay",
    title="番剧自动重命名",
    favicon="🍭",
    log_config=None,
    reload=False,
    uvicorn_reload_excludes="*.log",
)
