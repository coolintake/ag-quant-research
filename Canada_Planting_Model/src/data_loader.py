"""Factual ingestion for the acreage calibration (model_crop_wallet_step1.md sections 2, 4, 5).

Sources, all read-only:

1. StatCan 32-10-0359 - provincial seeded acres (acres; hectares x 2.47105 only where no acre
   series exists) and yields (bu/ac; Lentils lb/ac as CANSIM publishes them, never converted).
   Expected yield (5): 21-year OLS trend on T-21 .. T-1 evaluated at T (>= 10 points), else the
   T-5 .. T-1 mean flagged INSUFFICIENT_YIELD_HISTORY.
2. RPA_Calculator - variable costs = seed + fertilizer + chemicals ($/ac) [DCASH]:
     2026      RPA_Master_Calculator.xlsx, RPA_Summary_<prov>
     2019-2025 the normalized history tables output/<prov>/mvs/*_historical_2019_2026.csv
   ``validate_against_workbook`` recomputes the workbook's Historical_Ratios sheet from the
   2019-2025 cost lines and stops on any mismatch. No OCR output or raw guide tables are read.
3. Prices - ``prompt_prices``: PDQ prompt bids, then template, guide price, NULL (4.1-4.3).

Nothing is interpolated or fitted beyond the yield trend the spec defines.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd
from openpyxl import load_workbook

import prompt_prices
import settings as S

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1. StatCan 32-10-0359
# --------------------------------------------------------------------------- #
def load_statcan(first_year: int = S.STATCAN_FIRST_YEAR) -> pd.DataFrame:
    """Seeded acres and yield (model unit) per province x year x crop, from ``first_year``."""
    raw = pd.read_csv(S.STATCAN_CSV, low_memory=False,
                      usecols=["REF_DATE", "GEO", "Harvest disposition", "Type of crop", "VALUE"])
    geo_to_prov = {v: k for k, v in S.PROVINCES.items()}
    name_to_crop = {c["statcan"]: k for k, c in S.CROPS.items()}
    raw = raw[raw["GEO"].isin(geo_to_prov) & raw["Type of crop"].isin(name_to_crop) & (raw["REF_DATE"] >= first_year)]
    raw = raw.assign(province=raw["GEO"].map(geo_to_prov), crop=raw["Type of crop"].map(name_to_crop),
                     year=raw["REF_DATE"].astype(int))
    wide = raw.pivot_table(index=["province", "year", "crop"], columns="Harvest disposition",
                           values="VALUE", aggfunc="first").reset_index()

    def col(name: str) -> pd.Series:
        return wide[name] if name in wide else pd.Series(np.nan, index=wide.index)

    acres = col("Seeded area (acres)")
    wide["acres"] = acres.where(acres.notna(), col("Seeded area (hectares)") * S.HA_TO_AC)
    wide["acres_source"] = np.where(acres.notna(), "acres",
                                    np.where(wide["acres"].notna(), "hectares x 2.47105", None))

    kg_ha, bu_ac, lb_ac = (col("Average yield (kilograms per hectare)"),
                           col("Average yield (bushels per acre)"), col("Average yield (pounds per acre)"))
    yields, units = [], []
    for i, crop in enumerate(wide["crop"]):
        spec = S.CROPS[crop]
        if spec["unit"] == "lb":      # Lentils: CANSIM lb/ac, kept in lb/ac to match the $/lb guide price
            v = lb_ac.iat[i] if pd.notna(lb_ac.iat[i]) else kg_ha.iat[i] * S.LB_PER_KG / S.HA_TO_AC
            units.append("lb/ac")
        else:
            v = bu_ac.iat[i]
            if pd.isna(v) and pd.notna(kg_ha.iat[i]):
                v = kg_ha.iat[i] / S.HA_TO_AC / S.KG_PER_BU[spec["bushel"]]
            units.append("bu/ac")
        yields.append(v)
    # StatCan reports 0 (not "..") when nothing was seeded, e.g. AB Lentils 2006: 0 ac, 0 lb/ac.
    # A zero yield is a non-observation, not a crop failure, so it is NULL (never a trend point).
    wide["yield"] = [np.nan if (v is not None and v == 0) else v for v in yields]
    wide["yield_unit"] = units
    return wide[["province", "year", "crop", "acres", "acres_source", "yield", "yield_unit"]].sort_values(
        ["province", "crop", "year"], ignore_index=True)


def expected_yields(statcan: pd.DataFrame, years=S.YEARS) -> pd.DataFrame:
    """Expected yield per province x crop x target year T (spec 5).

    Trend: OLS Yield = g0 + g1 * Year on the non-NULL actuals of T-21 .. T-1, evaluated at T,
    when at least YIELD_TREND_MIN_N points exist. Otherwise the mean of T-5 .. T-1 (at least
    YIELD_SMA_MIN_N points, else NULL), flagged INSUFFICIENT_YIELD_HISTORY. Each calibration
    year gets its own trend, fitted only on yields published before it.
    """
    comp = yield_components(statcan, years)
    use_trend = comp["trend_n"] >= S.YIELD_TREND_MIN_N
    return pd.DataFrame({
        "province": comp["province"], "crop": comp["crop"], "year": comp["year"],
        "exp_yield": np.where(use_trend, comp["trend_yield"], comp["sma_yield"]),
        "yield_method": np.where(use_trend, "TREND_21Y", "SMA_5Y"),
        "yield_n": np.where(use_trend, comp["trend_n"], comp["sma_n"]),
        "trend_slope": comp["trend_slope"],
        "yield_flag": np.where(use_trend, None, "INSUFFICIENT_YIELD_HISTORY"),
    })


def yield_components(statcan: pd.DataFrame, years) -> pd.DataFrame:
    """Both expected-yield candidates for every province x crop x year T, plus the actual.

    trend_yield: OLS on the non-NULL actuals of T-21 .. T-1 evaluated at T (NaN when fewer than
    YIELD_TREND_MIN_N points). sma_yield: mean of T-5 .. T-1 (NaN when fewer than YIELD_SMA_MIN_N).
    """
    out = []
    for (prov, crop), g in statcan.groupby(["province", "crop"]):
        by_year = g.dropna(subset=["yield"]).set_index("year")["yield"]
        for t in years:
            hist = by_year[(by_year.index >= t - S.YIELD_TREND_YEARS) & (by_year.index <= t - 1)]
            sma = by_year[(by_year.index >= t - S.YIELD_SMA_YEARS) & (by_year.index <= t - 1)]
            trend, slope = np.nan, np.nan
            if len(hist) >= S.YIELD_TREND_MIN_N:
                slope, intercept = np.polyfit(hist.index.to_numpy(float), hist.to_numpy(float), 1)
                trend = intercept + slope * t
            out.append({"province": prov, "crop": crop, "year": t,
                        "actual": by_year.get(t, np.nan),
                        "trend_yield": trend, "trend_n": len(hist), "trend_slope": slope,
                        "sma_yield": sma.mean() if len(sma) >= S.YIELD_SMA_MIN_N else np.nan,
                        "sma_n": len(sma)})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# 2. Variable costs
# --------------------------------------------------------------------------- #
def _to_model_price(price: float, unit: str, model_unit: str) -> float:
    """Guide price -> $/bu (peas at 60 lb/bu) or $/lb (lentils)."""
    unit = (unit or "").replace("¢", "c").strip().lower()
    per_lb = {"$/lb": price, "c/lb": price / 100.0}.get(unit)
    if model_unit == "lb":
        return per_lb if per_lb is not None else price / 60.0
    return per_lb * 60.0 if per_lb is not None else price


PULSE_KEYS = frozenset({"peas_yellow", "lentils_red"})   # [CONV] pulses: lb/ac and c/lb


def load_history_costs() -> pd.DataFrame:
    """2019-2026 guide budgets from the normalized history tables (2026 rows kept for the cross-check).

    Columns: province, year, crop, seed, fertilizer, chemical, budget_price (model unit),
    guide_yield / guide_unit_price (guide-native units, for the workbook validation).
    """
    key_to_crop = {c["budget"]: k for k, c in S.CROPS.items()}
    rec: list[dict] = []

    sk = pd.read_csv(S.RPA_HISTORY["SK"], encoding="utf-8-sig")
    for _, r in sk[sk["Crop Key"].isin(key_to_crop)].iterrows():
        crop = key_to_crop[r["Crop Key"]]
        pulse = r["Price Basis"] == "c/lb"
        unit_price = r["Farm Price (c/lb)"] / 100.0 if pulse else r["Farm Price ($/bu)"]
        rec.append({"province": "SK", "year": int(r["Year"]), "crop": crop,
                    "seed": r["Seed Total ($/ac)"], "fertilizer": r["Fertilizer Total ($/ac)"],
                    "chemical": r["Plant Protection Total ($/ac)"],
                    "budget_price": _to_model_price(unit_price, "$/lb" if pulse else "$/bu", S.CROPS[crop]["unit"]),
                    "guide_yield": r["Target Yield (lb/ac)"] if pulse else r["Target Yield (bu/ac)"],
                    "guide_unit_price": unit_price, "zones": r["Zones Used"]})

    # AB history is per soil zone: average per [REGION-AVG-RULE] (durum/lentils Brown + Dark Brown).
    ab = pd.read_csv(S.RPA_HISTORY["AB"], encoding="utf-8-sig")
    ab = ab[ab["crop_key"].isin(key_to_crop) & ab["zone"].isin(S.ACTIVE_ZONES)]
    ab = ab[~(ab["crop_key"].isin(S.SOUTHERN_CROPS) & ~ab["zone"].isin(S.SOUTHERN_ZONES))]
    for (year, key), g in ab.groupby(["year", "crop_key"]):
        crop = key_to_crop[key]
        if g["yield_unit"].nunique() > 1 or g["price_unit"].nunique() > 1:
            raise ValueError(f"AB {year} {key}: mixed units across zones {g[['zone', 'yield_unit', 'price_unit']]}")
        y_unit, p_unit = g["yield_unit"].iat[0], g["price_unit"].iat[0]
        price = g["price"].mean()
        # Guide-native basis for the workbook check: pulses in lb/ac and $/lb (60 lb/bu), others bu/ac, $/bu.
        y_lb = g["yield_"].mean() * (60.0 if y_unit == "bu/ac" else 1.0)
        p_lb = price / 60.0 if p_unit == "$/bu" else price
        if key in PULSE_KEYS:
            guide_yield, unit_price = y_lb, p_lb
        else:
            guide_yield, unit_price = y_lb / 60.0 if y_unit == "lb/ac" else g["yield_"].mean(), p_lb * 60.0
        rec.append({"province": "AB", "year": int(year), "crop": crop,
                    "seed": g["seed"].mean(), "fertilizer": g["fertilizer"].mean(), "chemical": g["chemical"].mean(),
                    "budget_price": _to_model_price(price, p_unit, S.CROPS[crop]["unit"]),
                    "guide_yield": guide_yield, "guide_unit_price": unit_price,
                    "zones": ", ".join(g["zone"])})

    mb = pd.read_csv(S.RPA_HISTORY["MB"], encoding="utf-8-sig")
    for _, r in mb[mb["crop_key"].isin(key_to_crop)].iterrows():
        crop = key_to_crop[r["crop_key"]]
        rec.append({"province": "MB", "year": int(r["year"]), "crop": crop,
                    "seed": r["seed"], "fertilizer": r["fertilizer"], "chemical": r["chemical"],
                    "budget_price": _to_model_price(r["price"], r["price_unit"], S.CROPS[crop]["unit"]),
                    # [MB-PEAS-BU]: the workbook carries MB peas in lb/ac and c/lb
                    "guide_yield": r["yield_"] * 60.0 if r["crop_key"] in PULSE_KEYS else r["yield_"],
                    "guide_unit_price": r["price"] / 60.0 if r["crop_key"] in PULSE_KEYS else r["price"],
                    "zones": "Province-Wide"})

    out = pd.DataFrame(rec)
    out["source"] = "RPA_Calculator normalized history (mvs)"
    return out


def _sheet_rows(ws) -> list[tuple]:
    return [r for r in ws.iter_rows(values_only=True)]


def load_workbook_2026() -> pd.DataFrame:
    """2026 seed / fertilizer / chemical / farm price per province from RPA_Summary_<prov>."""
    label_to_crop = {c["workbook"]: k for k, c in S.CROPS.items()}
    wb = load_workbook(S.RPA_WORKBOOK, data_only=True, read_only=True)
    rec: list[dict] = []
    try:
        for prov in S.PROVINCES:
            rows = _sheet_rows(wb[f"RPA_Summary_{prov}"])
            header = next(r for r in rows if r and r[0] == "Metric")
            metrics = {r[0]: r for r in rows if r and isinstance(r[0], str)}
            units_row = metrics["Farm Price"][1] or ""
            for j, label in enumerate(header):
                name = str(label or "").replace("†", "").strip()
                if j < 2 or name not in label_to_crop:
                    continue
                crop = label_to_crop[name]
                pulse = "†" in str(label)      # dagger = pulse, price in c/lb and yield in lb/ac
                price = metrics["Farm Price"][j]
                rec.append({"province": prov, "year": S.WORKBOOK_YEAR, "crop": crop,
                            "seed": metrics["Seed Cost"][j], "fertilizer": metrics["Fertilizer Cost"][j],
                            "chemical": metrics["Chemical Cost"][j],
                            "budget_price": _to_model_price(price, "c/lb" if pulse else "$/bu", S.CROPS[crop]["unit"]),
                            "guide_yield": metrics["Target Yield"][j],
                            "guide_unit_price": price / 100.0 if pulse else price,
                            "zones": None, "source": f"RPA_Master_Calculator.xlsx RPA_Summary_{prov} ({units_row})"})
    finally:
        wb.close()
    return pd.DataFrame(rec)


def load_costs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Model cost table (2019-2025 history + 2026 workbook) and the 2026 history-vs-workbook cross-check."""
    hist = load_history_costs()
    wb26 = load_workbook_2026()
    check = hist[hist["year"] == S.WORKBOOK_YEAR].merge(
        wb26, on=["province", "year", "crop"], how="outer", suffixes=("_history", "_workbook"))
    for f in ("seed", "fertilizer", "chemical", "budget_price"):
        check[f"{f}_diff"] = check[f"{f}_workbook"] - check[f"{f}_history"]
    costs = pd.concat([hist[hist["year"].isin(S.HISTORY_YEARS)], wb26], ignore_index=True)
    costs["variable_cost"] = costs[["seed", "fertilizer", "chemical"]].sum(axis=1, min_count=3)
    return costs.sort_values(["province", "crop", "year"], ignore_index=True), check


def _round_half_up(x: float) -> float:
    return math.floor(x + 0.5)


def validate_against_workbook(costs: pd.DataFrame) -> pd.DataFrame:
    """Recompute the workbook's Historical_Ratios (2019-2025) from the cost table.

    The workbook ratio is guide price x whole target yield [YIELD-ROUND] - seed - fertilizer -
    chemical, numerator / base. Every published ratio must match to 1e-4, or ingestion stops.
    """
    label_to_crop = {c["workbook"]: k for k, c in S.CROPS.items()}
    wb = load_workbook(S.RPA_WORKBOOK, data_only=True, read_only=True)
    try:
        rows = _sheet_rows(wb["Historical_Ratios"])
    finally:
        wb.close()
    header = next(r for r in rows if r and r[0] == "Base")
    year_cols = {int(h): j for j, h in enumerate(header) if str(h).isdigit() and int(h) in S.HISTORY_YEARS}

    c = costs[costs["year"].isin(S.HISTORY_YEARS)].copy()
    c["guide_rpa"] = [(_round_half_up(y) * p) - v for y, p, v in
                      zip(c["guide_yield"], c["guide_unit_price"], c["variable_cost"])]
    rpa = c.set_index(["province", "year", "crop"])["guide_rpa"]

    out = []
    for r in rows:
        if not r or r[1] is None or " / " not in str(r[1]) or r[2] not in S.PROVINCES:
            continue
        num, den = (s.strip() for s in str(r[1]).split(" / "))
        if num not in label_to_crop or den not in label_to_crop:
            continue
        for year, j in year_cols.items():
            wb_val = r[j] if isinstance(r[j], (int, float)) else np.nan
            try:
                ours = rpa[(r[2], year, label_to_crop[num])] / rpa[(r[2], year, label_to_crop[den])]
            except KeyError:
                ours = np.nan
            out.append({"province": r[2], "ratio": r[1], "year": year, "workbook": wb_val, "recomputed": ours,
                        "abs_diff": abs(wb_val - ours) if pd.notna(wb_val) and pd.notna(ours) else np.nan})
    val = pd.DataFrame(out)
    bad = val[(val["abs_diff"] > 1e-4) | (val["workbook"].notna() != val["recomputed"].notna())]
    if not bad.empty:
        raise ValueError(f"Cost lines do not reproduce RPA_Master_Calculator Historical_Ratios:\n{bad}")
    return val


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #
def build_panel(years=S.YEARS) -> dict[str, pd.DataFrame]:
    """All inputs, keyed: panel (province x year x run x crop), statcan, yields, costs, cost_check,
    workbook_validation, prices, prompt."""
    statcan = load_statcan()
    costs, cost_check = load_costs()
    validation = validate_against_workbook(costs)
    yields = expected_yields(statcan, years)
    guide = costs[["province", "year", "crop", "budget_price"]].rename(columns={"budget_price": "guide_price"})
    prices, prompt = prompt_prices.build_prices(guide, years)

    acres = statcan[["province", "year", "crop", "acres", "acres_source"]]
    prev = acres.assign(year=acres["year"] + 1).rename(columns={"acres": "acres_prev"})[
        ["province", "year", "crop", "acres_prev"]]
    base = pd.MultiIndex.from_product([list(S.PROVINCES), years, list(S.CROPS)],
                                      names=["province", "year", "crop"]).to_frame(index=False)
    base = (base.merge(acres, how="left").merge(prev, how="left").merge(yields, how="left")
                .merge(costs[["province", "year", "crop", "seed", "fertilizer", "chemical", "variable_cost",
                              "source"]].rename(columns={"source": "cost_source"}), how="left"))
    panel = base.merge(prices.rename(columns={"tier": "price_source", "detail": "price_detail"}),
                       on=["province", "year", "crop"], how="left")
    panel = panel.sort_values(["province", "run", "crop", "year"], ignore_index=True)
    panel["revenue"] = panel["exp_yield"] * panel["price"]
    panel["rpa"] = panel["revenue"] - panel["variable_cost"]
    return {"panel": panel, "statcan": statcan, "yields": yields, "costs": costs, "cost_check": cost_check,
            "workbook_validation": validation, "prices": prices, "prompt": prompt}
