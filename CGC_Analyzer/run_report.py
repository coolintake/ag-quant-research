"""run_report.py -- one-command CGC pipeline.

    python CGC_Analyzer/run_report.py                 # ingest -> monthly workbook -> weekly report HTML
    python CGC_Analyzer/run_report.py --publish       # ... and stage the report as a Ghost draft
    python CGC_Analyzer/run_report.py --ingest-only   # refresh data/gsw_data/ only (GitHub Actions)
    python CGC_Analyzer/run_report.py --no-refresh    # rebuild outputs from the cached data

Steps:
  1. Ingest   -- download the latest GSW release (canada.ca) into data/gsw_data/
                 and print a short text check of the newest week.
  2. Monthly  -- CGC_MonthlyEx.py -> Outputs/CGC_MonthlyEx_<date>.xlsx
  3. Weekly   -- generate_weekly_pulse.py -> Outputs/weekly_pulse/weekly_pulse_report.html
                 (Ghost-ready; only pushed to Ghost with --publish)

The data is downloaded once and shared by both reports. The Streamlit
dashboard lives in dashboard/ (`streamlit run CGC_Analyzer/dashboard/app.py`).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Self-locating paths: resolve relative to THIS file's folder, so the
# pipeline runs from any working directory (including the repo root in CI).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core.cgc_engine import DEFAULT_CROP_YEARS, DEFAULT_CURRENT_YEAR  # noqa: E402
from core.cgc_reports import CGCAnalytics  # noqa: E402

DATA_DIR = HERE / "data"
GSW_DATA_DIR = DATA_DIR / "gsw_data"

# Text check printed after ingestion (shows up in the GitHub Actions log).
COMMODITY = "Canola"


def _resolve_capacity_path(here: Path) -> Path:
    """Find CGC_Capacity.(xlsb|xlsx|xls) in `here`, preferring .xlsb but
    accepting whichever extension actually exists -- CapacityLoader reads
    all three natively. Falls back to the .xlsb path (even if missing) so
    the "not found" message below still shows a sensible expected name.
    """
    for ext in (".xlsb", ".xlsx", ".xls"):
        candidate = here / f"CGC_Capacity{ext}"
        if candidate.exists():
            return candidate
    return here / "CGC_Capacity.xlsb"


CAPACITY_PATH = _resolve_capacity_path(DATA_DIR)


def ingest(refresh: bool = True, force_refresh: bool = False) -> CGCAnalytics:
    print(f"Looking for capacity workbook at: {CAPACITY_PATH}")
    if not CAPACITY_PATH.exists():
        raise SystemExit(
            f"\nCGC_Capacity workbook was not found at the path above.\n"
            f"Fix: copy/move your CGC_Capacity.xlsb, .xlsx, or .xls file into this folder:\n"
            f"  {DATA_DIR}\n"
        )
    analytics = CGCAnalytics(gsw_data_dir=str(GSW_DATA_DIR), capacity_xlsb_path=str(CAPACITY_PATH),
                             years=DEFAULT_CROP_YEARS, current_year=DEFAULT_CURRENT_YEAR)
    if refresh:
        analytics.refresh(force_refresh=force_refresh)
    else:
        try:
            analytics.load_cached()
        except FileNotFoundError as exc:
            raise SystemExit(f"{exc} (drop --no-refresh)") from exc

    summary = analytics.get_executive_summary(COMMODITY, None, None)
    regional = analytics.get_regional_utilization_matrix(None, None)
    print(f"\n=== Executive Summary ({COMMODITY}) ===")
    print(summary.to_string(index=False))
    print("\n=== Regional Utilization Matrix ===")
    print(regional.to_string(index=False))
    return analytics


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="CGC pipeline: ingest GSW, build the monthly workbook and weekly report.")
    ap.add_argument("--ingest-only", action="store_true", help="refresh data/gsw_data/ and stop (CI)")
    ap.add_argument("--no-refresh", action="store_true", help="skip the download; use the cached data")
    ap.add_argument("--force-refresh", action="store_true", help="re-download every crop year")
    ap.add_argument("--publish", action="store_true", help="stage the weekly report as a Ghost draft")
    ap.add_argument("--no-llm", action="store_true", help="force rule-based Summary commentary")
    ap.add_argument("--week", type=int, default=None, help="build the weekly report for a specific grain week")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")

    analytics = ingest(refresh=not a.no_refresh, force_refresh=a.force_refresh)
    if a.ingest_only:
        return

    # Imported here so --ingest-only (CI) doesn't need the report-only
    # dependencies (jinja2, matplotlib).
    import CGC_MonthlyEx
    import generate_weekly_pulse as pulse

    print("\n=== Monthly export workbook ===")
    workbook = CGC_MonthlyEx.run(refresh=False)  # reads the cache ingest() just wrote

    print("\n=== Canadian Grain Handling Weekly ===")
    html = pulse.run(week=a.week, use_llm=pulse.USE_LLM_DEFAULT and not a.no_llm,
                     publish=a.publish, analytics=analytics)

    print(f"\nDone.\n  Workbook: {workbook}\n  Report:   {html}")


if __name__ == "__main__":
    main()
