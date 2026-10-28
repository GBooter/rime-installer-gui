"""部署引导（F11）：重启输入法框架、打开配置工具、触发重新部署。

关键工程约束：框架进程（ibus-daemon/fcitx5）是常驻进程，若由 QProcess 直接
start 会一直占用 runner 导致任务永不完成。因此所有启动命令统一
`setsid ... &` 脱离进程组，命令本身立即返回，runner 正常收尾。

v1.0.6：rime 部署是「惰性」的——重启 ibus-daemon 只注册引擎、不实例化引擎，
只有用户真正切换到「中文(Rime)」输入源时 ibus-engine-rime 才会跑部署。
真机实证：连续两次「删 build + 重启框架」后 build 目录依旧不存在。
因此「重新部署」升级为**主动部署**：直接调用 rime_deployer --build 编译
生成 build/（rime_deployer 随 librime/ibus-rime 安装），失败再重启框架兜底。

v1.0.26（审计修复）：rime_deployer 属于 librime-bin，Ubuntu 的 ibus-rime /
fcitx5-rime **都不依赖**它（noble 文件清单实证），本安装器只装单个引擎包。
旧脚本先 `rm -rf build` 再判断 deployer 是否存在：缺 deployer 时删了可用的
build、只重启框架、`echo 已完成` 且退出码 0 —— 界面报「已修复」、体检仍
BAD，属于拒绝过的「假成功」。现改为：编译进 `<build>.rime-new`，成功且
非空才替换 build/；缺 deployer 时**不删任何文件**，由框架重启触发部署并
轮询确认；任何失败一律 exit 1，让 runner 报失败并给出定向建议。

v1.0.37（UOS 25 真机反馈，手动安装残留 + 首次部署不生效）：
1. **可写预检**：手动装过 rime 的机器，用户目录常是 root 属主（sudo cp
   残留）——读得出来、写不进去，rime_deployer 只会失败在一句看不懂的
   错误上。脚本先建目录 + 实测写入探针，不可写时直接给出 chown 命令。
2. **强制全量重建**：走框架兜底时先 `touch default.custom.yaml`——
   DetectModifications 按 mtime 判断，触碰后引擎重启必然走全量
   WorkspaceUpdate（Deepin 25/UOS 25「装完方案首次部署不生效」的已知
   变通方法，rime-ice#1439）。
3. **轮询验真**：兜底轮询从「目录存在」改为「build 里有比重启时新的
   文件」（find -newermt）——旧 build 原样存在时旧实现会瞬间假报成功，
   现在必须等引擎真的重建；超时 30s（rime-ice 大词库冷部署要十几秒）。
4. **失败不放弃**：rime_deployer 编译失败不再直接 exit 1，清理 staging
   后走同一套框架兜底，build 确实被刷新才算成功。
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from core.runner import Step

# rime 共享数据目录（rime-data 包在 Ubuntu/Debian/Deepin 的常见位置）
_SHARED_DATA_CANDIDATES = (
    "/usr/share/rime-data",
    "/usr/share/rime",
    "/usr/share/fcitx5/rime-data",
    "/usr/local/share/rime-data",
)

# rime_deployer 的实际落点（PATH 之外的兜底；.desktop 启动时 PATH 可能被裁剪）
_DEPLOYER_CANDIDATES = (
    "/usr/bin/rime_deployer",
    "/usr/local/bin/rime_deployer",
    "/usr/lib/ibus-rime/rime_deployer",
)


def find_deployer() -> str | None:
    """定位 rime_deployer：先查 PATH，再查发行版常见绝对路径。"""
    found = shutil.which("rime_deployer")
    if found:
        return found
    for candidate in _DEPLOYER_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def rime_shared_data_dir() -> str:
    for candidate in _SHARED_DATA_CANDIDATES:
        if Path(candidate).is_dir():
            return candidate
    return _SHARED_DATA_CANDIDATES[0]


def build_deploy_script(frontend: str, rime_dir) -> str:
    """主动部署脚本：编译到 staging → 校验 → 替换 build/ → 重启框架（v1.0.26）。

    - rime_deployer 用法：--build <user_data_dir> <shared_data_dir> <staging_dir>；
      staging 用 `<build>.rime-new`，**不再先删现有 build**（失败时用户的原
      配置仍可继续用，这是旧实现最伤人的地方）；
    - 缺 deployer（Ubuntu 未装 librime-bin）时不删任何文件，改为重启框架并
      轮询 build/ 是否被引擎重建，超时则 exit 1；
    - v1.0.37：先做目录可写预检（root 残留给 chown 指引）；deployer 编译
      失败也走框架兜底（touch 强制全量重建 + 重启 + find -newermt 轮询
      验真），build 确实被刷新才算成功——拒绝「旧 build 原样存在就报成功」。
    """
    build_dir = Path(rime_dir) / "build"
    staging = Path(str(build_dir) + ".rime-new")
    deployer = find_deployer()
    shared_candidates = " ".join(shlex.quote(c) for c in _SHARED_DATA_CANDIDATES)
    deployer_candidates = " ".join(shlex.quote(c) for c in _DEPLOYER_CANDIDATES)
    # 冒烟/自动化测试必须能「只构建、不碰输入法」：默认重启会杀掉开发者本机的
    # fcitx5（2026-10-18 实测：跑真机部署脚本把在位 fcitx5 杀掉了）。
    no_restart = os.environ.get("RIME_INSTALLER_NO_RESTART") == "1"
    start = (
        "echo '[测试模式] 跳过重启输入法框架'"
        if no_restart
        else _start_cmd(frontend)
    )

    lines = [
        f"BUILD={shlex.quote(str(build_dir))}",
        f"NEW={shlex.quote(str(staging))}",
        f"USERDIR={shlex.quote(str(rime_dir))}",
        f"SHARED={shlex.quote(rime_shared_data_dir())}",
        f"DEPLOYER={shlex.quote(deployer or '')}",
        # v1.0.37：目录就绪 + 可写预检。手动安装残留（sudo cp 进用户目录）
        # 会让目录变成 root 属主：读得出来、写不进去，deployer 的报错完全
        # 看不懂。这里实测写入探针（重定向建文件，不用 touch——utimes 在
        # 只读挂载/受限环境会失败，误判成不可写），不可写直接给 chown 命令。
        'mkdir -p "$USERDIR" 2>/dev/null || true',
        'if [ ! -d "$USERDIR" ]; then',
        '  echo "配置目录不存在且无法创建：$USERDIR" >&2',
        "  exit 1",
        "fi",
        'writable=0',
        'if [ -w "$USERDIR" ] && (: > "$USERDIR/.deploy-probe") 2>/dev/null; then',
        "  writable=1",
        "fi",
        'rm -f "$USERDIR/.deploy-probe" 2>/dev/null',
        'if [ "$writable" -ne 1 ]; then',
        '  echo "配置目录不可写：$USERDIR" >&2',
        '  echo "若曾用 sudo 向该目录复制过文件（手动安装的常见残留），请执行：sudo chown -R \\"$USER\\" \\"$USERDIR\\"" >&2',
        "  exit 1",
        "fi",
        # v1.0.8：共享数据目录运行时二次探测（发行版安装位置可能不同）
        'if [ ! -d "$SHARED" ]; then',
        f"  for d in {shared_candidates}; do",
        '    [ -d "$d" ] && SHARED="$d" && break',
        "  done",
        "fi",
        # v1.0.26：PATH 被裁剪时（.desktop/pkexec）再按绝对路径兜一次
        'if [ -z "$DEPLOYER" ]; then',
        f"  for c in {deployer_candidates}; do",
        '    [ -x "$c" ] && DEPLOYER="$c" && break',
        "  done",
        "fi",
        'if [ -z "$DEPLOYER" ]; then',
        '  DEPLOYER="$(command -v rime_deployer 2>/dev/null || true)"',
        "fi",
        "built=0",
        'if [ -n "$DEPLOYER" ]; then',
        '  rm -rf "$NEW"',
        '  if "$DEPLOYER" --build "$USERDIR" "$SHARED" "$NEW"; then',
        '    if [ -z "$(ls -A "$NEW" 2>/dev/null)" ]; then',
        '      echo "rime_deployer 退出码为 0 但没有产出任何文件，保留原有 build 目录，改用重启框架兜底" >&2',
        '      rm -rf "$NEW"',
        "    else",
        '      rm -rf "$BUILD" && mv "$NEW" "$BUILD"',
        '      echo "已重新生成 build（$BUILD）"',
        "      built=1",
        "    fi",
        "  else",
        '    echo "rime_deployer 编译失败（详见上方输出），保留原有 build 目录，改用重启框架兜底" >&2',
        '    rm -rf "$NEW"',
        "  fi",
        "else",
        '  echo "未找到 rime_deployer（部分发行版需另装 librime-bin）：保留现有 build，改用重启框架由引擎自行部署"',
        "fi",
    ]
    if no_restart:
        lines += [
            'if [ "$built" -eq 1 ]; then',
            '  echo "[测试模式] 跳过重启框架（build 已由 rime_deployer 生成）"',
            "else",
            '  echo "[测试模式] 跳过强制重建触碰、重启框架与部署轮询"',
            "fi",
            "echo '已完成'",
        ]
        return "\n".join(lines)

    lines += [
        # 两条路都要重启框架：built=1 时新 build 要重启后才被引擎加载；
        # built=0 时靠重启触发引擎自行部署。
        'if [ "$built" -eq 1 ]; then',
        "  " + start,
        "  echo '已完成'",
        "else",
        # v1.0.37：强制引擎全量重建。DetectModifications 按源文件 mtime
        # 判断是否需要 WorkspaceUpdate——给 default.custom.yaml 追加重建
        # 标记后，引擎重启必然全量重建（Deepin 25/UOS 25「装完方案首次
        # 部署不生效」的已知变通方法，见 rime-ice#1439）。用追加注释而非
        # touch：内容只多一行注释（rime 忽略），mtime 必然更新，且不依赖
        # utimes（只读挂载/受限环境下 touch 会失败）。
        '  if [ -f "$USERDIR/default.custom.yaml" ]; then',
        '    printf \'\\n# rime-installer 强制重新部署标记（可删除） %s\\n\' "$(date \'+%Y-%m-%d %H:%M:%S\')" >> "$USERDIR/default.custom.yaml" 2>/dev/null || true',
        "  else",
        '    printf \'# rime-installer 强制重新部署标记（可删除）\\n\' > "$USERDIR/default.custom.yaml" 2>/dev/null || true',
        "  fi",
        '  echo "已更新 default.custom.yaml 强制引擎全量重建"',
        "  " + start,
        # 轮询验真：必须是「比重启时更新的文件」，不能只判目录存在——
        # 旧 build 原样存在时，旧实现会瞬间假报「框架已自行生成 build」。
        '  stamp=$(date +%s)',
        '  i=0',
        '  while [ "$i" -lt 30 ]; do',
        '    if [ -d "$BUILD" ] && [ -n "$(find "$BUILD" -maxdepth 1 -newermt "@$stamp" -print -quit 2>/dev/null)" ]; then',
        '      echo "框架已重新生成 build（$BUILD）"',
        "      echo '已完成'",
        "      exit 0",
        "    fi",
        '    sleep 1; i=$((i+1))',
        "  done",
        '  echo "重启框架后 30s 内 build 未被刷新：引擎可能仍在后台部署，也可能不会自动部署" >&2',
        '  echo "请安装 librime-bin（sudo apt install librime-bin）后重试；或在输入法里切换一次 Rime 触发部署" >&2',
        "  exit 1",
        "fi",
    ]
    return "\n".join(lines)


def _start_cmd(frontend: str) -> str:
    if frontend == "ibus-rime":
        return "pkill -x ibus-daemon 2>/dev/null; sleep 0.3; setsid ibus-daemon -drxR >/dev/null 2>&1 &"
    if frontend == "fcitx5-rime":
        return "pkill -x fcitx5 2>/dev/null; sleep 0.3; setsid fcitx5 -d >/dev/null 2>&1 &"
    return "pkill -x fcitx 2>/dev/null; sleep 0.3; setsid fcitx -d >/dev/null 2>&1 &"


def config_tool_command(frontend: str) -> str:
    """打开输入法配置工具的命令（添加/管理输入法入口）。"""
    if frontend == "ibus-rime":
        return "setsid ibus-setup >/dev/null 2>&1 &"
    if frontend == "fcitx5-rime":
        return "setsid fcitx5-configtool >/dev/null 2>&1 &"
    return "setsid fcitx-configtool >/dev/null 2>&1 &"


def build_restart_steps(frontend: str) -> list[Step]:
    """重启输入法框架（触发 rime 自动部署）。"""
    return [
        Step(
            f"重启输入法框架（{frontend}）",
            ["bash", "-c", _start_cmd(frontend) + "\necho '已发送重启指令'"],
            weight=1,
        )
    ]


def build_redeploy_steps(frontend: str, rime_dir) -> list[Step]:
    """主动部署：rime_deployer 编译生成 build + 重启框架（v1.0.6）。"""
    return [
        Step(
            "重新部署（rime_deployer 编译 + 重启框架）",
            ["bash", "-c", build_deploy_script(frontend, rime_dir)],
            weight=1,
            hint_key="msg.deploy_failed",
        )
    ]


def build_open_config_tool_steps(frontend: str) -> list[Step]:
    """打开框架配置工具（ibus-setup / fcitx5-configtool）。"""
    tool = "ibus-setup" if frontend == "ibus-rime" else (
        "fcitx5-configtool" if frontend == "fcitx5-rime" else "fcitx-configtool"
    )
    return [
        Step(
            f"打开输入法配置工具（{tool}）",
            ["bash", "-c", config_tool_command(frontend) + f"\necho '已启动 {tool}'"],
            weight=1,
        )
    ]
