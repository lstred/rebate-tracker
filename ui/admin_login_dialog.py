"""
ui/admin_login_dialog.py
------------------------
Admin login dialog for Rebate Tracker.

Features
--------
• Password input (masked) with Enter-key support
• Inline error label on bad password
• Forgot Password button — prompts for requester's email then sends the
  current admin password to lukas_stred@nrfdist.com via the configured SMTP.
"""

from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ui.admin_state import get_admin_password
from ui.theme import C

# Recipient for password-recovery emails — never user-configurable
_RECOVERY_RECIPIENT = "lukas_stred@nrfdist.com"


# ---------------------------------------------------------------------------
# Background worker — sends the recovery email off the UI thread
# ---------------------------------------------------------------------------

class _ForgotPasswordWorker(QThread):
    """Sends the password-recovery email without blocking the UI."""

    finished = pyqtSignal(bool, str)   # (success, human-readable message)

    def __init__(self, requester_email: str, password: str, parent=None):
        super().__init__(parent)
        self._requester = requester_email
        self._password = password

    def run(self) -> None:
        from services.email_sender import smtp_configured, get_smtp_settings

        if not smtp_configured():
            self.finished.emit(
                False,
                "SMTP is not configured. Please enter email credentials in "
                "Settings \u2192 Email and try again.",
            )
            return

        cfg = get_smtp_settings()
        from_display = f"{cfg.get('from_name') or cfg['user']} <{cfg['user']}>"

        body_text = (
            f"A password recovery request was submitted.\n\n"
            f"Requester email: {self._requester}\n"
            f"Current admin password: {self._password}\n\n"
            f"If you did not request this, consider updating the admin password "
            f"via Settings \u2192 Admin Password."
        )
        body_html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:'Segoe UI',Arial,sans-serif;font-size:14px;
             color:#1F2328;background:#f6f8fa;margin:0;padding:0;">
  <div style="max-width:560px;margin:32px auto;background:#fff;
              border:1px solid #D0D7DE;border-radius:8px;overflow:hidden;">
    <div style="background:#2563EB;padding:20px 28px;">
      <h1 style="margin:0;color:#fff;font-size:17px;font-weight:bold;">
        Rebate Tracker &mdash; Password Recovery
      </h1>
    </div>
    <div style="padding:24px 28px;">
      <p style="margin-top:0;">
        A password recovery request was submitted from the Rebate Tracker app.
      </p>
      <table style="border-collapse:collapse;width:100%;">
        <tr>
          <td style="padding:6px 0;color:#57606A;width:160px;">Requester email:</td>
          <td style="padding:6px 0;font-weight:bold;">{self._requester}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#57606A;">Admin password:</td>
          <td style="padding:6px 0;font-weight:bold;font-family:monospace;
                     font-size:16px;letter-spacing:1px;">{self._password}</td>
        </tr>
      </table>
      <p style="color:#57606A;font-size:12px;margin-bottom:0;">
        If you did not request this, consider updating the admin password
        in Settings &rarr; Admin Password.
      </p>
    </div>
  </div>
</body>
</html>
"""
        msg = MIMEMultipart("alternative")
        msg["From"] = from_display
        msg["To"] = _RECOVERY_RECIPIENT
        msg["Subject"] = "Rebate Tracker \u2014 Admin Password Recovery"
        if cfg.get("reply_to"):
            msg["Reply-To"] = cfg["reply_to"]
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["user"], [_RECOVERY_RECIPIENT], msg.as_bytes())
            self.finished.emit(
                True,
                f"Recovery email sent to {_RECOVERY_RECIPIENT}.",
            )
        except smtplib.SMTPAuthenticationError:
            self.finished.emit(
                False,
                "Authentication failed — check your email credentials in Settings \u2192 Email.",
            )
        except smtplib.SMTPException as exc:
            self.finished.emit(False, f"SMTP error: {exc}")
        except OSError as exc:
            self.finished.emit(False, f"Network error: {exc}")
        except Exception as exc:
            self.finished.emit(False, f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Forgot-password email prompt
# ---------------------------------------------------------------------------

class _ForgotPasswordPromptDialog(QDialog):
    """Small dialog that collects the requester's email before sending."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Forgot Password")
        self.setMinimumWidth(380)
        self.setStyleSheet(f"background-color: {C['surface']}; color: {C['text']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 20)
        layout.setSpacing(14)

        info = QLabel(
            "Enter your email address. The admin password will be sent "
            "to the system administrator for review."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(8)
        self._email_input = QLineEdit()
        self._email_input.setPlaceholderText("your@email.com")
        self._email_input.setMinimumHeight(32)
        self._email_input.returnPressed.connect(self._try_accept)
        form.addRow("Your email:", self._email_input)
        layout.addLayout(form)

        self._err_lbl = QLabel("")
        self._err_lbl.setStyleSheet(f"color: {C['danger']}; font-size: 11px;")
        self._err_lbl.setVisible(False)
        layout.addWidget(self._err_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setProperty("class", "primary")
        ok_btn.clicked.disconnect()
        ok_btn.clicked.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _try_accept(self) -> None:
        import re
        text = self._email_input.text().strip()
        if not text or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
            self._err_lbl.setText("Please enter a valid email address.")
            self._err_lbl.setVisible(True)
            return
        self.accept()

    def get_email(self) -> str:
        return self._email_input.text().strip()


# ---------------------------------------------------------------------------
# Main admin login dialog
# ---------------------------------------------------------------------------

class AdminLoginDialog(QDialog):
    """
    Modal admin login dialog.

    Shows a lock icon, password field, Login / Cancel buttons,
    and an underlined "Forgot Password" link below.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Admin Login")
        self.setMinimumWidth(400)
        self.setModal(True)
        self.setStyleSheet(f"background-color: {C['surface']}; color: {C['text']};")
        self._worker: _ForgotPasswordWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(16)

        # Lock icon
        icon_lbl = QLabel("\U0001f512")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("font-size: 36px; background: transparent;")
        layout.addWidget(icon_lbl)

        # Title
        title_lbl = QLabel("Admin Login")
        title_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        # Subtitle
        sub_lbl = QLabel("Enter the admin password to enable write access.")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        layout.addWidget(sub_lbl)

        layout.addSpacing(4)

        # Password form
        form = QFormLayout()
        form.setSpacing(10)
        self._pw_input = QLineEdit()
        self._pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw_input.setPlaceholderText("Enter password\u2026")
        self._pw_input.setMinimumHeight(34)
        self._pw_input.returnPressed.connect(self._attempt_login)
        form.addRow("Password:", self._pw_input)
        layout.addLayout(form)

        # Inline error (hidden until needed)
        self._err_lbl = QLabel("")
        self._err_lbl.setStyleSheet(
            f"color: {C['danger']}; font-size: 11px; background: transparent;"
        )
        self._err_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._err_lbl.setVisible(False)
        layout.addWidget(self._err_lbl)

        # Button row: [Forgot Password] ........... [Cancel] [Login]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_forgot = QPushButton("Forgot Password")
        self._btn_forgot.setFlat(True)
        self._btn_forgot.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_forgot.setStyleSheet(
            f"color: {C['accent']}; font-size: 11px; border: none; "
            f"background: transparent; text-decoration: underline; padding: 0;"
        )
        self._btn_forgot.clicked.connect(self._forgot_password)
        btn_row.addWidget(self._btn_forgot)
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setMinimumWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self._btn_login = QPushButton("Login")
        self._btn_login.setProperty("class", "primary")
        self._btn_login.setMinimumWidth(90)
        self._btn_login.clicked.connect(self._attempt_login)
        btn_row.addWidget(self._btn_login)

        layout.addLayout(btn_row)

    def _attempt_login(self) -> None:
        entered = self._pw_input.text()
        correct = get_admin_password()
        if entered == correct:
            self.accept()
        else:
            self._err_lbl.setText("Incorrect password. Please try again.")
            self._err_lbl.setVisible(True)
            self._pw_input.selectAll()
            self._pw_input.setFocus()

    def _forgot_password(self) -> None:
        """Collect the requester's email and send the recovery email in a background thread."""
        prompt = _ForgotPasswordPromptDialog(self)
        if not prompt.exec():
            return

        requester_email = prompt.get_email()
        password = get_admin_password()

        self._btn_forgot.setEnabled(False)
        self._btn_forgot.setText("Sending\u2026")

        self._worker = _ForgotPasswordWorker(requester_email, password, self)
        self._worker.finished.connect(self._on_forgot_result)
        self._worker.start()

    def _on_forgot_result(self, success: bool, msg: str) -> None:
        self._btn_forgot.setEnabled(True)
        self._btn_forgot.setText("Forgot Password")

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Password Recovery")
        if success:
            dlg.setIcon(QMessageBox.Icon.Information)
            dlg.setText("Recovery email sent.")
            dlg.setInformativeText(msg)
        else:
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setText("Could not send recovery email.")
            dlg.setInformativeText(msg)
        dlg.exec()
