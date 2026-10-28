"""引擎装卸服务：构建 apt 步骤序列 + 用户目录安全清理。

- 提权：优先 pkexec（图形密码框），缺失时（容器/开发环境）降级为直接执行；
- v1.0.5：整个装卸任务合并为**单次 pkexec**（update/install、purge/autoremove
  串在同一个 bash -c 脚本里）。此前每步一次提权 = 每步弹一次 polkit 密码框，
  真机反馈「安装时提示好几次输入密码」；且 apt 满载 + 叠加模态对话框让用户
  感知系统卡死。合并后一次密码框走完整个任务。
- v1.0.5：apt 统一经 nice + ionice -c3 降优先级（真机反馈「安装过程中
  Ubuntu 界面非常卡然后卡死，只有鼠标能动」）：38MB 下载 + 20 包解包属重
  I/O 负载，降到空闲级后合成器/桌面保持响应。
- 用户目录清理仅允许 home 下的路径，杜绝误删系统目录。
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import config
from core.runner import PERCENT_RE, Step, shell_wrap


def engine_package(frontend: str) -> str:
    """前端键（ibus-rime/fcitx5-rime/fcitx-rime）→ apt 包名。"""
    return config.ENGINE_PACKAGES.get(frontend, "ibus-rime")


def _program(name: str, fallback: str) -> str:
    return shutil.which(name) or fallback


def apt_program() -> str:
    return _program("apt-get", "/usr/bin/apt-get")


def dpkg_query_program() -> str:
    return _program("dpkg-query", "/usr/bin/dpkg-query")


def pkexec_program() -> str | None:
    return shutil.which("pkexec")


def _deprioritize(argv: list[str]) -> list[str]:
    """给命令包上 nice/ionice 降优先级前缀（工具缺失时自动降级为原命令）。

    nice -n 10 降 CPU 调度权重；ionice -c 3 把磁盘 I/O 降到 idle 级——
    安装期间桌面/合成器仍能抢到 CPU 与 I/O，缓解「系统卡死只有鼠标能动」。
    """
    prefix: list[str] = []
    nice = shutil.which("nice")
    if nice:
        prefix += [nice, "-n", "10"]
    ionice = shutil.which("ionice")
    if ionice:
        prefix += [ionice, "-c", "3"]
    return [*prefix, *argv]


def apt_command(apt: str, *args: str) -> str:
    """降优先级 apt 子命令的 shell 字符串形式（供 && 串联/嵌入脚本）。"""
    return shlex.join(_deprioritize([apt, *[str(a) for a in args]]))


def _elevated(argv: list[str]) -> list[str]:
    """提权包装：pkexec env DEBIAN_FRONTEND=noninteractive <cmd>，无 pkexec 时降级。"""
    env_program = _program("env", "/usr/bin/env")
    prefixed = [env_program, "DEBIAN_FRONTEND=noninteractive", *argv]
    pkexec = pkexec_program()
    if pkexec:
        return [pkexec, *prefixed]
    return prefixed


def elevated_command(argv: list[str]) -> list[str]:
    """提权包装的公开入口（v1.0.37：残留清理的 chown 步复用同一套 polkit 逻辑）。"""
    return _elevated(argv)


def _single_step(
    title: str,
    script: str,
    weight: float,
    progress: bool = True,
    hint_key: str = "",
) -> list[Step]:
    """把整段 bash 脚本打成一个 Step：外层 shell_wrap + 单次 pkexec 提权。"""
    return [
        Step(
            title,
            shell_wrap(_elevated(["bash", "-c", script])),
            weight=weight,
            progress_re=PERCENT_RE if progress else None,
            hint_key=hint_key,
        )
    ]


#: apt update 失败时的日志留痕（v1.0.29；v1.0.30 起镜像切换共用）
UPDATE_FAILED_NOTE = (
    "警告：软件源索引更新失败（失效的第三方源 404 / 缺公钥最常见），"
    "已忽略该错误，改用本地已有索引继续安装"
)


def build_install_steps(frontend: str, update_first: bool = True) -> list[Step]:
    """引擎安装：apt update + apt install 合并为单步单次提权（v1.0.5）。

    update_first=False（用户取消勾选）时只执行 install，同样只有一次密码框。
    步骤内 echo 分隔线便于在合并后的单步日志里区分两个阶段。

    v1.0.29 两处修正（Linux Mint 22.3 真机实证，日志退出码 100）：

    1. **apt update 改为尽力而为**。旧实现 `apt-get update && ... && apt-get
       install`，而 apt update 只要遇到任一失效第三方源（本例：
       `ci.deepin.com/.../xUbuntu_24.04` 已 404）就以 100 退出，`&&` 后面
       整条链被短路——引擎根本没开始装，用户只看到「更新软件源索引并安装
       引擎」失败。这不是 Mint 特有：任何挂着失效源的机器都会踩。现在用
       `{ update || echo 警告; }` 吞掉非零退出码，让 install 照常走本地索引。
    2. **顺手安装 rime_deployer 所属包**（``ENGINE_DEPLOYER_PACKAGE``）。
       noble/numbat 的 ibus-rime 与 fcitx5-rime 都不依赖 librime-bin，
       缺它时「重新部署」只能退回重启框架 + 轮询，体检项长期修不绿。
       该包同样 best-effort：装不上（源里没有）只警告，不判任务失败。

    v1.0.35：**fcitx4 顺手补装图形配置工具**（``FRONTEND_EXTRA_PACKAGES``）。
    fcitx-config-gtk（提供 fcitx-configtool）不在 fcitx-rime 的依赖链里，
    纯净 fcitx4 机器上「打开配置工具」静默失败，而 fcitx4 添加输入法只能
    靠这个 GUI。按前端门控：只有 fcitx-rime 会追加，ibus / fcitx5 的安装链
    一字不变（它们的配置工具分别由 ibus / fcitx5 包自带或 Recommends 随装）。
    同样 best-effort。卸载时不主动 purge——用户可能自装、也可能 fcitx4 还在
    给其他输入法用，孤立包交给 apt autoremove 决定。
    """
    apt = apt_program()
    pkg = engine_package(frontend)
    install_cmd = apt_command(apt, "install", "-y", pkg)
    deployer = config.ENGINE_DEPLOYER_PACKAGE
    deployer_part = (
        "{ " + apt_command(apt, "install", "-y", deployer)
        + f" || echo '===== 提示：{deployer} 未能安装，"
        "稍后「重新部署」会退回重启框架的方式 ====='; }"
    )
    tail_parts = [deployer_part]
    for extra in config.FRONTEND_EXTRA_PACKAGES.get(frontend, []):
        tail_parts.append(
            "{ " + apt_command(apt, "install", "-y", extra)
            + f" || echo '===== 提示：{extra} 未能安装（源里可能没有），"
            "「打开配置工具」按钮可能无法使用 ====='; }"
        )
    tail = " && ".join(tail_parts)
    if update_first:
        update_part = (
            "{ " + apt_command(apt, "update")
            + f" || echo '===== {UPDATE_FAILED_NOTE} ====='; }}"
        )
        script = (
            f"{update_part}"
            f" && echo '===== 开始安装引擎 {pkg} ====='"
            f" && {install_cmd}"
            f" && {tail}"
        )
        title = f"更新软件源索引并安装引擎 {pkg}（apt update + install）"
    else:
        script = f"{install_cmd} && {tail}"
        title = f"安装引擎 {pkg}（apt install）"
    return _single_step(
        title, script, weight=4, hint_key="msg.engine_install_failed"
    )



def build_deployer_install_step() -> Step:
    """v1.0.37：缺 rime_deployer 时的修复步——best-effort 安装 librime-bin。

    各发行版的 ibus-rime / fcitx5-rime **都不依赖** librime-bin（包依赖与
    文件清单已核），手动按论坛教程装过引擎的机器基本都没有它——「重新部署」
    只能靠引擎惰性部署，成功率低（UOS 25 真机反馈的根因之一）。

    整步包在 `{ ... || echo 提示; }` 里：apt update 失败（失效第三方源）、
    源里没有该包、用户取消密码框，都只警告不判失败——部署脚本随后会走
    「重启框架 + 强制全量重建」兜底，仍有成功的可能。
    """
    apt = apt_program()
    pkg = config.ENGINE_DEPLOYER_PACKAGE
    update = apt_command(apt, "update")
    install = apt_command(apt, "install", "-y", pkg)
    script = (
        "{ " + update + " || echo '===== 提示：apt 索引更新失败"
        "（失效第三方源最常见），继续尝试安装 ====='; }"
        " && { " + install + f" || echo '===== 提示：{pkg} 未能安装"
        "（软件源里可能没有该包，或源需要激活），将改用重启框架的方式部署 ====='; }"
    )
    return Step(
        f"安装部署工具 {pkg}（提供 rime_deployer）",
        shell_wrap(_elevated(["bash", "-c", script])),
        weight=1,
    )


def build_uninstall_steps(frontend: str) -> list[Step]:
    """引擎卸载：apt purge + autoremove 合并为单步单次提权（v1.0.5）。

    v1.0.35：**不主动 purge FRONTEND_EXTRA_PACKAGES 里的附加包**
    （如 fcitx-config-gtk）——它可能是用户自己装的，且卸载 rime 引擎不
    代表用户不用 fcitx4 了（其他输入法仍在用时 purge 会打断人家配置）。
    安装时它是手动标记（apt-get install 默认行为），autoremove 不会动它；
    真嫌占地方用户自行 `apt purge fcitx-config-gtk` 即可。
    """
    apt = apt_program()
    pkg = engine_package(frontend)
    script = (
        f"{apt_command(apt, 'purge', '-y', pkg)}"
        f" && echo '===== 引擎已卸载，清理不再使用的依赖 ====='"
        f" && {apt_command(apt, 'autoremove', '-y')}"
    )
    return _single_step(
        f"卸载引擎 {pkg} 并清理孤立依赖（apt purge + autoremove）",
        script,
        weight=3,
        progress=False,
    )


def user_data_dirs(frontend: str, home: Path | None = None) -> list[Path]:
    """该前端对应的 rime 用户配置目录（与 detector 的映射一致）。"""
    home = home or Path.home()
    rel = config.RIME_RELATIVE_DIRS.get(frontend, config.RIME_RELATIVE_DIRS["ibus-rime"])
    return [home / rel]


def purge_user_data(frontend: str, home: Path | None = None) -> list[Path]:
    """删除用户配置目录（危险操作，必须由用户显式确认后调用）。

    安全护栏：仅删除位于 home 目录下、且真实存在的目录；任何异常都跳过。
    """
    home = (home or Path.home()).resolve()
    removed: list[Path] = []
    for directory in user_data_dirs(frontend, home):
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved == home or home not in resolved.parents:
            continue
        if not resolved.is_dir():
            continue
        shutil.rmtree(resolved, ignore_errors=True)
        removed.append(resolved)
    return removed


def query_installed(frontend: str) -> bool:
    """同步查询引擎包安装状态（dpkg-query，best-effort）。"""
    pkg = engine_package(frontend)
    try:
        result = subprocess.run(
            [dpkg_query_program(), "-W", "-f=${Status}", pkg],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "install ok installed" in (result.stdout or "")
