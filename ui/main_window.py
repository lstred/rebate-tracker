"""
ui/main_window.py
-----------------
Main application shell: sidebar navigation + stacked content area.

Layout
------
┌─ sidebar (fixed 220 px) ─┬─── content (fills remaining width) ──────────┐
│  [APP TITLE]             │  [Top Bar: date range | refresh | status]    │
│                          │──────────────────────────────────────────────│
│  ○ Dashboard             │                                              │
│  ○ Accounts              │         QStackedWidget (views swap here)     │
│  ○ Rebate Structures     │                                              │
│  ○ PDF Templates         │                                              │
│  ─────────────────────   │                                              │
│  ○ Settings              │                                              │
└──────────────────────────┴──────────────────────────────────────────────┘

The global date range (start / end QDateEdit) lives in the top bar and is
accessible to all views via the signal `date_range_changed`.
"""

from __future__ import annotations

from datetime import date, timedelta

from PyQt6.QtCore import QDate, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from db.local_db import get_setting, set_setting
from db.sync import SyncWorker
from ui.theme import C, apply_theme


# ---------------------------------------------------------------------------
# Navigation button
# ---------------------------------------------------------------------------

class NavButton(QPushButton):
    """Sidebar navigation item with an icon character and a text label."""

    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(f"  {icon}  {label}", parent)
        self.setProperty("class", "nav")
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)
        font = QFont("Segoe UI", 12)
        self.setFont(font)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


# ---------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------

class TopBar(QWidget):
    """Date range selector + sync button + status label."""

    date_range_changed = pyqtSignal(date, date)
    sync_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setProperty("class", "topbar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)

        # Date range labels + pickers
        lbl_from = QLabel("From")
        lbl_from.setProperty("class", "muted")
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDisplayFormat("MM/dd/yyyy")
        self.date_start.setFixedWidth(120)

        lbl_to = QLabel("To")
        lbl_to.setProperty("class", "muted")
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDisplayFormat("MM/dd/yyyy")
        self.date_end.setFixedWidth(120)

        # Initialise from saved settings
        saved_start = get_setting("date_range_start")
        saved_end = get_setting("date_range_end")

        default_end = date.today()
        # Default start: 24.5 months before today (≈ 735 days) so the window
        # captures roughly two full rebate years for new installs.
        default_start = default_end - timedelta(days=int(24.5 * 30.4375))

        start = _parse_setting_date(saved_start) or default_start
        end = _parse_setting_date(saved_end) or default_end

        self.date_start.setDate(QDate(start.year, start.month, start.day))
        self.date_end.setDate(QDate(end.year, end.month, end.day))

        # Apply button
        self.btn_apply = QPushButton("Apply Range")
        self.btn_apply.setProperty("class", "primary")
        self.btn_apply.setFixedWidth(110)
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setProperty("class", "vline-sep")

        # Sync button
        self.btn_sync = QPushButton("⟳  Refresh Data")
        self.btn_sync.setProperty("class", "primary")
        self.btn_sync.setFixedWidth(140)
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)

        # Status text
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setProperty("class", "muted")
        self.lbl_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        for w in [lbl_from, self.date_start, lbl_to, self.date_end, self.btn_apply,
                  sep, self.btn_sync, self.progress_bar, self.lbl_status]:
            layout.addWidget(w)

        # Wire up
        self.btn_apply.clicked.connect(self._emit_range)
        self.btn_sync.clicked.connect(self.sync_requested.emit)

    def _emit_range(self) -> None:
        start = self.date_start.date().toPyDate()
        end = self.date_end.date().toPyDate()
        set_setting("date_range_start", start.isoformat())
        set_setting("date_range_end", end.isoformat())
        self.date_range_changed.emit(start, end)

    def get_date_range(self) -> tuple[date, date]:
        return (
            self.date_start.date().toPyDate(),
            self.date_end.date().toPyDate(),
        )

    def set_syncing(self, syncing: bool) -> None:
        self.btn_sync.setEnabled(not syncing)
        self.progress_bar.setVisible(syncing)
        if syncing:
            self.progress_bar.setValue(0)

    def update_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def set_status(self, msg: str, color: str = "") -> None:
        if color:
            self.lbl_status.setStyleSheet(f"color: {color}; font-size:11px;")
        else:
            self.lbl_status.setStyleSheet("")  # restore class-based QSS
        self.lbl_status.setText(msg)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

class Sidebar(QWidget):
    nav_changed = pyqtSignal(int)  # index of selected view

    _NAV_ITEMS = [
        ("⬛", "Dashboard"),
        ("👤", "Accounts"),
        ("💰", "Rebate Structures"),
        ("📄", "PDF Templates"),
        ("📋", "Audit Log"),
    ]
    _BOTTOM_ITEMS = [
        ("⚙", "Settings"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setProperty("class", "sidebar-widget")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # App title
        title_frame = QFrame()
        title_frame.setFixedHeight(64)
        title_frame.setProperty("class", "title-frame")
        title_layout = QVBoxLayout(title_frame)
        title_layout.setContentsMargins(20, 12, 20, 12)
        title_label = QLabel("Rebate Tracker")
        title_label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        subtitle = QLabel("Sales & Rebate Management")
        subtitle.setProperty("class", "muted")
        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle)
        layout.addWidget(title_frame)

        layout.addSpacing(8)

        # Main nav buttons
        self._nav_buttons: list[NavButton] = []
        for icon, label in self._NAV_ITEMS:
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda checked, i=len(self._nav_buttons): self._select(i))
            layout.addWidget(btn)
            self._nav_buttons.append(btn)

        layout.addStretch()

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setProperty("class", "hline-sep")
        layout.addWidget(div)
        layout.addSpacing(6)

        # Admin mode button — shows inquiry / admin status; click to toggle
        self._btn_admin = QPushButton()
        self._btn_admin.setFixedHeight(38)
        self._btn_admin.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_admin.clicked.connect(self._on_admin_clicked)
        layout.addWidget(self._btn_admin)
        self._update_admin_button_style(False)  # start in inquiry mode

        layout.addSpacing(4)

        # Bottom nav buttons (Settings — index 5 in _nav_buttons)
        for icon, label in self._BOTTOM_ITEMS:
            btn = NavButton(icon, label)
            btn.clicked.connect(
                lambda checked, i=len(self._nav_buttons): self._select(i)
            )
            layout.addWidget(btn)
            self._nav_buttons.append(btn)

        layout.addSpacing(12)

        # Select first item
        self._select(0)

    def _on_admin_clicked(self) -> None:
        from ui import admin_state
        if admin_state.is_admin():
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Admin Mode Active",
                "Deactivate admin mode and return to inquiry-only mode?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                admin_state.set_admin(False)
                self._update_admin_button_style(False)
        else:
            from ui.admin_login_dialog import AdminLoginDialog
            dlg = AdminLoginDialog(self)
            if dlg.exec():
                admin_state.set_admin(True)
                self._update_admin_button_style(True)

    def _update_admin_button_style(self, is_admin: bool) -> None:
        """Redraw the admin button to reflect the current access level."""
        if is_admin:
            self._btn_admin.setText("\U0001f513  Admin Mode")
            self._btn_admin.setStyleSheet(
                f"margin: 0 10px; border-radius: 6px; font-size: 11px;"
                f"color: {C.get('warning', '#f59e0b')};"
                f"background: rgba(245,158,11,0.12);"
                f"border: 1px solid rgba(245,158,11,0.35);"
                f"text-align: left; padding-left: 10px;"
            )
        else:
            self._btn_admin.setText("\U0001f512  Inquiry Mode")
            self._btn_admin.setStyleSheet(
                f"margin: 0 10px; border-radius: 6px; font-size: 11px;"
                f"color: {C.get('text_muted', '#6b7a99')};"
                f"background: transparent;"
                f"border: 1px solid {C.get('border', '#2d3748')};"
                f"text-align: left; padding-left: 10px;"
            )

    def _select(self, index: int) -> None:
        for i, btn in enumerate(self._nav_buttons):
            btn.set_active(i == index)
        self.nav_changed.emit(index)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rebate Tracker")
        self.setMinimumSize(1280, 780)
        self.resize(1440, 900)

        self._sync_worker: SyncWorker | None = None

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        # Right panel (top bar + content)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Top bar
        self.top_bar = TopBar()
        right_layout.addWidget(self.top_bar)

        # Stacked views (imported lazily to avoid circular imports)
        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack)

        root.addWidget(right_panel)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Rebate Tracker ready.")

        # Load views after window is set up
        self._load_views()

    def _load_views(self) -> None:
        """Lazily import and instantiate each view."""
        from ui.views.dashboard_view import DashboardView
        from ui.views.accounts_view import AccountsView
        from ui.views.rebate_structures_view import RebateStructuresView
        from ui.views.pdf_template_view import PdfTemplateView
        from ui.views.audit_log_view import AuditLogView
        from ui.views.settings_view import SettingsView

        start, end = self.top_bar.get_date_range()

        self.view_dashboard = DashboardView(start, end)
        self.view_accounts = AccountsView(start, end)
        self.view_rebate = RebateStructuresView()
        self.view_pdf = PdfTemplateView(start, end)
        self.view_audit = AuditLogView()
        self.view_settings = SettingsView()

        for view in [
            self.view_dashboard,
            self.view_accounts,
            self.view_rebate,
            self.view_pdf,
            self.view_audit,
            self.view_settings,
        ]:
            self.stack.addWidget(view)

        # Start the cloud backup worker singleton so it can receive schedule() calls
        from services.cloud_backup import CloudBackupWorker
        self._cloud_worker = CloudBackupWorker(self)
        CloudBackupWorker._instance = self._cloud_worker
        self._cloud_worker.status_changed.connect(self._on_cloud_backup_status)

        # Apply saved theme
        saved_theme = get_setting("theme", "dark")
        if saved_theme != "dark":
            self._apply_theme(saved_theme)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.sidebar.nav_changed.connect(self._on_nav_changed)
        self.top_bar.date_range_changed.connect(self._on_date_range_changed)
        self.top_bar.sync_requested.connect(self._on_sync_requested)
        self.view_settings.theme_changed.connect(self._apply_theme)
        self.view_settings.restore_complete.connect(self._on_data_restored)

    def _on_nav_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        # Refresh audit log whenever it becomes visible (index 4)
        if index == 4:
            self.view_audit.refresh()

    def _on_date_range_changed(self, start: date, end: date) -> None:
        self.view_dashboard.set_date_range(start, end)
        self.view_accounts.set_date_range(start, end)
        self.view_pdf.set_date_range(start, end)

    def _on_sync_requested(self) -> None:
        if self._sync_worker and self._sync_worker.isRunning():
            return  # Already syncing

        self.top_bar.set_syncing(True)
        self.status_bar.showMessage("Syncing data from SQL Server…")

        self._sync_worker = SyncWorker(self)
        self._sync_worker.progress.connect(self._on_sync_progress)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.start()

    def _on_sync_progress(self, pct: int, msg: str) -> None:
        self.top_bar.update_progress(pct, msg)
        self.status_bar.showMessage(msg)

    def _on_sync_finished(self, success: bool, msg: str) -> None:
        from PyQt6.QtWidgets import QMessageBox
        self.top_bar.set_syncing(False)
        color = C["success"] if success else C["danger"]
        short = "Sync complete." if success else "Sync failed."
        self.top_bar.set_status(short, color)
        self.status_bar.showMessage(msg[:300])

        if not success:
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Sync Failed")
            dlg.setIcon(QMessageBox.Icon.Critical)
            dlg.setText("Data sync from SQL Server failed.")
            dlg.setDetailedText(msg)
            dlg.exec()

        if success:
            # Refresh all views with fresh data
            start, end = self.top_bar.get_date_range()
            self.view_dashboard.refresh(start, end)
            self.view_accounts.refresh()
            # Trigger a cloud backup after a successful sync (sales cache excluded)
            if hasattr(self, "_cloud_worker"):
                self._cloud_worker.schedule()

    def _on_cloud_backup_status(self, success: bool, msg: str) -> None:
        """Show cloud backup result unobtrusively in the status bar."""
        if success:
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M")
            self.status_bar.showMessage(f"☁  Cloud backup updated at {ts}.", 6000)

    def _apply_theme(self, theme_name: str) -> None:
        """Rebuild the stylesheet for the given theme and re-apply to the app."""
        from PyQt6.QtWidgets import QApplication
        qss = apply_theme(theme_name)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(qss)
        # Reload gallery items so they pick up new theme colors
        if hasattr(self, "view_accounts"):
            self.view_accounts._load_accounts()
            if self.view_accounts.detail_panel._account:
                self.view_accounts.detail_panel._rebuild()
        # Redraw dashboard chart with new palette
        if hasattr(self, "view_dashboard"):
            self.view_dashboard.refresh_theme()

    def _on_data_restored(self) -> None:
        """
        Reload all views after a successful cloud or file backup restore.
        Called via the SettingsView.restore_complete signal — no app restart needed.
        """
        # Re-apply theme in case the restored settings use a different theme
        saved_theme = get_setting("theme", "dark")
        self._apply_theme(saved_theme)

        # Reload data views
        start, end = self.top_bar.get_date_range()
        if hasattr(self, "view_accounts"):
            self.view_accounts._load_accounts()
            # Clear any open account detail so stale data isn't shown
            if self.view_accounts.detail_panel._account:
                self.view_accounts.detail_panel._account = None
                self.view_accounts.detail_panel._rebuild()
        if hasattr(self, "view_rebate"):
            self.view_rebate._load_structures()
        if hasattr(self, "view_dashboard"):
            self.view_dashboard.refresh(start, end)

        # Refresh Settings field widgets to show restored values
        if hasattr(self, "view_settings"):
            self.view_settings._refresh_fields()

        self.status_bar.showMessage(
            "✓  Restore complete — reloading data from SQL Server…", 10000
        )

        # Automatically trigger a SQL Server data refresh so sales cache is
        # repopulated with the restored accounts without any manual action.
        self._on_sync_requested()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _parse_setting_date(val: str) -> date | None:
    if not val:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(val, "%Y-%m-%d").date()
    except ValueError:
        return None
