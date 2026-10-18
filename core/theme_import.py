"""从 GitHub 仓库导入 fcitx5 主题（v1.0.21 起，v1.0.22 扩展为多形态）。

v1.0.14 的「主题市场」被砍掉的是**固定源目录**，不是这套能力：
下载 tarball → 找到 theme.conf → 整目录拷进用户主题目录。
这里改由用户粘贴的链接驱动，且**一次全导**（不做勾选），延续 v1.0.16
之后的极简路线：不记账、不做更新检查、不引 market 概念。

上游形态（2026-10-18 实测 27 个高星仓库，覆盖 5 类）：
- L1 根目录单主题：仓库根就有 theme.conf，图片资产也在根
  （drbbr/fcitx5-dracula-theme、iovxw、Vrchz/Psionics-Remix、
   escape0707/adwaita-dark、skykeyjoker/Bing-Input-Color、weearc）；
- L2 顶层多目录：每个含 theme.conf 的子目录 = 一款
  （thep0y/candlelight 10、sxqsfun/sogou 12、sanweiya/mellow 10…）；
- L3 嵌套子目录：theme.conf 在第二层子目录里
  （catppuccin/fcitx5 的 src/<variant>/，共 56 款）；
- 变体模式：根级 theme-*.conf + 根级共享图片，无 theme.conf
  （hosxy/Fcitx5-Material-Color 10、pu-007/gruvbox 6）；
- 非桌面主题：fcitx5-android 的 JSON 主题、android/macos 配色方案、
  需要构建的源码模板（tankb52、fcitx-contrib、scratch-er）——
  枚举为空，由 `diagnose_repo()` 给出「为什么装不了」。

安装 = 整目录拷贝（图片型主题的 `Image=panel.png` 是相对引用，只拷
theme.conf 会缺图）；根目录/变体模式只带 theme.conf + 被引用的图片，
避免把 README / 截图 / .psd 一起搬进去。重名先清后拷，重复导入即更新。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: fcitx5 classicui 主题配置文件名
THEME_CONF = "theme.conf"
#: 可被主题引用的图片扩展名（根目录/变体模式按需挑选）
_ASSET_SUFFIXES = (".png", ".svg", ".jpg", ".jpeg", ".webp")
#: 设计源文件，永远不拷进主题目录（大且无用）
_SKIP_SUFFIXES = (".psd", ".psb", ".xcf", ".ai", ".sketch", ".kra", ".afdesign")
#: 主题可嵌套的最大层数：1=仓库根，2=root/*/，3=root/*/*/（catppuccin 的 src/<v>/）
_MAX_LAYER = 3

#: theme.conf 里引用图片的键（Image= / OverlayImage= 等，可能带缩进）
_IMAGE_RE = re.compile(r"^([A-Za-z]*Image)\s*=\s*(\S+)\s*$")


@dataclass(frozen=True)
class ImportTheme:
    """仓库里枚举出的一款主题（安装单位）。"""

    key: str  # 安装后的目录名，也是 classicui.conf 里 Theme= 的取值
    name: str  # 显示名（[Metadata] Name，缺失回退 key）
    rel_dir: str = ""  # 子目录模式：仓库内相对目录
    variant_file: str = ""  # 变体模式：仓库根级 theme-<x>.conf 文件名
    root_theme: bool = False  # 根目录即一款主题（theme.conf 在仓库根）


def parse_theme_name(conf_path: Path) -> str:
    """取 theme.conf 里 [Metadata] Name（缺失返回空串）。"""
    try:
        text = conf_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    in_meta = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_meta = stripped == "[Metadata]"
            continue
        if not in_meta or not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == "Name" and value.strip():
            return value.strip()
    return ""


def _variant_suffix(conf: Path) -> str:
    """theme-blue.conf → blue。"""
    stem = conf.stem[len("theme-"):] if conf.stem.startswith("theme-") else conf.stem
    return stem or conf.stem


def _variant_default_name(conf: Path) -> str:
    """无 [Metadata] Name 时的兜底显示名：theme-blue.conf → Blue。"""
    suffix = _variant_suffix(conf)
    return f"{suffix[:1].upper()}{suffix[1:]}" if suffix else conf.stem


def _sanitize_key(text: str) -> str:
    """把显示名转成可用的主题目录名（保留中文，空白/分隔符换成 -）。"""
    cleaned = re.sub(r"[\s/\\\0]+", "-", text.strip()).strip("-.")
    return cleaned if valid_theme_name(cleaned) else ""


def _theme_dirs_at_layer(root: Path, layer: int) -> list[Path]:
    """取某一层的主题目录：layer=1 → root 自身；2 → root/*/；3 → root/*/*/。"""
    if layer == 1:
        return [root] if (root / THEME_CONF).is_file() else []
    if layer == 2:
        return sorted(
            p
            for p in root.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and (p / THEME_CONF).is_file()
        )
    found: list[Path] = []
    for parent in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        found.extend(
            p
            for p in sorted(parent.iterdir())
            if p.is_dir() and not p.name.startswith(".") and (p / THEME_CONF).is_file()
        )
    return found


def _enumerate_variants(root: Path, taken: set[str] | None = None) -> list[ImportTheme]:
    """变体模式：根级 theme-*.conf，每个文件一款，共享根级图片资产。

    实测两类上游：hosxy / gruvbox 的多个变体在 [Metadata] 里全叫同一个
    Name，直接拿 Name 当目录名会互相覆盖（10 款只剩 1 款）；也有 Name 本就
    互不相同的。所以先按 Name 定 key，**只有撞名的那些**才补变体后缀。
    """
    confs = [p for p in sorted(root.glob("theme-*.conf")) if p.is_file()]
    if not confs:
        return []

    rows = []
    for conf in confs:
        base = parse_theme_name(conf) or _variant_default_name(conf)
        rows.append((conf, base, _sanitize_key(base) or _variant_suffix(conf)))

    counts: dict[str, int] = {}
    for _conf, _base, key in rows:
        counts[key] = counts.get(key, 0) + 1

    used: set[str] = set(taken or ())
    themes: list[ImportTheme] = []
    for conf, base, key in rows:
        name = base
        # 撞名有两种：变体之间同名（counts>1），或与根目录那款同名（key in used）。
        # 两种都要补变体后缀，否则后装的会把先装的覆盖掉。
        if counts[key] > 1 or key in used:
            suffix = _variant_suffix(conf)
            with_suffix = _sanitize_key(f"{base}-{suffix}")
            if (
                with_suffix
                and with_suffix not in used
                and suffix.casefold() != base.casefold()
            ):
                key, name = with_suffix, f"{base} ({suffix})"
        while key in used:  # 极端兜底：连后缀也重名时加序号
            key = f"{key}-{len(used) + 1}"
        used.add(key)
        themes.append(ImportTheme(key=key, name=name, variant_file=conf.name))
    themes.sort(key=lambda t: t.name.casefold())
    return themes


def enumerate_themes(root: Path) -> list[ImportTheme]:
    """枚举仓库里可安装的主题（按显示名排序）。

    分层取「最浅的一层」：根目录有 theme.conf 就当单主题仓库（其子目录
    多是 assets/截图）；否则看顶层子目录；再看第二层（catppuccin 的
    src/<variant>/）。都没有才退到根级变体模式。分层而非全量递归，
    是为了避免把 example/、template/ 之类目录里的示例配置也当成主题。
    """
    if not root.is_dir():
        return []

    for layer in range(1, _MAX_LAYER + 1):
        dirs = _theme_dirs_at_layer(root, layer)
        if not dirs:
            continue
        themes: list[ImportTheme] = []
        used: set[str] = set()
        for entry in dirs:
            conf = entry / THEME_CONF
            display = parse_theme_name(conf)
            if layer == 1:
                key = _sanitize_key(display) or _sanitize_key(root.name) or "imported-theme"
                used.add(key)
                themes.append(
                    ImportTheme(
                        key=key, name=display or key, root_theme=True
                    )
                )
                continue
            key = entry.name
            if not valid_theme_name(key) or key in used:
                continue
            used.add(key)
            themes.append(
                ImportTheme(
                    key=key,
                    name=display or key,
                    rel_dir=entry.relative_to(root).as_posix(),
                )
            )
        if layer == 1:
            # 根目录既是一款主题，也可能同时带 theme-<x>.conf 变体
            # （pu-007/gruvbox 形态：theme.conf + theme-{aqua,blue,…}.conf）
            themes.extend(_enumerate_variants(root, used))
        if themes:
            themes.sort(key=lambda t: t.name.casefold())
            return themes

    return _enumerate_variants(root)


def diagnose_repo(root: Path) -> str:
    """枚举为空时判断「为什么装不了」，返回 i18n 键（识别不出返回空串）。

    只做只读嗅探，不写盘；用于把「这个仓库里没有 fcitx5 主题」升级成
    「这是手机端主题 / 这是配色方案 / 这是需要构建的源码」。
    """
    if not root.is_dir():
        return ""

    # 1) fcitx5-android 主题：JSON 里带 isDark + keyBackgroundColor 之类字段
    candidates = [p for p in root.glob("*.json")][:5]
    candidates += [p for p in (root / "json").glob("*.json")][:5] if (root / "json").is_dir() else []
    for path in candidates:
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            continue
        if '"isDark"' in head and ('"keyBackgroundColor"' in head or '"barColor"' in head):
            return "msg.theme_android"

    # 2) fcitx5-android / macOS 的配色方案：[LightMode]/[DarkMode] + OverrideDefault
    for conf in list(root.rglob("*.conf"))[:40]:
        try:
            text = conf.read_text(encoding="utf-8", errors="ignore")[:6000]
        except OSError:
            continue
        if ("[LightMode]" in text or "[DarkMode]" in text) and "OverrideDefault" in text:
            return "msg.theme_color_scheme"

    # 3) 需要构建的源码模板：build.py / *.tera / config/*.conf.in
    if (root / "build.py").is_file():
        return "msg.theme_source_only"
    if any(root.glob("config/*.conf.in")) or any(root.rglob("*.tera")):
        return "msg.theme_source_only"
    return ""


def valid_theme_name(name: str) -> bool:
    """主题目录名安全校验：拒绝空 / `.` / `..` / 绝对路径 / 分隔符 / 前置横线。"""
    if not name or name in (".", ".."):
        return False
    if name.startswith(("/", "~", "-")):
        return False
    return not any(ch in name for ch in ("/", "\\", "\0"))


def _image_refs(conf_text: str) -> list[str]:
    """取出 theme.conf 里所有相对图片引用（跳过注释与绝对路径）。"""
    refs: list[str] = []
    for line in conf_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _IMAGE_RE.match(stripped)
        if not match:
            continue
        value = match.group(2)
        if value.startswith(("/", "~")):
            continue
        refs.append(value)
    return refs


def _copy_root_assets(root: Path, target: Path, conf_text: str) -> list[Path]:
    """根目录/变体模式：只带主题真正引用的图片，引用不到才回退到「全部图片」。

    skykeyjoker 那类仓库根目录有 50+ 张 highlight 预设图 + .psd，
    全拷既慢又脏；按 theme.conf 的 Image= 精确取用即可（实测 4 张够）。
    """
    copied: list[Path] = []
    refs = _image_refs(conf_text)
    for ref in refs:
        pure = PurePosixPath(ref)
        if pure.is_absolute() or ".." in pure.parts:
            continue
        src = root.joinpath(*pure.parts)
        if not src.is_file():
            continue
        dst = target.joinpath(*pure.parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    if copied:
        return copied
    for item in sorted(root.iterdir()):
        if not item.is_file():
            continue
        suffix = item.suffix.lower()
        if suffix in _SKIP_SUFFIXES or suffix not in _ASSET_SUFFIXES:
            continue
        dst = target / item.name
        shutil.copy2(item, dst)
        copied.append(dst)
    return copied


def install_theme(root: Path, theme: ImportTheme, themes_dir: Path) -> list[Path]:
    """把一款主题从解压目录拷进用户主题目录，返回落盘文件列表。

    子目录模式整目录拷贝（自带 svg/png 资产）；根目录模式与变体模式只
    拷 theme.conf + 被引用的图片。重名先清空再拷，避免旧版残留文件与
    新版本混在一起（例如主题去掉了某张 svg，旧图还在）。
    """
    if not valid_theme_name(theme.key):
        raise ValueError(f"非法主题目录名: {theme.key!r}")
    target = themes_dir / theme.key
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    if theme.variant_file or theme.root_theme:
        src_conf = root / (theme.variant_file or THEME_CONF)
        if not src_conf.is_file():
            raise FileNotFoundError(f"主题配置缺失: {src_conf}")
        try:
            conf_text = src_conf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            conf_text = ""
        dst = target / THEME_CONF
        shutil.copy2(src_conf, dst)
        files.append(dst)
        files.extend(_copy_root_assets(root, target, conf_text))
        return files

    src = root / theme.rel_dir
    if not src.is_dir():
        raise FileNotFoundError(f"主题目录缺失: {src}")
    for item in sorted(src.iterdir()):
        dst = target / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    files.extend(p for p in target.rglob("*") if p.is_file())
    return files


def install_all(
    root: Path, themes: list[ImportTheme], themes_dir: Path
) -> tuple[list[str], list[str]]:
    """批量安装，返回 (成功目录名, 失败说明)；单款失败不影响其余。"""
    themes_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    failed: list[str] = []
    for theme in themes:
        try:
            install_theme(root, theme, themes_dir)
        except (OSError, ValueError) as exc:
            failed.append(f"{theme.key}: {exc}")
            continue
        installed.append(theme.key)
    return installed, failed
