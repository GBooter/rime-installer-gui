"""皮肤（配色）增强包 —— F15 的皮肤部分（Phase 6d / v1.0.0）。

与词库增强包不同，皮肤不需要下载任何仓库：RIME 的配色是**用户目录里的
一段配置**，写进 `default.custom.yaml` 后由各方案继承。因此：

- 不经过 plum / aria2，无网络依赖；
- 应用后再触发一次「重新部署」即可生效（与部署引导复用同一套步骤）；
- 卸载即移除本安装器写入的配置块，其余用户配置原样保留。

写入用「标记块」而非 YAML 解析：项目不引入 PyYAML（deb 也要少一个依赖），
纯文本只替换自己管理的区块，绝不解析或重写用户已有的 YAML。

颜色值沿用 RIME 社区常见写法 `0xBBGGRR`（Windows COLORREF 顺序），
与 RGB 直觉相反，选取时已按此换算。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import config
from core.runner import Step

#: 皮肤配置写入的文件（用户目录下）
SKIN_FILE = "default.custom.yaml"

MARK_BEGIN = "# >>> rime-installer: skin >>>"
MARK_END = "# <<< rime-installer: skin <<<"


@dataclass(frozen=True)
class Skin:
    """一套配色预设。"""

    key: str
    name: str
    intro: str
    #: 直接映射为 RIME preset_color_schemes 下的字段
    colors: dict[str, str]


#: 配色方案 ID（写入配置里的键名，固定不变，便于卸载与重复应用）
_SCHEME_ID = "rime_installer_skin"

SKINS: tuple[Skin, ...] = (
    Skin(
        key="skin-ink",
        name="墨黑（深色）",
        intro="纯黑底白字的极简深色配色，长时间输入不易疲劳，适合暗色桌面环境",
        colors={
            "name": "墨黑",
            "text_color": "0xffffff",
            "back_color": "0x000000",
            "border_color": "0x333333",
            "candidate_text_color": "0xe0e0e0",
            "comment_text_color": "0x808080",
            "hilited_text_color": "0xffffff",
            "hilited_back_color": "0x333333",
            "hilited_candidate_text_color": "0xffffff",
            "hilited_candidate_back_color": "0x1a1a1a",
            "label_color": "0x808080",
        },
    ),
    Skin(
        key="skin-paper",
        name="素白（浅色）",
        intro="白底黑字的经典浅色配色，接近系统原生观感，适合明亮环境与外接投影",
        colors={
            "name": "素白",
            "text_color": "0x000000",
            "back_color": "0xffffff",
            "border_color": "0xd0d0d0",
            "candidate_text_color": "0x333333",
            "comment_text_color": "0x808080",
            "hilited_text_color": "0x000000",
            "hilited_back_color": "0xe1e1e1",
            "hilited_candidate_text_color": "0x000000",
            "hilited_candidate_back_color": "0xf2f2f2",
            "label_color": "0x808080",
        },
    ),
    Skin(
        key="skin-deep",
        name="深海（蓝调）",
        intro="低饱和深蓝配色，候选与高亮层次分明，兼顾暗色环境的柔和观感",
        colors={
            "name": "深海",
            "text_color": "0xffffff",
            # 蓝调 #102a43 按 0xBBGGRR 写作 0x432a10
            "back_color": "0x432a10",
            "border_color": "0x5a3d1c",
            "candidate_text_color": "0xd8e0e8",
            "comment_text_color": "0x8a9aa8",
            "hilited_text_color": "0xffffff",
            "hilited_back_color": "0x5a3d1c",
            "hilited_candidate_text_color": "0xffffff",
            "hilited_candidate_back_color": "0x2f4a63",
            "label_color": "0x8a9aa8",
        },
    ),
    # ---- v1.0.7 新增：扩充至 6 款（真机反馈「只有 3 个感觉少」） ----
    Skin(
        key="skin-bamboo",
        name="青竹（浅绿）",
        intro="淡雅浅绿底色配墨绿文字，清新护眼，适合长时间码字的浅色环境",
        colors={
            "name": "青竹",
            "text_color": "0x24331c",
            # 米绿 #eef5ea → 0xeaf5ee
            "back_color": "0xeaf5ee",
            "border_color": "0xc4dcc8",
            "candidate_text_color": "0x2f4a33",
            "comment_text_color": "0x7a9682",
            "hilited_text_color": "0x1c3324",
            "hilited_back_color": "0xcfe8d2",
            "hilited_candidate_text_color": "0x14261a",
            "hilited_candidate_back_color": "0xdff0e0",
            "label_color": "0x7a9682",
        },
    ),
    Skin(
        key="skin-violet",
        name="暮山紫（深色）",
        intro="深紫底淡紫字，夜间氛围感强，与深蓝深海互补的另一款暗色选择",
        colors={
            "name": "暮山紫",
            "text_color": "0xffeaf0",
            # 深紫 #241a33 → 0x331a24
            "back_color": "0x331a24",
            "border_color": "0x4a2f47",
            "candidate_text_color": "0xe4d9f2",
            "comment_text_color": "0x9a8ab0",
            "hilited_text_color": "0xffffff",
            "hilited_back_color": "0x4a2f47",
            "hilited_candidate_text_color": "0xffffff",
            "hilited_candidate_back_color": "0x5c3f66",
            "label_color": "0x9a8ab0",
        },
    ),
    Skin(
        key="skin-amber",
        name="暖橙（浅色）",
        intro="米白底暖棕字、橙色高亮，柔和温暖，白天不刺眼、投影可读性好",
        colors={
            "name": "暖橙",
            "text_color": "0x1f2b3d",
            # 米白 #fff6ec → 0xecf6ff
            "back_color": "0xecf6ff",
            "border_color": "0xe0c8a8",
            "candidate_text_color": "0x3d2b1f",
            "comment_text_color": "0xa08060",
            "hilited_text_color": "0x2b1a0f",
            "hilited_back_color": "0xc2e3ff",
            "hilited_candidate_text_color": "0x1f120a",
            "hilited_candidate_back_color": "0xd2ecff",
            "label_color": "0xa08060",
        },
    ),
)

SKIN_MAP: dict[str, Skin] = {s.key: s for s in SKINS}


def skin_by_key(key: str) -> Skin | None:
    return SKIN_MAP.get(key)


def build_apply_steps(skin: Skin, rime_dir: Path, frontend: str) -> list[Step]:
    """应用皮肤：写配置块 → 重新部署（复用部署引导，setsid 脱离进程组）。"""
    from core.deploy_guide import build_redeploy_steps

    steps = [
        Step(
            argv=[
                sys.executable or "python3",
                str(config.SKIN_CLI),
                str(rime_dir),
                skin.key,
            ],
            title=f"写入皮肤配置：{skin.name}",
        )
    ]
    steps.extend(build_redeploy_steps(frontend, rime_dir))
    return steps


def build_revert_steps(rime_dir: Path, frontend: str) -> list[Step]:
    """移除皮肤配置块并重新部署。"""
    from core.deploy_guide import build_redeploy_steps

    steps = [
        Step(
            argv=[
                sys.executable or "python3",
                str(config.SKIN_CLI),
                str(rime_dir),
                "--revert",
            ],
            title="移除皮肤配置",
        )
    ]
    steps.extend(build_redeploy_steps(frontend, rime_dir))
    return steps


def render_block(skin: Skin) -> str:
    """渲染写入 default.custom.yaml 的标记块（含缩进，位于 patch: 之下）。

    `# key:` 注释行供 detect_active_skin 反读当前皮肤（v1.0.12）；
    旧版本写入的块没有该行，反读时回退到 name 字段匹配。
    """
    lines = [MARK_BEGIN, f"  # key: {skin.key}", "  preset_color_schemes:", f"    {_SCHEME_ID}:"]
    for field, value in skin.colors.items():
        lines.append(f"      {field}: {value}")
    lines.append("  style:")
    lines.append(f"    color_scheme: {_SCHEME_ID}")
    lines.append(MARK_END)
    return "\n".join(lines)


#: 皮肤显示名（colors["name"]）→ key：旧标记块（无 # key 注释）的反读回退
_NAME_KEYS: dict[str, str] = {s.colors.get("name", ""): s.key for s in SKINS}


def _parse_block(text: str) -> tuple[str, bool]:
    """解析 default.custom.yaml 内容，返回 (皮肤 key 或 "", 是否挂载 color_scheme)。

    只认本安装器标记块内的字段，不解析整个 YAML；块存在但 color_scheme
    被用户改指其他方案时视为「未使用安装器皮肤」。
    """
    if MARK_BEGIN not in text or MARK_END not in text:
        return "", False
    block = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0]
    key = ""
    mounted = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("# key:"):
            key = stripped[len("# key:"):].strip()
        elif stripped.startswith("name:") and not key:
            key = _NAME_KEYS.get(stripped[len("name:"):].strip(), "")
        elif stripped.startswith("color_scheme:"):
            if stripped[len("color_scheme:"):].strip() == _SCHEME_ID:
                mounted = True
    return key, mounted


def detect_active_skin(rime_dir: Path) -> Skin | None:
    """从用户目录实际配置反读当前生效的皮肤（v1.0.12）。

    UI「当前皮肤」标识以本函数为准而非 manifest 记账——用户可能手动
    编辑过 default.custom.yaml，manifest 会说谎。文件不存在/无标记块/
    未挂载 color_scheme 均返回 None。
    """
    path = Path(rime_dir) / SKIN_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    key, mounted = _parse_block(text)
    if not key or not mounted:
        return None
    return skin_by_key(key)
