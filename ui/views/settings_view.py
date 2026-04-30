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
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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


# ---------------------------------------------------------------------------
# Settings view
# ---------------------------------------------------------------------------

class SettingsView(QWidget):
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

        # Heading
        heading = QLabel("Settings")
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        root.addWidget(heading)

        # ── SQL Server Connection ─────────────────────────────────────
        conn_group = QGroupBox("SQL Server Connection")
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
        field_form = QFormLayout(field_group)
        field_form.setSpacing(10)

        self.bill_to_field = QLineEdit(get_setting("bill_to_account_field", "BACCT"))
        self.bill_to_field.setPlaceholderText("e.g. BACCT")
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

        root.addStretch()

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
        set_setting("bill_to_account_field", self.bill_to_field.text().strip() or "BACCT")
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
            QMessageBox.information(
                self, "Restore Complete",
                msg + "\n\nPlease restart the application to reload all views."
            )
        else:
            QMessageBox.critical(self, "Restore Failed", msg)
