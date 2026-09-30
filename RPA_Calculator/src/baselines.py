"""Province baselines on one schema: the aggregation layer between the regional parsers
and the scenario tools (market_engine.py, excel_generator.py).

The parsers stay faithful extractors of each guide. Everything that makes the two
provinces comparable happens here:

    * Alberta zone budgets are averaged (equal weight) over Brown / Dark Brown / Black only,
      narrowed per crop by config.ab_summary_zones (durum, red lentils, kabuli chickpeas:
      Brown + Dark Brown). Grey-Wooded, Peace Region and Irrigated never enter a baseline.
    * Fertilizer $/tonne -> $/lb nutrient uses one N-credit rule for both provinces
      (input_parser.per_lb): MAP and AS carry N, credited at the $/lb N.
    * Saskatchewan gains crops that have no guide history (config.SK_EXTRA_CROPS, e.g. CPS /
      CNHR wheat): they inherit NPKS, chemical and seed from a parent crop and start in the
      current engine year; they are never back-filled into the historical MVS.

Records follow the SK MVS schema (revenue / direct_costs_per_ac / agronomics / returns),
so the market engine prices both provinces the same way.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from config import (AB_CROPS, AB_OUTPUT_JSON, AB_ZONES, MVS_COMMODITY_ZONE_MAP, OUTPUT_JSON,
                    PULSE_LBS_PER_BU, SK_EXTRA_CROPS, ab_summary_zones)
from input_parser import LB_PER_TONNE, load_fertilizer_benchmarks, per_lb

NUTRIENT_KEYS = (("N", "n_lbs_ac", "fert_n"), ("P", "p_lbs_ac", "fert_p"),
                 ("K", "k_lbs_ac", "fert_k"), ("S", "s_lbs_ac", "fert_s"))
DEFAULT_K_PER_LB = 0.45    # SK guides budget no potash, so no implied K price
AB_DISPLAY_ORDER = ["wheat_spring", "wheat_cps", "durum", "canola", "barley_feed", "barley_malt", "oats",
                    "peas_yellow", "lentils_red", "chickpeas_kabuli"]


def _mean(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------------------------------------------------------------------------
# Fertilizer: one N-credit rule for both provinces
# ---------------------------------------------------------------------------
def quotes_from_per_lb(prices: dict[str, float], province: str) -> dict[str, float]:
    """$/lb nutrient -> retail $/tonne quotes (inverse of input_parser.per_lb)."""
    bench = load_fertilizer_benchmarks(province)
    n = prices["N"]
    return {k: (prices[k] * bench[k]["grade"] + bench[k]["n_credit"] * n) * LB_PER_TONNE
            for k in ("N", "P", "K", "S")}


def sk_implied_per_lb(records: list[dict]) -> dict[str, float]:
    """Mean guide $/lb of nutrient across SK crops (fertilizer $/ac / lb/ac)."""
    out = {}
    for key, rate_key, cost_key in NUTRIENT_KEYS:
        ratios = [r["direct_costs_per_ac"][cost_key] / r["agronomics"][rate_key]
                  for r in records if r["agronomics"].get(rate_key)]
        out[key] = sum(ratios) / len(ratios) if ratios else DEFAULT_K_PER_LB
    return out


def sk_last_yr_quotes(records: list[dict]) -> dict[str, float]:
    """SK Last Yr retail quotes ($/tonne, to the cent) that reproduce the guide's implied
    $/lb under the standardized N-credit conversion."""
    return {k: round(v, 2) for k, v in quotes_from_per_lb(sk_implied_per_lb(records), "SK").items()}


def ab_guide_quotes(year: int, path: Path = AB_OUTPUT_JSON) -> dict[str, dict]:
    """The AB guide's published product quotes for `year`: {N: {per_tonne, per_lb, product}}."""
    return load_ab(path)["meta"]["fert_prices"][str(year)]


def ab_last_yr_per_lb(year: int, path: Path = AB_OUTPUT_JSON) -> dict[str, float]:
    quotes = {k: v["per_tonne"] for k, v in ab_guide_quotes(year, path).items()}
    return per_lb(quotes, load_fertilizer_benchmarks("AB"))


# ---------------------------------------------------------------------------
# Saskatchewan
# ---------------------------------------------------------------------------
def load_sk_mvs(path: Path = OUTPUT_JSON) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"MVS database not found: {path} (run src/normalizer.py)")
    return json.loads(path.read_text(encoding="utf-8"))


def extra_crop_record(name: str, parent: dict, year: int) -> dict:
    """An SK crop with no guide history, built on its parent's baseline (NPKS, chemical,
    seed). Yield and price start at the parent's until a bid / yield is entered."""
    spec = SK_EXTRA_CROPS[name]
    rec = copy.deepcopy(parent)
    rec.update(commodity=name, crop_key=spec["crop_key"], variety=spec["variety"], price_basis=spec["price_basis"],
               synthetic=True, inherits=spec["inherits"], zone_detail={}, source={"inherits": spec["inherits"]},
               notes=[f"no guide history; NPKS, chemical and seed inherited from {spec['inherits']} "
                      f"({year} guide); price and yield mirror {spec['inherits']} until a {name} bid / "
                      "target yield is entered"])
    return rec


def sk_records(year: int, path: Path = OUTPUT_JSON, extras: bool = True) -> dict[str, dict]:
    """SK MVS records for `year` keyed by commodity, plus SK_EXTRA_CROPS from their start
    year (inserted right after their parent)."""
    data = load_sk_mvs(path)
    recs = {r["commodity"]: r for r in data["records"] if r["year"] == year}
    if not recs:
        raise ValueError(f"no SK MVS records for {year}; available: {data['meta']['years']}")
    out: dict[str, dict] = {}
    for commodity in MVS_COMMODITY_ZONE_MAP:
        if commodity not in recs:
            continue
        out[commodity] = {**recs[commodity], "crop_key": MVS_COMMODITY_ZONE_MAP[commodity]["crop_key"]}
        for name, spec in SK_EXTRA_CROPS.items():
            if extras and spec["inherits"] == commodity and year >= spec["start_year"]:
                out[name] = extra_crop_record(name, out[commodity], year)
    return out


# ---------------------------------------------------------------------------
# Alberta
# ---------------------------------------------------------------------------
def load_ab(path: Path = AB_OUTPUT_JSON) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"AB cost-of-production data not found: {path} (run python -m src.pdf_parser_ab)")
    return json.loads(path.read_text(encoding="utf-8"))


def ab_zone_averages(year: int, path: Path = AB_OUTPUT_JSON) -> list[dict]:
    """Per AB crop: simple average of its summary zones (exact, unrounded).

    yield / price are native: bu/ac and $/bu, or lb/ac and c/lb for pulses (peas are
    printed in bu and $/bu and converted at 60 lb/bu).
    """
    recs = [r for r in load_ab(path)["records"] if r["year"] == year]
    out = []
    for key in AB_DISPLAY_ORDER + [k for k in AB_CROPS if k not in AB_DISPLAY_ORDER]:
        spec, zones = AB_CROPS[key], ab_summary_zones(key)
        zr = sorted((r for r in recs if r["crop_key"] == key and r["zone"] in zones),
                    key=lambda r: zones.index(r["zone"]))
        if not zr:
            continue
        pulse = spec["price_basis"] == "c/lb"

        def y_native(r: dict) -> float | None:
            if r["yield_"] is None:
                return None
            if not pulse or r["yield_unit"] == "lb/ac":
                return r["yield_"]
            return r["yield_"] * PULSE_LBS_PER_BU

        def p_native(r: dict) -> float | None:
            if r["price"] is None:
                return None
            if not pulse:
                return r["price"]
            return r["price"] * 100 if r["price_unit"] == "$/lb" else r["price"] / PULSE_LBS_PER_BU * 100

        rates_bu = [r["seeding_rate"] for r in zr if r.get("seeding_rate_unit") == "bu/ac"]
        out.append({
            "crop_key": key, "display": spec["display"], "pulse": pulse, "unit": spec["price_basis"],
            "zones": [r["zone"] for r in zr], "zone_names": [AB_ZONES[r["zone"]] for r in zr],
            "grades": ", ".join(dict.fromkeys(r["grade"] for r in zr if r["grade"])),
            "yield": _mean(y_native(r) for r in zr), "price": _mean(p_native(r) for r in zr),
            "seed": _mean(r["seed"] for r in zr), "chem": _mean(r["chemical"] for r in zr),
            "rates": {"N": _mean(r["n_lbs_ac"] for r in zr), "P": _mean(r["p2o5_lbs_ac"] for r in zr),
                      "K": _mean(r["k2o_lbs_ac"] for r in zr), "S": _mean(r["s_lbs_ac"] for r in zr)},
            "seeding_lbs": _mean(r["seeding_rate_lbs_ac"] for r in zr),
            "seeding_bu": _mean(rates_bu), "bu_weight": spec["bu_weight"],
            "guide_fertilizer": _mean(r["fertilizer"] for r in zr),
            "flags": [f"{AB_ZONES[r['zone']]}: {n}" for r in zr for n in r["notes"]
                      if n.startswith(("unverified", "core field", "seed $"))],
        })
    return out


def ab_records(year: int, path: Path = AB_OUTPUT_JSON) -> dict[str, dict]:
    """AB zone averages as MVS-schema records (keyed by display name) for the engine.
    Fertilizer $ per nutrient = lb/ac x the guide's $/tonne quotes under the standardized
    N-credit conversion."""
    prices = ab_last_yr_per_lb(year, path)
    out = {}
    for a in ab_zone_averages(year, path):
        pulse = a["pulse"]
        yield_bu = a["yield"] / PULSE_LBS_PER_BU if pulse else a["yield"]
        price_bu = a["price"] / 100 * PULSE_LBS_PER_BU if pulse else a["price"]
        fert = {ck: (a["rates"][k] or 0.0) * prices[k] for k, _, ck in NUTRIENT_KEYS}
        fert_total = sum(fert.values())
        d = a["seed"] + fert_total + a["chem"]
        c = yield_bu * price_bu
        out[a["display"]] = {
            "year": year, "commodity": a["display"], "crop_key": a["crop_key"], "variety": a["grades"],
            "price_basis": a["unit"], "province": "AB", "soil_zones": a["zone_names"], "zones_used": a["zones"],
            "method": "blend" if len(a["zones"]) > 1 else "single",
            "revenue": {"price_basis": a["unit"], "target_yield_bu_ac": yield_bu,
                        "target_yield_lbs_ac": a["yield"] if pulse else None,
                        "farm_price_per_bu": price_bu, "farm_price_cents_lb": a["price"] if pulse else None,
                        "gross_revenue": c},
            "direct_costs_per_ac": {"seed_base": a["seed"], "seed_treatment": 0.0, "seed_total": a["seed"],
                                    **fert, "fert_total": fert_total, "protection_total": a["chem"],
                                    "direct_cash_inputs": d},
            "agronomics": {"seeding_rate_lbs_ac": a["seeding_lbs"], "seeding_rate_other": None,
                           "seeding_rate_basis": "AB Table 1", "n_lbs_ac": a["rates"]["N"],
                           "p_lbs_ac": a["rates"]["P"], "k_lbs_ac": a["rates"]["K"], "s_lbs_ac": a["rates"]["S"]},
            "returns": {"return_over_direct": c - d},
            "notes": ["AB seed $ includes cleaning & treatment (no separate treatment line)"] + a["flags"],
        }
    return out


def province_records(province: str, year: int) -> dict[str, dict]:
    province = province.upper()
    if province == "SK":
        return sk_records(year)
    if province == "AB":
        return ab_records(year)
    raise ValueError(f"province must be SK or AB, got {province!r}")
