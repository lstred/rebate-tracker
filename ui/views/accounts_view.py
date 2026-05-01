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
from datetime import date
from typing import Optional

from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
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
# Account detail loader (background)
# ---------------------------------------------------------------------------

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

        # Use account start_date for rebate period; monthly chart uses same account period
        effective_start, effective_end = get_account_period(self.account, self._end)
        prior_start, prior_end = get_prior_year_period(effective_start, effective_end)
        current_sales = get_period_sales(
            self.account.account_number, effective_start, effective_end
        )
        prior_sales = get_period_sales(
            self.account.account_number, prior_start, prior_end
        )
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

        info_layout.addLayout(name_col, stretch=3)

        # Source badge + start date + edit button
        src_col = QVBoxLayout()
        src_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        src_badge = QLabel("Marketing Program" if a.source == "marketing_program" else "Manual")
        src_badge.setProperty("class", "tag-success" if a.source == "marketing_program" else "tag-warning")
        src_col.addWidget(src_badge)

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

        # Structure label row
        struct_row = QHBoxLayout()
        struct_name_lbl = QLabel(
            f"Rebate Structure: <b>{structure.name if structure else '(none assigned)'}</b>"
        )
        struct_name_lbl.setTextFormat(Qt.TextFormat.RichText)
        struct_row.addWidget(struct_name_lbl)
        struct_row.addStretch()
        btn_assign = QPushButton("Assign Structure")
        btn_assign.setProperty("class", "primary")
        btn_assign.clicked.connect(lambda: self._assign_structure())
        struct_row.addWidget(btn_assign)
        rebate_layout.addLayout(struct_row)

        # KPI row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)

        def mini_kpi(label, value, color=C["text"]):
            f = QFrame()
            f.setStyleSheet(
                f"background:{C['surface2']}; border-radius:6px; padding:4px;"
            )
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

        # Tier progress bar (visual only)
        if rr and structure:
            tiers_raw = structure.get_tiers()
            if tiers_raw:
                sorted_tiers = sorted(tiers_raw, key=lambda x: x.get("threshold", 0))
                max_threshold = sorted_tiers[-1].get("threshold", 1)
                eval_sales = (
                    rr.growth_amount if structure.structure_type == "growth"
                    else rr.current_sales
                )
                pct = min(100, int(eval_sales / max(max_threshold, 1) * 100))

                prog_label = QLabel(
                    f"{'Growth' if structure.structure_type == 'growth' else 'Sales'} progress"
                    f" toward Tier {len(sorted_tiers)}  —  "
                    f"${eval_sales:,.0f} of ${max_threshold:,.0f}"
                )
                prog_label.setStyleSheet(f"color: {C['text_muted']}; font-size:10px;")
                rebate_layout.addWidget(prog_label)

                prog_bar = QProgressBar()
                prog_bar.setValue(pct)
                prog_bar.setTextVisible(True)
                prog_bar.setFormat(f"{pct}%")
                rebate_layout.addWidget(prog_bar)

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
        monthly_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        monthly_tbl.setAlternatingRowColors(True)
        monthly_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        monthly_tbl.verticalHeader().setVisible(False)
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
            tier_tbl.setAlternatingRowColors(True)
            tier_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tier_tbl.verticalHeader().setVisible(False)
            for tr in rr.tier_results:
                row = tier_tbl.rowCount()
                tier_tbl.insertRow(row)
                tier_tbl.setItem(row, 0, QTableWidgetItem(
                    f"Tier {tr.tier_number} (${tr.threshold:,.0f}+, {tr.mode.replace('_',' ')})"
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

    def _add_override(self):
        if not self._account:
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
        left.setFixedWidth(280)
        left.setStyleSheet(
            f"background-color: {C['surface']}; border-right: 1px solid {C['border']};"
        )
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

        # Account list
        self.account_list = QListWidget()
        self.account_list.setAlternatingRowColors(True)
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
        with get_session() as session:
            self._accounts = (
                session.query(Account).filter_by(is_active=True)
                .order_by(Account.account_name)
                .all()
            )
        self._populate_list(self._accounts)

    def _populate_list(self, accounts: list[Account]):
        self.account_list.clear()
        for acct in accounts:
            # Always show account number first, name below (avoids duplication when name is null)
            if acct.account_name:
                label = f"{acct.account_number}\n{acct.account_name}"
            else:
                label = acct.account_number
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, acct.account_number)
            self.account_list.addItem(item)

    def _filter_list(self, text: str):
        text = text.lower()
        filtered = [
            a for a in self._accounts
            if text in (a.account_name or "").lower()
            or text in a.account_number.lower()
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
