"""引擎安装页（Phase 2）：真实安装/卸载流程 + 进度条动画 + 日志入口。

本页只负责展示与意图发射（install_requested / uninstall_requested /
cancel_requested / log_requested），实际执行由主窗口编排；
执行期间进入 busy：全部交互禁用、进度区显示、取消按钮可用。
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
)

from core import i18n
from core.detector import EnvReport
from core.state_machine import StateMachine
from ui.pages.base import STATUS_COLORS, PageBase, ProgressAreaMixin


class EnginePage(ProgressAreaMixin, PageBase):
    install_requested = pyqtSignal(bool)  # update_first
    uninstall_requested = pyqtSignal(bool)  # purge_user_data
    cancel_requested = pyqtSignal()
    log_requested = pyqtSignal()

    _DOTS_FRAMES = ("", ".", "..", "...")

    def __init__(self, report: EnvReport, sm: StateMachine, parent=None) -> None:
        super().__init__(
            "engine.title",
            "engine.subtitle",
            parent,
        )
        self._report = report
        self._sm = sm

        self.status_row = self.add_row("引擎状态", "")
        self.pkg_row = self.add_row("已安装包", "")
        self.frontend_row = self.add_row("目标前端", "")
        self.rime_dir_row = self.add_row("配置目录", "")

        options = QHBoxLayout()
        options.setSpacing(16)
        self.update_check = CheckBox(i18n.tr("engine.update_apt"))
        self.update_check.setChecked(True)
        self.purge_check = CheckBox(i18n.tr("engine.purge"))
        options.addWidget(self.update_check)
        options.addWidget(self.purge_check)
        options.addStretch(1)
        self.card_layout.addLayout(options)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.install_button = PrimaryPushButton(
            FluentIcon.DOWNLOAD, i18n.tr("engine.install")
        )
        self.install_button.clicked.connect(
            lambda: self.install_requested.emit(self.update_check.isChecked())
        )
        self.uninstall_button = PushButton(
            FluentIcon.DELETE, i18n.tr("engine.uninstall")
        )
        self.uninstall_button.clicked.connect(
            lambda: self.uninstall_requested.emit(self.purge_check.isChecked())
        )
        self.cancel_button = PushButton(FluentIcon.CANCEL, i18n.tr("common.cancel"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.log_button = PushButton(FluentIcon.HISTORY, i18n.tr("common.view_log"))
        self.log_button.clicked.connect(self.log_requested.emit)
        for button in (
            self.install_button,
            self.uninstall_button,
            self.cancel_button,
            self.log_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        self.card_layout.addLayout(button_row)

        # 进度区（执行期间显示；等待态条纹 + 数值平滑动画见 ProgressAreaMixin）
        self._init_progress_area(self.card_layout)

        self.note_label = CaptionLabel(
            "安装/卸载经 pkexec 提权执行 apt，完整输出可在「日志」页实时查看。"
        )
        self.layout().addWidget(self.note_label)

        self._status_base = ""
        self._dots_index = 0
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(320)
        self._dots_timer.timeout.connect(self._tick_dots)

        self._fill(report, sm)
        self._show_progress_area(False)

    # ---- 状态刷新 ----

    def _fill(self, report: EnvReport, sm: StateMachine) -> None:
        installed = report.installed_engine_packages
        self._set_row(
            self.status_row,
            "已安装：" + "、".join(installed) if installed else "未安装",
            "ok" if installed else "bad",
        )
        self._set_row(self.pkg_row, "、".join(installed) or "—", "muted")
        self._set_row(self.frontend_row, report.recommended_frontend)
        self._set_row(
            self.rime_dir_row, str(report.rime_user_dir or "—"), "muted"
        )

        install_rule = sm.rule("install_engine")
        uninstall_rule = sm.rule("uninstall_engine")
        self.install_button.setEnabled(install_rule.enabled)
        self.install_button.setToolTip(install_rule.reason)
        self.uninstall_button.setEnabled(uninstall_rule.enabled)
        self.uninstall_button.setToolTip(uninstall_rule.reason)

    def _set_row(self, row, value: str, status: str | None = None) -> None:
        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value)
            if status in STATUS_COLORS:
                label.setStyleSheet(f"color: {STATUS_COLORS[status]};")

    # ---- 执行期 UI ----

    def set_busy(self, busy: bool, title: str = "") -> None:
        """进入/退出执行态：交互禁用、进度区显隐、动画起停。

        页面级不变量：busy 时安装/卸载/复选框全部禁用（不依赖调用方是否
        同步刷新状态机）；退出时按当前状态机规则恢复。
        """
        self.install_button.setEnabled(not busy)
        self.uninstall_button.setEnabled(not busy)
        self.update_check.setEnabled(not busy)
        self.purge_check.setEnabled(not busy)
        self.log_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self._show_progress_area(busy)
        if busy:
            self.enter_waiting()
            self.set_step(1, 1, title or "准备中")
            self._dots_timer.start()
        else:
            self._dots_timer.stop()
            self.reset_progress()
            self._fill(self._report, self._sm)

    def set_step(self, index: int, total: int, title: str) -> None:
        self._status_base = f"（{index}/{total}）{title}" if total > 1 else title
        self._dots_index = 0
        self.status_label.setText(self._status_base)

    def set_status(self, text: str) -> None:
        self._status_base = text
        self._dots_index = 0
        self.status_label.setText(text)

    def append_note(self, text: str) -> None:
        """在说明行追加一行结果摘要（如卸载后清理的目录）。"""
        self.note_label.setText(text)

    def _tick_dots(self) -> None:
        self._dots_index = (self._dots_index + 1) % len(self._DOTS_FRAMES)
        self.status_label.setText(self._status_base + self._DOTS_FRAMES[self._dots_index])

    def retranslate(self) -> None:
        """切语言：刷新按钮与选项文案（行值由 _fill 重填）。"""
        super().retranslate()
        self.install_button.setText(i18n.tr("engine.install"))
        self.uninstall_button.setText(i18n.tr("engine.uninstall"))
        self.cancel_button.setText(i18n.tr("common.cancel"))
        self.log_button.setText(i18n.tr("common.view_log"))
        self.update_check.setText(i18n.tr("engine.update_apt"))
        self.purge_check.setText(i18n.tr("engine.purge"))

    # ---- 绑定 ----

    def bind(self, report: EnvReport, sm: StateMachine) -> None:
        self._report, self._sm = report, sm
        self._fill(report, sm)
