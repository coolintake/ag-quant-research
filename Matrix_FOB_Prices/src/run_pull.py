"""Daily data drop for Matrix_FOB_Calculator.xlsx (Power Query reads these CSVs).

    python src/run_pull.py                     # as of today
    python src/run_pull.py --as-of 2026-10-06      # reproduce a past run
    python src/run_pull.py --refresh-excel         # also Refresh All + save the workbook (Excel must be installed)
    python src/run_pull.py --skip-world            # skip the Hammersmith world FOB step

Writes
  data_drop/pdq_latest.csv : latest PDQ snapshot on/before the as-of date (all delivery months)
  data_drop/fx.csv         : one row of run metadata - as-of, run month, PDQ date/age, BoC FXUSDCAD (4 dp),
                             plus the Hammersmith step's status (world_*)
  data_drop/world_*.csv    : Hammersmith world FOB + freight (Hammersmith/hammersmith_scraper.py). This step
                             never fails the run: on any problem the previous drop is kept and world_status says why.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

import pandas as pd

import config
import layout as L
from fob_common import fx_history, fx_on, latest_pdq_date, read_pdq

log = logging.getLogger("run_pull")

PDQ_COLUMNS = ["key", "pdq_code", "zone", "delivery_ym", "price_date", "cash_cad_mt", "futures_code",
               "futures_px", "local_basis"]
FX_COLUMNS = L.FX_COLS
WORLD_SKIPPED = {"world_report_date": "", "world_age_days": "", "world_status": "SKIPPED", "world_rows": 0,
                 "world_freight_rows": 0, "world_outliers": 0, "world_unmapped": 0}


def world_step(as_of: date) -> dict:
    """Hammersmith world FOB drop (Hammersmith/ folder). Never raises: the PDQ refresh must not depend on it."""
    try:
        sys.path.insert(0, str(config.PROJECT_DIR / "Hammersmith"))
        import hammersmith_scraper
        return hammersmith_scraper.run(as_of)
    except Exception as exc:
        log.exception("Hammersmith step could not start: %s", exc)
        return {**WORLD_SKIPPED, "world_status": f"ERROR: {type(exc).__name__}"}


def build_pdq_latest(as_of: date) -> tuple[pd.DataFrame, date]:
    pdq_date = latest_pdq_date(as_of)
    df = read_pdq(pdq_date, pdq_date)
    df["key"] = df.pdq_code + "|" + df.zone + "|" + df.delivery_ym
    df = df.rename(columns={"ton_price": "cash_cad_mt"})
    df["price_date"] = df.price_date.dt.strftime("%Y-%m-%d")
    df = df[PDQ_COLUMNS].sort_values(["pdq_code", "zone", "delivery_ym"]).reset_index(drop=True)
    dupes = df.key.duplicated()
    if dupes.any():
        raise RuntimeError(f"Duplicate PDQ keys in snapshot {pdq_date}: {df.key[dupes].tolist()[:5]}")
    return df, pdq_date


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--offline", action="store_true", help="use cached FX only")
    ap.add_argument("--refresh-excel", action="store_true", help="Refresh All and save the calculator")
    ap.add_argument("--skip-world", action="store_true", help="skip the Hammersmith world FOB step")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    config.DATA_DROP.mkdir(parents=True, exist_ok=True)

    pdq, pdq_date = build_pdq_latest(args.as_of)
    fx, fx_source = fx_history(end=args.as_of, offline=args.offline)
    usdcad, fx_date = fx_on(fx, pdq_date)            # FX for the PDQ price date
    age = (args.as_of - pdq_date).days
    world = WORLD_SKIPPED if args.skip_world else world_step(args.as_of)
    meta = pd.DataFrame([{
        "as_of": f"{args.as_of:%Y-%m-%d}", "run_month": f"{args.as_of:%Y-%m}-01", "pdq_date": f"{pdq_date:%Y-%m-%d}",
        "pdq_age_days": age, "fx_date": f"{fx_date:%Y-%m-%d}", "usdcad": usdcad, "fx_source": fx_source,
        "costing_file": str(config.COSTING_XLSX),
        "costing_modified": datetime.fromtimestamp(config.COSTING_XLSX.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), **world,
    }])[FX_COLUMNS]

    pdq.to_csv(config.PDQ_LATEST_CSV, index=False, encoding="utf-8")
    meta.to_csv(config.FX_CSV, index=False, encoding="utf-8")
    log.info("pdq_latest.csv: %d rows, PDQ snapshot %s (%d days old)%s", len(pdq), pdq_date, age,
             "  ** STALE **" if age > config.PDQ_STALE_DAYS else "")
    log.info("fx.csv: USDCAD %.4f on %s (%s)", usdcad, fx_date, fx_source)

    if args.refresh_excel:
        from excel_com import refresh_workbook
        refresh_workbook(config.WORKBOOK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
