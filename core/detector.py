"""环境检测服务（F1/F10 基础）。

检测 OS、会话协议、输入法框架、RIME 引擎状态、rime 用户目录、输入法环境变量。
所有探测均为 best-effort：命令缺失或超时返回 None，绝不抛异常，
保证在容器/最小环境（如开发沙箱）中也能给出部分结果。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import config

SUBPROCESS_TIMEOUT = 2

# 框架进程名 → config 中的前端键
FRONTEND_PROCESSES = {
    "ibus-rime": "ibus-daemon",
    "fcitx5-rime": "fcitx5",
    "fcitx-rime": "fcitx",
}

IM_ENV_KEYS = ("GTK_IM_MODULE", "QT_IM_MODULE", "XMODIFIERS", "SDL_IM_MODULE")

# 各前端期望的环境变量值（与 Deepin/Ubuntu 社区通行配置一致）。
# 注意（v1.0.5）：期望值仅用于「已设定时」的冲突比对——未设定是可接受的
# 正常状态：Wayland 下 GTK 应用走 Wayland 文本协议，XWayland/Qt/X11 应用
# 经 XMODIFIERS 指定的 XIM 回退由 ibus/fcitx 接管，无需四变量全设。
EXPECTED_ENV = {
    "ibus-rime": {
        "GTK_IM_MODULE": "ibus",
        "QT_IM_MODULE": "ibus",
        "XMODIFIERS": "@im=ibus",
        "SDL_IM_MODULE": "ibus",
    },
    "fcitx5-rime": {
        "GTK_IM_MODULE": "fcitx",
        "QT_IM_MODULE": "fcitx",
        "XMODIFIERS": "@im=fcitx",
        "SDL_IM_MODULE": "fcitx",
    },
    "fcitx-rime": {
        "GTK_IM_MODULE": "fcitx",
        "QT_IM_MODULE": "fcitx",
        "XMODIFIERS": "@im=fcitx",
        "SDL_IM_MODULE": "fcitx",
    },
}


def _run(cmd: list[str], timeout: int = SUBPROCESS_TIMEOUT) -> str | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass
class EnvReport:
    os_name: str = ""
    os_version: str = ""
    session_type: str = "unknown"  # x11 / wayland / unknown
    desktop: str = "unknown"
    running_frontends: list[str] = field(default_factory=list)
    active_frontend: str | None = None  # 运行中的框架（ibus-rime/fcitx5-rime/fcitx-rime）
    recommended_frontend: str = "ibus-rime"
    engine_packages: dict[str, bool] = field(default_factory=dict)
    rime_user_dir: Path | None = None
    im_env_vars: dict[str, str] = field(default_factory=dict)

    @property
    def engine_installed(self) -> bool:
        return any(self.engine_packages.values())

    @property
    def installed_engine_packages(self) -> list[str]:
        return [pkg for pkg, ok in self.engine_packages.items() if ok]

    @property
    def env_vars_ok(self) -> bool | None:
        """None = 无框架运行，无法判断（如容器/沙箱）。

        v1.0.5 起只把「已设定且值与当前前端冲突」判为异常；未设定视为
        可接受——此前要求四项全等，把 Ubuntu GNOME Wayland + ibus 的
        标准配置（GTK_IM_MODULE/SDL_IM_MODULE 未设、QT/XMODIFIERS 指向
        ibus）误报为「异常：GTK_IM_MODULE、SDL_IM_MODULE」（真机反馈）。
        """
        if not (self.active_frontend or self.running_frontends):
            return None
        return not self.env_var_mismatches

    @property
    def env_var_mismatches(self) -> list[str]:
        """已设定但值与当前前端冲突的环境变量键列表（未设定不计入，v1.0.5）。"""
        if not (self.active_frontend or self.running_frontends):
            return []
        expected = EXPECTED_ENV.get(self.active_frontend or self.recommended_frontend, {})
        return [
            key
            for key, value in expected.items()
            if self.im_env_vars.get(key, "") and self.im_env_vars[key] != value
        ]


def _detect_os(report: EnvReport) -> None:
    data: dict[str, str] = {}
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    report.os_name = data.get("NAME", "Linux")
    report.os_version = data.get("VERSION_ID") or data.get("VERSION", "")


def _detect_session(report: EnvReport) -> None:
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session in ("x11", "wayland"):
        report.session_type = session
    elif _run(["pgrep", "-x", "Xwayland"]):
        report.session_type = "wayland"
    elif _run(["pgrep", "-x", "Xorg"]) or _run(["pgrep", "-x", "X"]):
        report.session_type = "x11"
    report.desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").strip() or "unknown"


def _detect_frontends(report: EnvReport) -> None:
    running = [
        frontend
        for frontend, process in FRONTEND_PROCESSES.items()
        if _run(["pgrep", "-x", process])
    ]
    report.running_frontends = running

    gtk_module = os.environ.get("GTK_IM_MODULE", "").strip()
    if gtk_module == "ibus":
        report.active_frontend = "ibus-rime"
    elif gtk_module == "fcitx":
        report.active_frontend = (
            "fcitx5-rime" if "fcitx5-rime" in running else "fcitx-rime"
        )
    elif running:
        report.active_frontend = running[0]


def _detect_engine(report: EnvReport) -> None:
    for pkg in config.ENGINE_PACKAGES.values():
        status = _run(["dpkg-query", "-W", "-f=${Status}", pkg])
        report.engine_packages[pkg] = bool(status and "install ok installed" in status)


def _recommend_frontend(report: EnvReport) -> str:
    if report.active_frontend:
        return report.active_frontend
    desktop = report.desktop.upper()
    # DDE：Deepin 25（及同系 UOS V25）上报的 XDG_CURRENT_DESKTOP 实测为 "DDE"，
    # 不含 "DEEPIN" 子串；正常会话会被 active_frontend 旁路，但无前端进程运行的
    # 边缘场景（极简会话/开机早期）仍会走到这里，补 "DDE" 防误推 ibus-rime。
    if "DEEPIN" in desktop or "DDE" in desktop or "KDE" in desktop:
        return "fcitx5-rime"
    if "GNOME" in desktop:
        return "ibus-rime"
    return "ibus-rime"


def _detect_rime_dir(report: EnvReport) -> None:
    candidates: list[Path] = []
    if report.active_frontend:
        candidates.append(config.RIME_USER_DIRS[report.active_frontend])
    candidates.extend(config.RIME_USER_DIRS[f] for f in report.running_frontends)
    candidates.append(config.RIME_USER_DIRS[report.recommended_frontend])
    for candidate in candidates:
        if candidate.is_dir():
            report.rime_user_dir = candidate
            return
    report.rime_user_dir = config.RIME_USER_DIRS[report.recommended_frontend]


def detect() -> EnvReport:
    """执行完整环境检测，返回 EnvReport（沙箱中同样可用，字段降级为 unknown/False）。"""
    report = EnvReport()
    report.im_env_vars = {key: os.environ.get(key, "") for key in IM_ENV_KEYS}
    _detect_os(report)
    _detect_session(report)
    _detect_frontends(report)
    _detect_engine(report)
    report.recommended_frontend = _recommend_frontend(report)
    _detect_rime_dir(report)
    return report
