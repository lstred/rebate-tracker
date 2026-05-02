"""
ui/views/settings_view.py
--------------------------
Application settings: SQL Server connection, backup/restore, and data
management utilities.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db.local_db import SalesCache, get_session, get_setting, set_setting
from services.backup import export_backup, import_backup
from ui.theme import C


# ---------------------------------------------------------------------------
# Connection test worker
# ---------------------------------------------------------------------------

class ConnectionTestWorker(QThread):
    result = pyqtSignal(bool, str)

    def run(self):
        try:
            from db.connection import test_connection
            import io
            import sys
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            ok = test_connection()
            sys.stdout = old_stdout
            msg = buf.getvalue().strip()
            self.result.emit(ok, msg)
        except Exception as exc:
            self.result.emit(False, str(exc))


class CloudTestWorker(QThread):
    result = pyqtSignal(bool, str)

    def run(self):
        try:
            from services.cloud_backup import test_connection as cloud_test
            ok, msg = cloud_test()
            self.result.emit(ok, msg)
        except Exception as exc:
            self.result.emit(False, str(exc))


class CloudRestoreWorker(QThread):
    finished = pyqtSignal(bool, str)

    def run(self):
        try:
            from services.cloud_backup import restore_from_cloud
            ok, msg = restore_from_cloud()
            self.finished.emit(ok, msg)
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Settings view
# ---------------------------------------------------------------------------

class SettingsView(QWidget):
    theme_changed = pyqtSignal(str)    # "dark" | "light"
    restore_complete = pyqtSignal()    # emitted after a successful cloud or file restore

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(20)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # Track which widget groups are gated to admin mode only
        self._admin_only_groups: list = []    # whole QGroupBoxes to disable
        self._admin_cloud_widgets: list = []  # individual widgets inside cloud section

        # Heading
        heading = QLabel("Settings")
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        root.addWidget(heading)

        # ── Appearance ─────────────────────────────────────────────────
        appear_group = QGroupBox("Appearance")
        appear_layout = QVBoxLayout(appear_group)
        appear_layout.setSpacing(10)

        appear_lbl = QLabel("Choose the application colour theme.  Takes effect immediately.")
        appear_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        appear_lbl.setWordWrap(True)
        appear_layout.addWidget(appear_lbl)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(16)
        self._theme_group = QButtonGroup(self)
        current_theme = get_setting("theme", "dark")

        self._radio_dark  = QRadioButton("🌙  Dark")
        self._radio_light = QRadioButton("☀  Light")
        self._radio_dark.setChecked(current_theme != "light")
        self._radio_light.setChecked(current_theme == "light")
        self._theme_group.addButton(self._radio_dark,  0)
        self._theme_group.addButton(self._radio_light, 1)
        self._theme_group.idToggled.connect(self._on_theme_toggled)

        theme_row.addWidget(self._radio_dark)
        theme_row.addWidget(self._radio_light)
        theme_row.addStretch()
        appear_layout.addLayout(theme_row)

        root.addWidget(appear_group)

        # ── Email (SMTP) ────────────────────────────────────────────────
        email_group = QGroupBox("Email — Microsoft Outlook / Office 365")
        self._admin_only_groups.append(email_group)
        email_layout = QVBoxLayout(email_group)
        email_layout.setSpacing(10)

        email_help = QLabel(
            "Configure your Outlook credentials to send rebate statement PDFs directly "
            "from the PDF Templates tab.  Uses STARTTLS on port 587 by default.\n"
            "If your organisation uses multi-factor authentication, generate an "
            "App Password in your Microsoft account security settings."
        )
        email_help.setWordWrap(True)
        email_help.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        email_layout.addWidget(email_help)

        email_form = QFormLayout()
        email_form.setSpacing(8)

        self.smtp_host = QLineEdit(get_setting("smtp_host", "smtp.office365.com"))
        self.smtp_host.setPlaceholderText("smtp.office365.com")
        email_form.addRow("SMTP Host:", self.smtp_host)

        self.smtp_port = QLineEdit(get_setting("smtp_port", "587"))
        self.smtp_port.setPlaceholderText("587")
        self.smtp_port.setFixedWidth(80)
        email_form.addRow("Port:", self.smtp_port)

        self.smtp_user = QLineEdit(get_setting("smtp_user", ""))
        self.smtp_user.setPlaceholderText("your.name@company.com")
        email_form.addRow("Email Address:", self.smtp_user)

        self.smtp_password = QLineEdit(get_setting("smtp_password", ""))
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.smtp_password.setPlaceholderText("••••••••")
        email_form.addRow("Password / App Password:", self.smtp_password)

        self.smtp_from_name = QLineEdit(get_setting("smtp_from_name", ""))
        self.smtp_from_name.setPlaceholderText("e.g. NRF Flooring")
        email_form.addRow("Display Name:", self.smtp_from_name)

        self.smtp_reply_to = QLineEdit(get_setting("smtp_reply_to", ""))
        self.smtp_reply_to.setPlaceholderText("reply@company.com  (optional — defaults to Email Address)")
        email_form.addRow("Reply-To:", self.smtp_reply_to)

        email_layout.addLayout(email_form)

        email_btn_row = QHBoxLayout()
        btn_save_email = QPushButton("Save Email Settings")
        btn_save_email.setProperty("class", "primary")
        btn_save_email.clicked.connect(self._save_email_settings)
        email_btn_row.addWidget(btn_save_email)

        btn_test_email = QPushButton("Send Test Email")
        btn_test_email.clicked.connect(self._test_email)
        email_btn_row.addWidget(btn_test_email)
        email_btn_row.addStretch()
        email_layout.addLayout(email_btn_row)

        self.email_status = QLabel("")
        self.email_status.setWordWrap(True)
        email_layout.addWidget(self.email_status)

        root.addWidget(email_group)

        # ── SQL Server Connection ─────────────────────────────────────
        conn_group = QGroupBox("SQL Server Connection")
        self._admin_only_groups.append(conn_group)
        conn_layout = QVBoxLayout(conn_group)
        conn_layout.setSpacing(10)

        conn_help = QLabel(
            "Connection string is read from (in priority order):\n"
            "  1. Environment variable  SQLSERVER_ODBC\n"
            "  2. config.py  → SQLSERVER_ODBC   (gitignored, never committed)\n"
            "  3. Built-in default: NRFVMSSQL04 / NRF_REPORTS / Windows auth"
        )
        conn_help.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        conn_help.setWordWrap(True)
        conn_layout.addWidget(conn_help)

        self.conn_status = QLabel("Not tested.")
        self.conn_status.setStyleSheet(f"color: {C['text_muted']};")
        conn_layout.addWidget(self.conn_status)

        btn_test = QPushButton("Test SQL Server Connection")
        btn_test.setProperty("class", "primary")
        btn_test.clicked.connect(self._test_connection)
        conn_layout.addWidget(btn_test)

        root.addWidget(conn_group)

        # ── Field name configuration ──────────────────────────────────
        field_group = QGroupBox("Field Name Configuration")
        self._admin_only_groups.append(field_group)
        field_form = QFormLayout(field_group)
        field_form.setSpacing(10)

        self.bill_to_field = QLineEdit(get_setting("bill_to_account_field", "BACCT#"))
        self.bill_to_field.setPlaceholderText("e.g. BACCT#")
        field_form.addRow("BILL_TO account field:", self.bill_to_field)

        help_bill_to = QLabel("The column in dbo.BILL_TO that matches ACCOUNT#I in _ORDERS.")
        help_bill_to.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        field_form.addRow("", help_bill_to)

        btn_save_fields = QPushButton("Save Field Settings")
        btn_save_fields.setProperty("class", "primary")
        btn_save_fields.clicked.connect(self._save_field_settings)
        field_form.addRow("", btn_save_fields)

        root.addWidget(field_group)

        # ── Data management ───────────────────────────────────────────
        data_group = QGroupBox("Data Management")
        self._admin_only_groups.append(data_group)
        data_layout = QVBoxLayout(data_group)
        data_layout.setSpacing(10)

        # Cache info
        with get_session() as session:
            cache_count = session.query(SalesCache).count()
        self.lbl_cache = QLabel(f"Sales cache: {cache_count:,} daily rows stored locally.")
        self.lbl_cache.setStyleSheet(f"color: {C['text_muted']};")
        data_layout.addWidget(self.lbl_cache)

        btn_clear = QPushButton("Clear Sales Cache")
        btn_clear.setProperty("class", "danger")
        btn_clear.setToolTip("Clears cached SQL Server data. Run a refresh to repopulate.")
        btn_clear.clicked.connect(self._clear_cache)
        data_layout.addWidget(btn_clear)

        root.addWidget(data_group)

        # ── Backup & Restore ──────────────────────────────────────────
        backup_group = QGroupBox("Backup & Restore")
        self._admin_only_groups.append(backup_group)
        backup_layout = QVBoxLayout(backup_group)
        backup_layout.setSpacing(10)

        backup_help = QLabel(
            "Export all user-configured data (accounts, programs, rebate structures, "
            "templates, overrides) to a JSON file.  Import to restore after a crash "
            "or when moving to a new machine.  The sales cache is NOT included — "
            "run a data refresh after restoring."
        )
        backup_help.setWordWrap(True)
        backup_help.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        backup_layout.addWidget(backup_help)

        btn_row = QHBoxLayout()
        btn_export_bk = QPushButton("⬇  Export Backup")
        btn_export_bk.setProperty("class", "primary")
        btn_export_bk.clicked.connect(self._export_backup)
        btn_row.addWidget(btn_export_bk)

        btn_import_bk = QPushButton("⬆  Import / Restore Backup")
        btn_import_bk.setProperty("class", "success")
        btn_import_bk.clicked.connect(self._import_backup)
        btn_row.addWidget(btn_import_bk)
        btn_row.addStretch()
        backup_layout.addLayout(btn_row)

        self.backup_status = QLabel("")
        self.backup_status.setWordWrap(True)
        backup_layout.addWidget(self.backup_status)

        root.addWidget(backup_group)

        # ── Cloud Backup (MySQL) ───────────────────────────────────────
        cloud_group = QGroupBox("Cloud Backup — MySQL")
        cloud_layout = QVBoxLayout(cloud_group)
        cloud_layout.setSpacing(10)

        cloud_help = QLabel(
            "Keeps a live copy of all your user-configured data in a remote MySQL "
            "database.  A backup is pushed automatically within a few seconds of "
            "every change.  Sales cache and audit trail are NOT stored in the cloud."
        )
        cloud_help.setWordWrap(True)
        cloud_help.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        cloud_layout.addWidget(cloud_help)

        cloud_form = QFormLayout()
        cloud_form.setSpacing(8)

        self.mysql_host = QLineEdit(get_setting("mysql_host", ""))
        self.mysql_host.setPlaceholderText("e.g. tfnflooring.com")
        cloud_form.addRow("Host:", self.mysql_host)

        self.mysql_port = QLineEdit(get_setting("mysql_port", "3306"))
        self.mysql_port.setPlaceholderText("3306")
        self.mysql_port.setFixedWidth(80)
        cloud_form.addRow("Port:", self.mysql_port)

        self.mysql_database = QLineEdit(get_setting("mysql_database", ""))
        self.mysql_database.setPlaceholderText("database name")
        cloud_form.addRow("Database:", self.mysql_database)

        self.mysql_user = QLineEdit(get_setting("mysql_user", ""))
        self.mysql_user.setPlaceholderText("username")
        cloud_form.addRow("Username:", self.mysql_user)

        self.mysql_password = QLineEdit(get_setting("mysql_password", ""))
        self.mysql_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.mysql_password.setPlaceholderText("••••••••")
        cloud_form.addRow("Password:", self.mysql_password)

        cloud_layout.addLayout(cloud_form)

        cloud_btn_row = QHBoxLayout()
        btn_save_cloud = QPushButton("Save Credentials")
        btn_save_cloud.setProperty("class", "primary")
        btn_save_cloud.clicked.connect(self._save_cloud_settings)
        cloud_btn_row.addWidget(btn_save_cloud)
        self._admin_cloud_widgets.append(btn_save_cloud)

        btn_test_cloud = QPushButton("Test Connection")
        btn_test_cloud.clicked.connect(self._test_cloud_connection)
        cloud_btn_row.addWidget(btn_test_cloud)
        self._admin_cloud_widgets.append(btn_test_cloud)

        btn_push_now = QPushButton("⬆  Backup Now")
        btn_push_now.clicked.connect(self._push_cloud_backup)
        cloud_btn_row.addWidget(btn_push_now)
        self._admin_cloud_widgets.append(btn_push_now)

        btn_restore_cloud = QPushButton("⬇  Restore from Cloud")
        btn_restore_cloud.setProperty("class", "danger")
        btn_restore_cloud.clicked.connect(self._restore_from_cloud)
        cloud_btn_row.addWidget(btn_restore_cloud)

        cloud_btn_row.addStretch()
        cloud_layout.addLayout(cloud_btn_row)

        self.cloud_status = QLabel("")
        self.cloud_status.setWordWrap(True)
        cloud_layout.addWidget(self.cloud_status)

        root.addWidget(cloud_group)

        # ── About ─────────────────────────────────────────────────────
        about_group = QGroupBox("About")
        about_layout = QVBoxLayout(about_group)
        about_lbl = QLabel(
            "<b>Rebate Tracker</b><br>"
            "Sales &amp; Rebate Management Application<br>"
            "<br>"
            "Database: SQL Server — NRF_REPORTS (NRFVMSSQL04)<br>"
            "Local cache: SQLite (via SQLAlchemy)<br>"
            "UI: PyQt6 · Charts: Matplotlib · PDF: ReportLab"
        )
        about_lbl.setTextFormat(Qt.TextFormat.RichText)
        about_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        about_lbl.setWordWrap(True)
        about_layout.addWidget(about_lbl)
        root.addWidget(about_group)

        # MySQL credential fields are already instance attrs — add to cloud list
        self._admin_cloud_widgets.extend([
            self.mysql_host, self.mysql_port, self.mysql_database,
            self.mysql_user, self.mysql_password,
        ])

        # Apply initial admin state (inquiry mode on first open)
        self.refresh_admin_state()

        root.addStretch()

    # ------------------------------------------------------------------

    def refresh_admin_state(self) -> None:
        """Enable or disable settings controls based on current admin mode."""
        from ui.admin_state import is_admin
        admin = is_admin()
        for group in getattr(self, "_admin_only_groups", []):
            group.setEnabled(admin)
        for widget in getattr(self, "_admin_cloud_widgets", []):
            widget.setEnabled(admin)

    # ------------------------------------------------------------------

    def _test_connection(self):
        self.conn_status.setText("Testing…")
        self.conn_status.setStyleSheet(f"color: {C['text_muted']};")
        self._conn_worker = ConnectionTestWorker(self)
        self._conn_worker.result.connect(self._on_conn_result)
        self._conn_worker.start()

    def _on_conn_result(self, ok: bool, msg: str):
        if ok:
            self.conn_status.setText(f"✓  Connected — {msg}")
            self.conn_status.setStyleSheet(f"color: {C['success']};")
        else:
            self.conn_status.setText(f"✗  Failed — {msg}")
            self.conn_status.setStyleSheet(f"color: {C['danger']};")

    def _save_field_settings(self):
        set_setting("bill_to_account_field", self.bill_to_field.text().strip() or "BACCT#")
        QMessageBox.information(self, "Saved", "Field settings saved.")

    def _clear_cache(self):
        if (
            QMessageBox.question(
                self,
                "Clear Cache",
                "This will delete all locally cached sales data.\n"
                "Run a data refresh after to repopulate.\n\nContinue?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            with get_session() as session:
                session.query(SalesCache).delete()
            self.lbl_cache.setText("Sales cache cleared.  Run a refresh to repopulate.")
            QMessageBox.information(self, "Done", "Sales cache cleared.")

    def _export_backup(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Backup", "rebate_backup.json", "JSON Files (*.json)"
        )
        if not file_path:
            return
        ok, msg = export_backup(file_path)
        color = C["success"] if ok else C["danger"]
        self.backup_status.setText(msg)
        self.backup_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        if ok:
            QMessageBox.information(self, "Backup Complete", msg)
        else:
            QMessageBox.critical(self, "Backup Failed", msg)

    def _import_backup(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Backup", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        confirm = QMessageBox.warning(
            self,
            "Restore Backup",
            "This will REPLACE all existing accounts, structures, overrides, "
            "and settings with the backup data.\n\nAre you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        ok, msg = import_backup(file_path)
        color = C["success"] if ok else C["danger"]
        self.backup_status.setText(msg)
        self.backup_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        if ok:
            self.restore_complete.emit()
        else:
            QMessageBox.critical(self, "Restore Failed", msg)

    # ------------------------------------------------------------------
    # Cloud backup handlers
    # ------------------------------------------------------------------

    def _save_cloud_settings(self):
        set_setting("mysql_host", self.mysql_host.text().strip())
        set_setting("mysql_port", self.mysql_port.text().strip() or "3306")
        set_setting("mysql_database", self.mysql_database.text().strip())
        set_setting("mysql_user", self.mysql_user.text().strip())
        set_setting("mysql_password", self.mysql_password.text())
        self.cloud_status.setText("Credentials saved.")
        self.cloud_status.setStyleSheet(f"color: {C['success']}; font-size: 11px;")

    def _test_cloud_connection(self):
        self._save_cloud_settings()
        self.cloud_status.setText("Testing connection…")
        self.cloud_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        self._cloud_test_worker = CloudTestWorker(self)
        self._cloud_test_worker.result.connect(self._on_cloud_test_result)
        self._cloud_test_worker.start()

    def _on_cloud_test_result(self, ok: bool, msg: str):
        color = C["success"] if ok else C["danger"]
        prefix = "✓  " if ok else "✗  "
        self.cloud_status.setText(prefix + msg)
        self.cloud_status.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _push_cloud_backup(self):
        self._save_cloud_settings()
        self.cloud_status.setText("Pushing backup to cloud…")
        self.cloud_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")

        class _BackupNowWorker(QThread):
            finished_now = pyqtSignal(bool, str)
            def run(self):
                try:
                    from services.cloud_backup import push_backup
                    ok, msg = push_backup()
                    self.finished_now.emit(ok, msg)
                except Exception as exc:
                    self.finished_now.emit(False, str(exc))

        self._backup_now_worker = _BackupNowWorker(self)
        self._backup_now_worker.finished_now.connect(self._on_backup_now_finished)
        self._backup_now_worker.start()

    def _on_backup_now_finished(self, ok: bool, msg: str):
        color = C["success"] if ok else C["danger"]
        prefix = "✓  " if ok else "✗  "
        self.cloud_status.setText(prefix + msg)
        self.cloud_status.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _restore_from_cloud(self):
        """
        Fetch a preview of the cloud backup first, show the user what it contains,
        then proceed with the restore only after explicit confirmation.
        """
        self.cloud_status.setText("Checking cloud backup…")
        self.cloud_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")

        class _PreviewWorker(QThread):
            ready = pyqtSignal(bool, object)

            def run(self):
                try:
                    from services.cloud_backup import preview_backup
                    ok, result = preview_backup()
                    self.ready.emit(ok, result)
                except Exception as exc:
                    self.ready.emit(False, str(exc))

        def _on_preview(ok, result):
            self.cloud_status.setText("")

            if not ok:
                err = str(result)
                self.cloud_status.setText(f"✗  {err}")
                self.cloud_status.setStyleSheet(f"color: {C['danger']}; font-size: 11px;")
                QMessageBox.critical(self, "Cloud Backup Unavailable", err)
                return

            summary = result
            last_updated = summary.get("last_updated", "unknown")
            n_accounts   = summary.get("accounts", 0)
            n_programs   = summary.get("marketing_programs", 0)
            n_structures = summary.get("rebate_structures", 0)
            n_assigns    = summary.get("account_assignments", 0)
            n_overrides  = summary.get("sales_overrides", 0)

            acct_line = f"  • {n_accounts} account(s)"
            if n_accounts == 0:
                acct_line += "  ⚠  None — restoring will leave you with no accounts!"

            info = (
                f"Last backed up:  {last_updated}\n\n"
                f"Cloud backup contains:\n"
                f"{acct_line}\n"
                f"  • {n_programs} marketing program(s)\n"
                f"  • {n_structures} rebate structure(s)\n"
                f"  • {n_assigns} structure assignment(s)\n"
                f"  • {n_overrides} sales override(s)\n\n"
                f"Restoring will permanently replace ALL local accounts, structures,\n"
                f"overrides, templates, and settings with this data.\n\n"
                f"Your locally cached SQL Server sales data will NOT be affected.\n"
                f"The audit trail will NOT be affected."
            )

            icon = (
                QMessageBox.Icon.Warning
                if n_accounts == 0
                else QMessageBox.Icon.Question
            )
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Restore from Cloud Backup")
            msg_box.setIcon(icon)
            msg_box.setText("<b>Restore from Cloud Backup</b>")
            msg_box.setInformativeText(info)
            msg_box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            )
            msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            msg_box.button(QMessageBox.StandardButton.Yes).setText("Yes, Restore from Cloud")
            if msg_box.exec() != QMessageBox.StandardButton.Yes:
                return

            self.cloud_status.setText("Restoring from cloud…")
            self.cloud_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
            self._restore_worker = CloudRestoreWorker(self)
            self._restore_worker.finished.connect(self._on_cloud_restore_finished)
            self._restore_worker.start()

        self._preview_worker = _PreviewWorker(self)
        self._preview_worker.ready.connect(_on_preview)
        self._preview_worker.start()

    def _on_cloud_restore_finished(self, ok: bool, msg: str):
        color = C["success"] if ok else C["danger"]
        prefix = "✓  " if ok else "✗  "
        self.cloud_status.setText(prefix + msg)
        self.cloud_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        if ok:
            self.restore_complete.emit()
        else:
            QMessageBox.critical(self, "Restore Failed", msg)

    # ------------------------------------------------------------------
    # Theme toggle
    # ------------------------------------------------------------------

    def _refresh_fields(self) -> None:
        """
        Re-read all setting values from the database and update the UI fields.
        Called after a cloud or file restore so fields reflect restored values
        without requiring an app restart.
        Note: mysql_password is excluded from cloud backups and is never overwritten.
        """
        self.smtp_host.setText(get_setting("smtp_host", "smtp.office365.com"))
        self.smtp_port.setText(get_setting("smtp_port", "587"))
        self.smtp_user.setText(get_setting("smtp_user", ""))
        self.smtp_password.setText(get_setting("smtp_password", ""))
        self.smtp_from_name.setText(get_setting("smtp_from_name", ""))
        self.smtp_reply_to.setText(get_setting("smtp_reply_to", ""))
        self.mysql_host.setText(get_setting("mysql_host", ""))
        self.mysql_port.setText(get_setting("mysql_port", "3306"))
        self.mysql_database.setText(get_setting("mysql_database", ""))
        self.mysql_user.setText(get_setting("mysql_user", ""))
        # mysql_password intentionally skipped — not included in any backup
        self.bill_to_field.setText(get_setting("bill_to_account_field", "BACCT#"))
        theme = get_setting("theme", "dark")
        self._radio_dark.setChecked(theme != "light")
        self._radio_light.setChecked(theme == "light")

    def _on_theme_toggled(self, btn_id: int, checked: bool):
        if not checked:
            return
        theme = "light" if btn_id == 1 else "dark"
        from db.local_db import set_setting
        set_setting("theme", theme)
        self.theme_changed.emit(theme)

    # ------------------------------------------------------------------
    # Email settings handlers
    # ------------------------------------------------------------------

    def _save_email_settings(self):
        from db.local_db import set_setting
        set_setting("smtp_host",      self.smtp_host.text().strip() or "smtp.office365.com")
        set_setting("smtp_port",      self.smtp_port.text().strip() or "587")
        set_setting("smtp_user",      self.smtp_user.text().strip())
        set_setting("smtp_password",  self.smtp_password.text())
        set_setting("smtp_from_name", self.smtp_from_name.text().strip())
        set_setting("smtp_reply_to",   self.smtp_reply_to.text().strip())
        self.email_status.setText("✓  Email settings saved.")
        self.email_status.setStyleSheet(f"color: {C['success']}; font-size: 11px;")

    def _test_email(self):
        self._save_email_settings()
        recipient = self.smtp_user.text().strip()
        if not recipient:
            QMessageBox.warning(self, "No Address", "Enter your email address first.")
            return
        self.email_status.setText("Sending test email…")
        self.email_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")

        class _TestWorker(QThread):
            result = pyqtSignal(bool, str)
            def __init__(self, to_addr, parent=None):
                super().__init__(parent)
                self._to = to_addr
            def run(self):
                from services.email_sender import get_smtp_settings
                import smtplib, ssl
                cfg = get_smtp_settings()
                try:
                    ctx = ssl.create_default_context()
                    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as srv:
                        srv.ehlo()
                        srv.starttls(context=ctx)
                        srv.login(cfg["user"], cfg["password"])
                    self.result.emit(True, f"Connected to {cfg['host']} successfully. Credentials are valid.")
                except smtplib.SMTPAuthenticationError:
                    self.result.emit(False, "Authentication failed — check username and password / App Password.")
                except Exception as exc:
                    self.result.emit(False, str(exc))

        self._email_test_worker = _TestWorker(recipient, self)
        self._email_test_worker.result.connect(self._on_email_test_result)
        self._email_test_worker.start()

    def _on_email_test_result(self, ok: bool, msg: str):
        color = C["success"] if ok else C["danger"]
        prefix = "✓  " if ok else "✗  "
        self.email_status.setText(prefix + msg)
        self.email_status.setStyleSheet(f"color: {color}; font-size: 11px;")
