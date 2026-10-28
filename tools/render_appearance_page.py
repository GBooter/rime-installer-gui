"""离屏渲染「外观」页（中/英），用于核对候选数分区与长文案布局。

用法::

    QT_QPA_PLATFORM=offscreen /usr/bin/python3 tools/render_appearance_page.py

输出到 ``design/mockups/appearance-page-size-{zh,en}.png``（5 档）与
``appearance-page-size-zh-9.png``（9 档）。

为什么需要它：「每页候选词数量」是整页唯一「控件 + 多行说明」同排的卡片，
英文文案比中文长得多，容易挤掉控件或撑出横向滚动条。改文案或改布局后跑
一次，比在真机上切语言更快发现裁切问题。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "resources" / "vendor"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import appearance, i18n  # noqa: E402
from core.detector import EnvReport  # noqa: E402
from core.state_machine import StateMachine  # noqa: E402
from ui.pages.appearance_page import AppearancePage  # noqa: E402

OUT_DIR = ROOT / "design" / "mockups"


def sample_rime_dir(base: Path) -> Path:
    """造一个「装了雾凇拼音、用户已有自定义配置」的 rime 用户目录。"""
    rime = base / "rime"
    rime.mkdir()
    (rime / "default.yaml").write_text(
        "schema_list:\n  - schema: rime_ice\n\nmenu:\n  page_size: 5\n",
        encoding="utf-8",
    )
    (rime / "default.custom.yaml").write_text(
        "patch:\n  schema_list:\n    - schema: rime_ice\n  switcher/hotkeys:\n    - F4\n",
        encoding="utf-8",
    )
    (rime / "rime_ice.schema.yaml").write_text(
        "schema:\n  schema_id: rime_ice\n  name: 雾凇拼音\n", encoding="utf-8"
    )
    return rime


def main() -> int:
    app = QApplication([])
    base = Path(tempfile.mkdtemp(prefix="appearance-render-"))
    rime = sample_rime_dir(base)

    # 主题路径全部重定向到临时目录：渲染不该读写真机上正在用的配置
    themes = base / "themes"
    system = base / "sys-themes"
    themes.mkdir()
    system.mkdir()
    appearance.install_bundled_theme(
        appearance.bundled_theme("rime-installer-ink"), themes
    )
    conf = base / "classicui.conf"
    conf.write_text(
        "Theme=rime-installer-ink\nDarkTheme=rime-installer-ink\n", encoding="utf-8"
    )
    appearance.fcitx5_conf_path = lambda: conf
    appearance.user_themes_dir = lambda: themes
    appearance.system_themes_dir = lambda: system
    appearance.theme_backup_path = lambda: base / "classicui.conf.orig"

    report = EnvReport(recommended_frontend="fcitx5-rime", rime_user_dir=rime)
    page = AppearancePage(report, StateMachine(report))
    page.bind(report, StateMachine(report))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def show_state(value: int) -> None:
        """把刻度与预览摆到某一档（不写配置、不发意图），用于出图核对。"""
        page._page_size = value
        for item, button in page.page_size_buttons.items():
            button.setChecked(item == value)
        page.page_size_hint.setText(page._page_size_level_text(value))


    for lang, tag in ((i18n.ZH, "zh"), (i18n.EN, "en")):
        i18n.set_language(lang)
        page.retranslate()
        page.resize(1120, 780)
        page.show()
        app.processEvents()
        # 默认档（5）：最松弛的一屏，用来判断卡片是否有空洞
        show_state(5)
        app.processEvents()
        out = OUT_DIR / f"appearance-page-size-{tag}.png"
        page.grab().save(str(out))
        print(f"已渲染：{out}")
    # 上限档（9）：预览最高、文字列最容易被挤压
    i18n.set_language(i18n.ZH)
    page.retranslate()
    show_state(9)
    app.processEvents()
    out = OUT_DIR / "appearance-page-size-zh-9.png"
    page.grab().save(str(out))
    print(f"已渲染：{out}")
    i18n.set_language(i18n.ZH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
