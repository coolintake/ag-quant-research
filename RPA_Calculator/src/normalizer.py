"""Phase 1.3 - normalize raw guide tables into the Saskatchewan MVS database.

Reads output/sk/intermediate/raw_extracted_tables.json (Phase 1.2) and writes one
record per MVS commodity and year to output/sk/mvs/ as JSON, Excel and CSV.

Per record:
    revenue              target yield and farm price in bu/ac and $/bu, plus lb/ac
                         and c/lb for pulses; gross revenue C = yield x price
    direct_costs_per_ac  Total Direct Variable Expenses (D) = seed (base + treatment)
                         + fertilizer (N + P + K + S) + plant protection
                         (herbicides + insecticides + fungicides). Fuel, repairs,
                         insurance, interest, depreciation and land are excluded.
    returns              return over direct cash inputs (C - D), cash-input
                         break-even yield (D / price) and price (D / yield)
    agronomics           seeding rate and applied N / P2O5 / K2O / S (lb/ac), from
                         the 2019 side lists or the 2020+ agronomics prose
    guide_reference      the guide's own C, full variable expenses and C - D
    zone_detail          the per-zone values as printed, before blending
    notes                data-quality flags; nothing here halts the run

Zone selection follows config.MVS_COMMODITY_ZONE_MAP. Blended commodities (Wheat,
Rye) take the simple arithmetic mean of every printed numeric field across their
zones; C, D, returns and break-evens are then derived from the blended values so
the identities hold exactly on every record.

Usage:
    python src/normalizer.py
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from config import (MVS_COMMODITY_ZONE_MAP, OUTPUT_CSV, OUTPUT_JSON, OUTPUT_XLSX,
                    PULSE_LBS_PER_BU, RAW_TABLES_JSON, SOIL_ZONES, YEARS, is_pulse)

NORMALIZER_VERSION = "2.0.0"

# Seeding lb/ac from a plant stand: plants/ft2 * 43,560 ft2/ac * TKW g/1000 seeds
# / 453.592 g/lb / emergence. Checked against 2023+ Oats, which print both forms.
SQFT_PER_ACRE = 43_560
GRAMS_PER_LB = 453.592

# ---------------------------------------------------------------------------
# Printed (per-zone) fields: (key, header, decimals). Blended before derivation.
# ---------------------------------------------------------------------------
RAW_FIELDS: list[tuple[str, str, int]] = [
    ("target_yield",             "Target Yield (as printed)",                2),
    ("yield_unit",               "Printed Yield Unit",                       0),
    ("market_price",             "Farm Price (as printed)",                  4),
    ("price_unit",               "Printed Price Unit",                       0),
    ("gross_revenue",            "Guide Gross Revenue ($/ac)",               2),
    ("seed",                     "Seed ($/ac)",                              2),
    ("seed_treatments",          "Seed Treatment ($/ac)",                    2),
    ("fert_n",                   "Nitrogen (N) ($/ac)",                      2),
    ("fert_p2o5",                "Phosphate (P) ($/ac)",                     2),
    ("fert_s",                   "Sulphur (S) & Other ($/ac)",               2),
    ("herbicides",               "Herbicides ($/ac)",                        2),
    ("insecticides",             "Insecticides ($/ac)",                      2),
    ("fungicides",               "Fungicides ($/ac)",                        2),
    ("total_variable_expenses",  "Guide Total Variable Expenses ($/ac)",     2),
    ("return_over_variable",     "Guide Return over Variable Exp. ($/ac)",   2),
    ("breakeven_yield_variable", "Guide Breakeven Yield (variable)",         2),
    ("breakeven_yield_total",    "Guide Breakeven Yield (total)",            2),
    ("seeding_rate_lbs_ac",      "Seeding Rate (lb/ac)",                     1),
    ("seeding_rate_basis",       "Seeding Rate Basis",                       0),
    ("seeding_rate_other",       "Seeding Rate (other units)",               0),
    ("n_lbs_ac",                 "Nitrogen (N) (lb N/ac)",                   1),
    ("p2o5_lbs_ac",              "Phosphate (P) (lb P2O5/ac)",               1),
    ("s_lbs_ac",                 "Sulphur (S) (lb S/ac)",                    1),
]
RAW_NUMERIC = [k for k, _, d in RAW_FIELDS if d]
RAW_TEXT = [k for k, _, d in RAW_FIELDS if not d]

# ---------------------------------------------------------------------------
# Output schema: (key, header, group, decimals, description)
# ---------------------------------------------------------------------------
OUT_FIELDS: list[tuple[str, str, str, int, str]] = [
    ("price_basis",              "Price Basis",                             "revenue", 0,
     "native cash price unit: $/bu (cereals, oilseeds) or c/lb (pulses)"),
    ("target_yield_bu_ac",       "Target Yield (bu/ac)",                    "revenue", 2,
     "guide target yield; pulses printed in lb/ac are converted at 60 lb/bu"),
    ("target_yield_lbs_ac",      "Target Yield (lb/ac)",                    "revenue", 1,
     "pulses only"),
    ("farm_price_per_bu",        "Farm Price ($/bu)",                       "revenue", 4,
     "guide estimated farm-gate price"),
    ("farm_price_cents_lb",      "Farm Price (c/lb)",                       "revenue", 2,
     "pulses only"),
    ("gross_revenue",            "Gross Revenue (C) ($/ac)",                "revenue", 2,
     "C = target yield x farm price"),
    ("seed_base",                "Seed - Base ($/ac)",                      "direct_costs_per_ac", 2, "certified seed"),
    ("seed_treatment",           "Seed - Treatment ($/ac)",                 "direct_costs_per_ac", 2,
     "seed treatments / inoculants"),
    ("seed_total",               "Seed Total ($/ac)",                       "direct_costs_per_ac", 2, "base + treatment"),
    ("fert_n",                   "Nitrogen (N) ($/ac)",                     "direct_costs_per_ac", 2, ""),
    ("fert_p",                   "Phosphate (P) ($/ac)",                    "direct_costs_per_ac", 2, "priced as P2O5"),
    ("fert_k",                   "Potash (K) ($/ac)",                       "direct_costs_per_ac", 2,
     "guides budget no potash for MVS crops: 0"),
    ("fert_s",                   "Sulphur (S) ($/ac)",                      "direct_costs_per_ac", 2,
     "guide line 'Sulphur (S) and Other'"),
    ("fert_total",               "Fertilizer Total ($/ac)",                 "direct_costs_per_ac", 2, "N + P + K + S"),
    ("herbicides",               "Herbicides ($/ac)",                       "direct_costs_per_ac", 2, ""),
    ("insecticides",             "Insecticides ($/ac)",                     "direct_costs_per_ac", 2, ""),
    ("fungicides",               "Fungicides ($/ac)",                       "direct_costs_per_ac", 2, ""),
    ("protection_total",         "Plant Protection Total ($/ac)",           "direct_costs_per_ac", 2,
     "herbicides + insecticides + fungicides"),
    ("direct_cash_inputs",       "Total Direct Variable Expenses (D) ($/ac)", "direct_costs_per_ac", 2,
     "D = seed + fertilizer + plant protection (no fuel, repairs, insurance, interest, overhead)"),
    ("return_over_direct",       "Return Over Direct Cash Inputs (C-D) ($/ac)", "returns", 2, "C - D"),
    ("breakeven_yield_bu_ac",    "Cash Input Breakeven Yield (bu/ac)",      "returns", 2, "D / farm price"),
    ("breakeven_yield_lbs_ac",   "Cash Input Breakeven Yield (lb/ac)",      "returns", 1, "pulses only"),
    ("breakeven_price_per_bu",   "Cash Input Breakeven Price ($/bu)",       "returns", 4, "D / target yield"),
    ("breakeven_price_cents_lb", "Cash Input Breakeven Price (c/lb)",       "returns", 2, "pulses only"),
    ("seeding_rate_lbs_ac",      "Seeding Rate (lb/ac)",                    "agronomics", 1,
     "stated, or derived from plant stand / TKW / emergence (see basis)"),
    ("seeding_rate_other",       "Seeding Rate (other units)",              "agronomics", 0,
     "when the guide gives units/ac or plants/ac instead of lb"),
    ("seeding_rate_basis",       "Seeding Rate Basis",                      "agronomics", 0, ""),
    ("n_lbs_ac",                 "Nitrogen (N) (lb N/ac)",                  "agronomics", 1, ""),
    ("p_lbs_ac",                 "Phosphate (P) (lb P2O5/ac)",              "agronomics", 1, ""),
    ("k_lbs_ac",                 "Potash (K) (lb K2O/ac)",                  "agronomics", 1,
     "0: no potash in guide budgets"),
    ("s_lbs_ac",                 "Sulphur (S) (lb S/ac)",                   "agronomics", 1,
     "stated; 0 where the guide states none and budgets $0 of S"),
    ("guide_gross_revenue",      "Guide Gross Revenue (ref) ($/ac)",        "guide_reference", 2,
     "printed C, for reconciliation"),
    ("guide_total_variable_expenses", "Guide Total Variable Expenses (ref) ($/ac)", "guide_reference", 2,
     "printed D incl. fuel, repairs, custom work, insurance, utilities, interest"),
    ("guide_return_over_variable", "Guide Return over Variable Exp. (ref) ($/ac)", "guide_reference", 2,
     "printed C - D"),
]
OUT_GROUPS = ("revenue", "direct_costs_per_ac", "returns", "agronomics", "guide_reference")
PULSE_ONLY = {"target_yield_lbs_ac", "farm_price_cents_lb", "breakeven_yield_lbs_ac", "breakeven_price_cents_lb"}

# Budget row label -> field. (field, label regex, section regex or None, excluded section)
ROW_RULES: list[tuple[str, str, str | None]] = [
    ("target_yield",             r"yield.*\(a\)",                   None),
    ("market_price",             r"price.*\(b\)",                   None),
    ("gross_revenue",            r"gross revenue",                  None),
    ("seed",                     r"^seed$",                         None),
    ("seed_treatments",          r"seed treatments",                None),
    ("fert_n",                   r"^fertilizer\W*nitrogen",         None),
    ("fert_p2o5",                r"phosphorus",                     None),
    ("fert_s",                   r"sulphur",                        None),
    ("herbicides",               r"herbicides",                     None),
    ("insecticides",             r"insecticides",                   None),
    ("fungicides",               r"fungicides",                     None),
    ("total_variable_expenses",  r"^total variable expenses",       None),
    ("return_over_variable",     r"^return over variable",          r"^returns? per acre"),
    ("breakeven_yield_variable", r"^to cover variable",             r"break[- ]even yield"),
    ("breakeven_yield_total",    r"^to cover total",                r"break[- ]even yield"),
]

ZONE_WORDS = {"brown": "Brown", "dark brown": "Dark Brown", "black": "Black"}
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM = r"(\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + r")"
LB_AC = r"\s*[lI]bs?\.?\s*/\s*ac\.?"       # "lb./ac.", "lb./ ac", "74lb./ac.", "Ib./ac"
ZONE = r"(dark brown|brown|black)"


def _num(token: str) -> float:
    token = token.lower()
    return float(NUMBER_WORDS[token]) if token in NUMBER_WORDS else float(token.replace(",", ""))


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("*", "")).strip().lower()


def _zone_key(col: str) -> str | None:
    """'Black**' -> 'Black'."""
    col = _clean_name(col)
    return ZONE_WORDS.get(col)


# ---------------------------------------------------------------------------
# Financial rows
# ---------------------------------------------------------------------------
def extract_financials(table: dict, column: str) -> tuple[dict, list[str]]:
    """Pull the schema fields for one column of a raw economics table."""
    out: dict = {}
    notes: list[str] = []
    for r in table["rows"]:
        if r["row_type"] != "values":
            continue
        label = r["label"].lower().replace("*", "").strip()
        section = (r.get("section") or "").lower()
        if "yield sensitivity" in section:
            continue
        for field, lab_rx, sec_rx in ROW_RULES:
            if field in out or not re.search(lab_rx, label):
                continue
            if sec_rx and not re.search(sec_rx, section):
                continue
            value = r["numbers"].get(column)
            if value is not None:
                out[field] = value
                if field == "target_yield":
                    out["_yield_label"] = r["label"]
                if field == "market_price":
                    out["_price_label"] = r["label"]
            break

    out["yield_unit"], out["price_unit"] = _units(out.pop("_yield_label", ""),
                                                  out.pop("_price_label", ""),
                                                  out.get("target_yield"))
    missing = [f for f, _, _ in ROW_RULES if f not in out]
    if missing:
        notes.append(f"budget rows not found: {', '.join(missing)}")
    return out, notes


def _units(yield_label: str, price_label: str, yield_value: float | None) -> tuple[str | None, str | None]:
    ylab = yield_label.lower()
    has_bu, has_lb = "bu." in ylab, bool(re.search(r"\blbs?\.", ylab))
    if has_bu and not has_lb:
        yu = "bu/ac"
    elif has_lb and not has_bu:
        yu = "lb/ac"
    elif yield_value is not None:
        # 2019 prints "(bu./ac.,lb./ac.)" for every crop; pulses are in lb.
        yu = "lb/ac" if yield_value > 400 else "bu/ac"
    else:
        yu = None
    pu = {"bu/ac": "$/bu", "lb/ac": "$/lb"}.get(yu)
    plab = price_label.lower()
    if "$/lb" in plab and "bu" not in plab:
        pu = "$/lb"
    elif "$/bu" in plab and "lb" not in plab:
        pu = "$/bu"
    return yu, pu


# ---------------------------------------------------------------------------
# Agronomics: 2020+ prose
# ---------------------------------------------------------------------------
def _paragraph(text: str, start: str, stops: tuple[str, ...]) -> str:
    flat = " ".join(text.split())
    i = flat.find(start)
    if i < 0:
        return ""
    body = flat[i + len(start):]
    ends = [body.find(s) for s in stops if body.find(s) > 0]
    return body[:min(ends)] if ends else body


def parse_prose_agronomics(text: str, zones: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Seeding rate and N/P2O5/S per zone from a 2020+ agronomics panel."""
    per_zone = {z: {} for z in zones}
    notes: list[str] = []
    seeding = _paragraph(text, "Seeding:", ("Fertilization:", "Crop Rotation:"))
    fert = _paragraph(text, "Fertilization:", ("Producers are encouraged", "Crop Rotation:", "Crop Protection"))

    # --- seeding: explicit lb/ac per zone
    found = False
    for m in re.finditer(NUM + LB_AC + r"[^.]*?\bin (?:the )?" + ZONE + r" soil zone", seeding, re.I):
        z = ZONE_WORDS[m.group(2).lower()]
        if z in per_zone and "seeding_rate_lbs_ac" not in per_zone[z]:
            per_zone[z].update(seeding_rate_lbs_ac=_num(m.group(1)), seeding_rate_basis="stated lb/ac")
            found = True
    # --- one lb/ac rate for all zones
    if not found:
        m = re.search(r"rate of " + NUM + LB_AC + r".*?(?:all|each) soil zones?", seeding, re.I)
        if m:
            for z in per_zone:
                per_zone[z].update(seeding_rate_lbs_ac=_num(m.group(1)), seeding_rate_basis="stated lb/ac")
            found = True
    # --- units (hybrid rye: 1 unit = 1 million viable seeds)
    if not found:
        m = re.search(r"rate of " + NUM + r"\s*units?\s*/\s*ac", seeding, re.I)
        if m:
            for z in per_zone:
                per_zone[z].update(seeding_rate_other=f"{_num(m.group(1)):g} units/ac",
                                   seeding_rate_basis="stated units/ac (1 unit = 1M viable seeds)")
            found = True
    # --- plant stand + TKW + emergence -> derived lb/ac
    if not found:
        tkw = re.search(r"(?:thousand kernel weight of\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:TKW|thousand kernel weight))",
                        seeding, re.I)
        emerg = re.search(r"(\d+(?:\.\d+)?)\s*per ?cent emergence", seeding, re.I)
        stands = re.findall(r"(\d+(?:\.\d+)?)\s+(?:plants per square (?:foot|feet|meter|metre)\s+)?"
                            r"(?:in\s+)?(?:the\s+)?" + ZONE + r" soil zone", seeding, re.I)
        if tkw and emerg and stands:
            tkw_g = float(tkw.group(1) or tkw.group(2))
            em = float(emerg.group(1)) / 100
            for n, zone_word in stands:
                z = ZONE_WORDS[zone_word.lower()]
                if z in per_zone:
                    lbs = float(n) * SQFT_PER_ACRE * tkw_g / 1000 / GRAMS_PER_LB / em
                    per_zone[z].update(seeding_rate_lbs_ac=round(lbs, 1),
                                       seeding_rate_basis=f"derived: {n} plants/ft2, TKW {tkw_g:g} g, "
                                                          f"{em:.0%} emergence")
            found = True
            notes.append("seeding rate derived from target plant stand, TKW and emergence")
    # --- plant population only (soybeans)
    if not found:
        m = re.search(r"([\d,]+ to [\d,]+ plants per acre)", seeding, re.I)
        if m:
            for z in per_zone:
                per_zone[z].update(seeding_rate_other=m.group(1).replace("plants per acre", "plants/ac"),
                                   seeding_rate_basis="stated plant population; no seed weight given")
            found = True
            notes.append("seeding rate in lb/ac not derivable (plant population only)")
    if not found:
        notes.append("seeding rate not found in agronomics text")

    # --- fertility: "... N and ... P2O5 [and ... S] for the <zone> soil zone"
    body = fert[fert.find("These are:") + 10:] if "These are:" in fert else fert
    clauses = list(re.finditer(r"(.*?)for (?:the )?(dark brown|brown|black|all) soil zones?", body, re.I))
    if not clauses:
        notes.append("fertility rates not found in agronomics text")
    for c in clauses:
        clause, zone_word = c.group(1), c.group(2).lower()
        targets = list(per_zone) if zone_word == "all" else [ZONE_WORDS[zone_word]]
        vals = {}
        for key, nut in (("n_lbs_ac", r"N\b"), ("p2o5_lbs_ac", r"P2O5"), ("s_lbs_ac", r"S\b")):
            m = re.search(NUM + LB_AC + r"\s*" + nut, clause)
            if m:
                vals[key] = _num(m.group(1))
        for z in targets:
            if z in per_zone:
                per_zone[z].update(vals)
    return per_zone, notes


# ---------------------------------------------------------------------------
# Agronomics: 2019 side lists
# ---------------------------------------------------------------------------
def parse_side_list(table: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """{crop: {rate, unit, price}} and {crop: {N, P, S}} from a 2019 side list."""
    seeding: dict[str, dict] = {}
    fert: dict[str, dict] = {}
    mode = None
    for r in table["rows"]:
        text = r["text"]
        if re.match(r"^1\.\s*Seeding", text):
            mode = "seed"
            continue
        if re.match(r"^2\.\s*Fertilizer", text):
            mode = "fert"
            continue
        toks = r["tokens"]
        i = next((k for k, t in enumerate(toks) if re.match(r"^[\d.,]+$", t)), None)
        if i is None or i == 0:
            continue
        name = _clean_name(" ".join(toks[:i]))
        rest = toks[i:]
        if mode == "seed" and len(rest) >= 2:
            seeding[name] = {"rate": float(rest[0].replace(",", "")), "unit": rest[1].rstrip(".")}
        elif mode == "fert" and len(rest) >= 3 and all(re.match(r"^[\d.]+$", t) for t in rest[:3]):
            fert[name] = {"n": float(rest[0]), "p": float(rest[1]), "s": float(rest[2])}
    return seeding, fert


def side_list_agronomics(seeding: dict, fert: dict, names: set[str]) -> tuple[dict, list[str]]:
    out: dict = {}
    notes: list[str] = []
    s = next((seeding[n] for n in names if n in seeding), None)
    if s:
        if s["unit"].startswith("lb"):
            out.update(seeding_rate_lbs_ac=s["rate"], seeding_rate_basis="stated lb/ac")
        else:
            out.update(seeding_rate_other=f"{s['rate']:g} {s['unit']}/ac",
                       seeding_rate_basis=f"stated {s['unit']}/ac")
    else:
        notes.append("seeding rate not in 2019 side list")
    f = next((fert[n] for n in names if n in fert), None)
    if f:
        out.update(n_lbs_ac=f["n"], p2o5_lbs_ac=f["p"], s_lbs_ac=f["s"])
    else:
        notes.append("fertilizer rates not in 2019 side list")
    return out, notes


# ---------------------------------------------------------------------------
# Locate the per-zone source for one commodity-year
# ---------------------------------------------------------------------------
def zone_sources(doc: dict, names: set[str]) -> dict[str, dict]:
    """{zone: {"values": {...}, "notes": [...], "pages": [...], "table_id", "checks"}}"""
    found: dict[str, dict] = {}
    if doc["layout"] == "crop_per_page":
        for page in doc["pages"]:
            if page["page_type"] != "crop_budget" or _clean_name(page["crop"]) not in names:
                continue
            table = page["tables"][0]
            agro, agro_notes = parse_prose_agronomics(page.get("agronomics_text", ""),
                                                      [z for z in map(_zone_key, table["columns"]) if z])
            for col in table["columns"]:
                zone = _zone_key(col)
                if not zone:
                    continue
                values, notes = extract_financials(table, col)
                values.update(agro.get(zone, {}))
                found[zone] = {"values": values, "notes": notes + agro_notes, "pages": [page["page"]],
                               "table_id": table["table_id"], "column": col,
                               "source_crop": page["crop"], "checks": _failed_checks(table, col)}
            break
        return found

    # 2019: zone pages with crop columns, plus that zone's side list
    side = {}
    for page in doc["pages"]:
        for t in page.get("tables", []):
            if t["kind"] == "side_list" and page.get("zone") in SOIL_ZONES:
                side[page["zone"]] = (parse_side_list(t), page["page"])
    for page in doc["pages"]:
        zone = page.get("zone")
        if page["page_type"] != "zone_budget" or zone not in SOIL_ZONES or zone in found:
            continue
        for t in page["tables"]:
            if t["kind"] != "economics":
                continue
            col = next((c for c in t["columns"] if _clean_name(c) in names), None)
            if not col:
                continue
            values, notes = extract_financials(t, col)
            pages = [page["page"]]
            if zone in side:
                (seed_list, fert_list), side_page = side[zone]
                agro, agro_notes = side_list_agronomics(seed_list, fert_list, names)
                values.update(agro)
                notes += agro_notes
                pages.append(side_page)
                notes.append("2019 side list gives P as lb/ac P2O5 (heading 'P')")
            else:
                notes.append("no 2019 side list for this zone")
            found[zone] = {"values": values, "notes": notes, "pages": pages, "table_id": t["table_id"],
                           "column": col, "source_crop": col, "checks": _failed_checks(t, col)}
            break
    return found


def _failed_checks(table: dict, column: str) -> list[str]:
    return [f"source arithmetic check {c['check']} off by {c['diff']:+.2f} "
            f"(printed {c['reported']}, sum {c['expected']})"
            for c in table.get("checks", []) if c["column"] == column and not c["ok"]]


# ---------------------------------------------------------------------------
# Derive the direct-cash-input view from (blended) printed values
# ---------------------------------------------------------------------------
def _sum(*vals: float | None) -> float:
    return sum(v for v in vals if v is not None)


def _r(v: float | None, nd: int = 6) -> float | None:
    return None if v is None else round(v, nd)


def derive(base: dict, commodity: str) -> tuple[dict[str, dict], list[str]]:
    """Unit-normalized revenue, direct cash inputs (D), returns and break-evens."""
    notes: list[str] = []
    pulse = is_pulse(commodity)
    y, yu = base.get("target_yield"), base.get("yield_unit")
    p, pu = base.get("market_price"), base.get("price_unit")

    # Yield and price in bu/ac and $/bu; pulses also in lb/ac and c/lb.
    yield_bu = yield_lb = price_bu = price_lb = None
    if y is not None:
        if yu == "lb/ac":
            yield_lb = y
            yield_bu = y / PULSE_LBS_PER_BU if pulse else None
            if not pulse:
                notes.append("yield printed in lb/ac for a non-pulse; bu/ac not derived")
        else:
            yield_bu = y
            yield_lb = y * PULSE_LBS_PER_BU if pulse else None
    if p is not None:
        if pu == "$/lb":
            price_lb = p
            price_bu = p * PULSE_LBS_PER_BU if pulse else None
        else:
            price_bu = p
            price_lb = p / PULSE_LBS_PER_BU if pulse else None
    if pulse:
        notes.append(f"pulse: guide prints {yu} and {pu}; bu <-> lb at {PULSE_LBS_PER_BU:g} lb/bu")

    if yield_bu is not None and price_bu is not None:
        gross = yield_bu * price_bu
    elif yield_lb is not None and price_lb is not None:
        gross = yield_lb * price_lb
    else:
        gross = None
        notes.append("gross revenue not computable (yield or price missing)")
    guide_c = base.get("gross_revenue")
    if gross is not None and guide_c is not None and abs(gross - guide_c) > max(1.0, 0.005 * guide_c):
        notes.append(f"C = yield x price ({gross:.2f}) differs from printed C ({guide_c:.2f}); "
                     "printed price is rounded")

    # Nutrient rates: S implied 0 where the guide states none and budgets $0 of S.
    s_lbs = base.get("s_lbs_ac")
    if s_lbs is None and base.get("fert_s") == 0:
        s_lbs = 0.0
        notes.append("S rate not stated; budget carries $0 of S, so 0 lb S/ac")
    elif s_lbs is None:
        notes.append("S rate not stated but S cost is budgeted; lb S/ac left empty")

    seed_total = _sum(base.get("seed"), base.get("seed_treatments"))
    fert_k = 0.0
    fert_total = _sum(base.get("fert_n"), base.get("fert_p2o5"), fert_k, base.get("fert_s"))
    protection = _sum(base.get("herbicides"), base.get("insecticides"), base.get("fungicides"))
    d = seed_total + fert_total + protection
    for key in ("seed", "fert_n", "fert_p2o5", "herbicides"):
        if base.get(key) is None:
            notes.append(f"{key} missing from budget; counted as 0 in D")

    ret = None if gross is None else gross - d
    be_yield_bu = d / price_bu if price_bu else None
    be_price_bu = d / yield_bu if yield_bu else None
    be_yield_lb = d / price_lb if (pulse and price_lb) else None
    be_price_lb = d / yield_lb if (pulse and yield_lb) else None

    out = {
        "revenue": {
            "price_basis": "c/lb" if pulse else "$/bu",
            "target_yield_bu_ac": _r(yield_bu), "target_yield_lbs_ac": _r(yield_lb),
            "farm_price_per_bu": _r(price_bu), "farm_price_cents_lb": _r(None if price_lb is None else price_lb * 100),
            "gross_revenue": _r(gross),
        },
        "direct_costs_per_ac": {
            "seed_base": _r(base.get("seed")), "seed_treatment": _r(base.get("seed_treatments")),
            "seed_total": _r(seed_total),
            "fert_n": _r(base.get("fert_n")), "fert_p": _r(base.get("fert_p2o5")), "fert_k": fert_k,
            "fert_s": _r(base.get("fert_s")), "fert_total": _r(fert_total),
            "herbicides": _r(base.get("herbicides")), "insecticides": _r(base.get("insecticides")),
            "fungicides": _r(base.get("fungicides")), "protection_total": _r(protection),
            "direct_cash_inputs": _r(d),
        },
        "returns": {
            "return_over_direct": _r(ret),
            "breakeven_yield_bu_ac": _r(be_yield_bu), "breakeven_yield_lbs_ac": _r(be_yield_lb),
            "breakeven_price_per_bu": _r(be_price_bu),
            "breakeven_price_cents_lb": _r(None if be_price_lb is None else be_price_lb * 100),
        },
        "agronomics": {
            "seeding_rate_lbs_ac": base.get("seeding_rate_lbs_ac"),
            "seeding_rate_other": base.get("seeding_rate_other"),
            "seeding_rate_basis": base.get("seeding_rate_basis"),
            "n_lbs_ac": base.get("n_lbs_ac"), "p_lbs_ac": base.get("p2o5_lbs_ac"),
            "k_lbs_ac": 0.0, "s_lbs_ac": s_lbs,
        },
        "guide_reference": {
            "guide_gross_revenue": guide_c,
            "guide_total_variable_expenses": base.get("total_variable_expenses"),
            "guide_return_over_variable": base.get("return_over_variable"),
        },
    }
    return out, notes


# ---------------------------------------------------------------------------
# Build records
# ---------------------------------------------------------------------------
def build_record(doc: dict, commodity: str, rule: dict) -> dict:
    names = {_clean_name(n) for n in rule["source_names"]}
    sources = zone_sources(doc, names)
    zones = list(rule["zones"])
    used = {z: sources[z] for z in zones if z in sources}
    notes: list[str] = []

    for z in zones:
        if z not in sources:
            notes.append(f"{z} zone budget not found for {commodity} in {doc['year']}")
    zone_notes = {z: list(dict.fromkeys(src["notes"] + src["checks"])) for z, src in used.items()}
    shared = [n for n in next(iter(zone_notes.values()), []) if all(n in v for v in zone_notes.values())]
    notes += shared
    for z, zn in zone_notes.items():
        notes += [f"{z}: {n}" for n in zn if n not in shared]

    values: dict = {}
    for key in RAW_NUMERIC:
        nums = [src["values"][key] for src in used.values() if src["values"].get(key) is not None]
        values[key] = sum(nums) / len(nums) if nums else None
        if nums and len(nums) < len(used):
            notes.append(f"{key}: averaged over {len(nums)} of {len(used)} zones")
    for key in RAW_TEXT:
        texts = list(dict.fromkeys(src["values"].get(key) for src in used.values() if src["values"].get(key)))
        values[key] = " | ".join(texts) if texts else None
        if key in ("yield_unit", "price_unit") and len(texts) > 1:
            notes.append(f"{key} differs across blended zones: {texts}")

    if rule["method"] == "blend" and used:
        notes.append(f"simple average of {', '.join(used)} ({len(used)} zones)")
    derived, derive_notes = derive(values, commodity) if used else ({g: {} for g in OUT_GROUPS}, [])
    notes += derive_notes

    source_crops = list(dict.fromkeys(src["source_crop"] for src in used.values()))
    record = {
        "year": doc["year"],
        "commodity": commodity,
        "variety": rule.get("variety"),
        "method": rule["method"],
        "soil_zones": zones,
        "zones_used": list(used),
        "source": {
            "file": doc["file"],
            "crop_names": source_crops,
            "pages": sorted({p for src in used.values() for p in src["pages"]}),
            "tables": list(dict.fromkeys(src["table_id"] for src in used.values())),
        },
        "price_basis": rule["price_basis"],
        **derived,
        "zone_detail": {z: src["values"] for z, src in used.items()},
        "notes": notes,
    }
    return record


def source_discrepancies(raw: dict) -> list[dict]:
    """Every failed arithmetic check in the guides, whether or not it feeds the MVS."""
    out = []
    for doc in raw["documents"]:
        for page in doc["pages"]:
            for t in page.get("tables", []):
                for c in t.get("checks", []):
                    if not c["ok"]:
                        out.append({"year": doc["year"], "page": page["page"],
                                    "crop_or_zone": page.get("crop") or page.get("zone"),
                                    "column": c["column"], "check": c["check"],
                                    "printed": c["reported"], "computed": c["expected"], "diff": c["diff"]})
    return out


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def flatten(record: dict) -> dict:
    row = {
        "Year": record["year"],
        "Commodity": record["commodity"],
        "Variety": record["variety"],
        "Zone Method": "Simple average" if record["method"] == "blend" else "Single zone",
        "Soil Zones": ", ".join(record["soil_zones"]),
        "Zones Used": ", ".join(record["zones_used"]),
        "Source Crop Name": ", ".join(record["source"]["crop_names"]),
        "Source Pages": ", ".join(map(str, record["source"]["pages"])),
    }
    for key, header, group, _, _ in OUT_FIELDS:
        row[header] = record.get(group, {}).get(key)
    row["Notes"] = "; ".join(record["notes"])
    return row


def zone_rows(records: list[dict]) -> list[dict]:
    rows = []
    for rec in records:
        for zone, vals in rec["zone_detail"].items():
            row = {"Year": rec["year"], "Commodity": rec["commodity"], "Soil Zone": zone,
                   "Used In": "average" if rec["method"] == "blend" else "direct"}
            for key, header, _ in RAW_FIELDS:
                row[header] = vals.get(key)
            rows.append(row)
    return rows


def write_excel(path: Path, flat: pd.DataFrame, zones: pd.DataFrame, disc: pd.DataFrame,
                fields: pd.DataFrame) -> None:
    decimals = {header: d for _, header, d in RAW_FIELDS}
    decimals.update({header: d for _, header, _, d, _ in OUT_FIELDS})
    header_fill = PatternFill("solid", fgColor="1F4E78")
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in (("MVS", flat), ("Zone_Detail", zones),
                         ("Source_Discrepancies", disc), ("Field_Dictionary", fields)):
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = "C2" if name in ("MVS", "Zone_Detail") else "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = header_fill
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[1].height = 45
            for idx, col in enumerate(df.columns, start=1):
                letter = get_column_letter(idx)
                width = 60 if col == "Notes" else min(max(len(str(col)) * 0.9, 10), 28)
                ws.column_dimensions[letter].width = width
                d = decimals.get(col)
                if d:
                    fmt = "#,##0." + "0" * d
                    for cell in ws[letter][1:]:
                        cell.number_format = fmt
                if col == "Notes":
                    for cell in ws[letter][1:]:
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.auto_filter.ref = ws.dimensions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, default=RAW_TABLES_JSON)
    args = ap.parse_args(argv)

    if not args.raw.exists():
        print(f"Raw tables not found: {args.raw}\nRun: python src/pdf_parser.py")
        return 1
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    docs = {d["year"]: d for d in raw["documents"]}
    missing_years = [y for y in YEARS if y not in docs]

    records = []
    for year in YEARS:
        if year not in docs:
            continue
        for commodity, rule in MVS_COMMODITY_ZONE_MAP.items():
            try:
                records.append(build_record(docs[year], commodity, rule))
            except Exception as exc:  # flag, never halt the whole build
                records.append({"year": year, "commodity": commodity, "variety": rule.get("variety"),
                                "method": rule["method"], "soil_zones": list(rule["zones"]), "zones_used": [],
                                "source": {"file": docs[year]["file"], "crop_names": [], "pages": [], "tables": []},
                                "price_basis": rule["price_basis"], **{g: {} for g in OUT_GROUPS},
                                "zone_detail": {},
                                "notes": [f"normalization error: {type(exc).__name__}: {exc}"]})

    discrepancies = source_discrepancies(raw)
    payload = {
        "meta": {
            "normalizer_version": NORMALIZER_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": str(args.raw),
            "raw_parser_version": raw["meta"].get("parser_version"),
            "years": sorted(docs),
            "missing_years": missing_years,
            "blend_rule": "simple arithmetic mean across the commodity's soil zones (equal weights)",
            "direct_cash_inputs_rule": "D = seed (base + treatment) + fertilizer (N + P + K + S) "
                                       "+ plant protection (herbicides + insecticides + fungicides)",
            "units": {"direct_costs_per_ac": "$/ac", "revenue": "$/ac; yields bu/ac (+ lb/ac pulses)",
                      "prices": "$/bu (+ c/lb pulses)", "agronomics": "lb/ac (P as P2O5, K as K2O)",
                      "pulse_lbs_per_bu": PULSE_LBS_PER_BU},
            "zone_map": {c: {"zones": list(r["zones"]), "method": r["method"],
                             "source_names": list(r["source_names"])}
                         for c, r in MVS_COMMODITY_ZONE_MAP.items()},
        },
        "source_discrepancies": discrepancies,
        "records": records,
    }

    for path in (OUTPUT_JSON, OUTPUT_XLSX, OUTPUT_CSV):
        path.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    flat = pd.DataFrame([flatten(r) for r in records])
    flat.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    zones = pd.DataFrame(zone_rows(records))
    disc = pd.DataFrame(discrepancies, columns=["year", "page", "crop_or_zone", "column", "check",
                                                "printed", "computed", "diff"])
    disc.columns = ["Year", "Page", "Crop / Zone", "Column", "Check", "Printed", "Computed", "Diff"]
    fields = pd.DataFrame([{"Column": h, "Key": k, "Group": g, "Definition": desc}
                           for k, h, g, _, desc in OUT_FIELDS])
    write_excel(OUTPUT_XLSX, flat, zones, disc, fields)

    # Console summary
    print(f"Normalized {len(records)} records ({len(docs)} years x {len(MVS_COMMODITY_ZONE_MAP)} commodities)")
    incomplete = [r for r in records if len(r["zones_used"]) < len(r["soil_zones"])]
    gaps = [(r["year"], r["commodity"], k) for r in records
            for k, _, g, d, _ in OUT_FIELDS
            if d and k != "seeding_rate_lbs_ac" and not (k in PULSE_ONLY and not is_pulse(r["commodity"]))
            and r.get(g, {}).get(k) is None]
    print(f"  records missing a zone : {len(incomplete)}")
    by_field = pd.Series([g[2] for g in gaps]).value_counts().to_dict() if gaps else {}
    print(f"  empty numeric fields   : {len(gaps)} {by_field or ''} (excluding seeding lb/ac)")
    off = [r for r in records if r["returns"].get("return_over_direct") is not None
           and abs(r["revenue"]["gross_revenue"] - r["direct_costs_per_ac"]["direct_cash_inputs"]
                   - r["returns"]["return_over_direct"]) > 1e-6]
    print(f"  C - D identity breaks  : {len(off)}")
    no_seed = sorted({(r['commodity']) for r in records if r["agronomics"].get("seeding_rate_lbs_ac") is None})
    print(f"  no seeding lb/ac       : {', '.join(no_seed) or 'none'}")
    print(f"  source discrepancies   : {len(discrepancies)} (all guides)")
    for path in (OUTPUT_JSON, OUTPUT_XLSX, OUTPUT_CSV):
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
