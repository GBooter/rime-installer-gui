"""离屏渲染「环境修复」页（中/英），用于核对长文案与多语言布局。

用法::

    QT_QPA_PLATFORM=offscreen /usr/bin/python3 tools/render_doctor_page.py

输出到 ``design/mockups/doctor-{zh,en}.png``（2x，便于像素级核对）。

为什么需要它：体检结果是**动态文案**（路径、数量、冲突键都进句子），
英文又比中文长得多。改文案或改 i18n 后跑一次，比在真机上切语言更快发现
标题被裁、advice 挤压、换行难看之类的问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "resources" / "vendor"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import i18n  # noqa: E402
from core.detector import EnvReport  # noqa: E402
from core.state_machine import StateMachine  # noqa: E402
from ui.pages.doctor_page import DoctorPage  # noqa: E402

OUT_DIR = ROOT / "design" / "mockups"


def sample_report() -> EnvReport:
    """一台「Deepin 25 + fcitx5-rime 基本正常、但还没部署」的机器。"""
    report = EnvReport()
    report.os_name = "Deepin"
    report.os_version = "25.2"
    report.session_type = "x11"
    report.desktop = "DDE"
    report.engine_packages = {"ibus-rime": False, "fcitx5-rime": True, "fcitx-rime": False}
    report.recommended_frontend = "fcitx5-rime"
    report.active_frontend = "fcitx5-rime"
    report.running_frontends = ["fcitx5-rime"]
    report.rime_user_dir = Path("/home/u/.local/share/fcitx5/rime")
    report.im_env_vars = {
        "GTK_IM_MODULE": "fcitx",
        "QT_IM_MODULE": "fcitx",
        "XMODIFIERS": "@im=fcitx",
        "SDL_IM_MODULE": "fcitx",
    }
    return report


def grab(widget, path: Path) -> None:
    """2x 渲染：只设 devicePixelRatio，不要再手动 painter.scale（会放大两遍）。"""
    widget.resize(1120, 720)
    widget.show()
    QApplication.processEvents()
    pixmap = QPixmap(widget.width() * 2, widget.height() * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(QColor("#F5F5F5"))
    painter = QPainter(pixmap)
    widget.render(painter)
    painter.end()
    pixmap.save(str(path))


def main() -> int:
    app = QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = sample_report()
    for lang, tag in ((i18n.ZH, "zh"), (i18n.EN, "en")):
        i18n.set_language(lang)
        page = DoctorPage(report, StateMachine(report), {"preset", "rime-ice", "double-pinyin"})
        path = OUT_DIR / f"doctor-{tag}.png"
        grab(page, path)
        print("saved", path.relative_to(ROOT))
        page.deleteLater()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
