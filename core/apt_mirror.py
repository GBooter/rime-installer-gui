"""apt 国内镜像测速与切换（F13）。

仅支持 Ubuntu（deb822 格式 /etc/apt/sources.list.d/ubuntu.sources）；
Deepin 官方源本身位于国内，无需切换，UI 层会给出说明。
切换流程：pkexec 备份原源文件 → 写入新源（deb822）→ apt update。
v1.0.5：写源与 update 合并进同一次 pkexec（单次密码框，真机反馈
「好几次输入密码」）；apt 经 engine_service.apt_command 降优先级
（nice + ionice -c3，缓解安装/更新期间系统卡顿）。
v1.0.31：``sources_codenames()`` 解析源中的发行代号，供 doctor
「apt 源代号一致性」体检比对（系统升级后旧代号残留的探测）。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import config
from core.engine_service import UPDATE_FAILED_NOTE, apt_command, apt_program
from core.runner import Step

# Ubuntu deb822 源文件位置（24.04+ 默认；旧版回退 sources.list）
UBUNTU_SOURCES = Path("/etc/apt/sources.list.d/ubuntu.sources")
LEGACY_SOURCES = Path("/etc/apt/sources.list")
COMPONENTS = "main restricted universe multiverse"


@dataclass
class Mirror:
    name: str
    url: str
    is_official: bool = False

    @property
    def is_current(self) -> bool:
        return current_mirror_name() == self.name


def system_codename() -> str:
    """从 /etc/os-release 取 VERSION_CODENAME（26.04 起 codename 未知也能取到）。"""
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def is_ubuntu() -> bool:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return False
    return 'NAME="Ubuntu"' in text or "NAME=Ubuntu" in text


#: 口袋后缀：noble-updates / noble-backports 的「基座」仍是 noble
SUITE_SUFFIXES = ("-updates", "-backports", "-security", "-proposed")
#: 固定词 suite：不含发行版信息，与 os-release 代号比对时必须剔除
#: （Debian 系 stable/testing、Deepin 官方源的 deepin…）
SUITE_STOPWORDS = frozenset(
    {
        "stable",
        "oldstable",
        "testing",
        "unstable",
        "experimental",
        "sid",
        "deepin",
    }
)


def _codenames_from_text(text: str) -> list[str]:
    """从源文件文本中提取**发行代号**型 suite（去后缀、剔固定词，保序去重）。

    同时覆盖两种格式：
    - deb822：``Suites: noble noble-updates noble-backports``
    - 传统一行式：``deb [opts] URI suite [components…]``

    deb822 续行（缩进的裸 suite 行）不展开——Ubuntu 默认源与本工具写入的
    源均为单行 Suites，真实场景够用；漏展开只会少算一个代号（偏保守）。
    """
    codenames: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Suites:"):
            suites = line.split(":", 1)[1].split()
        elif line.startswith("deb ") or line.startswith("deb-src "):
            parts = [p for p in line.split() if not p.startswith("[")]
            suites = parts[2:3]  # deb [opts] URI suite …
        else:
            continue
        for suite in suites:
            base = suite
            for suffix in SUITE_SUFFIXES:
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            if (
                base
                and base not in SUITE_STOPWORDS
                and base not in codenames
                and re.fullmatch(r"[a-z][a-z0-9]*", base)
            ):
                codenames.append(base)
    return codenames


def sources_codenames() -> list[tuple[str, str]]:
    """解析 apt 源中的**发行代号**，返回 ``[(代号, 源文件), ...]``。

    供 doctor 的「apt 源代号一致性」体检比对：系统升级（do-release-upgrade /
    mintupgrade）后旧代号残留时，源里会出现与 os-release 不一致的代号。
    读不到源文件 / 源中没有代号型 suite 时返回空列表（调用方按无法判断处理）。
    """
    found: list[tuple[str, str]] = []
    for path in (UBUNTU_SOURCES, LEGACY_SOURCES):
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for codename in _codenames_from_text(text):
            found.append((codename, str(path)))
    return found


def mirrors() -> list[Mirror]:
    return [
        Mirror(m["name"], m["url"], m.get("is_official", False))
        for m in config.APT_MIRRORS
    ]


def _read_sources_text() -> str:
    for path in (UBUNTU_SOURCES, LEGACY_SOURCES):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def current_mirror_name() -> str:
    """识别当前源对应的已知镜像；未匹配返回「自定义源」。"""
    text = _read_sources_text()
    if not text.strip():
        return "未知"
    for mirror in mirrors():
        if mirror.url in text or mirror.url.replace("https://", "http://") in text:
            return mirror.name
    if "archive.ubuntu.com" in text or "security.ubuntu.com" in text:
        return "官方源"
    return "自定义源"


def _new_sources_content(mirror: Mirror, codename: str) -> str:
    suites = f"{codename} {codename}-updates {codename}-backports"
    return (
        f"# 由中州韵输入法安装器写入：apt 镜像切换为「{mirror.name}」\n"
        f"# 原文件已备份为同目录 .rime-backup 后缀\n"
        f"Types: deb\n"
        f"URIs: {mirror.url}\n"
        f"Suites: {suites}\n"
        f"Components: {COMPONENTS}\n"
        f"Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )


def build_switch_steps(mirror: Mirror) -> list[Step]:
    """备份并写入新源 + apt update，合并为单步单次 pkexec（v1.0.5）。

    此前写源与 update 各包一次 pkexec = 两次密码框；合并后一次提权完成，
    失败时按 set -e 在日志中可直接定位是写文件还是 update 阶段。

    v1.0.30：末尾 apt update 改为**尽力而为**（与引擎安装同款容错）。
    旧实现整段 `set -e`，update 只要遇到任一失效第三方源（404 / 缺公钥）
    就以 100 退出 → 整步判失败，用户看到「切换失败」——但新源此刻其实
    已经写盘成功，local 索引也能正常用。现在 `{ update || echo 警告; }`
    吞掉非零退出码：写源与 update 任一真实失败（如磁盘只读）仍会因 set -e
    在写文件阶段暴露，不会把「第三方源报废」误报成「切换失败」。
    """
    codename = system_codename()
    if not codename:
        raise RuntimeError("无法识别系统代号（VERSION_CODENAME），请手动配置 apt 源")
    target = UBUNTU_SOURCES if UBUNTU_SOURCES.parent.is_dir() else LEGACY_SOURCES
    content = _new_sources_content(mirror, codename)
    apt = apt_program()
    script = (
        "set -e\n"
        f"TARGET={shlex.quote(str(target))}\n"
        'if [ -f "$TARGET" ] && [ ! -f "$TARGET.rime-backup" ]; then cp "$TARGET" "$TARGET.rime-backup"; fi\n'
        f"cat > \"$TARGET\" <<'RIME_EOF'\n{content}RIME_EOF\n"
        'echo "已写入 $TARGET（原文件已备份为 .rime-backup）"\n'
        "echo '===== 已写入新源，更新软件源索引 ====='\n"
        "{ " + f"env DEBIAN_FRONTEND=noninteractive {apt_command(apt, 'update')}"
        + f" || echo '===== {UPDATE_FAILED_NOTE} ====='; }}"
    )
    return _with_pkexec(
        [Step(f"切换 apt 源（{mirror.name}）并更新索引", ["bash", "-c", script], weight=3)]
    )


def _with_pkexec(steps: list[Step]) -> list[Step]:
    """给需要 root 的步骤包上 pkexec（与 engine_service 同款降级策略）。"""
    import shutil

    pkexec = shutil.which("pkexec")
    wrapped: list[Step] = []
    for step in steps:
        if pkexec:
            argv = [pkexec, "bash", "-c", step.argv[2] if step.argv[:2] == ["bash", "-c"] else " ".join(step.argv)]
        else:
            argv = step.argv  # 容器/开发环境降级：已具备 root 或无鉴权时可用
        wrapped.append(Step(step.title, argv, weight=step.weight, work_dir=step.work_dir))
    return wrapped


def speedtest_urls() -> list[tuple[str, str]]:
    """(镜像名, 测速 URL) 列表：下载各镜像的 Release 文件头，比较耗时。"""
    codename = system_codename()
    if not codename:
        return []
    return [
        (m.name, f"{m.url}dists/{codename}/Release")
        for m in mirrors()
    ]


def measure_mirror_latency(urls: list[tuple[str, str]], timeout: float = 8.0) -> list[tuple[str, float | None]]:
    """并行测速（标准库 urllib，无需 requests）；失败返回 None。UI 层在 QThread 调用。"""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    def _probe(item: tuple[str, str]) -> tuple[str, float | None]:
        import time

        name, url = item
        start = time.monotonic()
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read(64 * 1024)
        except Exception:  # noqa: BLE001 — 测速失败即视为不可用
            return (name, None)
        return (name, time.monotonic() - start)

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
        return list(pool.map(_probe, urls))
