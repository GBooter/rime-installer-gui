"""方案目录：官方 preset / 官方单项 / 热门第三方 + 自定义规格解析。

每个方案带介绍与优势（F5：方案卡片旁显示简单介绍和优势）；
多包方案（如官方 preset）用 packages 元组表达，rime-install 目标为 ":preset"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 官方包名（plum preset/extra 列表，短名 → rime/rime-<name>）
_OFFICIAL_PRESET = ("prelude", "essay", "luna-pinyin", "terra-pinyin", "stroke", "cangjie", "bopomofo", "quick")


@dataclass(frozen=True)
class PackageRef:
    """一个 GitHub 上的 rime 包（user/repo@branch:recipe）。

    branch 留空（""）= 跟随仓库默认分支（v1.0.20）：codeload 侧用 /tar.gz/HEAD
    由 GitHub 解析默认分支，plum 侧不带 @branch（clone 默认分支）。
    自定义入口一律留空——GitHub 主分支 master/main 混杂，硬编码任何一边都会
    让另一半仓库直接 404（thep0y/fcitx5-themes-candlelight 实证）。
    """

    user: str
    repo: str
    branch: str = ""
    recipe: str = ""

    @property
    def is_auto_branch(self) -> bool:
        return not self.branch

    @property
    def spec(self) -> str:
        """rime-install 包规格。"""
        base = f"{self.user}/{self.repo}"
        if self.branch and self.branch != "master":
            base += f"@{self.branch}"
        if self.recipe:
            base += f":{self.recipe}"
        return base

    @property
    def pkg_dir(self) -> str:
        """plum/package/ 下的相对目录。

        plum 的 install-packages.sh：package_dir = package/<user>/<repo 去掉 rime- 前缀>，
        例如 rime/rime-luna-pinyin → package/rime/luna-pinyin，iDvel/rime-ice → package/iDvel/ice。
        预置解压目标必须与此一致，rime-install 才会跳过 clone。
        """
        name = self.repo[len("rime-"):] if self.repo.startswith("rime-") else self.repo
        return f"{self.user}/{name}"

    @property
    def label(self) -> str:
        suffix = f"@{self.branch}" if self.branch and self.branch != "master" else ""
        return f"{self.user}/{self.repo}{suffix}"


@dataclass(frozen=True)
class Scheme:
    key: str
    name: str
    category: str  # official / third-party
    intro: str
    advantages: tuple[str, ...]
    packages: tuple[PackageRef, ...]
    install_target: str = ""  # rime-install 目标；缺省用各包 spec 拼接
    size_hint: str = ""
    #: 非空表示这是皮肤（配色）条目：不下载仓库，只写用户目录配置
    skin_key: str = ""

    @property
    def is_skin(self) -> bool:
        return bool(self.skin_key)

    @property
    def source(self) -> str:
        if self.is_skin:
            return "内置配色"
        if not self.packages:
            return "—"
        if len(self.packages) == 1:
            return self.packages[0].label
        return f"{len(self.packages)} 个官方包"

    @property
    def targets(self) -> list[str]:
        """rime-install 的目标参数列表（preset 等合集为单目标 :preset）。"""
        if self.install_target:
            return [self.install_target]
        return [p.spec for p in self.packages]


def _official(name: str) -> PackageRef:
    return PackageRef("rime", f"rime-{name}")


SCHEMES: tuple[Scheme, ...] = (
    # ---- 官方合集 ----
    Scheme(
        key="official:preset",
        name="官方 preset（推荐新手）",
        category="official",
        intro="RIME 官方预设合集：明月拼音、地球拼音、五笔画、仓颉、注音、速成等 8 个核心包一次装齐",
        advantages=("开箱即用，无需折腾", "官方维护，稳定优先", "覆盖拼音/形码/注音主流需求"),
        packages=tuple(_official(n) for n in _OFFICIAL_PRESET),
        install_target=":preset",
        size_hint="约 1.5 MB",
    ),
    # ---- 官方单项 ----
    Scheme(
        key="official:luna-pinyin",
        name="明月拼音",
        category="official",
        intro="RIME 最具代表性的官方拼音方案，整句输入，行为可预期",
        advantages=("官方标准，文档齐全", "整句输入引擎成熟", "搭配 prelude + essay 即为完整拼音环境"),
        packages=(_official("luna-pinyin"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:terra-pinyin",
        name="地球拼音",
        category="official",
        intro="带声调的汉语拼音方案（拼音+声母韵母+声调拼合），适合注音学习与教学场景",
        advantages=("支持声调过滤，候选更准", "适合普通话教学", "与明月拼音互补"),
        packages=(_official("terra-pinyin"),),
        size_hint="约 200 KB",
    ),
    Scheme(
        key="official:prelude",
        name="序幕（prelude）",
        category="official",
        intro="官方基础配置包：default.yaml、符号表等公共配置，是其他方案的基础依赖",
        advantages=("其他官方方案的前置依赖", "提供默认快捷键与符号输入"),
        packages=(_official("prelude"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:essay",
        name="八股文（essay）",
        category="official",
        intro="官方现代汉语词库，为拼音方案提供基础词频与词组",
        advantages=("拼音方案标配词库", "与 luna-pinyin 配合效果最佳"),
        packages=(_official("essay"),),
        size_hint="约 1 MB",
    ),
    Scheme(
        key="official:double-pinyin",
        name="双拼",
        category="official",
        intro="官方双拼方案集，内置自然码、小鹤、微软、智能ABC、紫光、搜狗等布局",
        advantages=("多种双拼布局一键切换", "基于明月拼音，整句质量有保障"),
        packages=(_official("double-pinyin"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:wubi",
        name="五笔",
        category="official",
        intro="官方五笔 86 方案，形码用户的老朋友",
        advantages=("86 版编码，资料最多", "低重码，打字快"),
        packages=(_official("wubi"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:cangjie",
        name="仓颉",
        category="official",
        intro="官方仓颉五代方案，繁体用户常用形码",
        advantages=("繁体输入标准方案", "与 quick 速成互补"),
        packages=(_official("cangjie"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:quick",
        name="速成",
        category="official",
        intro="仓颉速成（取首末码），降低仓颉学习门槛",
        advantages=("仓颉入门友好", "两码一字，上手快"),
        packages=(_official("quick"),),
        size_hint="约 50 KB",
    ),
    Scheme(
        key="official:stroke",
        name="五笔画",
        category="official",
        intro="横竖撇折捺五笔画输入，适合不会拼音、年长用户与触摸屏",
        advantages=("零学习成本", "手机/平板友好"),
        packages=(_official("stroke"),),
        size_hint="约 50 KB",
    ),
    Scheme(
        key="official:bopomofo",
        name="注音",
        category="official",
        intro="台湾注音输入方案（ㄅㄆㄇㄈ）",
        advantages=("注音用户标准选择", "支持声调候选用字"),
        packages=(_official("bopomofo"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:combo-pinyin",
        name="宫保拼音",
        category="official",
        intro="兼容分号口令的拼音方案，RIME 早期经典",
        advantages=("支持口令造词", "老用户情怀之选"),
        packages=(_official("combo-pinyin"),),
        size_hint="约 50 KB",
    ),
    Scheme(
        key="official:array",
        name="行列三十",
        category="official",
        intro="行列 30 键形码方案，台湾流行输入法之一",
        advantages=("重码率低", "适合形码进阶用户"),
        packages=(_official("array"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:cantonese",
        name="粤语拼音",
        category="official",
        intro="官方粤语拼音方案（粤拼）",
        advantages=("粤语口语输入", "支持粤语词组"),
        packages=(_official("cantonese"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:jyutping",
        name="粤拼（Jyutping）",
        category="official",
        intro="香港语言学学会粤语拼音方案",
        advantages=("学界标准拼式", "与 cantonese 方案互补"),
        packages=(_official("jyutping"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:emoji",
        name="Emoji",
        category="official",
        intro="为任意方案增加 Emoji 输入支持（/fs 等口令）",
        advantages=("口令输入 Emoji", "与拼音方案叠加使用"),
        packages=(_official("emoji"),),
        size_hint="约 50 KB",
    ),
    Scheme(
        key="official:middle-chinese",
        name="中古汉语拼音",
        category="official",
        intro="中古汉语拼音输入，语言学爱好者向",
        advantages=("学术向拼音方案", "支持中古声韵调"),
        packages=(_official("middle-chinese"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:soutzoe",
        name="上海吴语",
        category="official",
        intro="上海话（吴语）拼音方案",
        advantages=("吴语输入", "方言保护向"),
        packages=(_official("soutzoe"),),
        size_hint="约 100 KB",
    ),
    Scheme(
        key="official:wugniu",
        name="吴语（老派拼式）",
        category="official",
        intro="上海话老派拼式方案，与 soutzoe 二选一",
        advantages=("兼容老派用户习惯", "吴语词组"),
        packages=(_official("wugniu"),),
        size_hint="约 100 KB",
    ),
    # ---- 热门第三方 ----
    Scheme(
        key="iDvel/rime-ice",
        name="雾凇拼音 rime-ice",
        category="third-party",
        intro="目前最活跃的社区拼音方案集：超大词库、云输入、中英混合、Emoji、双拼全布局，开箱即用",
        advantages=(
            "词库持续更新，网络热词覆盖快",
            "长句优先 + 云输入，整句准确率高",
            "英文/数字/日期增强输入",
            "内置小鹤/自然码/微软等双拼布局",
            "配置精简，升级省心",
        ),
        # 注意：rime-ice 默认分支为 main（codeload master 404，2026-10-13 实测）
        packages=(PackageRef("iDvel", "rime-ice", branch="main"),),
        size_hint="约 30 MB",
    ),
    Scheme(
        key="gaboolic/rime-frost",
        name="白霜拼音 rime-frost",
        category="third-party",
        intro="基于雾凇的纯净版：裁剪掉大型词库与部分增强，追求简洁、低占用与稳定",
        advantages=("体积小巧，部署更快", "候选更克制，误上屏少", "适合喜欢简洁的用户"),
        packages=(PackageRef("gaboolic", "rime-frost"),),
        size_hint="约 5 MB",
    ),
    # ---- 词库/皮肤增强包（F15：与方案解耦的可选组件） ----
    Scheme(
        key="addon:rime-dict",
        name="词库增强 rime-dict",
        category="addon",
        intro="社区词库增强包：在现有方案基础上扩充专业、网络与新兴词库，安装后需在方案中挂载并重新部署",
        advantages=("词库覆盖面广，按需取用", "与官方方案、雾凇、白霜均兼容", "单独安装，不影响已有配置"),
        packages=(PackageRef("Iorest", "rime-dict"),),
        size_hint="约 20 MB",
    ),
    Scheme(
        key="addon:rime-pinyin-simp",
        name="拼音反查词库 rime-pinyin-simp",
        category="addon",
        intro="简体拼音反查词库：以词定字、反查笔画出字的常用基础依赖",
        advantages=("反查功能的基础依赖", "简体字表，覆盖全面", "体积小，部署快"),
        packages=(PackageRef("rime", "rime-pinyin-simp"),),
        size_hint="约 2 MB",
    ),
    # ---- 皮肤（F15 皮肤部分：内置配色，不下载仓库） ----
    Scheme(
        key="addon:skin-ink",
        name="皮肤 · 墨黑（深色）",
        category="skin",
        intro="纯黑底白字的极简深色配色，写入用户目录 default.custom.yaml 后立即重新部署生效；卸载即移除，不影响你的其他配置",
        advantages=("无需下载，秒级生效", "暗色环境长时间输入不刺眼", "可随时切换或移除"),
        packages=(),
        skin_key="skin-ink",
        size_hint="内置",
    ),
    Scheme(
        key="addon:skin-paper",
        name="皮肤 · 素白（浅色）",
        category="skin",
        intro="白底黑字经典浅色配色，接近系统原生观感，适合明亮环境与外接投影",
        advantages=("无需下载，秒级生效", "观感接近系统原生", "投影/截图场景更清晰"),
        packages=(),
        skin_key="skin-paper",
        size_hint="内置",
    ),
    Scheme(
        key="addon:skin-deep",
        name="皮肤 · 深海（蓝调）",
        category="skin",
        intro="低饱和深蓝配色，候选与高亮层次分明，兼顾暗色环境的柔和观感",
        advantages=("无需下载，秒级生效", "层次分明不易看串行", "低饱和，久看不腻"),
        packages=(),
        skin_key="skin-deep",
        size_hint="内置",
    ),
    # ---- v1.0.7 新增皮肤：扩充至 6 款（真机反馈「只有 3 个感觉少」） ----
    Scheme(
        key="addon:skin-bamboo",
        name="皮肤 · 青竹（浅绿）",
        category="skin",
        intro="淡雅浅绿底色配墨绿文字，清新护眼，适合长时间码字的浅色环境",
        advantages=("无需下载，秒级生效", "浅色中带一点绿意，缓解视觉疲劳", "可随时切换或移除"),
        packages=(),
        skin_key="skin-bamboo",
        size_hint="内置",
    ),
    Scheme(
        key="addon:skin-violet",
        name="皮肤 · 暮山紫（深色）",
        category="skin",
        intro="深紫底淡紫字，夜间氛围感强，与深蓝深海互补的另一款暗色选择",
        advantages=("无需下载，秒级生效", "夜间氛围感强", "可随时切换或移除"),
        packages=(),
        skin_key="skin-violet",
        size_hint="内置",
    ),
    Scheme(
        key="addon:skin-amber",
        name="皮肤 · 暖橙（浅色）",
        category="skin",
        intro="米白底暖棕字、橙色高亮，柔和温暖，白天不刺眼、投影可读性好",
        advantages=("无需下载，秒级生效", "暖色调柔和舒适", "可随时切换或移除"),
        packages=(),
        skin_key="skin-amber",
        size_hint="内置",
    ),
)

SCHEME_MAP: dict[str, Scheme] = {s.key: s for s in SCHEMES}

_SPEC_RE = re.compile(r"^(?P<user>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?:@(?P<branch>[A-Za-z0-9_./-]+))?(?::(?P<recipe>[A-Za-z0-9_-]+))?$")

#: GitHub 仓库链接前缀（http/https、www. 均可，大小写不敏感）
_URL_PREFIX_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/", re.IGNORECASE)


def _normalize_spec(text: str) -> str:
    """GitHub 仓库链接归一化为 user/repo 规格（v1.0.17）。

    粘贴 https://github.com/user/repo（可带 www. / .git 后缀）直接可用，
    不再强迫用户手工改写成 user/repo——输入框 placeholder 也这么写。
    """
    spec = _URL_PREFIX_RE.sub("", (text or "").strip()).strip("/")
    return spec[:-4] if spec.endswith(".git") else spec


def parse_spec(text: str) -> PackageRef | None:
    """解析自定义规格 user/repo@branch:recipe 或 GitHub 仓库链接；非法返回 None。

    未写 @branch 时不留 master 默认值（v1.0.20）：改为空值交给下载层探测
    默认分支，否则默认分支是 main 的仓库整条链路 404。
    """
    match = _SPEC_RE.match(_normalize_spec(text))
    if not match:
        return None
    return PackageRef(
        user=match.group("user"),
        repo=match.group("repo"),
        branch=match.group("branch") or "",
        recipe=match.group("recipe") or "",
    )


def custom_scheme(ref: PackageRef) -> Scheme:
    key = f"custom:{ref.spec}"
    return Scheme(
        key=key,
        name=f"自定义 {ref.label}",
        category="custom",
        intro=f"来自 GitHub 的自定义方案包 {ref.label}",
        advantages=("由你指定的来源，安装前请确认仓库可信",),
        packages=(ref,),
        size_hint="视仓库而定",
    )
