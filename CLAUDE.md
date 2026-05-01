# Rebate Tracker — Project Context for Claude

## What This App Does
Desktop application for tracking dealer rebates against SQL Server sales data.
Dealers earn rebates based on tiered thresholds applied to their sales (or growth) within their rebate year.
The app caches SQL Server data locally in SQLite so it works offline after a sync.

---

## Tech Stack
| Layer | Technology |
|---|---|
| UI | Python 3.11 + PyQt6 6.11.0 |
| Local DB | SQLAlchemy 2.0 + SQLite (WAL mode) |
| Remote DB | SQL Server `NRFVMSSQL04/NRF_REPORTS`, Windows auth |
| PDF generation | ReportLab |
| Charts | Matplotlib (QtAgg backend) |
| Python env | `C:\rtenv` (short path required — Windows MAX_PATH issue with PyQt6 QML) |

## Run Command
```powershell
C:\rtenv\Scripts\python main.py
# Working directory: C:\Users\lukass\Desktop\rebate tracking\
```

## Git
- Repo: https://github.com/lstred/rebate-tracker  
- Branch: `main`

---

## File Structure
```
rebate tracking/
├── main.py                        # Entry point — sets matplotlib backend, then QApplication
├── db/
│   ├── local_db.py                # SQLite ORM models + init_db() + log_audit()
│   ├── connection.py              # SQL Server engine factory (Windows auth)
│   └── sync.py                   # SyncWorker QThread + sync_sales() / sync_account_info()
├── services/
│   ├── rebate_calculator.py       # Core calculation engine (no UI/DB imports)
│   ├── pdf_generator.py           # ReportLab PDF statement builder
│   └── backup.py                  # JSON backup/restore
├── ui/
│   ├── main_window.py             # App shell: sidebar + TopBar + QStackedWidget
│   ├── theme.py                   # Colour constants (C dict)
│   └── views/
│       ├── dashboard_view.py      # KPI cards + bar chart (DashboardLoader QThread)
│       ├── accounts_view.py       # Account list + detail panel + overrides
│       ├── rebate_structures_view.py  # Tier editor + structure CRUD
│       ├── pdf_template_view.py   # PDF template editor + batch export
│       ├── audit_log_view.py      # Read-only audit trail viewer
│       └── settings_view.py       # App settings (field names, data management)
└── models/__init__.py             # Re-exports all ORM models
```

---

## SQLite Local Database
**Path:** `%APPDATA%\RebateTracker\rebate_data.db`

### Key Tables
| Table | Purpose |
|---|---|
| `accounts` | Tracked dealers; `is_active` flag (removed = False, not deleted) |
| `sales_cache` | Daily sales totals per account synced from SQL Server |
| `rebate_structures` | Tier configs stored as JSON in `tiers_json` |
| `account_rebate_assignments` | Links account → rebate structure |
| `sales_overrides` | Manual prior-year sales corrections |
| `pdf_templates` | PDF template config as JSON |
| `app_settings` | Key-value settings store |
| `audit_log` | Append-only record of all user changes |

---

## SQL Server Schema (NRF_REPORTS)
### Sales data: `dbo._ORDERS`
| Column | Notes |
|---|---|
| `ACCOUNT#I` | Account number |
| `ENTENDED_PRICE_NO_FUNDS` | **Typo in source DB** — this is the sales amount (not EXTENDED_) |
| `INVOICE_DATE_YYYYMMDD` | Integer in YYYYMMDD format |
| `COST_CENTER` | Cost center (filter: `LIKE '0%'` for product sales) |

### Account info: `dbo.BILLTO` (no underscore — NOT `dbo.BILL_TO`)
| Column | Notes |
|---|---|
| `BACCT#` | Account number — **numeric column**, must cast via `CAST(CAST([BACCT#] AS BIGINT) AS NVARCHAR)` to avoid `50039.0` formatting |
| `BNAME` | Account/dealer name |
| `BADDR1`, `BADDR2` | Address lines |
| `BCITY`, `BSTATE`, `BZIP1`, `BZIP2` | City/state/zip (BZIP1/BZIP2 are also numeric, cast to NVARCHAR) |
| `BPHONB` | Phone (numeric, formatted to (XXX) XXX-XXXX) |

### App Settings for SQL Server fields
| Setting key | Current value | What it controls |
|---|---|---|
| `bill_to_account_field` | `BACCT#` | Column in BILL_TO that holds account number |
| `cost_center_filter` | `orders_field` | How cost center is filtered |
| `cost_center_orders_field` | `COST_CENTER` | Column name in _ORDERS for cost center |

---

## Rebate Calculation Logic
- **Period:** Always starts from `account.start_date` (anniversary-based), not Jan 1.
- **Prior year:** Same relative window shifted back exactly one year.
- **Tier types (per tier, not per structure):**
  - `sales` — rate applied to total current sales
  - `growth` — rate applied to `max(0, current_sales - prior_year_sales)`
  - `freight` — produces a `FreightQualification` (no dollar amount, just tracks qualification)
- **Tier modes:**
  - `dollar_one` — rate applies to ALL sales from dollar one when threshold is crossed (overrides lower tiers)
  - `forward_only` — rate applies only to incremental sales above the threshold (stacks)
- `structure_type` field in DB is kept for backward compat but new structures are always `"tiered"` with `applies_to` per tier.

---

## Audit Trail
Every user action is logged to `audit_log` via `log_audit()` in `local_db.py`.
Actions: `add`, `reactivate`, `remove`, `edit`, `assign`, `delete`.
Viewable in the **Audit Log** tab (sidebar nav index 4).

---

## Known Historical Fixes
- **PyQt6 MAX_PATH:** Install to `C:\rtenv` (short path) not default site-packages.
- **Column typo:** Source DB has `ENTENDED_PRICE_NO_FUNDS` (not `EXTENDED_`).
- **BILLTO table name:** The table is `dbo.BILLTO` (no underscore). `dbo.BILL_TO` gives "Invalid object name" error.
- **BACCT# is numeric:** The `BACCT#` column is a numeric type. Must cast via `CAST(CAST([BACCT#] AS BIGINT) AS NVARCHAR(50))` — a plain `CAST([BACCT#] AS NVARCHAR)` would produce `50039.0` and not match string account numbers.
- **BACCT# field:** BILL_TO account number column is `BACCT#` — must be quoted as `[BACCT#]` in T-SQL. Old default was wrong (`BACCT`); migration in `init_db()` fixes live DBs.
- **QThread.start() shadowing:** Never use `self.start = value` inside a QThread subclass — it overwrites the `start()` method. Use `self._start`.
- **Session detached objects:** Always capture data inside `with get_session()` before opening dialogs; open fresh sessions for writes.
