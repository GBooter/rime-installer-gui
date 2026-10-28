"""候选词数量（``menu/page_size``）——v1.0.41。

**用户诉求**：装完方案、部署完成后，候选窗每页只有 5 个候选，希望能自己设。

**这是 rime 引擎的配置，不是前端能力**（与「候选窗主题」正相反）：
- fcitx5-rime / ibus-rime 都直接从 librime 读 ``menu/page_size``，改一处两边都生效；
- 有效值 = 部署时合入 ``build/`` 的配置，所以改完**必须重新部署**
  （与换皮肤同一套机制，复用 ``deploy_guide.build_redeploy_steps``）。

**优先级**（决定这个功能不能只写一个地方就收工）::

    方案自身 .schema.yaml 的 menu/page_size   ← 最高（oh-my-rime 这类方案把 6 写死）
      ↑ 被它盖住的
    用户 default.custom.yaml 的 patch
      ↑ 盖住
    方案包自带的 default.yaml（雾凇/白霜：menu.page_size = 5）
      ↑ 盖住
    引擎内置默认 5

所以「写全局 patch」只对**没在方案文件里写 page_size** 的方案生效。为了不出现
「设成 9 了但某个方案还是 5 个」的假成功，``write_page_size()`` 会顺带扫描生效
``schema_list``，给那些自带 page_size 的方案补写 ``<id>.custom.yaml`` 的标记块。

**值域**：1–9。rime 官方定制指南的口径是 1–9——第 10 个候选要靠 ``0`` 键选字，
而选字键由各方案的 ``alternative_select_keys`` 决定，前端行为不一致，因此不开放 10。

**写法**：一律用「带引号的扁平键」``"menu/page_size": 9``。嵌套写法
``menu: {page_size: 9}`` 会**整体顶替**用户的整个 ``menu`` 段，把
``alternative_select_labels`` / ``alternative_select_keys`` 一起冲掉（Rime wiki
明确警告，rime-ice 的 default.yaml 里这两个键就在 menu 下）。

所有文件 IO 包 ``try/except OSError``：本模块跑在任务构建与页面回调里，
未捕获的 PermissionError 会让 PyQt6 直接 abort（v1.0.26 教训）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import config
from core.deploy_guide import build_redeploy_steps, rime_shared_data_dir
from core.runner import Step

#: 值域与默认值（UI、CLI、写入三处共用同一事实源）
PAGE_SIZE_MIN = 1
PAGE_SIZE_MAX = 9
PAGE_SIZE_DEFAULT = 5

#: 配置落点：全局 patch 写在用户目录的 default.custom.yaml
CONFIG_FILE = "default.custom.yaml"
#: 标记块（只替换自己管理的区块，不解析/重写用户其余 YAML）
MARK_BEGIN = "# >>> rime-installer: page-size >>>"
MARK_END = "# <<< rime-installer: page-size <<<"
#: 改前备份后缀（非 .yaml，rime 不解析）
BAK_SUFFIX = ".rime-installer-bak"

#: 扁平键形态（引号可选）：patch 里合法且不碰 menu 段
_FLAT_RE = re.compile(r'^\s*"?menu/page_size"?\s*:\s*(\d+)')
#: 嵌套形态的父键
_MENU_KEY_RE = re.compile(r"^(\s*)menu\s*:\s*(?:#.*)?$")
_SUB_KEY_RE = re.compile(r"^\s*page_size\s*:\s*(\d+)")


def clamp(value) -> int:
    """把任意输入夹到合法值域（用户手改 YAML 写成 20 也不会把界面带崩）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return PAGE_SIZE_DEFAULT
    return max(PAGE_SIZE_MIN, min(PAGE_SIZE_MAX, number))


# ---- 读写文本 ----

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def render_block(size: int) -> str:
    """渲染写进 patch 的标记块（缩进 2 空格，挂在 ``patch:`` 之下）。"""
    return "\n".join(
        [
            MARK_BEGIN,
            f'  "menu/page_size": {clamp(size)}',
            MARK_END,
        ]
    )


def _scan_patch_value(text: str) -> int | None:
    """从 patch 文件里取 page_size（扁平键，任意缩进；后出现的优先）。

    只认扁平键：嵌套 ``menu:`` 在 custom 文件里同样合法，但那属于用户自己
    的写法，能被引擎读到——反读时也认，交给 ``_scan_nested``。
    """
    found: int | None = None
    for line in text.splitlines():
        match = _FLAT_RE.match(line)
        if match:
            found = int(match.group(1))
    return found


def _scan_nested(text: str, root_only: bool = False) -> int | None:
    """取 ``menu:`` 下 ``page_size`` 的值。

    ``root_only=True``：只认顶层 ``menu:``（用于 ``*.schema.yaml`` /
    ``default.yaml`` 这类普通配置文件）；``False``：任意缩进（用于 patch 文件）。
    """
    found: int | None = None
    menu_indent: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _MENU_KEY_RE.match(line)
        if match:
            indent = len(match.group(1))
            if root_only and indent != 0:
                menu_indent = None
                continue
            menu_indent = indent
            continue
        if menu_indent is None:
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= menu_indent:
            menu_indent = None
            continue
        sub = _SUB_KEY_RE.match(line)
        if sub:
            found = int(sub.group(1))
    return found


def read_configured(rime_dir) -> int | None:
    """反读「用户配置里写着的」page_size；没有则 None。

    顺序 = 本安装器的标记块 → 用户自己写的扁平键 → 嵌套 menu 段。
    标记块优先，因为那是用户在本程序里最后确认过的选择。
    """
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return None
    text = _read_text(directory / CONFIG_FILE)
    if not text:
        return None
    if MARK_BEGIN in text and MARK_END in text:
        block = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0]
        value = _scan_patch_value(block)
        if value is not None:
            return clamp(value)
    value = _scan_patch_value(text)
    if value is not None:
        return clamp(value)
    value = _scan_nested(text)
    return clamp(value) if value is not None else None


def has_own_block(rime_dir) -> bool:
    """本安装器是否写过候选数配置（决定「恢复默认」按钮是否可用）。"""
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return False
    text = _read_text(directory / CONFIG_FILE)
    return MARK_BEGIN in text and MARK_END in text


def schema_page_size(path: Path) -> int | None:
    """读一个 ``.schema.yaml`` 自己写的 ``menu/page_size``（顶层 menu 段）。"""
    if not path.is_file():
        return None
    return _scan_nested(_read_text(path), root_only=True)


def detect(rime_dir, schema_id: str = "", shared_dir=None) -> int:
    """反读「当前真实生效」的候选数。

    顺序与 rime 的生效顺序一致（见模块 docstring 的优先级表）：
    用户 patch → 当前方案自身 → 方案包 default.yaml → 5。

    已知 ``schema_id`` 时**只看它自己**：顺着 schema_list 往下找会把「列表里
    另一个方案写死 6」误当成当前方案的值（离屏复现过）。
    这样界面上显示的数字就是用户输入时看到的那一页，而不是本程序的记账。
    """
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return PAGE_SIZE_DEFAULT
    configured = read_configured(directory)
    if configured is not None:
        return configured
    shared = Path(shared_dir) if shared_dir else Path(rime_shared_data_dir())
    from core.schema_switch import effective_schema_ids

    # 已知当前方案：只认它自己写的值——**不能顺着 schema_list 往下找**，
    # 否则「当前方案没写、列表里另一个方案写了」时会显示成另一个方案的数字。
    ids: list[str] = [schema_id] if schema_id else []
    if not ids:
        # 不知道当前方案（没装方案/无 user.yaml）：退回列表顺序，第一个写了的算数
        ids = effective_schema_ids(directory, shared)
    for sid in ids:
        for base in (directory, shared):
            value = schema_page_size(base / f"{sid}.schema.yaml")
            if value is not None:
                return clamp(value)
    # 方案没写 = 继承方案包的 default.yaml（雾凇/白霜把 5 写在这里）
    for base in (directory, shared):
        value = _scan_nested(_read_text(base / "default.yaml"), root_only=True)
        if value is not None:
            return clamp(value)
    return PAGE_SIZE_DEFAULT


def schemas_with_own_page_size(rime_dir, shared_dir=None) -> list[str]:
    """生效 schema_list 里「自己在方案文件里写死 page_size」的方案 id。

    这些方案不受全局 patch 影响（方案级配置优先），必须补写
    ``<id>.custom.yaml`` 才能真正改到它们的候选数。
    """
    directory = Path(rime_dir) if rime_dir else None
    if directory is None:
        return []
    shared = Path(shared_dir) if shared_dir else Path(rime_shared_data_dir())
    from core.schema_switch import effective_schema_ids

    found: list[str] = []
    for sid in effective_schema_ids(directory, shared):
        for base in (directory, shared):
            if schema_page_size(base / f"{sid}.schema.yaml") is not None:
                found.append(sid)
                break
    return found


# ---- 写配置 ----

def _merge_block(path: Path, block: str) -> None:
    """把标记块放进文件：已有则替换，无则追加（必要时新建 ``patch:`` 段）。

    与 ``core/appearance._merge_block`` 同构：块内容自带 2 空格缩进，挂在
    文件既有的顶层 ``patch:`` 键下；不解析 YAML，只替换自己管理的区块。
    """
    original = _read_text(path)
    if MARK_BEGIN in original and MARK_END in original:
        head, rest = original.split(MARK_BEGIN, 1)
        _, tail = rest.split(MARK_END, 1)
        merged = head.rstrip("\n") + "\n" + block + "\n" + tail.lstrip("\n")
    elif original.strip():
        lines = original.rstrip("\n").split("\n")
        if any(line.rstrip().endswith("patch:") for line in lines):
            merged = "\n".join(lines) + "\n" + block + "\n"
        else:
            merged = "\n".join(lines) + "\npatch:\n" + block + "\n"
    else:
        merged = "patch:\n" + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8")


def _backup(path: Path) -> None:
    """改前留一份 ``.rime-installer-bak``（失败不阻塞写入，与皮肤模块一致）。"""
    try:
        if path.is_file():
            (path.parent / f"{path.name}{BAK_SUFFIX}").write_text(
                path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
            )
    except OSError:
        pass


def _is_effectively_empty(text: str) -> bool:
    """只剩空行/注释/一个孤零零的 ``patch:`` → 视作空文件。

    ``_merge_block`` 在我们自己新建的文件里会补一行 ``patch:``；移除标记块后
    那里会剩一个空的 ``patch:``——不认这一步就会留下一个「看起来像配置」的
    空壳文件（离屏复现：ohmy.custom.yaml 剩 ``patch:\\n``）。
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "patch:":
            continue
        return False
    return True


def _remove_block(path: Path) -> bool:
    """删掉文件里的标记块；没有块返回 False。"""
    if not path.is_file():
        return False
    text = _read_text(path)
    if MARK_BEGIN not in text or MARK_END not in text:
        return False
    head, rest = text.split(MARK_BEGIN, 1)
    _, tail = rest.split(MARK_END, 1)
    merged = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).rstrip("\n")
    _backup(path)
    if _is_effectively_empty(merged):
        # 去掉标记块后只剩空壳：整个文件就是我们建的，删掉才算恢复出厂。
        # 删除被拒（只读挂载 / 宿主 safe-delete 钩子）时退回写空文件——
        # rime 对空的 .custom.yaml 同样容忍，不会因此部署失败。
        try:
            path.unlink()
            return True
        except OSError:
            pass
        path.write_text("", encoding="utf-8")
        return True
    path.write_text(merged + "\n", encoding="utf-8")
    return True


def write_page_size(rime_dir, size, extra_ids=None) -> str:
    """写入候选数：全局 patch + （必要时）方案级 custom 补丁。

    ``extra_ids`` 不传时自动扫描（``schemas_with_own_page_size``）。返回给
    CLI 打印的说明串——日志里能看出「写了哪几个文件」，排障时不用猜。
    """
    directory = Path(rime_dir)
    value = clamp(size)
    target = directory / CONFIG_FILE
    _backup(target)
    _merge_block(target, render_block(value))
    written = [CONFIG_FILE]

    ids = (
        list(extra_ids)
        if extra_ids is not None
        else schemas_with_own_page_size(directory)
    )
    for sid in ids:
        # 方案 id 只做文件名，必须拒绝路径分隔符/上跳（防御式，正常 id 不含这些）
        if not sid or "/" in sid or "\\" in sid or sid in (".", ".."):
            continue
        scoped = directory / f"{sid}.custom.yaml"
        _backup(scoped)
        _merge_block(scoped, render_block(value))
        written.append(scoped.name)

    suffix = (
        f"（含方案级补丁 {len(written) - 1} 个）" if len(written) > 1 else ""
    )
    return (
        f"候选词数量已设为 {value} 个{suffix}，写入：{'、'.join(written)}；"
        "重新部署后生效"
    )


def remove_page_size(rime_dir) -> str:
    """移除本安装器写入的候选数配置（全局 + 所有方案级补丁）。"""
    directory = Path(rime_dir)
    removed: list[str] = []
    try:
        candidates = sorted(directory.glob("*.custom.yaml"))
    except OSError:
        candidates = []
    target = directory / CONFIG_FILE
    if target not in candidates:
        candidates.insert(0, target)
    for path in candidates:
        try:
            if _remove_block(path):
                removed.append(path.name)
        except OSError as exc:
            raise OSError(f"移除 {path.name} 失败: {exc}") from exc
    if not removed:
        return "未发现本安装器写入的候选数配置，无需恢复"
    return f"已移除候选数配置：{'、'.join(removed)}；重新部署后回到方案默认值"


# ---- 任务步骤（复用外观 CLI，不新增进程入口） ----

def _cli_argv(*args: str) -> list[str]:
    return [sys.executable or "python3", str(config.APPEARANCE_CLI), *args]


def build_apply_steps(size, rime_dir, frontend: str) -> list[Step]:
    """设置候选数：写配置 → 重新部署（编译 + 重启框架）。"""
    value = clamp(size)
    return [
        Step(
            title=f"写入候选词数量：{value} 个",
            argv=_cli_argv("--rime-conf", str(rime_dir), "--page-size", str(value)),
            weight=1,
            hint_key="msg.page_size_failed",
        ),
        *build_redeploy_steps(frontend, rime_dir),
    ]


def build_revert_steps(rime_dir, frontend: str) -> list[Step]:
    """恢复默认：移除标记块 → 重新部署。"""
    return [
        Step(
            title="移除候选词数量配置",
            argv=_cli_argv("--rime-conf", str(rime_dir), "--revert-page-size"),
            weight=1,
            hint_key="msg.page_size_failed",
        ),
        *build_redeploy_steps(frontend, rime_dir),
    ]
