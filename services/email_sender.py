"""
services/email_sender.py
-------------------------
Send rebate statement PDFs via SMTP (Microsoft Outlook / Office 365).

Credentials are read from app_settings:
  smtp_host       — default: smtp.office365.com
  smtp_port       — default: 587 (STARTTLS)
  smtp_user       — sender address / login
  smtp_password   — entered via Settings UI, never hardcoded
  smtp_from_name  — display name for the From header

Usage
-----
    from services.email_sender import send_statement_email, get_smtp_settings

    ok, msg = send_statement_email(
        to_email="dealer@example.com",
        to_name="Dealer Name",
        account_number="12345",
        pdf_path="/path/to/12345.pdf",
    )
"""

from __future__ import annotations

import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from db.local_db import get_setting


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_smtp_settings() -> dict:
    """Return current SMTP settings from app_settings."""
    return {
        "host":      get_setting("smtp_host", "smtp.office365.com"),
        "port":      int(get_setting("smtp_port", "587") or "587"),
        "user":      get_setting("smtp_user", ""),
        "password":  get_setting("smtp_password", ""),
        "from_name": get_setting("smtp_from_name", ""),
        "reply_to":  get_setting("smtp_reply_to", ""),
    }


def smtp_configured() -> bool:
    """Return True if enough credentials exist to attempt a send."""
    s = get_smtp_settings()
    return bool(s["host"] and s["user"] and s["password"])


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------

def send_statement_email(
    to_email: str,
    to_name: str,
    account_number: str,
    pdf_path: str,
    subject: str | None = None,
    body_html: str | None = None,
) -> tuple[bool, str]:
    """
    Send a single rebate statement PDF to one recipient.

    Returns (success: bool, message: str).
    Errors are caught and returned as (False, description) — never raised.
    """
    cfg = get_smtp_settings()
    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return False, (
            "SMTP credentials are not configured. "
            "Go to Settings → Email to enter your Outlook credentials."
        )

    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        return False, f"PDF file not found: {pdf_path}"

    from_addr = cfg["user"]
    from_name = cfg["from_name"] or from_addr
    display_from = f"{from_name} <{from_addr}>"

    if not subject:
        subject = f"Rebate Statement — Account {account_number}"

    if not body_html:
        body_html = _default_body_html(to_name or to_email, account_number)

    msg = MIMEMultipart("mixed")
    msg["From"]    = display_from
    msg["To"]      = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Subject"] = subject
    if cfg.get("reply_to"):
        msg["Reply-To"] = cfg["reply_to"]

    # HTML body
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(_html_to_plain(to_name, account_number), "plain"))
    alt.attach(MIMEText(body_html, "html"))
    msg.attach(alt)

    # PDF attachment
    with open(pdf_file, "rb") as fh:
        pdf_part = MIMEApplication(fh.read(), _subtype="pdf")
        pdf_part.add_header(
            "Content-Disposition",
            "attachment",
            filename=pdf_file.name,
        )
    msg.attach(pdf_part)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(from_addr, [to_email], msg.as_bytes())
        return True, f"Statement sent to {to_email}"
    except smtplib.SMTPAuthenticationError:
        return False, (
            "Authentication failed — check your Outlook username and password "
            "in Settings → Email.  If using MFA, you may need an App Password."
        )
    except smtplib.SMTPException as exc:
        return False, f"SMTP error: {exc}"
    except OSError as exc:
        return False, f"Network error: {exc}"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


# ---------------------------------------------------------------------------
# Email body helpers
# ---------------------------------------------------------------------------

def _default_body_html(to_name: str, account_number: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family: 'Segoe UI', Arial, sans-serif; font-size: 14px;
             color: #1F2328; background: #f6f8fa; margin: 0; padding: 0;">
  <div style="max-width: 600px; margin: 32px auto; background: #ffffff;
              border: 1px solid #D0D7DE; border-radius: 8px; overflow: hidden;">
    <div style="background: #2563EB; padding: 24px 32px;">
      <h1 style="margin: 0; color: #ffffff; font-size: 20px; font-weight: bold;">
        Rebate Statement
      </h1>
    </div>
    <div style="padding: 28px 32px;">
      <p style="margin-top: 0;">Dear {to_name},</p>
      <p>
        Please find your rebate statement attached as a PDF document
        for account <strong>{account_number}</strong>.
      </p>
      <p>
        If you have any questions about your rebate calculation or statement,
        please don&rsquo;t hesitate to contact us.
      </p>
      <p style="margin-bottom: 0;">Best regards</p>
    </div>
    <div style="background: #F6F8FA; padding: 16px 32px;
                border-top: 1px solid #D0D7DE; font-size: 11px; color: #57606A;">
      This message was sent automatically by the Rebate Tracker system.
    </div>
  </div>
</body>
</html>
"""


def _html_to_plain(to_name: str, account_number: str) -> str:
    return (
        f"Dear {to_name},\n\n"
        f"Please find your rebate statement attached for account {account_number}.\n\n"
        f"If you have any questions, please contact us.\n\nBest regards"
    )
