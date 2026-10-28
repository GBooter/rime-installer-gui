"""手动安装残留检测与清理（v1.0.37）。

背景（UOS 25 真机反馈）：用户按论坛教程**手动**装过 rime——
`pkexec apt install fcitx5-rime`（不装 librime-bin）、`sudo cp` 方案文件进
用户目录、`sudo` 编辑 `/usr/share/rime-data/default.yaml`——之后再用本安装器
「重新部署」失败。手动安装会留下三类直接打断部署的残留：

1. **属主残留**（``foreign_owner``）：用 sudo 往 rime 用户目录复制过文件，
   目录/文件属主变成 root。体检只查「可读」，这类目录读得出来、写不进去——
   rime_deployer 建不了 ``build.rime-new``，直接 exit 1（截图上「重新部署
   未完成」而各项体检全绿，正是这个形态）。
2. **失效方案引用**（``stale_schema``）：手动编辑 ``default.custom.yaml`` 的
   ``schema_list`` 加过方案，后来方案文件删了/没拷全 → 部署报
   ``missing input schema``，整个部署失败。引用写在**共享** ``default.yaml``
   里时（UOS 教程的标准做法）标记为 ``shared_stale``——系统文件不动，只报告。
3. **暂存残留**（``staging``）：上次部署被强杀留下的 ``build.rime-new``
   （rime 不读它，但会干扰本安装器的 staging 判断）。

清理策略（保守，绝不删用户配置）：
- 属主残留 → ``chown -R`` 回当前用户（需管理员密码，单次 pkexec）；
- 失效引用 → 只删 ``default.custom.yaml`` 里指向失效方案的
  ``- schema: <id>`` 行；**改前留** ``default.custom.yaml.rime-installer-bak``
  （非 .yaml 后缀，rime 不会解析它），绝不整文件删除；
- 暂存残留 → 整个删掉（本安装器自己生成的目录，无用户数据）；
- 清理前先在备份目录打一份整包 zip（目录可读时才做；root 700 目录读不了，
  备份必然失败，那种场景直接进 chown）。

所有文件 IO 包 ``try/except OSError``：本模块跑在体检/页面回调里，
PermissionError 未捕获会让 PyQt6 直接 abort（v1.0.26 教训）。
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from core.deploy_guide import rime_shared_data_dir
from core.engine_service import elevated_command
from core.runner import Step, shell_wrap
from core.snapshot_service import build_backup_step

# ---- 残留类型 ----
FOREIGN_OWNER = "foreign_owner"  # 属主不是当前用户（sudo 复制/运行的残留）
STALE_SCHEMA = "stale_schema"  # schema_list 引用了不存在的方案（用户文件，可清理）
STAGING = "staging"  # 上次部署被强杀留下的 build.rime-new
SHARED_STALE = "shared_stale"  # 失效引用在共享配置里（系统文件，只报告不动手）

#: 暂存目录名（与 deploy_guide 的 staging 命名一致）
STAGING_NAME = "build.rime-new"
#: 改前备份后缀（刻意不用 .yaml——rime 会解析用户目录里所有 *.yaml）
BACKUP_SUFFIX = ".rime-installer-bak"
#: 界面/体检文案的残留类型展示顺序（两处组合共用，防顺序漂移）
DISPLAY_ORDER = (FOREIGN_OWNER, STALE_SCHEMA, STAGING, SHARED_STALE)
#: schema_list 条目行：``- schema: <id>``（容许引号与行尾注释）
_SCHEMA_ENTRY_RE = re.compile(r"^\s*-\s*schema:\s*[\"']?([^\s\"']+)[\"']?\s*(?:#.*)?$")
#: schema_list 键行（任意缩进：default.yaml 在 0 级，default.custom.yaml 在 patch 下）
_LIST_KEY_RE = re.compile(r"^\s*schema_list\s*:")
#: 可安全嵌进 sed 单引号模式的 id（词字符 + . - +）；其余（引号/$ 等）
#: 只报告不自动删——宁可漏清理，不可误删用户配置行
_SAFE_ID_RE = re.compile(r"[\w.+\-]+", re.UNICODE)


@dataclass(frozen=True)
class ResidueItem:
    """一项残留。``manual=True`` 表示只能手动处理（共享配置/特殊字符），
    不进自动清理步骤，只在界面与体检里给指引。``ref`` 是类型特定的键值：
    ``stale_schema`` 时为方案 id（供清理脚本精确删除行）。"""

    kind: str
    detail: str
    path: str = ""
    manual: bool = False
    ref: str = ""


def counts(items: list[ResidueItem]) -> dict[str, int]:
    """按类型计数（界面与体检文案共用）。"""
    result: dict[str, int] = {}
    for item in items:
        result[item.kind] = result.get(item.kind, 0) + 1
    return result


def cleanable(items: list[ResidueItem]) -> list[ResidueItem]:
    """可自动处理的子集（排除 manual）。"""
    return [i for i in items if not i.manual]


# ---- 检测 ----


def scan(rime_dir, shared_dir=None) -> list[ResidueItem]:
    """扫描 rime 用户目录里的手动安装残留；任何异常都按「没发现」处理。"""
    items: list[ResidueItem] = []
    directory = Path(rime_dir) if rime_dir else None
    if directory is None or not _is_dir(directory):
        return items
    try:
        items += _foreign_owner_items(directory)
        items += _staging_items(directory)
        items += _stale_schema_items(directory, shared_dir)
    except OSError:
        # 单个子项探测失败不应带崩整次扫描（体检/页面回调里未捕获即 abort）
        pass
    return items


def _foreign_owner_items(directory: Path) -> list[ResidueItem]:
    """目录自身 + 直接子项的属主检查（深度 1：部署只要求在目录根部可写）。"""
    items: list[ResidueItem] = []
    uid = getattr(os, "getuid", lambda: None)()
    if uid is None:  # 非 POSIX（Windows 开发机）不做属主判断
        return items
    candidates = [directory]
    try:
        candidates.extend(sorted(directory.iterdir()))
    except OSError:
        pass
    for path in candidates:
        try:
            owner = path.stat().st_uid
        except OSError:
            continue
        if owner != uid:
            items.append(
                ResidueItem(
                    FOREIGN_OWNER,
                    f"属主不是当前用户（疑似 sudo 复制/运行残留）：{path}",
                    str(path),
                )
            )
    return items


def _staging_items(directory: Path) -> list[ResidueItem]:
    staging = directory / STAGING_NAME
    try:
        exists = staging.exists()
    except OSError:
        return []
    if not exists:
        return []
    return [
        ResidueItem(
            STAGING,
            f"上次部署被中断留下的暂存目录：{staging}",
            str(staging),
        )
    ]


def _stale_schema_items(directory: Path, shared_dir) -> list[ResidueItem]:
    """schema_list 里引用了不存在方案文件的条目。

    rime 的配置合并规则：``default.yaml`` 被同名用户文件整体顶替，再被
    ``default.custom.yaml`` 的 ``patch`` 修补；**patch 里的 list 是整体替换**
    （不是合并）。因此生效的 schema_list = 用户 custom 的 list（有则用它），
    否则基础 default.yaml 的 list。
    """
    shared = Path(shared_dir) if shared_dir else Path(rime_shared_data_dir())
    custom = directory / "default.custom.yaml"
    base = directory / "default.yaml"
    if not _is_file(base):
        base = shared / "default.yaml"

    custom_ids = _schema_list_ids(custom)
    base_ids = _schema_list_ids(base)
    effective = custom_ids or base_ids
    if not effective:
        return []

    from_custom = bool(custom_ids)
    items: list[ResidueItem] = []
    for schema_id in effective:
        if _schema_exists(schema_id, directory, shared):
            continue
        if from_custom:
            # id 含引号/空格等字符时无法安全嵌进 sed 模式——只报告，不自动删
            safe = bool(_SAFE_ID_RE.fullmatch(schema_id))
            items.append(
                ResidueItem(
                    STALE_SCHEMA,
                    f"方案引用「{schema_id}」找不到对应的 .schema.yaml"
                    f"（部署会报 missing input schema）",
                    str(custom),
                    manual=not safe,
                    ref=schema_id if safe else "",
                )
            )
        else:
            items.append(
                ResidueItem(
                    SHARED_STALE,
                    f"方案引用「{schema_id}」找不到对应的 .schema.yaml，"
                    f"写在共享配置里（需手动编辑）",
                    str(base),
                    manual=True,
                    ref=schema_id,
                )
            )
    return items


def _schema_list_ids(path: Path) -> list[str]:
    """按行解析 yaml 里的 schema_list 条目（不引 yaml 依赖，保住注释与格式）。

    只认规范块状格式：``schema_list:`` 键行之后、缩进更深的
    ``- schema: <id>`` 行；遇到缩进回退即结束。流式/多行等怪异格式解析不到
    ——解析不到就不会误删，最多漏报（由部署失败信息兜底）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ids: list[str] = []
    in_list = False
    list_indent = -1
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if not in_list:
            if _LIST_KEY_RE.match(line):
                in_list = True
                list_indent = indent
            continue
        if indent <= list_indent:
            in_list = False
            continue
        match = _SCHEMA_ENTRY_RE.match(line)
        if match:
            schema_id = match.group(1)
            if schema_id and schema_id not in ids:
                ids.append(schema_id)
    return ids


def _schema_exists(schema_id: str, user_dir: Path, shared_dir) -> bool:
    for base in (user_dir, Path(shared_dir) if shared_dir else None):
        if base is None:
            continue
        try:
            if (base / f"{schema_id}.schema.yaml").is_file():
                return True
        except OSError:
            continue
    return False


# ---- 清理步骤 ----


def build_clean_steps(
    items: list[ResidueItem],
    rime_dir,
    frontend: str = "",
    home: Path | None = None,
    dest_dir: Path | None = None,
) -> list[Step]:
    """把可自动处理的残留变成 runner 步骤（空列表 = 没有可自动处理项）。

    顺序：整包备份（可读时才做）→ chown 修属主（需管理员）→ 清理 yaml 与暂存。
    备份前置：chown/sed 都动用户数据，先留一份可整体还原的 zip。
    ``dest_dir`` 供测试把备份导到临时目录（默认落在安装器备份目录）。
    """
    directory = Path(rime_dir)
    auto = cleanable(items)
    if not auto:
        return []

    steps: list[Step] = []
    if dir_readable(directory):
        steps.append(
            build_backup_step(
                directory,
                frontend,
                packages=(),
                note="清理残留前自动备份",
                dest_dir=dest_dir,
            )
        )
    if any(i.kind == FOREIGN_OWNER for i in auto):
        steps.append(
            Step(
                "修复配置目录属主（chown，需管理员密码）",
                shell_wrap(elevated_command(["bash", "-c", _chown_script(directory, home)])),
                weight=1,
                hint_key="msg.residue_clean_failed",
            )
        )
    if any(i.kind in (STALE_SCHEMA, STAGING) for i in auto):
        stale_ids = sorted({i.ref for i in auto if i.kind == STALE_SCHEMA and i.ref})
        steps.append(
            Step(
                "清理失效方案引用与暂存文件",
                ["bash", "-c", _clean_script(directory, stale_ids)],
                weight=1,
                hint_key="msg.residue_clean_failed",
            )
        )
    return steps


def _chown_script(directory: Path, home: Path | None) -> str:
    """chown -R 回当前用户；目标必须在家目录之下（防提权误伤）。"""
    home_dir = str(home or Path.home())
    return "\n".join(
        [
            "set -e",
            f"DIR={shlex.quote(str(directory))}",
            f"HOME_DIR={shlex.quote(home_dir)}",
            'case "$DIR" in',
            '  "$HOME_DIR"/*) ;;',
            '  *) echo "拒绝：$DIR 不在主目录下，已中止" >&2; exit 1 ;;',
            "esac",
            'chown -R "$(id -u):$(id -g)" "$DIR"',
            'echo "属主已修复为当前用户：$DIR"',
        ]
    )


def _clean_script(directory: Path, stale_ids: list[str]) -> str:
    """删除 default.custom.yaml 里的失效引用行 + 清理暂存目录。

    sed 模式里的 id 经 ``re.escape`` 转义——id 是文件名，可能含 ``.``/`-`
    等正则元音字符，原样嵌入会误删别的行（``a.b`` 会匹配到 ``axb``）。
    """
    lines = [
        "set -e",
        f"DIR={shlex.quote(str(directory))}",
        f'F="$DIR/default.custom.yaml"',
        f'BAK="$F{BACKUP_SUFFIX}"',
    ]
    if stale_ids:
        lines.append('[ -f "$F" ] || { echo "default.custom.yaml 不存在，跳过引用清理"; exit 0; }')
        # 普通 cp（不用 -p）：备份只为兜底还原，不需要保留时间戳；-p 的
        # utimes 在只读挂载/受限环境会失败，把清理步整体带崩
        lines.append('cp "$F" "$BAK"')
        for schema_id in stale_ids:
            pattern = re.escape(schema_id)
            lines.append(
                f"sed -i -e '/^[[:space:]]*-[[:space:]]*schema:[[:space:]]*"
                f"{pattern}[[:space:]]*$/d' \"$F\""
            )
        lines += [
            'before=$(grep -c "schema:" "$BAK" || true)',
            'after=$(grep -c "schema:" "$F" || true)',
            'echo "已从 default.custom.yaml 删除失效方案引用（schema 条目 $before → $after），备份：$BAK"',
            '[ "$after" -gt 0 ] || echo "注意：default.custom.yaml 里已没有任何方案引用，请安装方案后再部署"',
        ]
    lines += [
        f'rm -rf "$DIR/{STAGING_NAME}"',
        'echo "已清理暂存目录与失效引用"',
    ]
    return "\n".join(lines)


# ---- 内部工具 ----


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def dir_readable(path: Path) -> bool:
    """目录是否可列举（确认框用它判断能否先备份；空目录也算可读）。"""
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return True
    except OSError:
        return False
