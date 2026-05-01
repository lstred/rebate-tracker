"""
PyInstaller runtime hook — executed before any app code when running as frozen exe.
Redirects matplotlib's config/cache directory to a user-writable APPDATA location
so the bundled app never tries to write into its own (read-only) install folder.
"""
import os
import sys

if getattr(sys, "frozen", False):
    _rt_cache = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        "RebateTracker",
        "mpl_cache",
    )
    os.makedirs(_rt_cache, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", _rt_cache)
    os.environ.setdefault("MPLBACKEND", "QtAgg")
