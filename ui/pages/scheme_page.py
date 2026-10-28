"""方案市场页（Phase 3）：方案卡片（介绍/优势/来源）+ aria2 加速安装 + 卸载 + 更新检查。

v1.0.39：重构「已安装方案切换」的交互（真机反馈「方式感觉不太好」）。
v1.0.38 的一版把 21 个 schema 平铺成胶囊墙，问题有三：
1. 状态卡里的「当前方案」行与胶囊墙的高亮项表达同一件事（两套视觉语言）；
2. 胶囊墙的粒度是 **文件**（``*.schema.yaml``）不是 **方案包**——一个包
   带出多个变体（朙月拼音 → 简化字/慕潮正體/語句流），用户看到的是一堆
   碎片，不是「我装了哪些方案」；
3. 胶囊墙夹在状态卡与筛选工具栏之间，破坏页面动线，且把市场卡片挤到
   折叠线以下。

改为：**状态卡内一行「当前方案」切换器**（唯一事实源，当前项带 ✓）+
**下拉菜单**（按方案包分组、组标题=包名、顶部可搜索、当前项 ✓ 勾选）。
卡片上的「启用」按钮接管主路径：点它即把该包主方案设为当前。
详见 core/schema_switch.group_by_package。

本页只负责展示与意图发射，执行由主窗口编排：
- install_requested(key)：安装/重装走同一条 aria2 + plum 流水线；
- uninstall_requested(key)：依赖 manifest 快照做干净卸载；
- check_update_requested(key)：仅下载对比指纹，不改动用户目录；
- switch_requested(schema_id)：切换当前输入方案（core/schema_switch 构建步骤）；
- custom_install_requested(spec)：支持任意 user/repo[@branch][:recipe]。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    FlowLayout,
    LineEdit,
    MessageBox,
    PillPushButton,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
)

from core.detector import EnvReport
from core import i18n
from core.schemes import SCHEME_MAP, SCHEMES, parse_spec, Scheme
from core.schema_switch import group_by_package
from core.state_machine import StateMachine
from ui.pages.base import STATUS_COLORS, PageBase, ProgressAreaMixin


#: 分类 → 语言键（值在运行时按当前语言取，切换语言可立即刷新）
CATEGORY_KEYS = {
    "official": "category.official",
    "third-party": "category.third-party",
    "addon": "category.addon",
    "skin": "category.skin",
    "custom": "category.custom",
}
#: 筛选下拉项 → 语言键（顺序即下拉顺序）
FILTER_KEYS = (
    "filter.all",
    "filter.official",
    "filter.third-party",
    "filter.addon",
    "filter.skin",
    "filter.installed",
)


def category_label(category: str) -> str:
    key = CATEGORY_KEYS.get(category)
    return i18n.tr(key) if key else category


def filter_items() -> list[str]:
    return [i18n.tr(key) for key in FILTER_KEYS]


class SchemaSwitcher(QWidget):
    """「当前方案」下拉选择器（v1.0.40）。

    **形态**：一个标准下拉框（``ComboBox``）——控件本身就显示当前正在使用
    的方案名，带边框与下拉箭头，一眼能看出「这里能点」。

    **为什么换掉 v1.0.39 的菜单**：那一版是「文字标签 + 32px 透明箭头
    按钮 + 自定义 widget 菜单行」，真机反馈两处硬伤：①箭头太隐蔽，不点
    不知道能点；②菜单项点击不触发（用户机上没有任何切换任务日志、
    ``user.yaml`` 未被写）。自定义 widget 行依赖 qfluentwidgets
    ``RoundMenu.addWidget`` 的 ``onClick`` 间接链路，在真实输入事件下
    不可靠。改回 Qt 原生下拉后，选中项走 ``activated`` 信号（只在用户
    操作时发），点击路径与页面「筛选」下拉完全一致。

    **分组**：下拉列表里插入**禁用项**当方案包标题（``── 雾凇拼音 ──``），
    点击无反应，把同一包的变体在视觉上收拢。

    **唯一事实源**：控件显示的文本 = 实际生效方案（反读 user.yaml），
    不再另设一行「当前方案」文字标签。
    """

    switch_requested = pyqtSignal(str)  # schema_id

    #: 下拉框宽度（px）——够放下「注音·慕潮正體双拼」这类长名
    WIDTH = 320

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list = []
        self._active_id = ""
        #: 下拉项索引 → schema_id（分组标题项为空串）
        self._index_ids: list[str] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.combo = ComboBox(self)
        self.combo.setFixedWidth(self.WIDTH)
        self.combo.setPlaceholderText(i18n.tr("scheme.switchable_none"))
        #: 只在用户操作时发出（程序设值不发），避免重建时误触发切换
        self.combo.activated.connect(self._on_activated)
        layout.addWidget(self.combo)

    # ---- 对外接口 ----

    def set_state(self, groups, active_id: str) -> None:
        """按分组重建下拉项，并把显示值同步到实际生效方案。

        groups：``core.schema_switch.group_by_package`` 的结果；
        active_id：当前生效的 schema_id（反读 user.yaml）。
        """
        self._groups = list(groups or [])
        self._active_id = active_id or ""
        self._rebuild()

    def set_busy(self, busy: bool) -> None:
        """执行期禁用下拉（切换任务在跑时不能再发一次切换）。"""
        self.combo.setEnabled(not busy and bool(self._groups))

    def retranslate(self) -> None:
        """切语言：分组标题与占位文案都来自 i18n，重建即可。"""
        self._rebuild()

    def current_text(self) -> str:
        """当前显示的方案名（无可用方案时为空串）。"""
        return self.combo.currentText() if self.combo.currentIndex() >= 0 else ""

    # ---- 内部 ----

    def _rebuild(self) -> None:
        combo = self.combo
        combo.blockSignals(True)
        combo.clear()
        self._index_ids = []
        target = -1
        placeholder = i18n.tr("scheme.switchable_none")
        combo.setPlaceholderText(placeholder)

        for group in self._groups:
            # 分组标题：禁用项（点击不触发），视觉上作分隔
            combo.addItem(f"── {group.title} ──")
            combo.items[-1].isEnabled = False
            self._index_ids.append("")
            for item in group.items:
                label = item.name
                # 当前生效项加 ✓：控件文本与下拉列表都能一眼看出「正在用哪个」
                if item.id == self._active_id:
                    label = f"✓ {label}"
                if not item.in_list:
                    label = f"{label}（{i18n.tr('scheme.not_in_list_short')}）"
                combo.addItem(label, userData=item.id)
                self._index_ids.append(item.id)
                if item.id == self._active_id:
                    target = combo.count() - 1

        if target >= 0:
            combo.setCurrentIndex(target)
        else:
            # 当前方案不在可切换列表里（例如手动装完没部署）——显示占位文案
            combo.setCurrentIndex(-1)
            combo.setText(placeholder)

        combo.blockSignals(False)
        combo.setEnabled(bool(self._groups))
        combo.setToolTip(
            i18n.tr("scheme.switcher_tip")
            if self._groups
            else placeholder
        )

    def _current_display(self) -> str:
        for group in self._groups:
            for item in group.items:
                if item.id == self._active_id:
                    return f"{item.name}（{item.id}）"
        return self._active_id

    def _on_activated(self, index: int) -> None:
        """用户在下拉里选中一项（``activated`` 只在用户操作时发出）。"""
        if not (0 <= index < len(self._index_ids)):
            return
        schema_id = self._index_ids[index]
        if not schema_id:
            # 兜底：误点分组标题（禁用项本不可选）——把显示拉回真实值
            self._rebuild()
            return
        if schema_id != self._active_id:
            self.switch_requested.emit(schema_id)


class SchemeCard(SimpleCardWidget):
    """单张方案卡：名称 + 分类徽标 + 简介 + 优势列表 + 来源体积 + 操作按钮。

    v1.0.38：非皮肤卡新增「启用」按钮（切换当前输入方案）与「当前方案」徽标。
    卡片自身不知道 schema_id——由页面经 ``set_schema_ids`` 注入（manifest 的
    files 记录推导），皮肤卡不走这条路（皮肤的「切换」就是 install_button）。
    """

    install_requested = pyqtSignal(str)
    check_update_requested = pyqtSignal(str)
    uninstall_requested = pyqtSignal(str)
    set_default_requested = pyqtSignal(str)
    switch_requested = pyqtSignal(str)  # schema_id

    def retranslate(self) -> None:
        """切语言：刷新分类徽标与操作按钮（皮肤用「切换/移除」语义）。"""
        self.category_badge.setText(category_label(self.scheme.category))
        self.check_update_button.setText(i18n.tr("scheme.check_update"))
        self.uninstall_button.setText(
            i18n.tr("scheme.remove" if self.scheme.is_skin else "common.uninstall")
        )
        self.switch_button.setText(i18n.tr("scheme.enable"))
        if self.scheme.is_skin:
            self.set_default_button.setText(i18n.tr("scheme.set_default"))
            self.set_default_button.setToolTip(i18n.tr("scheme.set_default_tip"))
            self._refresh_skin_labels()
        else:
            self.install_button.setText(i18n.tr("common.install"))
            self._refresh_scheme_labels()

    def __init__(self, scheme: Scheme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scheme = scheme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel(scheme.name))
        self.category_badge = PillPushButton(category_label(scheme.category))
        self.category_badge.setCheckable(False)
        self.category_badge.setEnabled(False)
        self.category_badge.setFixedHeight(24)
        head.addWidget(self.category_badge)
        head.addStretch(1)
        self.installed_label = BodyLabel("")
        head.addWidget(self.installed_label)
        layout.addLayout(head)

        intro = BodyLabel(scheme.intro)
        intro.setWordWrap(True)
        intro.setMinimumWidth(340)
        layout.addWidget(intro)

        for advantage in scheme.advantages:
            row = QHBoxLayout()
            row.setSpacing(6)
            check = BodyLabel("✓")
            check.setStyleSheet(f"color: {STATUS_COLORS['ok']};")
            row.addWidget(check)
            text = CaptionLabel(advantage)
            text.setWordWrap(True)
            row.addWidget(text, 1)
            layout.addLayout(row)

        meta = QHBoxLayout()
        meta.setSpacing(14)
        meta.addWidget(CaptionLabel(f"来源：{scheme.source}"))
        meta.addWidget(CaptionLabel(f"体积：{scheme.size_hint or '未知'}"))
        meta.addStretch(1)
        layout.addLayout(meta)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.check_update_button = PushButton(
            FluentIcon.SYNC, i18n.tr("scheme.check_update")
        )
        self.check_update_button.clicked.connect(lambda: self.check_update_requested.emit(scheme.key))
        # 皮肤无上游仓库可对比：隐藏检查更新（保留对象供 busy 协议统一置位）
        if scheme.is_skin:
            self.check_update_button.setVisible(False)
        self.uninstall_button = PushButton(
            FluentIcon.DELETE,
            i18n.tr("scheme.remove" if scheme.is_skin else "common.uninstall"),
        )
        self.uninstall_button.clicked.connect(lambda: self.uninstall_requested.emit(scheme.key))
        self.install_button = PrimaryPushButton(
            FluentIcon.DOWNLOAD,
            i18n.tr("scheme.switch_to" if scheme.is_skin else "common.install"),
        )
        self.install_button.clicked.connect(lambda: self.install_requested.emit(scheme.key))
        # v1.0.38：方案切换按钮（皮肤卡的切换语义由 install_button 承担）
        self.switch_button = PushButton(
            FluentIcon.PLAY, i18n.tr("scheme.enable")
        )
        self.switch_button.setToolTip(i18n.tr("scheme.enable_tip"))
        self.switch_button.clicked.connect(self._on_switch_clicked)
        self.switch_button.setVisible(not scheme.is_skin)
        # v1.0.12：皮肤卡「设为默认」（安装器侧偏好，不写 rime 配置）
        self.set_default_button = PushButton(
            FluentIcon.PIN, i18n.tr("scheme.set_default")
        )
        self.set_default_button.setToolTip(i18n.tr("scheme.set_default_tip"))
        self.set_default_button.clicked.connect(lambda: self.set_default_requested.emit(scheme.key))
        self.set_default_button.setVisible(scheme.is_skin)
        for button in (
            self.set_default_button,
            self.switch_button,
            self.check_update_button,
            self.uninstall_button,
            self.install_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        # 皮肤生效范围提示（v1.0.7）：Linux 候选窗由桌面会话绘制，配色未必生效
        if scheme.is_skin:
            self.install_button.setToolTip(i18n.tr("scheme.skin_scope"))
            self.category_badge.setToolTip(i18n.tr("scheme.skin_scope"))

        # v1.0.7：统一卡片高度——瀑布流中高度不一的卡片上下错位（真机反馈
        # 「排列乱」）；330 = 当前最高卡（雾凇 5 条优势 + 3 行简介）+ 余量
        self.setFixedHeight(330)

        self._installed = False
        self._active = False
        self._default = False
        # v1.0.38：该卡方案包提供的 schema_id（manifest files 推导）与当前态
        self._schema_ids: tuple[str, ...] = ()
        # v1.0.39：schema_id → 显示名（供「启用」提示显示人可读名）
        self._schema_names: dict[str, str] = {}
        self._schema_active = False
        self.set_installed(False)

    @property
    def installed(self) -> bool:
        return self._installed

    def set_installed(self, installed: bool) -> None:
        self._installed = installed
        if self.scheme.is_skin:
            self._refresh_skin_labels()
        else:
            self._refresh_scheme_labels()
        self.installed_label.setStyleSheet(f"color: {STATUS_COLORS['ok']};")

    # ---- 方案切换态（v1.0.38） ----

    def set_schema_ids(self, ids) -> None:
        """注入该卡方案包提供的 schema_id（manifest files 记录推导）。

        空列表 = 该包没有可识别的方案文件（或未安装）——「启用」按钮保持
        隐藏，避免点出一个没有目标的开关。
        """
        self._schema_ids = tuple(ids or ())
        if not self.scheme.is_skin:
            self.switch_button.setVisible(bool(self._schema_ids))
            self._refresh_scheme_labels()

    def set_schema_names(self, names: dict) -> None:
        """注入 schema_id → 显示名映射（供「启用」提示显示人可读名）。"""
        self._schema_names = dict(names or {})
        if not self.scheme.is_skin:
            self._refresh_scheme_labels()

    def _primary_schema_name(self) -> str:
        """主方案的可读名（未知时回退 schema_id）。"""
        if not self._schema_ids:
            return ""
        return self._schema_names.get(self._schema_ids[0], self._schema_ids[0])

    def set_schema_active(self, active: bool) -> None:
        """当前生效方案是否属于这个包（驱动「当前方案」徽标）。"""
        self._schema_active = active
        if not self.scheme.is_skin:
            self._refresh_scheme_labels()

    def _refresh_scheme_labels(self) -> None:
        """非皮肤卡徽标与主按钮：当前方案 > 已安装；「启用」随安装态显隐。"""
        if self.scheme.is_skin:
            return
        self.install_button.setText(
            i18n.tr("common.reinstall" if self._installed else "common.install")
        )
        if self._schema_active:
            self.installed_label.setText(i18n.tr("scheme.current_schema"))
            self.switch_button.setVisible(False)
        else:
            self.installed_label.setText("✓ 已安装" if self._installed else "")
            self.switch_button.setVisible(self._installed and bool(self._schema_ids))
        self.installed_label.setStyleSheet(f"color: {STATUS_COLORS['ok']};")
        if self._installed and self._schema_ids:
            if len(self._schema_ids) > 1:
                # v1.0.39：胶囊墙已改为下拉菜单，提示语随之改写
                self.switch_button.setToolTip(
                    i18n.tr(
                        "scheme.enable_multi_tip",
                        n=len(self._schema_ids),
                        name=self._primary_schema_name(),
                    )
                )
            else:
                self.switch_button.setToolTip(i18n.tr("scheme.enable_tip"))

    def _on_switch_clicked(self) -> None:
        if self._schema_ids:
            self.switch_requested.emit(self._schema_ids[0])

    def set_skin_state(self, active: bool, default: bool) -> None:
        """v1.0.12：刷新皮肤卡的「当前皮肤 / 我的默认」态。

        active 由 detect_active_skin 从 default.custom.yaml 反读（UI 以实际
        配置为准，不信 manifest）；installed 仍由 manifest 记账驱动。
        """
        self._active = active
        self._default = default
        self._refresh_skin_labels()

    def _refresh_skin_labels(self) -> None:
        """皮肤卡徽标与主按钮：当前皮肤 > 我的默认 > 已应用。"""
        if not self.scheme.is_skin:
            return
        if self._active:
            self.installed_label.setText(i18n.tr("scheme.current_skin"))
            self.install_button.setText(i18n.tr("scheme.redeploy"))
        elif self._installed:
            self.installed_label.setText(i18n.tr("scheme.applied"))
            self.install_button.setText(i18n.tr("scheme.switch_to"))
        else:
            self.installed_label.setText("")
            self.install_button.setText(i18n.tr("scheme.switch_to"))
        if self._default:
            base = self.installed_label.text()
            mark = i18n.tr("scheme.my_default")
            self.installed_label.setText(f"{base} · {mark}" if base else mark)
        self.installed_label.setStyleSheet(f"color: {STATUS_COLORS['ok']};")

    def apply_rules(self, sm: StateMachine) -> None:
        """按状态机规则刷新按钮可用性与禁用提示（F7）。"""
        install_rule = sm.rule("install_scheme")
        uninstall_rule = sm.rule("uninstall_scheme")
        switch_rule = sm.rule("switch_scheme")

        self.install_button.setEnabled(install_rule.enabled)
        if self.scheme.is_skin:
            self.install_button.setToolTip(
                install_rule.reason
                or i18n.tr(
                    "scheme.redeploy_tip" if self._active else "scheme.switch_to_tip"
                )
            )
        else:
            self.install_button.setToolTip(
                install_rule.reason
                or (
                    i18n.tr("scheme.reinstall_tip")
                    if self._installed
                    else i18n.tr("scheme.install_tip")
                )
            )

        uninstall_reason = uninstall_rule.reason
        if uninstall_rule.enabled and not self._installed:
            uninstall_reason = i18n.tr("scheme.not_installed")
        self.uninstall_button.setEnabled(uninstall_rule.enabled and self._installed)
        if self.scheme.is_skin:
            self.uninstall_button.setToolTip(
                uninstall_reason or i18n.tr("scheme.remove_tip")
            )
        else:
            self.uninstall_button.setToolTip(uninstall_reason or i18n.tr("scheme.uninstall_tip"))

        check_reason = install_rule.reason
        if install_rule.enabled and not self._installed:
            check_reason = i18n.tr("scheme.not_installed_no_update")
        self.check_update_button.setEnabled(install_rule.enabled and self._installed)
        self.check_update_button.setToolTip(check_reason or i18n.tr("scheme.check_update_tip"))

        # v1.0.38：方案切换按钮——未安装/无 schema_id/规则禁用时不可点
        switch_reason = switch_rule.reason
        if switch_rule.enabled and not self._installed:
            switch_reason = i18n.tr("scheme.not_installed")
        elif switch_rule.enabled and not self._schema_ids:
            switch_reason = i18n.tr("scheme.no_schema_files")
        self.switch_button.setEnabled(
            switch_rule.enabled and self._installed and bool(self._schema_ids)
        )
        if not self.switch_button.toolTip() or switch_reason:
            self.switch_button.setToolTip(switch_reason or i18n.tr("scheme.enable_tip"))

    def set_busy(self, busy: bool, running: bool = False) -> None:
        """busy 时全部禁用；running=True 的卡片显示「执行中」徽标。"""
        self.install_button.setEnabled(not busy)
        self.uninstall_button.setEnabled(not busy and self._installed)
        self.check_update_button.setEnabled(not busy and self._installed)
        self.set_default_button.setEnabled(not busy)
        self.switch_button.setEnabled(
            not busy and self._installed and bool(self._schema_ids)
        )
        if running:
            self.installed_label.setText("⟳ 执行中")
            self.installed_label.setStyleSheet(f"color: {STATUS_COLORS['warn']};")
        else:
            self.set_installed(self._installed)


class SchemePage(ProgressAreaMixin, PageBase):
    install_requested = pyqtSignal(str)  # scheme key
    uninstall_requested = pyqtSignal(str)
    check_update_requested = pyqtSignal(str)
    switch_requested = pyqtSignal(str)  # schema_id：切换当前输入方案（v1.0.38）
    custom_install_requested = pyqtSignal(str)  # 自定义规格文本
    set_default_requested = pyqtSignal(str)  # 皮肤 key：设为我的默认
    restore_default_requested = pyqtSignal()  # 恢复我的默认皮肤
    cancel_requested = pyqtSignal()
    log_requested = pyqtSignal()

    _DOTS_FRAMES = ("", ".", "..", "...")

    def __init__(
        self,
        report: EnvReport,
        sm: StateMachine,
        installed_keys: set[str] | None = None,
        schemes: tuple[Scheme, ...] = SCHEMES,
        parent=None,
    ) -> None:
        super().__init__(
            "scheme.title",
            "scheme.subtitle",
            parent,
        )
        self._report = report
        self._sm = sm
        self._installed_keys: set[str] = set(installed_keys or ())
        self._running_key = ""
        # v1.0.12：皮肤当前态（active 由配置文件反读）与我的默认（manifest 偏好）
        self._active_skin_key = ""
        self._default_skin_key = ""
        # v1.0.38：方案切换态（由主窗口经 set_switchable_state/set_schema_ids 注入）
        self._switchable: list = []
        self._active_schema_id = ""
        self._schema_ids: dict[str, tuple[str, ...]] = {}
        # v1.0.39：包归属（key → schema_id 列表 / key → 显示名），
        # 下拉菜单按包分组时需要
        self._package_schemas: dict[str, list[str]] = {}
        self._package_names: dict[str, str] = {}

        self.status_row = self.add_row("安装状态", "")
        self.frontend_row = self.add_row("目标前端", "")
        self.rime_dir_row = self.add_row("配置目录", "")

        # v1.0.40：「当前方案」是**可操作控件**，不是只读状态——用分隔线把
        # 它与上面三行纯事实隔开，标签紧贴下拉（不隔空右对齐），右侧补一句
        # 操作提示。v1.0.39 曾把它做成「文字 + 32px 透明箭头」，真机反馈
        # 「隐藏太深，不点就不知道能点」。
        self._switcher_divider = QWidget(self.card)
        self._switcher_divider.setFixedHeight(1)
        # 中性灰：浅色/深色主题下都可见，无需跟随主题重刷
        self._switcher_divider.setStyleSheet(
            "background-color: rgba(128, 128, 128, 0.28);"
        )
        self.card_layout.addWidget(self._switcher_divider)

        self.current_schema_row = QHBoxLayout()
        self.current_schema_row.setSpacing(10)
        self._switcher_title = BodyLabel(i18n.tr("row.current_schema"))
        self.current_schema_row.addWidget(self._switcher_title)
        self.schema_switcher = SchemaSwitcher(self)
        self.schema_switcher.switch_requested.connect(self.switch_requested.emit)
        self.current_schema_row.addWidget(self.schema_switcher)
        self._switcher_hint = CaptionLabel(i18n.tr("scheme.switcher_hint"))
        self._switcher_hint.setStyleSheet("color: rgba(128, 128, 128, 0.9);")
        self.current_schema_row.addWidget(self._switcher_hint)
        self.current_schema_row.addStretch(1)
        self.card_layout.addLayout(self.current_schema_row)

        # 筛选 + 搜索
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        self.filter_box = ComboBox()
        self.filter_box.addItems(filter_items())
        self.filter_box.setFixedWidth(140)
        self.filter_box.currentIndexChanged.connect(self._apply_filter)
        self.search_input = LineEdit()
        self.search_input.setPlaceholderText("搜索方案名称、简介或来源…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_box)
        filter_row.addWidget(self.search_input, 1)
        self.card_layout.addLayout(filter_row)

        # 自定义安装
        custom_row = QHBoxLayout()
        custom_row.setSpacing(10)
        self.custom_input = LineEdit()
        self.custom_input.setPlaceholderText(
            "自定义：粘贴 GitHub 仓库链接或 user/repo，如 https://github.com/iDvel/rime-ice"
        )
        self.custom_input.textChanged.connect(self._refresh_custom_button)
        self.custom_button = PushButton(
            FluentIcon.ADD, i18n.tr("scheme.custom_install")
        )
        self.custom_button.setEnabled(False)
        self.custom_button.clicked.connect(
            lambda: self.custom_install_requested.emit(self.custom_input.text().strip())
        )
        custom_row.addWidget(self.custom_input, 1)
        custom_row.addWidget(self.custom_button)
        self.card_layout.addLayout(custom_row)

        self.cache_check = CheckBox("安装前清理本地包缓存（确保拉取最新内容）")
        self.cache_check.setChecked(True)
        self.cache_check.setToolTip("plum 发现本地已有包目录会跳过拉取，重装/升级前必须清理")
        self.card_layout.addWidget(self.cache_check)

        # 执行进度区（等待态条纹 + 数值平滑动画见 ProgressAreaMixin）
        self._init_progress_area(self.card_layout)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.cancel_button = PushButton(
            FluentIcon.CANCEL, i18n.tr("common.cancel_task")
        )
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.log_button = PushButton(FluentIcon.HISTORY, i18n.tr("common.view_log"))
        self.log_button.clicked.connect(self.log_requested.emit)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.log_button)
        action_row.addStretch(1)
        self.card_layout.addLayout(action_row)

        # 卡片区（可滚动）：方案与皮肤分区展示（v1.0.9）。
        # isTight=True 修复筛选后布局错位：默认 False 时隐藏卡片仍占位，
        # 瀑布流出现整块空洞、行起点参差（真机反馈「排序不太对」）；
        # 收紧后隐藏卡片完全让位，剩余卡片从左侧依次向右排列、放不下换行。
        self._sections_host = QWidget()
        sections = QVBoxLayout(self._sections_host)
        sections.setContentsMargins(2, 2, 2, 2)
        sections.setSpacing(8)

        self.scheme_section_label = StrongBodyLabel(i18n.tr("scheme.section_schemes"))
        self.cards_host = QWidget()
        self.cards_layout = FlowLayout(self.cards_host, needAni=False, isTight=True)
        self.cards_layout.setContentsMargins(2, 2, 2, 2)
        self.cards_layout.setHorizontalSpacing(14)
        self.cards_layout.setVerticalSpacing(14)

        # v1.0.12：皮肤分区头部——标题 + 当前皮肤 + 「恢复我的默认」，
        # 第二行是「已安装皮肤」快捷切换条（点击 chip 直接切换，无需翻卡片）
        self.skin_section_label = StrongBodyLabel(i18n.tr("scheme.section_skins"))
        self.skin_current_label = CaptionLabel(i18n.tr("scheme.no_current"))
        self.restore_default_button = PushButton(
            FluentIcon.HISTORY, i18n.tr("scheme.restore_default")
        )
        self.restore_default_button.setToolTip(i18n.tr("scheme.restore_default_tip"))
        self.restore_default_button.setEnabled(False)
        self.restore_default_button.clicked.connect(self.restore_default_requested.emit)

        self._skin_chips_host = QWidget()
        chips_row = QHBoxLayout(self._skin_chips_host)
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(8)
        self._skin_chips_label = CaptionLabel(i18n.tr("scheme.installed_skins"))
        chips_row.addWidget(self._skin_chips_label)
        self._skin_chips_layout = QHBoxLayout()
        self._skin_chips_layout.setContentsMargins(0, 0, 0, 0)
        self._skin_chips_layout.setSpacing(8)
        chips_row.addLayout(self._skin_chips_layout)
        chips_row.addStretch(1)
        self._skin_chips: list[PillPushButton] = []

        self._skin_header = QWidget()
        header = QVBoxLayout(self._skin_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(10)
        head_row.addWidget(self.skin_section_label)
        head_row.addWidget(self.skin_current_label)
        head_row.addStretch(1)
        head_row.addWidget(self.restore_default_button)
        header.addLayout(head_row)
        header.addWidget(self._skin_chips_host)
        self._skin_chips_host.setVisible(False)

        self.skins_host = QWidget()
        self.skins_layout = FlowLayout(self.skins_host, needAni=False, isTight=True)
        self.skins_layout.setContentsMargins(2, 2, 2, 2)
        self.skins_layout.setHorizontalSpacing(14)
        self.skins_layout.setVerticalSpacing(14)

        self._cards: list[SchemeCard] = []
        for scheme in schemes:
            self._add_card(scheme)

        sections.addWidget(self.scheme_section_label)
        sections.addWidget(self.cards_host)
        sections.addWidget(self._skin_header)
        sections.addWidget(self.skins_host)
        sections.addStretch(1)

        self.scroll_area = ScrollArea()
        self.scroll_area.setWidget(self._sections_host)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.enableTransparentBackground()
        # v1.0.5：滚动区独享剩余高度（此前与末尾 stretch 1:1 平分，只露 2 张卡）
        self.add_expanding_scroll(self.scroll_area)

        self._status_base = ""
        self._dots_index = 0
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(320)
        self._dots_timer.timeout.connect(self._tick_dots)

        self.bind(report, sm, self._installed_keys)
        self._show_progress_area(False)

    # ---- 卡片构建与筛选 ----

    def _add_card(self, scheme: Scheme) -> SchemeCard:
        card = SchemeCard(scheme, self.cards_host)
        card.setMinimumWidth(430)
        card.install_requested.connect(self.install_requested.emit)
        card.uninstall_requested.connect(self.uninstall_requested.emit)
        card.check_update_requested.connect(self.check_update_requested.emit)
        card.set_default_requested.connect(self.set_default_requested)
        card.switch_requested.connect(self.switch_requested.emit)
        card.set_schema_ids(self._schema_ids.get(scheme.key, ()))
        self._cards.append(card)
        # v1.0.9：皮肤归入独立分区（与输入法方案分开展示）
        (self.skins_layout if scheme.is_skin else self.cards_layout).addWidget(card)
        return card

    def add_scheme(self, scheme: Scheme) -> SchemeCard:
        """动态补一张卡（自定义方案首装后用于展示其状态）。"""
        existing = next((c for c in self._cards if c.scheme.key == scheme.key), None)
        if existing is not None:
            return existing
        card = self._add_card(scheme)
        card.set_installed(scheme.key in self._installed_keys)
        card.apply_rules(self._sm)
        self._apply_filter()
        return card

    def _apply_filter(self) -> None:
        """按分类 + 关键字决定卡片可见性（隐藏而非销毁，保留已装状态）。

        按索引而非文本比较：筛选项文案已外置到 i18n，英文模式下中文字面量
        比较会整体失效（2026-10-14 修）。
        """
        index = self.filter_box.currentIndex()
        keyword = self.search_input.text().strip().lower()
        for card in self._cards:
            scheme = card.scheme
            visible = True
            if index == 1 and scheme.category != "official":
                visible = False
            elif index == 2 and scheme.category != "third-party":
                visible = False
            elif index == 3 and scheme.category != "addon":
                visible = False
            elif index == 4 and scheme.category != "skin":
                visible = False
            elif index == 5 and not card.installed:
                visible = False
            if visible and keyword:
                haystack = " ".join(
                    [scheme.name, scheme.intro, scheme.source, *scheme.advantages]
                ).lower()
                visible = keyword in haystack
            card.setVisible(visible)

        # v1.0.9：分区标题随分区内容显隐（仅皮肤筛选时隐藏方案区，反之亦然）
        scheme_visible = any(
            c.scheme.category != "skin" and not c.isHidden() for c in self._cards
        )
        skin_visible = any(
            c.scheme.category == "skin" and not c.isHidden() for c in self._cards
        )
        self.scheme_section_label.setVisible(scheme_visible)
        self.cards_host.setVisible(scheme_visible)
        self._skin_header.setVisible(skin_visible)
        self.skins_host.setVisible(skin_visible)
        # 隐藏/显示卡片后强制重算瀑布流高度，滚动区内容即时贴合
        self.cards_host.adjustSize()
        self.skins_host.adjustSize()
        self._sections_host.adjustSize()

    # ---- 状态刷新 ----

    def _fill(self, report: EnvReport, sm: StateMachine) -> None:
        self._set_row(
            self.status_row,
            sm.state.value,
            "ok" if report.engine_installed else "bad",
        )
        self._set_row(self.frontend_row, report.recommended_frontend)
        self._set_row(self.rime_dir_row, str(report.rime_user_dir or "—"), "muted")

        for card in self._cards:
            card.apply_rules(sm)
        self._refresh_custom_button()
        self._apply_filter()

    def set_installed_keys(self, keys: set[str]) -> None:
        self._installed_keys = set(keys)
        for card in self._cards:
            card.set_installed(card.scheme.key in self._installed_keys)
        self._rebuild_skin_chips()
        self._apply_filter()

    # ---- 方案切换态（v1.0.38） ----

    def set_switchable_state(self, items, active_id: str) -> None:
        """刷新切换器与卡片当前徽标。

        items：``core.schema_switch.SwitchableSchema`` 列表（扫盘得到）；
        active_id：``detect_active_schema`` 反读的当前方案（用户在现场用
        Ctrl+` 切换后 rime 自己写 user.yaml，反读依然准确）。
        """
        self._switchable = list(items or [])
        self._active_schema_id = active_id or ""
        # v1.0.39：schema 显示名注入卡片（「启用」提示用可读名而非 id）
        names = {s.id: s.name for s in self._switchable}
        for card in self._cards:
            card.set_schema_names(names)
        self._refresh_switch_ui()

    def set_schema_ids(self, mapping: dict) -> None:
        """注入 scheme key → schema_id 列表（主窗口从 manifest files 推导）。"""
        self._schema_ids = {k: tuple(v) for k, v in (mapping or {}).items()}
        for card in self._cards:
            card.set_schema_ids(self._schema_ids.get(card.scheme.key, ()))
            # 可用态由 apply_rules 决定（含「未安装」原因），注入后必须重算——
            # 否则 bind 时（schema_ids 还空）算出的禁用态会一直留着
            card.apply_rules(self._sm)
        # v1.0.39：包归属随映射一同更新（分组菜单需要）
        self._package_schemas = {k: list(v) for k, v in (mapping or {}).items()}
        self._package_names = {
            key: (SCHEME_MAP[key].name if key in SCHEME_MAP else key)
            for key in self._package_schemas
        }
        self._refresh_switch_ui()

    def _refresh_switch_ui(self) -> None:
        """刷新切换器（当前方案显示 + 下拉菜单）与卡片当前徽标。

        v1.0.39：菜单按 **方案包** 分组（组标题=包名，组内=变体），
        恢复「我装了哪些方案」的心智；无包归属的方案归入手动/共享组。
        """
        groups = group_by_package(
            self._switchable,
            package_schemas=self._package_schemas,
            package_names=self._package_names,
            group_titles={
                "manual": i18n.tr("scheme.group.manual"),
                "shared": i18n.tr("scheme.group.shared"),
                "other": i18n.tr("scheme.group.other"),
            },
        )
        self.schema_switcher.set_state(groups, self._active_schema_id)

        active_ids = {self._active_schema_id} - {""}
        for card in self._cards:
            if not card.scheme.is_skin:
                card.set_schema_active(
                    bool(active_ids & set(self._schema_ids.get(card.scheme.key, ())))
                )

    @staticmethod
    def _set_row_title(row, text: str) -> None:
        """刷新行标题（切语言用；结构见 PageBase.add_row：标题是首个控件）。"""
        item = row.itemAt(0)
        label = item.widget()
        if label is not None:
            label.setText(text)

    # ---- 皮肤当前态 / 我的默认 / 已安装快捷切换（v1.0.12） ----

    def set_skin_state(self, active_key: str, default_key: str) -> None:
        """刷新皮肤卡当前态、分区头部与快捷切换条。

        active_key：detect_active_skin 从 default.custom.yaml 反读的方案 key；
        default_key：manifest 里「我的默认皮肤」偏好。
        """
        self._active_skin_key = active_key or ""
        self._default_skin_key = default_key or ""
        for card in self._cards:
            if card.scheme.is_skin:
                card.set_skin_state(
                    active=card.scheme.key == self._active_skin_key,
                    default=card.scheme.key == self._default_skin_key,
                )
        self._refresh_skin_header()

    def _refresh_skin_header(self) -> None:
        scheme = SCHEME_MAP.get(self._active_skin_key)
        if scheme is not None:
            name = scheme.name
            if name.startswith("皮肤 · "):
                name = name[len("皮肤 · "):]
            self.skin_current_label.setText(i18n.tr("scheme.current_label", name=name))
        else:
            self.skin_current_label.setText(i18n.tr("scheme.no_current"))
        self.restore_default_button.setEnabled(bool(self._default_skin_key))
        for chip in self._skin_chips:
            chip.setChecked(chip.property("scheme_key") == self._active_skin_key)

    def _rebuild_skin_chips(self) -> None:
        """重建「已安装皮肤」快捷切换条：manifest 里登记过的皮肤各一枚 chip。"""
        for chip in self._skin_chips:
            self._skin_chips_layout.removeWidget(chip)
            chip.deleteLater()
        self._skin_chips.clear()
        installed_skins = [
            c.scheme
            for c in self._cards
            if c.scheme.is_skin and c.scheme.key in self._installed_keys
        ]
        for scheme in installed_skins:
            name = scheme.name
            if name.startswith("皮肤 · "):
                name = name[len("皮肤 · "):]
            chip = PillPushButton(name)
            chip.setCheckable(True)
            chip.setChecked(scheme.key == self._active_skin_key)
            chip.setProperty("scheme_key", scheme.key)
            chip.setToolTip(i18n.tr("scheme.switch_to_tip"))
            chip.setFixedHeight(24)
            chip.clicked.connect(
                lambda checked=False, key=scheme.key: self.install_requested.emit(key)
            )
            self._skin_chips_layout.addWidget(chip)
            self._skin_chips.append(chip)
        self._skin_chips_host.setVisible(bool(installed_skins))

    def _refresh_custom_button(self) -> None:
        """自定义输入框：格式与状态机规则都通过才允许点击。"""
        text = self.custom_input.text().strip()
        rule = self._sm.rule("install_scheme")
        valid = bool(text) and parse_spec(text) is not None and rule.enabled
        self.custom_button.setEnabled(valid)
        if not rule.enabled:
            self.custom_button.setToolTip(rule.reason or "")
        elif not valid:
            self.custom_button.setToolTip(
                "支持 user/repo、user/repo@branch:recipe 或直接粘贴 GitHub 仓库链接"
            )
        else:
            self.custom_button.setToolTip("安装该 GitHub 仓库的 rime 方案包")

    def _set_row(self, row, value: str, status: str | None = None) -> None:
        item = row.itemAt(row.count() - 1)
        label = item.widget()
        if isinstance(label, BodyLabel):
            label.setText(value)
            if status in STATUS_COLORS:
                label.setStyleSheet(f"color: {STATUS_COLORS[status]};")

    # ---- 执行期 UI（主窗口 busy 协议） ----

    def set_busy(self, busy: bool, title: str = "", running_key: str = "") -> None:
        """进入/退出执行态。

        页面级不变量：busy 时全部方案卡按钮、自定义安装、缓存选项、已安装
        方案快捷条均禁用，仅取消可用；退出时按状态机规则恢复。running_key
        卡片显示「执行中」。
        """
        self._running_key = running_key if busy else ""
        for card in self._cards:
            card.set_busy(busy, running=busy and card.scheme.key == self._running_key)
        self.cache_check.setEnabled(not busy)
        self.custom_input.setEnabled(not busy)
        self.log_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        # v1.0.39：切换器下拉在执行期禁用（避免并发发两次切换）
        self.schema_switcher.set_busy(busy)
        # v1.0.12：busy 时禁用「恢复我的默认」（退出 busy 后按偏好恢复可用性）
        self.restore_default_button.setEnabled(
            not busy and bool(self._default_skin_key)
        )
        self._show_progress_area(busy)
        if busy:
            self.enter_waiting()
            self.set_step(1, 1, title or "准备中")
            self._dots_timer.start()
        else:
            self._dots_timer.stop()
            self.reset_progress()
            self._fill(self._report, self._sm)
            # 退出执行态后按状态机恢复快捷条可用性（引擎未装时置灰）
            self._refresh_switch_ui()

    def set_step(self, index: int, total: int, title: str) -> None:
        self._status_base = f"（{index}/{total}）{title}" if total > 1 else title
        self._dots_index = 0
        self.status_label.setText(self._status_base)

    def set_status(self, text: str) -> None:
        self._status_base = text
        self._dots_index = 0
        self.status_label.setText(text)

    def _tick_dots(self) -> None:
        self._dots_index = (self._dots_index + 1) % len(self._DOTS_FRAMES)
        self.status_label.setText(self._status_base + self._DOTS_FRAMES[self._dots_index])

    def retranslate(self) -> None:
        """切语言：标题 + 按钮 + 所有卡片 + 筛选下拉（保留当前选中项）。"""
        super().retranslate()
        self.custom_button.setText(i18n.tr("scheme.custom_install"))
        self.cancel_button.setText(i18n.tr("common.cancel_task"))
        self.log_button.setText(i18n.tr("common.view_log"))
        self.scheme_section_label.setText(i18n.tr("scheme.section_schemes"))
        self.skin_section_label.setText(i18n.tr("scheme.section_skins"))
        self.restore_default_button.setText(i18n.tr("scheme.restore_default"))
        self.restore_default_button.setToolTip(i18n.tr("scheme.restore_default_tip"))
        self._skin_chips_label.setText(i18n.tr("scheme.installed_skins"))
        # v1.0.39：切换器相关文案（行标题此前硬编码中文，英文界面会漏中文）
        self._switcher_title.setText(i18n.tr("row.current_schema"))
        self._switcher_hint.setText(i18n.tr("scheme.switcher_hint"))
        self.schema_switcher.retranslate()
        for row, key in (
            (self.status_row, "row.install_status"),
            (self.frontend_row, "row.frontend"),
            (self.rime_dir_row, "row.rime_dir"),
        ):
            self._set_row_title(row, i18n.tr(key))
        self._refresh_skin_header()
        self._refresh_switch_ui()
        for card in getattr(self, "_cards", []):
            card.retranslate()
        current = self.filter_box.currentIndex()
        self.filter_box.blockSignals(True)
        self.filter_box.clear()
        self.filter_box.addItems(filter_items())
        self.filter_box.setCurrentIndex(min(current, self.filter_box.count() - 1))
        self.filter_box.blockSignals(False)

    # ---- 绑定 ----

    def bind(
        self,
        report: EnvReport,
        sm: StateMachine,
        installed_keys: set[str] | None = None,
    ) -> None:
        if installed_keys is not None:
            self._installed_keys = set(installed_keys)
        self._report, self._sm = report, sm
        for card in self._cards:
            card.set_installed(card.scheme.key in self._installed_keys)
        self._fill(report, sm)

    # ---- 卸载二次确认（主窗口在起任务前调用） ----

    @staticmethod
    def confirm_uninstall(scheme: Scheme, parent: QWidget | None = None) -> bool:
        if scheme.is_skin:
            box = MessageBox(
                i18n.tr("scheme.uninstall_skin_title", name=scheme.name),
                i18n.tr("scheme.uninstall_skin_body"),
                parent,
            )
            return bool(box.exec())
        box = MessageBox(
            f"确认卸载「{scheme.name}」？",
            "将删除该方案引入的文件，并还原它覆盖过的旧配置。\n"
            "卸载基于安装前快照，不影响其他方案与你的个人用户词典。",
            parent,
        )
        return bool(box.exec())
