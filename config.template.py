# config.template.py
#
# Copy this file to config.py (gitignored) and fill in your values.
# Alternatively, set the SQLSERVER_ODBC environment variable — that takes priority.
#
# config.py is the lowest-priority fallback; env var SQLSERVER_ODBC overrides it.

SQLSERVER_ODBC = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=NRFVMSSQL04;"
    "Database=NRF_REPORTS;"
    "Trusted_Connection=Yes;"
    "Encrypt=no;"
)
