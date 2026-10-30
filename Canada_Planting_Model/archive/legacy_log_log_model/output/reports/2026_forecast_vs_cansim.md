# 2026 acreage forecast vs CANSIM — Run 3 (Feb 15 - Mar 15), parameters from the 2025/2026 calibration

Generated 2026-10-07 by `src/run_forecast.py` · spec `model_crop_wallet_step1.md` §8–§9. Parameters: matches the 2025/2026 tab of rpa_elasticity_matrices.xlsx (RUN3) exactly.

**Prices** (2026 RUN3, crop lines by tier: BUDGET_FALLBACK 12, PDQ_PROMPT 11, MISSING_PRICE_DATA 4). CWRS, Durum (S ALTA / SW SASK only), Canola and Yellow Peas use PDQ prompt bids from the window. Barley, Oats, Lentils, Soybeans and Flax have no PDQ series and use the provincial guide price (BUDGET_FALLBACK) by design. MISSING_PRICE_DATA lines are regional exclusions held at 2025.

## Accuracy summary

Errors vs CANSIM for the crops whose elasticity was applied. The benchmark is the no-change forecast (A_T = A_T-1). MAPE = mean absolute % error (small crops weigh as much as Canola). WAPE = Σ|F − A| / ΣA (acreage-weighted, the better headline).

| Province | Forecast lines | WAPE model | WAPE no-change | MAPE model | MAPE no-change | Total cropland error |
|---|---|---|---|---|---|---|
| Manitoba | 7 | 10.5% | 10.3% | 25.0% | 22.5% | +1.2% |
| Saskatchewan | 9 | 7.5% | 8.8% | 18.9% | 13.8% | -0.6% |
| Alberta | 7 | 5.5% | 7.9% | 7.5% | 11.3% | -1.2% |
| **All** | 23 | 7.3% | 8.7% | 17.3% | 15.7% | |

## Land pool

| Province | Cropland 2025 | Fallow 2025 | Fallow floor (year) | Land pool | Unconstrained S | Case | Scale | Allocated cropland | Implied fallow 2026 | CANSIM fallow 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| Manitoba | 10,180.4 | 49.9 | 49.9 (2025) | 10,180.4 | 10,249.4 | B | 0.9933 | 10,180.4 | 49.9 | 102.8 |
| Saskatchewan | 37,298.0 | 607.7 | 590.8 (2024) | 37,314.9 | 36,819.8 | A | 1.0000 | 36,819.8 | 1,085.9 | 612.7 |
| Alberta | 21,171.8 | 248.6 | 248.6 (2025) | 21,171.8 | 20,943.9 | A | 1.0000 | 20,943.9 | 476.5 | 261.6 |

Acres in thousands. Case A = the unconstrained basket fits inside the pool, so nothing is scaled and fallow absorbs the change.

## Variance by crop and province

Acres in thousands. Variance = allocated forecast − CANSIM. Unconstrained = 2025 × exp(α₀ + β·x). — = not published.

### Manitoba (MB)

| Line | Status | β / α₀ | x = ln(R/R̄) | Price source (crop / anchor) | 2025 | Unconstrained | Allocated | CANSIM 2026 | Variance | % error | No-change % error | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CWRS Wheat | APPLY_ELASTICITY | 0.146 / +0.007 | -0.091 | PDQ_PROMPT / PDQ_PROMPT | 2,817.8 | 2,800.6 | 2,781.8 | 2,628.4 | 153.4 | +5.8% | +7.2% |  |
| Durum Wheat | EXCLUDED_REGIONAL_CROP | — | — | MISSING_PRICE_DATA / PDQ_PROMPT | 56.0 | 56.0 | 55.6 | 74.3 | -18.7 | -25.1% | -24.6% |  |
| Other wheat (CPSR, CNHR, soft, winter, other) | UNMODELLED_HELD | — | — | — | 427.4 | 427.4 | 424.5 | 322.4 | 102.1 | +31.7% | +32.6% |  |
| Barley (feed budget) | APPLY_ELASTICITY | 0.100 / -0.012 | -0.053 | BUDGET_FALLBACK / PDQ_PROMPT | 309.4 | 304.0 | 301.9 | 302.2 | -0.3 | -0.1% | +2.4% | FLAG_LOWER_BOUND_BREACH (-2.4% vs min 309,400) |
| Oats | APPLY_ELASTICITY | 0.636 / +0.027 | +0.151 | BUDGET_FALLBACK / PDQ_PROMPT | 555.9 | 628.7 | 624.5 | 400.8 | 223.7 | +55.8% | +38.7% |  |
| Corn for grain | UNMODELLED_HELD | — | — | — | 619.4 | 619.4 | 615.2 | 692.6 | -77.4 | -11.2% | -10.6% |  |
| Canola | APPLY_ELASTICITY | 0.100 / -0.016 | +0.047 | PDQ_PROMPT / PDQ_PROMPT | 3,043.3 | 3,008.0 | 2,987.8 | 3,220.0 | -232.2 | -7.2% | -5.5% | FLAG_LOWER_BOUND_BREACH (-1.8% vs min 3,043,300) |
| Flax | APPLY_ELASTICITY | 0.100 / -0.149 | -0.014 | BUDGET_FALLBACK / PDQ_PROMPT | 34.3 | 29.5 | 29.3 | 38.3 | -9.0 | -23.5% | -10.4% | FLAG_LOWER_BOUND_BREACH (-14.5% vs min 34,300) |
| Soybeans | APPLY_ELASTICITY | 0.100 / +0.034 | +0.051 | BUDGET_FALLBACK / PDQ_PROMPT | 1,656.1 | 1,722.9 | 1,711.3 | 1,924.3 | -213.0 | -11.1% | -13.9% | FLAG_UPPER_BOUND_BREACH (+3.3% vs max 1,656,100) |
| Yellow Peas | APPLY_ELASTICITY | 0.431 / +0.035 | -0.177 | PDQ_PROMPT / PDQ_PROMPT | 196.0 | 188.0 | 186.8 | 109.1 | 77.7 | +71.2% | +79.7% |  |
| Lentils (all; red lentil budget) | EXCLUDED_REGIONAL_CROP | — | — | MISSING_PRICE_DATA / MISSING_PRICE_DATA | 0.0 | 0.0 | 0.0 | — | — | — | — |  |
| Other field crops | UNMODELLED_HELD | — | — | — | 464.8 | 464.8 | 461.7 | 346.0 | 115.7 | +33.4% | +34.3% |  |
| **Total cropland** | | | | | 10,180.4 | 10,249.4 | 10,180.4 | 10,058.4 | 122.0 | +1.2% | +1.2% | |

### Saskatchewan (SK)

| Line | Status | β / α₀ | x = ln(R/R̄) | Price source (crop / anchor) | 2025 | Unconstrained | Allocated | CANSIM 2026 | Variance | % error | No-change % error | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CWRS Wheat | APPLY_ELASTICITY | 0.100 / +0.013 | -0.123 | PDQ_PROMPT / PDQ_PROMPT | 8,004.9 | 8,008.1 | 8,008.1 | 7,676.9 | 331.2 | +4.3% | +4.3% |  |
| Durum Wheat | APPLY_ELASTICITY | 0.129 / +0.025 | -0.517 | PDQ_PROMPT / PDQ_PROMPT | 5,118.8 | 4,911.0 | 4,911.0 | 4,652.1 | 258.9 | +5.6% | +10.0% |  |
| Other wheat (CPSR, CNHR, soft, winter, other) | UNMODELLED_HELD | — | — | — | 895.0 | 895.0 | 895.0 | 862.6 | 32.4 | +3.8% | +3.8% |  |
| Barley (feed budget) | APPLY_ELASTICITY | 0.205 / -0.033 | +0.275 | BUDGET_FALLBACK / PDQ_PROMPT | 2,253.9 | 2,306.1 | 2,306.1 | 2,538.7 | -232.6 | -9.2% | -11.2% |  |
| Oats | APPLY_ELASTICITY | 0.409 / -0.089 | -0.091 | BUDGET_FALLBACK / PDQ_PROMPT | 1,285.4 | 1,133.4 | 1,133.4 | 1,092.3 | 41.1 | +3.8% | +17.7% |  |
| Corn for grain | EXCLUDED_REGIONAL_CROP | — | — | — | 34.1 | 34.1 | 34.1 | — | — | — | — |  |
| Canola | APPLY_ELASTICITY | 0.100 / -0.001 | +0.080 | PDQ_PROMPT / PDQ_PROMPT | 12,187.4 | 12,272.6 | 12,272.6 | 13,386.3 | -1,113.7 | -8.3% | -9.0% |  |
| Flax | APPLY_ELASTICITY | 0.118 / -0.028 | +0.085 | BUDGET_FALLBACK / PDQ_PROMPT | 536.9 | 527.4 | 527.4 | 535.9 | -8.5 | -1.6% | +0.2% |  |
| Soybeans | APPLY_ELASTICITY | 0.529 / +0.054 | +0.579 | BUDGET_FALLBACK / PDQ_PROMPT | 95.5 | 137.0 | 137.0 | 63.3 | 73.7 | +116.4% | +50.9% |  |
| Yellow Peas | APPLY_ELASTICITY | 0.100 / -0.066 | -0.970 | PDQ_PROMPT / PDQ_PROMPT | 1,773.0 | 1,506.7 | 1,506.7 | 1,643.7 | -137.0 | -8.3% | +7.9% | FLAG_LOWER_BOUND_BREACH (-5.7% vs min 1,597,200) |
| Lentils (all; red lentil budget) | APPLY_ELASTICITY | 0.155 / -0.016 | +0.061 | BUDGET_FALLBACK / PDQ_PROMPT | 3,808.6 | 3,783.9 | 3,783.9 | 3,363.5 | 420.4 | +12.5% | +13.2% |  |
| Other field crops | UNMODELLED_HELD | — | — | — | 1,304.5 | 1,304.5 | 1,304.5 | 1,230.2 | 74.3 | +6.0% | +6.0% |  |
| **Total cropland** | | | | | 37,298.0 | 36,819.8 | 36,819.8 | 37,045.5 | -225.7 | -0.6% | +0.7% | |

### Alberta (AB)

| Line | Status | β / α₀ | x = ln(R/R̄) | Price source (crop / anchor) | 2025 | Unconstrained | Allocated | CANSIM 2026 | Variance | % error | No-change % error | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CWRS Wheat | APPLY_ELASTICITY | 0.122 / -0.008 | -0.207 | PDQ_PROMPT / PDQ_PROMPT | 5,021.4 | 4,857.7 | 4,857.7 | 4,974.5 | -116.8 | -2.3% | +0.9% |  |
| Durum Wheat | APPLY_ELASTICITY | 0.556 / +0.085 | -0.371 | PDQ_PROMPT / PDQ_PROMPT | 1,327.4 | 1,176.1 | 1,176.1 | 1,101.0 | 75.1 | +6.8% | +20.6% |  |
| Other wheat (CPSR, CNHR, soft, winter, other) | UNMODELLED_HELD | — | — | — | 1,685.9 | 1,685.9 | 1,685.9 | 1,522.4 | 163.5 | +10.7% | +10.7% |  |
| Barley (feed budget) | APPLY_ELASTICITY | 0.107 / -0.019 | +0.113 | BUDGET_FALLBACK / PDQ_PROMPT | 3,304.9 | 3,281.3 | 3,281.3 | 3,700.6 | -419.3 | -11.3% | -10.7% | FLAG_LOWER_BOUND_BREACH (-0.7% vs min 3,304,900) |
| Oats | APPLY_ELASTICITY | 0.573 / -0.035 | -0.178 | BUDGET_FALLBACK / PDQ_PROMPT | 839.9 | 732.0 | 732.0 | 743.7 | -11.7 | -1.6% | +12.9% |  |
| Corn for grain | EXCLUDED_REGIONAL_CROP | — | — | — | 35.3 | 35.3 | 35.3 | 52.6 | -17.3 | -32.9% | -32.9% |  |
| Canola | APPLY_ELASTICITY | 0.239 / -0.003 | +0.184 | PDQ_PROMPT / PDQ_PROMPT | 6,208.4 | 6,468.5 | 6,468.5 | 6,636.7 | -168.2 | -2.5% | -6.5% |  |
| Flax | EXCLUDED_REGIONAL_CROP | — | — | MISSING_PRICE_DATA / PDQ_PROMPT | 48.0 | 48.0 | 48.0 | 85.2 | -37.2 | -43.7% | -43.7% |  |
| Soybeans | EXCLUDED_REGIONAL_CROP | — | — | MISSING_PRICE_DATA / PDQ_PROMPT | 0.0 | 0.0 | 0.0 | — | — | — | — |  |
| Yellow Peas | APPLY_ELASTICITY | 0.100 / -0.039 | -0.161 | PDQ_PROMPT / PDQ_PROMPT | 1,498.6 | 1,418.5 | 1,418.5 | 1,234.6 | 183.9 | +14.9% | +21.4% |  |
| Lentils (all; red lentil budget) | APPLY_ELASTICITY | 0.143 / +0.068 | -0.013 | BUDGET_FALLBACK / PDQ_PROMPT | 565.2 | 603.8 | 603.8 | 533.8 | 70.0 | +13.1% | +5.9% | FLAG_UPPER_BOUND_BREACH (+6.8% vs max 565,200) |
| Other field crops | UNMODELLED_HELD | — | — | — | 636.8 | 636.8 | 636.8 | 622.1 | 14.7 | +2.4% | +2.4% |  |
| **Total cropland** | | | | | 21,171.8 | 20,943.9 | 20,943.9 | 21,207.2 | -263.3 | -1.2% | -0.2% | |

## Notes

- **Basket**: StatCan 32-10-0359 seeded area. Wheat = 'Wheat, all including winter wheat remaining', split into CWRS, Durum and other wheat. All other principal field crops are included; tame hay and corn for silage are excluded. Lines that aren't forecast (other wheat, corn, other field crops, exclusions) are held at 2025.
- **CANSIM 2026** = the latest 2026 seeded-area estimate in the local 32-10-0359 extract.
- **Yields** use the 21-year trend evaluated at the forecast year, and **costs** come from the 2026 workbook.
- MB: unpublished series counted as 0 — 2025: Lentils; 2026: Lentils.
- SK: unpublished series counted as 0 — 2025: none; 2026: Corn for grain.
- AB: unpublished series counted as 0 — 2025: Soybeans; 2026: Soybeans.
