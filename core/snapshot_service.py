"""配置快照与备份（F12）：备份 / 恢复 / 导出 zip / 导入。

设计要点：
- 备份 = rime 用户目录整体打包 + backup.json 元数据（时间、前端、方案清单、备注），
  zip 可脱离本安装器使用（手工解压即用），也可被本安装器导入；
- 恢复 = 覆盖式还原（默认）/ 清空后还原（可选），解压前校验路径穿越（zip slip），
  且目标目录必须位于 home 之下——杜绝把备份解到系统目录；
- 全部为纯逻辑（不依赖 Qt），可在冒烟与 CLI 中直接调用。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import config

META_NAME = "backup.json"
PAYLOAD_ROOT = "rime"  # zip 内的配置目录前缀
SCHEMA_VERSION = 1

#: 恢复临时目录名前缀（同目录下）：先解压到这里，校验完整后再替换目标目录，
#: 避免「先删后写」在取消/断电时留下半个配置甚至空目录。
STAGING_PREFIX = ".rime-restoring-"


class BackupError(ValueError):
    """可展示给用户的备份/恢复错误（CLI 与 UI 都按 ValueError 处理）。"""


class EmptyBackupError(BackupError):
    """备份源目录为空——不允许生成「0 个文件」的假备份。"""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created_at: str = ""
    frontend: str = ""
    rime_dir: str = ""
    note: str = ""
    file_count: int = 0
    packages: tuple[str, ...] = ()
    size: int = 0
    valid: bool = True
    error: str = ""

    @property
    def label(self) -> str:
        """一行摘要（**日志/CLI 用**）。

        v1.0.23 起界面不再用它——`BackupPage._row_text()` 按 i18n 渲染，
        否则英文界面会漏出中文（「个文件」「无法识别」）。
        """
        stamp = self.created_at or self.path.stem
        size = human_size(self.size)
        note = f" · {self.note}" if self.note else ""
        if not self.valid:
            return f"{stamp} · {size} · 无法识别{('：' + self.error) if self.error else ''}"
        return f"{stamp} · {size} · {self.file_count} 个文件 · {self.frontend}{note}"


def human_size(num: int) -> str:
    value = float(max(num, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _as_int(value: object, default: int = 0) -> int:
    """元数据里的数值一律防御式取值：手工改过/其他版本写坏的值不能让列表崩。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_text(value: object) -> str:
    return "" if value is None else str(value)


def _as_str_list(value: object) -> tuple[str, ...]:
    """packages 可能是 list/str/None/数字——只有可迭代字符串序列才收。"""
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())



def backup_dir() -> Path:
    return config.BACKUP_DIR


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _safe_tag(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in text)[:40]


def new_backup_path(tag: str = "", dest_dir: Path | None = None) -> Path:
    """生成不与现有文件冲突的备份路径。

    v1.0.23：时间戳只到秒，同一秒内两次备份（或同备注连点两次）会拿到同一
    文件名，`zipfile(..., "w")` 静默覆盖前一个备份——用户以为存了两份，其实
    只剩一份。冲突时顺延 -2 / -3 …。
    """
    root = Path(dest_dir) if dest_dir else backup_dir()
    root.mkdir(parents=True, exist_ok=True)
    suffix = f"-{_safe_tag(tag)}" if tag else ""
    base = f"rime-backup-{_stamp()}{suffix}"
    candidate = root / f"{base}.zip"
    if not candidate.exists():
        return candidate
    for index in range(2, 100):
        candidate = root / f"{base}-{index}.zip"
        if not candidate.exists():
            return candidate
    return root / f"{base}-{int(time.time() * 1000) % 100000}.zip"


def _is_safe_target(target: Path, home: Path | None = None) -> bool:
    """恢复目标必须在 home 之下且不是 home 本身——防止把备份解到系统目录。"""
    home = (home or Path.home()).resolve()
    try:
        resolved = target.resolve()
    except OSError:
        return False
    if resolved == home or resolved == Path("/"):
        return False
    return home in resolved.parents


def _iter_files(rime_dir: Path) -> list[Path]:
    if not rime_dir.is_dir():
        return []
    return sorted(
        p for p in rime_dir.rglob("*") if p.is_file() and not p.is_symlink()
    )


def create_backup(
    rime_dir: Path,
    frontend: str = "",
    packages: Sequence[str] = (),
    note: str = "",
    dest_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> BackupInfo:
    """把 rime 用户目录打包为 zip（含元数据），返回备份信息。

    v1.0.23：目录不存在或里面一个文件都没有时**拒绝备份**（`EmptyBackupError`）。
    此前会生成一个「0 个文件」的 zip 并判定为有效备份——它出现在列表里可被
    选中恢复，配上「清空后恢复」就是把用户配置清空，属于假成功引发的数据丢失。
    """
    rime_dir = Path(rime_dir)
    if not rime_dir.is_dir():
        raise EmptyBackupError(f"配置目录不存在：{rime_dir}")
    files = _iter_files(rime_dir)
    if not files:
        raise EmptyBackupError(f"配置目录里没有任何文件，未生成备份：{rime_dir}")
    dest = new_backup_path(_safe_tag(note) or "manual", dest_dir)
    meta = {
        "schema": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": config.APP_NAME_EN,
        "app_version": config.VERSION,
        "frontend": frontend,
        "rime_dir": str(rime_dir),
        "note": note,
        "packages": list(packages),
        "file_count": len(files),
    }
    total = len(files) or 1
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(META_NAME, json.dumps(meta, ensure_ascii=False, indent=2))
        for index, path in enumerate(files, start=1):
            archive.write(path, f"{PAYLOAD_ROOT}/{path.relative_to(rime_dir).as_posix()}")
            if progress and (index % 20 == 0 or index == total):
                progress(index, len(files))
        if progress:
            progress(len(files), len(files))
    return BackupInfo(
        path=dest,
        created_at=meta["created_at"],
        frontend=frontend,
        rime_dir=str(rime_dir),
        note=note,
        file_count=len(files),
        packages=tuple(packages),
        size=dest.stat().st_size,
    )


def read_backup_info(path: Path) -> BackupInfo:
    """读取 zip 元数据；无法识别时返回 `valid=False`，**任何情况都不抛异常**。

    v1.0.23：元数据是外部数据（可被手工编辑、可来自别人的机器），此前
    `int("abc")` / `packages=3` 会把 ValueError/TypeError 抛出去——列表里
    只要有一个坏 zip，`list_backups()` 就会失败，进而让整个安装器启动失败。
    """
    path = Path(path)
    try:
        if not path.is_file():
            return BackupInfo(path=path, valid=False, error="文件不存在")
        size = path.stat().st_size
    except OSError as exc:
        return BackupInfo(path=path, valid=False, error=f"无法读取：{exc}")

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            payload = [n for n in names if n.startswith(f"{PAYLOAD_ROOT}/")]
            if META_NAME not in names:
                if not payload:
                    return BackupInfo(path=path, size=size, valid=False, error="不含配置数据")
                return BackupInfo(
                    path=path,
                    created_at=time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)
                    ),
                    file_count=len(payload),
                    size=size,
                )
            try:
                meta = json.loads(archive.read(META_NAME).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return BackupInfo(path=path, size=size, valid=False, error=f"元数据损坏：{exc}")
            except (OSError, KeyError) as exc:
                return BackupInfo(path=path, size=size, valid=False, error=f"元数据读取失败：{exc}")
            if not isinstance(meta, dict):
                return BackupInfo(path=path, size=size, valid=False, error="元数据格式异常")
    except zipfile.BadZipFile as exc:
        return BackupInfo(path=path, size=size, valid=False, error=f"不是有效的 zip：{exc}")
    except OSError as exc:
        return BackupInfo(path=path, size=size, valid=False, error=f"无法读取：{exc}")
    except Exception as exc:  # noqa: BLE001 — 外部数据，宁可标记损坏也不能拖垮列表
        return BackupInfo(path=path, size=size, valid=False, error=f"无法识别：{exc}")

    return BackupInfo(
        path=path,
        created_at=_as_text(meta.get("created_at")),
        frontend=_as_text(meta.get("frontend")),
        rime_dir=_as_text(meta.get("rime_dir")),
        note=_as_text(meta.get("note")),
        file_count=_as_int(meta.get("file_count"), len(payload)),
        packages=_as_str_list(meta.get("packages")),
        size=size,
    )


def list_backups(dest_dir: Path | None = None) -> list[BackupInfo]:
    """列出备份，按修改时间倒序（新→旧）。

    v1.0.23：①逐项隔离——单个坏文件只标记为无法识别，不影响其它备份与
    启动；②改按 mtime 排序——此前按文件名倒序，导入的任意命名 zip
    （如 `old.zip`）会被当成「最近备份」。
    """
    root = Path(dest_dir) if dest_dir else backup_dir()
    try:
        if not root.is_dir():
            return []
        paths = sorted(root.glob("*.zip"))
    except OSError:
        return []

    infos: list[BackupInfo] = []
    for candidate in paths:
        try:
            infos.append(read_backup_info(candidate))
        except Exception as exc:  # noqa: BLE001 — 兜底，绝不因单项中断整表
            infos.append(BackupInfo(path=candidate, valid=False, error=f"无法识别：{exc}"))
    infos.sort(key=_sort_key, reverse=True)
    return infos


def _sort_key(info: BackupInfo) -> tuple[float, str]:
    try:
        return (info.path.stat().st_mtime, info.path.name)
    except OSError:
        return (0.0, info.path.name)


def _safe_members(archive: zipfile.ZipFile, target: Path) -> list[str]:
    """过滤出可安全解压的成员：仅 rime/ 前缀且解压后不越出 target。"""
    base = target.resolve()
    members: list[str] = []
    for name in archive.namelist():
        if not name.startswith(f"{PAYLOAD_ROOT}/"):
            continue
        rel = name[len(PAYLOAD_ROOT) + 1 :]
        if not rel:
            continue
        # zip slip 防护：规范化后必须仍在目标目录内
        resolved = os.path.normpath((base / rel).resolve())
        if resolved == str(base) or not resolved.startswith(str(base) + os.sep):
            continue
        members.append(name)
    return members


def restore_backup(
    path: Path,
    rime_dir: Path,
    clean: bool = False,
    home: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """恢复备份到 rime 目录。

    clean=True 时清空目录内现有文件（干净的「回到那一刻」），默认 False 为
    覆盖合并（保留备份之外的个人文件）。

    v1.0.23 两阶段恢复：先把 zip 完整解压到目标目录旁的临时目录
    （`.rime-restoring-*`），校验通过后才动真实目录。此前是「先删光现有文件
    再逐个写」——用户在任务进度里点一次取消（runner 直接 kill 子进程），
    配置目录就只剩 0 个文件，且没有任何提示。
    """
    path = Path(path)
    rime_dir = Path(rime_dir)
    info = read_backup_info(path)
    if not info.valid:
        raise BackupError(f"备份无法识别：{info.error or path}")
    if not _is_safe_target(rime_dir, home):
        raise BackupError(f"恢复目标不在家目录下，已拒绝：{rime_dir}")

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"无法打开备份文件：{exc}") from exc

    with archive:
        members = _safe_members(archive, rime_dir)
        if not members:
            raise BackupError("备份里没有任何配置数据（0 个文件），已拒绝恢复")
        total = len(members)
        staging = rime_dir.parent / f"{STAGING_PREFIX}{rime_dir.name}-{os.getpid()}"
        try:
            rime_dir.mkdir(parents=True, exist_ok=True)
            # 上一次被强杀留下的临时目录（进程来不及时清理）——按命名约定回收
            for stale in rime_dir.parent.glob(f"{STAGING_PREFIX}*"):
                if stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            extracted = 0
            for index, name in enumerate(members, start=1):
                rel = name[len(PAYLOAD_ROOT) + 1 :]
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted += 1
                if progress and (index % 20 == 0 or index == total):
                    progress(index, total)
            if progress:
                progress(total, total)

            removed = 0
            # 顺序很关键：**先**把临时目录里的文件搬进目标目录，**再**清理
            # 备份之外的多余文件。反过来（先删后写）一但进程被强杀，配置目录
            # 就是空的；按这个顺序，最坏情况只是残留几个旧文件，可重跑恢复。
            keep = {name[len(PAYLOAD_ROOT) + 1 :] for name in members}
            for staged in sorted(p for p in staging.rglob("*") if p.is_file()):
                rel = staged.relative_to(staging).as_posix()
                dst = rime_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(dst))
            if clean:
                for item in _iter_files(rime_dir):
                    if item.relative_to(rime_dir).as_posix() in keep:
                        continue
                    item.unlink()
                    removed += 1
                for sub in sorted(
                    (p for p in rime_dir.rglob("*") if p.is_dir()), reverse=True
                ):
                    if not any(sub.iterdir()):
                        sub.rmdir()
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return {"extracted": extracted, "removed": removed, "total": total}


def export_backup(path: Path, dest: Path) -> Path:
    """导出到用户指定位置（dest 为目录时沿用原文件名）。"""
    src = Path(path)
    dest = Path(dest)
    if dest.is_dir():
        dest = dest / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def import_backup(src: Path, dest_dir: Path | None = None) -> BackupInfo:
    """校验后复制到备份目录，返回其信息（损坏文件直接拒绝）。

    v1.0.23：先完整校验元数据再复制——此前只检查 zip 结构与 `rime/` 前缀，
    畸形 backup.json 会被原样复制进备份目录，随后 `read_backup_info` 在
    导入回调里抛 TypeError（槽函数未捕获 → 闪退），且此后每次启动都崩。
    同时把 OSError 统一转成 BackupError，交给 UI 用 InfoBar 展示。
    """
    src = Path(src)
    try:
        if not src.is_file():
            raise BackupError(f"文件不存在：{src}")
        info = read_backup_info(src)
        if not info.valid:
            raise BackupError(f"无法识别的备份：{info.error or src.name}")
        dest = Path(dest_dir) if dest_dir else backup_dir()
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / src.name
        if target.exists():
            target = dest / f"{src.stem}-import-{_stamp()}{src.suffix}"
            index = 2
            while target.exists():
                target = dest / f"{src.stem}-import-{_stamp()}-{index}{src.suffix}"
                index += 1
        shutil.copy2(src, target)
    except BackupError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"导入失败：{exc}") from exc
    return read_backup_info(target)


def delete_backup(
    path: Path,
    home: Path | None = None,
    allowed_dir: Path | None = None,
) -> bool:
    """删除备份文件。

    护栏：①必须是 `.zip`；②必须在家目录之下；③默认必须位于本安装器的备份
    目录内——此前只校验「在家目录下」，界面提示「只允许删除备份目录中的
    .zip」与实现不符。测试/CLI 场景可用 `allowed_dir` 指定其它白名单目录。
    """
    path = Path(path)
    try:
        resolved = path.resolve()
        allowed = Path(allowed_dir).resolve() if allowed_dir else backup_dir().resolve()
    except OSError:
        return False
    if allowed not in resolved.parents:
        return False
    if not _is_safe_target(path, home):
        return False
    if path.suffix.lower() != ".zip":
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def package_labels(packages: Iterable[str]) -> list[str]:
    return [str(p) for p in packages if str(p).strip()]


# ---- runner 步骤（延迟导入 core.runner，保持本模块对 CLI 场景无 Qt 依赖） ----


def _python() -> str:
    import sys

    return sys.executable or "python3"


def build_backup_step(
    rime_dir: Path,
    frontend: str = "",
    packages: Sequence[str] = (),
    note: str = "",
    dest_dir: Path | None = None,
):
    """打包步骤：经 runner 驱动，进度来自 CLI 输出的 (n%)。"""
    from core.runner import PERCENT_RE, Step, shell_wrap

    argv = [
        _python(),
        str(config.SNAPSHOT_CLI),
        "backup",
        "--rime-dir",
        str(rime_dir),
    ]
    if frontend:
        argv += ["--frontend", frontend]
    if note:
        argv += ["--note", note]
    for name in package_labels(packages):
        argv += ["--package", name]
    if dest_dir is not None:
        argv += ["--dest", str(dest_dir)]
    return Step(
        "打包配置快照",
        shell_wrap(argv),
        weight=1.0,
        progress_re=PERCENT_RE,
        hint_key="msg.backup_failed",
    )


def build_restore_step(
    zip_path: Path, rime_dir: Path, clean: bool = False, home: Path | None = None
):
    """恢复步骤：clean=True 时先清空目标目录再还原。"""
    from core.runner import PERCENT_RE, Step, shell_wrap

    argv = [
        _python(),
        str(config.SNAPSHOT_CLI),
        "restore",
        "--zip",
        str(zip_path),
        "--rime-dir",
        str(rime_dir),
    ]
    if clean:
        argv += ["--clean"]
    if home is not None:
        argv += ["--home", str(home)]
    return Step(
        "恢复配置快照",
        shell_wrap(argv),
        weight=1.0,
        progress_re=PERCENT_RE,
        hint_key="msg.restore_failed",
    )


@dataclass
class _ProgressShim:
    """供 CLI 使用：把 (index, total) 转成 0-100 的回调。"""

    emit: Callable[[str], None] = field(default=lambda line: print(line, flush=True))
    prefix: str = "进度"

    def __call__(self, index: int, total: int) -> None:
        pct = int(index * 100 / total) if total else 100
        self.emit(f"{self.prefix} {index}/{total} ({pct}%)")
