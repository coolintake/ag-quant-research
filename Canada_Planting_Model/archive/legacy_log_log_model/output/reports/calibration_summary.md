# Acreage elasticity re-estimation, 2019–2025 — run-specific parameters by province

Generated 2026-10-05 by `src/run_calibration.py` · spec `model_crop_wallet_step1.md` §5–§7.

ln(Acres_t / Acres_t-1) = α₀,j,p,w + β_j,p,w · ln(R_t / R̄_t-1). R = RPA_crop / RPA_anchor; RPA = trend yield × price − (seed + fertilizer + chemicals); R̄ = mean R over t-5 … t-1 in the same window. Tiers: T0 CWRS on the inverted CWRS/Canola ratio, T1 vs CWRS, T2 vs Durum or Canola. β is the bounded estimate on [0.10, 2.00]. **INVALID_INVERTED_SLOPE** = OLS β ≤ 0: β shows the 0.10 floor with α₀ re-fitted (marked “prior”), and the negative value is never reported. **AT_LOWER_BOUND** = 0 < OLS β < 0.10. **LOW_N** = N < 5. **UNCALIBRATED** = N < 3. **N/A** = EXCLUDED_REGIONAL_CROP (no regression). Both hold acres at T-1. SE and p refer to the OLS slope.

## Run-specific parameters (each release uses its own run)

Cell = β / α₀ / R² / N from each run's own-sample fit.

### Manitoba (MB)

| Pair | RUN1 β / α₀ / R² / N | RUN2 β / α₀ / R² / N | RUN3 β / α₀ / R² / N | Flags |
|---|---|---|---|---|
| T0 · CWRS Wheat / Canola | 0.132 / 0.003 / 0.236 / 6 | 0.110 / 0.003 / 0.194 / 6 | 0.146 / 0.007 / 0.418 / 6 | — |
| T1 · Canola / CWRS Wheat | 0.100 / -0.016 / -0.074 / 6 | 0.100 (prior) / -0.016 / -0.241 / 6 | 0.100 / -0.016 / -0.192 / 6 | RUN1: AT_LOWER_BOUND; RUN2: INVALID_INVERTED_SLOPE; RUN3: AT_LOWER_BOUND |
| T1 · Durum Wheat / CWRS Wheat | N/A | N/A | N/A | EXCLUDED_REGIONAL_CROP |
| T1 · Yellow Peas / CWRS Wheat | 0.292 / 0.049 / 0.073 / 6 | 0.198 / 0.057 / 0.047 / 6 | 0.431 / 0.035 / 0.210 / 6 | — |
| T1 · Barley (feed budget) / CWRS Wheat | 0.100 (prior) / -0.012 / -0.111 / 6 | 0.100 (prior) / -0.013 / -0.123 / 6 | 0.100 (prior) / -0.012 / -0.180 / 6 | RUN1: INVALID_INVERTED_SLOPE; RUN2: INVALID_INVERTED_SLOPE; RUN3: INVALID_INVERTED_SLOPE |
| T1 · Oats / CWRS Wheat | 0.869 / 0.041 / 0.789 / 6 | 0.943 / 0.031 / 0.764 / 6 | 0.636 / 0.027 / 0.495 / 6 | — |
| T2 · Lentils (all; red lentil budget) / Durum Wheat | N/A | N/A | N/A | EXCLUDED_REGIONAL_CROP |
| T2 · Soybeans / Canola | 0.100 / 0.032 / 0.022 / 6 | 0.100 / 0.031 / -0.008 / 6 | 0.100 / 0.034 / -0.018 / 6 | RUN1: AT_LOWER_BOUND; RUN2: AT_LOWER_BOUND; RUN3: AT_LOWER_BOUND |
| T2 · Flax / Canola | 0.182 / -0.148 / 0.017 / 6 | 0.100 (prior) / -0.152 / -0.020 / 6 | 0.100 (prior) / -0.149 / -0.061 / 6 | RUN2: INVALID_INVERTED_SLOPE; RUN3: INVALID_INVERTED_SLOPE |

### Saskatchewan (SK)

| Pair | RUN1 β / α₀ / R² / N | RUN2 β / α₀ / R² / N | RUN3 β / α₀ / R² / N | Flags |
|---|---|---|---|---|
| T0 · CWRS Wheat / Canola | 0.100 / 0.010 / 0.043 / 6 | 0.100 / 0.010 / -0.024 / 6 | 0.100 / 0.013 / 0.062 / 6 | RUN1: AT_LOWER_BOUND; RUN2: AT_LOWER_BOUND; RUN3: AT_LOWER_BOUND |
| T1 · Canola / CWRS Wheat | 0.100 / -0.001 / 0.038 / 6 | 0.100 / 0.001 / -0.039 / 6 | 0.100 / -0.001 / 0.080 / 6 | RUN1: AT_LOWER_BOUND; RUN2: AT_LOWER_BOUND; RUN3: AT_LOWER_BOUND |
| T1 · Durum Wheat / CWRS Wheat | 0.100 / 0.018 / 0.058 / 6 | 0.100 / 0.023 / 0.164 / 6 | 0.129 / 0.025 / 0.514 / 6 | RUN1: AT_LOWER_BOUND; RUN2: AT_LOWER_BOUND |
| T1 · Yellow Peas / CWRS Wheat | 0.100 (prior) / -0.067 / -0.718 / 6 | 0.100 (prior) / -0.066 / -0.813 / 6 | 0.100 (prior) / -0.066 / -0.617 / 6 | RUN1: INVALID_INVERTED_SLOPE; RUN2: INVALID_INVERTED_SLOPE; RUN3: INVALID_INVERTED_SLOPE |
| T1 · Barley (feed budget) / CWRS Wheat | 0.272 / -0.025 / 0.478 / 6 | 0.307 / -0.025 / 0.371 / 6 | 0.205 / -0.033 / 0.233 / 6 | — |
| T1 · Oats / CWRS Wheat | 0.809 / -0.125 / 0.434 / 6 | 0.878 / -0.133 / 0.468 / 6 | 0.409 / -0.089 / 0.191 / 6 | — |
| T2 · Lentils (all; red lentil budget) / Durum Wheat | 0.100 / 0.010 / -0.045 / 6 | 0.100 / 0.003 / -0.041 / 6 | 0.155 / -0.016 / 0.087 / 6 | RUN1: AT_LOWER_BOUND; RUN2: AT_LOWER_BOUND |
| T2 · Soybeans / Canola | 0.686 / 0.061 / 0.648 / 6 | 0.602 / 0.041 / 0.581 / 6 | 0.529 / 0.054 / 0.632 / 6 | — |
| T2 · Flax / Canola | 0.311 / -0.003 / 0.333 / 6 | 0.174 / -0.025 / 0.121 / 6 | 0.118 / -0.028 / 0.083 / 6 | — |

### Alberta (AB)

| Pair | RUN1 β / α₀ / R² / N | RUN2 β / α₀ / R² / N | RUN3 β / α₀ / R² / N | Flags |
|---|---|---|---|---|
| T0 · CWRS Wheat / Canola | 0.100 / -0.011 / 0.039 / 6 | 0.100 / -0.011 / -0.024 / 6 | 0.122 / -0.008 / 0.109 / 6 | RUN1: AT_LOWER_BOUND; RUN2: AT_LOWER_BOUND |
| T1 · Canola / CWRS Wheat | 0.280 / -0.004 / 0.510 / 6 | 0.213 / 0.001 / 0.414 / 6 | 0.239 / -0.003 / 0.713 / 6 | — |
| T1 · Durum Wheat / CWRS Wheat | 0.100 / 0.082 / 0.050 / 6 | 0.126 / 0.085 / 0.083 / 6 | 0.556 / 0.085 / 0.331 / 6 | RUN1: AT_LOWER_BOUND |
| T1 · Yellow Peas / CWRS Wheat | 0.100 (prior) / -0.039 / -0.090 / 6 | 0.100 (prior) / -0.039 / -0.125 / 6 | 0.100 (prior) / -0.039 / -0.109 / 6 | RUN1: INVALID_INVERTED_SLOPE; RUN2: INVALID_INVERTED_SLOPE; RUN3: INVALID_INVERTED_SLOPE |
| T1 · Barley (feed budget) / CWRS Wheat | 0.132 / -0.020 / 0.127 / 6 | 0.142 / -0.022 / 0.085 / 6 | 0.107 / -0.019 / 0.062 / 6 | — |
| T1 · Oats / CWRS Wheat | 0.501 / -0.030 / 0.726 / 6 | 0.624 / -0.038 / 0.875 / 6 | 0.573 / -0.035 / 0.740 / 6 | — |
| T2 · Lentils (all; red lentil budget) / Durum Wheat | 0.100 (prior) / 0.077 / -0.097 / 6 | 0.100 / 0.071 / -0.043 / 6 | 0.143 / 0.068 / 0.103 / 6 | RUN1: INVALID_INVERTED_SLOPE; RUN2: AT_LOWER_BOUND |
| T2 · Soybeans / Canola | N/A | N/A | N/A | EXCLUDED_REGIONAL_CROP |
| T2 · Flax / Canola | N/A | N/A | N/A | EXCLUDED_REGIONAL_CROP |

## Diagnostic window ranking (does not select parameters)

Runs compared on the years all three share. Winner = highest R² among fits with OLS β > 0.

| Province | Crop / Anchor pair | Winning window | β (bounded) | α₀ | R² | p(β) | SE(β) | SE(α₀) | N | Flags |
|---|---|---|---|---|---|---|---|---|---|---|
| MB | T0 · CWRS Wheat / Canola | **Run 3 (Feb 15 - Mar 15)** | 0.146 | 0.007 | 0.418 | 0.166 | 0.086 | 0.022 | 6 | INTERIOR |
| MB | T1 · Canola / CWRS Wheat | **Run 1 (Dec 15 - Jan 15)** | 0.100 | -0.016 | -0.074 | 0.826 | 0.121 | 0.026 | 6 | AT_LOWER_BOUND |
| MB | T1 · Durum Wheat / CWRS Wheat | none | — | — | — | — | — | — | — | EXCLUDED_REGIONAL_CROP |
| MB | T1 · Yellow Peas / CWRS Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.431 | 0.035 | 0.210 | 0.360 | 0.418 | 0.087 | 6 | INTERIOR |
| MB | T1 · Barley (feed budget) / CWRS Wheat | none | — | — | — | — | — | — | — | no valid fit: all runs inverted |
| MB | T1 · Oats / CWRS Wheat | **Run 1 (Dec 15 - Jan 15)** | 0.869 | 0.041 | 0.789 | 0.018 | 0.225 | 0.047 | 6 | INTERIOR |
| MB | T2 · Lentils (all; red lentil budget) / Durum Wheat | none | — | — | — | — | — | — | — | EXCLUDED_REGIONAL_CROP |
| MB | T2 · Soybeans / Canola | **Run 1 (Dec 15 - Jan 15)** | 0.100 | 0.032 | 0.022 | 0.770 | 0.257 | 0.103 | 6 | AT_LOWER_BOUND |
| MB | T2 · Flax / Canola | **Run 1 (Dec 15 - Jan 15)** | 0.182 | -0.148 | 0.017 | 0.803 | 0.685 | 0.134 | 6 | INTERIOR |
| SK | T0 · CWRS Wheat / Canola | **Run 3 (Feb 15 - Mar 15)** | 0.100 | 0.013 | 0.062 | 0.630 | 0.168 | 0.059 | 6 | AT_LOWER_BOUND |
| SK | T1 · Canola / CWRS Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.100 | -0.001 | 0.080 | 0.439 | 0.069 | 0.023 | 6 | AT_LOWER_BOUND |
| SK | T1 · Durum Wheat / CWRS Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.129 | 0.025 | 0.514 | 0.109 | 0.063 | 0.016 | 6 | INTERIOR |
| SK | T1 · Yellow Peas / CWRS Wheat | none | — | — | — | — | — | — | — | no valid fit: all runs inverted |
| SK | T1 · Barley (feed budget) / CWRS Wheat | **Run 1 (Dec 15 - Jan 15)** | 0.272 | -0.025 | 0.478 | 0.128 | 0.142 | 0.052 | 6 | INTERIOR |
| SK | T1 · Oats / CWRS Wheat | **Run 2 (Jan 15 - Feb 15)** | 0.878 | -0.133 | 0.468 | 0.134 | 0.468 | 0.103 | 6 | INTERIOR |
| SK | T2 · Lentils (all; red lentil budget) / Durum Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.155 | -0.016 | 0.087 | 0.570 | 0.252 | 0.074 | 6 | INTERIOR |
| SK | T2 · Soybeans / Canola | **Run 1 (Dec 15 - Jan 15)** | 0.686 | 0.061 | 0.648 | 0.053 | 0.253 | 0.120 | 6 | INTERIOR |
| SK | T2 · Flax / Canola | **Run 1 (Dec 15 - Jan 15)** | 0.311 | -0.003 | 0.333 | 0.230 | 0.220 | 0.080 | 6 | INTERIOR |
| AB | T0 · CWRS Wheat / Canola | **Run 3 (Feb 15 - Mar 15)** | 0.122 | -0.008 | 0.109 | 0.523 | 0.175 | 0.035 | 6 | INTERIOR |
| AB | T1 · Canola / CWRS Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.239 | -0.003 | 0.713 | 0.034 | 0.076 | 0.015 | 6 | INTERIOR |
| AB | T1 · Durum Wheat / CWRS Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.556 | 0.085 | 0.331 | 0.232 | 0.395 | 0.046 | 6 | INTERIOR |
| AB | T1 · Yellow Peas / CWRS Wheat | none | — | — | — | — | — | — | — | no valid fit: all runs inverted |
| AB | T1 · Barley (feed budget) / CWRS Wheat | **Run 1 (Dec 15 - Jan 15)** | 0.132 | -0.020 | 0.127 | 0.488 | 0.173 | 0.037 | 6 | INTERIOR |
| AB | T1 · Oats / CWRS Wheat | **Run 2 (Jan 15 - Feb 15)** | 0.624 | -0.038 | 0.875 | 0.006 | 0.118 | 0.025 | 6 | INTERIOR |
| AB | T2 · Lentils (all; red lentil budget) / Durum Wheat | **Run 3 (Feb 15 - Mar 15)** | 0.143 | 0.068 | 0.103 | 0.535 | 0.211 | 0.059 | 6 | INTERIOR |
| AB | T2 · Soybeans / Canola | none | — | — | — | — | — | — | — | EXCLUDED_REGIONAL_CROP |
| AB | T2 · Flax / Canola | none | — | — | — | — | — | — | — | EXCLUDED_REGIONAL_CROP |

| Province | Pair wins R1 / R2 / R3 | Mean valid R² R1 / R2 / R3 | Valid fits R1 / R2 / R3 | Province winning window |
|---|---|---|---|---|
| Manitoba | 4 / 0 / 2 | 0.177 / 0.249 / 0.183 | 6 / 4 / 5 | **Run 1 (Dec 15 - Jan 15)** |
| Saskatchewan | 3 / 1 / 4 | 0.248 / 0.200 / 0.235 | 8 / 8 / 8 | **Run 3 (Feb 15 - Mar 15)** |
| Alberta | 1 / 1 / 4 | 0.290 / 0.232 / 0.343 | 5 / 6 / 6 | **Run 3 (Feb 15 - Mar 15)** |

## Run 1 / Run 2 / Run 3 comparison (ranking basis)

### Manitoba (MB)

| Pair | Run | Sample | N | β (bounded) | α₀ | R² | p(β) | SE(β) | Flag | Winner |
|---|---|---|---|---|---|---|---|---|---|---|
| T0 · CWRS Wheat / Canola | RUN1 | common | 6 | 0.132 | 0.003 | 0.236 | 0.329 | 0.119 | INTERIOR |  |
|  | RUN2 | common | 6 | 0.110 | 0.003 | 0.194 | 0.382 | 0.113 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.146 | 0.007 | 0.418 | 0.166 | 0.086 | INTERIOR | **✓** |
| T1 · Canola / CWRS Wheat | RUN1 | common | 6 | 0.100 | -0.016 | -0.074 | 0.826 | 0.121 | AT_LOWER_BOUND | **✓** |
|  | RUN2 | common | 6 | 0.100 (prior) | -0.016 | -0.241 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN3 | common | 6 | 0.100 | -0.016 | -0.192 | 0.907 | 0.099 | AT_LOWER_BOUND |  |
| T1 · Durum Wheat / CWRS Wheat | RUN1 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN2 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN3 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
| T1 · Yellow Peas / CWRS Wheat | RUN1 | common | 6 | 0.292 | 0.049 | 0.073 | 0.605 | 0.522 | INTERIOR |  |
|  | RUN2 | common | 6 | 0.198 | 0.057 | 0.047 | 0.680 | 0.446 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.431 | 0.035 | 0.210 | 0.360 | 0.418 | INTERIOR | **✓** |
| T1 · Barley (feed budget) / CWRS Wheat | RUN1 | own | 6 | 0.100 (prior) | -0.012 | -0.111 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN2 | own | 6 | 0.100 (prior) | -0.013 | -0.123 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN3 | own | 6 | 0.100 (prior) | -0.012 | -0.180 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
| T1 · Oats / CWRS Wheat | RUN1 | common | 6 | 0.869 | 0.041 | 0.789 | 0.018 | 0.225 | INTERIOR | **✓** |
|  | RUN2 | common | 6 | 0.943 | 0.031 | 0.764 | 0.023 | 0.262 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.636 | 0.027 | 0.495 | 0.119 | 0.321 | INTERIOR |  |
| T2 · Lentils (all; red lentil budget) / Durum Wheat | RUN1 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN2 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN3 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
| T2 · Soybeans / Canola | RUN1 | common | 6 | 0.100 | 0.032 | 0.022 | 0.770 | 0.257 | AT_LOWER_BOUND | **✓** |
|  | RUN2 | common | 6 | 0.100 | 0.031 | -0.008 | 0.876 | 0.244 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.100 | 0.034 | -0.018 | 0.877 | 0.208 | AT_LOWER_BOUND |  |
| T2 · Flax / Canola | RUN1 | common | 6 | 0.182 | -0.148 | 0.017 | 0.803 | 0.685 | INTERIOR | **✓** |
|  | RUN2 | common | 6 | 0.100 (prior) | -0.152 | -0.020 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN3 | common | 6 | 0.100 (prior) | -0.149 | -0.061 | n/a | n/a | INVALID_INVERTED_SLOPE |  |

### Saskatchewan (SK)

| Pair | Run | Sample | N | β (bounded) | α₀ | R² | p(β) | SE(β) | Flag | Winner |
|---|---|---|---|---|---|---|---|---|---|---|
| T0 · CWRS Wheat / Canola | RUN1 | common | 6 | 0.100 | 0.010 | 0.043 | 0.690 | 0.205 | AT_LOWER_BOUND |  |
|  | RUN2 | common | 6 | 0.100 | 0.010 | -0.024 | 0.859 | 0.181 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.100 | 0.013 | 0.062 | 0.630 | 0.168 | AT_LOWER_BOUND | **✓** |
| T1 · Canola / CWRS Wheat | RUN1 | common | 6 | 0.100 | -0.001 | 0.038 | 0.549 | 0.086 | AT_LOWER_BOUND |  |
|  | RUN2 | common | 6 | 0.100 | 0.001 | -0.039 | 0.589 | 0.077 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.100 | -0.001 | 0.080 | 0.439 | 0.069 | AT_LOWER_BOUND | **✓** |
| T1 · Durum Wheat / CWRS Wheat | RUN1 | common | 6 | 0.100 | 0.018 | 0.058 | 0.245 | 0.039 | AT_LOWER_BOUND |  |
|  | RUN2 | common | 6 | 0.100 | 0.023 | 0.164 | 0.243 | 0.043 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.129 | 0.025 | 0.514 | 0.109 | 0.063 | INTERIOR | **✓** |
| T1 · Yellow Peas / CWRS Wheat | RUN1 | own | 6 | 0.100 (prior) | -0.067 | -0.718 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN2 | own | 6 | 0.100 (prior) | -0.066 | -0.813 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN3 | own | 6 | 0.100 (prior) | -0.066 | -0.617 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
| T1 · Barley (feed budget) / CWRS Wheat | RUN1 | common | 6 | 0.272 | -0.025 | 0.478 | 0.128 | 0.142 | INTERIOR | **✓** |
|  | RUN2 | common | 6 | 0.307 | -0.025 | 0.371 | 0.199 | 0.200 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.205 | -0.033 | 0.233 | 0.333 | 0.186 | INTERIOR |  |
| T1 · Oats / CWRS Wheat | RUN1 | common | 6 | 0.809 | -0.125 | 0.434 | 0.155 | 0.463 | INTERIOR |  |
|  | RUN2 | common | 6 | 0.878 | -0.133 | 0.468 | 0.134 | 0.468 | INTERIOR | **✓** |
|  | RUN3 | common | 6 | 0.409 | -0.089 | 0.191 | 0.386 | 0.420 | INTERIOR |  |
| T2 · Lentils (all; red lentil budget) / Durum Wheat | RUN1 | common | 6 | 0.100 | 0.010 | -0.045 | 0.776 | 0.121 | AT_LOWER_BOUND |  |
|  | RUN2 | common | 6 | 0.100 | 0.003 | -0.041 | 0.862 | 0.159 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.155 | -0.016 | 0.087 | 0.570 | 0.252 | INTERIOR | **✓** |
| T2 · Soybeans / Canola | RUN1 | common | 6 | 0.686 | 0.061 | 0.648 | 0.053 | 0.253 | INTERIOR | **✓** |
|  | RUN2 | common | 6 | 0.602 | 0.041 | 0.581 | 0.078 | 0.256 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.529 | 0.054 | 0.632 | 0.059 | 0.202 | INTERIOR |  |
| T2 · Flax / Canola | RUN1 | common | 6 | 0.311 | -0.003 | 0.333 | 0.230 | 0.220 | INTERIOR | **✓** |
|  | RUN2 | common | 6 | 0.174 | -0.025 | 0.121 | 0.499 | 0.234 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.118 | -0.028 | 0.083 | 0.580 | 0.196 | INTERIOR |  |

### Alberta (AB)

| Pair | Run | Sample | N | β (bounded) | α₀ | R² | p(β) | SE(β) | Flag | Winner |
|---|---|---|---|---|---|---|---|---|---|---|
| T0 · CWRS Wheat / Canola | RUN1 | common | 6 | 0.100 | -0.011 | 0.039 | 0.709 | 0.248 | AT_LOWER_BOUND |  |
|  | RUN2 | common | 6 | 0.100 | -0.011 | -0.024 | 0.902 | 0.213 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.122 | -0.008 | 0.109 | 0.523 | 0.175 | INTERIOR | **✓** |
| T1 · Canola / CWRS Wheat | RUN1 | common | 6 | 0.280 | -0.004 | 0.510 | 0.111 | 0.137 | INTERIOR |  |
|  | RUN2 | common | 6 | 0.213 | 0.001 | 0.414 | 0.168 | 0.127 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.239 | -0.003 | 0.713 | 0.034 | 0.076 | INTERIOR | **✓** |
| T1 · Durum Wheat / CWRS Wheat | RUN1 | common | 6 | 0.100 | 0.082 | 0.050 | 0.667 | 0.190 | AT_LOWER_BOUND |  |
|  | RUN2 | common | 6 | 0.126 | 0.085 | 0.083 | 0.581 | 0.210 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.556 | 0.085 | 0.331 | 0.232 | 0.395 | INTERIOR | **✓** |
| T1 · Yellow Peas / CWRS Wheat | RUN1 | own | 6 | 0.100 (prior) | -0.039 | -0.090 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN2 | own | 6 | 0.100 (prior) | -0.039 | -0.125 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN3 | own | 6 | 0.100 (prior) | -0.039 | -0.109 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
| T1 · Barley (feed budget) / CWRS Wheat | RUN1 | common | 6 | 0.132 | -0.020 | 0.127 | 0.488 | 0.173 | INTERIOR | **✓** |
|  | RUN2 | common | 6 | 0.142 | -0.022 | 0.085 | 0.576 | 0.234 | INTERIOR |  |
|  | RUN3 | common | 6 | 0.107 | -0.019 | 0.062 | 0.634 | 0.208 | INTERIOR |  |
| T1 · Oats / CWRS Wheat | RUN1 | common | 6 | 0.501 | -0.030 | 0.726 | 0.031 | 0.154 | INTERIOR |  |
|  | RUN2 | common | 6 | 0.624 | -0.038 | 0.875 | 0.006 | 0.118 | INTERIOR | **✓** |
|  | RUN3 | common | 6 | 0.573 | -0.035 | 0.740 | 0.028 | 0.170 | INTERIOR |  |
| T2 · Lentils (all; red lentil budget) / Durum Wheat | RUN1 | common | 6 | 0.100 (prior) | 0.077 | -0.097 | n/a | n/a | INVALID_INVERTED_SLOPE |  |
|  | RUN2 | common | 6 | 0.100 | 0.071 | -0.043 | 0.952 | 0.207 | AT_LOWER_BOUND |  |
|  | RUN3 | common | 6 | 0.143 | 0.068 | 0.103 | 0.535 | 0.211 | INTERIOR | **✓** |
| T2 · Soybeans / Canola | RUN1 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN2 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN3 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
| T2 · Flax / Canola | RUN1 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN2 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |
|  | RUN3 | own | 0 | — | — | — | — | — | EXCLUDED_REGIONAL_CROP | |

## Data notes

- **Variable costs**: 2026 seed, fertilizer and chemical come from `RPA_Master_Calculator.xlsx` (`RPA_Summary_<prov>`), the only year the workbook budgets per crop. 2019–2025 come from RPA_Calculator's normalized history tables (`output/<prov>/mvs`). Those cost lines reproduce all 77 published 2019–2025 values on the workbook's `Historical_Ratios` sheet (max |diff| 1.6e-08). No OCR output or raw guide tables are read. Fuel is excluded per [DCASH].
- **2026 cross-check, history table vs workbook**: seed and chemical match exactly. Fertilizer differs where the workbook recomputes it with its N-credit rule: MB SOYBEANS -6.93 $/ac, MB Y_PEAS -5.19 $/ac. The workbook value is used.
- **Yields**: StatCan 32-10-0359 provincial actuals. Each year T uses an OLS linear trend on T-21 … T-1 evaluated at T (at least 10 points), so no year's own yield is used. Otherwise the T-5 … T-1 mean is used, flagged INSUFFICIENT_YIELD_HISTORY: AB SOYBEANS (2019–2025), MB DURUM (2019–2025), MB LENTILS (2019–2025), SK SOYBEANS (2019–2022). Units are bu/ac; Lentils stay in lb/ac.
- **Prices** (spec §4): PDQ prompt bids (window start delivery month, else the earliest later month up to JUL of T). CWRS, Canola and Yellow Peas use every PDQ zone; Durum uses S ALTA (AB) and SW SASK (SK) only. Cells without a prompt bid use the template, then the year's provincial guide price, then NULL. Tier counts over the calibration cells: BUDGET_FALLBACK 252, PDQ_PROMPT 231, MISSING_PRICE_DATA 84. Barley, Oats, Lentils, Soybeans and Flax are guide-priced (the same in all runs), so those pairs differ by window only through the anchor's prompt price.
- **Regional exclusions** (EXCLUDED_REGIONAL_CROP, acres held at T-1): AB CORN, AB FLAX, AB SOYBEANS, MB DURUM, MB LENTILS, SK CORN. CORN is not a modelled crop.
- **Sample**: planting years 2019–2025. 2019 supplies t-1 and the first baseline, so N ≤ 6.
- **Barley** uses the feed barley budget (MB publishes no malt budget). StatCan acres are total barley.

## Pairs not estimable

- **Red vs Green Lentils (separately)**: StatCan 32-10-0359 publishes one Lentils seeded-area series (no red/green split); estimated as Lentils (all) / Durum on the red lentil budget.
- **Green Lentils / Durum**: No green-lentil seeded-area series in 32-10-0359 and no green-lentil budget in the AB/SK/MB guides.

## NULL observations

| Province | Pair | Reason | Run:year |
|---|---|---|---|
| AB | FLAX/CANOLA | FLAX price MISSING_PRICE_DATA; FLAX no budget | RUN1:2020, RUN1:2021, RUN1:2022, RUN1:2023, RUN1:2024, RUN1:2025, RUN2:2020, RUN2:2021, RUN2:2022, RUN2:2023, RUN2:2024, RUN2:2025, RUN3:2020, RUN3:2021, RUN3:2022, RUN3:2023, RUN3:2024, RUN3:2025 |
| AB | SOYBEANS/CANOLA | SOYBEANS price MISSING_PRICE_DATA; SOYBEANS no budget; SOYBEANS no StatCan yield | RUN1:2020, RUN1:2021, RUN1:2022, RUN1:2023, RUN1:2024, RUN1:2025, RUN2:2020, RUN2:2021, RUN2:2022, RUN2:2023, RUN2:2024, RUN2:2025, RUN3:2020, RUN3:2021, RUN3:2022, RUN3:2023, RUN3:2024, RUN3:2025 |
| MB | DURUM/CWRS | DURUM price MISSING_PRICE_DATA; DURUM no budget | RUN1:2022, RUN1:2023, RUN1:2024, RUN1:2025, RUN2:2022, RUN2:2023, RUN2:2024, RUN2:2025, RUN3:2022, RUN3:2023, RUN3:2024, RUN3:2025 |
| MB | DURUM/CWRS | DURUM price MISSING_PRICE_DATA; DURUM no budget; DURUM no StatCan yield | RUN1:2020, RUN1:2021, RUN2:2020, RUN2:2021, RUN3:2020, RUN3:2021 |
| MB | LENTILS/DURUM | LENTILS price MISSING_PRICE_DATA; LENTILS no budget; LENTILS no StatCan yield; DURUM price MISSING_PRICE_DATA; DURUM no budget | RUN1:2022, RUN1:2023, RUN1:2024, RUN1:2025, RUN2:2022, RUN2:2023, RUN2:2024, RUN2:2025, RUN3:2022, RUN3:2023, RUN3:2024, RUN3:2025 |
| MB | LENTILS/DURUM | LENTILS price MISSING_PRICE_DATA; LENTILS no budget; LENTILS no StatCan yield; DURUM price MISSING_PRICE_DATA; DURUM no budget; DURUM no StatCan yield | RUN1:2020, RUN1:2021, RUN2:2020, RUN2:2021, RUN3:2020, RUN3:2021 |
