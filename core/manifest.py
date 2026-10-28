"""安装清单（manifest）：记录每个方案引入/覆盖了哪些文件 + 安装前快照。

背景：plum 只能装不能卸（cp 覆盖、无备份）→ 本模块提供「干净卸载」的数据基础：
- packages：一行一个已装方案（含快照目录、上游内容指纹 tree_hash）
- files：该方案引入（added）/覆盖（overwritten）的文件清单
- operations：装卸与更新流水

卸载 = 删除 added 文件 + 从快照还原 overwritten 文件；
更新 = 以全新快照重新记账（更新前状态即新的还原点）。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    frontend TEXT NOT NULL,
    rime_dir TEXT NOT NULL,
    snapshot_dir TEXT NOT NULL,
    tree_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'installed',
    installed_at TEXT NOT NULL,
    uninstalled_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    UNIQUE(package_id, path)
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_key TEXT NOT NULL,
    ts TEXT NOT NULL,
    op TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(directory: Path) -> str:
    """目录内容指纹：排序后的 相对路径 + 文件 sha256，对内容变化敏感、对路径顺序不敏感。"""
    digest = hashlib.sha256()
    if not directory.is_dir():
        return ""
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(directory).as_posix()
            digest.update(f"{rel}\0{file_sha256(path)}\n".encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class PackageInfo:
    key: str
    name: str
    source: str
    frontend: str
    rime_dir: str
    snapshot_dir: str
    tree_hash: str
    installed_at: str


class Manifest:
    def __init__(self, db_path: Path | None = None, snapshot_root: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        self.snapshot_root = Path(snapshot_root) if snapshot_root else config.SNAPSHOT_DIR
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---- 包记录 ----

    def add_package(
        self,
        key: str,
        name: str,
        source: str,
        frontend: str,
        rime_dir: Path,
        snapshot_dir: Path,
        tree_digest: str,
    ) -> int:
        """登记一个已安装方案（同 key 重复安装 = 更新，先清旧文件记录）。"""
        with self._conn:
            self._conn.execute("DELETE FROM files WHERE package_id IN (SELECT id FROM packages WHERE key=?)", (key,))
            self._conn.execute("DELETE FROM packages WHERE key=?", (key,))
            cursor = self._conn.execute(
                "INSERT INTO packages(key,name,source,frontend,rime_dir,snapshot_dir,tree_hash,installed_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    key,
                    name,
                    source,
                    frontend,
                    str(rime_dir),
                    str(snapshot_dir),
                    tree_digest,
                    _now(),
                ),
            )
            package_id = int(cursor.lastrowid or 0)
        self.log_operation(key, "install", f"snapshot={snapshot_dir}")
        return package_id

    def record_files(self, package_id: int, added: list[str], overwritten: list[str]) -> None:
        with self._conn:
            for path in added:
                self._conn.execute(
                    "INSERT OR IGNORE INTO files(package_id,path,action) VALUES (?,?, 'added')",
                    (package_id, path),
                )
            for path in overwritten:
                self._conn.execute(
                    "INSERT OR REPLACE INTO files(package_id,path,action) VALUES (?,?, 'overwritten')",
                    (package_id, path),
                )

    def installed_packages(self) -> list[PackageInfo]:
        rows = self._conn.execute(
            "SELECT * FROM packages WHERE status='installed' ORDER BY installed_at"
        ).fetchall()
        return [
            PackageInfo(
                key=r["key"],
                name=r["name"],
                source=r["source"],
                frontend=r["frontend"],
                rime_dir=r["rime_dir"],
                snapshot_dir=r["snapshot_dir"],
                tree_hash=r["tree_hash"],
                installed_at=r["installed_at"],
            )
            for r in rows
        ]

    def installed_keys(self) -> set[str]:
        return {p.key for p in self.installed_packages()}

    def package_files(self, key: str) -> list[str]:
        """该方案包安装时记录的文件清单（v1.0.38：方案切换据此推导 schema_id）。

        顺序即安装顺序（record_files 的 added 在前）——多方案包的「主方案」
        取第一个。查不到/异常返回空列表，调用方按「无映射」处理。
        """
        try:
            rows = self._conn.execute(
                "SELECT f.path AS path, f.action AS action FROM files f "
                "JOIN packages p ON p.id = f.package_id "
                "WHERE p.key=? AND p.status='installed' "
                "ORDER BY CASE f.action WHEN 'added' THEN 0 ELSE 1 END, f.id",
                (key,),
            ).fetchall()
        except Exception:  # noqa: BLE001 — 清单查询失败不应拖垮调用方
            return []
        return [r["path"] for r in rows]

    def installed_count(self) -> int:
        return len(self.installed_packages())

    def get_package(self, key: str) -> PackageInfo | None:
        row = self._conn.execute(
            "SELECT * FROM packages WHERE key=? AND status='installed'", (key,)
        ).fetchone()
        if row is None:
            return None
        return PackageInfo(
            key=row["key"],
            name=row["name"],
            source=row["source"],
            frontend=row["frontend"],
            rime_dir=row["rime_dir"],
            snapshot_dir=row["snapshot_dir"],
            tree_hash=row["tree_hash"],
            installed_at=row["installed_at"],
        )

    def log_operation(self, key: str, op: str, detail: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO operations(package_key,ts,op,detail) VALUES (?,?,?,?)",
                (key, _now(), op, detail),
            )

    # ---- 快照与 diff ----

    def create_snapshot(self, rime_dir: Path, tag: str) -> Path:
        """整目录快照（rime 目录通常只有几百 KB～数 MB 配置文件）。"""
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in tag) or "scheme"
        dest = self.snapshot_root / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}"
        counter = 1
        while dest.exists():
            counter += 1
            dest = self.snapshot_root / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}-{counter}"
        if rime_dir.is_dir():
            shutil.copytree(rime_dir, dest)
        else:
            dest.mkdir(parents=True)
        return dest

    @staticmethod
    def diff_snapshot(snapshot_dir: Path, rime_dir: Path) -> tuple[list[str], list[str]]:
        """对比快照与当前 rime 目录，返回 (added, overwritten)（相对路径，POSIX）。"""
        before: dict[str, str] = {}
        after: dict[str, str] = {}
        if snapshot_dir.is_dir():
            for path in snapshot_dir.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    before[path.relative_to(snapshot_dir).as_posix()] = file_sha256(path)
        if rime_dir.is_dir():
            for path in rime_dir.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    after[path.relative_to(rime_dir).as_posix()] = file_sha256(path)
        added = sorted(set(after) - set(before))
        overwritten = sorted(k for k in set(after) & set(before) if after[k] != before[k])
        return added, overwritten

    def discard_snapshot(self, snapshot_dir: Path) -> None:
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    # ---- 卸载 ----

    def restore_package(self, key: str) -> dict:
        """干净卸载：删 added、还原 overwritten、标记 uninstalled。返回统计。"""
        package = self.get_package(key)
        if package is None:
            return {"deleted": 0, "restored": 0, "missing": 0}
        rime_dir = Path(package.rime_dir)
        snapshot_dir = Path(package.snapshot_dir)
        rows = self._conn.execute(
            "SELECT path, action FROM files WHERE package_id=(SELECT id FROM packages WHERE key=?)",
            (key,),
        ).fetchall()
        deleted = restored = missing = 0
        for row in rows:
            target = rime_dir / row["path"]
            if row["action"] == "added":
                if target.is_file():
                    target.unlink()
                    deleted += 1
                else:
                    missing += 1
            else:
                source = snapshot_dir / row["path"]
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    restored += 1
                else:
                    missing += 1
        with self._conn:
            self._conn.execute(
                "UPDATE packages SET status='uninstalled', uninstalled_at=? WHERE key=?",
                (_now(), key),
            )
        self.log_operation(key, "uninstall", f"deleted={deleted} restored={restored} missing={missing}")
        return {"deleted": deleted, "restored": restored, "missing": missing}

    # ---- 偏好（安装器侧用户偏好，不写 rime 配置） ----

    #: 「我的默认皮肤」偏好键（v1.0.12）：值为方案 key（addon:skin-*）
    _PREF_DEFAULT_SKIN = "default_skin"

    def get_default_skin(self) -> str:
        row = self._conn.execute(
            "SELECT value FROM preferences WHERE key=?",
            (self._PREF_DEFAULT_SKIN,),
        ).fetchone()
        return row["value"] if row else ""

    def set_default_skin(self, key: str) -> None:
        """设置/清除默认皮肤偏好（key 为空即清除）。只记安装器数据库。"""
        with self._conn:
            if key:
                self._conn.execute(
                    "INSERT OR REPLACE INTO preferences(key,value) VALUES (?,?)",
                    (self._PREF_DEFAULT_SKIN, key),
                )
            else:
                self._conn.execute(
                    "DELETE FROM preferences WHERE key=?",
                    (self._PREF_DEFAULT_SKIN,),
                )

    # ---- 更新检查辅助 ----

    def package_tree_hash(self, key: str) -> str:
        row = self._conn.execute(
            "SELECT tree_hash FROM packages WHERE key=? AND status='installed'", (key,)
        ).fetchone()
        return row["tree_hash"] if row else ""
