"""外观配置写入 CLI（v1.0.13：fcitx5 主题切换 + ibus 全局外观项）。

用法::

    python3 appearance_apply.py --fcitx5-conf <path> --themes-dir <path> \
        --backup <path> --theme <key>
    python3 appearance_apply.py --fcitx5-conf <path> --backup <path> --revert-theme
    python3 appearance_apply.py --ibus-conf <rime_user_dir> \
        --ibus-style horizontal=true,inline_preedit=false,color_scheme=ink
    python3 appearance_apply.py --ibus-conf <rime_user_dir> --revert-ibus-style
    python3 appearance_apply.py --themes-dir <path> --delete-theme <key> \
        [--fcitx5-conf <p> --backup <p> --reset-theme-to-default]
    python3 appearance_apply.py --rime-conf <rime_user_dir> --page-size <1..9>
    python3 appearance_apply.py --rime-conf <rime_user_dir> --revert-page-size

- `--theme`：内置主题（rime-installer-*）先写入 themes-dir，再改写
  classicui.conf 的 Theme/DarkTheme（首次改写前备份原件）；
- `--revert-theme`：从备份恢复 classicui.conf；
- `--delete-theme`：删除用户主题目录里的一款主题；带
  `--reset-theme-to-default` 时先把 classicui.conf 切回**本安装器改写前的
  原件主题**（不可用则退回编译内置的 default）再删目录——顺序不可颠倒，
  反过来一旦写配置失败，conf 就指向了已删掉的目录；
- `--ibus-style`：把 style/horizontal、inline_preedit、color_scheme
  写进 <rime_dir>/ibus_rime.custom.yaml 的本安装器标记块；
- `--page-size`（v1.0.41）：把候选词数量（menu/page_size）写进
  <rime_dir>/default.custom.yaml 的标记块，并给「自己在方案文件里写死
  page_size」的方案补写 <id>.custom.yaml；`--revert-page-size` 移除这些块。

退出码：0 成功；2 参数或目录错误；3 未知主题；4 写入失败。
被主窗口的 TaskRunner 以子进程方式调用（stdout 供日志与进度解析）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import appearance, candidate_count  # noqa: E402

_EXIT_OK, _EXIT_ARGS, _EXIT_UNKNOWN, _EXIT_WRITE = 0, 2, 3, 4


def _parse_style_spec(spec: str) -> appearance.IbusStyle:
    """解析 horizontal=...,inline_preedit=...,color_scheme=... 规格串。"""
    fields: dict[str, str] = {}
    for part in spec.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        fields[key.strip()] = value.strip()
    return appearance.IbusStyle(
        horizontal=fields.get("horizontal", "false") == "true",
        inline_preedit=fields.get("inline_preedit", "true") == "true",
        color_scheme=fields.get("color_scheme", ""),
    )


def main(argv: list[str]) -> int:
    opts: dict[str, str] = {}
    flags: set[str] = set()
    index = 0
    while index < len(argv):
        item = argv[index]
        if item.startswith("--"):
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                opts[item[2:]] = argv[index + 1]
                index += 2
            else:
                flags.add(item[2:])
                index += 1
        else:
            print(f"无法识别的参数: {item}")
            return _EXIT_ARGS

    # v1.0.41：候选词数量（menu/page_size）——全局 patch + 方案级补丁
    if "page-size" in opts or "revert-page-size" in flags:
        rime_dir = opts.get("rime-conf")
        if not rime_dir or not Path(rime_dir).is_dir():
            print(f"配置目录不存在: {rime_dir}")
            return _EXIT_ARGS
        try:
            if "page-size" in opts:
                print(
                    candidate_count.write_page_size(
                        Path(rime_dir), opts["page-size"]
                    )
                )
            else:
                print(candidate_count.remove_page_size(Path(rime_dir)))
        except ValueError as exc:
            print(f"参数错误: {exc}")
            return _EXIT_ARGS
        except OSError as exc:
            print(f"写入失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    if "revert-theme" in flags:
        conf = opts.get("fcitx5-conf")
        backup = opts.get("backup")
        if not conf or not backup:
            print("用法: --fcitx5-conf <path> --backup <path> --revert-theme")
            return _EXIT_ARGS
        try:
            print(appearance.revert_theme_setting(Path(conf), Path(backup)))
        except OSError as exc:
            print(f"恢复失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    if "revert-ibus-style" in flags:
        rime_dir = opts.get("ibus-conf")
        if not rime_dir or not Path(rime_dir).is_dir():
            print(f"配置目录不存在: {rime_dir}")
            return _EXIT_ARGS
        try:
            print(appearance.remove_ibus_style(Path(rime_dir)))
        except OSError as exc:
            print(f"移除失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    if "delete-theme" in opts:
        key = opts["delete-theme"]
        themes_dir = opts.get("themes-dir")
        if not themes_dir:
            print("用法: --themes-dir <path> --delete-theme <key> "
                  "[--fcitx5-conf <p> --backup <p> --reset-theme-to-default]")
            return _EXIT_ARGS
        conf = opts.get("fcitx5-conf")
        backup = opts.get("backup")
        try:
            if "reset-theme-to-default" in flags:
                if not conf or not backup:
                    print("--reset-theme-to-default 需要同时给出 --fcitx5-conf 与 --backup")
                    return _EXIT_ARGS
                # 先落配置再删目录：反过来一旦写配置失败，conf 就指向了不存在的主题
                fallback = appearance.delete_fallback_theme(
                    key, Path(conf), Path(backup), Path(themes_dir)
                )
                print(
                    appearance.write_theme_setting(Path(conf), fallback, Path(backup))
                )
            target = appearance.delete_user_theme(key, Path(themes_dir))
            print(f"已删除主题目录: {target}")
        except (ValueError, FileNotFoundError) as exc:
            print(f"无法删除: {exc}")
            return _EXIT_UNKNOWN
        except OSError as exc:
            print(f"删除失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    if "theme" in opts:
        conf = opts.get("fcitx5-conf")
        themes_dir = opts.get("themes-dir")
        backup = opts.get("backup")
        if not conf or not themes_dir or not backup:
            print("用法: --fcitx5-conf <path> --themes-dir <path> --backup <path> --theme <key>")
            return _EXIT_ARGS
        theme = appearance.bundled_theme(opts["theme"])
        try:
            if theme is not None:
                target = appearance.install_bundled_theme(theme, Path(themes_dir))
                print(f"内置主题已安装: {target}")
            elif opts["theme"] in appearance.bundled_full_theme_keys():
                target = appearance.install_bundled_full_theme(opts["theme"], Path(themes_dir))
                print(f"内置主题已安装: {target}")
            elif not appearance.theme_exists(opts["theme"], Path(themes_dir)):
                print(f"未知主题: {opts['theme']}（既非内置主题，主题目录也不存在）")
                return _EXIT_UNKNOWN
            print(appearance.write_theme_setting(Path(conf), opts["theme"], Path(backup)))
        except OSError as exc:
            print(f"写入失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    if "ibus-style" in opts:
        rime_dir = opts.get("ibus-conf")
        if not rime_dir or not Path(rime_dir).is_dir():
            print(f"配置目录不存在: {rime_dir}")
            return _EXIT_ARGS
        try:
            print(
                appearance.write_ibus_style(
                    Path(rime_dir), _parse_style_spec(opts["ibus-style"])
                )
            )
        except OSError as exc:
            print(f"写入失败: {exc}")
            return _EXIT_WRITE
        return _EXIT_OK

    print(
        "用法: appearance_apply.py --fcitx5-conf <p> --themes-dir <p> --backup <p> "
        "--theme <key> | --revert-theme | --themes-dir <p> --delete-theme <key> "
        "[--reset-theme-to-default] | --ibus-conf <dir> "
        "--ibus-style <spec> | --revert-ibus-style | --rime-conf <dir> "
        "--page-size <1..9> | --revert-page-size"
    )
    return _EXIT_ARGS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
