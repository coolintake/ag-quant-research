"""Price input templates, by province.

    Input_Prices/Input_price_template.csv   retail fertilizer benchmarks: product, grade and the
                                            $/tonne -> $/lb nutrient conversion (per province)
    Input_Prices/crop_price_template.csv    dated grain-bid snapshots: Date, Province, Crop_Key,
                                            Price (or Futures + Basis), Unit

Crop keys are the canonical keys shared by both provinces (config: MVS_COMMODITY_ZONE_MAP
crop_key for SK, AB_CROPS for AB). Rows with no price (blank template rows) are skipped.

    from input_parser import load_fertilizer_benchmarks, load_crop_bids
    load_fertilizer_benchmarks("AB")          # {"N": {...}, "P": {...}, "K": {...}, "S": {...}}
    load_crop_bids("AB", as_of="2027-02-10")  # latest snapshot per crop on or before the date

    python -m src.input_parser --province AB [--as-of 2027-02-10]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # flat imports under `python -m src.…`

from config import (AB_CROPS, CROP_PRICE_TEMPLATE, INPUT_PRICE_TEMPLATE,  # noqa: E402
                    MVS_COMMODITY_ZONE_MAP, PULSE_LBS_PER_BU, SK_EXTRA_CROPS)

PROVINCES = ("SK", "AB")
LB_PER_TONNE = 2204.62


def crop_keys(province: str) -> dict[str, str]:
    """{crop_key: price basis ('$/bu' or 'c/lb')} for a province."""
    province = _province(province)
    if province == "SK":
        keys = {r["crop_key"]: r["price_basis"] for r in MVS_COMMODITY_ZONE_MAP.values()}
        keys.update({r["crop_key"]: r["price_basis"] for r in SK_EXTRA_CROPS.values()})
        return keys
    return {k: v["price_basis"] for k, v in AB_CROPS.items()}


def _province(p: str) -> str:
    p = (p or "").strip().upper()
    if p not in PROVINCES:
        raise ValueError(f"province must be one of {PROVINCES}, got {p!r}")
    return p


def _plain(text: str) -> str:
    """Strip the template's LaTeX-style markup: '$\\$ / \\text{tonne}$' -> '$ / tonne'."""
    t = (text or "").replace("\\$", "\x00").replace("$", "").replace("\x00", "$")   # drop math delimiters
    t = re.sub(r"\\text\{([^}]*)\}", r"\1", t)
    t = t.replace("\\div", "÷").replace("\\times", "×").replace("_", "").replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", t).strip()


def _numbers(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text or "")]


# ---------------------------------------------------------------------------
# Fertilizer benchmarks
# ---------------------------------------------------------------------------
def load_fertilizer_benchmarks(province: str = "SK", path: Path = INPUT_PRICE_TEMPLATE) -> dict[str, dict]:
    """{nutrient: {product, grade, n_credit, example_per_lb, conversion}} for one province.

    grade     : nutrient content of the benchmark product (0.46 for urea)
    n_credit  : lb N per lb product credited at the N price (MAP 0.11, AS 0.205 in AB;
                0 where the template charges the whole product to the primary nutrient)
    """
    province = _province(province)
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("Province") or "").strip().upper() != province:
                continue
            m = re.search(r"\(\s*\$?([NPKS])\$?\s*\)", row["Nutrient Line"])
            if not m:
                raise ValueError(f"cannot read nutrient from {row['Nutrient Line']!r}")
            nutrient = m.group(1)
            conv = row["Unit Conversion Factor"]
            divisors = [float(x) for x in re.findall(r"\\div\s*([0-9.]+)", conv)]
            credit = re.search(r"([0-9.]+)\s*\\times\s*\\?\$?\s*N", conv)
            grade = next((d for d in reversed(divisors) if d < 1), None)
            if grade is None:
                raise ValueError(f"{province} {nutrient}: no nutrient grade in {conv!r}")
            example = _numbers(row.get("Net Price Vector ($/lb)", ""))
            out[nutrient] = {
                "province": province,
                "product": _plain(row["Standard Benchmark Product"]),
                "quote_unit": _plain(row["Retail Quote Unit"]),
                "grade": grade,
                "n_credit": float(credit.group(1)) if credit else 0.0,
                "example_per_lb": example[0] if example else None,
                "conversion": _plain(conv),
            }
    missing = {"N", "P", "K", "S"} - set(out)
    if missing:
        raise ValueError(f"{province}: no template row for {sorted(missing)} in {path.name}")
    return out


def per_lb(quotes_per_tonne: dict[str, float], bench: dict[str, dict]) -> dict[str, float]:
    """$/tonne product quotes -> $/lb nutrient, crediting N where the benchmark says so."""
    n = quotes_per_tonne["N"] / LB_PER_TONNE / bench["N"]["grade"]
    out = {"N": n}
    for k in ("P", "K", "S"):
        out[k] = (quotes_per_tonne[k] / LB_PER_TONNE - bench[k]["n_credit"] * n) / bench[k]["grade"]
    return out


# ---------------------------------------------------------------------------
# Grain bids
# ---------------------------------------------------------------------------
def _unit(u: str) -> str:
    u = (u or "").strip().lower().replace("¢", "c").replace(" ", "")
    return {"$/bu": "$/bu", "c/lb": "c/lb", "cents/lb": "c/lb", "$/lb": "$/lb"}.get(u, u)


def load_crop_bids(province: str = "SK", as_of: str | date | None = None,
                   path: Path = CROP_PRICE_TEMPLATE) -> dict[str, dict]:
    """Latest bid per crop_key on or before `as_of` (default: latest in the file).

    Returns {crop_key: {date, price, unit, per_bu, cents_lb, yield, yield_unit, yield_date, display,
    notes}}: the latest bid (native basis, plus $/bu and c/lb for pulses) and, separately, the
    latest target yield when the template's optional Yield column is filled. Either may be None.
    """
    province = _province(province)
    known = crop_keys(province)
    cutoff = date.fromisoformat(str(as_of)) if as_of else None
    best: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            if (row.get("Province") or "").strip().upper() != province:
                continue
            key = (row.get("Crop_Key") or "").strip()
            if key not in known:
                raise ValueError(f"{path.name} line {n}: unknown {province} crop_key {key!r}; "
                                 f"known: {', '.join(known)}")
            when = date.fromisoformat(row["Date"].strip())
            if cutoff and when > cutoff:
                continue
            pulse = known[key] == "c/lb"
            entry = best.setdefault(key, {"price": None, "date": None, "yield": None, "yield_date": None,
                                          "unit": "c/lb" if pulse else "$/bu", "yield_unit": None,
                                          "per_bu": None, "cents_lb": None, "display": "", "notes": ""})
            # Optional target yield (bu/ac, or lb/ac for pulses): high-yield classes such as CPS / CNHR.
            y = _numbers(row.get("Yield", ""))
            if y and (entry["yield_date"] is None or when.isoformat() >= entry["yield_date"]):
                yu = (row.get("Yield_Unit") or "").strip().lower() or ("lb/ac" if pulse else "bu/ac")
                if yu not in ("bu/ac", "lb/ac"):
                    raise ValueError(f"{path.name} line {n}: Yield_Unit must be bu/ac or lb/ac, got {yu!r}")
                entry.update({"yield": y[0], "yield_unit": yu, "yield_date": when.isoformat()})
            unit = _unit(row.get("Unit"))
            price = _numbers(row.get("Price", ""))
            futures, basis = _numbers(row.get("Futures", "")), re.findall(r"-?\d[\d,]*\.?\d*", row.get("Basis") or "")
            if price:
                value = price[0]
            elif futures:
                value = futures[0] + (float(basis[0].replace(",", "")) if basis else 0.0)
                unit = unit or "$/bu"
            else:
                continue                          # no bid on this row (blank or yield-only)
            if unit == "$/lb":
                value, unit = value * 100, "c/lb"
            if unit == "$/bu":
                per_bu = value
            elif unit == "c/lb":
                if not pulse:
                    raise ValueError(f"{path.name} line {n}: c/lb bid for non-pulse {key!r}")
                per_bu = value / 100 * PULSE_LBS_PER_BU
            else:
                raise ValueError(f"{path.name} line {n}: unit must be $/bu or c/lb, got {row.get('Unit')!r}")
            if entry["date"] is None or when.isoformat() >= entry["date"]:
                entry.update({"date": when.isoformat(), "price": per_bu / PULSE_LBS_PER_BU * 100 if pulse else per_bu,
                              "per_bu": per_bu, "cents_lb": per_bu / PULSE_LBS_PER_BU * 100 if pulse else None,
                              "display": (row.get("Display_Name") or "").strip(),
                              "notes": (row.get("Notes") or "").strip()})
    return {k: v for k, v in best.items() if v["price"] is not None or v["yield"] is not None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--province", default="SK")
    ap.add_argument("--as-of")
    args = ap.parse_args(argv)
    prov = _province(args.province)
    print(f"Fertilizer benchmarks ({prov}):")
    for k, b in load_fertilizer_benchmarks(prov).items():
        print(f"  {k}: {b['product']:<38} grade {b['grade']:.3f}  N credit {b['n_credit']:.3f}  "
              f"e.g. ${b['example_per_lb']}/lb")
    bids = load_crop_bids(prov, args.as_of)
    print(f"Grain bids ({prov}, as of {args.as_of or 'latest'}): {len(bids)} crop(s)")
    for k, b in bids.items():
        price = "-" if b["price"] is None else f"{b['price']:.2f} {b['unit']}"
        yld = "" if b["yield"] is None else f"  yield {b['yield']:g} {b['yield_unit']} ({b['yield_date']})"
        print(f"  {k:<18} {price:>14}  ({b['date']}, {b['notes']}){yld}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
