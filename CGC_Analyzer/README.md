# CGC Grain Market Analytics

## What this is

- Pulls weekly Canadian Grain Commission (CGC) Grain Statistics Weekly (GSW) data and licensed elevator capacity data, and turns them into grain-logistics market metrics.
- Tracks 6 core commodities (Wheat, Durum, Canola, Soybeans, Barley, Oats) across Primary Elevators (by province), Process Elevators (East/West), and Export Terminal Ports (Vancouver, Prince Rupert, Thunder Bay, St. Lawrence, Churchill, Bay & Lakes).
- Computes capacity utilization, weeks-of-supply, system velocity, seasonal outflow anomalies, and a Red/Yellow/Green bottleneck matrix.
- Two ways to view results: a plain-text report (`run_report.py`) and an interactive Plotly/Streamlit dashboard (`app.py`) with a stacked capacity-vs-utilization chart and a cumulative outflow pacing chart (current year vs. 3-year historical range).

## Files

| File | Purpose |
|---|---|
| `cgc_engine.py` | Constants, schema normalization, and all metric math |
| `ingestion.py` | Downloading/caching GSW CSVs, loading the capacity workbook |
| `cgc_reports.py` | `CGCAnalytics` facade + Plotly chart builders |
| `run_report.py` | Quick text-only report |
| `app.py` | Interactive Streamlit dashboard |

## Setup

Open this folder in VS Code, then in the terminal:

```
pip install -r requirements.txt
```

Before running anything, open `run_report.py` and/or `app.py` and edit these two lines near the top with your actual file locations:

```python
GSW_DATA_DIR = r"C:\path\to\your\gsw_cache_folder"
CAPACITY_PATH = r"C:\path\to\CGC_Capacity.xlsb"
```

## Run it

**Text report** (prints tables to the terminal):
```
python run_report.py
```

**Interactive dashboard** (opens in your browser):
```
streamlit run app.py
```
