"""
ui/views/rebate_structures_view.py
-----------------------------------
Create, edit, and manage rebate structure templates.
Apply structures to individual accounts or entire marketing programs.

Structure types
---------------
'tiered'  — tiers evaluated against total period sales
'growth'  — tiers evaluated against growth amount (current − prior year)

Each tier has:
  threshold  : $ amount (or growth $) at which tier activates
  rate       : rebate percentage (entered as %, stored as decimal)
  mode       : 'dollar_one'  — when reached, rate applies to ALL sales
               'forward_only' — rate only on sales above threshold
"""

from __future__ import annotations

import json
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)

from db.local_db import (
    Account,
    AccountRebateAssignment,
    MarketingProgram,
    RebateStructure,
    get_session,
)
from ui.theme import C


# ---------------------------------------------------------------------------
# Tier editor widget
# ---------------------------------------------------------------------------

# Columns: # | Threshold | Applies To | Rate % | Mode | Delete
_COL_NUM   = 0
_COL_THRESH = 1
_COL_APPLIES = 2
_COL_RATE   = 3
_COL_MODE   = 4
_COL_DEL    = 5

_APPLIES_OPTIONS = [
    ("Sales (total)",       "sales"),
    ("Growth (vs prior yr)", "growth"),
    ("Freight %",            "freight"),
]
_APPLIES_LABELS = [o[0] for o in _APPLIES_OPTIONS]
_APPLIES_VALUES = [o[1] for o in _APPLIES_OPTIONS]

_MODE_BY_TYPE = {
    "sales":   ["Dollar One (all sales)",  "Forward Only (incremental)"],
    "growth":  ["Dollar One (all growth)", "Forward Only (incremental)"],
    "freight": ["Qualifies at threshold"],
}


class TierEditorWidget(QWidget):
    """Editable table of tiers — each tier specifies what it applies to."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        lbl = QLabel("Rebate Tiers")
        lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        hdr.addWidget(lbl)
        hdr.addStretch()
        btn_add = QPushButton("+ Add Tier")
        btn_add.setProperty("class", "primary")
        btn_add.clicked.connect(self._add_tier)
        hdr.addWidget(btn_add)
        layout.addLayout(hdr)

        help_lbl = QLabel(
            "Sales: rebate on total sales.   "
            "Growth: rebate on (current − prior year) once prior-year sales threshold is met.   "
            "Freight: % of freight returned at threshold (informational — no $ calc).\n"
            "Dollar One: rate applies to ALL qualifying sales.   "
            "Forward Only: rate applies only on qualifying sales above this threshold."
        )
        help_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        help_lbl.setWordWrap(True)
        layout.addWidget(help_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["#", "Threshold ($)", "Applies To", "Rate (%)", "Mode", ""]
        )
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(_COL_NUM,     QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(_COL_THRESH,  QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(_COL_APPLIES, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(_COL_RATE,    QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(_COL_MODE,    QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_DEL,     QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(_COL_NUM,     42)
        self.table.setColumnWidth(_COL_THRESH, 130)
        self.table.setColumnWidth(_COL_APPLIES,155)
        self.table.setColumnWidth(_COL_RATE,   105)
        self.table.setColumnWidth(_COL_DEL,     52)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        layout.addWidget(self.table)

    def _add_tier(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._setup_row(row, row + 1, 0.0, "sales", 0.0, "dollar_one")

    def _setup_row(
        self, row: int, tier_num: int,
        threshold: float, applies_to: str,
        rate: float, mode: str,
    ):
        self.table.setItem(row, _COL_NUM, self._ro_item(str(tier_num)))

        # Threshold
        thresh_spin = QDoubleSpinBox()
        thresh_spin.setPrefix("$")
        thresh_spin.setMaximum(999_999_999.0)
        thresh_spin.setDecimals(0)
        thresh_spin.setSingleStep(1000)
        thresh_spin.setValue(threshold)
        thresh_spin.setStyleSheet(f"background:{C['surface2']}; padding:2px 4px;")
        self.table.setCellWidget(row, _COL_THRESH, thresh_spin)

        # Applies To
        applies_combo = QComboBox()
        applies_combo.addItems(_APPLIES_LABELS)
        idx = _APPLIES_VALUES.index(applies_to) if applies_to in _APPLIES_VALUES else 0
        applies_combo.setCurrentIndex(idx)
        applies_combo.setStyleSheet(f"background:{C['surface2']};")
        applies_combo.currentIndexChanged.connect(
            lambda _, r=row: self._on_applies_changed(r)
        )
        self.table.setCellWidget(row, _COL_APPLIES, applies_combo)

        # Rate
        rate_spin = QDoubleSpinBox()
        rate_spin.setSuffix(" %")
        rate_spin.setMaximum(100.0)
        rate_spin.setDecimals(3)
        rate_spin.setSingleStep(0.5)
        rate_spin.setValue(rate * 100)
        rate_spin.setStyleSheet(f"background:{C['surface2']}; padding:2px 4px;")
        self.table.setCellWidget(row, _COL_RATE, rate_spin)

        # Mode
        mode_combo = QComboBox()
        mode_options = _MODE_BY_TYPE.get(applies_to, _MODE_BY_TYPE["sales"])
        mode_combo.addItems(mode_options)
        if applies_to == "freight":
            mode_combo.setCurrentIndex(0)
            mode_combo.setEnabled(False)
        else:
            mode_combo.setCurrentIndex(0 if mode == "dollar_one" else 1)
        mode_combo.setStyleSheet(f"background:{C['surface2']};")
        self.table.setCellWidget(row, _COL_MODE, mode_combo)

        # Delete
        del_btn = QPushButton("✕")
        del_btn.setProperty("class", "danger")
        del_btn.setFixedSize(34, 28)
        del_btn.clicked.connect(self._delete_row_by_widget)
        self.table.setCellWidget(row, _COL_DEL, del_btn)

    def _on_applies_changed(self, row: int):
        applies_combo = self.table.cellWidget(row, _COL_APPLIES)
        mode_combo    = self.table.cellWidget(row, _COL_MODE)
        if not applies_combo or not mode_combo:
            return
        applies_to = _APPLIES_VALUES[applies_combo.currentIndex()]
        mode_options = _MODE_BY_TYPE.get(applies_to, _MODE_BY_TYPE["sales"])
        mode_combo.blockSignals(True)
        mode_combo.clear()
        mode_combo.addItems(mode_options)
        mode_combo.setEnabled(applies_to != "freight")
        mode_combo.blockSignals(False)

    def _delete_row_by_widget(self):
        sender = self.sender()
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, _COL_DEL) is sender:
                self.table.removeRow(row)
                break
        self._refresh_tier_numbers()

    def _refresh_tier_numbers(self):
        for row in range(self.table.rowCount()):
            self.table.setItem(row, _COL_NUM, self._ro_item(str(row + 1)))

    @staticmethod
    def _ro_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def get_tiers(self) -> list[dict]:
        tiers = []
        for row in range(self.table.rowCount()):
            thresh      = self.table.cellWidget(row, _COL_THRESH)
            applies_cb  = self.table.cellWidget(row, _COL_APPLIES)
            rate        = self.table.cellWidget(row, _COL_RATE)
            mode_combo  = self.table.cellWidget(row, _COL_MODE)
            if not (thresh and applies_cb and rate and mode_combo):
                continue
            applies_to = _APPLIES_VALUES[applies_cb.currentIndex()]
            if applies_to == "freight":
                mode_val = "dollar_one"
            else:
                mode_val = "dollar_one" if mode_combo.currentIndex() == 0 else "forward_only"
            tiers.append({
                "threshold":  thresh.value(),
                "applies_to": applies_to,
                "rate":       round(rate.value() / 100, 6),
                "mode":       mode_val,
            })
        return sorted(tiers, key=lambda t: t["threshold"])

    def set_tiers(self, tiers: list[dict]):
        self.table.setRowCount(0)
        for i, t in enumerate(sorted(tiers, key=lambda x: x.get("threshold", 0))):
            self.table.insertRow(i)
            self._setup_row(
                i, i + 1,
                float(t.get("threshold", 0)),
                t.get("applies_to", "sales"),
                float(t.get("rate", 0)),
                t.get("mode", "dollar_one"),
            )


# ---------------------------------------------------------------------------
# Structure edit dialog
# ---------------------------------------------------------------------------

class StructureDialog(QDialog):
    def __init__(self, existing: Optional[RebateStructure] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Rebate Structure" if existing else "New Rebate Structure")
        self.setMinimumSize(960, 660)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(f"background-color: {C['surface']}; color: {C['text']};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 22, 28, 16)
        layout.setSpacing(16)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        # Name + description
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_input = QLineEdit(existing.name if existing else "")
        self.name_input.setPlaceholderText("e.g. Standard Growth 2026")
        self.name_input.setMinimumHeight(32)
        form.addRow("Name:", self.name_input)

        self.desc_input = QLineEdit(existing.description or "" if existing else "")
        self.desc_input.setPlaceholderText("Optional description")
        self.desc_input.setMinimumHeight(32)
        form.addRow("Description:", self.desc_input)

        layout.addLayout(form)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{C['border']};")
        layout.addWidget(sep)

        # Eligibility overrides
        elig_frame = QFrame()
        elig_frame.setStyleSheet(
            f"background:{C['surface2']}; border-radius:6px; padding:4px;"
        )
        elig_layout = QVBoxLayout(elig_frame)
        elig_layout.setContentsMargins(16, 12, 16, 12)
        elig_layout.setSpacing(6)

        elig_title = QLabel("Rebate Eligibility Overrides")
        elig_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        elig_layout.addWidget(elig_title)

        elig_desc = QLabel(
            "By default, Direct-Ship (DIR) and Unfinished Wood (041) orders count toward "
            "tier thresholds but are excluded from rebate calculations. "
            "Enable below to include them."
        )
        elig_desc.setWordWrap(True)
        elig_desc.setStyleSheet(f"color:{C['text_muted']}; font-size:10px;")
        elig_layout.addWidget(elig_desc)

        self.chk_dir = QCheckBox("Include Direct-Ship (DIR) orders in rebate calculations")
        self.chk_041 = QCheckBox("Include Unfinished Wood (Cost Center 041) in rebate calculations")
        self.chk_dir.setChecked(getattr(existing, "include_dir", False) if existing else False)
        self.chk_041.setChecked(getattr(existing, "include_041", False) if existing else False)
        elig_layout.addWidget(self.chk_dir)
        elig_layout.addWidget(self.chk_041)
        layout.addWidget(elig_frame)

        # Tier editor
        self.tier_editor = TierEditorWidget()
        if existing:
            self.tier_editor.set_tiers(existing.get_tiers())
        else:
            self.tier_editor.table.insertRow(0)
            self.tier_editor._setup_row(0, 1, 0.0, "sales", 0.01, "dollar_one")
        layout.addWidget(self.tier_editor)

        # Buttons (outside scroll)
        btn_bar = QWidget()
        btn_bar.setStyleSheet(f"background:{C['surface']}; border-top:1px solid {C['border']};")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(28, 12, 28, 12)
        btn_layout.addStretch()
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setMinimumWidth(100)
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        btn_layout.addWidget(btns)
        root.addWidget(btn_bar)

    def _validate_and_accept(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Validation", "Please enter a structure name.")
            return
        if not self.tier_editor.get_tiers():
            QMessageBox.warning(self, "Validation", "Add at least one tier.")
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "structure_type": "tiered",   # type is now per-tier via applies_to
            "description": self.desc_input.text().strip(),
            "tiers": self.tier_editor.get_tiers(),
            "include_dir": self.chk_dir.isChecked(),
            "include_041": self.chk_041.isChecked(),
        }


# ---------------------------------------------------------------------------
# Apply-to dialog
# ---------------------------------------------------------------------------

class ApplyStructureDialog(QDialog):
    def __init__(self, structure: RebateStructure, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Apply: {structure.name}")
        self.setMinimumWidth(440)
        self.setStyleSheet(f"background-color: {C['surface']}; color: {C['text']};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel(f"Apply structure <b>{structure.name}</b> to:", self))

        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Individual Account", "Marketing Program (all members)"])
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        layout.addWidget(self.scope_combo)

        # Account selector
        self.acct_widget = QWidget()
        acct_form = QFormLayout(self.acct_widget)
        acct_form.setSpacing(8)
        self.acct_combo = QComboBox()
        with get_session() as session:
            accounts = session.query(Account).filter_by(is_active=True).order_by(Account.account_name).all()
            for a in accounts:
                self.acct_combo.addItem(a.display_name, a.account_number)
        acct_form.addRow("Account:", self.acct_combo)
        layout.addWidget(self.acct_widget)

        # MP selector
        self.mp_widget = QWidget()
        mp_form = QFormLayout(self.mp_widget)
        mp_form.setSpacing(8)
        self.mp_combo = QComboBox()
        with get_session() as session:
            programs = session.query(MarketingProgram).all()
            for p in programs:
                self.mp_combo.addItem(f"{p.name or p.bccode} ({p.bccode})", p.id)
        mp_form.addRow("Program:", self.mp_combo)
        layout.addWidget(self.mp_widget)
        self.mp_widget.hide()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_scope_changed(self, idx: int):
        self.acct_widget.setVisible(idx == 0)
        self.mp_widget.setVisible(idx == 1)

    def get_data(self) -> dict:
        return {
            "scope": "account" if self.scope_combo.currentIndex() == 0 else "program",
            "account_number": self.acct_combo.currentData(),
            "program_id": self.mp_combo.currentData(),
        }


# ---------------------------------------------------------------------------
# Rebate Structures view
# ---------------------------------------------------------------------------

class RebateStructuresView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_structures()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left panel ────────────────────────────────────────────────
        left = QFrame()
        left.setFixedWidth(300)
        left.setProperty("class", "left-panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Structures / Templates"))
        hdr.addStretch()
        btn_new = QPushButton("+ New")
        btn_new.setProperty("class", "primary")
        btn_new.clicked.connect(self._new_structure)
        hdr.addWidget(btn_new)
        left_layout.addLayout(hdr)

        self.struct_list = QListWidget()
        self.struct_list.currentItemChanged.connect(self._on_structure_selected)
        left_layout.addWidget(self.struct_list)

        left_layout.addWidget(self._make_sep())

        btn_edit = QPushButton("Edit Selected")
        btn_edit.clicked.connect(self._edit_structure)
        left_layout.addWidget(btn_edit)

        btn_apply = QPushButton("Apply to Account / Program")
        btn_apply.setProperty("class", "success")
        btn_apply.clicked.connect(self._apply_structure)
        left_layout.addWidget(btn_apply)

        btn_del = QPushButton("Delete Selected")
        btn_del.setProperty("class", "danger")
        btn_del.clicked.connect(self._delete_structure)
        left_layout.addWidget(btn_del)

        root.addWidget(left)

        # ── Right panel ───────────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(24, 20, 24, 20)
        right_layout.setSpacing(14)

        heading = QLabel("Rebate Structures")
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        right_layout.addWidget(heading)

        desc = QLabel(
            "Define and save rebate structure templates.\n"
            "Apply them to individual accounts or entire marketing programs."
        )
        desc.setStyleSheet(f"color: {C['text_muted']};")
        desc.setWordWrap(True)
        right_layout.addWidget(desc)

        # Detail area
        self.detail_frame = QFrame()
        self.detail_frame.setProperty("class", "card")
        self.detail_layout = QVBoxLayout(self.detail_frame)
        self.detail_layout.setContentsMargins(16, 14, 16, 14)
        self._lbl_select = QLabel("Select a structure from the list to view its details.")
        self._lbl_select.setStyleSheet(f"color: {C['text_muted']};")
        self._lbl_select.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_layout.addWidget(self._lbl_select)
        right_layout.addWidget(self.detail_frame)

        # Assignments table
        asgn_lbl = QLabel("Assignments")
        asgn_lbl.setStyleSheet(
            f"color:{C['text_muted']}; font-size:11px; font-weight:bold;"
        )
        right_layout.addWidget(asgn_lbl)

        self.assign_tbl = QTableWidget(0, 3)
        self.assign_tbl.setHorizontalHeaderLabels(["Account #", "Account Name", "Effective Date"])
        self.assign_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.assign_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.assign_tbl.setAlternatingRowColors(True)
        self.assign_tbl.verticalHeader().setVisible(False)
        self.assign_tbl.setMaximumHeight(200)
        right_layout.addWidget(self.assign_tbl)

        right_layout.addStretch()
        root.addWidget(right)

    @staticmethod
    def _make_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C['border']};")
        return sep

    # ------------------------------------------------------------------

    def _load_structures(self):
        with get_session() as session:
            structures = (
                session.query(RebateStructure).order_by(RebateStructure.name).all()
            )
            self._structures = structures
        self.struct_list.clear()
        for s in structures:
            type_tag = "📈" if s.structure_type == "growth" else "🏆"
            item = QListWidgetItem(f"{type_tag}  {s.name}")
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            self.struct_list.addItem(item)

    def _on_structure_selected(self, current, _previous):
        if not current:
            return
        struct_id = current.data(Qt.ItemDataRole.UserRole)
        self._show_detail(struct_id)

    def _show_detail(self, struct_id: int):
        with get_session() as session:
            struct = session.query(RebateStructure).filter_by(id=struct_id).first()
            assignments = (
                session.query(AccountRebateAssignment)
                .filter_by(rebate_structure_id=struct_id)
                .all()
            )
            acct_map = {
                a.account_number: a
                for a in session.query(Account).filter_by(is_active=True).all()
            }

        if not struct:
            return

        # Clear detail frame
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        type_label = (
            "Growth-Based (applies to growth amount)"
            if struct.structure_type == "growth"
            else "Tiered (applies to total sales)"
        )
        self.detail_layout.addWidget(
            self._kv_label("Name", struct.name)
        )
        self.detail_layout.addWidget(self._kv_label("Type", type_label))
        if struct.description:
            self.detail_layout.addWidget(self._kv_label("Description", struct.description))

        # Tiers summary
        tiers = struct.get_tiers()
        _applies_display = {"sales": "Sales", "growth": "Growth", "freight": "Freight %"}
        if tiers:
            tier_tbl = QTableWidget(len(tiers), 4)
            tier_tbl.setHorizontalHeaderLabels(["Threshold", "Applies To", "Rate", "Mode"])
            tier_tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            tier_tbl.verticalHeader().setVisible(False)
            tier_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tier_tbl.verticalHeader().setDefaultSectionSize(30)
            tier_tbl.setMaximumHeight(36 * (len(tiers) + 2))
            for i, t in enumerate(tiers):
                applies = t.get("applies_to", "sales")
                mode_str = "Qualifies" if applies == "freight" else (
                    "Dollar One" if t.get("mode") == "dollar_one" else "Forward Only"
                )
                tier_tbl.setItem(i, 0, QTableWidgetItem(f"${float(t['threshold']):,.0f}"))
                tier_tbl.setItem(i, 1, QTableWidgetItem(_applies_display.get(applies, applies)))
                tier_tbl.setItem(i, 2, QTableWidgetItem(f"{float(t['rate'])*100:.2f}%"))
                tier_tbl.setItem(i, 3, QTableWidgetItem(mode_str))
            self.detail_layout.addWidget(tier_tbl)

        # Assignments table
        self.assign_tbl.setRowCount(0)
        for asgn in assignments:
            row = self.assign_tbl.rowCount()
            self.assign_tbl.insertRow(row)
            acct = acct_map.get(asgn.account_number)
            self.assign_tbl.setItem(row, 0, QTableWidgetItem(asgn.account_number))
            self.assign_tbl.setItem(row, 1, QTableWidgetItem(
                acct.account_name or "" if acct else ""
            ))
            eff = asgn.effective_date.strftime("%m/%d/%Y") if asgn.effective_date else "—"
            self.assign_tbl.setItem(row, 2, QTableWidgetItem(eff))

    @staticmethod
    def _kv_label(key: str, value: str) -> QLabel:
        lbl = QLabel(f"<b>{key}:</b>  {value}")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(f"color: {C['text']};")
        return lbl

    def _new_structure(self):
        dlg = StructureDialog(parent=self)
        if dlg.exec():
            data = dlg.get_data()
            with get_session() as session:
                struct = RebateStructure(
                    name=data["name"],
                    structure_type=data["structure_type"],
                    description=data["description"],
                    is_template=True,
                    include_dir=data.get("include_dir", False),
                    include_041=data.get("include_041", False),
                )
                struct.set_tiers(data["tiers"])
                session.add(struct)
            self._load_structures()

    def _edit_structure(self):
        item = self.struct_list.currentItem()
        if not item:
            return
        struct_id = item.data(Qt.ItemDataRole.UserRole)
        with get_session() as session:
            struct = session.query(RebateStructure).filter_by(id=struct_id).first()
            if not struct:
                return
            # Capture data before session closes
            snap = {
                "id": struct.id,
                "name": struct.name,
                "structure_type": struct.structure_type,
                "description": struct.description or "",
                "tiers": struct.get_tiers(),
                "include_dir": getattr(struct, "include_dir", False),
                "include_041": getattr(struct, "include_041", False),
            }

        # Build a lightweight proxy for the dialog
        class _Proxy:
            pass
        proxy = _Proxy()
        proxy.id = snap["id"]
        proxy.name = snap["name"]
        proxy.structure_type = snap["structure_type"]
        proxy.description = snap["description"]
        proxy.get_tiers = lambda: snap["tiers"]
        proxy.include_dir = snap["include_dir"]
        proxy.include_041 = snap["include_041"]

        dlg = StructureDialog(proxy, parent=self)
        if dlg.exec():
            data = dlg.get_data()
            with get_session() as session:
                struct = session.query(RebateStructure).filter_by(id=snap["id"]).first()
                if struct:
                    struct.name = data["name"]
                    struct.structure_type = data["structure_type"]
                    struct.description = data["description"]
                    struct.include_dir = data.get("include_dir", False)
                    struct.include_041 = data.get("include_041", False)
                    struct.set_tiers(data["tiers"])
        self._load_structures()

    def _delete_structure(self):
        item = self.struct_list.currentItem()
        if not item:
            return
        struct_id = item.data(Qt.ItemDataRole.UserRole)
        if (
            QMessageBox.question(
                self,
                "Delete Structure",
                "Delete this structure? Existing account assignments will also be removed.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            with get_session() as session:
                session.query(AccountRebateAssignment).filter_by(
                    rebate_structure_id=struct_id
                ).delete()
                session.query(RebateStructure).filter_by(id=struct_id).delete()
            self._load_structures()

    def _apply_structure(self):
        item = self.struct_list.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Select a structure first.")
            return
        struct_id = item.data(Qt.ItemDataRole.UserRole)
        with get_session() as session:
            struct = session.query(RebateStructure).filter_by(id=struct_id).first()
            if not struct:
                return
            dlg = ApplyStructureDialog(struct, parent=self)
            if dlg.exec():
                data = dlg.get_data()
                if data["scope"] == "account" and data["account_number"]:
                    _upsert_assignment(session, data["account_number"], struct_id)
                elif data["scope"] == "program" and data["program_id"]:
                    accounts = (
                        session.query(Account)
                        .filter_by(
                            marketing_program_id=data["program_id"],
                            is_active=True,
                        )
                        .all()
                    )
                    for acct in accounts:
                        _upsert_assignment(session, acct.account_number, struct_id)
                    QMessageBox.information(
                        self, "Applied",
                        f"Structure applied to {len(accounts)} account(s)."
                    )
        self._show_detail(struct_id)


def _upsert_assignment(session, account_number: str, struct_id: int):
    existing = (
        session.query(AccountRebateAssignment)
        .filter_by(account_number=account_number)
        .first()
    )
    if existing:
        existing.rebate_structure_id = struct_id
    else:
        session.add(
            AccountRebateAssignment(
                account_number=account_number,
                rebate_structure_id=struct_id,
            )
        )
