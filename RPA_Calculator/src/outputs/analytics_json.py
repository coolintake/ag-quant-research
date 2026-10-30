"""Analytics JSON: output/analytics/rpa_analytics_<year>.json, one file for all four summary levels.

    provinces.<SK|AB|MB>          per crop: Last Yr yield, price, C, seed / fertilizer / chemical, D,
                                  C - D, rank, breakevens, plus the province's Last Yr $/lb nutrient
    prairie_weighted_benchmark    the acreage-weighted Prairie benchmark (src/engine/prairie_engine.py,
                                  [PRAIRIE-WEIGHTED-RPA]) with its weights, coverage and the provincial
                                  returns it is built from

All $ per acre; yields in bu/ac (lb/ac for pulses), prices in $/bu (c/lb for pulses).

    python -m src.outputs.analytics_json [--year 2026]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from ..config import BASELINE_YEAR, OUTPUT_DIR, PROVINCES, RULES, province
from ..engine.market import ENGINE_VERSION, last_yr
from ..engine.prairie_engine import ENGINE_VERSION as PRAIRIE_VERSION, aggregate, provincial_last_yr
from ..normalize.baselines import last_yr_per_lb, last_yr_quotes, n_shares, province_baselines

ECON = ("yield", "price", "revenue", "seed", "fert", "chem", "cost", "rpa", "be_price", "be_yield")


def _r(v, nd: int = 4):
    return None if v is None else round(v, nd)


def provincial(code: str, year: int) -> dict:
    spec = province(code)
    rows = {}
    for b in province_baselines(code, year):
        e = last_yr(b)
        rows[b.crop_key] = {"trade_label": b.trade_label, "variety": b.variety, "regions": list(b.regions),
                            "region_label": b.region_label, "price_basis": b.price_basis, "yield_unit": b.yield_unit,
                            "inherits": b.inherits, **{k: _r(e[k]) for k in ECON}}
    for k, row in rows.items():
        row["rank"] = 1 + sum(o["rpa"] > row["rpa"] for o in rows.values())
    return {"name": spec.name, "source": spec.source["title"], "region_scope": spec.region_scope,
            "last_yr_quotes": last_yr_quotes(code, year), "quote_unit": spec.quote_unit,
            "n_shares": n_shares(code, year), "last_yr_per_lb": {k: _r(v) for k, v in last_yr_per_lb(code, year).items()},
            "crops": rows}


def prairie(year: int) -> dict:
    res = aggregate(provincial_last_yr(year))
    crops = {}
    for k in res["meta"]["order"]:
        r = res["crops"][k]
        crops[k] = {"trade_label": r["trade_label"], "price_basis": r["price_basis"], "rank": r["rank"],
                    "coverage": _r(r["coverage"], 6), "weights": {p: _r(w, 6) for p, w in r["weights"].items()},
                    "acreage_weights": {p: _r(w, 6) for p, w in r["acreage_weights"].items()},
                    **{m: _r(r[m]) for m in ECON},
                    "provincial_rpa": {p: _r(v) for p, v in r["provinces"].items()},
                    "weighted_mean_provincial_rpa": _r(r["weighted_mean_rpa"])}
    return {"rule_id": "PRAIRIE-WEIGHTED-RPA", "engine_version": PRAIRIE_VERSION, "weights_source": res["meta"]["source"],
            "weights_years": res["meta"]["years"], "weights_generated": res["meta"]["generated"],
            "method": "weights = 3-year average StatCan 32-10-0359 seeded acres, rescaled over provinces with a guide "
                      "budget; Yield, Price, Seed, Fert, Chem = weighted means; C = Yield x Price; D = Seed + Fert + "
                      "Chem; RPA = C - D", "crops": crops}


def build(year: int = BASELINE_YEAR) -> dict:
    return {"meta": {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "year": year,
                     "rules_schema": RULES.schema_version, "engine_version": ENGINE_VERSION,
                     "units": "$/ac; yields bu/ac (lb/ac pulses); prices $/bu (c/lb pulses)",
                     "levels": [*PROVINCES, "Prairie"]},
            "provinces": {code: provincial(code, year) for code in PROVINCES},
            "prairie_weighted_benchmark": prairie(year)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=BASELINE_YEAR)
    args = ap.parse_args(argv)
    data = build(args.year)
    out = OUTPUT_DIR / "analytics" / f"rpa_analytics_{args.year}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = ", ".join(f"{c} {len(v['crops'])}" for c, v in data["provinces"].items())
    print(f"  wrote {out}  (crops: {counts}, Prairie {len(data['prairie_weighted_benchmark']['crops'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
