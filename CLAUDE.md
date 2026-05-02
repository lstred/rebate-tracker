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
│   ├── email_sender.py            # SMTP email service: get_smtp_settings, smtp_configured, send_statement_email
│   └── backup.py                  # JSON backup/restore (local file)
├── ui/
│   ├── main_window.py             # App shell: sidebar + TopBar + QStackedWidget
│   ├── theme.py                   # Colour constants (C dict) + apply_theme() + _DARK/_LIGHT palettes
│   ├── admin_state.py             # Session-scoped admin mode state + require_admin() helper
│   ├── admin_login_dialog.py      # Admin login dialog + forgot-password email flow
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
| `accounts` | Tracked dealers; `is_active` flag (removed = False, not deleted); `email VARCHAR(255)` column added |
| `sales_cache` | Daily sales totals per account synced from SQL Server; columns: `total_sales`, `rebate_eligible_sales`, `dir_sales`, `sales_041` |
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

### App Settings for SMTP Email
| Setting key | Default | What it controls |
|---|---|---|
| `smtp_host` | `smtp.office365.com` | SMTP server hostname |
| `smtp_port` | `587` | SMTP port (STARTTLS) |
| `smtp_user` | `""` | Sender email address / login |
| `smtp_password` | `""` | Password or App Password (never hardcoded) |
| `smtp_from_name` | `""` | Display name in From header |
| `smtp_reply_to` | `""` | Reply-To address (optional; defaults to smtp_user if blank) |
| `theme` | `dark` | Active UI theme (`dark` or `light`) |
| `last_export_dir` | `""` | Last PDF export folder (shared between Generate and Email sections) |

---

## Rebate Calculation Logic
- **Period:** Always starts from `account.start_date` (anniversary-based), not Jan 1.
- **Prior year:** Same relative window shifted back exactly one year.
- **Rebate exclusions:** `COST_CENTER='041'` (unfinished wood) and direct-ship orders (`OPENPO_H.H@WARE='DIR'`, joined via `_ORDERS.[ORDER#] = OPENPO_H.[H@REF#]`) are excluded from `rebate_eligible_sales` by default but still count toward tier thresholds (tracked in `total_sales`). The sync query also stores `dir_sales` and `sales_041` as separate breakdown columns so per-structure eligibility overrides can add them back.
- **Per-structure eligibility overrides:** `RebateStructure` has `include_dir` and `include_041` boolean flags. When set, `calculate_account_rebate()` adds the corresponding breakdown column back into `current_eligible` / `prior_eligible` before calculating rebates. Managed via checkboxes in `StructureDialog`.
- **Customer-level customization:** An account can have its own private `RebateStructure` with `is_template=False`. The account detail panel shows **Customize** (create a copy), **Edit Custom Rebate** (edit existing copy), and **Reset to Template** (delete copy, revert to `derived_from_id` template). A **Custom** amber badge appears in the header when a custom structure is active.
- **Tier types (per tier, not per structure):**
  - `sales` — rate applied to total current sales
  - `growth` — rate applied to `max(0, current_sales - prior_year_sales)`
  - `freight` — produces a `FreightQualification` (no dollar amount, just tracks qualification)
- **Tier modes:**
  - `dollar_one` — rate applies to ALL sales from dollar one when threshold is crossed (overrides lower tiers)
  - `forward_only` — rate applies only to incremental sales above the threshold (stacks)
- `structure_type` field in DB is kept for backward compat but new structures are always `"tiered"` with `applies_to` per tier.

---

## Admin Mode
- **Files:** `ui/admin_state.py` (state) + `ui/admin_login_dialog.py` (dialog).
- **State:** Module-level `_admin_active: bool = False`. `is_admin()` / `set_admin(bool)` / `require_admin(parent)` are the public API.
- **Default password:** `123nrf`. Stored in `app_settings` under key `admin_password`. `get_admin_password()` reads from DB; falls back to `"123nrf"` if not set. Password is stored as plain text (internal desktop app).
- **Sidebar button:** Sits between the hline divider and the Settings nav button. Shows 🔒 Inquiry Mode (muted border, gray text) when not admin; 🔓 Admin Mode (amber tint, amber border) when admin. Click to log in or log out (logout requires confirmation).
- **Session persistence:** Admin mode stays active for the lifetime of the process — `set_admin(False)` is only called on explicit logout.
- **Gated write actions (Accounts view):**
  - Add Account, Remove Selected
  - Edit start date, Edit email (✉ button)
  - Assign Structure, Customize rebate, Edit Custom Rebate, Reset to Template
  - Add/Edit/Delete prior-year overrides
- **Gated write actions (Rebate Structures view):**
  - + New, Edit Selected, Delete Selected, Apply to Account / Program
- **Inquiry-mode UX:** Write action methods start with `from ui.admin_state import require_admin; if not require_admin(self): return`. `require_admin()` shows a polished QMessageBox if not admin.
- **Forgot Password flow:** In the login dialog, "Forgot Password" (styled as a link) opens a small prompt for the requester's email, then `_ForgotPasswordWorker(QThread)` sends the current password to `lukas_stred@nrfdist.com` via the configured SMTP. Success/failure shown inline. Requires SMTP to be configured in Settings → Email.

---

## Theme System
- **Palettes:** `_DARK` and `_LIGHT` dicts defined in `ui/theme.py`. Global mutable `C` dict starts as a copy of `_DARK`.
- **`apply_theme(theme_name)`:** Clears and updates `C` in-place, rebuilds `STYLESHEET` via `_build_stylesheet()`, returns the new QSS string. Caller does `QApplication.instance().setStyleSheet(apply_theme(...))`.
- **`apply_mpl_style()`:** Rebuilds matplotlib rcParams from **current C** values (NOT a cached dict). Call after `apply_theme()` to update charts.
- **Startup:** `main_window.py` reads `get_setting("theme", "dark")` and calls `_apply_theme()` if not dark.
- **`MainWindow._apply_theme()`:** Sets app QSS, reloads account gallery (`_load_accounts()`), rebuilds account detail if open, calls `view_dashboard.refresh_theme()` to re-draw chart.
- **`DashboardView.refresh_theme()`:** Calls `apply_mpl_style()` then `_update_ui()` (if data loaded) so chart redraws with correct colors.
- **`BarChartCanvas._apply_colors()`:** Sets fig/ax facecolor + tick/spine colors from current C. Called at init AND in `plot()` (axes.clear() resets colors, so they must be reapplied each draw).
- **Light palette:** bg=`#F0F4F8`, surface=`#FFFFFF`, accent=`#0969DA`, text=`#1F2328`, sidebar=`#FFFFFF`, sidebar_sel=`#EBF2FF`.
- **Dark palette:** bg=`#0D1117`, surface=`#161B22`, accent=`#388BFD`, text=`#E6EDF3`, sidebar=`#0D1117`.
- **Anti-pattern (AVOID):** Any code that f-strings `C` values at construction time with `setStyleSheet()` will NOT update on theme switch. Use `widget.setProperty("class", "classname")` and define the style in `_build_stylesheet()` QSS instead.
- **QSS class selectors used:** `topbar`, `sidebar-widget`, `title-frame`, `left-panel`, `kpi-card`, `badge`, `vline-sep`, `hline-sep`, `card`, `card-flat`, `heading`, `subheading`, `muted`, `kpi-value`, `kpi-label`, `tag-success`, `tag-warning`, `tag-danger`, `primary`, `danger`, `success`, `nav`, `icon-btn`.
- **TierProgressBar:** Uses `C["surface3"]`, `C["accent"]`, `C["success"]`, `C["text_dim"]`, `C["warning"]`, `C["text_muted"]` at paint time — safe for theme switching. Now accepts `prior_year=0.0` param (4th positional after projected); draws a gray dashed vertical line on the bar at the prior year sales position with an amber ◆ diamond marker. Projected year-end is shown only as a translucent blue fill — no separate marker. `build_legend(show_prior_year=True)` static method returns a compact QWidget legend strip (▓ Current Sales, ▒ Projected (Year-End), ◆ Prior Year, │green Tier Reached, │gray Tier Pending).
- **Rebate Structures assignments table:** 7 columns — Acct #, Account Name, Sales YTD, Projected, Prior Year, Rebate Est., Progress (mini TierProgressBar). Data loaded synchronously from SQLite in `_show_detail()`. Totals row at bottom sums active-account values. Inactive/CLSD accounts hidden by default with show-closed toggle; closed rows dimmed at 50% alpha. `_current_rebate_year_start()` helper duplicated at module level (same as accounts_view.py). `_show_detail()` stores `self._current_struct_id` for toggle re-render.

---

## Email (SMTP) Service
- **File:** `services/email_sender.py`
- **`get_smtp_settings() -> dict`:** Reads smtp_host/port/user/password/from_name from `app_settings`.
- **`smtp_configured() -> bool`:** True only if host + user + password are all set.
- **`send_statement_email(to_email, to_name, account_number, pdf_path, subject=None, body_html=None) -> tuple[bool, str]`:** STARTTLS on port 587, MIMEMultipart with HTML body + PDF attachment. Catches `SMTPAuthenticationError`, `SMTPException`, `OSError` separately.
- **MFA / App Passwords:** If the organisation uses MFA, user must generate an App Password in their Microsoft account security settings and enter it in Settings → Email.
- **Test connection:** Settings → Email → *Send Test Email* runs an async STARTTLS login check in a `QThread` and reports success or error without sending an actual email.

---

## PDF Templates — Email Statements section
- Located at the bottom of the **PDF Templates** view below the batch export section.
- Shows a `QTableWidget` with columns: Account #, Name, Email, PDF File, Preview, Send.
- **Preview** opens the PDF with the system default viewer (`QDesktopServices.openUrl`); disabled if the file doesn't exist yet.
- **Send** generates the PDF (calls `generate_statement`) then emails it — all in `EmailSendWorker(QThread)`. Row status updates on completion.
- Group filter combo lets you scope the list to one marketing program.
- PDF folder is shared with the Generate section; persisted in `last_export_dir` setting.
- If `smtp_configured()` is False, the Send button shows a warning dialog directing user to Settings.

---

## Cloud Backup
- **Service:** `services/cloud_backup.py` — MySQL via PyMySQL 2.2.8.
- **Table:** `rebate_tracker_snapshots` (columns: `table_name` PK, `snapshot_json` LONGTEXT, `updated_at` DATETIME).
- **Singleton:** `CloudBackupWorker(QThread)` — use `CloudBackupWorker._instance` to get the running instance. `schedule()` debounces 3 s then calls `push_backup()`.
- **Trigger:** `log_audit()` calls `_instance.schedule()` after every write, so backup is always live.
- **Credentials:** Never hardcoded. Non-sensitive defaults seeded in `app_settings`; password is empty by default — user enters in Settings → Cloud Backup.
- **Fields backed up:** Accounts now include `email`; RebateStructures now include `include_dir`, `include_041`, `derived_from_id`. Both `_collect_payload()` (cloud) and `_account_to_dict()`/`_structure_to_dict()` (JSON file) serialize all these fields.
- **Safety guard in `push_backup()`:** If the local DB has 0 accounts but the cloud snapshot already contains accounts, the push is aborted with a descriptive error message. Prevents a fresh-install empty push from silently overwriting good cloud data.
- **`preview_backup() -> tuple[bool, dict|str]`:** Fetches cloud backup summary (counts only, no restore) — used by Settings → Restore from Cloud to show users what the cloud contains before they confirm.
- **Restore flow:** Settings → Restore from Cloud first fetches a preview, shows the cloud account count + timestamp in a confirmation dialog, then starts `CloudRestoreWorker` only if user confirms. `restore_from_cloud()` pulls JSON snapshot then delegates to `import_backup()` via tempfile.
- **Auto-reload after restore:** `SettingsView` emits `restore_complete` signal on successful cloud or JSON restore. `MainWindow._on_data_restored()` handles it: reloads accounts gallery, rebate structures, dashboard, and settings fields. No app restart needed.
- **`SettingsView._refresh_fields()`:** Re-reads all settings from DB and updates UI fields after a restore. Does NOT refresh `mysql_password` (excluded from all backups).
- **`import_backup()` derived_from_id fix:** Two-pass restore — first pass creates all structures and records old→new ID mapping; second pass sets `derived_from_id` using the mapping so per-account custom structure links survive backup/restore cycles.
- **Excluded from backup:** `sales_cache`, `audit_log`, `mysql_password` setting.

---

## UI / Gallery Conventions
- **Gallery panel** (left, 320 px): Custom `AccountGalleryItem` widgets — account number, program BCCODE badge, days-to-renewal countdown, account name, start date, mini `TierProgressBar`.
- **Sort order:** Accounts sorted by days until next rebate-year anniversary (soonest renewals at top). Users can follow up before the new year starts.
- **Renewal countdown colours:** ≤ 30 d = red, ≤ 60 d = amber, else muted.
- **TierProgressBar:** Custom `QWidget` used in the detail panel (full, with labels + legend), the gallery (mini, 9 px), and the assignments table (mini per row). Merges tiers sharing the same threshold into one boundary marker. The projected year-end fill is shown as a translucent blue area only (no separate marker). Amber diamond ◆ marks the **prior year** sales position (also drawn as a gray dashed vertical line on the bar). Green tick = threshold crossed, gray tick = not yet reached. Freight-only tiers at the same threshold get a ✦ suffix. `build_legend()` static method returns a standalone legend QWidget placed below the bar.
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
- **\*CLSD\* accounts:** Source system marks closed accounts with `*CLSD*` at the start of BNAME. `sync_account_info()` automatically deactivates these. `init_db()` runs a one-time migration to deactivate any already-cached closed accounts on startup. `sync_marketing_program()` guards re-activation: if an account's `account_name` starts with `*CLSD*`, it is never re-activated even if it appears in the program roster again. Accounts view has a **Show closed accounts** checkbox that loads `is_active=False` accounts with `*CLSD*` prefix alongside active ones; closed cards are shown at 45% opacity and disabled.
- **OPENPO_H join:** Table is `dbo.OPENPO_H`. Join key is `_ORDERS.[ORDER#] = OPENPO_H.[H@REF#]`. Field `H@WARE` = `'DIR'` means direct ship (excluded from rebate-eligible sales).
- **BACCT# field:** BILL_TO account number column is `BACCT#` — must be quoted as `[BACCT#]` in T-SQL. Old default was wrong (`BACCT`); migration in `init_db()` fixes live DBs.
- **QThread.start() shadowing:** Never use `self.start = value` inside a QThread subclass — it overwrites the `start()` method. Use `self._start`.
- **Session detached objects:** Always capture data inside `with get_session()` before opening dialogs; open fresh sessions for writes.
- **Tier contribution display (PDF + UI):** `TierResult.tier_number` is the within-subset index (sales tiers counted separately from growth/freight tiers), so it cannot be used to match contributions back to the global sorted tier list. Always match by `(threshold, rate, applies_to)` with a small float tolerance. Dollar-one tiers superseded by a higher dollar-one tier will have `rebate_contribution == 0` and should show `—` rather than `$0.00`.
- **Projection uses current rebate year start:** `get_account_period()` returns the original `account.start_date` (e.g. 2024-07-03), which may be years in the past. For annualization, use `_current_rebate_year_start(start_date, today)` (defined in `accounts_view.py`) — the most recent anniversary ≤ today — so `elapsed_days` is always within the current 12-month window. Using `account.start_date` directly caused multi-year elapsed spans and wildly underestimated projections.
- **Table columns squeezed / internal scroll:** For `QTableWidget` panels in the detail view, set `ResizeToContents` on all data columns and `Stretch` only on the label column. Disable the vertical scrollbar and set `minimumHeight = header_height + row_height × row_count` so the outer `QScrollArea` handles all scrolling without a nested scroll.
- **MySQL "host not allowed" error:** When a `pymysql.err.OperationalError` message contains `"is not allowed to connect"`, catch it in `_connect()` and surface an actionable message directing the user to cPanel → MySQL Databases → Remote Database Access Hosts to authorise their outgoing IP.
- **OPENPO_H fan-out inflates sales / subquery-in-aggregate error:** `LEFT JOIN dbo.OPENPO_H` fans out when an ORDER# has multiple rows, inflating SUM. `EXISTS` inside `SUM(CASE WHEN ...)` is also rejected by SQL Server (error 130). Fix: use a CTE `WITH dir_orders AS (SELECT DISTINCT [H@REF#] FROM dbo.OPENPO_H WHERE [H@WARE] = 'DIR')` then `LEFT JOIN dir_orders d ON o.[ORDER#] = d.[H@REF#]` and `WHEN d.[H@REF#] IS NOT NULL THEN 0`. DISTINCT guarantees at most one row per ORDER# so no fan-out, and no subquery inside the aggregate.
- **Invoice# = 0 and NULL exclusion:** Use `ISNULL(o.[INVOICE#], 0) <> 0` rather than `> 0` to correctly exclude both NULL and zero invoice records.
- **Cost center '1%' explicit exclusion:** `AND o.[{cc_field}] NOT LIKE '1%'` is always added to WHERE regardless of filter mode. In `orders_field` mode this pairs with `LIKE '0%'`; in `item_join` mode it guards the _ORDERS table directly since the item join only filters `dbo.ITEM.ICCTR`.
- **dir_sales / sales_041 breakdown columns:** The sync CTE query adds two extra `SUM(CASE WHEN ...)` columns — `dir_sales` (rows where `d.[H@REF#] IS NOT NULL`) and `sales_041` (rows where cost center = '041'). Legacy cache rows have these as 0 until a re-sync. `get_period_sales_breakdown()` in `rebate_calculator.py` returns a dict `{total, eligible, dir_sales, sales_041}` and is called by `calculate_account_rebate()` to apply `include_dir`/`include_041` flags.
- **Customer-level rebate customization:** `RebateStructure.is_template=False` marks a structure as a per-account copy. `derived_from_id` stores the originating template's ID so **Reset to Template** can reassign. When editing, always check `not custom.is_template` before modifying to avoid accidentally editing templates. The `StructureDialog` proxy pattern (class `_Proxy`) is reused from `_edit_structure()` for the customize flow in `accounts_view.py`.
- **Account.email field:** `accounts` table has an `email VARCHAR(255)` column added via `ALTER TABLE` migration in `init_db()`. Shown in the account detail panel header with a `✉` edit button. Used by the Email Statements section in PDF Templates.
- **Account.account_name vs Account.name:** The ORM field is `account_name` (not `name`). Always use `a.account_name` when referencing the dealer name from the `Account` model.
- **Backup Now status:** Settings → Cloud Backup → *Backup Now* now runs a private `_BackupNowWorker(QThread)` that calls `push_backup()` directly and emits a `finished_now` signal, so the status label shows a real ✓/✗ result instead of "queued in background".
- **theme.py stray CSS outside f-string:** When editing `_build_stylesheet()`, ensure all QSS stays inside the triple-quoted f-string. Anything placed after the closing `"""` becomes bare Python and causes `SyntaxError: invalid character` on Unicode box-drawing chars in CSS comments.
- **`_active_config` in pdf_template_view:** `_on_template_selected` stores the parsed template JSON as `self._active_config`. `_current_config()` reads this first, then falls back to the default template. `template_json` is the correct column name (not `config_json`).
- **`_choose_export_dir` syncs both labels:** When the user picks an export folder it sets both `self._export_dir`, `self._last_export_dir`, `self.lbl_export_dir`, and `self.lbl_email_dir`, and persists to `last_export_dir` setting.
- **Cloud backup "0 accounts loaded" after restore:** Root cause: `push_backup()` had no safety guard — on a fresh machine, `log_audit()` triggered a cloud push before any data existed, silently overwriting the good cloud backup with an empty snapshot. Fixed with a guard that checks cloud account count before pushing and aborts if local has 0 accounts but cloud has >0. Also fixed: `_collect_payload()`, `_account_to_dict()`, `_structure_to_dict()` were missing `email`, `include_dir`, `include_041`, `derived_from_id` fields (silently dropped every push → lost on restore). And `import_backup()` was missing a second pass to re-link `derived_from_id` after all structure IDs are reassigned. All four issues fixed simultaneously.
- **Restore no longer requires restart:** After cloud or JSON file restore, `SettingsView` emits `restore_complete` → `MainWindow._on_data_restored()` reloads accounts/structures/dashboard/settings in-place, then automatically triggers a SQL Server data refresh (`_on_sync_requested()`) so the sales cache is repopulated for the restored accounts.
- **Default date range:** When no saved date range exists (fresh install), the top-bar defaults to today − 24.5 months → today. This is computed as `today - timedelta(days=int(24.5 * 30.4375))` so the window spans roughly two full rebate years. Previously defaulted to Jan 1 of the current year.
