import os
import json
import shutil
from typing import Dict
from pathlib import Path

from ..logger import logger
from ..utils.path import RECORD_PATH
from ..config.config_manager import cm

from src.rename.utils import VIDEO_SUFFIX

class Trans:
    def __init__(self, R: Dict[Path, Path], uuid: str) -> None:
        self.mode = cm.get_config('mode')
        self.overwrite_mode = cm.get_config('overwrite_mode')
        self.R = R
        self.uuid = uuid

    def _cleanup_old_targets_with_prefix(self) -> None:
        """根据上一轮记录，删除旧目标文件及同前缀的相关文件，
        额外：
        - 删除所在 Season 目录里的 season.nfo
        - 如果 Season 目录没有视频文件，则强行清空并删除；
        - 如果是 Season 目录，且上级目录下没有包含视频文件的子目录，则强行清空并删除上级目录。
        """
        record_file = RECORD_PATH / f'{self.uuid}.json'
        if not record_file.exists():
            return

        try:
            with record_file.open('r', encoding='utf-8') as f:
                old_map = json.load(f)  # {source_str: target_str}
        except Exception as e:
            logger.warning(f'[处理迁移] 读取旧记录失败 {record_file}: {e}')
            return

        if not old_map:
            return

        # --- 获取当前任务的所有目标路径和目标目录，用于防误删 ---
        current_target_paths = set(self.R.values())
        current_target_dirs = set(p.parent for p in current_target_paths)

        dirs_to_check: set[Path] = set()
        possible_show_roots: set[Path] = set()

        for _, target_str in old_map.items():
            try:
                t = Path(target_str)
            except TypeError:
                continue

            # --- 如果旧目标路径存在于当前新任务中，说明是同一文件重做，跳过清理 ---
            if t in current_target_paths:
                continue

            parent = t.parent
            if not parent.exists() or not parent.is_dir():
                continue

            # 如果是 Season 目录，记录一下上级目录，后面用于“整部剧目录是否残留视频”的判断
            if parent.name.lower().startswith('season'):
                possible_show_roots.add(parent.parent)

            prefix = t.stem  # 旧目标文件名（不含后缀）的前缀

            try:
                for child in parent.iterdir():
                    if not child.is_file():
                        continue

                    # 1) 删除同前缀文件：视频本体 + 相关元数据
                    if child.stem.startswith(prefix):
                        try:
                            logger.info(f'[处理迁移] 删除旧文件(含元数据): {child}')
                            child.unlink()
                        except Exception as e:
                            logger.warning(f'[处理迁移] 删除文件失败 {child}: {e}')
                        continue

                    # 2) 删除同目录的 season.nfo（不论前缀）
                    elif child.name.lower() == 'season.nfo':
                        if parent in current_target_dirs:
                            continue
                        try:
                            logger.info(f'[处理迁移] 删除 season.nfo: {child}')
                            child.unlink()
                        except Exception as e:
                            logger.warning(f'[处理迁移] 删除 season.nfo 失败 {child}: {e}')
                        continue

                dirs_to_check.add(parent)
            except Exception as e:
                logger.warning(f'[处理迁移] 枚举目录失败 {parent}: {e}')

        # 辅助函数：判断目录是否包含视频文件（递归）
        def has_video_files(directory: Path) -> bool:
            try:
                for item in directory.rglob('*'): # 递归扫描所有子文件
                    if item.is_file() and item.suffix.lower() in VIDEO_SUFFIX:
                        return True
            except Exception:
                pass
            return False

        # 辅助函数：强制清理目录（删除里面剩余的 nfo/图片等垃圾文件，然后删目录）
        def force_cleanup_dir(directory: Path):
            try:
                for item in directory.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        # 递归清理子目录
                        force_cleanup_dir(item)
                directory.rmdir()
                logger.info(f'[处理迁移] 已清理无视频目录: {directory}')
            except Exception as e:
                logger.warning(f'[处理迁移] 清理目录失败 {directory}: {e}')

        # 3) 从深到浅尝试删除已经没有视频文件的 Season 目录
        all_dirs = sorted(dirs_to_check, key=lambda p: len(p.parts), reverse=True)
        for d in all_dirs:
            if d in current_target_dirs:
                continue
            try:
                if d.exists() and d.is_dir():
                    if not has_video_files(d):
                        logger.info(f'[处理迁移] 目录 {d.name} 内已无视频文件，执行清理...')
                        force_cleanup_dir(d)
            except Exception:
                pass

        # 4) 对可能的 show 根目录：如果下面已经没有任何“视频文件”，则删除整部剧目录
        for show_root in possible_show_roots:
            try:
                if not show_root.exists() or not show_root.is_dir():
                    continue

                if not has_video_files(show_root):
                    logger.info(f'[处理迁移] 剧集根目录 {show_root.name} 内已无视频文件，执行清理...')
                    force_cleanup_dir(show_root)

            except Exception as e:
                logger.warning(f'[处理迁移] 检查/删除剧集根目录失败 {show_root}: {e}')

    def trans_file(self):
        path = RECORD_PATH / f'{self.uuid}.json'

        # 在 复制 / 链接 模式下，先清理上一轮生成的旧内容
        if self.mode in ('复制', '链接'):
            self._cleanup_old_targets_with_prefix()

        # 记录本次映射
        _R = {str(k): str(v) for k, v in self.R.items()}
        with path.open('w', encoding='utf-8') as f:
            json.dump(_R, f, ensure_ascii=False)

        # 执行实际迁移
        for source_path, target_path in self.R.items():
            try:
                if target_path.is_dir() or source_path.is_dir():
                    continue
                if not target_path.parent.exists():
                    target_path.parent.mkdir(parents=True)
                if target_path.exists():
                    if self.overwrite_mode == '从不覆盖':
                        logger.warning(f'[处理迁移] 跳过已存在文件: {target_path.name}')
                        continue

                    elif self.overwrite_mode == '总是覆盖':
                        logger.info(f'[处理迁移] 覆盖已存在文件: {target_path.name}')
                        target_path.unlink()  # 删除旧文件以便重新生成

                    elif self.overwrite_mode == '保留最新':
                        try:
                            src_mtime = source_path.stat().st_mtime
                            dst_mtime = target_path.stat().st_mtime
                            if src_mtime > dst_mtime:
                                logger.info(f'[处理迁移] 源文件较新，覆盖: {target_path.name}')
                                target_path.unlink()
                            else:
                                logger.warning(f'[处理迁移] 目标文件较新，跳过: {target_path.name}')
                                continue
                        except Exception:
                            continue
                if self.mode == '剪切':
                    shutil.move(source_path, target_path)
                elif self.mode == '复制':
                    shutil.copy(source_path, target_path)
                elif self.mode == '链接' or self.mode == '硬链接':
                    try:
                        os.link(source_path, target_path)
                    except Exception:
                        logger.warning('[处理迁移] 无法创建硬链接, 尝试软链接...')
                        os.symlink(source_path, target_path)
                elif self.mode == '软链接':
                    os.symlink(source_path, target_path)
                else:
                    logger.error('[处理迁移] 模式错误！仅支持剪切, 复制, 链接')
            except Exception as e:
                logger.error(str(e))
                return str(e)
