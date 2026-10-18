"""备份与恢复页（Phase 4 / F12）：配置快照的备份、恢复、导入导出。

本页只负责展示与意图发射，执行由主窗口编排（与引擎页/方案页同一套 busy 协议）：
- backup_requested(note)：打包当前 rime 配置目录 + 元数据；
- restore_requested(zip, clean, backup_first)：恢复前可选先备份当前状态（防不可逆）；
- import_requested(zip) / export_requested(zip, dest) / delete_requested(zip)。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
)

from core.detector import EnvReport
from core import i18n
from core.snapshot_service import BackupInfo, backup_dir, human_size
from core.state_machine import StateMachine
from ui.pages.base import STATUS_COLORS, PageBase, ProgressAreaMixin


def _xdg_download_dir() -> Path | None:
    """从 `~/.config/user-dirs.dirs` 读 XDG_DOWNLOAD_DIR（英文/中文桌面都适用）。"""
    config_file = Path.home() / ".config" / "user-dirs.dirs"
    try:
        for line in config_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("XDG_DOWNLOAD_DIR="):
                continue
            raw = line.split("=", 1)[1].strip().strip('"')
            expanded = raw.replace("$HOME", str(Path.home()))
            return Path(expanded)
    except OSError:
        return None
    return None


class BackupPage(ProgressAreaMixin, PageBase):
    backup_requested = pyqtSignal(str)  # 备注
    restore_requested = pyqtSignal(str, bool, bool)  # zip 路径、清空后恢复、恢复前先备份
    import_requested = pyqtSignal(str)  # 外部 zip 路径
    export_requested = pyqtSignal(str, str)  # zip 路径、导出目标
    delete_requested = pyqtSignal(str)  # zip 路径
    refresh_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    log_requested = pyqtSignal()

    _DOTS_FRAMES = ("", ".", "..", "...")

    def __init__(
        self,
        report: EnvReport,
        sm: StateMachine,
        backups: list[BackupInfo] | None = None,
        parent=None,
    ) -> None:
        super().__init__(
            "backup.title",
            "backup.subtitle",
            parent,
        )
        self._report = report
        self._sm = sm
        self._backups: list[BackupInfo] = list(backups or [])

        self.rime_dir_row = self.add_row(i18n.tr("row.rime_dir"), "")
        self.frontend_row = self.add_row(i18n.tr("row.frontend"), "")
        self.backup_dir_row = self.add_row(i18n.tr("row.backup_dir"), "")
        self.count_row = self.add_row(i18n.tr("row.backup_count"), "")
        self.latest_row = self.add_row(i18n.tr("row.latest_backup"), "")

        # ---- 立即备份 ----
        backup_row = QHBoxLayout()
        backup_row.setSpacing(10)
        self.note_input = LineEdit()
        self.note_input.setPlaceholderText(i18n.tr("backup.note_placeholder"))
        self.backup_button = PrimaryPushButton(FluentIcon.SAVE, i18n.tr("backup.do"))
        self.backup_button.clicked.connect(
            lambda: self.backup_requested.emit(self.note_input.text().strip())
        )
        backup_row.addWidget(self.note_input, 1)
        backup_row.addWidget(self.backup_button)
        self.card_layout.addLayout(backup_row)

        # ---- 恢复选项 ----
        self.pre_backup_check = CheckBox(i18n.tr("backup.pre_backup"))
        self.pre_backup_check.setChecked(True)
        self.clean_check = CheckBox(i18n.tr("backup.clean"))
        self.clean_check.setChecked(False)
        self.clean_check.setToolTip(i18n.tr("backup.clean_tip"))
        self.card_layout.addWidget(self.pre_backup_check)
        self.card_layout.addWidget(self.clean_check)

        # ---- 执行进度区（等待态条纹 + 数值平滑动画见 ProgressAreaMixin） ----
        self._init_progress_area(self.card_layout)

        # 取消/查看日志贴在进度区右侧（v1.0.23）：此前放在备份列表下方，
        # 与列表操作挤成两行，窗口略窄时「取消任务 / 查看日志」就被折到下一行。
        self.cancel_button = PushButton(
            FluentIcon.CANCEL, i18n.tr("common.cancel_task")
        )
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.log_button = PushButton(FluentIcon.HISTORY, i18n.tr("common.view_log"))
        self.log_button.clicked.connect(self.log_requested.emit)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        progress_row.addStretch(1)
        progress_row.addWidget(self.cancel_button)
        progress_row.addWidget(self.log_button)
        self.card_layout.addLayout(progress_row)

        # ---- 备份列表 ----
        list_card = SimpleCardWidget(self)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(18, 14, 18, 14)
        list_layout.setSpacing(10)
        self.list_title_label = StrongBodyLabel(i18n.tr("backup.list_title"))
        list_layout.addWidget(self.list_title_label)
        self.list_widget = ListWidget(list_card)
        self.list_widget.itemSelectionChanged.connect(self._refresh_actions)
        self.list_widget.setMinimumHeight(150)
        list_layout.addWidget(self.list_widget, 1)

        list_actions = QHBoxLayout()
        list_actions.setSpacing(8)
        self.restore_button = PushButton(FluentIcon.SYNC, i18n.tr("backup.restore"))
        self.restore_button.clicked.connect(self._on_restore_clicked)
        self.export_button = PushButton(
            FluentIcon.SHARE, i18n.tr("backup.export_to")
        )
        self.export_button.clicked.connect(self._on_export_clicked)
        self.import_button = PushButton(FluentIcon.ADD, i18n.tr("backup.do_import"))
        self.import_button.clicked.connect(self._on_import_clicked)
        self.delete_button = PushButton(
            FluentIcon.DELETE, i18n.tr("common.delete")
        )
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.refresh_button = PushButton(
            FluentIcon.UPDATE, i18n.tr("common.refresh")
        )
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.open_dir_button = PushButton(
            FluentIcon.FOLDER, i18n.tr("backup.open_dir")
        )
        self.open_dir_button.clicked.connect(self._open_backup_dir)
        for button in (
            self.restore_button,
            self.export_button,
            self.import_button,
            self.delete_button,
            self.refresh_button,
            self.open_dir_button,
        ):
            list_actions.addWidget(button)
        list_actions.addStretch(1)
        list_layout.addLayout(list_actions)
        self.list_card = list_card

        # 列表卡片独享剩余高度（与 PageBase.add_expanding_scroll 同一手法）：
        # 否则卡片只按 sizeHint 长到 3 行，窗口下方留一大片空白。
        outer = self.layout()
        outer.insertWidget(outer.count() - 1, list_card, 1)
        outer.setStretch(outer.count() - 1, 0)

        self._status_base = ""
        self._dots_index = 0
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(320)
        self._dots_timer.timeout.connect(self._tick_dots)

        self.bind(report, sm)
        self.set_backups(self._backups)
        self._show_progress_area(False)

    # ---- 列表 ----

    def set_backups(self, backups: list[BackupInfo]) -> None:
        self._backups = list(backups)
        current = self.selected_path()
        # 重建期间屏蔽选中信号：否则 `clear()` 与逐项插入会在“半重置”状态
        # 触发 _refresh_actions，用上一个选中项算出错误按钮态并留在界面上
        # （v1.0.23 实证：删除后「删除」按钮仍可点，point 到已不存在的项）。
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            for info in self._backups:
                item = QListWidgetItem(self._row_text(info))
                item.setData(Qt.ItemDataRole.UserRole, str(info.path))
                item.setToolTip(str(info.path))
                self.list_widget.addItem(item)
            if current:
                self.select_path(current)
            if not self._backups:
                empty = QListWidgetItem(i18n.tr("backup.empty"))
                empty.setData(Qt.ItemDataRole.UserRole, "")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(empty)
        finally:
            self.list_widget.blockSignals(False)
        self._refresh_meta()
        self._refresh_actions()  # 重建后必须重算一次，信号已被屏蔽

    @staticmethod
    def _row_text(info: BackupInfo) -> str:
        """列表行文案（走 i18n）：中文界面与旧版一致，英文界面不再漏中文。"""
        stamp = info.created_at or info.path.stem
        size = human_size(info.size)
        if not info.valid:
            if info.error:
                return i18n.tr("backup.row_invalid", stamp=stamp, size=size, error=info.error)
            return i18n.tr("backup.row_invalid_no_error", stamp=stamp, size=size)
        text = i18n.tr(
            "backup.row_base",
            stamp=stamp,
            size=size,
            count=info.file_count,
        )
        if info.frontend:
            text += i18n.tr("backup.row_frontend", frontend=info.frontend)
        if info.note:
            text += i18n.tr("backup.row_note", note=info.note)
        return text

    def _refresh_meta(self) -> None:
        count = len(self._backups)
        self._set_row(self.count_row, i18n.tr("backup.file_count", count=count), "muted")
        if count:
            latest = self._backups[0]
            self._set_row(
                self.latest_row,
                i18n.tr(
                    "backup.latest_value",
                    stamp=latest.created_at or latest.path.stem,
                    size=human_size(latest.size),
                ),
            )
        else:
            self._set_row(self.latest_row, "—", "muted")

    def selected_path(self) -> str:
        item = self.list_widget.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def selected_info(self) -> BackupInfo | None:
        path = self.selected_path()
        return next((b for b in self._backups if str(b.path) == path), None)

    def select_path(self, path: str) -> None:
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == str(path):
                self.list_widget.setCurrentItem(item)
                return

    # ---- 交互 ----

    def _on_restore_clicked(self) -> None:
        info = self.selected_info()
        if info is None:
            return
        self.restore_requested.emit(
            str(info.path),
            self.clean_check.isChecked(),
            self.pre_backup_check.isChecked(),
        )

    def _on_export_clicked(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        info = self.selected_info()
        if info is None:
            return
        default = str(Path(self._default_export_dir()) / info.path.name)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            i18n.tr("dialog.export_title"),
            default,
            i18n.tr("dialog.zip_filter"),
        )
        if dest:
            if not dest.lower().endswith(".zip"):
                dest += ".zip"
            self.export_requested.emit(str(info.path), dest)

    def _on_delete_clicked(self) -> None:
        info = self.selected_info()
        if info is None:
            return
        self.delete_requested.emit(str(info.path))

    def _on_import_clicked(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            i18n.tr("dialog.import_title"),
            str(Path.home()),
            f'{i18n.tr("dialog.zip_filter")};;{i18n.tr("dialog.all_files")}',
        )
        if path:
            self.import_requested.emit(path)

    def _default_export_dir(self) -> str:
        """导出默认目录：遵循 XDG 下载目录，缺失时回退常见中文/英文目录名。"""
        configured = _xdg_download_dir()
        for candidate in (configured, Path.home() / "下载", Path.home() / "Downloads"):
            if candidate and candidate.is_dir():
                return str(candidate)
        return str(Path.home())

    def _open_backup_dir(self) -> None:
        """打开备份目录。

        v1.0.23 修复闪退：`Path.as_uri()` 返回的是 **str**，而 PyQt6 的
        `QDesktopServices.openUrl()` 只接受 `QUrl` —— 传 str 会抛 TypeError，
        PyQt6 对槽函数里的未捕获异常直接 abort 进程（真机表现就是点一下
        软件瞬间消失）。改用 `QUrl.fromLocalFile()`，并把创建目录失败、系统
        没有文件管理器这两种情况都变成可见提示而不是异常。
        """
        directory = Path(backup_dir())
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            InfoBar.error(
                title=i18n.tr("msg.open_dir_failed"),
                content=i18n.tr("msg.open_dir_failed_hint", path=str(directory), error=str(exc)),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            InfoBar.warning(
                title=i18n.tr("msg.open_dir_unsupported"),
                content=i18n.tr("msg.open_dir_unsupported_hint", path=str(directory)),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=8000,
            )

    def _refresh_actions(self) -> None:
        """按钮态的唯一入口：恢复/导出/删除都按「选中项」与状态机规则计算。"""
        info = self.selected_info()
        has_sel = info is not None
        has_valid = bool(info and info.valid)
        busy = self._dots_timer.isActive()
        rule = self._sm.rule("restore_backup")
        self.restore_button.setEnabled(has_valid and rule.enabled and not busy)
        self.export_button.setEnabled(has_valid and not busy)
        self.delete_button.setEnabled(has_sel and not busy)
        self.import_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.open_dir_button.setEnabled(not busy)
        self.list_widget.setEnabled(not busy)
        if busy:
            self.restore_button.setToolTip(i18n.tr("tip.busy"))
        elif not has_sel:
            self.restore_button.setToolTip(i18n.tr("backup.select_first"))
        elif not has_valid:
            self.restore_button.setToolTip(i18n.tr("backup.invalid_tip"))
        elif not rule.enabled:
            self.restore_button.setToolTip(rule.reason or i18n.tr("backup.invalid_tip"))
        else:
            self.restore_button.setToolTip(i18n.tr("backup.restore_tip"))

    def _refresh_backup_button(self) -> None:
        rule = self._sm.rule("backup")
        self.backup_button.setEnabled(rule.enabled)
        self.backup_button.setToolTip(rule.reason or i18n.tr("backup.create_tip"))
        self.note_input.setEnabled(rule.enabled)

    # ---- 绑定与执行期 UI ----

    def _fill(self, report: EnvReport, sm: StateMachine) -> None:
        rime_dir = report.rime_user_dir
        if rime_dir is None:
            self._set_row(self.rime_dir_row, i18n.tr("backup.rime_missing"), "warn")
        elif not Path(rime_dir).is_dir():
            # 探测逻辑在没有任何候选目录时会给出一个「预期路径」，它并不存在；
            # 必须显式说明，否则用户会以为配置就在那里（备份也因此不该可用）。
            self._set_row(
                self.rime_dir_row,
                i18n.tr("backup.rime_not_created", path=str(rime_dir)),
                "warn",
            )
        else:
            self._set_row(self.rime_dir_row, str(rime_dir), "muted")
        self._set_row(self.frontend_row, report.recommended_frontend)
        self._set_row(self.backup_dir_row, str(backup_dir()), "muted")
        self._refresh_backup_button()

    def retranslate(self) -> None:
        """切语言：标题 + 行标签 + 按钮 + 选项 + 列表标题与空态。"""
        super().retranslate()
        for row, key in (
            (self.rime_dir_row, "row.rime_dir"),
            (self.frontend_row, "row.frontend"),
            (self.backup_dir_row, "row.backup_dir"),
            (self.count_row, "row.backup_count"),
            (self.latest_row, "row.latest_backup"),
        ):
            label = row.itemAt(0).widget()
            if label is not None:
                label.setText(i18n.tr(key))
        self.backup_button.setText(i18n.tr("backup.do"))
        self.restore_button.setText(i18n.tr("backup.restore"))
        self.export_button.setText(i18n.tr("backup.export_to"))
        self.import_button.setText(i18n.tr("backup.do_import"))
        self.delete_button.setText(i18n.tr("common.delete"))
        self.refresh_button.setText(i18n.tr("common.refresh"))
        self.open_dir_button.setText(i18n.tr("backup.open_dir"))
        self.cancel_button.setText(i18n.tr("common.cancel_task"))
        self.log_button.setText(i18n.tr("common.view_log"))
        self.note_input.setPlaceholderText(i18n.tr("backup.note_placeholder"))
        self.pre_backup_check.setText(i18n.tr("backup.pre_backup"))
        self.clean_check.setText(i18n.tr("backup.clean"))
        self.clean_check.setToolTip(i18n.tr("backup.clean_tip"))
        self.list_title_label.setText(i18n.tr("backup.list_title"))
        # 列表行与「数量/最近备份」的值也是文案，切语言后必须重算
        self.set_backups(self._backups)

    def bind(self, report: EnvReport, sm: StateMachine) -> None:
        self._report, self._sm = report, sm
        self._fill(report, sm)
        self._refresh_actions()

    def set_busy(self, busy: bool, title: str = "", cancellable: bool = True) -> None:
        """页面级不变量：busy 时备份与列表操作全部禁用。

        `cancellable=False` 用于恢复任务：恢复虽已改成「先解压到临时目录再
        替换」，但替换阶段被强制中断仍可能留下不完整状态，索性不给取消入口
        （v1.0.23，此前一点取消就等于把配置目录删空）。
        """
        self.note_input.setEnabled(not busy)
        self.pre_backup_check.setEnabled(not busy)
        self.clean_check.setEnabled(not busy)
        self.log_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and cancellable)
        self.cancel_button.setToolTip(
            "" if cancellable or not busy else i18n.tr("backup.restore_uncancellable")
        )
        self._show_progress_area(busy)
        if busy:
            self.backup_button.setEnabled(False)
            self.enter_waiting()
            self.set_step(1, 1, title or i18n.tr("common.prepare"))
            self._dots_timer.start()
        else:
            self._dots_timer.stop()
            self.reset_progress()
            self._fill(self._report, self._sm)
        self._refresh_actions()

    def set_step(self, index: int, total: int, title: str) -> None:
        self._status_base = f"（{index}/{total}）{title}" if total > 1 else title
        self._dots_index = 0
        self.status_label.setText(self._status_base)

    def set_status(self, text: str) -> None:
        self._status_base = text
        self._dots_index = 0
        self.status_label.setText(text)

    def _tick_dots(self) -> None:
        self._dots_index = (self._dots_index + 1) % len(self._DOTS_FRAMES)
        self.status_label.setText(self._status_base + self._DOTS_FRAMES[self._dots_index])

    def _set_row(self, row, value: str, status: str | None = None) -> None:
        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value)
            if status in STATUS_COLORS:
                label.setStyleSheet(f"color: {STATUS_COLORS[status]};")

    def append_note(self, text: str) -> None:
        self.note_input.setPlaceholderText(text)

    # ---- 恢复二次确认（主窗口在起任务前调用） ----

    @staticmethod
    def confirm_restore(
        info: BackupInfo,
        clean: bool,
        parent: QWidget | None = None,
        target: Path | str | None = None,
    ) -> bool:
        """恢复前二次确认。

        v1.0.23：补上「将还原到」——此前只显示备份来源目录，目标目录对用户
        不可见；当备份来自另一前端（ibus ↔ fcitx5）时会静默写到另一个目录。
        目标与来源不一致、目标尚不存在时都显式标注。
        """
        lines = [
            i18n.tr("backup.confirm_time", stamp=info.created_at or "—"),
            i18n.tr("backup.confirm_source", path=info.rime_dir or i18n.tr("backup.unknown")),
            i18n.tr("backup.confirm_files", count=info.file_count),
        ]
        if info.packages:
            lines.append(
                i18n.tr("backup.confirm_packages", packages="、".join(info.packages))
            )
        target_path = Path(target) if target else None
        if target_path is not None:
            lines.append(i18n.tr("backup.confirm_target", path=str(target_path)))
        lines.append("")
        if clean:
            lines.append(i18n.tr("backup.confirm_clean"))
        else:
            lines.append(i18n.tr("backup.confirm_merge"))
        if target_path is not None:
            if not target_path.is_dir():
                lines.append(i18n.tr("backup.confirm_target_missing"))
            try:
                source = Path(info.rime_dir).resolve() if info.rime_dir else None
            except OSError:
                source = None
            if source is not None and source != target_path.resolve():
                lines.append(i18n.tr("backup.confirm_target_differs", path=str(source)))
        window = parent
        if window is None:
            from PyQt6.QtWidgets import QApplication

            window = QApplication.activeWindow()
        box = MessageBox(i18n.tr("backup.confirm_title"), "\n".join(lines), window)
        return bool(box.exec())
