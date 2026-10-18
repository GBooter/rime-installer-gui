"""深浅色主题（Phase 6 / v1.0.0）：跟随系统 / 浅色 / 深色。

设计要点：
- 偏好持久化用 QSettings（org/app 均取 config.APP_ID），不新增存储设施；
- 「跟随系统」委托给 qfluentwidgets 的 Theme.AUTO（内部监听系统主题变化），
  同时在 core 侧用带超时的 gsettings 探测解析出当前实际明暗，供状态色板
  切换使用（探测失败按浅色，不用无超时的 darkdetect，避免阻塞启动）；
- STATUS_COLORS（ui/pages/base.py）采用「整体替换内容」而非换引用：
  各页面在 fill 时以 `STATUS_COLORS[status]` 实时查表，替换 dict 内容后
  经主窗口 _dispatch() 重fill 即可全量换色，无需逐个页面加钩子。
"""

from __future__ import annotations

import shutil
import subprocess

AUTO = "auto"
LIGHT = "light"
DARK = "dark"

#: gsettings 探测超时（秒）：darkdetect 的 gsettings 调用无超时，会话异常时
#: 会阻塞启动；本模块自带超时探测，超时即按浅色，绝不拉长启动时间
_GSETTINGS_TIMEOUT = 2.0

#: 设置页下拉与持久化的合法值（顺序即展示顺序）
PREFERENCES = (AUTO, LIGHT, DARK)

_LABELS = {AUTO: "跟随系统", LIGHT: "浅色", DARK: "深色"}


def label(mode: str) -> str:
    """主题模式的中文展示名（设置页与 tooltip 共用）。"""
    return _LABELS.get(mode, _LABELS[AUTO])


def load_preference() -> str:
    """读取持久化的主题偏好；无记录或非法值返回 auto。"""
    from PyQt6.QtCore import QSettings

    settings = QSettings("rime-installer", "rime-installer")
    mode = str(settings.value("theme", AUTO) or AUTO)
    return mode if mode in PREFERENCES else AUTO


def save_preference(mode: str) -> None:
    """持久化主题偏好（非法值忽略，保持原值）。"""
    if mode not in PREFERENCES:
        return
    from PyQt6.QtCore import QSettings

    QSettings("rime-installer", "rime-installer").setValue("theme", mode)


def _probe_system_dark() -> bool | None:
    """gsettings 探测系统深浅色；不可用/超时/异常返回 None。

    优先 color-scheme（'prefer-dark' 为深色），fallback gtk-theme
    （主题名含 dark 为深色）。无 gsettings、命令失败、超时都返回 None，
    由调用方按浅色处理，绝不阻断启动。
    """
    gsettings = shutil.which("gsettings")
    if gsettings is None:
        return None
    probes = (
        ("color-scheme", ("prefer-dark",)),
        ("gtk-theme", ("dark",)),
    )
    for key, dark_markers in probes:
        try:
            result = subprocess.run(
                [gsettings, "get", "org.gnome.desktop.interface", key],
                capture_output=True,
                text=True,
                timeout=_GSETTINGS_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if result.returncode != 0:
            continue
        value = result.stdout.strip().strip("'\"").lower()
        if not value:
            continue
        return any(marker in value for marker in dark_markers)
    return None


def resolve_mode(mode: str) -> str:
    """把 auto 解析为当前实际的 light/dark（探测失败按浅色）。

    只用带超时的 gsettings 探测：vendor 的 darkdetect 在 Linux 上用无
    timeout 的 subprocess 调 gsettings，gsettings 挂起时两次调用各阻塞
    约 30s（QA 回归实测回退路径总耗时 62s），故探测失败直接按浅色，
    不再回退 darkdetect。显式 light/dark 直通；任何路径不抛异常。
    """
    if mode in (LIGHT, DARK):
        return mode
    is_dark = _probe_system_dark()
    return DARK if is_dark else LIGHT


def apply_theme(mode: str) -> str:
    """应用主题：qfluentwidgets 全局主题 + 状态色板；返回解析后的实际明暗。"""
    from qfluentwidgets import Theme, setTheme

    resolved = resolve_mode(mode)
    qt_theme = {
        AUTO: Theme.AUTO,
        LIGHT: Theme.LIGHT,
        DARK: Theme.DARK,
    }[mode if mode in PREFERENCES else AUTO]
    if qt_theme == Theme.AUTO:
        # AUTO 不透传 Theme.AUTO：vendor 的 theme setter 会调用无超时的
        # darkdetect.theme()（gsettings 挂起时阻塞启动约 60s）；「跟随系统」
        # 在启动时按 resolve_mode 的快照落地（本应用未启用运行中主题监听）
        qt_theme = Theme.DARK if resolved == DARK else Theme.LIGHT
    setTheme(qt_theme)
    from ui.pages.base import apply_status_palette

    apply_status_palette(resolved)
    return resolved
