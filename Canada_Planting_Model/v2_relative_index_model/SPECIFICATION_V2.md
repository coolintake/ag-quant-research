# Specification V2 — Relative Cash RPA Index & Acreage Diagnostic

**Folder:** `Canada_Planting_Model/v2_relative_index_model/`
**Supersedes:** the log-log OLS model (archived in `archive/legacy_log_log_model/`)
**Region:** Manitoba, Saskatchewan, Alberta, plus a Prairie aggregate
**Purpose:** map pre-plant *relative* cash profitability against CANSIM seeded-acreage shifts. The forecast is analyst-led (Plan-A), with an automated pooled-regression fallback (Plan-B).

---

## 1. Principles

1. **Liquid cash prices only.** Prices are PDQ prompt cash bids. No guide prices, template prices or model price assumptions are used.
2. **Ex-ante inputs only.** A crop year's signal uses only information available before planting: prompt bids from the window, that year's published budget costs, and trend yields fitted on prior years.
3. **Relative, not absolute.** Acres move between crops, so every crop is measured against **CWRS Wheat**.
4. **Like with like.** A year-on-year % change in relative return is compared with a year-on-year % change in acres.
5. **No synthetic data.** Missing inputs are NULL and flagged. Nothing is interpolated, smoothed or clamped.

**Time convention:** crop year $T$ = spring planting $T$ = marketing year $T/T{+}1$ = provincial budget year $T$.

---

## 2. Scope

### 2.1 Crops

| Crop $i$ | PDQ series (`crop_symbol`) | CANSIM 32-10-0359 seeded-area & yield series | Cost source |
|---|---|---|---|
| **CWRS** (benchmark) | `1 CWRS 13.5` (`CWRS`) | Wheat, Canada Western Red Spring (CWRS) — class series | Provincial CWRS budget |
| **CPSR** | `1 CPSR 11.5` (`CPSR`) | Wheat, Canada Prairie Spring Red (CPSR) and Canada Prairie Spring White (CPSW) | AB, MB: provincial CPS budget · SK: **CWRS budget as an exact proxy, all years** (SK publishes no CPS budget) |
| **Durum** | `1 CWAD 13.0` (`DURUM`) | Wheat, durum | Provincial Durum budget |
| **Canola** | `1 CDA CANOLA` (`CANOLA`) | Canola (rapeseed) | Provincial Canola budget |
| **Yellow Peas** | `2 YELLOW PEAS` (`Y_PEAS`) | Peas, dry | Provincial Field / Yellow Pea budget |

"Spring wheat (excl. Durum)" is never used, because it contains CPSR and the other classes.

### 2.2 Province routing

| Province | Crops | Exclusions |
|---|---|---|
| Manitoba | CWRS, Canola, Yellow Peas | **Durum** (no Durum-belt PDQ zone, no budget); **CPSR** (zero PDQ CPSR quotes in MB 2019–2026) |
| Saskatchewan | CWRS, CPSR, Durum, Canola, Yellow Peas | — |
| Alberta | CWRS, CPSR, Durum, Canola, Yellow Peas | — |

PDQ zones: CWRS, CPSR, Canola and Peas use every PDQ zone in the province. **Durum uses S ALTA (→ AB) and SW SASK (→ SK) only.**

### 2.3 Coverage note

The five crops cover roughly 75% of Saskatchewan, 69% of Alberta and 60% of Manitoba cropland (2026). V2 is a signal and diagnostic framework, not a land-balance model.

---

## 3. Inputs

### 3.1 Price windows and prompt rule

| Window $w$ | Dates (inclusive) | Release |
|---|---|---|
| Run 1 | Dec 15 ($T-1$) – Jan 15 ($T$) | Mid-January |
| Run 2 | Jan 15 – Feb 15 ($T$) | Mid-February |
| Run 3 | Feb 15 – Mar 15 ($T$) | Mid-March |

For each crop × zone × price date, the **prompt bid** is the bid for the window's start delivery month (Run 1 DEC of $T-1$, Run 2 JAN, Run 3 FEB). If that bid is NULL or unlisted, the earliest later delivery month with a bid is used, no later than JUL of $T$ (old crop only). Zero and `-` are NULL.

Provincial daily price = mean over quoting zones. $P_{i,p,w,T}$ = mean of the daily prices in the window ($/bu).

**Checks:**
- A window with fewer than **10 quoted days** is NULL, flagged `THIN_WINDOW`.
- A price outside the plausible $/bu range for its crop is rejected, flagged `UNIT_SUSPECT`. This guards against $/t exports.

### 3.2 Trend yield

$\hat{Y}_{i,p,T}$ = OLS linear trend $\text{Yield} = \gamma_0 + \gamma_1 \cdot \text{Year}$, fitted on CANSIM actual yields for **2005 … $T-1$** and evaluated at $T$. The window **expands** from 14 points ($T = 2019$) to 21 points ($T = 2026$).
- 2005 is the start of the modern-genetics era (Clearfield, pod-shatter-resistant canola).
- Year $T$'s own yield is never used, so harvest outcomes such as the 2021 drought stay out of spring signals.
- At least 10 non-NULL points are required, otherwise the value is NULL, flagged `INSUFFICIENT_YIELD_HISTORY`.
- A zero yield (StatCan's "not seeded") is NULL.

### 3.3 Variable costs

$\text{VC}_{i,p,T}$ = Seed + Fertilizer + Chemicals ($/ac). Costs are the same in all three windows of a year.
- **2019–2025:** RPA_Calculator normalized history tables (`output/{ab,sk,mb}/mvs/*_historical_2019_2026.csv`). They must reproduce the master workbook's `Historical_Ratios` sheet.
- **2026+:** `RPA_Master_Calculator.xlsx`, `RPA_Summary_<prov>`.
- Soil-zone averaging follows RPA_Calculator `[REGION-AVG-RULE]`: Durum on Brown + Dark Brown only.

### 3.4 Acres

CANSIM seeded area (acres; hectares × 2.47105 only if no acre series). The latest year is labelled **preliminary** and the extract date is recorded.

---

## 4. Calculations

All quantities are per crop $i$, province $p$, window $w$ and crop year $T$.

**Step 1 — Return per acre ($/ac)**

$$\text{RPA}_{i,p,w,T} = \hat{Y}_{i,p,T} \times P_{i,p,w,T} - \text{VC}_{i,p,T}$$

`NONPOSITIVE_RPA` is flagged when RPA ≤ 0. The value is kept in Block 1, but every ratio built from it is NULL.

**Step 2 — Base-100 RPA index (6-year base)**

$$\text{Base}_{i,p,w} = \frac{1}{6}\sum_{k=2019}^{2024} \text{RPA}_{i,p,w,k}, \qquad \text{Index}_{i,p,w,T} = 100 \times \frac{\text{RPA}_{i,p,w,T}}{\text{Base}_{i,p,w}}$$

- The base is **window-matched**: each window is indexed to its own 2019–2024 average, so 100 means normal for that point of the pre-plant season.
- This removes seasonal bias. For example, Durum's margin relative to CWRS is typically 12–16% higher in Run 1 than in Run 3.
- The base uses only 2019–2024 history, so there's no look-ahead at any release.
- Indices aren't comparable across windows: 100 in Run 2 and 100 in Run 3 are different baselines.
- The panel records the base used in `base_window`.
- Base ≤ 0 or fewer than 4 valid base years → NULL, flagged `UNSTABLE_BASE` (checked per window).
- Step 4 (%ΔRelRPA), Plan-B and the evaluation don't depend on the base.

**Step 3 — Relative index vs CWRS (PerfChart measure)**

$$\text{RelRPA}_{i,p,w,T} = \frac{\text{RPA}_{i,p,w,T}}{\text{RPA}_{\text{CWRS},p,w,T}}, \qquad \text{RelIndex}_{i,p,w,T} = 100 \times \frac{\text{Index}_{i,p,w,T}}{\text{Index}_{\text{CWRS},p,w,T}} = 100 \times \frac{\text{RelRPA}_{i,p,w,T}}{\text{Base}_{i,p}/\text{Base}_{\text{CWRS},p}}$$

- RelIndex > 100 means crop $i$ is more profitable relative to CWRS than in its 2019–2024 norm.
- CWRS ≡ 100 by construction.

**Step 4 — Year-on-year changes (same window)**

$$\%\Delta\text{RelRPA}_{i,p,w,T} = \frac{\text{RelRPA}_{i,p,w,T}}{\text{RelRPA}_{i,p,w,T-1}} - 1, \qquad \%\Delta\text{Acres}_{i,p,T} = \frac{\text{Acres}_{i,p,T}}{\text{Acres}_{i,p,T-1}} - 1$$

- **CWRS's own signal:** CWRS has no relative change against itself. For year-on-year analysis and Plan-B, CWRS uses **$\%\Delta(\text{RPA}_{\text{CWRS}}/\text{RPA}_{\text{Canola}})$**, its main acreage competitor, flagged `CWRS_VS_CANOLA`.

---

## 5. Prairie aggregate

**Fixed weights:** each province's share of the crop's average seeded acres over **2019–2024** (the base period). Weights are re-normalized over the provinces where the crop is in scope (Durum and CPSR: SK + AB).

$$\text{RPA}_{i,\text{Prairie},w,T} = \sum_{p} \omega_{i,p}\,\text{RPA}_{i,p,w,T}, \qquad \omega_{i,p} = \frac{\overline{A}_{i,p,2019\text{–}2024}}{\sum_{p'}\overline{A}_{i,p',2019\text{–}2024}}$$

Fixed weights keep the aggregate from moving when only the provincial acreage mix changes.

- Prairie Base, Index, RelRPA, RelIndex and %ΔRelRPA follow §4, applied to the Prairie RPA.
- Prairie %ΔAcres uses the summed provincial acres of the in-scope provinces.

---

## 6. Forecasting

**Hierarchy: expert-gated updates.** Plan-B is the automated *prior*: a data-driven %ΔAcres for every province × crop at every release. Plan-A is the analyst's *update* to that prior.
- Where the analyst enters an assignment, Plan-A is the release forecast.
- Where they don't, Plan-B stands as the fallback.
- Plan-B is never the primary forecast. Its magnitude skill is thin (§6.2), and the backtest supports it only as a directional baseline that the analyst confirms or overrides.

### 6.1 Plan-A — analyst assignment (primary)

The analyst sets a %ΔAcres (or acreage) per province × crop for year $T$ in `inputs/plan_a_assignments.csv`. The columns are `province, crop, year, window, pct_change, rationale, author, date`. The analyst starts from the Plan-B prior and adjusts it using:
- the relative index matrix;
- the historical %ΔRelRPA vs %ΔAcres pairs;
- external context: futures ratios, specialty-crop bids, rotation and agronomy.

$$\widehat{A}^{\,A}_{i,p,T} = A_{i,p,T-1} \times (1 + \text{pct\_change}_{i,p,T})$$

**Gating rules (operating procedure):**
1. Every Plan-A entry records its rationale.
2. An entry whose direction is opposite to the Plan-B prior, or that differs from it by more than the historical range of %ΔAcres for that series, gets a second review before release.
3. Plan-A and Plan-B are both scored against CANSIM (§6.3), so the value the analyst adds over the prior is tracked each year.

*Implementation:* each workbook's `Plan-B Forecast` tab builds the **release forecast**: Plan-A where entered, else Plan-B, with a `Source` column. Rule 2 is automated as two gate flags (`export_excel.release_forecast`):
- `PLAN_A_SIGN_CONFLICT`: Plan-A is opposite in sign to Plan-B.
- `PLAN_A_OUTSIDE_RANGE`: |Plan-A| exceeds the largest |%ΔAcres| of that series over the fit years.

Plan-A entries are matched on province, crop, year and window. The window may be written `RUN3`, `R3` or `Run 3`.

### 6.2 Plan-B — pooled regression through the origin (automated fallback / prior)

Fit one OLS per window $w$, pooled across provinces (MB, SK, AB, not the Prairie aggregate) and crops (CWRS on its Canola signal) over years 2020 … last actual, **with the intercept forced to zero**:

$$\%\Delta\text{Acres}_{i,p,T} = b_w \cdot \%\Delta\text{RelRPA}_{i,p,w,T} + e$$

**Current estimates (2020–2025):**

| Window | $b_w$ | SE | p |
|---|---|---|---|
| Run 1 | 0.093 | 0.058 | 0.11 |
| Run 2 | 0.080 | 0.055 | 0.15 |
| Run 3 | **0.098** | 0.056 | 0.08 |

The headline Run 3 model is $\%\Delta\text{Acres} = 0.098 \cdot \%\Delta\text{Signal}$, i.e. a 10% gain in relative return implies about +1% acres. $b_w$ is re-estimated every year, so treat these numbers as the current calibration, not constants.

- **Why no intercept.** The backtest (`analysis/backtest_plan_b.py`, `output/backtest/`) compared:
  - the earlier model with an intercept, $a + b x$;
  - crop-province fixed effects;
  - tier-specific slopes;
  - mixed models (random intercepts and slopes);
  - a no-change benchmark.

  The fitted drift $a \approx +2\%$ was insignificant (p ≈ 0.2) but pushed most forecasts upward, cutting the hit rate to about 50%. Removing it gave the lowest out-of-sample error of all specifications (Run 3 WAPE 5.2% vs 6.0%, hit rate 69% vs 49%) and the most stable slope (SD across refits 0.018). None of the more flexible specifications improved consistently. Fixed effects overfit (adjusted R² < 0), and the mixed model's group variance was estimated at zero. A zero-signal year (no relative change) is therefore forecast as no change in acres.
- **No clamping, no bounds** other than the zero intercept. Report $b_w$, SE, p-value, centered R² (it can be ≈ 0 or slightly negative without an intercept), N (up to 78 per window at 2020–2025: 13 province-crop series × 6 changes) and the residuals.
- If $b_w \le 0$, the window is flagged `PLAN_B_NO_SIGNAL` and Plan-B is not used for that release.
- A fit needs at least 12 observations (`forecast_engine.MIN_FIT_OBS`), so out-of-sample predictions start in 2022 (fitted on 2020–2021).

$$\widehat{A}^{\,B}_{i,p,T} = A_{i,p,T-1} \times (1 + b_w \cdot \%\Delta\text{RelRPA}_{i,p,w,T})$$

### 6.3 Evaluation

The following are computed on every year with actuals, with Plan-B evaluated out-of-sample (fitted on years before $T$ only):
- **Directional hit rate:** sign(%ΔRelRPA) = sign(%ΔAcres).
- **Spearman rank correlation** across crops within each province-year.
- **WAPE** for Plan-A, Plan-B and the no-change forecast, against CANSIM.

---

## 7. Outputs

### 7.1 Tidy data (source of truth)

`output/v2_panel.csv`: one row per province × crop × window × year. Columns:
- prices, quoted days and delivery months;
- trend yield and its N, and VC;
- RPA, Base, `base_window`, Index, RelRPA, RelIndex and %ΔRelRPA;
- acres and %ΔAcres;
- flags.

The workbooks are views of this table.

### 7.2 Workbooks, one per window

`output/Prairie_RPA_Acreage_Diagnostic_R1.xlsx`, `_R2.xlsx` and `_R3.xlsx`. Each holds only its window's data, indexed to that window's base (§4 Step 2).

Tabs: `README` (definitions, CHECK rows, flags), `Manitoba`, `Saskatchewan`, `Alberta`, `Prairie Aggregate`, `Plan-B Forecast`, and `Evaluation` (this window's hit rate, rank correlation and WAPE, plus Plan-A).

The `Plan-B Forecast` tab holds, in order:
1. The model box: rule `% Δ acres = b × % Δ signal`, intercept 0 (forced), b, SE, p, N, centered R², and a CHECK row for b > 0.
2. The forecast-year **release forecast** by province × crop: signal, Plan-B, Plan-A, release % Δ, source, prior-year acres, release acres, CANSIM acres and % Δ, release and no-change errors, gate flags. Prairie rows sum the provinces.
3. A bar chart of release vs CANSIM % Δ.
4. The observations behind the rule, with a scatter showing the rule through the origin.

Each geography tab has crop-year rows × crop columns:

| Block | Content | Format |
|---|---|---|
| 1 | RPA ($/ac) | `#,##0` |
| 2 | Index (this window's 2019–2024 average = 100) | `0.0` |
| 3 | RelIndex vs CWRS (CWRS = 100) | `0.0` |
| 4 | %ΔRelRPA (YoY, raw ratio, base-free; CWRS on its Canola signal) | `0.0%` |
| 5a / 5b | CANSIM seeded area (MLN AC) / YoY %ΔAcres (latest year preliminary, italic) | `0.000` / `0.0%` |
| 6a–c | Inputs: prompt price ($/bu), trend yield (bu/ac), variable costs ($/ac) | `0.00` / `0.000` / `#,##0` |

Charts (native Excel, **no dual axes**):
- **PerfChart:** RelIndex by crop year, one line per crop. Line roles: Canola is the hero line (black, 2.5pt); Durum is the highlight (green #16A34A, 2.0pt); Peas and CPSR are gray #595959 (long-dash and dotted, 1.5pt); CWRS is the benchmark at 100 (#A6A6A6, short dash, 1.25pt).
- **Stacked pair per crop:** %ΔRelRPA (black line with markers) above %ΔAcres (bars). Both share the same year axis.

**Style:** "The Desk | Chilmeran Research" (`Python/Design/DesignSkills.md`).
- **Tables:** Calibri; 18pt bold title in A1; black header rows with white bold text; left-aligned labels and right-aligned numbers; no sheet gridlines; CHECK rows in #C6EFCE / #006100.
- **Flags:** flagged cells are shaded #E5E5E5 with a comment explaining the flag.
- **Charts:** square black outline; white plot with #E5E5E5 horizontal gridlines only; 18pt #595959 titles; 10pt axes with x labels rotated 90°; uppercase y-axis titles; #D4F1D6 bars with 0.75pt black borders and a 90% gap width; legend on the right for multi-series charts, none for single-series.

### 7.3 Flags

`THIN_WINDOW`, `UNIT_SUSPECT`, `INSUFFICIENT_YIELD_HISTORY`, `NONPOSITIVE_RPA`, `UNSTABLE_BASE`, `CWRS_VS_CANOLA`, `PLAN_B_NO_SIGNAL`, `PLAN_A_SIGN_CONFLICT`, `PLAN_A_OUTSIDE_RANGE`, `EXCLUDED` (MB Durum, MB CPSR), `PRELIMINARY_ACRES`.

---

## 8. Implementation layout

```text
v2_relative_index_model/
├── SPECIFICATION_V2.md
├── config.py              crops, CANSIM series, PDQ symbols and zones, windows, base period, exclusions, paths, checks
├── engine/
│   ├── __init__.py
│   ├── price_engine.py    §3.1 prompt bids from crop_prices.duckdb (read-only), checks
│   ├── yield_engine.py    §3.2 expanding 2005..T-1 trend yields
│   ├── rpa_engine.py      §3.3 costs (CPS proxy rule) + §4 Step 1 RPA
│   ├── index_engine.py    §4 Steps 2-4, §5 Prairie aggregate
│   └── forecast_engine.py §6 Plan-A ingestion, Plan-B pooled OLS, §6.3 evaluation
├── export_excel.py        §7.2 three window workbooks (R1/R2/R3), The Desk style
├── run_v2.py              entry point: build panel -> forecast/evaluate -> export
├── analysis/backtest_plan_b.py   Plan-B specification backtest (-> output/backtest/)
├── inputs/plan_a_assignments.csv
├── tests/
└── output/
```

V2 is self-contained: it reads the raw sources (StatCan CSV, `crop_prices.duckdb`, RPA_Calculator budgets) **read-only**, and imports nothing from the archived model or the legacy `src/` modules.
