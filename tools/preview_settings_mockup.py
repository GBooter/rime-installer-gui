"""设置页改版（v1.0.26 设计预览）：离屏渲染多尺寸效果图。

不进入正式构建、不修改任何既有页面：仅用 qfluentwidgets 现成控件搭出
「应用信息 Hero 卡 + 界面与外观 + apt 镜像」三段式版式，按不同窗口宽度
渲染 2x 截图，供确认图标尺寸/留白/自适应规则。

用法：
    QT_QPA_PLATFORM=offscreen /usr/bin/python3 tools/preview_settings_mockup.py
输出：
    design/mockups/settings-<w>x<h>-<theme>-<variant>.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "resources" / "vendor"))

import config  # noqa: E402
from PyQt6.QtCore import QSize, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (  # noqa: E402
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    Theme,
    setTheme,
)

ICON_512 = ROOT / "packaging" / "icons" / "rime-installer-512.png"
OUT_DIR = ROOT / "design" / "mockups"

NAV_W = 100  # 侧栏宽度（MSFluentWindow 在最小窗口下的实际值）

NAV_ITEMS = [
    (FluentIcon.HOME, "欢迎"),
    (FluentIcon.DOWNLOAD, "引擎安装"),
    (FluentIcon.APPLICATION, "方案市场"),
    (FluentIcon.PALETTE, "外观"),
    (FluentIcon.SAVE, "备份恢复"),
    (FluentIcon.DEVELOPER_TOOLS, "环境修复"),
    (FluentIcon.HISTORY, "日志"),
    (FluentIcon.SETTING, "设置"),
]


def palette(dark: bool) -> dict[str, str]:
    if dark:
        return {
            "page": "#202124",
            "card": "#2B2B2B",
            "hairline": "#3A3D42",
            "text": "#E8E8E8",
            "muted": "#A8A6A0",
            "chip_bg": "#363A3F",
            "chip_border": "#454A50",
            "chip_text": "#C3CBD4",
            "accent": "#4DD0D8",
            "nav_sel": "rgba(77, 208, 216, 0.14)",
            "ok": "#5FD35F",
        }
    return {
        "page": "#F0F4F9",
        "card": "#FFFFFF",
        "hairline": "#EDF0F4",
        "text": "#1A1D21",
        "muted": "#5F5E5A",
        "chip_bg": "#F1F4F8",
        "chip_border": "#E3E8EF",
        "chip_text": "#4A5560",
        "accent": "#009FAA",
        "nav_sel": "rgba(0, 159, 170, 0.10)",
        "ok": "#3B6D11",
    }


class ElidedLabel(CaptionLabel):
    """单行省略标签：卡片内长文本不得把卡片撑宽。"""

    def __init__(self, text: str = "", parent=None) -> None:
        # qfluentwidgets 的标签构造器是 @overload 单分派，子类只能用 (parent)
        # 形态调用基类构造器，文本改走 super().setText()
        super().__init__(parent)
        super().setText(text)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        text = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width())
        )
        if text != super().text():
            super().setText(text)


class MirrorList(ListWidget):
    """镜像列表：默认 sizeHint(192) 会把卡片撑高、逼出滚动条，
    这里让 sizeHint 跟随 minimumHeight，多出来的高度由 layout stretch 分配。"""

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        hint.setHeight(self.minimumHeight())
        return hint


class IconBadge(QLabel):
    """高清图标：按 DPR 取 512 源图重采样，外加一枚柔和投影。"""

    def __init__(self, size: int, dpr: float = 2.0, parent=None) -> None:
        super().__init__(parent)
        pad = 6
        self._size = size
        self._dpr = dpr
        self.setFixedSize(size + pad * 2, size + pad * 2)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._render_pixmap()
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(29, 57, 99, 42))
        self.setGraphicsEffect(shadow)

    def _render_pixmap(self) -> None:
        icon = QIcon(str(ICON_512))
        pixmap = icon.pixmap(QSize(self._size, self._size), self._dpr)
        pixmap.setDevicePixelRatio(self._dpr)
        self.setPixmap(pixmap)

    def set_icon_size(self, size: int) -> None:
        if size == self._size:
            return
        self._size = size
        self.setFixedSize(size + 12, size + 12)
        self._render_pixmap()


def hairline(color: str) -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {color}; border: none;")
    return line


def chip(text: str, pal: dict[str, str]) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setFixedHeight(20)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"background: {pal['chip_bg']}; border: 1px solid {pal['chip_border']};"
        f" border-radius: 10px; color: {pal['chip_text']}; padding: 0 8px;"
    )
    return label


def meta_column(title: str, value: str, pal: dict[str, str], stretch: int) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(2)
    key = CaptionLabel(title)
    key.setStyleSheet(f"color: {pal['muted']};")
    val = BodyLabel(value)
    val.setStyleSheet(f"color: {pal['text']};")
    box.addWidget(key)
    box.addWidget(val)
    box.setProperty("stretch", stretch)
    return box


class SettingsMock(QWidget):
    """按窗口宽度自适应的设置页原型。"""

    MAX_CONTENT = 1200  # 宽屏内容上限：超过则左右留白，避免超长行

    def __init__(self, capped: bool = True, icon_override: int | None = None,
                 dark: bool = False) -> None:
        super().__init__()
        self._capped = capped
        self._icon_override = icon_override
        self._pal = palette(dark)
        self._icon_size = icon_override or 72

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        self._outer = outer
        outer.setSpacing(10)

        title = SubtitleLabel("设置")
        title.setStyleSheet(
            "font-size: 28px; font-weight: 600; color: %s;" % self._pal["text"]
        )
        outer.addWidget(title)
        subtitle = CaptionLabel("界面语言、外观主题、apt 软件源与关于信息")
        subtitle.setStyleSheet(f"color: {self._pal['muted']};")
        outer.addWidget(subtitle)

        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.enableTransparentBackground()
        self.stage = QWidget()
        self.stage_layout = QVBoxLayout(self.stage)
        self.stage_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_layout.setSpacing(10)
        self.stage.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.stage)
        outer.addWidget(self.scroll, 1)

        self.card_hero = self._build_hero()
        self.card_prefs = self._build_prefs()
        self.card_apt = self._build_apt()
        self.stage_layout.addWidget(self.card_hero)
        self.stage_layout.addWidget(self.card_prefs)
        self.stage_layout.addWidget(self.card_apt)
        self.stage_layout.addStretch(1)

        note = CaptionLabel("当前系统为 Deepin，非 Ubuntu：apt 镜像切换不可用（Deepin 官方源位于国内）。")
        note.setStyleSheet(f"color: {self._pal['muted']};")
        outer.addWidget(note)

    # ---- 三段式 ----
    def _card(self) -> SimpleCardWidget:
        card = SimpleCardWidget()
        card.setBorderRadius(8)
        return card

    def _build_hero(self) -> SimpleCardWidget:
        pal = self._pal
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.badge = IconBadge(self._icon_size)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(4)
        line = QHBoxLayout()
        line.setSpacing(10)
        self.name_label = SubtitleLabel(config.APP_NAME)
        self.name_label.setStyleSheet(
            f"color: {pal['text']}; font-size: 20px; font-weight: 600;"
        )
        line.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(chip(f"v{config.VERSION}", pal), 0, Qt.AlignmentFlag.AlignVCenter)
        line.addStretch(1)
        text.addLayout(line)

        tagline = CaptionLabel("RIME Installer · 基于 RIME / plum / aria2")
        tagline.setStyleSheet(f"color: {pal['muted']};")
        text.addWidget(tagline)

        text.addSpacing(10)
        meta = QHBoxLayout()
        meta.setSpacing(28)
        meta.addLayout(meta_column("推荐前端", "fcitx5-rime", pal, 0), 2)
        meta.addLayout(meta_column("作者", config.AUTHOR, pal, 0), 2)
        sys_col = QVBoxLayout()
        sys_col.setSpacing(2)
        key = CaptionLabel("适用系统")
        key.setStyleSheet(f"color: {pal['muted']};")
        self.sys_label = ElidedLabel(config.SUPPORTED_SYSTEMS)
        self.sys_label.setStyleSheet(f"color: {pal['text']};")
        sys_col.addWidget(key)
        sys_col.addWidget(self.sys_label)
        meta.addLayout(sys_col, 5)
        text.addLayout(meta)

        row.addLayout(text, 1)
        lay.addLayout(row)
        return card

    def _build_prefs(self) -> SimpleCardWidget:
        pal = self._pal
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(4)
        head = StrongBodyLabel("界面与外观")
        head.setStyleSheet(f"color: {pal['text']};")
        lay.addWidget(head)
        lay.addWidget(hairline(pal["hairline"]))

        for caption, value in (("界面语言", "简体中文"), ("外观主题", "跟随系统（当前：light）")):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(12)
            label = BodyLabel(caption)
            label.setStyleSheet(f"color: {pal['text']};")
            box = ComboBox()
            box.addItems([value])
            box.setFixedWidth(200)
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(box)
            lay.addLayout(row)
        return card

    def _build_apt(self) -> SimpleCardWidget:
        pal = self._pal
        card = self._card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = StrongBodyLabel("apt 软件源镜像")
        title.setStyleSheet(f"color: {pal['text']};")
        head.addWidget(title)
        head.addWidget(chip("仅 Ubuntu", pal), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        key = CaptionLabel("当前源")
        key.setStyleSheet(f"color: {pal['muted']};")
        val = BodyLabel("自定义源（Deepin 25）")
        val.setStyleSheet(f"color: {pal['ok']};")
        head.addWidget(key, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(val, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)
        lay.addWidget(hairline(pal["hairline"]))

        hint = CaptionLabel(
            "切换镜像会备份原源文件（同目录 .rime-backup）并执行 apt update；"
            "Deepin 官方源位于国内，通常无需切换。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {pal['muted']};")
        lay.addWidget(hint)

        self.mirror_list = MirrorList()
        for text in (
            "官方源（官方）— 未测速",
            "阿里云（镜像）— 未测速",
            "清华大学（镜像）— 未测速",
            "中科大（镜像）— 未测速",
        ):
            self.mirror_list.addItem(text)
        self._fit_list()
        lay.addWidget(self.mirror_list)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(PushButton(FluentIcon.SYNC, "测速"))
        switch = PrimaryPushButton(FluentIcon.UPDATE, "切换为选中镜像")
        switch.setEnabled(False)
        buttons.addWidget(switch)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        return card

    def _fit_list(self) -> None:
        rows = self.mirror_list.count()
        row_h = max(self.mirror_list.sizeHintForRow(0), 34) if rows else 34
        self.mirror_list.setFixedHeight(min(rows * row_h + 14, 240))

    # ---- 自适应 ----
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = self.width() - NAV_W - 48
        if self._capped:
            side = max(24, (self.width() - self.MAX_CONTENT) // 2)
        else:
            side = 24
        # 整列（标题 / 卡片 / 底部说明）一起居中收缩，避免标题贴左、卡片居中
        self._outer.setContentsMargins(side, 16, side, 16)
        # 两档足够：窄窗口 72、常规及以上 88（88 恰好与文字块等高，不额外撑高卡片）
        size = self._icon_override or (72 if width < 900 else 88)
        if hasattr(self, "badge"):
            self.badge.set_icon_size(size)



class Shell(QWidget):
    """窗口外壳：标题栏 + 侧栏 + 页面（只为看上下文比例，非真实 MainWindow）。"""

    def __init__(self, page: SettingsMock, dark: bool) -> None:
        super().__init__()
        pal = palette(dark)
        self.setStyleSheet(f"background: {pal['page']};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(40)
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 0, 12, 0)
        bar_lay.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(QIcon(str(ICON_512)).pixmap(QSize(18, 18), 2.0))
        bar_lay.addWidget(logo)
        title = CaptionLabel(f"{config.APP_NAME} v{config.VERSION}")
        title.setStyleSheet(f"color: {pal['text']};")
        bar_lay.addWidget(title)
        bar_lay.addStretch(1)
        for glyph in ("—", "▢", "✕"):
            btn = CaptionLabel(glyph)
            btn.setStyleSheet(f"color: {pal['muted']}; padding: 0 8px;")
            bar_lay.addWidget(btn)
        outer.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 6, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._nav(dark))
        body.addWidget(page, 1)
        outer.addLayout(body, 1)

    def _nav(self, dark: bool) -> QWidget:
        pal = palette(dark)
        rail = QWidget()
        rail.setFixedWidth(NAV_W)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        for index, (icon, text) in enumerate(NAV_ITEMS):
            selected = index == len(NAV_ITEMS) - 1
            item = QWidget()
            item.setFixedHeight(56)
            item_lay = QVBoxLayout(item)
            item_lay.setContentsMargins(0, 6, 0, 4)
            item_lay.setSpacing(2)
            color = QColor(pal["accent"] if selected else pal["muted"])
            pic = QLabel()
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pic.setPixmap(icon.icon(color=color).pixmap(QSize(20, 20), 2.0))
            item_lay.addWidget(pic)
            cap = CaptionLabel(text)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setStyleSheet(
                "font-size: 10px; color: %s;" % (pal["accent"] if selected else pal["muted"])
            )
            item_lay.addWidget(cap)
            if selected:
                item.setStyleSheet(
                    f"background: {pal['nav_sel']}; border-radius: 6px;"
                )
            lay.addWidget(item)
        lay.addStretch(1)
        return rail


def grab(widget: QWidget, width: int, height: int, path: Path) -> None:
    widget.resize(width, height)
    widget.show()
    QApplication.processEvents()
    widget.resize(width, height)
    QApplication.processEvents()
    scale = 2
    pixmap = QPixmap(width * scale, height * scale)
    # 关键：给 pixmap 设 DPR 后，QPainter 的逻辑坐标仍是 1x CSS 像素，
    # 字体与矢量按 2x 栅格化；若再手动 scale(2,2) 会放大两遍、只出四分之一画面。
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(QColor("#FFFFFF"))
    painter = QPainter(pixmap)
    widget.render(painter)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(path))
    print(f"{path.name}  {width}x{height} @2x")


def main() -> int:
    app = QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # (宽, 高, 限宽, 图标覆盖, 主题, 文件名后缀, 说明)
    cases = [
        (940, 640, True, None, "light", "940x640-min", "最小窗口"),
        (1180, 770, True, None, "light", "1180x770-default", "默认窗口 · 图标 88（推荐）"),
        (1180, 770, True, 72, "light", "1180x770-icon72", "默认窗口 · 图标 72（对照）"),
        (1600, 900, True, None, "light", "1600x900-cap", "宽屏 · 内容限宽 1200"),
        (1600, 900, False, None, "light", "1600x900-full", "宽屏 · 内容铺满（对照）"),
        (1180, 770, True, None, "dark", "1180x770-dark", "深色主题"),
    ]
    for width, height, capped, icon, theme_name, tag, note in cases:
        dark = theme_name == "dark"
        setTheme(Theme.DARK if dark else Theme.LIGHT)
        page = SettingsMock(capped=capped, icon_override=icon, dark=dark)
        shell = Shell(page, dark)
        path = OUT_DIR / f"settings-{tag}.png"
        grab(shell, width, height, path)
        print("   %s：%s" % (path.name, note))
        m = page._outer.contentsMargins()
        print("   page_w=%d margins=(%d,%d,%d,%d) title_x=%d card_x=%d scroll_x=%d" % (
            page.width(), m.left(), m.top(), m.right(), m.bottom(),
            page.findChild(type(page.card_hero)).x() if False else page.card_hero.mapTo(page, page.card_hero.rect().topLeft()).x(),
            0, page.scroll.mapTo(page, page.scroll.rect().topLeft()).x()))
        print("   vp=%d stage=%d/%d scroll=%d" % (
            page.scroll.viewport().height(), page.stage.height(),
            page.stage.minimumSizeHint().height(),
            page.scroll.verticalScrollBar().maximum()))
        shell.deleteLater()
        QApplication.processEvents()

    render_hero_plate()
    return 0




# ---------------------------------------------------------------------------
# 图标摆放方案对照（单卡渲染，便于比较"左置 / 居中 / 放大"三种观感）
# ---------------------------------------------------------------------------


def _hero_card(kind: str, icon: int) -> SimpleCardWidget:
    pal = palette(False)
    card = SimpleCardWidget()
    card.setBorderRadius(8)
    badge = IconBadge(icon)

    if kind == "center":
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(8)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
        name = SubtitleLabel(config.APP_NAME)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name.setStyleSheet(f"color: {pal['text']}; font-size: 20px; font-weight: 600;")
        lay.addWidget(name)
        tagline = CaptionLabel("RIME Installer · 基于 RIME / plum / aria2 · v" + config.VERSION)
        tagline.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        tagline.setStyleSheet(f"color: {pal['muted']};")
        lay.addWidget(tagline)
        lay.addSpacing(4)
        meta = CaptionLabel(
            "推荐前端 fcitx5-rime    作者 Feelup    适用系统 "
            "Ubuntu 26.04+ / Deepin 25.2+（X11 / Wayland）"
        )
        meta.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        meta.setStyleSheet(f"color: {pal['muted']};")
        lay.addWidget(meta)
        return card

    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 14, 20, 14)
    lay.setSpacing(0)
    row = QHBoxLayout()
    row.setSpacing(16)
    row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
    text = QVBoxLayout()
    text.setSpacing(4)
    line = QHBoxLayout()
    line.setSpacing(10)
    name = SubtitleLabel(config.APP_NAME)
    name.setStyleSheet(f"color: {pal['text']}; font-size: 20px; font-weight: 600;")
    line.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
    line.addWidget(chip(f"v{config.VERSION}", pal), 0, Qt.AlignmentFlag.AlignVCenter)
    line.addStretch(1)
    text.addLayout(line)
    tagline = CaptionLabel("RIME Installer · 基于 RIME / plum / aria2")
    tagline.setStyleSheet(f"color: {pal['muted']};")
    text.addWidget(tagline)
    text.addSpacing(10)
    meta = QHBoxLayout()
    meta.setSpacing(28)
    meta.addLayout(meta_column("推荐前端", "fcitx5-rime", pal, 0), 2)
    meta.addLayout(meta_column("作者", config.AUTHOR, pal, 0), 2)
    meta.addLayout(meta_column("适用系统", config.SUPPORTED_SYSTEMS, pal, 5), 5)
    text.addLayout(meta)
    text.addStretch(1)  # 图标比文字块高时，多余高度留在底部而不是撑开行距
    row.addLayout(text, 1)
    lay.addLayout(row)
    return card


def render_hero_plate() -> None:
    setTheme(Theme.LIGHT)
    pal = palette(False)
    holder = QWidget()
    holder.setObjectName("heroPlate")
    # 必须限定选择器：#heroPlate 之外不加作用域会把灰底刷到每个子标签上
    holder.setStyleSheet(f"#heroPlate {{ background: {pal['page']}; }}")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(24, 20, 24, 24)
    lay.setSpacing(8)

    for kind, icon, caption in (
        ("left", 88, "A · 左置图标 88px（推荐）"),
        ("left", 112, "B · 左置图标 112px（更强调，卡片会增高 24px）"),
        ("center", 88, "C · 居中图标 88px（关于窗口式，纵向多吃 90px）"),
    ):
        cap = CaptionLabel(caption)
        cap.setStyleSheet(f"color: {pal['muted']};")
        lay.addWidget(cap)
        lay.addWidget(_hero_card(kind, icon))
        lay.addSpacing(6)

    holder.resize(1080, holder.sizeHint().height())
    grab(holder, 1080, holder.sizeHint().height(), OUT_DIR / "hero-variants.png")


if __name__ == "__main__":
    raise SystemExit(main())
