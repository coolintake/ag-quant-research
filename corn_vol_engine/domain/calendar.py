"""
domain/calendar.py
===================
Agricultural calendar logic for Corn: USDA WASDE report lock windows
(to prevent ingesting toxic data during a report release) and crop-season
phase mapping (Planting / Pollination / Harvest) for chart context.
"""

from datetime import datetime

from config import CONFIG


def is_wasde_lock() -> bool:
    """Checks if a WASDE report is currently in progress (Market Lock)."""
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    if date_str in CONFIG['wasde_report_dates']:
        # CT Time Check: 10:55 AM to 11:15 AM
        # Note: Local system time is used, assumed to be synced with CT or managed by user.
        start_lock = now.replace(hour=10, minute=55, second=0, microsecond=0)
        end_lock = now.replace(hour=11, minute=15, second=0, microsecond=0)
        if start_lock <= now <= end_lock:
            return True
    return False


def get_seasonal_context() -> str:
    """Returns the current seasonal phase for the crop (e.g. 'Pollination')."""
    now = datetime.now()
    month = now.month
    for season, months in CONFIG['seasons'].items():
        if month in months:
            return season
    return "Off-Season"
