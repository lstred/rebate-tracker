"""
db/local_db.py
--------------
SQLite local database — single source of truth for all user-defined data and
cached SQL Server data.  Uses SQLAlchemy 2.0 ORM with the modern mapped_column
style.  All SQL Server data is stored here after a sync; the app never queries
the remote server during normal operation.

Database file location (Windows): %APPDATA%\\RebateTracker\\rebate_data.db
Can be overridden via the APP_DATA_DIR env variable.
"""

import json
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer,
    String, Text, UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker,
)

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

def _resolve_db_path() -> Path:
    base = os.environ.get("APP_DATA_DIR", "").strip()
    if base:
        p = Path(base)
    else:
        appdata = os.environ.get("APPDATA", "")
        p = Path(appdata) / "RebateTracker" if appdata else Path("rebate_data_dir")
    p.mkdir(parents=True, exist_ok=True)
    return p / "rebate_data.db"


LOCAL_DB_PATH: Path = _resolve_db_path()

# ---------------------------------------------------------------------------
# SQLAlchemy engine + session
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            f"sqlite:///{LOCAL_DB_PATH}",
            connect_args={"check_same_thread": False},
        )
        # Enable WAL mode for better concurrency
        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    return _engine


def _get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional session scope."""
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# ORM Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MarketingProgram(Base):
    """A marketing program tracked by its BCCODE from the BILL_CD table."""
    __tablename__ = "marketing_programs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bccode: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    accounts: Mapped[list["Account"]] = relationship(
        "Account", back_populates="marketing_program", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<MarketingProgram bccode={self.bccode!r}>"


class Account(Base):
    """
    A dealer/account tracked in the rebate app.
    Source can be 'manual' (user-added by account number) or 'marketing_program'.
    """
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    account_name: Mapped[Optional[str]] = mapped_column(String(200))
    address1: Mapped[Optional[str]] = mapped_column(String(200))
    address2: Mapped[Optional[str]] = mapped_column(String(200))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(10))
    zip1: Mapped[Optional[str]] = mapped_column(String(10))
    zip2: Mapped[Optional[str]] = mapped_column(String(10))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    email: Mapped[Optional[str]] = mapped_column(String(255))

    source: Mapped[str] = mapped_column(String(20), nullable=False)  # 'manual' | 'marketing_program'
    marketing_program_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("marketing_programs.id"), nullable=True
    )
    # Anniversary / rebate year start date
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    marketing_program: Mapped[Optional[MarketingProgram]] = relationship(
        "MarketingProgram", back_populates="accounts"
    )
    rebate_assignments: Mapped[list["AccountRebateAssignment"]] = relationship(
        "AccountRebateAssignment", back_populates="account", cascade="all, delete-orphan"
    )
    sales_overrides: Mapped[list["SalesOverride"]] = relationship(
        "SalesOverride", back_populates="account", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        if self.account_name:
            return f"{self.account_name} ({self.account_number})"
        return self.account_number

    @property
    def full_address(self) -> str:
        parts = [self.address1, self.address2, self.city]
        parts = [p for p in parts if p]
        line1 = ", ".join(parts)
        line2_parts = []
        if self.state:
            line2_parts.append(self.state)
        zip_str = " ".join(filter(None, [self.zip1, self.zip2]))
        if zip_str:
            line2_parts.append(zip_str)
        line2 = " ".join(line2_parts)
        return "\n".join(filter(None, [line1, line2]))

    def __repr__(self) -> str:
        return f"<Account {self.account_number!r} source={self.source!r}>"


class SalesCache(Base):
    """
    Daily sales totals cached from SQL Server.
    One row per (account_number, invoice_date).
    Aggregated as: SUM(EXTENDED_PRICE_NO_FUNDS) for filtered records.
    Rebuilt entirely on each sync.
    """
    __tablename__ = "sales_cache"
    __table_args__ = (
        UniqueConstraint("account_number", "invoice_date", name="uq_sales_cache_acct_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_sales: Mapped[float] = mapped_column(Float, nullable=False)
    # Sales eligible for rebate payment (excludes unfinished wood COST_CENTER=041 and direct-ship H@WARE=DIR)
    rebate_eligible_sales: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Breakdown of excluded amounts (populated by sync; 0 for legacy rows until re-synced)
    dir_sales: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sales_041: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<SalesCache {self.account_number} {self.invoice_date} ${self.total_sales:.2f}>"


class SalesOverride(Base):
    """
    User-supplied override for a specific account's sales in a date range.
    Used to set or adjust prior-year sales for growth comparison.
    mode='replace': ignore SQL data for this period, use override amount.
    mode='add'    : SQL data + override amount.
    """
    __tablename__ = "sales_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("accounts.account_number"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)  # 'replace' | 'add'
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account: Mapped[Account] = relationship("Account", back_populates="sales_overrides")

    def __repr__(self) -> str:
        return (
            f"<SalesOverride {self.account_number} "
            f"{self.period_start}–{self.period_end} "
            f"${self.amount:.2f} mode={self.mode!r}>"
        )


class RebateStructure(Base):
    """
    A rebate structure template.
    tiers_json is a JSON array:
      [{"threshold": 0, "rate": 0.01, "mode": "dollar_one"}, ...]
    structure_type: 'tiered' | 'growth'
    For 'growth' type, thresholds represent growth-amount thresholds.
    """
    __tablename__ = "rebate_structures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    structure_type: Mapped[str] = mapped_column(String(20), nullable=False)  # tiered | growth
    description: Mapped[Optional[str]] = mapped_column(Text)
    tiers_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    is_template: Mapped[bool] = mapped_column(Boolean, default=True)
    # Eligibility overrides: include DIR / 041 items in rebate calculation for this structure
    include_dir: Mapped[bool] = mapped_column(Boolean, default=False)
    include_041: Mapped[bool] = mapped_column(Boolean, default=False)
    # For customer-level custom copies: id of the template this was derived from
    derived_from_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    assignments: Mapped[list["AccountRebateAssignment"]] = relationship(
        "AccountRebateAssignment", back_populates="rebate_structure"
    )

    def get_tiers(self) -> list[dict]:
        try:
            return json.loads(self.tiers_json)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_tiers(self, tiers: list[dict]) -> None:
        self.tiers_json = json.dumps(tiers)

    def __repr__(self) -> str:
        return f"<RebateStructure {self.name!r} type={self.structure_type!r}>"


class AccountRebateAssignment(Base):
    """Links an account to a rebate structure with an effective date."""
    __tablename__ = "account_rebate_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(
        String(50), ForeignKey("accounts.account_number"), nullable=False
    )
    rebate_structure_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rebate_structures.id"), nullable=False
    )
    effective_date: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped[Account] = relationship("Account", back_populates="rebate_assignments")
    rebate_structure: Mapped[RebateStructure] = relationship(
        "RebateStructure", back_populates="assignments"
    )

    def __repr__(self) -> str:
        return (
            f"<AccountRebateAssignment {self.account_number} "
            f"-> structure {self.rebate_structure_id}>"
        )


class PdfTemplate(Base):
    """
    PDF statement template configuration stored as JSON.
    template_json keys: company_name, primary_color, secondary_color,
    accent_color, logo_path, header_text, footer_text,
    show_tier_breakdown, show_monthly_sales, paper_size.
    """
    __tablename__ = "pdf_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    template_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def get_config(self) -> dict:
        try:
            return json.loads(self.template_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_config(self, config: dict) -> None:
        self.template_json = json.dumps(config)

    def __repr__(self) -> str:
        return f"<PdfTemplate {self.name!r} default={self.is_default}>"


class AppSetting(Base):
    """Key-value store for application settings."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<AppSetting {self.key!r}={self.value!r}>"


class AuditLog(Base):
    """Append-only audit trail of all user-initiated changes."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    action: Mapped[str] = mapped_column(String(50))         # 'add' | 'remove' | 'edit' | 'assign' | ...
    entity_type: Mapped[str] = mapped_column(String(50))    # 'account' | 'override' | 'structure' | ...
    entity_id: Mapped[Optional[str]] = mapped_column(String(200))  # account_number, override id, etc.
    description: Mapped[str] = mapped_column(Text)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<AuditLog {self.timestamp} {self.action} {self.entity_type} {self.entity_id!r}>"


# ---------------------------------------------------------------------------
# DB initialization
# ---------------------------------------------------------------------------

_DEFAULT_PDF_TEMPLATE = {
    "company_name": "Your Company",
    "primary_color": "#1a3a6e",
    "secondary_color": "#f5f5f5",
    "accent_color": "#2ecc71",
    "logo_path": "",
    "header_text": "Rebate Statement",
    "footer_text": "Thank you for your continued business.",
    "show_tier_breakdown": True,
    "show_monthly_sales": True,
    "paper_size": "letter",
}


def init_db() -> None:
    """Create all tables and seed default data on first run."""
    engine = _get_engine()
    Base.metadata.create_all(engine)

    with get_session() as session:
        # Seed default PDF template
        existing = session.query(PdfTemplate).filter_by(is_default=True).first()
        if not existing:
            tmpl = PdfTemplate(
                name="Default Template",
                is_default=True,
                template_json=json.dumps(_DEFAULT_PDF_TEMPLATE),
            )
            session.add(tmpl)

        # Seed default settings
        _seed_setting(session, "date_range_start", "")
        _seed_setting(session, "date_range_end", "")
        _seed_setting(session, "bill_to_account_field", "BACCT#")  # BILLTO column that holds account number
        _seed_setting(session, "cost_center_filter", "orders_field")  # 'item_join' | 'orders_field'
        _seed_setting(session, "cost_center_orders_field", "COST_CENTER")  # If cost_center_filter=orders_field
        # Cloud backup MySQL connection (non-sensitive defaults only; password stored by user)
        _seed_setting(session, "mysql_host", "tfnflooring.com")
        _seed_setting(session, "mysql_port", "3306")
        _seed_setting(session, "mysql_database", "dbcnqdrgsooaia")
        _seed_setting(session, "mysql_user", "nrfselec_wp404")
        _seed_setting(session, "mysql_password", "")  # entered via Settings UI
        # Email (SMTP via Outlook / Office 365)
        _seed_setting(session, "smtp_host", "smtp.office365.com")
        _seed_setting(session, "smtp_port", "587")
        _seed_setting(session, "smtp_user", "")
        _seed_setting(session, "smtp_password", "")  # entered via Settings UI
        _seed_setting(session, "smtp_from_name", "")
        # UI theme
        _seed_setting(session, "theme", "dark")

        # Migration: fix BACCT -> BACCT# if the old wrong default was seeded
        row = session.query(AppSetting).filter_by(key="bill_to_account_field").first()
        if row and row.value == "BACCT":
            row.value = "BACCT#"

    # Migration: add email column to accounts
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN email VARCHAR(255)"))
            conn.commit()
    except Exception:
        pass  # Column already exists — no-op

    # Migration: add rebate_eligible_sales column to sales_cache if it doesn't exist
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE sales_cache ADD COLUMN rebate_eligible_sales REAL NOT NULL DEFAULT 0"
            ))
            # Seed existing rows: treat all historical sales as fully eligible until next sync
            conn.execute(text(
                "UPDATE sales_cache SET rebate_eligible_sales = total_sales WHERE rebate_eligible_sales = 0"
            ))
            conn.commit()
    except Exception:
        pass  # Column already exists — no-op

    # Migration: add dir_sales and sales_041 breakdown columns to sales_cache
    for _col, _def in [
        ("dir_sales",  "REAL NOT NULL DEFAULT 0"),
        ("sales_041",  "REAL NOT NULL DEFAULT 0"),
    ]:
        try:
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE sales_cache ADD COLUMN {_col} {_def}"))
                conn.commit()
        except Exception:
            pass  # Column already exists — no-op

    # Migration: add eligibility flags and derived_from_id to rebate_structures
    for _col, _def in [
        ("include_dir",     "BOOLEAN NOT NULL DEFAULT 0"),
        ("include_041",     "BOOLEAN NOT NULL DEFAULT 0"),
        ("derived_from_id", "INTEGER"),
    ]:
        try:
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE rebate_structures ADD COLUMN {_col} {_def}"))
                conn.commit()
        except Exception:
            pass  # Column already exists — no-op

    # Migration: deactivate accounts marked as closed by the source system (*CLSD* prefix)
    with get_session() as session:
        closed = (
            session.query(Account)
            .filter(
                Account.is_active == True,
                Account.account_name.ilike("*CLSD*%"),
            )
            .all()
        )
        for _a in closed:
            _a.is_active = False


def _seed_setting(session: Session, key: str, default_value: str) -> None:
    """Insert a setting only if it does not already exist."""
    existing = session.query(AppSetting).filter_by(key=key).first()
    if not existing:
        session.add(AppSetting(key=key, value=default_value))


def get_setting(key: str, default: str = "") -> str:
    """Retrieve a single app setting value."""
    with get_session() as session:
        row = session.query(AppSetting).filter_by(key=key).first()
        return row.value if row and row.value is not None else default


def set_setting(key: str, value: str) -> None:
    """Upsert a single app setting."""
    with get_session() as session:
        row = session.query(AppSetting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            session.add(AppSetting(key=key, value=value))


def log_audit(
    action: str,
    entity_type: str,
    entity_id: str,
    description: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
) -> None:
    """Append one row to the audit log. Always opens its own session."""
    try:
        with get_session() as session:
            session.add(AuditLog(
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                description=description,
                old_value=old_value,
                new_value=new_value,
            ))
    except Exception:
        pass  # Audit logging must never crash the app

    # Trigger a live cloud backup in the background (fire-and-forget)
    try:
        from services.cloud_backup import CloudBackupWorker
        if CloudBackupWorker._instance is not None:
            CloudBackupWorker._instance.schedule()
    except Exception:
        pass  # Cloud backup must never crash the app
