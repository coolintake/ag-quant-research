"""
report_utils.py — Shared helpers for the arbitrage report scripts.
"""

import sqlite3
from datetime import datetime
from pathlib import Path


def _window_sort_key(label: str) -> datetime:
    """Convert 'Mon-YY' label to datetime for chronological sorting."""
    try:
        return datetime.strptime(label, "%b-%y")
    except ValueError:
        return datetime.max


def get_comparison_window(db_path: Path) -> str:
    """
    Read the user-chosen comparison window from run_metadata.
    This is the window entered at pipeline runtime — the centre column
    of the matrix and the basis for all spread calculations.

    Falls back to the first available non-Spot window with prices if
    comparison_window was not stored (e.g. old DB from before this feature).
    """
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT comparison_window FROM run_metadata "
            "ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass

    # Fallback: first non-Spot window that has actual prices
    return _fallback_window(db_path)


def _fallback_window(db_path: Path) -> str:
    """Derive the first populated non-Spot window — used only for old DBs."""
    try:
        con  = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT DISTINCT Delivery_Window FROM wheat_summary "
            "WHERE Price_USD IS NOT NULL AND Delivery_Window != 'Spot'"
        ).fetchall()
        con.close()
        windows = sorted([r[0] for r in rows], key=_window_sort_key)
        if windows:
            return windows[1] if len(windows) >= 2 else windows[0]
    except Exception:
        pass
    return "Jul-26"


def derive_display_windows(db_path: Path, comparison_window: str) -> list[str]:
    """
    Return the three display columns for the matrix:
        [comparison_window,  comparison_window+1,  comparison_window+2]

    The comparison_window is the LEFT (prompt) column. The spread is always
    computed on comparison_window. The two flanking columns show the forward
    curve beyond it.

    Example: comparison_window = Jul-26  → columns: Jul-26 | Aug-26 | Sep-26
    Example: comparison_window = Sep-26  → columns: Sep-26 | Oct-26 | Nov-26
    """
    from dateutil.relativedelta import relativedelta

    try:
        base = datetime.strptime(comparison_window, "%b-%y")
    except ValueError:
        base = datetime.strptime("Jul-26", "%b-%y")

    return [
        base.strftime("%b-%y"),
        (base + relativedelta(months=1)).strftime("%b-%y"),
        (base + relativedelta(months=2)).strftime("%b-%y"),
    ]
