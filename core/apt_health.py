"""apt 源可用性探测（v1.0.32）：定位失效 / 不可达的源，引导**手动**禁用。

安全边界（用户明确要求）：**只引导禁用第三方源，系统官方源任何情况下都不
建议禁用**。分类策略据此设计——宁可漏报（未识别源按官方保守处理），不可
错报（把官方源当第三方，用户就会照着指引去禁用它，这是本模块最严重的
失败模式）：

- 官方：Ubuntu 官方主机（archive/security/ports…）、知名国内镜像的
  ``/ubuntu`` 与 ``/linuxmint*`` 路径、Mint 与 Deepin 的官方源主机；
- 第三方：PPA、Steam、Microsoft、Google、Docker、``ci.deepin.com``
  （OBS/CI 仓库，非系统底座源）等已知第三方主机；
- 未识别：一律保守按官方处理——不可达时只说「请自行确认」，不给禁用指引。

与 doctor 的关系：``run_checks`` 是同步纯计算（v1.0.26 铁律：跑在 Qt 槽里，
不抛异常、不阻塞、无网络），因此本模块只提供「解析 + 探测 + 模块级缓存」；
体检页在 ``bind`` 时按 TTL 起 QThread 做后台探测，结果落缓存后由
``run_checks`` 渲染成 ``apt_sources`` 行。

探测方式：对每个源 ``GET {uri}/dists/{suite}/InRelease``（404 时退一步试
``Release``）。只读、无需提权，与镜像测速同款并行模型；``file:``/``cdrom:``
本地源不参与（无网络可达性可言）。GPG 缺钥（NO_PUBKEY）无法用 HTTP 状态
识别，留给后续可选的提权深度检测。
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

#: 探测结果缓存 TTL（秒）：体检页 bind 时超过该间隔才重新探测
CACHE_TTL = 600.0
#: 单源探测超时（秒）
PROBE_TIMEOUT = 4.0

# ---- 分类主机表 ----

#: Ubuntu 官方源主机
UBUNTU_OFFICIAL_HOSTS = frozenset(
    {
        "archive.ubuntu.com",
        "security.ubuntu.com",
        "ports.ubuntu.com",
        "old-releases.ubuntu.com",
    }
)

#: 知名国内镜像主机。只有路径落在 /ubuntu* 或 /linuxmint* 下才算官方——
#: 同一主机上的 kubernetes、docker-ce 等目录是第三方软件源，不能算。
CN_MIRROR_HOSTS = frozenset(
    {
        "mirrors.aliyun.com",
        "mirrors.cloud.tencent.com",
        "mirrors.huaweicloud.com",
        "mirrors.tuna.tsinghua.edu.cn",
        "mirrors.ustc.edu.cn",
        "mirror.nju.edu.cn",
        "mirrors.163.com",
        "mirror.sjtu.edu.cn",
        "mirrors.neusoft.edu.cn",
    }
)

#: Linux Mint 官方源主机
MINT_OFFICIAL_HOSTS = frozenset(
    {
        "packages.linuxmint.com",
        "cdn.packages.linuxmint.com",
    }
)

#: Deepin 官方源主机（系统底座与应用商店仓库）
DEEPIN_OFFICIAL_HOSTS = frozenset(
    {
        "packages.deepin.com",
        "community-packages.deepin.com",
        "community-store-packages.deepin.com",
        "commercial-store-packages.deepin.com",
        "cdn.packages.deepin.com",
        # UOS/Deepin 应用商店（统信）官方源
        "com-store-packages.uniontech.com",
    }
)

#: 已知第三方源主机——命中即「第三方」，不可达时才给禁用引导
KNOWN_THIRD_PARTY_HOSTS = frozenset(
    {
        "ppa.launchpadcontent.net",
        "ppa.launchpad.net",
        "repo.steampowered.com",
        "packages.microsoft.com",
        "dl.google.com",
        "download.docker.com",
        "ci.deepin.com",
    }
)

KIND_OFFICIAL = "official"
KIND_THIRD = "third"
KIND_UNKNOWN = "unknown"

#: 源文件解析（与 apt_mirror 同一对路径；此处只读解析，不做任何写入）
SOURCE_FILES = (
    Path("/etc/apt/sources.list.d/ubuntu.sources"),
    Path("/etc/apt/sources.list"),
)


@dataclass
class SourceEntry:
    """一个 apt 仓库条目（URI + suite 的组合，与 components 无关）。"""

    uri: str  # 已去尾斜杠
    suite: str
    components: str
    path: str  # 来源文件（展示用）

    @property
    def host(self) -> str:
        return urlparse(self.uri).netloc.lower()

    @property
    def url_path(self) -> str:
        return urlparse(self.uri).path or "/"

    @property
    def kind(self) -> str:
        return classify(self)

    @property
    def label(self) -> str:
        """展示名：域名 + 简短路径（如 ppa.launchpadcontent.net/…/ubuntu）"""
        path = self.url_path.strip("/")
        return f"{self.host}/{path}" if path else self.host


def classify(entry: SourceEntry) -> str:
    """官方 / 第三方 / 未识别。未识别一律保守——不给禁用引导。"""
    host = entry.host
    path = entry.url_path
    if host in KNOWN_THIRD_PARTY_HOSTS or host.startswith("ppa."):
        return KIND_THIRD
    if host in UBUNTU_OFFICIAL_HOSTS or host in MINT_OFFICIAL_HOSTS:
        return KIND_OFFICIAL
    if host in DEEPIN_OFFICIAL_HOSTS:
        return KIND_OFFICIAL
    if host in CN_MIRROR_HOSTS:
        if path.startswith(("/ubuntu", "/linuxmint")):
            return KIND_OFFICIAL
        return KIND_UNKNOWN
    return KIND_UNKNOWN


def _norm_uri(uri: str) -> str:
    return uri.strip().rstrip("/")


def _is_network_uri(uri: str) -> bool:
    """是否网络源（http/https）。``file:``/``cdrom:`` 等本地源没有网络
    可达性可言，解析层直接跳过——健康探测的消费者只关心远端仓库。"""
    return uri.strip().lower().startswith(("http://", "https://"))


def _parse_deb822(text: str, path: str) -> list[SourceEntry]:
    """解析 deb822 源文件（ubuntu.sources）：Types/URIs/Suites 字段。

    续行（缩进行）并入上一个字段；``Types`` 不含 ``deb`` 的 stanza
    （如纯 deb-src）跳过——与 deb 型条目同 URI，探测一次即可。
    """
    entries: list[SourceEntry] = []
    stanza: dict[str, str] = {}
    order: list[str] = []

    def _flush() -> None:
        if not stanza:
            return
        types = stanza.get("types", "")
        uris = stanza.get("uris", "").split()
        suites = stanza.get("suites", "").split()
        comps = stanza.get("components", "")
        if "deb" not in types.split() or not uris or not suites:
            return
        for uri in uris:
            if not _is_network_uri(uri):
                continue
            for suite in suites:
                entries.append(
                    SourceEntry(_norm_uri(uri), suite, comps, path)
                )

    for raw in text.splitlines():
        if not raw.strip():
            _flush()
            stanza, order = {}, []
            continue
        if raw[0] in " \t" and order:
            stanza[order[-1]] += f" {raw.strip()}"
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip().lower()
        stanza[key] = value.strip()
        order.append(key)
    _flush()
    return entries


def _parse_traditional(text: str, path: str) -> list[SourceEntry]:
    """解析一行式源（sources.list / *.list）：``deb [opts] URI suite comps…``。

    注释行与 ``file:``/``cdrom:`` 本地源跳过（见 ``_is_network_uri``）。
    """
    entries: list[SourceEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not (line.startswith("deb ") or line.startswith("deb-src ")):
            continue
        parts = [p for p in line.split() if not p.startswith("[")]
        if len(parts) < 3 or not _is_network_uri(parts[1]):
            continue
        entries.append(SourceEntry(_norm_uri(parts[1]), parts[2], " ".join(parts[3:]), path))
    return entries


def parse_source_entries(text: str, path: str = "") -> list[SourceEntry]:
    """按文件类型解析源文本（.sources 走 deb822，其余走一行式）。"""
    if path.endswith(".sources") or re.search(r"^\s*Types:", text, re.MULTILINE):
        return _parse_deb822(text, path)
    return _parse_traditional(text, path)


def read_source_entries() -> list[SourceEntry]:
    """读取系统 apt 源条目；读不到 / 解析失败按空处理（调用方判无法判断）。

    解析层已跳过 ``file:``/``cdrom:`` 本地源，这里只做去重与容错。
    """
    entries: list[SourceEntry] = []
    seen: set[tuple[str, str]] = set()
    for path in SOURCE_FILES:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for entry in parse_source_entries(text, str(path)):
            key = (entry.uri, entry.suite)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    return entries


def probe_url(uri: str, suite: str, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """GET ``{uri}/dists/{suite}/InRelease``；404 退一步试 ``Release``。

    返回 ``(ok, reason)``：ok 为 True 时 reason 为空串。
    """
    for name in ("InRelease", "Release"):
        url = f"{uri}/dists/{suite}/{name}"
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "rime-installer/apt-health"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read(4096)
                return True, ""
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and name == "InRelease":
                continue  # 有的源只提供 Release
            return False, f"HTTP {exc.code}"
        except TimeoutError:
            return False, "连接超时"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return False, str(reason) or "连接失败"
        except OSError as exc:  # noqa: BLE001 — DNS/连接层一切失败归为不可达
            return False, str(exc) or "连接失败"
    return False, "404（InRelease / Release 均不存在）"


Prober = Callable[[str, str, float], tuple[bool, str]]


@dataclass
class HealthReport:
    """一次探测的完整结果（不可变快照，落模块缓存供体检项渲染）。"""

    outcomes: list[tuple[SourceEntry, bool, str]] = field(default_factory=list)
    checked_at: float = 0.0

    def _failed(self, kind: str) -> list[tuple[SourceEntry, str]]:
        return [(e, r) for e, ok, r in self.outcomes if not ok and e.kind == kind]

    @property
    def third_failed(self) -> list[tuple[SourceEntry, str]]:
        return self._failed(KIND_THIRD)

    @property
    def official_failed(self) -> list[tuple[SourceEntry, str]]:
        return self._failed(KIND_OFFICIAL)

    @property
    def unknown_failed(self) -> list[tuple[SourceEntry, str]]:
        return self._failed(KIND_UNKNOWN)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def ok_count(self) -> int:
        return sum(1 for _, ok, _ in self.outcomes if ok)

    @property
    def failed_count(self) -> int:
        return self.total - self.ok_count


def check_health(timeout: float = PROBE_TIMEOUT, prober: Prober | None = None) -> HealthReport:
    """解析全部源并并行探测；``prober`` 可注入（冒烟用假探测器，不走网络）。"""
    entries = read_source_entries()
    prober = prober or probe_url
    outcomes: list[tuple[SourceEntry, bool, str]] = []
    if entries:
        with ThreadPoolExecutor(max_workers=min(4, len(entries))) as pool:
            results = pool.map(lambda e: prober(e.uri, e.suite, timeout), entries)
        for entry, (ok, reason) in zip(entries, results):
            outcomes.append((entry, ok, reason))
    return HealthReport(outcomes=outcomes, checked_at=time.monotonic())


# ---- 模块级缓存与状态 ----

_cache: HealthReport | None = None
_probing = False


def mark_probing() -> None:
    """探测开始（体检项据此渲染「检测中」而非「尚未检测」）。"""
    global _probing
    _probing = True


def abort_probing() -> None:
    """探测整体失败：清除探测中标志，缓存保持原样（None 即「尚未检测」）。"""
    global _probing
    _probing = False


def store_report(report: HealthReport) -> None:
    """探测完成，写入缓存并清除探测中标志。"""
    global _cache, _probing
    _cache, _probing = report, False


def last_report() -> HealthReport | None:
    return _cache


def is_probing() -> bool:
    return _probing


def is_stale(max_age: float = CACHE_TTL) -> bool:
    """是否需要（重新）探测：从未探测、正在探测中（避免重入）、或缓存过期。"""
    if _probing:
        return False
    if _cache is None:
        return True
    return (time.monotonic() - _cache.checked_at) > max_age


def probing_enabled() -> bool:
    """自动探测总开关。``RIME_INSTALLER_NO_SOURCE_PROBE=1`` 时关闭（冒烟用——
    窗口构建会触发 bind，离屏环境里对外网做真实探测只会拖慢且不稳定）。"""
    import os

    return os.environ.get("RIME_INSTALLER_NO_SOURCE_PROBE") != "1"
