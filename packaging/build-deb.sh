#!/usr/bin/env bash
# 构建 .deb 安装包（Phase 6 会细化依赖与图标）
# 用法：bash packaging/build-deb.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(grep -Po '(?<=^VERSION = ")[^"]+' "$ROOT/config.py")"
# 优先用 dpkg 取架构；不可变系统（OSTree/磐石）封装的 dpkg 在 re-exec 时
# 会尝试重挂只读的 /usr 并报 "operation not permitted"，此时回退 uname -m。
ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
if [ -z "$ARCH" ]; then
    case "$(uname -m)" in
        x86_64)  ARCH=amd64  ;;
        aarch64) ARCH=arm64  ;;
        armv7*)  ARCH=armhf  ;;
        riscv64) ARCH=riscv64 ;;
        i386|i686) ARCH=i386 ;;
        *) ARCH="$(uname -m)" ;;
    esac
fi
OUT="$ROOT/build"
mkdir -p "$OUT"

# 暂挂目录每次构建都取 mktemp 全新目录（v1.0.5 构建事故修复）：
# 此前用固定 build/deb + rm -rf 清场——宿主 safe-delete 钩子对批量删除按
# 轮次累计计数并拦截，rm 被拦截时（2>/dev/null || true 静默吞掉）旧暂挂树
# 残留，tar 解包将新旧内容合并：实测把上一版 .smoke-tmp 的 54 个条目打进了
# v1.0.5 deb。mktemp 从根上杜绝合并，全程无需任何删除操作。
BUILD="$(mktemp -d "$OUT/stage-XXXXXX")"

# dpkg-deb 压缩阶段使用 TMPDIR；部分环境 /tmp 是小容量 tmpfs，改用构建目录
mkdir -p "$OUT/tmp"
export TMPDIR="$OUT/tmp"

mkdir -p "$BUILD/DEBIAN" "$BUILD/usr/lib/rime-installer" \
         "$BUILD/usr/bin" "$BUILD/usr/share/applications"

# 应用本体（排除开发期产物；.Trash* 为沙箱删除转存目录，任何层级都排除）
# .smoke-tmp 为冒烟测试临时目录（CLI 子进程用例不自清，2026-10-15 实测有残留）；
# tools/ 下只有被应用以子进程调用的三个脚本入包（appearance_apply / skin_apply /
# snapshot_cli），其余为开发期辅助：diag_layout（一次性诊断）、rerun_flow_checks
# （冒烟重跑）、preview_settings_mockup / render_doctor_page（离屏出图）。
# design/ 为设计稿渲染产物（5MB），smoke_test.py 与 .gitignore、REQUIREMENTS.md 均为
# 开发期文件，运行时无任何引用，一律不入包。
# packaging/ 只排除构建脚本与 desktop 源文件：icons/ 是运行时资源（config.APP_LOGO
# 设置页 Hero 高清图标指向 packaging/icons/rime-installer-512.png，v1.0.27 曾整目录
# 排除，装机后该路径缺失只能回退主题图标），必须随应用树发布。
# v1.0.36：docs/（README 引用的实机截图 2.4MB）、图片/（开发期截图 dump 5.3MB）、
# 故障日志/（真机故障日志 100KB）、store-assets/（商店上架素材：截图/文案/抓图脚本
# 3.7MB）同属开发/运营物料，运行时零引用（grep 核过），排除——v1.0.36 构建时发现
# 这四个目录被打进 deb（store-assets 为 v1.0.35 之后新建，排除名单未覆盖），
# 白增约 11.4MB 且把内部抓图脚本发给用户。
cd "$ROOT"
tar --exclude='./build' --exclude='./.git' --exclude='./.workbuddy' \
    --exclude='./rayburst-main' --exclude='./.venv' \
    --exclude='./resources/plum/package' --exclude='.Trash*' \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='./.smoke-tmp' --exclude='./tools/diag_layout.py' \
    --exclude='./tools/rerun_flow_checks.py' \
    --exclude='./tools/preview_settings_mockup.py' \
    --exclude='./tools/render_doctor_page.py' \
    --exclude='./packaging/build-deb.sh' --exclude='./packaging/rime-installer.desktop' \
    --exclude='./smoke_test.py' --exclude='./.gitignore' \
    --exclude='./REQUIREMENTS.md' --exclude='./design' \
    --exclude='./docs' --exclude='./图片' --exclude='./故障日志' \
    --exclude='./store-assets' \
    --exclude='./New-Logo.png' --exclude='./preview-*.png' \
    --exclude='./.smoke-out.log' --exclude='./.smoke-*.log' \
    --exclude='./fcitx5-mellow-themes-main.zip' -cf - . | tar -xmf - -C "$BUILD/usr/lib/rime-installer"

cat > "$BUILD/DEBIAN/control" <<EOF
Package: rime-installer
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: RIME Installer Team <noreply@example.com>
Depends: python3 (>= 3.11), python3-pyqt6, python3-pyqt6.qtsvg, qt6-qpa-plugins, libgl1, libegl1, libxkbcommon0, libglib2.0-0t64 | libglib2.0-0, curl, tar, file
Section: utils
Priority: optional
Description: 中州韵输入法安装器 (RIME Installer)
 Graphical installer for the RIME input method engine and input schemata.
 Supports X11 and Wayland, with built-in aria2 accelerated downloads,
 plum (东风破) schema management, snapshot backup and clean uninstall.
 Works on Ubuntu 24.04+, Linux Mint 22+ and Deepin 25.2+.
 Version support is family/baseline matched (see core/compat.py), so later
 system releases keep working without an installer update.
EOF

cat > "$BUILD/usr/bin/rime-installer" <<'EOF'
#!/usr/bin/env bash
# 启动兜底（v1.0.2）：桌面环境下 Terminal=false，Python 层未捕获的崩溃会写进
# stderr；重定向到日志目录留痕，超过 1MB 自动截断。
# 点击图标后「秒退且无窗口显示」时按 默认 → xcb → wayland+软渲染 顺序重试
# （最多 3 次）：启动成功 = 进程退出码为 0，或窗口成功显示过（launch-ok 标记，
# 含用户主动关闭）。平台主决策在 main.py 内 core/platform.py，此处只做兜底。
# 路径必须与 config.USER_DATA_DIR（Path.home() 推导）逐字保持一致：
# 应用侧 launch-ok 标记与日志都按 HOME 推导，改这里先改 config.py
LOG_DIR="${HOME:-/tmp}/.local/share/rime-installer/logs"
MARKER="${HOME:-/tmp}/.local/share/rime-installer/launch-ok"
APP_MAIN="/usr/lib/rime-installer/main.py"
mkdir -p "$LOG_DIR" 2>/dev/null
if [ -f "$LOG_DIR/launcher.log" ] && [ "$(stat -c%s "$LOG_DIR/launcher.log" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    : > "$LOG_DIR/launcher.log"
fi

# 预检（v1.0.3）：Ubuntu 的 PyQt6 把 SVG 绑定放在独立包 python3-pyqt6.qtsvg，
# 缺失时 qfluentwidgets import 期即秒崩（窗口从未创建）；失败才写提示，成功不写
python3 -c "import PyQt6.QtWidgets, PyQt6.QtSvg" 2>/dev/null || echo "[预检] PyQt6 模块导入失败，请执行: sudo apt install python3-pyqt6 python3-pyqt6.qtsvg" >> "$LOG_DIR/launcher.log"

# 保留桌面入口传入的参数（Exec=rime-installer 通常无参数）
APP_ARGS=("$@")
LAST_CODE=0

# 记录一次尝试：尝试标签 / QT_QPA_PLATFORM / LIBGL_ALWAYS_SOFTWARE / 退出码 / 标记是否写入
log_attempt() {
    local label="$1" code="$2" marker="无"
    [ -f "$MARKER" ] && marker="有"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 尝试=${label} QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-未设置} LIBGL_ALWAYS_SOFTWARE=${LIBGL_ALWAYS_SOFTWARE:-未设置} 退出码=${code} launch-ok标记=${marker}" >> "$LOG_DIR/launcher.log"
}

# 跑一次应用：启动前删掉旧标记，退出码存全局 LAST_CODE（$? 不在条件判断间丢失）
run_attempt() {
    local label="$1"
    rm -f "$MARKER" 2>/dev/null
    python3 "$APP_MAIN" "${APP_ARGS[@]}" 2>>"$LOG_DIR/launcher.log"
    LAST_CODE=$?
    log_attempt "$label" "$LAST_CODE"
}

# 第 1 次：默认环境启动（不预设 QT_QPA_PLATFORM，由 main.py 内平台逻辑决策）
run_attempt "默认环境"
if [ "$LAST_CODE" -eq 0 ] || [ -f "$MARKER" ]; then
    exit "$LAST_CODE"
fi

# 第 2 次：秒退且无标记 → 强制 xcb（XWayland 路径）
QT_QPA_PLATFORM=xcb
export QT_QPA_PLATFORM
run_attempt "xcb 重试"
if [ "$LAST_CODE" -eq 0 ] || [ -f "$MARKER" ]; then
    exit "$LAST_CODE"
fi
unset QT_QPA_PLATFORM

# 第 3 次：仅纯 Wayland 场景（无 XWayland）→ wayland 插件 + 软件渲染
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    QT_QPA_PLATFORM=wayland
    LIBGL_ALWAYS_SOFTWARE=1
    export QT_QPA_PLATFORM LIBGL_ALWAYS_SOFTWARE
    run_attempt "wayland+软渲染重试"
    code3=$LAST_CODE
    unset QT_QPA_PLATFORM LIBGL_ALWAYS_SOFTWARE
    if [ "$code3" -eq 0 ] || [ -f "$MARKER" ]; then
        exit "$code3"
    fi
fi

# 全部失败：返回最后退出码
exit "$LAST_CODE"
EOF
chmod +x "$BUILD/usr/bin/rime-installer"

install -m 644 "$ROOT/packaging/rime-installer.desktop" \
    "$BUILD/usr/share/applications/rime-installer.desktop"

# 应用图标：hicolor 主题多尺寸安装，桌面环境按需选取（Icon=rime-installer）
# v1.0.25：补 16/24/32 三档——面板、任务栏、Alt-Tab 列表按实际显示像素取图，
# 缺小尺寸时会拿 48 或 64 硬缩，边缘发糊（韵字笔画在小尺寸最先糊掉）
for size in 16 24 32 48 64 128 256 512; do
    mkdir -p "$BUILD/usr/share/icons/hicolor/${size}x${size}/apps"
    install -m 644 "$ROOT/packaging/icons/rime-installer-${size}.png" \
        "$BUILD/usr/share/icons/hicolor/${size}x${size}/apps/rime-installer.png"
done

# root 下构建时统一属主；非 root（开发机）容忍失败，条目属主为当前用户
if [ "$(id -u)" = "0" ]; then
    chown -R root:root "$BUILD"
else
    echo "警告：非 root 用户构建，包内文件属主为 $(id -un)（正式发布请在 root 或 fakeroot 下构建）"
fi
mkdir -p "$OUT"
dpkg-deb --build "$BUILD" "$OUT/rime-installer_${VERSION}_${ARCH}.deb"
echo "Built: $OUT/rime-installer_${VERSION}_${ARCH}.deb"
# 暂挂目录尽力清理（被 safe-delete 钩子拦截时留痕无害：build/ 不入包，
# 下次构建用新的 mktemp 目录，不会与本次内容合并）
rm -rf "$BUILD" 2>/dev/null || true
