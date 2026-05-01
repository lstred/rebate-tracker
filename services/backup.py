"""
services/backup.py
------------------
Export all user-managed data to a portable JSON file and restore it.

What is backed up (everything the user has entered — NOT SQL Server cache):
  • MarketingPrograms
  • Accounts  (source, start_date, is_active — NOT cached sales rows)
  • SalesOverrides
  • RebateStructures
  • AccountRebateAssignments
  • PdfTemplates
  • AppSettings

What is NOT backed up (re-fetchable from SQL Server on demand):
  • SalesCache  (rebuilt on next sync)

Backup file is a single UTF-8 JSON file with a version header.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

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

BACKUP_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _to_str(val: Any) -> Any:
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def _account_to_dict(a: Account) -> dict:
    return {
        "account_number": a.account_number,
        "account_name": a.account_name,
        "address1": a.address1,
        "address2": a.address2,
        "city": a.city,
        "state": a.state,
        "zip1": a.zip1,
        "zip2": a.zip2,
        "phone": a.phone,
        "email": a.email,
        "source": a.source,
        "marketing_program_bccode": (
            a.marketing_program.bccode if a.marketing_program else None
        ),
        "start_date": _to_str(a.start_date),
        "is_active": a.is_active,
    }


def _override_to_dict(o: SalesOverride) -> dict:
    return {
        "account_number": o.account_number,
        "period_start": _to_str(o.period_start),
        "period_end": _to_str(o.period_end),
        "amount": o.amount,
        "mode": o.mode,
        "notes": o.notes,
    }


def _structure_to_dict(s: RebateStructure) -> dict:
    return {
        "name": s.name,
        "structure_type": s.structure_type,
        "description": s.description,
        "tiers": s.get_tiers(),
        "is_template": s.is_template,
        "include_dir": getattr(s, "include_dir", False) or False,
        "include_041": getattr(s, "include_041", False) or False,
        "derived_from_id": s.derived_from_id,
        "_import_id": s.id,  # used during restore to re-link assignments
    }


def _assignment_to_dict(a: AccountRebateAssignment) -> dict:
    return {
        "account_number": a.account_number,
        "rebate_structure_import_id": a.rebate_structure_id,
        "effective_date": _to_str(a.effective_date),
    }


def _pdf_template_to_dict(t: PdfTemplate) -> dict:
    return {
        "name": t.name,
        "is_default": t.is_default,
        "config": t.get_config(),
    }


def _setting_to_dict(s: AppSetting) -> dict:
    return {"key": s.key, "value": s.value}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_backup(file_path: str) -> tuple[bool, str]:
    """
    Write all user data to a JSON backup file.
    Returns (success, message).
    """
    try:
        with get_session() as session:
            programs = session.query(MarketingProgram).all()
            accounts = session.query(Account).all()
            overrides = session.query(SalesOverride).all()
            structures = session.query(RebateStructure).all()
            assignments = session.query(AccountRebateAssignment).all()
            pdf_templates = session.query(PdfTemplate).all()
            settings = session.query(AppSetting).all()

            payload = {
                "version": BACKUP_VERSION,
                "exported_at": datetime.utcnow().isoformat(),
                "marketing_programs": [
                    {"bccode": p.bccode, "name": p.name} for p in programs
                ],
                "accounts": [_account_to_dict(a) for a in accounts],
                "sales_overrides": [_override_to_dict(o) for o in overrides],
                "rebate_structures": [_structure_to_dict(s) for s in structures],
                "account_rebate_assignments": [
                    _assignment_to_dict(a) for a in assignments
                ],
                "pdf_templates": [_pdf_template_to_dict(t) for t in pdf_templates],
                "app_settings": [_setting_to_dict(s) for s in settings],
            }

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

        account_count = len(payload["accounts"])
        return True, f"Backup saved — {account_count} accounts exported to:\n{file_path}"

    except Exception as exc:
        return False, f"Backup failed: {exc}"


# ---------------------------------------------------------------------------
# Import / Restore
# ---------------------------------------------------------------------------

def import_backup(file_path: str) -> tuple[bool, str]:
    """
    Restore user data from a JSON backup file.
    Existing data is cleared for restored tables; SalesCache is NOT touched.
    Returns (success, message).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        version = payload.get("version", "unknown")
        if version != BACKUP_VERSION:
            return False, (
                f"Incompatible backup version ({version}). "
                f"Expected {BACKUP_VERSION}."
            )

        with get_session() as session:
            # --- Clear restorable tables (order respects FK constraints) ---
            session.query(AccountRebateAssignment).delete()
            session.query(SalesOverride).delete()
            session.query(Account).delete()
            session.query(RebateStructure).delete()
            session.query(PdfTemplate).delete()
            session.query(MarketingProgram).delete()
            # Keep AppSettings — merge below

            # --- Marketing Programs ---
            bccode_to_id: dict[str, int] = {}
            for pd in payload.get("marketing_programs", []):
                prog = MarketingProgram(bccode=pd["bccode"], name=pd.get("name"))
                session.add(prog)
                session.flush()
                bccode_to_id[pd["bccode"]] = prog.id

            # --- Accounts ---
            for ad in payload.get("accounts", []):
                mp_id = bccode_to_id.get(ad.get("marketing_program_bccode"))
                start = _parse_date(ad.get("start_date"))
                acct = Account(
                    account_number=ad["account_number"],
                    account_name=ad.get("account_name"),
                    address1=ad.get("address1"),
                    address2=ad.get("address2"),
                    city=ad.get("city"),
                    state=ad.get("state"),
                    zip1=ad.get("zip1"),
                    zip2=ad.get("zip2"),
                    phone=ad.get("phone"),
                    email=ad.get("email"),
                    source=ad.get("source", "manual"),
                    marketing_program_id=mp_id,
                    start_date=start or date.today(),
                    is_active=ad.get("is_active", True),
                )
                session.add(acct)

            # --- Rebate Structures ---
            # First pass: create all structures and record old→new ID mapping.
            # Track which structures need derived_from_id re-linked after all are created.
            old_id_to_new: dict[int, int] = {}
            pending_derived: list[tuple[int, int]] = []  # (new_struct_id, old_derived_from_id)
            for sd in payload.get("rebate_structures", []):
                struct = RebateStructure(
                    name=sd["name"],
                    structure_type=sd["structure_type"],
                    description=sd.get("description"),
                    is_template=sd.get("is_template", True),
                    include_dir=bool(sd.get("include_dir", False)),
                    include_041=bool(sd.get("include_041", False)),
                )
                struct.set_tiers(sd.get("tiers", []))
                session.add(struct)
                session.flush()
                old_id = sd.get("_import_id")
                if old_id is not None:
                    old_id_to_new[old_id] = struct.id
                old_derived = sd.get("derived_from_id")
                if old_derived is not None:
                    pending_derived.append((struct.id, int(old_derived)))

            # Second pass: fix up derived_from_id now that all new IDs are known
            for new_struct_id, old_derived_id in pending_derived:
                new_derived_id = old_id_to_new.get(old_derived_id)
                if new_derived_id:
                    to_fix = session.get(RebateStructure, new_struct_id)
                    if to_fix:
                        to_fix.derived_from_id = new_derived_id

            # --- Account Rebate Assignments ---
            for asgn in payload.get("account_rebate_assignments", []):
                old_struct_id = asgn.get("rebate_structure_import_id")
                new_struct_id = old_id_to_new.get(old_struct_id)
                if new_struct_id:
                    session.add(
                        AccountRebateAssignment(
                            account_number=asgn["account_number"],
                            rebate_structure_id=new_struct_id,
                            effective_date=_parse_date(asgn.get("effective_date")),
                        )
                    )

            # --- Sales Overrides ---
            for od in payload.get("sales_overrides", []):
                session.add(
                    SalesOverride(
                        account_number=od["account_number"],
                        period_start=_parse_date(od["period_start"]),
                        period_end=_parse_date(od["period_end"]),
                        amount=float(od["amount"]),
                        mode=od["mode"],
                        notes=od.get("notes"),
                    )
                )

            # --- PDF Templates ---
            for td in payload.get("pdf_templates", []):
                t = PdfTemplate(
                    name=td["name"],
                    is_default=td.get("is_default", False),
                )
                t.set_config(td.get("config", {}))
                session.add(t)

            # --- App Settings (merge — do not overwrite with empty values) ---
            for sd in payload.get("app_settings", []):
                from db.local_db import AppSetting
                existing = session.query(AppSetting).filter_by(key=sd["key"]).first()
                if existing:
                    if sd.get("value"):  # Only overwrite if backup has a value
                        existing.value = sd["value"]
                else:
                    session.add(AppSetting(key=sd["key"], value=sd.get("value", "")))

        acct_count = len(payload.get("accounts", []))
        return True, (
            f"Restore complete — {acct_count} accounts loaded from backup.\n"
            "Note: Sales cache was not restored; run a data refresh to reload sales."
        )

    except Exception as exc:
        return False, f"Restore failed: {exc}"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_date(val) -> date | None:
    if not val:
        return None
    if isinstance(val, date):
        return val
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(val), fmt).date()
        except ValueError:
            continue
    return None
