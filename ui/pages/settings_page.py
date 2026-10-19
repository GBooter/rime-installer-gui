"""设置页（v1.0.28 三段式）：应用信息 Hero + 界面与外观 + apt 镜像。

v1.0.28 的起因是「设置页的软件 Logo 高清图标不显示了」：旧版把 Logo 图标
和一堆 kv 行混在同一个信息卡里，图标只有 64 档（APP_ICON），在 2x 屏上是
放大的位图；更关键的是打包脚本曾把整个 packaging/ 目录排除，装机后图标
路径根本不存在，只剩主题图标兜底。

现在的做法：
- 图标单独成为 Hero 卡的主角，源图固定取 config.APP_LOGO（512 档），
  按屏幕 DPR 重采样到 88/72 两档（窄窗口降 72，避免卡片被撑高），
  每档都从 512 原图直接缩放，绝不放大小图；
- 版式拆成三张卡：应用信息 / 界面与外观 / apt 镜像，宽屏限宽 1200 居中，
  窄窗口自动铺满；
- 芯片、分隔线、次要文字这些自绘元素在 _fill() 里按当前明暗主题重刷
  （主窗口切主题会 _dispatch → bind → _fill，无需额外钩子）。

apt 镜像（F13）仅支持 Ubuntu（deb822 源）；Deepin 官方源位于国内，无需
切换，页面会给出说明。测速在 QThread 中并行执行，不阻塞 UI；切换走主窗口
pkexec + apt update 流水线（进度经 busy 协议展示）。
"""

from __future__ import annotations

from pathlib import Path

import config
from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    isDarkTheme,
)

from core import apt_mirror, compat, i18n, theme
from core.detector import EnvReport
from core.state_machine import StateMachine
from ui.pages.base import STATUS_COLORS, PageBase, reap_worker

#: 自绘元素（芯片 / 分隔线 / Hero 底色文字）的两套取色。qfluentwidgets 的
#: 标准控件自己会跟随主题，只有这几类需要我们手动刷。
_PALETTE_LIGHT = {
    "hairline": "#EDF0F4",
    "chip_bg": "#F1F4F8",
    "chip_border": "#E3E8EF",
    "chip_text": "#4A5560",
    "text": "#1A1D21",
}
_PALETTE_DARK = {
    "hairline": "#3A3D42",
    "chip_bg": "#363A3F",
    "chip_border": "#454A50",
    "chip_text": "#C3CBD4",
    "text": "#E8E8E8",
}


def _palette() -> dict[str, str]:
    """按当前 qfluentwidgets 主题取色（主题切换后由 _fill 重新取）。"""
    return _PALETTE_DARK if isDarkTheme() else _PALETTE_LIGHT


class MirrorSpeedWorker(QThread):
    """并行测速各镜像（urllib），完成后发回结果列表。"""

    results_ready = pyqtSignal(list)  # [(name, seconds|None), ...]

    def __init__(self, urls: list[tuple[str, str]], timeout: float = 8.0) -> None:
        super().__init__()
        self._urls = urls
        self._timeout = timeout

    def run(self) -> None:
        results = apt_mirror.measure_mirror_latency(self._urls, self._timeout)
        self.results_ready.emit(results)


class _IconBadge(QLabel):
    """Hero 图标：从 512 源图按 DPR 重采样，外加一枚柔和投影。

    QIcon.pixmap(size, dpr) 会直接按「逻辑 size × dpr」的物理分辨率渲染，
    因此 2x 屏上拿到的是 176×176 位图、逻辑尺寸仍是 88，不会二次放大。
    取 max(2.0, DPR) 是为了在 1x 屏上也做一次超采样（降采样比原图直出更锐）。
    """

    PAD = 6

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._rendered = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(size + self.PAD * 2, size + self.PAD * 2)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(22)
        self._shadow.setOffset(0, 3)
        self.setGraphicsEffect(self._shadow)
        self.apply_theme()
        self._render()

    @property
    def icon_size(self) -> int:
        return self._size

    @property
    def source_path(self) -> Path:
        """实际使用的源图（smoke_test 用它断言取的是 512 档）。"""
        return self._pick_source()

    @property
    def last_pixmap(self) -> QPixmap:
        """最近一次 _render 交给 setPixmap 的位图（smoke_test 断言物理分辨率用）。

        v1.0.30：不能改用 `self.pixmap()` 断言——Qt 6.11 起 QLabel 的
        pixmap() getter 会按控件自身 DPR 归一化后返回（2.0 的位图经过
        setPixmap()/pixmap() 往返变成 1.0、宽高减半），而本标签的超采样
        正是在这张 176 物理像素的位图里。这里原样保留渲染产物。
        """
        return self._rendered

    @staticmethod
    def _pick_source() -> Path:
        for candidate in (config.APP_LOGO, config.APP_ICON):
            try:
                if Path(candidate).is_file():
                    return Path(candidate)
            except OSError:
                continue
        return Path(config.APP_LOGO)

    def apply_theme(self) -> None:
        """投影色随主题调整：浅色用冷灰蓝，深色用纯黑避免发灰。"""
        color = QColor(0, 0, 0, 120) if isDarkTheme() else QColor(29, 57, 99, 42)
        self._shadow.setColor(color)

    def _render(self) -> None:
        source = self._pick_source()
        icon = QIcon(str(source)) if source.is_file() else QIcon()
        if icon.isNull():
            # 源图缺失（开发期漏拷 / 异常安装）：清空 pixmap，不抛异常也不留旧图
            self._rendered = QPixmap()
            self.setPixmap(QPixmap())
            return
        dpr = max(2.0, float(self.devicePixelRatioF()))
        pixmap = icon.pixmap(QSize(self._size, self._size), dpr)
        if pixmap.isNull():
            self._rendered = QPixmap()
            self.setPixmap(QPixmap())
            return
        pixmap.setDevicePixelRatio(dpr)
        self._rendered = pixmap
        self.setPixmap(pixmap)

    def set_icon_size(self, size: int) -> None:
        if size == self._size:
            return
        self._size = size
        self.setFixedSize(size + self.PAD * 2, size + self.PAD * 2)
        self._render()


class _ElidedLabel(CaptionLabel):
    """单行省略标签：卡片内的长文本不得把卡片（乃至整页）撑宽。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        # qfluentwidgets 的标签构造器是 @overload 单分派，子类只能用 (parent)
        # 形态调用基类构造器，文本改走 super().setText()
        super().__init__(parent)
        super().setText(text)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text
        super().setText(text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = self.width()
        if width <= 0:
            return
        text = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, width
        )
        if text != super().text():
            super().setText(text)


class _Chip(CaptionLabel):
    """圆角小标签（版本号 / 「仅 Ubuntu」）。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        super().setText(text)
        self.setFixedHeight(20)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.apply_palette()

    def apply_palette(self) -> None:
        pal = _palette()
        self.setStyleSheet(
            f"background: {pal['chip_bg']}; border: 1px solid {pal['chip_border']};"
            f" border-radius: 10px; color: {pal['chip_text']}; padding: 0 8px;"
        )


class _MirrorList(ListWidget):
    """镜像列表：默认 sizeHint(192) 会把卡片撑高、逼出滚动条，
    这里让 sizeHint 跟随 minimumHeight，多出来的高度由 layout stretch 分配。"""

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        hint.setHeight(self.minimumHeight())
        return hint


class SettingsPage(PageBase):
    mirror_switch_requested = pyqtSignal(str)  # 镜像名
    mirror_speedtest_requested = pyqtSignal()
    theme_change_requested = pyqtSignal(str)  # 主题模式 auto/light/dark
    language_change_requested = pyqtSignal(str)  # 语言代码 zh_CN / en_US
    log_requested = pyqtSignal()

    #: 宽屏内容上限：超过则左右留白，避免超长行（Hero meta 三列拉太开）
    MAX_CONTENT = 1200
    #: 图标两档：88 与文字块等高；窄窗口降 72，卡片不至于多长出来 16px
    ICON_LARGE = 88
    ICON_SMALL = 72
    #: 页面内容宽度低于此值时用 ICON_SMALL
    NARROW_WIDTH = 900

    def __init__(self, report: EnvReport, sm: StateMachine, parent=None) -> None:
        super().__init__("settings.title", "settings.subtitle", parent)
        self._report = report
        self._sm = sm
        self._latencies: dict[str, float | None] = {}
        self._worker: MirrorSpeedWorker | None = None
        self._hairlines: list[QFrame] = []
        self._testing = False
        self._busy_title = ""

        self._build_hero()
        self._build_prefs()
        self._build_apt()
        self._install_scroll()
        self._fill(report, sm)
        self._reload_mirrors()

    # ---- 版式 ----

    def _card(self) -> SimpleCardWidget:
        card = SimpleCardWidget()
        card.setBorderRadius(8)
        return card

    def _hairline(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setFrameShape(QFrame.Shape.NoFrame)
        self._hairlines.append(line)
        return line

    def _build_hero(self) -> None:
        """应用信息：图标 + 名称 + 版本胶囊 + tagline + 三列 meta。"""
        # PageBase 已经建好一张卡，直接复用为主角卡，避免再多一层嵌套
        card = self.card
        card.setBorderRadius(8)
        lay = self.card_layout
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.logo_badge = _IconBadge(self.ICON_LARGE)
        row.addWidget(self.logo_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(4)

        line = QHBoxLayout()
        line.setSpacing(10)
        self.name_label = SubtitleLabel(i18n.tr("app.title"))
        self.name_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        line.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.version_chip = _Chip(f"v{config.VERSION}")
        line.addWidget(self.version_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addStretch(1)
        text.addLayout(line)

        self.tagline_label = CaptionLabel(self._tagline_text())
        text.addWidget(self.tagline_label)
        text.addSpacing(10)

        meta = QHBoxLayout()
        meta.setSpacing(28)
        self.meta_keys: dict[str, tuple[CaptionLabel, str]] = {}
        self.frontend_value = BodyLabel("—")
        self.author_value = BodyLabel(config.AUTHOR)
        self.supported_value = _ElidedLabel(config.SUPPORTED_SYSTEMS)
        # v1.0.30：「当前系统」列——运行时探测 + 版本通配判定（core/compat）。
        # 与写死的「适用系统」互补：未来系统升级后这里自动显示实际版本与
        # 判定结果，不再需要为新版本改代码。
        self.current_system_value = _ElidedLabel("—")
        for key, i18n_key, value_widget, stretch in (
            ("frontend", "settings.frontend", self.frontend_value, 2),
            ("author", "settings.author", self.author_value, 2),
            ("supported", "settings.supported_systems", self.supported_value, 3),
            ("current_system", "settings.current_system", self.current_system_value, 3),
        ):
            column = QVBoxLayout()
            column.setSpacing(2)
            caption = CaptionLabel(i18n.tr(i18n_key))
            self.meta_keys[key] = (caption, i18n_key)
            column.addWidget(caption)
            column.addWidget(value_widget)
            meta.addLayout(column, stretch)
        text.addLayout(meta)
        text.addStretch(1)

        row.addLayout(text, 1)
        lay.addLayout(row)

    def _build_prefs(self) -> None:
        """界面与外观：两行「标签 + 右对齐 200 宽下拉」。"""
        self.prefs_card = self._card()
        lay = QVBoxLayout(self.prefs_card)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(4)

        self.prefs_title = StrongBodyLabel(i18n.tr("settings.prefs"))
        lay.addWidget(self.prefs_title)
        lay.addWidget(self._hairline())

        self.language_label = BodyLabel(i18n.tr("common.language"))
        self.language_combo = ComboBox()
        for lang in i18n.languages():
            # ⚠️ addItem 第二位置参数是 icon，userData 必须关键字传
            self.language_combo.addItem(i18n.label(lang), userData=lang)
        saved_lang = i18n.load_preference()
        self.language_combo.setCurrentIndex(max(i18n.languages().index(saved_lang), 0))
        self.language_combo.setFixedWidth(200)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)

        self.theme_label = BodyLabel(i18n.tr("settings.theme"))
        self.theme_combo = ComboBox()
        self.theme_combo.setFixedWidth(200)
        self._fill_theme_items()
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        for caption, combo in (
            (self.language_label, self.language_combo),
            (self.theme_label, self.theme_combo),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(12)
            row.addWidget(caption)
            row.addStretch(1)
            row.addWidget(combo)
            lay.addLayout(row)

    def _build_apt(self) -> None:
        """apt 镜像：标题行（含当前源）+ 说明 + 列表 + 按钮。"""
        self.apt_card = self._card()
        lay = QVBoxLayout(self.apt_card)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.mirror_title = StrongBodyLabel(i18n.tr("settings.mirror"))
        head.addWidget(self.mirror_title)
        self.ubuntu_chip = _Chip(i18n.tr("settings.mirror_only_ubuntu"))
        head.addWidget(self.ubuntu_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.current_key = CaptionLabel(i18n.tr("settings.current_source"))
        self.current_value = BodyLabel("—")
        head.addWidget(self.current_key, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.current_value, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)
        lay.addWidget(self._hairline())

        self.mirror_hint = CaptionLabel(i18n.tr("settings.mirror_hint"))
        self.mirror_hint.setWordWrap(True)
        lay.addWidget(self.mirror_hint)

        self.mirror_list = _MirrorList()
        self.mirror_list.setMinimumHeight(120)
        self.mirror_list.itemSelectionChanged.connect(self._on_mirror_selection_changed)
        lay.addWidget(self.mirror_list)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.speedtest_button = PushButton(
            FluentIcon.SYNC, i18n.tr("settings.speedtest")
        )
        self.speedtest_button.clicked.connect(self._on_speedtest)
        self.switch_button = PrimaryPushButton(
            FluentIcon.UPDATE, i18n.tr("settings.switch_mirror")
        )
        self.switch_button.clicked.connect(self._on_switch_clicked)
        buttons.addWidget(self.speedtest_button)
        buttons.addWidget(self.switch_button)
        buttons.addStretch(1)
        lay.addLayout(buttons)

    def _install_scroll(self) -> None:
        """三张卡收进滚动区：窄窗口时靠滚动而非挤压控件（v1.0.17 教训）。"""
        outer = self.layout()
        outer.removeWidget(self.card)

        self.stage = QWidget()
        self.stage.setStyleSheet("background: transparent;")
        self.stage_layout = QVBoxLayout(self.stage)
        self.stage_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_layout.setSpacing(10)
        self.stage_layout.addWidget(self.card)
        self.stage_layout.addWidget(self.prefs_card)
        self.stage_layout.addWidget(self.apt_card)
        self.stage_layout.addStretch(1)

        self.scroll_area = ScrollArea()
        self.scroll_area.setWidget(self.stage)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.enableTransparentBackground()
        self.add_expanding_scroll(self.scroll_area)

        # 非 Ubuntu 的说明放滚动区外底部（设计稿位置），空文本时隐藏不占位
        self.mirror_note = _ElidedLabel("")
        self.mirror_note.hide()
        outer.addWidget(self.mirror_note)

    # ---- 自适应 ----

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_content_width()
        self._apply_icon_tier()

    def _apply_content_width(self) -> None:
        """宽屏限宽 1200 居中：整列（标题 / 卡片 / 底部说明）一起收，避免标题贴左。"""
        outer = self.layout()
        if outer is None:
            return
        side = max(24, (self.width() - self.MAX_CONTENT) // 2)
        margins = outer.contentsMargins()
        if margins.left() != side or margins.right() != side:
            outer.setContentsMargins(side, margins.top(), side, margins.bottom())

    def _apply_icon_tier(self) -> None:
        target = self.ICON_SMALL if self.width() < self.NARROW_WIDTH else self.ICON_LARGE
        self.logo_badge.set_icon_size(target)

    # ---- 状态刷新 ----

    def _tagline_text(self) -> str:
        return f"{config.APP_NAME_EN} · {i18n.tr('settings.tagline')}"

    def _current_system_text(self, report: EnvReport) -> str:
        """「当前系统」列文案：运行时探测版本 + 版本通配判定（v1.0.30）。

        与写死的「适用系统」互补：系统升级（乃至未来的 28.04 / Mint 23）
        后这里自动显示实际版本与判定结果，无需为新版本改任何代码。
        """
        name = (report.os_name or "").strip() or "Linux"
        version = (report.os_version or "").strip()
        shown = f"{name} {version}".strip()
        verdict = compat.support_verdict(report.os_name, report.os_version)
        if verdict.state == compat.OK:
            note = i18n.tr("settings.support_ok", minimum=verdict.minimum_text)
        elif verdict.state == compat.BELOW:
            note = i18n.tr("settings.support_below", minimum=verdict.minimum_text)
        else:
            note = i18n.tr("settings.support_unknown")
        return f"{shown} · {note}"

    def _fill_theme_items(self) -> None:
        """重建主题下拉：跟随系统时把实际生效的明暗写进文案。"""
        current = theme.load_preference()
        resolved = theme.resolve_mode(current) if current == theme.AUTO else current
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for mode in theme.PREFERENCES:
            text = (
                i18n.tr("theme.auto_current", mode=resolved)
                if mode == theme.AUTO
                else i18n.tr(f"theme.{mode}")
            )
            self.theme_combo.addItem(text, userData=mode)
        index = theme.PREFERENCES.index(current) if current in theme.PREFERENCES else 0
        self.theme_combo.setCurrentIndex(index)
        self.theme_combo.blockSignals(False)

    def _fill(self, report: EnvReport, sm: StateMachine) -> None:
        pal = _palette()
        muted = STATUS_COLORS["muted"]

        # 自绘元素随主题换色（切主题 → bind → _fill，这一处就够）
        self.logo_badge.apply_theme()
        for chip in (self.version_chip, self.ubuntu_chip):
            chip.apply_palette()
        for line in self._hairlines:
            line.setStyleSheet(f"background: {pal['hairline']}; border: none;")
        for caption, _key in self.meta_keys.values():
            caption.setStyleSheet(f"color: {muted};")
        for widget in (
            self.tagline_label,
            self.current_key,
            self.mirror_hint,
            self.mirror_note,
        ):
            widget.setStyleSheet(f"color: {muted};")
        for widget in (
            self.frontend_value,
            self.author_value,
            self.supported_value,
            self.current_system_value,
        ):
            widget.setStyleSheet(f"color: {pal['text']};")

        self.frontend_value.setText(report.recommended_frontend or "—")
        self.supported_value.set_full_text(config.SUPPORTED_SYSTEMS)
        self.current_system_value.set_full_text(self._current_system_text(report))
        self._fill_theme_items()

        supported = "ubuntu" in (report.os_name or "").lower()
        current = apt_mirror.current_mirror_name()
        self.current_value.setText(
            f"{current}（{report.os_name} {report.os_version}）"
        )
        self.current_value.setStyleSheet(
            f"color: {STATUS_COLORS['ok' if current != '未知' else 'muted']};"
        )

        rule = sm.rule("switch_mirror")
        self.speedtest_button.setEnabled(rule.enabled and not self._testing)
        self.switch_button.setEnabled(
            rule.enabled and self._selected_mirror() is not None
        )
        self.switch_button.setToolTip(rule.reason or i18n.tr("settings.switch_tip"))
        self.speedtest_button.setToolTip(
            rule.reason or i18n.tr("settings.speedtest_tip")
        )

        if supported:
            self.mirror_note.hide()
        else:
            self.mirror_note.set_full_text(
                i18n.tr("settings.mirror_note", os=report.os_name or "Linux")
            )
            self.mirror_note.show()

    def bind(self, report: EnvReport, sm: StateMachine) -> None:
        self._report, self._sm = report, sm
        self._fill(report, sm)

    def retranslate(self) -> None:
        """切语言：刷新全部静态文案，再由 _fill 重建动态值。"""
        super().retranslate()
        self.name_label.setText(i18n.tr("app.title"))
        self.tagline_label.setText(self._tagline_text())
        for caption, key in self.meta_keys.values():
            caption.setText(i18n.tr(key))
        self.prefs_title.setText(i18n.tr("settings.prefs"))
        self.language_label.setText(i18n.tr("common.language"))
        self.theme_label.setText(i18n.tr("settings.theme"))
        self.mirror_title.setText(i18n.tr("settings.mirror"))
        self.ubuntu_chip.setText(i18n.tr("settings.mirror_only_ubuntu"))
        self.current_key.setText(i18n.tr("settings.current_source"))
        self.mirror_hint.setText(i18n.tr("settings.mirror_hint"))
        self.speedtest_button.setText(
            i18n.tr("settings.speedtesting")
            if self._testing
            else i18n.tr("settings.speedtest")
        )
        self.switch_button.setText(
            self._busy_title or i18n.tr("settings.switch_mirror")
        )
        self._fill(self._report, self._sm)
        self._reload_mirrors()

    # ---- 镜像列表与测速 ----

    def _mirror_line(self, mirror, current: str) -> str:
        latency = self._latencies.get(mirror.name)
        speed = i18n.tr("mirror.untested") if latency is None else f"{latency:.2f} s"
        kind = (
            i18n.tr("mirror.official") if mirror.is_official else i18n.tr("mirror.mirror")
        )
        suffix = f"（{i18n.tr('mirror.current')}）" if mirror.name == current else ""
        return f"{mirror.name}（{kind}）{suffix} — {speed}"

    def _reload_mirrors(self) -> None:
        current = apt_mirror.current_mirror_name()
        self.mirror_list.clear()
        for mirror in apt_mirror.mirrors():
            self.mirror_list.addItem(self._mirror_line(mirror, current))
        self._fit_list()

    def _fit_list(self) -> None:
        """列表高度跟随项数，最多 240：省下的高度留给 Hero 卡。"""
        rows = self.mirror_list.count()
        row_h = max(self.mirror_list.sizeHintForRow(0), 34) if rows else 34
        self.mirror_list.setFixedHeight(min(rows * row_h + 14, 240))

    def _selected_mirror(self):
        row = self.mirror_list.currentRow()
        if row < 0:
            return None
        mirrors = apt_mirror.mirrors()
        return mirrors[row] if row < len(mirrors) else None

    def _on_mirror_selection_changed(self) -> None:
        # 选中变化只影响切换按钮可用性；其余状态仍由 _fill 统一刷新
        rule = self._sm.rule("switch_mirror")
        self.switch_button.setEnabled(
            rule.enabled and self._selected_mirror() is not None
        )

    def _on_theme_changed(self) -> None:
        mode = self.theme_combo.currentData() or theme.AUTO
        self.theme_change_requested.emit(mode)

    def _on_language_changed(self) -> None:
        lang = self.language_combo.currentData() or i18n.ZH
        self.language_change_requested.emit(lang)

    def _on_speedtest(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._testing = True
        self.speedtest_button.setEnabled(False)
        self.speedtest_button.setText(i18n.tr("settings.speedtesting"))
        self._worker = MirrorSpeedWorker(apt_mirror.speedtest_urls())
        self._worker.results_ready.connect(self._on_speedtest_done)
        self._worker.start()

    def _on_speedtest_done(self, results: list) -> None:
        # v1.0.33：results_ready 在 run() 内发出，槽收到时线程可能仍在退出
        # 序列（isRunning() 仍为真）——直接清引用会 ~QThread → qFatal →
        # SIGABRT(134)（真机闪退根因，见 base.reap_worker）。先等线程真正
        # 退出再清引用；正常路径毫秒级。
        if reap_worker(self._worker):
            self._worker = None
        self._testing = False
        self._latencies = {name: seconds for name, seconds in results}
        self.speedtest_button.setText(i18n.tr("settings.speedtest"))
        self.speedtest_button.setEnabled(self._sm.rule("switch_mirror").enabled)
        self._reload_mirrors()

    def shutdown_workers(self) -> None:
        """v1.0.33：窗口关闭前回收测速线程（主窗口 closeEvent 调用）。

        关窗会销毁页面、丢掉 worker 最后一个引用；线程若还在跑，
        ~QThread 撞 isRunning() → SIGABRT(134)。有界等待后放行。
        """
        reap_worker(self._worker, 10_000)

    def _on_switch_clicked(self) -> None:
        mirror = self._selected_mirror()
        if mirror is None:
            return
        box = MessageBox(
            i18n.tr("dialog.switch_mirror_title", name=mirror.name),
            i18n.tr("dialog.switch_mirror_body", url=mirror.url),
            self,
        )
        if not box.exec():
            return
        self.mirror_switch_requested.emit(mirror.name)

    # ---- busy 协议（镜像切换任务由主窗口驱动） ----

    def set_busy(self, busy: bool, title: str = "") -> None:
        self.speedtest_button.setEnabled(not busy)
        self.switch_button.setEnabled(not busy)
        self.mirror_list.setEnabled(not busy)
        if busy:
            self._busy_title = title or i18n.tr("settings.switching")
            self.switch_button.setText(self._busy_title)
        else:
            self._busy_title = ""
            self.switch_button.setText(i18n.tr("settings.switch_mirror"))
            self._fill(self._report, self._sm)

    def set_step(self, index: int, total: int, title: str) -> None:
        text = f"（{index}/{total}）{title}" if total > 1 else title
        self._busy_title = text
        self.switch_button.setText(text)

    def set_progress(self, percent: int) -> None:
        pass  # 镜像任务为两步 apt 操作，进度由按钮文字与日志体现

    def set_status(self, text: str) -> None:
        self._busy_title = text
        self.switch_button.setText(text)
