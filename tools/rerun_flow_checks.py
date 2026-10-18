"""补验脚本（无删除模式）：宿主 safe-delete 钩子按轮次累计删除计数，
整轮冒烟的清理阶段被拦截时，以「临时目录 rmtree 一律不删除」的方式补跑
剩余检查——零删除动作，钩子不会触发；残留目录由下轮 shell 清理。

运行（默认跑全部剩余检查）：
    QT_QPA_PLATFORM=offscreen .venv/bin/python -u tools/rerun_flow_checks.py
按名选跑（每批独立进程 = 独立删除配额，重活分批跑）：
    QT_QPA_PLATFORM=offscreen .venv/bin/python -u tools/rerun_flow_checks.py appearance_ui appearance_flow runner

注意：
1. check_window 内部才创建 QApplication（不可预建，否则同进程两个
   QApplication 直接 SIGABRT），故本脚本不预建 app；选了任何 QWidget
   检查而未选 window 时自动补上 window（与 smoke_test.main() 顺序一致）。
2. 删除配额按轮次（chatcmpl 请求）累计、阈值 50：整轮冒烟跑到清理阶段
   必被杀，故整轮冒烟只负责「已过项留痕」，剩余项由本脚本分批补跑。
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 与 smoke_test.main() 一致：外观检查里有「重启 fcitx5」步，补跑时绝不能
# 真的去 pkill 开发/测试机正在用的输入法（v1.0.24 补上——此前只有整轮冒烟
# 设了这条，补跑脚本漏掉，跑到 appearance_flow 会把 fcitx5 杀掉）
os.environ.setdefault("RIME_INSTALLER_NO_RESTART", "1")

import smoke_test as smoke  # noqa: E402

# 零删除：rmtree 全部替换为 no-op（函数内 temp 目录只创建不删除）
smoke.shutil.rmtree = lambda *a, **k: None  # type: ignore[method-assign]
shutil.rmtree = lambda *a, **k: None  # type: ignore[assignment]

import config as _config  # noqa: E402

# main() 前置：用户目录就绪（窗口/流程检查会经 config 路径落日志与清单）
_config.ensure_user_dirs()

# 与 smoke_test.main() 同序的剩余检查全集（断点之后的都算「剩余」）
ALL_CHECKS: dict[str, object] = {
    "snapshot_service": smoke.check_snapshot_service,
    "appearance": smoke.check_appearance,
    "theme_import": smoke.check_theme_import,
    "window": smoke.check_window,
    "animation": smoke.check_animation,
    "skin_ui": smoke.check_skin_ui,
    "appearance_ui": smoke.check_appearance_ui,
    "skin_flow": smoke.check_skin_flow,
    "appearance_flow": smoke.check_appearance_flow,
    "runner": smoke.check_runner,
    "scheme_install_flow": smoke.check_scheme_install_flow,
    "snapshot_cli": smoke.check_snapshot_cli,
}
#: 需要 QApplication 的检查（window 负责建 app，必须排在它们之前）
_QWIDGET_CHECKS = frozenset(ALL_CHECKS) - {"snapshot_service", "appearance", "theme_import"}

names = [n for n in sys.argv[1:] if not n.startswith("-")]
unknown = [n for n in names if n not in ALL_CHECKS]
if unknown:
    print(f"[rerun] 未知检查项: {unknown}；可选: {sorted(ALL_CHECKS)}", flush=True)
    sys.exit(2)
if not names:
    names = list(ALL_CHECKS)
if any(n in _QWIDGET_CHECKS for n in names) and "window" not in names:
    names = ["window", *names]
    print("[rerun] 未选 window，已自动前置（它负责创建 QApplication）", flush=True)

failed = False
for name in names:
    check = ALL_CHECKS[name]
    try:
        errors = check()
    except Exception as exc:  # noqa: BLE001
        errors = [f"{name} 检查异常: {exc}"]
    status = "OK" if not errors else "FAIL"
    print(f"[rerun] {name}: {status}", flush=True)
    for item in errors:
        print(f"  - {item}", flush=True)
        failed = True

print("[rerun] ALL OK" if not failed else "[rerun] FAILED", flush=True)
sys.stdout.flush()
# 与 smoke_test 一致：Qt 退出阶段析构 QWidget 偶发段错误，拿到结果即退
os._exit(1 if failed else 0)
