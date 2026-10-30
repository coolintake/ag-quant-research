"""Readers for the price templates in Input_Prices/.

    fertilizer_quotes.csv      dated retail quotes per province:
                               Date, Province, Nutrient (N/P/K/S), Product, Price, Unit ($/t | $/sh.t), Notes
    crop_price_template.csv    dated grain bids (Price, or Futures + Basis) and optional target Yield
    Input_price_template.csv   human-readable benchmark definitions; must agree with the rules
                               (check_benchmark_template)

Every province code and crop_key is checked against docs/AGENT_RULES.json. Rows without a
price (blank template rows) are skipped; nothing is invented.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from ..config import (CROP_PRICE_TEMPLATE, FERTILIZER_QUOTES, INPUT_PRICE_TEMPLATE, NUTRIENT_KEYS,
                      PULSE_LBS_PER_BU, QUOTE_UNITS, RULES, crop, province)
from .fertilizer import convert_quote


def _numbers(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", text or "")]


def _unit(u: str) -> str:
    u = (u or "").strip().lower().replace("¢", "c").replace(" ", "")
    aliases = {"$/bu": "$/bu", "c/lb": "c/lb", "cents/lb": "c/lb", "$/lb": "$/lb", "$/t": "$/t",
               "$/tonne": "$/t", "$/mt": "$/t", "$/sh.t": "$/sh.t", "$/st": "$/sh.t", "$/shortton": "$/sh.t",
               "$/ton": "$/sh.t"}
    return aliases.get(u, u)


def _cutoff(as_of) -> date | None:
    return date.fromisoformat(str(as_of)) if as_of else None


# ---------------------------------------------------------------------------
# Fertilizer quotes
# ---------------------------------------------------------------------------
def load_fertilizer_quotes(code: str, as_of=None, path: Path = FERTILIZER_QUOTES) -> dict[str, dict]:
    """Latest quote per nutrient product on or before `as_of`, in the province's quote unit.

    Returns {N: {price, unit, date, product, notes, source_price, source_unit}}; a nutrient with
    no quote is absent (callers keep Last Yr for it).
    """
    spec = province(code)
    if not path.exists():
        return {}
    cut = _cutoff(as_of)
    best: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            if (row.get("Province") or "").strip().upper() != spec.code:
                continue
            nut = (row.get("Nutrient") or "").strip().upper()
            if nut not in spec.quote_keys:
                raise ValueError(f"{path.name} line {n}: {spec.code} Nutrient must be one of {spec.quote_keys}, got {nut!r}")
            price = _numbers(row.get("Price", ""))
            if not price:
                continue
            unit = _unit(row.get("Unit"))
            if unit not in QUOTE_UNITS:
                raise ValueError(f"{path.name} line {n}: Unit must be $/t or $/sh.t, got {row.get('Unit')!r}")
            when = date.fromisoformat(row["Date"].strip())
            if cut and when > cut:
                continue
            if nut in best and best[nut]["date"] > when.isoformat():
                continue
            best[nut] = {"price": convert_quote(price[0], unit, spec.quote_unit), "unit": spec.quote_unit,
                         "date": when.isoformat(), "product": (row.get("Product") or "").strip(),
                         "notes": (row.get("Notes") or "").strip(), "source_price": price[0], "source_unit": unit}
    return best


# ---------------------------------------------------------------------------
# Grain bids and target yields
# ---------------------------------------------------------------------------
def load_crop_bids(code: str, as_of=None, path: Path = CROP_PRICE_TEMPLATE) -> dict[str, dict]:
    """Latest bid and, separately, latest target yield per crop_key on or before `as_of`.

    Returns {crop_key: {price (native: $/bu or c/lb), unit, per_bu, date, yield, yield_unit,
    yield_date, display, notes}}; price or yield may be None.
    """
    spec = province(code)
    cut = _cutoff(as_of)
    best: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            if (row.get("Province") or "").strip().upper() != spec.code:
                continue
            key = (row.get("Crop_Key") or "").strip()
            if key not in spec.crops:
                raise ValueError(f"{path.name} line {n}: unknown {spec.code} crop_key {key!r}; "
                                 f"known: {', '.join(spec.crops)}")
            c = crop(key)
            when = date.fromisoformat(row["Date"].strip())
            if cut and when > cut:
                continue
            e = best.setdefault(key, {"price": None, "unit": c.price_basis, "per_bu": None, "date": None,
                                      "yield": None, "yield_unit": None, "yield_date": None,
                                      "display": "", "notes": ""})
            y = _numbers(row.get("Yield", ""))
            if y and (e["yield_date"] is None or when.isoformat() >= e["yield_date"]):
                yu = (row.get("Yield_Unit") or "").strip().lower() or ("lb/ac" if c.pulse else "bu/ac")
                if yu not in ("bu/ac", "lb/ac"):
                    raise ValueError(f"{path.name} line {n}: Yield_Unit must be bu/ac or lb/ac, got {yu!r}")
                if (yu == "lb/ac") != c.pulse:
                    y = [y[0] * PULSE_LBS_PER_BU] if yu == "bu/ac" else [y[0] / PULSE_LBS_PER_BU]
                e.update({"yield": y[0], "yield_unit": "lb/ac" if c.pulse else "bu/ac",
                          "yield_date": when.isoformat()})
            unit = _unit(row.get("Unit"))
            price = _numbers(row.get("Price", ""))
            futures, basis = _numbers(row.get("Futures", "")), _numbers(row.get("Basis", ""))
            if price:
                value = price[0]
            elif futures:
                value, unit = futures[0] + (basis[0] if basis else 0.0), unit or "$/bu"
            else:
                continue
            if unit == "$/lb":
                value, unit = value * 100, "c/lb"
            if unit == "$/bu":
                per_bu = value
            elif unit == "c/lb":
                if not c.pulse:
                    raise ValueError(f"{path.name} line {n}: c/lb bid for non-pulse {key!r}")
                per_bu = value / 100 * PULSE_LBS_PER_BU
            else:
                raise ValueError(f"{path.name} line {n}: unit must be $/bu or c/lb, got {row.get('Unit')!r}")
            if e["date"] is None or when.isoformat() >= e["date"]:
                e.update({"price": per_bu / PULSE_LBS_PER_BU * 100 if c.pulse else per_bu, "per_bu": per_bu,
                          "date": when.isoformat(), "display": (row.get("Display_Name") or "").strip(),
                          "notes": (row.get("Notes") or "").strip()})
    return {k: v for k, v in best.items() if v["price"] is not None or v["yield"] is not None}


# ---------------------------------------------------------------------------
# Benchmark documentation must agree with the rules
# ---------------------------------------------------------------------------
def check_benchmark_template(path: Path = INPUT_PRICE_TEMPLATE) -> list[str]:
    """Differences between Input_price_template.csv and the rules' grades / N content."""
    problems = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for code, spec in RULES.provinces.items():
        for n, prod in {**spec.products, **spec.n_blend}.items():
            row = next((r for r in rows if r.get("Province", "").strip().upper() == code
                        and re.search(rf"\(\s*\$?{n}\$?\s*\)", r.get("Nutrient Line", ""))), None)
            if row is None:
                problems.append(f"{code} {n}: no row in {path.name}")
                continue
            conv = row["Unit Conversion Factor"]
            grade = [float(x) for x in re.findall(r"\\div\s*([0-9.]+)", conv) if float(x) < 1]
            credit = re.search(r"([0-9.]+)\s*\\times\s*\\?\$?\s*N", conv)
            if not grade or abs(grade[-1] - prod.grade) > 1e-9:
                problems.append(f"{code} {n}: template grade {grade[-1:] or '?'} vs rules {prod.grade}")
            got_credit = float(credit.group(1)) if credit else 0.0
            if abs(got_credit - prod.n_content) > 1e-9:
                problems.append(f"{code} {n}: template N credit {got_credit} vs rules {prod.n_content}")
    return problems
