"""
config.py
=========
Centralized configuration for the Corn Volatility Engine: connection
parameters, file paths, filtering thresholds, and agricultural calendar
constants (WASDE report dates, crop-season windows).
"""

import logging

CONFIG = {
    # ── Contract targeting ──────────────────────────────────────────
    'target_futures_months': ['202603', '202605', '202607', '202609', '202612'],
    'trading_class': 'OZC',        # Standard Corn Options (rejects weekly/serial noise)

    # ── Mode & connectivity ─────────────────────────────────────────
    'offline_mode': True,          # True = load from market_data/, False = live TWS
    'market_data_type': 1,         # 1 = Real-time, 3 = Delayed

    # ── Data quality thresholds ─────────────────────────────────────
    'max_relative_spread': 0.25,   # Relaxed for commodity markets
    'strike_otm_range': 0.50,      # % either side of underlying to scan
    'atm_threshold_pct': 0.015,    # 1.5% band treated as ATM
    'min_dte': 2,                  # Drop contracts with DTE <= 2

    # ── Pricing ──────────────────────────────────────────────────────
    'risk_free_rate': 0.046,

    # ── I/O ──────────────────────────────────────────────────────────
    'data_path': "market_data/corn_options_surface_historical.xlsx",
    'log_level': logging.INFO,

    # ── Agricultural calendar (domain/calendar.py) ──────────────────
    'wasde_report_dates': [
        '2026-02-10', '2026-03-10', '2026-04-09', '2026-05-12', '2026-06-11',
        '2026-07-10', '2026-08-12', '2026-09-11', '2026-10-09', '2026-11-10', '2026-12-10'
    ],
    'seasons': {
        'Planting': [4, 5],
        'Pollination': [6, 7],
        'Harvest': [9, 10, 11]
    },
}
