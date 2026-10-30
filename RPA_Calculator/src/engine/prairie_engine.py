"""Tier 2 Prairie aggregator: acreage-weighted Prairie benchmark across MB, SK and AB [PRAIRIE-WEIGHTED-RPA].

Ingests the provincial CropBaseline economics (Last Yr, or any scenario's results) and applies the
StatCan 32-10-0359 three-year average seeded-acreage weights (docs/AGENT_RULES.json
prairie_acreage_weights, built by src/pricing/statcan_weights.py). Per crop:

    w_p        = acreage weight_p / coverage, over the provinces with a guide budget for the crop
    coverage   = sum of acreage weight_p over those provinces (share of Prairie acres represented)
    Yield      = sum w_p x Yield_p              (whole guide targets; lb/ac for pulses)
    Price      = sum w_p x Price_p              ($/bu, or c/lb for pulses)
    C          = Yield x Price                  (pulses / 100)
    Seed, Fert, Chem = sum w_p x provincial $/ac  (Fert = each province's N-credited NPKS cost)
    D          = Seed + Fert + Chem;  RPA = C - D;  breakevens D / Yield and D / Price

    python -m src.engine.prairie_engine [--year 2026]
"""

from __future__ import annotations

import argparse
import json

from ..config import BASELINE_YEAR, PROVINCES, RULES, RULES_PATH, crop
from ..normalize.baselines import province_baselines
from .market import last_yr

ENGINE_VERSION = "1.0.0"
SUM_KEYS = ("yield", "price", "seed", "fert", "chem")


def load_weights() -> dict:
    """The rules file's prairie_acreage_weights block, read fresh (the pipeline may just have rebuilt it)."""
    block = json.loads(RULES_PATH.read_text(encoding="utf-8")).get("prairie_acreage_weights")
    if not block:
        raise KeyError("docs/AGENT_RULES.json has no prairie_acreage_weights (run python -m src.pricing.statcan_weights)")
    return block


def provincial_last_yr(year: int = BASELINE_YEAR) -> dict[str, dict[str, dict]]:
    """{code: {crop_key: economics}} at each province's Last Yr inputs."""
    return {code: {b.crop_key: last_yr(b) for b in province_baselines(code, year)} for code in PROVINCES}


def aggregate(econ: dict[str, dict[str, dict]], weights: dict | None = None) -> dict:
    """Prairie benchmark from provincial economics {code: {crop_key: {yield, price, seed, fert, chem, ...}}}."""
    block = weights or load_weights()
    table = block["crops"]
    out: dict[str, dict] = {}
    for key in RULES.crops:
        present = [code for code in PROVINCES if key in econ.get(code, {})]
        if not present:
            continue
        raw = (table.get(key) or {}).get("weights", {})
        coverage = sum(raw.get(c, 0.0) for c in present)
        w = ({c: raw.get(c, 0.0) / coverage for c in present} if coverage > 0
             else {c: 1 / len(present) for c in present})
        row = {k: sum(w[c] * econ[c][key][k] for c in present) for k in SUM_KEYS}
        c = crop(key)
        scale = 100 if c.pulse else 1
        row["revenue"] = row["yield"] * row["price"] / scale
        row["cost"] = row["seed"] + row["fert"] + row["chem"]
        row["rpa"] = row["revenue"] - row["cost"]
        row["be_price"] = row["cost"] / row["yield"] * scale if row["yield"] else None
        row["be_yield"] = row["cost"] / (row["price"] / scale) if row["price"] else None
        row.update(crop_key=key, trade_label=c.trade_label, price_basis=c.price_basis, coverage=coverage,
                   weights=w, acreage_weights={p: raw.get(p, 0.0) for p in PROVINCES},
                   provinces={p: econ[p][key]["rpa"] for p in present},
                   weighted_mean_rpa=sum(w[p] * econ[p][key]["rpa"] for p in present))
        out[key] = row
    ranked = sorted(out, key=lambda k: -out[k]["rpa"])
    for k in out:
        out[k]["rank"] = 1 + sum(out[o]["rpa"] > out[k]["rpa"] for o in out)   # ties share a rank
    return {"meta": {"engine_version": ENGINE_VERSION, "rule_id": "PRAIRIE-WEIGHTED-RPA", "years": block["years"],
                     "source": block["source"], "generated": block.get("generated"), "order": ranked},
            "crops": out}


def prairie_last_yr(year: int = BASELINE_YEAR) -> dict:
    return aggregate(provincial_last_yr(year))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=BASELINE_YEAR)
    args = ap.parse_args(argv)
    res = prairie_last_yr(args.year)
    meta = res["meta"]
    print(f"Prairie benchmark, Last Yr {args.year} — weights: {meta['source']}, {meta['years'][0]}-{meta['years'][-1]} average")
    print(f"{'Crop':<17}{'Cover':>7}  " + "".join(f"{c:>7}" for c in PROVINCES)
          + f"{'Yield':>9}{'Price':>8}{'C':>9}{'D':>9}{'C-D':>9}  Rank")
    for key in meta["order"]:
        r = res["crops"][key]
        ws = "".join(f"{r['weights'].get(c, 0):>7.1%}" if c in r["weights"] else f"{'—':>7}" for c in PROVINCES)
        print(f"{r['trade_label']:<17}{r['coverage']:>7.1%}  {ws}{r['yield']:>9.1f}{r['price']:>8.2f}"
              f"{r['revenue']:>9.2f}{r['cost']:>9.2f}{r['rpa']:>9.2f}  #{r['rank']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
