"""Application entry point (Qt)."""
from __future__ import annotations

import logging
import sys

from .. import config


def main(argv=None) -> int:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(config.LOG_FILE, encoding="utf-8")],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("paramiko").setLevel(logging.WARNING)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    # Qt 6 scales per monitor automatically; pass fractional factors (125 %, 150 %, 175 %) through unrounded.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if sys.platform == "win32":
        try:  # own taskbar entry and icon instead of python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SubPull.SubPull")
        except Exception:
            pass

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationDisplayName(config.APP_NAME)
    app.setOrganizationName(config.APP_NAME)

    from . import icons
    from .controller import Controller
    from .main_window import MainWindow
    from .models import LogModel
    from .theme import apply_theme
    from .workers import LogBridge, QtLogHandler

    apply_theme(app)
    app.setWindowIcon(icons.logo_icon())
    log_model = LogModel()
    bridge = LogBridge()
    bridge.record.connect(log_model.append)
    logging.getLogger().addHandler(QtLogHandler(bridge))

    ctl = Controller()
    win = MainWindow(ctl, log_model)
    ctl.reload_sites()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
