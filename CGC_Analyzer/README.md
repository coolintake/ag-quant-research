# CGC Grain Market Analytics (`CGC_Analyzer`)

Canadian grain-logistics market intelligence built on the Canadian Grain Commission (CGC)
**Grain Statistics Weekly (GSW)** release and the licensed **CGC elevator capacity** workbook.
Part of the `coolintake/ag-quant-research` portfolio, published under the Chilmeran Research brand.

One shared backend (`cgc_engine.py` → `ingestion.py` → `cgc_reports.py`) feeds every output:
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
├── .github/
│   └── workflows/
│       └── weekly_cgc_update.yml           # Scheduled GSW refresh (Wed/Thu/Fri)
├── CGC_Analyzer/
│   ├── gsw_data/                           # GSW data store (committed by the workflow)
│   │   ├── gsw-shg-en_<YYYY-YY>.csv        # Raw CGC CSV, one per crop year
│   │   └── _cache/
│   │       ├── combined_gsw.parquet        # Standardized all-years dataset
│   │       └── last_current_year.txt       # Crop-year rollover marker
│   ├── tabs/                               # Dashboard tabs, one file per tab
│   │   ├── _widgets.py                     # Shared UI helpers
│   │   ├── tab_segment_capacity.py         # Commercial Pipeline
│   │   ├── tab_outflow_pacing.py           # Export Pace
│   │   ├── tab_bottleneck_matrix.py        # Capacity Bottleneck
│   │   ├── tab_seasonal_pacing.py          # Export Distribution
│   │   └── tab_producer_deliveries.py      # Producer Deliveries
│   ├── templates/
│   │   └── weekly_pulse_template.html.j2   # Weekly report HTML template
│   ├── Outputs/                            # CGC_MonthlyEx workbooks (auto-created)
│   │
│   ├── cgc_engine.py                       # Constants, schema fixes, all metric math
│   ├── ingestion.py                        # GSW download/cache, capacity loader
│   ├── cgc_reports.py                      # CGCAnalytics facade (data assembly)
│   ├── cgc_charts.py                       # Plotly figure builders (dashboard)
│   │
│   ├── app.py                              # REPORT: CanGrainStats dashboard
│   ├── generate_weekly_pulse.py            # REPORT: Canadian Grain Handling Weekly
│   ├── CGC_MonthlyEx.py                    # REPORT: Monthly export S&D matrix
│   ├── run_report.py                       # REPORT: Text summary + workflow ingestion
│   │
│   ├── CGC_DB.ipynb                        # Research notebook; export-formula ground truth
│   ├── CGC_Capacity.xlsb                   # Licensed elevator capacity workbook
│   ├── cangrainstat_logo.png               # Dashboard header logo
│   ├── requirements.txt                    # Python dependencies
│   ├── .env                                # Local secrets for the Pulse (not committed)
│   └── README.md                           # This file
└── ...                                     # Other ag-quant-research projects
```

**Generated at runtime (in `CGC_Analyzer/`):** `weekly_pulse_report.html`,
`weekly_pulse_payload.json`, `export_pacing_chart.png` (Weekly Pulse), and
`Outputs/CGC_MonthlyEx_<date>.xlsx` (Monthly Export matrix).

### Architecture rules

- `cgc_engine.py`, `ingestion.py`, and `cgc_reports.py` are the **single source of truth** for
  data logic. Report scripts consume them; they never re-derive exports, stocks, or capacity.
- Flat imports, no `-m` flags. Every script finds its own folder via `Path(__file__)`, so
  `gsw_data/`, the capacity workbook, and `Outputs/` resolve correctly from any working
  directory (and on Streamlit Cloud).
- Commodity naming follows raw CGC labels: always **"Amber Durum"**, never "Durum".
- **Export definition** (validated in `CGC_DB.ipynb`): Terminal Exports (`metric = Exports`)
  + Primary Shipment Distribution to `Export Destinations`, on the Crop Year cumulative.

---

## Available Reports & Analytics

| Report | Module | Output | Run |
| :--- | :--- | :--- | :--- |
| CanGrainStats Dashboard | `app.py` | Interactive web app (5 tabs) | `streamlit run app.py` |
| Canadian Grain Handling Weekly | `generate_weekly_pulse.py` | HTML + JSON + PNG, Ghost draft | `python generate_weekly_pulse.py` |
| Monthly Export S&D Matrix | `CGC_MonthlyEx.py` | Excel workbook in `Outputs/` | `python CGC_MonthlyEx.py` |
| Executive Text Report | `run_report.py` | Terminal tables | `python run_report.py` |

All commands are run from inside `CGC_Analyzer/` (or with VS Code's Run button on the file).

### 1. CanGrainStats Dashboard — `app.py`

**Purpose.** Self-serve, chart-first view of where Canadian grain sits in the handling system
and how fast it is leaving. Used to spot logistics bottlenecks and export-pace deviations from
seasonal norms before they show up in price spreads or basis.

**Output structure.** Five tabs, each rendered by its own module in `tabs/`:

| Tab | What it answers |
| :--- | :--- |
| Commercial Pipeline | Stocks vs. licensed capacity, stacked by commodity, per segment (Primary by province, Process East/West, terminal regions) |
| Export Pace | Current crop-year cumulative exports vs. the prior 3-year range, plus a pacing summary table |
| Capacity Bottleneck | Red/Yellow/Green utilization matrix (Red ≥ 85%, Yellow ≥ 75%) across segments and commodities |
| Export Distribution | YTD export pace vs. the 2018-19-to-date seasonal baseline, framed as percentiles |
| Producer Deliveries | Weekly and YTD primary-elevator deliveries by province (SK, AB, MB, BC) with pacing chart |

**Run.** `streamlit run app.py` (not `python app.py`). Needs `CGC_Capacity.xlsb`
(or `.xlsx` / `.xls`) in `CGC_Analyzer/`.

### 2. Canadian Grain Handling Weekly — `generate_weekly_pulse.py`

*Formerly "Weekly Pulse".*

**Purpose.** Automated weekly market note for Chilmeran Research readers. It turns each GSW
release into a short, publishable brief covering the system-wide macro snapshot, export pace
vs. the 5-year average, deliveries, and terminal-corridor stocks and utilization. The draft
goes straight to Ghost, so the only manual step is review and publish.

**Output structure.**
- `weekly_pulse_payload.json` holds the objective metrics, rule tags, and commentary.
  Key Watch and Signal Highlights are kept here but are not rendered in the HTML.
- `export_pacing_chart.png` is a Matplotlib 5-year export pacing chart with the Chilmeran Research watermark.
- `weekly_pulse_report.html` is a self-contained report (chart embedded as base64) in the
  dark-green theme, with all volumes in KMT. Terminal Stocks are merged into the corridor table.
- A Ghost **draft** post is created through the Admin API. The PNG is uploaded as the `feature_image`.

**Rules of note.** Corn and Soybeans are Eastern commodities, so delivery commentary is
suppressed for them (CGC primary-elevator coverage is incomplete in the East). Canola and
Wheat get a signal-ranking boost. YTD export percentages are footnoted through week 10.

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
python CGC_MonthlyEx.py --no-refresh      # offline: use gsw_data/_cache as-is
python CGC_MonthlyEx.py --force-refresh   # re-download every crop year first
python CGC_MonthlyEx.py --keep-truncated  # keep partial leading marketing years
```
Does not need the capacity workbook. Close the previous workbook in Excel before re-running
the same week, or the save will fail.

### 4. Executive Text Report — `run_report.py`

**Purpose.** Fast sanity check straight in the terminal, with no browser needed. It is also
the **data-ingestion entry point** for the GitHub Actions workflow: running it refreshes
`gsw_data/`, including the current crop year and any crop-year rollover catch-up.

**Output structure.** Two tables for the latest crop year and week:
- **Executive Summary** for one commodity (default `COMMODITY = "Canola"`).
- **Regional Utilization Matrix** of stocks vs. licensed capacity by segment.

**Run.** `python run_report.py`. Set `COMMODITY`, `CROP_YEAR`, or `GRAIN_WEEK` at the top of
the file. Leaving them as `None` picks the latest available. Requires the capacity workbook.

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
3. Runs `python CGC_Analyzer/run_report.py` to download and standardize GSW data.
4. Stages **only** `CGC_Analyzer/gsw_data/`, and commits only if something changed.
   The commit message is `Auto-update CGC weekly data [skip ci]`.
5. Runs `git pull --rebase origin main`, then `git push`.

**Safety checks.**

| Setting | Why |
| :--- | :--- |
| `permissions: contents: write` | Without it, the default token is read-only and the push fails with a 403 |
| `concurrency` block (queue, don't cancel) | Overlapping runs, such as a manual trigger during a scheduled one, wait in line instead of racing to push |
| `fetch-depth: 0` + `git pull --rebase` | The bot's commit is replayed on top of any newer commits instead of being rejected as out of date |
| `[skip ci]` + commit-only-if-changed | No empty commits and no recursive workflow triggers on weeks CGC hasn't posted |

**Output directory.** `CGC_Analyzer/gsw_data/`: the raw per-year CSVs plus
`_cache/combined_gsw.parquet`. Run `git pull` before running reports locally to pick up the
bot's latest data. Only the data refresh is automated. The Pulse, `CGC_MonthlyEx`, and the
dashboard all read the refreshed data but are run on demand.

---

## Setup

```
cd CGC_Analyzer
pip install -r requirements.txt
```

1. Place `CGC_Capacity.xlsb` (or `.xlsx` / `.xls`, sheet `CGC_Capacity`) in `CGC_Analyzer/`.
   Paths resolve on their own, so there is nothing to edit.
2. The first run downloads every crop year from 2021-22 onward into `gsw_data/`.
   After that, only the current crop year is refreshed.
3. The Weekly Pulse also needs `jinja2`, `matplotlib`, and `PyJWT`, plus `python-dotenv` if
   you use a `.env` file.

**Git hygiene.** Stage from inside `CGC_Analyzer/`, and never run `git add .` from a parent folder.
Consider adding `CGC_Analyzer/Outputs/` and `.env` to `.gitignore` so generated workbooks and
secrets are never committed.
