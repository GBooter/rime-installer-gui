"""全局配置：版本号、路径常量、外部资源定位。

版本号是唯一事实来源，所有功能落地在此 bump（见 REQUIREMENTS.md 执行计划）。
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

APP_NAME = "中州韵输入法安装器"
APP_NAME_EN = "RIME Installer"
APP_ID = "rime-installer"
VERSION = "1.0.35"
AUTHOR = "Feelup"
# 适用系统：实测最低版本 + 显示服务端（X11 / Wayland）。
# v1.0.30：改为**区间式**（下限定死、上沿不封顶）——版本通配判定见
# core/compat.py（家族 + 最低版本元组比较，不枚举具体版本），用户系统
# 升级到更高版本（Ubuntu 26.04 之后、Mint 23 等）无需改代码即自动落入
# 支持区间；具体「当前系统是否受支持」由设置页按运行时探测结果展示。
SUPPORTED_SYSTEMS = "Ubuntu 24.04 及以上 / Linux Mint 22 及以上 / Deepin 25.2 及以上（X11 / Wayland）"


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PROJECT_ROOT = _project_root()
RESOURCES_DIR = PROJECT_ROOT / "resources"
# 窗口/标题栏图标：源码树随应用安装到 /usr/lib/rime-installer（deb 会整树拷贝），
# 缺失时调用方回退 QIcon.fromTheme(APP_ID)（hicolor 多尺寸已随 deb 安装）
APP_ICON = PROJECT_ROOT / "packaging" / "icons" / "rime-installer-64.png"
# 设置页 Hero 高清图标（v1.0.28）：取 512 源图按屏幕 DPR 重采样——HiDPI 屏
# （实测约 2.7×）下 88px 需 ~240 物理像素，用 64 档放大必糊。deb 刻意保留
# packaging/icons 随应用树发布（见 packaging/build-deb.sh 排除规则），装机后
# 路径依然存在；缺失时界面回退 QIcon.fromTheme(APP_ID)。
APP_LOGO = PROJECT_ROOT / "packaging" / "icons" / "rime-installer-512.png"

# aria2-next 下载内核（来自 rayburst-main/src-tauri/binaries/）
ARIA2_BIN_X86_64 = RESOURCES_DIR / "bin" / "aria2-next-x86_64-unknown-linux-gnu"
ARIA2_BIN_AARCH64 = RESOURCES_DIR / "bin" / "aria2-next-aarch64-unknown-linux-gnu"
ARIA2_CONF = RESOURCES_DIR / "bin" / "aria2.conf"

# vendored 东风破 plum（github.com/rime/plum）
# PLUM_DIR 的实际取值在下方 USER_DATA_DIR 定义后由 _resolve_plum_dir() 解析，
# 以保证 plum 运行时缓存（package/）落在可写目录，详见该函数的说明。

# 用户数据目录（manifest、日志、快照）
USER_DATA_DIR = Path.home() / ".local" / "share" / APP_ID
USER_CONFIG_DIR = Path.home() / ".config" / APP_ID
DB_PATH = USER_DATA_DIR / "manifest.db"
LOG_DIR = USER_DATA_DIR / "logs"
SNAPSHOT_DIR = USER_DATA_DIR / "snapshots"
CACHE_DIR = USER_DATA_DIR / "cache"
BACKUP_DIR = USER_DATA_DIR / "backups"
# 主题导入仓库解压缓存（v1.0.21：按链接导入 fcitx5 主题包，一次请求一个子目录）
THEME_IMPORT_CACHE = USER_DATA_DIR / "cache" / "theme-import"

# ---------------------------------------------------------------------------
# vendored 东风破 plum（github.com/rime/plum）运行时目录解析
# ---------------------------------------------------------------------------
# plum 加速路径会在 <plum>/package/ 下预置下载的方案 tarball（解压预置），
# 而 Deepin 25 等不可变根把 /usr 挂成只读，bundled plum 装到
# /usr/lib/rime-installer/resources/plum 后无法写入 → 方案市场安装失败
# （日志：mkdir ... 只读文件系统）。因此 PLUM_DIR 必须落在可写目录：
#   - bundled 可写（dev/源码运行）→ 直接用，行为不变；
#   - 只读安装树 → 同步到用户目录副本 ~/.local/share/rime-installer/plum，
#     rime-install 用 $0 推导 root_dir，副本里的 package/ 自然可写。
def _plum_dir_writable(path: Path) -> bool:
    """稳健可写判定：实测写入探针，能识别只读挂载（os.W_OK 只看权限位不可靠）。"""
    if not path.is_dir():
        return False
    probe = path / ".rw-probe"
    try:
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def _sync_plum_to(src: Path, dst: Path) -> None:
    """把 bundled plum 整树同步到可写副本；仅在缺失或 bundled 更新时复制，
    避免每次启动都复制。忽略 package/（运行时缓存，安装前会被 clear_package_cache 清掉、
    bundled 本就不含它）与 .rw-probe（可写探针残留）。"""
    dst.mkdir(parents=True, exist_ok=True)
    src_sig = (src / "rime-install").stat().st_mtime if (src / "rime-install").exists() else 0.0
    dst_sig = (dst / "rime-install").stat().st_mtime if (dst / "rime-install").exists() else 0.0
    if dst_sig >= src_sig:
        return
    shutil.copytree(
        src, dst, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("package", ".rw-probe"),
    )


def _resolve_plum_dir() -> Path:
    bundled = RESOURCES_DIR / "plum"
    if _plum_dir_writable(bundled):
        return bundled
    if not bundled.is_dir():
        return bundled  # 兜底：bundled 缺失，至少不崩
    # 只读安装树 → 同步到用户目录副本。同步失败（home 不可写/磁盘满/权限）
    # 绝不能让 import config 崩溃：回退 bundled 保证应用可启动，方案市场的
    # 写入错误改在安装步骤中暴露（有日志），而非启动即崩。
    copy = USER_DATA_DIR / "plum"
    try:
        _sync_plum_to(bundled, copy)
        if (copy / "rime-install").exists():
            return copy
    except OSError as exc:
        print(f"[config] plum 副本同步失败，回退 bundled 目录：{exc}", file=sys.stderr)
    return bundled


PLUM_DIR = _resolve_plum_dir()
PLUM_RIME_INSTALL = PLUM_DIR / "rime-install"
# 启动成功标记：窗口 show() 后写入 ISO 时间戳，launcher 据此判断
# 「窗口确实显示过」（含用户主动关闭），避免把正常退出误判为启动失败
LAUNCH_OK_MARKER = USER_DATA_DIR / "launch-ok"

# 快照 CLI（备份/恢复由 QProcess 驱动，复用 runner 的进度与日志）
SNAPSHOT_CLI = PROJECT_ROOT / "tools" / "snapshot_cli.py"
# 皮肤 CLI（F15 皮肤部分：写 default.custom.yaml 的配色标记块）
SKIN_CLI = PROJECT_ROOT / "tools" / "skin_apply.py"
# 外观 CLI（v1.0.13：fcitx5 classicui 主题切换 + ibus 全局外观项）
APPEARANCE_CLI = PROJECT_ROOT / "tools" / "appearance_apply.py"

# rime 用户目录随前端而变（plum frontend.sh 同款映射；相对路径便于测试与安全清理）
RIME_RELATIVE_DIRS = {
    "ibus-rime": ".config/ibus/rime",
    "fcitx-rime": ".config/fcitx/rime",
    "fcitx5-rime": ".local/share/fcitx5/rime",
}
RIME_USER_DIRS = {k: Path.home() / v for k, v in RIME_RELATIVE_DIRS.items()}

# apt 引擎包名（按前端选择）
ENGINE_PACKAGES = {
    "ibus-rime": "ibus-rime",
    "fcitx5-rime": "fcitx5-rime",
    "fcitx-rime": "fcitx-rime",
}
# rime_deployer 所属包（v1.0.29）：Ubuntu 24.04/26.04 的 ibus-rime / fcitx5-rime
# **都不依赖** librime-bin（包依赖与文件清单已核），缺它时「重新部署」只能退回
# 「重启框架 + 轮询」，成功率低。安装引擎时顺手装上（best-effort，装不上不判失败）。
ENGINE_DEPLOYER_PACKAGE = "librime-bin"

# 各前端的「顺手安装」附加包（v1.0.35）。目前只有 fcitx4 需要：
# 图形配置工具 fcitx-config-gtk（提供 /usr/bin/fcitx-configtool）**不在**
# fcitx-rime 的依赖链里（其 Recommends 只有 fcitx 守护进程本身，包依赖已核），
# 缺它时「打开配置工具」按钮静默失败——而 fcitx4 添加/上移输入法全靠这个 GUI
# （体检对 fcitx4 只给引导、无一键修复，v1.0.8 决策）。
# fcitx5 无此问题：fcitx5-configtool 由 fcitx5 包的 Recommends
# （fcitx5-config-qt）默认随装；ibus-setup 由 ibus 包提供。
# 安装引擎时 best-effort 一并装上（装不上只警告，不判任务失败）。
FRONTEND_EXTRA_PACKAGES = {
    "fcitx-rime": ["fcitx-config-gtk"],
}

# apt 国内镜像（F13：测速与切换）
APT_MIRRORS = [
    {"name": "官方源", "url": "http://archive.ubuntu.com/ubuntu/", "is_official": True},
    {"name": "阿里云", "url": "http://mirrors.aliyun.com/ubuntu/"},
    {"name": "清华大学", "url": "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/"},
    {"name": "中科大", "url": "https://mirrors.ustc.edu.cn/ubuntu/"},
]

# GitHub 加速镜像（aria2 下载 codeload tarball 时按序探测）
GITHUB_MIRRORS = [
    "https://github.com",
    "https://gh-proxy.com/https://github.com",
    "https://ghfast.top/https://github.com",
]


def aria2_binary() -> Path | None:
    """按当前 CPU 架构返回内置 aria2 二进制路径。"""
    machine = platform.machine()
    if machine in ("x86_64", "amd64"):
        return ARIA2_BIN_X86_64
    if machine in ("aarch64", "arm64"):
        return ARIA2_BIN_AARCH64
    return None


def ensure_user_dirs() -> None:
    for d in (USER_DATA_DIR, USER_CONFIG_DIR, LOG_DIR, SNAPSHOT_DIR, CACHE_DIR, BACKUP_DIR, THEME_IMPORT_CACHE):
        d.mkdir(parents=True, exist_ok=True)
