# RPA Calculator — Methodology

Human-readable guide to the rules in `docs/AGENT_RULES.json` (the single source of truth). Each rule has an ID in
brackets; the code cites these IDs, and `tests/test_docs.py` fails if a rule ID used in the rules file or the code
is missing from this document. To change a rule, edit `AGENT_RULES.json` and the matching section here;
`src/config.py` refuses a rules file that is inconsistent.

## 1. Architecture

```
Input_Prices/*.csv ─────────────────────────────┐
                                                 ▼
PDF guides ─► src/sources/  ─► raw tables ─► src/normalize/ ─► CropBaseline ─► src/engine/ ─► src/outputs/
              (extractors)     (as printed)   (crop map, regions,  (province,     (C, D, C−D)   (workbook, JSON)
                                               overrides)          year, crop)
                               src/pricing/ (templates, units, N credit) ─────────┘
docs/AGENT_RULES.json ─► src/config.py (validated registry) ─► every layer
```

| Layer | Modules | Rule |
|---|---|---|
| Sources | `sources/sk_extractor.py`, `sources/ab_extractor.py` | Pure extraction: tables as printed, no mapping, no overrides |
| Normalize | `normalize/crops.py`, `normalize/sk_mvs.py`, `normalize/ab_cop.py`, `normalize/baselines.py` | Crop keys, region rules, source overrides, `CropBaseline` |
| Pricing | `pricing/templates.py`, `pricing/fertilizer.py`, `pricing/units.py` | Price templates, units, N-credit $/lb per province |
| Engine | `engine/market.py` | Pure calculation on `CropBaseline` |
| Outputs | `outputs/excel.py`, `outputs/excel_verify.py` | Workbook generated from the registry; Excel checks |

Adding a province (as done for MB): a `provinces.<code>` block in `AGENT_RULES.json` (region scope, regions,
fertilizer products, crops with source aliases), an extractor in `src/sources/`, a normalizer in `src/normalize/`, and
one entry each in `normalize/baselines.py BUILDERS` and `pipeline.py STAGES`. The engine, pricing and workbook pick
the province up from the registry.

## 2. Conventions [CONV]

| Item | Value |
|---|---|
| Guide years | 2019–2026; Last Yr / baseline year 2026 |
| Pulse pricing | ¢/lb (Yellow Peas, Red Lentils); other crops $/bu |
| Pulse bushel | 60 lb/bu for pulse bu ↔ lb conversions |
| Quote units | `$/t` = 2,204.62 lb, `$/sh.t` = 2,000 lb |

### [YIELD-ROUND] Whole target yields
Target yields are the guide targets rounded to a whole number in their native unit (bu/ac, or lb/ac for pulses),
halves up (`pricing.units.round_yield`). The engine, the JSON outputs and the workbook all use this one rounded
yield, so their numbers match to the cent. (Before P0 the engine used unrounded guide yields; its C and C − D
differed from the workbook by the revenue on the rounding, at most $4.18/ac for SK Soybeans.)

### [DCASH] Direct cash inputs
- **C (Gross Revenue)** = whole target yield × farm price (pulses: lb/ac × ¢/lb ÷ 100).
- **D (Direct Cash Costs)** = seed (incl. treatment) + fertilizer (N + P + K + S) + chemicals (herbicide,
  fungicide, insecticide, pre-harvest / desiccation).
- **RPA** = C − D. Fuel, repairs, insurance, trucking, labour, interest and overhead are excluded.
- **Breakeven price** = D ÷ yield; **breakeven yield** = D ÷ price.

## 3. Crop registry [CROP-MAP]

Canonical `crop_key`s never change; trade labels are what every output displays. Raw guide text is mapped by
`normalize/crops.py`: an exact source alias first (case, spacing, punctuation and `*` ignored), then the first
matching source pattern (regex). Anything else stays unmapped — never guessed.

| crop_key | Trade label | Price basis | lb/bu | SK source aliases | AB source aliases / patterns | MB workbook names |
|---|---|---|---|---|---|---|
| wheat_spring | CWRS Wheat | $/bu | 60 | Spring Wheat, Hard Red Spring Wheat, Hard Red Spring | Spring Wheat · `spring\s+wheat`, `^spring\b` | Wheat - Hard Red Spring |
| wheat_cps | CPS Wheat | $/bu | 60 | CPS Wheat, CNHR Wheat (inherits, see [CROP-INHERIT]) | CPS Wheat · `\bcps\b` | Wheat - Prairie Spring (own budget) |
| durum | Durum Wheat | $/bu | 60 | Durum, Amber Durum, Durum Wheat | Durum Wheat · `durum` | — |
| canola | Canola | $/bu | 50 | Canola | Argentine HT Canola, Argentine Canola · `argentine`, `^canola` | Canola |
| barley_feed | Feed Barley | $/bu | 48 | Feed Barley | Feed Barley · `feed\s+barley` | Barley [MB-BARLEY-FEED] |
| barley_malt | Malt Barley | $/bu | 48 | Malt Barley | Malt Barley · `malt` | — |
| oats | Oats | $/bu | 34 | Oats | Milling Oats · `milling\s+oats`, `^oats` | Oats |
| peas_yellow | Yellow Peas | ¢/lb | 60 | Yellow Peas, Edible Yellow Peas | Field Pea, Yellow Peas · `field\s+pea`, `yellow\s+peas?\b` | Peas [MB-PEAS-BU] |
| lentils_red | Red Lentils | ¢/lb | 60 | Red Lentils, Large Red Lentils | Red Lentil(s) · `red\s+lentil` | — |
| chickpeas_kabuli | Kabuli Chickpeas | ¢/lb | 60 | — | — (dropped 2026-10-01: minor crop) | — |
| flax | Flax | $/bu | 56 | Flax | — | Flaxseed |
| rye_hybrid_fall | Fall Rye | $/bu | 56 | Hybrid Fall Rye | — | Hybrid Fall Rye (2021+), Fall Rye (2019-20) |
| soybeans | Soybeans | $/bu | 60 | Soybean, Soybeans | — | Soybeans |
| corn | Corn | $/bu | 56 | — | — | Corn (grain) |

Not mapped in MB (no registry crop): Wheat - Northern Hard Red (CNHR), Wheat - Winter, Wheat - Special Purpose /
Other Spring (2019-23), Sunflower (oil / confection), Beans (pinto / white / black) and the "Your Farm" column. MB
publishes CPS and CNHR as separate budgets; only Prairie Spring maps to `wheat_cps`.

Kabuli Chickpeas stay in the registry but are no longer budgeted in any province (dropped from the calculator on 2026-10-01 as a minor crop).

### [CROP-INHERIT] Crops with no guide history
`wheat_cps` (SK, CPS / CNHR high-yield class) inherits CWRS wheat's N, P, K, S rates, chemical and seed, and starts
from CWRS yield and price, from its `start_year` (2026). It is never back-filled into the historical database. Its own
bid, target yield and seed cost are entered on the Control Panel (section B2) or in `crop_price_template.csv`
(`Price`, `Yield`, `Yield_Unit`); in the workbook its NPKS cells are live links to the CWRS column.

## 4. Provinces and regions

### [PROV-SK] Saskatchewan — Crop Planning Guide
Regions: Brown, Dark Brown, Black (all active). Soil zones follow [REGION-AVG-RULE] below, exactly as in Alberta:
every printed numeric field is averaged (equal weights) across the crop's zones, then C, D and C − D are derived.
Every SK guide 2019–2026 publishes each general crop in all three zones and durum in Brown and Dark Brown only.

Agronomy rules applied by `normalize/sk_mvs.py`:
- **[SK-S-IMPLIED-ZERO]** Where the guide states no S rate and budgets $0 of S, record 0 lb S/ac.
- **[SK-K-ZERO]** The SK guides budget no potash: K = 0 lb K2O/ac and $0/ac.
- **[SK-SEED-DERIVED]** Where only plant stand, TKW and emergence are given, seeding lb/ac =
  plants/ft² × 43,560 × TKW (g/1,000) ÷ 453.592 ÷ emergence.

### [PROV-AB] Alberta — AgriProfit$ Cropping Alternatives
Regions in the guide: Black, Brown, Dark Brown, Grey-Wooded, Peace Region, Irrigated. All six are parsed and kept
in `output/ab/mvs/`, but only the active regions enter a baseline.

### [PROV-MB] Manitoba — Crop Production Cost Guidelines (COP workbooks)
Source: Manitoba Agriculture's official COP workbooks 2019-2026 (`RPA_MB_Input/cop-crop-production-<year>.xls[m]`,
provided by Darren Bond). The guide is **province-wide** (`region_scope: province_wide`, one region "Province-Wide");
its crop insurance line uses MASC risk areas, but no other budget line is regionalized. `src/sources/mb_extractor.py`
reads raw cells with openpyxl; 2019-2021 are legacy `.xls` files with Excel's default (no-password) protection,
converted once by Excel (read-only, macros disabled) to `output/mb/intermediate/xlsx/`. Every row is found by its
label and every crop by its name, never by a fixed address:

| Data | Sheet | Location |
|---|---|---|
| Yield, unit, price, premium | Summary | crops across row 2; rows "Target Yield per acre", "Unit type", "Target Price $ per unit", "Premium $ per unit" (rows 37 / 38 / 35 / 36 in 2019-20, 36 / 37 / 34 / 35 from 2021) |
| Seed $/ac | Summary row 4 = Seed, Fertilizer & Chemical col J | seed block rows 4-20 (rate D, unit E, $/unit G) |
| N / P2O5 / K2O / S lb and $/ac | Seed, Fertilizer & Chemical | per-crop table from row 38: C/D, F/G, I/J, L/M, total O (= Summary row 5) |
| Fertilizer quotes, N source shares | Seed, Fertilizer & Chemical | rows 27-33: $/tonne D, workbook $/lb G, N usage J |
| Chemical $/ac | Summary row 6 (= detail total J) | "Herbicide / Fungicide / Insecticide" rows 6-8 (2019-20), "Pesticide" (2021-22), "Crop Protection" (2023+); detail block "Chemicals" / "Crop Protection" at row 90-91 |

Structural changes across 2019-2026: Summary columns were ordered by revenue rank (2019-23, plus a "Your Farm"
column) and are fixed in two panels C:J / M:T from 2024; Beans - Black was added in 2024 and Special Purpose / Other
Spring wheat dropped after 2023; "Fall Rye" became "Hybrid Fall Rye" (2021) and "Sunflower Oil" became
"Sunflower - Oilseed" (2024); the N source split moved from 50 / 50 urea / NH3 (2019-21) to 75 / 25. The extractor
writes the per-year layout to `output/mb/intermediate/mb_layout_report.md` (`--inspect` prints it).

Agronomy rules applied by `normalize/mb_cop.py`:
- **[MB-BARLEY-FEED]** MB publishes one barley budget, priced as feed barley; it maps to Feed Barley (no malt budget).
- **[MB-PEAS-BU]** Peas are budgeted in bu/ac and $/bu: lb/ac = bu × 60, ¢/lb = $/bu ÷ 60 × 100.
- **[MB-SEED-UNITS]** Seed rates in bu use the crop's bushel weight; units (soybean, hybrid rye) and plants (corn) are
  kept as printed with no lb/ac equivalent.

All 80 MB crop-years (10 crops × 8 years) reconcile: Summary seed, fertilizer and chemical equal the detail sheet,
sales = yield × price, and fertilizer $/ac equals N / P / K / S lb × the guide's quotes under [NCREDIT-MB] (or its
capped variant for legumes, below).

### [REGION-ACTIVE] Active regions (soil-zone provinces)
Only **Brown, Dark Brown and Black** soils enter any summary. Grey-Wooded, Peace Region and Irrigated are excluded.

### [REGION-AVG-RULE] One soil-zone averaging rule (both provinces)
Set once in `AGENT_RULES.json` `region_averaging`; crops carry no region lists of their own (`src/config.py` rejects
one). A crop's baseline is the simple (equal-weight) average of its budgets across:

| Crops | Zones averaged |
|---|---|
| General: CWRS Wheat, CPS Wheat, Canola, Yellow Peas, Feed / Malt Barley, Oats, Flax, Soybeans, Fall Rye | every active zone where the guide publishes the crop (Brown, Dark Brown, Black) |
| Southern: Durum Wheat, Red Lentils | Brown + Dark Brown only, even where a Black budget is published |

A province-wide guide (MB) has one region, so each crop's baseline is its single budget and the southern restriction
does not apply (MB publishes no durum or lentil budget).

"Where published" means a zone with no budget for the crop that year is left out of the average rather than filled
(the record notes it). CPS Wheat in SK inherits CWRS
([CROP-INHERIT]), so it carries CWRS's three-zone average.

Adopted 2026-09-30. Before it, SK used single zones (Canola, Yellow Peas, Feed / Malt Barley, Flax: Dark Brown;
Durum, Red Lentils: Brown; Oats, Soybeans: Black; Fall Rye: Brown + Dark Brown). The change moved SK 2026 Last Yr
C − D per acre by: Oats −106.57, Soybeans −52.69, Feed Barley −9.29, Yellow Peas −7.75, Malt Barley −5.89,
Canola +4.42, Flax +12.26, Fall Rye +31.14, Red Lentils +32.95, Durum +45.06, and CWRS / CPS −0.005 (through the
Last Yr quotes below). Alberta was unchanged. The superseded values are kept in `tests/golden/rpa_2026.json`.

## 5. Fertilizer: active nutrient pricing

### [NCREDIT] One N-credit rule for every province
Retail product quotes → $ per lb of active nutrient (`pricing/fertilizer.py`):

- $/lb N = quote(urea) ÷ lb per unit ÷ 0.46
- $/lb P2O5, K2O, S = (quote ÷ lb per unit − N content × $/lb N) ÷ nutrient grade

The N carried by MAP and ammonium sulphate is credited at the province's own $/lb N. Each province has its own
product list and quotes, so regional freight and basis produce distinct $/lb vectors while the math stays identical.
Quotes may be `$/t` or `$/sh.t`.

| Province | Rule | Urea | MAP (N credit) | Potash | AS (N credit) | Quote unit |
|---|---|---|---|---|---|---|
| SK | [NCREDIT-SK] | 46-0-0 | 11-52-0 (0.11) | 0-0-60 | 21-0-0-24S (0.21) | $/t |
| AB | [NCREDIT-AB] | 46-0-0 | 11-52-0 (0.11) | 0-0-60 | 20.5-0-0-24 (0.205) | $/t |
| MB | [NCREDIT-MB] | 46-0-0, blended with NH3 82-0-0 | 11-52-0 (0.11) | 0-0-60 | 20.5-0-0-24 (0.205) | $/t |

### [NCREDIT-MB] Manitoba: blended applied N
The MB workbooks price applied N as a blend of urea and anhydrous ammonia at each guide year's usage shares
(NH3 50% in 2019-21, 25% from 2022), while the N carried by MAP and AS is credited at the **urea** $/lb:

- $/lb N applied = (1 − share) × urea $/lb + share × NH3 quote ÷ lb per unit ÷ 0.82
- $/lb P2O5, K2O, S = the [NCREDIT] formula with $/lb N = urea

This reproduces the workbooks' printed $/lb (to 0.0002; they convert at 0.4536 kg/lb) and every crop's fertilizer
$/ac to the cent. The NH3 share is the guide year's (`n_shares`) and can be changed on the Control Panel or in a
scenario (`n_shares`). One divergence is kept on purpose: from 2022 the workbooks credit MAP / AS N only up to the
crop's N rate, so legumes (soybeans, peas: 0 lb N) pay full MAP. The engine keeps the single [NCREDIT] credit for every
province, so MB 2026 Soybeans and Peas carry $6.93 and $5.19/ac less fertilizer than printed; each record notes it.

### [LASTYR-QUOTES] Last Yr quotes
- **SK (`implied_from_guide`)**: the quotes (to the cent) that reproduce the 2026 guide's mean implied $/lb
  (fertilizer $/ac ÷ lb/ac across crops): $830.15 urea, $1,300.33 MAP, $595.25 potash, $669.23 AS → $0.819 N,
  $0.961 P2O5, $0.450 K2O (default — the SK guide budgets no potash), $0.549 S.
- **MB (`guide_published`)**: the workbook's $/tonne quotes for urea, NH3, MAP, potash and AS, at the year's N shares.
- **AB (`guide_published`)**: the guide's own published quotes ($832.50, $1,256.23, $685.95, $624.16), which
  reproduce its printed $0.82 / $0.92 / $0.52 / $0.48 per lb.

Dated retail quotes go in `Input_Prices/fertilizer_quotes.csv` (Date, Province, Nutrient, Product, Price, Unit);
`Input_price_template.csv` documents the benchmarks and must agree with the rules (tested).

## 6. Source overrides

### [OVR-AB-LENTIL-SEED] Alberta red lentil seed
Every AB budget table prices red lentil seed with the green-lentil Table 1 cost (90 lb/ac). The normalizer uses
Table 1's red-lentil rate and cost instead (2026: 60 lb/ac, $29.40/ac vs $70.20 printed). The printed value is kept
as `seed_printed`, and `seed_source` records the override ID.

## 7. Outputs

### Workbook layout and theme
Seven tabs, in order: **RPA_Prairie**, **Control_Panel**, **RPA_Summary_SK / _AB / _MB**, **Agronomic_Rates**, **Historical_Ratios**.
Styling is in `src/outputs/theme.py` (one file for every font, colour, border and number format), following the
CanadaSD_Engine `_Yr` tabs: Calibri 10, a solid black header row with bold white text, unfilled bordered body, bold
key rows, a medium rule closing each block, green rank cells and red negatives. Units sit in a Unit column or in the
header and never inside a value's number format (pulse crops are marked † with lb/ac and ¢/lb).

| Tab | Layout |
|---|---|
| RPA_Prairie | Acreage weights per province and coverage; Prairie Yield, Price, Gross Revenue, Seed, Fertilizer, Chemical, Direct Cash Costs, Return, Rank, breakevens; then the SK / AB / MB returns and their acre-weighted mean, side by side ([PRAIRIE-WEIGHTED-RPA]). |
| Control_Panel | A. Fertilizer: one table for every province (Province, Product, Analysis, Nutrient, content, N credit, quote unit, Last Yr / Current quote, Last Yr / Current $/lb, change); MB adds an NH3 row and an "Applied N" blend row whose NH3 share is editable. Conversion constants below. B. Grain bids and class inputs: one table for every province (Last Yr / Current bid; class yield and seed inputs for crops with no guide history). Yellow = input. |
| RPA_Summary_<code> | Metric \| Unit \| crops: Soil Zone Target, Target Yield, Farm Price, Gross Revenue; Seed, Fertilizer, Chemical, Direct Cash Costs; Return Over Cash Inputs, Last Yr RPA, Delta; Rank; Breakeven Price and Yield. |
| Agronomic_Rates | One table, rows grouped by province: Soil Zone Target, Target Yield + unit, Seeding Rate + unit, N / P2O5 / K2O / S lb/ac. |
| Historical_Ratios | One ratio table (below). |

### [PRAIRIE-WEIGHTED-RPA] Prairie acreage-weighted benchmark
Tier 2 aggregates the three provincial benchmarks into one Prairie benchmark per crop
(`src/engine/prairie_engine.py`), weighted by where each crop is actually grown.

**Weights** (`src/pricing/statcan_weights.py`, written to `AGENT_RULES.json` `prairie_acreage_weights`): StatCan
Table 32-10-0359, *Seeded area (acres)*, for MB, SK and AB over the latest three reference years (2024–2026), read
from the local CANSIM file named in `statcan_acreage.path` (read-only). Per crop and province the mean of the
window's published values is taken (suppressed F / x cells are skipped, not zeroed), and

- acreage weight_p = mean acres_p ÷ (mean acres_MB + mean acres_SK + mean acres_AB)

StatCan classes per crop_key: CWRS → `wheat_spring`, CPSR + CPSW → `wheat_cps`, durum → `durum`, canola, barley →
both `barley_feed` and `barley_malt` (StatCan does not split them), oats, dry peas → `peas_yellow`, lentils (all) →
`lentils_red`, chick peas, soybeans, grain corn, flaxseed, fall rye.

**Aggregation**, over the provinces P whose guide budgets the crop:

- coverage = Σ_P acreage weight_p (share of Prairie acres represented; e.g. Corn 89%, MB only)
- w_p = acreage weight_p ÷ coverage (rescaled to sum to 1)
- Prairie Yield = Σ w_p × whole target yield_p; Prairie Price = Σ w_p × farm price_p (native units)
- Prairie C = Prairie Yield × Prairie Price (pulses ÷ 100)
- Prairie Seed, Fertilizer, Chemical = Σ w_p × provincial $/ac, where each province's fertilizer is its own
  N-credited NPKS cost ([NCREDIT], [NCREDIT-MB])
- Prairie D = Seed + Fertilizer + Chemical; Prairie RPA = C − D; breakevens D ÷ Yield and D ÷ Price

The Prairie RPA is not the weighted mean of provincial returns, since C multiplies the weighted yield by the weighted
price; RPA_Prairie shows both. The same aggregation runs on Last Yr economics or on any scenario's provincial results,
and the workbook tab is live (its formulas weight the RPA_Summary cells). `output/analytics/rpa_analytics_<year>.json`
carries the MB, SK, AB objects and `prairie_weighted_benchmark` (`src/outputs/analytics_json.py`).

### [RPA-DIAGNOSTICS] Historical RPA diagnostics (planting support)
A self-contained dashboard in `output/RPA_Dashboard/` (see its README): a Streamlit app (`streamlit run
app/dashboard.py`) and an export (`python scripts/export_diagnostics.py`, writing
`output/RPA_Dashboard/output/RPA_Diagnostics.xlsx`). It reads this calculator's guide budgets, engine, fertilizer
pricing and theme (loaded as `rpa_core`) and never writes to the pipeline or the master calculator. Historical only:
no forecast, no regression, no score. The earlier historical matrix is archived in `archive/v1_matrix/` there.

**Groups.** Crops are compared only with the crops they compete with for a field:
- G1 Anchor rotation: canola vs spring wheat.
- G2 Semi-arid dryland: lentils and peas vs durum, SK and AB.
- G3 Cereal swaps: barley and oats vs CWRS.
- G4 Row crops: soybeans, corn and MB peas vs canola.
- G5 Minor crops: flax and fall rye vs CWRS.

Every province × crop has exactly one home group (`config/groups.json`), so owned acres reconcile to the StatCan series
with nothing counted twice; a yardstick (CWRS, canola) keeps its acres in G1. Spring wheat acres are StatCan "Wheat,
spring" (every spring class, durum excluded), priced on CWRS economics. Malt barley and CPS wheat are treated as quality
tiers of the barley and wheat acre, not separate choices.

**Measures** (per province, or Prairie for G1–G3, planting year and price window):

| Measure | Definition |
|---|---|
| Spread, spread change | challenger RPA − yardstick RPA ($/ac), and its change on the prior year |
| Share, share shift | challenger acres ÷ (challenger + yardstick acres), and its change in percentage points |
| Outcome | flat under $10/ac or 0.5 pts; tagged in a `config/context_tags.json` year (left out of counts both ways); else hit when the spread change and share shift point the same way, miss otherwise |
| Decomposition | Δ acres = prior share × Δ group acres (group size) + group acres × Δ share (within-group swap) |

Windows differ by price alone: R1–R3 keep the guide's yield and costs and swap in the PDQ 30-day cash average from
`Canada_Planting_Model/v2_relative_index_model/output/v2_panel.csv` (CWRS, durum, canola and peas only, so in G3–G5 a
window moves the yardstick). Prairie spreads weight provincial spreads by prior-year contest acres. Seeded acres come
from the newest `32100359.csv` under `Workspace_CanadaSD/Data_Inputs`; the Prairie weights in
`src/pricing/statcan_weights.py` use the same discovery. Historical what-ifs are budget arithmetic only: the price move
that would have closed a spread, and a urea / MAP shock priced with [NCREDIT].

### [HIST-RATIOS] Historical_Ratios
Rows are numerator ÷ base crop return ratios, grouped by base crop and repeated for every province that budgets
both crops (`outputs.historical_ratios`):

| Base | Ratios | Provinces |
|---|---|---|
| CWRS Wheat | Canola / CWRS Wheat | MB, SK, AB |
| Durum Wheat | CWRS Wheat / Durum Wheat, Red Lentils / Durum Wheat | SK, AB |
| Red Lentils | Yellow Peas / Red Lentils | SK, AB |
| Canola | Soybeans / Canola | MB, SK |

Each guide year's return = its bid × whole target yield − its own seed, fertilizer and chemical lines (SK: the
guide's line items; AB and MB: seed after overrides, printed fertilizer, chemical). The baseline year is locked to the
Last Yr RPA; the Forecast column divides the live RPA_Summary returns. A ratio is n/m when either return is ≤ 0,
and "—" where a crop was not budgeted that year.

### AB reference tables (inspection only)
`python -m src.outputs.export_ab_reference_tables [--years ...]` writes `output/AB_AgriProfit_Reference_Tables.xlsx`:
AgriProfit$ Table 1 (lbs/ac, bu/ac, $/ac) and Table 2 (PreSeed, In Crop, F/I/PH/D) for all six soil zones, one
block per guide year, each value placed by its printed position. Beside each table: the budget-table seed or chemical
$/ac per zone and Δ = budget − Table (flagged beyond ±$0.015 seed, ±$0.03 chemical — the normalizer's tolerances).
Tables are read from the text layer when it parses cleanly, else by a 3-variant OCR vote (disagreeing cells shaded).
Nothing in this workbook feeds the engine; the Δ columns are where guide inconsistencies such as
[OVR-AB-LENTIL-SEED] show up.

## 8. Verification

| Command | Checks |
|---|---|
| `python -m unittest discover -s tests -t .` | Rules validation, pricing, crop translation, baselines, golden financial outputs, workbook structure (no Excel needed) |
| `python -m src.outputs.excel --verify` | Excel recalculation: workbook == engine on identical inputs, == golden snapshot, independence, class inputs, history |
| `python -m src.pipeline [--verify]` | Full rebuild: extract → normalize → StatCan weights → workbook → engine samples → Prairie benchmark + analytics JSON |

`tests/golden/rpa_2026.json` holds the recalculated workbook's C, D and C − D for every SK and AB crop, at Last Yr
and at sample inputs. It was captured before the P0 refactor (which changed nothing) and re-baselined for SK under
[REGION-AVG-RULE]; each change is logged in its `revisions` with the superseded values.
