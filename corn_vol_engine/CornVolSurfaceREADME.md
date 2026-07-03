# Corn Volatility Surface Engine

A Black-76 implied volatility surface engine for CBOT corn futures options
(ZC), with dual-mode live/offline data acquisition, arbitrage validation,
and an interactive Plotly dashboard.

## What it does

- **Dual-mode operation**: connects live to IBKR TWS via async requests, or
  loads from an offline Excel/CSV snapshot when markets are closed or TWS
  is unavailable.
- **Multi-expiry, moneyness-normalized**: prices each expiry against its own
  underlying futures contract using K/F moneyness, capturing the full crop
  cycle (planting, pollination, harvest) rather than a single underlying.
- **Black-76 implied volatility**: solved via Brent's method root-finding.
- **Spline surface fitting**: `SmoothBivariateSpline` across moneyness and
  time to expiry.
- **Arbitrage validation**: butterfly (within-expiry convexity) and calendar
  (cross-expiry total variance monotonicity) checks, with any strike relying
  on a theoretical fallback price (no live market quote) flagged and
  excluded from triggering a violation.
- **Data quality guardrails**: strict standard-monthly (OZC) contract
  filtering, minimum volume/DTE thresholds, stale-quote rejection (>30s),
  and a USDA WASDE report time-lock to avoid ingesting toxic data during a
  release.
- **Visualization dashboard**: 3D vol surface, smile-by-expiry grid, ATM
  term structure, put/call skew analysis, and a residual (Market IV vs.
  Model IV) trading map.

## Architecture

```
corn_vol_engine/
├── config.py                # Centralized configuration, paths, constants
├── main.py                  # Coordinator / application entry point
├── data/
│   └── harvester.py         # IBKR TWS async connectivity + offline loader
├── models/
│   ├── black76.py           # Stateless Black-76 pricing & IV solver
│   └── surface.py           # Data cleaning, spline fitting, arbitrage checks
├── visualization/
│   └── presenter.py         # Plotly dashboards, residual map, skew report
├── domain/
│   └── calendar.py          # WASDE report locks, crop-season context
└── market_data/             # Local Excel/CSV snapshots (gitignored)
```

## Setup

A virtual environment is recommended to keep dependencies isolated from
other projects on your machine:

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

## Usage

Set the mode in `config.py`:

```python
'offline_mode': True   # Load from market_data/ -- no TWS connection needed
'offline_mode': False  # Connect live to IBKR TWS
```

For offline mode, place a data file at `market_data/corn_options_surface_historical.xlsx`
(or update `CONFIG['data_path']`), then run:

```bash
python main.py
```

For live mode, ensure TWS or IB Gateway is running with the API enabled on
port 7496 before running.
<img width="975" height="842" alt="image" src="https://github.com/user-attachments/assets/a287fa59-c4b0-4113-9ce4-a7098bf69fef" />
<img width="975" height="900" alt="image" src="https://github.com/user-attachments/assets/dbe875d4-3952-440b-99d9-522c249283e3" />

## A note on data quality (offline mode)

Historical snapshots pulled without live bid/ask quotes will show `bid`/`ask`
as invalid (e.g. sentinel values like `-100`), causing every price to fall
back to `last`. Because `last`-trade prints across different strikes can be
asynchronous -- especially on thin, low-volume contracts -- arbitrage checks
run against such a snapshot may flag violations that reflect stale timing
mismatches rather than genuine live mispricing. This is expected behavior,
not a bug; live-mode data with synchronized bid/ask quotes avoids this.

## Requirements

`ib_insync`, `pandas`, `numpy`, `scipy`, `plotly`, `openpyxl`, `nest_asyncio`
(see `requirements.txt` for versions).
