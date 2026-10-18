"""皮肤配置写入 CLI（F15 皮肤部分）。

用法::

    python3 skin_apply.py <rime_user_dir> <skin_key>
    python3 skin_apply.py <rime_user_dir> --revert

只操作 `<rime_user_dir>/default.custom.yaml` 中本安装器的标记块
（`# >>> rime-installer: skin >>>` … `# <<< …`），其余内容原样保留。
不解析 YAML，避免引入 PyYAML 依赖，也避免重写用户的既有配置。

退出码：0 成功；2 参数或目录错误；3 未知皮肤；4 写入失败。
被主窗口的 TaskRunner 以子进程方式调用（stdout 供日志与进度解析）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skin import MARK_BEGIN, MARK_END, SKIN_MAP, SKIN_FILE, render_block  # noqa: E402

_EXIT_OK, _EXIT_ARGS, _EXIT_UNKNOWN, _EXIT_WRITE = 0, 2, 3, 4


def _apply(path: Path, block: str) -> None:
    """把标记块放进文件：已有则替换，无则追加（必要时新建 patch: 段）。"""
    original = path.read_text(encoding="utf-8") if path.is_file() else ""

    if MARK_BEGIN in original and MARK_END in original:
        head, rest = original.split(MARK_BEGIN, 1)
        _, tail = rest.split(MARK_END, 1)
        # 标记块自身独占行，去掉上一标记块的残留空行风格差异
        merged = head.rstrip("\n")
        merged += "\n" + block + "\n"
        merged += tail.lstrip("\n") if tail.strip() else "\n"
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
    print(f"已写入皮肤配置: {path}")


def _revert(path: Path) -> None:
    if not path.is_file():
        print("配置文件不存在，无需移除")
        return
    text = path.read_text(encoding="utf-8")
    if MARK_BEGIN not in text or MARK_END not in text:
        print("未发现本安装器写入的皮肤配置，无需移除")
        return
    head, rest = text.split(MARK_BEGIN, 1)
    _, tail = rest.split(MARK_END, 1)
    merged = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).rstrip("\n")
    if merged.strip():
        merged += "\n"
    else:
        merged = ""  # 文件只剩我们的配置块则清空，不留空 patch 段
    path.write_text(merged, encoding="utf-8")
    print(f"已移除皮肤配置: {path}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("用法: skin_apply.py <rime_user_dir> <skin_key> | --revert")
        return _EXIT_ARGS

    rime_dir = Path(argv[0]).expanduser()
    if not rime_dir.is_dir():
        print(f"配置目录不存在: {rime_dir}")
        return _EXIT_ARGS
    target = rime_dir / SKIN_FILE

    if argv[1] == "--revert":
        try:
            _revert(target)
        except OSError as exc:
            print(f"移除失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    skin = SKIN_MAP.get(argv[1])
    if skin is None:
        print(f"未知皮肤: {argv[1]}（可选: {', '.join(SKIN_MAP)}）")
        return _EXIT_UNKNOWN
    try:
        _apply(target, render_block(skin))
    except OSError as exc:
        print(f"写入失败: {exc}")
        return _EXIT_WRITE
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
