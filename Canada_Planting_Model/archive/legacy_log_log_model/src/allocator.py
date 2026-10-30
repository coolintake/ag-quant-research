"""Land pool allocation and forecast flags (model_crop_wallet_step1.md 8.3 and 9).

Cropland basket for province p (StatCan 32-10-0359 seeded area, one series per crop, no
sub-aggregates, forage and silage excluded):

    CWRS, Durum, Other wheat (= Wheat incl. winter remaining - CWRS - Durum), the other modelled
    crops, Corn for grain, Other field crops (every remaining principal field crop).

Lines that are not forecast (other wheat, corn, other field crops, exclusions) are held at T-1.

    SF_min  = min summer fallow over T-25 .. T-1 (rolling)
    L       = C_{T-1} + (SF_{T-1} - SF_min)                       land pool
    S       = sum of unconstrained basket acres
    Case A  S <= L : no scaling, SF_T = SF_{T-1} - (S - C_{T-1})
    Case B  S >  L : every basket line x L / S, SF_T = SF_min       (LAND_POOL_SCALED)

Missing (suppressed / unpublished) series count as 0 acres in the basket and are listed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import settings as S

WHEAT_TOTAL = "Wheat, all including winter wheat remaining"   # land in crop: spring + durum + surviving winter
OTHER_PRINCIPAL = (
    "Barley", "Oats", "Rye, all including fall rye remaining", "Mixed grains", "Corn for grain", "Triticale",
    "Canola (rapeseed)", "Flaxseed", "Mustard seed", "Canary seed", "Sunflower seed", "Soybeans", "Peas, dry",
    "Lentils", "Chick peas", "Beans, all dry (white and coloured)", "Faba beans", "Buckwheat", "Hemp",
    "Sugar beets", "Caraway seed", "Coriander seed", "Borage seed", "Safflower",
)
FALLOW = "Summerfallow"
CORN = "Corn for grain"
SF_FLOOR_YEARS = 25
ROTATION_CEILING = 1 / 3


def load_field_area(first_year: int) -> pd.DataFrame:
    """Seeded area (acres) per province x year x StatCan series for the basket and summer fallow."""
    raw = pd.read_csv(S.STATCAN_CSV, low_memory=False,
                      usecols=["REF_DATE", "GEO", "Harvest disposition", "Type of crop", "VALUE"])
    geo_to_prov = {v: k for k, v in S.PROVINCES.items()}
    keep = (WHEAT_TOTAL, FALLOW, *OTHER_PRINCIPAL) + tuple(c["statcan"] for c in S.CROPS.values())
    raw = raw[raw["GEO"].isin(geo_to_prov) & raw["Type of crop"].isin(keep) & (raw["REF_DATE"] >= first_year)
              & raw["Harvest disposition"].isin(["Seeded area (acres)", "Seeded area (hectares)"])]
    wide = raw.pivot_table(index=["GEO", "REF_DATE", "Type of crop"], columns="Harvest disposition",
                           values="VALUE", aggfunc="first").reset_index()
    ac = wide.get("Seeded area (acres)", pd.Series(np.nan, index=wide.index))
    ha = wide.get("Seeded area (hectares)", pd.Series(np.nan, index=wide.index))
    return pd.DataFrame({"province": wide["GEO"].map(geo_to_prov), "year": wide["REF_DATE"].astype(int),
                         "series": wide["Type of crop"], "acres": ac.where(ac.notna(), ha * S.HA_TO_AC)})


def basket(field: pd.DataFrame, province: str, year: int) -> tuple[pd.DataFrame, list[str]]:
    """Basket lines for one province-year. Returns (lines, missing series counted as 0).

    Columns: line, crop (model key or None), acres, published (False when the line's single StatCan
    series is unpublished and counted as 0). Lines sum to cropland C exactly.
    """
    f = field[(field["province"] == province) & (field["year"] == year)].set_index("series")["acres"]
    missing: list[str] = []

    def get(series: str) -> float:
        v = f.get(series, np.nan)
        if pd.isna(v):
            missing.append(series)
            return 0.0
        return float(v)

    statcan_to_crop = {c["statcan"]: k for k, c in S.CROPS.items()}
    lines = []
    wheat = get(WHEAT_TOTAL)
    cwrs, durum = get(S.CROPS["CWRS"]["statcan"]), get(S.CROPS["DURUM"]["statcan"])
    lines += [("CWRS Wheat", "CWRS", cwrs, S.CROPS["CWRS"]["statcan"]),
              ("Durum Wheat", "DURUM", durum, S.CROPS["DURUM"]["statcan"]),
              ("Other wheat (CPSR, CNHR, soft, winter, other)", None, wheat - cwrs - durum, WHEAT_TOTAL)]
    other = 0.0
    for series in OTHER_PRINCIPAL:
        crop = statcan_to_crop.get(series)
        if crop:
            lines.append((S.CROPS[crop]["label"], crop, get(series), series))
        elif series == CORN:
            lines.append(("Corn for grain", "CORN", get(series), series))
        else:
            v = f.get(series, np.nan)
            other += 0.0 if pd.isna(v) else float(v)
    lines.append(("Other field crops", None, other, None))
    out = pd.DataFrame(lines, columns=["line", "crop", "acres", "series"])
    out["published"] = ~out["series"].isin(missing)
    return out.drop(columns="series"), missing


def fallow_floor(field: pd.DataFrame, province: str, year: int) -> tuple[float, int]:
    """Rolling minimum summer fallow over year-25 .. year-1 and the year it occurred."""
    sf = field[(field["province"] == province) & (field["series"] == FALLOW)
               & field["year"].between(year - SF_FLOOR_YEARS, year - 1)].dropna(subset=["acres"])
    row = sf.loc[sf["acres"].idxmin()]
    return float(row["acres"]), int(row["year"])


def allocate(lines: pd.DataFrame, field: pd.DataFrame, province: str, year: int) -> tuple[pd.DataFrame, dict]:
    """Apply the land pool to one province.

    ``lines``: basket lines with acres_prev and acres_unconstrained (held lines: equal).
    Returns (lines + acres_allocated, pool summary dict).
    """
    c_prev = float(lines["acres_prev"].sum())
    sf_prev = field[(field["province"] == province) & (field["series"] == FALLOW)
                    & (field["year"] == year - 1)]["acres"].iloc[0]
    sf_min, sf_min_year = fallow_floor(field, province, year)
    pool = c_prev + (sf_prev - sf_min)
    s = float(lines["acres_unconstrained"].sum())
    out = lines.copy()
    if s <= pool:
        case, scale = "A", 1.0
        sf_t = sf_prev - (s - c_prev)
    else:
        case, scale = "B", pool / s
        sf_t = sf_min
    out["acres_allocated"] = out["acres_unconstrained"] * scale
    summary = {"province": province, "year": year, "cropland_prev": c_prev, "summerfallow_prev": float(sf_prev),
               "sf_floor": sf_min, "sf_floor_year": sf_min_year, "available_fallow": float(sf_prev - sf_min),
               "land_pool": pool, "unconstrained_total": s, "case": case, "scale_factor": scale,
               "allocated_total": float(out["acres_allocated"].sum()), "summerfallow_implied": float(sf_t),
               "flag": "LAND_POOL_SCALED" if case == "B" else None}
    return out, summary


def forecast_flags(out: pd.DataFrame, history: pd.DataFrame, province: str, year: int) -> pd.Series:
    """Spec 9 flags per line: post-2019 range breaches (forecast lines only - held lines just carry
    T-1 acres) and the canola rotation ceiling."""
    flags = []
    total = out["acres_allocated"].sum()
    for _, r in out.iterrows():
        f = []
        if r["crop"] in S.CROPS and r["status"] == "APPLY_ELASTICITY":
            h = history[(history["province"] == province) & (history["crop"] == r["crop"])
                        & history["year"].between(2019, year - 1)]["acres"].dropna()
            if len(h):
                if r["acres_allocated"] > h.max():
                    f.append(f"FLAG_UPPER_BOUND_BREACH (+{r['acres_allocated'] / h.max() - 1:.1%} vs max {int(h.max()):,})")
                elif r["acres_allocated"] < h.min():
                    f.append(f"FLAG_LOWER_BOUND_BREACH ({r['acres_allocated'] / h.min() - 1:.1%} vs min {int(h.min()):,})")
        if r["crop"] == "CANOLA" and total and r["acres_allocated"] / total > ROTATION_CEILING:
            f.append(f"FLAG_ROTATIONAL_CEILING ({r['acres_allocated'] / total:.1%} of cropland)")
        flags.append("; ".join(f) or None)
    return pd.Series(flags, index=out.index)
