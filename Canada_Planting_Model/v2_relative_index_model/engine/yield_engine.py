"""CANSIM yields, trend yields and seeded acres (SPECIFICATION_V2.md 3.2 and 3.4).

Trend yield for crop year T: OLS Yield = g0 + g1 * Year on the non-NULL actuals of
YIELD_TREND_FIRST_YEAR (2005) .. T-1, evaluated at T. The window expands (14 points for T = 2019,
21 for T = 2026); year T's own yield is never used. Fewer than YIELD_TREND_MIN_N points -> NULL,
INSUFFICIENT_YIELD_HISTORY. A zero yield (StatCan's 'nothing seeded') is NULL.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

import config as C


def load_cansim(first_year: int = C.YIELD_TREND_FIRST_YEAR) -> pd.DataFrame:
    """Province x crop x year: yield (bu/ac) and seeded acres for the V2 crops."""
    raw = pd.read_csv(C.STATCAN_CSV, low_memory=False,
                      usecols=["REF_DATE", "GEO", "Harvest disposition", "Type of crop", "VALUE"])
    geo = {v: k for k, v in C.PROVINCES.items()}
    series = {c["statcan"]: k for k, c in C.CROPS.items()}
    raw = raw[raw["GEO"].isin(geo) & raw["Type of crop"].isin(series) & (raw["REF_DATE"] >= first_year)]
    wide = raw.pivot_table(index=["GEO", "REF_DATE", "Type of crop"], columns="Harvest disposition",
                           values="VALUE", aggfunc="first").reset_index()

    def col(name):
        return wide[name] if name in wide else pd.Series(np.nan, index=wide.index)

    kg = wide["Type of crop"].map(lambda s: C.CROPS[series[s]]["kg_per_bu"])
    bu = col("Average yield (bushels per acre)")
    yld = bu.where(bu.notna(), col("Average yield (kilograms per hectare)") / C.HA_TO_AC / kg)
    acres = col("Seeded area (acres)")
    acres = acres.where(acres.notna(), col("Seeded area (hectares)") * C.HA_TO_AC)
    out = pd.DataFrame({"province": wide["GEO"].map(geo), "crop": wide["Type of crop"].map(series),
                        "year": wide["REF_DATE"].astype(int),
                        "yield_actual": yld.where(yld != 0),       # 0 = not seeded -> NULL
                        "acres": acres.where(acres != 0)})
    return out.sort_values(["province", "crop", "year"], ignore_index=True)


def trend_yields(cansim: pd.DataFrame, years=C.YEARS) -> pd.DataFrame:
    """Expanding 2005 .. T-1 OLS trend, evaluated at T."""
    rows = []
    for (prov, crop), g in cansim.groupby(["province", "crop"]):
        hist = g.dropna(subset=["yield_actual"]).set_index("year")["yield_actual"]
        for t in years:
            h = hist[(hist.index >= C.YIELD_TREND_FIRST_YEAR) & (hist.index <= t - 1)]
            rec = {"province": prov, "crop": crop, "year": t, "trend_n": len(h),
                   "trend_yield": np.nan, "trend_slope": np.nan, "yield_flags": None}
            if len(h) >= C.YIELD_TREND_MIN_N:
                slope, icpt = np.polyfit(h.index.to_numpy(float), h.to_numpy(float), 1)
                rec.update(trend_yield=icpt + slope * t, trend_slope=slope)
            else:
                rec["yield_flags"] = "INSUFFICIENT_YIELD_HISTORY"
            rows.append(rec)
    return pd.DataFrame(rows)


def acreage(cansim: pd.DataFrame) -> tuple[pd.DataFrame, int, str]:
    """Seeded acres with prior-year acres and %change. Returns (table, preliminary year, extract date)."""
    a = cansim[["province", "crop", "year", "acres"]].copy()
    prev = a.assign(year=a["year"] + 1).rename(columns={"acres": "acres_prev"})
    a = a.merge(prev, on=["province", "crop", "year"], how="left")
    a["pct_d_acres"] = a["acres"] / a["acres_prev"] - 1
    prelim = int(a.dropna(subset=["acres"])["year"].max())
    extract = datetime.fromtimestamp(C.STATCAN_CSV.stat().st_mtime).date().isoformat()
    return a, prelim, extract
