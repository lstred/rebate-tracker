# -*- mode: python ; coding: utf-8 -*-
"""
rebate_tracker.spec
--------------------
PyInstaller build spec for Rebate Tracker.

Build command (from project root):
    C:\rtenv\Scripts\pyinstaller rebate_tracker.spec

Output: dist\RebateTracker\   (folder mode — no temp extraction, AV-friendly)
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ── Data files ───────────────────────────────────────────────────────────────
# matplotlib ships font / style-sheet data that must travel with the exe.
# reportlab ships font data required for PDF generation.
datas = []
datas += collect_data_files("matplotlib")
datas += collect_data_files("reportlab")
datas += collect_data_files("mpl_toolkits")

# ── Hidden imports ───────────────────────────────────────────────────────────
# PyInstaller static analysis misses these because they're loaded dynamically.
hiddenimports = [
    # SQLAlchemy
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "sqlalchemy.pool",
    "sqlalchemy.pool.impl",
    "sqlalchemy.pool.base",
    "sqlalchemy.event",
    "sqlalchemy.events",
    # SQL Server (pyodbc)
    "pyodbc",
    # MySQL cloud backup
    "pymysql",
    "pymysql.cursors",
    "pymysql.connections",
    "pymysql.converters",
    # Matplotlib
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends._backend_tk",   # may be imported by some mpl internals
    "matplotlib.pyplot",
    # ReportLab
    "reportlab",
    "reportlab.graphics",
    "reportlab.graphics.charts",
    "reportlab.graphics.renderPDF",
    "reportlab.platypus",
    "reportlab.platypus.doctemplate",
    "reportlab.pdfbase",
    "reportlab.pdfbase.ttfonts",
    "reportlab.pdfbase.pdfmetrics",
    "reportlab.lib.colors",
    "reportlab.lib.enums",
    "reportlab.lib.pagesizes",
    "reportlab.lib.styles",
    "reportlab.lib.units",
    # Pillow (ReportLab uses it for image embedding)
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    # pandas (used for SQL Server result sets in sync.py)
    "pandas",
    "pandas.io.sql",
    # PyQt6
    "PyQt6.sip",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtSvg",
]

a = Analysis(
    ["main.py"],
    pathex=[r"C:\Users\lukass\Desktop\rebate tracking"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["build_hooks"],
    hooksconfig={},
    runtime_hooks=["build_hooks/rthook_matplotlib.py"],
    # Strip out unused GUI backends to keep bundle size down
    excludes=["tkinter", "_tkinter", "Tkinter", "tcl", "tk"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RebateTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX compression is commonly flagged by AV tools — keep it off
    upx=False,
    # No console window — this is a GUI app
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",  # uncomment and add icon.ico when available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RebateTracker",
)
