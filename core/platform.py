"""Qt6 平台插件选择（F9 双协议支持：Wayland → xcb 自动回退）。

背景：Ubuntu 26.04 默认 GNOME + Wayland 会话。Qt6 应用在 wayland 平台
插件下的 GPU/合成器交互在部分驱动组合上会挂死整个会话（点击图标后窗口
不映射、系统冻结、只能强制重启）。xcb（经 XWayland）路径成熟稳定，
是本安装器类工具的安全默认。

决策规则（按优先级）：
1. 用户显式设置了 QT_QPA_PLATFORM → 尊重，不干预（offscreen 冒烟依赖）；
2. RIME_INSTALLER_FORCE_PLATFORM=wayland|xcb → 显式覆盖（逃生阀）；
3. Wayland 会话且 XWayland 可用（DISPLAY 存在且 X socket/Xwayland 进程在）
   → 强制 xcb，并关闭 xcb 的 GL 集成（QT_XCB_GL_INTEGRATION=none）——
   GL 集成在部分驱动组合下同样会挂死 GPU，安装器界面简单，无 GL 损耗可忽略；
4. Wayland 会话但无 XWayland → 不干预平台，但强制软件渲染
   （LIBGL_ALWAYS_SOFTWARE=1）——该路径 Qt 走 wayland 插件 EGL 直连 GPU，
   是冻结高危路径，软渲染规避；
5. 其他情况（X11 会话 / 探测失败）→ 不设置，交给 Qt 自行选择。

逃生阀 RIME_INSTALLER_HW_GL=1：优先级最高之一，设置后不施加任何软渲染/
GL 集成限制（个别机器软渲染本身有问题时用）。

探测全部 best-effort：任何异常都退到「不设置」，绝不影响启动。
"""

from __future__ import annotations

import os
import shutil
import subprocess

#: 逃生阀：强制指定平台（调试或个别机器规避问题时用）
FORCE_ENV = "RIME_INSTALLER_FORCE_PLATFORM"
#: 用户显式指定平台时尊重的标准 Qt 环境变量
QT_ENV = "QT_QPA_PLATFORM"
#: 逃生阀：为 1 时不施加软渲染 / GL 集成限制（硬件 GL 路径）
HW_GL_ENV = "RIME_INSTALLER_HW_GL"
#: xcb 路径下关闭 GL 集成，规避部分驱动的 GPU 挂死
QT_XCB_GL_INTEGRATION = "QT_XCB_GL_INTEGRATION"
#: wayland 路径下强制软件渲染，规避 EGL 直连 GPU 的冻结
LIBGL_SOFTWARE = "LIBGL_ALWAYS_SOFTWARE"

_PROBE_TIMEOUT = 1.0


def _probe_xwayland() -> bool:
    """XWayland 是否可用：X socket 文件存在或 Xwayland 进程在运行。"""
    display = os.environ.get("DISPLAY", "").strip()
    if not display:
        return False
    # display 形如 :0 / :1 / localhost:10.0；取编号部分
    try:
        number = display.rsplit(":", 1)[-1].split(".")[0]
    except (IndexError, ValueError):
        return False
    if not number.isdigit():
        return False
    if os.path.exists(f"/tmp/.X11-unix/X{number}"):
        return True
    # 某些环境 XWayland 只监听 abstract socket，无 socket 文件；
    # 用 pgrep 兜底（找不到 pgrep 或超时都按不可用处理，不影响启动）
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return False
    try:
        result = subprocess.run(
            [pgrep, "-x", "Xwayland"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return result.returncode == 0


def _is_wayland_session() -> bool:
    """当前是否 Wayland 会话（XDG_SESSION_TYPE 或 WAYLAND_DISPLAY 判定）。"""
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY", "").strip())


def _hardware_gl_allowed() -> bool:
    """硬件 GL 逃生阀是否开启（开启则不施加软渲染 / GL 集成限制）。"""
    return os.environ.get(HW_GL_ENV, "").strip() == "1"


def select_qpa_platform() -> str:
    """返回应当使用的 Qt 平台插件名；"" 表示不干预（交给 Qt 默认选择）。

    调用时机：必须在 QApplication 创建之前（Qt 在构造 QApplication 时
    才读取 QT_QPA_PLATFORM）。附带决策 GL 渲染策略（见模块docstring）。
    """
    forced = os.environ.get(FORCE_ENV, "").strip().lower()
    if forced in ("wayland", "xcb"):
        os.environ[QT_ENV] = forced
        return forced
    user_set = os.environ.get(QT_ENV, "").strip()
    if user_set:
        # 用户显式指定（如 offscreen / wayland / xcb）：尊重
        return user_set
    try:
        if _is_wayland_session():
            if _probe_xwayland():
                # F9：Wayland 会话走 XWayland（xcb），规避 wayland 插件
                # 在部分驱动组合下挂死会话的问题
                os.environ[QT_ENV] = "xcb"
                if not _hardware_gl_allowed():
                    # GL 集成（xcb 下的 OpenGL 上下文）在部分驱动组合下
                    # 同样会挂死 GPU；安装器界面简单，关闭无感知
                    os.environ[QT_XCB_GL_INTEGRATION] = "none"
                return "xcb"
            # 纯 Wayland 无 XWayland：不干预平台（强退 xcb 反而起不来），
            # 但该路径 EGL 直连 GPU 是冻结高危路径，强制软件渲染规避
            if not _hardware_gl_allowed():
                os.environ[LIBGL_SOFTWARE] = "1"
    except Exception:  # noqa: BLE001 — 探测失败绝不阻断启动
        return ""
    return ""


def platform_decision_note() -> str:
    """人类可读的平台决策说明（写入启动日志，便于现场排查）。"""
    platform = select_qpa_platform()
    session = os.environ.get("XDG_SESSION_TYPE", "").strip() or "unknown"
    notes: list[str] = []
    if os.environ.get(QT_XCB_GL_INTEGRATION) == "none":
        notes.append("xcb GL 集成已关闭（QT_XCB_GL_INTEGRATION=none）")
    if os.environ.get(LIBGL_SOFTWARE) == "1":
        notes.append("已强制软件渲染（LIBGL_ALWAYS_SOFTWARE=1）")
    if _hardware_gl_allowed():
        notes.append("硬件 GL 逃生阀生效（RIME_INSTALLER_HW_GL=1，未施加 GL 限制）")
    suffix = f"；{'；'.join(notes)}" if notes else ""
    forced = os.environ.get(FORCE_ENV, "").strip().lower()
    if forced in ("wayland", "xcb"):
        return f"Qt 平台：{platform}（RIME_INSTALLER_FORCE_PLATFORM 显式覆盖；会话类型={session}）{suffix}"
    if platform == "xcb":
        return f"Qt 平台：xcb（Wayland 会话经 XWayland 运行，QT_QPA_PLATFORM=xcb；会话类型={session}）{suffix}"
    if platform:
        return f"Qt 平台：{platform}（QT_QPA_PLATFORM 已显式设置或被覆盖；会话类型={session}）{suffix}"
    if os.environ.get(LIBGL_SOFTWARE) == "1":
        return (
            f"Qt 平台：Qt 默认选择（纯 Wayland 无 XWayland，EGL 直连 GPU 为冻结高危路径，"
            f"已强制软件渲染规避；会话类型={session}）{suffix}"
        )
    return f"Qt 平台：Qt 默认选择（Wayland 回退条件不满足；会话类型={session}）{suffix}"
