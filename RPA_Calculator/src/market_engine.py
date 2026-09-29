"""Phase 2 - Tier 1 dynamic market vector engine.

Re-prices the MVS historical baselines (Tier 2 physical rates) with current market
inputs and recomputes the direct-cash-input RPA.

Market vectors (all optional; anything not given is held at the baseline):
    nutrient_prices  $/lb of nutrient: N, P (as P2O5), K (as K2O), S
    crop_prices      per commodity, either
                         {"farm_price": 10.15, "unit": "$/bu"}   or "c/lb" for pulses
                         {"futures": 12.40, "basis": -1.10}      $/bu, farm = futures + basis
    seed_prices      per commodity, $/lb of seed (re-prices base seed where the MVS
                     carries a lb/ac seeding rate; treatment stays at baseline)

Per commodity:
    Fertilizer ($/ac) = lb N x $N + lb P2O5 x $P + lb K2O x $K + lb S x $S
    D                 = seed + fertilizer + plant protection (baseline $/ac)
    C                 = target yield x farm price
    Return            = C - D
    Breakeven yield   = D / farm price        Breakeven price = D / target yield

A nutrient whose lb/ac rate is unknown keeps its baseline $/ac (flagged).

Usage:
    python src/market_engine.py                       # built-in sample scenario + self-checks
    python src/market_engine.py --scenario my.json    # custom scenario file
    python src/market_engine.py --baseline-year 2025
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from config import (MVS_COMMODITY_ZONE_MAP, OUTPUT_JSON, PULSE_LBS_PER_BU, SCENARIO_DIR,
                    is_pulse)

ENGINE_VERSION = "2.0.0"
NUTRIENTS = (("N", "n_lbs_ac", "fert_n"), ("P", "p_lbs_ac", "fert_p"),
             ("K", "k_lbs_ac", "fert_k"), ("S", "s_lbs_ac", "fert_s"))

SAMPLE_SCENARIO = {
    "name": "sample",
    "description": "Phase 2 verification inputs",
    "baseline_year": 2026,
    "nutrient_prices": {"N": 0.75, "P": 0.65, "K": 0.45, "S": 0.50},
    "crop_prices": {
        "Wheat": {"farm_price": 10.15, "unit": "$/bu"},
        "Canola": {"farm_price": 14.50, "unit": "$/bu"},
        "Red Lentils": {"farm_price": 32.0, "unit": "c/lb"},
    },
}


# ---------------------------------------------------------------------------
# Scenario model
# ---------------------------------------------------------------------------
@dataclass
class CropPrice:
    farm_price: float | None = None
    unit: str = "$/bu"
    futures: float | None = None
    basis: float | None = None

    def per_bu(self, commodity: str) -> tuple[float, str]:
        """Farm price in $/bu and a description of where it came from."""
        if self.farm_price is not None:
            if self.unit == "$/bu":
                return self.farm_price, f"farm price override {self.farm_price:g} $/bu"
            if self.unit == "c/lb":
                if not is_pulse(commodity):
                    raise ValueError(f"{commodity}: c/lb pricing is for pulses only")
                return (self.farm_price / 100 * PULSE_LBS_PER_BU,
                        f"farm price override {self.farm_price:g} c/lb")
            raise ValueError(f"{commodity}: unit must be '$/bu' or 'c/lb', got {self.unit!r}")
        if self.futures is not None:
            basis = self.basis or 0.0
            return self.futures + basis, f"futures {self.futures:g} + basis {basis:+g} $/bu"
        raise ValueError(f"{commodity}: crop price needs farm_price or futures")


@dataclass
class Scenario:
    name: str
    baseline_year: int
    nutrient_prices: dict[str, float] = field(default_factory=dict)
    crop_prices: dict[str, CropPrice] = field(default_factory=dict)
    seed_prices: dict[str, float] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict, baseline_year: int | None = None) -> "Scenario":
        aliases = commodity_aliases()

        def resolve(name: str) -> str:
            key = _norm(name)
            if key not in aliases:
                raise ValueError(f"unknown commodity {name!r}; known: {', '.join(MVS_COMMODITY_ZONE_MAP)}")
            return aliases[key]

        nutrients = {k.upper(): float(v) for k, v in (d.get("nutrient_prices") or {}).items()}
        bad = set(nutrients) - {n for n, _, _ in NUTRIENTS}
        if bad:
            raise ValueError(f"unknown nutrient(s) {sorted(bad)}; use N, P, K, S")
        return cls(
            name=d.get("name", "scenario"),
            description=d.get("description", ""),
            baseline_year=int(baseline_year or d.get("baseline_year") or 2026),
            nutrient_prices=nutrients,
            crop_prices={resolve(k): CropPrice(**v) for k, v in (d.get("crop_prices") or {}).items()},
            seed_prices={resolve(k): float(v) for k, v in (d.get("seed_prices") or {}).items()},
        )


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def commodity_aliases() -> dict[str, str]:
    """Commodity name, variety and guide source names -> MVS commodity."""
    out = {}
    for commodity, rule in MVS_COMMODITY_ZONE_MAP.items():
        for name in (commodity, rule.get("variety") or "", *rule["source_names"]):
            if name:
                out.setdefault(_norm(name), commodity)
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def load_baseline(year: int, path: Path = OUTPUT_JSON) -> dict[str, dict]:
    if not path.exists():
        raise FileNotFoundError(f"MVS database not found: {path} (run src/normalizer.py)")
    data = json.loads(path.read_text(encoding="utf-8"))
    recs = {r["commodity"]: r for r in data["records"] if r["year"] == year}
    if not recs:
        raise ValueError(f"no MVS records for {year}; available: {data['meta']['years']}")
    return recs


def implied_nutrient_prices(baseline: dict[str, dict]) -> dict[str, float | None]:
    """Mean baseline $/lb per nutrient, from records with a positive rate."""
    out = {}
    for nut, rate_key, cost_key in NUTRIENTS:
        ratios = [r["direct_costs_per_ac"][cost_key] / r["agronomics"][rate_key] for r in baseline.values()
                  if r["agronomics"].get(rate_key) and r["direct_costs_per_ac"].get(cost_key) is not None]
        out[nut] = round(sum(ratios) / len(ratios), 4) if ratios else None
    return out


def economics(yield_bu: float, price_bu: float, seed: float, fert: float, protection: float,
              pulse: bool) -> dict:
    d = seed + fert + protection
    c = yield_bu * price_bu
    out = {
        "farm_price_per_bu": price_bu,
        "gross_revenue": c,
        "seed_total": seed, "fert_total": fert, "protection_total": protection,
        "direct_cash_inputs": d,
        "return_over_direct": c - d,
        "breakeven_yield_bu_ac": d / price_bu if price_bu else None,
        "breakeven_price_per_bu": d / yield_bu if yield_bu else None,
    }
    if pulse:
        out.update(farm_price_cents_lb=price_bu / PULSE_LBS_PER_BU * 100,
                   breakeven_yield_lbs_ac=None if out["breakeven_yield_bu_ac"] is None
                   else out["breakeven_yield_bu_ac"] * PULSE_LBS_PER_BU,
                   breakeven_price_cents_lb=None if out["breakeven_price_per_bu"] is None
                   else out["breakeven_price_per_bu"] / PULSE_LBS_PER_BU * 100)
    return out


def run_commodity(rec: dict, sc: Scenario) -> dict:
    commodity = rec["commodity"]
    pulse = is_pulse(commodity)
    rev, cost, agro = rec["revenue"], rec["direct_costs_per_ac"], rec["agronomics"]
    notes: list[str] = []
    yield_bu = rev["target_yield_bu_ac"]
    if pulse and rev.get("target_yield_lbs_ac") is not None:
        yield_bu = rev["target_yield_lbs_ac"] / PULSE_LBS_PER_BU  # exact, not the rounded bu/ac

    # Fertilizer, nutrient by nutrient
    fert_lines = {}
    for nut, rate_key, cost_key in NUTRIENTS:
        lbs, base_cost, price = agro.get(rate_key), cost.get(cost_key) or 0.0, sc.nutrient_prices.get(nut)
        if price is None:
            fert_lines[nut] = {"lbs_ac": lbs, "price_per_lb": None, "baseline": base_cost,
                               "scenario": base_cost, "basis": "held at baseline (no price given)"}
        elif lbs is None:
            fert_lines[nut] = {"lbs_ac": None, "price_per_lb": price, "baseline": base_cost,
                               "scenario": base_cost, "basis": "held at baseline (rate unknown)"}
            notes.append(f"{nut}: lb/ac rate unknown, baseline ${base_cost:.2f}/ac kept")
        else:
            fert_lines[nut] = {"lbs_ac": lbs, "price_per_lb": price, "baseline": base_cost,
                               "scenario": lbs * price, "basis": "repriced"}
    fert = sum(v["scenario"] for v in fert_lines.values())

    # Seed: base re-priced only with a $/lb seed price and a lb/ac rate
    seed = cost["seed_total"]
    seed_price = sc.seed_prices.get(commodity)
    if seed_price is not None:
        rate = agro.get("seeding_rate_lbs_ac")
        if rate is None:
            notes.append(f"seed price given but no lb/ac seeding rate ({agro.get('seeding_rate_other')}); "
                         "seed held at baseline")
        else:
            seed = rate * seed_price + (cost.get("seed_treatment") or 0.0)
            notes.append(f"seed re-priced: {rate:g} lb/ac x ${seed_price:g}/lb + baseline treatment")

    # Crop price
    base_price = rev["farm_price_per_bu"]
    if commodity in sc.crop_prices:
        price_bu, price_src = sc.crop_prices[commodity].per_bu(commodity)
    else:
        price_bu, price_src = base_price, "held at baseline"

    baseline = economics(yield_bu, base_price, cost["seed_total"], cost["fert_total"],
                         cost["protection_total"], pulse)
    scenario = economics(yield_bu, price_bu, seed, fert, cost["protection_total"], pulse)
    delta = {k: scenario[k] - baseline[k] for k in
             ("farm_price_per_bu", "gross_revenue", "seed_total", "fert_total",
              "direct_cash_inputs", "return_over_direct")}

    return {
        "commodity": commodity,
        "variety": rec.get("variety"),
        "price_basis": rec["price_basis"],
        "baseline_year": rec["year"],
        "target_yield_bu_ac": yield_bu,
        "target_yield_lbs_ac": rev.get("target_yield_lbs_ac"),
        "price_source": price_src,
        "fertilizer": fert_lines,
        "baseline": baseline,
        "scenario": scenario,
        "delta": delta,
        "notes": notes,
    }


def run_scenario(sc: Scenario, mvs_path: Path = OUTPUT_JSON) -> dict:
    baseline = load_baseline(sc.baseline_year, mvs_path)
    missing = set(sc.crop_prices) - set(baseline)
    if missing:
        raise ValueError(f"no {sc.baseline_year} baseline for {sorted(missing)}")
    return {
        "meta": {
            "engine_version": ENGINE_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mvs_source": str(mvs_path),
            "scenario": sc.name,
            "description": sc.description,
            "baseline_year": sc.baseline_year,
            "inputs": {
                "nutrient_prices_per_lb": sc.nutrient_prices,
                "crop_prices": {k: {kk: vv for kk, vv in vars(v).items() if vv is not None}
                                for k, v in sc.crop_prices.items()},
                "seed_prices_per_lb": sc.seed_prices,
            },
            "baseline_implied_nutrient_prices_per_lb": implied_nutrient_prices(baseline),
            "rules": {
                "D": "seed + fertilizer (N + P + K + S) + plant protection",
                "fertilizer": "lb/ac x $/lb per nutrient (P as P2O5, K as K2O)",
                "C": "target yield x farm price (futures + basis when given)",
                "held_at_baseline": "plant protection; seed unless a seed $/lb is given; "
                                    "any nutrient or crop without a scenario price",
            },
        },
        "results": [run_commodity(rec, sc) for rec in baseline.values()],
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def flat_rows(result: dict) -> list[dict]:
    rows = []
    for r in result["results"]:
        b, s, dl = r["baseline"], r["scenario"], r["delta"]
        pulse = r["price_basis"] == "c/lb"
        row = {
            "Commodity": r["commodity"],
            "Variety": r["variety"],
            "Baseline Year": r["baseline_year"],
            "Price Basis": r["price_basis"],
            "Target Yield (bu/ac)": r["target_yield_bu_ac"],
            "Target Yield (lb/ac)": r["target_yield_lbs_ac"],
            "Price Source": r["price_source"],
            "Baseline Farm Price ($/bu)": b["farm_price_per_bu"],
            "Scenario Farm Price ($/bu)": s["farm_price_per_bu"],
            "Baseline Farm Price (c/lb)": b.get("farm_price_cents_lb"),
            "Scenario Farm Price (c/lb)": s.get("farm_price_cents_lb"),
        }
        for nut, fl in r["fertilizer"].items():
            row[f"{nut} Rate (lb/ac)"] = fl["lbs_ac"]
            row[f"{nut} Baseline ($/ac)"] = fl["baseline"]
            row[f"{nut} Scenario ($/ac)"] = fl["scenario"]
        row.update({
            "Baseline Gross Revenue (C) ($/ac)": b["gross_revenue"],
            "Scenario Gross Revenue (C) ($/ac)": s["gross_revenue"],
            "Seed ($/ac)": s["seed_total"],
            "Baseline Fertilizer ($/ac)": b["fert_total"],
            "Scenario Fertilizer ($/ac)": s["fert_total"],
            "Plant Protection ($/ac)": s["protection_total"],
            "Baseline Direct Cash Inputs (D) ($/ac)": b["direct_cash_inputs"],
            "Scenario Direct Cash Inputs (D) ($/ac)": s["direct_cash_inputs"],
            "Baseline Return Over Direct Cash Inputs ($/ac)": b["return_over_direct"],
            "Scenario Return Over Direct Cash Inputs ($/ac)": s["return_over_direct"],
            "Change in Return ($/ac)": dl["return_over_direct"],
            "Scenario Breakeven Yield (bu/ac)": s["breakeven_yield_bu_ac"],
            "Scenario Breakeven Yield (lb/ac)": s.get("breakeven_yield_lbs_ac") if pulse else None,
            "Scenario Breakeven Price ($/bu)": s["breakeven_price_per_bu"],
            "Scenario Breakeven Price (c/lb)": s.get("breakeven_price_cents_lb") if pulse else None,
            "Notes": "; ".join(r["notes"]),
        })
        rows.append(row)
    return rows


def input_rows(result: dict) -> list[dict]:
    meta = result["meta"]
    implied = meta["baseline_implied_nutrient_prices_per_lb"]
    rows = [{"Input": f"{nut} price ($/lb{' P2O5' if nut == 'P' else ' K2O' if nut == 'K' else ''})",
             "Scenario": meta["inputs"]["nutrient_prices_per_lb"].get(nut),
             f"Baseline {meta['baseline_year']} implied": implied.get(nut)} for nut, _, _ in NUTRIENTS]
    for c, v in meta["inputs"]["crop_prices"].items():
        rows.append({"Input": f"{c} price", "Scenario": json.dumps(v), f"Baseline {meta['baseline_year']} implied": None})
    for c, v in meta["inputs"]["seed_prices_per_lb"].items():
        rows.append({"Input": f"{c} seed ($/lb)", "Scenario": v, f"Baseline {meta['baseline_year']} implied": None})
    return rows


def write_outputs(result: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", result["meta"]["scenario"].lower()).strip("_") or "scenario"
    stem = f"sk_scenario_{slug}_base{result['meta']['baseline_year']}"
    paths = [out_dir / f"{stem}.{ext}" for ext in ("json", "xlsx", "csv")]

    paths[0].write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    flat = pd.DataFrame(flat_rows(result))
    flat.to_csv(paths[2], index=False, encoding="utf-8-sig")
    inputs = pd.DataFrame(input_rows(result))

    fill = PatternFill("solid", fgColor="1F4E78")
    with pd.ExcelWriter(paths[1], engine="openpyxl") as xw:
        for name, df in (("Scenario_Results", flat), ("Inputs", inputs)):
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "B2"
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = fill
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[1].height = 48
            for idx, col in enumerate(df.columns, start=1):
                letter = get_column_letter(idx)
                ws.column_dimensions[letter].width = 60 if col in ("Notes", "Scenario") and name == "Inputs" \
                    else 50 if col == "Notes" else min(max(len(str(col)) * 0.8, 10), 24)
                if col.endswith("($/ac)") or "$/bu" in col or "c/lb" in col or "$/lb" in col:
                    for cell in ws[letter][1:]:
                        cell.number_format = "#,##0.00"
            ws.auto_filter.ref = ws.dimensions
    return paths


# ---------------------------------------------------------------------------
# Console + verification
# ---------------------------------------------------------------------------
def _f(v: float | None, nd: int = 2, sign: bool = False) -> str:
    if v is None:
        return "-"
    return f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"


def print_comparison(result: dict) -> None:
    meta = result["meta"]
    implied = meta["baseline_implied_nutrient_prices_per_lb"]
    print(f"\nScenario '{meta['scenario']}' vs {meta['baseline_year']} MVS baseline")
    print("Nutrient $/lb   " + "   ".join(
        f"{n}: {_f(meta['inputs']['nutrient_prices_per_lb'].get(n))} (base {_f(implied.get(n), 3)})"
        for n, _, _ in NUTRIENTS))
    hdr = (f"{'Commodity':<12} {'Price base -> scen':>24} {'C base':>8} {'C scen':>8} {'Fert base':>9} "
           f"{'Fert scen':>9} {'D base':>8} {'D scen':>8} {'Return base':>11} {'Return scen':>11} "
           f"{'Change':>9} {'BE yield':>10} {'BE price':>12}")
    print(hdr)
    print("-" * len(hdr))
    for r in result["results"]:
        b, s, dl = r["baseline"], r["scenario"], r["delta"]
        if r["price_basis"] == "c/lb":
            price = f"{_f(b['farm_price_cents_lb'])} -> {_f(s['farm_price_cents_lb'])} c/lb"
            be_y = f"{_f(s['breakeven_yield_lbs_ac'], 0)} lb"
            be_p = f"{_f(s['breakeven_price_cents_lb'])} c/lb"
        else:
            price = f"{_f(b['farm_price_per_bu'])} -> {_f(s['farm_price_per_bu'])} $/bu"
            be_y = f"{_f(s['breakeven_yield_bu_ac'], 1)} bu"
            be_p = f"{_f(s['breakeven_price_per_bu'])} $/bu"
        flag = "*" if r["price_source"] != "held at baseline" else " "
        print(f"{r['commodity']:<11}{flag} {price:>24} {_f(b['gross_revenue']):>8} {_f(s['gross_revenue']):>8} "
              f"{_f(b['fert_total']):>9} {_f(s['fert_total']):>9} {_f(b['direct_cash_inputs']):>8} "
              f"{_f(s['direct_cash_inputs']):>8} {_f(b['return_over_direct']):>11} "
              f"{_f(s['return_over_direct']):>11} {_f(dl['return_over_direct'], sign=True):>9} "
              f"{be_y:>10} {be_p:>12}")
    print("* crop price overridden in this scenario; others held at baseline price. All $ are $/ac.")


def self_checks(mvs_path: Path = OUTPUT_JSON, year: int = 2026) -> bool:
    """Engine identities against the MVS baseline and independent hand calculations."""
    base = load_baseline(year, mvs_path)
    checks: list[tuple[str, bool, str]] = []
    tol = 1e-6

    # 1. Null scenario reproduces the baseline exactly.
    null = run_scenario(Scenario(name="null", baseline_year=year), mvs_path)
    worst = max(abs(r["scenario"][k] - r["baseline"][k]) for r in null["results"]
                for k in ("gross_revenue", "direct_cash_inputs", "return_over_direct"))
    checks.append(("null scenario == baseline", worst < tol, f"max diff {worst:.2e}"))

    # 2. Baseline engine economics == MVS record (C, D, C-D).
    worst = max(max(abs(r["baseline"]["gross_revenue"] - base[r["commodity"]]["revenue"]["gross_revenue"]),
                    abs(r["baseline"]["direct_cash_inputs"]
                        - base[r["commodity"]]["direct_costs_per_ac"]["direct_cash_inputs"]),
                    abs(r["baseline"]["return_over_direct"]
                        - base[r["commodity"]]["returns"]["return_over_direct"]))
                for r in null["results"])
    checks.append(("engine baseline == MVS record", worst < 1e-3, f"max diff {worst:.2e}"))

    # 3. Re-pricing at each record's own implied $/lb reproduces its fertilizer cost.
    ok, worst = True, 0.0
    for rec in base.values():
        prices = {n: rec["direct_costs_per_ac"][ck] / rec["agronomics"][rk]
                  for n, rk, ck in NUTRIENTS if rec["agronomics"].get(rk)}
        res = run_commodity(rec, Scenario(name="implied", baseline_year=year, nutrient_prices=prices))
        worst = max(worst, abs(res["scenario"]["fert_total"] - rec["direct_costs_per_ac"]["fert_total"]))
    checks.append(("implied $/lb re-pricing == baseline fertilizer", worst < 1e-3, f"max diff {worst:.2e}"))

    # 4. Hand calculation for the sample inputs.
    sample = run_scenario(Scenario.from_dict(SAMPLE_SCENARIO, year), mvs_path)
    by = {r["commodity"]: r for r in sample["results"]}
    np_ = SAMPLE_SCENARIO["nutrient_prices"]
    for commodity, price_bu in (("Wheat", 10.15), ("Canola", 14.50),
                                ("Lentils", 32.0 / 100 * PULSE_LBS_PER_BU)):
        rec = base[commodity]
        a, c = rec["agronomics"], rec["direct_costs_per_ac"]
        fert = sum((a[rk] or 0) * np_[n] if a.get(rk) is not None else c[ck] for n, rk, ck in NUTRIENTS)
        d = c["seed_total"] + fert + c["protection_total"]
        y_bu = (rec["revenue"]["target_yield_lbs_ac"] / PULSE_LBS_PER_BU if is_pulse(commodity)
                else rec["revenue"]["target_yield_bu_ac"])
        rev = y_bu * price_bu
        got = by[commodity]["scenario"]
        diff = max(abs(got["fert_total"] - fert), abs(got["direct_cash_inputs"] - d),
                   abs(got["gross_revenue"] - rev), abs(got["return_over_direct"] - (rev - d)),
                   abs(got["breakeven_price_per_bu"] - d / y_bu),
                   abs(got["breakeven_yield_bu_ac"] - d / price_bu))
        checks.append((f"hand calc {commodity}", diff < tol, f"max diff {diff:.2e}"))

    # 5. Pulse units: 32 c/lb on lb/ac yield == $/bu on bu/ac yield.
    lent = by["Lentils"]
    via_lb = base["Lentils"]["revenue"]["target_yield_lbs_ac"] * 0.32
    diff = abs(lent["scenario"]["gross_revenue"] - via_lb)
    checks.append(("pulse c/lb x lb/ac == $/bu x bu/ac", diff < tol, f"diff {diff:.2e}"))

    print("\nSelf-checks")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<48} {detail}")
    return all(ok for _, ok, _ in checks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", type=Path, help="scenario JSON (default: built-in sample)")
    ap.add_argument("--baseline-year", type=int, help="MVS year to re-price (default: scenario's, else 2026)")
    ap.add_argument("--mvs", type=Path, default=OUTPUT_JSON, help="MVS database JSON")
    ap.add_argument("--out-dir", type=Path, default=SCENARIO_DIR)
    ap.add_argument("--no-checks", action="store_true", help="skip self-checks")
    args = ap.parse_args(argv)

    spec = json.loads(args.scenario.read_text(encoding="utf-8")) if args.scenario else SAMPLE_SCENARIO
    sc = Scenario.from_dict(spec, args.baseline_year)
    result = run_scenario(sc, args.mvs)
    print_comparison(result)
    for path in write_outputs(result, args.out_dir):
        print(f"  wrote {path}")

    if not args.no_checks and not self_checks(args.mvs, sc.baseline_year):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
