"""输入法环境变量的持久化配置（v1.0.26）。

为什么不再写 ``~/.xprofile``
----------------------------
v1.0.26 审计在两套真机环境分别核对过：

- Debian 系图形会话由 ``/etc/X11/Xsession`` 驱动，它只 source
  ``~/.xsessionrc``（``40x11-common_xsessionrc``，``USERXSESSIONRC=$HOME/.xsessionrc``），
  全盘 grep 无任何 xprofile 引用；
- Ubuntu GDM + GNOME（X11 与 Wayland）不读 ``~/.xprofile``；
- Deepin 25 的 lightdm 配置是 ``session-wrapper=/etc/X11/Xsession``，
  ``dde-session`` 本身不读 ``.xprofile``。

于是旧实现「写 ``~/.xprofile`` 再提示重新登录」在两家都不生效；而
``run_checks`` 读的是**当前进程**的 ``os.environ``，写完文件后值不会变，
这一项因此永远修不绿（用户只看到「点了修复还是红的」）。

改为写入两处覆盖面最广的位置
----------------------------
1. ``~/.config/environment.d/90-rime-im.conf``
   systemd 的 ``30-systemd-environment-d-generator`` 在 user manager 启动时
   读取该目录；GNOME/KDE 的 Wayland 会话与任何由 ``systemd --user`` 拉起的
   图形会话都会继承。
2. ``~/.xsessionrc``
   lightdm 的 ``session-wrapper=/etc/X11/Xsession`` 会 source 它，
   因此 **Ubuntu X11 与 Deepin 25 X11 都生效**。

两处互补，覆盖 X11 与 Wayland。注意 Deepin 的
``/etc/X11/Xsession.d/00deepin-dde-env`` 会把 systemd 环境导入会话时**排除**
``XMODIFIERS`` / ``GTK_IM_MODULE`` / ``QT_IM_MODULE`` / ``SDL_IM_MODULE``
（避免覆盖 dde-session 自己的判断），所以在 DDE 上真正起作用的是
``.xsessionrc`` 而不是 ``environment.d``——这正是两处都要写的原因。

两处都只在**下次登录**后生效——这是环境变量的固有性质，不是缺陷。写完后
额外调一次 ``dbus-update-activation-environment``，让当前会话中新启动的程序
（例如新开的终端）也能立刻拿到正确值；已在运行的程序仍需重启。

旧的 ``~/.xprofile`` 标记段会在写入时顺手清理（该文件本就不被读取，留着只会
让人误以为配置生效了）。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from core.detector import EXPECTED_ENV

#: environment.d 片段名（安装器独占，整文件重写）
ENV_D_NAME = "90-rime-im.conf"

#: 旧实现写入的文件名（仅用于清理残留标记段）
LEGACY_XPROFILE = ".xprofile"

#: 旧版标记行（精确匹配后才清理，避免误删用户自己写的内容）
LEGACY_HEADER = "# 中州韵输入法安装器：输入法环境变量"
LEGACY_TARGET_PREFIX = "# 目标前端："

#: ``.xsessionrc`` 里的段落标记
MARK_BEGIN = "# >>> rime-installer >>>"
MARK_END = "# <<< rime-installer <<<"

CONF_HEADER = "# 由「中州韵输入法安装器」写入：输入法环境变量"

#: 配置文件状态
STATE_OK = "ok"          # 已写入且与目标前端一致
STATE_MISSING = "missing"  # 尚未写入
STATE_STALE = "stale"    # 已写入但值/前端不一致


def env_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "environment.d"


def env_conf_path(home: Path | None = None) -> Path:
    return env_dir(home) / ENV_D_NAME


def xsessionrc_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".xsessionrc"


def legacy_xprofile_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / LEGACY_XPROFILE


def login_targets(home: Path | None = None) -> list[Path]:
    """写入目标清单（确认框与文案共用，避免路径在 UI 里再写一份）。

    两个文件都在用户主目录下：写入**不需要管理员权限**，删除任意一个
    （或其中的标记段）即可恢复系统默认。
    """
    return [env_conf_path(home), xsessionrc_path(home)]


def expected_vars(frontend: str) -> dict[str, str]:
    """目标前端应有的四个环境变量（未知前端回退 ibus-rime，与 detector 一致）。"""
    return dict(EXPECTED_ENV.get(frontend, EXPECTED_ENV["ibus-rime"]))


def read_configured_vars(home: Path | None = None) -> dict[str, str]:
    """读回 environment.d 片段里的 ``KEY=VALUE``。

    纯文本解析，不引 Qt、不调 systemd——体检跑在 Qt 槽链里，任何外部进程
    都可能拖慢 UI；这里只需知道「上次写了什么」。
    """
    try:
        text = env_conf_path(home).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def detect_state(frontend: str, home: Path | None = None) -> tuple[str, list[str]]:
    """返回 ``(状态, 不一致的键列表)``。

    - ``missing``：配置文件不存在或没有任何 KV；
    - ``stale``：存在但值与本前端期望不符（换过前端、被手工改过）；
    - ``ok``：四个键全部一致。
    """
    expected = expected_vars(frontend)
    configured = read_configured_vars(home)
    if not configured:
        return STATE_MISSING, list(expected)
    wrong = [key for key, value in expected.items() if configured.get(key, "") != value]
    if wrong:
        return STATE_STALE, wrong
    return STATE_OK, []


def _conf_body(frontend: str) -> list[str]:
    """environment.d 片段正文：``KEY=VALUE``，不支持 export/shell 语法。"""
    return [
        f"{CONF_HEADER}（目标前端：{frontend}）",
        "# 下次登录后生效。删除本文件即可恢复系统默认。",
        *[f"{key}={value}" for key, value in expected_vars(frontend).items()],
    ]


def _xsession_body(frontend: str) -> list[str]:
    """``.xsessionrc`` 段落正文：X11 会话由 Xsession source，必须 export。"""
    return [
        MARK_BEGIN,
        f"{CONF_HEADER}（目标前端：{frontend}）",
        "# 下次登录后生效。删除本段即可恢复系统默认。",
        *[f"export {key}={value}" for key, value in expected_vars(frontend).items()],
        MARK_END,
    ]


def _awk_strip_legacy() -> str:
    """清理旧版 ``~/.xprofile`` 标记段的 awk 脚本。

    只删「标记行 + 紧随的 4 个 export 行」，遇到其他内容立刻停手——避免
    误删用户自己写的同名变量（旧实现用无差别 sed 删过，属危险动作）。
    """
    exports = "|".join(EXPECTED_ENV["fcitx5-rime"])
    return (
        "awk 'BEGIN{s=0} "
        f"/^{LEGACY_HEADER}/{{s=1;next}} "
        f"s==1 && /^{LEGACY_TARGET_PREFIX}/{{next}} "
        f"s==1 && /^export ({exports})=/{{next}} "
        "{s=0; print}'"
    )


def build_write_script(frontend: str, home: Path | None = None) -> str:
    """构建幂等写入脚本（供 ``Step`` 经 bash 执行）。

    幂等性：
    - environment.d 片段是安装器专属文件，整文件覆盖；
    - ``.xsessionrc`` 先删标记段再追加，重复执行不会堆叠；
    - ``.xprofile`` 残留只在检测到旧标记时才重写。
    """
    base = home or Path.home()
    conf = env_conf_path(base)
    xsession = xsessionrc_path(base)
    legacy = legacy_xprofile_path(base)
    conf_body = "\n".join(_conf_body(frontend))
    xs_body = "\n".join(_xsession_body(frontend))
    dbus_args = " ".join(f"{k}={v}" for k, v in expected_vars(frontend).items())
    mark_begin = re.escape(MARK_BEGIN)
    mark_end = re.escape(MARK_END)
    return "\n".join(
        [
            "set -e",
            f"CONF={shlex.quote(str(conf))}",
            f"XS={shlex.quote(str(xsession))}",
            f"LEGACY={shlex.quote(str(legacy))}",
            'mkdir -p "$(dirname "$CONF")"',
            "cat > \"$CONF\" <<'RIME_ENV_EOF'",
            conf_body,
            "RIME_ENV_EOF",
            # 不调 touch：某些文件系统（网络家目录/只读挂载）上 utimes 会被拒绝，
            # 用 `>>` 追加即可在缺失时自动创建
            f'sed -i \'/^{mark_begin}/,/^{mark_end}/d\' "$XS" 2>/dev/null || true',
            "cat >> \"$XS\" <<'RIME_XS_EOF'",
            xs_body,
            "RIME_XS_EOF",
            # 清理旧版 ~/.xprofile 残留（文件本身不被任何会话读取）
            f'if [ -f "$LEGACY" ] && grep -q "{LEGACY_HEADER}" "$LEGACY" 2>/dev/null; then',
            f'  {_awk_strip_legacy()} "$LEGACY" > "$LEGACY.rime-tmp" '
            '&& mv "$LEGACY.rime-tmp" "$LEGACY"',
            '  echo "已清理 ~/.xprofile 中旧版写入的段落（该文件不被会话读取）"',
            "fi",
            # 尽力让当前会话中新启动的程序立刻拿到（GNOME/KDE 走 D-Bus 激活）
            f"dbus-update-activation-environment --systemd {dbus_args} 2>/dev/null || true",
            'echo "已写入 $CONF 与 $XS（重新登录后生效）"',
        ]
    )
