"""冒烟测试：offscreen 下验证资源、检测、状态机、runner、日志、引擎/方案装卸、快照备份、环境体检与主窗口。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py
退出码 0 = 全部通过；任一断言失败即 0。

注：方案安装的「aria2 真实下载」不在此覆盖（依赖网络），离线链路由本地
tarball 走同一条 解压预置 → rime-install → manifest 记账 → 卸载还原 流程验证。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config  # noqa: E402
from core import doctor, i18n, theme  # noqa: E402
from core.apt_mirror import (  # noqa: E402
    Mirror,
    build_switch_steps,
    current_mirror_name,
    is_ubuntu,
    measure_mirror_latency,
    mirrors,
    speedtest_urls,
    system_codename,
)
from core.deploy_guide import (  # noqa: E402
    build_open_config_tool_steps,
    build_redeploy_steps,
    build_restart_steps,
)
from core.detector import EnvReport, _recommend_frontend, detect  # noqa: E402
from core.doctor import (  # noqa: E402
    _fcitx5_input_methods,
    build_fix_all_steps,
    build_fix_steps,
    run_checks,
)
from core.engine_service import (  # noqa: E402
    build_install_steps,
    build_uninstall_steps,
    engine_package,
    purge_user_data,
    user_data_dirs,
)
from core.log_manager import LogManager  # noqa: E402
from core.manifest import Manifest, tree_hash  # noqa: E402
from core.runner import PERCENT_RE, Step, TaskRunner, shell_wrap  # noqa: E402
from core.scheme_service import (  # noqa: E402
    build_check_steps,
    build_clean_step,
    build_extract_step,
    build_install_step,
    build_install_steps as build_scheme_install_steps,
    build_verify_step,
    clear_package_cache,
    package_tree_hash,
    tarball_urls,
)
from core.schemes import (  # noqa: E402
    SCHEME_MAP,
    SCHEMES,
    PackageRef,
    custom_scheme,
    parse_spec,
)
from core.snapshot_service import (  # noqa: E402
    META_NAME,
    STAGING_PREFIX,
    EmptyBackupError,
    create_backup,
    delete_backup,
    export_backup,
    human_size,
    import_backup,
    list_backups,
    new_backup_path,
    read_backup_info,
    restore_backup,
)
from core.state_machine import InstallerState, StateMachine  # noqa: E402

NAV_PAGES_EXPECTED = 8
# 冒烟临时目录放在项目内：home 区（~/.tmp）受宿主 safe-delete 钩子监护，
# 冒烟结束的成批 rmtree 会触发拦截甚至被杀进程（2026-10-13 实测）
SMOKE_TMP = Path(__file__).resolve().parent / ".smoke-tmp"

#: 原始 rmtree 引用：tools/rerun_flow_checks.py 的「零删除」模式会把它置空，
#: 需要「文件确实被删掉」的断言必须先判断当前是否处于该模式（v1.0.21）
_PRISTINE_RMTREE = shutil.rmtree

# QApplication 必须在窗口存活期间保持引用（局部变量会被 GC 导致段错误）
_APP_HOLDER: list = []


def _tmp_dir(prefix: str) -> Path:
    SMOKE_TMP.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(SMOKE_TMP)))


def check_resources() -> list[str]:
    errors: list[str] = []
    binary = config.aria2_binary()
    if binary is None or not binary.exists():
        errors.append(f"aria2 二进制缺失: {binary}")
    if not config.ARIA2_CONF.exists():
        errors.append(f"aria2.conf 缺失: {config.ARIA2_CONF}")
    if not config.PLUM_RIME_INSTALL.exists():
        errors.append(f"plum/rime-install 缺失: {config.PLUM_RIME_INSTALL}")
    for script in ("install-packages.sh", "frontend.sh", "fetch-package.sh"):
        if not (config.PLUM_DIR / "scripts" / script).exists():
            errors.append(f"plum/scripts/{script} 缺失")
    for vendor in ("qfluentwidgets", "qframelesswindow", "darkdetect"):
        if not (config.RESOURCES_DIR / "vendor" / vendor).is_dir():
            errors.append(f"resources/vendor/{vendor} 缺失")
    if binary and binary.exists():
        out = subprocess.run(
            ["file", "-b", str(binary)], capture_output=True, text=True
        ).stdout
        if "ELF" not in out:
            errors.append(f"aria2 二进制不是 ELF: {out.strip()}")
    errors += check_icon_set()
    return errors


#: hicolor 需要哪些档位（build-deb.sh 的安装清单必须与此一致）
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def check_icon_set() -> list[str]:
    """v1.0.25 应用图标自检：八档齐全 / 尺寸与档位一致 / 有 alpha 通道。

    图标是纯静态资源，坏了不会有任何报错——桌面只是「悄悄」显示成空白方块
    或默认齿轮图标，跑起来一切正常。所以在这里把它钉死：读 PNG 头（IHDR
    前 26 字节）即可拿到尺寸与颜色类型，不引入 Pillow。
    """
    errors: list[str] = []
    icon_dir = config.PROJECT_ROOT / "packaging" / "icons"
    for size in ICON_SIZES:
        path = icon_dir / f"rime-installer-{size}.png"
        try:
            header = path.read_bytes()[:26]
        except OSError:
            errors.append(f"应用图标缺失: {path.name}")
            continue
        if header[:8] != b"\x89PNG\r\n\x1a\n":
            errors.append(f"应用图标不是 PNG: {path.name}")
            continue
        width, height = struct.unpack(">II", header[16:24])
        color_type = header[25]
        if (width, height) != (size, size):
            errors.append(f"应用图标尺寸不符: {path.name} 实为 {width}x{height}")
        # 16/24 是任务栏极简档，圆角在那一档会被栅格化成一两个像素，
        # 强制 alpha 没有意义；≥32 必须是 RGBA（圆角外真透明，不是白底）
        if size >= 32 and color_type != 6:
            errors.append(f"应用图标缺少透明通道: {path.name} color_type={color_type}")
    if not config.APP_ICON.exists():
        errors.append(f"窗口图标缺失: {config.APP_ICON}")
    elif config.APP_ICON.name != "rime-installer-64.png":
        errors.append(f"窗口图标应取 64 档: {config.APP_ICON.name}")
    # v1.0.28：设置页 Hero 高清图标。512 档同时是 hicolor 的最高档，两份用途
    # 共用一个文件；缺失时设置页只是「少一块图」，没有任何报错，故在此断言。
    if not config.APP_LOGO.exists():
        errors.append(f"设置页 Hero 图标缺失: {config.APP_LOGO}")
    elif config.APP_LOGO.name != "rime-installer-512.png":
        errors.append(f"Hero 图标应取 512 档: {config.APP_LOGO.name}")
    desktop = config.PROJECT_ROOT / "packaging" / "rime-installer.desktop"
    try:
        if "Icon=rime-installer" not in desktop.read_text(encoding="utf-8"):
            errors.append("desktop 文件的 Icon= 与图标文件名不一致")
    except OSError:
        errors.append(f"desktop 文件缺失: {desktop}")
    # 装了哪几档必须与 ICON_SIZES 一致：打包脚本漏一档不会报错，只是那一档
    # 由桌面环境拿相邻尺寸硬缩（典型症状是任务栏图标发糊）
    build = config.PROJECT_ROOT / "packaging" / "build-deb.sh"
    try:
        text = build.read_text(encoding="utf-8")
    except OSError:
        errors.append(f"打包脚本缺失: {build}")
    else:
        line = next(
            (ln for ln in text.splitlines() if ln.strip().startswith("for size in")),
            "",
        )
        packed = tuple(int(n) for n in re.findall(r"\d+", line))
        if packed != ICON_SIZES:
            errors.append(f"build-deb.sh 安装的图标档位与 ICON_SIZES 不一致: {packed}")
        # 整目录排除 packaging/ 会让装机后的设置页 Hero 图标路径不存在
        # （v1.0.27 就是这么坏的：界面只是少一块图，没有任何报错）
        if "--exclude='./packaging'" in text:
            errors.append(
                "build-deb.sh 整体排除了 packaging/：装机后设置页 Hero 图标会缺失"
            )
    return errors


def check_platform() -> list[str]:
    """F9 平台回退 + v1.0.2 GL 渲染决策。

    覆盖：尊重用户显式设置 / 逃生阀 / Wayland→xcb 自动回退（并关 GL 集成）/
    无 XWayland 强制软渲染 / 硬件 GL 逃生阀（RIME_INSTALLER_HW_GL）/
    决策说明 / offscreen 尊重。
    """
    errors: list[str] = []
    from core import platform as core_platform

    saved = {
        core_platform.QT_ENV: os.environ.get(core_platform.QT_ENV),
        core_platform.FORCE_ENV: os.environ.get(core_platform.FORCE_ENV),
        core_platform.HW_GL_ENV: os.environ.get(core_platform.HW_GL_ENV),
        core_platform.QT_XCB_GL_INTEGRATION: os.environ.get(
            core_platform.QT_XCB_GL_INTEGRATION
        ),
        core_platform.LIBGL_SOFTWARE: os.environ.get(core_platform.LIBGL_SOFTWARE),
        "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE"),
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
        "DISPLAY": os.environ.get("DISPLAY"),
    }
    gl_keys = (core_platform.QT_XCB_GL_INTEGRATION, core_platform.LIBGL_SOFTWARE)
    original_probe = core_platform._probe_xwayland
    try:
        # 0) 清空本轮全部相关变量，保证各场景从干净环境出发
        for key in saved:
            os.environ.pop(key, None)

        # 1) 无任何设置：不干预，交给 Qt 默认选择，也不设软渲染/GL 限制
        if core_platform.select_qpa_platform() != "":
            errors.append("默认情况下不应干预平台选择")
        if core_platform.QT_ENV in os.environ:
            errors.append("默认情况下不应写入 QT_QPA_PLATFORM")
        for gl_key in gl_keys:
            if gl_key in os.environ:
                errors.append(f"默认情况下不应设置 {gl_key}")

        # 2) 用户显式设置 QT_QPA_PLATFORM：尊重（offscreen 冒烟依赖此路径）
        os.environ[core_platform.QT_ENV] = "offscreen"
        if core_platform.select_qpa_platform() != "offscreen":
            errors.append("用户显式 QT_QPA_PLATFORM 未被尊重")

        # 3) 逃生阀：RIME_INSTALLER_FORCE_PLATFORM 可强制覆盖
        os.environ.pop(core_platform.QT_ENV, None)
        os.environ[core_platform.FORCE_ENV] = "xcb"
        if core_platform.select_qpa_platform() != "xcb":
            errors.append("RIME_INSTALLER_FORCE_PLATFORM=xcb 未生效")
        if os.environ.get(core_platform.QT_ENV) != "xcb":
            errors.append("逃生阀未写入 QT_QPA_PLATFORM")
        os.environ.pop(core_platform.FORCE_ENV, None)
        os.environ.pop(core_platform.QT_ENV, None)

        # 4) Wayland 会话 + XWayland 可用 → 强制 xcb 并关闭 GL 集成（F9 主路径）
        os.environ["XDG_SESSION_TYPE"] = "wayland"
        os.environ["DISPLAY"] = ":0"
        core_platform._probe_xwayland = lambda: True
        for gl_key in gl_keys:
            os.environ.pop(gl_key, None)
        if core_platform.select_qpa_platform() != "xcb":
            errors.append("Wayland 会话 + XWayland 可用时应回退 xcb")
        if os.environ.get(core_platform.QT_ENV) != "xcb":
            errors.append("Wayland→xcb 回退未写入 QT_QPA_PLATFORM")
        if os.environ.get(core_platform.QT_XCB_GL_INTEGRATION) != "none":
            errors.append("Wayland→xcb 回退应关闭 GL 集成（QT_XCB_GL_INTEGRATION=none）")
        note = core_platform.platform_decision_note()
        if "GL" not in note:
            errors.append(f"xcb 决策说明未体现 GL 集成关闭: {note}")

        # 5) Wayland 会话但无 XWayland → 不干预平台，但强制软件渲染
        os.environ.pop(core_platform.QT_ENV, None)
        os.environ.pop(core_platform.QT_XCB_GL_INTEGRATION, None)
        core_platform._probe_xwayland = lambda: False
        if core_platform.select_qpa_platform() != "":
            errors.append("无 XWayland 的 Wayland 会话不应强制 xcb")
        if os.environ.get(core_platform.LIBGL_SOFTWARE) != "1":
            errors.append("无 XWayland 的 Wayland 会话应强制软件渲染（LIBGL_ALWAYS_SOFTWARE=1）")
        note = core_platform.platform_decision_note()
        if "软件渲染" not in note:
            errors.append(f"无 XWayland 决策说明未体现软渲染: {note}")

        # 6) 硬件 GL 逃生阀：RIME_INSTALLER_HW_GL=1 时不施加任何 GL 限制
        os.environ[core_platform.HW_GL_ENV] = "1"
        core_platform._probe_xwayland = lambda: True
        for gl_key in gl_keys:
            os.environ.pop(gl_key, None)
        if core_platform.select_qpa_platform() != "xcb":
            errors.append("硬件 GL 逃生阀不应影响平台回退决策")
        for gl_key in gl_keys:
            if gl_key in os.environ:
                errors.append(f"RIME_INSTALLER_HW_GL=1 时不应设置 {gl_key}")
        os.environ.pop(core_platform.QT_ENV, None)
        core_platform._probe_xwayland = lambda: False
        if core_platform.select_qpa_platform() != "":
            errors.append("硬件 GL 逃生阀 + 无 XWayland 时不应干预平台")
        if core_platform.LIBGL_SOFTWARE in os.environ:
            errors.append("RIME_INSTALLER_HW_GL=1 时不应强制软件渲染")
        os.environ.pop(core_platform.HW_GL_ENV, None)

        # 7) 决策说明可读（写入启动日志用）
        if not core_platform.platform_decision_note():
            errors.append("platform_decision_note 为空")
    finally:
        core_platform._probe_xwayland = original_probe
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return errors


def check_launch_marker() -> list[str]:
    """v1.0.2 启动标记：write_launch_marker 写 ISO 时间戳（launcher 判定用）。"""
    errors: list[str] = []
    import main as app_main

    marker = config.LAUNCH_OK_MARKER
    original = marker.read_text(encoding="utf-8") if marker.is_file() else None
    try:
        marker.unlink(missing_ok=True)
        app_main.write_launch_marker()
        if not marker.is_file():
            errors.append("write_launch_marker 未生成标记文件")
        else:
            content = marker.read_text(encoding="utf-8").strip()
            try:
                datetime.fromisoformat(content)
            except ValueError:
                errors.append(f"launch-ok 内容不是 ISO 时间: {content!r}")
    finally:
        # 还原现场：原本没有标记就删除，有就恢复原内容
        if original is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(original, encoding="utf-8")
    return errors


def check_launcher_script() -> list[str]:
    """launcher 三级重试链回归防线：关键决策逻辑不能丢。"""
    errors: list[str] = []
    script = (
        Path(__file__).resolve().parent / "packaging" / "build-deb.sh"
    ).read_text(encoding="utf-8")
    for token in (
        "QT_QPA_PLATFORM=xcb",
        "QT_QPA_PLATFORM=wayland",
        "LIBGL_ALWAYS_SOFTWARE=1",
        "launch-ok",
        "launcher.log",
    ):
        if token not in script:
            errors.append(f"build-deb.sh 缺少关键逻辑: {token}")
    return errors


def check_deb_depends() -> list[str]:
    """v1.0.3 防回归：deb Depends 必须含 python3-pyqt6 与 qtsvg 分包。

    Ubuntu 把 PyQt6 的 SVG 绑定放在独立包 python3-pyqt6.qtsvg，缺失时
    qfluentwidgets import 期秒崩（真机根因）。build/ 下无 deb 时跳过。
    """
    errors: list[str] = []
    debs = sorted(
        (Path(__file__).resolve().parent / "build").glob("rime-installer_*_amd64.deb")
    )
    if not debs:
        print("[check_deb_depends] 跳过：build/ 下无 deb（重建 deb 后本项生效）")
        return errors
    deb = debs[-1]
    result = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Depends"], capture_output=True, text=True
    )
    if result.returncode != 0:
        errors.append(f"dpkg-deb -f Depends 失败: {result.stderr.strip()}")
        return errors
    depends = result.stdout.strip()
    for package in ("python3-pyqt6", "python3-pyqt6.qtsvg"):
        if package not in depends:
            errors.append(f"deb Depends 缺少 {package}: {depends}")
    return errors


def check_detector() -> list[str]:
    errors: list[str] = []
    report = detect()
    if not report.os_name:
        errors.append("os_name 为空")
    if report.session_type not in ("x11", "wayland", "unknown"):
        errors.append(f"session_type 异常: {report.session_type}")
    if report.recommended_frontend not in config.RIME_USER_DIRS:
        errors.append(f"recommended_frontend 异常: {report.recommended_frontend}")
    if set(report.engine_packages) != set(config.ENGINE_PACKAGES.values()):
        errors.append(f"engine_packages 键异常: {report.engine_packages}")
    if report.rime_user_dir is None:
        errors.append("rime_user_dir 为空")

    # v1.0.5：环境变量只报「已设定且冲突」，未设定不再误报
    # （真机反馈：Ubuntu GNOME Wayland + ibus 标准配置被误报
    #  「异常：GTK_IM_MODULE、SDL_IM_MODULE」——这两项在 Wayland 下本就不该设）
    wayland_ok = EnvReport(
        active_frontend="ibus-rime",
        recommended_frontend="ibus-rime",
        im_env_vars={
            "GTK_IM_MODULE": "",
            "QT_IM_MODULE": "ibus",
            "XMODIFIERS": "@im=ibus",
            "SDL_IM_MODULE": "",
        },
    )
    if wayland_ok.env_vars_ok is not True or wayland_ok.env_var_mismatches:
        errors.append(
            f"Wayland 标准配置（GTK/SDL 未设定）不应判为异常: {wayland_ok.env_var_mismatches}"
        )
    conflict = EnvReport(
        active_frontend="ibus-rime",
        recommended_frontend="ibus-rime",
        im_env_vars={
            "GTK_IM_MODULE": "fcitx",
            "QT_IM_MODULE": "ibus",
            "XMODIFIERS": "@im=ibus",
            "SDL_IM_MODULE": "ibus",
        },
    )
    if conflict.env_vars_ok is not False:
        errors.append("GTK_IM_MODULE=fcitx 与 ibus 冲突应判为异常")
    if "GTK_IM_MODULE" not in conflict.env_var_mismatches:
        errors.append(f"冲突键列表应含 GTK_IM_MODULE: {conflict.env_var_mismatches}")
    none_case = EnvReport(recommended_frontend="ibus-rime")
    if none_case.env_vars_ok is not None:
        errors.append("无框架运行时 env_vars_ok 应为 None")
    doctor_env = {r.key: r for r in run_checks(wayland_ok)}
    if doctor_env["im_env"].level != "ok":
        errors.append(f"体检项 im_env 对 Wayland 标准配置应通过: {doctor_env['im_env'].detail}")

    # v1.0.11：Deepin 25（及同系 UOS V25）的 XDG_CURRENT_DESKTOP 实测为 "DDE"，
    # 桌面匹配必须命中 fcitx5-rime（无 active_frontend 旁路的边缘场景防误推 ibus）
    for desktop, expect in [
        ("DDE", "fcitx5-rime"),
        ("Deepin", "fcitx5-rime"),
        ("KDE", "fcitx5-rime"),
        ("ubuntu:GNOME", "ibus-rime"),
        ("unknown", "ibus-rime"),
    ]:
        got = _recommend_frontend(EnvReport(active_frontend=None, desktop=desktop))
        if got != expect:
            errors.append(f"_recommend_frontend(desktop={desktop!r}) 应为 {expect}: {got}")
    return errors


_SMOKE_RIME_DIR: Path | None = None


def _smoke_rime_dir() -> Path:
    """冒烟用的「已存在」配置目录。

    v1.0.23 起 `StateMachine.rule("backup")` 要求目录真实存在（否则会打出
    0 个文件的假备份），因此固定路径 `/tmp/rime` 不再够用。
    """
    global _SMOKE_RIME_DIR
    if _SMOKE_RIME_DIR is None:
        _SMOKE_RIME_DIR = Path(tempfile.mkdtemp(prefix="smoke-rime-"))
        (_SMOKE_RIME_DIR / "default.custom.yaml").write_text("patch:\n", encoding="utf-8")
    return _SMOKE_RIME_DIR


def _report_no_engine() -> EnvReport:
    return EnvReport(
        os_name="Ubuntu",
        os_version="26.04",
        session_type="wayland",
        desktop="ubuntu:GNOME",
        engine_packages={"ibus-rime": False, "fcitx5-rime": False, "fcitx-rime": False},
        recommended_frontend="ibus-rime",
        rime_user_dir=_smoke_rime_dir(),
    )


def _report_with_engine() -> EnvReport:
    report = _report_no_engine()
    report.engine_packages["ibus-rime"] = True
    return report


def _report_no_rime_dir() -> EnvReport:
    """未能识别配置目录（备份/恢复必须禁用并给出原因）。"""
    return EnvReport(
        os_name="Deepin",
        os_version="25.2",
        session_type="x11",
        desktop="Deepin",
        engine_packages={"ibus-rime": True, "fcitx5-rime": False, "fcitx-rime": False},
        recommended_frontend="fcitx5-rime",
        rime_user_dir=None,
    )


def check_state_machine() -> list[str]:
    errors: list[str] = []

    sm = StateMachine(_report_no_engine())
    if sm.state is not InstallerState.NO_ENGINE:
        errors.append("无引擎时应为 NO_ENGINE")
    if sm.rule("install_engine").enabled is not True:
        errors.append("无引擎时 install_engine 应可用")
    if sm.rule("install_scheme").enabled is not False:
        errors.append("无引擎时 install_scheme 应禁用")
    if "引擎" not in sm.rule("install_scheme").reason:
        errors.append("install_scheme 禁用原因应提示先装引擎")
    if sm.rule("uninstall_engine").enabled is not False:
        errors.append("无引擎时 uninstall_engine 应禁用")

    sm = StateMachine(_report_with_engine())
    if sm.state is not InstallerState.ENGINE_READY:
        errors.append("有引擎无方案时应为 ENGINE_READY")
    if sm.rule("install_scheme").enabled is not True:
        errors.append("有引擎时 install_scheme 应可用")
    if sm.rule("uninstall_scheme").enabled is not False:
        errors.append("无方案时 uninstall_scheme 应禁用")
    if sm.rule("install_engine").enabled is not False:
        errors.append("有引擎时 install_engine 应禁用")

    sm = StateMachine(_report_with_engine(), installed_schemes=2)
    if sm.state is not InstallerState.SCHEMES_INSTALLED:
        errors.append("有方案未部署时应为 SCHEMES_INSTALLED")
    if sm.rule("uninstall_scheme").enabled is not True:
        errors.append("有方案时 uninstall_scheme 应可用")

    # 备份/恢复只依赖配置目录，不依赖引擎是否已装
    if sm.rule("backup").enabled is not True:
        errors.append("有配置目录时 backup 应可用")
    if StateMachine(_report_no_rime_dir()).rule("backup").enabled is not False:
        errors.append("无配置目录时 backup 应禁用")
    if "配置目录" not in StateMachine(_report_no_rime_dir()).rule("backup").reason:
        errors.append("backup 禁用原因应提示配置目录")
    if StateMachine(_report_no_rime_dir()).rule("restore_backup").enabled is not False:
        errors.append("无配置目录时 restore_backup 应禁用")

    # Phase 5：体检修复不依赖引擎；部署引导依赖引擎；镜像切换仅 Ubuntu
    if StateMachine(_report_no_engine()).rule("doctor_fix").enabled is not True:
        errors.append("无引擎时 doctor_fix 仍应可用（写环境变量不依赖引擎）")
    for action in ("restart_frontend", "redeploy", "open_config_tool"):
        if StateMachine(_report_no_engine()).rule(action).enabled is not False:
            errors.append(f"无引擎时 {action} 应禁用")
        if StateMachine(_report_with_engine()).rule(action).enabled is not True:
            errors.append(f"有引擎时 {action} 应可用")
    if StateMachine(_report_with_engine()).rule("switch_mirror").enabled is not True:
        errors.append("Ubuntu 上 switch_mirror 应可用")
    deepin_rule = StateMachine(_report_no_rime_dir()).rule("switch_mirror")
    if deepin_rule.enabled is not False or "Ubuntu" not in deepin_rule.reason:
        errors.append(f"Deepin 上 switch_mirror 应禁用并说明: {deepin_rule}")

    busy = StateMachine(_report_with_engine(), busy=True, installed_schemes=2)
    for action in (
        "install_engine",
        "uninstall_engine",
        "install_scheme",
        "uninstall_scheme",
        "backup",
        "restore_backup",
        "doctor_fix",
        "restart_frontend",
        "redeploy",
        "open_config_tool",
        "switch_mirror",
    ):
        if busy.rule(action).enabled is not False:
            errors.append(f"busy 时 {action} 应禁用")
    return errors


def check_log_manager() -> list[str]:
    errors: list[str] = []
    base = _tmp_dir("rime-log-")
    try:
        manager = LogManager(base)
        path = manager.open_session("engine-install/ibus-rime")
        if path is None or not path.exists():
            errors.append("会话日志文件未创建")
            return errors
        manager.write("第一行日志")
        manager.close_session()
        text = manager.read_session(path)
        if "第一行日志" not in text:
            errors.append("日志内容未写入")
        if path not in manager.sessions():
            errors.append("sessions() 未包含当前会话")
        if manager.latest_session() != path:
            errors.append("latest_session() 异常")

        dirty = manager.open_session("a/b c*d?e")
        if dirty is None or "/" in dirty.name or "*" in dirty.name:
            errors.append(f"会话名未净化: {dirty}")
        manager.close_session()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_engine_service() -> list[str]:
    errors: list[str] = []
    if engine_package("fcitx5-rime") != "fcitx5-rime":
        errors.append("fcitx5-rime 包名映射错误")
    if engine_package("未知前端") != "ibus-rime":
        errors.append("未知前端应回退 ibus-rime")

    # v1.0.5：update+install 合并为单步单次 pkexec（真机反馈「好几次输入密码」）
    steps = build_install_steps("fcitx5-rime", update_first=True)
    if len(steps) != 1:
        errors.append(f"安装应合并为单步（单次密码框）: {len(steps)}")
    else:
        joined = " ".join(steps[0].argv)
        if "install" not in joined or "fcitx5-rime" not in joined:
            errors.append(f"安装命令异常: {joined}")
        if "update" not in joined:
            errors.append("安装步骤缺少 apt update")
        if joined.count("pkexec") != 1:
            errors.append(f"安装应只有一次 pkexec 提权: {joined}")
        if shutil.which("nice") and "nice" not in joined:
            errors.append(f"apt 应经 nice 降优先级: {joined}")

    steps = build_install_steps("ibus-rime", update_first=False)
    if len(steps) != 1:
        errors.append(f"跳过 update 时应只有 1 步: {len(steps)}")
    else:
        joined = " ".join(steps[0].argv)
        if "install" not in joined or "update" in joined:
            errors.append(f"跳过 update 时不应包含 update: {joined}")
        if joined.count("pkexec") != 1:
            errors.append(f"安装应只有一次 pkexec 提权: {joined}")

    # v1.0.5：purge+autoremove 同样合并为单步单次提权
    uninstall = build_uninstall_steps("ibus-rime")
    if len(uninstall) != 1:
        errors.append(f"卸载应合并为单步（单次密码框）: {len(uninstall)}")
    else:
        joined = " ".join(uninstall[0].argv)
        if "purge" not in joined or "autoremove" not in joined:
            errors.append(f"卸载命令异常: {joined}")
        if joined.count("pkexec") != 1:
            errors.append(f"卸载应只有一次 pkexec 提权: {joined}")

    wrapped = shell_wrap(["echo", "hello world"])
    if wrapped[-1] != "exec echo 'hello world'":
        errors.append(f"shell_wrap 结果异常: {wrapped}")

    if user_data_dirs("fcitx5-rime", Path("/home/tester")) != [
        Path("/home/tester/.local/share/fcitx5/rime")
    ]:
        errors.append("fcitx5-rime 用户目录映射异常")

    # purge 安全护栏：只删 home 下已存在的目录
    fake_home = _tmp_dir("rime-home-")
    try:
        target = fake_home / ".config" / "ibus" / "rime"
        target.mkdir(parents=True)
        (target / "default.yaml").write_text("x: 1", encoding="utf-8")
        removed = purge_user_data("ibus-rime", fake_home)
        if len(removed) != 1 or target.exists():
            errors.append("purge_user_data 未删除目标目录")
        if purge_user_data("ibus-rime", fake_home):
            errors.append("重复 purge 应返回空")
        if purge_user_data("ibus-rime", Path("/home/nano/.tmp/不存在的家目录")):
            errors.append("空 home 应无删除")
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)
    return errors


def check_manifest() -> list[str]:
    """清单核心：快照 diff（新增/覆盖）+ 卸载还原 + 指纹。"""
    errors: list[str] = []
    base = _tmp_dir("rime-manifest-")
    try:
        manifest = Manifest(base / "m.db", base / "snap")
        rime_dir = base / "rime"
        rime_dir.mkdir()
        (rime_dir / "user.yaml").write_text("old", encoding="utf-8")

        snapshot = manifest.create_snapshot(rime_dir, "unit")
        (rime_dir / "user.yaml").write_text("new", encoding="utf-8")
        (rime_dir / "added.yaml").write_text("brand new", encoding="utf-8")

        added, overwritten = Manifest.diff_snapshot(snapshot, rime_dir)
        if added != ["added.yaml"]:
            errors.append(f"新增文件识别异常: {added}")
        if overwritten != ["user.yaml"]:
            errors.append(f"覆盖文件识别异常: {overwritten}")

        package_id = manifest.add_package(
            "unit:pkg", "单元测试方案", "lab/unit", "ibus-rime",
            rime_dir, snapshot, "digest-1",
        )
        manifest.record_files(package_id, added, overwritten)
        if manifest.installed_keys() != {"unit:pkg"}:
            errors.append("installed_keys 异常")
        if manifest.package_tree_hash("unit:pkg") != "digest-1":
            errors.append("tree_hash 存取异常")

        stats = manifest.restore_package("unit:pkg")
        if (rime_dir / "user.yaml").read_text(encoding="utf-8") != "old":
            errors.append("覆盖文件未还原为安装前内容")
        if (rime_dir / "added.yaml").exists():
            errors.append("新增文件未被删除")
        if stats != {"deleted": 1, "restored": 1, "missing": 0}:
            errors.append(f"卸载统计异常: {stats}")
        if manifest.installed_keys():
            errors.append("卸载后不应仍在已安装列表")

        # 同 key 重装 = 更新记账（旧文件记录先清）
        manifest.add_package("unit:pkg", "单元测试方案", "lab/unit", "ibus-rime",
                            rime_dir, snapshot, "digest-2")
        if manifest.package_tree_hash("unit:pkg") != "digest-2":
            errors.append("重装未覆盖旧记账")

        # v1.0.12：默认皮肤偏好往返（安装器侧 SQLite，不写 rime 配置）
        if manifest.get_default_skin() != "":
            errors.append("初始默认皮肤偏好应为空")
        manifest.set_default_skin("addon:skin-ink")
        if manifest.get_default_skin() != "addon:skin-ink":
            errors.append("默认皮肤偏好存取异常")
        manifest.set_default_skin("addon:skin-deep")
        if manifest.get_default_skin() != "addon:skin-deep":
            errors.append("默认皮肤偏好覆盖异常")
        manifest.set_default_skin("")
        if manifest.get_default_skin() != "":
            errors.append("默认皮肤偏好清除异常")

        manifest.close()

        # tree_hash 对内容敏感
        d1 = _tmp_dir("rime-tree-")
        try:
            (d1 / "a.txt").write_text("1", encoding="utf-8")
            h1 = tree_hash(d1)
            (d1 / "a.txt").write_text("2", encoding="utf-8")
            if tree_hash(d1) == h1:
                errors.append("tree_hash 对内容变化不敏感")
            if tree_hash(d1 / "missing") != "":
                errors.append("tree_hash 处理缺失目录应返回空串")
        finally:
            shutil.rmtree(d1, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_schemes() -> list[str]:
    """方案目录：pkg_dir 映射、规格解析、卡片必备字段、增强包与分支。"""
    errors: list[str] = []
    if PackageRef("rime", "rime-luna-pinyin").pkg_dir != "rime/luna-pinyin":
        errors.append("官方包 pkg_dir 应去掉 rime- 前缀")
    if PackageRef("iDvel", "rime-ice").pkg_dir != "iDvel/ice":
        errors.append("第三方包 pkg_dir 映射错误")
    if PackageRef("a", "b", branch="main", recipe="custom").spec != "a/b@main:custom":
        errors.append("spec 拼接错误")

    if len(SCHEME_MAP) != len(SCHEMES):
        errors.append("SCHEME_MAP 与 SCHEMES 数量不一致（key 冲突）")
    for scheme in SCHEMES:
        if not scheme.intro.strip():
            errors.append(f"{scheme.key} 缺少介绍")
        if not scheme.advantages:
            errors.append(f"{scheme.key} 缺少优势条目")
        if not scheme.size_hint:
            errors.append(f"{scheme.key} 缺少体积提示")
        if not scheme.packages and not scheme.is_skin:
            errors.append(f"{scheme.key} 无包引用（皮肤除外）")
    preset = SCHEME_MAP.get("official:preset")
    if preset is None or preset.targets != [":preset"]:
        errors.append("官方 preset 的 rime-install 目标应为 :preset")

    # rime-ice 默认分支为 main（codeload master 404，2026-10-13 实测）
    ice = SCHEME_MAP["iDvel/rime-ice"]
    if ice.packages[0].branch != "main":
        errors.append(f"rime-ice 分支应为 main: {ice.packages[0].branch}")
    frost = SCHEME_MAP["gaboolic/rime-frost"]
    # v1.0.20：未写分支 = 跟随仓库默认分支（HEAD 探测）；显式 master 也不算错
    if frost.packages[0].branch not in ("", "master"):
        errors.append(f"rime-frost 分支应为默认分支（留空）: {frost.packages[0].branch}")

    # 词库/皮肤增强包（F17）
    for key in ("addon:rime-dict", "addon:rime-pinyin-simp"):
        addon = SCHEME_MAP.get(key)
        if addon is None or addon.category != "addon":
            errors.append(f"增强包缺失或分类错误: {key}")
        elif not addon.advantages:
            errors.append(f"增强包缺少优势条目: {key}")

    # 皮肤条目（F15 皮肤部分）：必须挂在 addon 分类、无包引用、skin_key 可解析
    from core.skin import SKIN_MAP

    skin_entries = [s for s in SCHEMES if s.is_skin]
    if not skin_entries:
        errors.append("皮肤条目缺失")
    # v1.0.7：皮肤扩充至 6 款，分类改为独立 "skin"（徽标/筛选与词库增强区分）
    if len(skin_entries) != 6:
        errors.append(f"皮肤应 6 款: {len(skin_entries)}")
    for entry in skin_entries:
        if entry.category != "skin":
            errors.append(f"皮肤分类应为 skin: {entry.key}")
        if entry.packages:
            errors.append(f"皮肤不应有包引用（不下载仓库）: {entry.key}")
        if entry.skin_key not in SKIN_MAP:
            errors.append(f"皮肤 skin_key 无法解析: {entry.key}")

    # 自定义规格解析
    for good in (
        "iDvel/rime-ice",
        "a/b@main",
        "a/b@dev:recipe",
        "https://github.com/iDvel/rime-ice",
        "http://github.com/a/b.git",
        "github.com/iDvel/rime-ice",
    ):
        if parse_spec(good) is None:
            errors.append(f"合法规格解析失败: {good}")
    for bad in ("", "no-slash", "a/", "/b", "a/b@branch:rec:ipe", "https://gitlab.com/a/b"):
        if parse_spec(bad) is not None:
            errors.append(f"非法规格未被拦截: {bad!r}")
    ref = parse_spec("iDvel/rime-ice@master")
    if ref is None or ref.label != "iDvel/rime-ice":
        errors.append("默认分支 label 异常")
    ref_url = parse_spec("https://github.com/iDvel/rime-ice.git")
    if ref_url is None or ref_url.spec != "iDvel/rime-ice":
        errors.append(f"GitHub 链接归一化异常: {ref_url and ref_url.spec}")
    # v1.0.20：未写 @branch 时 branch 必须留空（跟随仓库默认分支），
    # 且 spec/label 不能出现悬空的 @（默认分支是 main 的仓库曾整条链路 404）
    auto_ref = parse_spec("https://github.com/thep0y/fcitx5-themes-candlelight")
    if auto_ref is None or auto_ref.branch:
        errors.append(f"未指定分支时应留空跟随默认分支: {auto_ref}")
    elif auto_ref.spec != "thep0y/fcitx5-themes-candlelight" or auto_ref.label.endswith("@"):
        errors.append(f"自动分支的 spec/label 不应带 @: {auto_ref.spec} / {auto_ref.label}")
    elif not auto_ref.is_auto_branch:
        errors.append("is_auto_branch 应为 True")
    scheme = custom_scheme(PackageRef("x", "y"))
    if not scheme.key.startswith("custom:") or not scheme.advantages:
        errors.append("自定义方案构造异常")
    return errors


def check_scheme_service() -> list[str]:
    """装卸计划构建：多镜像回退、清缓存步骤、rime_dir/frontend 注入、更新检查计划。"""
    errors: list[str] = []
    urls = tarball_urls(PackageRef("rime", "rime-prelude"))
    if not urls or "codeload.github.com" not in urls[0]:
        errors.append(f"主下载源应为 codeload: {urls[:1]}")
    if len(urls) < 3:
        errors.append("应包含 codeload + 国内镜像回退")
    # v1.0.20：未指定分支时必须先用 HEAD 探测默认分支，再兜底 main/master
    if not urls[0].endswith("/tar.gz/HEAD"):
        errors.append(f"默认分支应先用 /tar.gz/HEAD 探测: {urls[:1]}")
    if not any(u.endswith("/tar.gz/refs/heads/main") for u in urls):
        errors.append("应包含 main 兜底源")
    if not any(u.endswith("/tar.gz/refs/heads/master") for u in urls):
        errors.append("应包含 master 兜底源")
    if sum(1 for u in urls if u.endswith("/tar.gz/HEAD")) != 3:
        errors.append(f"HEAD 应覆盖直连 + 两个镜像: {urls}")
    # main 分支的仓库（rime-ice）URL 必须带 refs/heads/main，且不掺 HEAD
    ice_urls = tarball_urls(PackageRef("iDvel", "rime-ice", branch="main"))
    if not all(u.endswith("/tar.gz/refs/heads/main") for u in ice_urls):
        errors.append(f"main 分支 URL 异常: {ice_urls[:1]}")
    if len(ice_urls) != 3:
        errors.append(f"显式分支应只有三条镜像线路: {len(ice_urls)}")

    base = _tmp_dir("rime-plan-")
    try:
        rime_dir = base / "rime"
        work = base / "work"
        preset = SCHEME_MAP["official:preset"]
        steps = build_scheme_install_steps(preset, "fcitx5-rime", rime_dir, work)
        # 清缓存 + 8 下载 + 8 解压 + 1 校验 + 1 安装
        if len(steps) != 2 + 2 * len(preset.packages) + 1:
            errors.append(f"多包安装步骤数异常: {len(steps)}")
        if clear_package_cache.__name__ != "clear_package_cache":
            errors.append("缺少 clear_package_cache")

        steps_no_clean = build_scheme_install_steps(
            preset, "fcitx5-rime", rime_dir, work, clean=False
        )
        if len(steps_no_clean) != len(steps) - 1:
            errors.append("clean=False 时应少一步清缓存")

        ice = SCHEME_MAP["iDvel/rime-ice"]
        steps = build_scheme_install_steps(ice, "fcitx5-rime", rime_dir, work, clean=False)
        if len(steps) != 4:
            errors.append(f"单包安装应为 下载+解压+校验+安装 4 步: {len(steps)}")
        download_cmd = " ".join(steps[0].argv)
        if "aria2-next" not in download_cmd or "codeload" not in download_cmd:
            errors.append(f"下载步骤命令异常: {download_cmd[:80]}")
        if steps[0].progress_re is None:
            errors.append("下载步骤应带百分比解析器")
        extract_cmd = " ".join(steps[1].argv)
        if "iDvel/ice" not in extract_cmd or "--strip-components=1" not in extract_cmd:
            errors.append(f"解压目标应为 plum/package/iDvel/ice: {extract_cmd[:80]}")
        # v1.0.20：解压后、rime-install 前必须校验包内容（拦「假成功」）
        verify_cmd = " ".join(steps[2].argv)
        if "plum/package/iDvel/ice" not in verify_cmd:
            errors.append(f"校验步骤应检查解压后的包目录: {verify_cmd[:80]}")
        if steps[2].hint_key != "msg.not_rime_package":
            errors.append(f"校验步骤应带失败提示键: {steps[2].hint_key!r}")
        install_cmd = " ".join(steps[3].argv)
        if f"rime_dir={rime_dir}" not in install_cmd:
            errors.append("安装步骤必须显式注入 rime_dir（Deepin 装错目录的根因）")
        if "rime_frontend=fcitx5-rime" not in install_cmd:
            errors.append("安装步骤必须显式注入 rime_frontend")
        # 注意：不能用子串 "rime-install" 判断，用户数据目录 rime-installer 天然含它
        plum_bin = str(config.PLUM_RIME_INSTALL)
        if plum_bin not in install_cmd or "iDvel/rime-ice" not in install_cmd:
            errors.append(f"rime-install 目标异常: {install_cmd[-60:]}")

        check = build_check_steps(ice, work)
        if len(check) != 2 or any(plum_bin in " ".join(s.argv) for s in check):
            errors.append("更新检查计划不应包含安装步骤")

        # 清缓存：删已存在目录、越界防护
        target = config.PLUM_DIR / "package" / "iDvel" / "ice"
        target.mkdir(parents=True, exist_ok=True)
        (target / "probe.txt").write_text("x", encoding="utf-8")
        removed = clear_package_cache(ice)
        if len(removed) != 1 or target.exists():
            errors.append("clear_package_cache 未清理 plum 包缓存")
        if clear_package_cache(ice):
            errors.append("重复清理应返回空")
        # 连空父目录一并清理，避免污染 resources（plum 运行时缓存不入库）
        shutil.rmtree(config.PLUM_DIR / "package" / "iDvel", ignore_errors=True)

        clean_step = build_clean_step(custom_scheme(PackageRef("cleansmoke", "repo")))
        if "cleansmoke/repo" not in " ".join(clean_step.argv):
            errors.append("清缓存步骤路径异常")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_doctor() -> list[str]:
    """体检核心：检查项覆盖、修复步骤构建（不执行）、汇总步骤。"""
    errors: list[str] = []
    report = _report_with_engine()
    results = run_checks(report, ["official:preset"])
    keys = [r.key for r in results]
    for expected in (
        "im_env",
        "frontend_running",
        "engine",
        "schemes",
        "deployed",
        "input_source",
        "autostart",
        "rime_dir",
    ):
        if expected not in keys:
            errors.append(f"缺少体检项: {expected}")
    if not all(r.title and r.detail for r in results):
        errors.append("体检项缺少标题/详情")
    if not all(r.icon in "✓!✕i" for r in results):
        errors.append(f"体检项图标异常: {[r.icon for r in results]}")

    # 修复步骤构建（仅构建，不执行——写环境变量、拉起框架有副作用）
    env_steps = build_fix_steps("im_env", report)
    env_cmd = " ".join(env_steps[0].argv) if env_steps else ""
    if len(env_steps) != 1 or ".xsessionrc" not in env_cmd:
        errors.append("im_env 修复步骤异常（应写 ~/.xsessionrc）")
    # v1.0.26 第二步：旧实现写 ~/.xprofile，但 Debian/Ubuntu/Deepin 的会话脚本
    # 都不读它（/etc/X11/Xsession 只 source ~/.xsessionrc），等于写了没写
    if "environment.d" not in env_cmd or "90-rime-im.conf" not in env_cmd:
        errors.append(f"im_env 应写 ~/.config/environment.d 片段: {env_cmd[:120]}")
    if "export " not in env_cmd:
        errors.append(".xsessionrc 段落必须用 export（Xsession 是 shell 上下文）")
    if "GTK_IM_MODULE" not in env_cmd:
        errors.append("环境变量修复脚本应包含变量定义")

    # 配置文件状态检测：missing / stale / ok 三态（纯文本读回，不启动进程）
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        from core import im_env

        if im_env.detect_state("fcitx5-rime", home)[0] != im_env.STATE_MISSING:
            errors.append("无配置时应判 missing")
        subprocess.run(
            ["bash", "-c", im_env.build_write_script("fcitx5-rime", home)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        state, wrong = im_env.detect_state("fcitx5-rime", home)
        if state != im_env.STATE_OK:
            errors.append(f"写入后应判 ok：{state} {wrong}")
        if not (home / ".config" / "environment.d" / "90-rime-im.conf").is_file():
            errors.append("未生成 environment.d 片段")
        # 幂等：重复写入不得堆叠段落
        subprocess.run(
            ["bash", "-c", im_env.build_write_script("fcitx5-rime", home)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        xs_text = (home / ".xsessionrc").read_text(encoding="utf-8")
        if xs_text.count(im_env.MARK_BEGIN) != 1:
            errors.append("重复写入后 .xsessionrc 标记段重复")
        # 换前端后按旧前端读应为 stale（触发 BAD + 可修复）
        state, wrong = im_env.detect_state("ibus-rime", home)
        if state != im_env.STATE_STALE:
            errors.append(f"切换前端后应判 stale：{state}")
        # 已写入待生效：当前会话仍冲突时不得继续报 BAD（否则永远修不绿）
        pending = _report_no_engine()
        pending.engine_packages["fcitx5-rime"] = True
        pending.recommended_frontend = "fcitx5-rime"
        pending.active_frontend = "fcitx5-rime"
        pending.running_frontends = ["fcitx5-rime"]
        pending.im_env_vars = {
            "GTK_IM_MODULE": "ibus",
            "QT_IM_MODULE": "ibus",
            "XMODIFIERS": "@im=ibus",
            "SDL_IM_MODULE": "ibus",
        }
        orig_home_cls = Path.home
        Path.home = staticmethod(lambda: home)  # type: ignore[assignment]
        try:
            pending_item = {r.key: r for r in run_checks(pending, [])}["im_env"]
        finally:
            Path.home = orig_home_cls  # type: ignore[assignment]
        if pending_item.level != "warn":
            errors.append(
                f"配置已写入但会话未生效时应判 warn（可期待重新登录）: {pending_item.level}"
            )
        if pending_item.fixable:
            errors.append("配置已就绪时不应再标可修复（否则用户反复点）")

    # 英文覆盖：每个 i18n_key 都必须能在英文包里查到 title/detail/advice
    from core.translations.en import EN_TEXT

    sample_reports = [report, _report_no_engine()]
    for sample in sample_reports:
        for item in run_checks(sample, []):
            title_key = f"doctor.check.{item.key}.title"
            if title_key not in EN_TEXT:
                errors.append(f"体检项缺英文标题: {title_key}")
            if not item.i18n_key:
                errors.append(f"体检结果缺少 i18n_key: {item.key}/{item.level}")
                continue
            if f"{item.i18n_key}.detail" not in EN_TEXT:
                errors.append(f"体检详情缺英文: {item.i18n_key}.detail")
            if item.advice and f"{item.i18n_key}.advice" not in EN_TEXT:
                errors.append(f"体检建议缺英文: {item.i18n_key}.advice")

    frontend_steps = build_fix_steps("frontend_running", report)
    if not frontend_steps or "setsid" not in " ".join(frontend_steps[0].argv):
        errors.append("框架启动步骤必须 setsid 脱离进程组（否则 runner 永不完成）")

    deploy_steps = build_fix_steps("deployed", report)
    deploy_cmd = " ".join(deploy_steps[0].argv) if deploy_steps else ""
    if not deploy_steps or "build" not in deploy_cmd:
        errors.append("部署修复步骤应清理 build 目录")
    # v1.0.6：必须主动编译（rime_deployer），异常裁剪系统才退回重启框架
    if deploy_steps and "rime_deployer" not in deploy_cmd and "未找到" not in deploy_cmd:
        errors.append(f"部署修复步骤应主动调用 rime_deployer: {deploy_cmd[:120]}")
    # v1.0.26：编译到 staging，失败保留原 build 并 exit 1（Ubuntu 无 librime-bin 时
    # 旧实现会「删掉可用 build + 报成功」，属拒绝过的假成功）
    if ".rime-new" not in deploy_cmd:
        errors.append("部署修复应编译到 build.rime-new 再替换（失败时保留原 build）")
    if "exit 1" not in deploy_cmd:
        errors.append("部署修复失败必须 exit 1，不得以 0 谎报成功")
    if deploy_cmd.find('rm -rf "$NEW"') > deploy_cmd.find('rm -rf "$BUILD"'):
        errors.append("部署脚本必须先清理 staging、成功后才替换 build")
    if 'rm -rf "$BUILD"' in deploy_cmd.split("if [ -n \"$DEPLOYER\" ]")[0]:
        errors.append("部署脚本不得在判断 deployer 之前删除 build")
    # v1.0.26：冒烟/自动化必须以 RIME_INSTALLER_NO_RESTART=1 构建脚本。真机教训：
    # 跑真部署脚本时 `pkill -x fcitx5` 把开发者本机的输入法杀掉了。
    if os.environ.get("RIME_INSTALLER_NO_RESTART") == "1" and "pkill" in deploy_cmd:
        errors.append("测试模式下部署脚本不应包含 pkill/重启框架命令（会杀本机输入法）")

    source_steps = build_fix_steps("input_source", report)
    source_cmd = " ".join(source_steps[0].argv) if source_steps else ""
    if not source_steps or "gsettings set" not in source_cmd:
        errors.append("输入源修复步骤应包含 gsettings set")
    if source_steps and "ibus-daemon" not in source_cmd:
        errors.append("输入源修复步骤应重启 ibus 使输入源生效")

    if build_fix_steps("engine", report):
        errors.append("引擎未装不可自动修复，应返回空")

    fix_all = build_fix_all_steps(results, report)
    fixable_count = sum(1 for r in results if r.fixable)
    if fixable_count and not fix_all:
        errors.append("存在可修复项但汇总步骤为空")

    # 无框架运行时 im_env 应为 info 级（不可武断判错）
    idle_report = _report_no_engine()
    idle_results = {r.key: r for r in run_checks(idle_report, [])}
    if idle_results["im_env"].level != "info":
        errors.append(f"无框架运行时 im_env 应为 info: {idle_results['im_env'].level}")

    # v1.0.8：fcitx5 / 旧 fcitx 前端同样要有「输入法列表」检查项（Deepin 场景）
    # v1.0.15：fcitx5 列表解析改分段结构后，三态（已注册 ok / 未注册 bad /
    # 无配置 info）一律用注入返回值断言，不再依赖真机 profile 状态——
    # 旧实现只找不存在的 IMList= 键，真机已装 Rime 却误报 BAD（v1.0.15 实证）。
    orig_methods = doctor._fcitx5_input_methods
    try:
        for frontend in ("fcitx5-rime", "fcitx-rime"):
            f_report = _report_no_engine()
            f_report.engine_packages[frontend] = True
            f_report.recommended_frontend = frontend
            if frontend == "fcitx5-rime":
                doctor._fcitx5_input_methods = lambda: "keyboard-cn, rime"
            f_results = {r.key: r for r in run_checks(f_report, [])}
            item = f_results.get("input_source")
            if item is None:
                errors.append(f"{frontend} 缺少输入法列表检查项")
            elif item.level not in ("ok", "bad", "info"):
                errors.append(f"{frontend} 输入法列表级别异常: {item.level}")
            elif frontend == "fcitx5-rime" and item.level == "bad" and "中州韵" not in item.advice:
                errors.append(f"fcitx5 未注册 Rime 时应引导添加中州韵: {item.advice[:60]}")
            # fcitx5 的列表由配置工具维护，不做自动改写（改错会打乱输入法顺序）
            if frontend == "fcitx5-rime" and item is not None and item.fixable:
                errors.append("fcitx5 输入法列表不应自动改写（应引导用户添加）")
        # fcitx5 三态断言
        f_report = _report_no_engine()
        f_report.engine_packages["fcitx5-rime"] = True
        f_report.recommended_frontend = "fcitx5-rime"
        doctor._fcitx5_input_methods = lambda: "keyboard-cn, rime, pinyin"
        ok_item = {r.key: r for r in run_checks(f_report, [])}["input_source"]
        if ok_item.level != "ok":
            errors.append(f"已注册 rime 应判 ok: {ok_item.level} {ok_item.detail[:40]}")
        doctor._fcitx5_input_methods = lambda: "keyboard-cn, pinyin"
        bad_item = {r.key: r for r in run_checks(f_report, [])}["input_source"]
        if bad_item.level != "bad":
            errors.append(f"未注册 rime 应判 bad: {bad_item.level}")
        if "pinyin" not in bad_item.detail or "中州韵" not in bad_item.advice:
            errors.append(f"bad 详情应列出当前输入法并引导添加中州韵: {bad_item.detail[:40]}")
        doctor._fcitx5_input_methods = lambda: None
        info_item = {r.key: r for r in run_checks(f_report, [])}["input_source"]
        if info_item.level != "info":
            errors.append(f"无 fcitx5 配置应判 info: {info_item.level}")
    finally:
        doctor._fcitx5_input_methods = orig_methods

    # 解析器直测：真实分段格式 / 组名不误收 / IMList 兼容 / 文件缺失
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp) / "profile"
        profile.write_text(
            "[Groups/0]\n"
            "Name=默认\n"
            "DefaultIM=rime\n"
            "\n"
            "[Groups/0/Items/0]\n"
            "Name=keyboard-cn\n"
            "\n"
            "[Groups/0/Items/1]\n"
            "Name=rime\n",
            encoding="utf-8",
        )
        methods = _fcitx5_input_methods(profile)
        if not methods or "rime" not in methods.split(", "):
            errors.append(f"真实格式 profile 未解析出 rime: {methods!r}")
        if "默认" in (methods or ""):
            errors.append(f"组名被误收为输入法: {methods!r}")
        legacy = Path(tmp) / "legacy"
        legacy.write_text("IMList=keyboard-cn,rime\n", encoding="utf-8")
        if "rime" not in (_fcitx5_input_methods(legacy) or ""):
            errors.append("IMList= 兼容分支失效")
        if _fcitx5_input_methods(Path(tmp) / "missing") is not None:
            errors.append("profile 缺失应返回 None")
        empty = Path(tmp) / "empty"
        empty.write_text("[Groups/0]\nName=默认\n", encoding="utf-8")
        if _fcitx5_input_methods(empty) != "":
            errors.append("无输入法段应返回空串")

    # ---- v1.0.26 审计修复回归 ----
    # a) 非 GNOME 桌面（DDE/KDE）用 ibus：不得误报「GNOME 输入源未添加」
    dde = _report_no_engine()
    dde.desktop = "DDE"
    dde.engine_packages["ibus-rime"] = True
    dde.recommended_frontend = "ibus-rime"
    dde.active_frontend = "ibus-rime"
    dde.running_frontends = ["ibus-rime"]
    dde_item = {r.key: r for r in run_checks(dde, [])}["input_source"]
    if dde_item.level != "info":
        errors.append(f"非 GNOME 桌面不应判输入源缺失: {dde_item.level} {dde_item.detail[:40]}")
    if build_fix_steps("input_source", dde):
        errors.append("非 GNOME 桌面不应构建 gsettings 修复步骤（写了没人读）")

    # b) ~/.xinputrc 只认 run_im 指令行：注释里的 "im-config" 曾导致误报不一致
    orig_home = Path.home
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / ".xinputrc").write_text(
            "# im-config(8) generated on Sat, 18 Oct 2026 10:00:00 +0800\n"
            "#\n"
            "run_im fcitx5\n",
            encoding="utf-8",
        )
        Path.home = staticmethod(lambda: home)  # type: ignore[assignment]
        try:
            xin = _report_no_engine()
            xin.engine_packages["fcitx5-rime"] = True
            xin.recommended_frontend = "fcitx5-rime"
            xin.active_frontend = "fcitx5-rime"
            xin.running_frontends = ["fcitx5-rime"]
            xin_item = {r.key: r for r in run_checks(xin, [])}["autostart"]
        finally:
            Path.home = orig_home  # type: ignore[assignment]
        if xin_item.level != "ok":
            errors.append(
                f"run_im fcitx5 应判一致（注释行不得参与解析）: "
                f"{xin_item.level} {xin_item.detail[:60]}"
            )

    # c) 配置目录不可读：必须降级为 warn，绝不能抛异常（槽内异常 → PyQt6 abort）
    if os.geteuid() != 0:
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            locked.chmod(0o000)
            unread = _report_no_engine()
            unread.rime_user_dir = locked
            try:
                unread_res = {r.key: r for r in run_checks(unread, [])}
            except OSError as exc:  # noqa: BLE001
                errors.append(f"配置目录不可读时 run_checks 抛异常: {exc}")
            else:
                if unread_res["deployed"].level != "warn":
                    errors.append(
                        f"配置目录不可读时部署状态应为 warn: {unread_res['deployed'].level}"
                    )
                if unread_res["rime_dir"].level != "warn":
                    errors.append(
                        f"配置目录不可读时应给 warn: {unread_res['rime_dir'].level}"
                    )
            finally:
                locked.chmod(0o755)
    return errors


def check_apt_mirror() -> list[str]:
    """apt 镜像：当前源识别、测速 URL、切换步骤构建、并行测速容错。"""
    errors: list[str] = []
    all_mirrors = mirrors()
    if len(all_mirrors) < 3:
        errors.append(f"镜像数量不足: {len(all_mirrors)}")
    names = {m.name for m in all_mirrors}
    if not {"官方源", "阿里云", "清华大学"} <= names:
        errors.append(f"镜像名单缺少国内源: {names}")

    current = current_mirror_name()
    if not current:
        errors.append("当前源识别为空")

    if is_ubuntu():
        if not system_codename():
            errors.append("Ubuntu 上未取到 VERSION_CODENAME")
        urls = speedtest_urls()
        if len(urls) != len(all_mirrors) or not all("dists/" in u for _, u in urls):
            errors.append(f"测速 URL 异常: {urls[:2]}")
        # v1.0.5：写源 + update 合并为单步单次 pkexec（单次密码框）
        steps = build_switch_steps(all_mirrors[1])
        if len(steps) != 1:
            errors.append(f"切换应合并为单步（单次密码框）: {len(steps)}")
        else:
            joined = " ".join(steps[0].argv)
            if "apt-get update" not in joined:
                errors.append("切换缺少 apt update")
            if "aliyun" not in joined:
                errors.append(f"切换未包含目标镜像: {steps[0].argv[:4]}")
            if ".rime-backup" not in joined:
                errors.append("切换步骤应备份原源文件")
            if joined.count("pkexec") != 1:
                errors.append(f"切换应只有一次 pkexec 提权: {joined}")

    # 并行测速对不可达地址必须返回 None 而非崩溃（TEST-NET-1 黑洞地址）
    probed = measure_mirror_latency(
        [("黑洞", "http://192.0.2.1/ubuntu/dists/n/Release")], timeout=1.0
    )
    if len(probed) != 1 or probed[0][1] is not None:
        errors.append(f"不可达镜像应返回 None: {probed}")
    return errors


def check_deploy_guide() -> list[str]:
    """部署引导：重启/重新部署/配置工具步骤均以 setsid 脱离，避免阻塞 runner。"""
    errors: list[str] = []
    for frontend in ("ibus-rime", "fcitx5-rime", "fcitx-rime"):
        restart = build_restart_steps(frontend)
        if len(restart) != 1 or "setsid" not in " ".join(restart[0].argv):
            errors.append(f"{frontend} 重启步骤异常: {restart}")
        tool = build_open_config_tool_steps(frontend)
        expected_tool = {
            "ibus-rime": "ibus-setup",
            "fcitx5-rime": "fcitx5-configtool",
            "fcitx-rime": "fcitx-configtool",
        }[frontend]
        if expected_tool not in " ".join(tool[0].argv):
            errors.append(f"{frontend} 配置工具应为 {expected_tool}: {tool[0].argv}")

    redeploy = build_redeploy_steps("fcitx5-rime", Path("/tmp/rime-x"))
    cmd = " ".join(redeploy[0].argv)
    # v1.0.26：测试模式下脚本不重启框架（setsid 被替换为测试模式提示）
    need_setsid = os.environ.get("RIME_INSTALLER_NO_RESTART") != "1"
    if "/tmp/rime-x/build" not in cmd or (need_setsid and "setsid" not in cmd):
        errors.append(f"重新部署步骤异常: {cmd[:100]}")
    if not need_setsid and "测试模式" not in cmd:
        errors.append("测试模式下应显式跳过重启框架（否则会杀掉本机 fcitx5）")
    # v1.0.6：重新部署必须主动编译（rime_deployer），而非仅删 build + 重启
    if "rime_deployer" not in cmd and "未找到" not in cmd:
        errors.append(f"重新部署步骤应主动调用 rime_deployer: {cmd[:140]}")
    # v1.0.8：共享数据目录需运行时二次探测（Deepin 等发行版路径可能不同）
    if "rime_deployer" in cmd and 'SHARED=' not in cmd:
        errors.append("部署脚本应包含共享数据目录运行时探测（SHARED）")
    return errors


def check_skin() -> list[str]:
    """皮肤（F15 皮肤部分）：步骤构建、标记块渲染、CLI 幂等/切换/移除、i18n 键。"""
    errors: list[str] = []
    from core import i18n
    from core.skin import (
        MARK_BEGIN,
        MARK_END,
        SKINS,
        SKIN_MAP,
        build_apply_steps,
        build_revert_steps,
        detect_active_skin,
        render_block,
    )

    if len({s.key for s in SKINS}) != len(SKINS) or not SKINS:
        errors.append("皮肤 key 应非空且唯一")
    for skin in SKINS:
        if not skin.name.strip() or not skin.intro.strip() or not skin.colors:
            errors.append(f"皮肤字段不完整: {skin.key}")
        if "name" not in skin.colors or "back_color" not in skin.colors:
            errors.append(f"皮肤缺少基础色字段: {skin.key}")

    # 步骤构建：写配置 + 重新部署（复用部署引导，setsid）
    rime_dir = Path("/tmp/rime-skin-x")
    apply_steps = build_apply_steps(SKINS[0], rime_dir, "fcitx5-rime")
    if len(apply_steps) != 2:
        errors.append(f"皮肤应用步骤应为 写配置+重新部署: {len(apply_steps)}")
    joined = " ".join(apply_steps[0].argv)
    if str(config.SKIN_CLI) not in joined or SKINS[0].key not in joined:
        errors.append(f"皮肤写入步骤未指向 CLI 与皮肤键: {apply_steps[0].argv}")
    if sys.executable not in joined:
        errors.append(f"皮肤写入步骤应用当前解释器而非裸 python3: {apply_steps[0].argv}")
    # 重新部署步：正常运行必须是 setsid 脱离进程组；但冒烟/自动化（
    # RIME_INSTALLER_NO_RESTART=1）刻意把重启换成 echo，免得把开发机
    # 正在用的 fcitx5 杀掉——此时断 setsid 会恒红，改为断「测试模式」。
    # 2026-10-18 修：v1.0.26 让 build_deploy_script 认这个开关后，本项在
    # 整轮冒烟里一直 FAIL(1)，属于测试自身的回归。
    deploy_argv = " ".join(apply_steps[1].argv)
    if os.environ.get("RIME_INSTALLER_NO_RESTART") == "1":
        if "测试模式" not in deploy_argv:
            errors.append("测试模式下重新部署脚本未标注为测试模式")
        if "pkill" in deploy_argv:
            errors.append("测试模式下重新部署脚本不应重启输入法框架")
    elif "setsid" not in deploy_argv:
        errors.append("皮肤应用后应触发重新部署（setsid）")
    revert_steps = build_revert_steps(rime_dir, "fcitx5-rime")
    if "--revert" not in " ".join(revert_steps[0].argv):
        errors.append(f"皮肤移除步骤缺少 --revert: {revert_steps[0].argv}")

    # 标记块渲染：色值为 0xBBGGRR 形式（name 是显示名，不参与色值断言）
    block = render_block(SKINS[0])
    if MARK_BEGIN not in block or MARK_END not in block:
        errors.append("标记块缺少边界注释")
    if "color_scheme: rime_installer_skin" not in block:
        errors.append("标记块未挂载 color_scheme")
    for field, value in SKINS[0].colors.items():
        if field != "name" and not value.startswith("0x"):
            errors.append(f"皮肤色值应为 0x 开头: {field}={value}")

    # CLI 端到端：保留用户配置 / 幂等 / 切换替换 / 移除干净
    base = _tmp_dir("rime-skin-")
    try:
        user_dir = base / "rime"
        user_dir.mkdir()
        target = user_dir / "default.custom.yaml"
        user_config = "patch:\n  schema_list:\n    - schema: luna_pinyin\n"
        target.write_text(user_config, encoding="utf-8")

        def run_cli(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(config.SKIN_CLI), str(user_dir), *args],
                capture_output=True,
                text=True,
                timeout=30,
            )

        first = run_cli("skin-ink")
        if first.returncode != 0:
            errors.append(f"应用皮肤失败: {first.stderr[-200:]}")
            return errors
        text = target.read_text(encoding="utf-8")
        if "schema: luna_pinyin" not in text:
            errors.append("应用皮肤破坏了用户既有配置")
        if text.count(MARK_BEGIN) != 1 or text.count(MARK_END) != 1:
            errors.append("应用后标记块应恰好一对")
        # v1.0.12：detect_active_skin 从配置文件反读当前皮肤
        active = detect_active_skin(user_dir)
        if active is None or active.key != "skin-ink":
            errors.append(f"detect_active_skin 应识别当前皮肤: {active}")

        again = run_cli("skin-ink")
        if again.returncode != 0:
            errors.append(f"重复应用皮肤失败: {again.stderr[-200:]}")
        text = target.read_text(encoding="utf-8")
        if text.count(MARK_BEGIN) != 1:
            errors.append("重复应用后标记块仍应恰好一对（幂等）")

        run_cli("skin-deep")
        text = target.read_text(encoding="utf-8")
        if text.count(MARK_BEGIN) != 1:
            errors.append("切换皮肤后标记块应仍为一对（替换而非叠加）")
        if "0x432a10" not in text:
            errors.append("切换皮肤后应写入新皮肤色值")
        active = detect_active_skin(user_dir)
        if active is None or active.key != "skin-deep":
            errors.append(f"切换后 detect_active_skin 应识别新皮肤: {active}")

        reverted = run_cli("--revert")
        if reverted.returncode != 0:
            errors.append(f"移除皮肤失败: {reverted.stderr[-200:]}")
        text = target.read_text(encoding="utf-8")
        if MARK_BEGIN in text or MARK_END in text:
            errors.append("移除后标记块应彻底消失")
        if "schema: luna_pinyin" not in text:
            errors.append("移除皮肤破坏了用户既有配置")
        if detect_active_skin(user_dir) is not None:
            errors.append("移除后 detect_active_skin 应返回 None")
        # 无标记块/目录不存在时反读应为 None（不抛异常）
        if detect_active_skin(base / "missing-dir") is not None:
            errors.append("目录不存在时 detect_active_skin 应返回 None")

        # 未知皮肤/参数错误应有非零退出码
        if run_cli("skin-nope").returncode == 0:
            errors.append("未知皮肤应返回非零")
        if run_cli().returncode == 0:
            errors.append("缺少参数应返回非零")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # 皮肤 UI 文案键（中英都要有，缺键回退虽安全但主干不应缺）
    from core.translations.en import EN_TEXT

    for key in (
        "scheme.apply",
        "scheme.reapply",
        "scheme.remove",
        "scheme.applied",
        "scheme.apply_tip",
        "scheme.remove_tip",
        "scheme.uninstall_skin_title",
        "scheme.uninstall_skin_body",
        "msg.skin_applied",
        "msg.skin_removed",
        # v1.0.12：切换语义 + 当前态 + 我的默认 + 已安装快捷切换
        "scheme.switch_to",
        "scheme.switch_to_tip",
        "scheme.redeploy",
        "scheme.redeploy_tip",
        "scheme.current_skin",
        "scheme.my_default",
        "scheme.set_default",
        "scheme.set_default_tip",
        "scheme.restore_default",
        "scheme.restore_default_tip",
        "scheme.current_label",
        "scheme.no_current",
        "scheme.installed_skins",
        "msg.default_skin_set",
        "msg.default_skin_set_hint",
        "msg.default_skin_restored",
        "msg.default_skin_restore_hint",
        "msg.no_default_skin",
        "msg.no_default_skin_hint",
        "msg.already_default_skin",
        "msg.already_default_skin_hint",
        "msg.default_skin_cleared",
        "msg.default_skin_cleared_hint",
    ):
        if key not in i18n.ZH_TEXT:
            errors.append(f"中文源缺少皮肤文案键: {key}")
        if key not in EN_TEXT:
            errors.append(f"英文包缺少皮肤文案键: {key}")
    if "墨黑" not in i18n.tr("msg.skin_applied", name="墨黑"):
        errors.append("msg.skin_applied 占位替换异常")
    return errors


def check_theme_import() -> list[str]:
    """从链接导入主题（v1.0.21 / v1.0.22）：五种上游形态枚举、目录名防御、
    资产按引用挑选、重名覆盖、批量单款失败不中断、空仓库诊断。"""
    errors: list[str] = []
    from core import theme_import

    base = _tmp_dir("theme-import-")
    try:
        # 形态 L2（thep0y / zhiim）：顶层子目录 + theme.conf
        repo = base / "repo"
        (repo / "alpha").mkdir(parents=True)
        (repo / "alpha" / "theme.conf").write_text(
            "[Metadata]\nName=Alpha 四季\n\n[InputPanel]\nNormalColor=#222222\n",
            encoding="utf-8",
        )
        (repo / "alpha" / "panel.svg").write_text("<svg/>", encoding="utf-8")
        (repo / "alpha" / "radio.png").write_text("png", encoding="utf-8")
        (repo / "beta").mkdir()
        (repo / "beta" / "theme.conf").write_text(
            "[InputPanel]\nNormalColor=#ffffff\n", encoding="utf-8"
        )
        (repo / "images").mkdir()
        (repo / "images" / "shot.png").write_text("img", encoding="utf-8")
        (repo / ".gitignore").write_text("build/", encoding="utf-8")

        themes = theme_import.enumerate_themes(repo)
        if [t.key for t in themes] != ["alpha", "beta"]:
            errors.append(
                f"目录模式应只认含 theme.conf 的子目录: {[t.key for t in themes]}"
            )
        if [t.name for t in themes] != ["Alpha 四季", "beta"]:
            errors.append(
                f"显示名应取 [Metadata] Name、缺失回退目录名: {[t.name for t in themes]}"
            )

        # 形态 L1（dracula / iovxw / adwaita）：根目录即一款主题，
        # 资产按 theme.conf 的 Image= 精确取，README/.psd 不搬
        solo = base / "solo"
        solo.mkdir()
        (solo / "theme.conf").write_text(
            "[Metadata]\nName=Dracula Solo\n\n[InputPanel/Background]\n"
            " Image=panel.png\n\n[InputPanel/Highlight]\n  Image=highlight.png\n",
            encoding="utf-8",
        )
        for name in ("panel.png", "highlight.png", "unused-var.png", "arrow.psd"):
            (solo / name).write_text("x", encoding="utf-8")
        (solo / "README.md").write_text("readme", encoding="utf-8")
        (solo / "screenshots").mkdir()
        (solo / "screenshots" / "shot.png").write_text("x", encoding="utf-8")
        solo_themes = theme_import.enumerate_themes(solo)
        if [t.key for t in solo_themes] != ["Dracula-Solo"]:
            errors.append(f"根目录主题应只枚举一款且用 Name 作 key: {[t.key for t in solo_themes]}")
        elif not solo_themes[0].root_theme:
            errors.append("根目录主题应标记 root_theme")

        # 形态 L3（catppuccin）：theme.conf 在第二层子目录里
        nested = base / "nested"
        for variant in ("mocha-blue", "latte-red"):
            d = nested / "src" / variant
            d.mkdir(parents=True)
            (d / "theme.conf").write_text(
                f"[Metadata]\nName=Catppuccin {variant}\n", encoding="utf-8"
            )
            (d / "panel.svg").write_text("<svg/>", encoding="utf-8")
        (nested / "templates").mkdir()
        (nested / "templates" / "x.tera").write_text("t", encoding="utf-8")
        nested_themes = theme_import.enumerate_themes(nested)
        if [t.key for t in nested_themes] != ["latte-red", "mocha-blue"]:
            errors.append(f"嵌套子目录主题未识别: {[t.key for t in nested_themes]}")
        elif nested_themes[0].rel_dir != "src/latte-red":
            errors.append(f"嵌套主题 rel_dir 应为 src/<变体>: {nested_themes[0].rel_dir}")

        # 变体模式（hosxy / gruvbox）：根级 theme-*.conf + 根级图片资产；
        # Name 互不相同时保持原名，撞名才补变体后缀（否则互相覆盖）
        variant = base / "variant"
        variant.mkdir()
        (variant / "theme-blue.conf").write_text(
            "[InputPanel]\nNormalColor=#3366cc\n", encoding="utf-8"
        )
        (variant / "theme-red.conf").write_text(
            "[Metadata]\nName=Material-Red\n", encoding="utf-8"
        )
        (variant / "arrow.png").write_text("png", encoding="utf-8")
        (variant / "README.md").write_text("x", encoding="utf-8")
        vthemes = theme_import.enumerate_themes(variant)
        if [t.key for t in vthemes] != ["Blue", "Material-Red"]:
            errors.append(f"变体模式枚举异常: {[t.key for t in vthemes]}")

        clash = base / "clash"
        clash.mkdir()
        for suffix in ("black", "blue", "red"):
            (clash / f"theme-{suffix}.conf").write_text(
                "[Metadata]\nName=Material-Color\n", encoding="utf-8"
            )
        cthemes = theme_import.enumerate_themes(clash)
        if [t.key for t in cthemes] != [
            "Material-Color-black",
            "Material-Color-blue",
            "Material-Color-red",
        ]:
            errors.append(f"同名变体应补后缀避免互相覆盖: {[t.key for t in cthemes]}")
        if len({t.key for t in cthemes}) != 3:
            errors.append("同名变体的 key 必须唯一，否则安装时互相覆盖")

        # 根目录主题 + 同层变体共存（gruvbox 形态）
        hybrid = base / "hybrid"
        hybrid.mkdir()
        (hybrid / "theme.conf").write_text(
            "[Metadata]\nName=gruvbox\n", encoding="utf-8"
        )
        (hybrid / "theme-aqua.conf").write_text(
            "[Metadata]\nName=gruvbox\n", encoding="utf-8"
        )
        hthemes = theme_import.enumerate_themes(hybrid)
        if sorted(t.key for t in hthemes) != ["gruvbox", "gruvbox-aqua"]:
            errors.append(f"根目录主题与变体应同时枚举: {[t.key for t in hthemes]}")

        # 目录名防御：越界/绝对路径/分隔符一律拒绝
        for bad in ("", ".", "..", "/abs", "a/b", "a\\b", "-x", "a\0b"):
            if theme_import.valid_theme_name(bad):
                errors.append(f"非法主题名未被拦截: {bad!r}")
        for good in ("autumn", "macOS-dark", "Material-Color-Blue", "珍珠白"):
            if not theme_import.valid_theme_name(good):
                errors.append(f"合法主题名被误拦: {good}")

        # 安装：整目录拷贝（图片型主题必须带资产）+ 重名先清后拷
        themes_dir = base / "user-themes"
        files = theme_import.install_theme(repo, themes[0], themes_dir)
        target = themes_dir / "alpha"
        for name in ("theme.conf", "panel.svg", "radio.png"):
            if not (target / name).is_file():
                errors.append(f"主题资产未整目录拷贝: {name}")
        if len(files) < 3:
            errors.append(f"落盘文件列表异常: {files}")
        (target / "stale.svg").write_text("<svg/>", encoding="utf-8")
        theme_import.install_theme(repo, themes[0], themes_dir)
        # 零删除补跑模式下 rmtree 被置空，跳过「旧资产已清」这一条（其余断言照跑）
        if shutil.rmtree is _PRISTINE_RMTREE and (target / "stale.svg").exists():
            errors.append("重复导入应先清旧目录（避免旧资产残留）")

        # 根目录主题安装：只带被引用的图，不搬 README / .psd / 未引用图
        theme_import.install_theme(solo, solo_themes[0], themes_dir)
        starget = themes_dir / "Dracula-Solo"
        if not (starget / "theme.conf").is_file():
            errors.append("根目录主题应落成 theme.conf")
        for name in ("panel.png", "highlight.png"):
            if not (starget / name).is_file():
                errors.append(f"根目录主题漏拷被引用的图: {name}")
        for name in ("README.md", "arrow.psd", "unused-var.png"):
            if (starget / name).exists():
                errors.append(f"根目录主题不应搬无关文件: {name}")
        if (starget / "screenshots").exists():
            errors.append("根目录主题不应搬截图目录")

        # 变体安装：theme-<x>.conf 落成 theme.conf + 只带根级图片资产
        theme_import.install_theme(variant, vthemes[0], themes_dir)
        vtarget = themes_dir / "Blue"
        if not (vtarget / "theme.conf").is_file():
            errors.append("变体模式应落成 theme.conf")
        if not (vtarget / "arrow.png").is_file():
            errors.append("变体模式应带上根级图片资产")
        if (vtarget / "theme-red.conf").exists():
            errors.append("变体模式不应把其它变体配置一起拷进去")

        # 批量：单款失败不中断，越界名不落盘
        bad_theme = theme_import.ImportTheme(key="../escape", name="坏主题")
        good_theme = theme_import.ImportTheme(key="beta", name="beta", rel_dir="beta")
        installed, failed = theme_import.install_all(
            repo, [bad_theme, good_theme], themes_dir
        )
        if installed != ["beta"] or not failed:
            errors.append(f"单款失败不应中断批量: installed={installed} failed={failed}")
        if (base / "escape").exists():
            errors.append("越界主题名不得写到主题目录之外")

        # 空仓库：没有任何 theme.conf → 空列表（上层据此提示「不是主题仓库」）
        empty = base / "empty"
        empty.mkdir()
        (empty / "README.md").write_text("x", encoding="utf-8")
        if theme_import.enumerate_themes(empty):
            errors.append("无 theme.conf 的仓库应枚举为空")
        if theme_import.diagnose_repo(empty):
            errors.append("普通仓库（只有 README）不应被诊断成特定类型")

        # 空仓库诊断：手机端主题 / 配色方案 / 需构建源码
        android = base / "android"
        (android / "json").mkdir(parents=True)
        (android / "json" / "mint.json").write_text(
            '{"name":"mint","isDark":false,"keyBackgroundColor":-1,'
            '"barColor":-1118482,"backgroundColor":-855310}',
            encoding="utf-8",
        )
        if theme_import.diagnose_repo(android) != "msg.theme_android":
            errors.append("fcitx5-android JSON 主题应被识别")

        scheme = base / "scheme"
        (scheme / "theme").mkdir(parents=True)
        (scheme / "theme" / "Ink.conf").write_text(
            "[LightMode]\nOverrideDefault=True\nHighlightColor=#8caaee\n", encoding="utf-8"
        )
        if theme_import.diagnose_repo(scheme) != "msg.theme_color_scheme":
            errors.append("android/macos 配色方案应被识别")

        build_only = base / "build-only"
        (build_only / "config").mkdir(parents=True)
        (build_only / "build.py").write_text("print(1)", encoding="utf-8")
        (build_only / "config" / "theme.conf.in").write_text("[InputPanel]\n", encoding="utf-8")
        if theme_import.diagnose_repo(build_only) != "msg.theme_source_only":
            errors.append("源码模板仓库应被识别为需要构建")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_appearance() -> list[str]:
    """外观（v1.0.13）：主题枚举/渲染/安装、classicui.conf 切换与备份还原、
    ibus 标记块往返、CLI 端到端、状态机规则、i18n 键。"""
    errors: list[str] = []
    base = _tmp_dir("appearance-")
    try:
        from core import appearance

        conf = base / "classicui.conf"
        themes = base / "themes"
        backups = base / "backups"
        rime_dir = base / "rime"
        sys_themes = base / "sys-themes"
        for d in (themes, backups, rime_dir, sys_themes):
            d.mkdir()
        conf.write_text(
            'Theme=deepin-light\nDarkTheme=deepin-dark\nUseDarkTheme=True\nFont="Sans 12"\n',
            encoding="utf-8",
        )
        (rime_dir / "ibus_rime.custom.yaml").write_text(
            "patch:\n  schema_list:\n    - schema: luna_pinyin\n", encoding="utf-8"
        )

        # 枚举：伪造一个系统主题（用户目录初始为空）
        (sys_themes / "fake-dark").mkdir()
        (sys_themes / "fake-dark" / "theme.conf").write_text(
            "[Metadata]\nName=假暗主题\n", encoding="utf-8"
        )
        found = appearance.list_themes(sys_themes, themes)
        # 注意：list_themes 现会先枚举内置完整主题（Mellow 系列），故按 key 取 fake-dark
        fake = next((t for t in found if t.key == "fake-dark"), None)
        if fake is None or fake.name != "假暗主题":
            errors.append(f"系统主题枚举异常: {[(t.key, t.name) for t in found]}")
        elif fake.source != "system":
            errors.append("系统主题来源标注异常")
        if fake and (fake.palette is not None or fake.dark is not None):
            errors.append("无颜色字段的 theme.conf 不应解析出配色/深浅")

        # 内置主题渲染 + 安装 + 来源标注
        theme = appearance.bundled_theme("rime-installer-ink")
        if theme is None:
            errors.append("内置主题 rime-installer-ink 缺失")
            return errors
        rendered = appearance.render_bundled_theme(theme)
        for token in (
            "[Metadata]",
            "[InputPanel]",
            "NormalColor=#e8e8e8",
            "[InputPanel/Background]",
            "Color=#141414",
            "[InputPanel/Highlight]",
        ):
            if token not in rendered:
                errors.append(f"内置主题 theme.conf 缺少段/键: {token}")
        appearance.install_bundled_theme(theme, themes)
        if not (themes / "rime-installer-ink" / "theme.conf").is_file():
            errors.append("内置主题未落盘")
        entry = next(
            (t for t in appearance.list_themes(sys_themes, themes)
             if t.key == "rime-installer-ink"),
            None,
        )
        if entry is None or entry.source != "installer":
            errors.append("内置主题来源应标注 installer")
        if entry is not None:
            if entry.palette is None or entry.palette.background != "#141414":
                errors.append(f"内置墨黑应带权威五色: {entry.palette}")
            if entry.dark is not True:
                errors.append("内置墨黑应判定为深色")
        # 内置完整主题（Mellow 系列）：资源目录枚举 + 预览五色 + 切换安装
        full = appearance.list_bundled_full_themes()
        if len(full) != 10:
            errors.append(f"内置完整主题应枚举 10 款，实际 {len(full)}")
        full_keys = {t.key for t in full}
        for need in ("mellow-graphite", "mellow-graphite-dark"):
            if need not in full_keys:
                errors.append(f"内置完整主题缺少: {need}")
        if "mellow-graphite" in full_keys and "mellow-graphite-dark" in full_keys:
            light = next(t for t in full if t.key == "mellow-graphite")
            dark = next(t for t in full if t.key == "mellow-graphite-dark")
            for t in (light, dark):
                if t.source != "installer":
                    errors.append(f"内置完整主题来源应标 installer: {t.key}")
                if t.palette is None:
                    errors.append(f"内置完整主题预览应非空: {t.key}")
            if light.dark is not False:
                errors.append("mellow-graphite 应判为浅色")
            if dark.dark is not True:
                errors.append("mellow-graphite-dark 应判为深色")
        if not appearance.theme_exists("mellow-graphite", base / "no-such-dir"):
            errors.append("内置完整主题应通过 theme_exists 校验")
        # v1.0.19：按「内置 / 我的」拆分——系统自带主题不进界面
        if not appearance.BUNDLED_THEMES_REPO.startswith("https://github.com/sanweiya/"):
            errors.append(f"内置主题上游仓库地址异常: {appearance.BUNDLED_THEMES_REPO}")
        if not (appearance.BUNDLED_FULL_THEMES_DIR / "LICENSE").is_file():
            errors.append("内置主题目录应随附上游 LICENSE（BSD-2-Clause 要求）")
        builtin_list, custom_list = appearance.split_themes(
            appearance.list_themes(sys_themes, themes)
        )
        if any(t.source != "installer" for t in builtin_list):
            errors.append("内置分组应只含 installer 来源")
        if any(t.source != "user" for t in custom_list):
            errors.append("我的主题分组应只含 user 来源")
        if any(t.key == "fake-dark" for t in builtin_list + custom_list):
            errors.append("系统自带主题不应进入展示分组")
        if not any(t.key == "rime-installer-ink" for t in builtin_list):
            errors.append("内置墨黑应归入内置分组")
        # 安装（整包复制）落盘 5 个文件
        installed = appearance.install_bundled_full_theme("mellow-graphite", themes)
        if sorted(p.name for p in installed.iterdir()) != [
            "arrow.svg", "highlight.svg", "panel.svg", "radio.svg", "theme.conf"
        ]:
            errors.append(f"内置完整主题安装文件异常: {sorted(p.name for p in installed.iterdir())}")
        # 安装副本应保留深色主题 Menu 修复（Menu/NormalColor == InputPanel 文字色）
        for dk in ("mellow-graphite-dark", "mellow-sakura-dark", "mellow-vermilion-dark",
                   "mellow-wechat-dark", "mellow-youlan-dark"):
            appearance.install_bundled_full_theme(dk, themes)
            conf_lines = (themes / dk / "theme.conf").read_text(encoding="utf-8").splitlines()
            menu = ip = None
            cur = None
            for line in conf_lines:
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    cur = s
                elif cur == "[Menu]" and s.startswith("NormalColor="):
                    menu = s.split("=", 1)[1]
                elif cur == "[InputPanel]" and s.startswith("NormalColor="):
                    ip = s.split("=", 1)[1]
            if menu != ip:
                errors.append(f"深色主题 {dk} Menu/NormalColor 未修复: menu={menu} ip={ip}")
        # 用户主题：theme.conf 解析五色 + 浅色判定（v1.0.14 预览数据源）
        (themes / "pearl").mkdir()
        (themes / "pearl" / "theme.conf").write_text(
            "[Metadata]\nName=珍珠白\n\n[InputPanel]\nNormalColor=#444441\n"
            "HighlightColor=#ffffff\n\n[InputPanel/Background]\nColor=#f7f5f0\n"
            "BorderColor=#d8d3c8\n\n[InputPanel/Highlight]\nColor=#dcd6c8\n",
            encoding="utf-8",
        )
        pearl = next(
            (t for t in appearance.list_themes(sys_themes, themes) if t.key == "pearl"),
            None,
        )
        if pearl is None or pearl.palette is None:
            errors.append("用户主题 pearl 枚举/配色解析异常")
        elif (
            pearl.palette.background != "#f7f5f0"
            or pearl.palette.border != "#d8d3c8"
            or pearl.palette.highlight_bg != "#dcd6c8"
            or pearl.dark is not False
        ):
            errors.append(f"用户主题五色/深浅解析异常: {pearl.palette} dark={pearl.dark}")
        # 内置素白：浅色权威值（与墨黑合起来覆盖 dark 两态）
        appearance.install_bundled_theme(
            appearance.bundled_theme("rime-installer-paper"), themes
        )
        paper = next(
            (
                t
                for t in appearance.list_themes(sys_themes, themes)
                if t.key == "rime-installer-paper"
            ),
            None,
        )
        if (
            paper is None
            or paper.dark is not False
            or paper.palette is None
            or paper.palette.background != "#ffffff"
        ):
            errors.append("内置素白应判定为浅色且配色来自权威值")

        # 图片型主题（如 deepin 系）：Color= 仅为兜底色，按它判深浅会得到
        # 相反结论（v1.0.16 真机实证：deepin-dark 被判「浅色」）。观感由图片
        # 决定，解析器应返回 palette=None/dark=None，由 UI 中性占位、不标深浅
        img_t = themes / "deepin-like"
        img_t.mkdir()
        (img_t / "theme.conf").write_text(
            "[Metadata]\nName=图片型深主题\n\n[InputPanel]\nNormalColor=#ffffff\n\n"
            "[InputPanel/Background]\nImage=panel.png\nColor=#ffffff\n",
            encoding="utf-8",
        )
        img = next(
            (t for t in appearance.list_themes(sys_themes, themes)
             if t.key == "deepin-like"),
            None,
        )
        if img is None:
            errors.append("图片型主题枚举异常")
        elif img.palette is not None or img.dark is not None:
            errors.append("图片型主题不应解析出配色/深浅（Color= 仅为兜底色）")

        backup = backups / "classicui.conf.orig"

        def run_cli(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(config.APPEARANCE_CLI), *args],
                capture_output=True,
                text=True,
                timeout=30,
            )

        # 切换主题（CLI）：Theme/DarkTheme 双写、其他行保留、备份只存最初原件
        first = run_cli(
            "--fcitx5-conf", str(conf),
            "--themes-dir", str(themes),
            "--backup", str(backup),
            "--theme", "rime-installer-deep",
        )
        if first.returncode != 0:
            errors.append(f"切换主题失败: {first.stderr[-200:]}")
            return errors
        text = conf.read_text(encoding="utf-8")
        if "Theme=rime-installer-deep" not in text or "DarkTheme=rime-installer-deep" not in text:
            errors.append("Theme/DarkTheme 应同时改为新主题")
        if 'Font="Sans 12"' not in text or "UseDarkTheme=True" not in text:
            errors.append("切换主题破坏了 classicui.conf 其他配置")
        if not backup.is_file() or "deepin-light" not in backup.read_text(encoding="utf-8"):
            errors.append("首次切换前应备份原件")
        if appearance.current_theme_key(conf) != "rime-installer-deep":
            errors.append("current_theme_key 反读异常（UseDarkTheme=True 应取 DarkTheme）")

        # 再次切换：备份仍是最初原件
        run_cli(
            "--fcitx5-conf", str(conf),
            "--themes-dir", str(themes),
            "--backup", str(backup),
            "--theme", "rime-installer-ink",
        )
        if "deepin-light" not in backup.read_text(encoding="utf-8"):
            errors.append("备份不应被第二次切换覆盖")

        # 还原：恢复最初配置
        reverted = run_cli(
            "--fcitx5-conf", str(conf), "--backup", str(backup), "--revert-theme"
        )
        if reverted.returncode != 0:
            errors.append(f"还原主题失败: {reverted.stderr[-200:]}")
        text = conf.read_text(encoding="utf-8")
        if "Theme=deepin-light" not in text or "DarkTheme=deepin-dark" not in text:
            errors.append("还原后应恢复最初 Theme/DarkTheme")

        # 未知主题 / 缺参数 → 非零退出码
        if run_cli(
            "--fcitx5-conf", str(conf),
            "--themes-dir", str(themes),
            "--backup", str(backup),
            "--theme", "nope",
        ).returncode == 0:
            errors.append("未知主题应返回非零")
        if run_cli().returncode == 0:
            errors.append("缺少参数应返回非零")

        # v1.0.24：删除用户主题（CLI 端到端）
        # 非当前主题：只删目录，conf 一动不动
        (themes / "cli-theme").mkdir()
        (themes / "cli-theme" / "theme.conf").write_text(
            "[Metadata]\nName=待删主题\n", encoding="utf-8"
        )
        removed = run_cli(
            "--fcitx5-conf", str(conf),
            "--themes-dir", str(themes),
            "--backup", str(backup),
            "--delete-theme", "cli-theme",
        )
        if removed.returncode != 0:
            errors.append(f"删除主题失败: {removed.stderr[-200:]}{removed.stdout[-200:]}")
        if "Theme=deepin-light" not in conf.read_text(encoding="utf-8"):
            errors.append("删除非当前主题不应改动 classicui.conf")
        if shutil.rmtree is _PRISTINE_RMTREE and (themes / "cli-theme").exists():
            errors.append("删除后主题目录应消失")
        # 删除当前主题：先换回「原件主题」（用户原来那套），再删目录
        conf2 = base / "classicui2.conf"
        backup2 = base / "orig2.conf"
        conf2.write_text(
            "Theme=prev-theme\nDarkTheme=prev-theme\nUseDarkTheme=True\n"
            'Font="Sans 12"\n',
            encoding="utf-8",
        )
        (themes / "prev-theme").mkdir()
        (themes / "prev-theme" / "theme.conf").write_text(
            "[Metadata]\nName=之前用的主题\n", encoding="utf-8"
        )
        (themes / "cli-theme").mkdir()
        (themes / "cli-theme" / "theme.conf").write_text(
            "[Metadata]\nName=待删主题\n", encoding="utf-8"
        )
        run_cli(
            "--fcitx5-conf", str(conf2),
            "--themes-dir", str(themes),
            "--backup", str(backup2),
            "--theme", "cli-theme",
        )
        reset = run_cli(
            "--fcitx5-conf", str(conf2),
            "--themes-dir", str(themes),
            "--backup", str(backup2),
            "--delete-theme", "cli-theme",
            "--reset-theme-to-default",
        )
        if reset.returncode != 0:
            errors.append(f"删除当前主题失败: {reset.stderr[-200:]}{reset.stdout[-200:]}")
        if appearance.current_theme_key(conf2) != "prev-theme":
            errors.append(
                "删除当前主题应回到原件主题 prev-theme，实际 "
                f"{appearance.current_theme_key(conf2)}"
            )
        if 'Font="Sans 12"' not in conf2.read_text(encoding="utf-8"):
            errors.append("删除当前主题时不应破坏 classicui.conf 其他配置")
        # 原件主题已不存在（用户删过它）→ 退回编译内置的 default
        conf3 = base / "classicui3.conf"
        backup3 = base / "orig3.conf"
        conf3.write_text(
            "Theme=ghost-theme\nDarkTheme=ghost-theme\nUseDarkTheme=True\n",
            encoding="utf-8",
        )
        (themes / "cli-theme").mkdir()
        (themes / "cli-theme" / "theme.conf").write_text(
            "[Metadata]\nName=待删主题\n", encoding="utf-8"
        )
        run_cli(
            "--fcitx5-conf", str(conf3),
            "--themes-dir", str(themes),
            "--backup", str(backup3),
            "--theme", "cli-theme",
        )
        reset = run_cli(
            "--fcitx5-conf", str(conf3),
            "--themes-dir", str(themes),
            "--backup", str(backup3),
            "--delete-theme", "cli-theme",
            "--reset-theme-to-default",
        )
        if reset.returncode != 0:
            errors.append(f"删除当前主题失败: {reset.stderr[-200:]}{reset.stdout[-200:]}")
        if appearance.current_theme_key(conf3) != appearance.DEFAULT_THEME_KEY:
            errors.append(
                "原件主题不存在时应退回 default，实际 "
                f"{appearance.current_theme_key(conf3)}"
            )
        # 越界 / 内置 / 不存在的主题：一律拒绝，且不得动别的目录
        if run_cli(
            "--themes-dir", str(themes), "--delete-theme", "../escape"
        ).returncode == 0:
            errors.append("越界主题名应被拒绝")
        if run_cli(
            "--themes-dir", str(themes), "--delete-theme", "rime-installer-ink"
        ).returncode == 0:
            errors.append("内置主题应拒绝删除")
        if run_cli(
            "--themes-dir", str(themes), "--delete-theme", "no-such-theme"
        ).returncode == 0:
            errors.append("不存在的主题应返回非零")

        # 直接调用层：合法 key 才删，且只删「有 theme.conf 的直接子目录」
        try:
            appearance.delete_user_theme("pearl", themes)
        except (OSError, ValueError) as exc:
            errors.append(f"delete_user_theme 正常用例失败: {exc}")
        if shutil.rmtree is _PRISTINE_RMTREE and (themes / "pearl").exists():
            errors.append("delete_user_theme 后 pearl 目录应消失")
        for bad_key in (
            "", ".", "..", "../escape", "/etc", "a/b",
            "default", "default-dark",
            "rime-installer-ink", "rime-installer-paper", "rime-installer-deep",
        ):
            try:
                appearance.delete_user_theme(bad_key, themes)
            except (ValueError, FileNotFoundError):
                continue
            errors.append(f"delete_user_theme 应拒绝: {bad_key!r}")
        junk = themes / "not-a-theme"
        junk.mkdir()
        (junk / "readme.txt").write_text("x", encoding="utf-8")
        try:
            appearance.delete_user_theme("not-a-theme", themes)
        except ValueError:
            pass
        else:
            errors.append("目录里没有 theme.conf 时应拒绝删除")
        if not junk.is_dir():
            errors.append("拒绝删除时不应动该目录")
        try:
            appearance.delete_user_theme("no-such-theme", themes)
        except FileNotFoundError:
            pass
        else:
            errors.append("不存在的主题应抛 FileNotFoundError")

        # ibus 外观：写入（保留用户配置）→ 反读 → 幂等 → 移除
        style = appearance.IbusStyle(horizontal=True, inline_preedit=False, color_scheme="ink")
        applied = run_cli(
            "--ibus-conf", str(rime_dir),
            "--ibus-style", "horizontal=true,inline_preedit=false,color_scheme=ink",
        )
        if applied.returncode != 0:
            errors.append(f"写入 ibus 外观失败: {applied.stderr[-200:]}")
            return errors
        text = (rime_dir / "ibus_rime.custom.yaml").read_text(encoding="utf-8")
        if "schema: luna_pinyin" not in text:
            errors.append("写入 ibus 外观破坏了用户既有配置")
        if text.count(appearance.IBUS_MARK_BEGIN) != 1:
            errors.append("ibus 标记块应恰好一对")
        if appearance.detect_ibus_style(rime_dir) != style:
            errors.append(f"ibus 外观反读不一致: {appearance.detect_ibus_style(rime_dir)}")
        run_cli(
            "--ibus-conf", str(rime_dir),
            "--ibus-style", "horizontal=true,inline_preedit=false,color_scheme=ink",
        )
        text = (rime_dir / "ibus_rime.custom.yaml").read_text(encoding="utf-8")
        if text.count(appearance.IBUS_MARK_BEGIN) != 1:
            errors.append("重复写入 ibus 外观后标记块仍应恰好一对（幂等）")
        removed = run_cli("--ibus-conf", str(rime_dir), "--revert-ibus-style")
        if removed.returncode != 0:
            errors.append(f"移除 ibus 外观失败: {removed.stderr[-200:]}")
        text = (rime_dir / "ibus_rime.custom.yaml").read_text(encoding="utf-8")
        if appearance.IBUS_MARK_BEGIN in text:
            errors.append("移除后 ibus 标记块应消失")
        if "schema: luna_pinyin" not in text:
            errors.append("移除 ibus 外观破坏了用户既有配置")
        if appearance.detect_ibus_style(rime_dir) is not None:
            errors.append("移除后 detect_ibus_style 应返回 None")

        # 状态机规则
        from core.detector import EnvReport
        from core.state_machine import StateMachine

        if not StateMachine(EnvReport(recommended_frontend="fcitx5-rime")).rule(
            "appearance_theme"
        ).enabled:
            errors.append("appearance_theme 规则应可用")
        if StateMachine(EnvReport(recommended_frontend="ibus-rime")).rule(
            "appearance_ibus"
        ).enabled:
            errors.append("无 rime 目录时 appearance_ibus 应禁用")
        if not StateMachine(
            EnvReport(recommended_frontend="ibus-rime", rime_user_dir=rime_dir)
        ).rule("appearance_ibus").enabled:
            errors.append("有 rime 目录时 appearance_ibus 应可用")
        if StateMachine(EnvReport(), busy=True).rule("appearance_theme").enabled:
            errors.append("busy 时外观规则应禁用")

        # v1.0.16：切换主题含「重启 fcitx5」步（classicui 主题进程启动加载，
        # 只改 classicui.conf 不重启 → 候选窗毫无变化，用户反馈「无法切换」根因）
        theme_steps = appearance.build_theme_steps(
            "rime-installer-deep", conf, themes, backup
        )
        if len(theme_steps) != 2:
            errors.append(f"build_theme_steps 应含 2 步（切换+重启），实际 {len(theme_steps)}")
        elif "bash" not in theme_steps[1].argv or "fcitx5" not in " ".join(theme_steps[1].argv):
            errors.append(f"第二步应为重启 fcitx5 步: {theme_steps[1].argv}")
        # 还原同样改写 classicui.conf，必须重启 fcitx5 才生效（与切换同款）
        revert_steps = appearance.build_theme_revert_steps(conf, backup)
        if len(revert_steps) != 2:
            errors.append(f"build_theme_revert_steps 应含 2 步（还原+重启），实际 {len(revert_steps)}")
        elif "bash" not in revert_steps[1].argv or "fcitx5" not in " ".join(revert_steps[1].argv):
            errors.append(f"还原第二步应为重启 fcitx5 步: {revert_steps[1].argv}")
        # v1.0.24：删除步——删当前主题 = 2 步（切回 default + 重启 fcitx5），
        # 删其他主题 = 1 步（只删目录，不打断用户正在用的候选窗）
        del_steps = appearance.build_theme_delete_steps(
            "pearl", conf, themes, backup, reset_theme=True
        )
        if len(del_steps) != 2:
            errors.append(f"删除当前主题应含 2 步（切回+重启），实际 {len(del_steps)}")
        elif (
            del_steps[0].argv.count("--delete-theme") != 1
            or "--reset-theme-to-default" not in del_steps[0].argv
        ):
            errors.append(f"删除步 argv 异常: {del_steps[0].argv}")
        plain_steps = appearance.build_theme_delete_steps(
            "pearl", conf, themes, backup, reset_theme=False
        )
        if len(plain_steps) != 1:
            errors.append(f"删除非当前主题应只 1 步，实际 {len(plain_steps)}")
        elif "--reset-theme-to-default" in plain_steps[0].argv:
            errors.append("非当前主题不应触发「切回 default」")

        # i18n 键（中英双包）
        from core import i18n
        from core.translations.en import EN_TEXT

        for key in (
            "appearance.title",
            "appearance.subtitle",
            "appearance.section_fcitx5",
            "appearance.section_ibus",
            "appearance.theme_switch",
            "appearance.theme_revert",
            "appearance.open_themes_dir",
            "appearance.tone_dark",
            "appearance.tone_light",
            "appearance.ibus_horizontal",
            "appearance.ibus_inline_preedit",
            "appearance.ibus_preedit_color",
            "appearance.ibus_apply",
            "appearance.ibus_revert",
            "appearance.theme_delete_tip",
            "appearance.delete_title",
            "appearance.delete_current_warn",
            "msg.theme_deleted",
            "nav.appearance",
            "msg.theme_switched",
            "msg.theme_reverted",
            "msg.ibus_style_applied",
            "msg.ibus_style_reverted",
        ):
            if key not in i18n.ZH_TEXT:
                errors.append(f"中文源缺少外观文案键: {key}")
            if key not in EN_TEXT:
                errors.append(f"英文包缺少外观文案键: {key}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_theme() -> list[str]:
    """主题核心：偏好持久化往返、auto 解析、apply 换色板（Phase 6）。"""
    errors: list[str] = []
    from ui.pages import base

    original = theme.load_preference()
    try:
        for mode in theme.PREFERENCES:
            theme.save_preference(mode)
            if theme.load_preference() != mode:
                errors.append(f"主题偏好持久化往返失败: {mode}")

        if theme.resolve_mode(theme.LIGHT) != "light":
            errors.append("resolve_mode(light) 应为 light")
        if theme.resolve_mode(theme.DARK) != "dark":
            errors.append("resolve_mode(dark) 应为 dark")
        if theme.resolve_mode(theme.AUTO) not in ("light", "dark"):
            errors.append(f"resolve_mode(auto) 异常: {theme.resolve_mode(theme.AUTO)}")

        if theme.apply_theme(theme.LIGHT) != "light":
            errors.append("apply_theme(light) 应返回 light")
        if base.STATUS_COLORS != base.STATUS_COLORS_LIGHT:
            errors.append("浅色主题状态色板未应用")
        if theme.apply_theme(theme.DARK) != "dark":
            errors.append("apply_theme(dark) 应返回 dark")
        if base.STATUS_COLORS != base.STATUS_COLORS_DARK:
            errors.append("深色主题状态色板未应用")
        theme.apply_theme(theme.AUTO)  # 不得崩溃
        if base.STATUS_COLORS not in (base.STATUS_COLORS_LIGHT, base.STATUS_COLORS_DARK):
            errors.append("auto 应用后色板异常")
        if theme.label(theme.AUTO) != "跟随系统":
            errors.append(f"主题展示名异常: {theme.label(theme.AUTO)}")
    finally:
        theme.save_preference(original)
        theme.apply_theme(original)
    return errors


def check_animation() -> list[str]:
    """动效工具 + 进度区 mixin（Phase 6b）。

    覆盖：等待态条纹切换、数值补间、动画跑完后 stop 不炸（DeleteWhenStopped
    回归防线）、复位归零、显隐门控。
    """
    errors: list[str] = []
    from PyQt6.QtCore import QAbstractAnimation, QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from ui.animation import animate_progress, stop_progress_animation
    from ui.pages.base import ProgressAreaMixin

    app = QApplication.instance()

    class _Probe(ProgressAreaMixin):
        """最小宿主：Mixin 只依赖 card_layout。"""

        def __init__(self) -> None:
            self._host = QWidget()
            self.card_layout = QVBoxLayout(self._host)
            self._init_progress_area(self.card_layout)

    probe = _Probe()
    bar = probe.progress_bar

    # 说明：用 isHidden() 而非 isVisible()。isVisible() 受祖先窗口可见性牵连，
    # offscreen 下页面未必处于 stacked widget 的当前页，断言会假失败。
    # 1) 等待态：条纹显示、确定条隐藏
    probe.enter_waiting()
    if probe.waiting_bar.isHidden() or not bar.isHidden():
        errors.append("enter_waiting 未正确进入等待态")

    # 2) 首个 >0 百分比：自动切回确定条并建立补间
    probe.set_progress(50)
    if not probe.waiting_bar.isHidden() or bar.isHidden():
        errors.append("收到进度后未从等待态切出")
    ani = getattr(bar, "_value_ani", None)
    if ani is None or ani.endValue() != 50:
        errors.append(f"进度动画未建立或目标值错误: {ani}")

    # 3) 动画自然跑完后 stop 不得抛 RuntimeError（DeleteWhenStopped 回归防线）
    if ani is not None:
        loop = QEventLoop()
        ani.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)  # 兜底，防事件循环挂死
        if ani.state() == QAbstractAnimation.State.Running:
            loop.exec()
    try:
        stop_progress_animation(bar)
    except RuntimeError as exc:  # C++ 对象已被删除的典型症状
        errors.append(f"动画结束后 stop 崩溃: {exc}")
    if bar.value() != 50:
        errors.append(f"动画结束后数值应停在 50，实际 {bar.value()}")

    # 4) 二次补间可打断前一次
    animate_progress(bar, 90)
    if getattr(bar, "_value_ani", None) is None:
        errors.append("二次补间未建立")
    probe.reset_progress()
    if bar.value() != 0:
        errors.append(f"reset_progress 未归零: {bar.value()}")
    if not probe.waiting_bar.isHidden() or getattr(bar, "_value_ani", None) is not None:
        errors.append("reset_progress 未清干净（条纹仍显示或动画残留）")

    # 5) 显隐门控
    probe.enter_waiting()
    probe._show_progress_area(False)
    if not bar.isHidden() or not probe.waiting_bar.isHidden():
        errors.append("_show_progress_area(False) 未隐藏两条")
    probe._show_progress_area(True)
    if probe.waiting_bar.isHidden():
        errors.append("_show_progress_area(True) 等待态应显示条纹")

    probe._host.deleteLater()
    return errors


def check_skin_ui() -> list[str]:
    """皮肤卡片（F15）：隐藏检查更新、应用/移除语义、已应用徽标；普通卡不受影响。"""
    errors: list[str] = []
    from core import i18n
    from core.schemes import SCHEME_MAP
    from ui.pages.scheme_page import SchemeCard

    original = i18n.current()
    i18n.set_language(i18n.ZH)
    try:
        card = SchemeCard(SCHEME_MAP["addon:skin-ink"])
        card.apply_rules(StateMachine(_report_with_engine()))
        if not card.check_update_button.isHidden():
            errors.append("皮肤卡应隐藏检查更新按钮")
        if card.install_button.text() != "切换到此皮肤":
            errors.append(f"皮肤卡安装按钮文案异常: {card.install_button.text()}")
        if "default.custom.yaml" not in (card.install_button.toolTip() or ""):
            errors.append("皮肤卡安装 tooltip 应说明写入用户目录")
        if "替换" not in (card.install_button.toolTip() or ""):
            errors.append("皮肤卡安装 tooltip 应说明切换会替换当前皮肤")
        if card.uninstall_button.text() != "移除皮肤":
            errors.append(f"皮肤卡卸载按钮文案异常: {card.uninstall_button.text()}")
        if card.set_default_button.isHidden():
            errors.append("皮肤卡应显示设为默认按钮")
        if card.set_default_button.text() != "设为默认":
            errors.append(f"设为默认按钮文案异常: {card.set_default_button.text()}")
        # 已安装但非当前：徽标「已应用」，按钮仍是切换语义
        card.set_installed(True)
        card.apply_rules(StateMachine(_report_with_engine()))
        if card.install_button.text() != "切换到此皮肤":
            errors.append(f"已安装皮肤卡按钮文案异常: {card.install_button.text()}")
        if card.installed_label.text() != "✓ 已应用":
            errors.append(f"已应用徽标异常: {card.installed_label.text()}")
        # 当前生效：徽标「当前皮肤」，按钮改「重新部署」
        card.set_skin_state(active=True, default=False)
        card.apply_rules(StateMachine(_report_with_engine()))
        if card.install_button.text() != "重新部署":
            errors.append(f"当前皮肤卡按钮文案异常: {card.install_button.text()}")
        if card.installed_label.text() != "当前皮肤":
            errors.append(f"当前皮肤徽标异常: {card.installed_label.text()}")
        if "替换" not in (card.install_button.toolTip() or ""):
            errors.append("当前皮肤卡 tooltip 应说明切换会替换掉它")
        # 当前 + 我的默认：徽标叠加
        card.set_skin_state(active=True, default=True)
        if card.installed_label.text() != "当前皮肤 · 我的默认":
            errors.append(f"当前+默认徽标异常: {card.installed_label.text()}")
        # 仅默认（非当前生效）：徽标「已应用 · 我的默认」
        card.set_skin_state(active=False, default=True)
        if card.installed_label.text() != "✓ 已应用 · 我的默认":
            errors.append(f"仅默认徽标异常: {card.installed_label.text()}")

        normal = SchemeCard(SCHEME_MAP["official:preset"])
        if normal.check_update_button.isHidden():
            errors.append("普通方案卡不应隐藏检查更新按钮")
        if normal.install_button.text() != "安装":
            errors.append(f"普通方案卡文案异常: {normal.install_button.text()}")
        if normal.uninstall_button.text() != "卸载":
            errors.append(f"普通方案卡卸载文案异常: {normal.uninstall_button.text()}")
        if not normal.set_default_button.isHidden():
            errors.append("普通方案卡不应显示设为默认按钮")
        card.deleteLater()
        normal.deleteLater()
    finally:
        i18n.set_language(original)
    return errors


def _wait_runner(runner, timeout_ms=30000) -> dict:
    """在事件循环里等 runner 自然结束（返回 {"ok","code"}，超时 ok=None）。"""
    from PyQt6.QtCore import QEventLoop, QTimer

    state: dict = {"ok": None, "code": None}
    loop = QEventLoop()

    def _fin(ok: bool, code: int) -> None:
        state["ok"], state["code"] = ok, code
        loop.quit()

    runner.finished.connect(_fin)
    QTimer.singleShot(timeout_ms, loop.quit)  # 兜底，防挂死
    loop.exec()
    runner.finished.disconnect(_fin)
    return state


def check_skin_flow() -> list[str]:
    """皮肤端到端（F15 / v1.0.12）：主窗口接线 → runner 跑 CLI → 记账 →
    切换互斥（配置文件只有一套块）但已安装清单保留 → 设为默认/恢复默认 →
    卸载（当前生效款动配置、非当前款仅除名；移除默认皮肤清偏好）。

    覆盖 _start_scheme_install 皮肤分支、_commit_skin_apply 记账、
    _on_skin_set_default / _on_skin_restore_default、_on_scheme_uninstall
    皮肤分支（confirm 对话框在 offscreen 下 monkeypatch）。
    """
    errors: list[str] = []
    from core.skin import MARK_BEGIN, MARK_END, detect_active_skin
    from ui.main_window import MainWindow
    from ui.pages.scheme_page import SchemePage

    base = _tmp_dir("rime-skin-flow-")
    original_confirm = SchemePage.confirm_uninstall
    SchemePage.confirm_uninstall = staticmethod(lambda scheme, parent=None: True)
    try:
        rime_dir = base / "rime"
        rime_dir.mkdir()
        target = rime_dir / "default.custom.yaml"
        user_config = "patch:\n  schema_list:\n    - schema: luna_pinyin\n"
        target.write_text(user_config, encoding="utf-8")

        report = _report_with_engine()
        report.rime_user_dir = rime_dir
        manifest = Manifest(base / "m.db", base / "snap")
        window = MainWindow(report, manifest)

        # _on_task_finished 会调 redetect() 做真实环境探测，会把假报告覆盖成
        # 真实机器的 rime 目录（本机不存在 → CLI 退出码 2）。钉住假报告，
        # 只重建状态机与分发，保持其余接线不变。
        def _pinned_redetect() -> None:
            window.report = report
            window._refresh_installed()
            window.sm = StateMachine(
                report,
                busy=window.runner.running,
                installed_schemes=len(window._installed_keys),
            )
            window._dispatch()

        window.redetect = _pinned_redetect
        # v1.0.7：任务结束改走 redetect_async（后台线程真实探测）——不钉住会把
        # 假报告覆盖成真实机器环境，后续卸载任务的 revert 会写坏真实用户目录
        window.redetect_async = _pinned_redetect

        # 1) 应用墨黑
        window._on_scheme_install("addon:skin-ink")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"皮肤应用任务失败: {state}")
            return errors
        text = target.read_text(encoding="utf-8")
        if MARK_BEGIN not in text or "schema: luna_pinyin" not in text:
            errors.append("应用后应含标记块且保留用户配置")
        if "addon:skin-ink" not in manifest.installed_keys():
            errors.append("皮肤应用后未记账")
        if "addon:skin-ink" not in window._installed_keys:
            errors.append("皮肤应用后窗口已安装集合未刷新")

        # 2) 切换深海：文件里仍只有一对标记块；「已安装」与「当前生效」解耦——
        #    墨黑保留在已安装清单（供皮肤分区快捷切换条），当前生效的是深海
        window._on_scheme_install("addon:skin-deep")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"皮肤切换任务失败: {state}")
            return errors
        text = target.read_text(encoding="utf-8")
        if text.count(MARK_BEGIN) != 1 or "0x432a10" not in text:
            errors.append("切换皮肤后应只剩一对新皮肤标记块")
        if "addon:skin-deep" not in manifest.installed_keys():
            errors.append("新皮肤未记账")
        if "addon:skin-ink" not in manifest.installed_keys():
            errors.append("切换后旧皮肤应保留在已安装清单（解耦）")
        if window._active_skin_key != "addon:skin-deep":
            errors.append(f"切换后窗口当前皮肤 key 异常: {window._active_skin_key}")
        active = detect_active_skin(rime_dir)
        if active is None or active.key != "skin-deep":
            errors.append(f"切换后 detect_active_skin 应为深海: {active}")

        # 2b) 设为默认（墨黑）→ 恢复我的默认：复用应用管线切回墨黑
        window._on_skin_set_default("addon:skin-ink")
        if manifest.get_default_skin() != "addon:skin-ink":
            errors.append("设为默认后偏好未记录")
        if window._active_skin_key != "addon:skin-deep":
            errors.append("设为默认不应改变当前生效皮肤")
        window._on_skin_restore_default()
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"恢复默认皮肤任务失败: {state}")
            return errors
        active = detect_active_skin(rime_dir)
        if active is None or active.key != "skin-ink":
            errors.append(f"恢复默认后当前皮肤应为墨黑: {active}")
        if "0x000000" not in target.read_text(encoding="utf-8"):
            errors.append("恢复默认后应写回墨黑色值")

        # 3) 卸载深海（非当前生效款）：清单除名即可，不动配置文件、不起任务
        window._on_scheme_uninstall("addon:skin-deep")
        if window.runner.running:
            errors.append("移除非当前皮肤不应启动任务")
        if "addon:skin-deep" in manifest.installed_keys():
            errors.append("皮肤移除后记账未清除")
        if MARK_BEGIN not in target.read_text(encoding="utf-8"):
            errors.append("移除非当前皮肤不应破坏当前配置块")

        # 4) 卸载墨黑（当前生效 + 我的默认）：移除配置块、清偏好、重新部署
        window._on_scheme_uninstall("addon:skin-ink")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"皮肤移除任务失败: {state}")
            return errors
        text = target.read_text(encoding="utf-8")
        if MARK_BEGIN in text or MARK_END in text:
            errors.append("移除后标记块应彻底消失")
        if "schema: luna_pinyin" not in text:
            errors.append("移除皮肤破坏了用户既有配置")
        if "addon:skin-ink" in manifest.installed_keys():
            errors.append("皮肤移除后记账未清除")
        if manifest.get_default_skin() != "":
            errors.append("移除默认皮肤后偏好应清除")
        if detect_active_skin(rime_dir) is not None:
            errors.append("移除后 detect_active_skin 应返回 None")
        window.close()
        manifest.close()
    finally:
        SchemePage.confirm_uninstall = original_confirm
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_appearance_ui() -> list[str]:
    """外观页（v1.0.13 / v1.0.19）：分区按前端显隐、主题卡（预览/深浅/当前态/
    标签页分栏/系统主题隐藏）、鸣谢区、ibus 控件回填、busy 协议。"""
    errors: list[str] = []
    from core import appearance, i18n
    from core.state_machine import StateMachine
    from ui.pages import appearance_page as appearance_page_mod
    from ui.pages.appearance_page import AppearancePage

    # 路径注入：主题枚举/当前主题/备份全部重定向到临时目录（防污染真机）
    base = _tmp_dir("appearance-ui-")
    themes_dir = base / "themes"
    sys_dir = base / "sys-themes"
    themes_dir.mkdir()
    sys_dir.mkdir()
    conf_file = base / "classicui.conf"
    conf_file.write_text(
        "Theme=rime-installer-ink\nDarkTheme=rime-installer-ink\nUseDarkTheme=True\n",
        encoding="utf-8",
    )
    # 内置墨黑（BUNDLED_THEMES 权威五色，深色）
    appearance.install_bundled_theme(
        appearance.bundled_theme("rime-installer-ink"), themes_dir
    )
    # 用户手动安装的浅色主题（theme.conf 解析五色）
    (themes_dir / "pearl").mkdir()
    (themes_dir / "pearl" / "theme.conf").write_text(
        "[Metadata]\nName=珍珠白\n\n[InputPanel]\nNormalColor=#444441\n"
        "HighlightColor=#ffffff\n\n[InputPanel/Background]\nColor=#f7f5f0\n"
        "BorderColor=#d8d3c8\n\n[InputPanel/Highlight]\nColor=#dcd6c8\n",
        encoding="utf-8",
    )
    # v1.0.24：超长主题名（真实形态：catppuccin 的长目录名 + 长中文 Name）——
    # 名字不得把卡片撑宽（否则网格从两列挤成一列），应省略并保留 tooltip 全文
    long_key = "catppuccin-mocha-blue-extra"
    long_name = "非常非常长的中文主题名称测试样例"
    (themes_dir / long_key).mkdir()
    (themes_dir / long_key / "theme.conf").write_text(
        f"[Metadata]\nName={long_name}\n\n[InputPanel/Background]\nColor=#202020\n",
        encoding="utf-8",
    )
    # 系统主题（无配色字段 → 中性占位、不标深浅）
    (sys_dir / "fake-dark").mkdir()
    (sys_dir / "fake-dark" / "theme.conf").write_text(
        "[Metadata]\nName=假暗主题\n", encoding="utf-8"
    )
    path_originals = (
        appearance.fcitx5_conf_path,
        appearance.user_themes_dir,
        appearance.system_themes_dir,
        appearance.theme_backup_path,
    )
    appearance.fcitx5_conf_path = lambda: conf_file
    appearance.user_themes_dir = lambda: themes_dir
    appearance.system_themes_dir = lambda: sys_dir
    appearance.theme_backup_path = lambda: base / "backup.orig"

    original = i18n.current()
    i18n.set_language(i18n.ZH)
    try:
        # fcitx5 报告：只显示主题分区
        report = EnvReport(recommended_frontend="fcitx5-rime")
        page = AppearancePage(report, StateMachine(report))
        page.bind(report, StateMachine(report))
        if page.fcitx5_label.isHidden():
            errors.append("fcitx5 报告应显示主题分区")
        if not page.ibus_label.isHidden():
            errors.append("fcitx5 报告应隐藏 ibus 分区")
        # v1.0.19：当前主题应有对应卡片，且卡片处于「当前使用」态
        if page._current_theme and page._current_theme not in page._cards:
            errors.append("当前主题应有一张卡片")
        current_card = page._cards.get(page._current_theme)
        if current_card is not None:
            if current_card.current_tag.isHidden():
                errors.append("当前主题卡应显示「当前使用」徽标")
            if current_card.switch_button.isVisible():
                errors.append("当前主题卡的切换按钮应隐藏")
            if current_card.switch_button.isEnabled():
                errors.append("当前主题卡的切换按钮应禁用")
        for key, card in page._cards.items():
            if key == page._current_theme:
                continue
            if not card.switch_button.isEnabled():
                errors.append("非当前主题卡的切换按钮应可用")
            if card.current_tag.isVisible():
                errors.append("非当前主题卡不应显示「当前使用」徽标")
        # v1.0.16：移除市场，保留「打开主题目录」按钮（fcitx5 可见/可用）
        if page.open_dir_button.isHidden():
            errors.append("fcitx5 报告应显示「打开主题目录」按钮")
        if not page.open_dir_button.isEnabled():
            errors.append("fcitx5 报告「打开主题目录」按钮应可用")
        # v1.0.18：新增「刷新主题目录」按钮（无需重启即可发现新放入的主题）
        if page.refresh_button.isHidden():
            errors.append("fcitx5 报告应显示「刷新主题目录」按钮")
        if not page.refresh_button.isEnabled():
            errors.append("fcitx5 报告「刷新主题目录」按钮应可用")
        # v1.0.19：卡片网格 + 候选窗预览 + 标签页分栏 + 系统主题不列出
        cards = page._cards
        if page.tabs.currentRouteKey() != "builtin":
            errors.append("默认应停在「内置主题」标签页")
        if not page.custom_host.isHidden():
            errors.append("默认应隐藏「我的主题」页")
        for key, card in cards.items():
            if card.height() != appearance_page_mod._CARD_HEIGHT:
                errors.append(f"主题卡高度异常: {key} -> {card.height()}")
            if card.preview.width() < 200:
                errors.append(f"主题卡预览宽度异常: {key}")
        # 内置完整主题（Mellow）应进「内置主题」标签页，且预览取到真实五色
        mellow = cards.get("mellow-graphite")
        if mellow is None:
            errors.append("内置完整主题 mellow-graphite 应出现在主题卡")
        elif mellow.preview.palette is None or not mellow.preview.palette.background:
            errors.append("内置完整主题 mellow-graphite 预览应非空")
        if "mellow-graphite" not in [t.key for t in page._builtin_themes]:
            errors.append("mellow-graphite 应归入内置主题")
        ink = cards.get("rime-installer-ink")
        if ink is None:
            errors.append("内置墨黑应出现在主题卡")
        else:
            if ink.preview.palette.background != "#141414":
                errors.append("内置墨黑预览应取权威底色 #141414")
            if ink.tone_tag is None or i18n.tr("appearance.tone_dark") not in ink.tone_tag.text():
                errors.append("内置墨黑应标「深色」")
            if ink.key_label.text() != "rime-installer-ink":
                errors.append(f"key 行异常: {ink.key_label.text()}")
        # 用户自装主题归「我的主题」，系统主题（fake-dark）不进界面
        pearl = cards.get("pearl")
        if pearl is None:
            errors.append("用户主题 pearl 应出现在主题卡")
        else:
            if pearl.preview.palette.background != "#f7f5f0":
                errors.append("pearl 预览应取解析底色 #f7f5f0")
            if pearl.tone_tag is None or i18n.tr("appearance.tone_light") not in pearl.tone_tag.text():
                errors.append("pearl 应标「浅色」")
        if "pearl" not in [t.key for t in page._custom_themes]:
            errors.append("pearl 应归入我的主题")
        # v1.0.24：超长主题名走省略号，不得靠撑宽卡片来容纳
        long_card = cards.get(long_key)
        if long_card is None:
            errors.append("超长名主题应出现在主题卡")
        else:
            if not isinstance(
                long_card.name_label, appearance_page_mod._ElidedLabel
            ):
                errors.append("主题名应使用 _ElidedLabel（长名会撑宽卡片）")
            elif long_card.name_label._full_text != long_name:
                errors.append("省略标签应保留完整主题名")
            if long_card.name_label.toolTip() != long_name:
                errors.append("省略标签的 tooltip 应为完整主题名")
            if long_card.name_label.width() > long_card.width():
                errors.append("主题名宽度不应超过卡片宽度")
        if pearl is not None and pearl.name_label.toolTip() != "珍珠白":
            errors.append("主题名标签的 tooltip 应为完整名称")
        if "fake-dark" in cards:
            errors.append("系统自带主题不应出现在卡片列表（v1.0.19 起隐藏）")
        if not page.custom_empty.isHidden():
            errors.append("有自定义主题时应隐藏空状态")
        # 切换标签页：内容互换
        page.tabs.setCurrentItem("custom")
        if page.custom_host.isHidden():
            errors.append("切到「我的主题」后应显示其内容")
        if not page.builtin_host.isHidden():
            errors.append("切到「我的主题」后应隐藏内置列表")
        page.tabs.setCurrentItem("builtin")
        if page.builtin_host.isHidden():
            errors.append("切回「内置主题」后应恢复显示")
        # 点「切换」按钮发意图（当前主题卡无按钮）
        emitted: list[str] = []
        page.theme_switch_requested.connect(emitted.append)
        other_key = next(k for k in cards if k != page._current_theme)
        cards[other_key].switch_button.click()
        if emitted != [other_key]:
            errors.append(f"切换按钮应发出主题 key: {emitted}")
        # v1.0.24：删除按钮只出现在「我的主题」——内置主题随安装包发布，删了
        # 下次启动又回来，给按钮只会让用户白忙（当前主题**允许**删除）
        if not ink.delete_button.isHidden():
            errors.append("内置主题卡不应显示删除按钮")
        if pearl.delete_button.isHidden():
            errors.append("用户主题卡应显示删除按钮")
        elif not pearl.delete_button.isEnabled():
            errors.append("非 busy 时用户主题删除按钮应可用")
        original_confirm_delete = AppearancePage.confirm_delete
        AppearancePage.confirm_delete = staticmethod(lambda *a, **k: True)
        try:
            deletes: list[str] = []
            switches_before = len(emitted)
            page.theme_delete_requested.connect(deletes.append)
            ink.delete_button.click()  # 隐藏 + deletable=False：必须一声不吭
            if deletes:
                errors.append(f"内置主题不应发出删除意图: {deletes}")
            pearl.delete_button.click()
            if deletes != ["pearl"]:
                errors.append(f"删除按钮应发出主题 key: {deletes}")
            if len(emitted) != switches_before:
                errors.append("点删除按钮不应同时触发切换（事件不得冒泡到整卡）")
            # 确认框取消 → 不得发出删除意图
            AppearancePage.confirm_delete = staticmethod(lambda *a, **k: False)
            pearl.delete_button.click()
            if deletes != ["pearl"]:
                errors.append(f"取消确认后不应再发删除意图: {deletes}")
        finally:
            AppearancePage.confirm_delete = staticmethod(original_confirm_delete)
        # 鸣谢区：作者/仓库署名 + 按钮发链接
        if "sanweiya" not in page.thanks_body.text():
            errors.append("鸣谢文案应包含上游作者 sanweiya")
        if appearance.BUNDLED_THEMES_REPO not in page.thanks_button.toolTip():
            errors.append("鸣谢按钮 tooltip 应带上游仓库地址")
        links: list[str] = []
        page.link_requested.connect(links.append)
        page.thanks_button.click()
        if links != [appearance.BUNDLED_THEMES_REPO]:
            errors.append(f"鸣谢按钮应发出仓库链接: {links}")
        # busy 协议：全部禁用、取消可用
        page.set_busy(True, "切换中")
        if not page.cancel_button.isEnabled():
            errors.append("busy 时应启用取消按钮")
        if any(card.switch_button.isEnabled() for card in page._cards.values()):
            errors.append("busy 时主题切换按钮应全部禁用")
        if any(card.delete_button.isEnabled() for card in page._cards.values()):
            errors.append("busy 时主题删除按钮应全部禁用")
        page.set_busy(False)

        # v1.0.21：从链接导入主题——输入框/按钮可用性 + prefill 跨页联动 + busy 禁用
        if page.import_input.isHidden():
            errors.append("fcitx5 报告应显示主题导入输入框")
        if page.import_button.isEnabled():
            errors.append("输入框为空时导入按钮应禁用")
        page.import_input.setText("not a repo url")
        if page.import_button.isEnabled():
            errors.append("非法输入时导入按钮应禁用")
        page.prefill_import("https://github.com/thep0y/fcitx5-themes-candlelight")
        if page.tabs.currentRouteKey() != "custom":
            errors.append("prefill_import 应切到「我的主题」标签页")
        if not page.import_button.isEnabled():
            errors.append("合法链接应启用导入按钮")
        import_specs: list[str] = []
        page.theme_import_requested.connect(import_specs.append)
        page.import_button.click()
        if import_specs != ["https://github.com/thep0y/fcitx5-themes-candlelight"]:
            errors.append(f"导入按钮应发出原始链接: {import_specs}")
        page.set_busy(True, "下载中")
        if page.import_input.isEnabled() or page.import_button.isEnabled():
            errors.append("busy 时导入输入框与按钮应禁用")
        page.set_busy(False)
        page.clear_import_input()
        if page.import_button.isEnabled():
            errors.append("清空输入后导入按钮应禁用")

        # 空主题目录：我的主题标签页显示空状态（零删除：新建空目录重绑）
        empty_themes = _tmp_dir("appearance-ui-nothemes-")
        try:
            appearance.user_themes_dir = lambda: empty_themes
            page_empty = AppearancePage(report, StateMachine(report))
            if page_empty.custom_empty.isHidden():
                errors.append("无自定义主题时应显示空状态")
            if not page_empty.custom_grid.isHidden():
                errors.append("无自定义主题时不应显示卡片网格")
            if page_empty._custom_themes:
                errors.append(
                    f"空目录下我的主题应为空: {[t.key for t in page_empty._custom_themes]}"
                )
            page_empty.deleteLater()
        finally:
            appearance.user_themes_dir = lambda: themes_dir
            shutil.rmtree(empty_themes, ignore_errors=True)

        # ibus 报告：只显示 ibus 分区 + 控件回填
        rime_dir = _tmp_dir("appearance-ui-")
        try:
            (rime_dir / "ibus_rime.custom.yaml").write_text(
                "patch:\n"
                + appearance.render_ibus_block(
                    appearance.IbusStyle(
                        horizontal=True, inline_preedit=False, color_scheme="azure"
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            report2 = EnvReport(recommended_frontend="ibus-rime", rime_user_dir=rime_dir)
            page2 = AppearancePage(report2, StateMachine(report2))
            if not page2.fcitx5_label.isHidden():
                errors.append("ibus 报告应隐藏主题分区")
            if page2.ibus_label.isHidden():
                errors.append("ibus 报告应显示 ibus 分区")
            if not page2.horizontal_switch.isChecked():
                errors.append("横排开关应回填为开")
            if page2.inline_switch.isChecked():
                errors.append("内嵌预编辑开关应回填为关")
            if page2.preedit_combo.currentData() != "azure":
                errors.append(f"preedit 下拉应回填 azure: {page2.preedit_combo.currentData()}")
            if not page2.ibus_revert_button.isEnabled():
                errors.append("有配置块时恢复默认按钮应可用")
            # 无样式块 → 回填 ibus 默认，恢复默认禁用
            # （零删除：另建空目录重绑，不 unlink——宿主 safe-delete 钩子按轮次
            # 计数，冒烟内任何 unlink 都可能把进程删除配额耗光而被杀）
            empty_dir = _tmp_dir("appearance-ui-empty-")
            try:
                report3 = EnvReport(
                    recommended_frontend="ibus-rime", rime_user_dir=empty_dir
                )
                page2.bind(report3, StateMachine(report3))
                if page2.horizontal_switch.isChecked():
                    errors.append("无配置块时横排应回填默认（关）")
                if not page2.inline_switch.isChecked():
                    errors.append("无配置块时内嵌预编辑应回填默认（开）")
                if page2.preedit_combo.currentData() != "":
                    errors.append("无配置块时 preedit 应为默认")
                if page2.ibus_revert_button.isEnabled():
                    errors.append("无配置块时恢复默认按钮应禁用")
            finally:
                shutil.rmtree(empty_dir, ignore_errors=True)
            page2.retranslate()
            page2.deleteLater()
        finally:
            shutil.rmtree(rime_dir, ignore_errors=True)
        page.retranslate()
        page.deleteLater()
    finally:
        (
            appearance.fcitx5_conf_path,
            appearance.user_themes_dir,
            appearance.system_themes_dir,
            appearance.theme_backup_path,
        ) = path_originals
        shutil.rmtree(base, ignore_errors=True)
        i18n.set_language(original)
    return errors


def check_appearance_flow() -> list[str]:
    """外观端到端（v1.0.13）：主窗口 → runner 跑 CLI → classicui.conf 切换/还原、
    ibus 标记块写入/移除。路径全部注入临时目录，不碰真实 ~/.config/fcitx5。"""
    errors: list[str] = []
    base = _tmp_dir("appearance-flow-")
    try:
        from core import appearance
        from ui.main_window import MainWindow

        conf = base / "classicui.conf"
        themes = base / "themes"
        backups = base / "backups"
        rime_dir = base / "rime"
        for d in (themes, backups, rime_dir):
            d.mkdir()
        conf.write_text(
            "Theme=deepin-light\nDarkTheme=deepin-dark\nUseDarkTheme=True\n",
            encoding="utf-8",
        )

        # 路径注入：页面与主窗口的外观读写全部重定向到临时目录
        original_paths = (
            appearance.fcitx5_conf_path,
            appearance.user_themes_dir,
            appearance.theme_backup_path,
        )
        appearance.fcitx5_conf_path = lambda: conf
        appearance.user_themes_dir = lambda: themes
        appearance.theme_backup_path = lambda: backups / "classicui.conf.orig"

        report = _report_with_engine()
        report.recommended_frontend = "fcitx5-rime"
        report.rime_user_dir = rime_dir
        manifest = Manifest(base / "m.db", base / "snap")
        window = MainWindow(report, manifest)

        # 同 check_skin_flow：钉住 redetect，防后台真实探测覆盖假报告
        def _pinned_redetect() -> None:
            window.report = report
            window._refresh_installed()
            window.sm = StateMachine(
                report,
                busy=window.runner.running,
                installed_schemes=len(window._installed_keys),
            )
            window._dispatch()

        window.redetect = _pinned_redetect
        window.redetect_async = _pinned_redetect

        # 1) 切换内置主题：conf 改写 + 主题落盘 + 原件备份
        window._on_theme_switch("rime-installer-ink")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"主题切换任务失败: {state}")
            return errors
        text = conf.read_text(encoding="utf-8")
        if "Theme=rime-installer-ink" not in text:
            errors.append("切换后 classicui.conf 未写入新主题")
        if not (themes / "rime-installer-ink" / "theme.conf").is_file():
            errors.append("内置主题未安装到用户主题目录")
        if not (backups / "classicui.conf.orig").is_file():
            errors.append("首次切换未备份原件")

        # 2) 还原：恢复最初配置
        window._on_theme_revert()
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"主题还原任务失败: {state}")
            return errors
        if "Theme=deepin-light" not in conf.read_text(encoding="utf-8"):
            errors.append("还原后未恢复最初主题")

        # 2.5) v1.0.24：删除用户主题
        # 非当前主题：只删目录，classicui.conf 一动不动（只有 1 步，不重启框架）
        can_assert_deleted = shutil.rmtree is _PRISTINE_RMTREE
        (themes / "pearl").mkdir()
        (themes / "pearl" / "theme.conf").write_text(
            "[Metadata]\nName=珍珠白\n", encoding="utf-8"
        )
        window._on_theme_delete("pearl")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"删除非当前主题失败: {state}")
            return errors
        if can_assert_deleted and (themes / "pearl").exists():
            errors.append("删除后主题目录应消失")
        if "Theme=deepin-light" not in conf.read_text(encoding="utf-8"):
            errors.append("删除非当前主题不应改动 classicui.conf")
        # 当前主题：任务里先切回 default 再删目录（顺序不可颠倒）
        (themes / "pearl").mkdir()
        (themes / "pearl" / "theme.conf").write_text(
            "[Metadata]\nName=珍珠白\n", encoding="utf-8"
        )
        window._on_theme_switch("pearl")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"切换到待删主题失败: {state}")
            return errors
        window._on_theme_delete("pearl")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"删除当前主题失败: {state}")
            return errors
        # 落点由 delete_fallback_theme 定（原件主题 / default），此处只验核心
        # 不变量：conf 绝不能仍指向刚被删掉的那款（精确落点见 check_appearance
        # 里受控的 conf2/conf3 用例，避免依赖真机系统主题目录内容）
        current_after = appearance.current_theme_key(conf)
        if not current_after or current_after == "pearl":
            errors.append(f"删除当前主题后 conf 仍指向它: {current_after!r}")
        if can_assert_deleted and (themes / "pearl").exists():
            errors.append("删除当前主题后目录应消失")

        # 3) ibus 外观写入 + 移除
        window._on_ibus_style(True, False, "ink")
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"ibus 外观任务失败: {state}")
            return errors
        text = (rime_dir / "ibus_rime.custom.yaml").read_text(encoding="utf-8")
        if "horizontal: true" not in text or "color_scheme: ink" not in text:
            errors.append("ibus 外观未正确写入")
        if appearance.detect_ibus_style(rime_dir) is None:
            errors.append("写入后反读失败")
        window._on_ibus_revert()
        state = _wait_runner(window.runner)
        if state["ok"] is not True:
            errors.append(f"ibus 外观移除任务失败: {state}")
            return errors
        if appearance.detect_ibus_style(rime_dir) is not None:
            errors.append("移除后反读应为 None")
        window.close()
        manifest.close()
    finally:
        from core import appearance as _appearance

        _appearance.fcitx5_conf_path = original_paths[0]
        _appearance.user_themes_dir = original_paths[1]
        _appearance.theme_backup_path = original_paths[2]
        shutil.rmtree(base, ignore_errors=True)
    return errors




def _run_steps_sync(runner, steps, timeout_ms=20000, cancel_after_ms=None) -> dict:
    """在已有事件循环中同步等待 runner 完成（offscreen 冒烟用）。"""
    result = {"ok": None, "code": None, "lines": [], "progress": 0}

    def _on_out(line: str) -> None:
        result["lines"].append(line)

    def _on_prog(pct: int) -> None:
        result["progress"] = max(result["progress"], pct)

    def _on_fin(ok: bool, code: int) -> None:
        result["ok"], result["code"] = ok, code
        loop.quit()

    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    runner.output.connect(_on_out)
    runner.progress.connect(_on_prog)
    runner.finished.connect(_on_fin)
    QTimer.singleShot(timeout_ms, loop.quit)
    if cancel_after_ms is not None:
        QTimer.singleShot(cancel_after_ms, runner.cancel)
    runner.run_steps(steps)
    loop.exec()
    runner.output.disconnect(_on_out)
    runner.progress.disconnect(_on_prog)
    runner.finished.disconnect(_on_fin)
    return result


def check_runner() -> list[str]:
    """用真实 bash 命令验证 runner：进度解析、失败传播、多步、取消。"""
    errors: list[str] = []

    # 1) 成功 + 百分比解析
    runner = TaskRunner()
    result = _run_steps_sync(
        runner,
        [
            Step(
                "模拟下载",
                shell_wrap(["bash", "-c", "echo start; echo '[#1a 1MiB/2MiB(42%)]'; echo done"]),
                weight=2,
                progress_re=PERCENT_RE,
            )
        ],
    )
    if result["ok"] is not True or result["code"] != 0:
        errors.append(f"成功任务应 ok=True: {result['ok']}/{result['code']}")
    if "start" not in result["lines"] or "done" not in result["lines"]:
        errors.append(f"输出未捕获: {result['lines']}")
    if result["progress"] < 42:
        errors.append(f"百分比未解析: {result['progress']}")

    # 2) 失败传播（含 stderr 合并）
    runner = TaskRunner()
    result = _run_steps_sync(
        runner,
        [Step("必败步骤", shell_wrap(["bash", "-c", "echo boom >&2; exit 3"]))],
    )
    if result["ok"] is not False or result["code"] != 3:
        errors.append(f"失败任务应 ok=False code=3: {result['ok']}/{result['code']}")
    if "boom" not in result["lines"]:
        errors.append("stderr 未合并捕获")

    # 3) 多步：第二步失败时整体失败
    runner = TaskRunner()
    result = _run_steps_sync(
        runner,
        [
            Step("第一步", shell_wrap(["bash", "-c", "echo one"])),
            Step("第二步", shell_wrap(["bash", "-c", "echo two; exit 7"]), weight=2),
        ],
    )
    if result["ok"] is not False or result["code"] != 7:
        errors.append(f"多步失败应 code=7: {result['ok']}/{result['code']}")
    if "one" not in result["lines"] or "two" not in result["lines"]:
        errors.append(f"多步输出不全: {result['lines']}")

    # 4) 取消：sleep 进程应被真正终止
    runner = TaskRunner()
    result = _run_steps_sync(
        runner,
        [Step("长任务", shell_wrap(["bash", "-c", "sleep 30"]))],
        timeout_ms=10000,
        cancel_after_ms=400,
    )
    if result["ok"] is not False or result["code"] != -1:
        errors.append(f"取消应 ok=False code=-1: {result['ok']}/{result['code']}")

    # 5) 空步骤序列
    runner = TaskRunner()
    result = _run_steps_sync(runner, [])
    if result["ok"] is not True:
        errors.append("空步骤应直接成功")
    return errors


def check_scheme_install_flow() -> list[str]:
    """离线端到端：本地假包 → plum 安装到临时 rime_dir → 记账 → 卸载还原。

    用本地 tarball 顶替 aria2 下载步骤，其余链路（解压预置 / rime-install /
    diff 记账 / 卸载还原）与线上完全一致，离线可重复验证。
    """
    errors: list[str] = []
    base = _tmp_dir("rime-scheme-")
    ref = PackageRef("smokelab", "smokepkg")
    dest = config.PLUM_DIR / "package" / ref.pkg_dir
    try:
        rime_dir = base / "rime"
        rime_dir.mkdir()
        work = base / "work"
        work.mkdir()
        src = base / "src"
        src.mkdir()
        (src / "default.yaml").write_text("config_version: 'smoke'\n", encoding="utf-8")
        (src / "smokepkg.schema.yaml").write_text("schema:\n  schema_id: smoke\n", encoding="utf-8")
        tarball = work / f"{ref.user}-{ref.repo}.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(tarball), "-C", str(src), "."], check=True
        )

        scheme = custom_scheme(ref)
        manifest = Manifest(base / "m.db", base / "snap")
        snapshot = manifest.create_snapshot(rime_dir, scheme.key)
        steps = [
            build_extract_step(ref, work, dest),
            build_verify_step(scheme),
            build_install_step(scheme, "ibus-rime", rime_dir),
        ]
        result = _run_steps_sync(TaskRunner(), steps, timeout_ms=30000)
        if result["ok"] is not True:
            errors.append(f"方案安装流程失败（code={result['code']}）: {result['lines'][-3:]}")
            return errors
        if not (rime_dir / "default.yaml").is_file():
            errors.append("文件未安装到显式指定的 rime_dir")

        # v1.0.20 反向用例：根目录无 RIME 数据文件的仓库（主题包/图片仓库）
        # 必须让校验步骤失败，而不是被 plum 的 No files updated. 蒙混过关
        theme_ref = PackageRef("smokelab", "themepkg")
        theme_dest = config.PLUM_DIR / "package" / theme_ref.pkg_dir
        theme_src = base / "theme-src"
        theme_src.mkdir()
        (theme_src / "theme.conf").write_text("[InputPanel]\n", encoding="utf-8")
        (theme_src / "panel.svg").write_text("<svg/>", encoding="utf-8")
        theme_tarball = work / f"{theme_ref.user}-{theme_ref.repo}.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(theme_tarball), "-C", str(theme_src), "."], check=True
        )
        bad_result = _run_steps_sync(
            TaskRunner(),
            [
                build_extract_step(theme_ref, work, theme_dest),
                build_verify_step(custom_scheme(theme_ref)),
            ],
            timeout_ms=30000,
        )
        if bad_result["ok"] is not False:
            errors.append("非 RIME 包（主题包）应当被校验步骤拦下")
        if not any("RIME 数据文件" in line for line in bad_result["lines"]):
            errors.append(f"校验失败应给出可读原因: {bad_result['lines'][-3:]}")
        shutil.rmtree(theme_dest, ignore_errors=True)

        added, overwritten = Manifest.diff_snapshot(snapshot, rime_dir)
        if sorted(added) != ["default.yaml", "smokepkg.schema.yaml"]:
            errors.append(f"安装后 diff 异常: added={added} overwritten={overwritten}")

        digest = package_tree_hash(config.PLUM_DIR / "package", scheme)
        package_id = manifest.add_package(
            scheme.key, scheme.name, scheme.source, "ibus-rime",
            rime_dir, snapshot, digest,
        )
        manifest.record_files(package_id, added, overwritten)
        if manifest.installed_keys() != {scheme.key}:
            errors.append("安装后未出现在已安装列表")
        if manifest.installed_count() != 1:
            errors.append("installed_count 异常")

        stats = manifest.restore_package(scheme.key)
        if any(rime_dir.iterdir()):
            errors.append(f"卸载后仍有残留文件: {list(rime_dir.iterdir())}")
        if stats != {"deleted": 2, "restored": 0, "missing": 0}:
            errors.append(f"卸载统计异常: {stats}")
        manifest.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(config.PLUM_DIR / "package" / ref.user, ignore_errors=True)
    return errors


def check_snapshot_service() -> list[str]:
    """备份/恢复核心：元数据往返、覆盖合并 vs 清空恢复、zip slip 防御、home 护栏。"""
    errors: list[str] = []
    base = _tmp_dir("rime-backup-")
    try:
        home = base / "home"
        rime_dir = home / ".config" / "ibus" / "rime"
        (rime_dir / "build").mkdir(parents=True)
        (rime_dir / "default.yaml").write_text("config_version: '1'", encoding="utf-8")
        (rime_dir / "build" / "luna_pinyin.schema.yaml").write_text("schema: 1", encoding="utf-8")

        dest = base / "backups"
        info = create_backup(rime_dir, "ibus-rime", ["雾凇拼音"], "单元备份", dest_dir=dest)
        if not info.path.is_file() or info.file_count != 2 or info.size <= 0:
            errors.append(f"备份创建异常: {info}")
        if not info.path.name.startswith("rime-backup-"):
            errors.append(f"备份命名异常: {info.path.name}")

        back = read_backup_info(info.path)
        if back.note != "单元备份" or back.frontend != "ibus-rime":
            errors.append(f"元数据往返异常: {back}")
        if back.packages != ("雾凇拼音",) or back.file_count != 2:
            errors.append(f"元数据方案/文件数异常: {back}")
        if len(list_backups(dest)) != 1:
            errors.append("list_backups 未列出备份")
        if len(list_backups(base / "不存在的目录")) != 0:
            errors.append("空目录应返回空列表")

        # 覆盖合并：恢复同名文件，保留备份外的文件
        (rime_dir / "default.yaml").write_text("broken", encoding="utf-8")
        (rime_dir / "extra.yaml").write_text("keep", encoding="utf-8")
        stats = restore_backup(info.path, rime_dir, home=home)
        if (rime_dir / "default.yaml").read_text(encoding="utf-8") != "config_version: '1'":
            errors.append("覆盖合并未还原原内容")
        if not (rime_dir / "extra.yaml").exists():
            errors.append("覆盖合并误删了备份之外的文件")
        if stats["extracted"] != 2 or stats["removed"] != 0:
            errors.append(f"合并恢复统计异常: {stats}")

        # 清空恢复：严格回到备份那一刻
        stats = restore_backup(info.path, rime_dir, clean=True, home=home)
        if (rime_dir / "extra.yaml").exists():
            errors.append("clean 模式应删除备份之外的文件")
        if stats["removed"] < 1 or stats["extracted"] != 2:
            errors.append(f"清空恢复统计异常: {stats}")

        # zip slip 防御：成员路径含 .. 必须被丢弃；全部成员被丢弃时直接拒绝恢复
        # （v1.0.23：此前返回 extracted=0 的「成功」，现在按空载荷报错）
        evil = dest / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr(META_NAME, json.dumps({"created_at": "x", "file_count": 1}))
            archive.writestr("rime/../../evil.yaml", "pwned")
        try:
            restore_backup(evil, rime_dir, home=home)
            errors.append("只含越界成员的备份应被拒绝")
        except ValueError:
            pass
        if (base / "evil.yaml").exists() or (home / "evil.yaml").exists():
            errors.append("恶意 zip 写出了目标目录之外")

        # 安全护栏：非 home 下的恢复/删除目标一律拒绝
        try:
            restore_backup(info.path, base / "outside", home=home)
            errors.append("非 home 恢复目标应被拒绝")
        except ValueError:
            pass
        outside_zip = base / "outside.zip"
        outside_zip.write_text("x", encoding="utf-8")
        if delete_backup(outside_zip, home=home, allowed_dir=base):
            errors.append("非 home 删除目标应被拒绝")
        not_zip = home / "note.txt"
        not_zip.write_text("x", encoding="utf-8")
        if delete_backup(not_zip, home=home, allowed_dir=home):
            errors.append("非 zip 文件不应被删除")
        # 合法目标：位于 home 之下的备份（真实场景即 ~/.local/share/…/backups）
        in_home = create_backup(rime_dir, "ibus-rime", [], "", dest_dir=home / "backups")
        if not delete_backup(in_home.path, home=home, allowed_dir=home / "backups"):
            errors.append("家目录下的备份应可删除")
        # v1.0.23：不在备份目录内的 zip，即使在家目录下也不允许删
        (home / "kept.zip").write_bytes(info.path.read_bytes())
        if delete_backup(home / "kept.zip", home=home, allowed_dir=home / "backups"):
            errors.append("备份目录之外的 zip 不应被删除")
        if not (home / "kept.zip").exists():
            errors.append("被拒绝的删除不应真的删掉文件")
        (home / "kept.zip").unlink(missing_ok=True)

        # 导入 / 导出
        again = create_backup(rime_dir, "ibus-rime", [], "", dest_dir=dest)
        imported = import_backup(again.path, dest_dir=base / "imported")
        if not imported.path.is_file() or imported.frontend != "ibus-rime":
            errors.append(f"导入异常: {imported}")
        exported = export_backup(again.path, base / "exported" / "out.zip")
        if not exported.is_file():
            errors.append("导出失败")
        try:
            import_backup(base / "不存在的.zip")
            errors.append("导入缺失文件应报错")
        except ValueError:
            pass
        bad = base / "bad.zip"
        bad.write_text("not a zip", encoding="utf-8")
        try:
            import_backup(bad)
            errors.append("导入损坏 zip 应报错")
        except ValueError:
            pass

        # 损坏/缺失备份应标记为无法识别而非崩溃
        if read_backup_info(bad).valid:
            errors.append("损坏 zip 应标记为无效")
        if read_backup_info(base / "missing.zip").valid:
            errors.append("缺失文件应标记为无效")
        if "KB" not in human_size(2048) or "B" not in human_size(10):
            errors.append(f"human_size 异常: {human_size(2048)}")

        # ---- v1.0.23 回归：畸形元数据不能让列表/启动崩掉 ----
        # 此前 int("abc") / packages=3 会把异常抛给 list_backups，
        # 只要备份目录里有这样一个 zip，整个安装器就启动失败。
        weird_dir = base / "weird"
        weird_dir.mkdir()
        weird_cases = {
            "count.zip": {"file_count": "abc", "created_at": 5},
            "packages.zip": {"packages": 3},
            "none.zip": {"file_count": None, "packages": None},
            "list.zip": [1, 2, 3],
        }
        for name, meta in weird_cases.items():
            target = weird_dir / name
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr(META_NAME, json.dumps(meta))
                archive.writestr("rime/x.yaml", "x")
            try:
                parsed = read_backup_info(target)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"畸形元数据 {name} 不应抛异常: {exc!r}")
                continue
            if name in ("count.zip", "none.zip") and not parsed.valid:
                errors.append(f"{name} 应可读（字段已防御式取值）")
        weird_infos = list_backups(weird_dir)
        if len(weird_infos) != 4:
            errors.append(f"含畸形项时 list_backups 应仍返回全部: {len(weird_infos)}")
        if not any(not i.valid for i in weird_infos):
            errors.append("格式异常的元数据应标记为无效项")
        try:
            import_backup(weird_dir / "packages.zip", dest_dir=weird_dir / "imported")
        except ValueError:
            pass  # 拒绝也可接受（载荷完好时通常放行，见下）
        except Exception as exc:  # noqa: BLE001
            errors.append(f"导入畸形元数据应报 ValueError 而非 {type(exc).__name__}: {exc}")
        else:
            # 载荷完好、只是 packages 字段写坏：放行是合理的，但必须仍能被列出
            try:
                listed = list_backups(weird_dir / "imported")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"导入后列表不应抛异常: {exc!r}")
            else:
                if len(listed) != 1:
                    errors.append(f"导入畸形元数据后列表项异常: {len(listed)}")

        # ---- v1.0.23 回归：空目录不产出「0 个文件」的假备份 ----
        empty_dir = base / "empty-rime"
        empty_dir.mkdir()
        try:
            create_backup(empty_dir, "ibus-rime", [], "", dest_dir=base / "empty-out")
            errors.append("空配置目录应拒绝备份（否则会生成 0 文件假备份）")
        except EmptyBackupError:
            pass
        if (base / "empty-out").is_dir() and list((base / "empty-out").glob("*.zip")):
            errors.append("空目录备份不应留下 zip 文件")
        # 空载荷备份 + 清空恢复 = 清空用户配置，必须拒绝
        hollow = base / "hollow.zip"
        with zipfile.ZipFile(hollow, "w") as archive:
            archive.writestr(META_NAME, json.dumps({"created_at": "x", "file_count": 0}))
        keep_dir = base / "keep-rime"
        keep_dir.mkdir()
        (keep_dir / "important.yaml").write_text("mine", encoding="utf-8")
        try:
            restore_backup(hollow, keep_dir, clean=True, home=home)
            errors.append("0 文件备份的清空恢复应被拒绝")
        except ValueError:
            pass
        if not (keep_dir / "important.yaml").exists():
            errors.append("被拒绝的恢复不应删掉现有配置")
        if list(keep_dir.parent.glob(STAGING_PREFIX + "*")):
            errors.append("恢复失败后遗留了临时目录")

        # ---- v1.0.23 回归：同秒同名不覆盖 ----
        # 注意：只有「文件真的存在」时才应顺延——纯函数调用两次返回同一路径
        # 是正常的，冲突发生在第二次落盘前。
        clash_dir = base / "name-clash"
        first = new_backup_path("same", dest_dir=clash_dir)
        first.write_text("x", encoding="utf-8")
        second = new_backup_path("same", dest_dir=clash_dir)
        if second == first:
            errors.append("同秒同备注应生成不同文件名（否则静默覆盖前一个备份）")
        second.write_text("x", encoding="utf-8")
        third = new_backup_path("same", dest_dir=clash_dir)
        if third in (first, second):
            errors.append("已存在同名文件时应继续顺延文件名")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_snapshot_cli() -> list[str]:
    """CLI 端到端（经 runner/QProcess）：备份产出 + 进度解析 + 恢复文件数。"""
    errors: list[str] = []
    from core.snapshot_service import build_backup_step, build_restore_step

    base = _tmp_dir("rime-cli-")
    try:
        rime_dir = base / "rime"
        rime_dir.mkdir()
        for index in range(30):
            (rime_dir / f"f{index}.yaml").write_text("x" * 100, encoding="utf-8")
        dest = base / "backups"

        result = _run_steps_sync(
            TaskRunner(),
            [build_backup_step(rime_dir, "ibus-rime", ["方案A"], "CLI 备份", dest)],
            timeout_ms=30000,
        )
        if result["ok"] is not True:
            errors.append(f"CLI 备份失败（code={result['code']}）: {result['lines'][-3:]}")
            return errors
        if result["progress"] < 90:
            errors.append(f"CLI 备份进度未解析到 100%: {result['progress']}")
        zips = sorted(dest.glob("*.zip"))
        if len(zips) != 1:
            errors.append(f"CLI 备份产物异常: {zips}")

        for path in rime_dir.iterdir():
            path.unlink()
        result = _run_steps_sync(
            TaskRunner(),
            [build_restore_step(zips[0], rime_dir, clean=True, home=base)],
            timeout_ms=30000,
        )
        if result["ok"] is not True:
            errors.append(f"CLI 恢复失败（code={result['code']}）: {result['lines'][-3:]}")
        if len(list(rime_dir.iterdir())) != 30:
            errors.append(f"CLI 恢复文件数异常: {len(list(rime_dir.iterdir()))}")
        if result["progress"] < 90:
            errors.append(f"CLI 恢复进度未解析: {result['progress']}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return errors


def check_i18n() -> list[str]:
    """i18n 核心：语言列表、切换、缺键回退、持久化、英包键一致性（Phase 6c）。"""
    errors: list[str] = []
    from core import i18n

    original = i18n.load_preference()
    original_current = i18n.current()
    try:
        if i18n.languages() != (i18n.ZH, i18n.EN):
            errors.append(f"语言列表异常: {i18n.languages()}")

        # 中英切换
        i18n.set_language(i18n.ZH)
        if i18n.tr("nav.engine") != "引擎安装":
            errors.append(f"中文取值异常: {i18n.tr('nav.engine')}")
        i18n.set_language(i18n.EN)
        if i18n.tr("nav.engine") != "Engine":
            errors.append(f"英文取值异常: {i18n.tr('nav.engine')}")

        # 英文名应为该语言原生写法（不随当前语言变）
        if i18n.label(i18n.EN) != "English" or i18n.label(i18n.ZH) != "简体中文":
            errors.append("语言展示名应固定为各语言原生写法")

        # 缺键回退：英文包未覆盖的键回落中文源，绝不返回裸 key
        zh_value = i18n.ZH_TEXT["doctor.subtitle"]
        if i18n.tr("doctor.subtitle") == "doctor.subtitle":
            errors.append("英文包缺键未回退中文源")
        i18n.set_language(i18n.ZH)
        if i18n.tr("doctor.subtitle") != zh_value:
            errors.append("中文取值与源字典不一致")

        # 未知语言代码回落中文
        if i18n.set_language("xx_YY") != i18n.ZH:
            errors.append("未知语言代码应回退中文")

        # 持久化往返
        for lang in i18n.languages():
            i18n.save_preference(lang)
            if i18n.load_preference() != lang:
                errors.append(f"语言偏好持久化往返失败: {lang}")

        # 英文包键必须都存在于中文源（防拼写漂移）。
        # 例外：doctor.check.* 是「仅英文」的体检结果文案——中文由
        # core/doctor.py 的字面量提供（i18n.tr_or 的 fallback），避免同一句
        # 中文在代码与 ZH_TEXT 里各存一份。覆盖是否完整由 check_doctor 用
        # 「每个 i18n_key 都能查到英文」来保证（比键名比对更严格）。
        from core.translations.en import EN_TEXT

        unknown = sorted(
            key
            for key in set(EN_TEXT) - set(i18n.ZH_TEXT)
            if not key.startswith("doctor.check.")
        )
        if unknown:
            errors.append(f"英文包存在中文源没有的键: {unknown}")

        # tr_or：英文有则用英文，没有则用调用方给的 fallback（不是裸 key）
        i18n.set_language(i18n.EN)
        if i18n.tr_or("doctor.check.engine.title", "输入法引擎") != "Input method engine":
            errors.append("tr_or 未取到英文")
        if i18n.tr_or("doctor.check.nonexistent.title", "中文兜底") != "中文兜底":
            errors.append("tr_or 缺键时应返回 fallback")
        if i18n.tr_or("doctor.check.nonexistent.title", "x{y}", y="1") != "x{y}":
            errors.append("tr_or 的 fallback 不应做占位符替换")
        i18n.set_language(i18n.ZH)
        # dict 字面量里的重复键会被静默覆盖（v1.0.26 前 doctor.restart /
        # doctor.redetect 各定义两次，后者覆盖前者且无人察觉）
        import ast

        root = Path(__file__).resolve().parent
        for source, name in (
            (root / "core" / "i18n.py", "ZH_TEXT"),
            (root / "core" / "translations" / "en.py", "EN_TEXT"),
        ):
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"))
            except SyntaxError as exc:  # 语法错误由编译检查兜住
                errors.append(f"{source.name} 无法解析: {exc}")
                continue
            for node in ast.walk(tree):
                # EN_TEXT 带类型注解（AnnAssign），ZH_TEXT 是普通 Assign
                if isinstance(node, ast.AnnAssign):
                    target, value = node.target, node.value
                elif isinstance(node, ast.Assign):
                    target, value = (node.targets[0] if node.targets else None), node.value
                else:
                    continue
                if not isinstance(target, ast.Name) or target.id != name:
                    continue
                if not isinstance(value, ast.Dict):
                    continue
                keys = [
                    k.value
                    for k in value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                ]
                dups = sorted({k for k in keys if keys.count(k) > 1})
                if dups:
                    errors.append(f"{source.name} 的 {name} 存在重复键: {dups}")

        # 主干键必须有英文翻译（导航与页面标题是用户第一眼看到的）
        for key in (
            *(f"nav.{n}" for n in (
                "welcome", "engine", "scheme", "appearance", "backup", "doctor", "log", "settings"
            )),
            *(f"{p}.title" for p in (
                "welcome", "engine", "scheme", "appearance", "backup", "doctor", "log", "settings"
            )),
            *(f"{p}.subtitle" for p in (
                "engine", "scheme", "appearance", "backup", "doctor", "log", "settings"
            )),
        ):
            if key not in EN_TEXT:
                errors.append(f"主干文案缺英文翻译: {key}")
    finally:
        i18n.save_preference(original)
        i18n.set_language(original_current)
    return errors


def _progress_target(page) -> int:
    """进度条的目标值。

    Phase 6b 起进度走补间动画，setValue 后数值要等事件循环推进才到位；
    断言关心的是「目标是否设对」，动画在途时取终点值。
    """
    ani = getattr(page.progress_bar, "_value_ani", None)
    return ani.endValue() if ani is not None else page.progress_bar.value()


#: 首页体检行标签中文预期值（v1.0.4：此前把裸 key 直传给 add_row，
#: 界面左侧显示成 os/session/desktop… 一排英文标识符，用户反馈「都是英文的」）
_WELCOME_ROW_LABELS_ZH = {
    "os": "操作系统",
    "session": "会话协议",
    "desktop": "桌面环境",
    "frontend": "输入法框架",
    "engine": "引擎状态",
    "rime_dir": "配置目录",
    "env": "输入法环境变量",
}


def _welcome_row_label(window, row_key: str) -> str:
    """取欢迎页某行左列标签文本（itemAt(0) 为标签，末位为值列）。"""
    row = window.welcome_page._rows.get(row_key)
    if row is None:
        return ""
    item = row.itemAt(0)
    label = item.widget() if item is not None else None
    return label.text() if label is not None else ""


def check_watchdog() -> list[str]:
    """GUI 卡死看门狗（v1.0.4）：心跳丢失触发、恢复重置、停止静默、trace 落盘。

    用 0.1s 阈值 + 0.02s 检查间隔的快速实例验证状态机，不碰真实 10s 阈值。
    """
    errors: list[str] = []
    import time

    from core.watchdog import StallWatchdog

    with tempfile.TemporaryDirectory() as tmp:
        trace_file = Path(tmp) / "trace.log"
        fired: list[float] = []
        wd = StallWatchdog(
            stall_threshold=0.1,
            check_interval=0.02,
            on_stall=lambda: fired.append(time.monotonic()),
            trace_path=trace_file,
        )
        wd.trace("冒烟：看门狗启动")
        try:
            content = trace_file.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if "冒烟：看门狗启动" not in content:
            errors.append("看门狗 trace 未写入注入路径")

        wd.start()
        time.sleep(0.05)
        wd.beat()  # 启动后先打卡，避免把启动窗口误判为卡死
        time.sleep(0.3)  # 不打卡：0.3s > 0.1s 阈值 → 应触发一次
        if len(fired) != 1:
            errors.append(f"心跳丢失应触发一次 on_stall: {len(fired)}")
        try:
            content = trace_file.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if "心跳丢失" not in content:
            errors.append("心跳丢失应记 trace")

        wd.beat()  # 恢复打卡
        time.sleep(0.05)  # 小于阈值：不应再触发
        if len(fired) != 1:
            errors.append(f"心跳恢复后不应重复触发: {len(fired)}")
        try:
            content = trace_file.read_text(encoding="utf-8")
        except OSError:
            content = ""
        if "心跳恢复" not in content:
            errors.append("心跳恢复应记 trace")

        wd.stop()
        time.sleep(0.2)  # 停止后即使不打卡也不应触发
        if len(fired) != 1:
            errors.append(f"stop 后看门狗应静默: {len(fired)}")
    return errors


def check_window() -> list[str]:
    errors: list[str] = []
    from PyQt6.QtWidgets import QApplication

    from ui.main_window import _INSTALL_PAGES, MainWindow

    tmp_base = _tmp_dir("rime-window-")
    app = QApplication(sys.argv[:1])
    _APP_HOLDER.append(app)
    try:
        manifest = Manifest(tmp_base / "m.db", tmp_base / "snap")
        window = MainWindow(_report_with_engine(), manifest)
        window.show()
        app.processEvents()

        if config.APP_NAME not in window.windowTitle():
            errors.append(f"窗口标题异常: {window.windowTitle()}")

        nav = window.navigationInterface
        items = len(nav.items)
        stack_items = window.stackedWidget.count()
        # v1.0.5：严格相等——routeKey 碰撞（重复 objectName）会让
        # NavigationPanel.insertItem 静默丢弃导航项，侧栏少一项且页面不可达
        # （曾因欢迎页/体检页同名「环境体检」只剩 6 项）
        if items != NAV_PAGES_EXPECTED:
            errors.append(f"导航项应恰好 {NAV_PAGES_EXPECTED} 项: {items}")
        if stack_items != NAV_PAGES_EXPECTED:
            errors.append(f"堆叠页应恰好 {NAV_PAGES_EXPECTED} 个: {stack_items}")
        # 各页 objectName（routeKey）必须唯一
        route_keys = [
            window.stackedWidget.widget(i).objectName()
            for i in range(stack_items)
        ]
        if len(set(route_keys)) != len(route_keys):
            errors.append(f"页面 objectName 重复（routeKey 碰撞）: {route_keys}")
        # v1.0.5：方案市场滚动区应独享卡片以下全部高度（曾与末尾 stretch
        # 1:1 平分，只露出 2 张卡、下方大片空白）
        scroll = window.scheme_page.scroll_area
        page_h = window.scheme_page.height()
        if page_h > 0 and scroll.height() * 2 < page_h:
            errors.append(
                f"方案市场滚动区高度不足（应占页面过半）: {scroll.height()}/{page_h}"
            )

        if window.engine_page.install_button.isEnabled():
            errors.append("有引擎时安装按钮应禁用（已安装）")
        if not window.engine_page.uninstall_button.isEnabled():
            errors.append("有引擎时卸载按钮应可用")

        # ---- 方案市场卡片（F5/F7） ----
        cards = window.scheme_page._cards
        if len(cards) != len(SCHEMES):
            errors.append(f"方案卡数量应为 {len(SCHEMES)}: {len(cards)}")
        first = cards[0]
        if not first.install_button.isEnabled():
            errors.append("有引擎时方案安装按钮应可用")
        if first.uninstall_button.isEnabled():
            errors.append("未安装方案的卸载按钮应禁用")
        if "尚未安装" not in first.uninstall_button.toolTip():
            errors.append(f"卸载按钮 tooltip 应说明未安装: {first.uninstall_button.toolTip()}")
        if not first.scheme.intro or not first.scheme.advantages:
            errors.append("卡片缺少介绍/优势")
        # 增强包卡片（F17）应存在且走同一状态机规则
        addon_cards = [c for c in cards if c.scheme.category == "addon"]
        if len(addon_cards) < 2:
            errors.append(f"增强包卡片不足: {len(addon_cards)}")
        elif not addon_cards[0].install_button.isEnabled():
            errors.append("有引擎时增强包安装按钮应可用")
        # ---- v1.0.9：方案/皮肤分区展示 + 左对齐瀑布流 ----
        skin_cards = [c for c in cards if c.scheme.category == "skin"]
        if not skin_cards:
            errors.append("皮肤卡片缺失")
        else:
            in_skins_flow = all(
                window.scheme_page.skins_layout.indexOf(c) >= 0 for c in skin_cards
            )
            in_scheme_flow = any(
                window.scheme_page.cards_layout.indexOf(c) >= 0 for c in skin_cards
            )
            if not in_skins_flow or in_scheme_flow:
                errors.append("皮肤卡片应全部位于皮肤分区瀑布流")
        if window.scheme_page.cards_layout.count() != len(cards) - len(
            [c for c in cards if c.scheme.category == "skin"]
        ):
            errors.append("方案分区卡片数与方案瀑布流不一致")
        if window.scheme_page.scheme_section_label.isHidden():
            errors.append("方案分区标题应显示")
        if window.scheme_page.skin_section_label.isHidden():
            errors.append("皮肤分区标题应显示")
        # 左对齐：可见卡片应从左缘起排、x 递增、放不下才换行
        page_geo = window.scheme_page.cards_host.geometry()
        xs = [c.pos().x() for c in cards if not c.isHidden()]
        if xs and min(xs) > page_geo.width() * 0.3 and len(xs) > 1:
            errors.append(f"瀑布流应左对齐（首卡 x={min(xs)}）")
        # ---- v1.0.9：日志页输出框在信息卡片内 ----
        if window.log_page.viewer.parent() is not window.log_page.card:
            errors.append("日志输出框应位于信息卡片内")
        # ---- v1.0.9：窗口图标（标题栏左上角） ----
        if window.windowIcon().isNull():
            errors.append("窗口图标未设置（标题栏左上角将空白）")
        # 自定义输入：空/非法/合法三态
        custom = window.scheme_page
        custom.custom_input.setText("")
        if custom.custom_button.isEnabled():
            errors.append("空输入时自定义安装按钮应禁用")
        custom.custom_input.setText("不合法/no-slash")
        if custom.custom_button.isEnabled():
            errors.append("非法规格时自定义安装按钮应禁用")
        custom.custom_input.setText("iDvel/rime-ice")
        app.processEvents()
        if not custom.custom_button.isEnabled():
            errors.append("合法规格时自定义安装按钮应可用")
        custom.custom_input.setText("")

        # ---- 方案页 busy 不变量 ----
        scheme_key = first.scheme.key
        custom.set_busy(True, "测试任务", running_key=scheme_key)
        app.processEvents()
        if any(c.install_button.isEnabled() for c in cards):
            errors.append("busy 时所有方案卡安装按钮应禁用")
        if custom.custom_button.isEnabled() or custom.cache_check.isEnabled():
            errors.append("busy 时自定义安装/缓存选项应禁用")
        if not custom.cancel_button.isEnabled():
            errors.append("busy 时取消按钮应可用")
        if custom.status_label.isHidden() or (
            custom.progress_bar.isHidden() and custom.waiting_bar.isHidden()
        ):
            errors.append("busy 时进度区应显示")
        if "执行中" not in first.installed_label.text():
            errors.append(f"运行中的卡片应显示执行中: {first.installed_label.text()}")
        custom.set_step(2, 5, "下载包")
        if "2/5" not in custom.status_label.text():
            errors.append(f"步骤指示异常: {custom.status_label.text()}")
        custom.set_progress(66)
        if _progress_target(custom) != 66:
            errors.append(f"进度条值异常: {_progress_target(custom)}")
        custom.set_busy(False)
        if not custom.status_label.isHidden() or not (
            custom.progress_bar.isHidden() and custom.waiting_bar.isHidden()
        ):
            errors.append("非 busy 时进度区应隐藏")
        if not cards[0].install_button.isEnabled():
            errors.append("退出 busy 后应恢复可用性")

        # ---- 引擎页 busy（Phase 2 回归） ----
        page = window.engine_page
        page.set_busy(True, "测试任务")
        app.processEvents()
        if page.install_button.isEnabled() or page.uninstall_button.isEnabled():
            errors.append("busy 时安装/卸载按钮应禁用")
        if not page.cancel_button.isEnabled():
            errors.append("busy 时取消按钮应可用")
        page.set_busy(False)

        # ---- 备份页（Phase 4 / F12） ----
        backup = window.backup_page
        if backup.backup_button.isEnabled() is not True:
            errors.append("有配置目录时备份按钮应可用")
        if backup.restore_button.isEnabled() or backup.delete_button.isEnabled():
            errors.append("未选中备份时恢复/删除应禁用")
        if "选择" not in (backup.restore_button.toolTip() or ""):
            errors.append(f"恢复按钮 tooltip 应提示先选择: {backup.restore_button.toolTip()}")

        # 注入一个假备份（不依赖真实备份目录），验证选中态与按钮联动
        fake_rime = tmp_base / "fakerime"
        fake_rime.mkdir(parents=True, exist_ok=True)
        (fake_rime / "default.yaml").write_text("config_version: '1'", encoding="utf-8")
        fake_info = create_backup(fake_rime, "ibus-rime", ["雾凇拼音"], "注入备份", dest_dir=tmp_base)
        backup.set_backups([fake_info])
        backup.list_widget.setCurrentRow(0)
        app.processEvents()
        if not backup.restore_button.isEnabled() or not backup.delete_button.isEnabled():
            errors.append("选中备份后恢复/删除应可用")
        selected = backup.selected_info()
        if selected is None or selected.note != "注入备份":
            errors.append(f"selected_info 未返回选中备份: {selected}")
        if "注入备份" not in backup.list_widget.item(0).text():
            errors.append(f"列表项摘要异常: {backup.list_widget.item(0).text()}")

        backup.set_busy(True, "测试备份")
        app.processEvents()
        if backup.backup_button.isEnabled() or backup.restore_button.isEnabled():
            errors.append("busy 时备份/恢复按钮应禁用")
        if not backup.cancel_button.isEnabled():
            errors.append("busy 时取消按钮应可用")
        if backup.status_label.isHidden() or (
            backup.progress_bar.isHidden() and backup.waiting_bar.isHidden()
        ):
            errors.append("busy 时备份进度区应显示")
        backup.set_step(2, 3, "打包中")
        if "2/3" not in backup.status_label.text():
            errors.append(f"备份页步骤指示异常: {backup.status_label.text()}")
        backup.set_progress(55)
        if _progress_target(backup) != 55:
            errors.append("备份页进度条异常")
        backup.set_busy(False)
        if not backup.backup_button.isEnabled():
            errors.append("退出 busy 后备份按钮应恢复")

        # ---- v1.0.23 回归：列表重建后按钮态必须重算 ----
        # 此前 set_backups 重建完不刷 _refresh_actions，且删除判据用
        # selected_path()（重置中途仍返回旧路径）→ 删除按钮停在可用状态。
        backup.set_backups([])
        backup.list_widget.setCurrentRow(-1)
        app.processEvents()
        if backup.delete_button.isEnabled():
            errors.append("清空列表后删除按钮应禁用（重建列表后未重算按钮态）")
        if backup.restore_button.isEnabled() or backup.export_button.isEnabled():
            errors.append("清空列表后恢复/导出应禁用")

        # ---- v1.0.23 回归：打开备份目录必须传 QUrl ----
        # 此前 `openUrl(Path.as_uri())` 传 str → TypeError → PyQt6 abort（闪退）。
        import ui.pages.backup_page as _backup_mod
        from PyQt6.QtCore import QUrl

        opened: list = []

        class _FakeDesktopServices:
            @staticmethod
            def openUrl(url):  # noqa: N802 — 与 Qt API 同名
                opened.append(url)
                return True

        original_desktop = _backup_mod.QDesktopServices
        _backup_mod.QDesktopServices = _FakeDesktopServices  # type: ignore[assignment]
        try:
            backup.open_dir_button.setEnabled(True)
            backup.open_dir_button.click()
            app.processEvents()
        finally:
            _backup_mod.QDesktopServices = original_desktop  # type: ignore[assignment]
        if not opened:
            errors.append("打开备份目录未调用 QDesktopServices.openUrl")
        elif not isinstance(opened[0], QUrl):
            errors.append(
                f"打开备份目录应传 QUrl，实际为 {type(opened[0]).__name__}（会触发 TypeError 闪退）"
            )

        # ---- v1.0.23 回归：英文界面不再漏中文 ----
        from core import i18n as _i18n

        backup.set_backups([fake_info])
        _i18n.set_language("en_US")
        backup.retranslate()
        app.processEvents()
        row_en = backup.list_widget.item(0).text()
        count_en = backup.count_row.itemAt(backup.count_row.count() - 1).widget().text()
        _i18n.set_language("zh_CN")
        backup.retranslate()
        app.processEvents()
        if "个文件" in row_en or "个" in count_en:
            errors.append(f"英文界面备份列表仍含中文: {row_en!r} / {count_en!r}")
        if "files" not in row_en:
            errors.append(f"英文界面备份列表应显示 files: {row_en!r}")

        # ---- 体检页（Phase 5 / F10） ----
        doctor = window.doctor_page
        doctor_keys = [r.key for r in doctor._results]
        for expected in (
            "im_env",
            "frontend_running",
            "engine",
            "schemes",
            "deployed",
            "input_source",
        ):
            if expected not in doctor_keys:
                errors.append(f"体检页缺少检查项: {expected}")
        # v1.0.6 回归：重复 bind 必须清理旧行 widget（QLayout 泄漏 → 文字重叠）
        from PyQt6.QtCore import QCoreApplication, QEvent

        from PyQt6.QtWidgets import QLabel

        labels_before = len(doctor.checks_host.findChildren(QLabel))
        doctor.bind(window.report, window.sm, window._installed_keys)
        # deleteLater 的 DeferredDelete 事件不经 processEvents 处理，需显式 flush
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        labels_after = len(doctor.checks_host.findChildren(QLabel))
        if labels_after > labels_before:
            errors.append(
                f"重复 bind 后检查行未清理（{labels_before}→{labels_after}），存在 QLayout 泄漏"
            )
        if not doctor.fix_button.isEnabled():
            errors.append("存在可修复项时一键修复应可用")
        if not doctor.restart_button.isEnabled():
            errors.append("有引擎时重启框架按钮应可用")
        if not doctor.redeploy_button.isEnabled():
            errors.append("有引擎时重新部署按钮应可用")
        doctor.set_busy(True, "测试修复")
        app.processEvents()
        if doctor.fix_button.isEnabled() or doctor.restart_button.isEnabled():
            errors.append("busy 时体检页操作按钮应禁用")
        if doctor.login_button.isEnabled():
            errors.append("busy 时「写入登录配置」应禁用")
        if not doctor.cancel_button.isEnabled():
            errors.append("busy 时体检页取消按钮应可用")
        if doctor.status_label.isHidden() or (
            doctor.progress_bar.isHidden() and doctor.waiting_bar.isHidden()
        ):
            errors.append("busy 时体检页进度区应显示")
        doctor.set_step(1, 2, "写入环境变量")
        if "1/2" not in doctor.status_label.text():
            errors.append(f"体检页步骤指示异常: {doctor.status_label.text()}")
        doctor.set_progress(50)
        if _progress_target(doctor) != 50:
            errors.append("体检页进度条异常")
        doctor.set_busy(False)
        if not doctor.fix_button.isEnabled():
            errors.append("退出 busy 后一键修复应恢复")

        # ---- v1.0.27：登录配置的次要入口（体检已就绪时也能主动重写） ----
        # 只断言接线与产物，不 emit 信号：真跑会改本机 ~/.xsessionrc（冒烟污染真机）
        if not hasattr(doctor, "login_button"):
            errors.append("体检页缺少「写入登录配置」按钮")
        else:
            if not doctor.login_button.isEnabled():
                errors.append("「写入登录配置」应始终可用（与是否可修复无关）")
            if not doctor.login_button.toolTip():
                errors.append("「写入登录配置」缺少说明")
            want_tip = i18n.tr(
                "doctor.write_login_tip_ok"
                if doctor._im_env_ready()
                else "doctor.write_login_tip"
            )
            if doctor.login_button.toolTip() != want_tip:
                errors.append("「写入登录配置」的提示未随配置状态切换")
        if not StateMachine(window.report).rule("write_login_env").enabled:
            errors.append("write_login_env 规则应恒可用（写环境变量不依赖引擎）")
        login_steps = build_fix_steps("im_env", window.report)
        login_cmd = " ".join(login_steps[0].argv) if login_steps else ""
        if (
            not login_steps
            or ".xsessionrc" not in login_cmd
            or "90-rime-im.conf" not in login_cmd
        ):
            errors.append("「写入登录配置」应同时写 environment.d 与 ~/.xsessionrc")

        # v1.0.26 第二步：体检结果的 title/detail/advice 必须跟随语言，
        # 且标题列不能裁切（固定 132px 会把英文 “Input method environment”
        # 截成 “Input method envirc”）
        original_lang = i18n.current()
        try:
            from core.translations.en import EN_TEXT
            from ui.pages.doctor_page import localized

            i18n.set_language(i18n.EN)
            doctor.retranslate()
            for result in doctor._results:
                row = doctor._rows.get(result.key)
                if row is None:
                    continue
                title, detail, advice = localized(result)
                if row.title_label.text() != title:
                    errors.append(f"切英文后标题未刷新: {result.key}")
                if row.detail_label.text() != detail:
                    errors.append(f"切英文后详情未刷新: {result.key}")
                if row.title_label.width() < row.title_label.sizeHint().width():
                    errors.append(
                        f"英文标题被裁切: {result.key} "
                        f"{row.title_label.width()} < {row.title_label.sizeHint().width()}"
                    )
                if f"doctor.check.{result.key}.title" not in EN_TEXT:
                    errors.append(f"体检标题缺英文: {result.key}")
            # 各行的 detail 起点必须一致（标题列统一宽度）
            starts = {
                row.title_label.width() for row in doctor._rows.values()
            }
            if len(starts) > 1:
                errors.append(f"标题列宽不一致: {sorted(starts)}")
        finally:
            i18n.set_language(original_lang)
            doctor.retranslate()

        # v1.0.26：体检类任务的进度必须落在体检页（旧实现一律回落方案市场页，
        # 点「一键修复」后本页毫无反馈、取消按钮却出现在别处）
        saved_kind = window._task_kind
        try:
            for kind in (
                "doctor-fix",
                "doctor-write-login",
                "frontend-restart",
                "frontend-redeploy",
                "config-tool",
            ):
                window._task_kind = kind
                if window._target_page() is not window.doctor_page:
                    errors.append(f"{kind} 任务的进度应显示在体检页")
            window._task_kind = "scheme-install"
            if window._target_page() is not window.scheme_page:
                errors.append("方案任务的进度仍应在方案市场页")
        finally:
            window._task_kind = saved_kind

        # ---- 四页进度区（Phase 6b / 等待态条纹 + 补间） ----
        for name, page in (
            ("engine", window.engine_page),
            ("scheme", window.scheme_page),
            ("backup", window.backup_page),
            ("doctor", window.doctor_page),
        ):
            if not hasattr(page, "waiting_bar"):
                errors.append(f"{name} 页缺少等待态条纹 waiting_bar")
                continue
            if not page.waiting_bar.isHidden() or not page.progress_bar.isHidden():
                errors.append(f"{name} 页初始不应显示进度条")
            page.enter_waiting()
            if page.waiting_bar.isHidden():
                errors.append(f"{name} 页 enter_waiting 后条纹未显示")
            page.set_progress(60)
            if not page.waiting_bar.isHidden():
                errors.append(f"{name} 页收到进度后仍处于等待态")
            if _progress_target(page) != 60:
                errors.append(f"{name} 页进度目标值异常: {_progress_target(page)}")
            page.reset_progress()
            if page.progress_bar.value() != 0:
                errors.append(f"{name} 页 reset 未归零")
            page._show_progress_area(False)  # 复位到空闲态，避免影响后续断言

        # ---- 设置页镜像（Phase 5 / F13） ----
        settings = window.settings_page
        if settings.mirror_list.count() < 3:
            errors.append(f"镜像列表项不足: {settings.mirror_list.count()}")
        if not settings.switch_button.toolTip():
            errors.append("镜像切换按钮缺少说明")
        # 未选中镜像时切换禁用
        settings.mirror_list.setCurrentRow(-1)
        app.processEvents()
        if settings.switch_button.isEnabled():
            errors.append("未选中镜像时切换应禁用")
        settings.mirror_list.setCurrentRow(1)
        app.processEvents()
        if not settings.switch_button.isEnabled():
            errors.append("选中镜像后切换应可用（Ubuntu）")

        # ---- 设置页 Hero 图标（v1.0.28：修「软件 Logo 高清图标不显示」） ----
        # 症状极隐蔽：图标路径不存在时 QIcon 静默返回空 pixmap，界面只是少了
        # 一块图，没有任何异常。所以这里把「源图档位 + 物理分辨率」钉死。
        badge = settings.logo_badge
        if badge.source_path.name != "rime-installer-512.png":
            errors.append(f"设置页 Hero 应取 512 源图: {badge.source_path.name}")
        hero_pixmap = badge.pixmap()
        if hero_pixmap.isNull():
            errors.append("设置页 Hero 图标未渲染（pixmap 为空）")
        elif badge.icon_size != 88 or hero_pixmap.width() < 176:
            errors.append(
                f"设置页 Hero 图标分辨率不足: size={badge.icon_size} "
                f"pixmap={hero_pixmap.width()}x{hero_pixmap.height()}"
            )
        badge.set_icon_size(72)  # 窄窗口档：同样从 512 重采样，不是缩放大图
        small_pixmap = badge.pixmap()
        if small_pixmap.isNull() or small_pixmap.width() < 144:
            errors.append(f"Hero 图标 72 档重采样失败: {small_pixmap.width()}")
        badge.set_icon_size(88)
        for name, card in (
            ("应用信息", settings.card),
            ("界面与外观", settings.prefs_card),
            ("apt 镜像", settings.apt_card),
        ):
            if card.height() <= 0:
                errors.append(f"设置页「{name}」卡片未布局（高度为 0）")
        if settings.scroll_area.horizontalScrollBar().maximum() > 0:
            errors.append("设置页出现横向滚动条（卡片被长文本撑宽）")

        # ---- 设置页主题（Phase 6 / v1.0.0） ----
        from ui.pages.base import STATUS_COLORS, STATUS_COLORS_DARK, STATUS_COLORS_LIGHT

        original_theme = theme.load_preference()
        try:
            if settings.theme_combo.count() != len(theme.PREFERENCES):
                errors.append(f"主题下拉项数异常: {settings.theme_combo.count()}")
            # 选中深色：信号发射 + 主窗口应用 + 色板切换 + 不崩溃
            dark_index = theme.PREFERENCES.index(theme.DARK)
            settings.theme_combo.setCurrentIndex(dark_index)
            app.processEvents()
            if theme.load_preference() != theme.DARK:
                errors.append("选择深色后偏好应持久化为 dark")
            if STATUS_COLORS != STATUS_COLORS_DARK:
                errors.append("切换深色后状态色板未更换")
            if "深色" not in settings.theme_combo.currentText():
                errors.append(
                    f"主题下拉未显示当前深色: {settings.theme_combo.currentText()}"
                )
            settings.theme_combo.setCurrentIndex(theme.PREFERENCES.index(theme.LIGHT))
            app.processEvents()
            if STATUS_COLORS != STATUS_COLORS_LIGHT:
                errors.append("切回浅色后状态色板未恢复")
        finally:
            theme.save_preference(original_theme)
            theme.apply_theme(original_theme)
            settings.theme_combo.blockSignals(True)
            settings.theme_combo.setCurrentIndex(
                theme.PREFERENCES.index(original_theme)
                if original_theme in theme.PREFERENCES
                else 0
            )
            settings.theme_combo.blockSignals(False)
            app.processEvents()

        # ---- 设置页语言（Phase 6c） ----
        original_lang = i18n.load_preference()
        try:
            if settings.language_combo.count() != len(i18n.languages()):
                errors.append(f"语言下拉项数异常: {settings.language_combo.count()}")
            en_index = i18n.languages().index(i18n.EN)
            settings.language_combo.setCurrentIndex(en_index)
            app.processEvents()
            if i18n.load_preference() != i18n.EN:
                errors.append("选择 English 后偏好应持久化")
            # 页面标题与导航项都跟着换；objectName（routeKey）保持不变
            if window.engine_page.title_label.text() != "Input Method Engine":
                errors.append(f"引擎页标题未切换英文: {window.engine_page.title_label.text()}")
            route_key = window.engine_page.objectName()
            nav_widget = window.navigationInterface.widget(route_key)
            if nav_widget is None:
                errors.append(f"导航项丢失（routeKey 变化？）: {route_key}")
            elif nav_widget.text() != "Engine":
                errors.append(f"导航项未切换英文: {nav_widget.text()}")
            if "RIME Installer" not in window.windowTitle():
                errors.append(f"窗口标题未切换英文: {window.windowTitle()}")
            # 首页体检行标签同样要跟着换（v1.0.4：不能停留在裸 key 或中文）
            for row_key, zh in _WELCOME_ROW_LABELS_ZH.items():
                text = _welcome_row_label(window, row_key)
                if text == row_key or text == zh:
                    errors.append(f"欢迎页行标签未切换英文: {row_key} -> {text!r}")
            if window.welcome_page.redetect_button.text() == "重新检测":
                errors.append("欢迎页重新检测按钮未切换英文")
            # 页面内按钮与分组文案同样要跟着换（6c-2）
            for page, attr, expect in (
                (window.engine_page, "install_button", "Install engine"),
                (window.backup_page, "backup_button", "Back up now"),
                (window.doctor_page, "fix_button", "Fix all"),
                (window.doctor_page, "login_button", "Write login config"),
                (window.scheme_page, "custom_button", "Install custom scheme"),
            ):
                button = getattr(page, attr, None)
                if button is None or button.text() != expect:
                    errors.append(
                        f"{type(page).__name__}.{attr} 未切换英文: "
                        f"{getattr(button, 'text', lambda: None)()}"
                    )

            # 切回中文：routeKey 不变，路由依然可用
            settings.language_combo.setCurrentIndex(
                i18n.languages().index(i18n.ZH)
            )
            app.processEvents()
            if window.engine_page.objectName() != route_key:
                errors.append("切回中文后 routeKey 不应变化")
            if window.engine_page.title_label.text() != "引擎安装":
                errors.append(f"切回中文标题异常: {window.engine_page.title_label.text()}")
            # 切回中文：行标签应为中文且绝不是裸 key（v1.0.4 回归）
            for row_key, zh in _WELCOME_ROW_LABELS_ZH.items():
                text = _welcome_row_label(window, row_key)
                if text != zh:
                    errors.append(f"欢迎页行标签中文异常: {row_key} -> {text!r}")
            if window.welcome_page.redetect_button.text() != "重新检测":
                errors.append(
                    f"欢迎页重新检测按钮中文异常: {window.welcome_page.redetect_button.text()}"
                )
        finally:
            i18n.save_preference(original_lang)
            i18n.set_language(original_lang)
            settings.language_combo.blockSignals(True)
            settings.language_combo.setCurrentIndex(
                i18n.languages().index(original_lang)
                if original_lang in i18n.languages()
                else 0
            )
            settings.language_combo.blockSignals(False)
            for name in _INSTALL_PAGES + ("log_page",):
                page = getattr(window, name, None)
                if page is not None:
                    page.retranslate()
            window._retranslate_nav()
            app.processEvents()

        # ---- 意图端到端（替换 run_steps，不真正执行 apt/下载/写文件） ----
        captured: list[list] = []
        original_run_steps = window.runner.run_steps

        def capture(steps):
            captured.append(list(steps))

        window.runner.run_steps = capture  # type: ignore[assignment]
        try:
            window.engine_page.install_requested.emit(False)
            app.processEvents()
            if len(captured) != 1 or len(captured[0]) != 1:
                errors.append(f"引擎安装意图未构建单步任务: {captured}")
            if not window.sm.busy:
                errors.append("任务开始后应进入 busy")
            session_name = window.log_page.session_row_text or ""
            if "engine-install" not in session_name or not session_name.endswith(".log"):
                errors.append(f"日志页未显示会话文件: {session_name}")
            window._set_busy(False)

            captured.clear()
            window.scheme_page.install_requested.emit(cards[0].scheme.key)
            app.processEvents()
            if len(captured) != 1 or len(captured[0]) < 3:
                errors.append(f"方案安装意图步骤数异常: {len(captured[0]) if captured else 0}")
            # 用 plum 脚本绝对路径判断，避免 CACHE_DIR（rime-installer）造成误判
            plum_bin = str(config.PLUM_RIME_INSTALL)
            last = " ".join(captured[0][-1].argv) if captured else ""
            if plum_bin not in last:
                errors.append(f"方案安装最后一步应为 rime-install: {last[-60:]}")
            window._set_busy(False)

            captured.clear()
            window.scheme_page.check_update_requested.emit(cards[0].scheme.key)
            app.processEvents()
            if not captured or any(plum_bin in " ".join(s.argv) for s in captured[0]):
                errors.append("更新检查不应触发 rime-install")
            window._set_busy(False)

            # 备份意图：单步调用快照 CLI
            captured.clear()
            window.backup_page.backup_requested.emit("冒烟备份")
            app.processEvents()
            if len(captured) != 1 or len(captured[0]) != 1:
                errors.append(f"备份意图未构建单步任务: {captured}")
            else:
                backup_cmd = " ".join(captured[0][0].argv)
                if str(config.SNAPSHOT_CLI) not in backup_cmd or " backup " not in backup_cmd:
                    errors.append(f"备份步骤应调用快照 CLI: {backup_cmd[-80:]}")
            window._set_busy(False)

            # 恢复意图：预备份=True 时两步、clean=True 时带 --clean（屏蔽确认框）
            from ui.pages.backup_page import BackupPage

            original_confirm = BackupPage.confirm_restore
            BackupPage.confirm_restore = staticmethod(lambda *a, **k: True)  # type: ignore[assignment]
            try:
                captured.clear()
                window.backup_page.restore_requested.emit(str(fake_info.path), False, True)
                app.processEvents()
                if len(captured) != 1 or len(captured[0]) != 2:
                    errors.append(
                        f"恢复意图（含预备份）应为 2 步: {len(captured[0]) if captured else 0}"
                    )
                elif " restore " not in " ".join(captured[0][1].argv):
                    errors.append("最后一步应为 restore")
                window._set_busy(False)

                captured.clear()
                window.backup_page.restore_requested.emit(str(fake_info.path), True, False)
                app.processEvents()
                if len(captured) != 1 or len(captured[0]) != 1:
                    errors.append("未预备份时恢复应为 1 步")
                elif "--clean" not in " ".join(captured[0][0].argv):
                    errors.append("清空恢复应带 --clean 参数")
                window._set_busy(False)
            finally:
                BackupPage.confirm_restore = original_confirm  # type: ignore[assignment]

            # 体检修复意图：应汇总可修复项步骤（含环境变量写入）
            captured.clear()
            window.doctor_page.fix_requested.emit(
                [r.key for r in window.doctor_page._results if r.fixable]
            )
            app.processEvents()
            if not captured:
                errors.append("一键修复意图未构建任务")
            else:
                fix_cmd = " ".join(" ".join(s.argv) for s in captured[0])
                if ".xsessionrc" not in fix_cmd and "setsid" not in fix_cmd:
                    errors.append(f"修复步骤内容异常: {fix_cmd[:100]}")
            window._set_busy(False)

            # v1.0.27：写入登录配置的次要入口。确认框 monkeypatch——
            # 真弹会阻塞无头环境，真跑会改本机 ~/.xsessionrc。
            import ui.main_window as _mw

            class _FakeBox:
                def __init__(self, *args, **kwargs) -> None:
                    self.args = args

                def exec(self) -> bool:
                    return _FakeBox.answer

            original_box = _mw.MessageBox
            try:
                # 取消：不得起任务
                _FakeBox.answer = False
                _mw.MessageBox = _FakeBox  # type: ignore[assignment]
                captured.clear()
                window.doctor_page.write_login_requested.emit()
                app.processEvents()
                if captured:
                    errors.append("取消写入登录配置时不应起任务")
                # 确认：起 doctor-write-login，步骤仍写两个文件
                _FakeBox.answer = True
                captured.clear()
                window.doctor_page.write_login_requested.emit()
                app.processEvents()
                if not captured or len(captured[0]) != 1:
                    errors.append("写入登录配置意图应构建单步任务")
                else:
                    login_cmd = " ".join(captured[0][0].argv)
                    if ".xsessionrc" not in login_cmd:
                        errors.append("写入登录配置步骤应含 ~/.xsessionrc")
                    if "90-rime-im.conf" not in login_cmd:
                        errors.append("写入登录配置步骤应含 environment.d 片段")
                if window._task_kind != "doctor-write-login":
                    errors.append(f"写入登录配置任务类型异常: {window._task_kind}")
            finally:
                _mw.MessageBox = original_box  # type: ignore[assignment]
            window._set_busy(False)
            window.doctor_page.set_busy(False)  # run_steps 被替换，任务不会自己收尾

            # 部署引导意图：重启 / 重新部署 / 配置工具各一步
            # 配置工具名依前端而定（ibus-setup / fcitx5-configtool / fcitx-configtool）
            expected_tool = {
                "ibus-rime": "ibus-setup",
                "fcitx5-rime": "fcitx5-configtool",
                "fcitx-rime": "fcitx-configtool",
            }[window.report.recommended_frontend]
            for signal, expect in (
                (window.doctor_page.restart_requested, "setsid"),
                (window.doctor_page.config_tool_requested, expected_tool),
            ):
                captured.clear()
                signal.emit()
                app.processEvents()
                if not captured or len(captured[0]) != 1:
                    errors.append(f"{signal} 意图应构建单步任务")
                elif expect not in " ".join(captured[0][0].argv):
                    errors.append(f"{signal} 步骤命令异常: {captured[0][0].argv[:4]}")
                window._set_busy(False)

            captured.clear()
            window.doctor_page.redeploy_requested.emit()
            app.processEvents()
            if not captured or "build" not in " ".join(captured[0][0].argv):
                errors.append("重新部署意图应清理 build 目录")
            window._set_busy(False)
            # v1.0.7：重新部署意图已改接后台 redetect_async——等待后台检测
            # 完成并处理回调，避免用例结束后 worker 回调撞上已清理的 manifest
            worker = getattr(window, "_detect_worker", None)
            if worker is not None and worker.isRunning():
                worker.wait(10000)
            app.processEvents()

            # 镜像切换意图：v1.0.5 起合并为单步单次提权（写源 + apt update）
            captured.clear()
            window.settings_page.mirror_switch_requested.emit("阿里云")
            app.processEvents()
            if not captured or len(captured[0]) != 1:
                errors.append(f"镜像切换意图应为单步: {len(captured[0]) if captured else 0}")
            elif "apt-get update" not in " ".join(captured[0][0].argv):
                errors.append("镜像切换步骤应含 apt update")
            window._set_busy(False)
        finally:
            window.runner.run_steps = original_run_steps  # type: ignore[assignment]
            window._set_busy(False)
            app.processEvents()

        # ---- 无引擎窗口：方案按钮必须禁用并给出原因 ----
        no_engine_manifest = Manifest(tmp_base / "m2.db", tmp_base / "snap2")
        no_engine_window = MainWindow(_report_no_engine(), no_engine_manifest)
        no_engine_window.show()
        app.processEvents()
        card = no_engine_window.scheme_page._cards[0]
        if card.install_button.isEnabled():
            errors.append("无引擎时方案安装按钮应禁用")
        if "引擎" not in (card.install_button.toolTip() or ""):
            errors.append("无引擎时方案安装按钮 tooltip 应提示先装引擎")
        if no_engine_window.scheme_page.custom_button.isEnabled():
            errors.append("无引擎时自定义安装按钮应禁用")
        if no_engine_window.engine_page.install_button.isEnabled() is not True:
            errors.append("无引擎时引擎安装按钮应可用")
        # 无引擎时部署引导禁用，但体检修复仍可用
        if no_engine_window.doctor_page.restart_button.isEnabled():
            errors.append("无引擎时重启框架按钮应禁用")
        if not no_engine_window.doctor_page.fix_button.isEnabled():
            errors.append("无引擎时一键修复仍应可用")
        no_engine_window.close()

        # ---- 无配置目录窗口：备份/恢复必须禁用并说明原因 ----
        no_dir_window = MainWindow(
            _report_no_rime_dir(), Manifest(tmp_base / "m3.db", tmp_base / "snap3")
        )
        no_dir_window.show()
        app.processEvents()
        if no_dir_window.backup_page.backup_button.isEnabled():
            errors.append("无配置目录时备份按钮应禁用")
        if "配置目录" not in (no_dir_window.backup_page.backup_button.toolTip() or ""):
            errors.append(
                f"无配置目录时备份按钮 tooltip 应说明原因: "
                f"{no_dir_window.backup_page.backup_button.toolTip()}"
            )
        # v1.0.23：即使选中了有效备份，恢复按钮也必须禁用（此前可点，点了
        # 静默无反应——main_window._on_backup_restore 里 rime_dir is None 直接 return）
        no_dir_page = no_dir_window.backup_page
        no_dir_page.set_backups([fake_info])
        no_dir_page.list_widget.setCurrentRow(0)
        app.processEvents()
        if no_dir_page.restore_button.isEnabled():
            errors.append("无配置目录时恢复按钮应禁用（否则点击后静默无反应）")
        if "配置目录" not in (no_dir_page.restore_button.toolTip() or ""):
            errors.append(
                f"无配置目录时恢复按钮 tooltip 应说明原因: "
                f"{no_dir_page.restore_button.toolTip()}"
            )
        no_dir_window.close()

        window.redetect()
        app.processEvents()
        # v1.0.5：窗口级工具提示已删除（悬停弹「状态：…」长条属干扰），
        # 状态信息由欢迎页摘要与各页状态行承担
        if window.toolTip():
            errors.append(f"窗口不应再挂工具提示: {window.toolTip()}")
        if not (window.welcome_page.summary_label.text() or ""):
            errors.append("欢迎页摘要为空")
        window.close()
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)
    return errors


def main() -> int:
    failures: list[str] = []
    # 冒烟环境专用：禁用外观切换的 fcitx5 重启步（避免误杀开发/测试机输入法）
    os.environ.setdefault("RIME_INSTALLER_NO_RESTART", "1")

    def _run_check(name: str, check) -> None:
        """逐项执行并即时打印进度（flush）——宿主钩子拦截清理导致进程
        被杀时（2026-10-17 实测），已通过/未通过的检查项仍有据可查。"""
        before = len(failures)
        try:
            failures.extend(check())
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name} 检查异常: {exc}")
        status = "OK" if len(failures) == before else f"FAIL({len(failures) - before})"
        print(f"[smoke] {name}: {status}", flush=True)

    failures += check_resources()

    assert config.VERSION == "1.0.28", "版本号与执行计划不一致"
    config.ensure_user_dirs()
    for d in (
        config.USER_DATA_DIR,
        config.LOG_DIR,
        config.SNAPSHOT_DIR,
        config.CACHE_DIR,
        config.BACKUP_DIR,
    ):
        if not d.is_dir():
            failures.append(f"用户目录未创建: {d}")
    if not config.SNAPSHOT_CLI.is_file():
        failures.append(f"快照 CLI 缺失: {config.SNAPSHOT_CLI}")

    failures += check_detector()
    failures += check_platform()
    failures += check_state_machine()

    for name, check in (
        ("log_manager", check_log_manager),
        ("engine_service", check_engine_service),
        ("manifest", check_manifest),
        ("schemes", check_schemes),
        ("scheme_service", check_scheme_service),
        ("doctor", check_doctor),
        ("apt_mirror", check_apt_mirror),
        ("theme", check_theme),
        ("launch_marker", check_launch_marker),
        ("launcher_script", check_launcher_script),
        ("deb_depends", check_deb_depends),
        ("i18n", check_i18n),
        ("watchdog", check_watchdog),
        ("deploy_guide", check_deploy_guide),
        ("skin", check_skin),
        ("appearance", check_appearance),
        ("theme_import", check_theme_import),
        ("snapshot_service", check_snapshot_service),
    ):
        _run_check(name, check)

    _run_check("window", check_window)

    if not _APP_HOLDER:
        failures.append("QApplication 未创建，跳过 runner 与安装流程检查")
    else:
        # 动效检查需要 QWidget，必须排在 check_window（创建 QApplication）之后
        _run_check("animation", check_animation)
        # 皮肤卡片检查同样需要 QWidget（SchemeCard），排在 QApplication 创建后
        _run_check("skin_ui", check_skin_ui)
        # 外观页（fcitx5 主题 / ibus 外观项）离屏检查
        _run_check("appearance_ui", check_appearance_ui)
        # 皮肤主窗口端到端（runner 执行 CLI + 记账 + 卸载）
        _run_check("skin_flow", check_skin_flow)
        # 外观主窗口端到端（主题切换/还原 + ibus 应用/还原，路径注入防污染真机）
        _run_check("appearance_flow", check_appearance_flow)
        _run_check("runner", check_runner)
        _run_check("scheme_install_flow", check_scheme_install_flow)
        _run_check("snapshot_cli", check_snapshot_cli)

    if failures:
        print("SMOKE FAILED")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(
        f"SMOKE OK — {config.APP_NAME} v{config.VERSION} "
        "资源/检测/平台回退+GL渲染决策/状态机/日志/引擎装卸/方案装卸/manifest/快照备份/环境体检/apt镜像/部署引导/深浅色主题/启动标记/launcher重试链/deb依赖/动效/i18n/窗口验证通过"
        "（含皮肤装卸/切换/默认、fcitx5主题切换+自动重启生效+ibus外观项切换与还原）"
    )
    return 0


if __name__ == "__main__":
    # PyQt 在解释器退出阶段析构 QWidget 偶发段错误（与业务无关），
    # 冒烟测试已拿到全部断言结果，显式 flush 后直接退出。
    _code = main()
    sys.stdout.flush()
    os._exit(_code)
