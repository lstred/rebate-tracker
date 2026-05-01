"""
ui/views/pdf_template_view.py
------------------------------
Design and preview PDF statement templates.  Users can set colors, company
info, logo, and toggle sections.  Batch-export PDFs for all accounts to a
chosen folder.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db.local_db import Account, AccountRebateAssignment, MarketingProgram, PdfTemplate, RebateStructure, get_session
from services.pdf_generator import batch_generate, generate_statement
from ui.theme import C


# ---------------------------------------------------------------------------
# Colour picker button
# ---------------------------------------------------------------------------

class ColorButton(QPushButton):
    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._color = color
        self._update_style()
        self.setFixedSize(36, 28)
        self.clicked.connect(self._pick)

    def _update_style(self):
        self.setStyleSheet(
            f"background-color: {self._color}; "
            f"border: 1px solid {C['border']}; border-radius: 4px;"
        )

    def _pick(self):
        color = QColorDialog.getColor(QColor(self._color), self)
        if color.isValid():
            self._color = color.name()
            self._update_style()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color
        self._update_style()


# ---------------------------------------------------------------------------
# Batch export worker
# ---------------------------------------------------------------------------

class BatchExportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, int)  # success, message, count

    def __init__(
        self,
        output_dir: str,
        period_start: date,
        period_end: date,
        template_config: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.output_dir = output_dir
        self.period_start = period_start
        self.period_end = period_end
        self.template_config = template_config

    def run(self):
        try:
            written = batch_generate(
                self.output_dir,
                self.period_start,
                self.period_end,
                self.template_config,
                progress_cb=lambda p, m: self.progress.emit(p, m),
            )
            self.finished.emit(True, f"Export complete.", len(written))
        except Exception as exc:
            self.finished.emit(False, str(exc), 0)


# ---------------------------------------------------------------------------
# Single / group export worker
# ---------------------------------------------------------------------------

class TargetedExportWorker(QThread):
    """Export PDFs for a specific account number or marketing program BCCODE."""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, int)

    def __init__(
        self,
        output_dir: str,
        period_end,
        template_config: dict,
        account_numbers: list[str],   # empty = batch all
        parent=None,
    ):
        super().__init__(parent)
        self.output_dir = output_dir
        self.period_end = period_end
        self.template_config = template_config
        self.account_numbers = account_numbers

    def run(self):
        import os
        from pathlib import Path
        from db.local_db import Account, AccountRebateAssignment, RebateStructure, get_session
        from services.rebate_calculator import get_account_period
        from services.pdf_generator import generate_statement

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        written = []

        with get_session() as session:
            accounts = (
                session.query(Account)
                .filter(
                    Account.is_active == True,
                    Account.account_number.in_(self.account_numbers),
                )
                .all()
            )
            assignments = {
                a.account_number: a
                for a in session.query(AccountRebateAssignment).all()
            }
            structures = {s.id: s for s in session.query(RebateStructure).all()}

        total = len(accounts)
        for i, acct in enumerate(accounts):
            if total:
                self.progress.emit(int(i / total * 100), f"Generating {acct.account_number}…")

            assignment = assignments.get(acct.account_number)
            if not assignment or assignment.rebate_structure_id not in structures:
                continue

            structure = structures[assignment.rebate_structure_id]
            period_start, period_end = get_account_period(acct, self.period_end)
            out_path = os.path.join(self.output_dir, f"{acct.account_number}.pdf")
            try:
                generate_statement(
                    acct, period_start, period_end,
                    self.template_config, structure,
                    output_path=out_path,
                )
                written.append(out_path)
            except Exception as exc:
                print(f"[PDF] Error generating {acct.account_number}: {exc}")

        self.finished.emit(True, "Export complete.", len(written))


# ---------------------------------------------------------------------------
# Email send worker (one account at a time)
# ---------------------------------------------------------------------------

class EmailSendWorker(QThread):
    """Generate a PDF (if needed) and email it to a single account."""

    finished = pyqtSignal(bool, str)   # success, message

    def __init__(
        self,
        account_number: str,
        to_email: str,
        to_name: str,
        pdf_dir: str,
        period_end,
        template_config: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.account_number = account_number
        self.to_email = to_email
        self.to_name = to_name
        self.pdf_dir = pdf_dir
        self.period_end = period_end
        self.template_config = template_config

    def run(self):
        import os
        from pathlib import Path
        from db.local_db import Account, AccountRebateAssignment, RebateStructure, get_session
        from services.rebate_calculator import get_account_period
        from services.pdf_generator import generate_statement
        from services.email_sender import send_statement_email

        Path(self.pdf_dir).mkdir(parents=True, exist_ok=True)
        pdf_path = os.path.join(self.pdf_dir, f"{self.account_number}.pdf")

        # Generate PDF if not already present
        try:
            with get_session() as session:
                acct = session.query(Account).filter_by(
                    account_number=self.account_number
                ).first()
                assignment = session.query(AccountRebateAssignment).filter_by(
                    account_number=self.account_number
                ).first()
                structure = (
                    session.query(RebateStructure).filter_by(
                        id=assignment.rebate_structure_id
                    ).first()
                    if assignment else None
                )
                acct_name = acct.account_name if acct else self.to_name

            if not acct or not structure:
                self.finished.emit(
                    False,
                    f"{self.account_number}: no account or rebate structure found."
                )
                return

            period_start, period_end = get_account_period(acct, self.period_end)
            generate_statement(
                acct, period_start, period_end,
                self.template_config, structure,
                output_path=pdf_path,
            )
        except Exception as exc:
            self.finished.emit(False, f"PDF generation failed: {exc}")
            return

        ok, msg = send_statement_email(
            to_email=self.to_email,
            to_name=self.to_name,
            account_number=self.account_number,
            pdf_path=pdf_path,
        )
        self.finished.emit(ok, msg)


# ---------------------------------------------------------------------------
# Template editor panel
# ---------------------------------------------------------------------------

class TemplateEditorPanel(QWidget):
    def __init__(self, template: PdfTemplate, parent=None):
        super().__init__(parent)
        self._template = template
        cfg = template.get_config()
        self._build_ui(cfg)

    def _build_ui(self, cfg: dict):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(16)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # ── Company Info ─────────────────────────────────────────────
        company_group = QGroupBox("Company Information")
        cg_layout = QFormLayout(company_group)
        cg_layout.setSpacing(8)

        self.company_name = QLineEdit(cfg.get("company_name", ""))
        cg_layout.addRow("Company Name:", self.company_name)

        self.header_text = QLineEdit(cfg.get("header_text", "Rebate Statement"))
        cg_layout.addRow("Statement Title:", self.header_text)

        self.footer_text = QTextEdit(cfg.get("footer_text", ""))
        self.footer_text.setMaximumHeight(60)
        cg_layout.addRow("Footer Text:", self.footer_text)

        layout.addWidget(company_group)

        # ── Logo ─────────────────────────────────────────────────────
        logo_group = QGroupBox("Logo")
        logo_layout = QHBoxLayout(logo_group)
        logo_layout.setSpacing(8)
        self.logo_path_lbl = QLabel(cfg.get("logo_path", "") or "No logo selected")
        self.logo_path_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size:11px;")
        self.logo_path_lbl.setWordWrap(True)
        logo_layout.addWidget(self.logo_path_lbl, stretch=1)
        btn_logo = QPushButton("Browse…")
        btn_logo.clicked.connect(self._browse_logo)
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._clear_logo)
        self._logo_path = cfg.get("logo_path", "")
        logo_layout.addWidget(btn_logo)
        logo_layout.addWidget(btn_clear)
        layout.addWidget(logo_group)

        # ── Colors ───────────────────────────────────────────────────
        color_group = QGroupBox("Color Scheme")
        color_form = QFormLayout(color_group)
        color_form.setSpacing(8)

        self.color_primary = ColorButton(cfg.get("primary_color", "#1a3a6e"))
        self.color_secondary = ColorButton(cfg.get("secondary_color", "#f5f5f5"))
        self.color_accent = ColorButton(cfg.get("accent_color", "#2ecc71"))

        for row_label, btn in [
            ("Primary (headers):", self.color_primary),
            ("Secondary (backgrounds):", self.color_secondary),
            ("Accent (rebate totals):", self.color_accent),
        ]:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(btn)
            row_layout.addStretch()
            color_form.addRow(row_label, row_widget)

        layout.addWidget(color_group)

        # ── Sections ─────────────────────────────────────────────────
        sections_group = QGroupBox("Statement Sections")
        sec_layout = QVBoxLayout(sections_group)
        self.chk_tiers = QCheckBox("Show Tier Breakdown")
        self.chk_tiers.setChecked(cfg.get("show_tier_breakdown", True))
        self.chk_monthly = QCheckBox("Show Monthly Sales Detail")
        self.chk_monthly.setChecked(cfg.get("show_monthly_sales", True))
        sec_layout.addWidget(self.chk_tiers)
        sec_layout.addWidget(self.chk_monthly)
        layout.addWidget(sections_group)

        layout.addStretch()

    def _browse_logo(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Logo", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff)"
        )
        if path:
            self._logo_path = path
            self.logo_path_lbl.setText(path)

    def _clear_logo(self):
        self._logo_path = ""
        self.logo_path_lbl.setText("No logo selected")

    def get_config(self) -> dict:
        return {
            "company_name": self.company_name.text().strip(),
            "header_text": self.header_text.text().strip(),
            "footer_text": self.footer_text.toPlainText().strip(),
            "logo_path": self._logo_path,
            "primary_color": self.color_primary.color(),
            "secondary_color": self.color_secondary.color(),
            "accent_color": self.color_accent.color(),
            "show_tier_breakdown": self.chk_tiers.isChecked(),
            "show_monthly_sales": self.chk_monthly.isChecked(),
            "paper_size": "letter",
        }

    def save(self):
        cfg = self.get_config()
        with get_session() as session:
            tmpl = session.query(PdfTemplate).filter_by(id=self._template.id).first()
            if tmpl:
                tmpl.set_config(cfg)
                tmpl.name = self.company_name.text().strip() or tmpl.name
        return cfg


# ---------------------------------------------------------------------------
# Main PDF template view
# ---------------------------------------------------------------------------

class PdfTemplateView(QWidget):
    def __init__(self, start: date, end: date, parent=None):
        super().__init__(parent)
        self._start = start
        self._end = end
        self._export_worker: Optional[BatchExportWorker] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        heading = QLabel("PDF Statement Templates")
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        root.addWidget(heading)

        desc = QLabel(
            "Customize the look of dealer rebate statements.  "
            "Save your template then export PDFs for all active accounts."
        )
        desc.setStyleSheet(f"color: {C['text_muted']};")
        desc.setWordWrap(True)
        root.addWidget(desc)

        # Splitter: template list | editor
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left: template list
        left = QFrame()
        left.setFixedWidth(240)
        left.setStyleSheet(
            f"background:{C['surface']}; border-right:1px solid {C['border']};"
        )
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Templates"))
        hdr.addStretch()
        btn_new = QPushButton("+ New")
        btn_new.clicked.connect(self._new_template)
        hdr.addWidget(btn_new)
        left_layout.addLayout(hdr)

        self.tmpl_list = QListWidget()
        self.tmpl_list.currentItemChanged.connect(self._on_template_selected)
        left_layout.addWidget(self.tmpl_list)

        btn_del = QPushButton("Delete")
        btn_del.setProperty("class", "danger")
        btn_del.clicked.connect(self._delete_template)
        left_layout.addWidget(btn_del)

        splitter.addWidget(left)

        # Right: editor area
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._editor_container = QWidget()
        self._editor_layout = QVBoxLayout(self._editor_container)
        self._editor_layout.setContentsMargins(0, 0, 0, 0)
        placeholder = QLabel("Select a template to edit.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet(f"color: {C['text_muted']};")
        self._editor_layout.addWidget(placeholder)
        self._current_editor: Optional[TemplateEditorPanel] = None

        right_layout.addWidget(self._editor_container, stretch=1)

        # Save button
        save_row = QHBoxLayout()
        self.btn_save = QPushButton("Save Template")
        self.btn_save.setProperty("class", "primary")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save_template)
        save_row.addWidget(self.btn_save)
        save_row.addStretch()
        right_layout.addLayout(save_row)

        splitter.addWidget(right)
        root.addWidget(splitter, stretch=1)

        # ── Export section ────────────────────────────────────────────
        export_frame = QFrame()
        export_frame.setProperty("class", "card")
        export_outer = QVBoxLayout(export_frame)
        export_outer.setContentsMargins(16, 12, 16, 12)
        export_outer.setSpacing(10)

        export_heading = QLabel("Generate PDF Statements")
        export_heading.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        export_outer.addWidget(export_heading)

        # Folder selector (shared across modes)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_lbl_prefix = QLabel("Output folder:")
        folder_lbl_prefix.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        folder_row.addWidget(folder_lbl_prefix)
        self.lbl_export_dir = QLabel("No folder selected")
        self.lbl_export_dir.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        folder_row.addWidget(self.lbl_export_dir, stretch=1)
        btn_dir = QPushButton("Choose Folder")
        btn_dir.clicked.connect(self._choose_export_dir)
        folder_row.addWidget(btn_dir)
        export_outer.addLayout(folder_row)

        # Three export buttons in a row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        # Single account
        single_col = QVBoxLayout()
        single_col.setSpacing(4)
        single_label = QLabel("Single Account")
        single_label.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        single_col.addWidget(single_label)
        self.single_acct_combo = QComboBox()
        self.single_acct_combo.setMinimumWidth(200)
        single_col.addWidget(self.single_acct_combo)
        self.btn_export_single = QPushButton("⬇  Generate Statement")
        self.btn_export_single.setProperty("class", "primary")
        self.btn_export_single.setEnabled(False)
        self.btn_export_single.clicked.connect(self._export_single)
        single_col.addWidget(self.btn_export_single)
        btn_row.addLayout(single_col)

        # Vertical separator
        vsep = QFrame()
        vsep.setFrameShape(QFrame.Shape.VLine)
        vsep.setStyleSheet(f"color: {C['border']};")
        btn_row.addWidget(vsep)

        # Marketing program group
        group_col = QVBoxLayout()
        group_col.setSpacing(4)
        group_label = QLabel("By Marketing Program")
        group_label.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        group_col.addWidget(group_label)
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(200)
        group_col.addWidget(self.group_combo)
        self.btn_export_group = QPushButton("⬇  Generate for Group")
        self.btn_export_group.setProperty("class", "primary")
        self.btn_export_group.setEnabled(False)
        self.btn_export_group.clicked.connect(self._export_group)
        group_col.addWidget(self.btn_export_group)
        btn_row.addLayout(group_col)

        vsep2 = QFrame()
        vsep2.setFrameShape(QFrame.Shape.VLine)
        vsep2.setStyleSheet(f"color: {C['border']};")
        btn_row.addWidget(vsep2)

        # Batch all
        batch_col = QVBoxLayout()
        batch_col.setSpacing(4)
        batch_label = QLabel("All Active Accounts")
        batch_label.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        batch_col.addWidget(batch_label)
        batch_spacer = QLabel("")   # align vertically with combos
        batch_spacer.setFixedHeight(self.single_acct_combo.sizeHint().height())
        batch_col.addWidget(batch_spacer)
        self.btn_export = QPushButton("⬇  Export All PDFs")
        self.btn_export.setProperty("class", "success")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_all)
        batch_col.addWidget(self.btn_export)
        btn_row.addLayout(batch_col)

        btn_row.addStretch()
        export_outer.addLayout(btn_row)

        # Progress bar (hidden when idle)
        self.export_progress = QProgressBar()
        self.export_progress.setVisible(False)
        self.export_status = QLabel("")
        self.export_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        prog_row = QHBoxLayout()
        prog_row.addWidget(self.export_progress)
        prog_row.addWidget(self.export_status)
        export_outer.addLayout(prog_row)

        root.addWidget(export_frame)

        # ── Email Statements section ──────────────────────────────────
        email_frame = QFrame()
        email_frame.setProperty("class", "card")
        email_outer = QVBoxLayout(email_frame)
        email_outer.setContentsMargins(16, 12, 16, 12)
        email_outer.setSpacing(10)

        email_heading_row = QHBoxLayout()
        email_heading_lbl = QLabel("Email Statements to Dealers")
        email_heading_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        email_heading_row.addWidget(email_heading_lbl)
        email_heading_row.addStretch()

        btn_reload_email = QPushButton("⟳ Refresh List")
        btn_reload_email.clicked.connect(self._load_email_table)
        email_heading_row.addWidget(btn_reload_email)
        email_outer.addLayout(email_heading_row)

        email_desc = QLabel(
            "Select an output folder (PDFs are generated on demand), then use the buttons "
            "below to preview a statement or send it directly to the dealer's email address. "
            "Accounts without an email address are shown but the Send button is disabled. "
            "Set email addresses in the Accounts view."
        )
        email_desc.setWordWrap(True)
        email_desc.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        email_outer.addWidget(email_desc)

        # Folder + group filter row
        email_ctrl_row = QHBoxLayout()
        email_ctrl_row.setSpacing(8)

        folder_lbl2 = QLabel("PDF folder:")
        folder_lbl2.setStyleSheet(f"color: {C['text_muted']}; font-size:11px;")
        email_ctrl_row.addWidget(folder_lbl2)
        self.lbl_email_dir = QLabel("Use same folder as Generate section above")
        self.lbl_email_dir.setStyleSheet(f"color: {C['text_muted']}; font-size:11px;")
        email_ctrl_row.addWidget(self.lbl_email_dir, stretch=1)

        email_ctrl_row.addSpacing(16)
        filter_lbl = QLabel("Group:")
        filter_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size:11px;")
        email_ctrl_row.addWidget(filter_lbl)
        self.email_group_filter = QComboBox()
        self.email_group_filter.setFixedWidth(200)
        self.email_group_filter.currentIndexChanged.connect(self._load_email_table)
        email_ctrl_row.addWidget(self.email_group_filter)

        email_outer.addLayout(email_ctrl_row)

        # Table: Account# | Name | Email | PDF File | Preview | Send
        self.email_table = QTableWidget(0, 6)
        self.email_table.setHorizontalHeaderLabels(
            ["Account #", "Name", "Email", "PDF File", "Preview", "Send"]
        )
        self.email_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.email_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.email_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.email_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.email_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.email_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self.email_table.verticalHeader().setVisible(False)
        self.email_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.email_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.email_table.setAlternatingRowColors(True)
        self.email_table.setMinimumHeight(200)
        email_outer.addWidget(self.email_table)

        self.email_status_lbl = QLabel("")
        self.email_status_lbl.setWordWrap(True)
        email_outer.addWidget(self.email_status_lbl)

        root.addWidget(email_frame)

        self._email_workers: list = []   # keep references to avoid GC

        self._export_dir: str = ""
        self._export_worker: Optional[QThread] = None
        self._load_templates()
        self._load_export_combos()

    def _load_templates(self):
        with get_session() as session:
            templates = session.query(PdfTemplate).order_by(PdfTemplate.name).all()

        self.tmpl_list.clear()
        for t in templates:
            item = QListWidgetItem(
                f"{'★ ' if t.is_default else ''}{t.name}"
            )
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            self.tmpl_list.addItem(item)

        if templates:
            self.tmpl_list.setCurrentRow(0)

        # Populate email group filter
        self._init_email_group_filter()
        self._load_email_table()

    def _init_email_group_filter(self):
        self.email_group_filter.blockSignals(True)
        self.email_group_filter.clear()
        self.email_group_filter.addItem("All Accounts", None)
        with get_session() as session:
            from db.local_db import Account
            groups = (
                session.query(Account.program_bc_code)
                .filter(Account.is_active.is_(True), Account.program_bc_code.isnot(None))
                .distinct()
                .order_by(Account.program_bc_code)
                .all()
            )
        for (grp,) in groups:
            if grp:
                self.email_group_filter.addItem(grp, grp)
        self.email_group_filter.blockSignals(False)

    def _load_email_table(self):
        import os
        self.email_table.setRowCount(0)
        group_filter = self.email_group_filter.currentData()
        with get_session() as session:
            from db.local_db import Account
            q = session.query(Account).filter(Account.is_active.is_(True))
            if group_filter:
                q = q.filter(Account.program_bc_code == group_filter)
            accounts = q.order_by(Account.account_number).all()
            account_data = [
                (a.account_number, a.account_name or a.account_number, getattr(a, "email", "") or "")
                for a in accounts
            ]

        # Determine PDF dir from generate section
        pdf_dir = getattr(self, "_last_export_dir", "")
        if not pdf_dir:
            from db.local_db import get_setting
            pdf_dir = get_setting("last_export_dir", "")

        for acct_num, acct_name, email in account_data:
            row = self.email_table.rowCount()
            self.email_table.insertRow(row)
            self.email_table.setItem(row, 0, QTableWidgetItem(acct_num))
            self.email_table.setItem(row, 1, QTableWidgetItem(acct_name))
            self.email_table.setItem(row, 2, QTableWidgetItem(email))

            # PDF file cell
            pdf_path = os.path.join(pdf_dir, f"{acct_num}.pdf") if pdf_dir else ""
            pdf_exists = os.path.isfile(pdf_path)
            pdf_item = QTableWidgetItem(os.path.basename(pdf_path) if pdf_dir else "—")
            if pdf_exists:
                pdf_item.setForeground(
                    __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(C["success"])
                )
            self.email_table.setItem(row, 3, pdf_item)

            # Preview button
            btn_preview = QPushButton("Preview")
            btn_preview.setFixedHeight(24)
            btn_preview.setEnabled(pdf_exists)
            btn_preview.setProperty("_pdf_path", pdf_path)
            btn_preview.clicked.connect(
                lambda checked, p=pdf_path: self._preview_pdf(p)
            )
            self.email_table.setCellWidget(row, 4, btn_preview)

            # Send button
            btn_send = QPushButton("✉ Send")
            btn_send.setFixedHeight(24)
            btn_send.setProperty("class", "primary")
            btn_send.setEnabled(bool(email))
            btn_send.setToolTip("" if email else "No email address — set it in the Accounts view")
            btn_send.clicked.connect(
                lambda checked, r=row, n=acct_num, nm=acct_name, em=email:
                    self._send_statement(r, n, nm, em)
            )
            self.email_table.setCellWidget(row, 5, btn_send)

    def _preview_pdf(self, pdf_path: str):
        import os
        if not os.path.isfile(pdf_path):
            QMessageBox.warning(
                self, "File Not Found",
                f"PDF not found:\n{pdf_path}\n\nGenerate the statement first."
            )
            return
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(pdf_path))

    def _send_statement(self, row: int, account_number: str, account_name: str, email: str):
        if not email:
            QMessageBox.warning(
                self, "No Email Address",
                f"Account {account_number} has no email address.\n"
                "Set it in the Accounts view."
            )
            return

        from services.email_sender import smtp_configured
        if not smtp_configured():
            QMessageBox.warning(
                self, "Email Not Configured",
                "Please configure your SMTP credentials in Settings → Email before sending."
            )
            return

        # Get PDF dir
        pdf_dir = getattr(self, "_last_export_dir", "")
        if not pdf_dir:
            from db.local_db import get_setting
            pdf_dir = get_setting("last_export_dir", "")
        if not pdf_dir:
            pdf_dir = QFileDialog.getExistingDirectory(self, "Choose PDF Output Folder")
            if not pdf_dir:
                return
            self._last_export_dir = pdf_dir
            from db.local_db import set_setting
            set_setting("last_export_dir", pdf_dir)

        # Collect template config
        config = self._current_config()
        period_end = self._period_end_date()

        # Disable send button while working
        send_btn = self.email_table.cellWidget(row, 5)
        if send_btn:
            send_btn.setEnabled(False)
            send_btn.setText("Sending…")

        # Update status cell (col 3)
        self.email_table.setItem(row, 3, QTableWidgetItem("Generating…"))

        worker = EmailSendWorker(
            account_number=account_number,
            to_email=email,
            to_name=account_name,
            pdf_dir=pdf_dir,
            period_end=period_end,
            template_config=config,
            parent=self,
        )
        self._email_workers.append(worker)

        def on_done(ok, msg, r=row, acct=account_number, em=email, w=worker):
            import os
            self._email_workers.remove(w)
            pdf_path = os.path.join(pdf_dir, f"{acct}.pdf")
            self.email_table.setItem(
                r, 3,
                QTableWidgetItem(os.path.basename(pdf_path) if os.path.isfile(pdf_path) else "—")
            )
            status_item = QTableWidgetItem("✓ Sent" if ok else f"✗ {msg}")
            from PyQt6.QtGui import QColor
            status_item.setForeground(QColor(C["success"] if ok else C["danger"]))
            self.email_table.setItem(r, 2, QTableWidgetItem(em))

            btn = self.email_table.cellWidget(r, 5)
            if btn:
                btn.setEnabled(True)
                btn.setText("✉ Send")

            prev_btn = self.email_table.cellWidget(r, 4)
            if prev_btn:
                prev_btn.setEnabled(os.path.isfile(pdf_path))

            self.email_status_lbl.setText(
                f"{'✓' if ok else '✗'}  {acct}: {msg}"
            )
            self.email_status_lbl.setStyleSheet(
                f"color: {C['success'] if ok else C['danger']}; font-size:11px;"
            )

        worker.finished.connect(on_done)
        worker.start()

    def _period_end_date(self):
        """Return the period end date from the export date picker."""
        if hasattr(self, "date_to"):
            return self.date_to.date().toPyDate()
        from datetime import date
        return date.today()

    def _current_config(self) -> dict:
        """Return the template config dict for the currently selected template."""
        if hasattr(self, "_active_config"):
            return self._active_config
        with get_session() as session:
            t = session.query(PdfTemplate).filter_by(is_default=True).first()
            if t:
                import json
                return json.loads(t.template_json)
        return {}
        if not current:
            return
        tmpl_id = current.data(Qt.ItemDataRole.UserRole)
        with get_session() as session:
            tmpl = session.query(PdfTemplate).filter_by(id=tmpl_id).first()
            if not tmpl:
                return
            import json as _json
            self._active_config = _json.loads(tmpl.template_json)
            # Clear old editor
            while self._editor_layout.count():
                item = self._editor_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._current_editor = TemplateEditorPanel(tmpl)
            self._editor_layout.addWidget(self._current_editor)
            self.btn_save.setEnabled(True)

    def _save_template(self):
        if self._current_editor:
            self._current_editor.save()
            QMessageBox.information(self, "Saved", "Template saved.")

    def _new_template(self):
        import json
        default_cfg = {
            "company_name": "Your Company",
            "header_text": "Rebate Statement",
            "footer_text": "Thank you for your business.",
            "logo_path": "",
            "primary_color": "#1a3a6e",
            "secondary_color": "#f5f5f5",
            "accent_color": "#2ecc71",
            "show_tier_breakdown": True,
            "show_monthly_sales": True,
            "paper_size": "letter",
        }
        with get_session() as session:
            t = PdfTemplate(
                name="New Template",
                is_default=False,
                template_json=json.dumps(default_cfg),
            )
            session.add(t)
        self._load_templates()

    def _delete_template(self):
        item = self.tmpl_list.currentItem()
        if not item:
            return
        tmpl_id = item.data(Qt.ItemDataRole.UserRole)
        if (
            QMessageBox.question(self, "Delete", "Delete this template?")
            == QMessageBox.StandardButton.Yes
        ):
            with get_session() as session:
                session.query(PdfTemplate).filter_by(id=tmpl_id).delete()
            self._load_templates()

    def _choose_export_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Export Folder")
        if folder:
            self._export_dir = folder
            self._last_export_dir = folder
            self.lbl_export_dir.setText(folder)
            self.lbl_email_dir.setText(folder)
            self.btn_export.setEnabled(True)
            self.btn_export_single.setEnabled(True)
            self.btn_export_group.setEnabled(True)
            from db.local_db import set_setting
            set_setting("last_export_dir", folder)

    def _load_export_combos(self):
        """Populate the single-account and marketing-program combo boxes."""
        with get_session() as session:
            accounts = (
                session.query(Account)
                .filter_by(is_active=True)
                .order_by(Account.account_number)
                .all()
            )
            programs = session.query(MarketingProgram).order_by(MarketingProgram.name).all()

        self.single_acct_combo.clear()
        for a in accounts:
            label = f"{a.account_number}  —  {a.account_name}" if a.account_name else a.account_number
            self.single_acct_combo.addItem(label, userData=a.account_number)

        self.group_combo.clear()
        for p in programs:
            self.group_combo.addItem(f"{p.name or p.bccode} ({p.bccode})", userData=p.bccode)
        if not programs:
            self.group_combo.addItem("(no marketing programs)", userData=None)

    def _export_single(self):
        if not self._export_dir or not self._current_editor:
            QMessageBox.warning(self, "Not Ready", "Choose an output folder and select a template first.")
            return
        acct_no = self.single_acct_combo.currentData()
        if not acct_no:
            QMessageBox.warning(self, "No Account", "Select an account.")
            return
        self._run_targeted_export([acct_no])

    def _export_group(self):
        if not self._export_dir or not self._current_editor:
            QMessageBox.warning(self, "Not Ready", "Choose an output folder and select a template first.")
            return
        bccode = self.group_combo.currentData()
        if not bccode:
            QMessageBox.warning(self, "No Group", "No marketing program available.")
            return
        with get_session() as session:
            account_numbers = [
                a.account_number
                for a in session.query(Account)
                .join(Account.marketing_program)
                .filter(
                    Account.is_active == True,
                    MarketingProgram.bccode == bccode,
                )
                .all()
            ]
        if not account_numbers:
            QMessageBox.information(self, "No Accounts", f"No active accounts in program {bccode}.")
            return
        self._run_targeted_export(account_numbers)

    def _run_targeted_export(self, account_numbers: list):
        cfg = self._current_editor.get_config()
        self.btn_export.setEnabled(False)
        self.btn_export_single.setEnabled(False)
        self.btn_export_group.setEnabled(False)
        self.export_progress.setVisible(True)
        self.export_progress.setValue(0)

        self._export_worker = TargetedExportWorker(
            self._export_dir, self._end, cfg, account_numbers, parent=self
        )
        self._export_worker.progress.connect(
            lambda p, m: (
                self.export_progress.setValue(p),
                self.export_status.setText(m),
            )
        )
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.start()

    def _export_all(self):
        if not self._export_dir:
            return
        if not self._current_editor:
            QMessageBox.warning(self, "No Template", "Select a template first.")
            return

        cfg = self._current_editor.get_config()
        self.btn_export.setEnabled(False)
        self.btn_export_single.setEnabled(False)
        self.btn_export_group.setEnabled(False)
        self.export_progress.setVisible(True)
        self.export_progress.setValue(0)

        self._export_worker = BatchExportWorker(
            self._export_dir, self._start, self._end, cfg, parent=self
        )
        self._export_worker.progress.connect(
            lambda p, m: (
                self.export_progress.setValue(p),
                self.export_status.setText(m),
            )
        )
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.start()

    def _on_export_finished(self, success: bool, msg: str, count: int):
        self.export_progress.setVisible(False)
        if self._export_dir:
            self.btn_export.setEnabled(True)
            self.btn_export_single.setEnabled(True)
            self.btn_export_group.setEnabled(True)
        if success:
            QMessageBox.information(
                self, "Export Complete",
                f"{count} PDF statement(s) saved to:\n{self._export_dir}"
            )
        else:
            QMessageBox.critical(self, "Export Failed", msg)

    def set_date_range(self, start: date, end: date):
        self._start = start
        self._end = end
