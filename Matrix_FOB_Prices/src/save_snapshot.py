"""Record the calculator's current state in the history store.

    python src/save_snapshot.py                 # saves the workbook first if it is open with unsaved edits
    python src/save_snapshot.py --out D:/tmp    # write the parquet files elsewhere (testing)

Reads the SAVED workbook (values Excel calculated) - never writes it - and upserts:
  data_drop/history/pdq_fob_history.parquet  one row per priced crop x zone x route x window month, keyed by as-of date
                                     (re-saving the same as-of date replaces that day's rows)
  data_drop/history/manual_indications_history.parquet  every Manual_Inputs entry ever saved (de-duplicated)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

import config
import layout as L
from excel_com import find_open_workbook

log = logging.getLogger("snapshot")
ROUTE_CODE = {r["route"]: r["code"] for r in config.ROUTES}


def num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def read_workbook(path: Path) -> tuple[dict, list[tuple], list[tuple]]:
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        fx_hdr = [c.value for c in next(wb[L.D_FX].iter_rows(min_row=1, max_row=1))]
        fx_row = [c.value for c in next(wb[L.D_FX].iter_rows(min_row=2, max_row=2))]
        meta = dict(zip(fx_hdr, fx_row))
        calc = list(wb[L.CALC].iter_rows(min_row=L.CALC_FIRST, max_row=L.CALC_LAST, max_col=31, values_only=True))
        man = list(wb[L.MANUAL].iter_rows(min_row=L.MAN_FIRST, max_row=L.MAN_LAST, max_col=13, values_only=True))
    finally:
        wb.close()
    return meta, calc, man


def upsert(path: Path, new: pd.DataFrame, key: list[str], replace_on: str | None = None) -> int:
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    if not old.empty and replace_on:
        old = old[~old[replace_on].isin(new[replace_on].unique())]
    out = pd.concat([old, new], ignore_index=True) if not old.empty else new
    out = out.drop_duplicates(key, keep="last")
    tmp = path.with_suffix(".tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(path)
    return len(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook", type=Path, default=config.WORKBOOK)
    ap.add_argument("--out", type=Path, default=config.HISTORY_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    open_wb = find_open_workbook(args.workbook)
    if open_wb is not None and not open_wb.Saved:
        open_wb.Save()
        log.info("saved open workbook before snapshot")

    meta, calc, man = read_workbook(args.workbook)
    if meta.get("as_of") is None:
        log.error("Data_FX is empty - run Refresh_FOB.bat first")
        return 1
    if all(r[24] is None for r in calc):
        log.error("Calc has no calculated values - open the workbook in Excel, recalculate and save")
        return 1
    as_of = pd.Timestamp(meta["as_of"]).date()
    saved_at = datetime.now().replace(microsecond=0)

    rows = []
    for r in calc:
        crop, zone, route, k = r[0], r[1], r[2], int(r[3])
        source = r[15]
        if source not in ("PDQ", "Manual") or num(r[24]) is None:
            continue
        rows.append({
            "as_of": as_of, "pdq_date": pd.Timestamp(meta["pdq_date"]).date(), "fx_date": pd.Timestamp(meta["fx_date"]).date(),
            "usdcad": num(meta["usdcad"]), "crop": crop, "zone": zone, "route": route, "route_code": ROUTE_CODE[route],
            "window_month": k, "delivery_ym": r[10], "source": source, "cash_cad_mt": num(r[14]),
            "primary": num(r[17]), "rail": num(r[18]), "port_elev": num(r[19]), "margin": num(r[20]),
            "other_fees": num(r[21]), "cost_cad_mt": num(r[22]), "fob_cad_mt": num(r[23]), "fob_usd_mt": num(r[24]),
            "fob_usd_bu": num(r[25]), "futures_code": r[26] or None, "futures_px": num(r[27]),
            "export_basis": num(r[28]), "basis_unit": r[29] if r[29] not in ("-", "") else None,
            "export_basis_usd_mt": num(r[30]), "costing_modified": meta.get("costing_modified"), "saved_at": saved_at,
        })
    fob = pd.DataFrame(rows)
    if fob.empty:
        log.error("no priced cells to record")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    n_fob = upsert(args.out / config.FOB_HISTORY.name, fob,
                   ["as_of", "crop", "zone", "route_code", "delivery_ym"], replace_on="as_of")

    entries = []
    for r in man:
        if not r[11]:  # Status blank -> empty row
            continue
        entries.append({"entry_date": pd.Timestamp(r[0]).date() if r[0] else None, "crop": r[1], "zone": r[2],
                        "delivery_month": r[3], "delivery_ym": r[9], "price": num(r[4]), "unit": r[5],
                        "price_cad_mt": num(r[8]), "source_broker": r[6], "notes": r[7], "status": r[11],
                        "as_of": as_of, "saved_at": saved_at})
    n_man = 0
    if entries:
        n_man = upsert(args.out / config.MANUAL_HISTORY.name, pd.DataFrame(entries),
                       ["entry_date", "crop", "zone", "delivery_ym", "price", "unit", "source_broker"])
    log.info("snapshot %s: %d priced cells (%d PDQ, %d manual) -> %s (%d rows total); manual entries: %d (%d total)",
             as_of, len(fob), (fob.source == "PDQ").sum(), (fob.source == "Manual").sum(), config.FOB_HISTORY.name,
             n_fob, len(entries), n_man)
    return 0


if __name__ == "__main__":
    sys.exit(main())
