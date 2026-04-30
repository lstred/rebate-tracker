"""
main.py
--------
Application entry point for Rebate Tracker.

Startup sequence
----------------
1. Set matplotlib backend BEFORE any matplotlib import.
2. Create QApplication and apply the dark stylesheet.
3. Initialise the local SQLite database (creates tables + seeds defaults).
4. Show the main window.
5. Enter the Qt event loop.
"""

import sys
import traceback

# Must happen before any other matplotlib import
import matplotlib
matplotlib.use("QtAgg")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from db.local_db import init_db
from ui.main_window import MainWindow
from ui.theme import STYLESHEET, apply_mpl_style


def _install_exception_hook(app: QApplication) -> None:
    """Show a dialog instead of silently crashing on unhandled exceptions."""

    def handler(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        dlg = QMessageBox()
        dlg.setWindowTitle("Unexpected Error")
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.setText(
            "An unexpected error occurred. Please report this to the administrator."
        )
        dlg.setDetailedText(msg)
        dlg.exec()

    sys.excepthook = handler


def main() -> None:
    # High-DPI scaling (Qt6 handles this automatically, but keep attribute for clarity)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Rebate Tracker")
    app.setOrganizationName("NRF")
    app.setStyle("Fusion")  # Base style before custom QSS

    # Dark stylesheet
    app.setStyleSheet(STYLESHEET)

    # Matplotlib dark theme
    apply_mpl_style()

    # Global exception dialog
    _install_exception_hook(app)

    # Init local SQLite DB (idempotent)
    init_db()

    # Launch main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
