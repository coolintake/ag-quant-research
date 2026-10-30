# V2 diagnostics — relative cash RPA index vs CANSIM (2026-10-09)

Spec: `SPECIFICATION_V2.md`. CANSIM extract 2026-09-10; 2026 acres preliminary. Cost lines reproduce 35 Historical_Ratios values (max |diff| 1.6e-08).

## Signal skill (2020–2026, provinces pooled)

| Window | Directional hit rate | N | Mean Spearman ρ (across crops) | Province-years |
|---|---|---|---|---|
| Run 1 (Dec 15 - Jan 15) | 67% | 91 | +0.40 | 21 |
| Run 2 (Jan 15 - Feb 15) | 69% | 89 | +0.21 | 21 |
| Run 3 (Feb 15 - Mar 15) | 70% | 91 | +0.41 | 21 |

Hit rate by crop (Run 3): Canola 71%, CPSR Wheat 64%, CWRS Wheat 86%, Durum Wheat 64%, Yellow Peas 62%.

## Plan-B pooled regression through the origin (fit 2020–2025)

%ΔAcres = b × %ΔSignal (intercept forced to 0). Plan-B is the automated fallback / prior behind analyst Plan-A. R² is centered.

| Window | N | b (SE) | p(b) | R² | Flag |
|---|---|---|---|---|---|
| Run 1 (Dec 15 - Jan 15) | 78 | +0.093 (0.058) | 0.112 | 0.003 | — |
| Run 2 (Jan 15 - Feb 15) | 77 | +0.080 (0.055) | 0.151 | 0.000 | — |
| Run 3 (Feb 15 - Mar 15) | 78 | +0.098 (0.056) | 0.082 | 0.010 | — |

Out-of-sample WAPE (fitted on years before T), Plan-B vs no-change:

| Window | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| R1 | 6.6% vs 7.4% | 6.2% vs 6.7% | 3.2% vs 3.4% | 3.8% vs 2.7% | 7.4% vs 8.1% |
| R2 | 7.0% vs 7.4% | 6.2% vs 6.7% | 3.3% vs 3.4% | 3.9% vs 2.5% | 6.7% vs 7.8% |
| R3 | 6.1% vs 7.4% | 6.1% vs 6.7% | 3.3% vs 3.4% | 4.2% vs 2.7% | 6.4% vs 8.1% |

## 2026 Run 3: relative index (R3 base) and signal vs CANSIM (preliminary)

| Geography | Crop | RPA $/ac | RelIndex | % Δ signal | CANSIM % Δ acres | Same direction | Plan-B % Δ | Flags |
|---|---|---|---|---|---|---|---|---|
| MB | CWRS Wheat | 284 | 100.0 | -16.3% | -6.7% | yes | -1.6% | CWRS_VS_CANOLA;PRELIMINARY_ACRES |
| MB | Canola | 375 | 110.3 | +19.5% | +5.8% | yes | +1.9% | PRELIMINARY_ACRES |
| MB | Yellow Peas | 263 | 89.8 | -16.9% | -44.3% | yes | -1.7% | PRELIMINARY_ACRES |
| SK | CWRS Wheat | 160 | 100.0 | -15.0% | -4.1% | yes | -1.5% | CWRS_VS_CANOLA;PRELIMINARY_ACRES |
| SK | CPSR Wheat | 166 | 89.9 | -15.9% | +81.1% | no | -1.6% | COST_PROXY_CWRS;PRELIMINARY_ACRES |
| SK | Durum Wheat | 101 | 63.5 | -20.4% | -9.1% | yes | -2.0% | PRELIMINARY_ACRES |
| SK | Canola | 330 | 121.2 | +17.6% | +9.8% | yes | +1.7% | PRELIMINARY_ACRES |
| SK | Yellow Peas | 63 | 42.9 | -51.8% | -7.3% | yes | -5.1% | PRELIMINARY_ACRES |
| AB | CWRS Wheat | 244 | 100.0 | -21.8% | -0.9% | yes | -2.1% | CWRS_VS_CANOLA;PRELIMINARY_ACRES |
| AB | CPSR Wheat | 301 | 99.5 | -7.1% | -17.2% | yes | -0.7% | PRELIMINARY_ACRES |
| AB | Durum Wheat | 135 | 73.1 | -20.8% | -17.1% | yes | -2.0% | PRELIMINARY_ACRES |
| AB | Canola | 403 | 126.4 | +27.8% | +6.9% | yes | +2.7% | PRELIMINARY_ACRES |
| AB | Yellow Peas | 203 | 91.6 | -11.1% | -17.6% | yes | -1.1% | PRELIMINARY_ACRES |
| PRAIRIE | CWRS Wheat | 210 | 100.0 | -20.0% | -3.6% | yes | — | CWRS_VS_CANOLA;PRELIMINARY_ACRES |
| PRAIRIE | CPSR Wheat | 257 | 94.4 | -12.4% | +8.1% | no | — | PRELIMINARY_ACRES |
| PRAIRIE | Durum Wheat | 107 | 66.4 | -13.4% | -10.8% | yes | — | PRELIMINARY_ACRES |
| PRAIRIE | Canola | 358 | 121.6 | +25.0% | +8.4% | yes | — | PRELIMINARY_ACRES |
| PRAIRIE | Yellow Peas | 129 | 69.6 | -27.7% | -13.8% | yes | — | PRELIMINARY_ACRES |

Signal for CWRS = RPA_CWRS / RPA_Canola. Plan-A: see `inputs/plan_a_assignments.csv` and the Evaluation tab of the workbook.
