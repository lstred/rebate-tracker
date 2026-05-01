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
| Cloud Backup | MySQL via PyMySQL 2.2.8 — live sync to `tfnflooring.com` |
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
│   ├── cloud_backup.py            # MySQL live backup: CloudBackupWorker (QThread singleton) + push/pull/restore
│   └── backup.py                  # JSON backup/restore (local file)
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

### App Settings for MySQL Cloud Backup
| Setting key | Default | What it controls |
|---|---|---|
| `mysql_host` | `tfnflooring.com` | Cloud DB host |
| `mysql_port` | `3306` | Cloud DB port |
| `mysql_database` | `dbcnqdrgsooaia` | Cloud DB name |
| `mysql_user` | `nrfselec_wp404` | Cloud DB user |
| `mysql_password` | `""` | Cloud DB password (user enters in Settings UI; never hardcoded) |

---

## Rebate Calculation Logic
- **Period:** Always starts from `account.start_date` (anniversary-based), not Jan 1.
- **Prior year:** Same relative window shifted back exactly one year.
- **Rebate exclusions:** `COST_CENTER='041'` (unfinished wood) and direct-ship orders (`OPENPO_H.H@WARE='DIR'`, joined via `_ORDERS.[ORDER#] = OPENPO_H.[H@REF#]`) are excluded from `rebate_eligible_sales` but still count toward tier thresholds (tracked in `total_sales`). Rate is prorated by `elig_frac = eligible / total`.
- **Tier types (per tier, not per structure):**
  - `sales` — rate applied to total current sales
  - `growth` — rate applied to `max(0, current_sales - prior_year_sales)`
  - `freight` — produces a `FreightQualification` (no dollar amount, just tracks qualification)
- **Tier modes:**
  - `dollar_one` — rate applies to ALL sales from dollar one when threshold is crossed (overrides lower tiers)
  - `forward_only` — rate applies only to incremental sales above the threshold (stacks)
- `structure_type` field in DB is kept for backward compat but new structures are always `"tiered"` with `applies_to` per tier.

---

## Cloud Backup
- **Service:** `services/cloud_backup.py` — MySQL via PyMySQL 2.2.8.
- **Table:** `rebate_tracker_snapshots` (columns: `table_name` PK, `snapshot_json` LONGTEXT, `updated_at` DATETIME).
- **Singleton:** `CloudBackupWorker(QThread)` — use `CloudBackupWorker._instance` to get the running instance. `schedule()` debounces 3 s then calls `push_backup()`.
- **Trigger:** `log_audit()` calls `_instance.schedule()` after every write, so backup is always live.
- **Credentials:** Never hardcoded. Non-sensitive defaults seeded in `app_settings`; password is empty by default — user enters in Settings → Cloud Backup.
- **Restore:** `restore_from_cloud()` pulls JSON snapshot then delegates to `import_backup()` via tempfile. App must restart after restore.
- **Excluded from backup:** `sales_cache`, `audit_log`, `mysql_password` setting.

---

## UI / Gallery Conventions
- **Gallery panel** (left, 320 px): Custom `AccountGalleryItem` widgets — account number, program BCCODE badge, days-to-renewal countdown, account name, start date, mini `TierProgressBar`.
- **Sort order:** Accounts sorted by days until next rebate-year anniversary (soonest renewals at top). Users can follow up before the new year starts.
- **Renewal countdown colours:** ≤ 30 d = red, ≤ 60 d = amber, else muted.
- **TierProgressBar:** Custom `QWidget` used both in the detail panel (full, with labels) and the gallery (mini, 9 px). Merges tiers sharing the same threshold into one boundary marker. Amber diamond ◆ shows straight-line projected year-end position. Green tick = threshold crossed, gray tick = not yet reached. Freight-only tiers at the same threshold get a ✦ suffix.
- **Program BCCODE badge:** Shown in both the gallery card and the detail panel header.
Every user action is logged to `audit_log` via `log_audit()` in `local_db.py`.
Actions: `add`, `reactivate`, `remove`, `edit`, `assign`, `delete`.
Viewable in the **Audit Log** tab (sidebar nav index 4).

---

## Known Historical Fixes
- **PyQt6 MAX_PATH:** Install to `C:\rtenv` (short path) not default site-packages.
- **Column typo:** Source DB has `ENTENDED_PRICE_NO_FUNDS` (not `EXTENDED_`).
- **BILLTO table name:** The table is `dbo.BILLTO` (no underscore). `dbo.BILL_TO` gives "Invalid object name" error.
- **BACCT# is numeric:** The `BACCT#` column is a numeric type. Must cast via `CAST(CAST([BACCT#] AS BIGINT) AS NVARCHAR(50))` — a plain `CAST([BACCT#] AS NVARCHAR)` would produce `50039.0` and not match string account numbers.
- **\*CLSD\* accounts:** Source system marks closed accounts with `*CLSD*` at the start of BNAME. `sync_account_info()` automatically deactivates these. `init_db()` runs a one-time migration to deactivate any already-cached closed accounts on startup.
- **OPENPO_H join:** Table is `dbo.OPENPO_H`. Join key is `_ORDERS.[ORDER#] = OPENPO_H.[H@REF#]`. Field `H@WARE` = `'DIR'` means direct ship (excluded from rebate-eligible sales).
- **BACCT# field:** BILL_TO account number column is `BACCT#` — must be quoted as `[BACCT#]` in T-SQL. Old default was wrong (`BACCT`); migration in `init_db()` fixes live DBs.
- **QThread.start() shadowing:** Never use `self.start = value` inside a QThread subclass — it overwrites the `start()` method. Use `self._start`.
- **Session detached objects:** Always capture data inside `with get_session()` before opening dialogs; open fresh sessions for writes.
- **Tier contribution display (PDF + UI):** `TierResult.tier_number` is the within-subset index (sales tiers counted separately from growth/freight tiers), so it cannot be used to match contributions back to the global sorted tier list. Always match by `(threshold, rate, applies_to)` with a small float tolerance. Dollar-one tiers superseded by a higher dollar-one tier will have `rebate_contribution == 0` and should show `—` rather than `$0.00`.
- **Projection uses current rebate year start:** `get_account_period()` returns the original `account.start_date` (e.g. 2024-07-03), which may be years in the past. For annualization, use `_current_rebate_year_start(start_date, today)` (defined in `accounts_view.py`) — the most recent anniversary ≤ today — so `elapsed_days` is always within the current 12-month window. Using `account.start_date` directly caused multi-year elapsed spans and wildly underestimated projections.
- **Table columns squeezed / internal scroll:** For `QTableWidget` panels in the detail view, set `ResizeToContents` on all data columns and `Stretch` only on the label column. Disable the vertical scrollbar and set `minimumHeight = header_height + row_height × row_count` so the outer `QScrollArea` handles all scrolling without a nested scroll.
- **MySQL "host not allowed" error:** When a `pymysql.err.OperationalError` message contains `"is not allowed to connect"`, catch it in `_connect()` and surface an actionable message directing the user to cPanel → MySQL Databases → Remote Database Access Hosts to authorise their outgoing IP.
- **OPENPO_H fan-out inflates sales:** The original query used `LEFT JOIN dbo.OPENPO_H ph ON o.[ORDER#] = ph.[H@REF#]` + `SUM(ENTENDED_PRICE_NO_FUNDS)`. If an ORDER# has multiple rows in OPENPO_H, the JOIN fans out and the price is summed multiple times. Fixed by removing the LEFT JOIN and using a correlated `EXISTS` subquery: `WHEN EXISTS (SELECT 1 FROM dbo.OPENPO_H ph WHERE ph.[H@REF#] = o.[ORDER#] AND ph.[H@WARE] = 'DIR') THEN 0`.
- **Invoice# = 0 and NULL exclusion:** Use `ISNULL(o.[INVOICE#], 0) <> 0` rather than `> 0` to correctly exclude both NULL and zero invoice records.
- **Cost center '1%' explicit exclusion:** `AND o.[{cc_field}] NOT LIKE '1%'` is always added to WHERE regardless of filter mode. In `orders_field` mode this pairs with `LIKE '0%'`; in `item_join` mode it guards the _ORDERS table directly since the item join only filters `dbo.ITEM.ICCTR`.
