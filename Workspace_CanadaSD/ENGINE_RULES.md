# CMFT ENGINE RULES — Canada Supply & Disposition Engine

*Rewritten 2026-09-28 as a numbered reference; protein meals (3.5) and the summary-first tab order (5.1.A) added the same day; the scope moved to 25-26 and the `CanadaSD_Main` merge (5.5) was added the same day. Every rule has an ID (`[RULE-x.y.z]`); cite it in commits and reviews. Items marked **[PLANNED]** are target behaviour not yet built. Quick-start and commands: `CLAUDE.md`.*

**How to read this file:**
- `#` headings are system pillars and `##` headings are modules.
- Each bullet is one rule.
- `>` callouts are hard constraints: never break them without changing this file first.

| Pillar | Covers |
|---|---|
| 1.0 | Architecture, source of truth, paths, commands |
| 2.0 | Crop calendars and StatCan survey mapping |
| 3.0 | Byproduct method: vegetable oils (Canola Oil, Soy Oil) and protein meals (Canola Meal, Soymeal) |
| 4.0 | Balance identities, residuals, tolerances |
| 5.0 | Excel output: tabs, formats, ratios, layout, `CanadaSD_Main` merge |
| 6.0 | Data ingestion: StatCan matching, CIMT trade |
| 7.0 | Run diagnostics and roadmap |

---

# 1.0 ARCHITECTURE & SINGLE SOURCE OF TRUTH

## 1.1 Mapping Authority

> **CONSTRAINT:** `CMFT_Main.csv` (workspace root) is the only mapping source. The retired `Archive\old_CMFT_Main.xlsx` is a reference copy the engine never reads and must never be copied back over the CSV.

- **[RULE-1.1.A]** `CMFT_Main.csv` is the flat copy of the old `Mapping_Dictionary` sheet: `Source Commodity | Crop_Year | Commodity_Group | StatCan_Raw_Metric | Tier1_Commodity_Metric | Tier2_National_Metric | Calculation_Notes`. Edit mappings only here.
- **[RULE-1.1.B]** A blank `Tier1_Commodity_Metric` marks a StatCan line that is known but deliberately unused. `StatCan_Raw_Metric = N-A` marks a line with no StatCan source (vegetable oil / meal notes) and never matches.
- **[RULE-1.1.C]** Vegetable oil marketing years are read from the `Vegetable Oil` rows of this CSV at run time (see 2.2). Grain, oilseed and pulse start months live in `config\commodities.py` and are validated against the CSV by `--check` (6.1.F).
- **[RULE-1.1.D]** `config\commodities.py` is the source of truth for commodity names: `COMMODITY_ALIASES`, `DISPLAY_NAME`, `COMMODITY_GROUP`, `CIMT_FILE_COMMODITY`, `VEG_OILS`. A commodity without a `COMMODITY_GROUP` entry raises at export instead of defaulting silently.

## 1.2 Core Data Directories & Relative Paths

> **CONSTRAINT:** All paths resolve from `Engine\config\paths.py`. The only absolute path is `CIMT_DIR` (override with the `CIMT_DIR` environment variable). The workspace root is `C:\Users\ahmed\OneDrive\Desktop\Python\Workspace_CanadaSD\`; the old `C:\Workspace_CanadaSD\` is retired.

| Item | Path (from workspace root) | Written by engine? |
|---|---|---|
| Rules / memory | `ENGINE_RULES.md`, `CLAUDE.md` | No |
| Mapping table | `CMFT_Main.csv` | No |
| Engine code | `Engine\` (`run_pipeline.py`, `engine_balancer.py`, `exporter_excel.py`, `populate_input_forecast.py`, `merge_main_workbook.py`) | — |
| Config | `Engine\config\` (`paths.py`, `commodities.py`, `mappings.py`, `styles.py`) | — |
| Modules | `Engine\modules\` (`ingest_statcan.py`, `ingest_cimt.py`, `ingest_overrides.py`, `monthly_builder.py`, `vegoil_builder.py`, `meal_builder.py`) | — |
| StatCan CANSIM | `Data_Inputs\<name>_<table>-eng\<table>.csv` + `*_MetaData.csv` (32100013, 32100359, 32100352, 25100082) | No |
| Forecast input workbook | `Data_Inputs\CanSD_Input_wb\CanSD_Input_wb.xlsx` | Yes, `*_Mo_F` tabs only (5.1.C) |
| Grain master fact table | `data_store\CMFT_Master.csv` (commodity × month) | Yes |
| Veg oil fact table | `data_store\CMFT_VegOil_Master.csv` (oil × month) | Yes |
| Protein meal fact table | `data_store\CMFT_Meal_Master.csv` (meal × month) | Yes |
| Engine output | `Outputs\CanadaSD_Engine.xlsx` | Yes (rebuilt whole) |
| Merged history + forecast | `Outputs\CanadaSD_Main.xlsx` | Yes (rebuilt whole by `merge_main_workbook.py`) |
| Hand-maintained workbook | `CanadaSD_Main.xlsx` (workspace **root**) | **Never**; the merge refuses to write it |
| CIMT trade (external) | `C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD\` | **Never** (read-only) |

- **[RULE-1.2.A]** The engine writes only the rows marked "Yes" above.
- **[RULE-1.2.B]** Never copy or duplicate CIMT files into `Data_Inputs\`.
- **[RULE-1.2.C]** The git repository root is the parent folder (`...\Desktop\Python`), not this workspace.

## 1.3 Execution Commands & CLI Flags

| Command (from workspace root) | Effect |
|---|---|
| `python Engine\run_pipeline.py --check` | Verify every input exists; validate `CMFT_Main.csv` (6.1.F); print its fingerprint |
| `python Engine\run_pipeline.py` | check → balance → export |
| `python Engine\run_pipeline.py --balance` | Rebuild `CMFT_Master.csv`, `CMFT_VegOil_Master.csv` and `CMFT_Meal_Master.csv` only |
| `python Engine\run_pipeline.py --export` | Rebuild `Outputs\CanadaSD_Engine.xlsx` only |
| `python Engine\populate_input_forecast.py --inspect` | Show detected `*_Mo_F` row/column layout |
| `python Engine\populate_input_forecast.py` | Dry run: print what would be written |
| `python Engine\populate_input_forecast.py --write` | Save to `CanSD_Input_wb.xlsx` (timestamped backup first), then print summary + stocks tie-out |
| `python Engine\merge_main_workbook.py` | Build `Outputs\CanadaSD_Main.xlsx` (5.5); run after the pipeline and after forecast edits |

- **[RULE-1.3.A]** Run the pipeline before `populate_input_forecast.py`, so the master is current. The script warns when the master is older than a StatCan/CIMT input.
- **[RULE-1.3.B]** `--write` refuses while Excel holds the workbook (`~$CanSD_Input_wb.xlsx`). Close Excel; delete the lock only if Excel is not running.
- **[RULE-1.3.C]** Pipeline flow: ingest (`modules\ingest_*`) → balance (`engine_balancer.py` + `modules\monthly_builder.py` + `modules\vegoil_builder.py` + `modules\meal_builder.py`) → export (`exporter_excel.py`). Adding a crop = entries in `config\commodities.py` + rows in `CMFT_Main.csv`; adding a data source = a new `modules\ingest_*.py`.
- **[RULE-1.3.D]** Config switches, all in `config\commodities.py`: `TRADE_SOURCE`, `STOCKS_MODE`, `CIMT_HS_PREFIX`, `CIMT_PRODUCT_HS`, `CIMT_GRAIN_EQUIV`, `SCOPE_FIRST_CROP_YEAR` / `SCOPE_LAST_CROP_YEAR`, `VEG_OIL_FIRST_CROP_YEAR`, `FEEDSTOCK_BASELINE_YEAR`, `MAIN_HISTORY_YEARS`, `MAIN_FORECAST_COUNT`, `MAIN_TAB_ORDER`, `TOTAL_TAB_MEMBERS`.

---

# 2.0 CROP CALENDARS & CANSIM MAPPING RULES

| Commodity | Marketing year | Months (tab columns) | Defined in | First year in engine |
|---|---|---|---|---|
| Wheat, Durum, Barley, Oats, Canola, Peas, Lentils | Aug–Jul | `AUG … JUL`, total `AUG/JUL` | `CROP_YEAR_START_MONTH` | 02-03 |
| Soybeans | **Sep–Aug** | `SEP … AUG`, total `SEP/AUG` | `CROP_YEAR_START_MONTH` | 02-03 |
| Canola Oil | **Sep–Aug** | `SEP … AUG`, total `SEP/AUG` | `CMFT_Main.csv` (Vegetable Oil) | 15-16 |
| Soy Oil | **Oct–Sep** | `OCT … SEP`, total `OCT/SEP` | `CMFT_Main.csv` (Vegetable Oil) | 15-16 |
| Canola Meal | **Sep–Aug** | `SEP … AUG`, total `SEP/AUG` | `CMFT_Main.csv` (Protein Meal) | 15-16 |
| Soymeal | **Oct–Sep** | `OCT … SEP`, total `OCT/SEP` | `CMFT_Main.csv` (Protein Meal) | 15-16 |

## 2.1 Standard Grain/Pulse Marketing Years (Aug–Jul)

- **[RULE-2.1.A]** A crop year is labelled by its starting calendar year: 2002 = 2002-03. Tabs show `02-03` on both `*_Mo` and `*_Yr`.
- **[RULE-2.1.B]** Historical scope is **02-03 through 25-26** (`SCOPE_FIRST_CROP_YEAR = 2002`, `SCOPE_LAST_CROP_YEAR = 2025`). A crop year that is not complete (fewer than 12 months, e.g. Soybeans 25-26 before StatCan's Jul 31 release) shows its months on `*_Mo`. Its annual total (column P) and every `*_Yr` / group column for it stay **blank**, never a partial-year sum. Forecast years live only in the `*_Mo_F` tabs.
- **[RULE-2.1.C]** Planting table 32100359 is annual: calendar year *Y* (harvest) belongs to crop year *Y/Y+1*.
- **[RULE-2.1.D]** Production enters in month 1 of the crop year (harvest).

## 2.2 Staggered Oilseed Calendars

- **[RULE-2.2.A]** Soybeans run **Sep–Aug**: StatCan 32100013 releases cover Dec = Sep–Dec, Mar = Sep–Mar, Jul = **Sep–Aug** (the full soybean year).
- **[RULE-2.2.B]** Canola Oil and Canola Meal run **Sep–Aug**; Soy Oil and Soymeal run **Oct–Sep**. They are read from the `Vegetable Oil` / `Protein Meal` rows of `CMFT_Main.csv` (`start_months()` in `vegoil_builder.py`) and are deliberately absent from `CROP_YEAR_START_MONTH`. A missing or ambiguous label raises.
- **[RULE-2.2.C]** Staggered years are combined by **label alignment**: on `Can_Vegoils_Yr` the column `15-16` holds Canola Oil Sep 2015–Aug 2016 + Soy Oil Oct 2015–Sep 2016. `Can_ProteinMeal_Yr` does the same for Canola Meal + Soymeal.

## 2.3 StatCan Survey Dates & Ending Stock Proxy Mappings

> **CONSTRAINT:** StatCan 32100013 surveys stocks only at **Dec 31, Mar 31 and Jul 31** (REF_DATE `YYYY-12`, `YYYY-03`, `YYYY-07`). There is **no Aug 31 survey**. For soybeans the **Jul 31 release proxies the Aug 31 crop-year close**.

| Release (REF_DATE) | Aug-start crops: month index | Soybeans: month index |
|---|---|---|
| `YYYY-12` (Dec 31) | 5 (Dec) | 4 (Dec) |
| `YYYY-03` (Mar 31) | 8 (Mar) | 7 (Mar) |
| `YYYY-07` (Jul 31) | 12 (Jul) | **12 (Aug)** — proxy |

- **[RULE-2.3.A]** `release_index()` in `modules\monthly_builder.py` maps every July release to month 12, so the soybean Jul 31 figure lands on the August position of the Sep–Aug year.
- **[RULE-2.3.B]** Releases are **cumulative** over the crop year; flows are disaggregated per 4.2.B.
- **[RULE-2.3.C]** Ingest keeps only the July release for **completed** crop years (`_filter_july_revision` in `modules\ingest_statcan.py`).
- **[RULE-2.3.D]** **Current-year fallback:** while a crop year's July release is unpublished (all values `..`), its Dec 31 / Mar 31 releases are kept, so the builder anchors the months they cover (e.g. Soybeans 25-26 through Mar 2026). Completed years are unaffected.
- **[RULE-2.3.E]** Months after the last stock anchor are not generated. A crop year with no stock observation is skipped.

---

# 3.0 BYPRODUCT METHODOLOGY (VEGETABLE OILS & PROTEIN MEALS)

> **CONSTRAINT:** Code in `Engine\modules\vegoil_builder.py`; output `data_store\CMFT_VegOil_Master.csv` (one row per oil × month). All StatCan volumes are **tonnes × 0.001 = KMT**. The build raises if any unit is not tonnes with scalar `units`.

| Line | Source | StatCan label / HS |
|---|---|---|
| Crush | 32100352 | `Seed crushed` |
| Production | 32100352 | `Oil produced` |
| Stocks (month-end) | 32100352 | `Oil produced (stocks)` |
| Exports, Imports | CIMT oil workbooks | Canola 1514.11 + 1514.19; Soy 1507.10 + 1507.90 (crude + refined) |
| Biofuel feedstock total | 25100082 | `Vegetable oils, total` / `Inputs` (Jan 2020 →) |

## 3.1 Data Scope & Historical Starting Anchors

- **[RULE-3.1.A]** History starts **15-16** (`VEG_OIL_FIRST_CROP_YEAR = 2015`): 32100352 oil stocks begin Aug 2015, so 15-16 is the first year that opens from a surveyed stock (Canola Oil opens Aug 31 2015, Soy Oil Sep 30 2015).
- **[RULE-3.1.B]** Columns 02-03 to 14-15 stay **blank** for every oil metric.
- **[RULE-3.1.C]** A marketing year is published only when all 12 months have every flow and the closing stock is surveyed. Otherwise it is left blank, never estimated. Example: Soy Oil 24-25, because StatCan suppresses soybean crush from Jul 2025 (status `x`).
- **[RULE-3.1.D]** A suppressed mid-year stock (Soy Oil Nov 2018) does not drop the year: its segment's residual is shared evenly (3.4.D).

## 3.2 CANSIM 25-10-0082 Biofuel Feedstock Allocation (Apparent Availability Proxy)

> **Formula, per month *t*:**
> `AS[oil] = max(0, Production + Imports − Exports)`
> `Weight[Canola Oil] = AS[canola] / (AS[canola] + AS[soy])`; `Weight[Soy Oil] = 1 − Weight[Canola Oil]`
> `Industrial[oil] = 25100082 total × Weight[oil]`

- **[RULE-3.2.A]** Weights are recomputed **monthly** from both oils' raw series over the union of their windows, so every month of either oil's year has a weight.
- **[RULE-3.2.B]** Apparent supply is floored at 0 before weighting.
- **[RULE-3.2.C]** When one oil's crush is suppressed (from Jul 2025), the weight is held **flat at the trailing 12-month average** of the last known month. These months are flagged `weight carried` in `Industrial_Basis`.

## 3.3 Backcasting & Industrial Feedstock Flatlining (15-16 to 19-20)

- **[RULE-3.3.A]** Before Jan 2020 (no 25100082 data), the monthly feedstock total is **flatlined at the 2020 calendar-year monthly average** (`FEEDSTOCK_BASELINE_YEAR = 2020`).
- **[RULE-3.3.B]** The weights stay **dynamic** (monthly, 3.2) during the backcast; only the total is flat.
- **[RULE-3.3.C]** Every month records its basis in `Industrial_Basis` (`25100082 Inputs` / `flatlined 2020 avg`, plus `apparent supply` / `weight carried`).

## 3.4 Calculated Identity Equations

| Identity | Formula |
|---|---|
| Yield | Oil produced ÷ Seed crushed (shown as %) |
| Production | Crush × Yield (= StatCan `Oil produced`) |
| Beginning Stocks | Ending Stocks of the prior month |
| Total Supply | Beginning Stocks + Production + Imports |
| Total Disposition | Food (Residual) + Industrial + Exports |
| Ending Stocks | StatCan 32100352 month-end stock (surveyed) |
| Food (Residual) | Total Supply − Exports − Industrial − Ending Stocks (**balancing plug**) |
| CHECK | Total Supply − Total Disposition − Ending Stocks |

- **[RULE-3.4.A]** Crush on oil tables is seed crushed **within the oil's own marketing year** (32100352), not the seed balance sheet's Aug–Jul crush.
- **[RULE-3.4.B]** Total Disposition **excludes** Ending Stocks (as on the grain tables), so Stocks-to-Use = Ending Stocks / Total Disposition. The spec identity "Supply = Exports + Industrial + Food + Ending Stocks" is enforced by CHECK.
- **[RULE-3.4.C]** Food (Residual) is the only plug. It may be negative in a single month (4 months so far, at most −11 KMT); annual totals are positive.
- **[RULE-3.4.D]** Where a month-end stock is missing, Food (Residual) is computed over the segment between surveyed stocks and spread evenly; stocks are rolled and snapped to each survey.
- **[RULE-3.4.E]** On `Can_Vegoils_Yr`, Industrial = Canola Oil share + Soy Oil share, i.e. the 25100082 total over each oil's own marketing year. A memo row shows the unsplit 25100082 Sep–Aug total. The two differ only by the one-month soy offset; the raw total is not used on the Industrial line because CHECK would not close.


## 3.5 Protein Meal Methodology (Canola Meal & Soymeal)

> **CONSTRAINT:** Code in `Engine\modules\meal_builder.py`, which reuses the crush loader, CIMT trade and residual roll from `vegoil_builder.py`; output `data_store\CMFT_Meal_Master.csv` (one row per meal × month). Same scope rules as the oils (3.1): history from **15-16**, blank before, and incomplete years left blank (Soymeal 24-25).

| Line | Source | StatCan label / HS |
|---|---|---|
| Crush | 32100352 | `Seed crushed` (in the meal's marketing year) |
| Production | 32100352 | `Meal produced` |
| Stocks (month-end) | 32100352 | `Meal produced (stocks)` |
| Exports, Imports | CIMT meal workbooks | Canola 2306.41; Soymeal 2304.00 |

| Identity | Formula |
|---|---|
| Yield | Meal produced ÷ Seed crushed (%): about 57% canola, 78% soy |
| Production | Crush × Yield (= StatCan `Meal produced`) |
| Total Supply | Beginning Stocks + Production + Imports |
| Total Disposition | Feed & Residual + Exports |
| Ending Stocks | StatCan 32100352 month-end meal stock |
| Feed & Residual | Total Supply − Exports − Ending Stocks (**balancing plug** = domestic disappearance, mostly feed) |
| CHECK | Total Supply − Total Disposition − Ending Stocks |

- **[RULE-3.5.A]** Meals have no industrial line. Feed & Residual is the only plug, computed monthly per segment between surveyed stocks (as in 3.4.D). It can go negative in a single month (Canola Meal: 5 months, at most −45 KMT in Sep 2022); annual totals are positive.
- **[RULE-3.5.B]** Canola Meal trade uses the HS lines present in `CIMT_Canola_Meal` (2306.41). The `Calculation_Notes` in `CMFT_Main.csv` that cite HS 2306.41/2306.49 for Soymeal are a copy slip; Soymeal trade comes from `CIMT_Soybean_Meal` (2304.00).
- **[RULE-3.5.C]** The sheet label for the plug is `Feed & Residual` (matching `CMFT_Main.csv` `Tier1_Commodity_Metric`), also called domestic disappearance (feed).
---

# 4.0 BALANCE SHEET IDENTITY & RESIDUAL GUARDRAILS

## 4.1 Identity Equations (Supply = Disposition)

| Balance sheet | Identity |
|---|---|
| Grains, oilseeds, pulses (monthly) | Beg + Prod + Imp − Food − Industrial − Crush − FSR − Seed − Exp − End = 0 |
| Vegetable oils (monthly and annual) | Beg + Prod + Imp − Exp − Industrial − Food (Residual) − End = 0 |
| Protein meals (monthly and annual) | Beg + Prod + Imp − Exp − Feed & Residual − End = 0 |

- **[RULE-4.1.A]** **Intra-year rolling:** Beg[t] = End[t−1].
- **[RULE-4.1.B]** **Inter-year bridging:** Beg[first month of CY] = End[last month of CY−1] for 03-04 onward. If the prior crop year is incomplete, the chain restarts from StatCan's reported beginning stocks.
- **[RULE-4.1.C]** **02-03 opening stocks** come from StatCan 32100013 `Total beginning stocks`; oil opening stocks come from the 32100352 stock of the month before the first month. On a `*_Mo` tab this is the only constant in the STOCKS chain, and it carries a cell comment citing its source. Never hard-code carry-in values in code.

## 4.2 Residual / FSR Calculation Rules by Commodity Class

| Class | Balancing item | Stocks |
|---|---|---|
| Cereals, Canola, Soybeans | FSR (feed, seed & residual) | Anchored to StatCan surveys |
| Pulses (Peas, Lentils) | FSR, which also carries "Other domestic disappearance"; Food = Industrial = 0 | Anchored |
| Vegetable oils | Food (Residual) | Anchored to 32100352 month-end stocks |
| Protein meals | Feed & Residual | Anchored to 32100352 month-end stocks |

- **[RULE-4.2.A]** **Anchored mode** (`STOCKS_MODE = 'anchored'`): monthly ending stocks equal StatCan's surveyed `Total ending stocks` at every survey month (2.3). Within each release period, FSR = opening + production + imports − food − industrial/crush − exports − surveyed ending stocks, spread evenly across that period's months. CIMT-vs-StatCan trade differences therefore land in FSR, never in stocks.
- **[RULE-4.2.B]** **5/3/4 rule** for Food and Industrial/Crush on cumulative releases:
  - First 5 months = Month-5 YTD / 5.
  - Next 3 months = (Month-8 YTD − Month-5 YTD) / 3.
  - Final 4 months = (Month-12 YTD − Month-8 YTD) / 4.
  - Soybeans use **4/3/5** (Dec = month 4, Mar = month 7, Jul = month 12).
  - A missing release merges its two adjacent periods, split evenly.
- **[RULE-4.2.C]** StatCan's reported FSR = seed requirements + loss in handling + animal feed, waste and dockage (multi-line metrics are summed). Seed sits inside FSR, so the `Seed` row is 0.
- **[RULE-4.2.D]** `STOCKS_MODE = 'rolled'` instead uses StatCan's reported FSR split 5/3/4 and rolls stocks from the flows. Trade differences then accumulate in stocks.

## 4.3 Zero Residual Variance Guardrail

> **CONSTRAINT:** Every balance must close. Tolerances are hard limits; observed residuals are float noise (≈1e-12 KMT, printed as `0.000000`).

| Scope | Tolerance | Enforcement | Last observed max |
|---|---|---|---|
| Grain / oilseed / pulse master (every row) | ±0.0009 KMT (0.9 t) | Balancer prints PASS/FAIL | 0.000000 KMT |
| Vegetable oils and protein meals, monthly and annual | **±0.000001 KMT** | `run_vegoil` / `run_meal` **raise** if exceeded | ~1e-12 KMT |
| `*_Yr` CHECK cells (grains) | ±0.0009 | Green conditional fill | — |
| Byproduct tables, `Can_Vegoils_Yr`, `Can_ProteinMeal_Yr`, byproduct `*_Mo` CHECK cells | ±0.000001 | Green `#D9EAD3` conditional fill | — |

- **[RULE-4.3.A]** A FAIL or raise stops the run from being trusted. Fix the cause; never widen a tolerance to pass.
- **[RULE-4.3.B]** The exporter re-reads the saved workbook and prints the byproduct stock tie-out (every oil and meal tab/table, with a PASS/FAIL zero-variance column):
  - the maximum |Beg + flows − End| rolled from the written `*_Mo` values;
  - closing stock vs raw 32100352;
  - breaks in the Beg[t] = End[t−1] chain (must be 0).

---

# 5.0 PRESENTATION & EXCEL OUTPUT FORMATTING

> **CONSTRAINT:** `exporter_excel.py` builds `Outputs\CanadaSD_Engine.xlsx` **from scratch and saves it whole**. Hand edits to that file are lost on the next run, so keep reference mock-ups in a separate copy. Always `wb.close()` after `wb.save()`.

## 5.1 Tab Naming Standards

| Tab pattern | Workbook | Content |
|---|---|---|
| `<Crop>_Mo` | CanadaSD_Engine | Monthly, metric-major, 02-03 → 24-25 |
| `<Crop>_Yr` | CanadaSD_Engine | Annual via `SUMIFS` on `<Crop>_Mo`; `Canola_Yr` / `Soybeans_Yr` also carry the oil table (rows 30–53) and the meal table (rows 56–77) |
| `CanolaOil_Mo`, `SoyOil_Mo` | CanadaSD_Engine | Monthly oil balance, 15-16 → 24-25 |
| `CanolaMeal_Mo`, `Soymeal_Mo` | CanadaSD_Engine | Monthly meal balance, 15-16 → 24-25 |
| `Can_Cereals_Yr`, `Can_Oilseeds_Yr`, `Can_Pulses_Yr` | CanadaSD_Engine | Group sums of member `*_Yr` tabs |
| `Can_Vegoils_Yr` | CanadaSD_Engine | Canola Oil + Soy Oil by marketing-year label |
| `Can_ProteinMeal_Yr` | CanadaSD_Engine | Canola Meal + Soymeal by marketing-year label |
| `<Crop>_Mo_F` | CanSD_Input_wb | Forecast input: 25-26 actuals + 26-27 → 28-29 flatline |
| `Analytics_Dashboard`, `Can_Total_Yr` + all `*_Yr` | CanadaSD_Main (`Outputs\`) | Merged history + forecast (5.5) |

- **[RULE-5.1.A]** **Tab order** (`SUMMARY_TAB_ORDER` in `config\commodities.py`; the exporter verifies it after saving):
  1. Summary tabs first, in this order: `Can_Cereals_Yr`, `Can_Oilseeds_Yr`, `Can_Vegoils_Yr`, `Can_ProteinMeal_Yr`, `Can_Pulses_Yr`.
  2. Then commodity tabs in build order, each crop as `_Mo` then `_Yr`. After `Canola_Yr` come `CanolaOil_Mo` and `CanolaMeal_Mo`; after `Soybeans_Yr` come `SoyOil_Mo` and `Soymeal_Mo`.
- **[RULE-5.1.B]** Column A label = StatCan series name (`Wheat, excluding durum`, `Durum wheat`, …) except `Peas`. Byproduct rows use `Canola oil` / `Soybean oil` / `Canola meal` / `Soymeal`; `Can_Vegoils_Yr` uses `Canada veg oils` and `Can_ProteinMeal_Yr` uses `Canada protein meals`.
- **[RULE-5.1.C]** `populate_input_forecast.py` writes only **Barley_Mo_F, Oats_Mo_F, Soybeans_Mo_F, Peas_Mo_F, Lentils_Mo_F**. **Wheat_Mo_F, Durum_Mo_F and Canola_Mo_F are protected: never modify them.**
- **[RULE-5.1.D]** `*_Mo_F` population:
  - 25-26 comes from `CMFT_Master.csv`, with CIMT trade for months past the latest StatCan release.
  - 26-27 Planted Area is the 32100359 2026 seeded area.
  - 26-27 to 28-29 are a flatline of the 25-26 row.
  - Formula cells are never overwritten, and a backup is written before saving.
- **[RULE-5.1.E]** `*_Yr` values are looked up from `*_Mo` by metric name and crop-year label (`SUMIFS`), never by hard-coded row, so rows can move without breaking links. Group, `Can_Vegoils_Yr` / `Can_ProteinMeal_Yr` and `Can_Total_Yr` lines sum their member tabs by label (column A) **and** metric (column B). Matching on the metric alone would also pick up the oil and meal tables below `Canola_Yr` / `Soybeans_Yr`.

## 5.2 Number Formatting Specifications

| Metric | Format | Unit label |
|---|---|---|
| Planted / Harvested Area | `#,##0.000` | MLN AC |
| Grain Yield | `#,##0.0` | BU/AC (group tabs: `0.000` MT/AC) |
| Grain volumes, `*_Yr` KMT lines, CHECK | `#,##0` | KMT |
| Byproduct `*_Mo` volumes | `#,##0.0` | KMT |
| Byproduct Yield, Stocks-to-Use, Proportion Harvested | `0.0%` | % |
| Ratio rows | `0%` | (blank) |
| STU Days | `#,##0` (grains) / `0` (oils, meals) | Days |

- **[RULE-5.2.A]** STU Days = Ending Stocks / Total Disposition × 365 on annual columns. Byproduct `*_Mo` monthly cells use that month's day count.
- **[RULE-5.2.B]** Formula cells return `""` (not an error) when the denominator is 0 or blank.

## 5.3 Annual Ratio Calculations

| Table | Ratio | Formula |
|---|---|---|
| Grain / oilseed / pulse `*_Yr` and group tabs | `Ratio_F&I/Prod` | (Food + Industrial or Crush) / Production |
| | `Ratio_FSR/Prod` | FSR / Production |
| | `Ratio_Exp/Prod` | Exports / Production |
| Oil tables and `Can_Vegoils_Yr` | `Ratio_I/Prod` | Industrial / Production |
| | `Ratio_FSR/Prod` | Food (Residual) / Production |
| | `Ratio_Exp/Prod` | Exports / Production |
| Meal tables (`Canola_Yr` / `Soybeans_Yr`) | `Ratio_F&R/Prod` | Feed & Residual / Production |
| | `Ratio_Exp/Prod` | Exports / Production |
| `Can_ProteinMeal_Yr` | `Ratio_Feed/Prod` | Combined Feed & Residual / Combined Production |
| | `Ratio_Exp/Prod` | Combined Exports / Combined Production |

- **[RULE-5.3.A]** Ratio rows sit **2 blank rows below CHECK** on every `*_Yr`, group and byproduct table.
- **[RULE-5.3.B]** Each ratio is `=IF(N(Production)<>0, numerator / Production, "")`.

## 5.4 Visual Layout Guidelines

| Element | Style |
|---|---|
| Font | Calibri 10 for all data and headers |
| `*_Yr` title (A1, and each oil table title) | Calibri 20 bold; marketing-year span in B (e.g. `Aug/Jul`, `Sep/Aug`, `Oct/Sep`) at 20 regular; row height 26.25 |
| `Can_Vegoils_Yr` A1 | Rich text: `Canada Vegetable Oils ` 18pt bold + `- Canola oil (Sep/Aug) & Soyoil (Oct/Sep)` 10pt italic |
| `Can_ProteinMeal_Yr` A1 | Rich text: `Canada Protein Meals ` 18pt bold + `- Canola meal (Sep/Aug) & Soymeal (Oct/Sep)` 10pt italic |
| `*_Yr` / oil table header row | Solid **black** fill, **bold white**, centred |
| `*_Mo` header row | Solid blue `#4472C4`, bold white; freeze panes `D2` |
| Grain `*_Yr` Total Supply / Total Disposition | Thick top rule |
| Byproduct tables Total Supply / Total Disposition | **Thin top, thick bottom** rule |
| Bold rows | Total Supply, Total Disposition, Ending Stocks, CHECK |
| CHECK (grain `*_Yr`) | Green `#C6EFCE` fill when within ±0.0009 |
| CHECK (byproduct tables, `Can_Vegoils_Yr`, `Can_ProteinMeal_Yr`, byproduct `*_Mo`) | Green **`#D9EAD3`** fill when within ±0.000001 |
| Body cells | Unfilled, gridlines visible; freeze panes `D3` on `*_Yr` |

- **[RULE-5.4.A]** Each byproduct table sits **2 blank rows** below the ratio rows above it: title row, header row, then Crush at +2. On `Canola_Yr` / `Soybeans_Yr` the oil table is rows 30–53 and the meal table rows 56–77 (matching `Outputs\CanadaSD_Engine_MOCK_ref.xlsx`).
- **[RULE-5.4.B]** Byproduct `*_Mo` layout:
  - Oil blocks in this order: Crush, Yield, Beginning Stocks, Imports, Production, Total Supply, Exports, Industrial, Food (Residual), Total Disposition, Ending Stocks, Stocks-to-Use, STU Days, CHECK.
  - Meal blocks: the same without Industrial, with Feed & Residual in place of Food (Residual).
  - A month is filled only when every flow and its closing stock are known; the P total only for complete years.
- **[RULE-5.4.C]** Style constants live in `config\styles.py`; change cosmetics there, not in the exporter.
- **[RULE-5.4.D]** Oils and meals share one table engine in `exporter_excel.py`, driven by `OIL_SPEC` / `MEAL_SPEC`. Add a byproduct by adding a registry in `config\commodities.py` and a spec, not new writer code.


## 5.5 CanadaSD_Main Merge (History + Forecast)

> **CONSTRAINT:** `Engine\merge_main_workbook.py` writes **`Outputs\CanadaSD_Main.xlsx`** only. It refuses to write the hand-maintained root `CanadaSD_Main.xlsx`, refuses while Excel holds the output (`~$` lock), and never modifies `CanSD_Input_wb.xlsx` (opened read-only, saved values).

| Columns (every annual tab) | Source | Header |
|---|---|---|
| D–H | Last `MAIN_HISTORY_YEARS` (5) crop years on the engine `*_Yr` header row, e.g. 21-22 … 25-26 | `21-22` |
| I–J | First `MAIN_FORECAST_COUNT` (2, max 3) populated years of each `*_Mo_F` tab | `25-26*` (yellow text) |
| K–L | One delta per forecast year = forecast − last actual year | `Δ (25-26*, 25-26)` |

- **[RULE-5.5.A]** **Rolling window:** the history labels are read from `CanadaSD_Engine.xlsx`, so the window moves when the engine scope moves. History values come from the engine fact tables (`data_store\*.csv`), which are the numbers the engine's SUMIFS formulas display. Run the pipeline first.
- **[RULE-5.5.B]** **Forecast values** come from the `*_Mo_F` annual column (P). The opening stock is the first-month STOCKS minus that month's flows. FSR = FSR + Seed. The workbook must have been saved in Excel so formula results exist; the script stops otherwise.
- **[RULE-5.5.C]** **Cells:**
  - Metric lines are values.
  - Total Supply / Disposition, Ending Stocks, STU, ratios, deltas and every summary tab are live formulas.
  - CHECK on a crop tab = Ending Stocks − the source closing stock (engine `*_Mo` STOCKS or `*_Mo_F` STOCKS), which must be 0.
  - The workbook is flagged to recalculate on open.
- **[RULE-5.5.D]** **Deltas:** `=IF(AND(ISNUMBER(forecast),ISNUMBER(actual)), forecast − actual, "")`. Both deltas are measured against the **last actual year** (not forecast-to-forecast). Negative results get the soft red fill `#FCE4D6`. They are blank when the actual is blank (e.g. Soybeans 25-26).
- **[RULE-5.5.E]** **Tabs and order:** `Analytics_Dashboard`, `Can_Total_Yr`, `Can_Cereals_Yr`, `Can_Oilseeds_Yr`, `Can_Vegoils_Yr`, `Can_ProteinMeal_Yr`, `Can_Pulses_Yr`, then one `<Crop>_Yr` per crop (`MAIN_TAB_ORDER`). No `*_Mo` tabs. `Canola_Yr` / `Soybeans_Yr` keep their oil and meal tables; these are history only, with no forecast inputs.
- **[RULE-5.5.F]** **`Can_Total_Yr`** = Cereals + Oilseeds + Pulses (`TOTAL_TAB_MEMBERS`).
  - It excludes the oil and meal byproducts, which would double count the crushed seed.
  - Its `Industrial / Crush` line takes Industrial from Cereals and Pulses and Crush from Oilseeds.
  - A1 reads `Total Canadian Field Crops (Cereals, Oilseeds & Pulses)`.
  - A summary column is filled only where every member has a value.
- **[RULE-5.5.G]** **`Analytics_Dashboard`** (tab 1):
  - Live link tables of Ending Stocks and Stocks-to-Use for each crop plus the field-crop total, over the last two actual years and the forecast years.
  - Two native clustered column charts.
  - Unpublished values show `#N/A`, so charts leave a gap instead of a false zero.
- **[RULE-5.5.H]** **Formatting:**
  - Column C and every year column have width **8**; delta columns **12**; freeze panes `D3`.
  - Tab colours: every `<Crop>_Yr` **black**; dark accents on the dashboard and summary tabs. Excel picks the tab text colour itself, so white text cannot be set.
  - CHECK rows use a green `#D9EAD3` fill (±0.0009 for crops, ±0.000001 for byproducts).
- **[RULE-5.5.I]** The script prints a summary to verify:
  - tabs in order with their colours and charts;
  - column widths;
  - delta values and formula references;
  - the stock tie-out: every column's Beg + Imp + Prod − uses − close, plus the first forecast year's opening vs the last actual close (all within ±0.0009).
---

# 6.0 DATA INGESTION & TRADE SOURCES

## 6.1 StatCan CANSIM Matching

- **[RULE-6.1.A]** CANSIM rows are matched on raw labels, normalised through `COMMODITY_ALIASES`, then mapped through `CMFT_Main.csv`.
- **[RULE-6.1.B]** **Commodity keys:**
  - `Wheat` = wheat **excluding durum**; `Durum` = durum only.
  - The aggregate `All wheat` / `Wheat, all` is kept under its own key and never used, so durum is not double counted.
  - `Chickpeas` is its own crop, not peas.
  - `Dry peas` (32100013) and `Peas, dry` (32100359) normalise to `Peas`.
- **[RULE-6.1.C]** Multi-line metrics are summed (FSR = seed + loss + feed).
- **[RULE-6.1.D]** Only table 32100013 feeds the grain/oilseed/pulse balance sheets. Crush-table (32100352) rows feed only the vegetable oil sheets (3.0).
- **[RULE-6.1.E]** A mapped StatCan value of `..` or `x` stays blank (NaN), never 0.
- **[RULE-6.1.F]** **`--check` guardrails** (`config\mappings.validate_mapping`, balance-sheet rows only). The run stops if:
  - a (normalised commodity, raw metric) pair maps to more than one target, or is also claimed by a Vegetable Oil / Protein Meal row;
  - a Tier 1 commodity lacks a line for Production, Beginning Stocks, Ending Stocks, Exports, Imports or FSR;
  - a 32100013 line with non-zero data has no mapping row (subtotals in `STATCAN_SD_STRUCTURAL` are exempt);
  - a `Crop_Year` label disagrees with `config\commodities.py`.

  Identical repeated rows, and duplicates among Vegetable Oil / Protein Meal rows, are warnings only.

## 6.2 CIMT Trade (Read-Only)

> **CONSTRAINT:** Exports and Imports come **only** from CIMT (`TRADE_SOURCE = 'cimt'`). StatCan 32100013 trade lines are not used. Read the `Raw Data` tab only, in read-only mode.

- **[RULE-6.2.A]** Files are named `CIMT_<Commodity>_<YYYYMMDD>.xlsx` (built by `cimt_build.py`). **Latest date wins**; older files for the same key are skipped.
- **[RULE-6.2.B]** Columns: `Year, Month, TradeType, HS6, Country, ValueCAD` + `KMT` (used as is) or `Quantity_MT` (÷ 1,000). Pulse files add `HS8`.
- **[RULE-6.2.C]** Monthly trade is the observed CIMT value, never split evenly. In a covered crop year a month with no rows = 0; a crop year CIMT does not cover is left **blank** and logged, never estimated.
- **[RULE-6.2.D]** File key → commodity via `CIMT_FILE_COMMODITY` (e.g. `Amber_Durum` → `Durum`, `Wheat_(Ex-Durum)` → `Wheat`, `Canola_Oil` → `Canola Oil`, `Soybean_Oil` → `Soybean Oil`).
- **[RULE-6.2.E]** Pulses use **total** trade: `CIMT_Total_Peas` (all 0713.10 HS8), `CIMT_Total_Lentils` (all 0713.40), `CIMT_Chickpeas` (all 0713.20). The Yellow/Green and Red/Green tabs are detail only.
- **[RULE-6.2.F]** HS filters (`CIMT_HS_PREFIX`) apply only to the commodities listed; HS codes are normalised first (`1004.9` → `100490`). **Oats** = 1004.00 (to 2011), 1004.10, 1004.90, 1104.12, 1104.22. Excluded: other 1104 lines, 1103.12, 1103.19.
- **[RULE-6.2.G]** Grain equivalent: `cimt_build.py` already applies **1.65** to 1104.12 / 1104.22, so `CIMT_GRAIN_EQUIV` stays **1.0**. Setting 1.65 in the engine double counts.

---

# 7.0 RUN DIAGNOSTICS & ROADMAP

## 7.1 Read After Every Run

- **[RULE-7.1.A]** CIMT coverage window per commodity, and any commodity or crop year without CIMT trade (e.g. `CIMT_Chickpeas` currently lacks a `Raw Data` tab).
- **[RULE-7.1.B]** Oats export tie-out: CIMT grain/products vs StatCan `Grain exports` / `Product exports`; the implied GE factor should be ≈1.0.
- **[RULE-7.1.C]** Engine FSR vs StatCan reported FSR per commodity (a persistent gap means missing trade lines).
- **[RULE-7.1.D]** Derived vs StatCan July ending stocks (≈0 in anchored mode); maximum residual vs tolerance; any month with negative ending stocks.
- **[RULE-7.1.E]** Vegetable oil block:
  - max CHECK and complete years per oil;
  - max CHECK and complete years per meal;
  - months with negative Food (Residual) / Feed & Residual;
  - months with a carried proxy weight;
  - the exporter's byproduct summary (4.3.B), oil proxy weights 2020 onward, the A1 rich-text runs, and the verified tab order (5.1.A).
- **[RULE-7.1.F]** After `merge_main_workbook.py`: the summary in 5.5.I, the blank (incomplete) actual years, and the forecast-opening vs actual-close check.

## 7.2 Roadmap [PLANNED]

- **[RULE-7.2.A] [PLANNED]** Match CANSIM on Vector IDs held in `CMFT_Main.csv`; Vector IDs would supersede label matching.
- **[RULE-7.2.B] [PLANNED]** Per-table commodity dictionary with exact, case-sensitive raw strings (e.g. `"Chick peas"` in 32100359 vs `"Chickpeas"` in 32100013). A missing entry would raise `COMMODITY_MAPPING_ERROR`.
- **[RULE-7.2.C] [PLANNED]** Read grain/oilseed start months from `CMFT_Main.csv` at run time, as the oils already do.
- **[RULE-7.2.D] [PLANNED]** Tab-level write isolation into `CanadaSD_Main.xlsx`: each commodity writes only its own tabs.
- **[RULE-7.2.E] [PLANNED]** Clean up the `--check` warning. The Vegetable Oil / Protein Meal rows map `Oil/Meal produced (stocks)` to both Beginning and Ending Stocks. The byproduct builders don't read these rows, so it is harmless today.
