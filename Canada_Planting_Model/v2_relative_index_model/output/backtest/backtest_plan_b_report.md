# Plan-B alternatives — backtest vs CANSIM seeded acres

Panel: V2 relative cash RPA index, MB / SK / AB, 13 province-crop series (CWRS, CPSR, Durum, Canola, Yellow Peas). Tiers: T1 Canola/CWRS, T2 Durum/Peas, T3 CPSR. Barley, Oats and Lentils have no PDQ series and aren't in the V2 panel. Headline window: Run 3 (Feb 15 – Mar 15). 2026 acres are preliminary.

## 1. Model performance (out-of-sample)

### Run 3, expanding window 2022–2026 (fit on years before T, forecast T)

| Model | N | MAPE | Median APE | WAPE | RMSE (k ac) | RMSE (pp) | Hit rate |
|---|---|---|---|---|---|---|---|
| Legacy pooled OLS | 65 | 10.7% | 6.7% | 6.0% | 313 | 15.9 | 49% |
| Legacy, no intercept (y = b·x) | 65 | 9.8% | 5.6% | 5.2% | 293 | 15.6 | 69% |
| Alt 1 fixed effects | 65 | 13.6% | 5.4% | 6.8% | 419 | 20.0 | 58% |
| Alt 2 tier slopes | 65 | 11.3% | 6.4% | 5.8% | 296 | 17.7 | 51% |
| Alt 3 mixed (crop-province) | 65 | 11.8% | 6.8% | 6.1% | 342 | 17.9 | 51% |
| Alt 3b mixed (crop) | 65 | 12.1% | 7.0% | 6.3% | 330 | 17.8 | 51% |
| No-change benchmark | 65 | 10.4% | 7.2% | 5.7% | 322 | 15.9 | — |

### Run 3, expanding window 2022–2025 (final CANSIM years only)

| Model | N | MAPE | Median APE | WAPE | RMSE (k ac) | RMSE (pp) | Hit rate |
|---|---|---|---|---|---|---|---|
| Legacy pooled OLS | 52 | 8.9% | 6.3% | 5.8% | 314 | 11.4 | 52% |
| Legacy, no intercept (y = b·x) | 52 | 8.1% | 5.1% | 4.9% | 283 | 11.0 | 63% |
| Alt 1 fixed effects | 52 | 12.3% | 5.2% | 6.6% | 428 | 18.1 | 56% |
| Alt 2 tier slopes | 52 | 9.6% | 6.2% | 5.6% | 292 | 13.9 | 56% |
| Alt 3 mixed (crop-province) | 52 | 10.3% | 6.8% | 5.9% | 350 | 14.6 | 54% |
| Alt 3b mixed (crop) | 52 | 10.6% | 7.0% | 6.2% | 334 | 14.4 | 54% |
| No-change benchmark | 52 | 8.4% | 5.1% | 5.0% | 295 | 11.3 | — |

### Run 3, leave-one-year-out 2020–2025

| Model | N | MAPE | Median APE | WAPE | RMSE (k ac) | RMSE (pp) | Hit rate |
|---|---|---|---|---|---|---|---|
| Legacy pooled OLS | 78 | 9.6% | 6.7% | 6.7% | 437 | 16.3 | 59% |
| Legacy, no intercept (y = b·x) | 78 | 9.2% | 6.0% | 6.0% | 411 | 16.1 | 67% |
| Alt 1 fixed effects | 78 | 10.2% | 5.3% | 6.5% | 431 | 17.8 | 65% |
| Alt 2 tier slopes | 78 | 10.7% | 7.4% | 6.7% | 379 | 18.0 | 58% |
| Alt 3 mixed (crop-province) | 78 | 10.0% | 6.3% | 6.5% | 430 | 17.1 | 62% |
| Alt 3b mixed (crop) | 78 | 10.5% | 5.9% | 6.4% | 417 | 17.6 | 58% |
| No-change benchmark | 78 | 9.2% | 7.4% | 5.9% | 332 | 15.9 | — |

### All windows, expanding 2022–2026: WAPE / hit rate

| Model | R1 | R2 | R3 |
|---|---|---|---|
| Legacy pooled OLS | 6.3% / 49% | 6.3% / 49% | 6.0% / 49% |
| Legacy, no intercept (y = b·x) | 5.4% / 65% | 5.4% / 68% | 5.2% / 69% |
| Alt 1 fixed effects | 7.2% / 54% | 7.1% / 57% | 6.8% / 58% |
| Alt 2 tier slopes | 6.4% / 51% | 6.4% / 52% | 5.8% / 51% |
| Alt 3 mixed (crop-province) | 6.5% / 46% | 6.4% / 49% | 6.1% / 51% |
| Alt 3b mixed (crop) | 6.6% / 43% | 6.5% / 46% | 6.3% / 51% |
| No-change benchmark | 5.7% / — | 5.6% / — | 5.7% / — |

### In-sample fit, Run 3, 2020–2025 (N = 78)

| Model | Parameters | R² | Adjusted R² | Marginal R² (fixed part) | RE structure / SD(slope) |
|---|---|---|---|---|---|
| Legacy pooled OLS | 2 | 0.030 | 0.017 | — | — |
| Legacy, no intercept (y = b·x) | 2 | 0.039 | 0.026 | — | — |
| Alt 1 fixed effects | 14 | 0.152 | -0.021 | — | — |
| Alt 2 tier slopes | 4 | 0.033 | -0.006 | — | — |
| Alt 3 mixed (crop-province) | 2 fixed + random int/slope (13 groups) | 0.030 | — | 0.030 | pooled / 0.000 |
| Alt 3b mixed (crop) | 2 fixed + random int/slope (5 groups) | 0.038 | — | 0.030 | RI+RS / 0.019 |

Mixed models have no adjusted R². The R² shown uses fixed effects plus the shrunk group effects; the marginal R² uses the fixed part only.

## 2. Parameter estimates (Run 3, 2020–2025)

| Model | Term | Estimate | SE | p-value |
|---|---|---|---|---|
| Legacy pooled OLS | a | +0.0225 | 0.0180 | 0.216 |
| Legacy pooled OLS | b | +0.0857 | 0.0563 | 0.132 |
| Legacy, no intercept (y = b·x) | b | +0.0979 | 0.0556 | 0.082 |
| Alt 1 fixed effects | a | +0.0039 | 0.0652 | 0.952 |
| Alt 1 fixed effects | b | +0.0954 | 0.0582 | 0.106 |
| Alt 2 tier slopes | a | +0.0221 | 0.0183 | 0.231 |
| Alt 2 tier slopes | b | T1 | +0.0946 | 0.0754 | 0.214 |
| Alt 2 tier slopes | b | T2 | +0.0668 | 0.0869 | 0.444 |
| Alt 2 tier slopes | b | T3 | +0.2700 | 0.4248 | 0.527 |
| Alt 3 mixed (crop-province) | a (fixed) | +0.0225 | 0.0180 | 0.216 |
| Alt 3 mixed (crop-province) | b (fixed) | +0.0857 | 0.0563 | 0.132 |
| Alt 3b mixed (crop) | a (fixed) | +0.0229 | 0.0187 | 0.221 |
| Alt 3b mixed (crop) | b (fixed) | +0.0872 | — | — |
| Alt 3b mixed (crop) | b | CANOLA (shrunk) | +0.0851 | — | — |
| Alt 3b mixed (crop) | a | CANOLA (shrunk) | +0.0208 | — | — |
| Alt 3b mixed (crop) | b | CPSR (shrunk) | +0.0877 | — | — |
| Alt 3b mixed (crop) | a | CPSR (shrunk) | +0.0264 | — | — |
| Alt 3b mixed (crop) | b | CWRS (shrunk) | +0.0898 | — | — |
| Alt 3b mixed (crop) | a | CWRS (shrunk) | +0.0210 | — | — |
| Alt 3b mixed (crop) | b | DURUM (shrunk) | +0.0885 | — | — |
| Alt 3b mixed (crop) | a | DURUM (shrunk) | +0.0250 | — | — |
| Alt 3b mixed (crop) | b | Y_PEAS (shrunk) | +0.0849 | — | — |
| Alt 3b mixed (crop) | a | Y_PEAS (shrunk) | +0.0213 | — | — |

**Alt 1 fixed effects** (series intercept a_i = base + shift; base series = AB_CANOLA, a = +0.0039):

| Series | a_i | p (shift vs base) |
|---|---|---|
| AB_CPSR | +0.0448 | 0.659 |
| AB_CWRS | -0.0128 | 0.857 |
| AB_DURUM | +0.0996 | 0.303 |
| AB_Y_PEAS | -0.0285 | 0.726 |
| MB_CANOLA | -0.0182 | 0.811 |
| MB_CWRS | -0.0015 | 0.953 |
| MB_Y_PEAS | +0.0854 | 0.380 |
| SK_CANOLA | -0.0081 | 0.897 |
| SK_CPSR | +0.1428 | 0.137 |
| SK_CWRS | +0.0028 | 0.990 |
| SK_DURUM | +0.0327 | 0.756 |
| SK_Y_PEAS | -0.0579 | 0.505 |

### Slope stability across expanding-window fits (through 2021 … 2025), Run 3

| Model | Slope | Min | Max | SD |
|---|---|---|---|---|
| Alt 1 fixed effects | b | +0.018 | +0.125 | 0.041 |
| Alt 2 tier slopes | T1 | +0.070 | +0.102 | 0.013 |
| Alt 2 tier slopes | T2 | -0.029 | +0.072 | 0.048 |
| Alt 2 tier slopes | T3 | +0.270 | +1.787 | 0.598 |
| Alt 3 mixed (crop-province) | b | -0.010 | +0.198 | 0.080 |
| Alt 3b mixed (crop) | b | -0.019 | +0.283 | 0.113 |
| Legacy pooled OLS | b | +0.033 | +0.103 | 0.027 |
| Legacy, no intercept (y = b·x) | b | +0.072 | +0.120 | 0.018 |

Random-effects structure actually estimable per expanding fold (RI+RS = random intercept and slope; RI = intercept only, slope variance not identifiable; pooled = no group variance):

| Model | through 2021 | through 2022 | through 2023 | through 2024 | through 2025 |
|---|---|---|---|---|---|
| Alt 3 mixed (crop-province) | RI+RS | RI+RS | RI+RS | RI+RS | pooled |
| Alt 3b mixed (crop) | RI+RS | RI+RS | RI+RS | RI+RS | RI+RS |

## 3. Crop-level breakdown (Run 3, expanding 2022–2026): MAPE, and change vs legacy

| Crop | Legacy pooled OLS | Legacy, no intercept (y = b·x) | Alt 1 fixed effects | Alt 2 tier slopes | Alt 3 mixed (crop-province) | Alt 3b mixed (crop) | No-change benchmark |
|---|---|---|---|---|---|---|---|
| Canola | 6.1% | 4.2% (-1.9) | 5.4% (-0.8) | 5.6% (-0.5) | 5.2% (-0.9) | 4.9% (-1.2) | 4.7% (-1.4) |
| CPSR Wheat | 21.9% | 22.4% (+0.5) | 34.0% (+12.1) | 26.1% (+4.2) | 28.1% (+6.2) | 28.5% (+6.6) | 22.4% (+0.5) |
| CWRS Wheat | 4.1% | 3.9% (-0.2) | 6.1% (+2.0) | 4.1% (+0.0) | 5.2% (+1.2) | 4.8% (+0.8) | 4.6% (+0.5) |
| Durum Wheat | 7.2% | 7.6% (+0.4) | 9.2% (+2.1) | 7.2% (+0.0) | 8.1% (+0.9) | 9.1% (+2.0) | 8.0% (+0.8) |
| Yellow Peas | 16.6% | 14.3% (-2.3) | 18.6% (+2.0) | 16.9% (+0.3) | 16.6% (-0.0) | 17.4% (+0.8) | 15.4% (-1.2) |

In brackets: change in MAPE vs legacy, in percentage points (negative = better).

### By forecast year (Run 3): WAPE

| Year | Legacy pooled OLS | Legacy, no intercept (y = b·x) | Alt 1 fixed effects | Alt 2 tier slopes | Alt 3 mixed (crop-province) | Alt 3b mixed (crop) | No-change benchmark |
|---|---|---|---|---|---|---|---|
| 2022 | 7.8% | 6.1% | 10.4% | 6.9% | 9.5% | 10.0% | 7.4% |
| 2023 | 5.5% | 6.1% | 7.7% | 5.7% | 5.8% | 5.8% | 6.7% |
| 2024 | 5.4% | 3.3% | 4.2% | 4.6% | 4.5% | 4.9% | 3.4% |
| 2025 | 4.6% | 4.2% | 4.5% | 5.3% | 4.0% | 4.1% | 2.7% |
| 2026 (prelim.) | 6.6% | 6.4% | 7.4% | 6.5% | 6.6% | 6.6% | 8.1% |

## 4. Recommendation

Best out-of-sample WAPE (Run 3, expanding 2022–2026): **Legacy, no intercept (y = b·x)** at 5.2%, vs 6.0% for the legacy model and 5.7% for no change. Its hit rate is 69% (legacy 49%) and its slope SD across folds is 0.018 (legacy 0.027).

- **None of the three proposed alternatives improves consistently on the legacy model.** Each wins on some measure in one test and loses in another. Differences are a few tenths of a WAPE point.
  - Alt 1 (fixed effects) overfits: 14 parameters on 78 points, a negative adjusted R² and the worst expanding-window errors.
  - Alt 2 (tier slopes) trims expanding-window WAPE slightly but worsens MAPE and leave-one-year-out WAPE. It leaves Tiers 1 and 2 close to the pooled slope. Tier 3 rests on CPSR alone (12 points), with a slope that swings widely across folds.
  - Alt 3 (mixed) shrinks to the pooled model: the crop-province random-effects variance is estimated at zero. The data can't support slopes that differ by series.
- **The legacy model's weakness is its intercept, not its pooling.** The fitted drift (a ≈ +2%) is insignificant (p ≈ 0.2) but pushes most forecasts upward, cutting directional accuracy to about 50%. Dropping it restores the signal's direction and lowers the errors in every window.
- **Magnitude skill is still thin.** No-change stays competitive in leave-one-year-out WAPE, and the slope is only marginally significant. Plan-B should stay the automated fallback to analyst Plan-A.
