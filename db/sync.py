"""
db/sync.py
----------
Background worker that pulls data from SQL Server and stores it in the local
SQLite cache.  Runs on a QThread so the UI stays responsive.

Sync covers three areas:
  1. Sales data  — dbo._ORDERS  (filtered per business rules)
  2. Account info — dbo.BILL_TO (name, address, phone)
  3. Marketing program membership — dbo.BILL_CD

SQL NOTES
---------
Field names with # or @ require square-bracket quoting in T-SQL.
INVOICE_DATE_YYYYMMDD is stored as a numeric type (YYYYMMDD integer).
Cost-center filter: by default joins dbo.ITEM on ITEM.ICCTR LIKE '0%'.
  If your _ORDERS table has a denormalized cost-center column, change the
  APP_SETTING key 'cost_center_filter' to 'orders_field' and set
  'cost_center_orders_field' to the actual column name.
BILL_TO join key: configured via APP_SETTING 'bill_to_account_field'
  (default 'BACCT') — verify against your schema.
"""

from __future__ import annotations

import traceback
from datetime import date, datetime
from typing import Optional

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.connection import get_engine as get_sql_engine
from db.local_db import (
    Account,
    MarketingProgram,
    SalesCache,
    get_session,
    get_setting,
)

# ---------------------------------------------------------------------------
# SQL builder helpers
# ---------------------------------------------------------------------------

def _sales_query(account_numbers: Optional[list[str]] = None) -> str:
    """
    Build the T-SQL query that fetches daily sales aggregates.

    Filters applied (per business rules):
      • [INVOICE#]                  > 0
      • ICCTR (from ITEM)           LIKE '0%'    ← or orders field — see settings
      • [ACCOUNT#I]                 NOT IN ('1')
      • INVOICE_DATE_YYYYMMDD       > 0  (guards against null/zero dates)

    Returns one row per (account_number, invoice_date) with SUM(sales).
    """
    cost_filter_mode = get_setting("cost_center_filter", "item_join")

    if cost_filter_mode == "item_join":
        join_clause = (
            "INNER JOIN dbo.ITEM i ON o.ITEM_MFGR_COLOR_PAT = i.ItemNumber"
        )
        cost_where = "AND i.ICCTR LIKE '0%'"
    else:
        cc_field = get_setting("cost_center_orders_field", "COST_CTR")
        join_clause = ""
        cost_where = f"AND o.[{cc_field}] LIKE '0%'"

    acct_filter = ""
    if account_numbers:
        quoted = ", ".join(f"'{a}'" for a in account_numbers)
        acct_filter = f"AND CAST(o.[ACCOUNT#I] AS NVARCHAR) IN ({quoted})"

    return f"""
        SELECT
            CAST(o.[ACCOUNT#I] AS NVARCHAR(50))      AS account_number,
            CAST(o.INVOICE_DATE_YYYYMMDD AS BIGINT)  AS invoice_date_raw,
            SUM(o.EXTENDED_PRICE_NO_FUNDS)           AS total_sales
        FROM dbo._ORDERS o
        {join_clause}
        WHERE
            o.[INVOICE#] > 0
            {cost_where}
            AND CAST(o.[ACCOUNT#I] AS NVARCHAR) NOT IN ('1')
            AND o.INVOICE_DATE_YYYYMMDD > 0
            {acct_filter}
        GROUP BY
            o.[ACCOUNT#I],
            o.INVOICE_DATE_YYYYMMDD
    """


def _account_info_query(account_numbers: list[str]) -> str:
    bt_field = get_setting("bill_to_account_field", "BACCT")
    quoted = ", ".join(f"'{a}'" for a in account_numbers)
    return f"""
        SELECT
            CAST([{bt_field}] AS NVARCHAR(50)) AS account_number,
            BNAME   AS account_name,
            BADDR1  AS address1,
            BADDR2  AS address2,
            BCITY   AS city,
            BSTATE  AS state,
            BZIP1   AS zip1,
            BZIP2   AS zip2,
            CAST(BPHONB AS NVARCHAR(20)) AS phone_raw
        FROM dbo.BILL_TO
        WHERE CAST([{bt_field}] AS NVARCHAR) IN ({quoted})
    """


def _marketing_program_query(bccode: str) -> str:
    return """
        SELECT
            CAST(BCACCT AS NVARCHAR(50)) AS account_number,
            BCCODE                       AS bccode,
            BCDATE                       AS bcdate
        FROM dbo.BILL_CD
        WHERE BCCAT = 'MP'
          AND BCCODE = ?
    """


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _parse_yyyymmdd(raw) -> Optional[date]:
    """Convert a YYYYMMDD integer/string to a Python date, or None on failure."""
    try:
        s = str(int(raw)).zfill(8)
        return datetime.strptime(s, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _format_phone(raw) -> str:
    """Format a raw numeric phone value to (XXX) XXX-XXXX."""
    if raw is None:
        return ""
    digits = "".join(c for c in str(raw) if c.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return str(raw)


def _parse_bcdate(raw) -> Optional[date]:
    """BCDATE may be YYYYMMDD integer or already a date/datetime."""
    if isinstance(raw, (date, datetime)):
        return raw.date() if isinstance(raw, datetime) else raw
    return _parse_yyyymmdd(raw)


# ---------------------------------------------------------------------------
# Core sync functions (called from the worker thread)
# ---------------------------------------------------------------------------

def sync_sales(
    account_numbers: Optional[list[str]],
    progress_cb=None,
) -> int:
    """
    Fetch sales from SQL Server and upsert into local SalesCache.
    Returns the number of (account, date) rows processed.
    If account_numbers is None, fetches for ALL known tracked accounts.
    """
    sql_engine = get_sql_engine()
    query = _sales_query(account_numbers)

    if progress_cb:
        progress_cb(5, "Executing sales query on SQL Server…")

    df = pd.read_sql(query, sql_engine)

    if df.empty:
        return 0

    if progress_cb:
        progress_cb(40, f"Processing {len(df):,} rows…")

    rows = []
    for _, row in df.iterrows():
        parsed_date = _parse_yyyymmdd(row["invoice_date_raw"])
        if parsed_date is None:
            continue
        rows.append(
            {
                "account_number": str(row["account_number"]).strip(),
                "invoice_date": parsed_date,
                "total_sales": float(row["total_sales"] or 0.0),
                "last_synced_at": datetime.utcnow(),
            }
        )

    if not rows:
        return 0

    if progress_cb:
        progress_cb(70, "Writing to local database…")

    with get_session() as session:
        # Wipe existing cache for the affected accounts then bulk insert
        affected_accounts = list({r["account_number"] for r in rows})
        if account_numbers:
            session.query(SalesCache).filter(
                SalesCache.account_number.in_(affected_accounts)
            ).delete(synchronize_session=False)
        else:
            session.query(SalesCache).delete(synchronize_session=False)

        session.bulk_insert_mappings(SalesCache, rows)

    return len(rows)


def sync_account_info(account_numbers: list[str], progress_cb=None) -> int:
    """Fetch account name/address from BILL_TO and update local Account records."""
    if not account_numbers:
        return 0

    sql_engine = get_sql_engine()
    query = _account_info_query(account_numbers)

    if progress_cb:
        progress_cb(5, "Fetching account info from BILL_TO…")

    try:
        df = pd.read_sql(query, sql_engine)
    except Exception:
        # BILL_TO join key may be wrong — non-fatal; app still works
        return 0

    updated = 0
    with get_session() as session:
        for _, row in df.iterrows():
            acct_no = str(row.get("account_number", "")).strip()
            if not acct_no:
                continue
            acct = session.query(Account).filter_by(account_number=acct_no).first()
            if acct:
                acct.account_name = _clean(row.get("account_name"))
                acct.address1 = _clean(row.get("address1"))
                acct.address2 = _clean(row.get("address2"))
                acct.city = _clean(row.get("city"))
                acct.state = _clean(row.get("state"))
                acct.zip1 = _clean(row.get("zip1"))
                acct.zip2 = _clean(row.get("zip2"))
                acct.phone = _format_phone(row.get("phone_raw"))
                updated += 1

    return updated


def sync_marketing_program(bccode: str, program_id: int, progress_cb=None) -> tuple[int, int]:
    """
    Sync membership for one marketing program (BCCODE).
    - Adds new members found in BILL_CD.
    - Marks as inactive any accounts no longer in the program.
    Returns (added_count, deactivated_count).
    """
    sql_engine = get_sql_engine()
    query = _marketing_program_query(bccode)

    if progress_cb:
        progress_cb(5, f"Syncing marketing program {bccode}…")

    try:
        df = pd.read_sql(query, sql_engine, params=(bccode,))
    except Exception:
        return 0, 0

    remote_accounts = {}  # account_number -> bcdate
    for _, row in df.iterrows():
        acct_no = str(row.get("account_number", "")).strip()
        if acct_no:
            remote_accounts[acct_no] = _parse_bcdate(row.get("bcdate"))

    added = 0
    deactivated = 0

    with get_session() as session:
        # Get all current accounts for this program
        existing = (
            session.query(Account)
            .filter_by(marketing_program_id=program_id)
            .all()
        )
        existing_map = {a.account_number: a for a in existing}

        # Add new members
        for acct_no, bcdate in remote_accounts.items():
            if acct_no not in existing_map:
                start = bcdate or date.today()
                new_acct = Account(
                    account_number=acct_no,
                    source="marketing_program",
                    marketing_program_id=program_id,
                    start_date=start,
                    is_active=True,
                )
                session.add(new_acct)
                added += 1
            else:
                # Re-activate if they returned to the program
                if not existing_map[acct_no].is_active:
                    existing_map[acct_no].is_active = True

        # Deactivate members who left the program
        for acct_no, acct in existing_map.items():
            if acct_no not in remote_accounts and acct.is_active:
                acct.is_active = False
                deactivated += 1

    return added, deactivated


def _clean(val) -> Optional[str]:
    """Strip a pandas value to a clean string or None."""
    if val is None or (isinstance(val, float) and val != val):
        return None
    s = str(val).strip()
    return s if s else None


# ---------------------------------------------------------------------------
# QThread worker
# ---------------------------------------------------------------------------

class SyncWorker(QThread):
    """
    Background thread that performs a full data refresh from SQL Server.
    Emits progress(percent, message) and finished(success, summary_message).
    """

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self._do_sync()
        except Exception as exc:
            self.finished.emit(False, f"Sync failed: {exc}\n{traceback.format_exc()}")

    def _do_sync(self):
        def prog(pct, msg):
            self.progress.emit(pct, msg)
            return self._cancelled

        prog(0, "Starting sync…")

        # --- 1. Collect tracked accounts ---
        with get_session() as session:
            all_accounts = (
                session.query(Account).filter_by(is_active=True).all()
            )
            account_numbers = [a.account_number for a in all_accounts]
            programs = session.query(MarketingProgram).all()
            program_list = [(p.bccode, p.id) for p in programs]

        if self._cancelled:
            self.finished.emit(False, "Sync cancelled.")
            return

        # --- 2. Sync marketing program memberships ---
        prog(5, "Syncing marketing program memberships…")
        total_added = total_deac = 0
        for i, (bccode, prog_id) in enumerate(program_list):
            if self._cancelled:
                break
            a, d = sync_marketing_program(bccode, prog_id)
            total_added += a
            total_deac += d

        if total_added or total_deac:
            # Refresh account list after MP sync
            with get_session() as session:
                account_numbers = [
                    a.account_number
                    for a in session.query(Account).filter_by(is_active=True).all()
                ]

        if self._cancelled:
            self.finished.emit(False, "Sync cancelled.")
            return

        # --- 3. Sync account info from BILL_TO ---
        prog(20, "Fetching account details from BILL_TO…")
        info_updated = sync_account_info(account_numbers)

        if self._cancelled:
            self.finished.emit(False, "Sync cancelled.")
            return

        # --- 4. Sync sales data ---
        prog(35, "Fetching sales data from SQL Server…")
        rows = sync_sales(account_numbers, progress_cb=lambda p, m: prog(35 + int(p * 0.6), m))

        prog(100, "Sync complete.")
        summary = (
            f"Sync complete. "
            f"Sales rows: {rows:,} | "
            f"Accounts info updated: {info_updated} | "
            f"MP additions: {total_added} | "
            f"MP deactivations: {total_deac}"
        )
        self.finished.emit(True, summary)
