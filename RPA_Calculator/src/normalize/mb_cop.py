"""Manitoba COP normalizer: raw workbook cells -> one record per crop_key and year (province-wide).

Reads output/mb/intermediate/mb_raw_tables.json (src/sources/mb_extractor.py) and writes
output/mb/mvs/mb_historical_2019_2026.{json,csv}. Per crop:

    * crop name -> canonical crop_key + trade label (src/normalize/crops.py, [CROP-MAP]); MB
      guides are province-wide, so each crop has one budget [REGION-AVG-RULE]
    * yield, price (target + premium), seed $/ac, fertilizer N / P2O5 / K2O / S lb and $/ac,
      crop protection $/ac; peas converted to lb/ac and c/lb [MB-PEAS-BU]
    * seeding rate in lb/ac where the guide states bu or lb [MB-SEED-UNITS]
    * cross-checks: sales = yield x price; Summary seed / fertilizer / chemical == the Seed,
      Fertilizer & Chemical sheet; fertilizer $/ac == N / P / K / S lb x the guide's quotes under
      [NCREDIT-MB] (urea + NH3 at the guide's usage shares, MAP / AS N credited at urea); from
      2022 the workbooks cap that credit at the crop's N rate (legumes pay full MAP), which is
      recorded per record, while the engine keeps the uniform [NCREDIT] credit

Per year the guide's fertilizer quotes ($/tonne) and N source shares go to meta, where
src/normalize/baselines.py reads them for Last Yr ([LASTYR-QUOTES], guide_published).

    python -m src.normalize.mb_cop
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone

from ..config import PULSE_LBS_PER_BU, crop, normalize_label, province
from ..pricing.fertilizer import per_lb
from .crops import resolve, trade_label

SPEC = province("MB")
NORMALIZER_VERSION = "1.0.0"
QUOTE_KEY_BY_ANALYSIS = {**{p.analysis: k for k, p in SPEC.products.items()},
                         **{p.analysis: k for k, p in SPEC.n_blend.items()}}
CSV_FIELDS = ["year", "crop_key", "display", "source_name", "yield_", "yield_unit", "price", "price_unit", "sales",
              "seed", "seeding_rate", "seeding_rate_unit", "seeding_rate_lbs_ac", "fertilizer", "n_lbs_ac",
              "p2o5_lbs_ac", "k2o_lbs_ac", "s_lbs_ac", "fert_calc", "chemical", "chem_weed", "chem_disease",
              "chem_insect", "total_operating", "verified", "notes"]


def _v(cell: dict | None):
    return None if cell is None else cell.get("value")


def year_fertilizer(doc: dict) -> tuple[dict, dict, list[str]]:
    """({quote key: {per_tonne, per_lb, product}}, {blend source: share of applied N}, notes)."""
    prices, shares, notes, urea_share = {}, {}, [], None
    for label, p in doc["sfc"]["fert_prices"].items():
        key = QUOTE_KEY_BY_ANALYSIS.get(p["analysis"])
        if key is None:
            if p["n_usage"]:
                notes.append(f"N source {label!r} has usage {p['n_usage']} but is not in the rules' N blend")
            continue
        prices[key] = {"per_tonne": p["per_tonne"], "per_lb": p["per_lb"], "product": label, "cell": p["cell"]}
        if key in SPEC.n_blend:
            shares[key] = p["n_usage"] or 0.0
        elif key == "N" and p["n_usage"] is not None:
            urea_share = p["n_usage"]
    total = None if urea_share is None else urea_share + sum(shares.values())
    if total is not None and abs(total - 1) > 1e-9:
        notes.append(f"N source usage sums to {total:g}, not 1")
    missing = set(SPEC.quote_keys) - set(prices)
    if missing:
        notes.append(f"fertilizer quotes missing for {sorted(missing)}")
    return prices, shares, notes


def normalize(doc: dict) -> tuple[list[dict], dict]:
    prices, shares, year_notes = year_fertilizer(doc)
    quotes = {k: v["per_tonne"] for k, v in prices.items()}
    complete = not set(SPEC.quote_keys) - set(quotes)
    lb = per_lb(quotes, SPEC, n_shares=shares) if complete else None
    urea_lb = per_lb(quotes, SPEC)["N"] if complete else None
    sfc = {name: {part: doc["sfc"][part].get(name) for part in ("seed", "fert", "chem")}
           for name in doc["sfc"]["seed"]}
    by_norm = {normalize_label(n): n for n in sfc}
    records, unmapped, seen = [], [], set()
    for name, col in doc["summary"]["crops"].items():
        key = resolve("MB", name)
        if key is None or key in seen:
            if key is None and normalize_label(name) != "yourfarm":
                unmapped.append(name)
            continue
        seen.add(key)
        c = crop(key)
        detail = sfc.get(by_norm.get(normalize_label(name)), {})
        seed, fert, chem = detail.get("seed") or {}, detail.get("fert") or {}, detail.get("chem") or {}
        notes, checks = [], []

        def check(label: str, got, want, tol: float, info: bool = False) -> None:
            entry = {"check": label, "ok": None if got is None or want is None else abs(got - want) <= tol}
            if entry["ok"] is not None:
                entry.update(printed=round(got, 6), expected=round(want, 6))
            if info:
                entry["informational"] = True
            checks.append(entry)

        y, unit = _v(col.get("target_yield")), _v(col.get("yield_unit"))
        price = (_v(col.get("target_price")) or 0.0) + (_v(col.get("premium")) or 0.0)
        if _v(col.get("premium")):
            notes.append(f"price includes a ${_v(col['premium']):.2f} premium")
        sales = _v(col.get("gross_revenue"))
        check("sales = yield x price", sales, None if y is None else y * price, 0.01)
        if c.pulse:                                   # [MB-PEAS-BU]
            if unit == "bu":
                y_native, p_native = y * PULSE_LBS_PER_BU, price / PULSE_LBS_PER_BU * 100
                notes.append(f"{y:g} bu/ac x {PULSE_LBS_PER_BU:g} lb/bu; ${price:.2f}/bu -> {p_native:.4f} c/lb [MB-PEAS-BU]")
            else:
                y_native, p_native = y, price * 100
        else:
            y_native, p_native = y, price
            if unit != "bu":
                notes.append(f"yield unit {unit!r} for a $/bu crop")

        if col.get("crop_protection"):
            chem_total = _v(col["crop_protection"])
        else:
            parts = [_v(col.get(k)) for k in ("herbicide", "fungicide", "insecticide")]
            chem_total = sum(p or 0.0 for p in parts) if any(p is not None for p in parts) else None
        check("Summary chemical == crop protection sheet", chem_total, chem.get("total"), 0.005)
        check("Summary seed == seed sheet", _v(col.get("seed")), seed.get("cost"), 0.005)
        check("Summary fertilizer == fertilizer sheet", _v(col.get("fertilizer")), fert.get("total"), 0.005)
        rates = {"N": fert.get("n_lbs"), "P": fert.get("p_lbs"), "K": fert.get("k_lbs"), "S": fert.get("s_lbs")}
        fert_calc = (sum((rates[n] or 0.0) * lb[n] for n in rates) if lb and all(v is not None for v in rates.values())
                     else None)
        # the workbooks convert with 0.4536 kg/lb (2,204.59 lb/t) where the rules use 2,204.62 lb/t
        tol = max(0.02, 0.0005 * (fert_calc or 0))
        # From 2022 the workbooks credit the N in MAP / AS only up to the crop's N rate, so a
        # legume (0 lb N) pays full MAP. The engine keeps the one [NCREDIT] rule for every
        # province; where the printed total matches the capped variant, record the gap.
        capped = None
        if fert_calc is not None and urea_lb is not None:
            supplied = sum((rates[n] or 0.0) * SPEC.products[n].n_content / SPEC.products[n].grade for n in ("P", "K", "S"))
            capped = fert_calc + max(0.0, supplied - (rates["N"] or 0.0)) * urea_lb
        printed = fert.get("total")
        if (printed is not None and fert_calc is not None and abs(printed - fert_calc) > tol
                and capped is not None and abs(printed - capped) <= tol):
            check("fertilizer == NPKS x guide quotes, N credit capped at N rate", printed, capped, tol, info=True)
            notes.append(f"guide caps the MAP/AS N credit at the crop's N rate: printed ${printed:.2f}/ac vs "
                         f"${fert_calc:.2f}/ac under [NCREDIT] (engine uses [NCREDIT]) [NCREDIT-MB]")
        else:
            check("fertilizer == NPKS x guide quotes [NCREDIT-MB]", printed, fert_calc, tol)

        rate, r_unit = seed.get("rate"), seed.get("rate_unit")
        lbs = rate * c.bu_weight if r_unit == "bu" and rate else rate if r_unit == "lb" else None
        other = None if lbs is not None or rate is None else f"{rate:g} {r_unit}/ac @ ${seed.get('price') or 0:g}{seed.get('price_unit') or ''}"
        if other:
            notes.append(f"seeding rate {other}: no lb/ac equivalent [MB-SEED-UNITS]")

        failed = [ch["check"] for ch in checks if ch["ok"] is False and not ch.get("informational")]
        if failed:
            notes.append("unverified: " + "; ".join(failed))
        core = {"yield": y, "price": price, "seed": _v(col.get("seed")), "chemical": chem_total,
                "fertilizer": fert.get("total")}
        if any(v is None for v in core.values()) or any(v is None for v in rates.values()):
            notes.append("core field missing: " + ", ".join([k for k, v in core.items() if v is None]
                                                             + [f"{n} rate" for n, v in rates.items() if v is None]))
        records.append({
            "year": doc["year"], "province": "MB", "zone": "province_wide", "zone_display": SPEC.regions["province_wide"],
            "crop_key": key, "display": trade_label(key), "source_name": name,
            "source": {"file": doc["file"], "summary_column": col["column"], "cells": {
                k: col[k]["cell"] for k in ("target_yield", "target_price", "seed", "fertilizer") if col.get(k)}
                | {"fertilizer_detail": fert.get("cell"), "seed_detail": seed.get("cell"), "chem_detail": chem.get("cell")}},
            "yield_": y, "yield_unit": f"{unit}/ac" if unit else None, "price": price,
            "price_unit": f"$/{unit}" if unit else None, "yield_native": y_native, "price_native": p_native,
            "sales": sales, "seed": _v(col.get("seed")), "seeding_rate": rate, "seeding_rate_unit": r_unit,
            "seed_price": seed.get("price"), "seed_price_unit": seed.get("price_unit"),
            "seeding_rate_lbs_ac": lbs, "seeding_other": other,
            "fertilizer": fert.get("total"), "n_lbs_ac": rates["N"], "p2o5_lbs_ac": rates["P"],
            "k2o_lbs_ac": rates["K"], "s_lbs_ac": rates["S"],
            "fert_by_nutrient": {"N": fert.get("n_cost"), "P": fert.get("p_cost"), "K": fert.get("k_cost"),
                                 "S": fert.get("s_cost")},
            "fert_calc": fert_calc, "chemical": chem_total, "chem_weed": chem.get("weed"),
            "chem_disease": chem.get("disease"), "chem_insect": chem.get("insect"),
            "total_operating": _v(col.get("total_operating")),
            "checks": checks, "notes": notes,
            "verified": not failed and not any(n.startswith("core field missing") for n in notes),
        })
    year_meta = {"fert_prices": prices, "n_shares": shares, "per_lb": lb, "unmapped_crops": unmapped,
                 "notes": year_notes + doc.get("warnings", [])}
    return records, year_meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    if not SPEC.raw_json.exists():
        print(f"Raw MB tables not found: {SPEC.raw_json}\nRun: python -m src.sources.mb_extractor")
        return 1
    raw = json.loads(SPEC.raw_json.read_text(encoding="utf-8"))
    records, years = [], {}
    for doc in raw["documents"]:
        recs, ym = normalize(doc)
        records += recs
        years[str(doc["year"])] = ym
        print(f"  {doc['year']}  crops={len(recs):2}  verified={sum(r['verified'] for r in recs):2}  "
              f"N blend NH3={ym['n_shares'].get('NH3', 0):.2f}  unmapped: {', '.join(ym['unmapped_crops'])}"
              + (f"  notes: {ym['notes']}" if ym["notes"] else ""))
    SPEC.baseline_dir.mkdir(parents=True, exist_ok=True)
    meta = {"normalizer_version": NORMALIZER_VERSION, "extractor": raw["meta"],
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "zones": SPEC.regions, "fert_prices": {y: m["fert_prices"] for y, m in years.items()},
            "n_shares": {y: m["n_shares"] for y, m in years.items()}, "years": years}
    out_json, out_csv = SPEC.baseline_path("json"), SPEC.baseline_path("csv")
    out_json.write_text(json.dumps({"meta": meta, "records": records}, indent=1, ensure_ascii=False), encoding="utf-8")
    with out_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({**r, "notes": "; ".join(r["notes"])})
    print(f"  wrote {out_json}\n  wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
