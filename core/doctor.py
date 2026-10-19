"""环境体检与修复（F10）：一键排查「装了 rime 却用不了」的高频问题。

体检项覆盖 Linux 下 rime 的经典故障点：
- 环境变量未注入（GTK_IM_MODULE/QT_IM_MODULE/XMODIFIERS/SDL_IM_MODULE 与运行中前端不匹配）
- 输入法框架进程未运行
- 引擎包未安装 / 未安装任何方案
- 未部署（rime 用户目录无 build/，输入无候选）
- 框架未设为默认（~/.xinputrc 未指向当前前端）
- apt 源代号与系统不一致（v1.0.31：升级后旧代号残留，装到旧版包）
- apt 源可用性（v1.0.32：失效第三方源点名 + 手动禁用引导，官方源不禁）

修复策略：可自动执行的生成 Step 列表（写输入法环境变量配置、启动框架、
重新部署等），不可自动修复的（如未装引擎）给出跳转引导，由 UI 层处理。
所有命令经 bash -c 包装，与 runner 的取消/日志机制兼容。

v1.0.26 第二步：环境变量改写入 ``~/.config/environment.d`` + ``~/.xsessionrc``
（旧实现写 ``~/.xprofile``，但 Debian/Ubuntu/Deepin 三家的会话脚本都不读它，
详见 ``core/im_env.py``）；同时环境变量项会读回配置文件，区分「尚未写入」
与「已写入、重新登录后生效」，不再出现「点了修复还是红的」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core import apt_health, apt_mirror, im_env
from core.deploy_guide import build_deploy_script
from core.detector import EnvReport
from core.runner import Step

# 体检结果级别
OK = "ok"
WARN = "warn"
BAD = "bad"
INFO = "info"


@dataclass
class CheckResult:
    key: str
    title: str
    level: str  # ok / warn / bad / info
    detail: str
    advice: str = ""
    fixable: bool = False
    #: 英文文案键前缀（``doctor.check.<key>.<branch>``）。中文由上面的字面量
    #: 直接给出，英文包缺失时同样回退到这里——避免为同一句话维护两份中文。
    i18n_key: str = ""
    #: 文案占位符（路径、数量、冲突键等），中英共用
    i18n_params: dict[str, str] = field(default_factory=dict)
    # fix_steps 由 build_fix_steps 统一构建；此字段仅为 UI 标注「可自动修复」

    @property
    def icon(self) -> str:
        return {OK: "✓", WARN: "!", BAD: "✕", INFO: "i"}[self.level]


# 框架启动/重启命令（setsid + & 保证不阻塞 runner 的事件循环）
def _frontend_start_cmd(frontend: str) -> str:
    if frontend == "ibus-rime":
        return "pkill -x ibus-daemon 2>/dev/null; sleep 0.3; setsid ibus-daemon -drxR >/dev/null 2>&1 &"
    if frontend == "fcitx5-rime":
        return "pkill -x fcitx5 2>/dev/null; sleep 0.3; setsid fcitx5 -d >/dev/null 2>&1 &"
    return "pkill -x fcitx 2>/dev/null; sleep 0.3; setsid fcitx -d >/dev/null 2>&1 &"


#: 用「gsettings 输入源」管理的桌面 → (桌面匹配关键字, schema, 展示名)
#: v1.0.29：Cinnamon（Linux Mint）与 GNOME 同构——Cinnamon 的输入法面板就是
#: IBus 的输入源列表，schema 为 org.cinnamon.desktop.input-sources，条目格式
#: 也是 ('ibus','rime')。旧实现只认 GNOME，Mint 上这一整项永远「跳过」。
INPUT_SOURCE_SCHEMAS = (
    ("GNOME", "org.gnome.desktop.input-sources", "GNOME"),
    ("CINNAMON", "org.cinnamon.desktop.input-sources", "Cinnamon"),
    ("MINT", "org.cinnamon.desktop.input-sources", "Cinnamon"),
)


def input_source_schema(desktop: str = "") -> tuple[str, str] | None:
    """桌面 → (gsettings schema, 展示名)；不用 gsettings 管输入源的桌面返回 None。"""
    upper = (desktop or "").upper()
    for token, schema, label in INPUT_SOURCE_SCHEMAS:
        if token in upper:
            return schema, label
    return None


def _read_input_sources(schema: str) -> str | None:
    """读取某个 gsettings schema 的 sources 值；不可用（无 gsettings/无 schema）返回 None。"""
    import subprocess

    try:
        proc = subprocess.run(
            ["gsettings", "get", schema, "sources"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _gnome_input_sources(desktop: str = "") -> str | None:
    """读取桌面的输入法输入源配置；不可用（非 GNOME/Cinnamon / 无 gsettings）返回 None。

    GNOME Wayland 会话下，ibus-setup 的「输入法」列表不等于桌面输入源——
    必须在 org.gnome.desktop.input-sources sources 里注册 ('ibus','rime')
    才能从顶栏切换到 Rime（真机实证：preload 有 rime 但无法切换使用）。

    v1.0.26：必须先判桌面。`gsettings` 在 DDE/KDE 上也存在且返回
    `@a(ss) []`（exit 0），旧实现因此把「Deepin/KDE + ibus」误报成
    「GNOME 输入源未添加」，修复动作还会去写一批没人读的键。

    v1.0.29：Cinnamon 走同构的 org.cinnamon.desktop.input-sources。
    """
    resolved = input_source_schema(desktop)
    if resolved is None:
        return None
    return _read_input_sources(resolved[0])


def _fcitx5_input_methods(profile: Path | None = None) -> str | None:
    """读取 fcitx5 输入法列表（~/.config/fcitx5/profile）。

    fcitx5 的 profile 是 ini 风格分段结构：输入法注册在
    ``[Groups/<组>/Items/<序>]`` 段的 ``Name=``（fcitx5-rime 的注册名是
    ``rime``），组名在 ``[Groups/<组>]`` 段——两者都要区分，不能见 Name
    就收（真机实证：默认组名「默认」也会命中 Name=）。
    另兼容个别配置里的 ``IMList=`` 行。

    文件不存在返回 None（尚未配置输入法）；存在但无任何输入法返回空串。
    """
    if profile is None:
        profile = Path.home() / ".config" / "fcitx5" / "profile"
    if not profile.is_file():
        return None
    try:
        text = profile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    names: list[str] = []
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
        if section.startswith("[Groups/") and "/Items/" in section:
            if key == "Name":
                names.append(value)
        elif key == "IMList":
            names.extend(part.strip() for part in value.split(",") if part.strip())
    seen: set[str] = set()
    unique = [name for name in names if not (name in seen or seen.add(name))]
    return ", ".join(unique)


def _build_input_source_fix_script(frontend: str, schema: str = "org.gnome.desktop.input-sources") -> str:
    """把 ('ibus','rime') 合并进桌面输入源（幂等）+ 重启框架。"""
    return (
        f"KEY={schema}.sources\n"
        'CUR=$(gsettings get "$KEY" 2>/dev/null || echo "@a(ss) []")\n'
        'case "$CUR" in *"rime"*)\n'
        '  echo "输入源已包含 rime：$CUR"\n'
        ";; *)\n"
        '  if [ "$CUR" = "@a(ss) []" ] || [ "$CUR" = "[]" ]; then\n'
        '    NEW="[('
        "'ibus', 'rime'"
        ')]"\n'
        "  else\n"
        "    NEW=\"${CUR%]}, ('ibus', 'rime')]\"\n"
        "  fi\n"
        '  gsettings set "$KEY" "$NEW" && echo "已将 中文(Rime) 加入输入源"\n'
        ";; esac\n"
        + _frontend_start_cmd(frontend)
    )


def _has_build(rime_dir: Path | None) -> bool:
    """build/ 是否存在；目录无权限时按「不存在」处理，绝不抛异常。"""
    if rime_dir is None:
        return False
    try:
        return (rime_dir / "build").is_dir()
    except OSError:
        return False


def _child_count(path: Path) -> int | None:
    """目录直接子项数；无读权限返回 None（用于区分「空目录」与「读不了」）。"""
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return None


def _dir_readable(path: Path) -> bool:
    """目录是否可列举（无读权限时 iterdir 抛 OSError）。"""
    return _child_count(path) is not None


def _is_dir(path: Path) -> bool:
    """is_dir 的安全版本（父目录无权限时 3.12/3.13 会抛 PermissionError）。"""
    try:
        return path.is_dir()
    except OSError:
        return False


def _has_schema(rime_dir: Path | None) -> bool:
    """配置目录里是否有 ``*.schema.yaml``；无权限时按「没有」处理。"""
    if rime_dir is None:
        return False
    try:
        return any(rime_dir.glob("*.schema.yaml"))
    except OSError:
        return False


def _read_os_release() -> dict[str, str]:
    """解析 /etc/os-release 为 dict（键去引号）；读不到返回空 dict。"""
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    data: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _apt_codename_check() -> CheckResult:
    """v1.0.31：apt 源代号与运行时代号一致性——「系统升级后还能装」的探测。

    背景：Ubuntu / Linux Mint 的 apt 源按**发行代号**（noble / zena）寻址，
    do-release-upgrade / mintupgrade 正常会改写源；但三种情况会残留旧代号：
    ①曾用本程序「apt 镜像」改写过的 ubuntu.sources（升级工具不重写它的
    Suites 组合）；②手工添加的第三方源；③升级中断。表现为「升级后 apt
    还能 update，但装到的全是旧版包」——引擎安装看起来成功，版本却不对。

    探测用的代号全部运行时读取（os-release 的 VERSION_CODENAME + 源文件），
    与 v1.0.30 compat 的通配思路一致：不为任何具体版本写死分支，
    Ubuntu 28.04 / Mint 23 发布当天即可正确比对。

    家族口径：Ubuntu 只认 ``VERSION_CODENAME``；Mint 额外放行
    ``UBUNTU_CODENAME``——Mint 源同时引用自身代号与 Ubuntu 基座代号
    （实测 zena + noble 并存），只认一个必然误报。Deepin 等非 Ubuntu 系
    源的 suite 是固定词（deepin），不按代号寻址，直接跳过。
    """
    release = _read_os_release()
    name = release.get("NAME", "").strip()
    is_mint = "mint" in name.lower()
    runtime = release.get("VERSION_CODENAME", "").strip().lower()
    ubuntu_base = release.get("UBUNTU_CODENAME", "").strip().lower()
    title = "apt 源代号"
    if not (is_mint or name.lower() == "ubuntu"):
        return CheckResult(
            "apt_codename",
            title,
            INFO,
            f"当前系统（{name or '未知'}）的 apt 源不按发行代号寻址，跳过",
            "本项仅对 Ubuntu / Linux Mint 有意义",
            i18n_key="doctor.check.apt_codename.non_ubuntu",
            i18n_params={"os": name or "未知"},
        )
    allowed = ({runtime, ubuntu_base} if is_mint else {runtime}) - {""}
    if not allowed:
        return CheckResult(
            "apt_codename",
            title,
            INFO,
            "系统未提供发行代号（os-release 无 VERSION_CODENAME），无法比对",
            "如为定制发行版，请手动核对 apt 源中的发行代号",
            i18n_key="doctor.check.apt_codename.no_codename",
        )
    codenames = apt_mirror.sources_codenames()
    if not codenames:
        return CheckResult(
            "apt_codename",
            title,
            INFO,
            "未在 apt 源中检测到发行代号，跳过",
            "使用固定词 suite（如 Debian stable）或自定义源时无需比对",
            i18n_key="doctor.check.apt_codename.no_sources",
        )
    stale = sorted({c for c, _ in codenames if c not in allowed})
    expected = "/".join(sorted(allowed))
    if not stale:
        return CheckResult(
            "apt_codename",
            title,
            OK,
            f"apt 源代号与当前系统一致（{expected}）",
            i18n_key="doctor.check.apt_codename.ok",
            i18n_params={"codename": expected},
        )
    stale_desc = "、".join(
        f"{codename}（{Path(path).name}）"
        for codename, path in codenames
        if codename in stale
    )
    return CheckResult(
        "apt_codename",
        title,
        WARN,
        f"apt 源中存在与当前系统（{expected}）不一致的发行代号：{stale_desc}",
        "系统升级后旧代号残留会让 apt 装到旧版包：请在「软件和更新 / 软件源」"
        "中把这些源更新为当前代号或禁用；若曾用本程序切换镜像，重新切换一次"
        "即可按当前代号重写",
        i18n_key="doctor.check.apt_codename.mismatch",
        i18n_params={"codename": expected, "stale": stale_desc},
    )


def _apt_sources_check() -> CheckResult:
    """v1.0.32：apt 源可用性（读 apt_health 探测缓存，本函数无网络 IO）。

    失效的第三方源会让 ``apt update`` 报错（404 / NO_PUBKEY / 连接失败），
    引擎安装步骤的 update 已容错跳过，但残留的坏源会一直拖慢每次 apt 操作。
    本项把它逐个点名，并给出手动禁用指引。

    **安全边界**：只对「第三方」源给禁用指引；官方源（Ubuntu 官方 / 知名
    镜像的 ubuntu 路径 / Mint / Deepin 官方）与未识别源不可达时，一律只提示
    检查网络或自行确认——误引导用户禁用官方源是比漏报严重得多的失败。
    """
    title = "apt 源可用性"
    report = apt_health.last_report()
    if report is None:
        if apt_health.is_probing():
            return CheckResult(
                "apt_sources",
                title,
                INFO,
                "正在后台探测 apt 源可达性，请稍候（完成自动刷新）",
                i18n_key="doctor.check.apt_sources.probing",
            )
        return CheckResult(
            "apt_sources",
            title,
            INFO,
            "尚未检测 apt 源可达性（打开本页时自动在后台探测）",
            "失效的第三方源会让 apt update 报错；检测完成后本行自动刷新",
            i18n_key="doctor.check.apt_sources.not_tested",
        )
    third = report.third_failed
    official = report.official_failed + report.unknown_failed
    if not third and not official:
        if report.total == 0:
            return CheckResult(
                "apt_sources",
                title,
                INFO,
                "未在系统中检测到 apt 源，跳过",
                i18n_key="doctor.check.apt_sources.no_sources",
            )
        return CheckResult(
            "apt_sources",
            title,
            OK,
            f"{report.total} 个 apt 源全部可达",
            i18n_key="doctor.check.apt_sources.ok",
            i18n_params={"count": str(report.total)},
        )

    def _desc(items: list[tuple[apt_health.SourceEntry, str]]) -> str:
        return "、".join(f"{entry.label}（{reason}）" for entry, reason in items)

    if third:
        # 第三方失效：点名 + 禁用指引（官方源自始至终不出现禁用话术）
        detail = f"{len(third)} 个第三方 apt 源不可达：{_desc(third)}"
        advice = (
            "请在「软件和更新 / 软件源」中禁用它们（或把 sources.list.d 下对应"
            "文件移出目录、注释掉其中 deb 行）——只禁第三方源，切勿禁用系统官方源"
        )
        if official:
            detail += f"；另有 {len(official)} 个官方/未识别源不可达，通常是网络问题，请勿禁用"
        return CheckResult(
            "apt_sources",
            title,
            WARN,
            detail,
            advice,
            i18n_key="doctor.check.apt_sources.third_failed",
            i18n_params={
                "count": str(len(third)),
                "items": _desc(third),
                "official": str(len(official)),
            },
        )
    return CheckResult(
        "apt_sources",
        title,
        WARN,
        f"{len(official)} 个官方/未识别 apt 源不可达：{_desc(official)}",
        "请检查网络连接或代理设置；未识别源请自行确认后再处理——"
        "不要禁用系统官方源",
        i18n_key="doctor.check.apt_sources.official_failed",
        i18n_params={
            "count": str(len(official)),
            "items": _desc(official),
        },
    )


def run_checks(report: EnvReport, installed_scheme_keys: list[str] | None = None) -> list[CheckResult]:
    """按当前环境报告执行全部体检项（纯计算，无副作用）。

    v1.0.26：所有触及文件系统的探测都不得抛异常。`Path.is_dir()` /
    `iterdir()` 在目录无读/执行权限时抛 PermissionError（3.12/3.13 实测），
    而本函数运行在 `bind()` → Qt 信号槽链里——未捕获异常会让 PyQt6 直接
    abort（无弹窗、无 traceback）。root 拥有过 rime 目录的用户必踩。

    v1.0.34：frontend 取 `effective_frontend`（已装引擎锚定）。旧实现用
    `active_frontend or recommended_frontend`，在 Mint 真机上翻车：装的是
    ibus-rime，重开后 active 变 fcitx-rime，于是输入源注册检查走 fcitx
    分支（只给文字建议）、Cinnamon 的 gsettings 一键修复从未触发，用户
    「装完找不到中州韵输入法选项」。
    """
    frontend = report.effective_frontend
    results: list[CheckResult] = []

    # 1. 输入法环境变量（v1.0.5：只报「已设定但冲突」，未设定按会话自动处理属正常）
    # v1.0.26 第二步：同时读回配置文件。写文件只能影响**下次登录**，
    # 而本项读的是当前进程的 os.environ——若不区分「已写入待生效」，
    # 用户点完修复会看到它依旧是红的（旧实现写 ~/.xprofile 时更是永远红）。
    env_state, env_wrong = im_env.detect_state(frontend)
    env_conf = str(im_env.env_conf_path())
    if report.env_vars_ok is None:
        if env_state == im_env.STATE_OK:
            results.append(
                CheckResult(
                    "im_env",
                    "输入法环境变量",
                    INFO,
                    "当前未检测到运行中的输入法框架，无法判断（属正常情况则忽略）",
                    f"配置文件已就绪（{env_conf}），登录后自动生效",
                    i18n_key="doctor.check.im_env.idle_configured",
                    i18n_params={"path": env_conf},
                )
            )
        else:
            results.append(
                CheckResult(
                    "im_env",
                    "输入法环境变量",
                    INFO,
                    "当前未检测到运行中的输入法框架，无法判断（属正常情况则忽略）",
                    f"可预先写入 {env_conf} 与 ~/.xsessionrc，"
                    "保证下次登录时环境变量正确（幂等，可随时删除）",
                    fixable=True,
                    i18n_key="doctor.check.im_env.idle",
                    i18n_params={"path": env_conf},
                )
            )
    elif report.env_vars_ok:
        results.append(
            CheckResult(
                "im_env",
                "输入法环境变量",
                OK,
                f"已按 {frontend} 正确注入",
                i18n_key="doctor.check.im_env.ok",
                i18n_params={"frontend": frontend},
            )
        )
    elif env_state == im_env.STATE_OK:
        conflicts = "、".join(report.env_var_mismatches)
        results.append(
            CheckResult(
                "im_env",
                "输入法环境变量",
                WARN,
                f"当前会话的设置仍指向其他输入法：{conflicts}",
                f"配置已写入 {env_conf} 与 ~/.xsessionrc，重新登录后生效"
                "（本会话内的进程无法改写环境变量）",
                i18n_key="doctor.check.im_env.pending",
                i18n_params={"conflicts": conflicts, "path": env_conf},
            )
        )
    else:
        conflicts = "、".join(report.env_var_mismatches)
        if env_state == im_env.STATE_STALE:
            stale = "、".join(env_wrong)
            advice = (
                f"配置文件当前指向其他前端（{stale} 不一致），"
                "一键修复会改写为本前端，重新登录后生效"
            )
            i18n_key = "doctor.check.im_env.stale"
        else:
            stale = ""
            advice = (
                f"自动写入 {env_conf} 与 ~/.xsessionrc 后重新登录生效；"
                "未设置的变量按会话类型自动处理，无需干预"
            )
            i18n_key = "doctor.check.im_env.conflict"
        results.append(
            CheckResult(
                "im_env",
                "输入法环境变量",
                BAD,
                f"设置冲突：{conflicts}（与运行中的 {frontend} 不一致）",
                advice,
                fixable=True,
                i18n_key=i18n_key,
                i18n_params={
                    "conflicts": conflicts,
                    "frontend": frontend,
                    "path": env_conf,
                    "stale": stale,
                },
            )
        )

    # 2. 输入法框架进程
    if report.running_frontends:
        results.append(
            CheckResult(
                "frontend_running",
                "输入法框架进程",
                OK,
                "运行中：" + "、".join(report.running_frontends),
                i18n_key="doctor.check.frontend_running.ok",
                i18n_params={"frontends": "、".join(report.running_frontends)},
            )
        )
    elif report.engine_installed:
        results.append(
            CheckResult(
                "frontend_running",
                "输入法框架进程",
                WARN,
                "未检测到 ibus-daemon / fcitx5 / fcitx 进程",
                "引擎已安装但框架未运行，可自动拉起框架进程",
                fixable=True,
                i18n_key="doctor.check.frontend_running.missing",
            )
        )
    else:
        results.append(
            CheckResult(
                "frontend_running",
                "输入法框架进程",
                BAD,
                "未检测到 ibus-daemon / fcitx5 / fcitx 进程",
                "引擎尚未安装，请先到「引擎安装」页安装；框架会随引擎一并启用",
                i18n_key="doctor.check.frontend_running.no_engine",
            )
        )

    # 3. 引擎安装状态
    if report.engine_installed:
        installed = report.installed_engine_packages
        results.append(
            CheckResult(
                "engine",
                "输入法引擎",
                OK,
                "已安装：" + "、".join(installed),
                i18n_key="doctor.check.engine.ok",
                i18n_params={"packages": "、".join(installed)},
            )
        )
    else:
        results.append(
            CheckResult(
                "engine",
                "输入法引擎",
                BAD,
                "未安装 ibus-rime / fcitx5-rime / fcitx-rime 中任何一个",
                "请前往「引擎安装」页安装引擎",
                i18n_key="doctor.check.engine.missing",
            )
        )

    # 4. 输入方案
    scheme_count = len(installed_scheme_keys or [])
    if scheme_count > 0:
        results.append(
            CheckResult(
                "schemes",
                "输入方案",
                OK,
                f"已通过本安装器安装 {scheme_count} 个方案",
                i18n_key="doctor.check.schemes.ok",
                i18n_params={"count": str(scheme_count)},
            )
        )
    else:
        rime_dir = report.rime_user_dir
        has_schema = bool(rime_dir and _has_schema(rime_dir))
        if has_schema:
            results.append(
                CheckResult(
                    "schemes",
                    "输入方案",
                    OK,
                    "配置目录中已存在方案文件",
                    i18n_key="doctor.check.schemes.ok_files",
                )
            )
        else:
            results.append(
                CheckResult(
                    "schemes",
                    "输入方案",
                    BAD,
                    "尚未安装任何输入法方案",
                    "请前往「方案市场」安装方案（如官方 preset 或雾凇拼音）",
                    i18n_key="doctor.check.schemes.missing",
                )
            )

    # 5. 部署状态（build/ 是 rime 的编译产物，缺失通常表现为无候选词）
    rime_dir = report.rime_user_dir
    if rime_dir is None:
        results.append(
            CheckResult(
                "deployed",
                "部署状态",
                WARN,
                "未能识别 rime 用户目录",
                "请先安装引擎或到设置页确认前端",
                i18n_key="doctor.check.deployed.no_dir",
            )
        )
    elif not _is_dir(rime_dir):
        # 目录都没有时「未部署」虽然成立，但一键修复必然失败（脚本无处可写）
        results.append(
            CheckResult(
                "deployed",
                "部署状态",
                WARN,
                f"{rime_dir} 尚不存在，无法判断部署状态",
                "安装引擎或方案后 rime 会自动创建该目录",
                i18n_key="doctor.check.deployed.absent",
                i18n_params={"path": str(rime_dir)},
            )
        )
    elif not _dir_readable(rime_dir):
        results.append(
            CheckResult(
                "deployed",
                "部署状态",
                WARN,
                f"{rime_dir} 当前用户无读取权限，无法判断部署状态",
                "该目录可能由 root 创建（曾用 sudo 运行 rime）："
                f"sudo chown -R \"$USER\" {rime_dir} 后再体检",
                i18n_key="doctor.check.deployed.unreadable",
                i18n_params={"path": str(rime_dir)},
            )
        )
    elif _has_build(rime_dir):
        results.append(
            CheckResult(
                "deployed",
                "部署状态",
                OK,
                f"已部署：{rime_dir / 'build'}",
                i18n_key="doctor.check.deployed.ok",
                i18n_params={"path": str(rime_dir / "build")},
            )
        )
    else:
        results.append(
            CheckResult(
                "deployed",
                "部署状态",
                BAD,
                f"{rime_dir} 下无 build 目录，尚未完成部署",
                "一键修复将编译生成 build（失败会保留原配置并提示原因；"
                "Ubuntu 若报找不到 rime_deployer，需先装 librime-bin）",
                fixable=report.engine_installed,
                i18n_key="doctor.check.deployed.missing_build",
                i18n_params={"path": str(rime_dir)},
            )
        )

    # 5.5 输入源注册（v1.0.6）：GNOME/Cinnamon 会话必须显式把 Rime 加入输入源才能切换使用
    if frontend == "ibus-rime":
        resolved = input_source_schema(report.desktop)
        sources = _read_input_sources(resolved[0]) if resolved else None
        if resolved is None:
            desktop = report.desktop or "未知桌面"
            results.append(
                CheckResult(
                    "input_source",
                    "输入源注册",
                    INFO,
                    f"当前桌面（{desktop}）不使用 GNOME/Cinnamon 输入源配置，跳过"
                    "（DDE/KDE 由桌面自带的输入法模块管理，gsettings 写了也不生效）",
                    "如切换不到 Rime，请到桌面「输入法」设置里添加「中文(Rime)」",
                    i18n_key="doctor.check.input_source.ibus_non_gnome",
                    i18n_params={"desktop": desktop},
                )
            )
        elif sources is None:
            # v1.0.29：桌面认对了、但读不到配置（gsettings 缺失 / 系统未装该
            # schema，如精简版 Mint）——此时既不报 BAD（无从判断）也不谎称跳过。
            results.append(
                CheckResult(
                    "input_source",
                    "输入源注册",
                    INFO,
                    f"未能读取 {resolved[1]} 输入源配置（gsettings 不可用或系统未提供"
                    f" {resolved[0]}），无法判断",
                    "如切换不到 Rime，请到桌面「键盘 → 输入法」里手动添加「中文(Rime)」",
                    i18n_key="doctor.check.input_source.ibus_unreadable",
                    i18n_params={"desktop": resolved[1], "schema": resolved[0]},
                )
            )
        elif re.search(r"\('ibus',\s*'rime'\)", sources):
            label = resolved[1]
            results.append(
                CheckResult(
                    "input_source",
                    "输入源注册",
                    OK,
                    f"{label} 输入源已包含 中文(Rime)",
                    i18n_key="doctor.check.input_source.ibus_ok",
                    i18n_params={"desktop": label},
                )
            )
        else:
            label = resolved[1]
            # v1.0.26 第二步：ibus-rime 没装就不要往输入源里写一个指向空引擎的条目
            ibus_ready = report.engine_packages.get("ibus-rime", False)
            results.append(
                CheckResult(
                    "input_source",
                    "输入源注册",
                    BAD,
                    f"{label} 输入源未添加「中文(Rime)」——不添加则无法切换到 Rime"
                    f"（注意：ibus-setup 添加在 {label} 会话下不生效）",
                    "一键修复将把 中文(Rime) 写入桌面输入源并重启 ibus；"
                    "也可在 系统设置 → 键盘 → 输入源 手动添加"
                    if ibus_ready
                    else "请先到「引擎安装」页安装 ibus-rime，再回来添加输入源",
                    fixable=ibus_ready,
                    i18n_key="doctor.check.input_source.ibus_missing"
                    if ibus_ready
                    else "doctor.check.input_source.ibus_no_engine",
                    i18n_params={"desktop": label},
                )
            )

    # 5.6 fcitx5 输入法列表（v1.0.8）：装了 fcitx5-rime 不等于能用——必须把
    # 「中州韵」加进 fcitx5 的输入法列表（~/.config/fcitx5/profile 的 IMList），
    # 否则切换不到 Rime。Deepin/KDE 下这是与 GNOME 输入源等价的高频坑。
    elif frontend == "fcitx5-rime":
        methods = _fcitx5_input_methods()
        if methods is None:
            results.append(
                CheckResult(
                    "input_source",
                    "输入法列表",
                    INFO,
                    "未检测到 fcitx5 输入法配置（~/.config/fcitx5/profile），"
                    "首次添加输入法后会自动生成",
                    i18n_key="doctor.check.input_source.fcitx5_no_conf",
                )
            )
        elif "rime" in methods:
            results.append(
                CheckResult(
                    "input_source",
                    "输入法列表",
                    OK,
                    "fcitx5 输入法列表已包含 中州韵(Rime)",
                    i18n_key="doctor.check.input_source.fcitx5_ok",
                )
            )
        else:
            results.append(
                CheckResult(
                    "input_source",
                    "输入法列表",
                    BAD,
                    f"fcitx5 输入法列表未包含「中州韵(Rime)」（当前：{methods or '空'}）"
                    "——不添加则无法切换到 Rime 输入",
                    "点本页「配置工具」打开输入法配置 → 在输入法列表中搜索并添加"
                    "「中州韵(Rime)」并上移到首位（Deepin：控制中心 → 键盘和语言 → 输入法）",
                    i18n_key="doctor.check.input_source.fcitx5_missing",
                    i18n_params={"methods": methods or "空"},
                )
            )
    elif frontend == "fcitx-rime":
        results.append(
            CheckResult(
                "input_source",
                "输入法列表",
                INFO,
                "旧版 fcitx 的输入法列表需在图形配置中确认已添加 Rime",
                "点本页「配置工具」打开 fcitx 配置 → 输入法标签页添加「Rime」"
                "（若配置工具打不开：sudo apt install fcitx-config-gtk）",
                i18n_key="doctor.check.input_source.fcitx_legacy",
            )
        )

    # 6. 默认输入法框架（~/.xinputrc）
    xinputrc = Path.home() / ".xinputrc"
    configured = ""
    if xinputrc.is_file():
        try:
            text = xinputrc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        # v1.0.26：只认真正的指令行。旧实现用 `"im" in line` 抓首行，
        # 命中的是 im-config 生成的注释「# im-config(8) generated on ...」，
        # 于是 `run_im fcitx5` 的标准配置被误报成「与当前前端不一致」。
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("run_im"):
                parts = line.split()
                configured = parts[-1] if len(parts) > 1 else ""
                break
            if line in ("ibus", "fcitx", "fcitx5", "none", "auto"):
                configured = line
                break
    if not configured:
        if report.running_frontends:
            # v1.0.7：框架已在会话中运行 = 由桌面会话自动拉起，xinputrc 非必须；
            # 旧版恒显 i「未检测到」被用户当作一直修不掉的问题（真机反馈）
            results.append(
                CheckResult(
                    "autostart",
                    "默认输入法框架",
                    OK,
                    "框架已由桌面会话自动运行，无需 ~/.xinputrc 配置",
                    i18n_key="doctor.check.autostart.session_managed",
                )
            )
        else:
            results.append(
                CheckResult(
                    "autostart",
                    "默认输入法框架",
                    INFO,
                    "未检测到 ~/.xinputrc 配置，且框架未运行（多数桌面可自动识别）",
                    "如登录后框架未自动启动，可运行 im-config 手动指定"
                    "（缺少 zenity/kdialog/whiptail 的桌面无法运行 im-config）",
                    i18n_key="doctor.check.autostart.no_config",
                )
            )
    elif configured == "none":
        results.append(
            CheckResult(
                "autostart",
                "默认输入法框架",
                INFO,
                "~/.xinputrc 已禁用输入法框架（run_im none）",
                "如需使用 Rime，请重新运行 im-config 选择 ibus / fcitx5",
                i18n_key="doctor.check.autostart.disabled",
            )
        )
    else:
        expected_hint = "ibus" if frontend == "ibus-rime" else "fcitx"
        if expected_hint in configured.lower():
            results.append(
                CheckResult(
                    "autostart",
                    "默认输入法框架",
                    OK,
                    f"~/.xinputrc：{configured}",
                    i18n_key="doctor.check.autostart.ok",
                    i18n_params={"configured": configured},
                )
            )
        else:
            results.append(
                CheckResult(
                    "autostart",
                    "默认输入法框架",
                    WARN,
                    f"~/.xinputrc 指向的框架与当前前端（{frontend}）不一致",
                    "可重新运行 im-config 选择正确的输入法框架",
                    i18n_key="doctor.check.autostart.mismatch",
                    i18n_params={"frontend": frontend, "configured": configured},
                )
            )

    # 7. 配置目录可访问性
    if rime_dir is None:
        results.append(
            CheckResult(
                "rime_dir",
                "配置目录",
                BAD,
                "未找到任何前端的 rime 用户目录",
                i18n_key="doctor.check.rime_dir.no_dir",
            )
        )
    elif _is_dir(rime_dir):
        count = _child_count(rime_dir)
        if count is None:
            results.append(
                CheckResult(
                    "rime_dir",
                    "配置目录",
                    WARN,
                    f"{rime_dir} 存在但当前用户无读取权限",
                    f"目录可能由 root 创建：sudo chown -R \"$USER\" {rime_dir}",
                    i18n_key="doctor.check.rime_dir.unreadable",
                    i18n_params={"path": str(rime_dir)},
                )
            )
        else:
            results.append(
                CheckResult(
                    "rime_dir",
                    "配置目录",
                    OK,
                    f"{rime_dir}（{count} 项）",
                    i18n_key="doctor.check.rime_dir.ok",
                    i18n_params={"path": str(rime_dir), "count": str(count)},
                )
            )
    else:
        results.append(
            CheckResult(
                "rime_dir",
                "配置目录",
                WARN,
                f"{rime_dir} 尚不存在（安装引擎或方案后将自动创建）",
                "安装方案后 rime 会自动创建该目录",
                i18n_key="doctor.check.rime_dir.absent",
                i18n_params={"path": str(rime_dir)},
            )
        )

    # 8. apt 源代号一致性（v1.0.31）：系统升级后旧代号残留的探测。
    # 放在最后：它与 rime 本身无关，属于「升级后还能不能装」的基础设施检查；
    # 不可自动修复（改哪个源是用户的决定），只给 WARN 与处理指引。
    results.append(_apt_codename_check())

    # 9. apt 源可用性（v1.0.32）：定位失效 / 不可达的第三方源。
    # 探测本身有网络 IO，由体检页在 QThread 后台跑（run_checks 保持纯计算），
    # 结果落 apt_health 模块缓存后由这里渲染。同样不可自动修复——只引导
    # 用户手动禁用第三方源；官方源任何情况下都不给禁用指引。
    results.append(_apt_sources_check())

    return results


def build_fix_steps(key: str, report: EnvReport) -> list[Step]:
    """构建某项体检的修复步骤（UI 层对 fixable 项调用；空列表表示不可自动修复）。

    v1.0.34：frontend 与 run_checks 统一取 `effective_frontend`——修复动作
    必须打在「实际装着的引擎」上，而不是某次启动环境的瞬时快照。
    """
    frontend = report.effective_frontend
    rime_dir = report.rime_user_dir

    if key == "im_env":
        # v1.0.26 第二步：写 environment.d + .xsessionrc（旧实现的 ~/.xprofile
        # 在 Debian/Ubuntu/Deepin 都不被任何会话脚本读取）
        return [
            Step(
                "写入输入法环境变量（environment.d + .xsessionrc）",
                ["bash", "-c", im_env.build_write_script(frontend)],
                weight=1,
            )
        ]

    if key == "frontend_running":
        return [
            Step(
                f"启动输入法框架（{frontend}）",
                ["bash", "-c", _frontend_start_cmd(frontend)],
                weight=1,
            )
        ]

    if key == "deployed" and rime_dir is not None:
        # v1.0.6：主动部署（rime_deployer 编译生成 build），不再依赖框架惰性部署
        # v1.0.26：编译进 staging，失败保留原 build 并 exit 1（不再假成功）
        return [
            Step(
                "重新部署（rime_deployer 编译 + 重启框架）",
                ["bash", "-c", build_deploy_script(frontend, rime_dir)],
                weight=1,
                hint_key="msg.deploy_failed",
            )
        ]

    if key == "input_source" and frontend == "ibus-rime":
        # v1.0.26：非 GNOME/Cinnamon 桌面不写 gsettings（写了没人读，还会误报已修复）
        resolved = input_source_schema(report.desktop)
        if resolved is None:
            return []
        schema, label = resolved
        return [
            Step(
                f"将 中文(Rime) 加入 {label} 输入源并重启 ibus",
                ["bash", "-c", _build_input_source_fix_script(frontend, schema)],
                weight=1,
            )
        ]

    return []


def build_fix_all_steps(
    results: list[CheckResult], report: EnvReport
) -> list[Step]:
    """汇总所有可修复项的步骤（按体检项顺序）。"""
    steps: list[Step] = []
    for result in results:
        if result.fixable:
            steps.extend(build_fix_steps(result.key, report))
    return steps
