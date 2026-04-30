"""
db/connection.py
----------------
SQL Server connection factory for the rebate-tracking app.

Connection-string resolution order (first match wins):
  1. Environment variable  : SQLSERVER_ODBC
  2. Local config module   : config.py  →  SQLSERVER_ODBC  (gitignored, never committed)
  3. Built-in defaults     : NRFVMSSQL04 / NRF_REPORTS / Windows auth / no encrypt

Public API
----------
  get_odbc_connection_string() -> str          Raw ODBC string (for pyodbc or diagnostics)
  get_engine()                 -> sa.Engine    SQLAlchemy engine  (use for pandas / ORM)
  get_raw_connection()         -> pyodbc.Connection  (for ad-hoc cursor work)
  test_connection()            -> bool         Quick smoke-test; prints result
"""

import os
import urllib.parse

import pyodbc
import sqlalchemy
from sqlalchemy.engine import URL

# ---------------------------------------------------------------------------
# Defaults — match the known environment exactly
# ---------------------------------------------------------------------------
_DEFAULT_DRIVER   = "ODBC Driver 18 for SQL Server"
_DEFAULT_SERVER   = "NRFVMSSQL04"
_DEFAULT_DATABASE = "NRF_REPORTS"


def _build_default_odbc() -> str:
    """Build the default ODBC connection string from hard-coded environment values."""
    return (
        f"Driver={{{_DEFAULT_DRIVER}}};"
        f"Server={_DEFAULT_SERVER};"
        f"Database={_DEFAULT_DATABASE};"
        "Trusted_Connection=Yes;"
        "Encrypt=no;"
    )


# ---------------------------------------------------------------------------
# Public: ODBC string resolution
# ---------------------------------------------------------------------------

def get_odbc_connection_string() -> str:
    """
    Resolve and return the ODBC connection string.

    Priority:
      1. Env var SQLSERVER_ODBC
      2. config.py  →  SQLSERVER_ODBC
      3. Built-in defaults
    """
    # 1. Environment variable
    cs = os.environ.get("SQLSERVER_ODBC", "").strip()
    if cs:
        return cs

    # 2. Local config module (config.py — gitignored)
    try:
        import config  # type: ignore[import]
        cs = getattr(config, "SQLSERVER_ODBC", "").strip()
        if cs:
            return cs
    except ImportError:
        pass

    # 3. Built-in defaults
    return _build_default_odbc()


# ---------------------------------------------------------------------------
# Public: SQLAlchemy engine
# ---------------------------------------------------------------------------

def get_engine() -> sqlalchemy.engine.Engine:
    """
    Return a SQLAlchemy engine backed by pyodbc + Windows auth.

    fast_executemany=True is enabled for bulk INSERT / UPDATE performance.
    The engine is lazily connected — no network call until first use.
    """
    odbc_str = get_odbc_connection_string()
    connection_url = URL.create(
        drivername="mssql+pyodbc",
        query={"odbc_connect": odbc_str},
    )
    return sqlalchemy.create_engine(connection_url, fast_executemany=True)


# ---------------------------------------------------------------------------
# Public: raw pyodbc connection
# ---------------------------------------------------------------------------

def get_raw_connection() -> pyodbc.Connection:
    """
    Return a raw pyodbc connection for cursor-level work.
    Caller is responsible for commit/rollback/close.
    """
    return pyodbc.connect(get_odbc_connection_string())


# ---------------------------------------------------------------------------
# Public: smoke-test helper
# ---------------------------------------------------------------------------

def test_connection() -> bool:
    """
    Run a trivial query to verify the connection is reachable.
    Prints a one-line status and returns True on success, False on failure.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        print(f"[connection] OK — {_DEFAULT_SERVER}/{_DEFAULT_DATABASE}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[connection] FAILED — {exc}")
        return False


# ---------------------------------------------------------------------------
# Quick sanity check when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_connection()
