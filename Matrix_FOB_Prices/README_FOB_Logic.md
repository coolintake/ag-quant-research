# Matrix FOB Calculator: Logic, Data Flow & Architecture

An Excel-first calculator for **Canadian grain export FOB prices** at **Vancouver (VC)**, **Thunder Bay (TBAY)** and **St. Lawrence Direct (STL)**. It covers 9 crops, the 9 PDQ zones and a rolling 6-month delivery window, with export basis vs futures and weekly history back to 2019.

A world module (`Hammersmith\` folder) adds the weekly **Hammersmith** world FOB and ocean freight report, for **Canada-vs-world FOB spreads** and **landed (CIF) comparisons** at destination (section 8).

Python moves the data in and out. Excel does all the calculating and displaying.

---

## 1. Daily use

| Step | What to do |
|---|---|
| 1 | Download the latest PDQ price CSV into `Canada_Crop_Prices\input\`, then run `python backfill_pdq.py` there. (The PDQ API subscription has ended; CSVs are the only feed.) |
| 2 | Double-click **`Refresh_FOB.bat`**. It runs both data drops (including the Hammersmith world step), then Excel **Refresh All** + save, then `src\verify.py`. |
| 3 | Open `Matrix_FOB_Calculator.xlsx`. The **Control** tab shows the data dates and the CHECK row. |
| 4 | Optional: on **Manual_Inputs**, enter elevator bids for crops or months PDQ doesn't quote. The matrices update instantly. |
| 5 | Optional: double-click **`Save_Snapshot.bat`** to record today's matrix in the history store. |

You can also use the workbook's own **Data → Refresh All** after running `python src/run_pull.py` and `python src/run_historical_drop.py`.

---

## 2. Data flow

```text
 Canada_Crop_Prices\data\crop_prices.duckdb (read-only; fed by your PDQ CSV downloads)
        │
        ├─ run_pull.py ─────────────► data_drop\pdq_latest.csv   latest PDQ snapshot (all delivery months)
        │        └─ Bank of Canada ─► data_drop\fx.csv           as-of, run month, PDQ date/age, FXUSDCAD (4 dp)
        │
        └─ run_historical_drop.py ──► data_drop\history_chart.csv  weekly 2019→today (pro-forma / recorded)

 hammersmithltd.blogspot.com (weekly report, Blogger JSON feed)
        │
        └─ run_pull.py → Hammersmith\hammersmith_scraper.py  (non-fatal step)
                 ├─► cache\hammersmith\raw\*.json.gz              raw posts (re-downloadable)
                 ├─► data_drop\history\world_fob_history.parquet   + world_freight_history.parquet (2019→today)
                 └─► data_drop\world_fob_latest.csv, world_freight_latest.csv   latest report (atomic write)

 CAN_Costing_Input.xlsx (tab "Raw") ───────────┐
 data_drop\*.csv ──────────────────────────────┴─► Power Query ─► Data_* tabs ─► Calc ─► FOB_Summary / Basis
                                                                               ├────────► Historical_Charts
                                                                               └─► World_FOB_Matrix / Global_CIF_Matrix
                                                                                   (+ Data_World / Data_Freight)
 save_snapshot.py ◄── reads the SAVED workbook ──► data_drop\history\pdq_fob_history.parquet
                                                    + data_drop\history\manual_indications_history.parquet
                                                    (fed back into history_chart.csv as "recorded")
```

Python never edits the workbook after it is built. Excel's own Power Query and recalculation do all updates.

---

## 3. Workbook tabs

| Tab | Purpose |
|---|---|
| **Control** | Run status (as-of, PDQ date and age, FX rate and date, costing file timestamp), data checks, CHECK row, how-to |
| **FOB_Summary** | Three Crop × Zone matrices (FOB VC / TBAY / STL Direct). **Month** and **Unit** (USD/MT, USD/bu, CAD/MT) dropdowns. `*` marks a manual indication. |
| **Basis** | Export basis matrices per route: CWRS and CPSR in USD/bu, Canola in CAD/MT and USD/MT, with the futures contract and price |
| **Historical_Charts** | Line chart with dropdowns: commodity, focus zone, route, metric, compare by (None / Zones / Routes), horizon, date range |
| **Manual_Inputs** | Your bids (blue columns A–H). Grey columns calculate CAD/MT, the window month and a status. |
| **Calc** | The engine: one row per crop × zone × route × window month (1,458 rows). Formulas only. |
| **Config** | Crop rules, zones, routes, the 6-month window, dropdown lists, chart metrics, stale-day settings |
| **World_FOB_Matrix** | World FOB by delivery month × origin (Hammersmith), Canadian FOB for the same months (selected zone), and the **Canada − World spread** matrix. Dropdowns: commodity, ref zone, unit, spread month, band. Green = Canada cheaper, red = Canada premium. |
| **Global_CIF_Matrix** | Landed cost at a destination: FOB + ocean-freight proxy = CIF for Canadian routes and each competitor, and the **Canada − competitor CIF spread**. Dropdowns: commodity, destination, ref zone, month, band. |
| **FOB_Indications** | Every parsed world FOB row of the latest report (filter by commodity): delivery month, derivation, bid/offer, flag, raw quote, review note |
| **Freight_Rates** | Origin × destination freight matrix (the row CIF uses) and the filtered freight rows (origin / destination / vessel dropdowns) |
| **World_Config** (hidden) | Series map, freight proxies and dropdown lists, written from `Hammersmith\hs_config.py` by `src\wire_world.py` |
| **Data_PDQ / Data_FX / Data_Costing / Data_History / Data_World / Data_Freight** | Power Query outputs (read-only; `Data_History` is hidden). `Data_FX` also carries the Hammersmith step status (`world_*`). |

---

## 4. Calculation logic

### 4.1 Window and prices
- **Window:** the run month plus the next 5 (`Config!Win_*`). An October run covers Oct-26 to Mar-27.
- **Price per crop × zone × month:**
  1. The **PDQ quote** from the latest snapshot on or before the as-of date.
  2. Else the **manual indication**: the latest *valid* entry for that crop, zone and month, or else for that crop with zone **ALL**.
  3. Else **N/A**. There is no synthetic carry-forward.
- **Manual entries never override PDQ.** An entry is *valid* if it is complete and inside the window. Entries older than 7 days are flagged **STALE** but still used (change this on `Config`).
- **Durum** is priced only in **S ALTA** and **SW SASK** (the durum belt). All other zones show N/A ("Excluded" in Calc).

### 4.2 FOB build-up (CAD/MT)

```text
FOB (CAD/MT) = Cash + Primary Elevation + Rail Freight + Port Elevation + Export Margin + Other Fees
```

Every cost comes from `CAN_Costing_Input.xlsx`, tab **Raw**, matched on cost commodity | zone | route:

| Report route | Raw route row | Notes |
|---|---|---|
| FOB VC | `VC` | Rail to Vancouver + VC terminal elevation |
| FOB TBAY | `TBAY+Laker` | Uses that row's components **without** the laker leg or the St. Lawrence transfer. Full export margin and other fees apply: the buyer may be local or international, by laker or saltie. |
| FOB STL Direct | `STL Direct` | All-rail to the St. Lawrence + St. Lawrence elevation |

If any component is blank or "n/a", the FOB is N/A. That covers Barley and Lentils at TBAY/STL, and STL from the Alberta zones.

**Crop → costing commodity:**

| Crop | Costing commodity |
|---|---|
| CWRS, CPSR | Wheat |
| Durum | Durum |
| Canola | Canola |
| Yellow Peas | Peas |
| Red Lentils | Lentils |
| Feed Barley, Malt Barley | Barley (same handling) |
| Soybeans | Soybeans |

> **Lentils costing note:** the CGC elevation tariffs have no explicit lentils item. The **Lentils VC terminal elevation** uses the CGC column **"Other Grains or Scrgs."**, and lentils primary elevation is set at Peas + $8/MT per company. Lentils have no TBAY or St. Lawrence elevation (N/A).

### 4.3 Currency and units
- **USD/MT** = FOB CAD/MT ÷ **FXUSDCAD**: the Bank of Canada daily rate for the PDQ price date, rounded to 4 decimals and cached in `cache\fx_history.parquet`.
- **USD/bu** = USD/MT ÷ bushels per tonne:

  | Crop | Bushels per tonne |
  |---|---|
  | Wheat, durum, peas, lentils, soybeans | 36.74371 |
  | Canola | 44.0925 |
  | Barley | 45.9296 |

- **PDQ cash prices and costs are CAD.** PDQ's own *wheat* basis is **mixed-currency** (CAD cash − USD futures per bushel), so it is used only to back out the futures price, never as an export basis.

### 4.4 Futures and export basis (hedgeable crops only)
- **Futures prices are implied from the same PDQ snapshot** as cash − PDQ basis:
  - **CWRS** vs MIAX/MGEX HRS (`MW`) and **CPSR** vs KC HRW (`KE`), in USD/bu;
  - **Canola** vs ICE Canola (`RS`), in CAD/MT.
  - The contract follows PDQ's mapping per delivery month (e.g. Oct/Nov → `MWZ26`, Dec → `MWH27`).
- **Wheat export basis (USD/bu)** = (FOB USD/MT − futures USD/MT) ÷ 36.74371. This equals FOB USD/bu − futures USD/bu.
- **Canola export basis** = FOB CAD/MT − RS (CAD/MT), also shown ÷ FXUSDCAD in USD/MT.
- **Durum, peas, lentils, barley and soybeans are non-hedgeable:** flat price only, no basis.

---

## 5. Historical charts

`run_historical_drop.py` builds one row per **week (Friday)** × crop × zone × **horizon** (nearby, +1, +2, +3 months) from 2019 to today. It uses each week's last PDQ snapshot, about 52,000 rows.

| Metric | Unit |
|---|---|
| Prairie Cash Bid | CAD/MT or CAD/bu |
| Futures Settle | USD/bu wheat, CAD/MT canola |
| Local Basis (PDQ published) | as PDQ publishes it |
| Export Basis | per route |
| FOB Price | USD/MT or USD/bu, per route |
| Freight & Costing Stack | CAD/MT, per route |

**Pro-forma vs recorded.**
- No costing history exists before tracking started, so **pre-October-2026 FOB, export basis and cost stack are "pro-forma at current costing"**: historical cash and FX with **today's** `CAN_Costing_Input` costs. The chart title carries that label.
- Once you run `Save_Snapshot.bat` (history stored in `data_drop\history\`), those weeks use the **recorded** values. Manual-only crops (e.g. Red Lentils) get history from their recorded snapshots.

**Chart mechanics.**
- Dropdowns feed `FILTER`/`XLOOKUP` formulas in the helper grid (`Historical_Charts!P:Y`), which spill into named ranges (`HC_Weeks`, `HC_S1`…`HC_S9`) bound to a standard line chart.
- Line hierarchy follows the Design folder: focus series black 2.5pt, second green, others muted gray with dash styles.
- *Known limitation:* when comparing by Routes or None, the 6–8 unused series slots still show blank legend entries. Excel can't hide legend entries dynamically without VBA.

---

## 6. Files and architecture

```text
Matrix_FOB_Prices\
├── Matrix_FOB_Calculator.xlsx    the calculator (your interface)
├── Refresh_FOB.bat               daily refresh (PDQ + FX + Hammersmith) + verification
├── Save_Snapshot.bat             record today's matrix in history
├── USER_GUIDE.txt                step-by-step user guide (ASD-STE100)
├── README_FOB_Logic.md           this document
├── src\                          calculator code (below)
├── Hammersmith\                  world FOB + freight module: scraper, parser, rules (section 8)
├── tests\                        pytest suite + Hammersmith fixture posts (tests\fixtures\hammersmith)
├── data_drop\                    CSV drops read by Power Query (regenerated on every refresh)
│   └── history\                  recorded snapshots + Hammersmith history (Parquet) - keep
└── cache\                        BoC FX cache, Hammersmith raw posts, workbook backups (regenerable)
```

Calculator scripts run from the `Matrix_FOB_Prices` folder as `python src\<script>.py`; Hammersmith scripts as `python Hammersmith\<script>.py`.

| File | Role |
|---|---|
| `src\config.py` | Paths, crop rules, zones, routes, constants (one source of truth) |
| `src\layout.py` | Cell addresses and named ranges shared by the two build steps |
| `src\fob_styles.py` | Design tokens from `Python\Design\DesignSkills.md` (fonts, black headers, CHECK green, chart line hierarchy) |
| `src\fob_common.py` | Shared loaders: BoC FX (cached), PDQ from DuckDB (last import per day, implied futures), costing from `Raw` |
| `src\run_pull.py` | Daily drop: `pdq_latest.csv` + `fx.csv`; `--refresh-excel` also refreshes and saves the workbook |
| `src\run_historical_drop.py` | Weekly history drop: `history_chart.csv` |
| `src\save_snapshot.py` | Upserts the saved workbook's matrix and manual entries into the Parquet history (same as-of date → replaced) |
| `src\verify.py` | Independent check: load counts, error scan, every Calc row recomputed in Python (±0.005), history vs DuckDB |
| `src\excel_com.py` | Excel automation (refresh, save; finds your open copy, including OneDrive-URL paths) |
| `src\build_template.py` | **Build step 1:** static template via openpyxl (layout, styling, Config, Calc skeleton, validation, names) |
| `src\wire_workbook.py` | **Build step 2:** through Excel itself: Power Query, all formulas (`Formula2`, dynamic arrays), conditional formats, chart. Backs the workbook up to `cache\backup\` first. |
| `src\wire_world.py` | Called by `wire_workbook.py`: creates and wires the world tabs (World_FOB_Matrix, Global_CIF_Matrix, FOB_Indications, Freight_Rates, World_Config) through Excel COM |
| `Hammersmith\hammersmith_scraper.py` | Fetch (Blogger JSON feed, raw cache), parse, Parquet history upsert, weekly-move flags, CIF freight preference, atomic CSV drops; CLI for backfill / re-parse |
| `Hammersmith\hammersmith_parser.py` | Pure parser: yellow box (prices), pink box (freight), price-window header, quote grammar, month expansion |
| `Hammersmith\hs_config.py` | Every Hammersmith rule: series regexes, Canadian peers, freight proxies, destination aliases, vessel classes, thresholds |
| `Hammersmith\build_fixtures.py` | Downloads the parser test posts into `tests\fixtures\hammersmith\` (one-time) |
| `tests\test_hammersmith_parser.py` | Grammar tests for every quote, header and freight form seen 2019-2026, plus golden regression files (`python -m pytest tests -q`) |
| `Refresh_FOB.bat`, `Save_Snapshot.bat` | Double-click launchers |

**Why two build steps?** openpyxl can't write Excel's dynamic-array formulas (`FILTER`, array logic inside `XLOOKUP`, `LET`/`LAMBDA`); Excel would reopen them as legacy single-value formulas. openpyxl saving also strips Power Query connections. So:
- `build_template.py` writes only static content.
- `wire_workbook.py` has Excel enter the formulas and queries.
- **After wiring, never open and save the workbook with openpyxl.**

**Rebuild and maintenance**
- **Fix or change formulas:** edit `src\wire_workbook.py` and re-run `python src\wire_workbook.py` (workbook closed). It's idempotent: data and Manual_Inputs are kept.
- **Change Config rules:** edit `src\config.py`, then rebuild with `python src\build_template.py --force` and `python src\wire_workbook.py`. ⚠ `--force` recreates the workbook, so **Manual_Inputs entries are lost**; they're preserved in `data_drop\history\manual_indications_history.parquet` if you snapshotted.
- **If the folder moves,** re-run `python src\wire_workbook.py`: the Power Query source paths are absolute.

---

## 7. Checks and troubleshooting

| Symptom | Cause / fix |
|---|---|
| Control shows **STALE** PDQ | Snapshot older than 5 days. Download a new PDQ CSV (Daily use, step 1). |
| A cell shows **N/A** | No quote for that zone/month; route not costed; or crop outside its belt (Durum) |
| Manual entry status **OUT OF WINDOW** | The month is no longer in the 6-month window (window rolled), or a date outside it was typed |
| Excel asks to **enable external data** | Accept: the connections only read local files in this folder and `CAN_Costing_Input.xlsx` |
| `src\verify.py` fails "workbook refreshed from current drop" | Drops are newer than the workbook. Run Refresh All (or `Refresh_FOB.bat`). |
| Costing rows = 0 | `CAN_Costing_Input.xlsx` was saved without calculated values. Open and save it in Excel. |
| Control world block shows **STALE** | No new Hammersmith report for more than 9 days (missed week or holiday). The last report stays in use. |
| World step status **FETCH_FAILED** | Blog feed unreachable. The previous world drop is kept; the PDQ refresh is not affected. Run Refresh_FOB.bat later. |
| World step status **PARSE_FAILED** | The newest post did not parse (layout change). It is saved in `cache\hammersmith\failed\`; the previous report stays in use. Fix the parser, add the post as a fixture, then run `python Hammersmith\hammersmith_scraper.py --reparse`. |
| **Unmapped labels** > 0 | A new or renamed Hammersmith row. Add a regex rule to `WORLD_SERIES` (prices) or `FREIGHT_ORIGINS` (freight) in `Hammersmith\hs_config.py`, then `--reparse`. |
| World step status **WRITE_LOCKED** | A world CSV was open (e.g. in Excel) during the write. Close it and run again. |

Design standard: `Python\Design\DesignSkills.md` (the-desk-style). Agreed rules and decisions: see this file, sections 4–5 and 8.

---

## 8. World FOB & CIF (Hammersmith)

### 8.1 Source and folder
- **Source:** the weekly *Hammersmith Reports* blog (hammersmithltd.blogspot.com, normally Saturday), read through its Blogger JSON feed one post per request. Every price and freight rate is **USD per metric ton, FOB**; there are no cents/bu, CAD or long tons. USD/bu is derived with the calculator's bushel factors (wheat and soybeans 36.74371, barley 45.9296, corn 39.3679).
- **Folder isolation:** all Hammersmith code and rules live in `Hammersmith\`:

```text
Hammersmith\
├── hs_config.py            rules: series regexes, peers, freight proxies, aliases, thresholds, CSV contract
├── hammersmith_parser.py   pure parser (no I/O)
├── hammersmith_scraper.py  fetch + cache + history + drops; CLI (weekly / --backfill-from / --reparse / --offline)
└── build_fixtures.py       downloads parser test posts into tests\fixtures\hammersmith\
```

  `src\run_pull.py` calls the scraper (non-fatal), `src\layout.py` reads its CSV column order, and `src\wire_world.py` writes `World_Config` from `hs_config.py`.

### 8.2 Extraction (ETL)
- **Which posts:** a post is a report only if it has the price table *and* the price-window line. Titles are not trusted ("Fw: Resending…", "Correction…", blank titles, "Sorry no Weekly Report"). The report date comes from the body headline, else the title, else the publish date. **One report per date:** a resend or correction published later replaces the earlier post.
- **Yellow box = cash commodity FOBs** (cell shading `#FFFF99`/`#FFFFCC`, largest yellow table). **Pink box = ocean freight** (`#FED6F5`). If the shading disappears, the tables are found by content and the run notes it.
- **Price window:** `ALL PRICES ARE FOR <near> <</ <far>` (e.g. `November 2026<<January 2027`, `May/July 2019`).
- **Quote grammar:** `a<<b`, `a>>b` and `a -- b` are the near and far month (the arrow is not a direction); a trailing month (`Feb`) or pair (`Sep>>Nov`) overrides the window; `a/b` is a bid/offer (mid used); `spot`; `a flat to Mar`; `No prices`, `No info`, `N/A` or any text without a number means no quote.
- **Freight:** origin, destination, vessel class (only when named), vessel size, discharge rate and notes are split out. Black Sea rows give one row per country (`$95 Russia | Ukraine $56`). **Paper indices (Baltic Dry/Capesize/Panamax/Supramax/Handysize, Freightos/container) are excluded**: physical freight only.
- **Multi-destination strings** (`Algeria/Tunisia`, `Turkey/Egypt`) become one row per destination at the same rate, and the published string stays in `destination_raw`. Origins such as `France/Germany` stay whole. A short alias list fixes spellings (`Saudi` → Saudi Arabia, `East Spain` → Spain (East)).

### 8.3 Delivery months (derivation)

| Derivation | Rule |
|---|---|
| `QUOTED` | Hammersmith published a price for that month |
| `INTERPOLATED` | A month between a forward near and far quote, linear (e.g. Dec = 285 between Nov 283 and Jan 287). Shown grey italic. |
| `WINDOW` | A flat bid/offer or flat price applied to every month of its window (OND / JFM convention) |
| `SPOT_PROXY` | A prompt **spot** quote (French wheat, barley, corn) copied to the window's **near month only**, marked `†`. Deferred months stay **N/A** unless Hammersmith publishes a forward curve. |

Months outside the Hammersmith window are N/A. The Canadian side is unchanged: **no synthetic carry** for PDQ or manual prices (section 4.1).

### 8.4 Outliers: flagged, never excluded
- A value is flagged `OUTLIER` when it moves **more than 15%** from the previous report (same series and delivery month, or same freight route), or falls outside 50–2,000 USD/MT (FOB) or 10–200 USD/MT (freight). `review_note` says why (e.g. `w/w -92.0% vs 2026-10-10 (75.00)`).
- **Flagged values stay in the data, the matrices and CIF.** Jumps during wars or shipping crises (Black Sea, Red Sea, Hormuz) are real, tradable prices. Example: on 17-Oct-2026, US Gulf→China $6 (likely a typo for about $76) is flagged red and kept.

### 8.5 Spreads and CIF (guardrails)
- **FOB spread** (World_FOB_Matrix) = Canadian FOB (Calc, selected zone and month) − competitor FOB, same delivery month, in the selected unit (USD/MT, USD/bu, or CAD/MT at the PDQ-date FX). Green ≤ −band (Canada cheaper), red ≥ +band (Canada premium); band default 5.00.
- **CIF** (Global_CIF_Matrix) = FOB + freight to the destination.
  - Canadian freight proxies: **Vancouver = US PNW** (same Pacific corridor), **Thunder Bay = US Lakes (Duluth)** salties. Hammersmith has **no St. Lawrence route**, so STL Direct CIF is N/A.
  - Competitors use their own origin's freight (Black Sea by country; Black Sea feed grain and soybeans use Russian freight). Delivered series (Rotterdam) have no CIF.
  - **One freight row per origin × destination**: Panamax first, then Supramax, size-only, Handysize, coaster; then the lower rate (`cif_pref = 1`, also shown on Freight_Rates).
- Quality differs (CWRS 13.5 vs Russia 12.5): spreads are price gaps, not like-for-like arbitrage.
- Peers: CWRS and CPSR ↔ world wheat; Feed Barley ↔ world barley; Soybeans ↔ world soybeans. Durum, canola, peas, lentils and malt barley have **no Hammersmith series**.

### 8.6 Storage, refresh and fail-safes
- **Weekly:** `run_pull.py` re-checks posts from the last 21 days (this catches resends) and adds new reports. **Backfill:** `python Hammersmith\hammersmith_scraper.py --backfill-from 2019-01-01` (392 reports, Jan 2019 to today). **After a parser change:** `--reparse` rebuilds the history from `cache\hammersmith\raw\` without the network.
- **History:** `data_drop\history\world_fob_history.parquet` and `world_freight_history.parquet`. **Drops:** the latest report only, written to `*.tmp` and then swapped in (`os.replace`), so Power Query never reads a half-written file. A locked CSV is retried 3 times, then reported as `WRITE_LOCKED`.
- **Never fatal:** feed down → `FETCH_FAILED`; newest post unparseable → `PARSE_FAILED` (post saved to `cache\hammersmith\failed\`). Either way the previous drop stays in use and the PDQ refresh continues. The status is in `fx.csv` (`world_*`) and on the Control tab (World Data block, with its own CHECK row).
- **Stale:** the Control tab and both world tabs show STALE when the report is older than 9 days.
- **Tests:** `python -m pytest tests -q` (grammar tests + 19 fixture posts covering every format era). `src\verify.py` recomputes the visible World_FOB_Matrix spreads and Global_CIF_Matrix landed costs from the drops (±0.005).
