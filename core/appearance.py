"""前端外观（Linux 真实可见）：fcitx5 classicui 主题 + ibus-rime 全局外观项。

背景（2026-10-17 源码级核查，见 MEMORY.md「皮肤生效范围」）：
rime 的配色（preset_color_schemes / style/color_scheme）只被 Windows
小狼毫、macOS 鼠须管渲染；Linux 上——

- fcitx5-rime 完全不读 rime 的 style/color_scheme（候选窗 100% 由
  fcitx5 classicui 主题绘制）；
- ibus-rime 只从前端级配置 ibus_rime.yaml 读少量 style 项（全局、
  启动时加载一次），color_scheme 仅 4 个硬编码 preset 且只作用于
  preedit 高亮。

因此「Linux 上能真正看见的外观控制」只有两条路径，本模块各提供一套：

1. fcitx5 用户：classicui 主题——`~/.config/fcitx5/conf/classicui.conf`
   的 `Theme=` + `~/.local/share/fcitx5/themes/<名>/theme.conf`；
2. ibus 用户：`ibus_rime.custom.yaml` 的 style/horizontal 等全局项
   （启动时加载，需注销重登生效）。

与皮肤（core/skin.py）同构：只操作自己管理的配置键/标记块，不解析或
重写用户其他配置；首次改写 classicui.conf 前先备份原件。
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import config
from core.runner import Step
from core.theme_import import valid_theme_name
from core.theme_palette import ThemePalette, is_dark_color, parse_palette

#: classicui 配置文件（fcitx5 全局配置，非 rime 目录）
FCITX5_CONF_RELATIVE = Path(".config") / "fcitx5" / "conf" / "classicui.conf"
#: fcitx5 主题搜索路径（系统 → 用户，同名用户优先）
THEMES_SYSTEM_DIR = Path("/usr/share/fcitx5/themes")
THEMES_USER_DIR = Path(".local/share/fcitx5/themes")
#: classicui.conf 原件备份（首次改写前落盘，恢复默认用）
THEME_BACKUP_RELATIVE = Path("backups") / "classicui.conf.orig"

#: ibus-rime 前端级外观配置（写在 rime 用户目录）
IBUS_STYLE_FILE = "ibus_rime.custom.yaml"
IBUS_MARK_BEGIN = "# >>> rime-installer: ibus-style >>>"
IBUS_MARK_END = "# <<< rime-installer: ibus-style <<<"
#: ibus-rime 源码 rime_settings.c 硬编码的 4 个 preedit 高亮 preset
IBUS_COLOR_SCHEMES = ("aqua", "azure", "ink", "luna")


@dataclass(frozen=True)
class ThemeInfo:
    """一个已安装的 fcitx5 主题。"""

    key: str  # 目录名 = classicui.conf 的 Theme= 取值
    name: str  # theme.conf 的 [Metadata] Name（缺失回退目录名）
    source: str  # system / user / installer
    path: Path  # 主题目录
    #: 候选窗五色（v1.0.14：迷你预览用）。内置主题取 BUNDLED_THEMES 权威值，
    #: 其余解析 theme.conf；None = 无配色信息（显示中性占位、不标深浅）
    palette: ThemePalette | None = None
    #: 是否深色主题（None = 无法判定）
    dark: bool | None = None


@dataclass(frozen=True)
class BundledTheme:
    """安装器内置主题（自撰 minimal theme.conf，零网络依赖）。"""

    key: str
    name: str
    dark: bool
    normal: str  # 一般文字色
    highlight: str  # 高亮条上的文字色
    highlight_bg: str  # 高亮条背景
    background: str
    border: str


#: 内置 3 款，色语言与 core/skin.py 的 rime 皮肤一致（墨黑/素白/深海）
BUNDLED_THEMES: tuple[BundledTheme, ...] = (
    BundledTheme(
        key="rime-installer-ink",
        name="墨黑",
        dark=True,
        normal="#e8e8e8",
        highlight="#ffffff",
        highlight_bg="#333333",
        background="#141414",
        border="#3a3a3a",
    ),
    BundledTheme(
        key="rime-installer-paper",
        name="素白",
        dark=False,
        normal="#222222",
        highlight="#000000",
        highlight_bg="#e1e1e1",
        background="#ffffff",
        border="#d0d0d0",
    ),
    BundledTheme(
        key="rime-installer-deep",
        name="深海",
        dark=True,
        normal="#d8e0e8",
        highlight="#ffffff",
        highlight_bg="#2f4a63",
        background="#102a43",
        border="#24405a",
    ),
)

BUNDLED_THEME_MAP: dict[str, BundledTheme] = {t.key: t for t in BUNDLED_THEMES}

#: classicui 编译期内置主题（磁盘上未必有 theme.conf，fcitx5 恒可解析）
CLASSICUI_BUILTIN_THEMES = ("default", "default-dark")

#: 删除「当前使用中的主题」时的落点：default 由 classicui 编译内置，恒可用。
#: 不用 deepin-* / default-dark 之类发行版自带主题——它们未必存在。
DEFAULT_THEME_KEY = "default"


def bundled_theme(key: str) -> BundledTheme | None:
    return BUNDLED_THEME_MAP.get(key)


#: 内置「完整主题」资源目录（随安装包发布的第三方/精选主题，含 theme.conf + svg）。
#: 与 BUNDLED_THEMES（自撰极简主题，运行时生成 theme.conf）不同，这里是整包复制。
#: 解析相对安装根（core/appearance.py 的祖父目录），开发期与 deb 安装后都正确。
BUNDLED_FULL_THEMES_DIR = Path(__file__).resolve().parent.parent / "resources" / "bundled_themes"

#: 内置完整主题的上游出处与署名（外观页鸣谢区展示；许可证原文见
#: resources/bundled_themes/LICENSE，BSD-2-Clause 要求随分发保留）
BUNDLED_THEMES_REPO = "https://github.com/sanweiya/fcitx5-mellow-themes"
BUNDLED_THEMES_AUTHOR = "sanweiya"
BUNDLED_THEMES_LICENSE = "BSD-2-Clause"


def bundled_full_theme_keys() -> set[str]:
    """内置完整主题的 key 集合（即资源目录下各主题目录名）。"""
    if not BUNDLED_FULL_THEMES_DIR.is_dir():
        return set()
    return {
        entry.name
        for entry in BUNDLED_FULL_THEMES_DIR.iterdir()
        if entry.is_dir() and (entry / "theme.conf").is_file()
    }


def _panel_bg_color(theme_dir: Path) -> str:
    """从 panel.svg 取填充色作为迷你预览底色（图片型主题无法从 theme.conf 取）。"""
    try:
        text = (theme_dir / "panel.svg").read_text(encoding="utf-8")
    except OSError:
        return "#ffffff"
    match = re.search(r'fill:#([0-9a-fA-F]{6})', text)
    return f"#{match.group(1)}" if match else "#ffffff"


def _bundled_full_theme_info(entry: Path) -> ThemeInfo:
    """装配内置完整主题信息：从 theme.conf 解析五色 + panel.svg 底色。

    图片型主题（panel.svg 作底）本不取色（见 _theme_info），但内置主题需要
    有意义的迷你预览，故这里强行解析——key 已知、资源受控，色值可靠。
    """
    palette = parse_palette(entry / "theme.conf")
    background = _panel_bg_color(entry)
    return ThemeInfo(
        key=entry.name,
        name=_theme_display_name(entry),
        source="installer",
        path=entry,
        palette=ThemePalette(
            normal=(palette.normal if palette else "#888888"),
            highlight=(palette.highlight if palette else "#ffffff"),
            highlight_bg=(palette.highlight_bg if palette else "#aaaaaa"),
            background=background,
            border=(palette.border if palette else "#cccccc"),
        ),
        dark=is_dark_color(background) if background else None,
    )


def list_bundled_full_themes() -> list[ThemeInfo]:
    """枚举内置完整主题（资源目录下的每个主题目录）。"""
    if not BUNDLED_FULL_THEMES_DIR.is_dir():
        return []
    return [
        _bundled_full_theme_info(entry)
        for entry in sorted(BUNDLED_FULL_THEMES_DIR.iterdir())
        if entry.is_dir() and (entry / "theme.conf").is_file()
    ]


def split_themes(
    themes: list[ThemeInfo],
) -> tuple[list[ThemeInfo], list[ThemeInfo]]:
    """按「内置 / 用户自装」拆成两份，系统自带主题不进界面（v1.0.19）。

    Deepin / GNOME 自带的 default、default-dark、deepin-* 由桌面环境提供，
    与「换套好看的候选窗」这件事无关：混在列表里只会稀释可选项（用户
    实测时框选了 4 个系统主题要求去掉）。它们仍可通过「恢复系统默认」
    回到出厂状态，功能没有损失。

    返回 (内置主题, 用户自装主题)；两者按 list_themes 原有顺序（内置在前）。
    """
    builtin = [t for t in themes if t.source == "installer"]
    custom = [t for t in themes if t.source == "user"]
    return builtin, custom


def install_bundled_full_theme(key: str, user_dir: Path) -> Path:
    """把内置完整主题整包复制到用户主题目录，返回目标目录。"""
    src = BUNDLED_FULL_THEMES_DIR / key
    if not src.is_dir():
        raise OSError(f"内置主题目录缺失: {key}")
    target = user_dir / key
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    return target


def delete_user_theme(key: str, user_dir: Path) -> Path:
    """删除用户主题目录里的一款主题，返回被删的目录（v1.0.24）。

    主题目录是用户自己往里丢文件夹的地方，所以这里守得比较紧：
    1. key 必须过 `valid_theme_name`（拒绝 `..`、分隔符、绝对路径）；
    2. 目标必须是 user_dir 的**直接**子目录——不允许顺着 key 跨出主题目录；
    3. 目录里必须真的有 theme.conf，不是「看起来像主题」就删；
    4. classicui 编译内置主题与安装器自撰主题一律拒绝（删了也会再生成，
       只是把用户的目录搞脏）。

    符号链接只删链接本身（shutil.rmtree 对 symlink 直接抛错），不会顺着
    链接删掉别处的真实目录。
    """
    if not valid_theme_name(key):
        raise ValueError(f"非法主题名: {key!r}")
    if key in CLASSICUI_BUILTIN_THEMES or key in BUNDLED_THEME_MAP:
        raise ValueError(f"内置主题不可删除: {key}")
    base = Path(user_dir)
    target = base / key
    if target.parent != base:
        raise ValueError(f"目标不在主题目录内: {target}")
    if target.is_symlink():
        target.unlink()
        return target
    if not target.is_dir():
        raise FileNotFoundError(f"主题目录不存在: {target}")
    if not (target / "theme.conf").is_file():
        raise ValueError(f"目录里没有 theme.conf，拒绝删除: {target}")
    shutil.rmtree(target)
    return target


def delete_fallback_theme(
    theme_key: str, conf_path: Path, backup_path: Path, user_dir: Path
) -> str:
    """删掉「当前主题」后该落到哪一款（v1.0.24）。

    优先回到**本安装器首次改写前的原件主题**——用户删掉一款不喜欢的主题，
    期待的是「回到我原来的样子」，而不是 fcitx5 裸的 default（观感突兀，
    还会让人觉得「删了以后变丑了」）。原件主题满足以下两条才采用：
    不是正被删的这款、且现在仍然可用（`theme_exists`）。否则退到编译内置的
    default（fcitx5 恒可解析，永远不会指向不存在的目录）。
    """
    try:
        original = read_classicui(backup_path).get("Theme", "").strip()
    except OSError:
        original = ""
    if original and original != theme_key and theme_exists(original, user_dir):
        return original
    return DEFAULT_THEME_KEY


def theme_exists(theme_key: str, user_dir: Path) -> bool:
    """主题是否可用：classicui 编译内置 / 系统目录 / 用户目录任一命中。

    fcitx5 搜索主题时系统目录（THEMES_SYSTEM_DIR）与用户目录都查（用户
    优先）；此前 CLI 只查用户目录，Deepin 上枚举得到的 default /
    default-dark（位于系统目录）点「切换」报「未知主题」（v1.0.17 真机
    实证）。user_dir 参数为调用方传入的用户主题目录（冒烟可注入重定向）。
    """
    if theme_key in CLASSICUI_BUILTIN_THEMES:
        return True
    if theme_key in bundled_full_theme_keys():
        return True
    return any(
        (base / theme_key / "theme.conf").is_file()
        for base in (Path(user_dir), THEMES_SYSTEM_DIR)
    )


# ---- 路径（模块级函数便于冒烟注入重定向） ----

def fcitx5_conf_path() -> Path:
    return Path.home() / FCITX5_CONF_RELATIVE


def system_themes_dir() -> Path:
    return THEMES_SYSTEM_DIR


def user_themes_dir() -> Path:
    return Path.home() / THEMES_USER_DIR


def theme_backup_path() -> Path:
    return config.USER_DATA_DIR / THEME_BACKUP_RELATIVE


# ---- classicui.conf 读写 ----

def read_classicui(path: Path) -> dict[str, str]:
    """解析 classicui.conf，只取 Theme/DarkTheme/UseDarkTheme 三键。"""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        for key in ("Theme", "DarkTheme", "UseDarkTheme"):
            if stripped.startswith(f"{key}="):
                values[key] = stripped[len(key) + 1:].strip()
    return values


def current_theme_key(conf_path: Path) -> str:
    """当前生效主题：UseDarkTheme=True 时暗色用 DarkTheme，否则 Theme。"""
    values = read_classicui(conf_path)
    if values.get("UseDarkTheme", "").lower() == "true" and values.get("DarkTheme"):
        return values["DarkTheme"]
    return values.get("Theme", "")


def write_theme_setting(conf_path: Path, theme_key: str, backup_path: Path) -> str:
    """把 Theme/DarkTheme 改为 theme_key（保留其他行）。

    首次改写前把原件复制到 backup_path（已存在则不覆盖，保留最初状态）。
    同时改 Theme 与 DarkTheme：无论系统明暗，用户选的主题都生效。
    """
    try:
        original = conf_path.read_text(encoding="utf-8") if conf_path.is_file() else ""
    except OSError as exc:
        raise OSError(f"读取 fcitx5 配置失败: {exc}") from exc
    if not backup_path.exists() and original.strip():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(original, encoding="utf-8")
    lines = original.splitlines() if original else []
    out: list[str] = []
    seen_theme = seen_dark = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Theme="):
            out.append(f"Theme={theme_key}")
            seen_theme = True
        elif stripped.startswith("DarkTheme="):
            out.append(f"DarkTheme={theme_key}")
            seen_dark = True
        else:
            out.append(line)
    if not seen_theme:
        out.append(f"Theme={theme_key}")
    if not seen_dark:
        out.append(f"DarkTheme={theme_key}")
    merged = "\n".join(out).rstrip("\n") + "\n"
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(merged, encoding="utf-8")
    return f"fcitx5 主题已切换为 {theme_key}"


def revert_theme_setting(conf_path: Path, backup_path: Path) -> str:
    """恢复 classicui.conf 原件（无备份则无需恢复）。"""
    if not backup_path.is_file():
        return "未发现本安装器改写的记录，无需恢复"
    try:
        backup_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"读取备份失败: {exc}") from exc
    try:
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        raise OSError(f"恢复 fcitx5 配置失败: {exc}") from exc
    return "已恢复 fcitx5 默认主题配置"


# ---- 主题枚举与内置主题安装 ----

def _theme_display_name(theme_dir: Path) -> str:
    """从 theme.conf 的 [Metadata] Name 读显示名（失败回退目录名）。"""
    conf = theme_dir / "theme.conf"
    try:
        text = conf.read_text(encoding="utf-8")
    except OSError:
        return theme_dir.name
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name="):
            return stripped[len("Name="):].strip() or theme_dir.name
    return theme_dir.name


def _theme_has_image_bg(conf_path: Path) -> bool:
    """theme.conf 的 [InputPanel/Background] 段是否用 Image= 图片作底。

    图片型主题的 Color= 只是兜底色（deepin-dark 实测 #ffffff），不能用来
    判深浅/取预览色（详见 _theme_info docstring）。
    """
    try:
        text = conf_path.read_text(encoding="utf-8")
    except OSError:
        return False
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section == "[InputPanel/Background]" and stripped.startswith("Image="):
            if stripped[len("Image=") :].strip():
                return True
    return False


def list_themes(system_dir: Path, user_dir: Path) -> list[ThemeInfo]:
    """枚举已安装主题（用户目录优先，同名去重）。

    内置完整主题（resources/bundled_themes）无论是否已安装都先列出（source=
    installer），让用户「开箱即用」直接切换；磁盘扫描时跳过这些 key，避免重复。
    """
    themes: list[ThemeInfo] = []
    seen: set[str] = set()
    for bt in list_bundled_full_themes():
        if bt.key in seen:
            continue
        seen.add(bt.key)
        themes.append(bt)
    bundled_full = bundled_full_theme_keys()
    for base, source in ((user_dir, "user"), (system_dir, "system")):
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.name in seen:
                continue
            if not (entry / "theme.conf").is_file():
                continue
            seen.add(entry.name)
            src = (
                "installer"
                if entry.name.startswith("rime-installer-") or entry.name in bundled_full
                else source
            )
            themes.append(_theme_info(entry, src))
    return themes


def _theme_info(entry: Path, source: str) -> ThemeInfo:
    """装配一行主题信息：内置主题用 BUNDLED_THEMES 权威五色，其余解析 theme.conf。

    图片型主题（[InputPanel/Background] 带 Image=，如 deepin 系）不取色：
    其 Color= 只是兜底色（deepin-dark 实测为 #ffffff），按它判深浅会得到
    相反结论（v1.0.16 真机实证：深色主题被判「浅色」）。观感由图片决定，
    交回 palette=None/dark=None，由调用方中性占位预览、不标深浅。

    内置完整主题（key ∈ bundled_full_theme_keys）即便带图片底也走
    _bundled_full_theme_info 强行解析五色，保证迷你预览有意义。
    """
    bundled = bundled_theme(entry.name)
    if bundled is not None:
        return ThemeInfo(
            key=entry.name,
            name=bundled.name,
            source=source,
            path=entry,
            palette=ThemePalette(
                normal=bundled.normal,
                highlight=bundled.highlight,
                highlight_bg=bundled.highlight_bg,
                background=bundled.background,
                border=bundled.border,
            ),
            dark=bundled.dark,
        )
    if entry.name in bundled_full_theme_keys():
        return _bundled_full_theme_info(entry)
    if _theme_has_image_bg(entry / "theme.conf"):
        return ThemeInfo(
            key=entry.name,
            name=_theme_display_name(entry),
            source=source,
            path=entry,
            palette=None,
            dark=None,
        )
    palette = parse_palette(entry / "theme.conf")
    return ThemeInfo(
        key=entry.name,
        name=_theme_display_name(entry),
        source=source,
        path=entry,
        palette=palette,
        dark=is_dark_color(palette.background) if palette else None,
    )


def render_bundled_theme(theme: BundledTheme) -> str:
    """渲染内置主题的 theme.conf（fcitx5 经典扁平格式）。"""
    return "\n".join(
        [
            "[Metadata]",
            f"Name={theme.name}",
            "Version=1",
            "Author=rime-installer",
            f"Description=rime-installer bundled theme ({theme.key})",
            "",
            "[InputPanel]",
            f"NormalColor={theme.normal}",
            f"HighlightColor={theme.highlight}",
            "",
            "[InputPanel/TextMargin]",
            "Left=8",
            "Right=8",
            "Top=6",
            "Bottom=6",
            "",
            "[InputPanel/ContentMargin]",
            "Left=2",
            "Right=2",
            "Top=2",
            "Bottom=2",
            "",
            "[InputPanel/Background]",
            f"Color={theme.background}",
            f"BorderColor={theme.border}",
            "BorderWidth=1",
            "BorderRadius=8",
            "",
            "[InputPanel/Background/Margin]",
            "Left=4",
            "Right=4",
            "Top=4",
            "Bottom=4",
            "",
            "[InputPanel/Highlight]",
            f"Color={theme.highlight_bg}",
            "",
            "[InputPanel/Highlight/Margin]",
            "Left=6",
            "Right=6",
            "Top=4",
            "Bottom=4",
            "",
        ]
    )


def install_bundled_theme(theme: BundledTheme, user_dir: Path) -> Path:
    """把内置主题写入用户主题目录，返回主题目录。"""
    target = user_dir / theme.key
    target.mkdir(parents=True, exist_ok=True)
    (target / "theme.conf").write_text(render_bundled_theme(theme), encoding="utf-8")
    return target


# ---- ibus-rime 全局外观（标记块） ----

@dataclass(frozen=True)
class IbusStyle:
    """ibus_rime.custom.yaml 里的全局外观项（默认值即 ibus-rime 原生）。"""

    horizontal: bool = False
    inline_preedit: bool = True
    color_scheme: str = ""  # 空 = 不设置（取 ibus-rime 默认）


def render_ibus_block(style: IbusStyle) -> str:
    """渲染写入 ibus_rime.custom.yaml 的标记块（缩进在 patch: 之下）。"""
    lines = [
        IBUS_MARK_BEGIN,
        "  style:",
        f"    horizontal: {'true' if style.horizontal else 'false'}",
        f"    inline_preedit: {'true' if style.inline_preedit else 'false'}",
    ]
    if style.color_scheme:
        lines.append(f"    color_scheme: {style.color_scheme}")
    lines.append(IBUS_MARK_END)
    return "\n".join(lines)


def parse_ibus_block(text: str) -> IbusStyle | None:
    """从文件内容反读标记块；无块/字段缺失返回 None 或按默认补。"""
    if IBUS_MARK_BEGIN not in text or IBUS_MARK_END not in text:
        return None
    block = text.split(IBUS_MARK_BEGIN, 1)[1].split(IBUS_MARK_END, 1)[0]
    style = IbusStyle()
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("horizontal:"):
            style = IbusStyle(
                horizontal=stripped[len("horizontal:"):].strip() == "true",
                inline_preedit=style.inline_preedit,
                color_scheme=style.color_scheme,
            )
        elif stripped.startswith("inline_preedit:"):
            style = IbusStyle(
                horizontal=style.horizontal,
                inline_preedit=stripped[len("inline_preedit:"):].strip() == "true",
                color_scheme=style.color_scheme,
            )
        elif stripped.startswith("color_scheme:"):
            style = IbusStyle(
                horizontal=style.horizontal,
                inline_preedit=style.inline_preedit,
                color_scheme=stripped[len("color_scheme:"):].strip(),
            )
    return style


def _merge_block(path: Path, block: str, begin: str, end: str) -> None:
    """把标记块放进文件：已有则替换，无则追加（必要时新建 patch: 段）。

    与 tools/skin_apply.py 同构：块内容带 2 空格缩进，挂在文件既有的
    顶层 patch: 键下；不解析 YAML，只替换自己管理的区块。
    """
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    if begin in original and end in original:
        head, rest = original.split(begin, 1)
        _, tail = rest.split(end, 1)
        merged = head.rstrip("\n") + "\n" + block + "\n" + tail.lstrip("\n")
    elif original.strip():
        lines = original.rstrip("\n").split("\n")
        if any(line.rstrip().endswith("patch:") for line in lines):
            merged = "\n".join(lines) + "\n" + block + "\n"
        else:
            merged = "\n".join(lines) + "\npatch:\n" + block + "\n"
    else:
        merged = "patch:\n" + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8")


def _remove_block(path: Path, begin: str, end: str) -> None:
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if begin not in text or end not in text:
        return
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    merged = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).rstrip("\n")
    path.write_text((merged + "\n") if merged.strip() else "", encoding="utf-8")


def write_ibus_style(rime_dir: Path, style: IbusStyle) -> str:
    path = Path(rime_dir) / IBUS_STYLE_FILE
    _merge_block(path, render_ibus_block(style), IBUS_MARK_BEGIN, IBUS_MARK_END)
    return f"ibus 外观配置已写入 {path}"


def remove_ibus_style(rime_dir: Path) -> str:
    path = Path(rime_dir) / IBUS_STYLE_FILE
    _remove_block(path, IBUS_MARK_BEGIN, IBUS_MARK_END)
    return f"ibus 外观配置已移除 {path}"


def detect_ibus_style(rime_dir: Path) -> IbusStyle | None:
    """从 ibus_rime.custom.yaml 反读当前 ibus 外观（无块返回 None）。"""
    path = Path(rime_dir) / IBUS_STYLE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_ibus_block(text)


# ---- 任务步骤（复用部署引导同款 CLI 驱动） ----

def _cli_argv(*args: str) -> list[str]:
    return [sys.executable or "python3", str(config.APPEARANCE_CLI), *args]


def _restart_fcitx5_step(title: str) -> Step:
    """重启 fcitx5 使 classicui 主题生效（v1.0.16：真机实证必修步）。

    classicui 的主题在 fcitx5 进程启动时加载——只改写 classicui.conf
    不重启，候选窗毫无变化（用户反馈「主题无法切换」的根因）。
    命令与 deploy_guide._start_cmd("fcitx5-rime") 同款：pkill + setsid
    脱离进程组，Step 本身立即返回，runner 正常收尾。

    RIME_INSTALLER_NO_RESTART=1 时跳过重启（冒烟环境专用：避免误杀
    开发/测试机正在使用的输入法框架）。
    """
    script = (
        'if [ "${RIME_INSTALLER_NO_RESTART:-0}" = "1" ]; then\n'
        '  echo "冒烟环境：跳过 fcitx5 重启"\n'
        "else\n"
        "  pkill -x fcitx5 2>/dev/null; sleep 0.3; "
        "setsid fcitx5 -d >/dev/null 2>&1 &\n"
        '  echo "已重启 fcitx5，新主题已生效"\n'
        "fi"
    )
    return Step(title=title, argv=["bash", "-c", script], weight=1)


def build_theme_steps(
    theme_key: str, conf_path: Path, themes_dir: Path, backup_path: Path
) -> list[Step]:
    """切换 fcitx5 主题：安装内置主题（如需要）+ 改写 classicui.conf + 重启生效。"""
    theme = bundled_theme(theme_key)
    title = f"切换候选窗主题：{theme.name if theme else theme_key}"
    return [
        Step(
            argv=_cli_argv(
                "--fcitx5-conf", str(conf_path),
                "--themes-dir", str(themes_dir),
                "--backup", str(backup_path),
                "--theme", theme_key,
            ),
            title=title,
        ),
        _restart_fcitx5_step("重启 fcitx5 使新主题生效"),
    ]


def build_theme_revert_steps(conf_path: Path, backup_path: Path) -> list[Step]:
    return [
        Step(
            argv=_cli_argv(
                "--fcitx5-conf", str(conf_path),
                "--backup", str(backup_path),
                "--revert-theme",
            ),
            title="恢复 fcitx5 默认主题",
        ),
        _restart_fcitx5_step("重启 fcitx5 使默认主题生效"),
    ]


def build_theme_delete_steps(
    theme_key: str,
    conf_path: Path,
    themes_dir: Path,
    backup_path: Path,
    reset_theme: bool,
) -> list[Step]:
    """删除一款用户主题；删的是当前主题时先换一套主题再删目录。

    两件事的顺序不可颠倒：**先改写 classicui.conf，再删目录**。反过来的话，
    写配置一旦失败就留下「conf 指向已删主题」的残局，候选窗会掉回
    classicui 的硬编码默认值、且用户再也点不到那款主题。
    改写落点由 `delete_fallback_theme()` 决定（原件主题 → default 兜底）。

    只有真正动了 classicui.conf（reset_theme）才需要重启 fcitx5：删除一款
    没在用的主题不影响当前候选窗，此时不打断用户输入。
    """
    args = [
        "--fcitx5-conf", str(conf_path),
        "--themes-dir", str(themes_dir),
        "--backup", str(backup_path),
        "--delete-theme", theme_key,
    ]
    if reset_theme:
        args.append("--reset-theme-to-default")
    steps = [Step(argv=_cli_argv(*args), title=f"删除候选窗主题：{theme_key}")]
    if reset_theme:
        steps.append(_restart_fcitx5_step("重启 fcitx5 使默认主题生效"))
    return steps


def build_ibus_style_steps(rime_dir: Path, style: IbusStyle) -> list[Step]:
    spec = (
        f"horizontal={'true' if style.horizontal else 'false'},"
        f"inline_preedit={'true' if style.inline_preedit else 'false'},"
        f"color_scheme={style.color_scheme}"
    )
    return [
        Step(
            argv=_cli_argv("--ibus-conf", str(rime_dir), "--ibus-style", spec),
            title="写入 ibus 外观配置",
        )
    ]


def build_ibus_revert_steps(rime_dir: Path) -> list[Step]:
    return [
        Step(
            argv=_cli_argv("--ibus-conf", str(rime_dir), "--revert-ibus-style"),
            title="移除 ibus 外观配置",
        )
    ]
