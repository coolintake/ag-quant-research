"""Shared data access for Matrix_FOB_Prices: Bank of Canada FX, PDQ snapshots, costing.

All sources are local except the Bank of Canada Valet API (public, no key), which is cached in
cache/fx_history.parquet so the pipeline still runs offline on cached rates.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date, datetime, timedelta

import duckdb
import pandas as pd
from openpyxl import load_workbook

import config

log = logging.getLogger("fob")
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# --------------------------------------------------------------------------- #
# Bank of Canada FXUSDCAD
# --------------------------------------------------------------------------- #
def _fetch_boc(start: date, end: date) -> pd.DataFrame:
    url = f"{config.FX_URL}?start_date={start:%Y-%m-%d}&end_date={end:%Y-%m-%d}"
    req = urllib.request.Request(url, headers={"User-Agent": "Matrix_FOB_Prices/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        obs = json.load(resp).get("observations", [])
    rows = [(o["d"], float(o[config.FX_SERIES]["v"])) for o in obs if config.FX_SERIES in o]
    return pd.DataFrame(rows, columns=["fx_date", "usdcad"]).assign(fx_date=lambda d: pd.to_datetime(d.fx_date))


def fx_history(end: date | None = None, offline: bool = False) -> tuple[pd.DataFrame, str]:
    """Daily FXUSDCAD from HISTORY_START to `end`, cached. Returns (frame, source note)."""
    end = end or date.today()
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = pd.read_parquet(config.FX_CACHE) if config.FX_CACHE.exists() else pd.DataFrame(
        {"fx_date": pd.Series(dtype="datetime64[ns]"), "usdcad": pd.Series(dtype=float)})
    source = "Bank of Canada Valet FXUSDCAD"
    last = cache.fx_date.max().date() if len(cache) else None
    if not offline and (last is None or last < end):
        start = (last + timedelta(days=1)) if last else date.fromisoformat(config.HISTORY_START)
        try:
            new = _fetch_boc(start, end)
            if not new.empty:
                cache = new if cache.empty else pd.concat([cache, new])
            cache = cache.drop_duplicates("fx_date", keep="last").sort_values("fx_date").reset_index(drop=True)
            cache.to_parquet(config.FX_CACHE, index=False)
        except Exception as exc:  # network down -> cached rates, clearly flagged
            log.warning("BoC fetch failed (%s); using cached FX through %s", exc, last)
            source += f" (CACHED - fetch failed {datetime.now():%Y-%m-%d})"
    elif offline:
        source += " (cached, offline run)"
    if cache.empty:
        raise RuntimeError("No FX rates available (fetch failed and cache is empty)")
    cache["fx_date"] = cache.fx_date.astype("datetime64[ns]")
    cache["usdcad"] = cache.usdcad.round(config.FX_DECIMALS)
    return cache, source


def fx_on(fx: pd.DataFrame, when: date) -> tuple[float, date]:
    """Latest published rate on or before `when` (BoC publishes business days only)."""
    sub = fx[fx.fx_date <= pd.Timestamp(when)]
    if sub.empty:
        raise RuntimeError(f"No FXUSDCAD rate on or before {when}")
    row = sub.iloc[-1]
    return round(float(row.usdcad), config.FX_DECIMALS), row.fx_date.date()


# --------------------------------------------------------------------------- #
# PDQ (local DuckDB, read-only)
# --------------------------------------------------------------------------- #
def read_pdq(since: date, until: date) -> pd.DataFrame:
    """PDQ rows between two price dates, last import of each day, mapped to PDQ codes.

    Adds delivery_ym ('YYYY-MM'), implied futures (cash - PDQ basis) and the PDQ basis in its
    native unit: wheat basis is CAD cash minus USD futures per bushel (mixed currency, as PDQ
    publishes it), so futures_px is USD/bu for CWRS/CPSR and CAD/MT for canola.
    """
    con = duckdb.connect(str(config.PDQ_DB), read_only=True)
    try:
        df = con.sql(f"""
            WITH r AS (
              SELECT *, CAST(imported_at AS DATE) AS price_date,
                     max(imported_at) OVER (PARTITION BY CAST(imported_at AS DATE)) AS last_imp
              FROM pdq_raw_prices
              WHERE CAST(imported_at AS DATE) BETWEEN DATE '{since}' AND DATE '{until}')
            SELECT price_date, zone, commodity, delivery_month, delivery_year,
                   ton_price, bus_price, basis_mt, basis_bu, futures_code
            FROM r WHERE imported_at = last_imp""").df()
    finally:
        con.close()
    df["pdq_code"] = df.commodity.map(config.PDQ_COMMODITY_MAP)
    df = df[df.pdq_code.notna() & df.ton_price.notna()].copy()
    df["price_date"] = pd.to_datetime(df.price_date).astype("datetime64[ns]")
    df["delivery_ym"] = (df.delivery_year.astype(int).astype(str) + "-"
                         + df.delivery_month.map(lambda m: f"{MONTHS.index(m) + 1:02d}"))
    wheat = df.pdq_code.isin(["CWRS", "CPSR"])
    canola = df.pdq_code.eq("CANOLA")
    df["futures_px"] = None
    df.loc[wheat, "futures_px"] = ((df.ton_price - df.basis_mt) / config.BU_PER_MT_WHEAT)[wheat]
    df.loc[canola, "futures_px"] = (df.ton_price - df.basis_mt)[canola]
    df["futures_px"] = pd.to_numeric(df.futures_px).round(4)
    df["local_basis"] = None
    df.loc[wheat, "local_basis"] = df.basis_bu[wheat]
    df.loc[canola, "local_basis"] = df.basis_mt[canola]
    df["local_basis"] = pd.to_numeric(df.local_basis)
    df.loc[df.futures_px.isna(), "futures_code"] = None
    return df


def latest_pdq_date(as_of: date) -> date:
    con = duckdb.connect(str(config.PDQ_DB), read_only=True)
    try:
        d = con.sql(f"SELECT max(CAST(imported_at AS DATE)) FROM pdq_raw_prices "
                    f"WHERE CAST(imported_at AS DATE) <= DATE '{as_of}'").fetchone()[0]
    finally:
        con.close()
    if d is None:
        raise RuntimeError(f"No PDQ snapshot on or before {as_of}")
    return d


# --------------------------------------------------------------------------- #
# Costing (CAN_Costing_Input.xlsx / Raw, cached values written by Excel)
# --------------------------------------------------------------------------- #
RAW_COLUMNS = {
    "Commodity": "commodity", "PDQ Zone": "zone", "Route": "route",
    "Primary Elevation ($/MT)": "primary", "Rail Freight ($/MT)": "rail",
    "Port Elevation ($/MT)": "port_elev", "Export Margin ($/MT)": "margin", "Other Fees ($/MT)": "other_fees",
}


def read_costing() -> pd.DataFrame:
    """Component costs per commodity x zone x Raw route (CAD/MT); blanks / 'n/a' -> NaN.

    Raises if the workbook was saved without calculated values (all components empty).
    """
    wb = load_workbook(config.COSTING_XLSX, data_only=True, read_only=True)
    try:
        ws = wb[config.COSTING_SHEET]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    hdr_idx = next(i for i, r in enumerate(rows) if r and r[0] == "Commodity")
    hdr = [str(h).strip() if h else "" for h in rows[hdr_idx]]
    recs = []
    raw_routes = {r["raw"] for r in config.ROUTES}
    for r in rows[hdr_idx + 1:]:
        if not r or r[2] not in raw_routes:
            continue
        recs.append({v: r[hdr.index(k)] for k, v in RAW_COLUMNS.items()})
    df = pd.DataFrame(recs)
    for c in config.COST_COMPONENTS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df[list(config.COST_COMPONENTS)].isna().all().all():
        raise RuntimeError(f"{config.COSTING_XLSX.name} has no calculated values in '{config.COSTING_SHEET}'. "
                           "Open it in Excel and save, then re-run.")
    df["total"] = df[list(config.COST_COMPONENTS)].sum(axis=1, min_count=len(config.COST_COMPONENTS))
    return df


def cost_table(costing: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    """(cost commodity, zone, route code) -> total CAD/MT (NaN when any component is missing)."""
    raw_to_code = {r["raw"]: r["code"] for r in config.ROUTES}
    return {(r.commodity, r.zone, raw_to_code[r.route]): r.total for r in costing.itertuples()}


def window_months(as_of: date, n: int = config.WINDOW_MONTHS) -> list[str]:
    """Delivery 'YYYY-MM' for the run month and the next n-1 months."""
    y, m = as_of.year, as_of.month
    out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
