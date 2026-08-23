"""run_report.py — text-only report, no browser needed. Open in VS Code
and hit Run (or F5); plain `python run_report.py` from this folder, no -m
flags, no path setup needed.

For the interactive Plotly/Streamlit dashboard (stacked capacity matrix +
cumulative pacing charts), run `streamlit run app.py` instead -- Streamlit
apps are launched via the `streamlit` CLI, not `python app.py`."""

from pathlib import Path
from cgc_reports import CGCAnalytics

# Self-locating paths: these always resolve relative to THIS file's folder,
# regardless of where you run `python run_report.py` from. Put
# CGC_Capacity.xlsb directly in this same folder and it'll be found
# automatically -- no path editing required.
HERE = Path(__file__).resolve().parent
GSW_DATA_DIR = HERE / "gsw_data"
CAPACITY_PATH = HERE / "CGC_Capacity.xlsb"

COMMODITY = "Canola"
# Leave these as None to auto-resolve to the latest crop year & week:
CROP_YEAR = None
GRAIN_WEEK = None

print(f"Looking for capacity workbook at: {CAPACITY_PATH}")
print(f"  Found: {CAPACITY_PATH.exists()}")
if not CAPACITY_PATH.exists():
    raise SystemExit(
        f"\nCGC_Capacity.xlsb was not found at the path above.\n"
        f"Fix: copy/move your CGC_Capacity.xlsb file into this folder:\n"
        f"  {HERE}\n"
    )

analytics = CGCAnalytics(gsw_data_dir=str(GSW_DATA_DIR), capacity_xlsb_path=str(CAPACITY_PATH))
analytics.refresh()

summary = analytics.get_executive_summary(COMMODITY, CROP_YEAR, GRAIN_WEEK)
regional = analytics.get_regional_utilization_matrix(CROP_YEAR, GRAIN_WEEK)

print("\n=== Executive Summary ===")
print(summary.to_string(index=False))
print("\n=== Regional Utilization Matrix ===")
print(regional.to_string(index=False))
