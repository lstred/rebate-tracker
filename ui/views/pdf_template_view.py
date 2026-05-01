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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db.local_db import PdfTemplate, get_session
from services.pdf_generator import batch_generate
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
        export_layout = QHBoxLayout(export_frame)
        export_layout.setContentsMargins(16, 12, 16, 12)
        export_layout.setSpacing(12)

        export_lbl = QLabel(
            "<b>Batch Export</b>  — Generate a PDF for every active account "
            "using the current date range and selected template."
        )
        export_lbl.setTextFormat(Qt.TextFormat.RichText)
        export_lbl.setWordWrap(True)
        export_layout.addWidget(export_lbl, stretch=1)

        self.lbl_export_dir = QLabel("No folder selected")
        self.lbl_export_dir.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        export_layout.addWidget(self.lbl_export_dir)

        btn_dir = QPushButton("Choose Folder")
        btn_dir.clicked.connect(self._choose_export_dir)
        export_layout.addWidget(btn_dir)

        self.btn_export = QPushButton("⬇  Export All PDFs")
        self.btn_export.setProperty("class", "success")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_all)
        export_layout.addWidget(self.btn_export)

        root.addWidget(export_frame)

        # Progress bar (hidden when idle)
        self.export_progress = QProgressBar()
        self.export_progress.setVisible(False)
        self.export_status = QLabel("")
        self.export_status.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        prog_row = QHBoxLayout()
        prog_row.addWidget(self.export_progress)
        prog_row.addWidget(self.export_status)
        root.addLayout(prog_row)

        self._export_dir: str = ""
        self._load_templates()

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

    def _on_template_selected(self, current, _):
        if not current:
            return
        tmpl_id = current.data(Qt.ItemDataRole.UserRole)
        with get_session() as session:
            tmpl = session.query(PdfTemplate).filter_by(id=tmpl_id).first()
            if not tmpl:
                return
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
            self.lbl_export_dir.setText(folder)
            self.btn_export.setEnabled(True)

    def _export_all(self):
        if not self._export_dir:
            return
        if not self._current_editor:
            QMessageBox.warning(self, "No Template", "Select a template first.")
            return

        cfg = self._current_editor.get_config()
        self.btn_export.setEnabled(False)
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
        self.btn_export.setEnabled(True)
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
