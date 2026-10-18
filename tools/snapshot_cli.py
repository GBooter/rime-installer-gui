"""快照 CLI：备份 / 恢复，供安装器用 QProcess 驱动（进度与日志复用 runner）。

用法：
    python3 tools/snapshot_cli.py backup --rime-dir DIR --frontend ibus-rime \
        [--note 备注] [--package 名称...] [--dest 目录或zip]
    python3 tools/snapshot_cli.py restore --zip 备份.zip --rime-dir DIR [--clean] \
        [--home HOME]
    python3 tools/snapshot_cli.py info --zip 备份.zip

进度行形如「进度 20/80 (25%)」，可被 runner 的 PERCENT_RE 解析为真实进度。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from core.snapshot_service import (  # noqa: E402
    BackupError,
    _ProgressShim,
    create_backup,
    read_backup_info,
    restore_backup,
)


def _emit(line: str) -> None:
    print(line, flush=True)


def cmd_backup(args: argparse.Namespace) -> int:
    rime_dir = Path(args.rime_dir)
    if not rime_dir.is_dir():
        _emit(f"配置目录不存在：{rime_dir}")
        return 2
    dest_dir = Path(args.dest) if args.dest else None
    if args.dest and str(args.dest).endswith(".zip"):
        dest_dir = Path(args.dest).parent
    _emit(f"开始备份：{rime_dir}（前端 {args.frontend}）")
    try:
        info = create_backup(
            rime_dir,
            frontend=args.frontend,
            packages=args.package or [],
            note=args.note or "",
            dest_dir=dest_dir,
            progress=_ProgressShim(_emit, "打包进度"),
        )
    except BackupError as exc:
        _emit(f"备份失败：{exc}")
        return 2
    except OSError as exc:
        _emit(f"备份失败：{exc}")
        return 2
    _emit(f"备份完成：{info.path}")
    _emit(f"文件数 {info.file_count}，大小 {info.size} 字节")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    zip_path = Path(args.zip)
    rime_dir = Path(args.rime_dir)
    _emit(f"开始恢复：{zip_path} → {rime_dir}（clean={bool(args.clean)}）")
    try:
        stats = restore_backup(
            zip_path,
            rime_dir,
            clean=args.clean,
            home=Path(args.home) if args.home else None,
            progress=_ProgressShim(_emit, "恢复进度"),
        )
    except ValueError as exc:
        _emit(f"恢复失败：{exc}")
        return 3
    except OSError as exc:
        _emit(f"恢复失败（文件系统错误）：{exc}")
        return 3
    _emit(
        f"恢复完成：解压 {stats['extracted']} 个文件"
        + (f"，清理旧文件 {stats['removed']} 个" if stats["removed"] else "")
    )
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    info = read_backup_info(Path(args.zip))
    if not info.valid:
        _emit(f"无法识别：{info.error}")
        return 1
    _emit(f"备份：{info.path.name}")
    _emit(f"时间：{info.created_at}｜前端：{info.frontend}｜文件：{info.file_count}")
    _emit(f"备注：{info.note or '（无）'}")
    if info.packages:
        _emit("方案：" + "、".join(info.packages))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="中州韵输入法安装器 配置快照工具")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="打包当前配置目录")
    backup.add_argument("--rime-dir", required=True)
    backup.add_argument("--frontend", default="")
    backup.add_argument("--note", default="")
    backup.add_argument("--package", action="append", default=[])
    backup.add_argument("--dest", default="")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="恢复备份到配置目录")
    restore.add_argument("--zip", required=True)
    restore.add_argument("--rime-dir", required=True)
    restore.add_argument("--clean", action="store_true")
    restore.add_argument("--home", default="")
    restore.set_defaults(func=cmd_restore)

    info = sub.add_parser("info", help="查看备份元数据")
    info.add_argument("--zip", required=True)
    info.set_defaults(func=cmd_info)
    return parser


def main() -> int:
    config.ensure_user_dirs()
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
