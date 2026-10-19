"""发行版兼容判定（v1.0.30：版本通配）。

设计原则：**不枚举任何具体版本**。只识别「发行版家族 + 实测最低版本」，
用元组比较代替逐版本硬编码——用户系统升级（Ubuntu 24.04→26.04、
Mint 22→23，乃至尚未发布的 Ubuntu 28.04）后无需改一行代码，
判定自动跟随运行时读数。

数据来源与 detector._detect_os / apt_mirror.system_codename 一致：
全部来自 /etc/os-release 的 NAME / VERSION_ID，由调用方运行时传入。
本模块只做纯计算，不读文件、不抛异常。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 判定三态
OK = "ok"          # 已识别家族，且不低于实测基线（含未来更高版本）
BELOW = "below"    # 已识别家族，但低于实测基线（不阻断，仅如实告知）
UNKNOWN = "unknown"  # 无法识别（NAME 不含已知家族 / 版本号不可解析）

#: 家族匹配串（os-release NAME 的小写子串）→ 实测最低版本。
#: **只写下限、不写上限**——「通配」语义即来源于此：任何高于下限的
#: 未来版本自动落入 OK，不需要为每个新版本加分支。
#: 匹配用子串而非相等：Ubuntu Kylin（NAME="Ubuntu Kylin"）等官方衍生
#: 同样命中。
FAMILY_MIN: dict[str, tuple[int, ...]] = {
    "ubuntu": (24, 4),
    "linux mint": (22, 0),
    "deepin": (25, 2),
    "uos": (25, 2),
}

#: 各家族最低版本的中文展示（verdict.minimum_text，供提示文案引用）
_FAMILY_MIN_TEXT: dict[str, str] = {
    "ubuntu": "Ubuntu 24.04",
    "linux mint": "Linux Mint 22",
    "deepin": "Deepin 25.2",
    "uos": "UOS 25.2",
}


@dataclass(frozen=True)
class Verdict:
    """一次判定的结果。state ∈ {OK, BELOW, UNKNOWN}。"""

    state: str
    family: str                      # 命中的家族匹配串（unknown 时为 ""）
    minimum_text: str                # 该家族最低版本展示（unknown 时为 ""）
    version: tuple[int, ...] | None  # 解析出的版本元组（不可解析为 None）


def parse_version(text: str) -> tuple[int, ...] | None:
    """把 "24.04" 解析为 (24, 4)；不可解析时返回 None。

    覆盖形态："22.3" / "25.2" / "23" / "24.04.1" / "24.4"（不强制补零）；
    拒绝形态："" / "rolling" / "abc" / "24.x"（含非数字段即整体判不可解析）。
    """
    parts = (text or "").strip().split(".")
    numbers: list[int] = []
    for part in parts:
        part = part.strip()
        if not part.isdigit():
            return None
        numbers.append(int(part))
    return tuple(numbers) if numbers else None


def _match_family(os_name: str) -> tuple[str, tuple[int, ...], str]:
    """NAME 小写子串匹配家族；未命中返回 ("", (), "")。"""
    name = (os_name or "").lower()
    for family, minimum in FAMILY_MIN.items():
        if family in name:
            return family, minimum, _FAMILY_MIN_TEXT[family]
    return "", (), ""


def support_verdict(os_name: str, os_version: str) -> Verdict:
    """按家族 + 最低版本给出兼容判定（纯函数，相同输入恒定相同输出）。"""
    family, minimum, minimum_text = _match_family(os_name)
    version = parse_version(os_version)
    if not family or version is None:
        return Verdict(UNKNOWN, family, minimum_text, version)
    state = OK if version >= minimum else BELOW
    return Verdict(state, family, minimum_text, version)


def verdict_line(verdict: Verdict) -> str:
    """判定结果的中文摘要（测试断言与启动日志用；UI 文案走 i18n 键）。"""
    if verdict.state == OK:
        return f"支持（{verdict.minimum_text} 及以上）"
    if verdict.state == BELOW:
        return f"低于实测基线（最低 {verdict.minimum_text}），不保证可用"
    return "未能识别的发行版，按通用 Linux 处理"
