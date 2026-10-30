"""Base-100 index, relative index vs CWRS, YoY changes and the Prairie aggregate (SPECIFICATION_V2.md 4-5).

    Base_i,p,w    = mean RPA of the SAME window w over 2019-2024 (window-matched; >= MIN_BASE_YEARS valid
                    years and > 0, else UNSTABLE_BASE). Index 100 = normal for that point of the season.
    Index         = 100 x RPA / Base_i,p,w
    RelRPA        = RPA_i / RPA_CWRS                     (both > 0)
    RelIndex      = 100 x Index_i / Index_CWRS           (CWRS = 100)
    signal_ratio  = RelRPA; for CWRS = RPA_CWRS / RPA_Canola (CWRS_VS_CANOLA)
    pct_d_signal  = signal_ratio_T / signal_ratio_T-1 - 1 (same window)
    pct_d_acres   = Acres_T / Acres_T-1 - 1
Prairie: RPA weighted by fixed shares of 2019-2024 average seeded acres over the in-scope provinces
(re-normalized when a province is missing -> PARTIAL_AGGREGATE); then the same steps.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C

KEY = ["province", "crop", "run", "year"]


def _join_flags(*cols) -> list:
    out = []
    for vals in zip(*cols):
        f = [v for v in vals if isinstance(v, str) and v]
        out.append(";".join(dict.fromkeys(";".join(f).split(";"))) if f else None)
    return out


def bases(panel: pd.DataFrame) -> pd.DataFrame:
    """Window-matched base RPA per province x crop x window over BASE_YEARS."""
    ref = panel[panel["year"].isin(C.BASE_YEARS)]
    g = ref.groupby(["province", "crop", "run"])["rpa"].agg(base="mean", base_n="count").reset_index()
    g["base_window"] = g["run"]
    bad = (g["base_n"] < C.MIN_BASE_YEARS) | (g["base"] <= 0)
    g["base_flags"] = np.where(bad, "UNSTABLE_BASE", None)
    g.loc[bad, "base"] = np.nan
    return g


def add_indices(panel: pd.DataFrame) -> pd.DataFrame:
    """Index, RelRPA, RelIndex, CWRS signal and YoY changes for one geography set (provinces or Prairie)."""
    p = panel.merge(bases(panel), on=["province", "crop", "run"], how="left")
    pos = p["rpa"] > 0
    p["index"] = np.where(pos, 100 * p["rpa"] / p["base"], np.nan)

    bench = p[p["crop"] == C.BENCHMARK][["province", "run", "year", "rpa", "index"]].rename(
        columns={"rpa": "rpa_cwrs", "index": "index_cwrs"})
    comp = p[p["crop"] == C.CWRS_COMPETITOR][["province", "run", "year", "rpa"]].rename(columns={"rpa": "rpa_canola"})
    p = p.merge(bench, on=["province", "run", "year"], how="left").merge(comp, on=["province", "run", "year"], how="left")
    cw_pos = p["rpa_cwrs"] > 0
    p["rel_rpa"] = np.where(pos & cw_pos, p["rpa"] / p["rpa_cwrs"], np.nan)
    p["rel_index"] = np.where(p["index"].notna() & (p["index_cwrs"] > 0), 100 * p["index"] / p["index_cwrs"], np.nan)

    is_cwrs = p["crop"] == C.BENCHMARK
    cwrs_sig = np.where(cw_pos & (p["rpa_canola"] > 0), p["rpa_cwrs"] / p["rpa_canola"], np.nan)
    p["signal_ratio"] = np.where(is_cwrs, cwrs_sig, p["rel_rpa"])
    p["signal_flags"] = np.where(is_cwrs, "CWRS_VS_CANOLA", None)

    prev = p[KEY + ["signal_ratio"]].assign(year=p["year"] + 1).rename(columns={"signal_ratio": "signal_ratio_prev"})
    p = p.merge(prev, on=KEY, how="left")
    p["pct_d_signal"] = p["signal_ratio"] / p["signal_ratio_prev"] - 1
    return p.drop(columns=["rpa_canola"])


def prairie(panel: pd.DataFrame, acres: pd.DataFrame) -> pd.DataFrame:
    """Prairie aggregate rows (province = PRAIRIE) from the provincial RPA panel."""
    base_acres = (acres[acres["year"].isin(C.BASE_YEARS)].groupby(["province", "crop"])["acres"].mean()
                  .rename("w_acres").reset_index())
    in_scope = [(p, c) for p, crops in C.PROVINCE_CROPS.items() for c in crops]
    base_acres = base_acres[[tuple(x) in in_scope for x in base_acres[["province", "crop"]].to_numpy()]]
    rows = []
    for (crop, run, year), g in panel.merge(base_acres, on=["province", "crop"]).groupby(["crop", "run", "year"]):
        ok = g.dropna(subset=["rpa", "w_acres"])
        n_scope = sum(1 for p, c in in_scope if c == crop)
        rec = {"province": C.PRAIRIE, "crop": crop, "run": run, "year": year, "rpa": np.nan,
               "n_provinces": len(ok), "agg_flags": None}
        if len(ok):
            w = ok["w_acres"] / ok["w_acres"].sum()
            rec["rpa"] = float((w * ok["rpa"]).sum())
            rec["price"] = float((w * ok["price"]).sum()) if ok["price"].notna().all() else np.nan
            rec["weights"] = ", ".join(f"{p} {x:.3f}" for p, x in zip(ok["province"], w))
            if len(ok) < n_scope:
                rec["agg_flags"] = "PARTIAL_AGGREGATE"
        rows.append(rec)
    pr = pd.DataFrame(rows)
    pr["rpa_flags"] = np.where(pr["rpa"] <= 0, "NONPOSITIVE_RPA", None)
    tot = (acres[[tuple(x) in in_scope for x in acres[["province", "crop"]].to_numpy()]]
           .groupby(["crop", "year"]).agg(acres=("acres", lambda s: s.sum(min_count=1)),
                                          acres_prev=("acres_prev", lambda s: s.sum(min_count=1))).reset_index())
    tot["pct_d_acres"] = tot["acres"] / tot["acres_prev"] - 1
    return pr.merge(tot, on=["crop", "year"], how="left")


def build_index_panel(rpa_panel: pd.DataFrame, acres: pd.DataFrame, prelim_year: int) -> pd.DataFrame:
    """Provincial + Prairie panel with indices, signals, acreage changes and combined flags."""
    prov = rpa_panel.merge(acres, on=["province", "crop", "year"], how="left")
    prov = add_indices(prov)
    pr = add_indices(prairie(rpa_panel, acres))
    out = pd.concat([prov, pr], ignore_index=True)
    out["acres_flags"] = np.where(out["year"] == prelim_year, "PRELIMINARY_ACRES", None)
    flag_cols = [c for c in ("price_flags", "yield_flags", "cost_flags", "rpa_flags", "base_flags", "signal_flags",
                             "agg_flags", "acres_flags") if c in out]
    out["flags"] = _join_flags(*(out[c] for c in flag_cols))
    order = {p: i for i, p in enumerate([*C.PROVINCES, C.PRAIRIE])}
    out["_o"] = out["province"].map(order)
    out["_c"] = out["crop"].map({c: i for i, c in enumerate(C.CROP_ORDER)})
    return out.sort_values(["_o", "_c", "run", "year"]).drop(columns=["_o", "_c"]).reset_index(drop=True)
