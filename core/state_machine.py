"""按钮状态机（F7）：前置条件满足才可点击；操作进行中全部禁用。

core 层不依赖 Qt，可在 CLI/测试中直接使用；UI 层通过 rule() 查询每个动作的
可用性与禁用原因，实现「按钮置灰 + 提示为什么不可点」。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.detector import EnvReport


class InstallerState(Enum):
    NO_ENGINE = "引擎未安装"
    ENGINE_READY = "引擎已安装，待装方案"
    SCHEMES_INSTALLED = "方案已安装，待部署"
    DEPLOYED = "部署就绪"


@dataclass(frozen=True)
class ActionRule:
    action: str
    enabled: bool
    reason: str = ""


class StateMachine:
    """installed_schemes 来自 manifest（Phase 3 接入前的默认值 0）。"""

    def __init__(
        self,
        report: EnvReport,
        busy: bool = False,
        installed_schemes: int = 0,
    ) -> None:
        self.report = report
        self.busy = busy
        self.installed_schemes = installed_schemes

    @property
    def state(self) -> InstallerState:
        if not self.report.engine_installed:
            return InstallerState.NO_ENGINE
        if self.installed_schemes <= 0:
            return InstallerState.ENGINE_READY
        if self._deployed():
            return InstallerState.DEPLOYED
        return InstallerState.SCHEMES_INSTALLED

    def _deployed(self) -> bool:
        rime_dir = self.report.rime_user_dir
        if not rime_dir:
            return False
        # v1.0.26：无读权限时 is_dir() 抛 PermissionError，而 rule()/state 跑在
        # Qt 槽链里——未捕获异常会让 PyQt6 abort。按「未部署」处理即可。
        try:
            return (rime_dir / "build").is_dir()
        except OSError:
            return False

    def rule(self, action: str) -> ActionRule:
        if self.busy:
            return ActionRule(action, False, "操作进行中，请稍候…")

        if action == "install_engine":
            if self.report.engine_installed:
                return ActionRule(action, False, "引擎已安装，无需重复安装")
            return ActionRule(action, True)

        if action == "uninstall_engine":
            if not self.report.engine_installed:
                return ActionRule(action, False, "引擎尚未安装")
            return ActionRule(action, True)

        if action in ("install_scheme", "update_scheme", "deploy"):
            if not self.report.engine_installed:
                return ActionRule(action, False, "请先安装中州韵输入法引擎")
            return ActionRule(action, True)

        if action == "switch_scheme":
            # v1.0.38：切换当前输入方案。链路（入列 + 写 user.yaml + 部署重启）
            # 本身不需要引擎已装也能写文件，但没有引擎时重启框架没有意义、
            # 用户也装不了方案——统一要求先装引擎，与 install_scheme 一致。
            if not self.report.engine_installed:
                return ActionRule(action, False, "请先安装中州韵输入法引擎")
            return ActionRule(action, True)

        if action == "uninstall_scheme":
            if not self.report.engine_installed:
                return ActionRule(action, False, "请先安装中州韵输入法引擎")
            if self.installed_schemes <= 0:
                return ActionRule(action, False, "尚未安装任何输入法方案")
            return ActionRule(action, True)

        if action == "backup":
            if self.report.rime_user_dir is None:
                return ActionRule(action, False, "未能识别配置目录，请到设置页指定输入法前端")
            # v1.0.23：探测器在没有任何候选目录时会给出「预期路径」，它并不存在。
            # 对这种目录打包只会得到一个 0 文件的假备份，必须拦下。
            if not self.report.rime_user_dir.is_dir():
                return ActionRule(
                    action, False, "配置目录尚未创建（先启动一次输入法或安装方案后再备份）"
                )
            return ActionRule(action, True)

        if action == "restore_backup":
            if self.report.rime_user_dir is None:
                return ActionRule(action, False, "未能识别配置目录，请到设置页指定输入法前端")
            return ActionRule(action, True)

        if action in ("restore_snapshot", "export_diag", "open_log_dir"):
            return ActionRule(action, True)

        if action in ("doctor_fix", "write_login_env"):
            # 体检修复不依赖引擎：写环境变量、拉起框架等均可独立执行。
            # write_login_env 是「写入登录配置」的次要入口——与体检结果无关，
            # 任何状态都可以执行（已就绪时用于换前端后同步 / 幂等重写）。
            return ActionRule(action, True)

        if action in ("restart_frontend", "redeploy", "open_config_tool"):
            if not self.report.engine_installed:
                return ActionRule(action, False, "请先安装中州韵输入法引擎")
            return ActionRule(action, True)

        if action == "switch_mirror":
            name = (self.report.os_name or "").lower()
            if "ubuntu" not in name:
                # v1.0.29：Linux Mint 的源由 mint 自带工具维护
                # （official-package-repositories.list 同时含 ubuntu 镜像与
                # linuxmint-packages），改写 sources.list 会打乱 mint 的仓库组合，
                # 因此仍然只对 Ubuntu 开放，但把原因说清楚。
                reason = (
                    "apt 镜像切换仅支持 Ubuntu（Linux Mint 的源由 mint 自带工具管理，"
                    "改写会打乱它的仓库组合；可在「软件源」里换 ubuntu 镜像地址）"
                    if "mint" in name
                    else "apt 镜像切换仅支持 Ubuntu（Deepin 官方源在国内，无需切换）"
                )
                return ActionRule(action, False, reason)
            return ActionRule(action, True)

        if action == "appearance_theme":
            # v1.0.13：fcitx5 主题切换只写用户家目录配置，不依赖引擎
            return ActionRule(action, True)

        if action == "appearance_ibus":
            # v1.0.13：ibus 外观项写入 rime 用户目录，需先识别配置目录
            if self.report.rime_user_dir is None:
                return ActionRule(action, False, "未能识别配置目录，请到设置页指定输入法前端")
            return ActionRule(action, True)

        if action == "appearance_page_size":
            # v1.0.41：候选词数量写进 rime 用户目录的 default.custom.yaml 后重新
            # 部署——与 ibus 外观同款前置：先识别到配置目录，否则没有落点
            if self.report.rime_user_dir is None:
                return ActionRule(action, False, "未能识别配置目录，请到设置页指定输入法前端")
            return ActionRule(action, True)

        return ActionRule(action, False, "该功能将在后续版本提供")
