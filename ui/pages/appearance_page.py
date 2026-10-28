"""外观页（v1.0.13，v1.0.19 重做主题展示）：Linux 上真实可见的候选窗外观控制。

rime 配色在 Linux 不被前端渲染（源码级核查结论，见 core/appearance.py
模块 docstring），本页提供三条真实可见的路径：

- **候选词数量（v1.0.41）**：rime 引擎的 ``menu/page_size``——每页几个候选，
  由引擎决定、fcitx5 / ibus 通用，改完重新部署即生效；
- fcitx5 用户：classicui 主题切换（v1.0.19 起分「内置主题 / 我的主题」
  两个标签页的卡片网格，卡片里直接画出该主题的候选窗）+ 打开主题目录；
- ibus 用户：ibus_rime.custom.yaml 全局外观项（横排候选 / 内嵌预编辑 /
  preedit 高亮风格，注销重登生效）。

v1.0.14 的「可安装主题」市场（上游仓库下载）已于 v1.0.16 移除：
链路过重（下载→勾选→记账→卸载）且与「枚举+切换」价值重叠，换主题
收敛为「打开主题目录放文件夹 → 回本页点切换」一条路。

v1.0.19 的两处取舍：
1. 系统自带主题（default / default-dark / deepin-*）不再列出——它们与
   「挑一套好看的候选窗」无关，只是噪声；要回出厂态有「恢复系统默认」。
2. 主题行换成卡片网格 + 候选窗预览（ui/theme_swatch.CandidatePreview），
   内置来源在页内署名上游作者（BSD-2-Clause，见 resources/bundled_themes/LICENSE）。

本页只负责展示与意图发射，执行由主窗口编排（appearance_apply.py CLI）。
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    IconWidget,
    LineEdit,
    MessageBox,
    PillPushButton,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    SimpleCardWidget,
    StrongBodyLabel,
    SwitchButton,
    TransparentToolButton,
    isDarkTheme,
    setFont,
    themeColor,
)

from core import appearance, candidate_count, i18n, schema_switch
from core.detector import EnvReport
from core.schemes import parse_spec
from core.state_machine import StateMachine
from ui.pages.base import STATUS_COLORS, PageBase, ProgressAreaMixin
from ui.theme_swatch import CandidatePreview, current_tag_style, tag_style

#: 主题卡尺寸（预览 118 + 页脚），卡片网格按宽度自动 2~4 列
_CARD_HEIGHT = 196
#: 每列最小宽度（含间距）——决定网格列数
_COLUMN_WIDTH = 340

#: 选中档位的含义（v1.0.42：下拉换刻度后，解释放在控件下方一行动态文案里）
_PAGE_SIZE_LEVELS = {
    5: "appearance.page_size_level_default",
    7: "appearance.page_size_level_recommend",
    9: "appearance.page_size_level_max",
}

#: 刻度上带悬停解释的档位（5 默认 / 7 推荐 / 9 上限，9 顺便交代 0 键的取舍）
_PAGE_SIZE_TIPS = {
    5: "appearance.page_size_tip_default",
    7: "appearance.page_size_tip_recommend",
    9: "appearance.page_size_tip_max",
}


class _ElidedLabel(BodyLabel):
    """单行文本按可用宽度省略（v1.0.24）。

    主题名与 key 的长度完全由上游决定（`非常非常长的中文主题名称测试样例`、
    `catppuccin-mocha-blue-extra` 都是真实存在的形态）。QLabel 的默认行为是
    「按文本撑宽」——卡片被撑到 438px，网格从两列挤成一列、右边留一大片
    空白。这里把水平方向的 sizePolicy 设成 Ignored 让布局自由压缩，再自己
    按实际宽度做 ElideRight，全文放进 tooltip。
    """

    def __init__(self, text: str, parent: QWidget | None = None):
        # qfluentwidgets 的 BodyLabel 用 @overload 分派构造签名，子类再传
        # (text, parent) 会命中不到重载（TypeError）——先建空标签再 setText
        super().__init__(parent)
        self._full_text = text
        # 水平方向用 Preferred + minimumSizeHint().width()=0：空间够时按全文
        # 排版，不够时允许布局把它压窄（Ignored 会让布局直接给 0 宽，名字消失）
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        super().setText(text)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(0)  # 允许被压到任意窄（否则布局仍按全文宽度要空间）
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = self.width()
        if width <= 0:
            super().setText(self._full_text)
            return
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, width
            )
        )


class _ThemeCard(QWidget):
    """一张主题卡：候选窗预览 + 名称/深浅 + key + 切换/删除操作（v1.0.19）。

    整张卡片都是点击热区（点卡即切换），右侧按钮是显式入口；「当前使用」
    的卡片改用强调徽标、不再提供切换按钮，避免误点无反应的观感。
    自绘背景而非 QSS：需要按明暗主题取不同色，且当前态要描强调色边。

    v1.0.24：用户自装主题多一个「删除」图标按钮（内置主题不提供——它们
    随安装包发布，删掉下次启动又会回来，只会让用户白忙）。
    """

    switch_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        theme: appearance.ThemeInfo,
        parent: QWidget | None = None,
        deletable: bool = False,
    ):
        super().__init__(parent)
        self.theme = theme
        self._deletable = bool(deletable)
        self._hover = False
        self._current = False
        self._interactive = True
        self.setFixedHeight(_CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self.preview = CandidatePreview(theme.palette, theme.path, self)
        outer.addWidget(self.preview)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        footer.setSpacing(8)

        text_column = QVBoxLayout()
        text_column.setSpacing(1)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.name_label = _ElidedLabel(theme.name, self)
        setFont(self.name_label, 14, QFont.Weight.DemiBold)
        head.addWidget(self.name_label)
        # 深浅标签：仅当可判定（有 theme.conf 底色 / 内置主题权威值）
        self.tone_tag: CaptionLabel | None = None
        if theme.dark is not None:
            self.tone_tag = CaptionLabel(
                i18n.tr("appearance.tone_dark" if theme.dark else "appearance.tone_light"),
                self,
            )
            self.tone_tag.setStyleSheet(tag_style())
            head.addWidget(self.tone_tag)
        head.addStretch(1)
        text_column.addLayout(head)
        self.key_label = CaptionLabel(theme.key, self)
        text_column.addWidget(self.key_label)
        footer.addLayout(text_column, 1)

        # 删除：只给用户自装主题（内置主题随包发布，删了也会回来）。
        # 用无边框图标按钮：破坏性操作要能被发现，但不该和「切换」抢视线。
        self.delete_button = TransparentToolButton(self)
        self.delete_button.setIcon(FluentIcon.DELETE)
        self.delete_button.setIconSize(QSize(15, 15))
        self.delete_button.setFixedSize(30, 30)
        self.delete_button.setToolTip(i18n.tr("appearance.theme_delete_tip"))
        self.delete_button.clicked.connect(self._emit_delete)
        self.delete_button.setVisible(self._deletable)
        footer.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.switch_button = PushButton(
            FluentIcon.PALETTE, i18n.tr("appearance.theme_switch"), self
        )
        self.switch_button.setFixedSize(92, 30)
        self.switch_button.clicked.connect(self._emit_switch)
        footer.addWidget(self.switch_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.current_tag = CaptionLabel(i18n.tr("appearance.card_current"), self)
        self.current_tag.setStyleSheet(current_tag_style())
        self.current_tag.setFixedHeight(24)
        self.current_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_tag.setFixedWidth(76)
        self.current_tag.hide()
        footer.addWidget(self.current_tag, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(footer)
        self.setToolTip(i18n.tr("appearance.theme_switch_tip"))

    # ---- 交互 ----

    def _emit_switch(self) -> None:
        if not self._current and self._interactive:
            self.switch_requested.emit(self.theme.key)

    def _emit_delete(self) -> None:
        """删除意图：当前主题**允许**删除（最常见需求就是「删掉正在用的这款」），
        由页面确认框说明「会切回 default 主题」，而不是把按钮藏起来。"""
        if self._interactive and self._deletable:
            self.delete_requested.emit(self.theme.key)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_switch()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    # ---- 状态 ----

    def set_current(self, current: bool, enabled: bool) -> None:
        self._current = current
        self._interactive = enabled
        self.setCursor(
            Qt.CursorShape.ArrowCursor if current or not enabled
            else Qt.CursorShape.PointingHandCursor
        )
        self.switch_button.setVisible(not current)
        self.current_tag.setVisible(current)
        # 删除按钮不随「当前」隐藏：删掉正在用的那款是最常见的清理动作。
        # 但 busy / 状态机禁用时必须一起禁用，否则会在任务中途改目录。
        self.delete_button.setEnabled(enabled and self._deletable)
        # 当前态同时禁用按钮：按钮虽已隐藏，但保持 enabled 会让「点卡片 vs 点
        # 按钮」的可用性语义不一致（整卡点击与按钮走同一个 _emit_switch 守卫，
        # 这里再禁一次是纵深防御，也便于冒烟断言当前态确实不可操作）
        self.switch_button.setEnabled(enabled and not current)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        dark = isDarkTheme()
        if self._current:
            background = QColor(0, 120, 212, 26) if not dark else QColor(96, 205, 255, 30)
            border = QColor(0, 120, 212, 170) if not dark else QColor(96, 205, 255, 170)
        elif dark:
            background = QColor(255, 255, 255, 20 if self._hover else 10)
            border = QColor(255, 255, 255, 66 if self._hover else 32)
        else:
            background = QColor(255, 255, 255, 255)
            border = QColor(0, 0, 0, 46 if self._hover else 20)
        painter.setBrush(background)
        painter.setPen(QPen(border, 2 if self._current else 1))
        painter.drawRoundedRect(rect, 10, 10)


class _CardGrid(QWidget):
    """主题卡网格：按可用宽度自动分列（2~4 列），窗口拉宽自动铺满。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        self._cards: list[_ThemeCard] = []
        self._columns = 0

    def set_cards(self, cards: list[_ThemeCard]) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards = list(cards)
        for card in self._cards:
            card.setParent(self)
        self._columns = 0
        self._relayout()

    def _relayout(self) -> None:
        width = self.width() or 720
        columns = max(2, min(4, int((width + 12) // _COLUMN_WIDTH)))
        if columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()


class _EmptyState(QWidget):
    """空状态：虚线框 + 标题 + 一句可操作的提示。"""

    def __init__(self, title: str, hint: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(148)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(6)
        layout.addStretch(1)
        self.title_label = BodyLabel(title, self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        setFont(self.title_label, 14, QFont.Weight.DemiBold)
        layout.addWidget(self.title_label)
        self.hint_label = CaptionLabel(hint, self)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(0, 0, 0, 46) if not isDarkTheme() else QColor(255, 255, 255, 46), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


class AppearancePage(ProgressAreaMixin, PageBase):
    theme_switch_requested = pyqtSignal(str)  # theme key
    theme_delete_requested = pyqtSignal(str)  # theme key（v1.0.24：删除用户主题）
    theme_revert_requested = pyqtSignal()
    theme_import_requested = pyqtSignal(str)  # GitHub 仓库链接 / user/repo（v1.0.21）
    open_themes_dir_requested = pyqtSignal()
    link_requested = pyqtSignal(str)  # 外部链接（鸣谢区）
    ibus_style_requested = pyqtSignal(bool, bool, str)  # horizontal, inline_preedit, color_scheme
    ibus_revert_requested = pyqtSignal()
    page_size_requested = pyqtSignal(int)  # v1.0.41：每页候选词数量（menu/page_size）
    page_size_revert_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    log_requested = pyqtSignal()

    def __init__(self, report: EnvReport, sm: StateMachine, parent=None):
        super().__init__("appearance.title", "appearance.subtitle", parent)
        self._report = report
        self._sm = sm
        self._builtin_themes: list[appearance.ThemeInfo] = []
        self._custom_themes: list[appearance.ThemeInfo] = []
        self._cards: dict[str, _ThemeCard] = {}
        self._current_theme = ""
        self._backup_exists = False
        self._ibus_style: appearance.IbusStyle | None = None
        #: v1.0.41：当前生效的候选词数量 + 本安装器是否写过该配置
        self._page_size = candidate_count.PAGE_SIZE_DEFAULT
        self._page_size_block = False
        self._busy = False

        self.frontend_row = self.add_row(i18n.tr("appearance.frontend_row"), "")
        self.theme_row = self.add_row(i18n.tr("appearance.theme_row"), "")
        self.ibus_row = self.add_row(i18n.tr("appearance.ibus_row"), "")
        self.page_size_row = self.add_row(i18n.tr("appearance.page_size_row"), "")

        # 执行进度区（与方案页同款：等待态条纹 + 数值平滑动画）
        self._init_progress_area(self.card_layout)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.cancel_button = PushButton(FluentIcon.CANCEL, i18n.tr("common.cancel_task"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.log_button = PushButton(FluentIcon.HISTORY, i18n.tr("common.view_log"))
        self.log_button.clicked.connect(self.log_requested.emit)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.log_button)
        action_row.addStretch(1)
        self.card_layout.addLayout(action_row)

        # 滚动区：fcitx5 主题分区 + ibus 外观分区
        self._sections_host = QWidget()
        sections = QVBoxLayout(self._sections_host)
        sections.setContentsMargins(2, 2, 2, 2)
        sections.setSpacing(8)

        self.fcitx5_label = StrongBodyLabel(i18n.tr("appearance.section_fcitx5"))
        self.themes_card = SimpleCardWidget()
        themes_layout = QVBoxLayout(self.themes_card)
        themes_layout.setContentsMargins(10, 10, 10, 10)
        themes_layout.setSpacing(10)

        # 标签页：内置主题 / 我的主题（v1.0.19 拆开，避免几十张卡混在一起）
        tab_row = QHBoxLayout()
        tab_row.setSpacing(10)
        self.tabs = SegmentedWidget(self.themes_card)
        self.tabs.addItem("builtin", i18n.tr("appearance.tab_builtin"))
        self.tabs.addItem("custom", i18n.tr("appearance.tab_custom"))
        self.tabs.setCurrentItem("builtin")
        self.tabs.currentItemChanged.connect(self._on_tab_changed)
        tab_row.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        tab_row.addStretch(1)
        themes_layout.addLayout(tab_row)

        # 内置主题页：卡片网格 + 系统主题提示 + 鸣谢
        self.builtin_host = QWidget(self.themes_card)
        builtin_layout = QVBoxLayout(self.builtin_host)
        builtin_layout.setContentsMargins(0, 0, 0, 0)
        builtin_layout.setSpacing(10)
        self.builtin_grid = _CardGrid(self.builtin_host)
        builtin_layout.addWidget(self.builtin_grid)
        self.system_note = CaptionLabel("", self.builtin_host)
        self.system_note.setWordWrap(True)
        self.system_note.hide()
        builtin_layout.addWidget(self.system_note)
        builtin_layout.addWidget(self._build_thanks_card())
        themes_layout.addWidget(self.builtin_host)

        # 我的主题页：从链接导入（v1.0.21）+ 卡片网格 + 空状态
        self.custom_host = QWidget(self.themes_card)
        custom_layout = QVBoxLayout(self.custom_host)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(10)

        import_row = QHBoxLayout()
        import_row.setSpacing(8)
        self.import_input = LineEdit(self.custom_host)
        self.import_input.setPlaceholderText(i18n.tr("appearance.import_placeholder"))
        self.import_input.setClearButtonEnabled(True)
        self.import_input.textChanged.connect(self._sync_import_button)
        self.import_input.returnPressed.connect(self._emit_import)
        import_row.addWidget(self.import_input, 1)
        self.import_button = PushButton(
            FluentIcon.CLOUD_DOWNLOAD, i18n.tr("appearance.import_button")
        )
        self.import_button.setToolTip(i18n.tr("appearance.import_tip"))
        self.import_button.clicked.connect(self._emit_import)
        import_row.addWidget(self.import_button)
        custom_layout.addLayout(import_row)
        self.import_hint = CaptionLabel(i18n.tr("appearance.import_note"), self.custom_host)
        self.import_hint.setWordWrap(True)
        custom_layout.addWidget(self.import_hint)

        self.custom_grid = _CardGrid(self.custom_host)
        custom_layout.addWidget(self.custom_grid)
        self.custom_empty = _EmptyState(
            i18n.tr("appearance.custom_empty_title"),
            i18n.tr("appearance.custom_empty_hint"),
            self.custom_host,
        )
        custom_layout.addWidget(self.custom_empty)
        self.custom_hint = CaptionLabel(i18n.tr("appearance.custom_hint"), self.custom_host)
        self.custom_hint.setWordWrap(True)
        custom_layout.addWidget(self.custom_hint)
        self.custom_host.hide()
        themes_layout.addWidget(self.custom_host)

        # 操作行：恢复默认 / 打开主题目录 / 刷新
        themes_buttons = QHBoxLayout()
        themes_buttons.setSpacing(10)
        self.theme_revert_button = PushButton(
            FluentIcon.CANCEL, i18n.tr("appearance.theme_revert")
        )
        self.theme_revert_button.setToolTip(i18n.tr("appearance.theme_revert_tip"))
        self.theme_revert_button.setEnabled(False)
        self.theme_revert_button.clicked.connect(self.theme_revert_requested.emit)
        themes_buttons.addWidget(self.theme_revert_button)
        # 打开主题目录：自制主题的落地入口（v1.0.16 市场移除后的替代路径）
        self.open_dir_button = PushButton(
            FluentIcon.FOLDER, i18n.tr("appearance.open_themes_dir")
        )
        self.open_dir_button.setToolTip(i18n.tr("appearance.open_themes_dir_tip"))
        self.open_dir_button.clicked.connect(self.open_themes_dir_requested.emit)
        themes_buttons.addWidget(self.open_dir_button)
        # 刷新：重新扫描主题目录，发现刚放入的新主题（无需重启）
        self.refresh_button = PushButton(
            FluentIcon.SYNC, i18n.tr("appearance.refresh_themes")
        )
        self.refresh_button.setToolTip(i18n.tr("appearance.refresh_themes_tip"))
        self.refresh_button.clicked.connect(self._refresh)
        themes_buttons.addWidget(self.refresh_button)
        themes_buttons.addStretch(1)
        themes_layout.addLayout(themes_buttons)
        self.fcitx5_note = CaptionLabel(i18n.tr("appearance.theme_note"))
        self.fcitx5_note.setWordWrap(True)

        self.ibus_label = StrongBodyLabel(i18n.tr("appearance.section_ibus"))
        self.ibus_card = SimpleCardWidget()
        ibus_layout = QVBoxLayout(self.ibus_card)
        ibus_layout.setContentsMargins(16, 12, 16, 12)
        ibus_layout.setSpacing(10)

        self.horizontal_switch = SwitchButton()
        self.horizontal_switch.setToolTip(i18n.tr("appearance.ibus_horizontal_tip"))
        self.horizontal_label = BodyLabel(i18n.tr("appearance.ibus_horizontal"))
        self.inline_switch = SwitchButton()
        self.inline_switch.setToolTip(i18n.tr("appearance.ibus_inline_preedit_tip"))
        self.inline_label = BodyLabel(i18n.tr("appearance.ibus_inline_preedit"))
        ibus_layout.addLayout(self._switch_row(self.horizontal_switch, self.horizontal_label))
        ibus_layout.addLayout(self._switch_row(self.inline_switch, self.inline_label))

        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        self.preedit_color_label = BodyLabel(i18n.tr("appearance.ibus_preedit_color"))
        color_row.addWidget(self.preedit_color_label)
        self.preedit_combo = ComboBox()
        self.preedit_combo.setToolTip(i18n.tr("appearance.ibus_preedit_color_tip"))
        self.preedit_combo.setFixedWidth(200)
        color_row.addWidget(self.preedit_combo)
        color_row.addStretch(1)
        ibus_layout.addLayout(color_row)

        ibus_buttons = QHBoxLayout()
        ibus_buttons.setSpacing(10)
        self.ibus_apply_button = PrimaryPushButton(
            FluentIcon.DOWNLOAD, i18n.tr("appearance.ibus_apply")
        )
        self.ibus_apply_button.setToolTip(i18n.tr("appearance.ibus_apply_tip"))
        self.ibus_apply_button.clicked.connect(self._emit_ibus_style)
        self.ibus_revert_button = PushButton(
            FluentIcon.CANCEL, i18n.tr("appearance.ibus_revert")
        )
        self.ibus_revert_button.setToolTip(i18n.tr("appearance.ibus_revert_tip"))
        self.ibus_revert_button.clicked.connect(self.ibus_revert_requested.emit)
        ibus_buttons.addWidget(self.ibus_apply_button)
        ibus_buttons.addWidget(self.ibus_revert_button)
        ibus_buttons.addStretch(1)
        ibus_layout.addLayout(ibus_buttons)
        self.ibus_note = CaptionLabel(i18n.tr("appearance.ibus_note"))
        self.ibus_note.setWordWrap(True)
        ibus_layout.addWidget(self.ibus_note)

        # ---- v1.0.41：候选词数量（引擎侧，fcitx5 / ibus 通用） ----
        # 与下面的主题分区是两件事：主题决定「长什么样」，这里决定「一页几个」。
        # 值域 1–9（10 个要靠 0 键选字，各方案的前端行为不一致，故不开放）。
        #
        # v1.0.42 重做：下拉换成 1–9 刻度（值域一眼可见，省掉「最多能设几」的
        # 疑问，也少一次展开）；「恢复默认」移到标题行右侧（次要操作不跟控件抢
        # 位置）；说明与刻度同一行，卡片高度由内容决定——不再用一块大预览把
        # 卡片撑高、再在左下方留一片空白。
        self.engine_label = StrongBodyLabel(i18n.tr("appearance.section_candidates"))
        self.page_size_card = SimpleCardWidget()
        size_layout = QVBoxLayout(self.page_size_card)
        size_layout.setContentsMargins(18, 14, 18, 14)
        size_layout.setSpacing(8)

        # 控件行：标题 + 1–9 刻度 + 恢复默认，全部挤在一行（30px），
        # 说明文案另起一行——卡片高度只剩「一行控件 + 两行说明」。
        size_head = QHBoxLayout()
        size_head.setSpacing(16)
        self.page_size_label = BodyLabel(i18n.tr("appearance.page_size_title"))
        size_head.addWidget(self.page_size_label)

        # 1–9 刻度：每格一个胶囊按钮，选中态是实心强调色（PillPushButton 自带）
        self.page_size_buttons: dict[int, PillPushButton] = {}
        #: 每格的基础提示（禁用时临时覆盖成原因，恢复时按此还原）
        self._page_size_tips: dict[int, str] = {}
        self.page_size_group = QButtonGroup(self.page_size_card)
        self.page_size_group.setExclusive(True)
        # idClicked 只在用户点击时发出——程序化 setChecked 不会误触发部署
        self.page_size_group.idClicked.connect(self._on_page_size_clicked)
        selector = QWidget(self.page_size_card)
        selector_row = QHBoxLayout(selector)
        selector_row.setContentsMargins(0, 0, 0, 0)
        selector_row.setSpacing(8)
        for value in range(candidate_count.PAGE_SIZE_MIN, candidate_count.PAGE_SIZE_MAX + 1):
            button = PillPushButton(str(value), selector)
            button.setFixedHeight(30)
            button.setMinimumWidth(30)
            button.setMaximumWidth(40)
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            tip_key = _PAGE_SIZE_TIPS.get(value)
            tip_text = i18n.tr(tip_key) if tip_key else ""
            self._page_size_tips[value] = tip_text
            button.setToolTip(tip_text)
            self.page_size_group.addButton(button, value)
            self.page_size_buttons[value] = button
            selector_row.addWidget(button)
        selector_row.addStretch(1)

        size_head.addWidget(selector)
        size_head.addStretch(1)
        self.page_size_revert_button = PushButton(
            FluentIcon.SYNC, i18n.tr("appearance.page_size_revert")
        )
        self.page_size_revert_button.clicked.connect(self.page_size_revert_requested.emit)
        size_head.addWidget(self.page_size_revert_button)
        size_layout.addLayout(size_head)

        # 当前档位的含义（跟随点击变化）：回答「我该选几」
        self.page_size_hint = CaptionLabel(
            i18n.tr(
                _PAGE_SIZE_LEVELS.get(
                    candidate_count.PAGE_SIZE_DEFAULT, "appearance.page_size_level_other"
                ),
                n=candidate_count.PAGE_SIZE_DEFAULT,
            )
        )
        self.page_size_hint.setWordWrap(True)
        size_layout.addWidget(self.page_size_hint)

        self.page_size_scoped = CaptionLabel("")
        self.page_size_scoped.setWordWrap(True)
        self.page_size_scoped.hide()
        size_layout.addWidget(self.page_size_scoped)

        self.page_size_note = CaptionLabel(i18n.tr("appearance.page_size_note"))
        self.page_size_note.setWordWrap(True)
        size_layout.addWidget(self.page_size_note)

        sections.addWidget(self.engine_label)
        sections.addWidget(self.page_size_card)
        sections.addWidget(self.fcitx5_label)
        sections.addWidget(self.themes_card)
        sections.addWidget(self.fcitx5_note)
        sections.addWidget(self.ibus_label)
        sections.addWidget(self.ibus_card)
        sections.addStretch(1)

        self.scroll_area = ScrollArea()
        self.scroll_area.setWidget(self._sections_host)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.enableTransparentBackground()
        self.add_expanding_scroll(self.scroll_area)

        # 与其余任务页一致：空闲态隐藏进度区（否则会留下一条 0% 的进度条横线）
        self._show_progress_area(False)
        self._fill()

    # ---- 构建辅助 ----

    def _build_thanks_card(self) -> SimpleCardWidget:
        """鸣谢内置主题的上游作者（BSD-2-Clause 要求随分发署名）。"""
        card = SimpleCardWidget(self.builtin_host)
        self.thanks_card = card
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        self.thanks_icon = IconWidget(FluentIcon.HEART, card)
        self.thanks_icon.setFixedSize(18, 18)
        layout.addWidget(self.thanks_icon, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        self.thanks_title = StrongBodyLabel(i18n.tr("appearance.thanks_title"), card)
        text_column.addWidget(self.thanks_title)
        self.thanks_body = CaptionLabel(
            i18n.tr("appearance.thanks_body", count=len(appearance.list_bundled_full_themes())),
            card,
        )
        self.thanks_body.setWordWrap(True)
        text_column.addWidget(self.thanks_body)
        self.thanks_note = CaptionLabel(i18n.tr("appearance.thanks_note"), card)
        self.thanks_note.setWordWrap(True)
        text_column.addWidget(self.thanks_note)
        self.thanks_link_label = CaptionLabel(appearance.BUNDLED_THEMES_REPO, card)
        self.thanks_link_label.setStyleSheet(f"color:{themeColor().name()};")
        text_column.addWidget(self.thanks_link_label)
        layout.addLayout(text_column, 1)

        self.thanks_button = PushButton(FluentIcon.LINK, i18n.tr("appearance.thanks_open"))
        self.thanks_button.setToolTip(appearance.BUNDLED_THEMES_REPO)
        self.thanks_button.clicked.connect(
            lambda: self.link_requested.emit(appearance.BUNDLED_THEMES_REPO)
        )
        layout.addWidget(self.thanks_button, 0, Qt.AlignmentFlag.AlignTop)
        return card

    def _switch_row(self, switch: SwitchButton, label: BodyLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(switch)
        return row

    def _emit_ibus_style(self) -> None:
        color = self.preedit_combo.currentData() or ""
        self.ibus_style_requested.emit(
            self.horizontal_switch.isChecked(),
            self.inline_switch.isChecked(),
            color,
        )

    # ---- 候选词数量（v1.0.41） ----

    def _on_page_size_clicked(self, value: int) -> None:
        """用户在刻度上点了某一格（idClicked 只响应用户操作）。"""
        value = candidate_count.clamp(value)
        # 点的是当前值、且配置已由本安装器持有 → 不发意图，
        # 避免一次无意义的重新部署 + 框架重启（v1.0.40 同一教训）
        if value == self._page_size and self._page_size_block:
            return
        self.page_size_requested.emit(value)

    def _on_tab_changed(self, route_key: str) -> None:
        self.builtin_host.setVisible(route_key == "builtin")
        self.custom_host.setVisible(route_key == "custom")
        # 切换标签后重算网格列数：刚 show 出来的网格此刻 width() 还没更新，
        # 排到事件循环下一拍再算（否则隐藏期间算出的列数会一直沿用）
        grid = self.builtin_grid if route_key == "builtin" else self.custom_grid
        QTimer.singleShot(0, grid._relayout)

    # ---- 状态刷新 ----

    def _fill(self) -> None:
        report = self._report
        frontend = report.recommended_frontend or ""
        self._set_row(self.frontend_row, frontend or "—")
        is_fcitx5 = "fcitx5" in frontend
        is_ibus = "ibus" in frontend

        # fcitx5 主题：枚举 + 反读当前 + 备份存在性
        conf_path = appearance.fcitx5_conf_path()
        themes = appearance.list_themes(
            appearance.system_themes_dir(), appearance.user_themes_dir()
        )
        self._builtin_themes, self._custom_themes = appearance.split_themes(themes)
        self._current_theme = appearance.current_theme_key(conf_path)
        self._backup_exists = appearance.theme_backup_path().is_file()
        if self._current_theme:
            current_name = next(
                (t.name for t in self._builtin_themes + self._custom_themes
                 if t.key == self._current_theme),
                self._current_theme,
            )
            # 名称与 key 相同时只显示一个（v1.0.16：deepin-dark (deepin-dark) 去重）
            theme_display = (
                current_name
                if current_name == self._current_theme
                else f"{current_name}（{self._current_theme}）"
            )
            self._set_row(self.theme_row, theme_display, "ok")
        else:
            self._set_row(self.theme_row, i18n.tr("appearance.theme_unset"), "muted")
        if self._backup_exists:
            self.theme_row.itemAt(self.theme_row.count() - 1).widget().setToolTip(
                i18n.tr("appearance.theme_backup_exists")
            )
        self._rebuild_cards()
        self.fcitx5_label.setVisible(is_fcitx5)
        self.themes_card.setVisible(is_fcitx5)
        self.fcitx5_note.setVisible(is_fcitx5)
        self.theme_revert_button.setEnabled(is_fcitx5 and self._backup_exists and not self._busy)
        self.open_dir_button.setEnabled(is_fcitx5 and not self._busy)

        # ibus 外观：从标记块反读
        rime_dir = report.rime_user_dir
        self._ibus_style = (
            appearance.detect_ibus_style(rime_dir) if rime_dir else None
        )
        style = self._ibus_style
        if style is None:
            self._set_row(self.ibus_row, i18n.tr("appearance.ibus_unset"), "muted")
        else:
            parts = [
                i18n.tr("appearance.ibus_horizontal") + ("✓" if style.horizontal else "✗"),
                i18n.tr("appearance.ibus_inline_preedit") + ("✓" if style.inline_preedit else "✗"),
                (style.color_scheme or i18n.tr("appearance.ibus_default")),
            ]
            self._set_row(self.ibus_row, " · ".join(parts), "ok")
        self._fill_ibus_controls()
        self.ibus_label.setVisible(is_ibus)
        self.ibus_card.setVisible(is_ibus)
        if rime_dir is None:
            self.ibus_note.setText(
                i18n.tr("appearance.ibus_no_dir") + "；" + i18n.tr("appearance.ibus_note")
            )
        self._fill_page_size()
        self._apply_rules()

    def _fill_page_size(self) -> None:
        """反读「引擎实际会用」的候选数并回填控件（v1.0.41）。

        取值优先级见 ``core/candidate_count.detect``：用户 patch → 当前方案
        自身 → 方案包 default.yaml → 5。界面显示的数字必须就是用户输入时
        看到的那一页，而不是本程序的记账。
        """
        rime_dir = self._report.rime_user_dir
        schema_id = ""
        if rime_dir is not None:
            try:
                schema_id = schema_switch.detect_active_schema(rime_dir)
            except OSError:
                schema_id = ""
        self._page_size = candidate_count.detect(rime_dir, schema_id)
        self._page_size_block = candidate_count.has_own_block(rime_dir)
        scoped = (
            candidate_count.schemas_with_own_page_size(rime_dir) if rime_dir else []
        )

        # 回填刻度：只设选中态，不发意图（idClicked 才代表用户操作）
        for value, button in self.page_size_buttons.items():
            button.setChecked(value == self._page_size)
        self.page_size_hint.setText(self._page_size_level_text(self._page_size))

        if rime_dir is None:
            # 没有配置目录时不知道真实值，宁可显示占位也不要编一个 5 出来
            self._set_row(self.page_size_row, "—", "muted")
        else:
            self._set_row(
                self.page_size_row,
                i18n.tr("appearance.page_size_value", n=self._page_size),
                "ok",
            )
        if scoped:
            # 少数方案（如 oh-my-rime）把 page_size 写在自己的方案文件里，全局
            # patch 对它们无效——写入时会补写方案级 patch，这里提前说清楚
            self.page_size_scoped.setText(
                i18n.tr("appearance.page_size_scoped", count=len(scoped))
            )
            self.page_size_scoped.show()
        else:
            self.page_size_scoped.hide()

    def _page_size_level_text(self, value: int) -> str:
        """当前档位的一句话解释（5 默认 / 7 推荐 / 9 上限 / 其余为自定义）。"""
        key = _PAGE_SIZE_LEVELS.get(value, "appearance.page_size_level_other")
        return i18n.tr(key, n=value)

    def _fill_ibus_controls(self) -> None:
        style = self._ibus_style or appearance.IbusStyle()
        self.horizontal_switch.setChecked(style.horizontal)
        self.inline_switch.setChecked(style.inline_preedit)
        self.preedit_combo.clear()
        # 注意：qfluentwidgets ComboBox.addItem 的第二位置参数是 icon 而非
        # userData——位置传参会把配色键吞进 icon 槽，currentData() 恒 None，
        # 用户选的颜色永远传不出去（v1.0.13 冒烟实证，settings_page 同库先例）
        self.preedit_combo.addItem(i18n.tr("appearance.ibus_default"), userData="")
        for scheme in appearance.IBUS_COLOR_SCHEMES:
            self.preedit_combo.addItem(scheme, userData=scheme)
        index = self.preedit_combo.findData(style.color_scheme or "")
        self.preedit_combo.setCurrentIndex(max(index, 0))

    def _make_card(self, theme: appearance.ThemeInfo) -> _ThemeCard:
        card = _ThemeCard(
            theme,
            self.builtin_grid,
            # 只有「我的主题」里的东西归用户所有，删了不会自己长回来
            deletable=theme.source == "user",
        )
        card.switch_requested.connect(self.theme_switch_requested.emit)
        card.delete_requested.connect(self._on_delete_requested)
        return card

    # ---- 删除用户主题（v1.0.24） ----

    @staticmethod
    def confirm_delete(
        theme: appearance.ThemeInfo, current_key: str, parent: QWidget | None = None
    ) -> bool:
        """删除前二次确认：给出真实路径，删的是当前主题时点明后果。"""
        lines = [
            i18n.tr(
                "appearance.delete_target",
                path=str(appearance.user_themes_dir() / theme.key),
            ),
            i18n.tr("appearance.delete_note"),
        ]
        if theme.key == current_key:
            lines.append("")
            lines.append(i18n.tr("appearance.delete_current_warn"))
        box = MessageBox(
            i18n.tr("appearance.delete_title", name=theme.name),
            "\n".join(lines),
            parent,
        )
        return bool(box.exec())

    def _on_delete_requested(self, theme_key: str) -> None:
        """卡片删除按钮 → 确认框 → 交主窗口编排（真正删盘 + 必要时重启框架）。

        只查 `_custom_themes`：内置主题的按钮本就隐藏，即便被程序化触发也
        不该走到这里（删不掉，只会给用户一个假入口）。
        """
        theme = next((t for t in self._custom_themes if t.key == theme_key), None)
        if theme is None or self._busy:
            return
        if self.confirm_delete(theme, self._current_theme, self.window()):
            self.theme_delete_requested.emit(theme_key)

    def _rebuild_cards(self) -> None:
        """重建两个标签页的卡片（每个标签页独立网格，互不干扰）。"""
        builtin_cards = [self._make_card(t) for t in self._builtin_themes]
        custom_cards = [self._make_card(t) for t in self._custom_themes]
        self.builtin_grid.set_cards(builtin_cards)
        self.custom_grid.set_cards(custom_cards)
        self._cards = {card.theme.key: card for card in builtin_cards + custom_cards}
        for key, card in self._cards.items():
            card.set_current(key == self._current_theme, enabled=not self._busy)

        self.custom_empty.setVisible(not self._custom_themes)
        self.custom_grid.setVisible(bool(self._custom_themes))

        # 标签文案带数量；数量变化就更新
        self.tabs.setItemText(
            "builtin",
            i18n.tr(
                "appearance.tab_label",
                name=i18n.tr("appearance.tab_builtin"),
                count=len(self._builtin_themes),
            ),
        )
        self.tabs.setItemText(
            "custom",
            i18n.tr(
                "appearance.tab_label",
                name=i18n.tr("appearance.tab_custom"),
                count=len(self._custom_themes),
            ),
        )

        # 当前主题若是系统自带（不列出），给出提示而不是让列表「空着不说话」
        show_system_note = bool(self._current_theme) and self._current_theme not in self._cards
        if show_system_note:
            self.system_note.setText(
                i18n.tr("appearance.system_theme_note", name=self._current_theme)
            )
        self.system_note.setVisible(show_system_note)

        self.themes_card.adjustSize()
        self._sections_host.adjustSize()

    def _refresh(self) -> None:
        """重新扫描主题目录并刷新列表（无需重启软件）。

        把新放进 ~/.local/share/fcitx5/themes 的主题文件夹即时显示出来；
        内置主题本就常驻列表，刷新不影响它们。busy 时忽略（避免与
        切换任务并发改写 classicui.conf）。
        """
        if self._busy:
            return
        self._fill()
        self.set_status(i18n.tr("appearance.refreshed"))

    # ---- 从链接导入主题（v1.0.21） ----

    def _sync_import_button(self) -> None:
        """链接格式合法 + 状态机允许 + 非 busy 才可点（与方案市场共用 parse_spec）。"""
        text = self.import_input.text().strip()
        rule = self._sm.rule("appearance_theme")
        valid = bool(text) and parse_spec(text) is not None
        self.import_button.setEnabled(valid and rule.enabled and not self._busy)
        if not valid:
            self.import_button.setToolTip(i18n.tr("appearance.import_tip_invalid"))
        elif not rule.enabled:
            self.import_button.setToolTip(rule.reason or i18n.tr("appearance.import_tip"))
        else:
            self.import_button.setToolTip(i18n.tr("appearance.import_tip"))

    def _emit_import(self) -> None:
        if not self.import_button.isEnabled():
            return
        self.theme_import_requested.emit(self.import_input.text().strip())

    def prefill_import(self, text: str) -> None:
        """跨页联动：方案市场识别出「这是主题仓库」时把链接带过来（v1.0.21）。"""
        self.tabs.setCurrentItem("custom")
        self.import_input.setText(text)
        self._sync_import_button()
        self.import_input.setFocus()

    def refresh_themes(self, switch_to_custom: bool = False) -> None:
        """导入完成后由主窗口调用：重扫主题目录（busy 已退出，可安全刷新）。"""
        self._fill()
        if switch_to_custom:
            self.tabs.setCurrentItem("custom")

    def clear_import_input(self) -> None:
        """导入成功后清掉输入框，避免用户误以为还能再点一次。"""
        self.import_input.clear()
        self._sync_import_button()

    def _apply_rules(self) -> None:
        """按状态机规则刷新按钮可用性（busy 时全部禁用）。"""
        theme_rule = self._sm.rule("appearance_theme")
        ibus_rule = self._sm.rule("appearance_ibus")
        for key, card in self._cards.items():
            card.set_current(key == self._current_theme, enabled=theme_rule.enabled)
            card.switch_button.setToolTip(
                theme_rule.reason or i18n.tr("appearance.theme_switch_tip")
            )
            card.setToolTip(theme_rule.reason or i18n.tr("appearance.theme_switch_tip"))
        self.theme_revert_button.setEnabled(
            theme_rule.enabled and self._backup_exists
        )
        self.theme_revert_button.setToolTip(
            theme_rule.reason or i18n.tr("appearance.theme_revert_tip")
        )
        self.open_dir_button.setEnabled(theme_rule.enabled)
        self.open_dir_button.setToolTip(
            theme_rule.reason or i18n.tr("appearance.open_themes_dir_tip")
        )
        self.refresh_button.setEnabled(theme_rule.enabled)
        self.refresh_button.setToolTip(
            theme_rule.reason or i18n.tr("appearance.refresh_themes_tip")
        )
        self.import_input.setEnabled(theme_rule.enabled and not self._busy)
        self._sync_import_button()
        self.ibus_apply_button.setEnabled(ibus_rule.enabled)
        self.ibus_apply_button.setToolTip(
            ibus_rule.reason or i18n.tr("appearance.ibus_apply_tip")
        )
        self.ibus_revert_button.setEnabled(ibus_rule.enabled and self._ibus_style is not None)
        self.ibus_revert_button.setToolTip(
            ibus_rule.reason or i18n.tr("appearance.ibus_revert_tip")
        )
        # v1.0.41：候选词数量（写 rime 用户目录 + 重新部署，需先识别到目录）
        size_rule = self._sm.rule("appearance_page_size")
        self._set_page_size_enabled(size_rule.enabled and not self._busy)
        for value, button in self.page_size_buttons.items():
            button.setToolTip(size_rule.reason or self._page_size_tips.get(value, ""))
        self.page_size_revert_button.setEnabled(
            size_rule.enabled and self._page_size_block and not self._busy
        )
        self.page_size_revert_button.setToolTip(
            size_rule.reason or i18n.tr("appearance.page_size_revert_tip")
        )

    def _set_page_size_enabled(self, enabled: bool) -> None:
        """刻度整体启用/禁用（1–9 是一组，禁用原因看任一格的 tooltip）。"""
        for button in self.page_size_buttons.values():
            button.setEnabled(enabled)

    def bind(self, report: EnvReport, sm: StateMachine) -> None:
        self._report = report
        self._sm = sm
        self._busy = bool(sm.busy)
        self._fill()

    # ---- 执行期 UI（主窗口 busy 协议） ----

    def set_busy(self, busy: bool, title: str = "", running_key: str = "") -> None:
        self._busy = busy
        self.cancel_button.setEnabled(busy)
        self.log_button.setEnabled(not busy)
        if busy:
            # busy 协议：全部交互控件禁用（sm 规则由主窗口统一刷新，此处直接置位）
            for card in self._cards.values():
                card.set_current(card.theme.key == self._current_theme, enabled=False)
            self.theme_revert_button.setEnabled(False)
            self.open_dir_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.import_input.setEnabled(False)
            self.import_button.setEnabled(False)
            self.ibus_apply_button.setEnabled(False)
            self.ibus_revert_button.setEnabled(False)
            self._set_page_size_enabled(False)
            self.page_size_revert_button.setEnabled(False)
            self._show_progress_area(True)
            self.enter_waiting()
            self.set_step(1, 1, title or "准备中")
        else:
            self._show_progress_area(False)
            self.reset_progress()
            self._fill()

    def set_step(self, index: int, total: int, title: str) -> None:
        self.status_label.setText(f"（{index}/{total}）{title}" if total > 1 else title)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def retranslate(self) -> None:
        super().retranslate()
        self.cancel_button.setText(i18n.tr("common.cancel_task"))
        self.log_button.setText(i18n.tr("common.view_log"))
        self.frontend_row.itemAt(0).widget().setText(i18n.tr("appearance.frontend_row"))
        self.theme_row.itemAt(0).widget().setText(i18n.tr("appearance.theme_row"))
        self.ibus_row.itemAt(0).widget().setText(i18n.tr("appearance.ibus_row"))
        self.page_size_row.itemAt(0).widget().setText(i18n.tr("appearance.page_size_row"))
        self.engine_label.setText(i18n.tr("appearance.section_candidates"))
        self.page_size_label.setText(i18n.tr("appearance.page_size_title"))
        self.page_size_hint.setText(self._page_size_level_text(self._page_size))
        self.page_size_note.setText(i18n.tr("appearance.page_size_note"))
        for value, button in self.page_size_buttons.items():
            tip_key = _PAGE_SIZE_TIPS.get(value)
            tip_text = i18n.tr(tip_key) if tip_key else ""
            self._page_size_tips[value] = tip_text
            button.setToolTip(tip_text)
        self.page_size_revert_button.setText(i18n.tr("appearance.page_size_revert"))
        self.page_size_revert_button.setToolTip(
            i18n.tr("appearance.page_size_revert_tip")
        )
        self.fcitx5_label.setText(i18n.tr("appearance.section_fcitx5"))
        self.ibus_label.setText(i18n.tr("appearance.section_ibus"))
        self.fcitx5_note.setText(i18n.tr("appearance.theme_note"))
        self.ibus_note.setText(i18n.tr("appearance.ibus_note"))
        self.theme_revert_button.setText(i18n.tr("appearance.theme_revert"))
        self.theme_revert_button.setToolTip(i18n.tr("appearance.theme_revert_tip"))
        self.open_dir_button.setText(i18n.tr("appearance.open_themes_dir"))
        self.open_dir_button.setToolTip(i18n.tr("appearance.open_themes_dir_tip"))
        self.refresh_button.setText(i18n.tr("appearance.refresh_themes"))
        self.refresh_button.setToolTip(i18n.tr("appearance.refresh_themes_tip"))
        self.import_button.setText(i18n.tr("appearance.import_button"))
        self.import_input.setPlaceholderText(i18n.tr("appearance.import_placeholder"))
        self.import_hint.setText(i18n.tr("appearance.import_note"))
        self._sync_import_button()
        self.horizontal_label.setText(i18n.tr("appearance.ibus_horizontal"))
        self.horizontal_switch.setToolTip(i18n.tr("appearance.ibus_horizontal_tip"))
        self.inline_label.setText(i18n.tr("appearance.ibus_inline_preedit"))
        self.inline_switch.setToolTip(i18n.tr("appearance.ibus_inline_preedit_tip"))
        self.preedit_color_label.setText(i18n.tr("appearance.ibus_preedit_color"))
        self.preedit_combo.setToolTip(i18n.tr("appearance.ibus_preedit_color_tip"))
        self.ibus_apply_button.setText(i18n.tr("appearance.ibus_apply"))
        self.ibus_apply_button.setToolTip(i18n.tr("appearance.ibus_apply_tip"))
        self.ibus_revert_button.setText(i18n.tr("appearance.ibus_revert"))
        self.ibus_revert_button.setToolTip(i18n.tr("appearance.ibus_revert_tip"))
        # 鸣谢区 + 空状态 + 卡片文案都随语言切换（_fill 内部会重建卡片）
        self.tabs.setItemText("builtin", i18n.tr("appearance.tab_builtin"))
        self.tabs.setItemText("custom", i18n.tr("appearance.tab_custom"))
        self.thanks_title.setText(i18n.tr("appearance.thanks_title"))
        self.thanks_body.setText(
            i18n.tr(
                "appearance.thanks_body", count=len(appearance.list_bundled_full_themes())
            )
        )
        self.thanks_note.setText(i18n.tr("appearance.thanks_note"))
        self.thanks_button.setText(i18n.tr("appearance.thanks_open"))
        self.custom_empty.title_label.setText(i18n.tr("appearance.custom_empty_title"))
        self.custom_empty.hint_label.setText(i18n.tr("appearance.custom_empty_hint"))
        self.custom_hint.setText(i18n.tr("appearance.custom_hint"))
        self._fill()

    def _set_row(self, row, value: str, status: str | None = None) -> None:
        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value)
            if status in STATUS_COLORS:
                label.setStyleSheet(f"color: {STATUS_COLORS[status]};")
