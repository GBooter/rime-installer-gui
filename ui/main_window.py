"""主窗口（Phase 3）：MSFluentWindow 骨架 + 检测分发 + 引擎/方案任务编排。

编排职责集中在主窗口：Manifest / LogManager / TaskRunner / busy 状态 /
确认对话框 / 结果反馈，页面只发射意图，保持 UI 与执行逻辑解耦。

方案安装闭环：
  清缓存 → aria2 多镜像下载 → 解压预置到 plum/package → rime-install
  → diff 快照记账（added/overwritten）→ 记录上游指纹（供更新检查）。
卸载闭环：按 manifest 删新增 + 还原覆盖 + 清 plum 包缓存（否则重装会复用旧内容）。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from qfluentwidgets import (
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    MSFluentWindow,
    PushButton,
)
from PyQt6.QtCore import QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QDesktopServices, QIcon
from PyQt6.QtWidgets import QDialog

import config
from core import appearance, i18n, im_env, theme
from core.apt_mirror import build_switch_steps, mirrors
from core.deploy_guide import (
    build_open_config_tool_steps,
    build_redeploy_steps,
    build_restart_steps,
)
from core.detector import EnvReport, detect
from core.doctor import build_fix_steps, run_checks
from core.engine_service import (
    build_install_steps,
    build_uninstall_steps,
    engine_package,
    purge_user_data,
    user_data_dirs,
)
from core.log_manager import LogManager
from core.manifest import Manifest
from core.runner import TaskRunner
from core.scheme_service import (
    build_check_steps,
    build_download_step,
    build_extract_step,
    build_install_steps as build_scheme_install_steps,
    clear_package_cache,
    package_tree_hash,
)
from core.schemes import SCHEME_MAP, SCHEMES, Scheme, custom_scheme, parse_spec
from core.theme_import import diagnose_repo as diagnose_theme_repo
from core.theme_import import enumerate_themes as enumerate_import_themes
from core.theme_import import install_all as install_import_themes
from core.skin import (
    build_apply_steps,
    build_revert_steps,
    detect_active_skin,
    skin_by_key,
)
from core.snapshot_service import (
    BackupInfo,
    build_backup_step,
    build_restore_step,
    delete_backup,
    export_backup,
    human_size,
    import_backup,
    list_backups,
)
from core.state_machine import StateMachine
from ui.pages.appearance_page import AppearancePage
from ui.pages.backup_page import BackupPage
from ui.pages.base import reap_worker
from ui.pages.doctor_page import DoctorPage
from ui.pages.engine_page import EnginePage
from ui.pages.log_page import LogPage
from ui.pages.scheme_page import SchemePage
from ui.pages.settings_page import SettingsPage
from ui.pages.welcome_page import WelcomePage

_INSTALL_PAGES = (
    "welcome_page",
    "engine_page",
    "scheme_page",
    "appearance_page",
    "backup_page",
    "doctor_page",
    "settings_page",
)

#: 由环境修复页发起、进度也必须显示在环境修复页的任务（v1.0.26 / v1.0.27）
_DOCTOR_TASKS = (
    "doctor-fix",
    "doctor-write-login",
    "frontend-restart",
    "frontend-redeploy",
    "config-tool",
)

#: 导航图标按语言键索引，切语言时与页面一一对应刷新文本
_NAV_ICONS = {
    "nav.welcome": FluentIcon.HOME,
    "nav.engine": FluentIcon.DOWNLOAD,
    "nav.scheme": FluentIcon.TILES,
    "nav.appearance": FluentIcon.PALETTE,
    "nav.backup": FluentIcon.SAVE,
    "nav.doctor": FluentIcon.BROOM,
    "nav.log": FluentIcon.HISTORY,
    "nav.settings": FluentIcon.SETTING,
}


def _safe_tag(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in text) or "task"


#: 皮肤 key（skin-*）→ 方案 key（addon:skin-*）：配置反读与偏好记录共用
_SKIN_SCHEME_KEYS = {s.skin_key: s.key for s in SCHEMES if s.is_skin}


class _DetectWorker(QThread):
    """后台环境检测：detect() 是纯计算 + 子进程调用，不碰任何 Qt 对象。"""

    done = pyqtSignal(object)

    def run(self) -> None:
        try:
            report = detect()
        except Exception:  # noqa: BLE001 — 检测失败回退空报告，UI 不崩
            report = EnvReport()
        self.done.emit(report)


class MainWindow(MSFluentWindow):
    def __init__(
        self,
        report: EnvReport | None = None,
        manifest: Manifest | None = None,
    ) -> None:
        super().__init__()
        config.ensure_user_dirs()
        self.report = report or detect()
        self.manifest = manifest or Manifest()
        self._installed_keys = self.manifest.installed_keys()
        self.sm = StateMachine(
            self.report, installed_schemes=len(self._installed_keys)
        )

        self.setWindowTitle(f"{i18n.tr('app.title')} v{config.VERSION}")
        # v1.0.9：窗口图标——自绘 FluentTitleBar 左上角 iconLabel 由
        # windowIconChanged 驱动，此前从未 setWindowIcon，标题栏左侧空白
        if config.APP_ICON.exists():
            self.setWindowIcon(QIcon(str(config.APP_ICON)))
        else:
            self.setWindowIcon(QIcon.fromTheme(config.APP_ID))
        # v1.0.7：固定横向比例 + 最小尺寸——此前页面内容 sizeHint 会把窗口
        # 协商成竖向长条（真机 1080×1030），这里显式钉住合理比例
        self.resize(1180, 770)
        self.setMinimumSize(940, 640)

        # 执行基础设施（信号接线在页面创建后执行）
        self.log_manager = LogManager()
        self.runner = TaskRunner(log_write=self.log_manager.write)
        # v1.0.7：runner 输出节流——apt/aria2 高频输出逐行直达 UI 会淹没
        # 主线程（GNOME 判定窗口无响应 → 悬停长时间转圈），缓冲后批量刷
        self._log_buffer: list[str] = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(120)
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self._task_kind = ""
        self._total_steps = 0
        self._purge_after_uninstall = False
        self._pending: dict = {}
        # v1.0.24：删主题时是否一并把 classicui.conf 切回了 default（决定完成提示）
        self._theme_delete_reset = False
        # v1.0.12：当前生效皮肤的方案 key（detect_active_skin 反读）
        self._active_skin_key = ""

        self.welcome_page = WelcomePage(self.report, self.sm, on_redetect=self.redetect_async)
        self.engine_page = EnginePage(self.report, self.sm)
        self.scheme_page = SchemePage(self.report, self.sm, self._installed_keys)
        # v1.0.13：外观页（fcitx5 主题 + ibus 全局项，Linux 真实可见）
        self.appearance_page = AppearancePage(self.report, self.sm)
        # 注意：此处不能用 _refresh_backups()——它还依赖尚未赋值完成的 self.backup_page
        self.backup_page = BackupPage(self.report, self.sm, list_backups())
        self.doctor_page = DoctorPage(self.report, self.sm, self._installed_keys)
        self.log_page = LogPage()
        self.settings_page = SettingsPage(self.report, self.sm)

        # 导航文本键与页面一一对应，切语言时按同一顺序刷新（见 _retranslate_nav）
        self._nav_items: list[tuple[object, str]] = [
            (self.welcome_page, "nav.welcome"),
            (self.engine_page, "nav.engine"),
            (self.scheme_page, "nav.scheme"),
            (self.appearance_page, "nav.appearance"),
            (self.backup_page, "nav.backup"),
            (self.doctor_page, "nav.doctor"),
            (self.log_page, "nav.log"),
            (self.settings_page, "nav.settings"),
        ]
        for page, key in self._nav_items:
            self.addSubInterface(page, _NAV_ICONS[key], i18n.tr(key))

        self.engine_page.install_requested.connect(self._on_install_requested)
        self.engine_page.uninstall_requested.connect(self._on_uninstall_requested)
        self.engine_page.cancel_requested.connect(self.runner.cancel)
        self.engine_page.log_requested.connect(lambda: self.switchTo(self.log_page))

        self.scheme_page.install_requested.connect(self._on_scheme_install)
        self.scheme_page.uninstall_requested.connect(self._on_scheme_uninstall)
        self.scheme_page.check_update_requested.connect(self._on_scheme_check_update)
        self.scheme_page.custom_install_requested.connect(self._on_custom_install)
        self.scheme_page.set_default_requested.connect(self._on_skin_set_default)
        self.scheme_page.restore_default_requested.connect(self._on_skin_restore_default)
        self.scheme_page.cancel_requested.connect(self.runner.cancel)
        self.scheme_page.log_requested.connect(lambda: self.switchTo(self.log_page))

        self.appearance_page.theme_switch_requested.connect(self._on_theme_switch)
        self.appearance_page.theme_delete_requested.connect(self._on_theme_delete)
        self.appearance_page.theme_revert_requested.connect(self._on_theme_revert)
        self.appearance_page.theme_import_requested.connect(self._on_theme_import)
        self.appearance_page.ibus_style_requested.connect(self._on_ibus_style)
        self.appearance_page.ibus_revert_requested.connect(self._on_ibus_revert)
        self.appearance_page.open_themes_dir_requested.connect(self._on_open_themes_dir)
        self.appearance_page.link_requested.connect(self._on_open_link)
        self.appearance_page.cancel_requested.connect(self.runner.cancel)
        self.appearance_page.log_requested.connect(lambda: self.switchTo(self.log_page))

        self.backup_page.backup_requested.connect(self._on_backup_create)
        self.backup_page.restore_requested.connect(self._on_backup_restore)
        self.backup_page.import_requested.connect(self._on_backup_import)
        self.backup_page.export_requested.connect(self._on_backup_export)
        self.backup_page.delete_requested.connect(self._on_backup_delete)
        self.backup_page.refresh_requested.connect(self._refresh_backups)
        self.backup_page.cancel_requested.connect(self.runner.cancel)
        self.backup_page.log_requested.connect(lambda: self.switchTo(self.log_page))

        self.doctor_page.fix_requested.connect(self._on_doctor_fix)
        self.doctor_page.write_login_requested.connect(self._on_write_login)
        self.doctor_page.restart_requested.connect(self._on_frontend_restart)
        self.doctor_page.redeploy_requested.connect(self._on_redeploy)
        self.doctor_page.config_tool_requested.connect(self._on_open_config_tool)
        self.doctor_page.redetect_requested.connect(self.redetect_async)
        self.doctor_page.cancel_requested.connect(self.runner.cancel)
        self.doctor_page.log_requested.connect(lambda: self.switchTo(self.log_page))

        self.settings_page.mirror_switch_requested.connect(self._on_mirror_switch)
        self.settings_page.mirror_speedtest_requested.connect(lambda: None)
        self.settings_page.theme_change_requested.connect(self._on_theme_change)
        self.settings_page.language_change_requested.connect(self._on_language_change)
        self.settings_page.log_requested.connect(lambda: self.switchTo(self.log_page))

        self._wire_runner()
        # v1.0.12：启动即反读一次皮肤当前态（配置 + 默认偏好）
        self._refresh_skin_state()
        self._refresh_status()

    # ---- runner 编排 ----

    def _wire_runner(self) -> None:
        runner = self.runner
        runner.step_started.connect(self._on_step_started)
        runner.progress.connect(self._on_progress)
        runner.status.connect(self._on_status)
        # v1.0.7：输出经缓冲节流后再上 UI（防高频日志淹没主线程）
        runner.output.connect(self._buffer_log_line)
        runner.finished.connect(self._on_task_finished)

    def _buffer_log_line(self, line: str) -> None:
        self._log_buffer.append(line)
        if not self._log_timer.isActive():
            self._log_timer.start()

    def _flush_log_buffer(self) -> None:
        if not self._log_buffer:
            self._log_timer.stop()
            return
        lines, self._log_buffer = self._log_buffer, []
        self.log_page.append_lines(lines)

    def _engine_kind(self) -> bool:
        return self._task_kind.startswith("engine")

    def _target_page(self):
        """按任务类型返回承担进度展示的页面（引擎 / 方案 / 外观 / 备份 / 体检）。

        v1.0.26：体检相关任务必须回到环境修复页。旧实现一律回落到
        scheme_page——点「一键修复」后进度条、状态文字、可用的「取消」都
        出现在「方案市场」页，本页毫无反馈（离屏实测取证）。
        """
        if self._engine_kind():
            return self.engine_page
        if self._task_kind.startswith("backup"):
            return self.backup_page
        if self._task_kind.startswith("appearance"):
            return self.appearance_page
        if self._task_kind in _DOCTOR_TASKS:
            return self.doctor_page
        return self.scheme_page

    def _start_task(self, kind: str, steps: list, running_key: str = "") -> None:
        if self.runner.running or not steps:
            return
        self._task_kind = kind
        self._total_steps = len(steps)
        session = self.log_manager.open_session(f"{kind}-{_safe_tag(running_key or self.report.recommended_frontend)}")
        self.log_page.set_session(session)
        self._set_busy(True)
        page = self._target_page()
        if page is self.scheme_page:
            page.set_busy(True, "准备中", running_key)
        elif page is self.backup_page:
            # 恢复不给取消入口：中途 kill 可能留下不完整配置（v1.0.23）
            page.set_busy(True, "准备中", cancellable=(kind != "backup-restore"))
        else:
            page.set_busy(True, "准备中")
        self.runner.run_steps(steps)

    def _on_step_started(self, index: int, title: str) -> None:
        self._target_page().set_step(index + 1, self._total_steps, title)

    def _on_progress(self, percent: int) -> None:
        self._target_page().set_progress(percent)

    def _on_status(self, text: str) -> None:
        self._target_page().set_status(text)

    # ---- 引擎任务 ----

    def _on_install_requested(self, update_first: bool) -> None:
        # v1.0.34：装哪个引擎以「已装引擎」为锚（effective_frontend），
        # 无引擎时才信运行中的框架/桌面猜测——避免装错包
        frontend = self.report.effective_frontend
        self._start_task("engine-install", build_install_steps(frontend, update_first=update_first))

    def _on_uninstall_requested(self, purge_user_data_flag: bool) -> None:
        frontend = self.report.effective_frontend
        pkg = engine_package(frontend)
        lines = [f"将执行：apt purge {pkg} 并清理孤立依赖。", f"目标前端：{frontend}"]
        if purge_user_data_flag:
            dirs = "、".join(str(d) for d in user_data_dirs(frontend))
            lines.append(f"并将删除配置目录：{dirs}")
            lines.append("（个人词库、自定义配置将一并删除，且不可恢复！）")
        box = MessageBox("确认卸载中州韵输入法引擎？", "\n".join(lines), self)
        if not box.exec():
            return
        self._purge_after_uninstall = purge_user_data_flag
        self._start_task("engine-uninstall", build_uninstall_steps(frontend))

    # ---- 方案任务 ----

    def _scheme_from_key(self, key: str) -> Scheme | None:
        return SCHEME_MAP.get(key)

    def _on_scheme_install(self, key: str) -> None:
        scheme = self._scheme_from_key(key)
        if scheme is not None:
            self._start_scheme_install(scheme)

    def _on_custom_install(self, spec_text: str) -> None:
        ref = parse_spec(spec_text or "")
        if ref is None:
            InfoBar.warning(
                title=i18n.tr("msg.parse_failed"),
                content=i18n.tr("msg.parse_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        scheme = custom_scheme(ref)
        self.scheme_page.add_scheme(scheme)
        self._start_scheme_install(scheme)

    def _new_work_dir(self, prefix: str) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        work = config.CACHE_DIR / f"{stamp}-{_safe_tag(prefix)}"
        work.mkdir(parents=True, exist_ok=True)
        return work

    def _start_scheme_install(self, scheme: Scheme) -> None:
        rime_dir = self.report.rime_user_dir
        if rime_dir is None:
            InfoBar.error(
                title=i18n.tr("msg.no_rime_dir"),
                content=i18n.tr("msg.no_rime_dir_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        # 皮肤（F15）：不下载仓库，写用户目录配色配置后重新部署
        if scheme.is_skin:
            skin = skin_by_key(scheme.skin_key)
            if skin is None:
                return
            snapshot = self.manifest.create_snapshot(rime_dir, scheme.key)
            steps = build_apply_steps(
                skin, rime_dir, self.report.recommended_frontend
            )
            self._pending = {"scheme": scheme, "snapshot": snapshot, "work": None}
            self._start_task("skin-apply", steps, running_key=scheme.key)
            return
        clean = self.scheme_page.cache_check.isChecked()
        work = self._new_work_dir(scheme.key)
        snapshot = self.manifest.create_snapshot(rime_dir, scheme.key)
        steps = build_scheme_install_steps(
            scheme, self.report.recommended_frontend, rime_dir, work, clean=clean
        )
        self._pending = {"scheme": scheme, "snapshot": snapshot, "work": work}
        self._start_task("scheme-install", steps, running_key=scheme.key)

    def _commit_scheme_install(self) -> None:
        """安装成功后记账：diff 快照 → 写 manifest → 记上游指纹 → 清下载缓存。"""
        scheme: Scheme | None = self._pending.get("scheme")
        snapshot: Path | None = self._pending.get("snapshot")
        work: Path | None = self._pending.get("work")
        rime_dir = self.report.rime_user_dir
        if scheme is None or snapshot is None or rime_dir is None:
            return
        added, overwritten = Manifest.diff_snapshot(snapshot, rime_dir)
        digest = package_tree_hash(config.PLUM_DIR / "package", scheme)
        package_id = self.manifest.add_package(
            scheme.key,
            scheme.name,
            scheme.source,
            self.report.recommended_frontend,
            rime_dir,
            snapshot,
            digest,
        )
        self.manifest.record_files(package_id, added, overwritten)
        self._refresh_installed()
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        if added or overwritten:
            InfoBar.success(
                title=f"「{scheme.name}」安装完成",
                content=f"新增 {len(added)} 个文件，覆盖更新 {len(overwritten)} 个文件；已记账，可一键卸载",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
        else:
            InfoBar.warning(
                title=i18n.tr("msg.no_change"),
                content=i18n.tr("msg.no_change_hint", name=scheme.name),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=7000,
            )

    def _commit_skin_apply(self) -> None:
        """皮肤应用成功后记账（F15 / v1.0.12）。

        与方案安装的区别：
        - 不记文件、不整文件还原——卸载只移除标记块，保留用户其他配置；
        - v1.0.12 起不再把其他皮肤置为未应用：「已安装」（进过清单）与
          「当前生效」（default.custom.yaml 挂载）解耦——已安装皮肤保留在
          清单里，供皮肤分区快捷切换条一键切换；互斥由配置文件本身保证
          （同一时间只有一套标记块），当前态由 detect_active_skin 反读。
        """
        scheme: Scheme | None = self._pending.get("scheme")
        snapshot: Path | None = self._pending.get("snapshot")
        rime_dir = self.report.rime_user_dir
        if scheme is None or snapshot is None or rime_dir is None:
            return
        package_id = self.manifest.add_package(
            scheme.key,
            scheme.name,
            scheme.source,
            self.report.recommended_frontend,
            rime_dir,
            snapshot,
            "",
        )
        self.manifest.record_files(package_id, [], [])
        # 皮肤卸载走标记块移除，快照无还原用途，用后即弃避免堆积
        self.manifest.discard_snapshot(snapshot)
        self._refresh_installed()
        skin = skin_by_key(scheme.skin_key)
        name = skin.name if skin is not None else scheme.name
        InfoBar.success(
            title=i18n.tr("msg.skin_applied", name=name),
            content=i18n.tr("msg.skin_applied_hint"),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=6000,
        )

    def _on_skin_set_default(self, key: str) -> None:
        """v1.0.12：把一款皮肤设为「我的默认」（只记安装器数据库，不写 rime 配置）。"""
        scheme = self._scheme_from_key(key)
        if scheme is None or not scheme.is_skin:
            return
        self.manifest.set_default_skin(key)
        self._refresh_skin_state()
        skin = skin_by_key(scheme.skin_key)
        name = skin.name if skin is not None else scheme.name
        InfoBar.success(
            title=i18n.tr("msg.default_skin_set", name=name),
            content=i18n.tr("msg.default_skin_set_hint"),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_skin_restore_default(self) -> None:
        """v1.0.12：一键恢复「我的默认皮肤」——复用既有皮肤应用管线。"""
        key = self.manifest.get_default_skin()
        scheme = self._scheme_from_key(key) if key else None
        if scheme is None:
            InfoBar.warning(
                title=i18n.tr("msg.no_default_skin"),
                content=i18n.tr("msg.no_default_skin_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        skin = skin_by_key(scheme.skin_key)
        name = skin.name if skin is not None else scheme.name
        if key == self._active_skin_key:
            InfoBar.info(
                title=i18n.tr("msg.already_default_skin"),
                content=i18n.tr("msg.already_default_skin_hint", name=name),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        if self.report.rime_user_dir is None:
            InfoBar.error(
                title=i18n.tr("msg.no_rime_dir"),
                content=i18n.tr("msg.no_rime_dir_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        self._start_scheme_install(scheme)
        InfoBar.info(
            title=i18n.tr("msg.default_skin_restored"),
            content=i18n.tr("msg.default_skin_restore_hint", name=name),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_scheme_uninstall(self, key: str) -> None:
        scheme = self._scheme_from_key(key)
        if scheme is None:
            return
        if not SchemePage.confirm_uninstall(scheme, self):
            return
        # 皮肤（F15）：无 plum 包缓存可清；仅当前生效款需要动配置文件
        if scheme.is_skin:
            was_default = key == self.manifest.get_default_skin()
            self.manifest.restore_package(key)
            if was_default:
                # 移除的正是默认皮肤：同步清除偏好，避免「恢复我的默认」落空
                self.manifest.set_default_skin("")
                InfoBar.info(
                    title=i18n.tr("msg.default_skin_cleared"),
                    content=i18n.tr("msg.default_skin_cleared_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                )
            self._refresh_installed()
            if key != self._active_skin_key:
                # 非当前生效款：配置块里不是它，清单除名即可，无需重新部署
                return
            rime_dir = self.report.rime_user_dir
            if rime_dir is None:
                InfoBar.error(
                    title=i18n.tr("msg.no_rime_dir"),
                    content=i18n.tr("msg.no_rime_dir_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
                return
            steps = build_revert_steps(rime_dir, self.report.recommended_frontend)
            self._pending = {"scheme": scheme, "snapshot": None, "work": None}
            self._start_task("skin-revert", steps, running_key=scheme.key)
            return
        stats = self.manifest.restore_package(key)
        cached = clear_package_cache(scheme)  # 否则 plumb 会跳过拉取、重装无效
        self.redetect()
        removed = [str(p) for p in cached if p]
        content = (
            f"新增文件删除 {stats['deleted']} 个，"
            f"被覆盖文件还原 {stats['restored']} 个"
        )
        if stats["missing"]:
            content += f"，{stats['missing']} 个文件已不在原位（跳过）"
        if removed:
            content += f"；已清理本地包缓存 {len(removed)} 处"
        InfoBar.success(
            title=f"「{scheme.name}」已卸载",
            content=content,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=6000,
        )

    def _on_scheme_check_update(self, key: str) -> None:
        scheme = self._scheme_from_key(key)
        if scheme is None:
            return
        work = self._new_work_dir(f"check-{scheme.key}")
        steps = build_check_steps(scheme, work)
        self._pending = {"scheme": scheme, "work": work}
        self._start_task("scheme-check-update", steps, running_key=scheme.key)

    def _report_update_check(self) -> None:
        scheme: Scheme | None = self._pending.get("scheme")
        work: Path | None = self._pending.get("work")
        if scheme is None or work is None:
            return
        upstream = package_tree_hash(work, scheme)
        local = self.manifest.package_tree_hash(scheme.key)
        shutil.rmtree(work, ignore_errors=True)
        if not local:
            InfoBar.info(
                title=i18n.tr("msg.no_baseline"),
                content=i18n.tr("msg.no_baseline_hint", name=scheme.name),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        if upstream and upstream != local:
            InfoBar.warning(
                title=i18n.tr("msg.update_found"),
                content=i18n.tr("msg.update_found_hint", name=scheme.name),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=7000,
            )
        else:
            InfoBar.success(
                title=i18n.tr("msg.up_to_date"),
                content=i18n.tr("msg.up_to_date_hint", name=scheme.name),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )

    # ---- 环境体检与部署引导任务（F10/F11） ----

    def _on_doctor_fix(self, keys: list) -> None:
        steps = []
        for key in keys:
            steps.extend(build_fix_steps(key, self.report))
        if not steps:
            InfoBar.info(
                title=i18n.tr("msg.nothing_fixable"),
                content=i18n.tr("msg.nothing_fixable_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        self._start_task("doctor-fix", steps, running_key="fix")

    def _on_write_login(self) -> None:
        """写入登录配置（v1.0.27）：体检已就绪时也能主动重写一份。

        为什么单独给入口：环境变量一旦写对，体检项就变绿、不再出现在
        「一键修复」里；可用户仍可能想重写（换过前端、手工编辑过配置、
        从别的机器恢复备份）。这条路径与体检结果无关，幂等，随时可跑。

        两个目标文件都在用户主目录下——写入本身**不需要管理员密码**；
        但它改的是「下次登录才生效」的环境变量，所以照样先弹确认框，把
        写了什么、何时生效、怎么撤销逐条写明，用户点确定才执行。
        """
        # v1.0.34：effective_frontend（已装引擎锚定）——瞬时信号会让
        # 环境变量/重启框架/配置工具打到不存在的框架上（Mint 真机实证）
        frontend = self.report.effective_frontend
        targets = im_env.login_targets()
        # 把要写的变量逐条摆出来（而不是只说「四个环境变量」）：
        # 这类改的是「下次登录」的全局行为，用户有权在点确定前看到具体值
        vars_text = "\n".join(
            f"  {key}={value}" for key, value in im_env.expected_vars(frontend).items()
        )
        box = MessageBox(
            i18n.tr("dialog.write_login_title"),
            i18n.tr(
                "dialog.write_login_body",
                conf=str(targets[0]),
                xs=str(targets[1]),
                frontend=frontend,
                vars=vars_text,
            ),
            self,
        )
        if not box.exec():
            return
        steps = build_fix_steps("im_env", self.report)
        if not steps:
            InfoBar.info(
                title=i18n.tr("msg.nothing_fixable"),
                content=i18n.tr("msg.nothing_fixable_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        self._start_task("doctor-write-login", steps, running_key="login-env")

    def _on_frontend_restart(self) -> None:
        # v1.0.34：effective_frontend（已装引擎锚定）——瞬时信号会让
        # 环境变量/重启框架/配置工具打到不存在的框架上（Mint 真机实证）
        frontend = self.report.effective_frontend
        steps = build_restart_steps(frontend)
        self._start_task("frontend-restart", steps, running_key="restart")

    def _on_redeploy(self) -> None:
        rime_dir = self.report.rime_user_dir
        # v1.0.34：effective_frontend（已装引擎锚定）——瞬时信号会让
        # 环境变量/重启框架/配置工具打到不存在的框架上（Mint 真机实证）
        frontend = self.report.effective_frontend
        if rime_dir is None:
            InfoBar.error(
                title=i18n.tr("msg.no_rime_dir"),
                content=i18n.tr("msg.no_rime_dir_short"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        steps = build_redeploy_steps(frontend, rime_dir)
        self._start_task("frontend-redeploy", steps, running_key="redeploy")

    def _on_open_config_tool(self) -> None:
        # v1.0.34：effective_frontend（已装引擎锚定）——瞬时信号会让
        # 环境变量/重启框架/配置工具打到不存在的框架上（Mint 真机实证）
        frontend = self.report.effective_frontend
        steps = build_open_config_tool_steps(frontend)
        self._start_task("config-tool", steps, running_key="config")

    def _on_mirror_switch(self, mirror_name: str) -> None:
        mirror = next((m for m in mirrors() if m.name == mirror_name), None)
        if mirror is None:
            return
        try:
            steps = build_switch_steps(mirror)
        except RuntimeError as exc:
            InfoBar.error(
                title=i18n.tr("msg.mirror_failed"),
                content=str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        self._start_task("mirror-switch", steps, running_key=mirror.name)

    def _on_theme_change(self, mode: str) -> None:
        """主题切换：持久化 + 应用（qfluentwidgets 全局 + 状态色板）+ 重填各页换色。"""
        theme.save_preference(mode)
        theme.apply_theme(mode)
        self._dispatch()

    def _on_language_change(self, lang: str) -> None:
        """语言切换：持久化 + 切换 + 刷新导航与各页文案。

        导航项的 objectName（= routeKey）在构造时定下后不再变动，因此这里只
        改显示文本，路由依然有效。
        """
        i18n.save_preference(lang)
        i18n.set_language(lang)
        self._retranslate_nav()
        for name in _INSTALL_PAGES + ("log_page",):
            page = getattr(self, name, None)
            if page is not None:
                page.retranslate()
        self.setWindowTitle(f"{i18n.tr('app.title')} v{config.VERSION}")
        self._dispatch()

    def _retranslate_nav(self) -> None:
        """按当前语言刷新导航项显示文本。"""
        for page, key in self._nav_items:
            widget = self.navigationInterface.widget(page.objectName())
            if widget is not None:
                widget.setText(i18n.tr(key))

    # ---- 外观任务（v1.0.13：fcitx5 主题 + ibus 全局项） ----

    def _on_theme_switch(self, theme_key: str) -> None:
        """切换 fcitx5 classicui 主题（内置主题先落盘，再改写 classicui.conf）。"""
        steps = appearance.build_theme_steps(
            theme_key,
            appearance.fcitx5_conf_path(),
            appearance.user_themes_dir(),
            appearance.theme_backup_path(),
        )
        self._start_task("appearance-theme", steps, running_key=theme_key)

    def _on_theme_revert(self) -> None:
        """恢复 classicui.conf 原件（本安装器改写前的备份）。"""
        steps = appearance.build_theme_revert_steps(
            appearance.fcitx5_conf_path(), appearance.theme_backup_path()
        )
        self._start_task("appearance-theme-revert", steps, running_key="revert")

    def _on_theme_delete(self, theme_key: str) -> None:
        """删除用户主题目录里的一款主题（v1.0.24）。

        「是否正在使用」以 classicui.conf 的实际取值为准，不用界面上缓存的
        `_current_theme`——用户在别处（fcitx5-configtool）改过主题时两者会
        不一致，那会把 conf 指向一个马上要被删掉的目录。
        """
        conf = appearance.fcitx5_conf_path()
        in_use = appearance.current_theme_key(conf) == theme_key
        self._theme_delete_reset = in_use
        steps = appearance.build_theme_delete_steps(
            theme_key,
            conf,
            appearance.user_themes_dir(),
            appearance.theme_backup_path(),
            reset_theme=in_use,
        )
        self._start_task("appearance-theme-delete", steps, running_key=theme_key)

    # ---- 从链接导入主题（v1.0.21） ----

    def _on_theme_import(self, spec_text: str) -> None:
        """下载用户给的仓库并整包导入其中的 fcitx5 主题（一次全导，不勾选）。"""
        ref = parse_spec(spec_text or "")
        if ref is None:
            InfoBar.warning(
                title=i18n.tr("msg.parse_failed"),
                content=i18n.tr("appearance.import_tip_invalid"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        work = self._new_work_dir(f"theme-import-{ref.repo}")
        steps = [
            build_download_step(ref, work),
            build_extract_step(ref, work, work / "repo"),
        ]
        self._pending = {"theme_ref": ref, "work": work}
        self._start_task("appearance-import", steps, running_key=ref.label)

    def _goto_theme_import(self, url: str) -> None:
        """方案市场的「不是 RIME 方案包」提示 → 带链接跳到外观页并预填。"""
        self.appearance_page.prefill_import(url)
        self.switchTo(self.appearance_page)

    def _commit_theme_import(self) -> None:
        """下载完成后：枚举 → 全量拷进用户主题目录 → 刷列表 + 汇总提示。

        不写 manifest（v1.0.21 刻意保持极简）：主题是纯文件资产，删除入口
        交给「打开主题目录」，不为它引入一套记账/卸载链路。
        """
        ref = self._pending.get("theme_ref")
        work = self._pending.get("work")
        if ref is None or work is None:
            return
        root = work / "repo"
        themes = enumerate_import_themes(root)
        if not themes:
            # 空仓库不等于「用户贴错了」：先嗅探是手机端主题、配色方案还是
            # 需要构建的源码，能判定就给出确定结论，判不出来才说通用话术。
            hint = diagnose_theme_repo(root)
            title_key = hint or "msg.theme_import_empty"
            hint_key = f"{title_key}_hint" if hint else "msg.theme_import_empty_hint"
            InfoBar.warning(
                title=i18n.tr(title_key),
                content=i18n.tr(hint_key, name=ref.label),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=14000,
            )
            shutil.rmtree(work, ignore_errors=True)
            return
        installed, failed = install_import_themes(root, themes, appearance.user_themes_dir())
        self.appearance_page.refresh_themes(switch_to_custom=bool(installed))
        shutil.rmtree(work, ignore_errors=True)
        if not installed:
            InfoBar.error(
                title=i18n.tr("msg.failed"),
                content="; ".join(failed)[:200],
                parent=self,
                position=InfoBarPosition.TOP,
                duration=10000,
            )
            return
        content = i18n.tr("msg.theme_imported_hint")
        if failed:
            content += f"（{len(failed)} 款失败）"
        InfoBar.success(
            title=i18n.tr("msg.theme_imported", count=len(installed)),
            content=content,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=8000,
        )
        self.appearance_page.clear_import_input()

    def _on_ibus_style(self, horizontal: bool, inline_preedit: bool, color: str) -> None:
        """写入 ibus_rime.custom.yaml 的全局外观标记块（注销重登生效）。"""
        rime_dir = self.report.rime_user_dir
        if rime_dir is None:
            InfoBar.error(
                title=i18n.tr("appearance.ibus_no_dir"),
                content=i18n.tr("appearance.ibus_note"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        style = appearance.IbusStyle(
            horizontal=horizontal,
            inline_preedit=inline_preedit,
            color_scheme=color,
        )
        steps = appearance.build_ibus_style_steps(rime_dir, style)
        self._start_task("appearance-ibus", steps, running_key="ibus-style")

    def _on_ibus_revert(self) -> None:
        """移除 ibus 外观标记块，恢复 ibus-rime 原生外观。"""
        rime_dir = self.report.rime_user_dir
        if rime_dir is None:
            InfoBar.error(
                title=i18n.tr("appearance.ibus_no_dir"),
                content=i18n.tr("appearance.ibus_note"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        steps = appearance.build_ibus_revert_steps(rime_dir)
        self._start_task("appearance-ibus-revert", steps, running_key="ibus-revert")

    # ---- 打开主题目录（v1.0.16 市场移除后的自制主题落地入口） ----

    def _on_open_themes_dir(self) -> None:
        """在文件管理器中打开 fcitx5 用户主题目录。

        用户把自制主题文件夹放进来后，回外观页即可在列表里看到并一键切换。
        目录不存在则先创建，避免打开失败。
        """
        themes_dir = appearance.user_themes_dir()
        try:
            themes_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(themes_dir)))

    def _on_open_link(self, url: str) -> None:
        """用系统浏览器打开外部链接（外观页鸣谢区的上游仓库地址）。"""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ---- 备份任务（F12） ----

    def _refresh_backups(self) -> list[BackupInfo]:
        infos = list_backups()
        self.backup_page.set_backups(infos)
        return infos

    def _installed_package_names(self) -> list[str]:
        # 已安装方案（含皮肤）名列表，供备份/恢复页选择
        return [p.name for p in self.manifest.installed_packages()]

    def _on_backup_create(self, note: str) -> None:
        rime_dir = self.report.rime_user_dir
        # v1.0.23：目录不存在也要拦（探测器在没有任何候选目录时会给出一个
        # 「预期路径」，此前会被当成可用目录，打出 0 个文件的假备份）
        if rime_dir is None or not Path(rime_dir).is_dir():
            InfoBar.error(
                title=i18n.tr("msg.no_rime_dir"),
                content=i18n.tr("msg.no_rime_dir_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        step = build_backup_step(
            rime_dir,
            self.report.recommended_frontend,
            self._installed_package_names(),
            note or i18n.tr("backup.default_note"),
        )
        self._start_task("backup-create", [step], running_key="snapshot")

    def _on_backup_restore(self, zip_path: str, clean: bool, backup_first: bool) -> None:
        rime_dir = self.report.rime_user_dir
        info = next((b for b in self._backups_snapshot() if str(b.path) == zip_path), None)
        if info is None:
            info = next((b for b in list_backups() if str(b.path) == zip_path), None)
        if info is None:
            InfoBar.warning(
                title=i18n.tr("msg.restore_failed"),
                content=i18n.tr("msg.restore_failed_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        if rime_dir is None:
            # v1.0.23：此前直接 return，用户点「恢复选中」完全没反应
            InfoBar.error(
                title=i18n.tr("msg.no_rime_dir"),
                content=i18n.tr("msg.no_rime_dir_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        if not BackupPage.confirm_restore(info, clean, self, target=rime_dir):
            return
        steps = []
        if backup_first:
            # 目标目录不存在/为空时预备份会因 EmptyBackupError 失败并中断整个
            # 恢复——恢复本身是合法的（相当于全新部署），此时跳过预备份。
            target_usable = Path(rime_dir).is_dir() and any(Path(rime_dir).iterdir())
            if target_usable:
                steps.append(
                    build_backup_step(
                        rime_dir,
                        self.report.recommended_frontend,
                        self._installed_package_names(),
                        i18n.tr("backup.auto_note"),
                    )
                )
        steps.append(build_restore_step(Path(zip_path), rime_dir, clean=clean))
        self._start_task("backup-restore", steps, running_key="snapshot")

    def _backups_snapshot(self) -> list[BackupInfo]:
        return self.backup_page._backups

    def _on_backup_import(self, zip_path: str) -> None:
        try:
            info = import_backup(Path(zip_path))
        except (ValueError, OSError) as exc:
            # v1.0.23：此前只捕 ValueError——畸形元数据抛的 TypeError / 磁盘
            # 写失败的 OSError 会逃出槽函数，PyQt6 直接 abort（闪退）。
            InfoBar.error(
                title=i18n.tr("msg.import_failed"),
                content=str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=8000,
            )
            return
        except Exception as exc:  # noqa: BLE001 — 槽函数里绝不能让异常逃逸
            InfoBar.error(
                title=i18n.tr("msg.import_failed"),
                content=str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=8000,
            )
            return
        self._refresh_backups()
        self.backup_page.select_path(str(info.path))
        InfoBar.success(
            title=i18n.tr("msg.backup_imported"),
            content=i18n.tr(
                "msg.backup_imported_body",
                name=info.path.name,
                count=info.file_count,
            ),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_backup_export(self, zip_path: str, dest: str) -> None:
        try:
            target = export_backup(Path(zip_path), Path(dest))
        except Exception as exc:  # noqa: BLE001 — 槽函数内不能有异常逃逸（PyQt6 会 abort）
            InfoBar.error(
                title=i18n.tr("msg.export_failed"),
                content=str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        InfoBar.success(
            title=i18n.tr("msg.backup_exported"),
            content=str(target),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_backup_delete(self, zip_path: str) -> None:
        name = Path(zip_path).name
        box = MessageBox(
            i18n.tr("backup.delete_title"),
            i18n.tr("backup.delete_body", name=name),
            self,
        )
        if not box.exec():
            return
        if not delete_backup(Path(zip_path)):
            InfoBar.error(
                title=i18n.tr("msg.delete_failed"),
                content=i18n.tr("msg.delete_failed_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        self._refresh_backups()
        InfoBar.success(
            title=i18n.tr("msg.backup_deleted"),
            content=name,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    # ---- 任务收尾 ----

    def _on_task_finished(self, ok: bool, code: int) -> None:
        kind = self._task_kind
        # 先清 kind：本任务已结束。链式任务（主题市场 下载→勾选→安装）会在
        # 下方 _open_theme_picker 内经 _start_task 设置自己的 kind——若留到
        # 方法末尾才清，会把刚起步的链式任务 kind 擦掉（v1.0.14 冒烟实证：
        # 安装任务成功 InfoBar / 市场键刷新 / 进度路由全部失效）。
        self._task_kind = ""
        self._set_busy(False)
        self.engine_page.set_busy(False)
        self.scheme_page.set_busy(False)
        self.appearance_page.set_busy(False)
        self.backup_page.set_busy(False)
        # v1.0.26：环境修复页自己也有进度区/取消按钮，必须成对收尾。
        # 旧实现从不调用它的 set_busy——失败条纹与「取消」会一直留着。
        self.doctor_page.set_busy(False)
        self.log_page.reload_current()
        if kind.startswith("backup"):
            self._refresh_backups()

        if ok:
            if kind == "scheme-install":
                self._commit_scheme_install()
            elif kind == "skin-apply":
                self._commit_skin_apply()
            elif kind == "scheme-check-update":
                self._report_update_check()
            elif kind == "appearance-import":
                self._commit_theme_import()

        self.redetect_async()

        # v1.0.34：effective_frontend——成功提示里的包名必须与真正装着的
        # 引擎一致（Mint 真机：装着 ibus-rime 却提示 fcitx 相关文案）
        frontend = self.report.effective_frontend
        if ok:
            if kind == "backup-create":
                infos = self._refresh_backups()
                latest = infos[0] if infos else None
                InfoBar.success(
                    title=i18n.tr("msg.backup_done"),
                    content=(
                        i18n.tr(
                            "msg.backup_summary",
                            name=latest.path.name,
                            count=latest.file_count,
                            size=human_size(latest.size),
                        )
                        if latest
                        else i18n.tr("msg.backup_created")
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                )
            elif kind == "backup-restore":
                # v1.0.23：恢复只改文件，不重启框架/不重新部署——用户会认为
                # 「恢复了但没生效」。给一个就地入口（与外观页切主题一致）。
                bar = InfoBar.success(
                    title=i18n.tr("msg.restore_done"),
                    content=i18n.tr("msg.restore_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=10000,
                )
                restart = PushButton(i18n.tr("msg.restart_framework"))
                restart.setFixedHeight(26)
                restart.clicked.connect(self.doctor_page.restart_requested.emit)
                bar.addWidget(restart)
            elif kind == "engine-install":
                # v1.0.34：装完引擎只是第一步——ibus 系桌面（GNOME/Cinnamon/Mint）
                # 还必须把「中文(Rime)」加进输入源，否则就是真机反馈的
                # 「装完找不到中州韵输入法选项」。一键入口在体检页，这里直接
                # 给跳转按钮，别让用户自己去找。
                bar = InfoBar.success(
                    title=i18n.tr("msg.engine_installed"),
                    content=i18n.tr(
                        "msg.engine_install_hint", pkg=engine_package(frontend)
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=8000,
                )
                goto_doctor = PushButton(i18n.tr("msg.goto_doctor"))
                goto_doctor.setFixedHeight(26)
                goto_doctor.clicked.connect(lambda: self.switchTo(self.doctor_page))
                bar.addWidget(goto_doctor)
            elif kind in ("frontend-restart", "frontend-redeploy", "config-tool"):
                InfoBar.success(
                    title=i18n.tr("msg.op_done"),
                    content=i18n.tr("msg.framework_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                )
            elif kind == "doctor-fix":
                InfoBar.success(
                    title=i18n.tr("msg.fix_done"),
                    content=i18n.tr("msg.fix_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
            elif kind == "doctor-write-login":
                InfoBar.success(
                    title=i18n.tr("msg.login_written"),
                    content=i18n.tr(
                        "msg.login_written_hint", conf=str(im_env.env_conf_path())
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=8000,
                )
            elif kind == "mirror-switch":
                InfoBar.success(
                    title=i18n.tr("msg.mirror_switched"),
                    content=i18n.tr("msg.mirror_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                )
            elif kind == "skin-revert":
                InfoBar.success(
                    title=i18n.tr("msg.skin_removed"),
                    content=i18n.tr("msg.skin_removed_hint"),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
            elif kind == "appearance-theme-delete":
                # 删的正是当前主题时，任务里已把 conf 切回 default 并重启框架
                InfoBar.success(
                    title=i18n.tr("msg.theme_deleted"),
                    content=i18n.tr(
                        "msg.theme_deleted_hint_reset"
                        if self._theme_delete_reset
                        else "msg.theme_deleted_hint"
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
            elif kind in ("appearance-theme", "appearance-theme-revert"):
                InfoBar.success(
                    title=i18n.tr(
                        "msg.theme_reverted"
                        if kind.endswith("revert")
                        else "msg.theme_switched"
                    ),
                    content=i18n.tr(
                        "msg.theme_reverted_hint"
                        if kind.endswith("revert")
                        else "msg.theme_switched_hint"
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
            elif kind in ("appearance-ibus", "appearance-ibus-revert"):
                InfoBar.success(
                    title=i18n.tr(
                        "msg.ibus_style_reverted"
                        if kind.endswith("revert")
                        else "msg.ibus_style_applied"
                    ),
                    content=i18n.tr(
                        "msg.ibus_style_reverted_hint"
                        if kind.endswith("revert")
                        else "msg.ibus_style_applied_hint"
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
            elif kind == "engine-uninstall":
                content = i18n.tr(
                    "msg.engine_uninstalled_hint", pkg=engine_package(frontend)
                )
                if self._purge_after_uninstall:
                    removed = purge_user_data(frontend)
                    if removed:
                        paths = "、".join(str(p) for p in removed)
                        content += i18n.tr("msg.purge_suffix", paths=paths)
                        self.engine_page.append_note(
                            i18n.tr("msg.purge_note", paths=paths)
                        )
                InfoBar.success(
                    title=i18n.tr("msg.engine_uninstalled"),
                    content=content,
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
        elif code == -1:
            InfoBar.warning(
                title=i18n.tr("msg.cancelled"),
                content=i18n.tr("msg.cancel_hint"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=4000,
            )
        else:
            # v1.0.20：失败步骤可自带人话建议（Step.hint_key，约定同名 _hint 为正文）。
            # 典型场景：仓库下回来才发现不是 RIME 方案包——给「去哪儿装皮肤/方案」
            # 的方向，而不是只甩一个退出码。
            failed = self.runner.failed_step
            hint_key = getattr(failed, "hint_key", "") if failed is not None else ""
            if hint_key:
                pending_scheme = self._pending.get("scheme")
                bar = InfoBar.error(
                    title=i18n.tr(hint_key),
                    content=i18n.tr(
                        f"{hint_key}_hint",
                        name=getattr(pending_scheme, "name", "") or "",
                    ),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=12000,
                )
                # v1.0.21 跨页联动：贴的是主题仓库时，直接把链接带去外观页导入，
                # 省掉「看完提示 → 自己找入口 → 再粘一次」的往返。
                packages = getattr(pending_scheme, "packages", ()) if pending_scheme else ()
                if hint_key == "msg.not_rime_package" and packages:
                    url = f"https://github.com/{packages[0].user}/{packages[0].repo}"
                    goto = PushButton(i18n.tr("msg.go_import_theme"))
                    goto.setFixedHeight(26)
                    goto.clicked.connect(lambda: self._goto_theme_import(url))
                    bar.addWidget(goto)
            else:
                InfoBar.error(
                    title=i18n.tr("msg.failed"),
                    content=i18n.tr("msg.fail_hint", code=code),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=8000,
                )
        self._purge_after_uninstall = False
        self._pending = {}

    # ---- busy 状态（F7：操作中全部禁用） ----

    def _set_busy(self, busy: bool) -> None:
        self.sm = StateMachine(
            self.report,
            busy=busy,
            installed_schemes=len(self._installed_keys),
        )
        self._dispatch()

    def _refresh_installed(self) -> None:
        self._installed_keys = set(self.manifest.installed_keys())
        self.scheme_page.set_installed_keys(self._installed_keys)
        self._refresh_skin_state()

    def _refresh_skin_state(self) -> None:
        """v1.0.12：皮肤当前态（default.custom.yaml 反读）+ 我的默认（manifest 偏好）。"""
        rime_dir = self.report.rime_user_dir
        active = detect_active_skin(rime_dir) if rime_dir else None
        self._active_skin_key = _SKIN_SCHEME_KEYS.get(active.key, "") if active else ""
        self.scheme_page.set_skin_state(
            self._active_skin_key, self.manifest.get_default_skin()
        )

    # ---- 检测分发 ----

    def redetect(self) -> None:
        """重新检测环境并分发到所有页面（同步版：冒烟测试与既有调用保留）。"""
        self.report = detect()
        self._refresh_installed()
        self.sm = StateMachine(
            self.report,
            busy=self.runner.running,
            installed_schemes=len(self._installed_keys),
        )
        self._dispatch()

    def redetect_async(self) -> None:
        """v1.0.7：后台线程检测环境——detect() 内多个子进程各有 2s 超时，
        真机上叠加可达数秒，同步跑会令 GNOME 判定窗口无响应（悬停转圈）。"""
        worker = getattr(self, "_detect_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._detect_worker = _DetectWorker()
        self._detect_worker.done.connect(self._on_detect_done)
        self._detect_worker.start()

    def _on_detect_done(self, report) -> None:
        # v1.0.33：done 信号是在 run() 内部发出的，槽收到时线程可能仍走在
        # 退出序列里（isRunning() 仍为真）——此刻直接清引用会让最后一个
        # Python 包装对象立即销毁，~QThread() 撞 isRunning() → qFatal →
        # SIGABRT(134)。真机 Ubuntu 24.04 / Mint 22.3「点很多功能就闪退」
        # 的根因（每个任务结束都会触发一次 redetect_async）。先等线程真正
        # 退出再清引用；正常路径只阻塞毫秒级（详见 base.reap_worker）。
        worker = self._detect_worker
        if reap_worker(worker):
            self._detect_worker = None
        self.report = report
        try:
            self._refresh_installed()
        except Exception:  # noqa: BLE001 — 回调期 manifest 可能已关闭（测试收尾）
            return
        self.sm = StateMachine(
            self.report,
            busy=self.runner.running,
            installed_schemes=len(self._installed_keys),
        )
        self._dispatch()

    def closeEvent(self, event: QCloseEvent) -> None:
        # v1.0.33：关窗即窗口销毁，会丢掉后台 worker 的最后一个引用——线程
        # 若还在跑，~QThread 撞 isRunning() → qFatal(SIGABRT 134)。关窗前
        # 先等各线程退出（有界等待：detect 实测约 2.5s，上限 10s 覆盖其
        # 最坏子进程超时叠加），再放行关闭。
        reap_worker(getattr(self, "_detect_worker", None), 10_000)
        for name in ("doctor_page", "settings_page"):
            shutdown = getattr(getattr(self, name, None), "shutdown_workers", None)
            if callable(shutdown):
                shutdown()
        super().closeEvent(event)

    def _dispatch(self) -> None:
        for name in _INSTALL_PAGES:
            page = getattr(self, name)
            if page is self.scheme_page:
                page.bind(self.report, self.sm, self._installed_keys)
            elif page is self.doctor_page:
                page.bind(self.report, self.sm, self._installed_keys)
            else:
                page.bind(self.report, self.sm)
        self._refresh_status()

    def _refresh_status(self) -> None:
        """状态信息刷新（当前由欢迎页摘要 + 各页状态行承担，此处为扩展钩子）。

        v1.0.5：删除窗口级 setToolTip——鼠标悬停任意空白处即弹出
        「状态：… ｜ 推荐前端：…」长条提示（真机反馈属干扰）。
        """
        return
