# Western Canada Crop Acreage Allocation Model

**Document:** `model_crop_wallet_step1.md`
**Region:** Manitoba, Saskatchewan, Alberta (provincial models, never pooled)
**Framework:** Run-specific, provincial, anchor-tiered log-log acreage elasticities on PDQ prompt cash bids, with a StatCan summer-fallow land constraint.

---

## 1. Objective and Releases

The model forecasts provincial seeded area for CWRS Wheat, Durum Wheat, Canola, Yellow Peas, Lentils, Barley, Oats, Soybeans and Flax before spring planting. It is published three times, once per pricing window $w$:

| Release | Window $w$ | Prompt price window (inclusive) | Parameters used |
|---|---|---|---|
| Mid-January | Run 1 | Dec 15 (T−1) – Jan 15 (T) | $\alpha_{0,j,p,\text{Run1}},\ \beta_{j,p,\text{Run1}}$ |
| Mid-February | Run 2 | Jan 15 – Feb 15 (T) | $\alpha_{0,j,p,\text{Run2}},\ \beta_{j,p,\text{Run2}}$ |
| Mid-March | Run 3 | Feb 15 – Mar 15 (T) | $\alpha_{0,j,p,\text{Run3}},\ \beta_{j,p,\text{Run3}}$ |

Each release uses only its own window's prices and its own run-calibrated parameters. No release borrows another run's parameters (§7.3).

**Notation.** $j$ = crop, $A$ = its anchor, $p$ = province, $w$ = pricing window (Run 1/2/3), $T$ = planting (crop) year. Planting year $T$ = crop marketing year $T/T{+}1$ = provincial guide year $T$ (e.g., spring 2026 planting = 2026/27 = 2026 guides).

---

## 2. Data Sources

| Input | Source | Use |
|---|---|---|
| Seeded area, yield, summer fallow | StatCan CANSIM Table 32-10-0359-01 (`Workspace_CanadaSD/Data_Inputs/Planting_32100359-eng/32100359.csv`) | Dependent variable, trend yields, land pool |
| Prompt cash bids | PDQ via `Canada_Crop_Prices/data/crop_prices.duckdb` (`unified_cash_prices`); prompt extraction is built in this repository (§4) | Primary price |
| Secondary prices | `RPA_Calculator/Input_Prices/crop_price_template.csv` | Price fallback 1 (§4.3) |
| Guide prices | Provincial guide farm prices (same sources as costs, below) | Price fallback 2 (§4.3) |
| Variable costs, 2026+ | Provincial budget workbook: `RPA_Calculator/output/RPA_Master_Calculator.xlsx`, sheets `RPA_Summary_MB/SK/AB` (the 2027 workbook when released) | Costs for the current and forward year |
| Variable costs, 2019–2025 | RPA_Calculator normalized history tables `output/{ab,sk,mb}/mvs/*_historical_2019_2026.csv` | Calibration costs. Must reproduce every value on the workbook's `Historical_Ratios` sheet (tolerance 1e-4), or ingestion stops. No OCR output or raw guide tables. |
| March intentions | StatCan March Principal Field Crop Areas release (December survey) | Diagnostic benchmark for Run 3 only (§9) |

**Costs.** Variable Costs$_{j,p,T}$ = Seed + Fertilizer + Chemicals ($/ac, [DCASH]; fuel and overhead excluded). Costs are fixed within a year: the same value for Run 1, 2 and 3.

### 2.1 Canonical Units

| Quantity | Canonical unit | Conversion |
|---|---|---|
| Area | acres | 1 ha = 2.47105 ac; use the acre series where published |
| Yield, all crops except Lentils | bu/ac | — |
| Yield, Lentils | lb/ac | As published by CANSIM (lb/ac only). **Never convert to bushels.** |
| Price, all crops except Lentils | $/bu | $/MT ÷ (bu/MT), where bu/MT = 1000 ÷ kg/bu: Wheat, Durum, Peas, Soybeans 36.7438 (27.2155 kg) · Canola 44.0925 (22.6796) · Barley 45.9297 (21.7724) · Oats 64.8420 (15.4221) · Flax 39.3682 (25.4012) |
| Price, Yellow Peas from ¢/lb | $/bu | ¢/lb ÷ 100 × 60 |
| Price, Lentils | $/lb | ¢/lb ÷ 100 (e.g., 22 ¢/lb = $0.22/lb) |
| Costs, RPA | $/ac | — |

---

## 3. Crop and Anchor Structure

| Tier | Crop $j$ | Anchor $A$ | Ratio $R_{j/A}$ |
|---|---|---|---|
| 0 | CWRS Wheat | Canola | $R_{\text{CWRS/Canola}} = \text{RPA}_{\text{CWRS}} / \text{RPA}_{\text{Canola}}$ |
| 1 | Canola | CWRS | $\text{RPA}_{\text{Canola}} / \text{RPA}_{\text{CWRS}}$ |
| 1 | Durum Wheat | CWRS | $\text{RPA}_{\text{Durum}} / \text{RPA}_{\text{CWRS}}$ |
| 1 | Yellow Peas | CWRS | $\text{RPA}_{\text{Peas}} / \text{RPA}_{\text{CWRS}}$ |
| 1 | Barley | CWRS | $\text{RPA}_{\text{Barley}} / \text{RPA}_{\text{CWRS}}$ |
| 1 | Oats | CWRS | $\text{RPA}_{\text{Oats}} / \text{RPA}_{\text{CWRS}}$ |
| 2 | Lentils (all) | Durum | $\text{RPA}_{\text{Lentils}} / \text{RPA}_{\text{Durum}}$ |
| 2 | Soybeans | Canola | $\text{RPA}_{\text{Soybeans}} / \text{RPA}_{\text{Canola}}$ |
| 2 | Flax | Canola | $\text{RPA}_{\text{Flax}} / \text{RPA}_{\text{Canola}}$ |

* **CWRS signal.** CWRS is the base anchor. It responds to Canola competition through the *inverted* ratio $R_{\text{CWRS/Canola}} = 1 / R_{\text{Canola/CWRS}}$, so its expected elasticity is positive and the bounds in §7.2 apply unchanged. Canola and CWRS are estimated as separate equations on the same price information.
* **Lentils** = StatCan "Lentils" (red + green; no split is published), priced and costed on the Red Lentil budget. Green Lentils are not modelled separately: there is no area series and no budget.
* **Barley** = StatCan total barley, costed on the Feed Barley budget (Manitoba publishes no malt budget).
* **Durum price belt.** Durum bids use only PDQ zones S ALTA (→ AB) and SW SASK (→ SK). Manitoba has no Durum belt price, so MB Durum and MB Lentils are not modelled. Durum yields stay province-wide.
* **Regional exclusions:** MB Durum, MB Lentils, AB Soybeans, AB Flax, AB Corn and SK Corn are never regressed. Their acres are held at $T-1$ ($\widehat{\text{Acres}}_T = \text{Acres}_{T-1}$), tagged `EXCLUDED_REGIONAL_CROP`. Corn is not a modelled crop; it is listed so the land pool holds it flat under the same tag.
* **Excluded:** Chickpeas and CPSR (no SK budget input). Excluded and unmodelled crops are held at $T-1$ inside the land pool (§8.3).

---

## 4. Price Construction

### 4.1 Prompt Price Rule

Prompt bids are a **spot momentum signal**: nearby (old-crop) cash prices observed during the window. They are not new-crop forward prices, which are thinly quoted for Peas and Durum and are not used. Field mapping:

| PDQ export field | DuckDB column | Meaning |
|---|---|---|
| `IMPORT DATE` | `price_date` | Price collection date |
| `MONTH` (e.g., `DEC '25`) | `delivery_month` + `delivery_year` | Delivery contract period |
| `CASH` | `price_cad_bu` | $/bu; `-`, blank and 0 → NULL |

For a window starting in month $M$ of year $Y_M$ (Run 1: DEC of $T-1$; Run 2: JAN of $T$; Run 3: FEB of $T$), for each crop × zone × price date in the window:

1. **Primary:** the bid whose delivery period is $M$ of $Y_M$.
2. **Active fallback:** if that bid is NULL or not listed (e.g., once the month has expired), the earliest later delivery month with a non-NULL bid (e.g., `JAN`, then `FEB`), no later than `JUL` of $T$. Prompt bids are old-crop, so new-crop months are never used.
3. If no delivery month is quoted, the zone has no price that day.

**Aggregation:** provincial daily price = mean over the province's zones that quoted (Durum: belt zones only, §3). Window price = mean of the provincial daily prices. Record the delivery months and quoted-day count used for each window price.

### 4.2 Zero-Synthetic Rule

Missing quotes are never interpolated, curve-fitted or smoothed. A window with no quoted day for a crop is NULL at the PDQ level.

### 4.3 Fallback Hierarchy (per crop × province × window × year)

1. **PDQ prompt bid** (§4.1). Available for CWRS, Durum, Canola and Yellow Peas.
2. **`crop_price_template.csv`**: the row matching `Date` = the run's release date (window end + 1 day, e.g., 2026-01-16 for Run 1 2026), `Province` and `Display_Name`. Convert units per §2.1. Flag `TEMPLATE_PRICE`.
3. **Provincial guide price** for year $T$ (the farm price printed in that year's guide). It is identical across Runs 1–3. Flag `BUDGET_FALLBACK`.
4. **NULL**, flagged `MISSING_PRICE_DATA`.

For crops without PDQ bids (Barley, Oats, Lentils, Soybeans, Flax), a pair's window-to-window variation therefore comes only from its anchor's price.

---

## 5. Expected Yield

For each crop $j$, province $p$ and target year $T$ (calibration and forecast years alike):

* **Trend:** fit $\text{Yield} = \gamma_0 + \gamma_1 \cdot \text{Year}$ by OLS on StatCan actuals for years $T-21$ … $T-1$ (21 years). Use the non-NULL years, which must number at least 10. Evaluate at year $T$: $\hat{Y}_{j,p,T}$.
  * Example: $T = 2026$ → fit 2005–2025, evaluate at 2026. $T = 2020$ (calibration) → fit 1999–2019, evaluate at 2020.
* **Fallback:** if fewer than 10 non-NULL years exist (e.g., SK Soybeans from 2013, AB Soybeans, MB Lentils), use the simple mean of $T-5$ … $T-1$ (at least 3 years, else NULL) and flag `INSUFFICIENT_YIELD_HISTORY`.
* **Zero yields:** StatCan prints 0 (not `..`) when nothing was seeded (e.g., AB Lentils 2006: 0 ac, 0 lb/ac). A zero yield is a non-observation and is treated as NULL.
* **Diagnostic:** `output/yield_trend_diagnostics.xlsx`, one tab per province: actual, trend, SMA and the value used for 1998–2026, with a line chart per crop.

---

## 6. Return per Acre and Ratios

$$\text{RPA}_{j,p,w,T} = \hat{Y}_{j,p,T} \cdot P_{j,p,w,T} - \text{VC}_{j,p,T}$$

$$R_{j/A,p,w,T} = \text{RPA}_{j,p,w,T} \,/\, \text{RPA}_{A,p,w,T}$$

$$\bar{R}_{j/A,p,w,T-1} = \text{mean}\left(R_{j/A,p,w,t}\right),\quad t \in [T-5,\,T-1],\ \text{non-NULL only, at least 1 year}$$

* $\bar{R}$ uses the same window $w$'s historical ratios. Because cost data start in 2019, the 2020 baseline rests on one year, and the full 5-year baseline is reached in 2024.
* **NULL rules:** $R$ is NULL if either RPA is NULL or **≤ 0** (the log is undefined). Calibration drops that year. For live forecasts see §8.2.

---

## 7. Calibration

### 7.1 Equation

Estimated independently for every crop $j$, province $p$ and window $w$:

$$\ln\left(\frac{\text{Acres}_{j,p,t}}{\text{Acres}_{j,p,t-1}}\right) = \alpha_{0,j,p,w} + \beta_{j,p,w} \cdot \ln\left(\frac{R_{j/A,p,w,t}}{\bar{R}_{j/A,p,w,t-1}}\right) + \varepsilon_{j,p,w,t}$$

* **Sample:** $t$ = 2020 … latest year with prices (2019 supplies $t-1$ and the first baseline), giving $N \le 6$ until 2026 prices are available. This is a living calibration: re-estimate each year as StatCan and budget data arrive.
* $\alpha_{0,j,p,w}$: autonomous drift (agronomic adoption, scale). $\beta_{j,p,w}$: acreage elasticity to relative profitability.

### 7.2 Bounded Estimation and Flags

The slope is constrained to $\beta \in [0.10,\ 2.00]$. For a single regressor this equals the OLS slope clipped to the interval, with $\alpha_0$ re-fitted at the clipped slope ($\alpha_0 = \bar{y} - \beta\bar{x}$). No other adjustment or multiplier is applied to $\beta$.

| OLS result | Calibrated $\beta$ | Flag |
|---|---|---|
| $\beta_{\text{OLS}} \le 0$ | 0.10 (floor), $\alpha_0$ re-fitted | `INVALID_INVERTED_SLOPE`; negative values are never reported |
| $0 < \beta_{\text{OLS}} < 0.10$ | 0.10, $\alpha_0$ re-fitted | `AT_LOWER_BOUND` |
| $0.10 \le \beta_{\text{OLS}} \le 2.00$ | $\beta_{\text{OLS}}$ | — |
| $\beta_{\text{OLS}} > 2.00$ | 2.00, $\alpha_0$ re-fitted | `AT_UPPER_BOUND` |
| $N < 3$ or no variation in $x$ | none | `UNCALIBRATED` (acres held at $T-1$ in forecasts) |
| Regional exclusion (§3) | none (not estimated) | `EXCLUDED_REGIONAL_CROP` (acres held at $T-1$) |
| $3 \le N < 5$ | as above | `LOW_N` (in addition) |

Report for every fit: $N$, years, $\alpha_0$, $\beta$, $R^2$ of the calibrated fit, and SE and p-value of the OLS slope (shown as n/a for inverted fits).

### 7.3 Window Ranking (diagnostic only)

For each province and pair, refit Runs 1–3 on the years all three runs share. Rank the runs by calibrated-fit $R^2$ among fits with $\beta_{\text{OLS}} > 0$, breaking ties on the lower SE of $\beta$. Aggregate per province (pair wins, then mean $R^2$). The ranking reports how reliable each release is. **It never selects or replaces a release's parameters.**

---

## 8. Forecast (per province $p$, release $w$, year $T$)

### 8.1 Sequential Acreage Forecast

All releases use live window-$w$ prices, year-$T$ costs and trend yields, and the run-$w$ parameters.

**Step 1 — CWRS (anchor signal):**

$$\widehat{\text{Acres}}_{\text{CWRS},p,T} = \text{Acres}_{\text{CWRS},p,T-1} \cdot \exp\left(\alpha_{0,\text{CWRS},p,w} + \beta_{\text{CWRS},p,w} \cdot \ln\frac{R_{\text{CWRS/Canola},p,w,T}}{\bar{R}_{\text{CWRS/Canola},p,w,T-1}}\right)$$

**Step 2 — Secondary crops:** Tier 1 (vs CWRS), then Tier 2 (vs Durum or Canola), per §3:

$$\widehat{\text{Acres}}_{j,p,T} = \text{Acres}_{j,p,T-1} \cdot \exp\left(\alpha_{0,j,p,w} + \beta_{j,p,w} \cdot \ln\frac{R_{j/A,p,w,T}}{\bar{R}_{j/A,p,w,T-1}}\right)$$

**Step 3 — Land pool scaling** (§8.3).

### 8.2 Forecast Edge Cases

| Condition | Action | Flag |
|---|---|---|
| Live RPA of $j$ or its anchor ≤ 0 | $\widehat{\text{Acres}}_{j,T} = \text{Acres}_{j,T-1}$ | `NONPOSITIVE_RPA` |
| Live price NULL after §4.3 | $\widehat{\text{Acres}}_{j,T} = \text{Acres}_{j,T-1}$ | `MISSING_PRICE_DATA` |
| Pair `UNCALIBRATED` | $\widehat{\text{Acres}}_{j,T} = \text{Acres}_{j,T-1}$ | `UNCALIBRATED` |
| Regional exclusion (§3) | $\widehat{\text{Acres}}_{j,T} = \text{Acres}_{j,T-1}$ | `EXCLUDED_REGIONAL_CROP` |

### 8.3 Land Pool and Summer Fallow Buffer

* **Cropland** $C_{p,T-1}$ = sum of seeded area over all principal StatCan field crops in 32-10-0359. Use one series per crop with no sub-aggregates (e.g., "Wheat, all" but not its classes; "Beans, all dry" but not white/coloured), and exclude forage and silage (tame hay, corn for silage). Crops not forecast in §8.1 are held at $T-1$.
* **Summer fallow floor:** $\text{SF}_{\min,p,T} = \min\left(\text{SF}_{p,t}\right),\ t \in [T-25,\,T-1]$ (rolling).
* **Land pool:** $L_{p,T} = C_{p,T-1} + \left(\text{SF}_{p,T-1} - \text{SF}_{\min,p,T}\right)$.
* **Allocation:** let $S = \sum_k \widehat{A}_{k,p,T}$ over every crop in the cropland basket (forecast and held, including CWRS).
  * **Case A, $S \le L_{p,T}$:** no scaling. $\text{SF}_{p,T} = \text{SF}_{p,T-1} - (S - C_{p,T-1})$.
  * **Case B, $S > L_{p,T}$:** $\widehat{A}_{k,\text{final}} = \widehat{A}_k \cdot L_{p,T}/S$ for every crop in the basket, and $\text{SF}_{p,T} = \text{SF}_{\min,p,T}$. Flag `LAND_POOL_SCALED`.

---

## 9. Flags and Diagnostics

Evaluated on final (post-§8.3) acres:

| Flag | Condition | Meaning |
|---|---|---|
| `FLAG_UPPER_BOUND_BREACH` | $\widehat{A}_{j,p,T} > \max(A_{j,p,2019..T-1})$ | Expansion beyond the post-2019 range; oversupply or trade risk |
| `FLAG_LOWER_BOUND_BREACH` | $\widehat{A}_{j,p,T} < \min(A_{j,p,2019..T-1})$ | Contraction below the post-2019 range; margin collapse |
| `FLAG_ROTATIONAL_CEILING` | Canola $> 33.3\%$ of final cropland $\sum_k \widehat{A}_{k,\text{final}}$ | Clubroot / blackleg rotation risk |
| `INTENTIONS_GAP` (Run 3 only) | Report $(\widehat{A}_{\text{Run3}} - \text{Intentions}_{\text{March}})/\text{Intentions}_{\text{March}}$ per crop and province | Diagnostic benchmark only; never adjusts the forecast |

Each breach logs the percentage deviation from the boundary. Calibrated $\beta$ values are never manually altered in response to a flag: market shocks enter only through bids, costs and RPA ratios.

**Flag glossary:** `INVALID_INVERTED_SLOPE`, `AT_LOWER_BOUND`, `AT_UPPER_BOUND`, `UNCALIBRATED`, `EXCLUDED_REGIONAL_CROP`, `LOW_N` (§7.2) · `TEMPLATE_PRICE`, `BUDGET_FALLBACK`, `MISSING_PRICE_DATA` (§4.3) · `INSUFFICIENT_YIELD_HISTORY` (§5) · `NONPOSITIVE_RPA` (§8.2) · `LAND_POOL_SCALED` (§8.3) · the breach and diagnostic flags above.

---

## 10. Pipeline and Implementation

```text
For each province p in (MB, SK, AB):
  For each window w in (Run 1, Run 2, Run 3):
    1. INGEST    StatCan acres / yields / fallow (p); costs (p, 2019-T); prompt prices (p, w)
    2. PRICE     PDQ prompt rule (4.1) -> template -> guide price -> NULL (4.3)
    3. YIELD     21-yr OLS trend, N >= 10, else 5-yr mean (5)
    4. RPA       RPA = Y_hat * P - (Seed + Fertilizer + Chemicals); ratios; R_bar over T-5..T-1 (6)
    5. CALIBRATE OLS per pair; bounded beta in [0.10, 2.00]; flags (7.1-7.2)
  Window ranking on common years (diagnostic, 7.3)
  FORECAST (per release w): Step 1 CWRS -> Step 2 Tier 1, Tier 2 -> Step 3 land pool (8)
  FLAGS and Run 3 intentions benchmark (9)
```

| Module (`Canada_Planting_Model/src/`) | Role | Status |
|---|---|---|
| `settings.py` | Paths, crops, tiers, constants | Built (§3 tiers) |
| `data_loader.py` | StatCan, §5 trend yields, costs (with `Historical_Ratios` validation), §4 prices via `prompt_prices.py` | Built |
| `prompt_prices.py` | §4.1 prompt extraction from `crop_prices.duckdb` (read-only) and the §4.3 fallback hierarchy; tests in `tests/test_prompt_prices.py` | Built |
| `regression_engine.py` | §6–§7 ratios, run-specific bounded fits, flags ($\beta \le 0$, `UNCALIBRATED`), diagnostic ranking | Built; parameter export for the §8 forecast pending |
| `allocator.py` | §8.3 cropland basket (wheat split into CWRS / Durum / other; tame hay and silage excluded), rolling 25-year fallow floor, land pool, §9 flags | Built |
| `run_forecast.py` | §8.1–8.2 tier-ordered one-step forecast (`--year --run --vintage`), allocation, evaluation vs CANSIM and the no-change benchmark | Built |
| `run_calibration.py` | Nested loop, reports (`output/reports/`), charts (`output/charts/`) | Built |
| `excel_exports.py` | `output/rpa_elasticity_matrices.xlsx` (β matrices per vintage V = fits on $t \le V$, province, run) and `output/yield_trend_diagnostics.xlsx` (§5 diagnostic) | Built |
