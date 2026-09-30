"""Phase 2 - Tier 1 dynamic market vector engine (Saskatchewan and Alberta).

Re-prices the provincial baselines (Tier 2 physical rates, built by baselines.py) with
current market inputs and recomputes the direct-cash-input RPA.

Market vectors (all optional; anything not given is held at the baseline):
    nutrient_prices  $/lb of nutrient: N, P (as P2O5), K (as K2O), S
    nutrient_quotes  retail $/tonne for urea, MAP, potash, AS; converted with the one
                     N-credit rule used for both provinces (input_parser.per_lb). Explicit
                     nutrient_prices win over quotes.
    crop_prices      per commodity, either
                         {"farm_price": 10.15, "unit": "$/bu"}   or "c/lb" for pulses
                         {"futures": 12.40, "basis": -1.10}      $/bu, farm = futures + basis
    crop_yields      per commodity, target yield (bu/ac, or lb/ac for pulses), e.g. for
                     high-yield classes such as SK CPS / CNHR wheat
    seed_costs       per commodity, seed $/ac (replaces the baseline seed line)
    seed_prices      per commodity, $/lb of seed (re-prices base seed where the baseline
                     carries a lb/ac seeding rate; treatment stays at baseline)

Per commodity:
    Fertilizer ($/ac) = lb N x $N + lb P2O5 x $P + lb K2O x $K + lb S x $S
    D                 = seed + fertilizer + plant protection (baseline $/ac)
    C                 = target yield x farm price
    Return            = C - D
    Breakeven yield   = D / farm price        Breakeven price = D / target yield

A nutrient whose lb/ac rate is unknown keeps its baseline $/ac (flagged).

Usage:
    python src/market_engine.py                        # SK built-in sample + self-checks
    python src/market_engine.py --province AB          # AB sample
    python src/market_engine.py --scenario my.json     # custom scenario file ("province": "AB" allowed)
    python src/market_engine.py --prices-asof 2027-03-25   # bids / yields from crop_price_template.csv
    python src/market_engine.py --baseline-year 2025
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))  # flat imports under `python -m src.…`

import pandas as pd  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from baselines import province_records, quotes_from_per_lb  # noqa: E402
from config import (AB_CROPS, AB_OUTPUT_DIR, MVS_COMMODITY_ZONE_MAP, PULSE_LBS_PER_BU,  # noqa: E402
                    SCENARIO_DIR, SK_EXTRA_CROPS)
from input_parser import load_crop_bids, load_fertilizer_benchmarks, per_lb  # noqa: E402

ENGINE_VERSION = "3.0.0"
SCENARIO_DIRS = {"SK": SCENARIO_DIR, "AB": AB_OUTPUT_DIR / "scenarios"}
NUTRIENTS = (("N", "n_lbs_ac", "fert_n"), ("P", "p_lbs_ac", "fert_p"),
             ("K", "k_lbs_ac", "fert_k"), ("S", "s_lbs_ac", "fert_s"))

SAMPLE_SCENARIO = {
    "name": "sample",
    "province": "SK",
    "description": "Phase 2 verification inputs",
    "baseline_year": 2026,
    "nutrient_prices": {"N": 0.75, "P": 0.65, "K": 0.45, "S": 0.50},
    "crop_prices": {
        "Wheat": {"farm_price": 10.15, "unit": "$/bu"},
        "Canola": {"farm_price": 14.50, "unit": "$/bu"},
        "Red Lentils": {"farm_price": 32.0, "unit": "c/lb"},
    },
}
SAMPLE_SCENARIO_AB = {
    "name": "sample",
    "province": "AB",
    "description": "Alberta verification inputs (retail quotes through the N-credit rule)",
    "baseline_year": 2026,
    "nutrient_quotes": {"N": 800.0, "P": 1150.0, "K": 650.0, "S": 600.0},
    "crop_prices": {"Spring Wheat": {"farm_price": 8.25, "unit": "$/bu"},
                    "Canola": {"farm_price": 14.50, "unit": "$/bu"},
                    "Red Lentils": {"farm_price": 30.0, "unit": "c/lb"}},
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

    def per_bu(self, commodity: str, pulse: bool) -> tuple[float, str]:
        """Farm price in $/bu and a description of where it came from."""
        if self.farm_price is not None:
            if self.unit == "$/bu":
                return self.farm_price, f"farm price override {self.farm_price:g} $/bu"
            if self.unit == "c/lb":
                if not pulse:
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
    province: str = "SK"
    nutrient_prices: dict[str, float] = field(default_factory=dict)
    nutrient_quotes: dict[str, float] = field(default_factory=dict)
    crop_prices: dict[str, CropPrice] = field(default_factory=dict)
    crop_yields: dict[str, float] = field(default_factory=dict)
    seed_costs: dict[str, float] = field(default_factory=dict)
    seed_prices: dict[str, float] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict, baseline_year: int | None = None, province: str | None = None) -> "Scenario":
        prov = (province or d.get("province") or "SK").upper()
        aliases = commodity_aliases(prov)

        def resolve(name: str) -> str:
            key = _norm(name)
            if key not in aliases:
                raise ValueError(f"unknown {prov} commodity {name!r}; known: {', '.join(sorted(set(aliases.values())))}")
            return aliases[key]

        def nutrients(block: str) -> dict[str, float]:
            vals = {k.upper(): float(v) for k, v in (d.get(block) or {}).items()}
            bad = set(vals) - {n for n, _, _ in NUTRIENTS}
            if bad:
                raise ValueError(f"unknown nutrient(s) {sorted(bad)} in {block}; use N, P, K, S")
            return vals

        return cls(
            name=d.get("name", "scenario"),
            description=d.get("description", ""),
            baseline_year=int(baseline_year or d.get("baseline_year") or 2026),
            province=prov,
            nutrient_prices=nutrients("nutrient_prices"),
            nutrient_quotes=nutrients("nutrient_quotes"),
            crop_prices={resolve(k): CropPrice(**v) for k, v in (d.get("crop_prices") or {}).items()},
            crop_yields={resolve(k): float(v) for k, v in (d.get("crop_yields") or {}).items()},
            seed_costs={resolve(k): float(v) for k, v in (d.get("seed_costs") or {}).items()},
            seed_prices={resolve(k): float(v) for k, v in (d.get("seed_prices") or {}).items()},
        )

    def per_lb(self) -> dict[str, float]:
        """Nutrient $/lb for this scenario: explicit $/lb, else retail quotes converted with
        the standardized N-credit rule (quotes need all four products)."""
        out = {}
        if self.nutrient_quotes:
            missing = {"N", "P", "K", "S"} - set(self.nutrient_quotes)
            if missing:
                raise ValueError(f"nutrient_quotes needs N, P, K and S (missing {sorted(missing)})")
            out = per_lb(self.nutrient_quotes, load_fertilizer_benchmarks(self.province))
        out.update(self.nutrient_prices)
        return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def commodity_aliases(province: str = "SK") -> dict[str, str]:
    """Names a scenario may use (commodity, crop key, variety, guide names) -> baseline key."""
    out: dict[str, str] = {}
    if province == "SK":
        for commodity, rule in MVS_COMMODITY_ZONE_MAP.items():
            for name in (commodity, rule["crop_key"], rule.get("variety") or "", *rule["source_names"]):
                if name:
                    out.setdefault(_norm(name), commodity)
        for name, spec in SK_EXTRA_CROPS.items():
            for alias in (name, spec["crop_key"], spec["display"]):
                out.setdefault(_norm(alias), name)
    else:
        for key, spec in AB_CROPS.items():
            for alias in (spec["display"], key):
                out.setdefault(_norm(alias), spec["display"])
        out.setdefault(_norm("Canola"), AB_CROPS["canola"]["display"])
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def load_baseline(year: int, province: str = "SK") -> dict[str, dict]:
    """Provincial baseline records (MVS schema) from the aggregation layer."""
    return province_records(province, year)


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


def run_commodity(rec: dict, sc: Scenario, prices: dict[str, float] | None = None) -> dict:
    commodity = rec["commodity"]
    pulse = rec["price_basis"] == "c/lb"
    rev, cost, agro = rec["revenue"], rec["direct_costs_per_ac"], rec["agronomics"]
    notes: list[str] = list(rec.get("notes", [])[:1]) if rec.get("synthetic") else []
    prices = sc.per_lb() if prices is None else prices
    base_yield_bu = rev["target_yield_bu_ac"]
    if pulse and rev.get("target_yield_lbs_ac") is not None:
        base_yield_bu = rev["target_yield_lbs_ac"] / PULSE_LBS_PER_BU  # exact, not the rounded bu/ac
    yield_bu = base_yield_bu
    if commodity in sc.crop_yields:
        y = sc.crop_yields[commodity]
        yield_bu = y / PULSE_LBS_PER_BU if pulse else y
        notes.append(f"target yield override {y:g} {'lb/ac' if pulse else 'bu/ac'}")

    # Fertilizer, nutrient by nutrient
    fert_lines = {}
    for nut, rate_key, cost_key in NUTRIENTS:
        lbs, base_cost, price = agro.get(rate_key), cost.get(cost_key) or 0.0, prices.get(nut)
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

    # Seed: a $/ac override, else re-priced with a $/lb seed price and a lb/ac rate
    seed = cost["seed_total"]
    seed_price = sc.seed_prices.get(commodity)
    if commodity in sc.seed_costs:
        seed = sc.seed_costs[commodity]
        notes.append(f"seed cost override ${seed:.2f}/ac")
    elif seed_price is not None:
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
        price_bu, price_src = sc.crop_prices[commodity].per_bu(commodity, pulse)
    else:
        price_bu, price_src = base_price, "held at baseline"

    baseline = economics(base_yield_bu, base_price, cost["seed_total"], cost["fert_total"],
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
        "target_yield_lbs_ac": yield_bu * PULSE_LBS_PER_BU if pulse else None,
        "price_source": price_src,
        "fertilizer": fert_lines,
        "baseline": baseline,
        "scenario": scenario,
        "delta": delta,
        "notes": notes,
    }


def run_scenario(sc: Scenario) -> dict:
    baseline = load_baseline(sc.baseline_year, sc.province)
    missing = (set(sc.crop_prices) | set(sc.crop_yields) | set(sc.seed_costs)) - set(baseline)
    if missing:
        raise ValueError(f"no {sc.province} {sc.baseline_year} baseline for {sorted(missing)}")
    prices = sc.per_lb()
    return {
        "meta": {
            "engine_version": ENGINE_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "province": sc.province,
            "scenario": sc.name,
            "description": sc.description,
            "baseline_year": sc.baseline_year,
            "inputs": {
                "nutrient_prices_per_lb": prices,
                "nutrient_quotes_per_tonne": sc.nutrient_quotes,
                "crop_prices": {k: {kk: vv for kk, vv in vars(v).items() if vv is not None}
                                for k, v in sc.crop_prices.items()},
                "crop_yields": sc.crop_yields,
                "seed_costs_per_ac": sc.seed_costs,
                "seed_prices_per_lb": sc.seed_prices,
            },
            "baseline_implied_nutrient_prices_per_lb": implied_nutrient_prices(baseline),
            "rules": {
                "D": "seed + fertilizer (N + P + K + S) + plant protection",
                "fertilizer": "lb/ac x $/lb per nutrient (P as P2O5, K as K2O); $/tonne quotes -> $/lb with "
                              "the N in MAP and AS credited at the $/lb N (both provinces)",
                "C": "target yield x farm price (futures + basis when given)",
                "held_at_baseline": "plant protection; seed unless a seed $/ac or $/lb is given; "
                                    "any nutrient, crop price or yield not given",
            },
        },
        "results": [run_commodity(rec, sc, prices) for rec in baseline.values()],
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


def write_outputs(result: dict, out_dir: Path | None = None) -> list[Path]:
    prov = result["meta"]["province"]
    out_dir = out_dir or SCENARIO_DIRS[prov]
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", result["meta"]["scenario"].lower()).strip("_") or "scenario"
    stem = f"{prov.lower()}_scenario_{slug}_base{result['meta']['baseline_year']}"
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
    print(f"\n{meta['province']} scenario '{meta['scenario']}' vs {meta['baseline_year']} baseline")
    print("Nutrient $/lb   " + "   ".join(
        f"{n}: {_f(meta['inputs']['nutrient_prices_per_lb'].get(n))} (base {_f(implied.get(n), 3)})"
        for n, _, _ in NUTRIENTS))
    hdr = (f"{'Commodity':<20} {'Price base -> scen':>24} {'C base':>8} {'C scen':>8} {'Fert base':>9} "
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
        print(f"{r['commodity']:<19}{flag} {price:>24} {_f(b['gross_revenue']):>8} {_f(s['gross_revenue']):>8} "
              f"{_f(b['fert_total']):>9} {_f(s['fert_total']):>9} {_f(b['direct_cash_inputs']):>8} "
              f"{_f(s['direct_cash_inputs']):>8} {_f(b['return_over_direct']):>11} "
              f"{_f(s['return_over_direct']):>11} {_f(dl['return_over_direct'], sign=True):>9} "
              f"{be_y:>10} {be_p:>12}")
    print("* crop price overridden in this scenario; others held at baseline price. All $ are $/ac.")


def self_checks(year: int = 2026) -> bool:
    """Engine identities against both provincial baselines and independent hand calculations."""
    base = load_baseline(year, "SK")
    checks: list[tuple[str, bool, str]] = []
    tol = 1e-6

    # 1. Null scenario reproduces the baseline exactly.
    null = run_scenario(Scenario(name="null", baseline_year=year))
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
    for rec in (r for r in base.values() if not r.get("synthetic")):
        prices = {n: rec["direct_costs_per_ac"][ck] / rec["agronomics"][rk]
                  for n, rk, ck in NUTRIENTS if rec["agronomics"].get(rk)}
        res = run_commodity(rec, Scenario(name="implied", baseline_year=year, nutrient_prices=prices))
        worst = max(worst, abs(res["scenario"]["fert_total"] - rec["direct_costs_per_ac"]["fert_total"]))
    checks.append(("implied $/lb re-pricing == baseline fertilizer", worst < 1e-3, f"max diff {worst:.2e}"))

    # 4. Hand calculation for the sample inputs.
    sample = run_scenario(Scenario.from_dict(SAMPLE_SCENARIO, year))
    by = {r["commodity"]: r for r in sample["results"]}
    np_ = SAMPLE_SCENARIO["nutrient_prices"]
    for commodity, price_bu in (("Wheat", 10.15), ("Canola", 14.50),
                                ("Lentils", 32.0 / 100 * PULSE_LBS_PER_BU)):
        rec = base[commodity]
        a, c = rec["agronomics"], rec["direct_costs_per_ac"]
        fert = sum((a[rk] or 0) * np_[n] if a.get(rk) is not None else c[ck] for n, rk, ck in NUTRIENTS)
        d = c["seed_total"] + fert + c["protection_total"]
        y_bu = (rec["revenue"]["target_yield_lbs_ac"] / PULSE_LBS_PER_BU if rec["price_basis"] == "c/lb"
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

    # 6. SK CPS wheat: no guide history; inherits Wheat's NPKS, chemical and seed.
    if "CPS Wheat" in base:
        cps, wht = by["CPS Wheat"]["scenario"], by["Wheat"]["scenario"]
        same = max(abs(cps[k] - wht[k]) for k in ("seed_total", "fert_total", "protection_total"))
        checks.append(("CPS wheat inherits Wheat seed / NPKS / chemical", same < tol, f"max diff {same:.2e}"))
        live = run_scenario(Scenario.from_dict({"crop_prices": {"CPS Wheat": {"farm_price": 7.60}},
                                                "crop_yields": {"CPS Wheat": 64}}, year))
        got = {r["commodity"]: r for r in live["results"]}
        want = 64 * 7.60 - got["CPS Wheat"]["scenario"]["direct_cash_inputs"]
        diff = abs(got["CPS Wheat"]["scenario"]["return_over_direct"] - want)
        moved = abs(got["Wheat"]["scenario"]["return_over_direct"] - got["Wheat"]["baseline"]["return_over_direct"])
        checks.append(("CPS own price + yield; Wheat unaffected", diff < tol and moved < tol,
                       f"CPS RPA {got['CPS Wheat']['scenario']['return_over_direct']:.2f}"))

    # 7. Standardized N credit: quotes -> $/lb -> quotes round-trips for both provinces.
    worst = 0.0
    for prov in ("SK", "AB"):
        sample_lb = {"N": 0.8, "P": 0.9, "K": 0.5, "S": 0.45}
        q = quotes_from_per_lb(sample_lb, prov)
        back = per_lb(q, load_fertilizer_benchmarks(prov))
        worst = max(worst, max(abs(back[k] - sample_lb[k]) for k in sample_lb))
    checks.append(("N-credit quotes <-> $/lb round-trip (SK, AB)", worst < 1e-9, f"max diff {worst:.2e}"))

    # 8. Alberta: null scenario == baseline; the guide's own quotes reproduce its published $/lb.
    from baselines import ab_guide_quotes  # noqa: PLC0415
    ab_null = run_scenario(Scenario(name="null", baseline_year=year, province="AB"))
    worst = max(abs(r["scenario"][k] - r["baseline"][k]) for r in ab_null["results"]
                for k in ("gross_revenue", "direct_cash_inputs", "return_over_direct"))
    checks.append(("AB null scenario == baseline", worst < tol, f"max diff {worst:.2e}"))
    guide = ab_guide_quotes(year)
    conv = Scenario(name="q", baseline_year=year, province="AB",
                    nutrient_quotes={k: v["per_tonne"] for k, v in guide.items()}).per_lb()
    worst = max(abs(conv[k] - guide[k]["per_lb"]) for k in guide)
    checks.append(("AB guide $/tonne -> published $/lb (N credit)", worst < 0.005,
                   ", ".join(f"{k} {conv[k]:.3f}" for k in "NPKS")))
    ab_sample = run_scenario(Scenario.from_dict(SAMPLE_SCENARIO_AB, year))
    ab_by = {r["commodity"]: r for r in ab_sample["results"]}
    ab_base = load_baseline(year, "AB")
    rec = ab_base["Spring Wheat"]
    lb = Scenario.from_dict(SAMPLE_SCENARIO_AB, year).per_lb()
    a, c = rec["agronomics"], rec["direct_costs_per_ac"]
    d = c["seed_total"] + sum((a[rk] or 0) * lb[n] for n, rk, _ in NUTRIENTS) + c["protection_total"]
    want = rec["revenue"]["target_yield_bu_ac"] * 8.25 - d
    diff = abs(ab_by["Spring Wheat"]["scenario"]["return_over_direct"] - want)
    checks.append(("AB hand calc Spring Wheat", diff < tol, f"max diff {diff:.2e}"))

    print("\nSelf-checks")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<48} {detail}")
    return all(ok for _, ok, _ in checks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", type=Path, help="scenario JSON (default: built-in sample)")
    ap.add_argument("--baseline-year", type=int, help="guide year to re-price (default: scenario's, else 2026)")
    ap.add_argument("--province", choices=["SK", "AB"], help="default: the scenario's, else SK")
    ap.add_argument("--prices-asof", help="add bids / yields from crop_price_template.csv (latest on or before)")
    ap.add_argument("--out-dir", type=Path, help="default: output/<province>/scenarios")
    ap.add_argument("--no-checks", action="store_true", help="skip self-checks")
    args = ap.parse_args(argv)

    if args.scenario:
        spec = json.loads(args.scenario.read_text(encoding="utf-8"))
    else:
        spec = SAMPLE_SCENARIO_AB if (args.province or "SK") == "AB" else SAMPLE_SCENARIO
    spec = dict(spec)
    prov = (args.province or spec.get("province") or "SK").upper()
    if args.prices_asof:
        bids = load_crop_bids(prov, args.prices_asof)
        spec["crop_prices"] = {**{k: {"farm_price": b["price"], "unit": b["unit"]}
                                  for k, b in bids.items() if b["price"] is not None},
                               **(spec.get("crop_prices") or {})}
        spec["crop_yields"] = {**{k: b["yield"] for k, b in bids.items() if b["yield"] is not None},
                               **(spec.get("crop_yields") or {})}
        spec["name"] = f"{spec.get('name', 'scenario')}_asof_{args.prices_asof}"
    sc = Scenario.from_dict(spec, args.baseline_year, prov)
    result = run_scenario(sc)
    print_comparison(result)
    for path in write_outputs(result, args.out_dir):
        print(f"  wrote {path}")

    if not args.no_checks and not self_checks(sc.baseline_year):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
