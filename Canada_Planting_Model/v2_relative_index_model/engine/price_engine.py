"""PDQ prompt cash bids per province x crop x window x year (SPECIFICATION_V2.md 3.1).

For each crop x zone x price date in the window: the bid for the window's start delivery month,
else the earliest later delivery month with a bid (no later than JUL of T). Bids outside the crop's
plausible $/bu range are rejected (UNIT_SUSPECT). Provincial daily price = mean over quoting zones
(Durum: S ALTA / SW SASK only); window price = mean of the daily prices. Fewer than MIN_QUOTED_DAYS
quoted days -> NULL (THIN_WINDOW). Prices only - no guide or template fallback.
"""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd

import config as C


def window_bounds(year: int, run: str) -> tuple[date, date]:
    (sm, sd, sy), (em, ed, ey) = C.WINDOWS[run]
    return date(year + sy, sm, sd), date(year + ey, em, ed)


def _period(year: int, month: int) -> int:
    return year * 12 + month - 1


def connect(db_path=C.PRICE_DB) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path.as_posix()}' AS src (READ_ONLY)")
    return con


def _zone_sql() -> str:
    parts = [f"(crop_symbol <> '{C.CROPS[c]['pdq']}' OR pdq_zone IN ({', '.join(repr(z) for z in zones)}))"
             for c, zones in C.ZONE_FILTER.items() if zones]
    return " AND ".join(parts) or "TRUE"


def zone_days(con, year: int, run: str, table: str = f"src.{C.PRICE_TABLE}") -> pd.DataFrame:
    """One prompt bid per crop x zone x day (earliest eligible delivery period), before range checks."""
    start, end = window_bounds(year, run)
    first, last = _period(start.year, start.month), _period(year, C.PROMPT_LAST_DELIVERY_MONTH)
    months = "[" + ", ".join(f"'{m}'" for m in C.MONTHS) + "]"
    symbols = ", ".join(f"'{c['pdq']}'" for c in C.CROPS.values())
    sql = f"""
        WITH q AS (
            SELECT price_date, province, pdq_zone, crop_symbol, price_cad_bu,
                   CAST(delivery_year * 12 + list_position({months}, delivery_month) - 1 AS BIGINT) AS period
            FROM {table}
            WHERE source = 'PDQ' AND crop_symbol IN ({symbols})
              AND price_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'
              AND price_cad_bu IS NOT NULL AND price_cad_bu > 0 AND {_zone_sql()}
        )
        SELECT price_date, any_value(province) AS province, pdq_zone, crop_symbol,
               min(period) AS period, arg_min(price_cad_bu, period) AS price,
               min(period) = {first} AS is_primary
        FROM q WHERE period BETWEEN {first} AND {last}
        GROUP BY price_date, pdq_zone, crop_symbol
    """
    return con.execute(sql).df()


def window_prices(con, years=C.YEARS, table: str = f"src.{C.PRICE_TABLE}") -> pd.DataFrame:
    """Province x crop x run x year window prices with quality flags (one row per in-scope cell)."""
    sym_to_crop = {c["pdq"]: k for k, c in C.CROPS.items()}
    rows = []
    for year in years:
        for run in C.WINDOWS:
            zd = zone_days(con, year, run, table)
            zd["crop"] = zd["crop_symbol"].map(sym_to_crop)
            lo = zd["crop"].map(lambda c: C.CROPS[c]["price_range"][0])
            hi = zd["crop"].map(lambda c: C.CROPS[c]["price_range"][1])
            zd["in_range"] = zd["price"].between(lo, hi)
            for prov, crops in C.PROVINCE_CROPS.items():
                for crop in crops:
                    g = zd[(zd["province"] == prov) & (zd["crop"] == crop)]
                    rejected = int((~g["in_range"]).sum())
                    g = g[g["in_range"]]
                    flags = []
                    if rejected:
                        flags.append("UNIT_SUSPECT")
                    rec = {"province": prov, "crop": crop, "run": run, "year": year, "price": np.nan,
                           "n_days": 0, "n_zone_days": len(g), "n_rejected": rejected,
                           "primary_share": np.nan, "periods": None}
                    if g.empty:
                        flags.append("NO_QUOTES")
                    else:
                        daily = g.groupby("price_date")["price"].mean()
                        rec.update(n_days=len(daily), primary_share=float(g["is_primary"].mean()),
                                   periods=", ".join(f"{C.MONTHS[p % 12]} {p // 12}" for p in sorted(set(g["period"]))))
                        if len(daily) < C.MIN_QUOTED_DAYS:
                            flags.append("THIN_WINDOW")
                        else:
                            rec["price"] = float(daily.mean())
                    rec["price_flags"] = ";".join(flags) or None
                    rows.append(rec)
    return pd.DataFrame(rows)


def load_prices(years=C.YEARS) -> pd.DataFrame:
    con = connect()
    try:
        return window_prices(con, years)
    finally:
        con.close()
