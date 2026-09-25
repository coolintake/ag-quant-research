# CMFT ENGINE — ARCHITECTURE & DATA GUARDRAILS

*Revised 2026-09-25 to match the engine as built. Items marked **[PLANNED]** are target behaviour not yet implemented.*

## 1. WORKSPACE & PATH ANCHORS

* **Root Workspace Directory:** `C:\Workspace_CanadaSD\`
* **Script Engine Path:** `C:\Workspace_CanadaSD\Engine\` (entry point: `run_pipeline.py`)
* **Config Package:** `C:\Workspace_CanadaSD\Engine\config\` (`paths.py`, `commodities.py`, `mappings.py`, `styles.py`)
* **Ingest / Build Modules:** `C:\Workspace_CanadaSD\Engine\modules\` (`ingest_statcan.py`, `ingest_cimt.py`, `ingest_overrides.py`, `monthly_builder.py`)
* **Data Inputs Directory:** `C:\Workspace_CanadaSD\Data_Inputs\` — one sub-folder per CANSIM table, each holding the data CSV and its `*_MetaData.csv`.
* **Config Workbook:** `C:\Workspace_CanadaSD\CMFT_Main.xlsx` (`Mapping_Dictionary`, `Commodity_Dictionary`).
* **Master Fact Table:** `C:\Workspace_CanadaSD\data_store\CMFT_Master.csv` (one row per commodity × month).
* **Engine Output Workbook:** `C:\Workspace_CanadaSD\Outputs\CanadaSD_Engine.xlsx`.
* **Hand-Maintained Workbook:** `C:\Workspace_CanadaSD\CanadaSD_Main.xlsx` — the engine NEVER writes to it (see Section 6).
* All paths resolve relative to `Engine\config\paths.py`; the only absolute path is `CIMT_DIR` (override with the `CIMT_DIR` environment variable).
* Run `python run_pipeline.py --check` to verify every input before a run; `--balance` and `--export` run one stage.

## 2. READ-ONLY EXTERNAL DATA SOURCES (CIMT TRADE DATA)

* **CIMT Source Directory (Read-Only Link):** `C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD`
* **Do NOT copy or duplicate raw trade files into `Data_Inputs\`.**
* **File Format:**
  * One workbook per commodity, named `CIMT_<Commodity>_<YYYYMMDD>.xlsx` (produced by `cimt_build.py`).
  * Read strictly from the flat tab **"Raw Data"**, in read-only mode.
  * Columns: `Year`, `Month`, `TradeType` (`Export`/`Import`), `HS6`, `Country`, `ValueCAD`, plus one quantity column:
    * `KMT` — thousand metric tonnes, used as is; or
    * `Quantity_MT` — metric tonnes, divided by 1,000.
* **Latest File Wins:** if several dated files exist for one commodity, only the latest date is read (reading both would double count).
* **Commodity Mapping:** file key → engine commodity via `CIMT_FILE_COMMODITY` in `config/commodities.py` (e.g. `Amber_Durum` → `Durum`, `Wheat_(Ex-Durum)` → `Wheat`). `Yellow_Peas` and `Red_Lentils` are sub-classes and are deliberately NOT mapped to `Dry peas` / `Lentils`.
* **HS Filters (`CIMT_HS_PREFIX`):** commodities listed keep only the HS lines named; all others keep every line in their file. HS codes are normalised before matching (`1004.9`, `'1004.90'`, `100490`, `100490.0` → `100490`).
* **Oats = raw grain + oat products** (StatCan total exports include products):

  | HS6 | Description | Period |
  |---|---|---|
  | `1004.00` | Oats (legacy code) | 1988–2011 |
  | `1004.10` | Oats, seed for sowing | 2012– |
  | `1004.90` | Oats, other | 2012– |
  | `1104.12` | Rolled / flaked oats | all |
  | `1104.22` | Hulled / pearled / worked oats (reported in KGM) | all |

  Excluded: other `1104` subheadings (not oats), `1103.12` (expired Dec 2001), `1103.19` (other cereals' catch-all).
* **Grain Equivalent (GE):** `cimt_build.py` already multiplies `1104.12` and `1104.22` by **1.65** and converts KGM before writing "Raw Data". The engine's `CIMT_GRAIN_EQUIV` factors therefore stay at **1.0**; setting 1.65 in the engine would double count.

## 3. CROP YEAR & TIMELINE RULES

* **Crop-Year Start Months** are defined once, in `config/commodities.py` → `CROP_YEAR_START_MONTH`: August for all grains, oilseeds and pulses; **September for soybeans**. These must agree with `CMFT_Main.xlsx` → `Mapping_Dictionary` → `Crop_Year`. **[PLANNED]** read them directly from `Mapping_Dictionary` at run time.
* **Crop-Year Label:** by starting calendar year (2002 = 2002/03). `*_Mo` tabs show `02-03`; `*_Yr` tabs show `2002-03`.
* **StatCan 32100013 Releases** are cumulative over the crop year:
  * Aug-start crops: Dec = Aug–Dec, Mar = Aug–Mar, Jul = Aug–Jul.
  * Soybeans: Dec = Sep–Dec, Mar = Sep–Mar, **Jul = Sep–Aug** (the July release covers the full soybean year).
* **Harvest-Year Data** (planting table 32100359, annual): calendar year *Y* belongs to crop year *Y/Y+1*.
* **Historical Scope:** Crop Years **2002/03 through 2024/25** (`SCOPE_FIRST_CROP_YEAR` / `SCOPE_LAST_CROP_YEAR` in `config/commodities.py`).
* Months after the latest StatCan release are not generated; a crop year with no stock observation is skipped.

## 4. MONTHLY DISAGGREGATION

1. **Production** enters in month 1 of the crop year (harvest).
2. **Food and Industrial (Crush for oilseeds) — 5/3/4 Rule** on StatCan's cumulative releases:
   * **First 5 months** = Month 5 YTD Cumulative / 5
   * **Next 3 months** = (Month 8 YTD Cumulative − Month 5 YTD Cumulative) / 3
   * **Final 4 months** = (Month 12 YTD Cumulative − Month 8 YTD Cumulative) / 4
   * Soybeans use **4/3/5** (Dec = month 4, Mar = month 7, Jul release = month 12).
   * If a release is missing, the two adjacent periods merge and are split evenly.
3. **Exports and Imports — CIMT ONLY** (`TRADE_SOURCE = 'cimt'`):
   * The observed CIMT value for each calendar month; never split evenly.
   * StatCan 32100013 trade lines are NOT used.
   * A month with no CIMT rows in a covered crop year = 0 (no trade reported).
   * A crop year CIMT does not cover is left **blank** and reported in the log — never estimated.
   * `TRADE_SOURCE = 'statcan'` restores the StatCan 5/3/4 split.
4. **FSR (Feed, Seed & Residual)** — see Section 5.3. StatCan's reported FSR = seed requirements + loss in handling + animal feed, waste and dockage (for pulses, "Other domestic disappearance" also maps to FSR per `Mapping_Dictionary`).

## 5. STOCK ROLLING & ANCHOR GUARDRAILS

1. **2002/03 Opening Stocks:** month 1 of 2002/03 opens from StatCan 32100013 **"Total beginning stocks"** for that crop year. Never hardcode carry-in values in code; on the `*_Mo` tab this is the one constant, carried with a cell comment citing its source.
2. **Dec / Mar / Jul Stock Anchors (`STOCKS_MODE = 'anchored'`):**
   * Monthly ending stocks MUST equal StatCan's surveyed **Total ending stocks** at **Dec 31, Mar 31 and Jul 31** (Aug 31 for soybeans' July release).
   * Months between survey dates are interpolated by rolling the monthly flows forward.
3. **FSR as the Balancing Item (anchored mode):**
   * Within each release period, FSR = opening stocks + production + imports − food − industrial/crush − exports − surveyed ending stocks, spread evenly across that period's months.
   * Consequently any CIMT-vs-StatCan trade difference lands in FSR, never in stocks. The balancer log compares engine FSR with StatCan's reported FSR per commodity; a large, persistent gap indicates missing trade lines.
   * `STOCKS_MODE = 'rolled'` instead uses StatCan's reported FSR split 5/3/4 and rolls stocks from the flows (reproduces the hand-built `Wheat_Mo`, but trade differences accumulate in stocks across crop years).
4. **Intra-Year Rolling:** BegStock[Month_t] = EndStock[Month_{t-1}].
5. **Inter-Year Bridging:** BegStock[StartMonth_CY] = EndStock[EndMonth_{CY−1}] for 2003/04 through 2024/25. If the prior crop year is incomplete, the chain restarts from StatCan's reported beginning stocks.
6. **Balance Check & Tolerance:**
   * Beg + Prod + Imp − Food − Ind − Crush − FSR − Exp − End = Balance_Check
   * Absolute residual on every row must be within **±0.0009 KMT (0.9 MT)**. The balancer prints the maximum residual and PASS/FAIL.

## 6. FILE INTEGRITY & EXCEL WORKBOOK SAFETY

1. **Preservation:** the engine writes only `Outputs\CanadaSD_Engine.xlsx`. It NEVER writes to `CanadaSD_Main.xlsx`, because the exporter builds a new workbook and saves it whole — pointing it at `CanadaSD_Main.xlsx` would overwrite every tab. **[PLANNED]** tab-level writes into `CanadaSD_Main.xlsx` (Section 9.3).
2. **`*_Mo` Layout (monthly)** — contiguous, metric-major, no spacer rows:
   * Row 1 header: `Commodity | Metric | Crop Year | <12 months> | <first>/<last>` (e.g. `AUG … JUL | AUG/JUL`; soybeans `SEP … AUG | SEP/AUG`).
   * Column A = StatCan series name (e.g. `Wheat, excluding durum`, `Durum wheat`).
   * One block per metric, one row per crop year in scope, in this order:

     | Block | Monthly columns | Total column |
     |---|---|---|
     | Planted Area, Harvested Area, Yield, Production | first month only | `= first month` |
     | Exports, Imports | CIMT observed | `SUM` |
     | Food, Industrial (Crush for oilseeds), Seed | monthly | `SUM` |
     | Food & Processing | formula = Food + Industrial/Crush | `SUM` |
     | FSR | monthly | `SUM` |
     | STOCKS | formula = prior STOCKS + Prod + Imp − Exp − Food & Proc − FSR − Seed | `= last month` |

   * Seed is currently inside FSR, so the Seed row is zero.
   * Formats: areas `#,##0.000` (MLN AC), yield `#,##0.0` (BU/AC), volumes `#,##0` (KMT). Freeze panes at `D2`.
3. **`*_Yr` Layout (annual):** every value is looked up from its `*_Mo` tab by metric name and crop-year label with `SUMIFS` — never by hard-coded row number, so rows can move without breaking links. Beginning Stocks = prior year's Ending Stocks; Stocks-to-Use, STU Days and CHECK are computed on the tab; CHECK is highlighted green within ±0.0009, red outside.
4. **Save Protocol:** always issue `wb.close()` after `wb.save()` to release openpyxl file locks.

## 7. DATA INGESTION & MATCHING

1. **Current:** CANSIM rows are matched on raw labels, normalised through `COMMODITY_ALIASES` in `config/commodities.py`, and mapped to engine metrics through `CMFT_Main.xlsx` → `Mapping_Dictionary`.
2. **Commodity Keys:** `Wheat` = wheat **excluding durum**; `Durum` = durum only. The StatCan aggregate (`All wheat` / `Wheat, all`) is kept under its own key and never used, so durum is not double counted. `Chickpeas` is its own crop, not `Dry peas`.
3. **Multi-Line Metrics** are summed (e.g. FSR = seed + loss + feed).
4. **Only the 32100013 table** feeds the seed balance sheet; crush-table rows (oil/meal stocks) must not land on it.
5. **[PLANNED] Vector ID Anchoring:** match on CANSIM Vector IDs defined in `CMFT_Main.xlsx` where available; Vector IDs supersede label matching.

## 8. COMMODITY NAME RESOLUTION

1. **Current Source of Truth:** `config/commodities.py` (`COMMODITY_ALIASES`, `DISPLAY_NAME`, `COMMODITY_GROUP`, `CIMT_FILE_COMMODITY`). A commodity without a `COMMODITY_GROUP` entry raises a clear error at export instead of defaulting silently.
2. **[PLANNED] `Commodity_Dictionary` Lookup:** resolve labels per CANSIM table through `CMFT_Main.xlsx` → `Commodity_Dictionary`, with exact, case-sensitive raw strings per table ID (e.g. `"Chick peas"` in 32100359 vs `"Chickpeas"` in 32100013). Blank / `N/A` = skip that table for that commodity. A missing commodity raises:
   `"COMMODITY_MAPPING_ERROR: Target commodity '[Name]' not found in CMFT_Main -> Commodity_Dictionary."`

## 9. MODULAR ARCHITECTURE

1. **Current:** one shared, config-driven pipeline for all commodities with the same balance-sheet structure: ingest (`modules\`) → balance (`engine_balancer.py` + `modules\monthly_builder.py`) → export (`exporter_excel.py`). Adding a crop = entries in `config/commodities.py` + rows in `Mapping_Dictionary`; adding a data source = a new `modules\ingest_*.py`.
2. **Config Switches** (all in `config/commodities.py`): `TRADE_SOURCE`, `STOCKS_MODE`, `CIMT_HS_PREFIX`, `CIMT_PRODUCT_HS`, `CIMT_GRAIN_EQUIV`, scope years.
3. **[PLANNED]** Dedicated per-crop modules only for commodities whose balance sheet genuinely differs (e.g. canola oil / meal), in `Engine\crops\` (not `commodities\`, to avoid confusion with `config/commodities.py`).
4. **[PLANNED] Tab-Level Write Isolation:** each commodity writes strictly its own `*_Mo` / `*_Yr` tabs in `CanadaSD_Main.xlsx` without modifying, clearing or re-formatting any other tab.

## 10. RUN DIAGNOSTICS (READ AFTER EVERY RUN)

* CIMT coverage window per commodity, and any commodity or crop year with no CIMT trade.
* Oats export tie-out: CIMT grain / products vs StatCan "Grain exports" / "Product exports", with the implied GE factor (≈1.0 when the file is already in grain equivalent).
* Engine FSR vs StatCan reported FSR per commodity.
* Derived vs StatCan July ending stocks (≈0 in anchored mode).
* Maximum balance-check residual vs the ±0.0009 KMT tolerance, and any month with negative ending stocks.
