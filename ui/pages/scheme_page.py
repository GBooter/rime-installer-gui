"""方案市场页（Phase 3）：方案卡片（介绍/优势/来源）+ aria2 加速安装 + 卸载 + 更新检查。

本页只负责展示与意图发射，执行由主窗口编排：
- install_requested(key)：安装/重装走同一条 aria2 + plum 流水线；
- uninstall_requested(key)：依赖 manifest 快照做干净卸载；
- check_update_requested(key)：仅下载对比指纹，不改动用户目录；
- custom_install_requested(spec)：支持任意 user/repo[@branch][:recipe]。
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
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


class SchemeCard(SimpleCardWidget):
    """单张方案卡：名称 + 分类徽标 + 简介 + 优势列表 + 来源体积 + 操作按钮。"""

    install_requested = pyqtSignal(str)
    check_update_requested = pyqtSignal(str)
    uninstall_requested = pyqtSignal(str)
    set_default_requested = pyqtSignal(str)

    def retranslate(self) -> None:
        """切语言：刷新分类徽标与操作按钮（皮肤用「切换/移除」语义）。"""
        self.category_badge.setText(category_label(self.scheme.category))
        self.check_update_button.setText(i18n.tr("scheme.check_update"))
        self.uninstall_button.setText(
            i18n.tr("scheme.remove" if self.scheme.is_skin else "common.uninstall")
        )
        if self.scheme.is_skin:
            self.set_default_button.setText(i18n.tr("scheme.set_default"))
            self.set_default_button.setToolTip(i18n.tr("scheme.set_default_tip"))
            self._refresh_skin_labels()
        else:
            self.install_button.setText(i18n.tr("common.install"))

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
        # v1.0.12：皮肤卡「设为默认」（安装器侧偏好，不写 rime 配置）
        self.set_default_button = PushButton(
            FluentIcon.PIN, i18n.tr("scheme.set_default")
        )
        self.set_default_button.setToolTip(i18n.tr("scheme.set_default_tip"))
        self.set_default_button.clicked.connect(lambda: self.set_default_requested.emit(scheme.key))
        self.set_default_button.setVisible(scheme.is_skin)
        for button in (
            self.set_default_button,
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
        self.set_installed(False)

    @property
    def installed(self) -> bool:
        return self._installed

    def set_installed(self, installed: bool) -> None:
        self._installed = installed
        if self.scheme.is_skin:
            self._refresh_skin_labels()
        else:
            self.installed_label.setText("✓ 已安装" if installed else "")
            self.install_button.setText("重新安装" if installed else "安装")
        self.installed_label.setStyleSheet(f"color: {STATUS_COLORS['ok']};")

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
                    "重新安装该方案（aria2 拉取最新内容）"
                    if self._installed
                    else "安装该方案到当前 rime 配置目录"
                )
            )

        uninstall_reason = uninstall_rule.reason
        if uninstall_rule.enabled and not self._installed:
            uninstall_reason = "该方案尚未安装"
        self.uninstall_button.setEnabled(uninstall_rule.enabled and self._installed)
        if self.scheme.is_skin:
            self.uninstall_button.setToolTip(
                uninstall_reason or i18n.tr("scheme.remove_tip")
            )
        else:
            self.uninstall_button.setToolTip(uninstall_reason or "卸载该方案并还原被覆盖的文件")

        check_reason = install_rule.reason
        if install_rule.enabled and not self._installed:
            check_reason = "该方案尚未安装，无需检查更新"
        self.check_update_button.setEnabled(install_rule.enabled and self._installed)
        self.check_update_button.setToolTip(check_reason or "下载上游内容并与本地指纹对比")

    def set_busy(self, busy: bool, running: bool = False) -> None:
        """busy 时全部禁用；running=True 的卡片显示「执行中」徽标。"""
        self.install_button.setEnabled(not busy)
        self.uninstall_button.setEnabled(not busy and self._installed)
        self.check_update_button.setEnabled(not busy and self._installed)
        self.set_default_button.setEnabled(not busy)
        if running:
            self.installed_label.setText("⟳ 执行中")
            self.installed_label.setStyleSheet(f"color: {STATUS_COLORS['warn']};")
        else:
            self.set_installed(self._installed)


class SchemePage(ProgressAreaMixin, PageBase):
    install_requested = pyqtSignal(str)  # scheme key
    uninstall_requested = pyqtSignal(str)
    check_update_requested = pyqtSignal(str)
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

        self.status_row = self.add_row("安装状态", "")
        self.frontend_row = self.add_row("目标前端", "")
        self.rime_dir_row = self.add_row("配置目录", "")

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

        页面级不变量：busy 时全部方案卡按钮、自定义安装、缓存选项均禁用，
        仅取消可用；退出时按状态机规则恢复。running_key 卡片显示「执行中」。
        """
        self._running_key = running_key if busy else ""
        for card in self._cards:
            card.set_busy(busy, running=busy and card.scheme.key == self._running_key)
        self.cache_check.setEnabled(not busy)
        self.custom_input.setEnabled(not busy)
        self.log_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
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
        self._refresh_skin_header()
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
