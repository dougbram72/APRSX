"""aprsx-head entry point: the touch-screen radio head.

python -m aprsx.head [--url http://127.0.0.1:8080] [--fullscreen] [--size 800x480]
                     [--no-keyboard] [Qt options, e.g. -platform eglfs]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

QML_DIR = Path(__file__).parent / "qml"


def load(url: str, keyboard: bool = True, warnings: list[str] | None = None):
    """Create the core client and the QML engine. Needs a QGuiApplication.

    QML warnings are appended to ``warnings`` when given (tests).
    """
    from PySide6.QtQml import QQmlApplicationEngine

    from .client import CoreClient

    client = CoreClient(url)
    engine = QQmlApplicationEngine()
    if warnings is not None:
        engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    engine.rootContext().setContextProperty("core", client)
    engine.rootContext().setContextProperty("vkbEnabled", keyboard)
    engine.load(QML_DIR / "Main.qml")
    if not engine.rootObjects():
        raise RuntimeError("failed to load Main.qml")
    return engine, client


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="APRS-X touch-screen head unit")
    ap.add_argument("--url", default="http://127.0.0.1:8080", help="aprsx-core base URL")
    ap.add_argument("--fullscreen", action="store_true", help="full screen, no cursor")
    ap.add_argument("--size", default="800x480", help="window size when not full screen")
    ap.add_argument("--no-keyboard", action="store_true",
                    help="use a physical keyboard instead of the on-screen one")
    ap.add_argument("-v", "--verbose", action="store_true")
    args, qt_args = ap.parse_known_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Must be set before the QGuiApplication exists.
    if not args.no_keyboard:
        # Qt 6.8's Wayland plugin only honours the plural form.
        os.environ.setdefault("QT_IM_MODULE", "qtvirtualkeyboard")
        os.environ.setdefault("QT_IM_MODULES", "qtvirtualkeyboard")
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication([sys.argv[0], *qt_args])
    app.setApplicationName("APRS-X")
    engine, client = load(args.url, keyboard=not args.no_keyboard)
    window = engine.rootObjects()[0]
    if args.fullscreen:
        QGuiApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
        window.showFullScreen()
    else:
        w, h = (int(v) for v in args.size.lower().split("x"))
        window.resize(w, h)
        window.show()
    client.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
