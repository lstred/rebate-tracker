"""
ui/admin_state.py
-----------------
Session-scoped admin mode state for Rebate Tracker.

Admin mode is active until the user explicitly logs out or closes the app.
The password is stored in app_settings under 'admin_password'.
Default on a fresh install: 123nrf
"""

from __future__ import annotations

from db.local_db import get_setting

DEFAULT_PASSWORD = "123nrf"

_admin_active: bool = False


def is_admin() -> bool:
    """Return True if admin mode is currently active."""
    return _admin_active


def set_admin(value: bool) -> None:
    """Activate or deactivate admin mode."""
    global _admin_active
    _admin_active = value


def get_admin_password() -> str:
    """Return the current admin password (from settings, or the default)."""
    return get_setting("admin_password", DEFAULT_PASSWORD) or DEFAULT_PASSWORD


def require_admin(parent=None) -> bool:
    """
    If admin mode is active, return True immediately.
    Otherwise show a polished info dialog explaining what is needed and return False.
    """
    if _admin_active:
        return True
    from PyQt6.QtWidgets import QMessageBox
    dlg = QMessageBox(parent)
    dlg.setWindowTitle("Admin Access Required")
    dlg.setIcon(QMessageBox.Icon.Information)
    dlg.setText("This action requires admin access.")
    dlg.setInformativeText(
        "Click the \U0001f512 Inquiry Mode button in the sidebar to log in as admin."
    )
    dlg.exec()
    return False
