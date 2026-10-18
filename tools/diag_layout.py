"""临时诊断：验证方案市场滚动区高度占比 + 导航 routeKey 碰撞。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

import config
from core.detector import EnvReport
from core.manifest import Manifest
from ui.main_window import MainWindow, _INSTALL_PAGES


def fake_report() -> EnvReport:
    r = EnvReport()
    r.os_name, r.os_version = "Ubuntu", "26.04"
    r.session_type, r.desktop = "wayland", "ubuntu:GNOME"
    r.active_frontend = "ibus-rime"
    r.running_frontends = ["ibus-rime"]
    r.recommended_frontend = "ibus-rime"
    r.engine_packages = {"ibus-rime": True, "fcitx5-rime": False, "fcitx-rime": False}
    r.rime_user_dir = Path.home() / ".config/ibus/rime"
    r.im_env_vars = {"GTK_IM_MODULE": "", "QT_IM_MODULE": "ibus", "XMODIFIERS": "@im=ibus", "SDL_IM_MODULE": ""}
    return r


def main() -> None:
    app = QApplication(sys.argv[:1])
    manifest = Manifest()
    window = MainWindow(fake_report(), manifest)
    window.resize(1160, 800)
    window.show()
    app.processEvents()

    nav = window.navigationInterface
    print(f"nav items = {len(nav.items)} (expected {len(_INSTALL_PAGES)})")
    print(f"nav routeKeys = {sorted(nav.items.keys())}")
    print(f"stack pages = {window.stackedWidget.count()}")
    for i in range(window.stackedWidget.count()):
        w = window.stackedWidget.widget(i)
        print(f"  stack[{i}] objectName={w.objectName()!r}")

    # 切到方案市场，测量滚动区占比
    window.switchTo(window.scheme_page)
    app.processEvents()
    page = window.scheme_page
    outer = page.layout()
    scroll = page.scroll_area
    win_h = window.height()
    print(f"\nwindow height = {win_h}")
    print(f"scroll area height = {scroll.height()} ({scroll.height() / win_h:.0%} of window)")
    print(f"scroll area sizeHint height = {scroll.sizeHint().height()}")
    print(f"cards_host height = {page.cards_host.height()}, min = {page.cards_host.minimumHeight()}")
    print(f"cards count = {len(page._cards)}")
    # 视图内可见的卡片数（几何相交）
    viewport = scroll.viewport()
    visible = [c for c in page._cards if c.isVisible() and viewport.geometry().intersects(c.geometry())]
    print(f"cards intersecting viewport = {len(visible)}")
    # 末尾 stretch 项
    print(f"outer layout items: {[type(outer.itemAt(i).widget()).__name__ if outer.itemAt(i).widget() else 'stretch' for i in range(outer.count())]}")


if __name__ == "__main__":
    main()
