# CGC Grain Market Analytics (`CGC_Analyzer`)

Canadian grain-logistics market intelligence built on the Canadian Grain Commission (CGC)
**Grain Statistics Weekly (GSW)** release and the licensed **CGC elevator capacity** workbook.
Part of the `coolintake/ag-quant-research` portfolio, published under the Chilmeran Research brand.

One shared backend, the `core/` package (`cgc_engine` → `ingestion` → `cgc_reports`), feeds every output:
an interactive dashboard (**CanGrainStats**), a weekly published report (**Canadian Grain
Handling Weekly**), a monthly export S&D workbook (**CGC_MonthlyEx**), and a quick terminal report.

**Commodities tracked:** Wheat (excl. durum), Amber Durum, Barley, Oats, Canola, Peas, Lentils,
Soybeans (the Pulse also covers Corn; `CGC_MonthlyEx` covers every commodity in the GSW export data).
**Coverage:** Primary Elevators (by province), Process Elevators (East/West), and the six GSW
export-terminal regions: Vancouver, Prince Rupert, Churchill, Thunder Bay, Bay & Lakes, St. Lawrence.

---

## Project Structure

```
ag-quant-research/
├── .github/workflows/
│   └── weekly_cgc_update.yml               # Scheduled GSW refresh (Wed/Thu/Fri)
├── .streamlit/config.toml                  # Streamlit server config (read from the repo root)
├── CGC_Analyzer/
│   ├── run_report.py                       # PIPELINE: ingest -> monthly workbook -> weekly report
│   ├── generate_weekly_pulse.py            # REPORT: Canadian Grain Handling Weekly (HTML / Ghost)
│   ├── CGC_MonthlyEx.py                    # REPORT: Monthly export S&D matrix (Excel)
│   │
│   ├── core/                               # Shared data layer (single source of truth)
│   │   ├── cgc_engine.py                   # Constants, GSW source URLs, schema fixes, metric math
│   │   ├── ingestion.py                    # GSW download/cache (canada.ca), capacity loader
│   │   └── cgc_reports.py                  # CGCAnalytics facade (data assembly)
│   │
│   ├── dashboard/                          # CanGrainStats Streamlit app (occasional use)
│   │   ├── app.py                          # Entry point
│   │   ├── cgc_charts.py                   # Plotly figure builders
│   │   ├── tabs/                           # One module per tab + _widgets.py
│   │   └── assets/cangrainstat_logo.png    # Header logo
│   │
│   ├── data/
│   │   ├── CGC_Capacity.xlsb               # Licensed elevator capacity workbook
│   │   └── gsw_data/                       # GSW data store (committed by the workflow)
│   │       ├── gsw-shg-en_<YYYY-YY>.csv    # Raw CGC CSV, one per crop year
│   │       └── _cache/
│   │           ├── combined_gsw.parquet    # Standardized all-years dataset
│   │           └── last_current_year.txt   # Crop-year rollover marker
│   ├── templates/
│   │   └── weekly_pulse_template.html.j2   # Weekly report HTML template
│   ├── Outputs/                            # Generated files (auto-created)
│   │   ├── CGC_MonthlyEx_<date>.xlsx       # Monthly export matrix
│   │   └── weekly_pulse/                   # Report HTML, payload JSON, chart PNG
│   │
│   ├── requirements.txt                    # Python dependencies
│   ├── .env                                # Local secrets for the Pulse (not committed)
│   ├── USER_GUIDE.txt                      # Short how-to
│   └── README.md                           # This file
└── ...                                     # Other ag-quant-research projects
```

**Generated at runtime (in `CGC_Analyzer/Outputs/`):** `weekly_pulse/weekly_pulse_report.html`,
`weekly_pulse/weekly_pulse_payload.json`, `weekly_pulse/export_pacing_chart.png` (Weekly Pulse), and
`CGC_MonthlyEx_<date>.xlsx` (Monthly Export matrix).

### Architecture rules

- `core/` (`cgc_engine`, `ingestion`, `cgc_reports`) is the **single source of truth** for
  data logic. Report scripts and the dashboard consume it (`from core.cgc_reports import ...`);
  they never re-derive exports, stocks, or capacity.
- No `-m` flags. Every entry point finds its own folder via `Path(__file__)` and puts
  `CGC_Analyzer/` on `sys.path`, so `core`, `data/`, and `Outputs/` resolve from any working
  directory. Dashboard-only code (`cgc_charts.py`, `tabs/`) stays inside `dashboard/`.
- Commodity naming follows raw CGC labels: always **"Amber Durum"**, never "Durum".
- **Export definition** (validated in `CGC_DB.ipynb`): Terminal Exports (`metric = Exports`)
  + Primary Shipment Distribution to `Export Destinations`, on the Crop Year cumulative.

---

## Available Reports & Analytics

| Report | Module | Output | Run (from the repo root) |
| :--- | :--- | :--- | :--- |
| **Full pipeline** | `run_report.py` | Fresh data + workbook + report HTML | `python CGC_Analyzer/run_report.py` |
| Canadian Grain Handling Weekly | `generate_weekly_pulse.py` | HTML + JSON + PNG, Ghost draft | `python CGC_Analyzer/generate_weekly_pulse.py` |
| Monthly Export S&D Matrix | `CGC_MonthlyEx.py` | Excel workbook in `Outputs/` | `python CGC_Analyzer/CGC_MonthlyEx.py` |
| CanGrainStats Dashboard | `dashboard/app.py` | Interactive web app (5 tabs) | `streamlit run CGC_Analyzer/dashboard/app.py` |

Every script also runs from inside `CGC_Analyzer/` or with VS Code's Run button.

### 1. CanGrainStats Dashboard — `dashboard/app.py`

**Purpose.** Self-serve, chart-first view of where Canadian grain sits in the handling system
and how fast it is leaving. Used to spot logistics bottlenecks and export-pace deviations from
seasonal norms before they show up in price spreads or basis.

**Output structure.** Five tabs, each rendered by its own module in `dashboard/tabs/`:

| Tab | What it answers |
| :--- | :--- |
| Commercial Pipeline | Stocks vs. licensed capacity, stacked by commodity, per segment (Primary by province, Process East/West, terminal regions) |
| Export Pace | Current crop-year cumulative exports vs. the prior 3-year range, plus a pacing summary table |
| Capacity Bottleneck | Red/Yellow/Green utilization matrix (Red ≥ 85%, Yellow ≥ 75%) across segments and commodities |
| Export Distribution | YTD export pace vs. the 2018-19-to-date seasonal baseline, framed as percentiles |
| Producer Deliveries | Weekly and YTD primary-elevator deliveries by province (SK, AB, MB, BC) with pacing chart |

**Run.** `streamlit run CGC_Analyzer/dashboard/app.py` from the repo root (not `python app.py`),
so the root `.streamlit/config.toml` applies. Needs `CGC_Capacity.xlsb` (or `.xlsx` / `.xls`)
in `CGC_Analyzer/data/`.

### 2. Canadian Grain Handling Weekly — `generate_weekly_pulse.py`

*Formerly "Weekly Pulse".*

**Purpose.** Automated weekly market note for Chilmeran Research readers. It turns each GSW
release into a short, publishable brief covering the system-wide macro snapshot, export pace
vs. the 5-year average, deliveries, and terminal-corridor stocks and utilization. The draft
goes straight to Ghost, so the only manual step is review and publish.

**Output structure.**
- `weekly_pulse_payload.json` holds the objective metrics, rule tags, and the 3-bullet `summary`.
- `export_pacing_chart.png` is a Matplotlib 5-year export pacing chart with the Chilmeran Research
  watermark. It is used only as the Ghost `feature_image`; it is not in the report body.
- `weekly_pulse_report.html` is a standalone document that prints to exactly one A4 portrait page.
  Sections: Summary (3 narrative bullets: deliveries & pace, export drivers, logistics), Macro Snapshot,
  Commodity Flow & YTD, and YTD Exports by Corridor. Tables use a single
  palette (one dark-navy brand colour, light-slate group headers, green/red text for changes), and all volumes are in KMT.
  Ghost receives the same body without the print `<style>` wrapper.
- A Ghost **draft** post is created through the Admin API. The PNG is uploaded as the `feature_image`.

**Rules of note.** Corn and Soybeans are Eastern commodities, so delivery commentary is
suppressed for them (CGC primary-elevator coverage is incomplete in the East). YTD export percentages are footnoted through week 10.

**Summary voice.** The Summary follows the language skill
`Design/Language_Skills/Lingo_CGCWeeklyReport.md` (override with `WEEKLY_PULSE_LANGUAGE_SKILL`):
three "Headline: narrative" bullets of at most 30 words, qualitative only, with no tonnages or
percentages, since the tables already show them. The LLM path sends the skill as its system
prompt; the default rule-based writer maps the metrics onto the skill's word bank. Both outputs
are checked: exactly 3 bullets, word cap, no figures (other than "5-year average"). Commodity
outperformers/laggards are ranked by absolute KMT change in weeks 1–10 and by YoY % afterwards,
and a commodity is only named when its volume clears 100 KMT (`SUMMARY_MIN_BASE_KT`).

**Week-over-week contrast.** `run()` also builds a full week N-1 snapshot (`payload["prior"]`).
Each bullet reads momentum against it, not week N on its own:
- **Deliveries:** whether a decline is flattening, rebounding, or extending, and whether gains are
  building or fading (`DELIVERY_WOW_BAND_PCT`).
- **Exports:** whether total terminal shipments are accelerating or slowing (`EXPORT_WOW_BAND_PCT`),
  plus the commodity whose weekly loadings surged or eased most. A commodity counts when its
  weekly exports move by at least `EXPORT_SURGE_PCT`, with `EXPORT_SURGE_MIN_KT` in either week.
- **Logistics:** whether West Coast and eastern terminal space is relieving or tightening
  (`UTIL_SHIFT_PTS` utilization points).

The LLM path gets the same N-1 data and instructions.

**Run.**
```
python generate_weekly_pulse.py                 # full pipeline, stage draft to Ghost
python generate_weekly_pulse.py --no-publish    # build HTML/JSON/PNG locally only
python generate_weekly_pulse.py --no-llm        # force rule-based commentary
python generate_weekly_pulse.py --week 24       # force a specific grain week (testing)
```
Configured by environment variables or a local `.env` file:
- `GHOST_API_URL` and `GHOST_ADMIN_API_KEY` (in `id:secret` form) are required to publish.
- `WEEKLY_PULSE_USE_LLM=1` together with `ANTHROPIC_API_KEY` switches on LLM commentary. It is off by default.
- `CGC_DATA_DIR`, `CGC_CAPACITY_FILE`, and `WEEKLY_PULSE_LOG_LEVEL` are optional overrides.

The script and `templates/weekly_pulse_template.html.j2` must always be updated as a
matched pair. An error like `No filter named 'pctnum'` means one of them is out of date.

### 3. Monthly Export S&D Matrix — `CGC_MonthlyEx.py`

**Purpose.** Turns CGC's Sunday-ending weekly export data into **calendar-month totals** laid
out like an S&D balance sheet. Statistics Canada's CIMT monthly trade data is published with a
lag of several weeks. This matrix gives a same-week read on the month in progress and the
months CIMT hasn't released yet, in the same monthly frame used by S&D models and USDA/AAFC
balance sheets.

**Method.**
- Weekly exports come from `cgc_engine.weekly_outflow`, the same export definition as the
  dashboard and the Pulse.
- Each week's volume is spread evenly over the days it covers. A regular week is `weekly / 7`.
  If a reporting week is missing, the next week's volume is spread over the full gap.
- The daily amounts are then summed into calendar months.
- The first week of each crop year never starts before 1 Aug, so each Aug–Jul total
  reconciles with CGC's crop-year cumulative.

**Output structure.** `Outputs/CGC_MonthlyEx_<latest week-ending date>.xlsx`, units kt:

| Sheet | Contents |
| :--- | :--- |
| Monthly Exports | One block per commodity: `Commodity \| Metric \| Crop Year \| 12 months \| Total` |
| Monthly Long | Unrounded monthly values, one row per commodity-month, ready for pivots |
| Notes | Methodology, definitions, data-through date |

Marketing-year calendars:

| Commodities | Month order | Total column |
| :--- | :--- | :--- |
| All grains and oilseeds (default) | AUG … JUL | `AUG/JUL` |
| Soybeans | SEP … AUG | `SEP/AUG` |
| Corn | OCT … SEP | `OCT/SEP` |

- Values are rounded to whole kt, and each row total is a live `=SUM` of its months.
- Months that haven't happened yet show 0 in grey. The month in progress is shaded yellow.
- A marketing year that starts before the first available data is left out (for example,
  Soybeans and Corn 20-21, since the data begins in Aug 2021). Use `--keep-truncated` to keep it.

**Run.**
```
python CGC_MonthlyEx.py                   # refresh GSW data, then build the workbook
python CGC_MonthlyEx.py --no-refresh      # offline: use data/gsw_data/_cache as-is
python CGC_MonthlyEx.py --force-refresh   # re-download every crop year first
python CGC_MonthlyEx.py --keep-truncated  # keep partial leading marketing years
```
Does not need the capacity workbook. Close the previous workbook in Excel before re-running
the same week, or the save will fail.

### 4. Pipeline Runner — `run_report.py`

**Purpose.** One command for the whole weekly cycle, and the **data-ingestion entry point**
for the GitHub Actions workflow. GSW is downloaded once and shared by both reports.

**Steps.**
1. **Ingest:** refresh `data/gsw_data/` from canada.ca (current crop year, plus any crop-year
   rollover catch-up). It then prints the Canola executive summary and the regional
   utilization matrix as a quick terminal check.
2. **Monthly:** `CGC_MonthlyEx.run()` writes `Outputs/CGC_MonthlyEx_<date>.xlsx`.
3. **Weekly:** `generate_weekly_pulse.run()` writes the Ghost-ready report to `Outputs/weekly_pulse/`.

**Run.**
```
python CGC_Analyzer/run_report.py                 # full pipeline (no Ghost push)
python CGC_Analyzer/run_report.py --publish       # ... and stage the report as a Ghost draft
python CGC_Analyzer/run_report.py --ingest-only   # data refresh only (what CI runs)
python CGC_Analyzer/run_report.py --no-refresh    # rebuild outputs from cached data
python CGC_Analyzer/run_report.py --week 10       # weekly report for a specific grain week
```
Requires the capacity workbook.

### Supporting: Research Notebook — `CGC_DB.ipynb`

The original exploratory notebook: GSW import and cleaning, the validated export formula,
per-commodity plots, and the stocks analysis. It is not part of any pipeline. Its formulas are
the ground truth that `cgc_engine.OutflowDefinition` reproduces.

---

## Automated Data Refresh — `weekly_cgc_update.yml`

**Schedule.**
- Cron `0 11 * * 3,4,5` runs at 11:00 UTC on **Wednesday, Thursday and Friday**. That is
  7:00 AM Eastern in summer (EDT) and 6:00 AM in winter (EST).
- The three runs cover the day CGC usually posts the weekly release plus two catch-up days.
- `workflow_dispatch` also allows a manual run from the repository's Actions tab.

**What it does.**
1. Checks out the repo with `fetch-depth: 0` (full history).
2. Sets up Python 3.10 and installs `CGC_Analyzer/requirements.txt`.
3. Runs `python CGC_Analyzer/run_report.py --ingest-only` to download and standardize GSW data.
4. Stages **only** `CGC_Analyzer/data/gsw_data/`, and commits only if something changed.
   The commit message is `Auto-update CGC weekly data [skip ci]`.
5. Runs `git pull --rebase origin main`, then `git push`.

**Safety checks.**

| Setting | Why |
| :--- | :--- |
| `permissions: contents: write` | Without it, the default token is read-only and the push fails with a 403 |
| `concurrency` block (queue, don't cancel) | Overlapping runs, such as a manual trigger during a scheduled one, wait in line instead of racing to push |
| `fetch-depth: 0` + `git pull --rebase` | The bot's commit is replayed on top of any newer commits instead of being rejected as out of date |
| `[skip ci]` + commit-only-if-changed | No empty commits and no recursive workflow triggers on weeks CGC hasn't posted |

**Data source.** The CGC moved Grain Statistics Weekly to canada.ca in autumn 2026. The old
`grainscanada.gc.ca/.../gsw-shg-en.csv` URL still answers 200 OK, but it froze at 2026-27
week 9, which is why reports were stuck on week 9. `core/ingestion.py` now works through
three sources in order:
1. The CSV link it finds on the
   [GSW index page](https://www.canada.ca/en/grain-commission/services/statistics/grain-statistics-weekly.html).
   File names differ by year, so it reads the link rather than building the name.
2. The canada.ca URL pattern, if the page can't be read.
3. The legacy URL, as a last resort and with a warning.

It also logs `STALE GSW DATA` when the newest current-year week is more than 12 days old
(`GSW_STALE_AFTER_DAYS`).

**Output directory.** `CGC_Analyzer/data/gsw_data/`: the raw per-year CSVs plus
`_cache/combined_gsw.parquet`. Run `git pull` before running reports locally to pick up the
bot's latest data. Only the data refresh is automated. Run `python CGC_Analyzer/run_report.py`
to build the workbook and report; the dashboard reads the same data on demand.

---

## Setup

```
cd CGC_Analyzer
pip install -r requirements.txt
```

1. Place `CGC_Capacity.xlsb` (or `.xlsx` / `.xls`, sheet `CGC_Capacity`) in `CGC_Analyzer/data/`.
   Paths resolve on their own, so there is nothing to edit.
2. The first run downloads every crop year from 2021-22 onward into `data/gsw_data/`.
   After that, only the current crop year is refreshed.
3. Publishing to Ghost (`--publish`) also needs `PyJWT`, plus `python-dotenv` if you use a
   `.env` file.

**Git hygiene.** Stage from inside `CGC_Analyzer/`, and never run `git add .` from a parent folder.
Consider adding `CGC_Analyzer/Outputs/` and `.env` to `.gitignore` so generated workbooks and
secrets are never committed.
