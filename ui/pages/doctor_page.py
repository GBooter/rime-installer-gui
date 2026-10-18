"""环境修复页（Phase 5 / F10）：一键排查 + 自动修复 + 部署引导。

本页只负责展示与意图发射，执行由主窗口编排：
- fix_requested(keys)：对选中/全部可修复项执行修复步骤；
- restart_requested / redeploy_requested / config_tool_requested：部署引导；
- cancel_requested / log_requested / redetect_requested。
检查结果随主窗口 redetect 自动刷新（bind → run_checks）。

v1.0.5：标题由「环境体检」改为「环境修复」——与欢迎页（环境体检）的
objectName 相同时，NavigationPanel.insertItem 会静默丢弃本页导航项，
侧栏只剩 6 项、本页无法从侧栏到达（routeKey 必须全页面唯一）。
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
)

from core.detector import EnvReport
from core import i18n
from core.doctor import CheckResult, run_checks
from core.state_machine import StateMachine
from ui.pages.base import STATUS_COLORS, PageBase, ProgressAreaMixin

_LEVEL_STATUS = {"ok": "ok", "warn": "warn", "bad": "bad", "info": "muted"}

#: 标题列最小宽度（中文最长标题「默认输入法框架」约 100px，留余量）
_TITLE_MIN = 132


def localized(result: CheckResult) -> tuple[str, str, str]:
    """把体检结果本地化为 ``(title, detail, advice)``。

    中文由 ``core/doctor.py`` 的字面量直接给出（同时也是英文缺键时的回退），
    英文包只需覆盖 ``doctor.check.*``。旧实现把中文写死在 CheckResult 里、
    UI 原样展示，英文界面下八行结果全是中文。
    """
    title = i18n.tr_or(f"doctor.check.{result.key}.title", result.title)
    if not result.i18n_key:
        return title, result.detail, result.advice
    params = result.i18n_params
    detail = i18n.tr_or(f"{result.i18n_key}.detail", result.detail, **params)
    advice = (
        i18n.tr_or(f"{result.i18n_key}.advice", result.advice, **params)
        if result.advice
        else ""
    )
    return title, detail, advice


class CheckRow(QHBoxLayout):
    """单条体检结果：级别图标 + 标题 + 详情 + 建议。"""

    def __init__(self, result: CheckResult) -> None:
        super().__init__()
        self.setSpacing(10)
        icon = BodyLabel(result.icon)
        icon.setStyleSheet(f"color: {STATUS_COLORS[_LEVEL_STATUS[result.level]]};")
        icon.setFixedWidth(16)
        self.addWidget(icon)

        title, detail, advice = localized(result)
        self.title_label = BodyLabel(title)
        # 初始值只作下限；实际列宽由 DoctorPage._sync_title_width() 按所有行
        # 的最宽标题统一设置（英文标题远比中文长，固定 132 会裁成
        # “Input method envirc”）
        self.title_label.setFixedWidth(_TITLE_MIN)
        self.addWidget(self.title_label)

        self.detail_label = BodyLabel(detail)
        self.detail_label.setWordWrap(True)
        self.addWidget(self.detail_label, 1)

        self.advice_label = CaptionLabel(advice)
        self.advice_label.setWordWrap(True)
        self.advice_label.setFixedWidth(300)
        self.addWidget(self.advice_label)

    def update_result(self, result: CheckResult) -> None:
        title, detail, advice = localized(result)
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.advice_label.setText(advice)

    def title_hint(self) -> int:
        """当前标题所需宽度（含少量余量），供整列取最大值。"""
        return max(_TITLE_MIN, self.title_label.sizeHint().width() + 8)

    def set_title_width(self, width: int) -> None:
        self.title_label.setFixedWidth(width)


class DoctorPage(ProgressAreaMixin, PageBase):
    fix_requested = pyqtSignal(list)  # 待修复的体检项 key 列表
    write_login_requested = pyqtSignal()  # 写入登录配置（环境变量）的次要入口
    restart_requested = pyqtSignal()
    redeploy_requested = pyqtSignal()
    config_tool_requested = pyqtSignal()
    redetect_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    log_requested = pyqtSignal()

    _DOTS_FRAMES = ("", ".", "..", "...")

    def __init__(
        self,
        report: EnvReport,
        sm: StateMachine,
        installed_keys: set[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(
            "doctor.title",
            "doctor.subtitle",
            parent,
        )
        self._report = report
        self._sm = sm
        self._installed_keys = set(installed_keys or ())
        self._results: list[CheckResult] = []
        self._rows: dict[str, CheckRow] = {}

        self.summary_row = self.add_row(i18n.tr("doctor.overall"), "")

        # 操作按钮行
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.fix_button = PrimaryPushButton(FluentIcon.BROOM, i18n.tr("doctor.fix"))
        self.fix_button.setToolTip(i18n.tr("doctor.fix_tip"))
        self.fix_button.clicked.connect(self._on_fix_clicked)
        # v1.0.27：登录配置的独立入口。体检「输入法环境变量」一项在已就绪时
        # 不可修复（fix_button 里没有它），但用户仍可能想主动重写一份
        # （换过前端、手工改过配置文件、或换机器后从备份恢复）——给它一个
        # 与体检结果无关的次要按钮，点击后由主窗口弹确认框再执行。
        self.login_button = PushButton(
            FluentIcon.SAVE, i18n.tr("doctor.write_login")
        )
        self.login_button.setToolTip(i18n.tr("doctor.write_login_tip"))
        self.login_button.clicked.connect(self.write_login_requested.emit)
        self.restart_button = PushButton(
            FluentIcon.ROTATE, i18n.tr("doctor.restart")
        )
        self.restart_button.clicked.connect(self.restart_requested.emit)
        self.redeploy_button = PushButton(
            FluentIcon.SYNC, i18n.tr("doctor.redeploy")
        )
        self.redeploy_button.setToolTip(i18n.tr("doctor.redeploy_tip"))
        self.redeploy_button.clicked.connect(self.redeploy_requested.emit)
        self.config_tool_button = PushButton(
            FluentIcon.APPLICATION, i18n.tr("doctor.config_tool")
        )
        self.config_tool_button.setToolTip(i18n.tr("doctor.config_tool_tip"))
        self.config_tool_button.clicked.connect(self.config_tool_requested.emit)
        self.redetect_button = PushButton(
            FluentIcon.UPDATE, i18n.tr("doctor.redetect")
        )
        self.redetect_button.clicked.connect(self.redetect_requested.emit)
        for button in (
            self.fix_button,
            self.login_button,
            self.restart_button,
            self.redeploy_button,
            self.config_tool_button,
            self.redetect_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        self.card_layout.addLayout(button_row)

        # 部署引导说明
        self.guide_label = CaptionLabel(i18n.tr("doctor.guide"))
        self.guide_label.setWordWrap(True)
        self.card_layout.addWidget(self.guide_label)

        # 检查结果区（可滚动）
        from PyQt6.QtWidgets import QWidget

        from qfluentwidgets import ScrollArea

        self.checks_host = QWidget()
        from PyQt6.QtWidgets import QVBoxLayout

        self.checks_layout = QVBoxLayout(self.checks_host)
        self.checks_layout.setContentsMargins(2, 2, 2, 2)
        self.checks_layout.setSpacing(8)

        self.scroll_area = ScrollArea()
        self.scroll_area.setWidget(self.checks_host)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.enableTransparentBackground()
        # v1.0.5：滚动区独享剩余高度（与方案市场同款布局修复）
        self.add_expanding_scroll(self.scroll_area)

        # 执行进度区（等待态条纹 + 数值平滑动画见 ProgressAreaMixin）
        self._init_progress_area(self.card_layout)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.cancel_button = PushButton(FluentIcon.CANCEL, i18n.tr("common.cancel"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.log_button = PushButton(FluentIcon.HISTORY, i18n.tr("common.view_log"))
        self.log_button.clicked.connect(self.log_requested.emit)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.log_button)
        action_row.addStretch(1)
        self.card_layout.addLayout(action_row)

        self._status_base = ""
        self._dots_index = 0
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(320)
        self._dots_timer.timeout.connect(self._tick_dots)

        self.bind(report, sm)
        self._show_progress_area(False)

    # ---- 状态刷新 ----

    def bind(
        self,
        report: EnvReport,
        sm: StateMachine,
        installed_keys: set[str] | None = None,
    ) -> None:
        if installed_keys is not None:
            self._installed_keys = set(installed_keys)
        self._report, self._sm = report, sm
        self._results = run_checks(report, sorted(self._installed_keys))
        self._rebuild_rows()
        self._fill(report, sm)

    def _rebuild_rows(self) -> None:
        # 清空旧行。注意：CheckRow 是 QHBoxLayout，item.widget() 恒为 None——
        # v1.0.5 及之前只处理 QWidget item，旧行内的 label 从未销毁，重新检测
        # 后新旧行在同一位置叠加绘制（真机截图：底部一团文字互相重叠）。
        # 必须递归销毁 layout 内的子 widget，再销毁 layout 本身。
        while self.checks_layout.count():
            item = self.checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
                continue
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    sub = layout.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()
                layout.deleteLater()
        self._rows = {}
        for result in self._results:
            row = CheckRow(result)
            self._rows[result.key] = row
            self.checks_layout.addLayout(row)
        self.checks_layout.addStretch(1)
        self._sync_title_width()

    def _sync_title_width(self) -> None:
        """标题列统一取最宽的一个，保证各行 detail 起点对齐且标题不被裁切。

        旧实现每行固定 132px：中文够用，英文 “Input method environment” /
        “Input method engine” 会被截成 “Input method envirc”。
        """
        if not getattr(self, "_rows", None):
            return
        width = max(row.title_hint() for row in self._rows.values())
        for row in self._rows.values():
            row.set_title_width(width)

    def retranslate(self) -> None:
        """切语言：标题 + 按钮 + 引导说明；摘要与 tooltip 由 _fill 重算。"""
        super().retranslate()
        label = self.summary_row.itemAt(0).widget()
        if label is not None:
            label.setText(i18n.tr("doctor.overall"))
        self.fix_button.setText(i18n.tr("doctor.fix"))
        self.login_button.setText(i18n.tr("doctor.write_login"))
        self.login_button.setToolTip(
            i18n.tr(
                "doctor.write_login_tip_ok"
                if self._im_env_ready()
                else "doctor.write_login_tip"
            )
        )
        self.restart_button.setText(i18n.tr("doctor.restart"))
        self.redeploy_button.setText(i18n.tr("doctor.redeploy"))
        self.config_tool_button.setText(i18n.tr("doctor.config_tool"))
        self.redetect_button.setText(i18n.tr("doctor.redetect"))
        self.cancel_button.setText(i18n.tr("common.cancel"))
        self.log_button.setText(i18n.tr("common.view_log"))
        self.guide_label.setText(i18n.tr("doctor.guide"))
        # 八行体检结果的 title/detail/advice 也要跟着切语言（旧实现写死中文）。
        # 用 getattr 兜底：基类可能在 bind()（首次赋值 _results/_rows）之前就
        # 调一次 retranslate()。
        rows = getattr(self, "_rows", {})
        for result in getattr(self, "_results", []):
            row = rows.get(result.key)
            if row is not None:
                row.update_result(result)
        # 换语言后标题长度会变（中→英变长、英→中变短），列宽要重算
        self._sync_title_width()

    def _im_env_ready(self) -> bool:
        """登录配置文件是否已就绪（决定「写入登录配置」的按钮文案/提示）。

        判据取自体检结果本身：``im_env`` 不需要修复 = 文件已写好且与当前
        前端一致（或已写好待重新登录生效）。这样不用在 UI 层再读一次磁盘。
        """
        for result in getattr(self, "_results", []):
            if result.key == "im_env":
                return not result.fixable
        return False

    def _fill(self, report: EnvReport, sm: StateMachine) -> None:
        bad = sum(1 for r in self._results if r.level == "bad")
        warn = sum(1 for r in self._results if r.level == "warn")
        fixable = sum(1 for r in self._results if r.fixable)
        if bad:
            summary, status = i18n.tr("doctor.summary_bad", bad=bad, warn=warn), "bad"
        elif warn:
            summary, status = i18n.tr("doctor.summary_warn", warn=warn), "warn"
        else:
            summary, status = i18n.tr("doctor.summary_ok"), "ok"
        if fixable:
            summary += i18n.tr("doctor.fixable_count", fixable=fixable)
        self._set_row(self.summary_row, summary, status)

        self.fix_button.setEnabled(bool(fixable) and not sm.busy)
        self.fix_button.setToolTip(
            i18n.tr("doctor.fix_button_n", fixable=fixable)
            if fixable
            else i18n.tr("doctor.fix_button_none")
        )
        self.restart_button.setEnabled(sm.rule("restart_frontend").enabled)
        self.restart_button.setToolTip(sm.rule("restart_frontend").reason or "重启输入法框架进程")
        # v1.0.27：「写入登录配置」与体检结果无关——OK 状态下也可用（幂等重写）。
        # 提示文案随状态切换，避免用户以为它只在出问题时才能点。
        login_rule = sm.rule("write_login_env")
        self.login_button.setEnabled(login_rule.enabled)
        self.login_button.setToolTip(
            login_rule.reason
            if not login_rule.enabled
            else i18n.tr(
                "doctor.write_login_tip_ok"
                if self._im_env_ready()
                else "doctor.write_login_tip"
            )
        )

        self.redeploy_button.setEnabled(sm.rule("redeploy").enabled)
        self.redeploy_button.setToolTip(sm.rule("redeploy").reason or "强制 rime 重新编译配置")
        self.config_tool_button.setEnabled(sm.rule("open_config_tool").enabled)
        self.config_tool_button.setToolTip(
            sm.rule("open_config_tool").reason or "打开输入法框架的配置工具"
        )
        self.redetect_button.setEnabled(not sm.busy)

    def _set_row(self, row, value: str, status: str | None = None) -> None:
        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value)
            if status in STATUS_COLORS:
                label.setStyleSheet(f"color: {STATUS_COLORS[status]};")

    def _on_fix_clicked(self) -> None:
        keys = [r.key for r in self._results if r.fixable]
        if keys:
            self.fix_requested.emit(keys)

    # ---- 执行期 UI（主窗口 busy 协议） ----

    def set_busy(self, busy: bool, title: str = "") -> None:
        self.fix_button.setEnabled(not busy)
        self.login_button.setEnabled(not busy)
        self.restart_button.setEnabled(not busy)
        self.redeploy_button.setEnabled(not busy)
        self.config_tool_button.setEnabled(not busy)
        self.redetect_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.log_button.setEnabled(not busy)
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

    def _tick_dots(self) -> None:
        self._dots_index = (self._dots_index + 1) % len(self._DOTS_FRAMES)
        self.status_label.setText(self._status_base + self._DOTS_FRAMES[self._dots_index])
