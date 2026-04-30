"""
ui/theme.py
-----------
Design system for the Rebate Tracker app.

Dark professional theme:
  • Base: very dark navy/slate
  • Cards: slightly lighter surface
  • Accent: electric blue
  • Status: green / amber / red
  • Typography: system-native sans-serif (Segoe UI on Windows)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Color palette  (used both in QSS and in matplotlib charts)
# ---------------------------------------------------------------------------

C = {
    # Backgrounds
    "bg":          "#0D1117",
    "surface":     "#161B22",
    "surface2":    "#21262D",
    "surface3":    "#2D333B",

    # Borders
    "border":      "#30363D",
    "border_focus":"#388BFD",

    # Accents
    "accent":      "#388BFD",
    "accent_hov":  "#58A6FF",
    "accent_dim":  "#1a3a6e",

    # Status
    "success":     "#3FB950",
    "warning":     "#D29922",
    "danger":      "#F85149",

    # Text
    "text":        "#E6EDF3",
    "text_muted":  "#8B949E",
    "text_dim":    "#484F58",

    # Sidebar
    "sidebar":     "#0D1117",
    "sidebar_hov": "#161B22",
    "sidebar_sel": "#1c2433",
    "sidebar_ind": "#388BFD",   # active indicator bar
}


# ---------------------------------------------------------------------------
# Qt stylesheet (QSS)
# ---------------------------------------------------------------------------

STYLESHEET = f"""
/* ── Global ─────────────────────────────────────────── */
QWidget {{
    background-color: {C["bg"]};
    color: {C["text"]};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}}

QMainWindow {{
    background-color: {C["bg"]};
}}

/* ── Scroll bars ─────────────────────────────────────── */
QScrollBar:vertical {{
    background: {C["surface"]};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C["surface3"]};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C["accent"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {C["surface"]};
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {C["surface3"]};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C["accent"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Labels ──────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {C["text"]};
}}
QLabel[class="heading"] {{
    font-size: 20px;
    font-weight: bold;
    color: {C["text"]};
}}
QLabel[class="subheading"] {{
    font-size: 14px;
    color: {C["text_muted"]};
}}
QLabel[class="muted"] {{
    color: {C["text_muted"]};
    font-size: 11px;
}}
QLabel[class="kpi-value"] {{
    font-size: 28px;
    font-weight: bold;
    color: {C["accent"]};
}}
QLabel[class="kpi-label"] {{
    font-size: 11px;
    color: {C["text_muted"]};
}}
QLabel[class="tag-success"] {{
    background-color: rgba(63, 185, 80, 0.15);
    color: {C["success"]};
    border-radius: 4px;
    padding: 2px 8px;
}}
QLabel[class="tag-warning"] {{
    background-color: rgba(210, 153, 34, 0.15);
    color: {C["warning"]};
    border-radius: 4px;
    padding: 2px 8px;
}}
QLabel[class="tag-danger"] {{
    background-color: rgba(248, 81, 73, 0.15);
    color: {C["danger"]};
    border-radius: 4px;
    padding: 2px 8px;
}}

/* ── Card / Panel ────────────────────────────────────── */
QFrame[class="card"] {{
    background-color: {C["surface"]};
    border: 1px solid {C["border"]};
    border-radius: 8px;
    padding: 4px;
}}
QFrame[class="card-flat"] {{
    background-color: {C["surface"]};
    border-radius: 8px;
}}

/* ── Buttons ─────────────────────────────────────────── */
QPushButton {{
    background-color: {C["surface2"]};
    color: {C["text"]};
    border: 1px solid {C["border"]};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {C["surface3"]};
    border-color: {C["accent"]};
    color: {C["accent_hov"]};
}}
QPushButton:pressed {{
    background-color: {C["accent_dim"]};
}}
QPushButton[class="primary"] {{
    background-color: {C["accent"]};
    color: #ffffff;
    border: none;
    font-weight: bold;
}}
QPushButton[class="primary"]:hover {{
    background-color: {C["accent_hov"]};
}}
QPushButton[class="primary"]:pressed {{
    background-color: {C["accent_dim"]};
}}
QPushButton[class="danger"] {{
    background-color: {C["danger"]};
    color: #ffffff;
    border: none;
}}
QPushButton[class="danger"]:hover {{
    background-color: #ff6b63;
}}
QPushButton[class="success"] {{
    background-color: {C["success"]};
    color: #ffffff;
    border: none;
}}
QPushButton[class="nav"] {{
    background-color: transparent;
    color: {C["text_muted"]};
    border: none;
    border-radius: 0;
    text-align: left;
    padding: 10px 20px;
    font-size: 13px;
}}
QPushButton[class="nav"]:hover {{
    background-color: {C["sidebar_hov"]};
    color: {C["text"]};
}}
QPushButton[class="nav"][active="true"] {{
    background-color: {C["sidebar_sel"]};
    color: {C["accent"]};
    font-weight: bold;
    border-left: 3px solid {C["sidebar_ind"]};
    padding-left: 17px;
}}
QPushButton[class="icon-btn"] {{
    background: transparent;
    border: none;
    padding: 4px;
    border-radius: 4px;
}}
QPushButton[class="icon-btn"]:hover {{
    background: {C["surface2"]};
}}

/* ── Input fields ────────────────────────────────────── */
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{
    background-color: {C["surface"]};
    color: {C["text"]};
    border: 1px solid {C["border"]};
    border-radius: 6px;
    padding: 5px 10px;
    selection-background-color: {C["accent"]};
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus {{
    border: 1.5px solid {C["border_focus"]};
}}
QLineEdit::placeholder {{
    color: {C["text_dim"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {C["text_muted"]};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {C["surface2"]};
    border: 1px solid {C["border"]};
    selection-background-color: {C["accent_dim"]};
    selection-color: {C["text"]};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {C["surface3"]};
    border: none;
    width: 16px;
}}
QDateEdit::drop-down {{
    border: none;
    width: 20px;
}}
QCalendarWidget {{
    background-color: {C["surface"]};
    color: {C["text"]};
}}
QCalendarWidget QAbstractItemView {{
    background-color: {C["surface"]};
    selection-background-color: {C["accent"]};
    color: {C["text"]};
}}

/* ── Tables ──────────────────────────────────────────── */
QTableWidget, QTableView {{
    background-color: {C["surface"]};
    alternate-background-color: {C["surface2"]};
    gridline-color: {C["border"]};
    border: 1px solid {C["border"]};
    border-radius: 6px;
    selection-background-color: {C["accent_dim"]};
    selection-color: {C["text"]};
}}
QTableWidget::item, QTableView::item {{
    padding: 4px 8px;
}}
QHeaderView::section {{
    background-color: {C["surface2"]};
    color: {C["text_muted"]};
    border: none;
    border-bottom: 1px solid {C["border"]};
    padding: 6px 8px;
    font-weight: bold;
    font-size: 11px;
    text-transform: uppercase;
}}
QHeaderView::section:hover {{
    background-color: {C["surface3"]};
    color: {C["text"]};
}}

/* ── Tab widget ──────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {C["border"]};
    border-top: none;
    background: {C["surface"]};
    border-radius: 0 0 8px 8px;
}}
QTabBar::tab {{
    background: {C["surface2"]};
    color: {C["text_muted"]};
    padding: 8px 20px;
    border: 1px solid {C["border"]};
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {C["surface"]};
    color: {C["text"]};
    border-bottom: 2px solid {C["accent"]};
}}
QTabBar::tab:hover:!selected {{
    background: {C["surface3"]};
    color: {C["text"]};
}}

/* ── List widget ─────────────────────────────────────── */
QListWidget {{
    background-color: {C["surface"]};
    border: 1px solid {C["border"]};
    border-radius: 6px;
    outline: none;
}}
QListWidget::item {{
    padding: 8px 12px;
    border-radius: 4px;
    margin: 1px 4px;
}}
QListWidget::item:selected {{
    background-color: {C["accent_dim"]};
    color: {C["text"]};
}}
QListWidget::item:hover {{
    background-color: {C["surface2"]};
}}

/* ── Progress bar ────────────────────────────────────── */
QProgressBar {{
    background-color: {C["surface2"]};
    border: 1px solid {C["border"]};
    border-radius: 6px;
    text-align: center;
    color: {C["text"]};
    height: 12px;
}}
QProgressBar::chunk {{
    background-color: {C["accent"]};
    border-radius: 5px;
}}

/* ── Check box ───────────────────────────────────────── */
QCheckBox {{
    color: {C["text"]};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {C["border"]};
    border-radius: 3px;
    background: {C["surface"]};
}}
QCheckBox::indicator:checked {{
    background: {C["accent"]};
    border-color: {C["accent"]};
}}

/* ── Group box ───────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {C["border"]};
    border-radius: 8px;
    margin-top: 16px;
    padding-top: 8px;
    color: {C["text_muted"]};
    font-size: 11px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    background: {C["bg"]};
    color: {C["text_muted"]};
}}

/* ── Splitter ────────────────────────────────────────── */
QSplitter::handle {{
    background: {C["border"]};
    width: 1px;
    height: 1px;
}}

/* ── Status bar ──────────────────────────────────────── */
QStatusBar {{
    background: {C["surface"]};
    color: {C["text_muted"]};
    border-top: 1px solid {C["border"]};
    font-size: 11px;
}}

/* ── Message box ─────────────────────────────────────── */
QMessageBox {{
    background-color: {C["surface"]};
}}
QMessageBox QLabel {{
    color: {C["text"]};
}}
"""


# ---------------------------------------------------------------------------
# Matplotlib style dict  (apply to plt.rcParams)
# ---------------------------------------------------------------------------

MPL_STYLE: dict = {
    "figure.facecolor":       C["surface"],
    "axes.facecolor":         C["surface"],
    "axes.edgecolor":         C["border"],
    "axes.labelcolor":        C["text_muted"],
    "axes.grid":              True,
    "grid.color":             C["border"],
    "grid.linestyle":         "--",
    "grid.linewidth":         0.5,
    "xtick.color":            C["text_muted"],
    "ytick.color":            C["text_muted"],
    "xtick.labelsize":        8,
    "ytick.labelsize":        8,
    "text.color":             C["text"],
    "legend.facecolor":       C["surface2"],
    "legend.edgecolor":       C["border"],
    "legend.labelcolor":      C["text"],
    "legend.fontsize":        8,
    "figure.titlesize":       12,
    "axes.titlesize":         11,
    "axes.titlecolor":        C["text"],
    "axes.labelsize":         9,
    "savefig.facecolor":      C["surface"],
    "lines.color":            C["accent"],
    "patch.facecolor":        C["accent"],
}

CHART_COLORS = [
    C["accent"],
    C["success"],
    C["warning"],
    "#9B59B6",  # purple
    "#E74C3C",  # coral
    "#1ABC9C",  # teal
    "#F39C12",  # orange
    "#2980B9",  # steel blue
]


def apply_mpl_style() -> None:
    """Apply the dark theme to matplotlib globally."""
    import matplotlib.pyplot as plt
    plt.rcParams.update(MPL_STYLE)
