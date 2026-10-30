"""Alberta cost-of-production normalizer: raw extracted tables -> per region x crop records.

Reads output/ab/intermediate/ab_raw_tables.json (src/sources/ab_extractor.py) and writes
output/ab/mvs/ab_historical_2019_2026.{json,csv}. Per budget column:

    * crop name -> canonical crop_key + trade label (src/normalize/crops.py, [CROP-MAP])
    * seeding rate from Table 1, converted with the crop's bushel weight
    * cross-checks: sales = yield x price, items sum to total, sales - total = contribution,
      fertilizer ~ NPKS x published $/lb, seed vs Table 1, chemical vs Table 2 (informational)
    * OCR'd tables: values repaired only from an independent source or a table identity
    * source overrides from docs/AGENT_RULES.json (e.g. [OVR-AB-LENTIL-SEED]); the printed
      value is kept (seed_printed) and the override id is recorded (seed_source)

Records cover every region the guide budgets; which regions enter a baseline is decided in
src/normalize/baselines.py.

    python -m src.normalize.ab_cop
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone

from ..config import crop, overrides_for, province
from ..sources.ab_extractor import DIRECT_ITEMS, NPKS_RE
from .crops import resolve, resolve_votes, trade_label

SPEC = province("AB")
NORMALIZER_VERSION = "2.0.0"


def _grid_lookup(grid: dict, key: str) -> dict | None:
    for label, zones in grid.items():
        if resolve("AB", label) == key:
            return zones
    return None


def normalize(doc: dict) -> list[dict]:
    prices = {k: v["per_lb"] for k, v in doc["fert_prices"].items()}
    records = []
    for table in doc["budgets"]:
        if "error" in table:
            continue
        zone = table["zone"]
        rows = {r["key"]: r["values"] for r in table["rows"] if r["key"]}
        cands = {r["key"]: r.get("candidates") for r in table["rows"] if r["key"]}
        used_keys: set[str] = set()
        for col in table["columns"]:
            key = resolve_votes("AB", col.get("crop_name_variants", []))
            if key is None or key in used_keys:
                continue
            used_keys.add(key)
            i = col["index"]

            def v(field: str):
                vals = rows.get(field)
                x = vals[i] if vals and i < len(vals) else None
                return x if isinstance(x, (int, float)) else None

            rec = {"year": doc["year"], "province": "AB", "zone": zone, "zone_display": SPEC.regions.get(zone),
                   "crop_key": key, "display": trade_label(key), "crop_header": col["header"],
                   "grade": col["grade"], "source": {"file": doc["file"], "page": table["page"]},
                   "ocr": table["ocr"], "notes": [], "checks": []}
            y, p = v("yield"), v("price")
            lb_yield = y is not None and y > 300
            rec.update(yield_=y, yield_unit="lb/ac" if lb_yield else "bu/ac", price=p,
                       price_unit="$/lb" if lb_yield else "$/bu")
            for f in ("sales", "seed", "fertilizer", "chemical", "total_direct", "contribution", "break_even"):
                rec[f] = v(f)
            npks_raw = rows.get("npks", [None] * (i + 1))[i] if rows.get("npks") else None
            rec["npks_blend"] = npks_raw
            npks = [float(x) for x in npks_raw.split("-")] if isinstance(npks_raw, str) and NPKS_RE.match(npks_raw) else None
            rec.update(n_lbs_ac=npks[0] if npks else None, p2o5_lbs_ac=npks[1] if npks else None,
                       k2o_lbs_ac=npks[2] if npks else None, s_lbs_ac=npks[3] if npks else None)
            present = [k for k in DIRECT_ITEMS if k in rows]
            items = {k: v(k) for k in present}

            seed_grid = (_grid_lookup(doc["seed_table"], key) or {}).get(zone) or []
            chem_grid = (_grid_lookup(doc["chem_table"], key) or {}).get(zone) or []
            t1_cost = seed_grid[-1] if len(seed_grid) >= 2 else None
            rate = seed_grid[0] if len(seed_grid) >= 2 else None
            unit = crop(key).seed_rate_unit
            rec["seeding_rate"] = rate
            rec["seeding_rate_unit"] = unit if rate is not None else None
            rec["seeding_rate_lbs_ac"] = (None if rate is None else
                                          rate if unit == "lb/ac" else rate * crop(key).bu_weight)
            rec["chemical_table2"] = round(sum(chem_grid), 2) if chem_grid else None
            fert_calc = (sum(a * prices.get(k, 0) for a, k in zip(npks, "NPKS")) if npks and len(prices) == 4
                         else None)

            if rec["ocr"]:
                alts = {k: (cands.get(k) or [None] * (i + 1))[i] if cands.get(k) and i < len(cands[k]) else []
                        for k in present}
                repair_ocr(rec, items, t1_cost, fert_calc, alts)
            y, p = rec["yield_"], rec["price"]
            if y is not None:
                lb_yield = y > 300
                rec.update(yield_unit="lb/ac" if lb_yield else "bu/ac", price_unit="$/lb" if lb_yield else "$/bu")

            def check(name: str, got, want, tol: float, info: bool = False) -> None:
                entry = {"check": name, "ok": None if got is None or want is None else abs(got - want) <= tol}
                if entry["ok"] is not None:
                    entry.update(printed=got, expected=round(want, 4))
                if info:
                    entry["informational"] = True
                rec["checks"].append(entry)

            total_items = sum(x for x in items.values() if x is not None) if items else None
            check("sales = yield x price", rec["sales"], None if None in (y, p) else y * p,
                  max(0.51, 0.005 * (rec["sales"] or 0)))
            check("sum of direct items = total", rec["total_direct"],
                  total_items if all(x is not None for x in items.values()) else None, 0.06)
            check("sales - total = contribution", rec["contribution"],
                  None if None in (rec["sales"], rec["total_direct"]) else rec["sales"] - rec["total_direct"], 0.02)
            # The guides cost the physical product blend and round up, so this is a sanity bound.
            check("fertilizer ~ NPKS x published $/lb", rec["fertilizer"], fert_calc,
                  max(1.5, 0.012 * (fert_calc or 0)))
            check("seed vs Table 1 $/ac", rec["seed"], t1_cost, 0.015, info=True)
            seed_c = rec["checks"][-1]
            if seed_c["ok"] is False:
                rec["notes"].append(f"budget seed ${rec['seed']:.2f} differs from Table 1 ${t1_cost:.2f} "
                                    "(guide inconsistency; budget value kept)")
            check("chemical vs Table 2 total", rec["chemical"], rec["chemical_table2"], 0.03, info=True)

            failed = [c["check"] for c in rec["checks"] if c["ok"] is False and not c.get("informational")]
            if failed:
                rec["notes"].append("unverified: " + "; ".join(failed))
            core = ("yield_", "price", "seed", "chemical")
            if any(rec.get(f) is None for f in core) or npks is None:
                rec["notes"].append("core field missing: " + ", ".join(
                    [f.rstrip("_") for f in core if rec.get(f) is None] + (["npks"] if npks is None else [])))
            rec["verified"] = not failed and not any(n.startswith("core field missing") for n in rec["notes"])
            # Every year the budget prices red lentil seed with the green-lentil Table 1 cost
            # (90 lb rate); use Table 1's red-lentil rate and cost. Checks above ran on the
            # printed figure, which is kept in seed_printed.
            rec["seed_printed"] = rec["seed"]
            seed_rule = next((o for o in overrides_for("AB", key, "seed") if o["use"] == "table1_cost"), None)
            if seed_rule and t1_cost is not None and rec["seed"] != t1_cost:
                rec["notes"] = [n for n in rec["notes"] if not n.startswith("budget seed")]
                rec["notes"].append(f"seed ${t1_cost:.2f} from Table 1 red lentils ({rate:g} lb/ac); budget "
                                    f"printed ${rec['seed']:.2f} (green-lentil seed cost)")
                rec["seed"] = t1_cost
                rec["seed_source"] = f"Table 1 [{seed_rule['id']}]"
            else:
                rec["seed_source"] = "budget"
            records.append(rec)
    return records


def repair_ocr(rec: dict, items: dict, t1_cost: float | None, fert_calc: float | None,
               alts: dict | None = None) -> None:
    """Fill or correct OCR'd values only from an independent source or a table identity."""
    notes = rec["notes"]
    sales, contrib, total = rec["sales"], rec["contribution"], rec["total_direct"]
    t_alt = None if None in (sales, contrib) else round(sales - contrib, 2)
    targets = [t for t in (total, t_alt) if t is not None]

    def adds_up(vals: dict) -> bool:
        return all(x is not None for x in vals.values()) and any(abs(sum(vals.values()) - t) <= 0.06
                                                                  for t in targets)

    if rec["seed"] is None and t1_cost is not None:
        notes.append(f"OCR seed unread -> Table 1 ${t1_cost:.2f}")
        rec["seed"] = items["seed"] = t1_cost
    elif t1_cost is not None and abs(rec["seed"] - t1_cost) > 0.015 and not adds_up(items)             and adds_up({**items, "seed": t1_cost}):
        notes.append(f"OCR seed {rec['seed']} -> Table 1 ${t1_cost:.2f} (table then adds up)")
        rec["seed"] = items["seed"] = t1_cost
    # A cell the OCR variants disagreed on: try their other readings against the total.
    if targets and not adds_up(items) and alts:
        for k, options in alts.items():
            for alt in options or []:
                if isinstance(alt, (int, float)) and alt != items.get(k) and adds_up({**items, k: alt}):
                    notes.append(f"OCR {k} {items.get(k)} -> {alt} (other OCR reading; table then adds up)")
                    items[k] = alt
                    if k in rec:
                        rec[k] = alt
                    break
            if adds_up(items):
                break
    known = [x for x in items.values() if x is not None]
    missing = [k for k, x in items.items() if x is None]
    candidates = [t for t in (total, t_alt) if t is not None]
    if not missing and known:
        match = next((t for t in candidates if abs(sum(known) - t) <= 0.06), None)
        if match is not None and match != total:
            notes.append(f"OCR total {total} -> {match:.2f} (sales - contribution)")
            rec["total_direct"] = match
    elif len(missing) == 1 and candidates:
        t = t_alt if (total is None or (t_alt is not None and t_alt != total)) and t_alt is not None else total
        fill = round(t - sum(known), 2)
        if fill >= 0:
            notes.append(f"OCR {missing[0]} unread -> {fill:.2f} (total - other items)")
            items[missing[0]] = fill
            if missing[0] in ("seed", "chemical", "fertilizer"):
                rec[missing[0]] = fill
            if rec["total_direct"] is None:
                rec["total_direct"] = t
    if rec["sales"] is None and None not in (rec["total_direct"], contrib):
        rec["sales"] = round(rec["total_direct"] + contrib, 2)
        notes.append("OCR sales unread -> total + contribution")
    y, p, s = rec["yield_"], rec["price"], rec["sales"]
    if s is not None:
        if y is None and p:
            rec["yield_"] = round(s / p, 2)
            notes.append("OCR yield unread -> sales / price")
        elif p is None and y:
            rec["price"] = round(s / y, 2 if y < 300 else 4)
            notes.append("OCR price unread -> sales / yield")
        elif y and p and abs(y * p - s) > max(0.51, 0.005 * s):
            p2, y2 = round(s / y, 2 if y < 300 else 2), round(s / p, 2)
            if abs(p2 - p) / p <= abs(y2 - y) / y:
                notes.append(f"OCR price {p} -> {p2} (sales / yield)")
                rec["price"] = p2
            else:
                notes.append(f"OCR yield {y} -> {y2} (sales / price)")
                rec["yield_"] = y2
    if fert_calc is not None and (rec["fertilizer"] is None or
                                  abs(rec["fertilizer"] - fert_calc) > max(1.5, 0.012 * fert_calc)):
        notes.append(f"OCR fertilizer {rec['fertilizer']} -> NPKS x $/lb {fert_calc:.2f} (approximate)")
        rec["fertilizer"] = round(fert_calc, 2)
    if rec["chemical"] is None and rec.get("chemical_table2") is not None:
        notes.append(f"OCR chemical unread -> Table 2 ${rec['chemical_table2']:.2f} (approximate)")
        rec["chemical"] = rec["chemical_table2"]


CSV_FIELDS = ["year", "province", "zone", "zone_display", "crop_key", "display", "grade", "yield_", "yield_unit",
              "price", "price_unit", "sales", "seed", "seed_source", "seed_printed", "fertilizer", "npks_blend", "n_lbs_ac", "p2o5_lbs_ac",
              "k2o_lbs_ac", "s_lbs_ac", "chemical", "chemical_table2", "total_direct", "contribution",
              "break_even", "seeding_rate", "seeding_rate_unit", "seeding_rate_lbs_ac", "ocr", "verified", "notes"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    if not SPEC.raw_json.exists():
        print(f"Raw AB tables not found: {SPEC.raw_json}\nRun: python -m src.sources.ab_extractor")
        return 1
    raw = json.loads(SPEC.raw_json.read_text(encoding="utf-8"))
    records = []
    for doc in raw["documents"]:
        recs = normalize(doc)
        records += recs
        print(f"  {doc['year']}  crop-regions={len(recs):3}  verified={sum(r['verified'] for r in recs):3}")
    SPEC.baseline_dir.mkdir(parents=True, exist_ok=True)
    meta = {"normalizer_version": NORMALIZER_VERSION, "extractor": raw["meta"],
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "zones": SPEC.regions, "fert_prices": {d["year"]: d["fert_prices"] for d in raw["documents"]}}
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
