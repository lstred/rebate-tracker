"""
ui/views/dashboard_view.py
--------------------------
Executive overview: KPI cards, top-account bar chart, and accounts table
with rebate summaries.  All data comes from the local SQLite cache.
"""

from __future__ import annotations

from datetime import date

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.rebate_calculator import get_dashboard_summary
from ui.theme import C, CHART_COLORS, apply_mpl_style


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class DashboardLoader(QThread):
    ready = pyqtSignal(list)

    def __init__(self, start: date, end: date, parent=None):
        super().__init__(parent)
        self._start = start
        self._end = end

    def run(self):
        data = get_dashboard_summary(self._start, self._end)
        self.ready.emit(data)


# ---------------------------------------------------------------------------
# KPI Card
# ---------------------------------------------------------------------------

class KpiCard(QFrame):
    def __init__(self, label: str, value: str = "—", color: str = C["accent"], parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setMinimumWidth(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(4)

        self._value_lbl = QLabel(value)
        self._value_lbl.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        self._value_lbl.setStyleSheet(f"color: {color};")
        self._value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._label_lbl = QLabel(label)
        self._label_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px;")
        self._label_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        layout.addWidget(self._value_lbl)
        layout.addWidget(self._label_lbl)

    def update_value(self, value: str) -> None:
        self._value_lbl.setText(value)


# ---------------------------------------------------------------------------
# Bar chart canvas
# ---------------------------------------------------------------------------

class BarChartCanvas(FigureCanvas):
    def __init__(self, parent=None):
        apply_mpl_style()
        self.fig = Figure(figsize=(6, 3), tight_layout=True)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def plot(self, labels: list[str], values: list[float], title: str = "") -> None:
        self.ax.clear()
        if not labels:
            self.ax.text(
                0.5, 0.5, "No data", transform=self.ax.transAxes,
                ha="center", va="center", color=C["text_muted"], fontsize=12,
            )
            self.draw()
            return

        colors = CHART_COLORS[:len(labels)]
        bars = self.ax.barh(labels, values, color=colors, height=0.6, zorder=2)
        self.ax.set_xlabel("Sales ($)", color=C["text_muted"])
        if title:
            self.ax.set_title(title, color=C["text"])
        self.ax.invert_yaxis()

        # Value labels on bars
        for bar, val in zip(bars, values):
            self.ax.text(
                val + max(values) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"${val:,.0f}",
                va="center", ha="left",
                color=C["text_muted"], fontsize=7,
            )

        self.ax.xaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}K" if x >= 1000 else f"${x:.0f}")
        )
        self.fig.set_facecolor(C["surface"])
        self.ax.set_facecolor(C["surface"])
        self.draw()


# ---------------------------------------------------------------------------
# Accounts summary table
# ---------------------------------------------------------------------------

class AccountsTable(QTableWidget):
    COLS = ["Account #", "Name", "Current Sales", "Prior YR Sales",
            "Growth", "Rebate Structure", "Projected Rebate", "Tier"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSortingEnabled(True)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)

    def populate(self, rows: list[dict]) -> None:
        self.setSortingEnabled(False)
        self.setRowCount(0)
        for r in rows:
            row_idx = self.rowCount()
            self.insertRow(row_idx)
            growth = r["current_sales"] - r.get("prior_year_sales", 0)
            tier_str = (
                f"Tier {r['tier_reached']}" if r.get("tier_reached") else "None"
            )
            values = [
                r["account_number"],
                r["account_name"],
                f"${r['current_sales']:,.2f}",
                f"${r.get('prior_year_sales', 0):,.2f}",
                f"${growth:,.2f}",
                r["structure_name"],
                f"${r['rebate_amount']:,.2f}",
                tier_str,
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignRight if col >= 2 and col <= 4 or col == 6
                       else Qt.AlignmentFlag.AlignLeft)
                )
                # Color rebate column
                if col == 6 and r["rebate_amount"] > 0:
                    item.setForeground(
                        __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(C["success"])
                    )
                self.setItem(row_idx, col, item)

        self.setSortingEnabled(True)
        self.resizeRowsToContents()


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

class DashboardView(QWidget):
    def __init__(self, start: date, end: date, parent=None):
        super().__init__(parent)
        self._start = start
        self._end = end
        self._data: list[dict] = []
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(20)

        # Heading
        heading = QLabel("Dashboard")
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        heading.setStyleSheet(f"color: {C['text']};")
        root.addWidget(heading)

        # KPI row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(16)
        self.kpi_accounts = KpiCard("Active Accounts", "—")
        self.kpi_sales = KpiCard("Total Sales", "—", C["accent"])
        self.kpi_rebate = KpiCard("Projected Rebates", "—", C["success"])
        self.kpi_avg = KpiCard("Avg Rebate / Account", "—", C["warning"])
        for kpi in [self.kpi_accounts, self.kpi_sales, self.kpi_rebate, self.kpi_avg]:
            kpi_row.addWidget(kpi)
        root.addLayout(kpi_row)

        # Chart + table row
        mid_row = QHBoxLayout()
        mid_row.setSpacing(16)

        # Bar chart card
        chart_card = QFrame()
        chart_card.setProperty("class", "card")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(12, 12, 12, 12)
        chart_lbl = QLabel("Top 10 Accounts by Sales")
        chart_lbl.setStyleSheet(f"color: {C['text_muted']}; font-size: 11px; font-weight: bold;")
        self.chart = BarChartCanvas()
        chart_layout.addWidget(chart_lbl)
        chart_layout.addWidget(self.chart)
        chart_card.setMinimumWidth(440)
        mid_row.addWidget(chart_card, stretch=1)

        root.addLayout(mid_row)

        # Full accounts table
        tbl_label = QLabel("All Accounts — Rebate Summary")
        tbl_label.setStyleSheet(f"color: {C['text_muted']}; font-size:11px; font-weight:bold;")
        root.addWidget(tbl_label)

        self.table = AccountsTable()
        root.addWidget(self.table)

        # Loading label
        self.lbl_loading = QLabel("Loading data…")
        self.lbl_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_loading.setStyleSheet(f"color: {C['text_muted']}; font-size: 14px;")
        root.addWidget(self.lbl_loading)
        self.lbl_loading.hide()

    def _load(self) -> None:
        self.lbl_loading.show()
        self._loader = DashboardLoader(self._start, self._end, self)
        self._loader.ready.connect(self._on_data_ready)
        self._loader.start()

    def _on_data_ready(self, data: list[dict]) -> None:
        self.lbl_loading.hide()
        self._data = data
        self._update_ui()

    def _update_ui(self) -> None:
        data = self._data
        total_accounts = len(data)
        total_sales = sum(r["current_sales"] for r in data)
        total_rebate = sum(r["rebate_amount"] for r in data)
        avg_rebate = total_rebate / total_accounts if total_accounts else 0

        self.kpi_accounts.update_value(str(total_accounts))
        self.kpi_sales.update_value(f"${total_sales:,.0f}")
        self.kpi_rebate.update_value(f"${total_rebate:,.2f}")
        self.kpi_avg.update_value(f"${avg_rebate:,.2f}")

        # Chart — top 10
        top10 = data[:10]
        self.chart.plot(
            [r["account_name"][:20] for r in top10],
            [r["current_sales"] for r in top10],
            title="",
        )

        # Table
        self.table.populate(data)

    def set_date_range(self, start: date, end: date) -> None:
        self._start = start
        self._end = end

    def refresh(self, start: date, end: date) -> None:
        self._start = start
        self._end = end
        self._load()
