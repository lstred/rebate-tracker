"""
services/cloud_backup.py
------------------------
Live cloud backup to a remote MySQL database via PyMySQL.

WHAT IS BACKED UP
-----------------
Everything a user has entered that cannot be re-fetched from SQL Server:
  • accounts            (dealers, start dates, contact info)
  • rebate_structures   (tier configs)
  • account_rebate_assignments
  • sales_overrides
  • pdf_templates
  • app_settings
  • marketing_programs

NOT backed up:
  • sales_cache    — rebuilt from SQL Server on sync
  • audit_log      — per user preference

MYSQL TABLE DESIGN
------------------
One table: ``rebate_tracker_snapshots``
  table_name  VARCHAR(100)  PRIMARY KEY
  snapshot_json LONGTEXT
  updated_at  DATETIME

This stores a full JSON snapshot per logical table.  The dataset is small
(tens to a few hundred rows) so a full snapshot per table is safe and simple.

SECURITY NOTES
--------------
• Credentials are stored in the local SQLite app_settings table (never in
  source code) and read at runtime.
• PyMySQL uses parameterized queries throughout — no SQL injection risk.
• Only our dedicated table (``rebate_tracker_snapshots``) is touched.
• SSL is requested when the server supports it (ssl={"ssl_disabled": False}).
• Credentials are never logged or included in exception messages sent to the UI.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from db.local_db import get_setting


# ---------------------------------------------------------------------------
# Credentials helpers  (never hardcoded — always read from settings)
# ---------------------------------------------------------------------------

def _get_mysql_config() -> dict:
    """Return the connection kwargs for PyMySQL.  Reads settings at call time."""
    return {
        "host": get_setting("mysql_host", ""),
        "port": int(get_setting("mysql_port", "3306") or "3306"),
        "database": get_setting("mysql_database", ""),
        "user": get_setting("mysql_user", ""),
        "password": get_setting("mysql_password", ""),
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "autocommit": False,
        "ssl": {"ssl_disabled": False},  # request TLS when server supports it
    }


def is_cloud_backup_configured() -> bool:
    """Return True only if all required MySQL settings are non-empty."""
    cfg = _get_mysql_config()
    return all(cfg.get(k, "") for k in ("host", "database", "user", "password"))


# ---------------------------------------------------------------------------
# Low-level connection helper
# ---------------------------------------------------------------------------

def _connect():
    """Open and return a PyMySQL connection or raise RuntimeError on failure."""
    import pymysql  # deferred import so the app starts even if PyMySQL is absent

    cfg = _get_mysql_config()
    if not all(cfg.get(k, "") for k in ("host", "database", "user", "password")):
        raise RuntimeError("Cloud backup not configured. Enter MySQL credentials in Settings.")

    # Strip ssl dict if host is localhost (avoids handshake issues in dev)
    if cfg["host"].lower() in ("localhost", "127.0.0.1"):
        cfg.pop("ssl", None)

    try:
        conn = pymysql.connect(**cfg)
    except pymysql.err.OperationalError as exc:
        raw = exc.args[1] if exc.args else str(exc)
        if "is not allowed to connect" in raw:
            # The MySQL user doesn't have permission to connect from this network's
            # outgoing IP.  This is fixed on the server side (cPanel / phpMyAdmin).
            host_hint = cfg.get("host", "the MySQL server")
            raise RuntimeError(
                f"MySQL connection failed: {raw}\n\n"
                f"Your network's outgoing IP address is not authorized on {host_hint}. "
                f"To fix this, log in to cPanel → MySQL Databases → Remote Database Access Hosts "
                f"and add % (allow all) or your specific public IP address."
            ) from exc
        raise RuntimeError(f"MySQL connection failed: {raw}") from exc
    return conn


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS `rebate_tracker_snapshots` (
    `table_name`      VARCHAR(100) NOT NULL,
    `snapshot_json`   LONGTEXT     NOT NULL,
    `updated_at`      DATETIME     NOT NULL,
    PRIMARY KEY (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def _ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_BOOTSTRAP_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Serialisation  (mirrors services/backup.py logic — no ORM imports here)
# ---------------------------------------------------------------------------

def _isoformat(val: Any) -> Any:
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def _collect_payload() -> dict:
    """Read all backupable data from SQLite and return as a plain dict."""
    from db.local_db import (
        Account,
        AccountRebateAssignment,
        AppSetting,
        MarketingProgram,
        PdfTemplate,
        RebateStructure,
        SalesOverride,
        get_session,
    )

    with get_session() as session:
        programs = session.query(MarketingProgram).all()
        accounts = session.query(Account).all()
        overrides = session.query(SalesOverride).all()
        structures = session.query(RebateStructure).all()
        assignments = session.query(AccountRebateAssignment).all()
        pdf_templates = session.query(PdfTemplate).all()
        settings = session.query(AppSetting).all()

        return {
            "marketing_programs": [
                {"bccode": p.bccode, "name": p.name}
                for p in programs
            ],
            "accounts": [
                {
                    "account_number": a.account_number,
                    "account_name": a.account_name,
                    "address1": a.address1,
                    "address2": a.address2,
                    "city": a.city,
                    "state": a.state,
                    "zip1": a.zip1,
                    "zip2": a.zip2,
                    "phone": a.phone,
                    "source": a.source,
                    "marketing_program_bccode": (
                        a.marketing_program.bccode if a.marketing_program else None
                    ),
                    "start_date": _isoformat(a.start_date),
                    "is_active": a.is_active,
                }
                for a in accounts
            ],
            "sales_overrides": [
                {
                    "account_number": o.account_number,
                    "period_start": _isoformat(o.period_start),
                    "period_end": _isoformat(o.period_end),
                    "amount": o.amount,
                    "mode": o.mode,
                    "notes": o.notes,
                }
                for o in overrides
            ],
            "rebate_structures": [
                {
                    "_import_id": s.id,
                    "name": s.name,
                    "structure_type": s.structure_type,
                    "description": s.description,
                    "tiers": s.get_tiers(),
                    "is_template": s.is_template,
                }
                for s in structures
            ],
            "account_rebate_assignments": [
                {
                    "account_number": a.account_number,
                    "rebate_structure_import_id": a.rebate_structure_id,
                    "effective_date": _isoformat(a.effective_date),
                }
                for a in assignments
            ],
            "pdf_templates": [
                {
                    "name": t.name,
                    "is_default": t.is_default,
                    "config": t.get_config(),
                }
                for t in pdf_templates
            ],
            "app_settings": [
                {"key": s.key, "value": s.value}
                for s in settings
                # Never back up the MySQL password to the cloud (circular / insecure)
                if s.key != "mysql_password"
            ],
        }


# ---------------------------------------------------------------------------
# Core push / pull
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO `rebate_tracker_snapshots` (`table_name`, `snapshot_json`, `updated_at`)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE
    `snapshot_json` = VALUES(`snapshot_json`),
    `updated_at`    = VALUES(`updated_at`)
"""


def push_backup() -> tuple[bool, str]:
    """
    Collect all backupable data from SQLite and push to MySQL.
    Returns (success, human_readable_message).
    Credentials are never included in the returned message.
    """
    if not is_cloud_backup_configured():
        return False, "Cloud backup not configured. Enter MySQL credentials in Settings."

    try:
        payload = _collect_payload()
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        conn = _connect()
        try:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                for table_name, rows in payload.items():
                    cur.execute(
                        _UPSERT_SQL,
                        (table_name, json.dumps(rows, default=str), now),
                    )
            conn.commit()
        finally:
            conn.close()

        total = sum(len(v) for v in payload.values())
        return True, f"Cloud backup updated — {total} records across {len(payload)} tables."

    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Cloud backup error: {type(exc).__name__}"


def pull_backup() -> tuple[bool, dict | str]:
    """
    Fetch the latest snapshot from MySQL.
    Returns (True, payload_dict) on success or (False, error_string) on failure.
    """
    if not is_cloud_backup_configured():
        return False, "Cloud backup not configured."

    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT `table_name`, `snapshot_json`, `updated_at` "
                    "FROM `rebate_tracker_snapshots`"
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return False, "No cloud backup found. Push a backup first."

        payload: dict = {}
        last_updated: Optional[str] = None
        for table_name, snapshot_json, updated_at in rows:
            payload[table_name] = json.loads(snapshot_json)
            if last_updated is None or str(updated_at) > last_updated:
                last_updated = str(updated_at)

        payload["_meta"] = {"last_updated": last_updated}
        return True, payload

    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Cloud restore error: {type(exc).__name__}"


def test_connection() -> tuple[bool, str]:
    """Attempt a connection and table check. Returns (ok, message)."""
    if not is_cloud_backup_configured():
        return False, "MySQL credentials incomplete — fill in all fields and save first."
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM `rebate_tracker_snapshots`"
                )
                (count,) = cur.fetchone()
        finally:
            conn.close()
        return True, f"Connected successfully. {count} snapshot row(s) in cloud database."
    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Connection error: {type(exc).__name__}"


def get_last_backup_time() -> Optional[str]:
    """Return ISO timestamp string of last cloud push, or None."""
    if not is_cloud_backup_configured():
        return None
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(`updated_at`) FROM `rebate_tracker_snapshots`"
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Restore helper  (reuses logic from services/backup.py import_backup)
# ---------------------------------------------------------------------------

def restore_from_cloud() -> tuple[bool, str]:
    """
    Pull backup from MySQL and restore into the local SQLite database.
    Returns (success, message).
    """
    ok, result = pull_backup()
    if not ok:
        return False, result  # result is the error string

    payload = result
    payload.pop("_meta", None)

    # Delegate to the existing import logic via a temp JSON file approach,
    # but we replicate the core restore inline to avoid a temp file.
    from services.backup import import_backup as _file_import
    import tempfile, os

    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"version": "1.0", **payload}, tmp, default=str)
        tmp.close()
        ok2, msg = _file_import(tmp.name)
        return ok2, msg
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background QThread worker for non-blocking live push
# ---------------------------------------------------------------------------

class CloudBackupWorker(QThread):
    """
    Fire-and-forget background thread.
    Call schedule() from the main thread whenever data changes;
    it debounces rapid changes and pushes once everything settles.
    """

    status_changed = pyqtSignal(bool, str)   # (success, message) after each push

    # Singleton reference kept on the main window
    _instance: "Optional[CloudBackupWorker]" = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = False

    def schedule(self) -> None:
        """Mark that a push is needed.  Thread-safe."""
        self._pending = True
        if not self.isRunning():
            self.start()

    def run(self) -> None:
        import time
        # Debounce: wait up to 3 s for additional rapid changes to settle
        while self._pending:
            self._pending = False
            time.sleep(3)

        ok, msg = push_backup()
        self.status_changed.emit(ok, msg)
