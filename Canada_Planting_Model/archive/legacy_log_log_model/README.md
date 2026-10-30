# Legacy log-log OLS acreage model (archived)

This folder holds the **deprecated log-log OLS acreage allocation model**. It used dynamic elasticities calibrated on
N = 6 annual changes (2020–2025). It was archived on 2026-10-07, before the project switched to the **Base-100 Cash
RPA Index** framework. Nothing here is imported by the active code in `src/`.

## What the model did

- **Margin signal:** ln(R_T / R̄_T−1), where R = RPA_crop / RPA_anchor, RPA = 21-year trend yield × price − (seed + fertilizer + chemicals), and R̄ = the ratio's mean over T−5 … T−1 in the same pricing window.
- **Forecast:** Â_T = A_T−1 × exp(α₀ + β · signal), with anchor tiers T0 CWRS (vs Canola), T1 vs CWRS, and T2 Lentils vs Durum and Soybeans and Flax vs Canola.
- **Calibration:** α₀ and β fitted by OLS separately for each province, crop and window (Run 1/2/3). β was limited to [0.10, 2.00], with INVALID_INVERTED_SLOPE, AT_LOWER_BOUND, UNCALIBRATED and EXCLUDED_REGIONAL_CROP flags.
- **Land pool:** the total was fitted to cropland plus summer fallow above its rolling 25-year minimum. Fallow absorbed any residual.

## Contents

| Path | What it is |
|---|---|
| `model_crop_wallet_step1.md` | Specification of the log-log model (sections 1–10) |
| `src/regression_engine.py` | Ratios, baselines, bounded OLS fits, flags, vintage fits and averages |
| `src/allocator.py` | Cropland basket, rolling fallow floor, land pool scaling, §9 flags |
| `src/run_calibration.py` | Province × window calibration loop, reports and charts |
| `src/run_forecast.py` | One-step-forward forecast and evaluation against CANSIM |
| `src/charts.py` | R² by window and fit scatter charts |
| `src/excel_exports.py` | `rpa_elasticity_matrices.xlsx` and `yield_trend_diagnostics.xlsx` writers |
| `src/settings_snapshot.py` | Copy of `src/settings.py` as of archiving (tiers, β bounds, exclusions) |
| `output/` | Last outputs: calibration reports, parameter matrices, the 2026 Run 3 forecast vs CANSIM, the land pool balance sheet, workbooks and charts |

## Last results (for reference)

The 2026 Run 3 forecast used Feb 15 – Mar 15, 2026 PDQ prompt bids and the 2025/26 Run 3 parameters. Its acreage-weighted error was 7.3% overall, against 8.7% for a no-change forecast:

| Province | Model | No-change |
|---|---|---|
| MB | 10.5% | 10.3% |
| SK | 7.5% | 8.8% |
| AB | 5.5% | 7.9% |

Known weaknesses:

- Many β values sat on the 0.10 floor, so those crops barely responded to price.
- Summer fallow absorbed any shrinkage in forecast cropland, which overstated fallow (SK 1.09M ac implied vs 613k actual).
- The six-year sample left few slopes significant.

## Running it again

These modules import `settings`, `data_loader` and `prompt_prices`, which stay in the active `src/`. To run them, copy
them back into `src/` (and restore the tier/bound constants from `settings_snapshot.py` if `settings.py` has changed).
