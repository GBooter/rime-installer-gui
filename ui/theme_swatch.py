"""候选窗预览与标签样式（v1.0.19 重做）。

v1.0.14 的 ThemeSwatch 只画三根彩色横条，看不出主题长什么样；v1.0.19
换成 CandidatePreview——直接把主题自带的 panel.svg / highlight.svg 做
九宫格拉伸，叠上 theme.conf 里的文字色，画出一个「真实候选窗」：

- 面板的圆角、描边、投影全部来自主题本身，不是我们臆测的圆角矩形；
- 高亮条取 highlight.svg，图片型主题（deepin 系）同样准确；
- 文字取 [InputPanel] NormalColor / HighlightColor / HighlightBackgroundColor。

降级链：无 svg（自撰极简主题、用户只给 theme.conf）→ 按五色画圆角面板；
palette=None（图片型用户主题解析不出色）→ NEUTRAL_PALETTE 中性占位；
QtSvg 缺失（理论上 deb 依赖已覆盖）→ 同样走五色路径，不影响页面可用。

颜色解析注意：fcitx5 的 8 位色值是 #RRGGBBAA，Qt 认的是 #AARRGGBB，
必须换序——否则 BorderColor=#ffffff00（透明）会被当成不透明色画出来。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import QWidget

from qfluentwidgets import isDarkTheme, themeColor

from core.theme_palette import NEUTRAL_PALETTE, ThemePalette

try:  # QtSvg 在 deb 依赖里（qfluentwidgets 亦急切导入），缺失时降级不崩
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:  # pragma: no cover - 仅在裁剪发行版上出现
    QSvgRenderer = None  # type: ignore[assignment]

# 标签/徽标配色（浅色 UI / 暗色 UI 两套）
_TAG_LIGHT = "color:#5F5E5A;background:#F1EFE8;border-radius:4px;padding:1px 6px;"
_TAG_DARK = "color:#B4B2A9;background:#444441;border-radius:4px;padding:1px 6px;"
_BADGE_LIGHT = "color:#0F6E56;background:#E1F5EE;border-radius:4px;padding:1px 6px;"
_BADGE_DARK = "color:#5DCAA5;background:#085041;border-radius:4px;padding:1px 6px;"

#: 预览里演示用的候选词（不翻译：这是「拼音输入」的场景示意，中文候选最直观）
_CANDIDATES = (("1", "你好"), ("2", "拟好"), ("3", "尼豪"))

#: svg 光栅缓存（path+mtime+尺寸 → pixmap）：十张卡反复重绘不重复解析
_SVG_CACHE: dict[tuple[str, float, int], QPixmap | None] = {}
_SVG_PIXMAP_SIZE = 128
_SVG_CACHE_LIMIT = 96
#: 九宫格切片：panel.svg 圆角 9.5/31、highlight.svg 圆角 15/31。
#: 源切片按比例取（10/31、14/31），目标切片用固定像素——真实 fcitx5 渲染
#: svg 九宫格时四角保持原尺寸（不随窗口高度放大），否则圆角会被撑成胶囊。
_PANEL_SLICE_RATIO = 10 / 31
_PILL_SLICE_RATIO = 14 / 31
_PANEL_SLICE_PX = 14.0
_PILL_SLICE_PX = 12.0

_ALIGN_LEFT_VCENTER = int(
    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
)


def tag_style() -> str:
    """深浅标签样式（跟随当前明暗主题）。"""
    return _TAG_DARK if isDarkTheme() else _TAG_LIGHT


def badge_style() -> str:
    """强调徽标样式（跟随当前明暗主题）。"""
    return _BADGE_DARK if isDarkTheme() else _BADGE_LIGHT


def current_tag_style() -> str:
    """「当前使用」徽标：取系统强调色，与卡片当前态描边同色系。

    绿色（badge_style）在本页语义是「已安装」，当前态另有含义，
    混用会让蓝边+绿标互相打架。
    """
    accent = QColor(themeColor())
    if isDarkTheme():
        text = accent.name()
        background = f"rgba({accent.red()},{accent.green()},{accent.blue()},0.18)"
        return f"color:{text};background:{background};border-radius:4px;padding:1px 6px;"
    text = accent.name()
    background = f"rgba({accent.red()},{accent.green()},{accent.blue()},0.12)"
    return f"color:{text};background:{background};border-radius:4px;padding:1px 6px;"


def _parse_color(value: str, fallback: str | None = None) -> QColor | None:
    """fcitx5 色值 → QColor；全透明或无法解析时返回 fallback（默认 None）。"""
    text = (value or "").strip()
    if text.startswith("#") and len(text) == 9:  # #RRGGBBAA → #AARRGGBB
        text = "#" + text[7:9] + text[1:7]
    color = QColor(text) if text else QColor()
    if not color.isValid() or color.alpha() == 0:
        return QColor(fallback) if fallback else None
    return color


def _theme_pixmap(theme_dir: Path | None, filename: str) -> QPixmap | None:
    """把主题目录下的 svg 光栅成方形 pixmap（带 mtime 缓存，改图自动失效）。"""
    if theme_dir is None:
        return None
    path = Path(theme_dir) / filename
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = (str(path), mtime, _SVG_PIXMAP_SIZE)
    if key in _SVG_CACHE:
        return _SVG_CACHE[key]
    pixmap: QPixmap | None = None
    if QSvgRenderer is not None:
        renderer = QSvgRenderer(str(path))
        if renderer.isValid():
            pixmap = QPixmap(_SVG_PIXMAP_SIZE, _SVG_PIXMAP_SIZE)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            renderer.render(painter)
            painter.end()
    if len(_SVG_CACHE) > _SVG_CACHE_LIMIT:
        _SVG_CACHE.clear()
    _SVG_CACHE[key] = pixmap
    return pixmap


def _nine_slice(
    painter: QPainter,
    pixmap: QPixmap,
    target: QRectF,
    src_ratio: float,
    border: float,
) -> None:
    """九宫格铺满 target：四角原尺寸不拉伸，四边与中心填充区拉伸。

    src_ratio 是源图里切片边界占比（panel 10/31、highlight 14/31），
    border 是目标像素切片宽——与 fcitx5 渲染主题 svg 的方式一致：圆角
    保持原尺寸，只有中间部分随候选窗大小伸缩。若按 target 高度等比缩放
    四角（常见做法），84px 高的面板会被画成圆角 25px 的胶囊。
    """
    width, height = float(pixmap.width()), float(pixmap.height())
    border = min(border, target.width() / 2.0 - 0.5, target.height() / 2.0 - 0.5)
    if border <= 0.5:
        painter.drawPixmap(target, pixmap, QRectF(0, 0, width, height))
        return

    src_x = (0.0, width * src_ratio, width - width * src_ratio)
    src_w = (width * src_ratio, width - 2 * width * src_ratio, width * src_ratio)
    src_y = (0.0, height * src_ratio, height - height * src_ratio)
    src_h = (height * src_ratio, height - 2 * height * src_ratio, height * src_ratio)
    dst_x = (target.left(), target.left() + border, target.right() - border)
    dst_w = (border, target.width() - 2 * border, border)
    dst_y = (target.top(), target.top() + border, target.bottom() - border)
    dst_h = (border, target.height() - 2 * border, border)

    for column in range(3):
        if src_w[column] <= 0 or dst_w[column] <= 0:
            continue
        for row in range(3):
            if src_h[row] <= 0 or dst_h[row] <= 0:
                continue
            painter.drawPixmap(
                QRectF(dst_x[column], dst_y[row], dst_w[column], dst_h[row]),
                pixmap,
                QRectF(src_x[column], src_y[row], src_w[column], src_h[row]),
            )


class CandidatePreview(QWidget):
    """候选窗预览：桌面底色 + 主题面板 + 编码行 + 三条候选。

    背景（桌面底色）用中性渐变，浅色/深色主题都能看清轮廓，也不受
    当前 UI 明暗影响观感判断。控件本身不吃鼠标事件——点它等同点卡片。
    """

    HEIGHT = 118

    def __init__(
        self,
        palette: ThemePalette | None = None,
        theme_dir: Path | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.palette = palette or NEUTRAL_PALETTE
        self._dir = Path(theme_dir) if theme_dir else None
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(200)
        # 鼠标事件透传：整张卡片才是点击热区
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    # ---- 绘制 ----

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        area = QRectF(self.rect())
        self._paint_backdrop(painter, area)
        window = area.adjusted(18, 20, -18, -12)
        if window.width() < 140 or window.height() < 44:
            return
        self._paint_shadow(painter, window)
        if not self._paint_theme_panel(painter, window):
            self._paint_flat_panel(painter, window)
        self._paint_content(painter, window)

    def _paint_backdrop(self, painter: QPainter, area: QRectF) -> None:
        """桌面底色：浅色 UI 用冷灰白、暗色 UI 用深灰，主题面板浮在上面。"""
        gradient = QLinearGradient(area.topLeft(), area.bottomLeft())
        if isDarkTheme():
            gradient.setColorAt(0.0, QColor("#34363C"))
            gradient.setColorAt(1.0, QColor("#26282D"))
            border = QColor(255, 255, 255, 22)
        else:
            gradient.setColorAt(0.0, QColor("#F0F2F7"))
            gradient.setColorAt(1.0, QColor("#E0E4EC"))
            border = QColor(0, 0, 0, 18)
        painter.setPen(QPen(border, 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(area.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

    def _paint_shadow(self, painter: QPainter, window: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for index, alpha in enumerate((10, 7, 4)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            spread = 1.0 + index * 1.8
            painter.drawRoundedRect(
                window.adjusted(-spread, -spread + 2.0, spread, spread + 2.0),
                10 + spread,
                10 + spread,
            )

    def _paint_theme_panel(self, painter: QPainter, window: QRectF) -> bool:
        """用主题 panel.svg 铺面板；无 svg 返回 False 交给五色降级路径。"""
        pixmap = _theme_pixmap(self._dir, "panel.svg")
        if pixmap is None:
            return False
        _nine_slice(painter, pixmap, window, _PANEL_SLICE_RATIO, _PANEL_SLICE_PX)
        return True

    def _paint_flat_panel(self, painter: QPainter, window: QRectF) -> None:
        background = _parse_color(self.palette.background, "#FFFFFF")
        border = _parse_color(self.palette.border)
        painter.setBrush(background)
        painter.setPen(QPen(border, 1) if border else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(window, 8, 8)

    def _paint_content(self, painter: QPainter, window: QRectF) -> None:
        palette = self.palette
        normal = _parse_color(palette.normal, "#888888")
        highlight = _parse_color(palette.highlight, "#FFFFFF")
        highlight_bg = _parse_color(palette.highlight_bg, "#A5A5A5")

        pad_x = 16.0
        base = QFont(self.font())

        # 编码行（小字半透明，像真实候选窗里的拼音串）
        preedit_font = QFont(base)
        preedit_font.setPixelSize(10)
        painter.setFont(preedit_font)
        muted = QColor(normal)
        muted.setAlpha(140)
        painter.setPen(muted)
        painter.drawText(
            QRectF(window.left() + pad_x, window.top() + 11, window.width() - 2 * pad_x, 14),
            _ALIGN_LEFT_VCENTER,
            "ni hao",
        )

        # 候选行：首项带高亮底板，其余只是文字
        chip_height = 26.0
        chip_top = window.bottom() - 12 - chip_height
        candidate_font = QFont(base)
        candidate_font.setPixelSize(12)
        number_font = QFont(base)
        number_font.setPixelSize(9)
        metrics = QFontMetricsF(candidate_font)
        right_edge = window.right() - pad_x

        x = window.left() + pad_x
        for position, (number, text) in enumerate(_CANDIDATES):
            text_width = metrics.horizontalAdvance(text)
            chip_width = text_width + 30
            chip = QRectF(x, chip_top, chip_width, chip_height)
            if chip.right() > right_edge:
                break
            if position == 0:
                pill = _theme_pixmap(self._dir, "highlight.svg")
                if pill is not None:
                    _nine_slice(painter, pill, chip, _PILL_SLICE_RATIO, _PILL_SLICE_PX)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(highlight_bg)
                    painter.drawRoundedRect(chip, chip_height / 2, chip_height / 2)
                foreground = highlight
            else:
                foreground = normal
            # 序号比候选词淡一档，层次上像个角标
            number_pen = QColor(foreground)
            number_pen.setAlpha(150)
            painter.setFont(number_font)
            painter.setPen(number_pen)
            painter.drawText(
                QRectF(chip.left() + 11, chip.top(), 10, chip_height),
                _ALIGN_LEFT_VCENTER,
                number,
            )
            painter.setFont(candidate_font)
            painter.setPen(foreground)
            painter.drawText(
                QRectF(chip.left() + 20, chip.top(), text_width + 4, chip_height),
                _ALIGN_LEFT_VCENTER,
                text,
            )
            x += chip_width + 10

        # 右侧翻页箭头（真实候选窗的小细节，纯装饰）
        arrow = QColor(normal)
        arrow.setAlpha(90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(arrow)
        center_x, center_y = window.right() - 15, window.center().y()
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center_x - 4, center_y - 6),
                    QPointF(center_x + 4, center_y - 6),
                    QPointF(center_x, center_y - 1),
                ]
            )
        )
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(center_x - 4, center_y + 6),
                    QPointF(center_x + 4, center_y + 6),
                    QPointF(center_x, center_y + 1),
                ]
            )
        )
