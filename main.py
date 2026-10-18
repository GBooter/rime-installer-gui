"""应用入口。

开发期冒烟：QT_QPA_PLATFORM=offscreen python3 smoke_test.py
正常启动：.venv/bin/python main.py 或 python3 main.py

启动保障（2026-10-14 修复「deb 装机后点击图标无响应/系统冻结」）：
- F9 双协议回退：Wayland 会话且 XWayland 可用时强制 QT_QPA_PLATFORM=xcb，
  规避 Qt6 wayland 插件在部分驱动组合下挂死会话的问题（见 core/platform.py）；
- v1.0.2 启动加固：xcb 路径关闭 GL 集成、纯 Wayland 强制软件渲染
  （RIME_INSTALLER_HW_GL=1 逃生阀豁免），窗口成功显示即写 launch-ok 标记，
  launcher 依「退出码 + 标记」做三级重试（默认 → xcb → wayland+软渲染）；
- v1.0.3 缺依赖修复：deb Depends 补 python3-pyqt6.qtsvg（qfluentwidgets import 期
  急切导入 QtSvg，缺失即秒崩；「系统卡死」系反复点击无响应的误判）；
- v1.0.4 运行期取证：窗口显示后启动 GUI 心跳看门狗（core/watchdog.py）——事件循环
  卡死（真机反馈「鼠标悬停转圈、系统卡住」但无法复现）时自动 dump 全线程栈与
  平台环境到 logs/gui-stall-*.log，运行面包屑写 logs/runtime-trace.log。
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import config
from core import i18n, theme, watchdog

# 发布版保障：qfluentwidgets 等纯 Python 依赖随包放在 resources/vendor，
# 系统 site-packages 缺失时（deb 装机场景）回退到 vendor 目录。
_VENDOR = config.RESOURCES_DIR / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.platform import platform_decision_note, select_qpa_platform  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

#: 运行期看护（v1.0.4）：持有引用防 GC，进程退出随主线程结束
_RUNTIME_GUARD: watchdog.StallWatchdog | None = None
_HEARTBEAT: QTimer | None = None


def build_application() -> QApplication:
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_ID)
    app.setApplicationDisplayName(config.APP_NAME)
    app.setApplicationVersion(config.VERSION)
    app.setOrganizationName(config.APP_ID)
    # v1.0.9：应用级图标（对话框等无父窗口场景）；主窗口另有窗口级设置
    if config.APP_ICON.exists():
        app.setWindowIcon(QIcon(str(config.APP_ICON)))
    else:
        app.setWindowIcon(QIcon.fromTheme(config.APP_ID))
    # 主题与语言须在首个窗口创建前应用：前者是 qfluentwidgets 全局样式 +
    # 状态色板，后者决定页面标题（进而决定导航 routeKey）的初始文案
    theme.apply_theme(theme.load_preference())
    i18n.set_language(i18n.load_preference())
    return app


def write_launch_marker() -> None:
    """写入启动成功标记（ISO 时间戳）。

    调用时机：窗口 show() 之后、app.exec() 之前——标记存在即说明窗口
    成功显示过（含用户后续主动关闭），launcher 据此避免把正常退出
    误判为启动失败而重试。目录不存在先建，失败静默（不影响启动）。
    """
    try:
        config.LAUNCH_OK_MARKER.parent.mkdir(parents=True, exist_ok=True)
        config.LAUNCH_OK_MARKER.write_text(
            datetime.now().astimezone().isoformat(timespec="seconds"),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — 标记写失败不能影响启动
        pass


def _write_startup_crash(exc: BaseException) -> Path | None:
    """把启动期异常落盘到日志目录（Terminal=false 时 stderr 不可见）。"""
    try:
        config.ensure_user_dirs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = config.LOG_DIR / f"startup-crash-{stamp}.log"
        path.write_text(
            f"启动崩溃时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"版本：v{config.VERSION}\n"
            f"平台决策：{platform_decision_note()}\n"
            f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')}\n"
            f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '')}\n"
            f"DISPLAY={os.environ.get('DISPLAY', '')}\n"
            f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}\n"
            f"\n{traceback.format_exc()}\n",
            encoding="utf-8",
        )
        return path
    except Exception:  # noqa: BLE001 — 日志失败不能再抛
        return None


def _start_runtime_guard(app: QApplication) -> None:
    """v1.0.4：窗口显示后启动 GUI 心跳看门狗 + 运行面包屑。

    真机反馈「鼠标悬停一直转圈、系统卡住」但 offscreen 冒烟无法复现，缺现场
    证据。此处让 GUI 线程每秒打卡，看护狗线程据此判定事件循环是否卡死并 dump
    全线程栈（见 core/watchdog.py 模块 docstring）。
    """
    global _RUNTIME_GUARD, _HEARTBEAT
    guard = watchdog.StallWatchdog()
    guard.set_context(platform_decision_note())
    guard.trace(f"窗口已显示 v{config.VERSION} pid={os.getpid()}")
    guard.start()
    heartbeat = QTimer(app)
    heartbeat.setInterval(1000)
    heartbeat.timeout.connect(guard.beat)
    heartbeat.start()
    _RUNTIME_GUARD, _HEARTBEAT = guard, heartbeat


def _stop_runtime_guard(code: int) -> None:
    """事件循环退出：停心跳与看门狗，记一条正常退出面包屑。"""
    global _HEARTBEAT
    if _HEARTBEAT is not None:
        _HEARTBEAT.stop()
    if _RUNTIME_GUARD is not None:
        _RUNTIME_GUARD.trace(f"事件循环退出 code={code}")
        _RUNTIME_GUARD.stop()


def main() -> int:
    config.ensure_user_dirs()
    # F9：平台选择必须在 QApplication 创建前完成（Qt 构造时才读该变量）
    select_qpa_platform()
    try:
        app = build_application()
        window = MainWindow()
        window.show()
        # 窗口已显示：写入启动标记，供 launcher 判断是否发生可见启动
        write_launch_marker()
        _start_runtime_guard(app)
        code = app.exec()
        _stop_runtime_guard(code)
        return code
    except BaseException as exc:  # noqa: BLE001 — 启动期任何失败都要留痕
        log_path = _write_startup_crash(exc)
        detail = f"启动失败：{exc}"
        if log_path is not None:
            detail += f"\n详情已写入：{log_path}"
        # 尽量弹框告知（QApplication 可能尚未创建成功，失败则退回 stderr）
        try:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.critical(None, f"{config.APP_NAME} 启动失败", detail)
        except Exception:  # noqa: BLE001
            print(detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
