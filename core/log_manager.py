"""会话日志管理：每次安装/卸载任务一个日志文件，历史会话可列举、可查看。

日志目录：~/.local/share/rime-installer/logs/（config.LOG_DIR）
文件命名：YYYYmmdd-HHMMSS-<tag>.log，行首带时间戳。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import config


class LogManager:
    def __init__(self, log_dir: Path | None = None) -> None:
        self._dir = Path(log_dir) if log_dir else config.LOG_DIR
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._session_path: Path | None = None
        self._fh = None

    @property
    def log_dir(self) -> Path:
        return self._dir

    @property
    def current_path(self) -> Path | None:
        return self._session_path

    def open_session(self, tag: str) -> Path | None:
        """开启新会话日志（自动关闭上一个），返回日志文件路径。"""
        self.close_session()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = re.sub(r"[^\w\-]+", "-", tag).strip("-") or "task"
        path = self._dir / f"{stamp}-{safe}.log"
        try:
            self._fh = path.open("w", encoding="utf-8")
        except OSError:
            self._session_path = None
            return None
        self._session_path = path
        self.write(f"日志目录：{self._dir}")
        self.write(f"会话标签：{tag}")
        self.write(f"应用版本：v{config.VERSION}")
        return path

    def write(self, line: str) -> None:
        if not self._fh:
            return
        try:
            self._fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
            self._fh.flush()
        except (OSError, ValueError):
            pass

    def close_session(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except (OSError, ValueError):
                pass
            self._fh = None

    def sessions(self, limit: int = 50) -> list[Path]:
        """最近会话列表（新的在前）。"""
        try:
            files = [p for p in self._dir.glob("*.log") if p.is_file()]
        except OSError:
            return []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:limit]

    def latest_session(self) -> Path | None:
        sessions = self.sessions(limit=1)
        return sessions[0] if sessions else None

    def read_session(self, path: Path | None = None) -> str:
        target = path or self._session_path
        if not target:
            return ""
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
