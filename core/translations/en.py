"""英文语言包（Phase 6c / v1.0.0）。

覆盖范围是「主干文案」：导航项、页面标题/副标题、通用按钮与状态词。
其余文案由 `core.i18n.tr` 自动回退中文，随后续迭代补齐即可 —— 因此本包
不需要与 ZH_TEXT 严格等长。
"""

from __future__ import annotations

EN_TEXT: dict[str, str] = {
    # ---- 导航 ----
    "nav.welcome": "Welcome",
    "nav.engine": "Engine",
    "nav.scheme": "Schemes",
    "nav.appearance": "Appearance",
    "nav.backup": "Backup",
    "nav.doctor": "Diagnostics",
    "nav.log": "Logs",
    "nav.settings": "Settings",
    # ---- 窗口 ----
    "app.title": "RIME Installer",
    # ---- 通用 ----
    "common.install": "Install",
    "common.uninstall": "Uninstall",
    "common.refresh": "Refresh",
    "common.cancel": "Cancel",
    "common.close": "Close",
    "common.delete": "Delete",
    "common.restore": "Restore",
    "common.backup": "Backup",
    "common.export": "Export",
    "common.import": "Import",
    "common.view_log": "View logs",
    "common.running": "Running",
    "common.prepare": "Preparing",
    "common.dash": "—",
    "common.language": "Language",
    # ---- 状态 ----
    "status.installed": "Installed",
    "status.not_installed": "Not installed",
    "status.unknown": "Unknown",
    "status.enabled": "Enabled",
    "status.disabled": "Disabled",
    # ---- 欢迎页 ----
    "welcome.title": "Environment",
    "welcome.subtitle": (
        "Every install decision is based on this report; re-detect anytime"
    ),
    "welcome.redetect": "Re-detect",
    # ---- 引擎页 ----
    "engine.title": "Input Method Engine",
    "engine.subtitle": (
        "The RIME engine (librime) is installed via apt; install it before any scheme"
    ),
    "engine.install": "Install engine",
    "engine.uninstall": "Uninstall engine",
    "engine.installing": "Installing engine",
    "engine.uninstalling": "Uninstalling engine",
    # ---- 方案页 ----
    "scheme.title": "Scheme Market",
    "scheme.subtitle": (
        "Schemes are downloaded with multi-threaded aria2, then installed by plum; "
        "the engine must be installed first"
    ),
    "scheme.installing": "Installing scheme",
    "scheme.uninstalling": "Uninstalling scheme",
    "scheme.updating": "Updating scheme",
    # ---- 备份页 ----
    "backup.title": "Backup & Restore",
    "backup.subtitle": (
        "Zip your input method configuration (schemes, dictionaries, personal "
        "settings) for one-click restore or sharing"
    ),
    "backup.do": "Back up now",
    "backup.doing": "Backing up",
    "backup.restoring": "Restoring",
    # ---- 体检页 ----
    "doctor.title": "Diagnostics",
    "doctor.subtitle": (
        "Find why RIME is installed but not working: environment variables, "
        "framework process, deployment state and more"
    ),
    "doctor.fix": "Fix all",
    "doctor.fixing": "Fixing",
    # doctor.redetect / doctor.restart / doctor.config_tool are defined once in
    # the "diagnostics" section below (v1.0.26 removed the duplicates that
    # silently overrode them).
    "doctor.redeploy": "Redeploy",
    # ---- 日志页 ----
    "log.title": "Logs",
    "log.subtitle": (
        "Full output is written to the log directory; review or archive it anytime"
    ),
    # ---- 设置页 ----
    "settings.title": "Settings",
    "settings.subtitle": (
        "Language, theme, apt mirror and about"
    ),
    "settings.theme": "Theme",
    "settings.frontend": "Recommended frontend",
    "settings.version": "Version",
    "settings.author": "Author",
    "settings.supported_systems": "Supported systems",
    "settings.about": "About",
    # v1.0.28：three-section layout (hero icon + interface + apt mirror)
    "settings.tagline": "Built on RIME / plum / aria2",
    "settings.prefs": "Interface & appearance",
    "settings.mirror": "apt repository mirror",
    "settings.mirror_only_ubuntu": "Ubuntu only",
    "settings.current_source": "Current source",
    "settings.mirror_hint": (
        "Switching backs up the current sources file (as .rime-backup in the "
        "same directory) and runs apt update; Deepin's official repositories "
        "are already in China, so switching is usually unnecessary."
    ),
    "settings.mirror_note": (
        "This system is {os}, not Ubuntu: apt mirror switching is unavailable "
        "(Deepin's official repositories are already in China)."
    ),
    "settings.speedtest": "Speed test",
    "settings.speedtesting": "Testing…",
    "settings.switch_mirror": "Switch to selected mirror",
    "settings.switching": "Switching…",
    "settings.switch_tip": "Point the system apt sources at the selected mirror (requires admin password)",
    "settings.speedtest_tip": "Test download latency of all mirrors in parallel",
    # ---- Mirror list items ----
    "mirror.official": "official",
    "mirror.mirror": "mirror",
    "mirror.untested": "not tested",
    "mirror.current": "current",
    # ---- 主题 ----
    "theme.auto": "Follow system",
    "theme.light": "Light",
    "theme.dark": "Dark",
    # ComboBox 固定宽 200，英文再长就会被裁剪："(current: light)" 太长
    "theme.auto_current": "Follow system ({mode})",
    # ---- 页面按钮 / 选项 ----
    "engine.update_apt": "Update package index first (apt update)",
    "engine.purge": "Also delete configuration and dictionaries (irreversible)",
    "scheme.custom_install": "Install custom scheme",
    "scheme.check_update": "Check for updates",
    # ---- Skins (F15: color presets written to the user directory) ----
    "scheme.apply": "Apply skin",
    "scheme.reapply": "Apply again",
    "scheme.remove": "Remove skin",
    "scheme.applied": "✓ Applied",
    "scheme.apply_tip": (
        "Write this color scheme into default.custom.yaml and redeploy; "
        "takes effect immediately"
    ),
    "scheme.reapply_tip": (
        "Write this color scheme again and redeploy "
        "(switching skins replaces the previous one)"
    ),
    "scheme.remove_tip": (
        "Remove the color block written by this installer and restore the "
        "default RIME look; your other settings in default.custom.yaml stay untouched"
    ),
    "scheme.uninstall_skin_title": "Remove skin “{name}”?",
    "scheme.uninstall_skin_body": (
        "The color block written by this installer will be removed and RIME "
        "redeployed;\n"
        "the input method returns to the default RIME look;\n"
        "your other settings in default.custom.yaml and installed schemes stay "
        "untouched."
    ),
    # v1.0.12: explicit switch semantics + current-state badge + my default skin
    "scheme.switch_to": "Switch to this skin",
    "scheme.switch_to_tip": (
        "Write this color scheme into default.custom.yaml and redeploy; "
        "takes effect immediately and replaces the skin currently in use"
    ),
    "scheme.redeploy": "Redeploy",
    "scheme.redeploy_tip": (
        "Write this color scheme again and redeploy "
        "(currently in use; switching skins replaces it)"
    ),
    "scheme.current_skin": "Current skin",
    "scheme.my_default": "My default",
    "scheme.set_default": "Set as default",
    "scheme.set_default_tip": (
        "Mark as my default skin; restorable in one click from the skins section"
    ),
    "scheme.restore_default": "Restore my default",
    "scheme.restore_default_tip": (
        "Switch back to my default skin in one click (unavailable until set)"
    ),
    "scheme.current_label": "Current: {name}",
    "scheme.no_current": "Current: no installer skin in use",
    "scheme.installed_skins": "Installed:",
    "common.cancel_task": "Cancel task",
    # ---- 方案分类与筛选 ----
    "category.official": "Official",
    "category.third-party": "Community",
    "category.addon": "Dictionary pack",
    "category.skin": "Skin",
    "category.custom": "Custom",
    "filter.all": "All schemes",
    "filter.official": "Official only",
    "filter.third-party": "Community only",
    "filter.skin": "Skins only",
    "scheme.skin_scope": "Applies with an automatic redeploy. Note: on Linux the candidate window"
    " is drawn by the framework and RIME color schemes are not rendered — fcitx5 users should"
    " switch classicui themes on the Appearance page; ibus users can set layout options there."
    " Colors fully apply on Windows (weasel) / macOS (squirrel) only",
    "filter.addon": "Dictionary packs only",
    "filter.installed": "Installed only",
    "scheme.section_schemes": "Schemes",
    "scheme.section_skins": "Skins",
    # ---- Appearance (v1.0.13: fcitx5 themes + ibus global options, visible on Linux) ----
    "appearance.title": "Appearance",
    "appearance.subtitle": "On Linux the candidate window is drawn by the framework and RIME color schemes are not rendered; switch fcitx5 themes / set ibus options here for real, visible results",
    "appearance.frontend_row": "Current frontend",
    "appearance.theme_row": "fcitx5 current theme",
    "appearance.ibus_row": "ibus current appearance",
    "appearance.section_fcitx5": "Candidate window theme (fcitx5)",
    "appearance.section_ibus": "Candidate appearance (ibus-rime)",
    "appearance.theme_current": "Current",
    "appearance.theme_switch": "Switch",
    "appearance.theme_switch_tip": "Set Theme in classicui.conf to this theme; the candidate window changes instantly",
    "appearance.theme_revert": "Restore system default",
    "appearance.theme_revert_tip": "Restore classicui.conf as it was before this installer changed it (disabled if never changed)",
    "appearance.theme_source_system": "System",
    "appearance.theme_source_user": "User",
    "appearance.theme_source_installer": "Bundled",
    "appearance.theme_note": "Clicking a theme card (or its \"Switch\" button) rewrites classicui.conf and auto-restarts fcitx5, so the candidate window changes instantly. Bundled themes work out of the box; for your own theme, drop the folder into the user theme directory and click \"Refresh theme list\".",
    "appearance.theme_backup_exists": "Original backed up",
    "appearance.theme_no_backup": "Theme config not yet changed",
    "appearance.ibus_horizontal": "Horizontal candidates",
    "appearance.ibus_horizontal_tip": "Lay out candidates horizontally (read by ibus-rime at startup; log out to apply)",
    "appearance.ibus_inline_preedit": "Inline preedit",
    "appearance.ibus_inline_preedit_tip": "Embed the composing code at the cursor instead of inside the candidate window",
    "appearance.ibus_preedit_color": "Preedit highlight style",
    "appearance.ibus_preedit_color_tip": "ibus-rime ships only 4 preedit highlight presets; they do not affect candidate colors",
    "appearance.ibus_default": "Default",
    "appearance.ibus_apply": "Apply",
    "appearance.ibus_apply_tip": "Write the current options to ibus_rime.custom.yaml (applies after re-login)",
    "appearance.ibus_revert": "Restore defaults",
    "appearance.ibus_revert_tip": "Remove the ibus appearance block written by this installer",
    "appearance.ibus_note": "ibus-rime reads appearance config at startup only; log out or restart ibus after applying",
    "appearance.ibus_no_dir": "RIME config directory not found — set the frontend in Settings first",
    "appearance.ibus_unset": "Unset (ibus default)",
    "appearance.not_applicable": "The detected frontend does not use this mechanism",
    "msg.theme_switched": "Candidate window theme switched",
    "msg.theme_switched_hint": "classicui.conf has been updated and fcitx5 restarted; the candidate window changes instantly",
    "msg.theme_reverted": "System default theme restored",
    "msg.theme_reverted_hint": "classicui.conf has been restored to its pre-change state",
    # ---- Theme (v1.0.16: market removed; keep switch + open directory) ----
    "appearance.open_themes_dir": "Open theme directory",
    "appearance.open_themes_dir_tip": "Open the fcitx5 user theme directory in a file manager; drop a custom theme folder in, then come back to switch",
    "appearance.tone_dark": "Dark",
    "appearance.tone_light": "Light",
    # ---- Appearance theme gallery (v1.0.19: tabs + card grid + attribution) ----
    "appearance.tab_builtin": "Bundled",
    "appearance.tab_custom": "My themes",
    "appearance.tab_label": "{name} ({count})",
    "appearance.card_current": "In use",
    "appearance.custom_empty_title": "No custom themes yet",
    "appearance.custom_empty_hint": "Click \"Open theme directory\" below, drop the downloaded theme folder in (it must contain theme.conf), then click \"Refresh theme list\" and it will show up here.",
    "appearance.custom_hint": "Theme directory: ~/.local/share/fcitx5/themes (one folder per theme, containing theme.conf and its images)",
    "appearance.system_theme_note": "You are using the system theme {name}, which is not listed here to keep the list tidy; click \"Restore system default\" below to go back to it.",
    "appearance.thanks_title": "Thanks to the Mellow theme author",
    "appearance.thanks_body": "The bundled Mellow themes ({count}) are designed and maintained by sanweiya and open-sourced under BSD-2-Clause. Thanks for making the candidate window on Linux this beautiful.",
    "appearance.thanks_note": "This installer only repackages them, fills in the missing radio.svg / arrow.svg and fixes menu text colors of dark variants; the visual design is untouched.",
    "appearance.thanks_open": "View project",
    # ---- Appearance theme import from a link (v1.0.21) ----
    "appearance.import_placeholder": "Paste a GitHub theme repo link, e.g. https://github.com/thep0y/fcitx5-themes-candlelight",
    "appearance.import_button": "Import themes",
    "appearance.import_tip": "Download the repo and add every theme in it to “My themes”",
    "appearance.import_tip_invalid": "Enter a GitHub repo link or user/repo (optionally with @branch)",
    "appearance.import_note": "Imports all themes in the repo at once (a repo usually ships several); click a card to switch, use “Open themes folder” to clean up.",
    "msg.theme_imported": "Imported {count} theme(s)",
    "msg.theme_imported_hint": "Added to your user theme folder; click a card to switch",
    "msg.theme_import_empty": "This repo has no fcitx5 themes",
    "msg.theme_import_empty_hint": "No theme.conf was found in “{name}” (neither theme folders nor variant configs). Use “Schemes” for input methods.",
    "msg.theme_android": "This is a mobile theme, not a desktop one",
    "msg.theme_android_hint": "“{name}” contains fcitx5-android JSON colors (isDark / keyBackgroundColor), which only work in the Android app. Desktop themes must provide theme.conf.",
    "msg.theme_color_scheme": "This is a color scheme, not a desktop theme",
    "msg.theme_color_scheme_hint": "“{name}” contains fcitx5-android / fcitx5-macos color files ([LightMode], OverrideDefault), which desktop fcitx5 cannot apply. Desktop themes need theme.conf.",
    "msg.theme_source_only": "This repo has to be built first",
    "msg.theme_source_only_hint": "“{name}” only contains source templates (build.py / *.tera / config/*.conf.in) with no ready-made theme.conf. Download a built theme bundle from its Releases page, or run its build script.",
    "msg.go_import_theme": "Import themes in Appearance",
    "appearance.theme_delete_tip": "Remove this theme from the theme folder",
    "appearance.delete_title": "Delete theme “{name}”?",
    "appearance.delete_target": "Will delete: {path}",
    "appearance.delete_note": "The theme folder is removed from disk; you can import it again or copy it back later.",
    "appearance.delete_current_warn": "⚠ This is the theme in use: deleting it switches the candidate window back to the theme you used before (system default if that one is gone) and restarts fcitx5.",
    "msg.theme_deleted": "Theme deleted",
    "msg.theme_deleted_hint": "The theme folder has been removed from your user theme folder; other themes are unaffected",
    "msg.theme_deleted_hint_reset": "The theme folder has been removed and the candidate window is back on the theme you used before",
    "msg.ibus_style_applied": "ibus appearance applied",
    "msg.ibus_style_applied_hint": "Written to ibus_rime.custom.yaml; log out or restart ibus to apply",
    "msg.ibus_style_reverted": "ibus appearance restored",
    "msg.ibus_style_reverted_hint": "The ibus appearance block written by this installer has been removed",
    # ---- 行标签（多页共用） ----
    "row.rime_dir": "Configuration directory",
    "row.frontend": "Target frontend",
    "row.backup_dir": "Backup directory",
    "row.backup_count": "Backups",
    "row.latest_backup": "Latest backup",
    # ---- Row labels (welcome page, v1.0.4: raw keys used to leak as English) ----
    "row.os": "Operating system",
    "row.session": "Session protocol",
    "row.desktop": "Desktop environment",
    "row.framework": "Input method framework",
    "row.engine": "Engine status",
    "row.env": "Input method environment variables",
    # ---- 体检页 ----
    "doctor.overall": "Overall",
    "doctor.fix_tip": (
        "Run every fixable item automatically (write environment variables, "
        "start the framework, redeploy)"
    ),
    # v1.0.27：新增「Write login config」后工具栏变挤，英文两枚长按钮收短
    # （完整说明留给 tooltip）：Restart input method framework 270px → 170px，
    # Input method configuration 240px → 150px，页面最小宽度回到 948（改前 938）
    "doctor.restart": "Restart framework",
    "doctor.write_login": "Write login config",
    "doctor.write_login_tip": (
        "Write this frontend's environment variables into environment.d and "
        "~/.xsessionrc (takes effect after the next login; idempotent, safe to "
        "re-run anytime)"
    ),
    "doctor.write_login_tip_ok": (
        "Login config is already in place; click to write it again (use this "
        "to sync after switching frontend or editing the file by hand)"
    ),
    "doctor.redeploy_tip": (
        "Compile into a staging directory, then replace build/ and restart the "
        "framework only on success (Ubuntu needs librime-bin)"
    ),
    "doctor.config_tool": "IM configuration",
    "doctor.config_tool_tip": (
        "Open ibus-setup / fcitx5-configtool to add or manage input methods"
    ),
    "doctor.redetect": "Re-check",
    "doctor.guide": (
        "Deployment guide: if no candidates appear after installing a scheme, "
        "click “Redeploy” (compiles build/; on failure the existing config is "
        "kept and the reason is shown). To add or switch input methods in the "
        "framework, click “IM configuration”. Restarting the "
        "framework only restarts the process — it does not force a rebuild."
    ),
    "doctor.summary_bad": "{bad} issue(s) need attention, {warn} warning(s)",
    "doctor.summary_warn": "{warn} warning(s), nothing blocking",
    "doctor.summary_ok": "No issues found",
    "doctor.fixable_count": "({fixable} can be fixed automatically)",
    "doctor.fix_button_n": "Auto-fix {fixable} item(s)",
    "doctor.fix_button_none": "Nothing can be fixed automatically",
    # ---- 体检结果文案（v1.0.26 第二步）----
    # 中文文案由 core/doctor.py 的字面量直接提供（i18n.tr_or 的 fallback），
    # 这里只补英文；缺键时自动回退中文，不会出现裸 key。
    "doctor.check.im_env.title": "Input method environment",
    "doctor.check.frontend_running.title": "Framework process",
    "doctor.check.engine.title": "Input method engine",
    "doctor.check.schemes.title": "Input schemes",
    "doctor.check.deployed.title": "Deployment state",
    "doctor.check.input_source.title": "Input source",
    "doctor.check.autostart.title": "Default framework",
    "doctor.check.rime_dir.title": "Config directory",
    "doctor.check.im_env.idle_configured.advice": (
        "Config already in place ({path}); it takes effect after login"
    ),
    "doctor.check.im_env.idle.detail": (
        "No running input method framework detected, so this cannot be judged "
        "(ignore if that is expected)"
    ),
    "doctor.check.im_env.idle_configured.detail": (
        "No running input method framework detected, so this cannot be judged "
        "(ignore if that is expected)"
    ),
    "doctor.check.im_env.idle.advice": (
        "You can pre-write {path} and ~/.xsessionrc so the variables are "
        "correct on the next login (idempotent, safe to delete)"
    ),
    "doctor.check.im_env.ok.detail": "Correctly injected for {frontend}",
    "doctor.check.im_env.pending.detail": (
        "The current session still points at another input method: {conflicts}"
    ),
    "doctor.check.im_env.pending.advice": (
        "Config is written to {path} and ~/.xsessionrc; it takes effect after "
        "the next login (running processes cannot get new environment "
        "variables)"
    ),
    "doctor.check.im_env.stale.advice": (
        "The config currently targets another frontend ({stale} mismatch); "
        "auto-fix rewrites it for the current one and takes effect after login"
    ),
    "doctor.check.im_env.conflict.advice": (
        "Auto-fix writes {path} and ~/.xsessionrc, effective after the next "
        "login; unset variables are handled per session type and need no action"
    ),
    "doctor.check.frontend_running.ok.detail": "Running: {frontends}",
    "doctor.check.frontend_running.missing.detail": (
        "No ibus-daemon / fcitx5 / fcitx process found"
    ),
    "doctor.check.frontend_running.missing.advice": (
        "The engine is installed but the framework is not running; auto-fix "
        "can start it"
    ),
    "doctor.check.frontend_running.no_engine.detail": (
        "No ibus-daemon / fcitx5 / fcitx process found"
    ),
    "doctor.check.frontend_running.no_engine.advice": (
        "No engine installed yet — install one on the Engine page first; the "
        "framework starts together with it"
    ),
    "doctor.check.engine.ok.detail": "Installed: {packages}",
    "doctor.check.engine.missing.detail": (
        "None of ibus-rime / fcitx5-rime / fcitx-rime is installed"
    ),
    "doctor.check.engine.missing.advice": (
        "Install an engine on the Engine page"
    ),
    "doctor.check.schemes.ok.detail": "{count} scheme(s) installed by this app",
    "doctor.check.schemes.ok_files.detail": (
        "Scheme files already present in the config directory"
    ),
    "doctor.check.schemes.missing.detail": "No input scheme installed yet",
    "doctor.check.schemes.missing.advice": (
        "Install a scheme from the Scheme Market (e.g. the official preset or "
        "rime-ice)"
    ),
    "doctor.check.deployed.no_dir.advice": (
        "Install an engine first, or confirm the frontend on the Settings page"
    ),
    "doctor.check.deployed.absent.detail": (
        "{path} does not exist yet; deployment state unknown"
    ),
    "doctor.check.deployed.absent.advice": (
        "RIME creates this directory after an engine or scheme is installed"
    ),
    "doctor.check.deployed.unreadable.detail": (
        "{path} is not readable by the current user; deployment state unknown"
    ),
    "doctor.check.deployed.unreadable.advice": (
        "The directory may have been created by root (RIME run with sudo): "
        "run sudo chown -R \"$USER\" {path} and re-check"
    ),
    "doctor.check.deployed.ok.detail": "Deployed: {path}",
    "doctor.check.deployed.missing_build.detail": (
        "No build directory under {path}; deployment has not completed"
    ),
    "doctor.check.deployed.missing_build.advice": (
        "Auto-fix compiles build/ (on failure the existing config is kept and "
        "the reason is shown); if Ubuntu reports a missing rime_deployer, "
        "install librime-bin first"
    ),
    "doctor.check.input_source.ibus_non_gnome.detail": (
        "This desktop ({desktop}) does not use GNOME input sources; skipped "
        "(DDE/KDE manage IMEs themselves, gsettings has no effect)"
    ),
    "doctor.check.input_source.ibus_non_gnome.advice": (
        "If you cannot switch to RIME, add “Chinese (Rime)” in the desktop's "
        "own input method settings"
    ),
    "doctor.check.input_source.ibus_ok.detail": (
        "GNOME input sources already include Chinese (Rime)"
    ),
    "doctor.check.input_source.ibus_missing.detail": (
        "GNOME input sources do not include “Chinese (Rime)” — RIME cannot be "
        "selected without it (note: adding it via ibus-setup has no effect in "
        "a GNOME session)"
    ),
    "doctor.check.input_source.ibus_missing.advice": (
        "Auto-fix adds Chinese (Rime) to GNOME input sources and restarts ibus; "
        "you can also add it under Settings → Keyboard → Input Sources"
    ),
    "doctor.check.input_source.ibus_no_engine.detail": (
        "GNOME input sources do not include “Chinese (Rime)” — RIME cannot be "
        "selected without it (note: adding it via ibus-setup has no effect in "
        "a GNOME session)"
    ),
    "doctor.check.input_source.ibus_no_engine.advice": (
        "Install ibus-rime on the Engine page first, then add the input source"
    ),
    "doctor.check.input_source.fcitx5_no_conf.detail": (
        "No fcitx5 config found (~/.config/fcitx5/profile); it is created the "
        "first time you add an input method"
    ),
    "doctor.check.input_source.fcitx5_ok.detail": (
        "The fcitx5 input method list already includes Rime"
    ),
    "doctor.check.input_source.fcitx5_missing.detail": (
        "The fcitx5 input method list does not include “Rime” (current: "
        "{methods}) — RIME cannot be selected without it"
    ),
    "doctor.check.input_source.fcitx5_missing.advice": (
        "Click “Config tool” on this page → search and add “Rime”, then move it "
        "to the top (Deepin: Control Center → Keyboard and Language → Input "
        "Method)"
    ),
    "doctor.check.input_source.fcitx_legacy.detail": (
        "The legacy fcitx input method list must be confirmed in its GUI"
    ),
    "doctor.check.input_source.fcitx_legacy.advice": (
        "Click “Config tool” on this page → Input Method tab → add “Rime”"
    ),
    "doctor.check.autostart.session_managed.detail": (
        "The framework is started automatically by the desktop session; "
        "~/.xinputrc is not required"
    ),
    "doctor.check.autostart.no_config.detail": (
        "No ~/.xinputrc found and the framework is not running (most desktops "
        "detect it automatically)"
    ),
    "doctor.check.autostart.no_config.advice": (
        "If the framework does not start at login, run im-config to pick one "
        "(desktops without zenity/kdialog/whiptail cannot run im-config)"
    ),
    "doctor.check.autostart.disabled.detail": (
        "~/.xinputrc disables the input method framework (run_im none)"
    ),
    "doctor.check.autostart.disabled.advice": (
        "To use RIME, run im-config again and pick ibus / fcitx5"
    ),
    "doctor.check.autostart.ok.detail": "~/.xinputrc: {configured}",
    "doctor.check.autostart.mismatch.detail": (
        "~/.xinputrc points to a different framework than the current frontend "
        "({frontend})"
    ),
    "doctor.check.autostart.mismatch.advice": (
        "Run im-config again and pick the matching framework"
    ),
    "doctor.check.rime_dir.no_dir.detail": (
        "No RIME user directory found for any frontend"
    ),
    "doctor.check.rime_dir.unreadable.detail": (
        "{path} exists but is not readable by the current user"
    ),
    "doctor.check.rime_dir.unreadable.advice": (
        "It may have been created by root: sudo chown -R \"$USER\" {path}"
    ),
    "doctor.check.rime_dir.ok.detail": "{path} ({count} entries)",
    "doctor.check.rime_dir.absent.detail": (
        "{path} does not exist yet (created automatically after installing an "
        "engine or scheme)"
    ),
    "doctor.check.rime_dir.absent.advice": (
        "RIME creates this directory after a scheme is installed"
    ),
    # ---- 备份页 ----
    "backup.note_placeholder": "Note (optional), e.g. “before installing rime-ice”",
    "backup.default_note": "Manual backup",
    "backup.auto_note": "Automatic pre-restore backup",
    "backup.pre_backup": (
        "Back up the current state first (recommended, avoids irreversible "
        "overwrites)"
    ),
    "backup.clean": (
        "Restore cleanly (delete existing files in the configuration directory "
        "to match the backup exactly)"
    ),
    "backup.clean_tip": (
        "Unchecked means merge: files from the backup overwrite same-named "
        "files, everything else is kept"
    ),
    "backup.list_title": "Backups",
    "backup.restore": "Restore selected",
    "backup.export_to": "Export to…",
    "backup.do_import": "Import backup",
    "backup.open_dir": "Open backup folder",
    "backup.empty": "No backups yet; click “Back up now” to create the first snapshot",
    "backup.file_count": "{count} files",
    "backup.row_base": "{stamp} · {size} · {count} files",
    "backup.row_frontend": " · {frontend}",
    "backup.row_note": " · {note}",
    "backup.row_invalid": "{stamp} · {size} · unrecognized: {error}",
    "backup.row_invalid_no_error": "{stamp} · {size} · unrecognized",
    "backup.latest_value": "{stamp} ({size})",
    "backup.select_first": "Select a backup in the list first",
    "backup.invalid_tip": "This backup cannot be recognized and may be damaged",
    "backup.restore_tip": "Restore this backup into the current configuration directory",
    "backup.create_tip": "Pack the current configuration directory into a zip snapshot",
    "backup.rime_missing": "Not detected (choose an input method frontend in Settings)",
    "backup.rime_not_created": "{path} (not created yet)",
    "backup.restore_uncancellable": "Restore cannot be cancelled; please wait",
    "backup.unknown": "(unknown)",
    "backup.confirm_title": "Restore this backup?",
    "backup.confirm_time": "Backup time: {stamp}",
    "backup.confirm_source": "Backup source: {path}",
    "backup.confirm_files": "Files: {count}",
    "backup.confirm_packages": "Installed schemes: {packages}",
    "backup.confirm_target": "Restore to: {path}",
    "backup.confirm_clean": (
        "Existing files in the configuration directory will be deleted first, "
        "then the backup is restored (exactly as of that moment)."
    ),
    "backup.confirm_merge": (
        "Same-named files will be overwritten by the backup; files outside the "
        "backup are kept."
    ),
    "backup.confirm_target_missing": "The target directory does not exist yet and will be created.",
    "backup.confirm_target_differs": (
        "⚠ This backup comes from a different directory ({path}); its content "
        "will still be written into the target above."
    ),
    "backup.delete_title": "Delete this backup?",
    "backup.delete_body": (
        "{name}\nThis cannot be undone; consider exporting a copy first."
    ),
    "tip.busy": "An operation is running, please wait…",
    "dialog.export_title": "Export backup to",
    "dialog.zip_filter": "Backup files (*.zip)",
    "dialog.import_title": "Select a configuration backup",
    "dialog.all_files": "All files (*)",
    # v1.0.28: apt mirror switch confirmation
    "dialog.switch_mirror_title": "Switch the apt mirror to \u201c{name}\u201d?",
    "dialog.switch_mirror_body": (
        "The system package sources will point to: {url}\n"
        "The current sources file is backed up as .rime-backup in the same "
        "directory, then apt update runs.\n"
        "Administrator rights are required (pkexec)."
    ),
    "dialog.write_login_title": "Write login configuration (input method environment)?",
    "dialog.write_login_body": (
        "The input method environment variables for the current frontend "
        "({frontend}) will be written to two files:\n"
        "{vars}\n\n"
        "  {conf}\n"
        "  {xs} (only this installer's marked block is rewritten; your own "
        "content is left untouched)\n\n"
        "Both files live in your home directory, so no administrator password "
        "is required.\n"
        "· It takes effect after the next login; programs already running in "
        "this session are unaffected.\n"
        "· Re-running is idempotent: the values are rewritten for the current "
        "frontend.\n"
        "· To undo, remove this installer's block (or the whole file) to fall "
        "back to the system default."
    ),
    # ---- 任务反馈（InfoBar） ----
    "msg.backup_done": "Backup complete",
    "msg.backup_created": "Backup created",
    "msg.backup_summary": "{name} ({count} files, {size})",
    "msg.restore_done": "Configuration restored",
    "msg.restore_hint": (
        "Restored to the backup state; redeploy on the Engine page or restart "
        "the input method framework"
    ),
    "msg.backup_failed": "Backup failed",
    "msg.backup_failed_hint": (
        "Packing did not finish: the configuration directory may be missing or "
        "contain no files. See the Log page for details."
    ),
    "msg.restore_failed": "Restore failed",
    "msg.restore_failed_hint": (
        "The backup may be damaged or moved. The automatic pre-restore backup is "
        "still in Backups, so you can try again. See the Log page for details."
    ),
    "msg.open_dir_failed": "Cannot open the backup folder",
    "msg.open_dir_failed_hint": "Cannot create or access {path}: {error}",
    "msg.open_dir_unsupported": "No file manager available",
    "msg.open_dir_unsupported_hint": "The system has no file manager; open this path manually: {path}",
    "msg.backup_imported_body": "{name} ({count} files)",
    "msg.delete_failed_hint": "Only .zip files inside this installer's backup folder can be deleted",
    "msg.engine_installed": "Engine installed",
    "msg.engine_install_hint": (
        "{pkg} installed; head to Schemes to install an input scheme"
    ),
    "msg.op_done": "Done",
    "msg.restart_framework": "Restart framework",
    "msg.framework_hint": (
        "The framework has been handled; if no candidates appear, wait a moment "
        "for RIME to redeploy"
    ),
    "msg.fix_done": "Fix complete",
    "msg.fix_hint": (
        "Environment variables written to ~/.config/environment.d and "
        "~/.xsessionrc (effective after the next login); if the framework was "
        "started just now, give it a moment before typing"
    ),
    "msg.login_written": "Login configuration written",
    "msg.login_written_hint": (
        "Written to {conf} and ~/.xsessionrc; effective after the next login, "
        "programs already running in this session are unaffected"
    ),
    "msg.mirror_switched": "apt mirror switched",
    "msg.mirror_hint": (
        "Package index updated; later engine installs will use the new mirror"
    ),
    "msg.skin_applied": "“{name}” applied",
    "msg.skin_applied_hint": (
        "The color scheme has been written to your user directory and a "
        "redeploy was triggered; the input method appearance changes "
        "immediately. Applying another skin replaces the current one."
    ),
    "msg.skin_removed": "Skin removed",
    "msg.skin_removed_hint": (
        "The color block has been removed from default.custom.yaml and RIME "
        "redeployed; the input method returns to the default RIME look and your "
        "other settings are untouched."
    ),
    "msg.default_skin_set": "“{name}” set as default skin",
    "msg.default_skin_set_hint": (
        "Restore it anytime with “Restore my default” in the skins section"
    ),
    "msg.default_skin_restored": "Restoring default skin",
    "msg.default_skin_restore_hint": (
        "“{name}” is being written and redeployed; it takes effect shortly"
    ),
    "msg.no_default_skin": "No default skin set yet",
    "msg.no_default_skin_hint": (
        "Pick “Set as default” on a skin card to enable one-click restore"
    ),
    "msg.already_default_skin": "Already on the default skin",
    "msg.already_default_skin_hint": "“{name}” is your default skin; nothing to restore",
    "msg.default_skin_cleared": "Default skin preference cleared",
    "msg.default_skin_cleared_hint": "The removed skin was your default; you can set a new one",
    "msg.engine_uninstalled": "Engine uninstalled",
    "msg.engine_uninstalled_hint": "{pkg} uninstalled",
    "msg.purge_note": "Configuration directories removed: {paths}",
    "msg.purge_suffix": ", configuration directories removed: {paths}",
    "msg.cancelled": "Cancelled",
    "msg.cancel_hint": "Task cancelled by user; you can start over",
    "msg.failed": "Operation failed",
    "msg.fail_hint": "Exit code {code}; see the Logs page for full output",
    "msg.deploy_failed": "Redeploy did not complete",
    "msg.deploy_failed_hint": (
        "Your existing configuration was left untouched and is still usable. "
        "If rime_deployer was not found, install librime-bin on Ubuntu "
        "(sudo apt install librime-bin) and retry; switching to Rime once in "
        "the input method also triggers a deploy"
    ),
    # ---- 提示类（InfoBar） ----
    "msg.no_rime_dir": "Cannot locate configuration directory",
    "msg.no_rime_dir_hint": (
        "Could not detect the RIME user directory for the current frontend; "
        "install the engine or pick a frontend in Settings"
    ),
    "msg.no_rime_dir_short": (
        "Could not detect the RIME user directory; install the engine first"
    ),
    "msg.parse_failed": "Unrecognized format",
    "msg.parse_hint": (
        "Use user/repo, user/repo@branch or user/repo@branch:recipe"
    ),
    # v1.0.20: the repo is not a RIME scheme package (theme/image/plain project)
    "msg.not_rime_package": "This address is not a RIME scheme package",
    "msg.not_rime_package_hint": (
        "No RIME configuration (*.yaml / *.txt / *.gram) was found in “{name}”. "
        "Installation stopped and your RIME config is untouched. For skins or "
        "themes, drop the theme folder into your user theme directory on the "
        "Appearance page and click “Refresh theme directory”; for a scheme, "
        "make sure the repo root contains files such as default.yaml."
    ),
    "msg.no_change": "No file changes detected",
    "msg.no_change_hint": (
        "The install command for “{name}” ran, but the configuration "
        "directory is unchanged; check the log output"
    ),
    "msg.no_baseline": "No baseline to compare",
    "msg.no_baseline_hint": (
        "“{name}” was not recorded by this installer; reinstall it to "
        "create a baseline"
    ),
    "msg.update_found": "Update available",
    "msg.update_found_hint": (
        "Upstream content of “{name}” changed; click reinstall to upgrade"
    ),
    "msg.up_to_date": "Up to date",
    "msg.up_to_date_hint": "“{name}” matches upstream content",
    "msg.nothing_fixable": "Nothing to fix automatically",
    "msg.nothing_fixable_hint": (
        "The selected items need manual action; see the advice next to each one"
    ),
    "msg.mirror_failed": "Cannot switch mirror",
    "msg.import_failed": "Import failed",
    "msg.backup_imported": "Backup imported",
    "msg.export_failed": "Export failed",
    "msg.backup_exported": "Backup exported",
    "msg.delete_failed": "Delete failed",
    "msg.backup_deleted": "Backup deleted",
    # ---- 语言 ----
    "lang.zh_CN": "简体中文",
    "lang.en_US": "English",
}
