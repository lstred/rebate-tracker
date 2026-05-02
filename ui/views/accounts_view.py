"""
ui/views/accounts_view.py
--------------------------
Account management — left list panel + right detail panel.

Left panel  : searchable list of tracked accounts with quick-add controls
Right panel : account info header, rebate assignment, tier progress bar,
              monthly sales table, prior-year override editor

Add flow
--------
• Add by account# — user types an account number; start date required
• Add by marketing program (BCCODE) — user types the BCCODE; the sync
  engine will populate members automatically on next refresh
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal, QPointF, QSize
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QTextEdit,
    QGroupBox,
)

from db.local_db import (
    Account,
    AccountRebateAssignment,
    MarketingProgram,
    RebateStructure,
    SalesOverride,
    get_session,
    log_audit,
)
from services.rebate_calculator import (
    Tier,
    calculate_account_rebate,
    calculate_tiered_rebate,
    get_account_period,
    get_monthly_sales,
    get_period_both_sales,
    get_period_sales,
    get_prior_year_period,
)
from ui.theme import C


# ---------------------------------------------------------------------------
# Gallery utilities
# ---------------------------------------------------------------------------

def _current_rebate_year_start(start_date: date, reference: date) -> date:
    """
    The most recent anniversary of start_date that is <= reference.
    This is the START of the current rebate year, e.g. if start_date=2024-07-03
    and reference=2026-05-01 the current rebate year started 2025-07-03.
    """
    try:
        candidate = start_date.replace(year=reference.year)
    except ValueError:          # Feb 29 in non-leap year
        candidate = date(reference.year, 3, 1)
    if candidate <= reference:
        return candidate
    try:
        return start_date.replace(year=reference.year - 1)
    except ValueError:
        return date(reference.year - 1, 3, 1)


def _days_to_next_anniversary(start_date: date) -> int:
    """Days until start_date's next yearly anniversary (= rebate year renewal)."""
    today = date.today()
    try:
        this_year = start_date.replace(year=today.year)
    except ValueError:          # Feb 29 in a non-leap year
        this_year = date(today.year, 3, 1)
    if this_year > today:
        return (this_year - today).days
    try:
        next_year = start_date.replace(year=today.year + 1)
    except ValueError:
        next_year = date(today.year + 1, 3, 1)
    return (next_year - today).days


# ---------------------------------------------------------------------------
# Segmented tier progress bar
# ---------------------------------------------------------------------------

class TierProgressBar(QWidget):
    """
    Custom segmented rebate progress bar.

    • Blue fill          — current-period sales (or growth)
    • Translucent blue   — straight-line projected year-end total
    • Amber diamond ◆    — projected year-end marker
    • Green tick  |      — tier threshold already crossed
    • Gray tick   |      — tier threshold not yet reached
    • Tiers sharing a threshold are merged into one boundary marker
      (e.g. a sales tier and a freight qualification at the same level)
    • Full mode: shows $ threshold labels below the bar
    • Mini mode: compact bar only (used in the gallery panel)
    """

    _BAR_H   = 22
    _MINI_H  = 9

    def __init__(
        self,
        tiers: list[dict],
        current: float,
        projected: float = 0.0,
        prior_year: float = 0.0,
        mini: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._mini = mini
        self._segments: list[tuple[float, set]] = []
        self._max = 1.0
        self._current = 0.0
        self._projected = 0.0
        self._prior_year = 0.0
        self.set_data(tiers, current, projected, prior_year)

        if mini:
            self.setFixedHeight(self._MINI_H)
            self.setMinimumWidth(60)
        else:
            self.setFixedHeight(self._BAR_H + 30)
            self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, tiers: list[dict], current: float, projected: float, prior_year: float = 0.0):
        self._current = max(current, 0.0)
        self._projected = max(projected, self._current)
        self._prior_year = max(prior_year, 0.0)

        seen: dict[float, set] = {}
        for t in tiers:
            th = float(t.get("threshold", 0))
            seen.setdefault(th, set()).add(t.get("applies_to", "sales"))
        self._segments = sorted(seen.items())

        raw_max = self._segments[-1][0] if self._segments else max(self._current, 1.0)
        self._max = max(
            raw_max * 1.10,
            self._projected * 1.05,
            self._current * 1.10,
            self._prior_year * 1.05,
            1.0,
        )
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        bar_h = self._MINI_H if self._mini else self._BAR_H
        bar_y = 0

        def frac(v: float) -> float:
            return min(v / self._max, 1.0)

        fill_x = int(W * frac(self._current))
        proj_x = int(W * frac(self._projected))

        # Track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(C.get("surface3", "#1a2a40")))
        painter.drawRoundedRect(0, bar_y, W, bar_h, 3, 3)

        # Projected area
        if proj_x > fill_x:
            proj_col = QColor(C.get("accent", "#3b7dd8"))
            proj_col.setAlpha(55)
            painter.setBrush(proj_col)
            painter.drawRect(fill_x, bar_y, proj_x - fill_x, bar_h)

        # Current fill
        if fill_x > 0:
            painter.setBrush(QColor(C.get("accent", "#3b7dd8")))
            painter.drawRoundedRect(0, bar_y, fill_x, bar_h, 3, 3)

        # Prior year reference — gray dashed vertical line + amber diamond marker
        if self._prior_year > 0:
            py_x = int(W * frac(self._prior_year))
            if 1 < py_x < W - 1:
                py_col = QColor(C.get("text_dim", "#4a5568"))
                py_col.setAlpha(200)
                py_pen = QPen(py_col, 1 if self._mini else 2)
                if not self._mini:
                    py_pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(py_pen)
                painter.drawLine(py_x, bar_y, py_x, bar_y + bar_h)

                # Amber diamond ◆ at prior year position
                if not self._mini:
                    ds = 5
                    cx = min(py_x, W - ds - 1)
                    cy = bar_y + bar_h // 2
                    path = QPainterPath()
                    path.moveTo(QPointF(cx,      cy - ds))
                    path.lineTo(QPointF(cx + ds, cy))
                    path.lineTo(QPointF(cx,      cy + ds))
                    path.lineTo(QPointF(cx - ds, cy))
                    path.closeSubpath()
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(C.get("warning", "#fbbf24")))
                    painter.drawPath(path)

        # Tier boundary ticks
        for th, types in self._segments:
            x = int(W * frac(th))
            if x <= 1 or x >= W - 1:
                continue
            passed = self._current >= th
            tick_col = QColor(C.get("success", "#4ade80") if passed else C.get("text_dim", "#4a5568"))
            tick_w = 1 if self._mini else 2
            painter.setPen(QPen(tick_col, tick_w))
            painter.drawLine(x, bar_y, x, bar_y + bar_h)
            if not self._mini:
                painter.drawLine(x, bar_y - 4, x, bar_y)

        # Threshold labels (full mode only)
        if not self._mini and self._segments:
            painter.setFont(QFont("Segoe UI", 8))
            fm = QFontMetrics(painter.font())
            lbl_y = bar_y + bar_h + 18
            for i, (th, types) in enumerate(self._segments):
                x = int(W * frac(th))
                if x <= 0:
                    continue
                passed = self._current >= th
                painter.setPen(QPen(QColor(C.get("success", "#4ade80") if passed else C.get("text_muted", "#6b7a99"))))
                if th >= 1_000_000:
                    lbl = f"T{i+1} ${th/1_000_000:.1f}M"
                elif th >= 1_000:
                    lbl = f"T{i+1} ${th/1_000:.0f}K"
                else:
                    lbl = f"T{i+1} ${th:.0f}"
                if "freight" in types and len(types) > 1:
                    lbl += " ✦"
                tw = fm.horizontalAdvance(lbl)
                lx = max(0, min(x - tw // 2, W - tw))
                painter.drawText(lx, lbl_y, lbl)

        painter.end()

    @staticmethod
    def build_legend(show_prior_year: bool = True) -> "QWidget":
        """Compact horizontal legend strip explaining bar colours and markers."""
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(4)

        def add(marker: str, style: str, label: str, gap: int = 10):
            m = QLabel(marker)
            m.setStyleSheet(style)
            lay.addWidget(m)
            t = QLabel(label)
            t.setStyleSheet(f"color:{C['text_muted']}; font-size:9px;")
            lay.addWidget(t)
            lay.addSpacing(gap)

        add("█", f"color:{C['accent']}; font-size:11px;", "Current Sales")
        add("▒", f"color:{C['accent']}; font-size:11px;", "Projected (Year-End)")
        if show_prior_year:
            add("◆", f"color:{C['warning']}; font-size:10px;", "Prior Year")
        add("│", f"color:{C['success']}; font-size:13px; font-weight:bold;", "Tier Reached")
        add("│", f"color:{C.get('text_dim','#4a5568')}; font-size:13px;", "Tier Pending")
        lay.addStretch()
        return container


# ---------------------------------------------------------------------------
# Gallery card widget (one per account in the left panel)
# ---------------------------------------------------------------------------

class AccountGalleryItem(QWidget):
    """
    Rich gallery card for the account list panel.
    Shows: account number, program badge, days-to-renewal, account name,
    start date, and a mini segmented tier progress bar.
    """

    def __init__(self, account: Account, program_bccode: str = "", closed: bool = False, parent=None):
        super().__init__(parent)
        self._account = account
        self._mini_bar: Optional[TierProgressBar] = None
        self._closed = closed
        self._build(program_bccode)
        if closed:
            self.setEnabled(False)
            self.setToolTip("Closed account (*CLSD*)")
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            op = QGraphicsOpacityEffect(self)
            op.setOpacity(0.45)
            self.setGraphicsEffect(op)

    def _build(self, program_bccode: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 6)
        layout.setSpacing(2)

        # Row 1 — account number + program badge + renewal countdown
        row1 = QHBoxLayout()
        row1.setSpacing(5)

        acct_lbl = QLabel(self._account.account_number)
        acct_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        row1.addWidget(acct_lbl)

        if program_bccode:
            badge = QLabel(program_bccode)
            badge.setProperty("class", "badge")
            badge.setFixedHeight(16)
            row1.addWidget(badge)

        row1.addStretch()

        days = _days_to_next_anniversary(self._account.start_date)
        if days <= 30:
            days_color = C["danger"]
        elif days <= 60:
            days_color = "#f59e0b"
        else:
            days_color = C["text_muted"]
        days_lbl = QLabel(f"{days}d")
        days_lbl.setStyleSheet(
            f"color: {days_color}; font-size: 9px; font-weight: bold;"
        )
        days_lbl.setToolTip(f"Rebate year renews in {days} days")
        row1.addWidget(days_lbl)

        layout.addLayout(row1)

        # Row 2 — account name + start date
        row2 = QHBoxLayout()
        row2.setSpacing(0)

        name_lbl = QLabel(self._account.account_name or "—")
        name_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row2.addWidget(name_lbl, stretch=1)

        start_lbl = QLabel(self._account.start_date.strftime("%m/%d"))
        start_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 9px;")
        start_lbl.setToolTip(f"Rebate start: {self._account.start_date.strftime('%m/%d/%Y')}")
        row2.addWidget(start_lbl)

        layout.addLayout(row2)

        # Mini progress bar (placeholder until GalleryLoader fills it in)
        self._mini_bar = TierProgressBar([], 0.0, 0.0, mini=True)
        layout.addWidget(self._mini_bar)

    def update_tier_data(self, tiers: list[dict], current: float, projected: float, prior_year: float = 0.0):
        """Update the mini bar once background loading has the real data."""
        if self._mini_bar:
            self._mini_bar.set_data(tiers, current, projected, prior_year)


# ---------------------------------------------------------------------------
# Gallery data loader — computes per-account sales + tier info from SQLite
# ---------------------------------------------------------------------------

class GalleryLoader(QThread):
    """
    Background thread that computes current-period sales and tier structure
    for all tracked accounts so the gallery mini bars can be populated.
    No SQL Server calls — reads only from the local SQLite cache.
    """

    ready = pyqtSignal(dict)   # account_number -> {tiers, current, projected}

    def __init__(self, accounts: list, end: date, parent=None):
        super().__init__(parent)
        self._accounts = accounts
        self._end = end

    def run(self):
        from services.rebate_calculator import get_account_period, get_period_sales
        today = date.today()
        result: dict = {}

        with get_session() as session:
            assignments = {
                a.account_number: a
                for a in session.query(AccountRebateAssignment).all()
            }
            structures = {
                s.id: s
                for s in session.query(RebateStructure).all()
            }

        for acct in self._accounts:
            try:
                # Use the current rebate year start (most recent anniversary)
                # so elapsed_days is always within the current 12-month window
                ry_start = _current_rebate_year_start(acct.start_date, today)
                current = get_period_sales(acct.account_number, ry_start, today)

                tiers: list = []
                asn = assignments.get(acct.account_number)
                if asn and asn.rebate_structure_id in structures:
                    tiers = structures[asn.rebate_structure_id].get_tiers()

                elapsed = max(1, (today - ry_start).days)
                try:
                    fy_end = ry_start.replace(year=ry_start.year + 1)
                except ValueError:
                    fy_end = ry_start.replace(year=ry_start.year + 1, day=28)
                full_days = max(1, (fy_end - ry_start).days)
                projected = current * full_days / elapsed

                # Prior year: same elapsed window shifted back one year
                try:
                    py_start = ry_start.replace(year=ry_start.year - 1)
                    py_end = today.replace(year=today.year - 1)
                except ValueError:
                    py_start = ry_start - timedelta(days=365)
                    py_end = today - timedelta(days=365)
                prior_year = get_period_sales(acct.account_number, py_start, py_end)

                result[acct.account_number] = {
                    "tiers": tiers,
                    "current": current,
                    "projected": projected,
                    "prior_year": prior_year,
                }
            except Exception:
                result[acct.account_number] = {"tiers": [], "current": 0.0, "projected": 0.0, "prior_year": 0.0}

        self.ready.emit(result)

class DetailLoader(QThread):
    ready = pyqtSignal(dict)

    def __init__(self, account: Account, start: date, end: date, parent=None):
        super().__init__(parent)
        self.account = account
        self._start = start
        self._end = end

    def run(self):
        with get_session() as session:
            assignment = (
                session.query(AccountRebateAssignment)
                .filter_by(account_number=self.account.account_number)
                .first()
            )
            structure = None
            if assignment:
                structure = (
                    session.query(RebateStructure)
                    .filter_by(id=assignment.rebate_structure_id)
                    .first()
                )
            overrides = (
                session.query(SalesOverride)
                .filter_by(account_number=self.account.account_number)
                .all()
            )
            # Fetch program BCCODE while session is open
            program_bccode = ""
            if self.account.marketing_program_id:
                mp = session.query(MarketingProgram).filter_by(
                    id=self.account.marketing_program_id
                ).first()
                if mp:
                    program_bccode = mp.bccode or ""

        # Use account start_date for rebate period; monthly chart uses same account period
        effective_start, effective_end = get_account_period(self.account, self._end)
        prior_start, prior_end = get_prior_year_period(effective_start, effective_end)
        current_sales = get_period_sales(
            self.account.account_number, effective_start, effective_end
        )
        prior_sales = get_period_sales(
            self.account.account_number, prior_start, prior_end
        )

        # Straight-line projection using the CURRENT rebate year start (most recent
        # anniversary), so elapsed_days is always within the current 12-month window.
        today = date.today()
        ry_start = _current_rebate_year_start(self.account.start_date, today)
        elapsed_days = max(1, (today - ry_start).days)
        try:
            full_year_end = ry_start.replace(year=ry_start.year + 1)
        except ValueError:  # Feb 29 in non-leap year
            full_year_end = ry_start.replace(year=ry_start.year + 1, day=28)
        full_year_days = max(1, (full_year_end - ry_start).days)
        # Use current_sales for the same window (current rebate year to today)
        ry_sales = get_period_sales(self.account.account_number, ry_start, today)
        projected_sales = ry_sales * full_year_days / elapsed_days

        # Monthly: only current rebate year, with prior year side-by-side
        monthly = get_monthly_sales(
            self.account.account_number, effective_start, effective_end,
            include_prior_year=True,
        )

        rebate_result = None
        if structure:
            rebate_result = calculate_account_rebate(
                self.account, structure, self._end
            )

        self.ready.emit(
            {
                "current_sales": current_sales,
                "prior_sales": prior_sales,
                "prior_start": prior_start,
                "prior_end": prior_end,
                "monthly": monthly,
                "structure": structure,
                "assignment": assignment,
                "structure_is_custom": (not structure.is_template) if structure else False,
                "structure_derived_from_id": getattr(structure, "derived_from_id", None) if structure else None,
                "overrides": [
                    {
                        "id": o.id,
                        "period_start": o.period_start,
                        "period_end": o.period_end,
                        "amount": o.amount,
                        "mode": o.mode,
                        "notes": o.notes or "",
                    }
                    for o in overrides
                ],
                "rebate_result": rebate_result,
                "projected_sales": projected_sales,
                "program_bccode": program_bccode,
            }
        )


# ---------------------------------------------------------------------------
# Add account dialog
# ---------------------------------------------------------------------------

class AddAccountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Account")
        self.setMinimumWidth(400)
        self.setStyleSheet(
            f"background-color: {C['surface']}; color: {C['text']};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(QLabel("Add by Account #", self))

        form = QFormLayout()
        form.setSpacing(10)
        self.acct_input = QLineEdit()
        self.acct_input.setPlaceholderText("Enter account number")
        form.addRow("Account #:", self.acct_input)

        self.start_date = QDateEdit(QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("MM/dd/yyyy")
        form.addRow("Rebate Start Date:", self.start_date)

        layout.addLayout(form)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C['border']};")
        layout.addWidget(sep)

        layout.addWidget(QLabel("— OR — Add Marketing Program by BCCODE", self))

        form2 = QFormLayout()
        form2.setSpacing(10)
        self.mp_input = QLineEdit()
        self.mp_input.setPlaceholderText("BCCODE (e.g. MP2024)")
        form2.addRow("BCCODE:", self.mp_input)
        self.mp_name = QLineEdit()
        self.mp_name.setPlaceholderText("Optional display name")
        form2.addRow("Name:", self.mp_name)
        layout.addLayout(form2)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_data(self) -> dict:
        return {
            "account_number": self.acct_input.text().strip(),
            "start_date": self.start_date.date().toPyDate(),
            "bccode": self.mp_input.text().strip().upper(),
            "mp_name": self.mp_name.text().strip(),
        }


# ---------------------------------------------------------------------------
# Override dialog
# ---------------------------------------------------------------------------

class OverrideDialog(QDialog):
    def __init__(self, account_number: str, existing: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prior Year Sales Override")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"background-color: {C['surface']}; color: {C['text']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(
            QLabel(f"Override for Account: {account_number}")
        )

        form = QFormLayout()
        form.setSpacing(10)

        self.date_start = QDateEdit(
            QDate.fromString(
                (existing["period_start"].isoformat() if existing else date.today().isoformat()),
                "yyyy-MM-dd",
            )
        )
        self.date_start.setCalendarPopup(True)
        self.date_start.setDisplayFormat("MM/dd/yyyy")
        form.addRow("Period Start:", self.date_start)

        self.date_end = QDateEdit(
            QDate.fromString(
                (existing["period_end"].isoformat() if existing else date.today().isoformat()),
                "yyyy-MM-dd",
            )
        )
        self.date_end.setCalendarPopup(True)
        self.date_end.setDisplayFormat("MM/dd/yyyy")
        form.addRow("Period End:", self.date_end)

        self.amount = QDoubleSpinBox()
        self.amount.setPrefix("$")
        self.amount.setMaximum(999_999_999.0)
        self.amount.setDecimals(2)
        self.amount.setValue(existing["amount"] if existing else 0.0)
        form.addRow("Amount:", self.amount)

        self.mode = QComboBox()
        self.mode.addItems(["replace — use only this value", "add — add to SQL total"])
        if existing and existing.get("mode") == "add":
            self.mode.setCurrentIndex(1)
        form.addRow("Mode:", self.mode)

        self.notes = QLineEdit(existing.get("notes", "") if existing else "")
        self.notes.setPlaceholderText("Optional note")
        form.addRow("Notes:", self.notes)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_data(self) -> dict:
        return {
            "period_start": self.date_start.date().toPyDate(),
            "period_end": self.date_end.date().toPyDate(),
            "amount": self.amount.value(),
            "mode": "replace" if self.mode.currentIndex() == 0 else "add",
            "notes": self.notes.text().strip(),
        }


# ---------------------------------------------------------------------------
# Account detail panel
# ---------------------------------------------------------------------------

class AccountDetailPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._account: Optional[Account] = None
        self._detail_data: Optional[dict] = None
        self._start = date.today()
        self._end = date.today()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(20, 16, 20, 20)
        self._layout.setSpacing(16)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # Placeholder
        self._placeholder = QLabel("Select an account to view details.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {C['text_muted']}; font-size: 15px;")
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch()

    def load_account(self, account: Account, start: date, end: date):
        self._account = account
        self._start = start
        self._end = end
        self._rebuild()

    def _rebuild(self):
        # Clear layout
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._account:
            lbl = QLabel("Select an account.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {C['text_muted']};")
            self._layout.addWidget(lbl)
            self._layout.addStretch()
            return

        # Loading spinner
        self._lbl_loading = QLabel("Loading…")
        self._lbl_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_loading.setStyleSheet(f"color: {C['text_muted']};")
        self._layout.addWidget(self._lbl_loading)
        self._layout.addStretch()

        # Launch loader thread
        self._loader = DetailLoader(self._account, self._start, self._end, self)
        self._loader.ready.connect(self._on_detail_ready)
        self._loader.start()

    def _on_detail_ready(self, data: dict):
        self._detail_data = data
        # Clear and rebuild with data
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        a = self._account
        d = data

        # ── Account info card ─────────────────────────────────────────
        info_frame = QFrame()
        info_frame.setProperty("class", "card")
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 14, 16, 14)

        name_col = QVBoxLayout()
        name_lbl = QLabel(a.account_name or a.account_number)
        name_lbl.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        name_col.addWidget(name_lbl)
        name_col.addWidget(QLabel(f"Account #: {a.account_number}"))

        addr_parts = [
            p for p in [a.address1, a.address2,
                         ", ".join(filter(None, [a.city, a.state])),
                         " ".join(filter(None, [a.zip1, a.zip2]))]
            if p
        ]
        addr_str = "  |  ".join(addr_parts)
        addr_lbl = QLabel(addr_str)
        addr_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        name_col.addWidget(addr_lbl)
        if a.phone:
            phone_lbl = QLabel(a.phone)
            phone_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
            name_col.addWidget(phone_lbl)
        if getattr(a, "email", None):
            email_lbl = QLabel(f"✉  {a.email}")
            email_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
            name_col.addWidget(email_lbl)

        info_layout.addLayout(name_col, stretch=3)

        # Source badge + program code + start date + edit button
        src_col = QVBoxLayout()
        src_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        badge_row.addStretch()

        # 3-char program code badge (if applicable)
        program_bccode = d.get("program_bccode", "")
        if program_bccode:
            pgm_badge = QLabel(program_bccode)
            pgm_badge.setProperty("class", "badge")
            badge_row.addWidget(pgm_badge)

        src_badge = QLabel("Marketing Program" if a.source == "marketing_program" else "Manual")
        src_badge.setProperty("class", "tag-success" if a.source == "marketing_program" else "tag-warning")
        badge_row.addWidget(src_badge)
        src_col.addLayout(badge_row)

        start_row = QHBoxLayout()
        start_row.setSpacing(4)
        start_lbl = QLabel(f"Start: {a.start_date.strftime('%m/%d/%Y')}")
        start_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size:11px;")
        btn_edit_date = QPushButton("Edit")
        btn_edit_date.setProperty("class", "primary")
        btn_edit_date.setFixedHeight(20)
        btn_edit_date.setStyleSheet("font-size:10px; padding: 0 6px;")
        btn_edit_date.clicked.connect(self._edit_start_date)
        start_row.addStretch()
        start_row.addWidget(start_lbl)
        start_row.addWidget(btn_edit_date)
        src_col.addLayout(start_row)

        email_row = QHBoxLayout()
        email_row.setSpacing(4)
        current_email = getattr(a, "email", "") or ""
        email_lbl = QLabel(current_email if current_email else "No email set")
        email_lbl.setStyleSheet(
            f"color: {C['text_muted']}; font-size:11px;"
            + (" font-style:italic;" if not current_email else "")
        )
        btn_edit_email = QPushButton("✉")
        btn_edit_email.setToolTip("Set email address")
        btn_edit_email.setFixedSize(22, 20)
        btn_edit_email.setStyleSheet("font-size:10px; padding: 0;")
        btn_edit_email.clicked.connect(self._edit_email)
        email_row.addStretch()
        email_row.addWidget(email_lbl)
        email_row.addWidget(btn_edit_email)
        src_col.addLayout(email_row)

        info_layout.addLayout(src_col, stretch=1)

        self._layout.addWidget(info_frame)

        # ── Rebate structure + KPIs ───────────────────────────────────
        rebate_frame = QFrame()
        rebate_frame.setProperty("class", "card")
        rebate_layout = QVBoxLayout(rebate_frame)
        rebate_layout.setContentsMargins(16, 12, 16, 12)
        rebate_layout.setSpacing(8)

        rr = d.get("rebate_result")
        structure: Optional[RebateStructure] = d.get("structure")
        is_custom = d.get("structure_is_custom", False)

        # Structure label row
        struct_row = QHBoxLayout()
        custom_tag = (
            f" <span style='background:#f59e0b22; color:#f59e0b; border:1px solid #f59e0b55;"
            f" border-radius:3px; padding:1px 6px; font-size:10px; font-weight:bold;'>Custom</span>"
            if is_custom else ""
        )
        struct_name_lbl = QLabel(
            f"Rebate Structure: <b>{structure.name if structure else '(none assigned)'}</b>{custom_tag}"
        )
        struct_name_lbl.setTextFormat(Qt.TextFormat.RichText)
        struct_row.addWidget(struct_name_lbl)
        struct_row.addStretch()
        btn_assign = QPushButton("Assign Structure")
        btn_assign.setProperty("class", "primary")
        btn_assign.clicked.connect(lambda: self._assign_structure())
        struct_row.addWidget(btn_assign)
        if structure:
            if is_custom:
                btn_edit_custom = QPushButton("Edit Custom Rebate")
                btn_edit_custom.clicked.connect(lambda: self._customize_rebate())
                struct_row.addWidget(btn_edit_custom)
                btn_reset = QPushButton("Reset to Template")
                btn_reset.clicked.connect(lambda: self._reset_custom_rebate())
                struct_row.addWidget(btn_reset)
            else:
                btn_customize = QPushButton("Customize")
                btn_customize.clicked.connect(lambda: self._customize_rebate())
                struct_row.addWidget(btn_customize)
        rebate_layout.addLayout(struct_row)

        # KPI row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)

        def mini_kpi(label, value, color=C["text"]):
            f = QFrame()
            f.setProperty("class", "kpi-card")
            l = QVBoxLayout(f)
            l.setContentsMargins(12, 8, 12, 8)
            v = QLabel(value)
            v.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
            v.setStyleSheet(f"color: {color};")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            lb = QLabel(label)
            lb.setStyleSheet(f"color: {C['text_muted']}; font-size:10px;")
            lb.setAlignment(Qt.AlignmentFlag.AlignRight)
            l.addWidget(v)
            l.addWidget(lb)
            return f

        kpi_row.addWidget(mini_kpi("Current Sales", f"${d['current_sales']:,.2f}", C["accent"]))
        kpi_row.addWidget(mini_kpi("Prior Year Sales", f"${d['prior_sales']:,.2f}"))
        growth = d["current_sales"] - d["prior_sales"]
        kpi_row.addWidget(mini_kpi("Growth", f"${growth:,.2f}", C["success"] if growth >= 0 else C["danger"]))
        kpi_row.addWidget(
            mini_kpi(
                "Projected Rebate",
                f"${rr.rebate_amount:,.2f}" if rr else "—",
                C["success"],
            )
        )
        rebate_layout.addLayout(kpi_row)

        # Segmented tier progress bar
        if rr and structure:
            tiers_raw = structure.get_tiers()
            if tiers_raw:
                eval_sales = (
                    rr.growth_amount if structure.structure_type == "growth"
                    else rr.current_sales
                )
                projected = d.get("projected_sales", eval_sales)

                is_growth = structure.structure_type == "growth"
                sorted_thresh = sorted({t.get("threshold", 0) for t in tiers_raw})
                max_threshold = sorted_thresh[-1] if sorted_thresh else 1

                prog_label = QLabel(
                    f"{'Growth' if is_growth else 'Sales'}"
                    f"  ·  <b>${eval_sales:,.0f}</b> current"
                    f"  ·  <b>${projected:,.0f}</b> projected"
                    f"  ·  <b>${d['prior_sales']:,.0f}</b> prior year"
                    f"  ·  max tier ${max_threshold:,.0f}"
                )
                prog_label.setTextFormat(Qt.TextFormat.RichText)
                prog_label.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
                rebate_layout.addWidget(prog_label)

                prior_yr_sales = d["prior_sales"] if not is_growth else 0.0
                tier_bar = TierProgressBar(tiers_raw, eval_sales, projected, prior_year=prior_yr_sales)
                rebate_layout.addWidget(tier_bar)
                rebate_layout.addWidget(TierProgressBar.build_legend(show_prior_year=not is_growth))

        self._layout.addWidget(rebate_frame)

        # ── Tab: Monthly Sales / Tier Breakdown / Overrides ──────────
        tabs = QTabWidget()

        # Monthly sales tab
        monthly_tab = QWidget()
        mt_layout = QVBoxLayout(monthly_tab)
        mt_layout.setContentsMargins(4, 8, 4, 4)
        monthly_tbl = QTableWidget(0, 5)
        monthly_tbl.setHorizontalHeaderLabels(
            ["Month", "Current Year Sales", "Prior Year Sales", "YoY Growth", "CY Cumulative"]
        )
        _mh = monthly_tbl.horizontalHeader()
        _mh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _mh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        _mh.setStretchLastSection(False)
        monthly_tbl.setAlternatingRowColors(True)
        monthly_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        monthly_tbl.verticalHeader().setVisible(False)
        monthly_tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        monthly_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        partial_note_text = ""
        for m in d.get("monthly", []):
            row = monthly_tbl.rowCount()
            monthly_tbl.insertRow(row)
            monthly_tbl.setItem(row, 0, QTableWidgetItem(m["display_label"]))
            monthly_tbl.setItem(row, 1, QTableWidgetItem(f"${m['sales']:,.2f}"))
            monthly_tbl.setItem(row, 2, QTableWidgetItem(f"${m['prior_sales']:,.2f}"))
            growth_val = m["sales"] - m["prior_sales"]
            growth_item = QTableWidgetItem(f"${growth_val:,.2f}")
            if growth_val < 0:
                growth_item.setForeground(QColor(C["danger"]))
            elif growth_val > 0:
                growth_item.setForeground(QColor(C["success"]))
            monthly_tbl.setItem(row, 3, growth_item)
            monthly_tbl.setItem(row, 4, QTableWidgetItem(f"${m['cumulative']:,.2f}"))
            if m.get("partial_note") and not partial_note_text:
                partial_note_text = m["partial_note"]
        # Expand to fit all rows so the outer scroll handles navigation
        monthly_tbl.resizeRowsToContents()
        _row_h = monthly_tbl.rowHeight(0) if monthly_tbl.rowCount() else 26
        monthly_tbl.setMinimumHeight(
            monthly_tbl.horizontalHeader().height()
            + _row_h * monthly_tbl.rowCount()
            + 6
        )
        mt_layout.addWidget(monthly_tbl)
        if partial_note_text:
            note_lbl = QLabel(f"* {partial_note_text}")
            note_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px; font-style: italic;")
            mt_layout.addWidget(note_lbl)
        tabs.addTab(monthly_tab, "Monthly Sales")

        # Tier breakdown tab
        if rr and rr.tier_results:
            tier_tab = QWidget()
            tt_layout = QVBoxLayout(tier_tab)
            tt_layout.setContentsMargins(4, 8, 4, 4)
            tier_tbl = QTableWidget(0, 4)
            tier_tbl.setHorizontalHeaderLabels(["Tier", "Rate", "Applicable Sales", "Rebate"])
            _th = tier_tbl.horizontalHeader()
            _th.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            _th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            _th.setStretchLastSection(False)
            tier_tbl.setAlternatingRowColors(True)
            tier_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tier_tbl.verticalHeader().setVisible(False)
            tier_tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tier_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            for tr in rr.tier_results:
                row = tier_tbl.rowCount()
                tier_tbl.insertRow(row)
                # Build a readable label from threshold and mode
                if tr.threshold >= 1_000_000:
                    thresh_str = f"${tr.threshold / 1_000_000:.1f}M+"
                elif tr.threshold >= 1_000:
                    thresh_str = f"${tr.threshold / 1_000:.0f}K+"
                else:
                    thresh_str = f"${tr.threshold:,.0f}+"
                tier_tbl.setItem(row, 0, QTableWidgetItem(
                    f"{thresh_str}  {tr.rate*100:.2f}%  ({tr.mode.replace('_',' ')})"
                ))
                tier_tbl.setItem(row, 1, QTableWidgetItem(f"{tr.rate*100:.2f}%"))
                tier_tbl.setItem(row, 2, QTableWidgetItem(f"${tr.applicable_sales:,.2f}"))
                tier_tbl.setItem(row, 3, QTableWidgetItem(f"${tr.rebate_contribution:,.2f}"))
            # Total row
            row = tier_tbl.rowCount()
            tier_tbl.insertRow(row)
            tier_tbl.setItem(row, 2, QTableWidgetItem("Total Rebate"))
            total_item = QTableWidgetItem(f"${rr.rebate_amount:,.2f}")
            total_item.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            tier_tbl.setItem(row, 3, total_item)
            # Expand to fit all rows
            tier_tbl.resizeRowsToContents()
            _row_h = tier_tbl.rowHeight(0) if tier_tbl.rowCount() else 26
            tier_tbl.setMinimumHeight(
                tier_tbl.horizontalHeader().height()
                + _row_h * tier_tbl.rowCount()
                + 6
            )
            tt_layout.addWidget(tier_tbl)
            tabs.addTab(tier_tab, "Tier Breakdown")

        # Overrides tab
        override_tab = QWidget()
        ot_layout = QVBoxLayout(override_tab)
        ot_layout.setContentsMargins(4, 8, 4, 4)
        ov_btn_row = QHBoxLayout()
        ov_btn_row.addWidget(QLabel("Prior Year Sales Overrides"))
        ov_btn_row.addStretch()
        btn_add_ov = QPushButton("+ Add Override")
        btn_add_ov.setProperty("class", "primary")
        btn_add_ov.clicked.connect(lambda: self._add_override())
        ov_btn_row.addWidget(btn_add_ov)
        ot_layout.addLayout(ov_btn_row)

        self._override_tbl = QTableWidget(0, 5)
        self._override_tbl.setHorizontalHeaderLabels(
            ["Period Start", "Period End", "Amount", "Mode", "Actions"]
        )
        self._override_tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._override_tbl.setAlternatingRowColors(True)
        self._override_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._override_tbl.verticalHeader().setVisible(False)
        ot_layout.addWidget(self._override_tbl)
        self._populate_overrides(d.get("overrides", []))
        tabs.addTab(override_tab, "Prior Year Overrides")

        self._layout.addWidget(tabs)
        self._layout.addStretch()

    def _populate_overrides(self, overrides: list[dict]):
        self._override_tbl.setRowCount(0)
        for ov in overrides:
            row = self._override_tbl.rowCount()
            self._override_tbl.insertRow(row)
            self._override_tbl.setItem(row, 0, QTableWidgetItem(
                ov["period_start"].strftime("%m/%d/%Y")
            ))
            self._override_tbl.setItem(row, 1, QTableWidgetItem(
                ov["period_end"].strftime("%m/%d/%Y")
            ))
            self._override_tbl.setItem(row, 2, QTableWidgetItem(f"${ov['amount']:,.2f}"))
            self._override_tbl.setItem(row, 3, QTableWidgetItem(ov["mode"]))

            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(4, 2, 4, 2)
            act_layout.setSpacing(4)
            btn_edit = QPushButton("Edit")
            btn_edit.clicked.connect(lambda _, o=ov: self._edit_override(o))
            btn_del = QPushButton("Delete")
            btn_del.setProperty("class", "danger")
            btn_del.clicked.connect(lambda _, oid=ov["id"]: self._delete_override(oid))
            act_layout.addWidget(btn_edit)
            act_layout.addWidget(btn_del)
            self._override_tbl.setCellWidget(row, 4, act_widget)

    def _edit_start_date(self):
        if not self._account:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Start Date")
        dlg.setMinimumWidth(300)
        dlg.setStyleSheet(f"background-color: {C['surface']}; color: {C['text']};")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        date_edit = QDateEdit(
            QDate.fromString(self._account.start_date.isoformat(), "yyyy-MM-dd")
        )
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("MM/dd/yyyy")
        form.addRow("Start Date:", date_edit)
        layout.addLayout(form)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec():
            old_date = self._account.start_date.isoformat()
            new_date = date_edit.date().toPyDate()
            with get_session() as session:
                acct = (
                    session.query(Account)
                    .filter_by(account_number=self._account.account_number)
                    .first()
                )
                if acct:
                    acct.start_date = new_date
            log_audit(
                "edit", "account", self._account.account_number,
                f"Changed start date for {self._account.account_number}",
                old_value=old_date,
                new_value=new_date.isoformat(),
            )
            self._rebuild()

    def _edit_email(self):
        if not self._account:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout, QLabel
        import re

        dlg = QDialog(self)
        dlg.setWindowTitle("Set Email Address")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title_lbl = QLabel("Dealer Email Address")
        title_lbl.setFont(__import__("PyQt6.QtGui", fromlist=["QFont"]).QFont("Segoe UI", 11, __import__("PyQt6.QtGui", fromlist=["QFont"]).QFont.Weight.Bold))
        layout.addWidget(title_lbl)

        hint_lbl = QLabel(f"Set the contact email for <b>{self._account.account_name or self._account.account_number}</b>. Used for emailing PDF statements.")
        hint_lbl.setProperty("class", "muted")
        hint_lbl.setWordWrap(True)
        layout.addWidget(hint_lbl)

        form = QFormLayout()
        form.setSpacing(8)
        email_input = QLineEdit(getattr(self._account, "email", "") or "")
        email_input.setPlaceholderText("dealer@example.com")
        email_input.setMinimumHeight(32)
        form.addRow("Email:", email_input)
        layout.addLayout(form)

        err_lbl = QLabel("")
        err_lbl.setStyleSheet(f"color: {C['danger']}; font-size: 11px;")
        err_lbl.setVisible(False)
        layout.addWidget(err_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setProperty("class", "primary")

        def _try_accept():
            text = email_input.text().strip()
            if text and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
                err_lbl.setText("Please enter a valid email address.")
                err_lbl.setVisible(True)
                return
            dlg.accept()

        ok_btn.clicked.disconnect()
        ok_btn.clicked.connect(_try_accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec():
            new_email = email_input.text().strip()
            old_email = getattr(self._account, "email", "") or ""
            with get_session() as session:
                acct = (
                    session.query(Account)
                    .filter_by(account_number=self._account.account_number)
                    .first()
                )
                if acct:
                    acct.email = new_email or None
            log_audit(
                "edit", "account", self._account.account_number,
                f"Updated email for {self._account.account_number}",
                old_value=old_email,
                new_value=new_email,
            )
            self._rebuild()

    def _add_override(self):
        if not self._account:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        dlg = OverrideDialog(self._account.account_number, parent=self)
        if dlg.exec():
            data = dlg.get_data()
            with get_session() as session:
                session.add(
                    SalesOverride(
                        account_number=self._account.account_number,
                        period_start=data["period_start"],
                        period_end=data["period_end"],
                        amount=data["amount"],
                        mode=data["mode"],
                        notes=data["notes"],
                    )
                )
            log_audit(
                "add", "override", self._account.account_number,
                f"Added prior year override for {self._account.account_number}",
                new_value=(
                    f"{data['period_start']} – {data['period_end']}: "
                    f"${data['amount']:,.2f} ({data['mode']})"
                ),
            )
            self._rebuild()

    def _edit_override(self, ov: dict):
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        dlg = OverrideDialog(self._account.account_number, existing=ov, parent=self)
        if dlg.exec():
            data = dlg.get_data()
            with get_session() as session:
                existing = (
                    session.query(SalesOverride).filter_by(id=ov["id"]).first()
                )
                if existing:
                    existing.period_start = data["period_start"]
                    existing.period_end = data["period_end"]
                    existing.amount = data["amount"]
                    existing.mode = data["mode"]
                    existing.notes = data["notes"]
            log_audit(
                "edit", "override", self._account.account_number,
                f"Edited prior year override for {self._account.account_number}",
                old_value=(
                    f"{ov['period_start']} – {ov['period_end']}: "
                    f"${ov['amount']:,.2f} ({ov['mode']})"
                ),
                new_value=(
                    f"{data['period_start']} – {data['period_end']}: "
                    f"${data['amount']:,.2f} ({data['mode']})"
                ),
            )
            self._rebuild()

    def _delete_override(self, override_id: int):
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        if (
            QMessageBox.question(self, "Delete Override", "Delete this override?")
            == QMessageBox.StandardButton.Yes
        ):
            with get_session() as session:
                ov = session.query(SalesOverride).filter_by(id=override_id).first()
                if ov:
                    old_desc = (
                        f"{ov.period_start} – {ov.period_end}: "
                        f"${ov.amount:,.2f} ({ov.mode})"
                    )
                    session.delete(ov)
            log_audit(
                "delete", "override",
                self._account.account_number if self._account else "",
                f"Deleted prior year override",
                old_value=old_desc,
            )
            self._rebuild()

    def _assign_structure(self):
        if not self._account:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        with get_session() as session:
            structures = session.query(RebateStructure).all()
            names = [s.name for s in structures]
            ids = [s.id for s in structures]

        if not structures:
            QMessageBox.information(
                self, "No Structures",
                "No rebate structures exist yet. Create one in the Rebate Structures view."
            )
            return

        from PyQt6.QtWidgets import QInputDialog
        chosen, ok = QInputDialog.getItem(
            self, "Assign Rebate Structure",
            "Select structure:", names, 0, False
        )
        if ok and chosen:
            struct_id = ids[names.index(chosen)]
            old_struct_name = None
            with get_session() as session:
                existing = (
                    session.query(AccountRebateAssignment)
                    .filter_by(account_number=self._account.account_number)
                    .first()
                )
                if existing:
                    # Capture old structure name for audit
                    old_s = session.query(RebateStructure).filter_by(id=existing.rebate_structure_id).first()
                    old_struct_name = old_s.name if old_s else str(existing.rebate_structure_id)
                    existing.rebate_structure_id = struct_id
                else:
                    session.add(
                        AccountRebateAssignment(
                            account_number=self._account.account_number,
                            rebate_structure_id=struct_id,
                        )
                    )
            log_audit(
                "assign", "rebate_structure", self._account.account_number,
                f"Assigned rebate structure '{chosen}' to {self._account.account_number}",
                old_value=old_struct_name,
                new_value=chosen,
            )
            self._rebuild()

    def _customize_rebate(self):
        """Open the StructureDialog to create/edit a per-account custom rebate."""
        if not self._account:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        from ui.views.rebate_structures_view import StructureDialog

        structure = self._detail_data.get("structure") if self._detail_data else None
        is_custom = self._detail_data.get("structure_is_custom", False) if self._detail_data else False

        # Build a proxy for the dialog pre-populated with the current structure's data
        class _Proxy:
            pass

        proxy = None
        if structure:
            proxy = _Proxy()
            proxy.name = (
                structure.name if is_custom
                else f"Custom: {self._account.account_name or self._account.account_number}"
            )
            proxy.description = getattr(structure, "description", "") or ""
            proxy.include_dir = getattr(structure, "include_dir", False)
            proxy.include_041 = getattr(structure, "include_041", False)
            _tiers = structure.get_tiers()
            proxy.get_tiers = lambda: _tiers
        else:
            proxy = _Proxy()
            proxy.name = f"Custom: {self._account.account_name or self._account.account_number}"
            proxy.description = ""
            proxy.include_dir = False
            proxy.include_041 = False
            proxy.get_tiers = lambda: []

        dlg = StructureDialog(existing=proxy, parent=self)
        dlg.setWindowTitle(
            f"{'Edit' if is_custom else 'Customize'} Rebate: "
            f"{self._account.account_name or self._account.account_number}"
        )

        if not dlg.exec():
            return

        data = dlg.get_data()

        with get_session() as session:
            if is_custom and structure:
                # Update the existing custom structure in-place
                custom = session.query(RebateStructure).filter_by(id=structure.id).first()
                if custom and not custom.is_template:
                    custom.name = data["name"]
                    custom.description = data["description"]
                    custom.include_dir = data.get("include_dir", False)
                    custom.include_041 = data.get("include_041", False)
                    custom.set_tiers(data["tiers"])
                    log_audit(
                        "edit", "rebate_structure", self._account.account_number,
                        f"Edited custom rebate for {self._account.account_number}",
                        new_value=data["name"],
                    )
            else:
                # Create a new custom structure and assign it to this account
                derived_from_id = structure.id if structure else None
                custom = RebateStructure(
                    name=data["name"],
                    structure_type=data.get("structure_type", "tiered"),
                    description=data.get("description", ""),
                    is_template=False,
                    include_dir=data.get("include_dir", False),
                    include_041=data.get("include_041", False),
                    derived_from_id=derived_from_id,
                )
                custom.set_tiers(data["tiers"])
                session.add(custom)
                session.flush()  # get the new ID

                existing_assignment = (
                    session.query(AccountRebateAssignment)
                    .filter_by(account_number=self._account.account_number)
                    .first()
                )
                if existing_assignment:
                    existing_assignment.rebate_structure_id = custom.id
                else:
                    session.add(AccountRebateAssignment(
                        account_number=self._account.account_number,
                        rebate_structure_id=custom.id,
                    ))
                log_audit(
                    "assign", "rebate_structure", self._account.account_number,
                    f"Created custom rebate for {self._account.account_number}",
                    new_value=data["name"],
                )
        self._rebuild()

    def _reset_custom_rebate(self):
        """Remove the per-account custom rebate and revert to the derived template."""
        if not self._account or not self._detail_data:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return

        structure = self._detail_data.get("structure")
        derived_from_id = self._detail_data.get("structure_derived_from_id")

        if not structure or structure.is_template:
            return

        msg = (
            "Remove the custom rebate for this account and revert to the original template?"
            if derived_from_id
            else "Remove the custom rebate for this account? The account will have no structure assigned."
        )
        if (
            QMessageBox.question(self, "Reset to Template", msg)
            != QMessageBox.StandardButton.Yes
        ):
            return

        with get_session() as session:
            assignment = (
                session.query(AccountRebateAssignment)
                .filter_by(account_number=self._account.account_number)
                .first()
            )
            if derived_from_id:
                if assignment:
                    assignment.rebate_structure_id = derived_from_id
            else:
                if assignment:
                    session.delete(assignment)

            custom = session.query(RebateStructure).filter_by(id=structure.id).first()
            if custom and not custom.is_template:
                session.delete(custom)

        log_audit(
            "edit", "rebate_structure", self._account.account_number,
            f"Removed custom rebate for {self._account.account_number}",
        )
        self._rebuild()


# ---------------------------------------------------------------------------
# Main Accounts view
# ---------------------------------------------------------------------------

class AccountsView(QWidget):
    def __init__(self, start: date, end: date, parent=None):
        super().__init__(parent)
        self._start = start
        self._end = end
        self._build_ui()
        self._load_accounts()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left panel ────────────────────────────────────────────────
        left = QFrame()
        left.setFixedWidth(320)
        left.setProperty("class", "left-panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Accounts"))
        hdr.addStretch()
        btn_add = QPushButton("+ Add")
        btn_add.setProperty("class", "primary")
        btn_add.clicked.connect(self._add_account_dialog)
        hdr.addWidget(btn_add)
        left_layout.addLayout(hdr)

        # Search
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search accounts…")
        self.search_box.textChanged.connect(self._filter_list)
        left_layout.addWidget(self.search_box)

        # Show closed accounts toggle
        self.chk_show_closed = QCheckBox("Show closed accounts")
        self.chk_show_closed.setChecked(False)
        self.chk_show_closed.setProperty("class", "muted")
        self.chk_show_closed.toggled.connect(self._on_show_closed_toggled)
        left_layout.addWidget(self.chk_show_closed)

        # Account list
        self.account_list = QListWidget()
        self.account_list.setAlternatingRowColors(False)
        self.account_list.currentItemChanged.connect(self._on_account_selected)
        left_layout.addWidget(self.account_list)

        # Remove button
        btn_remove = QPushButton("Remove Selected")
        btn_remove.setProperty("class", "danger")
        btn_remove.clicked.connect(self._remove_account)
        left_layout.addWidget(btn_remove)

        root.addWidget(left)

        # ── Right panel ───────────────────────────────────────────────
        self.detail_panel = AccountDetailPanel()
        root.addWidget(self.detail_panel)

    def _load_accounts(self):
        show_closed = getattr(self, "chk_show_closed", None) and self.chk_show_closed.isChecked()
        with get_session() as session:
            if show_closed:
                # Active accounts + CLSD-marked inactive accounts
                active = session.query(Account).filter_by(is_active=True).all()
                closed = (
                    session.query(Account)
                    .filter(
                        Account.is_active == False,
                        Account.account_name.ilike("*CLSD*%"),
                    )
                    .all()
                )
                accounts = active + closed
            else:
                accounts = session.query(Account).filter_by(is_active=True).all()
            programs = {p.id: p.bccode or "" for p in session.query(MarketingProgram).all()}

        # Build program lookup: account_number -> bccode
        self._program_map: dict[str, str] = {
            a.account_number: programs.get(a.marketing_program_id, "")
            for a in accounts
        }

        # Sort by days until next rebate year anniversary (soonest renewals at top)
        self._accounts = sorted(accounts, key=lambda a: _days_to_next_anniversary(a.start_date))
        self._populate_list(self._accounts)

    def _on_show_closed_toggled(self, checked: bool):
        self._load_accounts()

    def _populate_list(self, accounts: list):
        self.account_list.clear()
        program_map = getattr(self, "_program_map", {})
        for acct in accounts:
            bccode = program_map.get(acct.account_number, "")
            is_closed = not acct.is_active
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, acct.account_number)
            item.setSizeHint(QSize(0, 74))
            widget = AccountGalleryItem(acct, bccode, closed=is_closed)
            self.account_list.addItem(item)
            self.account_list.setItemWidget(item, widget)
        # Kick off background loader to fill mini progress bars
        self._start_gallery_loader(accounts)

    def _start_gallery_loader(self, accounts: list):
        self._gallery_loader = GalleryLoader(accounts, self._end, self)
        self._gallery_loader.ready.connect(self._on_gallery_loaded)
        self._gallery_loader.start()

    def _on_gallery_loaded(self, data: dict):
        for i in range(self.account_list.count()):
            item = self.account_list.item(i)
            widget = self.account_list.itemWidget(item)
            if isinstance(widget, AccountGalleryItem):
                acct_no = item.data(Qt.ItemDataRole.UserRole)
                if acct_no in data:
                    d = data[acct_no]
                    widget.update_tier_data(d["tiers"], d["current"], d["projected"], d.get("prior_year", 0.0))

    def _filter_list(self, text: str):
        text = text.lower()
        filtered = [
            a for a in self._accounts
            if text in (a.account_name or "").lower()
            or text in a.account_number.lower()
            or text in getattr(self, "_program_map", {}).get(a.account_number, "").lower()
        ]
        self._populate_list(filtered)

    def _on_account_selected(self, current, previous):
        if not current:
            return
        acct_no = current.data(Qt.ItemDataRole.UserRole)
        acct = next((a for a in self._accounts if a.account_number == acct_no), None)
        if acct:
            self.detail_panel.load_account(acct, self._start, self._end)

    def _add_account_dialog(self):
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        dlg = AddAccountDialog(self)
        if not dlg.exec():
            return
        data = dlg.get_data()

        if data["bccode"]:
            # Add marketing program
            with get_session() as session:
                existing = (
                    session.query(MarketingProgram)
                    .filter_by(bccode=data["bccode"])
                    .first()
                )
                if existing:
                    QMessageBox.information(
                        self, "Already Tracked",
                        f"Marketing program {data['bccode']} is already being tracked."
                    )
                    return
                session.add(
                    MarketingProgram(
                        bccode=data["bccode"],
                        name=data["mp_name"] or data["bccode"],
                    )
                )
            QMessageBox.information(
                self, "Program Added",
                f"Marketing program {data['bccode']} added.\n"
                "Run a data refresh to populate accounts."
            )
        elif data["account_number"]:
            reactivated = False
            with get_session() as session:
                existing = (
                    session.query(Account)
                    .filter_by(account_number=data["account_number"])
                    .first()
                )
                if existing:
                    if existing.is_active:
                        QMessageBox.information(
                            self, "Already Tracked",
                            f"Account {data['account_number']} is already tracked."
                        )
                        return
                    else:
                        # Reactivate previously removed account
                        existing.is_active = True
                        existing.start_date = data["start_date"]
                        reactivated = True
                else:
                    session.add(
                        Account(
                            account_number=data["account_number"],
                            source="manual",
                            start_date=data["start_date"],
                            is_active=True,
                        )
                    )
            # Immediately try to fetch account name (BNAME) from BILLTO
            try:
                from db.sync import sync_account_info
                sync_account_info([data["account_number"]])
            except Exception:
                pass  # Non-fatal; run a full sync to populate name

            if reactivated:
                log_audit(
                    "reactivate", "account", data["account_number"],
                    f"Reactivated account {data['account_number']} with start date {data['start_date'].isoformat()}",
                    new_value=data["start_date"].isoformat(),
                )
                QMessageBox.information(
                    self, "Account Reactivated",
                    f"Account {data['account_number']} has been reactivated.\n"
                    "Run a data refresh to load sales data."
                )
            else:
                log_audit(
                    "add", "account", data["account_number"],
                    f"Added account {data['account_number']} with start date {data['start_date'].isoformat()}",
                    new_value=data["start_date"].isoformat(),
                )
                QMessageBox.information(
                    self, "Account Added",
                    f"Account {data['account_number']} added.\n"
                    "Run a data refresh to load sales data."
                )
        else:
            QMessageBox.warning(self, "Invalid Input", "Please enter an account number or BCCODE.")
            return

        self._load_accounts()

    def _remove_account(self):
        item = self.account_list.currentItem()
        if not item:
            return
        from ui.admin_state import require_admin
        if not require_admin(self):
            return
        acct_no = item.data(Qt.ItemDataRole.UserRole)
        if (
            QMessageBox.question(
                self,
                "Remove Account",
                f"Remove account {acct_no}? Data will be retained in the local database.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            with get_session() as session:
                acct = session.query(Account).filter_by(account_number=acct_no).first()
                if acct:
                    acct.is_active = False
            log_audit(
                "remove", "account", acct_no,
                f"Removed account {acct_no} from tracking (data retained)",
            )
            self._load_accounts()

    def set_date_range(self, start: date, end: date):
        self._start = start
        self._end = end
        # Refresh current detail
        if self.account_list.currentItem():
            acct_no = self.account_list.currentItem().data(Qt.ItemDataRole.UserRole)
            acct = next((a for a in self._accounts if a.account_number == acct_no), None)
            if acct:
                self.detail_panel.load_account(acct, start, end)

    def refresh(self):
        self._load_accounts()
