"""
ui/views/audit_log_view.py
--------------------------
Read-only audit trail viewer.  Shows all user-initiated changes
in reverse-chronological order with search/filter support.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db.local_db import AuditLog, get_session
from ui.theme import C


class AuditLogView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[AuditLog] = []
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        heading = QLabel("Audit Log")
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        root.addWidget(heading)

        sub = QLabel("All user-initiated changes are recorded here automatically.")
        sub.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        root.addWidget(sub)

        # Toolbar
        toolbar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by account, action, or description…")
        self._search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._load)
        toolbar.addWidget(btn_refresh)
        root.addLayout(toolbar)

        # Table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Timestamp", "Action", "Entity Type", "Account / ID", "Description", "Changes"]
        )
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, 140)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(3, 110)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        root.addWidget(self._table)

        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(f"color: {C['text_muted']}; font-size: 10px;")
        root.addWidget(self._lbl_count)

    def _load(self) -> None:
        with get_session() as session:
            self._entries = (
                session.query(AuditLog)
                .order_by(AuditLog.timestamp.desc())
                .limit(5000)
                .all()
            )
        self._apply_filter(self._search.text())

    def _apply_filter(self, text: str) -> None:
        text = text.lower().strip()
        if text:
            filtered = [
                e for e in self._entries
                if text in (e.entity_id or "").lower()
                or text in e.action.lower()
                or text in (e.description or "").lower()
                or text in (e.entity_type or "").lower()
            ]
        else:
            filtered = self._entries

        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        # Action colour map
        action_colors = {
            "add": C.get("success", "#2ecc71"),
            "reactivate": C.get("success", "#2ecc71"),
            "remove": C.get("danger", "#e74c3c"),
            "delete": C.get("danger", "#e74c3c"),
            "edit": C.get("accent", "#3498db"),
            "assign": C.get("accent", "#3498db"),
        }

        for e in filtered:
            row = self._table.rowCount()
            self._table.insertRow(row)

            ts = e.timestamp.strftime("%m/%d/%Y %H:%M:%S") if e.timestamp else ""
            action = e.action or ""
            etype = e.entity_type or ""
            eid = e.entity_id or ""
            desc = e.description or ""

            changes = ""
            if e.old_value and e.new_value:
                changes = f"{e.old_value}  →  {e.new_value}"
            elif e.new_value:
                changes = e.new_value
            elif e.old_value:
                changes = f"removed: {e.old_value}"

            cells = [ts, action, etype, eid, desc, changes]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if col == 1 and action in action_colors:
                    from PyQt6.QtGui import QColor
                    item.setForeground(QColor(action_colors[action]))
                    item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                self._table.setItem(row, col, item)

        self._table.setSortingEnabled(True)
        self._lbl_count.setText(f"{len(filtered):,} entries shown")

    def refresh(self) -> None:
        self._load()
