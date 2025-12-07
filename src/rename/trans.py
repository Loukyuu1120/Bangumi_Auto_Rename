import os
import json
import shutil
from typing import Dict
from pathlib import Path

from ..logger import logger
from ..utils.path import RECORD_PATH
from ..config.config_manager import cm


class Trans:
    def __init__(self, R: Dict[Path, Path], uuid: str) -> None:
        self.mode = cm.get_config('mode')
        self.R = R
        self.uuid = uuid

    def _cleanup_old_targets_with_prefix(self) -> None:
        """根据上一轮记录，删除旧目标文件及同前缀的相关文件，
        额外：
          - 删除所在 Season 目录里的 season.nfo
          - 如果 Season 目录删除为空，则删掉；
          - 如果是 Season 目录，且上级目录下没有非空子目录/文件，则删掉上级目录。
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

        dirs_to_check: set[Path] = set()
        possible_show_roots: set[Path] = set()

        for _, target_str in old_map.items():
            try:
                t = Path(target_str)
            except TypeError:
                continue

            parent = t.parent
            if not parent.exists() or not parent.is_dir():
                continue

            # 如果是 Season 目录，记录一下上级目录，后面用于“整部剧目录是否全空”的判断
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
                        try:
                            logger.info(f'[处理迁移] 删除 season.nfo: {child}')
                            child.unlink()
                        except Exception as e:
                            logger.warning(f'[处理迁移] 删除 season.nfo 失败 {child}: {e}')
                            continue

                dirs_to_check.add(parent)
            except Exception as e:
                logger.warning(f'[处理迁移] 枚举目录失败 {parent}: {e}')

        # 3) 从深到浅尝试删除已经变成空的 Season 目录等
        all_dirs = sorted(dirs_to_check, key=lambda p: len(p.parts), reverse=True)
        for d in all_dirs:
            try:
                if d.exists() and d.is_dir() and not any(d.iterdir()):
                    logger.info(f'[处理迁移] 删除空目录: {d}')
                    d.rmdir()
            except Exception:
                # 非空或无权限就忽略
                pass

        # 4) 对可能的 show 根目录：如果下面已经没有任何“非空子目录/文件”，则删除整部剧目录
        for show_root in possible_show_roots:
            try:
                if not show_root.exists() or not show_root.is_dir():
                    continue

                children = list(show_root.iterdir())
                if not children:
                    # 完全空，直接删
                    logger.info(f'[处理迁移] 删除空剧集根目录: {show_root}')
                    show_root.rmdir()
                    continue

                # 有子项：如果全部是“空目录”，就可以删整根
                all_empty = True
                for c in children:
                    if c.is_file():
                        all_empty = False
                        break
                    if c.is_dir() and any(c.iterdir()):  # 子目录非空
                        all_empty = False
                        break

                if all_empty:
                    logger.info(f'[处理迁移] 删除仅含空子目录的剧集根目录: {show_root}')
                    # 用 rmdir 一层层删，确保不是误删嵌套结构
                    for c in children:
                        try:
                            if c.is_dir():
                                c.rmdir()
                        except Exception:
                            pass
                    show_root.rmdir()

            except Exception as e:
                logger.warning(f'[处理迁移] 检查/删除剧集根目录失败 {show_root}: {e}')

    def trans_file(self):
        path = RECORD_PATH / f'{self.uuid}.json'

        # 在 复制 / 链接 模式下，先按“同前缀 + season.nfo + 空目录”清理上一轮生成的内容
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
                if self.mode == '剪切':
                    shutil.move(source_path, target_path)
                elif self.mode == '复制':
                    shutil.copy(source_path, target_path)
                elif self.mode == '链接':
                    try:
                        os.link(source_path, target_path)
                    except Exception:
                        logger.warning('[处理迁移] 无法创建硬链接, 尝试软链接...')
                        os.symlink(source_path, target_path)
                else:
                    logger.error('[处理迁移] 模式错误！仅支持剪切, 复制, 链接')
            except Exception as e:
                logger.error(str(e))
                return str(e)
