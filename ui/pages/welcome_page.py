"""欢迎页：环境体检结果总览 + 重新检测。

v1.0.4 修复：行标签此前直接把裸 key（os/session/desktop/…）传给 add_row，
界面左侧显示成一排英文标识符（用户反馈「体检下面的信息都是英文的」）。
现改为 i18n 键驱动，并在 retranslate 中随语言切换刷新。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout

from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon, PrimaryPushButton

import config
from core import i18n
from core.detector import EnvReport
from core.state_machine import InstallerState, StateMachine
from ui.pages.base import PageBase

_SESSION_LABELS = {"x11": "X11", "wayland": "Wayland", "unknown": "未能识别"}

#: 安装状态机 → i18n 键（底部摘要行的状态值；state_machine 的 .value 是中文源文案）
_STATE_KEYS = {
    InstallerState.NO_ENGINE: "state.no_engine",
    InstallerState.ENGINE_READY: "state.engine_ready",
    InstallerState.SCHEMES_INSTALLED: "state.schemes_installed",
    InstallerState.DEPLOYED: "state.deployed",
}

#: 行标识 → i18n 键（左列标签；值列由 _fill 填入检测结果）
_ROW_KEYS = {
    "os": "row.os",
    "session": "row.session",
    "desktop": "row.desktop",
    "frontend": "row.framework",
    "engine": "row.engine",
    "rime_dir": "row.rime_dir",
    "env": "row.env",
}


class WelcomePage(PageBase):
    def __init__(self, report: EnvReport, sm: StateMachine, on_redetect, parent=None) -> None:
        super().__init__(
            "welcome.title",
            "welcome.subtitle",
            parent,
        )
        self._report = report
        self._sm = sm
        self._on_redetect = on_redetect

        self._rows: dict[str, object] = {}
        for key, i18n_key in _ROW_KEYS.items():
            self._rows[key] = self.add_row(i18n.tr(i18n_key), "")

        button_row = QHBoxLayout()
        self.redetect_button = PrimaryPushButton(FluentIcon.SYNC, i18n.tr("welcome.redetect"))
        self.redetect_button.clicked.connect(lambda: self._on_redetect())
        button_row.addWidget(self.redetect_button)
        button_row.addStretch(1)
        self.card_layout.addLayout(button_row)

        self.summary_label = CaptionLabel("")
        self.layout().addWidget(self.summary_label)

        self._fill(report)

    def _fill(self, report: EnvReport) -> None:
        self._set("os", f"{report.os_name} {report.os_version}".strip())
        self._set("session", _SESSION_LABELS.get(report.session_type, report.session_type))
        # XDG_CURRENT_DESKTOP 未导出时为 "unknown"，转成中文展示
        desktop = report.desktop
        self._set(
            "desktop",
            i18n.tr("status.unknown") if desktop == "unknown" else desktop,
        )
        if report.active_frontend:
            frontend = f"{report.active_frontend}{i18n.tr('welcome.running')}"
            status = "ok"
        elif report.running_frontends:
            frontend = "、".join(report.running_frontends)
            status = "ok"
        else:
            frontend = i18n.tr("welcome.no_frontend_running")
            status = "warn"
        self._set("frontend", frontend, status)
        if report.engine_installed:
            self._set(
                "engine",
                f"{i18n.tr('status.installed')}: "
                + "、".join(report.installed_engine_packages),
                "ok",
            )
        else:
            self._set("engine", i18n.tr("status.not_installed"), "bad")
        self._set("rime_dir", str(report.rime_user_dir or "—"), "muted")
        if report.env_vars_ok is None:
            self._set("env", i18n.tr("welcome.env_skip"), "muted")
        elif report.env_vars_ok:
            self._set("env", i18n.tr("welcome.env_ok"), "ok")
        else:
            self._set(
                "env",
                i18n.tr(
                    "welcome.env_bad",
                    items="、".join(report.env_var_mismatches),
                ),
                "bad",
            )

        state = self._sm.state
        if state is InstallerState.NO_ENGINE:
            self.summary_label.setText(i18n.tr("welcome.next_engine"))
        else:
            self.summary_label.setText(
                i18n.tr("welcome.summary", state=i18n.tr(_STATE_KEYS[state]))
            )

    def _set(self, key: str, value: str, status: str | None = None) -> None:
        row: QHBoxLayout = self._rows[key]  # type: ignore[assignment]
        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value or "—")
            color = {"ok": "#3B6D11", "bad": "#A32D2D", "warn": "#854F0B", "muted": "#5F5E5A"}
            if status in color:
                label.setStyleSheet(f"color: {color[status]};")

    def retranslate(self) -> None:
        """切语言：刷新行标签与重新检测按钮（行值由 _fill 重填）。"""
        super().retranslate()
        for key, i18n_key in _ROW_KEYS.items():
            row = self._rows.get(key)
            if row is None:
                continue
            item = row.itemAt(0)
            label = item.widget() if item is not None else None
            if label is not None:
                label.setText(i18n.tr(i18n_key))
        self.redetect_button.setText(i18n.tr("welcome.redetect"))

    def bind(self, report: EnvReport, sm: StateMachine) -> None:
        self._report, self._sm = report, sm
        self._fill(report)
