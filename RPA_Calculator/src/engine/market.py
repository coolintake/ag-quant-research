"""Tier 1 market engine: pure RPA calculation on canonical crop baselines.

For each CropBaseline(province, year, crop_key) and a set of market vectors:

    Fertilizer ($/ac)  = sum over N, P2O5, K2O, S of lb/ac x $/lb      [NCREDIT for $/lb]
    D                  = seed + fertilizer + chemicals                  [DCASH]
    C                  = whole target yield x farm price                [YIELD-ROUND]
    Return (RPA)       = C - D
    Breakeven price    = D / yield         Breakeven yield = D / price

"Last Yr" is the same calculation at the guide's bid and the province's Last Yr quotes,
so a scenario with no inputs reproduces it exactly, and the workbook shows the same numbers.

Market vectors (all optional; anything not given stays at Last Yr):
    nutrient_quotes   retail quotes {N: urea, P: MAP, K: potash, S: AS (+ NH3 for MB)} in $/t or
                      $/sh.t (quote_unit); converted per province with the N-credit rule
    n_shares          MB only: share of applied N from NH3 (default: the guide year's) [NCREDIT-MB]
    nutrient_prices   $/lb of nutrient; override the quote-derived $/lb nutrient by nutrient
    crop_prices       {crop: {"farm_price": x, "unit": "$/bu"|"c/lb"}} or {"futures": f, "basis": b}
    crop_yields       {crop: target yield (bu/ac, or lb/ac for pulses)}
    seed_costs        {crop: seed $/ac}          seed_prices  {crop: seed $/lb}

Crops are named by crop_key, trade label or any source alias from docs/AGENT_RULES.json.

    python -m src.engine.market                          # SK sample scenario
    python -m src.engine.market --province AB
    python -m src.engine.market --scenario my.json       # "province": "AB" allowed
    python -m src.engine.market --prices-asof 2027-03-25 # bids, yields, fertilizer quotes from Input_Prices/
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

from ..config import BASELINE_YEAR, NUTRIENT_KEYS, crop, normalize_label, province
from ..normalize.baselines import CropBaseline, last_yr_per_lb, last_yr_quotes, n_shares, province_baselines
from ..normalize.crops import resolve
from ..pricing.fertilizer import convert_quote, per_lb
from ..pricing.templates import load_crop_bids, load_fertilizer_quotes
from ..pricing.units import price_native_from_bu, revenue

ENGINE_VERSION = "4.0.0"
ECON_KEYS = ("yield", "price", "revenue", "seed", "fert", "chem", "cost", "rpa", "be_price", "be_yield")

SAMPLE_SCENARIOS = {
    "SK": {"name": "sample", "province": "SK", "description": "SK verification inputs",
           "nutrient_prices": {"N": 0.75, "P": 0.65, "K": 0.45, "S": 0.50},
           "crop_prices": {"wheat_spring": {"farm_price": 10.15, "unit": "$/bu"},
                           "canola": {"farm_price": 14.50, "unit": "$/bu"},
                           "lentils_red": {"farm_price": 32.0, "unit": "c/lb"}}},
    "AB": {"name": "sample", "province": "AB", "description": "AB verification inputs (retail quotes)",
           "nutrient_quotes": {"N": 800.0, "P": 1150.0, "K": 650.0, "S": 600.0},
           "crop_prices": {"wheat_spring": {"farm_price": 8.25, "unit": "$/bu"},
                           "canola": {"farm_price": 14.50, "unit": "$/bu"},
                           "lentils_red": {"farm_price": 30.0, "unit": "c/lb"}}},
    "MB": {"name": "sample", "province": "MB", "description": "MB verification inputs (retail quotes incl. NH3)",
           "nutrient_quotes": {"N": 780.0, "NH3": 1200.0, "P": 1250.0, "K": 620.0, "S": 580.0},
           "crop_prices": {"wheat_spring": {"farm_price": 8.25, "unit": "$/bu"},
                           "canola": {"farm_price": 14.50, "unit": "$/bu"},
                           "soybeans": {"farm_price": 12.25, "unit": "$/bu"}}},
}


# ---------------------------------------------------------------------------
# Pure calculation
# ---------------------------------------------------------------------------
def economics(b: CropBaseline, prices_per_lb: dict[str, float], price_native: float,
              yield_native: float, seed: float) -> dict[str, float]:
    """C, D, RPA and breakevens for one crop; keys match the workbook's summary rows."""
    c = crop(b.crop_key)
    fert = sum((b.rates.get(n) or 0.0) * prices_per_lb[n] for n in NUTRIENT_KEYS)
    cost = seed + fert + b.chem
    gross = revenue(yield_native, price_native, c)
    scale = 100 if c.pulse else 1
    return {
        "yield": yield_native, "price": price_native, "revenue": gross, "seed": seed, "fert": fert,
        "chem": b.chem, "cost": cost, "rpa": gross - cost,
        "be_price": cost / yield_native * scale if yield_native else None,
        "be_yield": cost / (price_native / scale) if price_native else None,
    }


def last_yr(b: CropBaseline) -> dict[str, float]:
    """Last Yr economics: guide bid, whole guide yield, Last Yr quotes."""
    return economics(b, last_yr_per_lb(b.province, b.year), b.price, b.yield_whole, b.seed)


# ---------------------------------------------------------------------------
# Scenario model
# ---------------------------------------------------------------------------
@dataclass
class CropPrice:
    farm_price: float | None = None
    unit: str = "$/bu"
    futures: float | None = None
    basis: float | None = None

    def native(self, key: str) -> tuple[float, str]:
        """Price in the crop's native unit ($/bu or c/lb) and where it came from."""
        c = crop(key)
        if self.farm_price is not None:
            if self.unit not in ("$/bu", "c/lb"):
                raise ValueError(f"{key}: unit must be '$/bu' or 'c/lb', got {self.unit!r}")
            if self.unit == "c/lb" and not c.pulse:
                raise ValueError(f"{key}: c/lb pricing is for pulses only")
            if self.unit == "c/lb":
                return self.farm_price, f"farm price {self.farm_price:g} c/lb"
            return price_native_from_bu(self.farm_price, c), f"farm price {self.farm_price:g} $/bu"
        if self.futures is not None:
            per_bu = self.futures + (self.basis or 0.0)
            return price_native_from_bu(per_bu, c), f"futures {self.futures:g} + basis {self.basis or 0:+g} $/bu"
        raise ValueError(f"{key}: crop price needs farm_price or futures")


@dataclass
class Scenario:
    name: str
    province: str
    year: int = BASELINE_YEAR
    nutrient_quotes: dict[str, float] = field(default_factory=dict)
    quote_unit: str | None = None
    n_shares: dict[str, float] | None = None
    nutrient_prices: dict[str, float] = field(default_factory=dict)
    crop_prices: dict[str, CropPrice] = field(default_factory=dict)
    crop_yields: dict[str, float] = field(default_factory=dict)
    seed_costs: dict[str, float] = field(default_factory=dict)
    seed_prices: dict[str, float] = field(default_factory=dict)
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict, year: int | None = None, province_code: str | None = None) -> "Scenario":
        code = province(province_code or d.get("province") or "SK").code

        def keyed(block: str, conv=float) -> dict:
            return {resolve_crop(code, k): conv(v) for k, v in (d.get(block) or {}).items()}

        def nutrients(block: str, keys=NUTRIENT_KEYS) -> dict:
            vals = {k.upper(): float(v) for k, v in (d.get(block) or {}).items()}
            if set(vals) - set(keys):
                raise ValueError(f"{block}: {code} takes {', '.join(keys)}")
            return vals

        return cls(name=d.get("name", "scenario"), province=code, year=int(year or d.get("year")
                                                                              or d.get("baseline_year") or BASELINE_YEAR),
                   nutrient_quotes=nutrients("nutrient_quotes", province(code).quote_keys),
                   quote_unit=d.get("quote_unit"),
                   n_shares={k.upper(): float(v) for k, v in d["n_shares"].items()} if d.get("n_shares") else None,
                   nutrient_prices=nutrients("nutrient_prices"),
                   crop_prices={resolve_crop(code, k): CropPrice(**v) for k, v in (d.get("crop_prices") or {}).items()},
                   crop_yields=keyed("crop_yields"), seed_costs=keyed("seed_costs"), seed_prices=keyed("seed_prices"),
                   description=d.get("description", ""))

    def per_lb(self) -> dict[str, float]:
        """$/lb vector: Last Yr, replaced by converted quotes (missing products stay at Last Yr
        quotes), then by explicit $/lb."""
        spec = province(self.province)
        shares = {**n_shares(self.province, self.year), **(self.n_shares or {})}
        out = last_yr_per_lb(self.province, self.year)
        if self.nutrient_quotes or self.n_shares:
            unit = self.quote_unit or spec.quote_unit
            quotes = {n: convert_quote(q, spec.quote_unit, unit) for n, q in last_yr_quotes(self.province, self.year).items()}
            quotes.update(self.nutrient_quotes)
            out = per_lb(quotes, spec, unit, n_shares=shares)
        out = {**out, **self.nutrient_prices}
        return out


def resolve_crop(code: str, name: str) -> str:
    """crop_key from a crop_key, trade label, variety or guide alias."""
    spec = province(code)
    if name in spec.crops:
        return name
    norm = normalize_label(name)
    for key in spec.crops:
        if norm in (normalize_label(crop(key).trade_label), normalize_label(spec.crop(key).variety or "")):
            return key
    key = resolve(code, name)
    if key is None:
        raise ValueError(f"unknown {code} crop {name!r}; known: {', '.join(spec.crops)}")
    return key


def run_scenario(sc: Scenario) -> dict:
    spec = province(sc.province)
    base = province_baselines(sc.province, sc.year)
    keys = {b.crop_key for b in base}
    missing = (set(sc.crop_prices) | set(sc.crop_yields) | set(sc.seed_costs)) - keys
    if missing:
        raise ValueError(f"no {sc.province} {sc.year} baseline for {sorted(missing)}")
    prices = sc.per_lb()
    results = []
    for b in base:
        notes = list(b.flags[:1]) if b.inherits else []
        price, src = (sc.crop_prices[b.crop_key].native(b.crop_key) if b.crop_key in sc.crop_prices
                      else (b.price, "Last Yr (guide)"))
        yld = sc.crop_yields.get(b.crop_key, b.yield_whole)
        seed = b.seed
        if b.crop_key in sc.seed_costs:
            seed = sc.seed_costs[b.crop_key]
            notes.append(f"seed cost override ${seed:.2f}/ac")
        elif b.crop_key in sc.seed_prices:
            if b.seeding_lbs is None:
                notes.append("seed $/lb given but no lb/ac seeding rate; seed held at Last Yr")
            else:
                seed = b.seeding_lbs * sc.seed_prices[b.crop_key] + b.seed_treatment
                notes.append(f"seed re-priced: {b.seeding_lbs:g} lb/ac x ${sc.seed_prices[b.crop_key]:g}/lb")
        ly, now = last_yr(b), economics(b, prices, price, yld, seed)
        results.append({"crop_key": b.crop_key, "trade_label": b.trade_label, "variety": b.variety,
                        "regions": list(b.regions), "price_basis": b.price_basis, "yield_unit": b.yield_unit,
                        "price_source": src, "last_yr": ly, "scenario": now,
                        "delta": {k: now[k] - ly[k] for k in ("revenue", "fert", "cost", "rpa")}, "notes": notes})
    for r, rank in zip(results, _ranks([r["scenario"]["rpa"] for r in results])):
        r["rank"] = rank
    return {
        "meta": {"engine_version": ENGINE_VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "province": spec.code, "scenario": sc.name, "description": sc.description, "year": sc.year,
                 "inputs": {"nutrient_quotes": sc.nutrient_quotes, "quote_unit": sc.quote_unit or spec.quote_unit,
                            "nutrient_per_lb": prices, "last_yr_quotes": last_yr_quotes(spec.code, sc.year),
                            "n_shares": {**n_shares(spec.code, sc.year), **(sc.n_shares or {})},
                            "last_yr_per_lb": last_yr_per_lb(spec.code, sc.year),
                            "crop_prices": {k: {kk: vv for kk, vv in vars(v).items() if vv is not None}
                                            for k, v in sc.crop_prices.items()},
                            "crop_yields": sc.crop_yields, "seed_costs": sc.seed_costs, "seed_prices": sc.seed_prices},
                 "rules": "docs/METHODOLOGY.md: DCASH, NCREDIT, YIELD-ROUND, REGION-ACTIVE, REGION-AVG-RULE, CROP-INHERIT"},
        "results": results,
    }


def _ranks(values: list[float]) -> list[int]:
    """Excel RANK(.., 0): ties share a rank."""
    return [1 + sum(v2 > v + 1e-9 for v2 in values) for v in values]


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def flat_rows(result: dict) -> list[dict]:
    rows = []
    for r in result["results"]:
        ly, s = r["last_yr"], r["scenario"]
        unit_p = "¢/lb" if r["price_basis"] == "c/lb" else "$/bu"
        rows.append({
            "Crop": r["trade_label"], "Crop Key": r["crop_key"], "Regions": " + ".join(r["regions"]),
            f"Target Yield ({r['yield_unit']})": s["yield"], "Price Unit": unit_p,
            "Last Yr Price": ly["price"], "Scenario Price": s["price"], "Price Source": r["price_source"],
            "Last Yr Gross Revenue (C) ($/ac)": ly["revenue"], "Scenario Gross Revenue (C) ($/ac)": s["revenue"],
            "Seed ($/ac)": s["seed"], "Last Yr Fertilizer ($/ac)": ly["fert"], "Scenario Fertilizer ($/ac)": s["fert"],
            "Chemicals ($/ac)": s["chem"], "Last Yr Direct Cash Costs (D) ($/ac)": ly["cost"],
            "Scenario Direct Cash Costs (D) ($/ac)": s["cost"], "Last Yr RPA (C-D) ($/ac)": ly["rpa"],
            "Scenario RPA (C-D) ($/ac)": s["rpa"], "Delta vs Last Yr ($/ac)": r["delta"]["rpa"], "Rank": r["rank"],
            "Breakeven Price": s["be_price"], "Breakeven Yield": s["be_yield"], "Notes": "; ".join(r["notes"]),
        })
    return rows


def write_outputs(result: dict, out_dir: Path | None = None) -> list[Path]:
    spec = province(result["meta"]["province"])
    out_dir = out_dir or spec.scenario_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", result["meta"]["scenario"].lower()).strip("_") or "scenario"
    stem = f"{spec.code.lower()}_scenario_{slug}_{result['meta']['year']}"
    paths = [out_dir / f"{stem}.{ext}" for ext in ("json", "xlsx", "csv")]
    paths[0].write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    flat = pd.DataFrame(flat_rows(result))
    flat.to_csv(paths[2], index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(paths[1], engine="openpyxl") as xw:
        flat.to_excel(xw, sheet_name="Scenario_Results", index=False)
        ws = xw.sheets["Scenario_Results"]
        ws.freeze_panes = "B2"
        for cell in ws[1]:
            cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1E293B")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        for idx, col in enumerate(flat.columns, start=1):
            letter = get_column_letter(idx)
            ws.column_dimensions[letter].width = 50 if col == "Notes" else min(max(len(col) * 0.8, 10), 24)
            if "($/ac)" in col or col.startswith(("Last Yr Price", "Scenario Price", "Breakeven")):
                for cell in ws[letter][1:]:
                    cell.number_format = "#,##0.00"
    return paths


def print_comparison(result: dict) -> None:
    meta = result["meta"]
    lb, ly_lb = meta["inputs"]["nutrient_per_lb"], meta["inputs"]["last_yr_per_lb"]
    print(f"\n{meta['province']} scenario '{meta['scenario']}' vs {meta['year']} Last Yr")
    print("Nutrient $/lb   " + "   ".join(f"{n}: {lb[n]:.3f} (Last Yr {ly_lb[n]:.3f})" for n in NUTRIENT_KEYS))
    hdr = (f"{'Crop':<18} {'Price Last Yr -> scen':>24} {'C LY':>8} {'C scen':>8} {'D LY':>8} {'D scen':>8} "
           f"{'RPA LY':>8} {'RPA scen':>9} {'Change':>9} {'Rank':>5}")
    print(hdr + "\n" + "-" * len(hdr))
    for r in result["results"]:
        ly, s = r["last_yr"], r["scenario"]
        u = "c/lb" if r["price_basis"] == "c/lb" else "$/bu"
        move = f"{ly['price']:.2f} -> {s['price']:.2f} {u}"
        print(f"{r['trade_label']:<18} {move:>24} "
              f"{ly['revenue']:>8.2f} {s['revenue']:>8.2f} {ly['cost']:>8.2f} {s['cost']:>8.2f} "
              f"{ly['rpa']:>8.2f} {s['rpa']:>9.2f} {r['delta']['rpa']:>+9.2f} {'#' + str(r['rank']):>5}")
    print("All $ per acre; yields are whole guide targets unless a scenario yield is given.")


def scenario_from_templates(code: str, as_of: str, base: dict | None = None) -> dict:
    """Fill a scenario from Input_Prices/: latest bids, yields and fertilizer quotes on or
    before `as_of`. Values already in `base` win."""
    spec = dict(base or {"name": "template", "province": code})
    bids = load_crop_bids(code, as_of)
    spec["crop_prices"] = {**{k: {"farm_price": b["price"], "unit": b["unit"]} for k, b in bids.items()
                              if b["price"] is not None}, **(spec.get("crop_prices") or {})}
    spec["crop_yields"] = {**{k: b["yield"] for k, b in bids.items() if b["yield"] is not None},
                           **(spec.get("crop_yields") or {})}
    quotes = load_fertilizer_quotes(code, as_of)
    if quotes:
        spec["nutrient_quotes"] = {**{n: q["price"] for n, q in quotes.items()}, **(spec.get("nutrient_quotes") or {})}
    spec["name"] = f"{spec.get('name', 'scenario')}_asof_{as_of}"
    return spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", type=Path, help="scenario JSON (default: built-in sample for the province)")
    ap.add_argument("--province", help="default: the scenario's, else SK")
    ap.add_argument("--year", type=int, help=f"guide year (default {BASELINE_YEAR})")
    ap.add_argument("--prices-asof", help="bids, yields and fertilizer quotes from Input_Prices/ (YYYY-MM-DD)")
    ap.add_argument("--out-dir", type=Path)
    args = ap.parse_args(argv)
    code = province(args.province or "SK").code
    spec = (json.loads(args.scenario.read_text(encoding="utf-8")) if args.scenario
            else dict(SAMPLE_SCENARIOS.get(code, {"name": "last_yr", "province": code})))
    code = province(args.province or spec.get("province") or code).code
    if args.prices_asof:
        spec = scenario_from_templates(code, args.prices_asof, spec)
    result = run_scenario(Scenario.from_dict(spec, args.year, code))
    print_comparison(result)
    for path in write_outputs(result, args.out_dir):
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
