"""日志页（Phase 2）：实时输出面板 + 会话日志查看 + 日志目录入口。

主窗口在任务开始时调用 set_session()，执行中逐行 append_line()，
结束后 reload_current() 兜底全量刷新（防增量遗漏）。

v1.0.9 修复（真机反馈）：
- 输出框此前挂在末尾弹性占位之后，被压到页面底部且只剩最小高度，
  上方大片空白——现移入信息卡片并独享剩余高度；
- 占位文案此前用 setPlainText 写成真实内容，追加日志会排在「尚无日志」
  之后——改用 QTextEdit 占位符属性（不参与内容）；
- 无进行中会话时页面永远空白——启动后自动展示最近一次会话日志；
- append 此前走 Qt 富文本解析，apt 输出里的 `<...>` 会被当 HTML 吞掉
  ——改为 QTextCursor 纯文本插入。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QTextEdit

import config
from core.log_manager import LogManager
from qfluentwidgets import CaptionLabel, FluentIcon, PushButton

from ui.pages.base import PageBase

_EMPTY_HINT = "尚无日志。执行安装/卸载后，这里会显示实时输出。"


class LogPage(PageBase):
    def __init__(self, parent=None) -> None:
        super().__init__(
            "log.title",
            "log.subtitle",
            parent,
        )
        self.log_manager = LogManager()
        self.dir_row = self.add_row("日志目录", str(config.LOG_DIR), "muted")
        self.session_row = self.add_row("当前会话", "—", "muted")

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.open_button = PushButton(FluentIcon.FOLDER, "打开日志目录")
        self.open_button.clicked.connect(self._open_log_dir)
        self.refresh_button = PushButton(FluentIcon.SYNC, "刷新")
        self.refresh_button.clicked.connect(self.show_latest)
        button_row.addWidget(self.open_button)
        button_row.addWidget(self.refresh_button)
        button_row.addStretch(1)
        self.card_layout.addLayout(button_row)

        self.viewer = QTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.viewer.setMinimumHeight(320)
        self.viewer.setStyleSheet(
            "QTextEdit { font-family: 'Noto Sans Mono', 'DejaVu Sans Mono', monospace;"
            " font-size: 12px; }"
        )
        # 占位符不参与内容：首条真实日志追加时不会排在占位文案之后
        self.viewer.setPlaceholderText(_EMPTY_HINT)
        # v1.0.9：输出框在信息卡片内独享剩余高度（此前挂在末尾 stretch
        # 之后，被压到页面底部只剩最小高度，上方大片空白）
        self.card_layout.addWidget(self.viewer, 1)
        outer = self.layout()
        outer.setStretch(outer.count() - 1, 0)
        outer.setStretch(outer.indexOf(self.card), 1)

        self._session_path: Path | None = None
        self.show_latest()

    @property
    def session_row_text(self) -> str:
        """当前会话行显示值（冒烟断言用）。"""
        item = self.session_row.itemAt(self.session_row.count() - 1)
        label = item.widget()
        return label.text() if label is not None else ""

    # ---- 主窗口调用 ----

    def set_session(self, path: Path | None) -> None:
        self._session_path = path
        self._set_row(
            self.session_row, path.name if path else "—", "ok" if path else "muted"
        )
        self.viewer.clear()
        if path is None:
            self.viewer.setPlaceholderText(_EMPTY_HINT)

    def append_line(self, line: str) -> None:
        self._append_text(line + "\n")

    def append_lines(self, lines: list[str]) -> None:
        """批量追加（v1.0.7：配合主窗口输出节流，避免逐行刷新淹没主线程）。"""
        if not lines:
            return
        self._append_text("\n".join(lines) + "\n")

    def _append_text(self, text: str) -> None:
        """纯文本追加：apt/aria2 输出里的尖括号不会被当 HTML 解析。"""
        cursor = self.viewer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.viewer.setTextCursor(cursor)
        self.viewer.ensureCursorVisible()

    def reload_current(self) -> None:
        if not self._session_path or not self._session_path.exists():
            return
        try:
            text = self._session_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self.viewer.setPlainText(text)

    def show_latest(self) -> None:
        """刷新视图：有进行中会话则全量重读，否则展示最近一次历史会话。

        v1.0.9：此前启动后进入本页永远空白（占位文案 + 无内容），
        历史日志只能去文件系统翻——现在「刷新」按钮与页面初始化都会
        回填最近一次会话，让日志框真正可用。
        """
        if self._session_path is not None:
            self.reload_current()
            return
        latest = self.log_manager.latest_session()
        if latest is None:
            self._set_row(self.session_row, "—", "muted")
            return
        self._set_row(self.session_row, latest.name, "muted")
        self.viewer.setPlainText(self.log_manager.read_session(latest) or "最近一次会话日志为空。")

    # ---- 内部 ----

    def _set_row(self, row, value: str, status: str | None = None) -> None:
        from qfluentwidgets import BodyLabel

        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value)
            if status == "ok":
                label.setStyleSheet("color: #3B6D11;")
            elif status == "muted":
                label.setStyleSheet("color: #5F5E5A;")

    def _open_log_dir(self) -> None:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.LOG_DIR)))
