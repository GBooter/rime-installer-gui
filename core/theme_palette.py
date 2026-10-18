"""fcitx5 classicui 主题配色（theme.conf 五色）——市场预览与外观页共用。

theme.conf 扁平段格式（fcitx5 classicui 约定）：
- ``[InputPanel] NormalColor / HighlightColor``：一般文字 / 高亮条文字；
- ``[InputPanel/Background] Color / BorderColor``：面板底色 / 边框；
- ``[InputPanel/Highlight] Color``：高亮选中条背景。

本模块只做「读色」：把一款主题的候选窗观感抽象成 ThemePalette，
供主题市场选择对话框与外观页主题行渲染迷你预览（v1.0.14 Stage 2：
两处 UI 共用同一套五色语义，预览才与实际候选窗一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ThemePalette:
    """候选窗五色（单项缺失由调用方兜底，不在此填假设值）。"""

    normal: str  # 一般文字色
    highlight: str  # 高亮条上的文字色
    highlight_bg: str  # 高亮选中条背景
    background: str  # 面板底色
    border: str  # 面板边框


#: 解析不到任何颜色时的中性占位（外观页保持行高一致；此时不标深浅）
NEUTRAL_PALETTE = ThemePalette(
    normal="#9a9a9a",
    highlight="#ffffff",
    highlight_bg="#b8b8b8",
    background="#ececec",
    border="#c8c8c8",
)


def color_luminance(color: str) -> float:
    """#RRGGBB / 0xRRGGBB / AARRGGBB → 感知亮度 0~1；无法解析返回 0.5。

    亮度加权算法（0.299/0.587/0.114），与 is_dark_color 共用同一阈值，
    使多处 UI 的深浅判定保持一致。
    """
    text = color.strip().lstrip("#")
    if text.lower().startswith("0x"):
        text = text[2:]
    if len(text) == 8:  # AARRGGBB → 丢 alpha
        text = text[2:]
    if len(text) != 6:
        return 0.5
    try:
        r, g, b = (int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.5
    return 0.299 * r + 0.587 * g + 0.114 * b


def is_dark_color(background: str) -> bool:
    """底色是否算暗（阈值 0.5，与各处深度标签判定一致）。"""
    return color_luminance(background) < 0.5


def parse_palette(conf_path: Path) -> ThemePalette | None:
    """从 theme.conf 解析五色；文件缺失或没有任何颜色字段返回 None。

    与 theme_market.parse_theme_conf 的分包职责：那边返回带 Name 的
    dict（市场枚举用，含变体兜底），这里只给外观页要的五色。
    """
    try:
        text = conf_path.read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, str] = {}
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip()
        if not value:
            continue
        if section == "[InputPanel]":
            if key == "NormalColor":
                values["normal"] = value
            elif key in ("HighlightColor", "HighlightCandidateColor"):
                values.setdefault("highlight", value)
        elif section == "[InputPanel/Background]":
            if key == "Color":
                values["background"] = value
            elif key == "BorderColor":
                values["border"] = value
        elif section == "[InputPanel/Highlight]" and key == "Color":
            values["highlight_bg"] = value
    if not values:
        return None
    return ThemePalette(
        normal=values.get("normal", "#888888"),
        highlight=values.get("highlight", "#888888"),
        highlight_bg=values.get("highlight_bg", "#888888"),
        background=values.get("background", "#ffffff"),
        border=values.get("border", "#cccccc"),
    )
