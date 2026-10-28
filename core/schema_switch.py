"""输入方案切换（v1.0.38）：在已安装的多个方案间一键切换并立即生效。

**机制（已核实）**：
- rime 的当前方案写在用户目录 ``user.yaml`` 的 ``var/previously_selected_schema``
  （原生持久化——用户在输入法里 Ctrl+` 现场切换也是写这里）；
- 可选集合 = 合并后的 ``schema_list``：``default.custom.yaml`` 的 patch **整体
  顶替** base 列表（rime patch 语义，与 ``core/residue.py`` 同款判断）；
- 运行中的引擎**不热加载** user.yaml——改完必须重启框架才生效（fcitx5 没有
  「切换 rime 方案」的 IPC，重启是唯一可靠途径，与「换皮肤」同一机制）。

**与 rime_deployer --set-active-schema 的差别**：那个子命令以**当前工作目录**
为 user_dir 且依赖 librime-bin——恰恰是缺失最多的机器（手动装引擎的 UOS/
Deepin）上没有。因此本模块用 bash 行级改写 user.yaml，不依赖部署工具。

**切换链路**（build_switch_steps）：
1. 若目标不在生效 schema_list → 把「当前生效列表 + 目标」整列表写进
   default.custom.yaml 的 patch（**必须带原列表**：patch 是整体顶替，只写
   目标会静默弄丢预设方案——UOS 教程式共享 default.yaml 机器的典型陷阱）；
2. 写 user.yaml 的 previously_selected_schema；
3. 复用部署引导（rime_deployer 编译 + 重启框架）——新入列的方案要编译、
   新选择要重启后才被引擎加载。

两处 yaml 改写都是「构建时读原文件 → 算好新内容 → bash 备份 + heredoc 覆写」：
行级插入保证用户其余配置字节级保留（sed 三形态插入在 bash 里容易写错），
heredoc 用引号定界符杜绝变量展开。改前统一留 ``*.rime-installer-bak``
（非 .yaml 后缀，rime 不解析）。

所有文件 IO 包 ``try/except OSError``：本模块跑在页面回调/任务构建里，
PermissionError 未捕获会让 PyQt6 直接 abort（v1.0.26 教训）。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from core.deploy_guide import build_redeploy_steps, rime_shared_data_dir
from core.runner import Step

#: user.yaml 里记录当前方案的键
_ACTIVE_KEY = "previously_selected_schema"
_ACTIVE_RE = re.compile(r"^(\s*)previously_selected_schema\s*:\s*.*$")
#: schema_list 键行（任意缩进：default.yaml 在 0 级，custom 在 patch 下）
_LIST_KEY_RE = re.compile(r"^\s*schema_list\s*:")
#: schema_list 条目行
_ENTRY_RE = re.compile(r"^\s*-\s*schema:\s*[\"']?([^\s\"']+)[\"']?\s*(?:#.*)?$")
#: 可安全嵌进 bash 单引号/id 集合的字符（词字符 + . - +）
_SAFE_ID_RE = re.compile(r"[\w.+\-]+", re.UNICODE)
#: heredoc 定界符（用户配置里不可能出现）
_DOC_EOF = "RIME_INSTALLER_SCHEMA_SWITCH_EOF"
#: 改前备份后缀（非 .yaml，rime 不解析）
_BAK_SUFFIX = ".rime-installer-bak"


@dataclass(frozen=True)
class SwitchableSchema:
    """一个可切换的方案（扫盘得到，与安装器记账无关）。"""

    id: str
    name: str
    source: str  # installer（本安装器安装）/ manual（手动安装）/ shared（系统共享）
    in_list: bool  # 是否已在生效 schema_list


@dataclass(frozen=True)
class SchemaGroup:
    """下拉菜单里的一个分组（v1.0.39：按方案包分组）。

    ``key``：分组标识——方案包 key（installer 分组）或固定串
    ``manual`` / ``shared``（无包归属的方案）。
    ``title``：组标题（方案包名 / 「手动安装」/「系统共享」）。
    ``items``：组内的方案列表，顺序即安装顺序（主方案在前）。
    """

    key: str
    title: str
    items: tuple[SwitchableSchema, ...]
    source: str  # installer / manual / shared（决定图标与配色）


def group_by_package(
    items: list[SwitchableSchema],
    package_schemas: dict[str, list[str]] | None = None,
    package_names: dict[str, str] | None = None,
    group_titles: dict[str, str] | None = None,
) -> list[SchemaGroup]:
    """把可切换方案按「所属方案包」分组（v1.0.39）。

    **为什么需要分组**：扫盘得到的是 ``*.schema.yaml`` 文件——一个方案包
    会带出多个变体（如「朙月拼音」带「简化字 / 慕潮正體 / 語句流」，
    「注音」带「慕潮正體双拼 / 快打模式」）。平铺成 21 个胶囊时，用户
    看到的是一堆碎片而不是「我装了哪些方案」；按包收纳后，包与包的
    归属关系重新可见。

    ``package_schemas``：方案包 key → 该包提供的 schema_id 列表
    （来自 manifest 的 files 记录，见 ``manifest.package_files``）。
    ``package_names``：方案包 key → 显示名（用于组标题）。
    ``group_titles``：无包归属分组的显示名（manual / shared / other），
    由 UI 层传入以便本地化。

    无包归属的方案（手动安装 / 系统共享）各自归入独立分组，不与
    安装器装的方案混在一起——它们的卸载语义不同。
    """
    schemas = list(items or [])
    pkg_schemas = {k: list(v) for k, v in (package_schemas or {}).items()}
    pkg_names = dict(package_names or {})
    titles = dict(group_titles or {})

    # schema_id → 包 key（一个 schema 只属一个包；重复时取先出现的）
    owner: dict[str, str] = {}
    for key, ids in pkg_schemas.items():
        for schema_id in ids:
            owner.setdefault(schema_id, key)

    groups: list[SchemaGroup] = []
    consumed: set[str] = set()
    # 按包分组：组内顺序 = 该包 manifest 记录里的顺序（主方案在前）
    for key, ids in pkg_schemas.items():
        members = [s for s in schemas if s.id in ids]
        if not members:
            continue
        order = {sid: i for i, sid in enumerate(ids)}
        members.sort(key=lambda s: order.get(s.id, 999))
        groups.append(
            SchemaGroup(
                key=key,
                title=pkg_names.get(key, key),
                items=tuple(members),
                source="installer",
            )
        )
        consumed.update(s.id for s in members)

    # 其余按来源分组（手动安装 / 系统共享）
    for source in ("manual", "shared"):
        members = [s for s in schemas if s.id not in consumed and s.source == source]
        if members:
            groups.append(
                SchemaGroup(
                    key=source,
                    title=titles.get(source, source),
                    items=tuple(members),
                    source=source,
                )
            )
            consumed.update(s.id for s in members)

    # 兜底：既没被包收录、来源也不认的（理论上不该有）单独成组
    leftovers = [s for s in schemas if s.id not in consumed]
    if leftovers:
        groups.append(
            SchemaGroup(
                key="other",
                title=titles.get("other", "other"),
                items=tuple(leftovers),
                source="other",
            )
        )
    return groups


# ---- 检测 ----


def detect_active_schema(rime_dir, shared_dir=None) -> str:
    """当前生效方案 id；读不到时返回空串。

    rime 的行为：``previously_selected_schema`` 未设置（或指向已不在列表的
    方案）时取 schema_list 第一项——这里如实复现，UI 显示的「当前方案」
    才和输入法里实际一致。
    """
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return ""
    explicit = _user_yaml_value(directory / "user.yaml", _ACTIVE_KEY)
    effective = effective_schema_ids(directory, shared_dir)
    if explicit and explicit in effective:
        return explicit
    return effective[0] if effective else ""


def effective_schema_ids(rime_dir, shared_dir=None) -> list[str]:
    """生效的 schema_list（custom patch 整体顶替 base；residue 同款语义）。"""
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return []
    shared = Path(shared_dir) if shared_dir else Path(rime_shared_data_dir())
    custom = _list_ids(directory / "default.custom.yaml")
    if custom:
        return custom
    base = directory / "default.yaml"
    if not _is_file(base):
        base = shared / "default.yaml"
    return _list_ids(base)


def list_switchable(rime_dir, shared_dir=None, installer_ids=()) -> list[SwitchableSchema]:
    """扫盘列出全部可切换方案（用户目录 + 共享目录的 *.schema.yaml）。

    ``installer_ids``：manifest 记账的方案 id 集合——用于标注来源
    （本安装器 / 手动安装 / 系统共享），不影响可否切换。
    """
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return []
    shared = Path(shared_dir) if shared_dir else Path(rime_shared_data_dir())
    known = set(installer_ids or ())
    effective = set(effective_schema_ids(directory, shared))
    found: dict[str, SwitchableSchema] = {}
    for base, source in ((directory, "installer"), (shared, "shared")):
        try:
            paths = sorted(base.glob("*.schema.yaml"))
        except OSError:
            continue
        for path in paths:
            schema_id = path.name[: -len(".schema.yaml")]
            if not schema_id or schema_id in found:
                continue
            if source == "installer" and schema_id not in known:
                source_label = "manual"
            else:
                source_label = source
            found[schema_id] = SwitchableSchema(
                id=schema_id,
                name=_schema_name(path) or schema_id,
                source=source_label,
                in_list=schema_id in effective,
            )
    return sorted(found.values(), key=lambda s: (s.source != "installer", s.name))


# ---- 切换步骤 ----


def build_switch_steps(
    schema_id: str,
    rime_dir,
    frontend: str,
    shared_dir=None,
) -> list[Step]:
    """构建「切换到这个方案」的完整步骤链。

    入列步只在目标不在生效列表时才构建（已在列表就不动用户的 yaml）；
    第二步写 user.yaml；第三步复用部署引导（编译 + 重启框架）。
    """
    directory = Path(rime_dir)
    steps: list[Step] = []
    if not _SAFE_ID_RE.fullmatch(schema_id):
        # 极端 id（含引号/空格）无法安全生成脚本——交给调用方拒绝并提示
        raise ValueError(f"方案 id 含不支持的字符: {schema_id!r}")
    effective = effective_schema_ids(directory, shared_dir)
    if schema_id not in effective:
        steps.append(
            Step(
                f"将「{schema_id}」加入可选方案列表",
                ["bash", "-c", _ensure_in_list_script(directory, shared_dir, schema_id)],
                weight=1,
                hint_key="msg.schema_switch_failed",
            )
        )
    steps.append(
        Step(
            f"设置当前方案为「{schema_id}」",
            ["bash", "-c", _set_active_script(directory, schema_id)],
            weight=1,
            hint_key="msg.schema_switch_failed",
        )
    )
    steps.extend(build_redeploy_steps(frontend, directory))
    return steps


# ---- yaml 行级工具 ----


def _ensure_in_list_script(rime_dir: Path, shared_dir, schema_id: str) -> str:
    """生成「把方案加入 schema_list」的 bash 脚本（备份 + heredoc 覆写）。

    三种形态：
    - custom 不存在/为空 → 新建，列表 = base 生效列表 + 目标；
    - custom 有 schema_list → 在最后一个条目后按同缩进追加目标；
    - custom 无 schema_list → 在 patch: 后插入整列表（base + 目标，
      因为 patch 的 list 整体顶替 base，必须把原列表带上）。
    """
    custom = rime_dir / "default.custom.yaml"
    original = _read_text(custom)
    effective = effective_schema_ids(rime_dir, shared_dir)
    if original.strip():
        if _list_ids(custom):
            new_text = _append_entry(original, schema_id)
        else:
            new_text = _insert_list_block(original, [*effective, schema_id])
    else:
        entries = [*effective, schema_id]
        new_text = "patch:\n  schema_list:\n" + "".join(
            f"    - schema: {e}\n" for e in entries
        )
    return _rewrite_script(custom, new_text, f"加入方案列表（{schema_id}）")


def _set_active_script(rime_dir: Path, schema_id: str) -> str:
    """生成「写 user.yaml 当前方案」的 bash 脚本（备份 + heredoc 覆写）。"""
    target = rime_dir / "user.yaml"
    original = _read_text(target)
    new_text = _set_active_text(original, schema_id)
    return _rewrite_script(target, new_text, f"设置当前方案（{schema_id}）")


def _rewrite_script(target: Path, new_text: str, label: str) -> str:
    """备份 + heredoc 覆写的通用脚本（定界符加引号，内容不做变量展开）。"""
    backup = f"{target}{_BAK_SUFFIX}"
    return "\n".join(
        [
            "set -e",
            f"F={shlex.quote(str(target))}",
            f'cp "$F" "$F{_BAK_SUFFIX}" 2>/dev/null || true',
            f"cat > \"$F\" <<'{_DOC_EOF}'",
            new_text if new_text.endswith("\n") else new_text + "\n",
            _DOC_EOF,
            f"echo \"已{label}，备份：$F{_BAK_SUFFIX}\"",
        ]
    )


def _append_entry(text: str, schema_id: str) -> str:
    """在现有 schema_list 的最后一个条目后追加一行（同缩进）。"""
    lines = text.splitlines()
    key_index = -1
    for index, line in enumerate(lines):
        if _LIST_KEY_RE.match(line):
            key_index = index
            break
    if key_index < 0:  # 理论上不会走到（调用方已确认有 list）
        return _insert_list_block(text, [schema_id])
    key_indent = _indent(lines[key_index])
    entry_indent = key_indent + 2
    last_entry = key_index
    for index in range(key_index + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent(line) <= key_indent:
            break
        if _ENTRY_RE.match(line):
            last_entry = index
            entry_indent = _indent(line)
    lines.insert(last_entry + 1, f"{' ' * entry_indent}- schema: {schema_id}")
    return "\n".join(lines) + "\n"


def _insert_list_block(text: str, ids: list[str]) -> str:
    """在没有 schema_list 的文本里插入整列表块。

    有 ``patch:`` 行就插在其后（子级缩进 +2）；没有就整体前置一个 patch 块。
    """
    lines = text.splitlines()
    indent = 2
    insert_at = 0
    for index, line in enumerate(lines):
        if line.strip() == "patch:" or line.strip().startswith("patch:"):
            insert_at = index + 1
            indent = _indent(line) + 2
            break
    block = [f"{' ' * indent}schema_list:"]
    block += [f"{' ' * (indent + 2)}- schema: {i}" for i in ids]
    lines[insert_at:insert_at] = block
    return "\n".join(lines) + "\n"


def _set_active_text(text: str, schema_id: str) -> str:
    """user.yaml 三态：无文件 / 有 var: 无键 / 已有键（替换值）。"""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _ACTIVE_RE.match(line)
        if match:
            lines[index] = f"{match.group(1)}{_ACTIVE_KEY}: {schema_id}"
            return "\n".join(lines) + "\n"
    for index, line in enumerate(lines):
        if line.strip() == "var:":
            indent = _indent(line) + 2
            lines.insert(index + 1, f"{' ' * indent}{_ACTIVE_KEY}: {schema_id}")
            return "\n".join(lines) + "\n"
    if lines and lines[-1].strip():
        lines.append("")
    lines.append("var:")
    lines.append(f"  {_ACTIVE_KEY}: {schema_id}")
    return "\n".join(lines) + "\n"


def _user_yaml_value(path: Path, key: str) -> str:
    """读 user.yaml 里 ``<key>: value`` 的值（顶层或 var: 下，行级解析）。"""
    text = _read_text(path)
    if not text:
        return ""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*[\"']?([^\s\"']+)")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    return ""


def _list_ids(path: Path) -> list[str]:
    """解析 yaml 文本里的 schema_list 条目 id（不引 yaml 依赖，保注释格式）。"""
    text = _read_text(path)
    if not text:
        return []
    ids: list[str] = []
    in_list = False
    key_indent = -1
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _indent(line)
        if not in_list:
            if _LIST_KEY_RE.match(line):
                in_list = True
                key_indent = indent
            continue
        if indent <= key_indent:
            in_list = False
            continue
        match = _ENTRY_RE.match(line)
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def _schema_name(path: Path) -> str:
    """从 ``<id>.schema.yaml`` 读 ``schema:`` 块的 ``name:`` 显示名。"""
    text = _read_text(path)
    if not text:
        return ""
    in_schema = False
    key_indent = -1
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _indent(line)
        if not in_schema:
            if line.strip() == "schema:":
                in_schema = True
                key_indent = indent
            continue
        if indent <= key_indent:
            break
        match = re.match(r"^\s*name\s*:\s*[\"']?(.+?)[\"']?\s*$", line)
        if match:
            return match.group(1).strip()
    return ""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False
