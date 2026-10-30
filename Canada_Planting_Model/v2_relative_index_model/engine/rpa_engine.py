"""Variable costs and Return per Acre (SPECIFICATION_V2.md 3.3 and 4 step 1).

VC = Seed + Fertilizer + Chemicals ($/ac), identical across the three windows of a year:
    2019-2025  RPA_Calculator normalized history tables (AB soil zones averaged per [REGION-AVG-RULE])
    2026       RPA_Master_Calculator.xlsx, RPA_Summary_<prov>
SK CPSR is costed on the SK CWRS budget (COST_PROXY_CWRS). ``validate_costs`` recomputes the
workbook's Historical_Ratios for the in-scope pairs (Canola/CWRS, CWRS/Durum) and raises on any
mismatch, so the cost lines are tied to the master workbook.

RPA = trend yield x prompt price - VC; RPA <= 0 is flagged NONPOSITIVE_RPA.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from openpyxl import load_workbook

import config as C


def _budget_key(prov: str, crop: str) -> tuple[str, bool]:
    if crop == "CPSR" and prov in C.CPS_COST_PROXY:
        return C.CPS_COST_PROXY[prov], True
    return C.CROPS[crop]["budget"], False


def _history_rows(prov: str) -> pd.DataFrame:
    """Normalized history for one province: year, budget key, seed, fertilizer, chemical, guide yield/price."""
    d = pd.read_csv(C.RPA_HISTORY[prov], encoding="utf-8-sig")
    if prov == "SK":
        return pd.DataFrame({"year": d["Year"], "key": d["Crop Key"], "seed": d["Seed Total ($/ac)"],
                             "fertilizer": d["Fertilizer Total ($/ac)"], "chemical": d["Plant Protection Total ($/ac)"],
                             "guide_yield": d["Target Yield (bu/ac)"], "guide_price": d["Farm Price ($/bu)"]})
    if prov == "AB":
        d = d[d["zone"].isin(C.ACTIVE_ZONES)]
        d = d[~(d["crop_key"].isin(C.SOUTHERN_BUDGETS) & ~d["zone"].isin(C.SOUTHERN_ZONES))]
        g = d.groupby(["year", "crop_key"]).agg(seed=("seed", "mean"), fertilizer=("fertilizer", "mean"),
                                                chemical=("chemical", "mean"), guide_yield=("yield_", "mean"),
                                                guide_price=("price", "mean")).reset_index()
        return g.rename(columns={"crop_key": "key"})
    return pd.DataFrame({"year": d["year"], "key": d["crop_key"], "seed": d["seed"], "fertilizer": d["fertilizer"],
                         "chemical": d["chemical"], "guide_yield": d["yield_"], "guide_price": d["price"]})


def _workbook_rows() -> pd.DataFrame:
    wb = load_workbook(C.RPA_WORKBOOK, data_only=True, read_only=True)
    out = []
    try:
        for prov in C.PROVINCES:
            rows = list(wb[f"RPA_Summary_{prov}"].iter_rows(values_only=True))
            header = next(r for r in rows if r and r[0] == "Metric")
            m = {r[0]: r for r in rows if r and isinstance(r[0], str)}
            for j, label in enumerate(header):
                name = str(label or "").replace("†", "").strip()
                out.append({"province": prov, "label": name, "seed": m["Seed Cost"][j],
                            "fertilizer": m["Fertilizer Cost"][j], "chemical": m["Chemical Cost"][j]})
    finally:
        wb.close()
    return pd.DataFrame(out)


def load_costs() -> pd.DataFrame:
    """Province x crop x year cost lines (2019-2026) for every in-scope crop."""
    wb = _workbook_rows()
    hist = {p: _history_rows(p) for p in C.PROVINCES}
    rows = []
    for prov, crops in C.PROVINCE_CROPS.items():
        for crop in crops:
            key, proxy = _budget_key(prov, crop)
            h = hist[prov][hist[prov]["key"] == key].set_index("year")
            for year in C.HISTORY_COST_YEARS:
                if year in h.index:
                    r = h.loc[year]
                    rows.append({"province": prov, "crop": crop, "year": year, "seed": r["seed"],
                                 "fertilizer": r["fertilizer"], "chemical": r["chemical"],
                                 "cost_source": f"history:{key}", "proxy": proxy})
            label = "CWRS Wheat" if proxy else C.CROPS[crop]["workbook"]
            w = wb[(wb["province"] == prov) & (wb["label"] == label)]
            if not w.empty:
                r = w.iloc[0]
                rows.append({"province": prov, "crop": crop, "year": C.WORKBOOK_COST_YEAR, "seed": r["seed"],
                             "fertilizer": r["fertilizer"], "chemical": r["chemical"],
                             "cost_source": f"workbook:{label}", "proxy": proxy})
    costs = pd.DataFrame(rows)
    costs["vc"] = costs[["seed", "fertilizer", "chemical"]].sum(axis=1, min_count=3)
    costs["cost_flags"] = np.where(costs["proxy"], "COST_PROXY_CWRS", None)
    return costs


def validate_costs() -> pd.DataFrame:
    """Recompute Historical_Ratios (Canola/CWRS, CWRS/Durum; 2019-2025) from the history cost lines.

    Workbook rule: guide price x whole target yield (half-up) - seed - fertilizer - chemical.
    """
    wb = load_workbook(C.RPA_WORKBOOK, data_only=True, read_only=True)
    try:
        rows = list(wb["Historical_Ratios"].iter_rows(values_only=True))
    finally:
        wb.close()
    header = next(r for r in rows if r and r[0] == "Base")
    cols = {int(h): j for j, h in enumerate(header) if str(h).isdigit() and int(h) in C.HISTORY_COST_YEARS}
    label_key = {"CWRS Wheat": "wheat_spring", "Canola": "canola", "Durum Wheat": "durum"}
    hist = {p: _history_rows(p).set_index(["year", "key"]) for p in C.PROVINCES}

    def guide_rpa(prov, year, key):
        r = hist[prov].loc[(year, key)]
        return math.floor(r["guide_yield"] + 0.5) * r["guide_price"] - (r["seed"] + r["fertilizer"] + r["chemical"])

    out = []
    for r in rows:
        if not r or r[1] is None or r[2] not in C.PROVINCES or " / " not in str(r[1]):
            continue
        num, den = (s.strip() for s in str(r[1]).split(" / "))
        if num not in label_key or den not in label_key:
            continue
        for year, j in cols.items():
            ours = guide_rpa(r[2], year, label_key[num]) / guide_rpa(r[2], year, label_key[den])
            out.append({"province": r[2], "ratio": r[1], "year": year, "workbook": r[j], "recomputed": ours,
                        "abs_diff": abs(r[j] - ours)})
    val = pd.DataFrame(out)
    if val.empty or (val["abs_diff"] > 1e-4).any():
        raise ValueError(f"Cost lines do not reproduce Historical_Ratios:\n{val[val['abs_diff'] > 1e-4]}")
    return val


def compute_rpa(prices: pd.DataFrame, yields: pd.DataFrame, costs: pd.DataFrame) -> pd.DataFrame:
    """Province x crop x run x year RPA panel."""
    p = (prices.merge(yields, on=["province", "crop", "year"], how="left")
               .merge(costs[["province", "crop", "year", "vc", "seed", "fertilizer", "chemical", "cost_source",
                             "cost_flags"]], on=["province", "crop", "year"], how="left"))
    p["revenue"] = p["trend_yield"] * p["price"]
    p["rpa"] = p["revenue"] - p["vc"]
    p["rpa_flags"] = np.where(p["rpa"] <= 0, "NONPOSITIVE_RPA", None)
    return p
