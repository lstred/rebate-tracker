Rebate Tracker — Installation Guide
=====================================

REQUIREMENTS
------------
  • Windows 10 or 11 (64-bit)
  • Connected to the NRF company network (for sales data sync)
  • "ODBC Driver 18 for SQL Server" installed

    To check if the driver is present:
      Open "ODBC Data Sources (64-bit)" from the Start menu
      → click the "Drivers" tab and look for "ODBC Driver 18 for SQL Server"

    If missing, ask IT to install it, or download from:
      https://aka.ms/downloadmsodbcsql

INSTALLATION
------------
  1. Extract the RebateTracker folder anywhere (Desktop, C:\Apps, shared drive, etc.)
  2. Double-click RebateTracker.exe to launch

  No Python, no pip, no installer required — everything is self-contained.

FIRST RUN — Windows SmartScreen
--------------------------------
  Because this executable is not commercially code-signed, Windows may show a
  blue "Windows protected your PC" screen on the very first launch.

  To proceed:
    1. Click "More info"
    2. Click "Run anyway"

  This is a one-time prompt per machine. After that it will open normally.
  (IT can suppress this permanently by right-clicking the .exe → Properties →
  Unblock, or by deploying via a group policy / SCCM/Intune package.)

DATA
----
  Your rebate data is stored in:
    %APPDATA%\RebateTracker\rebate_data.db

  This file is created automatically on first run. It is separate from the
  application folder so you can safely delete or update the RebateTracker
  folder without losing your data.

UPDATING
--------
  To update to a newer version, simply replace the RebateTracker folder
  with the new one. Your data in %APPDATA%\RebateTracker\ is unaffected.

SUPPORT
-------
  Contact Lukas Stred for assistance.
