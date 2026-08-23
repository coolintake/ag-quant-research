# Corn Historical Volatility Analysis

Analysis tool for Corn options implied volatility around USDA Prospective Plantings reports.

## Overview

This project analyzes historical Corn (CBOT ZC) options volatility patterns around USDA Prospective Plantings report releases using event window analysis (T-1, T, T+1).

## Files

- **CZ_historicalvol.py** - Main analysis script with API integration, Black-76 IV calculation, and data processing
- **MASSIVE_API_NOTES.md** - Important notes about API integration and setup requirements

## Features

- ✅ Historical options data retrieval (Massive API)
- ✅ Black-76 implied volatility calculation
- ✅ Event window analysis (T-1, T, T+1 trading days)
- ✅ Moneyness normalization (K/F)
- ✅ Quality filtering and data cleaning
- ✅ CSV/JSON export for surface construction
- ✅ USDA QuickStats validation

## Events Analyzed

- March 31, 2023 (Prospective Plantings 2023)
- March 28, 2024 (Prospective Plantings 2024)

## Usage

### Test API Connectivity
```bash
python CZ_historicalvol.py --test-api
```

### Run Full Analysis
```bash
python CZ_historicalvol.py
```

## Output

For each event, generates:
- `corn_event_YYYY-MM-DD_T-1.csv` (pre-event data)
- `corn_event_YYYY-MM-DD_T.csv` (event day data)
- `corn_event_YYYY-MM-DD_T+1.csv` (post-event data)
- `corn_event_YYYY-MM-DD_summary.json` (metadata)

## Requirements

- Python 3.7+
- numpy
- pandas
- scipy
- requests

## Next Steps

⚠️ **Important**: Review [MASSIVE_API_NOTES.md](MASSIVE_API_NOTES.md) for API endpoint configuration.

## API Keys

- Massive API: Configured in script
- USDA QuickStats: Configured in script

---

Created: 2026-01-24
