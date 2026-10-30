# StatCan Field Crop Release Tables → DuckDB / Parquet

A modular pipeline that turns the Statistics Canada release tables pasted into
`StatCan Acres & Prodn.xlsx` into one flat, queryable dataset. Each tab is one release.

**Phase 1 (live):** seeded-area (planting) releases, March and June, 2015–2026 → `plantings_estimates`.
**Phase 2 (ready, disabled):** production releases (July / September / December) → `production_estimates`.

> This side project lives in `Canada_Planting_Model/StatCan_Planting&Prodn/` and is self-contained:
> run every command below from this folder. The rest of `Canada_Planting_Model/` (`src/`,
> `v2_relative_index_model/`, `archive/`) is unrelated and untouched by it.

---

## Setup

```bash
pip install pandas openpyxl duckdb
```

The source workbook is read-only:
- **Default path:** `C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\GT\Useful\StatCan Acres & Prodn.xlsx`.
- **Override:** set the `STATCAN_WORKBOOK` environment variable, or pass `--workbook`.
- **Excel lock:** the workbook can stay open in Excel. If Excel holds an exclusive lock, the extractor reads a temporary copy.

## Running

```bash
python main.py run                        # Phase 1: extract -> transform -> DuckDB + Parquet
python main.py run --dry-run              # validate everything, write nothing
python main.py sheets                     # which tabs each topic selects or skips, and why
python main.py march-vs-june --crop Canola
python main.py revisions --crop Canola --year 2021
python main.py snapshot --release Jun2026
python planting_report.py               # March vs June report workbook (see below)
python -m pytest tests -q                 # parsing and load tests
```

Outputs:
- `data/crop_data.duckdb`, table `plantings_estimates`.
- `data/parquet/plantings_estimates/release_year=YYYY/*.parquet`, Hive-partitioned by release year and rebuilt on every run.
- `reports/Planting_Report_<year>.xlsx`, from `planting_report.py`.

---

## Architecture

```text
config.py       paths, tab-name patterns, cleaning regexes, unit map, TOPICS (which tabs, which table)
extractor.py    open workbook -> select a topic's tabs (marker block + name + month/year filters) -> raw rows
transformer.py  release name -> Release; locate header; clean headers / crop names; wide -> long; validate
loader.py       DuckDB upsert (INSERT OR REPLACE on the primary key) + partitioned Parquet export
analytics.py    read-only query helpers (March vs June, revision paths, YoY, snapshots)
main.py         CLI
planting_report.py  March vs June report workbook (reads the database; run after main.py run)
desk_style.py   The Desk house style for openpyxl (Design/DesignSkills.md)
tests/          test_statcan_pipeline.py (+ conftest.py putting this folder on sys.path)
```

Flow: `extract(topics)` → `RawSheet`s → `transform()` → one long DataFrame → `upsert()` per topic → `export_parquet()`.

### How tabs are selected (`extractor.select_sheets`)

Each topic is a `config.TopicSpec`. A tab is selected when it meets all of these:

1. It sits **strictly between the topic's marker tabs**. Planting tabs sit between `<--Production` and `<--Seeded`. Production tabs sit before `<--Production`.
2. Its name parses as a release, in either order: `Mar2015`, `March2019`, `June2026` (month first) or `2022July` (year first). Names such as `Main` and `Sheet1` are skipped.
3. Its release month and year pass the topic filters. For planting: March and June, 2015–2026.

Two tabs resolving to the same release (e.g. `Mar2021` and `March2021`) is an error, so nothing is double-loaded. `python main.py sheets` prints every decision with its reason.

### How a tab is parsed (`transformer.transform_sheet`)

A release tab is a pasted StatCan table:

```text
[title row — present from Jun2015 on]
header : ''      | 2024 | 2025 | 2026{1} {p} | 2024 to 2025 | 2025 to 2026
units  :         | thousands of acres  (x3)  | % change (x2)
rows   : Canola  | 22011 | 21623 | 23442 | -1.8 | 8.4
footnotes, notes, "Source(s): Table 32-10-0359-01."
```

1. **Header row:** the first row (among the first 10) with at least two single-year cells. That's row 1 in `Mar2015` and row 2 in later tabs.
2. **Year headers:** the first 4-digit year is extracted after normalizing non-breaking spaces.
   - `2021{1}{p}` → 2021, `March 20151p` → 2015.
   - `{p}`, or a `p` glued to the year, marks the value `is_preliminary`.
3. **Columns dropped:**
   - comparison columns: any header with `%`, `to` or `change`, or with two years (`2014 to March 2015`);
   - columns with no header, such as the stray copied labels in some production tabs.
4. **Units:** read from the units row, with footnotes stripped, and mapped by `config.UNIT_MAP` to the topic's value column and a multiplier. For example, `thousands of hectares` → acres ×2.47105, and raw `metric tonnes` → thousand tonnes ×0.001. A unit that doesn't match the topic is an error.
5. **Crop names** (`normalize_crop_name`):
   - NFKC normalization; remove `{n}` footnotes and trailing footnote digits (`Winter wheat3`, `…remaining)2`);
   - collapse whitespace;
   - sentence case (`Fall Rye` → `Fall rye`);
   - apply optional `config.CROP_ALIASES`.
6. **Rows:** a row with a crop label and at least one numeric value becomes one record per year column.
   - Notes, footnotes and blank rows have no numbers, so they're dropped.
   - Non-numeric cells (`x`, `..`) are skipped with a warning.
7. **Validation:**
   - no duplicate crop × year within a tab;
   - target years within five years before the release year;
   - at least one value column per tab.

The two StatCan total-wheat definitions stay separate crops: *including winter wheat remaining* (June releases and most March releases) and *including winter wheat seeded in the fall*.

---

## Schema — `plantings_estimates`

Primary key: **(release_code, crop_name, target_crop_year)**. A re-run replaces rows rather than duplicating them.

| Column | Type | Example | Notes |
|---|---|---|---|
| `release_code` | VARCHAR | `Mar2021` | Canonical `MonYYYY`, even when the tab is `March2021` |
| `release_date` | DATE | `2021-03-01` | First day of the release month |
| `release_month` | VARCHAR | `March` | |
| `release_year` | INTEGER | `2021` | Year the report was released (Parquet partition key) |
| `crop_name` | VARCHAR | `Canola` | Standardized |
| `target_crop_year` | INTEGER | `2021` | Crop year the estimate refers to |
| `acres_thousand` | DOUBLE | `21530` | Thousands of acres |
| `metric_type` | VARCHAR | `Planting` | |
| `is_preliminary` | BOOLEAN | `true` | The release's own-year column (`{p}`) |
| `unit` | VARCHAR | `thousands of acres` | Unit as printed |
| `source_sheet` | VARCHAR | `March2021` | Tab name |
| `source_label` | VARCHAR | `Winter wheat{3}` | Crop label as printed |
| `source_header` | VARCHAR | `2021{1} {p}` | Column header as printed |
| `ingested_at` | TIMESTAMP | | Load time |

Each release reports three crop years: the current year (preliminary) and the two before it (revised). One crop-year value therefore appears in up to six releases (March and June of T, T+1, T+2). The full revision path is kept.

**Phase 1 load (2015–2026):** 24 releases and 1,365 rows. 19 crops per release, 18 in `Mar2015`, which has no Summerfallow line.

Crops: Barley, Canary seed, Canola, Chick peas, Corn for grain, Dry beans, Dry field peas, Durum wheat, Fall rye, Flaxseed, Lentils, Mustard seed, Oats, Soybeans, Spring wheat, Summerfallow, Sunflower seed, Total wheat (including winter wheat remaining), Total wheat (including winter wheat seeded in the fall), and Winter wheat.

## Analytics (`analytics.py`)

| Function | Returns |
|---|---|
| `march_vs_june(crop)` | Per crop year: March estimate, June estimate, the June − March revision and %, and the latest published estimate and its release |
| `revision_path(crop, year)` | Every estimate of one crop-year, oldest release first, with change vs the first estimate |
| `yoy_by_release(crop, release_month=None)` | Each release's preliminary estimate vs the prior year in the same release |
| `release_snapshot(code=None)` | All crops × years for one release (default: latest) |
| `crops()`, `releases()` | Lookups |

Example: `march_vs_june("Canola")` shows June revised March upward in 10 of 12 years (2015–2026). The largest revision was +1,603k acres in 2026.

---

## Planting report (`planting_report.py`)

A March vs June workbook built for fast desk reading: absolute million-acre shifts, % revision and a one-word
signal tag, with no statistical overhead. It writes `reports/Planting_Report_<year>.xlsx` in The Desk style
(`Desktop/Python/Design/DesignSkills.md`) and prints the narrative and the season table.

```bash
python planting_report.py                          # latest season, focus crop Canola
python planting_report.py --year 2024 --crop Lentils
python planting_report.py --crop "Wheat ex-Durum"  # subtotals work as focus crops too
python planting_report.py --out reports/custom_name.xlsx
```

Primary tables use one layout: `Crop`, `Group`, `March (Mln Ac)`, `June (Mln Ac)`, `Net Revision (Mln Ac)`,
`Revision (%)`, `Signal Tag`.

**Signal Tag** values:
- **Gained / Cut:** a revision of at least ±2%.
- **Steady:** a smaller revision.
- **Flipped ▲ / ▼:** June reversed the year-on-year direction March implied. This takes precedence over Gained / Cut.

| Tab | Content |
|---|---|
| Summary | Three narrative bullets (qualitative, per `Design/Language_Skills`), every crop ranked by net revision, a bold **Wheat ex-Durum (Spring + Winter)** row, the principal-crop CHECK row, and a chart |
| *Crop* (default Canola) | Season-by-season March vs June, with a line chart and a net-revision chart |
| Rotation | Per season: spring conditions, March / June area, acres added / removed, net, **Turnover Ratio**, biggest gainer / loser, **Dominance Ratio**, **Shuffle Type**, and two charts |
| Revision History | Each crop's average net revision, up share, largest gain / cut, and tendency; the crop × season matrix in Mln Ac |
| Ref - Accuracy | Reference (gray tab): March vs June error against the latest revised estimate |
| Ref - Signal Flips | Reference (gray tab): every Flipped crop-year with the prior-year area |
| Notes | Definitions |

**Rotation metrics:**
- **Turnover Ratio** = (added in June + |removed|) ÷ March area. It measures how violently crops were swapped.
- **Dominance Ratio** = biggest gainer ÷ |biggest loser|. The Shuffle Type follows from it:
  - between 0.67 and 1.5x: **Binary swap**, a one-for-one trade with the main loser;
  - above 1.5x: **Multi-donor**, the winner was funded by several crops;
  - below 0.67x: **Multi-recipient**, the loser's acres were scattered.
- **Spring Conditions** come from `spring_conditions.csv` (`year, conditions, detail, status`). The shipped
  entries for 2015–2024 are drafts marked `Draft - verify`; 2025–2026 are `Not set`. Edit the CSV
  (e.g. `Wet / delayed seeding`, `Dry / drought stress`, `Normal`) and re-run.

**Other rules:**
- **Wheat ex-Durum** = spring + winter wheat. Winter wheat is the area seeded in the fall in March and the area remaining in June, so its revision includes winterkill. Subtotals are never added into totals.
- **Total wheat:** StatCan's totals are excluded, because the two releases use different winter wheat definitions.
- **Principal crops:** sums exclude summerfallow.

---

## Adding the production tabs (Phase 2)

The production topic is already defined in `config.TOPICS["production"]`:
- **Block:** tabs before `<--Production`.
- **Months:** July, September, November and December.
- **Table:** `production_estimates`.
- **Value column:** `tonnes_thousand`.

The same parser already handles the production tabs. A dry run reads all 18 (Jul2015 … Dec2022) and produces 929 records, scaling `Sep2017`'s raw `metric tonnes{1}` to thousands of tonnes.

To enable it:

1. Finish the tabs. Name them `MonYYYY`, `MonthYYYY` or `YYYYMonth` and keep them left of `<--Production`.
2. Check the selection and the parse:
   ```bash
   python main.py sheets --topics production
   python main.py run --dry-run --topics production
   ```
   Warnings list any non-numeric cells, such as `x`.
3. Load it, either for one run or permanently:
   - one run: `python main.py run --topics planting production`;
   - permanently: set `enabled=True` on the production `TopicSpec`.

   `production_estimates` and `data/parquet/production_estimates/` are created automatically, with the same schema except that `tonnes_thousand` replaces `acres_thousand`.
4. Optionally add production helpers to `analytics.py` (e.g. July vs December estimates). The query pattern is the same as `march_vs_june`.

**Adding another topic or table type** (e.g. yields) takes three steps:
1. Add a `TopicSpec` (markers, month filter, table, value column).
2. Add its unit strings to `config.UNIT_MAP` if they're new.
3. If the tab names follow a new pattern, add a regex to `SHEET_NAME_PATTERNS`.

The extract, transform and load code doesn't change.

## Troubleshooting

| Message | Meaning / fix |
|---|---|
| `start/end marker '…' not found` | A marker tab was renamed or deleted; restore it or update the `TopicSpec` |
| `more than one tab for release(s) …` | Two tabs resolve to the same release (e.g. `Jun2021` and `June2021`); remove one |
| `no header row with year columns found` | The table doesn't start in the first 10 rows, or has fewer than two year columns |
| `unit '…' maps to …, topic … expects …` | A planting tab holds production data (or vice versa), or the units row is new; extend `UNIT_MAP` |
| `duplicate crop/year rows after normalization` | Two labels normalize to the same crop; check `source_label` and add a `CROP_ALIASES` entry if they differ |
