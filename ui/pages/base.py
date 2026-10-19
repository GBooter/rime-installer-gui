"""页面基类：标题 + 说明 + 信息卡片，统一 Phase 1 各页版式。"""

from __future__ import annotations

from PyQt6.QtCore import QSize, QThread
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, SimpleCardWidget, TitleLabel

from core import i18n
from core.detector import EnvReport
from core.state_machine import StateMachine

STATUS_COLORS_LIGHT = {"ok": "#3B6D11", "bad": "#A32D2D", "warn": "#854F0B", "muted": "#5F5E5A"}
STATUS_COLORS_DARK = {"ok": "#5FD35F", "bad": "#FF7A7A", "warn": "#E5B567", "muted": "#A8A6A0"}

#: 运行期色板：整体替换内容（非换引用），各页面 fill 时实时查表
STATUS_COLORS = dict(STATUS_COLORS_LIGHT)


def reap_worker(worker: QThread | None, timeout_ms: int = 5000) -> bool:
    """等后台 ``QThread`` 真正退出；返回 True 表示引用已可安全丢弃。

    v1.0.33 真机闪退根因修复：QThread 子类的「完成」信号是在 ``run()``
    **内部**发出的（``self.done.emit(...)`` 位于 run() 末尾），槽函数收到
    信号时线程往往还走在退出序列里——``isRunning()`` 仍为真。此刻丢掉
    最后一个 Python 引用，包装对象立即销毁 → ``~QThread()`` 撞上
    ``isRunning()`` → Qt 打「QThread: Destroyed while thread is still
    running」并 qFatal → SIGABRT（退出码 134）。

    真机实证（Ubuntu 24.04 / Linux Mint 22.3，v1.0.32）：launcher.log 里
    每次退出码 134 前都有该行 stderr；崩溃时刻与「任务结束 →
    redetect_async」「体检页 bind → AptHealthWorker」的完成时刻吻合——
    用户反馈的「点很多功能就闪退」，即每个任务结束/页面绑定都会触发一次
    该竞态（离线最小复现：同构代码 300 轮内必现 134 + 同一行 stderr）。

    ``wait()`` 期间 sip 会释放 GIL，不会与正在退出的工作线程抢解释器锁；
    完成信号本就发在 run() 末尾，正常路径只阻塞毫秒级。
    """
    if worker is None:
        return True
    if worker.isRunning():
        worker.wait(timeout_ms)
    return not worker.isRunning()


def apply_status_palette(mode: str) -> None:
    """按主题切换状态色（core.theme 调用；mode ∈ light/dark）。"""
    palette = STATUS_COLORS_DARK if mode == "dark" else STATUS_COLORS_LIGHT
    STATUS_COLORS.clear()
    STATUS_COLORS.update(palette)


class ProgressAreaMixin:
    """执行进度区公共行为（Phase 6）：等待态条纹 + 数值平滑动画。

    四个任务页（引擎/方案/备份/体检）原本各自重复同一套进度区代码，
    此处收敛为一处。页面保留 set_busy/set_step/set_status/点点动画等
    差异化部分，仅把进度三 widget 的创建与行为委托本类：

    - 任务开始 → `enter_waiting()`：显示 IndeterminateProgressBar 条纹，
      遮盖「已开始但还没解析到百分比」的空窗期；
    - 首个 >0 百分比 → 自动切到确定进度条并平滑补间；
    - 任务结束 → `reset_progress()`：停动画、归零、退出等待态。
    """

    def _init_progress_area(self, card_layout) -> None:
        from qfluentwidgets import BodyLabel, IndeterminateProgressBar, ProgressBar

        self.status_label = BodyLabel("")
        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        # 等待态条纹：高度仅 4px 的细条，贴在确定进度条同一槽位
        self.waiting_bar = IndeterminateProgressBar()
        self.waiting_bar.hide()
        self._waiting = False
        card_layout.addWidget(self.status_label)
        card_layout.addWidget(self.progress_bar)
        card_layout.addWidget(self.waiting_bar)

    def enter_waiting(self) -> None:
        """进入等待态：条纹可见、确定进度条归零隐藏。"""
        from ui.animation import stop_progress_animation

        self._waiting = True
        stop_progress_animation(self.progress_bar)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        self.waiting_bar.show()

    def set_progress(self, percent: int) -> None:
        """收到进度百分比；首个 >0 值负责从等待态切到确定态。"""
        from ui.animation import animate_progress

        percent = max(0, min(100, int(percent)))
        if percent > 0 and self._waiting:
            self._waiting = False
            self.waiting_bar.hide()
            self.progress_bar.show()
        animate_progress(self.progress_bar, percent)

    def reset_progress(self) -> None:
        """退出执行态：停动画、归零、离开等待态（显隐由 _show_progress_area 决定）。"""
        from ui.animation import stop_progress_animation

        self._waiting = False
        stop_progress_animation(self.progress_bar)
        self.progress_bar.setValue(0)

    def _show_progress_area(self, visible: bool) -> None:
        self.status_label.setVisible(visible)
        if visible:
            (self.waiting_bar if self._waiting else self.progress_bar).setVisible(True)
        else:
            self.progress_bar.setVisible(False)
            self.waiting_bar.setVisible(False)


class PageBase(QWidget):
    """所有子界面共用：大标题、副说明、一张信息卡片、底部弹性空间。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # objectName 即 addSubInterface 的 routeKey：构造时用「当前语言的标题」
        # 定下来后不再变动（切语言只改显示文本），中文下与改造前保持一致。
        # ⚠️ routeKey 必须全页面唯一：NavigationPanel.insertItem 对重复 key
        # 静默 return（不建导航项），页面将无法从侧栏到达——v1.0.5 曾因
        # 欢迎页与体检页标题同为「环境体检」导致体检页导航项被丢弃。
        self.setObjectName(i18n.tr(title).replace(" ", ""))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(10)

        # title/subtitle 存的是 i18n 键；未改造的字面量因 tr 的「缺键回退原串」
        # 行为而天然兼容，因此可以逐个页面渐进迁移。
        self._title_src = title
        self._subtitle_src = subtitle
        self.title_label = TitleLabel(i18n.tr(title))
        outer.addWidget(self.title_label)
        self.subtitle_label = None
        if subtitle:
            self.subtitle_label = CaptionLabel(i18n.tr(subtitle))
            outer.addWidget(self.subtitle_label)

        self.card = SimpleCardWidget()
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(20, 16, 20, 16)
        self.card_layout.setSpacing(10)
        outer.addWidget(self.card)
        outer.addStretch(1)

    def sizeHint(self) -> QSize:
        # v1.0.7：所有页面统一横向 sizeHint——此前滚动区内容（26 张方案卡 /
        # 体检多行）会把页面 sizeHint 撑高，窗口被协商成竖向长条（真机
        # 1080×1030）。固定 hint 后窗口尺寸只由 resize/用户拖拽决定。
        return QSize(1160, 780)

    def retranslate(self) -> None:
        """切换语言后刷新本页文案（子类重写以补充按钮等动态文本）。"""
        self.title_label.setText(i18n.tr(self._title_src))
        if self.subtitle_label is not None:
            self.subtitle_label.setText(i18n.tr(self._subtitle_src))

    def add_row(self, key: str, value: str, status: str | None = None) -> QHBoxLayout:
        row = QHBoxLayout()
        from qfluentwidgets import BodyLabel

        row.addWidget(BodyLabel(key))
        row.addStretch(1)
        label = BodyLabel(value or "—")
        if status in STATUS_COLORS:
            label.setStyleSheet(f"color: {STATUS_COLORS[status]};")
        row.addWidget(label)
        self.card_layout.addLayout(row)
        return row

    def add_expanding_scroll(self, scroll_area) -> None:
        """把滚动区插到信息卡之后并独享剩余高度（v1.0.5 布局修复）。

        背景：PageBase 构造时在末尾放了 addStretch(1)；页面若用
        `outer.insertWidget(outer.count() - 1, scroll, 1)` 插入滚动区，
        两者按 1:1 平分剩余空间——滚动区只拿到约半页高，内容（如方案
        市场 26 张卡）被压成可见 2 张、下方大片空白（真机反馈「区域
        太小，下面的部分都是空白」）。此处插入后把末尾弹性占位的
        stretch 降为 0，滚动区独享剩余空间。
        """
        outer = self.layout()
        outer.insertWidget(outer.count() - 1, scroll_area, 1)
        outer.setStretch(outer.count() - 1, 0)

    def bind(self, report: EnvReport, sm: StateMachine) -> None:
        """环境重新检测后由主窗口调用，子类按需覆写以刷新内容。"""
