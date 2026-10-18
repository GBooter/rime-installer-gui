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


def _single_step(title: str, script: str, weight: float, progress: bool = True) -> list[Step]:
    """把整段 bash 脚本打成一个 Step：外层 shell_wrap + 单次 pkexec 提权。"""
    return [
        Step(
            title,
            shell_wrap(_elevated(["bash", "-c", script])),
            weight=weight,
            progress_re=PERCENT_RE if progress else None,
        )
    ]


def build_install_steps(frontend: str, update_first: bool = True) -> list[Step]:
    """引擎安装：apt update + apt install 合并为单步单次提权（v1.0.5）。

    update_first=False（用户取消勾选）时只执行 install，同样只有一次密码框。
    步骤内 echo 分隔线便于在合并后的单步日志里区分两个阶段。
    """
    apt = apt_program()
    pkg = engine_package(frontend)
    install_cmd = apt_command(apt, "install", "-y", pkg)
    if update_first:
        script = (
            f"{apt_command(apt, 'update')}"
            f" && echo '===== 软件源索引已更新，开始安装引擎 ====='"
            f" && {install_cmd}"
        )
        title = f"更新软件源索引并安装引擎 {pkg}（apt update + install）"
    else:
        script = install_cmd
        title = f"安装引擎 {pkg}（apt install）"
    return _single_step(title, script, weight=4)


def build_uninstall_steps(frontend: str) -> list[Step]:
    """引擎卸载：apt purge + autoremove 合并为单步单次提权（v1.0.5）。"""
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
