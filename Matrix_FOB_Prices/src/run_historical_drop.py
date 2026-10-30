"""Weekly historical data drop for the 'Historical_Charts' tab (Power Query reads history_chart.csv).

    python src/run_historical_drop.py                # 2019-01-01 to today
    python src/run_historical_drop.py --as-of 2026-10-06

One row per week (Friday) x crop x zone x horizon (0 = nearby delivery month, +1..+3 months):
  * cash, PDQ-implied futures and PDQ local basis from the week's last PDQ snapshot (DuckDB, read-only)
  * BoC FXUSDCAD for that snapshot date
  * cost stack, FOB (USD/MT) and export basis per route.
    - "pro-forma": historical cash + CURRENT costing (CAN_Costing_Input.xlsx / Raw)
    - "recorded" : values saved by save_snapshot.py (pdq_fob_history.parquet) override pro-forma rows
                   for the weeks they cover, and add manual-only crops (e.g. Red Lentils).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

import numpy as np
import pandas as pd

import config
from fob_common import cost_table, fx_history, read_costing, read_pdq

log = logging.getLogger("history")

COLUMNS = ["week", "crop", "zone", "horizon", "delivery_ym", "cash_cad_mt", "futures_code", "futures_px",
           "local_basis", "usdcad", "cost_vc", "cost_tbay", "cost_stl", "fob_vc_usd_mt", "fob_tbay_usd_mt",
           "fob_stl_usd_mt", "xbasis_vc", "xbasis_tbay", "xbasis_stl", "cost_basis"]
ROUTE_CODES = [r["code"].lower() for r in config.ROUTES]          # vc, tbay, stl


def month_index(ym: pd.Series) -> pd.Series:
    return ym.str[:4].astype(int) * 12 + ym.str[5:7].astype(int)


def zone_allowed(crop: str, zone: str) -> bool:
    z = config.CROP_RULES[crop]["zones"]
    return z == "ALL" or zone in z.split(";")


def add_route_values(df: pd.DataFrame, costs: dict) -> pd.DataFrame:
    """cost_<route>, fob_<route>_usd_mt and xbasis_<route> from cash, futures, FX and the cost table."""
    rules = df.crop.map(config.CROP_RULES)
    cost_com = rules.map(lambda r: r["cost"])
    basis = rules.map(lambda r: r["basis"])
    allowed = np.array([zone_allowed(c, z) for c, z in zip(df.crop, df.zone)])
    for code in ROUTE_CODES:
        cost = np.array([costs.get((cc, z, code.upper()), np.nan) for cc, z in zip(cost_com, df.zone)], dtype=float)
        cost[~allowed] = np.nan
        fob_cad = df.cash_cad_mt.to_numpy(float) + cost
        fob_usd = fob_cad / df.usdcad.to_numpy(float)
        fut = pd.to_numeric(df.futures_px, errors="coerce").to_numpy(float)
        xb = np.where(basis.eq("USD_BU"), fob_usd / config.BU_PER_MT_WHEAT - fut,
                      np.where(basis.eq("CAD_MT"), fob_cad - fut, np.nan))
        df[f"cost_{code}"] = np.round(cost, 2)
        df[f"fob_{code}_usd_mt"] = np.round(fob_usd, 2)
        df[f"xbasis_{code}"] = np.round(xb, 4)
    return df


def recorded_rows(fx: pd.DataFrame) -> pd.DataFrame | None:
    """Weekly rows from pdq_fob_history.parquet (one per week x crop x zone x horizon, all routes)."""
    if not config.FOB_HISTORY.exists():
        return None
    h = pd.read_parquet(config.FOB_HISTORY)
    if h.empty:
        return None
    h["price_date"] = pd.to_datetime(h.pdq_date)
    h["week"] = h.price_date + pd.to_timedelta(4 - h.price_date.dt.weekday, unit="D")
    h["horizon"] = month_index(h.delivery_ym) - (h.price_date.dt.year * 12 + h.price_date.dt.month)
    h = h[h.horizon.isin(config.HISTORY_HORIZONS) & h.cash_cad_mt.notna()]
    h = h.sort_values("as_of").groupby(["week", "crop", "zone", "horizon", "route_code"]).tail(1)
    base = h.groupby(["week", "crop", "zone", "horizon"]).agg(
        delivery_ym=("delivery_ym", "first"), cash_cad_mt=("cash_cad_mt", "first"),
        futures_code=("futures_code", "first"), futures_px=("futures_px", "first"),
        usdcad=("usdcad", "first")).reset_index()
    for code in ROUTE_CODES:
        r = h[h.route_code.str.lower() == code][["week", "crop", "zone", "horizon", "cost_cad_mt",
                                                    "fob_usd_mt", "export_basis"]]
        r = r.rename(columns={"cost_cad_mt": f"cost_{code}", "fob_usd_mt": f"fob_{code}_usd_mt",
                              "export_basis": f"xbasis_{code}"})
        base = base.merge(r, on=["week", "crop", "zone", "horizon"], how="left")
    base["local_basis"] = np.nan
    base["cost_basis"] = "recorded"
    return base


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    config.DATA_DROP.mkdir(parents=True, exist_ok=True)

    pdq = read_pdq(date.fromisoformat(config.HISTORY_START), args.as_of)
    pdq["week"] = pdq.price_date + pd.to_timedelta(4 - pdq.price_date.dt.weekday, unit="D")
    last_day = pdq.groupby("week").price_date.transform("max")          # week's last trading day
    pdq = pdq[pdq.price_date == last_day].copy()
    pdq["horizon"] = month_index(pdq.delivery_ym) - (pdq.price_date.dt.year * 12 + pdq.price_date.dt.month)
    pdq = pdq[pdq.horizon.isin(config.HISTORY_HORIZONS)].copy()
    pdq["crop"] = pdq.pdq_code.map(config.PDQ_TO_CROP)

    fx, fx_source = fx_history(end=args.as_of, offline=args.offline)
    pdq = pd.merge_asof(pdq.sort_values("price_date"), fx.rename(columns={"fx_date": "price_date"}),
                        on="price_date", direction="backward")
    df = pdq.rename(columns={"ton_price": "cash_cad_mt"})
    df = add_route_values(df, cost_table(read_costing()))
    df["cost_basis"] = "pro-forma"

    rec = recorded_rows(fx)
    if rec is not None:
        key = ["week", "crop", "zone", "horizon"]
        df = pd.concat([df[~df.set_index(key).index.isin(rec.set_index(key).index)], rec], ignore_index=True)
        log.info("recorded snapshots override %d weekly rows", len(rec))

    df["week"] = pd.to_datetime(df.week).dt.strftime("%Y-%m-%d")
    for c in ("cash_cad_mt",):
        df[c] = df[c].round(2)
    df["usdcad"] = df.usdcad.round(config.FX_DECIMALS)
    df["local_basis"] = pd.to_numeric(df.local_basis).round(4)
    out = df[COLUMNS].sort_values(["crop", "zone", "horizon", "week"]).reset_index(drop=True)
    dup = out.duplicated(["week", "crop", "zone", "horizon"])
    if dup.any():
        raise RuntimeError(f"{dup.sum()} duplicate week/crop/zone/horizon rows")
    out.to_csv(config.HISTORY_CSV, index=False, encoding="utf-8")
    size = config.HISTORY_CSV.stat().st_size / 1e6
    log.info("history_chart.csv: %d rows, %d weeks (%s to %s), %.1f MB | FX: %s", len(out), out.week.nunique(),
             out.week.min(), out.week.max(), size, fx_source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
