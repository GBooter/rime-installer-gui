"""通用任务执行器：QProcess 驱动多步骤命令，步骤化进度 + 动画 + 日志 tee + 可取消。

设计要点：
- 步骤带权重，总进度 = 已完成权重 + 当前步骤内进度（有百分比解析则真实解析，
  否则定时器蠕进到 92% 保持动画感，步骤完成时补齐）；
- stdout/stderr 合并捕获（apt 的下载进度走 stderr），逐行 tee 到日志回调；
- 取消时 kill 进程树根进程；命令统一经 `bash -c exec ...` 包装，
  保证 pkexec 提权的 apt 进程被 kill 时能真正终止（exec 替换避免孤儿进程）。
"""

from __future__ import annotations

import re
import shlex
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

# apt / aria2 等输出的百分比，如 "(50%)" 或 "[#1a2b 1.2MiB/10MiB(12%)]"
PERCENT_RE = r"\((\d{1,3})%\)"


class Step:
    """一个可执行步骤：标题 + 命令 + 权重 + 可选进度解析正则。

    hint_key（v1.0.20）：失败时给用户看的人话建议的 i18n 键（约定同名 `_hint`
    键为正文）。只写键不写文案，是为了让 core 层不依赖语言偏好。
    """

    def __init__(
        self,
        title: str,
        argv: Sequence[str],
        weight: float = 1.0,
        progress_re: str | None = None,
        work_dir: str | None = None,
        hint_key: str = "",
    ) -> None:
        self.title = title
        self.argv = [str(a) for a in argv]
        self.weight = max(float(weight), 0.01)
        self.progress_re = re.compile(progress_re) if progress_re else None
        self.work_dir = work_dir
        self.hint_key = hint_key

    def __repr__(self) -> str:  # 调试友好
        return f"Step({self.title!r}, argv={self.argv!r})"


def shell_wrap(argv: Sequence[str]) -> list[str]:
    """bash -c 'exec ...' 包装：进程被 exec 替换，kill 时不会留下孤儿子进程。"""
    import shutil

    bash = shutil.which("bash") or "/bin/bash"
    return [bash, "-c", "exec " + shlex.join([str(a) for a in argv])]


class TaskRunner(QObject):
    """顺序执行 Step 列表；所有输出经 log_write 回调 tee 落盘。"""

    output = pyqtSignal(str)  # 一行合并后的输出
    step_started = pyqtSignal(int, str)  # 步骤序号（0 起）、标题
    progress = pyqtSignal(int)  # 0-100 总进度
    status = pyqtSignal(str)  # 当前状态文字（步骤标题）
    finished = pyqtSignal(bool, int)  # 成功标志、退出码（取消时为 -1）
    cancelled = pyqtSignal()

    _TICK_MS = 120
    _CREEP_TARGET = 0.92  # 无解析器时单步蠕进上限
    _CREEP_STEP = 0.004  # 每个 tick 的蠕进量
    #: 长步骤持续运行日志间隔（秒，v1.0.4）：pkexec/apt 挂起时进度条不动、
    #: 日志页无输出，用户无法区分「慢」与「死」；周期性记一笔 elapsed 即可判断
    _SLOW_LOG_INTERVAL_MS = 60_000

    def __init__(
        self,
        log_write: Callable[[str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._log_write = log_write
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        self._steps: list[Step] = []
        self._index = -1
        self._base_pct = 0.0  # 当前步骤起始百分比
        self._span_pct = 0.0  # 当前步骤百分比跨度
        self._step_pct = 0.0  # 当前步骤内进度 0~1
        self._buf = b""
        self._cancelled = False
        self._started_at = 0.0
        self._failed_step: Step | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._creep)

        # 长步骤持续运行日志（v1.0.4）：与蠕进定时器独立，有/无进度解析都生效
        self._slow_timer = QTimer(self)
        self._slow_timer.setInterval(self._SLOW_LOG_INTERVAL_MS)
        self._slow_timer.timeout.connect(self._log_still_running)
        self._step_started_at = 0.0

    # ---- 公开 API ----

    @property
    def running(self) -> bool:
        return self._index >= 0 and self._proc.state() != QProcess.ProcessState.NotRunning

    @property
    def current_step(self) -> Step | None:
        if 0 <= self._index < len(self._steps):
            return self._steps[self._index]
        return None

    @property
    def failed_step(self) -> Step | None:
        """最近一次失败的任务里出错的那一步（成功后清空，v1.0.20）。"""
        return self._failed_step

    def run_steps(self, steps: Iterable[Step]) -> None:
        """开始执行步骤序列；运行中调用将被忽略。"""
        if self.running:
            return
        self._steps = list(steps)
        if not self._steps:
            self.finished.emit(True, 0)
            return
        self._cancelled = False
        self._failed_step = None
        self._started_at = time.monotonic()
        total = sum(s.weight for s in self._steps)
        self._emit_log(f"===== 任务开始：{len(self._steps)} 步，权重 {total:.2f} =====")
        self._start_step(0)

    def cancel(self) -> None:
        if not self.running:
            return
        self._cancelled = True
        self._emit_log("用户请求取消，正在终止进程…")
        self._proc.kill()

    # ---- 内部实现 ----

    def _start_step(self, index: int) -> None:
        self._index = index
        step = self._steps[index]
        total = sum(s.weight for s in self._steps)
        self._base_pct = sum(s.weight for s in self._steps[:index]) / total * 100.0
        self._span_pct = step.weight / total * 100.0
        self._step_pct = 0.0
        self._buf = b""

        self.step_started.emit(index, step.title)
        self.status.emit(step.title)
        self._set_progress(self._base_pct)
        self._emit_log(f"--- [{index + 1}/{len(self._steps)}] {step.title} ---")
        self._emit_log("$ " + " ".join(step.argv))

        work_dir = step.work_dir or str(Path.home())
        try:
            self._proc.setWorkingDirectory(work_dir)
        except OSError:
            pass
        self._step_started_at = time.monotonic()
        self._proc.start(step.argv[0], step.argv[1:])
        self._slow_timer.start()
        if step.progress_re is None:
            self._timer.start()
        else:
            self._timer.stop()

    def _creep(self) -> None:
        if self._step_pct < self._CREEP_TARGET:
            self._step_pct = min(self._CREEP_TARGET, self._step_pct + self._CREEP_STEP)
            self._set_progress(self._base_pct + self._span_pct * self._step_pct)

    def _log_still_running(self) -> None:
        """长步骤周期性留痕（v1.0.4）：区分「慢」与「挂起」的关键证据。"""
        step = self.current_step
        if step is None:
            return
        elapsed = time.monotonic() - self._step_started_at
        self._emit_log(
            f"… 步骤「{step.title}」已执行 {elapsed:.0f}s 仍在运行"
            "（进程未退出、无新输出；如怀疑挂起可点「取消任务」）"
        )

    def _set_progress(self, pct: float) -> None:
        self.progress.emit(max(0, min(100, int(round(pct)))))

    def _on_output(self) -> None:
        self._buf += bytes(self._proc.readAllStandardOutput())
        while b"\n" in self._buf:
            raw, _, self._buf = self._buf.partition(b"\n")
            self._handle_line(raw.decode("utf-8", "replace").rstrip("\r"))

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        self._emit_log(line)
        self.output.emit(line)
        step = self.current_step
        if step and step.progress_re:
            match = step.progress_re.search(line)
            if match:
                try:
                    value = float(match.group(1))
                except (TypeError, ValueError):
                    return
                self._step_pct = max(self._step_pct, min(value / 100.0, 1.0))
                self._set_progress(self._base_pct + self._span_pct * self._step_pct)

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._timer.stop()
        self._slow_timer.stop()
        if self._buf:  # flush 残留半行
            line = self._buf.decode("utf-8", "replace").rstrip("\r")
            self._buf = b""
            if line:
                self._handle_line(line)

        if self._cancelled:
            self._index = -1
            self._emit_log(f"!! 任务已取消（退出码 {code}）")
            self.cancelled.emit()
            self.finished.emit(False, -1)
            return

        step = self.current_step
        if code != 0 and step is not None:
            self._index = -1
            self._failed_step = step
            self._emit_log(f"!! 步骤失败：{step.title}（退出码 {code}）")
            self.finished.emit(False, code)
            return

        if step is not None:
            self._step_pct = 1.0
            self._set_progress(self._base_pct + self._span_pct)
            nxt = self._index + 1
            if nxt < len(self._steps):
                self._start_step(nxt)
                return

        elapsed = time.monotonic() - self._started_at
        self._index = -1
        self._emit_log(f"===== 任务完成，用时 {elapsed:.1f}s =====")
        self.finished.emit(True, 0)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self._emit_log(f"!! 进程错误：{error}")

    def _emit_log(self, line: str) -> None:
        if self._log_write is None:
            return
        try:
            self._log_write(line)
        except Exception:  # noqa: BLE001 — 日志写失败绝不能中断任务
            pass
