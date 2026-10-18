"""共享动效工具（Phase 6 / v1.0.0）。

- `animate_progress`：进度数值平滑过渡。runner 的百分比来自输出解析，
  相邻两次可能 0 → 45 → 100 跳变，直接 setValue 显得生硬；
  用 QPropertyAnimation 补间，同一 bar 上的旧动画立即让位（打断重来）。
- `stop_progress_animation`：任务结束/复位的收尾，避免残留动画与直接
  setValue 打架。
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation

#: 数值过渡时长（ms）：短到不拖沓，长到能看出补间
PROGRESS_DURATION_MS = 220


def animate_progress(bar, target: int, duration: int = PROGRESS_DURATION_MS) -> None:
    """把进度条数值平滑过渡到 target（0-100 钳制；已到位则不动）。"""
    target = max(0, min(100, int(target)))
    if bar.value() == target:
        return
    stop_progress_animation(bar)
    # 不设 parent、不设 DeleteWhenStopped：动画自然结束后 C++ 侧对象若被删除，
    # 再对它 stop() 会抛 RuntimeError。交由 bar._value_ani 的 Python 引用持有，
    # 引用置空即回收；stop() 始终作用于存活对象。
    animation = QPropertyAnimation(bar, b"value")
    animation.setDuration(duration)
    animation.setStartValue(bar.value())
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    bar._value_ani = animation  # noqa: SLF001 — 持有引用防 GC，兼作打断凭据
    animation.start()


def stop_progress_animation(bar) -> None:
    """停止该 bar 上正在运行的数值动画（无则无操作）。"""
    animation = getattr(bar, "_value_ani", None)
    if animation is not None:
        animation.stop()
        bar._value_ani = None
