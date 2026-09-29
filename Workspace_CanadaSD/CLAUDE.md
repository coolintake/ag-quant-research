# Workspace_CanadaSD — Canada grain / oilseed / pulse / vegetable oil S&D engine

`ENGINE_RULES.md` is the full numbered rule set (`[RULE-x.y.z]`). Read the relevant pillar before changing engine logic, and cite rule IDs in commits. This file is the quick reference.

## Directory layout (relative to this folder)

| What | Path |
|---|---|
| Rule set / this memory | `ENGINE_RULES.md`, `CLAUDE.md` (root) |
| Mapping table — **single source of truth** | `CMFT_Main.csv` (root) |
| Retired mapping workbook (reference only) | `Archive\old_CMFT_Main.xlsx` |
| Engine entry point | `Engine\run_pipeline.py` (in `Engine\`, not the root) |
| Forecast populator | `Engine\populate_input_forecast.py` |
| Config | `Engine\config\` — `paths.py`, `commodities.py`, `mappings.py`, `styles.py` |
| Modules | `Engine\modules\` — `ingest_statcan.py`, `ingest_cimt.py`, `ingest_overrides.py`, `monthly_builder.py`, `vegoil_builder.py`, `meal_builder.py` |
| StatCan CANSIM inputs | `Data_Inputs\<name>_<table>-eng\<table>.csv` — `S&D_32100013-eng`, `Planting_32100359-eng`, `Crush_32100352-eng`, `Feedstock_25100082-eng` |
| Grain master fact table | `data_store\CMFT_Master.csv` |
| Veg oil / protein meal fact tables | `data_store\CMFT_VegOil_Master.csv`, `data_store\CMFT_Meal_Master.csv` |
| Forecast input workbook | `Data_Inputs\CanSD_Input_wb\CanSD_Input_wb.xlsx` (tabs `*_Mo_F`) |
| Engine output workbook | `Outputs\CanadaSD_Engine.xlsx` (rebuilt whole on every export) |
| Merged history + forecast | `Outputs\CanadaSD_Main.xlsx`, built by `Engine\merge_main_workbook.py` (the root `CanadaSD_Main.xlsx` is hand-maintained and never written) |
| CIMT trade (external, read-only) | `C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD\CIMT_<Commodity>_<YYYYMMDD>.xlsx`, tab `Raw Data`; latest date wins; override with env var `CIMT_DIR` |

The git repo root is the parent folder (`...\Desktop\Python`), not this workspace.

## Commands (run from this folder)

```bash
python Engine/run_pipeline.py --check      # verify inputs + validate CMFT_Main.csv
python Engine/run_pipeline.py              # check -> balance -> export
python Engine/run_pipeline.py --balance    # rebuild data_store CSVs only
python Engine/run_pipeline.py --export     # rebuild Outputs/CanadaSD_Engine.xlsx only
python Engine/populate_input_forecast.py --inspect   # show *_Mo_F layout
python Engine/populate_input_forecast.py             # dry run
python Engine/populate_input_forecast.py --write     # save (backup first) + summary + stocks tie-out
python Engine/merge_main_workbook.py                 # build Outputs/CanadaSD_Main.xlsx (after the pipeline / forecast edits)
```

Run the pipeline before populating forecasts. `--write` refuses while Excel holds the workbook (`~$CanSD_Input_wb.xlsx`); delete that lock only if Excel is not running.

## Core constraints & guardrails

- **Protected tabs:** never modify `Wheat_Mo_F`, `Durum_Mo_F`, `Canola_Mo_F`. The populator writes only Barley/Oats/Soybeans/Peas/Lentils `_Mo_F` [RULE-5.1.C].
- **Mapping source:** `CMFT_Main.csv` is the single source of truth; never overwrite it from `CMFT_Main.xlsx` [RULE-1.1].
- **Write limits:** the engine never writes `CanadaSD_Main.xlsx` or anything in `CIMTD\`. Hand edits to `Outputs\CanadaSD_Engine.xlsx` are lost on the next export, so keep mock-ups in a separate copy.
- **Calendars** [2.0]:
  - Aug–Jul for Wheat, Durum, Barley, Oats, Canola, Peas, Lentils.
  - **Soybeans Sep–Aug**, **Canola Oil / Canola Meal Sep–Aug**, **Soy Oil / Soymeal Oct–Sep**.
  - Oil and meal years come from `CMFT_Main.csv`; grain years from `CROP_YEAR_START_MONTH`.
- **StatCan survey dates** [2.3]:
  - 32-10-0013 surveys stocks on Dec 31, Mar 31 and Jul 31 only.
  - For Soybeans, the **Jul 31 survey proxies the Aug 31 ending-stock anchor** (month 12).
  - While a year's July release is unpublished (`..`), its Dec/Mar releases anchor the months they cover.
- **Biofuel feedstock** [3.2–3.3]: 25-10-0082 vegetable-oil feedstock intake is split monthly between Canola Oil and Soy Oil by the **Apparent Domestic Availability Proxy**: AS = max(0, Production + Imports − Exports); Weight = AS share. Before 2020 the total is flatlined at the 2020 average.
- **Balancing items** [4.2–4.3]:
  - FSR balances grains/oilseeds/pulses (tolerance ±0.0009 KMT); Food (Residual) balances vegetable oils and Feed & Residual balances protein meals (both ±0.000001 KMT; the build raises).
  - Exports/Imports come from CIMT only.
  - Pulses carry Other domestic disappearance in FSR (Food = Industrial = 0).
- **History** [2.1, 3.1, 3.5]: 02-03 → 25-26 for crops (an incomplete year, e.g. Soybeans 25-26, stays blank on every annual total), 15-16 onward for oils and meals. Incomplete years stay blank, never estimated. Soy Oil / Soymeal 24-25 are blank because StatCan suppresses soybean crush from Jul 2025.
- **Byproduct layout** [5.1, 5.4]:
  - `Canola_Yr` / `Soybeans_Yr` carry the oil table (rows 30–53) and the meal table (rows 56–77).
  - Monthly tabs: `CanolaOil_Mo`, `CanolaMeal_Mo`, `SoyOil_Mo`, `Soymeal_Mo`.
  - Combined: `Can_Vegoils_Yr`, `Can_ProteinMeal_Yr`, each with a two-font A1.
- **Tab order** [5.1.A]: `Can_Cereals_Yr`, `Can_Oilseeds_Yr`, `Can_Vegoils_Yr`, `Can_ProteinMeal_Yr`, `Can_Pulses_Yr` first, then commodity tabs (`_Mo`, `_Yr`, then its oil/meal `_Mo`). Set in `SUMMARY_TAB_ORDER`.
- **openpyxl** [5.0]: preserve formats and formulas; never touch unrelated tabs; `wb.close()` after `wb.save()`; style constants live in `Engine\config\styles.py`.
- **CanadaSD_Main** [5.5]:
  - Columns: the last 5 engine years (21-22 … 25-26), forecast years `25-26*` / `26-27*` from `*_Mo_F` saved values (`MAIN_FORECAST_COUNT`, max 3), then `Δ (f*, last actual)` deltas with a soft red fill on negatives.
  - Tab order: `Analytics_Dashboard`, `Can_Total_Yr` (field crops only, no byproducts), the group tabs, then crop `*_Yr` tabs (black tabs).
  - Save `CanSD_Input_wb.xlsx` in Excel before merging so formula results exist.
- **Summary SUMIFS** match column A label **and** metric; the oil/meal tables below `Canola_Yr` / `Soybeans_Yr` share metric names.
