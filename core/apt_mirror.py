"""apt 国内镜像测速与切换（F13）。

仅支持 Ubuntu（deb822 格式 /etc/apt/sources.list.d/ubuntu.sources）；
Deepin 官方源本身位于国内，无需切换，UI 层会给出说明。
切换流程：pkexec 备份原源文件 → 写入新源（deb822）→ apt update。
v1.0.5：写源与 update 合并进同一次 pkexec（单次密码框，真机反馈
「好几次输入密码」）；apt 经 engine_service.apt_command 降优先级
（nice + ionice -c3，缓解安装/更新期间系统卡顿）。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import config
from core.engine_service import apt_command, apt_program
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
        f"env DEBIAN_FRONTEND=noninteractive {apt_command(apt, 'update')}"
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
