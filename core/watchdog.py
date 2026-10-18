"""GUI 事件循环卡死看门狗 + 进程内运行痕迹（v1.0.4）。

背景：v1.0.3 修复 QtSvg 秒崩后，装机真机反馈「鼠标悬停一直转圈、系统卡住」，
但开发机 offscreen 冒烟全绿、无法复现——缺的是现场证据：事件循环一旦卡死，
应用自身的任务日志不再产出，launcher.log 也只有进程级退出码，无法回答
「卡死时GUI 线程停在哪一行」。

本模块两个能力，均为 best-effort：任何异常都不能影响主流程。

1. **StallWatchdog（卡死看门狗）**：
   - GUI 线程由 QTimer 每秒调用 `beat()` 打卡；
   - 独立守护线程每 `check_interval` 秒检查一次，距上次打卡超过
     `stall_threshold` 秒即判定事件循环卡死：dump 所有线程的 Python 栈 +
     平台决策 + GL 环境变量到 `logs/gui-stall-<时间戳>.log`；
   - 卡死期间按 `dump_min_interval` 最少间隔补 dump（避免刷盘），心跳恢复后
     重置状态并记一条 trace；
   - 看护线程只做纯 Python 计算与文件写，不依赖 Qt——GUI 卡死它照样运行；
     若卡死发生在 C++ 层（Qt/xcb/XWayland），dump 里主线程栈会停在事件处理的
   调用点上，同样是把「复现不了」变成「可定位」的关键证据。

2. **trace（运行面包屑）**：启动/窗口显示/心跳丢失/恢复/退出等节点追加到
   `logs/runtime-trace.log`，与 launcher.log（进程级退出码）互补：launcher 说
  「进程怎么没的」，本文件说「进程内到哪一步还活着」。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path

import config

#: 卡死判定阈值（秒）：GUI 线程超过该时长未打卡即视为事件循环卡死
DEFAULT_STALL_THRESHOLD = 10.0
#: 看护线程检查间隔（秒）
DEFAULT_CHECK_INTERVAL = 2.0
#: 持续卡死期间的最小补 dump 间隔（秒）
DEFAULT_DUMP_MIN_INTERVAL = 30.0


def _default_trace_path() -> Path:
    return config.LOG_DIR / "runtime-trace.log"


class StallWatchdog:
    """GUI 心跳看护：心跳丢失 → dump 全线程栈；恢复 → 重置。"""

    def __init__(
        self,
        stall_threshold: float = DEFAULT_STALL_THRESHOLD,
        check_interval: float = DEFAULT_CHECK_INTERVAL,
        dump_min_interval: float = DEFAULT_DUMP_MIN_INTERVAL,
        on_stall=None,
        trace_path: Path | None = None,
    ) -> None:
        self._stall_threshold = max(float(stall_threshold), 0.01)
        self._check_interval = max(float(check_interval), 0.01)
        self._dump_min_interval = max(float(dump_min_interval), 1.0)
        self._on_stall = on_stall
        self._trace_path = Path(trace_path) if trace_path else None
        self._context = ""
        self._lock = threading.Lock()
        self._last_beat = 0.0
        self._stalled = False
        self._last_dump = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 公开 API ----

    def set_context(self, note: str) -> None:
        """记录平台决策等上下文（dump 时一并落盘；由 main.py 启动时设置）。"""
        self._context = note or ""

    def beat(self) -> None:
        """GUI 线程心跳打卡（由 QTimer 每秒调用）。"""
        with self._lock:
            self._last_beat = time.monotonic()

    def start(self) -> None:
        """启动看护线程（幂等；已在运行则直接返回）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._last_beat = time.monotonic()
            self._stalled = False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._watch_loop, name="gui-watchdog", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """停止看护线程（幂等）。"""
        self._stop_event.set()

    def trace(self, message: str) -> None:
        """追加一条进程内面包屑（失败静默，绝不抛异常）。"""
        path = self._trace_path or _default_trace_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with self._lock, path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {message}\n")
        except Exception:  # noqa: BLE001 — 留痕失败不能影响主流程
            pass

    # ---- 内部实现 ----

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self._check_interval):
            with self._lock:
                last = self._last_beat
            if last <= 0:
                continue  # 尚未开始打卡
            stalled = time.monotonic() - last > self._stall_threshold
            if stalled and not self._stalled:
                self._stalled = True
                self.trace(
                    f"GUI 心跳丢失超过 {self._stall_threshold:.0f}s，"
                    "判定事件循环卡死，正在 dump 现场"
                )
            if stalled:
                self._dump_stall()
            elif self._stalled:
                self._stalled = False
                self.trace("GUI 心跳恢复")

    def _dump_stall(self) -> None:
        """卡死现场落盘：全线程栈 + 平台/GL 环境快照。"""
        now = time.monotonic()
        if now - self._last_dump < self._dump_min_interval:
            return
        self._last_dump = now
        if self._on_stall is not None:
            # 测试注入：不写文件，由调用方断言
            self._on_stall()
            return
        try:
            log_dir = config.LOG_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = log_dir / f"gui-stall-{stamp}.log"
            lines = [
                f"GUI 事件循环疑似卡死（>{self._stall_threshold:.0f}s 无心跳）",
                f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"版本：v{config.VERSION}",
                f"平台决策：{self._context or '（未记录）'}",
                f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')}",
                f"QT_XCB_GL_INTEGRATION={os.environ.get('QT_XCB_GL_INTEGRATION', '')}",
                f"LIBGL_ALWAYS_SOFTWARE={os.environ.get('LIBGL_ALWAYS_SOFTWARE', '')}",
                f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '')}",
                f"DISPLAY={os.environ.get('DISPLAY', '')}",
                f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}",
                "",
                "===== 全部线程栈（定位卡死点；主线程停在 Qt 调用处即事件循环被阻塞） =====",
            ]
            frames = sys._current_frames()
            for thread in threading.enumerate():
                frame = frames.get(thread.ident)
                lines.append(f"--- 线程 {thread.name} (ident={thread.ident}) ---")
                if frame is not None:
                    lines.extend(traceback.format_stack(frame))
                else:
                    lines.append("  （无栈信息）")
            path.write_text("\n".join(lines), encoding="utf-8")
            self.trace(f"卡死现场已写入：{path}")
        except Exception:  # noqa: BLE001 — dump 失败不能影响看护线程自身
            pass
