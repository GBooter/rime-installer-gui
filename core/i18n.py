"""轻量 i18n 核心（Phase 6c / v1.0.0）。

设计取舍：
- 中文为源语言（内嵌 `ZH`），英文包按需加载。**未覆盖的键自动回退中文**，
  因此翻译可以渐进补充，不会出现界面空白或 KeyError。
- 偏好与主题一致走 QSettings（org/app 与 core.theme 相同），不进 SQLite
  manifest：它属于界面呈现，不是业务数据。
- 所有 Qt / 英文包的导入都在函数内懒加载，保持 core 层 CLI 可导入
  （tools/snapshot_cli.py 等不引 Qt）。

用法::

    from core import i18n
    i18n.tr("nav.engine")              # "引擎安装" / "Engine"
    i18n.tr("engine.installing", n=3)  # 支持命名占位符
"""

from __future__ import annotations

ZH = "zh_CN"
EN = "en_US"

# 与 core.theme 保持一致的组织/应用名，避免偏好散落两处
_ORG_NAME = "rime-installer"
_APP_NAME = "rime-installer"
_SETTINGS_KEY = "ui/language"

#: 可切换语言（顺序即下拉展示顺序）
LANGUAGES = (ZH, EN)

#: 源语言文案：既是中文包，也是英文缺键时的回退
ZH_TEXT = {
    # ---- 导航 ----
    # ⚠️ nav.* 与 *.title 同时是页面 objectName（addSubInterface 的 routeKey），
    # 必须全页面唯一：v1.0.5 前 nav.doctor/doctor.title 与欢迎页同为「环境体检」，
    # routeKey 碰撞导致体检页导航项被 NavigationPanel 静默丢弃（侧栏只有 6 项）。
    "nav.welcome": "欢迎",
    "nav.engine": "引擎安装",
    "nav.scheme": "方案市场",
    "nav.appearance": "外观",
    "nav.backup": "备份恢复",
    "nav.doctor": "环境修复",
    "nav.log": "日志",
    "nav.settings": "设置",
    # ---- 窗口 ----
    "app.title": "中州韵输入法安装器",
    # ---- 通用 ----
    "common.install": "安装",
    "common.uninstall": "卸载",
    "common.refresh": "刷新",
    "common.cancel": "取消",
    "common.close": "关闭",
    "common.delete": "删除",
    "common.restore": "恢复",
    "common.backup": "备份",
    "common.export": "导出",
    "common.import": "导入",
    "common.view_log": "查看日志",
    "common.running": "执行中",
    "common.prepare": "准备中",
    "common.dash": "—",
    "common.language": "界面语言",
    # ---- 状态 ----
    "status.installed": "已安装",
    "status.not_installed": "未安装",
    "status.unknown": "未知",
    "status.enabled": "可用",
    "status.disabled": "禁用",
    # ---- 欢迎页 ----
    "welcome.title": "环境体检",
    "welcome.subtitle": "所有安装决策基于此检测结果；点击「重新检测」可随时刷新",
    "welcome.redetect": "重新检测",
    # ---- 引擎页 ----
    "engine.title": "引擎安装",
    "engine.subtitle": "中州韵输入法引擎（librime）经系统 apt 安装；安装方案前必须先装引擎",
    "engine.install": "安装引擎",
    "engine.uninstall": "卸载引擎",
    "engine.installing": "正在安装引擎",
    "engine.uninstalling": "正在卸载引擎",
    # ---- 方案页 ----
    "scheme.title": "方案市场",
    "scheme.subtitle": "所有方案经 aria2 多线程加速下载到本地，再由「东风破」安装；需先安装引擎",
    "scheme.installing": "正在安装方案",
    "scheme.uninstalling": "正在卸载方案",
    "scheme.updating": "正在更新方案",
    # ---- 备份页 ----
    "backup.title": "备份恢复",
    "backup.subtitle": (
        "把当前输入法配置（含方案、词典、个人设置）打包成 zip，出问题可一键还原，也可导出分享"
    ),
    "backup.do": "立即备份",
    "backup.doing": "正在备份",
    "backup.restoring": "正在恢复",
    # ---- 体检页 ----
    # v1.0.5：标题与欢迎页（环境体检）区分——两者 objectName 相同时
    # NavigationPanel.insertItem 会静默丢弃后者的导航项（详见 nav 区注释）
    "doctor.title": "环境修复",
    "doctor.subtitle": "一键排查「装了 rime 却用不了」的高频问题：环境变量、框架进程、部署状态等",
    "doctor.fix": "一键修复",
    "doctor.fixing": "正在修复",
    # doctor.redetect / doctor.restart / doctor.config_tool 的定义统一放在
    # 「体检页」区段（下方），v1.0.26 前这里重复定义过，后者会静默覆盖前者。
    "doctor.redeploy": "重新部署",
    # ---- 日志页 ----
    "log.title": "日志",
    "log.subtitle": "安装过程的完整输出会写入日志目录，可随时查看或打包反馈",
    # ---- 设置页 ----
    "settings.title": "设置",
    "settings.subtitle": "界面语言、外观主题、apt 软件源与关于信息",
    "settings.theme": "外观主题",
    "settings.frontend": "推荐前端",
    "settings.version": "版本",
    "settings.author": "作者",
    "settings.supported_systems": "适用系统",
    # v1.0.30：版本通配（core/compat.py）——「当前系统」列按运行时探测
    # 显示 os_name + os_version + 判定结果，不再依赖写死的支持列表
    "settings.current_system": "当前系统",
    "settings.support_ok": "支持（{minimum} 及以上）",
    "settings.support_below": "低于实测基线（最低 {minimum}），不保证可用",
    "settings.support_unknown": "未识别的发行版，按通用 Linux 处理",
    "settings.about": "关于",
    # v1.0.28：三段式改版（Hero 高清图标 + 界面与外观 + apt 镜像）
    "settings.tagline": "基于 RIME / plum / aria2",
    "settings.prefs": "界面与外观",
    "settings.mirror": "apt 软件源镜像",
    "settings.mirror_only_ubuntu": "仅 Ubuntu",
    "settings.current_source": "当前源",
    "settings.mirror_hint": (
        "切换镜像会备份原源文件（同目录 .rime-backup）并执行 apt update；"
        "Deepin 官方源位于国内，通常无需切换。"
        "Linux Mint 的源由 mint 自带工具管理，本页只做展示。"
    ),
    "settings.mirror_note": (
        "当前系统为 {os}：此页不提供镜像切换"
        "（Deepin 官方源位于国内，通常无需切换；Linux Mint 的源由 mint 自带工具管理）。"
    ),
    "settings.speedtest": "测速",
    "settings.speedtesting": "测速中…",
    "settings.switch_mirror": "切换为选中镜像",
    "settings.switching": "切换中…",
    "settings.switch_tip": "将系统 apt 源切换为选中镜像（需要管理员密码）",
    "settings.speedtest_tip": "并行测试各镜像下载延迟",
    # ---- 镜像列表项（v1.0.28：列表改为无边框行，文案拆成片段拼接） ----
    "mirror.official": "官方",
    "mirror.mirror": "镜像",
    "mirror.untested": "未测速",
    "mirror.current": "当前",
    # ---- 主题 ----
    "theme.auto": "跟随系统",
    "theme.light": "浅色",
    "theme.dark": "深色",
    # 「跟随系统」时下拉里显式标出实际生效的明暗，用户不必去猜
    "theme.auto_current": "跟随系统（当前：{mode}）",
    # ---- 页面按钮 / 选项 ----
    "engine.update_apt": "安装前更新软件源（apt update）",
    "engine.purge": "同时删除配置与词库（不可恢复）",
    "scheme.custom_install": "安装自定义方案",
    "scheme.check_update": "检查更新",
    # ---- 皮肤（F15：写入用户目录的配色，不下载仓库） ----
    "scheme.apply": "应用皮肤",
    "scheme.reapply": "重新应用",
    "scheme.remove": "移除皮肤",
    "scheme.applied": "✓ 已应用",
    "scheme.apply_tip": "把该配色写入用户目录 default.custom.yaml 并重新部署，即刻生效",
    "scheme.reapply_tip": "重新写入该配色并重新部署（切换皮肤会替换掉上一套）",
    "scheme.remove_tip": "移除本安装器写入的配色配置块并恢复 RIME 默认外观，你在 default.custom.yaml 里的其他配置原样保留",
    "scheme.uninstall_skin_title": "确认移除皮肤「{name}」？",
    "scheme.uninstall_skin_body": (
        "将移除本安装器写入的配色配置块并重新部署；\n"
        "移除后输入法恢复 RIME 默认外观，\n"
        "你在 default.custom.yaml 里的其他配置原样保留，不影响已安装的方案。"
    ),
    # v1.0.12：切换语义明确化 + 当前态标识 + 我的默认皮肤
    "scheme.switch_to": "切换到此皮肤",
    "scheme.switch_to_tip": "把该配色写入用户目录 default.custom.yaml 并重新部署，即刻生效；会替换当前正在使用的皮肤",
    "scheme.redeploy": "重新部署",
    "scheme.redeploy_tip": "重新写入该配色并部署（当前正在使用的皮肤，切换其他皮肤会替换掉它）",
    "scheme.current_skin": "当前皮肤",
    "scheme.my_default": "我的默认",
    "scheme.set_default": "设为默认",
    "scheme.set_default_tip": "标记为我的默认皮肤，之后可在皮肤分区一键恢复",
    "scheme.restore_default": "恢复我的默认",
    "scheme.restore_default_tip": "一键切换回我设置的默认皮肤（尚未设置时不可用）",
    "scheme.current_label": "当前：{name}",
    "scheme.no_current": "当前：未使用安装器皮肤",
    "scheme.installed_skins": "已安装：",
    "common.cancel_task": "取消任务",
    # ---- 方案分类与筛选 ----
    "category.official": "官方方案",
    "category.third-party": "社区热门",
    "category.addon": "词库增强",
    "category.skin": "皮肤",
    "category.custom": "自定义",
    "filter.all": "全部方案",
    "filter.official": "仅官方",
    "filter.third-party": "仅社区热门",
    "filter.addon": "仅词库增强",
    "filter.skin": "仅皮肤",
    "scheme.skin_scope": "应用后自动重新部署。注意：Linux 下候选窗外观由输入法框架决定，rime 配色在 Linux 上不渲染——"
    "fcitx5 用户请到「外观」页切换 classicui 主题；ibus 用户可在「外观」页设置横竖排。"
    "配色仅在 Windows(weasel)/macOS(squirrel) 完全生效",
    "filter.installed": "仅已安装",
    "scheme.section_schemes": "输入法方案",
    "scheme.section_skins": "皮肤",
    # ---- 外观（v1.0.13：fcitx5 主题 + ibus 全局项，Linux 真实可见） ----
    "appearance.title": "外观",
    "appearance.subtitle": "Linux 上候选窗由输入法框架绘制，rime 配色不渲染；这里切换 fcitx5 主题 / 设置 ibus 外观项，即时真实可见",
    "appearance.frontend_row": "当前前端",
    "appearance.theme_row": "fcitx5 当前主题",
    "appearance.ibus_row": "ibus 当前外观",
    "appearance.section_fcitx5": "候选窗主题（fcitx5）",
    "appearance.section_ibus": "候选外观（ibus-rime）",
    "appearance.theme_current": "当前",
    "appearance.theme_switch": "切换",
    "appearance.theme_switch_tip": "把 classicui.conf 的 Theme 改为此主题，候选窗即时换装",
    "appearance.theme_revert": "恢复系统默认",
    "appearance.theme_revert_tip": "还原本安装器改写前的 classicui.conf（未改写过时不可用）",
    "appearance.theme_source_system": "系统",
    "appearance.theme_source_user": "用户",
    "appearance.theme_source_installer": "内置",
    "appearance.theme_note": "点主题卡（或卡上的「切换」）会改写 classicui.conf 并自动重启 fcitx5，候选窗即时换装；内置主题开箱即用，自己的主题放进用户主题目录后点「刷新主题目录」即可看到。",
    "appearance.theme_backup_exists": "已备份原件",
    "appearance.theme_no_backup": "尚未改动过主题配置",
    "appearance.ibus_horizontal": "横排候选",
    "appearance.ibus_horizontal_tip": "候选词横向排列（ibus-rime 启动时读取，需注销重登生效）",
    "appearance.ibus_inline_preedit": "内嵌预编辑",
    "appearance.ibus_inline_preedit_tip": "把输入中的编码嵌入光标处而非候选窗内",
    "appearance.ibus_preedit_color": "预编辑高亮风格",
    "appearance.ibus_preedit_color_tip": "ibus-rime 仅内置 4 种预编辑高亮色，不影响候选窗配色",
    "appearance.ibus_default": "默认",
    "appearance.ibus_apply": "应用",
    "appearance.ibus_apply_tip": "把当前选项写入 ibus_rime.custom.yaml（注销重登后生效）",
    "appearance.ibus_revert": "恢复默认",
    "appearance.ibus_revert_tip": "移除本安装器写入的 ibus 外观配置块",
    "appearance.ibus_note": "ibus-rime 仅在启动时读取外观配置，应用后需注销重登或重启 ibus 生效",
    "appearance.ibus_no_dir": "未识别 rime 配置目录，请先到设置页指定输入法前端",
    "appearance.ibus_unset": "未设置（ibus 默认）",
    "appearance.not_applicable": "当前检测到的前端不使用该机制",
    "msg.theme_switched": "候选窗主题已切换",
    "msg.theme_switched_hint": "classicui.conf 已改写，候选窗即时换装；如未变化可注销重登",
    "msg.theme_reverted": "已恢复系统默认主题",
    "msg.theme_reverted_hint": "classicui.conf 已还原为改写前的状态",
    # ---- 主题（v1.0.16：移除市场，保留切换 + 打开目录） ----
    "appearance.open_themes_dir": "打开主题目录",
    "appearance.open_themes_dir_tip": "在文件管理器中打开 fcitx5 用户主题目录，放自制主题文件夹后回本页切换",
    "appearance.refresh_themes": "刷新主题目录",
    "appearance.refresh_themes_tip": "重新扫描主题目录，发现刚放入的新主题（无需重启软件）",
    "appearance.refreshed": "已刷新主题目录",
    "appearance.tone_dark": "深色",
    "appearance.tone_light": "浅色",
    # ---- 外观页主题展示（v1.0.19：标签页 + 卡片网格 + 上游署名） ----
    "appearance.tab_builtin": "内置主题",
    "appearance.tab_custom": "我的主题",
    "appearance.tab_label": "{name}（{count}）",
    "appearance.card_current": "当前使用",
    "appearance.custom_empty_title": "还没有自定义主题",
    "appearance.custom_empty_hint": "点下方「打开主题目录」，把下载的主题文件夹（需含 theme.conf）放进去，再点「刷新主题目录」，它就会出现在这里。",
    "appearance.custom_hint": "主题目录：~/.local/share/fcitx5/themes（一个主题一个文件夹，内含 theme.conf 与图片资源）",
    "appearance.system_theme_note": "当前用的是系统自带主题 {name}，为保持列表清爽未在此列出；想换回它点下方「恢复系统默认」。",
    "appearance.thanks_title": "鸣谢 · Mellow 主题作者",
    "appearance.thanks_body": "内置的 Mellow 系列主题（{count} 款）由 sanweiya 设计并持续维护，以 BSD-2-Clause 许可开源。感谢这份用心，让 Linux 上的候选窗也能这么好看。",
    "appearance.thanks_note": "本安装器只做整包内置，并补齐了上游缺失的 radio.svg / arrow.svg、修正了深色主题的菜单文字色，视觉设计未作改动。",
    "appearance.thanks_open": "查看开源项目",
    # ---- 外观页从链接导入主题（v1.0.21） ----
    "appearance.import_placeholder": "粘贴 GitHub 主题仓库链接，如 https://github.com/thep0y/fcitx5-themes-candlelight",
    "appearance.import_button": "导入主题",
    "appearance.import_tip": "下载该仓库并把里面全部主题装进「我的主题」",
    "appearance.import_tip_invalid": "请填写 GitHub 仓库链接或 user/repo（可带 @分支）",
    "appearance.import_note": "一次导入仓库里的所有主题（一个仓库通常有多款）；导入后点卡片即可切换，想清理可点「打开主题目录」。",
    "msg.theme_imported": "已导入 {count} 款主题",
    "msg.theme_imported_hint": "已放进用户主题目录，点卡片即可切换；部分主题是图片型，切换后立即可见",
    "msg.theme_import_empty": "这个仓库里没有 fcitx5 主题",
    "msg.theme_import_empty_hint": "「{name}」里没找到任何 theme.conf（主题目录或变体配置都没有）。若想装输入方案，请到「方案市场」。",
    # 空仓库的三类具体原因（v1.0.22）
    "msg.theme_android": "这是手机端主题，桌面版用不了",
    "msg.theme_android_hint": "「{name}」里是 fcitx5-android 的 JSON 配色（isDark / keyBackgroundColor），只适用于安卓版小企鹅输入法。桌面版主题的仓库根目录或子目录里应该有 theme.conf。",
    "msg.theme_color_scheme": "这是配色方案，不是桌面主题",
    "msg.theme_color_scheme_hint": "「{name}」里是 fcitx5-android / fcitx5-macos 的配色文件（[LightMode]、OverrideDefault），桌面版 fcitx5 无法直接套用。桌面主题需要 theme.conf。",
    "msg.theme_source_only": "这个仓库需要先自行构建",
    "msg.theme_source_only_hint": "「{name}」里只有源码模板（build.py / *.tera / config/*.conf.in），没有现成的 theme.conf。请到该仓库的 Releases 页下载编译好的主题包，或手动运行它的构建脚本。",
    "msg.go_import_theme": "去外观页导入主题",
    # ---- 外观页删除用户主题（v1.0.24） ----
    "appearance.theme_delete_tip": "从主题目录中删除这款主题",
    "appearance.delete_title": "确认删除主题「{name}」？",
    "appearance.delete_target": "将删除：{path}",
    "appearance.delete_note": "删除后该主题文件夹会从磁盘移除，之后仍可重新导入或手动放回。",
    "appearance.delete_current_warn": "⚠ 这是当前正在使用的主题：删除后会切回你之前用的主题（找不到则用系统默认），并重启 fcitx5。",
    "msg.theme_deleted": "主题已删除",
    "msg.theme_deleted_hint": "主题文件夹已从用户主题目录移除，其他主题不受影响",
    "msg.theme_deleted_hint_reset": "主题文件夹已移除，候选窗已切回你之前用的主题",
    "msg.ibus_style_applied": "ibus 外观已应用",
    "msg.ibus_style_applied_hint": "配置已写入 ibus_rime.custom.yaml，注销重登或重启 ibus 后生效",
    "msg.ibus_style_reverted": "ibus 外观已恢复默认",
    "msg.ibus_style_reverted_hint": "本安装器写入的 ibus 外观配置块已移除",
    # ---- 行标签（多页共用） ----
    "row.rime_dir": "配置目录",
    "row.frontend": "目标前端",
    "row.backup_dir": "备份目录",
    "row.backup_count": "备份数量",
    "row.latest_backup": "最近备份",
    # ---- 行标签（首页体检，v1.0.4：修复裸 key 直出成英文标识符） ----
    "row.os": "操作系统",
    "row.session": "会话协议",
    "row.desktop": "桌面环境",
    "row.framework": "输入法框架",
    "row.engine": "引擎状态",
    "row.env": "输入法环境变量",
    # ---- 体检页 ----
    "doctor.overall": "总体结果",
    "doctor.fix_tip": "自动执行所有可修复项（写环境变量、拉起框架、重新部署）",
    # v1.0.27：登录配置的次要入口（体检已就绪时也能主动重写）
    "doctor.write_login": "写入登录配置",
    "doctor.write_login_tip": (
        "把当前前端的环境变量写入 environment.d 与 ~/.xsessionrc"
        "（下次登录生效；幂等，可随时重写）"
    ),
    "doctor.write_login_tip_ok": (
        "登录配置已就绪；点此可重写一份（换过前端或手工改过配置时用它同步）"
    ),
    "doctor.restart": "重启输入法框架",
    "doctor.redeploy_tip": (
        "编译到临时目录，成功后才替换 build 并重启框架（Ubuntu 需装 librime-bin）"
    ),
    "doctor.config_tool": "输入法配置工具",
    "doctor.config_tool_tip": "打开 ibus-setup / fcitx5-configtool 添加或管理输入法",
    "doctor.redetect": "重新体检",
    "doctor.guide": (
        "部署引导：安装方案后若输入无候选，点「重新部署」（编译生成 build；"
        "失败会保留原配置并说明原因）；需要在框架中添加或切换输入法，"
        "点「输入法配置工具」。重启框架只重启进程，不保证重新编译。"
    ),
    "doctor.summary_bad": "{bad} 个问题需处理，{warn} 个警告",
    "doctor.summary_warn": "{warn} 个警告，无阻塞性问题",
    "doctor.summary_ok": "未发现问题",
    "doctor.fixable_count": "（{fixable} 项可自动修复）",
    "doctor.fix_button_n": "自动修复 {fixable} 个可修复项",
    "doctor.fix_button_none": "当前没有可自动修复的项目",
    # ---- 备份页 ----
    "backup.note_placeholder": "备注（可选），如「装雾凇前」",
    "backup.default_note": "手动备份",
    "backup.auto_note": "恢复前自动备份",
    "backup.pre_backup": "恢复前先备份当前状态（推荐，避免不可逆覆盖）",
    "backup.clean": "清空后恢复（删除配置目录中的现有文件，严格回到备份那一刻）",
    "backup.clean_tip": "不勾选时为覆盖合并：备份里的文件覆盖现有同名文件，其余文件保留",
    "backup.list_title": "备份记录",
    "backup.restore": "恢复选中",
    "backup.export_to": "导出到…",
    "backup.do_import": "导入备份",
    "backup.open_dir": "打开备份目录",
    "backup.empty": "暂无备份，点击「立即备份」创建第一个配置快照",
    "backup.file_count": "{count} 个",
    # v1.0.23：列表行/提示/确认框全部走 i18n（此前中文硬编码在 BackupInfo.label 里）
    "backup.row_base": "{stamp} · {size} · {count} 个文件",
    "backup.row_frontend": " · {frontend}",
    "backup.row_note": " · {note}",
    "backup.row_invalid": "{stamp} · {size} · 无法识别：{error}",
    "backup.row_invalid_no_error": "{stamp} · {size} · 无法识别",
    "backup.latest_value": "{stamp}（{size}）",
    "backup.select_first": "请先在上方列表中选择一个备份",
    "backup.invalid_tip": "该备份无法识别，可能已损坏",
    "backup.restore_tip": "把该备份还原到当前配置目录",
    "backup.create_tip": "打包当前配置目录为一个 zip 快照",
    "backup.rime_missing": "未能识别（请到设置页指定输入法前端）",
    "backup.rime_not_created": "{path}（尚未创建）",
    "backup.restore_uncancellable": "恢复过程中不可取消，请稍候",
    "backup.unknown": "（未知）",
    "backup.confirm_title": "确认恢复该备份？",
    "backup.confirm_time": "备份时间：{stamp}",
    "backup.confirm_source": "备份来源：{path}",
    "backup.confirm_files": "文件数量：{count}",
    "backup.confirm_packages": "已装方案：{packages}",
    "backup.confirm_target": "将还原到：{path}",
    "backup.confirm_clean": "将先清空配置目录中的现有文件，再还原备份内容（严格回到备份那一刻）。",
    "backup.confirm_merge": "将用备份内容覆盖同名文件，备份之外的现有文件会保留。",
    "backup.confirm_target_missing": "目标目录尚不存在，恢复时会自动创建。",
    "backup.confirm_target_differs": "⚠ 该备份来自另一个目录（{path}），内容仍会写入上面的目标目录。",
    "backup.delete_title": "确认删除该备份？",
    "backup.delete_body": "{name}\n删除后不可恢复，建议先导出一份到其他位置。",
    "tip.busy": "操作进行中，请稍候…",
    "dialog.export_title": "导出备份到",
    # v1.0.27：写入登录配置的确认框
    "dialog.write_login_title": "写入登录配置（输入法环境变量）？",
    "dialog.write_login_body": (
        "将把当前前端（{frontend}）的下列环境变量写入两个文件：\n"
        "{vars}\n\n"
        "  {conf}\n"
        "  {xs}（只改写本安装器的标记段，你原有的内容原样保留）\n\n"
        "这两个文件都在你的主目录下，写入不需要管理员密码。\n"
        "· 需要重新登录才生效；本次会话中已运行的程序不受影响。\n"
        "· 重复执行是幂等的，内容会被重写为当前前端的值。\n"
        "· 想撤销：删掉这两个文件里本安装器的段落即可恢复系统默认。"
    ),
    # v1.0.28：切换 apt 镜像的确认框
    "dialog.switch_mirror_title": "切换 apt 镜像为「{name}」？",
    "dialog.switch_mirror_body": (
        "将把系统软件源指向：{url}\n"
        "原源文件会备份为同目录 .rime-backup，随后执行 apt update。\n"
        "需要管理员权限（pkexec）。"
    ),
    "dialog.zip_filter": "备份文件 (*.zip)",
    "dialog.import_title": "选择配置备份",
    "dialog.all_files": "所有文件 (*)",
    # ---- 任务反馈（InfoBar） ----
    "msg.backup_done": "备份完成",
    "msg.backup_created": "备份已创建",
    "msg.backup_summary": "{name}（{count} 个文件，{size}）",
    "msg.restore_done": "配置已恢复",
    "msg.restore_hint": "已还原到备份状态，建议到「引擎安装」页重新部署或重启输入法框架",
    # v1.0.23：备份/恢复失败给定向建议，不再只甩退出码
    "msg.backup_failed": "备份失败",
    "msg.backup_failed_hint": "打包没有完成：配置目录可能不存在，或里面没有任何可备份的文件。详情见日志页。",
    "msg.restore_failed": "恢复失败",
    "msg.restore_failed_hint": "备份文件可能已损坏或被移动。恢复前的自动备份仍在「备份记录」里，可再次尝试。详情见日志页。",
    "msg.open_dir_failed": "打开备份目录失败",
    "msg.open_dir_failed_hint": "无法创建或访问 {path}：{error}",
    "msg.open_dir_unsupported": "无法打开文件管理器",
    "msg.open_dir_unsupported_hint": "系统没有提供文件管理器，可手动前往：{path}",
    "msg.backup_imported_body": "{name}（{count} 个文件）",
    "msg.delete_failed_hint": "只允许删除本安装器备份目录中的 .zip 文件",
    "msg.engine_installed": "引擎安装完成",
    # v1.0.34：补上「加入输入源」这一步——真机反馈装完找不到输入法选项，
    # 根因是输入源没注册（与 InfoBar 上的「去环境体检」按钮配套）
    "msg.engine_install_hint": (
        "{pkg} 已安装。可前往「方案市场」安装输入法方案，并到「环境体检」页"
        "把「中文(Rime)」加入输入源——不加入则切换不到 Rime"
    ),
    "msg.goto_doctor": "去环境体检",
    # v1.0.29：apt update 改为尽力而为后，install 失败才会走到这里
    "msg.engine_install_failed": "引擎安装失败",
    "msg.engine_install_failed_hint": (
        "apt 没能装上引擎。最常见的原因是软件源里有已失效的第三方仓库"
        "（报 404 或缺公钥），其次是没启用 universe 组件、或网络不通。"
        "软件源索引更新失败已被自动忽略，不再是阻断原因；"
        "详情见「日志」页，修好软件源后可直接重试。"
    ),
    "msg.op_done": "操作完成",
    "msg.restart_framework": "重启输入法框架",
    "msg.framework_hint": "输入法框架已处理，如输入无候选请稍候片刻待 rime 自动部署",
    "msg.fix_done": "修复完成",
    "msg.fix_hint": "环境变量已写入 ~/.config/environment.d 与 ~/.xsessionrc（重新登录后生效）；如本次拉起了框架，请稍候片刻再试输入",
    "msg.login_written": "登录配置已写入",
    "msg.login_written_hint": (
        "已写入 {conf} 与 ~/.xsessionrc；重新登录后生效，"
        "本次会话中已运行的程序不受影响"
    ),
    "msg.mirror_switched": "apt 镜像已切换",
    "msg.mirror_hint": "软件源索引已更新，后续引擎安装将走新镜像",
    "msg.skin_applied": "「{name}」已应用",
    "msg.skin_applied_hint": "配色已写入用户目录并触发重新部署，输入法外观即刻生效；切换到其他皮肤会替换当前配色",
    "msg.skin_removed": "皮肤已移除",
    "msg.skin_removed_hint": "配色配置块已从 default.custom.yaml 移除并重新部署，输入法恢复 RIME 默认外观，你的其他配置不受影响",
    "msg.default_skin_set": "已将「{name}」设为默认皮肤",
    "msg.default_skin_set_hint": "之后随时可在皮肤分区点「恢复我的默认」一键切回",
    "msg.default_skin_restored": "正在恢复默认皮肤",
    "msg.default_skin_restore_hint": "「{name}」正在写入并重新部署，即将生效",
    "msg.no_default_skin": "还没有设置默认皮肤",
    "msg.no_default_skin_hint": "在皮肤卡上点「设为默认」指定一款，之后即可一键恢复",
    "msg.already_default_skin": "当前已是默认皮肤",
    "msg.already_default_skin_hint": "「{name}」就是你设置的默认皮肤，无需恢复",
    "msg.default_skin_cleared": "默认皮肤偏好已清除",
    "msg.default_skin_cleared_hint": "移除的正是默认皮肤，可重新指定一款",
    "msg.engine_uninstalled": "引擎已卸载",
    "msg.engine_uninstalled_hint": "{pkg} 已卸载",
    "msg.purge_note": "已清理配置目录：{paths}",
    "msg.purge_suffix": "，配置目录已清理：{paths}",
    "msg.cancelled": "已取消",
    "msg.cancel_hint": "任务已被用户取消，可重新开始",
    "msg.failed": "操作失败",
    "msg.fail_hint": "退出码 {code}，请前往「日志」页查看详细输出",
    "msg.deploy_failed": "重新部署未完成",
    "msg.deploy_failed_hint": (
        "原配置目录未被删除，输入法仍可继续使用。若提示找不到 rime_deployer，"
        "Ubuntu 需另装 librime-bin（sudo apt install librime-bin）后重试；"
        "也可在输入法里切换一次 Rime 触发部署"
    ),
    # ---- 提示类（InfoBar） ----
    "msg.no_rime_dir": "无法确定配置目录",
    "msg.no_rime_dir_hint": "未能识别出当前前端的 rime 用户目录，请先安装引擎或到设置页指定前端",
    "msg.no_rime_dir_short": "未能识别 rime 用户目录，请先安装引擎",
    "msg.parse_failed": "格式无法识别",
    "msg.parse_hint": "请按 user/repo、user/repo@branch 或 user/repo@branch:recipe 填写",
    # v1.0.20：仓库不是 RIME 方案包（主题/图片/普通项目仓库）时的定向提示
    "msg.not_rime_package": "这个地址不是 RIME 方案包",
    "msg.not_rime_package_hint": "「{name}」里没找到任何 RIME 配置（*.yaml / *.txt / *.gram），已停止安装，你的配置没有被改动。想装皮肤/主题请到「外观」页，把主题文件夹放进用户主题目录后点「刷新主题目录」；想装输入方案请确认仓库根目录直接有 default.yaml 之类的配置文件。",
    "msg.no_change": "未检测到文件变化",
    "msg.no_change_hint": "「{name}」安装命令已执行，但配置目录无变化，请到日志页核对输出",
    "msg.no_baseline": "无可对比基线",
    "msg.no_baseline_hint": "「{name}」尚未通过本安装器记账，可先重新安装建立基线",
    "msg.update_found": "发现更新",
    "msg.update_found_hint": "「{name}」上游内容有变化，点击「重新安装」即可升级",
    "msg.up_to_date": "已是最新",
    "msg.up_to_date_hint": "「{name}」与上游内容一致",
    "msg.nothing_fixable": "无可自动修复项",
    "msg.nothing_fixable_hint": "所选项目需要手动处理，请参考每项右侧的建议",
    "msg.mirror_failed": "无法切换镜像",
    "msg.import_failed": "导入失败",
    "msg.backup_imported": "备份已导入",
    "msg.export_failed": "导出失败",
    "msg.backup_exported": "备份已导出",
    "msg.delete_failed": "删除失败",
    "msg.backup_deleted": "备份已删除",
    # ---- 语言 ----
    "lang.zh_CN": "简体中文",
    "lang.en_US": "English",
}

_current: str = ZH
#: 已加载的语言包按语言缓存，切换时整体换引用（避免切回中文时仍用着英文包）
_packs: dict[str, dict[str, str]] = {ZH: dict(ZH_TEXT)}
_texts: dict[str, str] = _packs[ZH]


def languages() -> tuple[str, ...]:
    """可切换语言列表。"""
    return LANGUAGES


def label(lang: str) -> str:
    """语言自身的展示名（始终用该语言的原生写法）。"""
    return {"zh_CN": "简体中文", "en_US": "English"}.get(lang, lang)


def current() -> str:
    """当前语言代码。"""
    return _current


def tr(key: str, **kwargs: object) -> str:
    """翻译 key；缺失时依次回退中文源 → key 本身。

    支持命名占位符：`tr("x", n=3)` 会替换 `{n}`。
    """
    text = _texts.get(key) or ZH_TEXT.get(key) or key
    return text.format(**kwargs) if kwargs else text


def tr_or(key: str, fallback: str, **kwargs: object) -> str:
    """翻译 key；当前语言与中文源都没有该键时返回 ``fallback``。

    用于「中文由代码字面量提供、英文按需补充」的场景（如体检结果的
    title/detail/advice）：中文不必在 ZH_TEXT 里重复维护一份，英文缺键也不会
    退化成裸 key。``fallback`` 视为成品文案，不再做占位符替换。
    """
    text = _texts.get(key) or ZH_TEXT.get(key)
    if text is None:
        return fallback
    return text.format(**kwargs) if kwargs else text


def set_language(lang: str) -> str:
    """切换当前语言（未知代码回退中文），返回实际生效的语言。"""
    global _current, _texts
    _current = lang if lang in LANGUAGES else ZH
    _texts = _pack(_current)
    return _current


def _pack(lang: str) -> dict[str, str]:
    """取语言包（首次按需加载后缓存）。"""
    if lang not in _packs:
        _packs[lang] = _load(lang)
    return _packs[lang]


def _load(lang: str) -> dict[str, str]:
    """加载语言包；英文包缺失或异常时静默降级为中文。"""
    if lang == ZH:
        return dict(ZH_TEXT)
    try:
        if lang == EN:
            from core.translations.en import EN_TEXT

            return dict(EN_TEXT)
    except Exception:  # noqa: BLE001 — 语言包损坏不应让界面起不来
        pass
    return dict(ZH_TEXT)


def load_preference() -> str:
    """读取持久化的语言偏好（默认中文）。"""
    from PyQt6.QtCore import QSettings

    settings = QSettings(_ORG_NAME, _APP_NAME)
    value = settings.value(_SETTINGS_KEY, ZH, type=str)
    return value if value in LANGUAGES else ZH


def save_preference(lang: str) -> None:
    """持久化语言偏好。"""
    from PyQt6.QtCore import QSettings

    settings = QSettings(_ORG_NAME, _APP_NAME)
    settings.setValue(_SETTINGS_KEY, lang if lang in LANGUAGES else ZH)
    settings.sync()
