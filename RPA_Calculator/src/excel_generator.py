"""Master Excel workbook for desk scenario planning.

Builds output/RPA_Master_Calculator.xlsx from the MVS database (baseline 2026):

    Control_Panel      A. retail fertilizer quotes ($/tonne) -> $/lb of nutrient
                       B. commodity bids ($/bu, or c/lb for pulses)
    RPA_Summary_SK     crops across, metrics down: revenue, direct cash costs, return
                       over cash inputs, delta vs Last Yr, rank, breakevens (formulas)
    Agronomic_Rates    crops across: target yield, seeding rate, N / P2O5 / K2O / S
    Historical_Ratios  Big-7 return over cash inputs and margin ratios: 2019-2026 guide
                       years (static) plus a Forecast column linked to RPA_Summary_SK

Every calculated cell is an Excel formula, so editing a yellow input on the
Control_Panel (or a rate / yield on Agronomic_Rates) flows through the Summary, the
ranking and the Forecast column of Historical_Ratios. "Last Yr" is the baseline guide
year (2026): the Last Yr RPA row uses the same formulas at that guide's prices, so
every delta reads 0.00 until an input changes.

Usage (from RPA_Calculator/):
    python -m src.excel_generator
    python -m src.excel_generator --year 2025 --out output/RPA_2025.xlsx
    python -m src.excel_generator --verify     # recalculate in Excel and check
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # flat imports under `python -m src.…`

from openpyxl import Workbook  # noqa: E402
from openpyxl.formatting.rule import CellIsRule  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.workbook.defined_name import DefinedName  # noqa: E402

from baselines import (ab_guide_quotes, ab_zone_averages, sk_implied_per_lb, sk_last_yr_quotes,  # noqa: E402
                       sk_records)
from config import (AB_BROWN_ZONES, AB_OUTPUT_JSON, AB_SUMMARY_ZONES, AB_ZONES, MASTER_WORKBOOK,  # noqa: E402
                    MVS_COMMODITY_ZONE_MAP, OUTPUT_JSON, SK_EXTRA_CROPS, is_pulse)
from input_parser import load_crop_bids, load_fertilizer_benchmarks  # noqa: E402
from input_parser import per_lb as quotes_per_lb  # noqa: E402

LB_PER_TONNE = 2204.62

# Retail products and their primary nutrient. Grades and the N credit on MAP / AS come from
# Input_Prices/Input_price_template.csv (one N-credit rule for both provinces).
FERTILIZERS = [
    # key, product, analysis, nutrient label, grade, MVS rate key
    ("N", "Urea", "46-0-0", "Nitrogen (N)", 0.46, "n_lbs_ac"),
    ("P", "MAP", "11-52-0", "Phosphate (P2O5)", 0.52, "p_lbs_ac"),
    ("K", "Potash", "0-0-60", "Potash (K2O)", 0.60, "k_lbs_ac"),
    ("S", "Ammonium Sulphate (AS)", "21-0-0-24S", "Sulphur (S)", 0.24, "s_lbs_ac"),
]
# Guides budget no potash, so there is no baseline K price; this is the default.
DEFAULT_K_PER_LB = 0.45

DISPLAY = {"Wheat": "Spring Wheat", "Lentils": "Red Lentils", "Peas": "Yellow Peas",
           "Barley": "Feed Barley", "Rye": "Hybrid Fall Rye"}
BIG7 = ["Wheat", "Durum", "Canola", "Barley", "Oats", "Peas", "Lentils"]
RATIOS = [  # label, numerator, denominator
    ("Canola / Spring Wheat", "Canola", "Wheat"),
    ("Spring Wheat / Durum", "Wheat", "Durum"),
    ("Yellow Peas / Red Lentils", "Peas", "Lentils"),
    ("Durum / Red Lentils", "Durum", "Lentils"),
]

# Summary rows (metric -> row) and Agronomic_Rates rows
S_ROW = {"crop": 4, "zone": 5, "yield": 6, "price": 7, "revenue": 8, "seed": 9, "fert": 10, "chem": 11,
         "cost": 12, "rpa": 13, "base": 14, "delta": 15, "rank": 16, "be_price": 17, "be_yield": 18}
A_ROW = {"crop": 4, "zone": 5, "yield": 6, "seed": 7, "N": 8, "P": 9, "K": 10, "S": 11}
FIRST_COL = 2   # crops start in column B


def display(commodity: str) -> str:
    return DISPLAY.get(commodity, commodity)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
SLATE = "1E293B"
SLATE_LIGHT = "E2E8F0"
INPUT_FILL = PatternFill("solid", fgColor="FFF4C2")
BASE_FILL = PatternFill("solid", fgColor="F1F5F9")
KEY_FILL = PatternFill("solid", fgColor="DBEAFE")
HEADER_FILL = PatternFill("solid", fgColor=SLATE)
SECTION_FILL = PatternFill("solid", fgColor=SLATE_LIGHT)
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
TITLE_FONT = Font(name="Calibri", bold=True, size=16, color=SLATE)
SUBTITLE_FONT = Font(name="Calibri", italic=True, size=10, color="475569")
SECTION_FONT = Font(name="Calibri", bold=True, size=11, color=SLATE)
TEXT_FONT = Font(name="Calibri", size=10)
NUM_FONT = Font(name="Consolas", size=10)
BOLD_NUM_FONT = Font(name="Consolas", size=10, bold=True)
INPUT_FONT = Font(name="Consolas", size=10, color="1D4ED8", bold=True)   # blue = input
NOTE_FONT = Font(name="Calibri", italic=True, size=9, color="475569")
RED_FONT = Font(name="Consolas", size=10, color="B91C1C")
LIGHT = Side(style="thin", color="CBD5E1")
GRID = Border(left=LIGHT, right=LIGHT, top=LIGHT, bottom=LIGHT)
DIVIDER = Border(bottom=Side(style="medium", color=SLATE))

CURRENCY = "$#,##0.00"
PER_LB = "$#,##0.000"
PERCENT = "0.0%"
RATIO = '0.00"x"'
RANK = '"#"0'
FMT = {
    "$/bu": {"price": '$#,##0.00" /bu"', "yield": '#,##0" bu/ac"', "yield_dec": '#,##0.0" bu/ac"'},
    "c/lb": {"price": '#,##0.00" ¢/lb"', "yield": '#,##0" lb/ac"', "yield_dec": '#,##0" lb/ac"'},
}


def _style_header(ws, row: int, first: int, last: int) -> None:
    for c in range(first, last + 1):
        cell = ws.cell(row, c)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, GRID
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32


def _row_header(ws, row: int, text: str, col: int = 1) -> None:
    """Dark slate metric label on the left of a transposed table."""
    cell = ws.cell(row, col, text)
    cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, GRID
    cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)


def _section(ws, row: int, text: str, first: int, last: int) -> None:
    ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    cell = ws.cell(row, first, text)
    cell.font = SECTION_FONT
    cell.alignment = Alignment(vertical="center")
    for c in range(first, last + 1):
        ws.cell(row, c).border = DIVIDER
        ws.cell(row, c).fill = SECTION_FILL
    ws.row_dimensions[row].height = 20


def _title(ws, title: str, subtitle: str) -> None:
    ws["A1"], ws["A2"] = title, subtitle
    ws["A1"].font, ws["A2"].font = TITLE_FONT, SUBTITLE_FONT
    ws.row_dimensions[1].height = 24
    ws.sheet_view.showGridLines = False


def _put(ws, row: int, col: int, value, fmt: str | None = None, *, kind: str = "num",
         align: str | None = None):
    """Write a bordered cell. kind: num | bold | text | input | base | key."""
    cell = ws.cell(row, col, value)
    cell.border = GRID
    if kind == "text":
        cell.font = TEXT_FONT
    elif kind == "input":
        cell.font, cell.fill = INPUT_FONT, INPUT_FILL
    elif kind == "base":
        cell.font, cell.fill = NUM_FONT, BASE_FILL
    elif kind == "key":
        cell.font, cell.fill = BOLD_NUM_FONT, KEY_FILL
    elif kind == "bold":
        cell.font = BOLD_NUM_FONT
    else:
        cell.font = NUM_FONT
    if fmt:
        cell.number_format = fmt
    cell.alignment = Alignment(horizontal=align or ("left" if kind == "text" else "right"),
                               vertical="center", wrap_text=kind == "text")
    return cell


def _autofit(ws, min_width: float = 8, max_width: float = 48, formula_width: int = 13,
             skip_rows: tuple[int, ...] = (1, 2)) -> None:
    widths: dict[int, float] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None or cell.row in skip_rows or type(cell).__name__ == "MergedCell":
                continue
            if isinstance(cell.value, str) and cell.value.startswith("="):
                length = formula_width
            elif isinstance(cell.value, float):
                length = 12
            else:
                text = str(cell.value)
                length = len(text)
                if cell.alignment.wrap_text and text.strip():
                    length = max(min(len(text), 16), max(len(w) for w in text.split()))
            widths[cell.column] = max(widths.get(cell.column, 0), length)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(max_width, w + 2))


def _name(wb: Workbook, name: str, ref: str) -> None:
    wb.defined_names[name] = DefinedName(name, attr_text=ref)


def _col(i: int) -> str:
    return get_column_letter(FIRST_COL + i)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_mvs(path: Path = OUTPUT_JSON) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"MVS database not found: {path} (run src/normalizer.py)")
    return json.loads(path.read_text(encoding="utf-8"))


def load_baseline(year: int, path: Path = OUTPUT_JSON) -> list[dict]:
    data = load_mvs(path)
    recs = {r["commodity"]: r for r in data["records"] if r["year"] == year}
    missing = [c for c in MVS_COMMODITY_ZONE_MAP if c not in recs]
    if missing:
        raise ValueError(f"{year} baseline missing {missing}; years: {data['meta']['years']}")
    return [recs[c] for c in MVS_COMMODITY_ZONE_MAP]


def whole_yield(rec: dict) -> int:
    """Guide target yield rounded to a whole bu/ac (lb/ac for pulses), halves up.

    The workbook works on these whole yields throughout so the numbers discussed with
    producers (55 bu/ac, 1,482 lb/ac) are exactly the ones behind every return.
    """
    rev = rec["revenue"]
    y = rev["target_yield_lbs_ac"] if rec["price_basis"] == "c/lb" else rev["target_yield_bu_ac"]
    return int(math.floor(y + 0.5))


def native_price(rec: dict) -> float:
    """Farm price in $ per yield unit: $/bu, or $/lb for pulses."""
    rev = rec["revenue"]
    return rev["farm_price_cents_lb"] / 100 if rec["price_basis"] == "c/lb" else rev["farm_price_per_bu"]


def whole_yield_return(rec: dict) -> float:
    """Return over direct cash inputs at the whole-number target yield."""
    return whole_yield(rec) * native_price(rec) - rec["direct_costs_per_ac"]["direct_cash_inputs"]


def historical_returns(year: int, path: Path = OUTPUT_JSON) -> dict[int, dict[str, float]]:
    """{year: {commodity: return over direct cash inputs at whole yields}} for guide years up to `year`."""
    out: dict[int, dict[str, float]] = {}
    for r in load_mvs(path)["records"]:
        if r["year"] <= year:
            out.setdefault(r["year"], {})[r["commodity"]] = whole_yield_return(r)
    return dict(sorted(out.items()))


def implied_per_lb(records: list[dict]) -> dict[str, float]:
    """Mean guide $/lb of nutrient across SK crops (fertilizer $/ac / lb/ac)."""
    return sk_implied_per_lb(records)


def last_yr_quotes(records: list[dict]) -> dict[str, float]:
    """SK Last Yr retail quotes ($/tonne, to the cent) reproducing the guide's implied $/lb
    under the standardized N-credit conversion."""
    return sk_last_yr_quotes(records)


def sk_last_yr_per_lb(records: list[dict]) -> dict[str, float]:
    """$/lb exactly as Control_Panel section A computes it from the Last Yr quotes."""
    return quotes_per_lb(last_yr_quotes(records), load_fertilizer_benchmarks("SK"))


def last_yr_returns(records: list[dict]) -> dict[str, float]:
    """Last Yr RPA exactly as RPA_Summary_SK computes it at Last Yr inputs: whole yield x guide
    price - (seed + sum lb/ac x Last Yr $/lb + chemicals)."""
    prices = sk_last_yr_per_lb(records)
    out = {}
    for r in records:
        c, a = r["direct_costs_per_ac"], r["agronomics"]
        fert = sum((a.get(rate_key) or 0) * prices[key] for key, *_, rate_key in FERTILIZERS)
        out[r["commodity"]] = whole_yield(r) * native_price(r) - (c["seed_total"] + fert + c["protection_total"])
    return out


def workbook_history(year: int, records: list[dict]) -> dict[int, dict[str, float]]:
    """Historical_Ratios static columns: guide years before `year` from the MVS, and `year`
    locked to the Summary's Last Yr RPA so Forecast == `year` until an input changes."""
    history = historical_returns(year)
    history[year] = last_yr_returns(records)
    return history


def zone_label(commodity: str) -> str:
    rule = MVS_COMMODITY_ZONE_MAP[commodity]
    zones = rule["zones"]
    return " + ".join(zones) + " (avg)" if rule["method"] == "blend" else zones[0]


# ---------------------------------------------------------------------------
# Province-neutral crop records for the workbook
#   key, display, variety, zone_label, pulse, unit ("$/bu" | "c/lb"), yield (whole bu or lb),
#   price (Last Yr, native unit), seed, chem ($/ac), rates {N,P,K,S} (lb/ac),
#   seeding_lbs, seeding_other, seeding_note, flags
# ---------------------------------------------------------------------------
def sk_crops(year: int) -> list[dict]:
    """SK workbook crops: the MVS commodities plus crops with no guide history (CPS / CNHR
    wheat), which carry `inherits` so their NPKS, seeding and chemical cells link to the parent."""
    out = []
    for name, rec in sk_records(year).items():
        a, unit = rec["agronomics"], rec["price_basis"]
        basis = a.get("seeding_rate_basis") or ""
        note = None
        if basis.startswith("derived") or "units" in basis or "population" in basis:
            note = basis.split(" | ")[0] + (" (per zone, averaged)" if " | " in basis else "")
        synthetic = rec.get("synthetic", False)
        parent = rec.get("inherits")
        out.append({
            "key": name, "crop_key": rec["crop_key"],
            "display": SK_EXTRA_CROPS[name]["display"] if synthetic else display(name),
            "variety": rec.get("variety") or "",
            "zone_label": zone_label(parent if synthetic else name), "pulse": unit == "c/lb", "unit": unit,
            "yield": whole_yield(rec),
            "price": rec["revenue"]["farm_price_cents_lb"] if unit == "c/lb" else rec["revenue"]["farm_price_per_bu"],
            "seed": rec["direct_costs_per_ac"]["seed_total"], "chem": rec["direct_costs_per_ac"]["protection_total"],
            "rates": {key: a.get(rate_key) for key, *_, rate_key in FERTILIZERS},
            "seeding_lbs": a.get("seeding_rate_lbs_ac"), "seeding_other": a.get("seeding_rate_other"),
            "seeding_note": (f"follows {display(parent)}" if synthetic else note),
            "inherits": parent if synthetic else None, "flags": [],
        })
    return out


def ab_crops(year: int) -> list[dict]:
    """AB workbook crops from the aggregation layer (baselines.ab_zone_averages): Brown / Dark
    Brown / Black only, Brown + Dark Brown for durum, red lentils and kabuli chickpeas."""
    out = []
    for a in ab_zone_averages(year):
        note = f"{round(a['seeding_bu'], 2):g} bu/ac × {a['bu_weight']} lb/bu" if a["seeding_bu"] else None
        out.append({
            "key": a["crop_key"], "crop_key": a["crop_key"], "display": a["display"], "variety": a["grades"],
            "zone_label": " + ".join(a["zone_names"]) + (" (avg)" if len(a["zones"]) > 1 else ""),
            "pulse": a["pulse"], "unit": a["unit"], "yield": int(math.floor(a["yield"] + 0.5)), "price": a["price"],
            "seed": a["seed"], "chem": a["chem"], "rates": a["rates"], "seeding_lbs": a["seeding_lbs"],
            "seeding_other": None, "seeding_note": note, "inherits": None, "flags": a["flags"], "zones": a["zones"],
            "guide_fertilizer": a["guide_fertilizer"],
        })
    return out


def ab_fertilizer(year: int, path: Path = AB_OUTPUT_JSON) -> list[dict]:
    """AB retail benchmarks for Control_Panel section C: the guide's $/tonne quotes, with the
    guide's N credit on MAP and AS (Input_price_template.csv AB rows)."""
    prices = ab_guide_quotes(year, path)
    bench = load_fertilizer_benchmarks("AB")
    rows = []
    for key, product, analysis, nutrient, _grade, _ in FERTILIZERS:
        b = bench[key]
        rows.append({"key": key, "product": product, "analysis": prices[key]["product"] if key == "S" else analysis,
                     "nutrient": nutrient, "grade": b["grade"], "n_credit": b["n_credit"],
                     "quote": prices[key]["per_tonne"], "published_per_lb": prices[key]["per_lb"]})
    return rows


def crop_last_yr_return(crop: dict, per_lb: dict[str, float]) -> float:
    """Last Yr RPA exactly as the Summary computes it at Last Yr inputs."""
    fert = sum((crop["rates"][k] or 0) * per_lb[k] for k in ("N", "P", "K", "S"))
    revenue = crop["yield"] * crop["price"] / (100 if crop["pulse"] else 1)
    return revenue - (crop["seed"] + fert + crop["chem"])


def ab_last_yr_per_lb(fert_rows: list[dict]) -> dict[str, float]:
    by = {r["key"]: r for r in fert_rows}
    n = by["N"]["quote"] / LB_PER_TONNE / by["N"]["grade"]
    out = {"N": n}
    for k in ("P", "K", "S"):
        out[k] = (by[k]["quote"] / LB_PER_TONNE - by[k]["n_credit"] * n) / by[k]["grade"]
    return out


# ---------------------------------------------------------------------------
# Tab 1: Control_Panel
# ---------------------------------------------------------------------------
def _fert_section(ws, wb: Workbook, top: int, title: str, rows: list[dict], lb_ref: str,
                  name_suffix: str, credit: bool) -> tuple[dict, int]:
    """Fertilizer quotes -> $/lb nutrient. With `credit`, MAP / AS carry an N credit column:
    $/lb = ($/tonne ÷ lb/tonne − N credit × $/lb N) ÷ nutrient content."""
    heads = ["Product", "Analysis", "Nutrient", "Nutrient Content"] + (["N Credit (lb N / lb)"] if credit else []) \
        + ["Last Yr Quote ($/tonne)", "Current Quote ($/tonne)", "Last Yr ($/lb nutrient)",
           "Current ($/lb nutrient)", "Change vs Last Yr"]
    last_col = len(heads)
    _section(ws, top, title, 1, 10)
    for i, h in enumerate(heads, 1):
        ws.cell(top + 1, i, h)
    _style_header(ws, top + 1, 1, last_col)
    L = {name: get_column_letter(i) for i, name in enumerate(
        ["product", "analysis", "nutrient", "grade"] + (["credit"] if credit else [])
        + ["last_q", "cur_q", "last_lb", "cur_lb", "change"], 1)}
    n_row = top + 2 + next(i for i, r in enumerate(rows) if r["key"] == "N")
    refs: dict = {"nutrient": {}, "nutrient_base": {}, "quote_cells": {}}
    for i, r in enumerate(rows):
        row = top + 2 + i
        _put(ws, row, 1, r["product"], kind="text")
        _put(ws, row, 2, r["analysis"], kind="text", align="center")
        _put(ws, row, 3, r["nutrient"], kind="text")
        _put(ws, row, 4, r["grade"], PERCENT)
        if credit:
            _put(ws, row, 5, r["n_credit"], "0.000")
        c0 = 6 if credit else 5
        _put(ws, row, c0, r["quote"], CURRENCY, kind="base")
        _put(ws, row, c0 + 1, r.get("current", r["quote"]), CURRENCY, kind="input")
        for which, qcol, out_col in (("last", L["last_q"], c0 + 2), ("cur", L["cur_q"], c0 + 3)):
            lb_col = L["last_lb"] if which == "last" else L["cur_lb"]
            if credit and r["key"] != "N":
                f = f"=({qcol}{row}/{lb_ref}-{L['credit']}{row}*{lb_col}${n_row})/{L['grade']}{row}"
            else:
                f = f"={qcol}{row}/{lb_ref}/{L['grade']}{row}"
            _put(ws, row, out_col, f, PER_LB)
        _put(ws, row, c0 + 4, f'=IFERROR({L["cur_lb"]}{row}/{L["last_lb"]}{row}-1,"")', PERCENT)
        refs["nutrient"][r["key"]] = f"Control_Panel!${L['cur_lb']}${row}"
        refs["nutrient_base"][r["key"]] = f"Control_Panel!${L['last_lb']}${row}"
        refs["quote_cells"][r["key"]] = (row, c0 + 1)
        _name(wb, f"Price_{r['key']}{name_suffix}", f"Control_Panel!${L['cur_lb']}${row}")
        _name(wb, f"Price_{r['key']}{name_suffix}_Base", f"Control_Panel!${L['last_lb']}${row}")
    return refs, top + 2 + len(rows)


def _bid_section(ws, top: int, title: str, crops: list[dict], year: int, note: str) -> tuple[dict, int]:
    _section(ws, top, title, 1, 10)
    heads = ["Crop", "Variety / Class", "Price Unit", "Last Yr Bid", "Current Bid", "Change vs Last Yr"]
    for i, h in enumerate(heads, 1):
        ws.cell(top + 1, i, h)
    _style_header(ws, top + 1, 1, 6)
    refs: dict = {"bid": {}, "bid_base": {}, "bid_cells": {}}
    for i, crop in enumerate(crops):
        r = top + 2 + i
        unit = crop["unit"]
        _put(ws, r, 1, crop["display"], kind="text")
        _put(ws, r, 2, crop["variety"], kind="text")
        _put(ws, r, 3, unit.replace("c/lb", "¢/lb"), kind="text", align="center")
        _put(ws, r, 4, crop["price"], FMT[unit]["price"], kind="base")
        _put(ws, r, 5, crop.get("current_price", crop["price"]), FMT[unit]["price"], kind="input")
        _put(ws, r, 6, f'=IFERROR(E{r}/D{r}-1,"")', PERCENT)
        refs["bid"][crop["key"]] = f"Control_Panel!$E${r}"
        refs["bid_base"][crop["key"]] = f"Control_Panel!$D${r}"
        refs["bid_cells"][crop["key"]] = (r, 5)
    end = top + 2 + len(crops)
    ws.cell(end, 1, note).font = NOTE_FONT
    return refs, end


def build_control_panel(wb: Workbook, sk: list[dict], sk_records: list[dict], ab: list[dict],
                        ab_fert: list[dict], year: int) -> dict:
    ws = wb.active
    ws.title = "Control_Panel"
    _title(ws, "RPA Master Calculator — Control Panel",
           f"Saskatchewan & Alberta · Last Yr = {year} provincial guides · edit the yellow cells; "
           "everything else recalculates")
    quotes = last_yr_quotes(sk_records)
    bench = load_fertilizer_benchmarks("SK")
    sk_rows = [{"key": key, "product": product, "analysis": analysis, "nutrient": nutrient,
                "grade": bench[key]["grade"], "n_credit": bench[key]["n_credit"], "quote": quotes[key]}
               for key, product, analysis, nutrient, _grade, _ in FERTILIZERS]

    # --- A. Saskatchewan fertilizer (rows 4..9; the lb-per-tonne constant sits below it)
    sk_fert, end = _fert_section(ws, wb, 4, "A. Saskatchewan — Retail Fertilizer Quotes  →  Active Nutrient Cost",
                                 sk_rows, "$B$11", "", credit=True)
    lb_row = end + 1
    ws.cell(lb_row, 1, "lb per tonne").font = TEXT_FONT
    _put(ws, lb_row, 2, LB_PER_TONNE, "#,##0.00")
    ws.cell(lb_row, 3, "Both provinces: $/lb nutrient = ($/tonne ÷ lb per tonne − N credit × $/lb N) ÷ nutrient "
                       "content; the N in MAP (11%) and AS (21% SK / 20.5% AB) is credited at the $/lb N.").font = NOTE_FONT
    ws.cell(lb_row + 1, 3, f"SK Last Yr quotes reproduce the {year} SK guide's implied $/lb of N, P2O5 and S. "
                           f"The SK guides budget no potash; K defaults to ${DEFAULT_K_PER_LB:.2f}/lb K2O."
            ).font = NOTE_FONT
    assert lb_row == 11, "SK layout moved; lb-per-tonne must stay at B11"

    # --- B. Saskatchewan bids
    sk_bids, end = _bid_section(ws, lb_row + 3, "B. Saskatchewan — Grain Bids", sk, year,
                                "Pulses (Red Lentils, Yellow Peas) are bid in ¢/lb; all other crops in $/bu. "
                                f"Yellow = input, grey = Last Yr ({year} SK guide).")

    # --- B2. SK classes with no guide history (CPS / CNHR wheat): own yield and seed inputs
    sk_class: dict = {}
    extras = [c for c in sk if c.get("inherits")]
    if extras:
        top = end + 2
        _section(ws, top, "B2. Saskatchewan — CPS / CNHR Wheat class inputs (NPKS and chemicals follow "
                          "Spring Wheat)", 1, 10)
        for i, h in enumerate(["Input", "Crop", "Unit", "Last Yr", "Current", "Change vs Last Yr"], 1):
            ws.cell(top + 1, i, h)
        _style_header(ws, top + 1, 1, 6)
        r = top + 2
        for crop in extras:
            refs = {}
            for label, unit, key, fmt in (("Target Yield", "bu/ac", "yield", FMT["$/bu"]["yield"]),
                                          ("Seed Cost", "$/ac", "seed", CURRENCY)):
                _put(ws, r, 1, label, kind="text")
                _put(ws, r, 2, crop["display"], kind="text")
                _put(ws, r, 3, unit, kind="text", align="center")
                _put(ws, r, 4, crop[key], fmt, kind="base")
                _put(ws, r, 5, crop.get(f"current_{key}", crop[key]), fmt, kind="input")
                _put(ws, r, 6, f'=IFERROR(E{r}/D{r}-1,"")', PERCENT)
                refs[key], refs[f"{key}_base"] = f"Control_Panel!$E${r}", f"Control_Panel!$D${r}"
                refs[f"{key}_cell"] = (r, 5)
                r += 1
            sk_class[crop["key"]] = refs
        ws.cell(r, 1, "No SK guide history for this class: Last Yr mirrors Spring Wheat until a CPS / CNHR bid and "
                      "target yield are entered (here or in crop_price_template.csv).").font = NOTE_FONT
        end = r

    # --- C. Alberta fertilizer (guide conversion credits the N in MAP and AS)
    ab_fert_refs, end = _fert_section(ws, wb, end + 3,
                                      "C. Alberta — Retail Fertilizer Quotes  →  Active Nutrient Cost",
                                      ab_fert, f"$B${lb_row}", "_AB", credit=True)
    pub = ", ".join(f"${r['published_per_lb']:.2f}/lb {r['key']}" for r in ab_fert)
    ws.cell(end, 3, "Same N-credit rule as section A (the AB guide itself prices this way).").font = NOTE_FONT
    ws.cell(end + 1, 3, f"Last Yr quotes are the {year} AB guide's published $/tonne ({pub}).").font = NOTE_FONT

    # --- D. Alberta bids
    zones = " + ".join(AB_ZONES[z] for z in AB_SUMMARY_ZONES)
    ab_bids, end = _bid_section(ws, end + 3, "D. Alberta — Grain Bids", ab, year,
                                f"Last Yr = {year} AB guide expected market price, averaged over {zones} "
                                "(Brown + Dark Brown for durum, red lentils, chickpeas). Pulses in ¢/lb.")
    ws.freeze_panes = "A4"
    _autofit(ws, max_width=24)
    ws.column_dimensions["C"].width = 20
    return {"SK": {**sk_fert, **sk_bids, "class": sk_class}, "AB": {**ab_fert_refs, **ab_bids}}


# ---------------------------------------------------------------------------
# Agronomic rates (built before the Summary, which links to it)
# ---------------------------------------------------------------------------
def build_agronomic_rates(wb: Workbook, sheet: str, crops: list[dict], title: str, subtitle: str,
                          notes: list[str], index: int | None = None) -> dict:
    ws = wb.create_sheet(sheet, index)
    _title(ws, title, subtitle)
    labels = {"crop": "Physical Input", "zone": "Soil Zone Target", "yield": "Target Yield (whole bu or lb)",
              "seed": "Seeding Rate (lb/ac or units/ac)", "N": "Applied N (lb/ac)",
              "P": "Applied P2O5 (lb/ac)", "K": "Applied K2O (lb/ac)", "S": "Applied S (lb/ac)"}
    last_col = FIRST_COL + len(crops) - 1
    for key in ("crop", "zone", "yield", "seed", "N", "P", "K", "S"):
        _row_header(ws, A_ROW[key], labels[key])
    refs: dict = {}
    seed_notes: list[str] = []
    for i, crop in enumerate(crops):
        col = FIRST_COL + i
        ws.cell(A_ROW["crop"], col, crop["display"])
        _put(ws, A_ROW["zone"], col, crop["zone_label"].replace(" + ", " +\n"), kind="text", align="center")
        parent = refs.get(crop.get("inherits") or "")
        if parent:   # class with no guide history: yield from Control_Panel, NPKS / seeding follow the parent
            _put(ws, A_ROW["yield"], col, f"={crop['class_refs']['yield']}", FMT[crop["unit"]]["yield"], kind="bold")
            for key in ("seed", "N", "P", "K", "S"):
                fmt = '#,##0.0" lb/ac"' if key == "seed" else "#,##0.0"
                _put(ws, A_ROW[key], col, f"={parent[key]}", fmt)
            refs[crop["key"]] = {k: f"{sheet}!${_col(i)}${A_ROW[k]}" for k in ("yield", "seed", "N", "P", "K", "S")}
            seed_notes.append(f"{crop['display']}: {crop['seeding_note']}")
            continue
        _put(ws, A_ROW["yield"], col, crop["yield"], FMT[crop["unit"]]["yield"], kind="bold")
        if crop["seeding_lbs"] is not None:
            _put(ws, A_ROW["seed"], col, crop["seeding_lbs"], '#,##0.0" lb/ac"')
        else:
            other = (crop["seeding_other"] or "—").replace(" to ", "–")
            _put(ws, A_ROW["seed"], col, other, kind="text", align="right")
        for key in ("N", "P", "K", "S"):
            _put(ws, A_ROW[key], col, crop["rates"][key], "#,##0.0")
        refs[crop["key"]] = {k: f"{sheet}!${_col(i)}${A_ROW[k]}" for k in ("yield", "seed", "N", "P", "K", "S")}
        if crop["seeding_note"]:
            seed_notes.append(f"{crop['display']}: {crop['seeding_note']}")
    _style_header(ws, A_ROW["crop"], FIRST_COL, last_col)
    ws.cell(A_ROW["crop"], 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[A_ROW["zone"]].height = 52
    note = A_ROW["S"] + 2
    _section(ws, note, "Notes", 1, last_col)
    lines = notes + ["Seeding rate basis:"] + [f"    • {n}" for n in seed_notes]
    for j, text in enumerate(lines):
        ws.cell(note + 1 + j, 1, text).font = NOTE_FONT
    ws.freeze_panes = ws.cell(A_ROW["zone"], FIRST_COL)
    ws.column_dimensions["A"].width = 32
    for i in range(len(crops)):
        ws.column_dimensions[_col(i)].width = 15
    return refs


# ---------------------------------------------------------------------------
# RPA summaries (transposed: crops across, metrics down)
# ---------------------------------------------------------------------------
def build_summary(wb: Workbook, sheet: str, crops: list[dict], cp: dict, rates: dict, title: str,
                  subtitle: str, definitions: list[str], index: int | None = None) -> dict:
    ws = wb.create_sheet(sheet, index)
    _title(ws, title, subtitle)
    labels = {
        "crop": "Metric", "zone": "Soil Zone Target", "yield": "Target Yield (whole bu or lb)",
        "price": "Farm Price ($/bu or ¢/lb)", "revenue": "Gross Revenue (C)", "seed": "Seed Cost ($/ac)",
        "fert": "Fertilizer Cost ($/ac)", "chem": "Chemical Cost ($/ac)",
        "cost": "Direct Cash Costs (D = Seed + Fert + Chem)", "rpa": "Return Over Cash Inputs (C − D)",
        "base": "Last Yr RPA ($/ac)", "delta": "Delta vs Last Yr ($/ac)", "rank": "RANK (by C − D)",
        "be_price": "Breakeven Price (D ÷ Yield)", "be_yield": "Breakeven Yield (D ÷ Price)",
    }
    for key, row in S_ROW.items():
        _row_header(ws, row, labels[key])
    first, last = _col(0), _col(len(crops) - 1)
    rpa_range = f"${first}${S_ROW['rpa']}:${last}${S_ROW['rpa']}"
    col_of = {crop["key"]: _col(i) for i, crop in enumerate(crops)}

    for i, crop in enumerate(crops):
        col, L, k = FIRST_COL + i, _col(i), crop["key"]
        pulse, unit = crop["pulse"], crop["unit"]
        rate = rates[k]
        scale = "/100" if pulse else ""          # ¢/lb x lb/ac -> $/ac
        R = {name: f"{L}{v}" for name, v in S_ROW.items()}

        def fert(prices: dict) -> str:
            return "+".join(f"{rate[n]}*{prices[n]}" for n in ("N", "P", "K", "S"))

        ws.cell(S_ROW["crop"], col, crop["display"])
        _put(ws, S_ROW["zone"], col, crop["zone_label"].replace(" + ", " +\n"), kind="text", align="center")
        _put(ws, S_ROW["yield"], col, f"={rate['yield']}", FMT[unit]["yield"])
        _put(ws, S_ROW["price"], col, f"={cp['bid'][k]}", FMT[unit]["price"])
        _put(ws, S_ROW["revenue"], col, f"={R['yield']}*{R['price']}{scale}", CURRENCY)
        cls = crop.get("class_refs")
        if cls:   # own seed input; chemicals follow the parent crop's column
            _put(ws, S_ROW["seed"], col, f"={cls['seed']}", CURRENCY)
            _put(ws, S_ROW["chem"], col, f"={col_of[crop['inherits']]}{S_ROW['chem']}", CURRENCY)
            y_base, seed_base = cls["yield_base"], cls["seed_base"]
        else:
            _put(ws, S_ROW["seed"], col, crop["seed"], CURRENCY)
            _put(ws, S_ROW["chem"], col, crop["chem"], CURRENCY)
            y_base, seed_base = R["yield"], R["seed"]
        _put(ws, S_ROW["fert"], col, f"={fert(cp['nutrient'])}", CURRENCY)
        _put(ws, S_ROW["cost"], col, f"={R['seed']}+{R['fert']}+{R['chem']}", CURRENCY, kind="bold")
        _put(ws, S_ROW["rpa"], col, f"={R['revenue']}-{R['cost']}", CURRENCY, kind="key")
        _put(ws, S_ROW["base"], col,
             f"={y_base}*{cp['bid_base'][k]}{scale}-({seed_base}+{fert(cp['nutrient_base'])}+{R['chem']})",
             CURRENCY, kind="base")
        _put(ws, S_ROW["delta"], col, f"={R['rpa']}-{R['base']}", CURRENCY)
        _put(ws, S_ROW["rank"], col, f"=RANK({R['rpa']},{rpa_range},0)", RANK, kind="key", align="center")
        be_price = f"{R['cost']}/{R['yield']}*100" if pulse else f"{R['cost']}/{R['yield']}"   # ¢/lb | $/bu
        be_yield = f"{R['cost']}/({R['price']}/100)" if pulse else f"{R['cost']}/{R['price']}"   # lb | bu
        _put(ws, S_ROW["be_price"], col, f'=IFERROR({be_price},"")', FMT[unit]["price"])
        _put(ws, S_ROW["be_yield"], col, f'=IFERROR({be_yield},"")', FMT[unit]["yield_dec"])

    _style_header(ws, S_ROW["crop"], FIRST_COL, FIRST_COL + len(crops) - 1)
    ws.cell(S_ROW["crop"], 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[S_ROW["zone"]].height = 52
    for key in ("rpa", "base", "delta"):
        row = S_ROW[key]
        ws.conditional_formatting.add(f"{first}{row}:{last}{row}",
                                      CellIsRule(operator="lessThan", formula=["0"], font=RED_FONT))
    ws.conditional_formatting.add(f"{first}{S_ROW['rank']}:{last}{S_ROW['rank']}",
                                  CellIsRule(operator="equal", formula=["1"],
                                             fill=PatternFill(start_color="BBF7D0", end_color="BBF7D0",
                                                              fill_type="solid")))
    note = S_ROW["be_yield"] + 2
    _section(ws, note, "Definitions", 1, FIRST_COL + len(crops) - 1)
    for j, text in enumerate(definitions):
        ws.cell(note + 1 + j, 1, text).font = NOTE_FONT
    ws.freeze_panes = ws.cell(S_ROW["zone"], FIRST_COL)
    ws.column_dimensions["A"].width = 40
    for i in range(len(crops)):
        ws.column_dimensions[_col(i)].width = 15
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return {crop["key"]: f"{sheet}!${_col(i)}${S_ROW['rpa']}" for i, crop in enumerate(crops)}


def summary_definitions(year: int, province: str, rates_sheet: str) -> list[str]:
    guide = {"SK": "SK Crop Planning Guide", "AB": "AB Cropping Alternatives guide"}[province]
    return [
        "Gross Revenue (C) = Target Yield × Farm Price; pulses: lb/ac × ¢/lb ÷ 100. Target yields are the "
        f"guide targets rounded to whole bu/ac (lb/ac for pulses); edit them on {rates_sheet}.",
        "Direct Cash Costs (D) = Seed (incl. treatment) + Fertilizer (N + P + K + S) + Chemicals "
        "(herbicides + fungicides / insecticides / desiccants). Fuel, repairs, insurance, trucking, labour, interest "
        "and overhead are excluded.",
        f"Fertilizer = Σ lb/ac ({rates_sheet}) × $/lb nutrient (Control_Panel). Seed and chemicals are the "
        f"{year} {guide} $/ac.",
        f"Last Yr RPA = the same calculation at the {year} {guide}'s bids and nutrient prices; "
        "Delta vs Last Yr = Return Over Cash Inputs (C − D) − Last Yr RPA. RANK 1 = highest return over cash inputs.",
        "Breakeven Price = D ÷ Target Yield; Breakeven Yield = D ÷ Farm Price (¢/lb and lb/ac for pulses).",
    ]


# ---------------------------------------------------------------------------
# Tab 4: Historical_Ratios
# ---------------------------------------------------------------------------
def build_historical(wb: Workbook, year: int, records: list[dict], live_rpa: dict[str, str]) -> dict:
    ws = wb.create_sheet("Historical_Ratios")
    history = workbook_history(year, records)
    years = list(history)
    ncols = len(years) + 1
    last_col = 1 + ncols
    _title(ws, "Historical Returns & Margin Ratios — Big-7",
           f"Return over direct cash inputs ($/ac) by guide year ({years[0]}–{years[-1]} static); Forecast "
           "recalculates from the Control_Panel")

    # --- A. Big-7 returns
    _section(ws, 4, f"A. Big-7 Return Over Cash Inputs ($/ac), {years[0]}–{years[-1]} and Forecast", 1, last_col)
    heads = ["Crop"] + [str(y) for y in years] + ["Forecast"]
    for j, h in enumerate(heads, 1):
        ws.cell(5, j, h)
    _style_header(ws, 5, 1, last_col)
    ws.cell(5, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
    cell_of: dict[str, dict] = {}
    for i, c in enumerate(BIG7):
        r = 6 + i
        _row_header(ws, r, display(c))
        cell_of[c] = {}
        for j, y in enumerate(years):
            col = 2 + j
            _put(ws, r, col, history[y].get(c), CURRENCY)
            cell_of[c][y] = f"{get_column_letter(col)}{r}"
        live_col = 2 + len(years)
        _put(ws, r, live_col, f"={live_rpa[c]}", CURRENCY, kind="key")
        cell_of[c]["live"] = f"{get_column_letter(live_col)}{r}"
    a_last = 6 + len(BIG7) - 1
    ws.conditional_formatting.add(f"B6:{get_column_letter(last_col)}{a_last}",
                                  CellIsRule(operator="lessThan", formula=["0"], font=RED_FONT))
    ws.cell(a_last + 1, 1, f"{years[0]}–{years[-1]}: each guide year's own prices and costs at whole-number "
                           f"target yields, direct cash definition (seed + fertilizer + chemicals). {year} is locked "
                           f"to the {year} guide (= Last Yr RPA). Forecast links to RPA_Summary_SK.").font = NOTE_FONT

    # --- B. Ratios
    top = a_last + 3
    _section(ws, top, f"B. Core Margin Ratios (RPA ÷ RPA), {years[0]}–{years[-1]} and Forecast", 1, last_col)
    for j, h in enumerate(["Ratio"] + heads[1:], 1):
        ws.cell(top + 1, j, h)
    _style_header(ws, top + 1, 1, last_col)
    ws.cell(top + 1, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ratio_cells: dict[str, dict] = {}
    for i, (label, num, den) in enumerate(RATIOS):
        r = top + 2 + i
        _row_header(ws, r, f"{label} RPA Ratio")
        ratio_cells[label] = {}
        for j, key in enumerate(years + ["live"]):
            n, d = cell_of[num][key], cell_of[den][key]
            kind = "key" if key == "live" else "num"
            _put(ws, r, 2 + j, f'=IF(AND(ISNUMBER({n}),ISNUMBER({d}),{n}>0,{d}>0),{n}/{d},"n/m")',
                 RATIO, kind=kind)
            ratio_cells[label][key] = f"{get_column_letter(2 + j)}{r}"
    b_last = top + 1 + len(RATIOS)
    ws.cell(b_last + 1, 1, "Ratio = numerator crop's return over cash inputs ÷ denominator's; above 1.00x the "
                           "numerator earns more per acre. n/m = not meaningful (a return ≤ 0).").font = NOTE_FONT

    ws.freeze_panes = "B6"
    ws.column_dimensions["A"].width = 34
    for j in range(ncols):
        ws.column_dimensions[get_column_letter(2 + j)].width = 13
    return {"returns": cell_of, "ratios": ratio_cells, "years": years}


def build_workbook(year: int, out: Path, prices_asof: str | None = None) -> tuple[Path, dict]:
    """Build the workbook; returns its path and the cell layout used by the verifier.

    prices_asof: fill the Current bids from Input_Prices/crop_price_template.csv snapshots
    (latest on or before that date, per province); Last Yr stays the guide price.
    """
    records = load_baseline(year)
    sk, ab, ab_fert = sk_crops(year), ab_crops(year), ab_fertilizer(year)
    if prices_asof:
        for prov, crops in (("SK", sk), ("AB", ab)):
            bids = load_crop_bids(prov, prices_asof)
            for crop in crops:
                bid = bids.get(crop["crop_key"])
                if bid and bid["price"] is not None:
                    crop["current_price"] = bid["price"]
                if bid and bid["yield"] is not None and crop.get("inherits"):
                    crop["current_yield"] = bid["yield"]
    wb = Workbook()
    cp = build_control_panel(wb, sk, records, ab, ab_fert, year)
    for crop in sk:
        if crop.get("inherits"):
            crop["class_refs"] = cp["SK"]["class"][crop["key"]]

    sk_rates = build_agronomic_rates(
        wb, "Agronomic_Rates", sk, "Agronomic Rates — Saskatchewan (Physical Inputs per Acre)",
        f"From the {year} Saskatchewan Crop Planning Guide · yields and rates feed RPA_Summary_SK",
        ["Target yields are the guide targets rounded to whole bu/ac (lb/ac for pulses) and are used as-is "
         "in every calculation.",
         "P as lb P2O5/ac, K as lb K2O/ac. Spring Wheat and Hybrid Fall Rye are simple averages across their "
         "soil zones.",
         "S = 0 where the guide states no sulphur and budgets $0 of it; K = 0 (no potash in SK guide budgets).",
         "CPS Wheat (CPS / CNHR class): no SK guide history; NPKS and seeding follow Spring Wheat, target yield and "
         "seed cost come from Control_Panel B2."])
    zones = " + ".join(AB_ZONES[z] for z in AB_SUMMARY_ZONES)
    ab_flags = [f"{c['display']} — {f}" for c in ab for f in c["flags"]]
    ab_rates = build_agronomic_rates(
        wb, "Agronomic_Rates_AB", ab, "Agronomic Rates — Alberta (Physical Inputs per Acre)",
        f"From the {year} Alberta AgriProfit$ Cropping Alternatives guide · yields and rates feed RPA_Summary_AB",
        ["Target yields are the guide targets rounded to whole bu/ac (lb/ac for pulses; peas converted at "
         "60 lb/bu) and are used as-is in every calculation.",
         f"Crops average {zones} where the guide budgets them; durum, red lentils and kabuli chickpeas use "
         f"{' + '.join(AB_ZONES[z] for z in AB_BROWN_ZONES)} only. Grey-Wooded, Peace Region and Irrigated budgets "
         "are parsed (output/ab/mvs) but excluded.",
         "Red lentil seed is the guide's Table 1 red-lentil cost; its budget tables print the green-lentil cost.",
         "N-P2O5-K2O-S rates are the guide's NPKS blend for each zone budget (lb/ac).",
         *(["Guide data flags:"] + [f"    • {f}" for f in ab_flags] if ab_flags else [])])
    sk_live = build_summary(wb, "RPA_Summary_SK", sk, cp["SK"], sk_rates,
                            "RPA Summary — Saskatchewan (Return Over Direct Cash Inputs)",
                            f"Live against Control_Panel (A, B) and Agronomic_Rates · Last Yr = {year} SK guide at "
                            "guide prices · $ per acre",
                            summary_definitions(year, "SK", "Agronomic_Rates"), index=1)
    build_summary(wb, "RPA_Summary_AB", ab, cp["AB"], ab_rates,
                  "RPA Summary — Alberta (Return Over Direct Cash Inputs)",
                  f"Live against Control_Panel (C, D) and Agronomic_Rates_AB · Last Yr = {year} AB guide at guide "
                  "prices · $ per acre", summary_definitions(year, "AB", "Agronomic_Rates_AB"), index=2)
    build_historical(wb, year, records, sk_live)
    wb.calculation.fullCalcOnLoad = True
    wb.properties.title = "RPA Master Calculator"
    wb.properties.subject = f"Saskatchewan and Alberta return per acre, Last Yr {year}"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    wb.close()
    return out, {"cp": cp, "sk": sk, "ab": ab, "ab_fert": ab_fert, "records": records}


# ---------------------------------------------------------------------------
# Verification in Excel (Windows + Excel + pywin32)
# ---------------------------------------------------------------------------
def verify_in_excel(path: Path, year: int, layout: dict) -> bool:
    """Recalculate a copy in Excel: no formula errors, zero deltas and a clean rank at
    baseline, history matches the MVS, then change inputs and compare every crop with
    market_engine on the same inputs."""
    import win32com.client  # noqa: PLC0415

    from market_engine import Scenario, run_scenario  # noqa: PLC0415

    from baselines import quotes_from_per_lb  # noqa: PLC0415

    records = {r["commodity"]: r for r in load_baseline(year)}
    order = [c["key"] for c in layout["sk"]]                 # MVS commodities + CPS Wheat
    mvs_order = list(MVS_COMMODITY_ZONE_MAP)
    sk_by = {c["key"]: c for c in layout["sk"]}
    extras = [c["key"] for c in layout["sk"] if c.get("inherits")]
    history = workbook_history(year, list(records.values()))
    tmp = Path(tempfile.mkdtemp()) / path.name
    shutil.copy(path, tmp)
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible, xl.DisplayAlerts = False, False
    ok = True
    try:
        wb = xl.Workbooks.Open(str(tmp))
        cp, sm, hr = wb.Worksheets("Control_Panel"), wb.Worksheets("RPA_Summary_SK"), wb.Worksheets(
            "Historical_Ratios")
        sa = wb.Worksheets("RPA_Summary_AB")
        ab = layout["ab"]
        xl.CalculateFull()

        def errors() -> list[str]:
            bad = []
            for ws in wb.Worksheets:
                used = ws.UsedRange
                for row in range(1, used.Rows.Count + 1):
                    for col in range(1, used.Columns.Count + 1):
                        text = str(used.Cells(row, col).Text)
                        if text.startswith("#") and not text.startswith("#1") and not text[1:].isdigit():
                            bad.append(f"{ws.Name}!{used.Cells(row, col).Address}={text}")
            return bad

        def summary() -> dict[str, dict]:
            return {c: {k: sm.Cells(S_ROW[k], FIRST_COL + i).Value for k in S_ROW} for i, c in enumerate(order)}

        def summary_ab() -> dict[str, dict]:
            return {c["key"]: {k: sa.Cells(S_ROW[k], FIRST_COL + i).Value for k in S_ROW} for i, c in enumerate(ab)}

        def big7_row(c: str) -> int:
            return 6 + BIG7.index(c)

        def report(name: str, passed: bool, detail: str) -> None:
            nonlocal ok
            ok &= passed
            print(f"  [{'PASS' if passed else 'FAIL'}] {name:<54} {detail}")

        print(f"\nExcel verification ({tmp.name})")
        bad = errors()
        report(f"no formula errors at Last Yr inputs ({wb.Worksheets.Count} tabs)", not bad, f"{len(bad)} error cells {bad[:3]}")
        base = summary()
        worst = max(abs(v["delta"]) for v in base.values())
        report("Delta vs Last Yr zero at Last Yr inputs", worst < 1e-9, f"max |delta| {worst:.2e}")
        want_labels = {
            ("Control_Panel", 5, 5): "N Credit (lb N / lb)", ("Control_Panel", 5, 6): "Last Yr Quote ($/tonne)",
            ("Control_Panel", 5, 8): "Last Yr ($/lb nutrient)", ("Control_Panel", 5, 10): "Change vs Last Yr",
            ("RPA_Summary_SK", S_ROW["base"], 1): "Last Yr RPA ($/ac)",
            ("RPA_Summary_SK", S_ROW["delta"], 1): "Delta vs Last Yr ($/ac)",
            ("Historical_Ratios", 5, 2 + len(history) - 1): str(year),
            ("Historical_Ratios", 5, 2 + len(history)): "Forecast",
        }
        bid_head = layout["cp"]["SK"]["bid_cells"][order[0]][0] - 1
        want_labels[("Control_Panel", bid_head, 6)] = "Change vs Last Yr"
        wrong = [f"{k[0]}!R{k[1]}C{k[2]}={wb.Worksheets(k[0]).Cells(k[1], k[2]).Value!r}"
                 for k, v in want_labels.items() if wb.Worksheets(k[0]).Cells(k[1], k[2]).Value != v]
        report("Last Yr / Forecast headers in place", not wrong, "; ".join(wrong))
        delta_f = str(sm.Cells(S_ROW["delta"], FIRST_COL).Formula)
        report("Delta = (C − D) − Last Yr RPA", delta_f == f"={_col(0)}{S_ROW['rpa']}-{_col(0)}{S_ROW['base']}",
               delta_f)
        worst = max(abs(base[c]["yield"] - whole_yield(records[c])) for c in mvs_order)
        report("target yields are whole numbers", worst == 0 and all(float(base[c]["yield"]).is_integer()
                                                                     for c in order), "")
        sk_lb = {k: cp.Range(layout["cp"]["SK"]["nutrient_base"][k].split("!")[1]).Value for k in "NPKS"}
        implied = implied_per_lb(list(records.values()))
        worst = max(abs(sk_lb[k] - implied[k]) for k in "NPKS")
        report("SK Last Yr $/lb (N-credit quotes) == guide implied", worst < 1e-4,
               ", ".join(f"{k} {sk_lb[k]:.4f}" for k in "NPKS"))
        worst = max(abs(base[c]["rpa"] - whole_yield_return(records[c])) for c in mvs_order)
        report("Last Yr RPA vs MVS at whole yields", worst < 1.0,
               f"max diff ${worst:.3f}/ac (single mean $/lb vs guide line items)")
        for x in extras:
            parent = sk_by[x]["inherits"]
            diff = max(abs(base[x][k] - base[parent][k]) for k in ("seed", "fert", "chem", "rpa", "base"))
            report(f"{sk_by[x]['display']} mirrors {display(parent)} until class inputs change", diff < 1e-9,
                   f"max diff {diff:.2e}")
        shift = {c: base[c]["rpa"] - records[c]["returns"]["return_over_direct"] for c in mvs_order}
        big = max(shift, key=lambda c: abs(shift[c]))
        print(f"  [INFO] rounding yields moves Last Yr returns by at most {shift[big]:+.2f} $/ac "
              f"({display(big)})")
        def std_rank(vals: dict) -> dict:     # Excel RANK: ties share a rank, the next rank is skipped
            return {k: 1 + sum(v2["rpa"] > v["rpa"] + 1e-9 for v2 in vals.values()) for k, v in vals.items()}

        want_rank = std_rank(base)
        top = max(order, key=lambda c: base[c]["rpa"])
        report(f"RANK (ties shared) matches C − D order, {len(order)} crops",
               all(int(base[c]["rank"]) == want_rank[c] for c in order) and base[top]["rank"] == 1,
               f"#1 {sk_by[top]['display']} ${base[top]['rpa']:.2f}")
        worst = max(abs(hr.Cells(big7_row(c), 2 + j).Value - history[y][c])
                    for c in BIG7 for j, y in enumerate(history))
        report(f"Historical_Ratios {min(history)}–{max(history) - 1} == MVS, {year} == Last Yr (static)",
               worst < 1e-9, f"max diff {worst:.2e}")
        worst = max(abs(hr.Cells(big7_row(c), 1 + len(history)).Value - base[c]["base"]) for c in BIG7)
        report(f"{year} column == Summary Last Yr RPA", worst < 1e-9, f"max diff {worst:.2e}")
        live_col = 2 + len(history)
        static_col = live_col - 1
        worst = max(abs(hr.Cells(big7_row(c), live_col).Value - base[c]["rpa"]) for c in BIG7)
        report("Forecast column == RPA_Summary_SK", worst < 1e-9, f"max diff {worst:.2e}")
        live_f = str(hr.Cells(big7_row("Wheat"), live_col).Formula)
        report("Forecast links to RPA_Summary_SK", live_f.startswith("=RPA_Summary_SK!"), live_f)
        static_before = [hr.Cells(r, static_col).Value for r in range(6, 6 + len(BIG7) + 4 + len(RATIOS))]
        ratio_row = 6 + len(BIG7) + 4
        got = hr.Cells(ratio_row, 2).Value
        want = history[min(history)]["Canola"] / history[min(history)]["Wheat"]
        report(f"Canola/Wheat {min(history)} ratio", abs(got - want) < 1e-9, f"{got:.4f}x vs {want:.4f}x")

        # ---- Alberta at Last Yr inputs
        ab_base = summary_ab()
        per_lb_last = ab_last_yr_per_lb(layout["ab_fert"])
        names = [sa.Cells(S_ROW["crop"], FIRST_COL + i).Value for i in range(len(ab))]
        agro_names = [wb.Worksheets("Agronomic_Rates_AB").Cells(A_ROW["crop"], FIRST_COL + i).Value
                      for i in range(len(ab))]
        bid_names = [cp.Cells(layout["cp"]["AB"]["bid_cells"][c["key"]][0], 1).Value for c in ab]
        want = [c["display"] for c in ab]
        report("AB crop labels align (Summary / Rates / Bids)", names == agro_names == bid_names == want,
               ", ".join(names))
        report("SK and AB summaries side by side", len(order) == 12 and len(ab) >= 8,
               f"SK {len(order)} crops, AB {len(ab)} crops")
        zones_row = {c["key"]: str(sa.Cells(S_ROW["zone"], FIRST_COL + i).Value) for i, c in enumerate(ab)}
        brown_only = {"durum", "lentils_red", "chickpeas_kabuli"}
        bad_z = [f"{k}: {z!r}" for k, z in zones_row.items()
                 if any(x in z for x in ("Peace", "Grey", "Irrig")) or (("Black" in z) == (k in brown_only))]
        report("AB zones: B/DB/Black; durum, lentils, chickpeas B/DB only", not bad_z, "; ".join(bad_z)[:120])
        lent = ab_base.get("lentils_red", {}).get("seed")
        report("AB red lentil seed = Table 1 (not the green-lentil budget cost)",
               lent is not None and abs(lent - 29.40) < 0.005, f"${lent:.2f}/ac")
        worst = max(abs(v["delta"]) for v in ab_base.values())
        report("AB Delta vs Last Yr zero at Last Yr inputs", worst < 1e-9, f"max |delta| {worst:.2e}")
        got_lb = {r["key"]: cp.Range(layout["cp"]["AB"]["nutrient_base"][r["key"]].split("!")[1]).Value
                  for r in layout["ab_fert"]}
        worst = max(abs(got_lb[r["key"]] - r["published_per_lb"]) for r in layout["ab_fert"])
        report("AB Last Yr $/lb (N-credit conversion) == guide published", worst < 0.005,
               ", ".join(f"{k} {v:.4f}" for k, v in got_lb.items()))
        worst = max(abs(ab_base[c["key"]]["base"] - crop_last_yr_return(c, per_lb_last)) for c in ab)
        report("AB Last Yr RPA == independent Python calc", worst < 1e-6, f"max diff {worst:.2e}")
        fert_gap = max(abs(ab_base[c["key"]]["fert"] - c["guide_fertilizer"]) for c in ab)
        print(f"  [INFO] AB fertilizer (NPKS x $/lb) vs guide's printed fertilizer $: max gap ${fert_gap:.2f}/ac")
        ranks = sorted(int(v["rank"]) for v in ab_base.values())
        top_ab = max(ab_base, key=lambda k: ab_base[k]["rpa"])
        report("AB RANK is 1..n", ranks == list(range(1, len(ab) + 1)),
               f"#1 {next(c['display'] for c in ab if c['key'] == top_ab)} ${ab_base[top_ab]['rpa']:.2f}")

        # Change inputs: Phase 2 sample market vectors.
        sample = {"N": 0.75, "P": 0.65, "K": 0.45, "S": 0.50}
        for key, quote in quotes_from_per_lb(sample, "SK").items():    # quotes that net to the sample $/lb
            row, col = layout["cp"]["SK"]["quote_cells"][key]
            cp.Cells(row, col).Value = quote
        bids = {"Wheat": (10.15, "$/bu"), "Canola": (14.50, "$/bu"), "Lentils": (32.0, "c/lb")}
        for c, (price, _) in bids.items():
            row, col = layout["cp"]["SK"]["bid_cells"][c]
            cp.Cells(row, col).Value = price
        xl.CalculateFull()
        bad = errors()
        report("no formula errors after input change", not bad, f"{len(bad)} error cells {bad[:3]}")
        after = summary()
        moved = [display(c) for c in bids if abs(after[c]["rpa"] - base[c]["rpa"]) > 1]
        report("changed bids move RPA_Summary_SK", len(moved) == len(bids), f"moved: {', '.join(moved)}")

        engine = run_scenario(Scenario.from_dict({
            "name": "excel-check", "baseline_year": year, "nutrient_prices": sample,
            "crop_prices": {c: {"farm_price": p, "unit": u} for c, (p, u) in bids.items()}}))
        eng = {r["commodity"]: r["scenario"] for r in engine["results"]}
        # market_engine prices D; revenue and breakevens are re-derived on the whole yields.
        worst = 0.0
        for c in order:
            wy, pulse = sk_by[c]["yield"], sk_by[c]["pulse"]
            price = eng[c]["farm_price_cents_lb"] / 100 if pulse else eng[c]["farm_price_per_bu"]
            d = eng[c]["direct_cash_inputs"]
            worst = max(worst, abs(after[c]["revenue"] - wy * price), abs(after[c]["cost"] - d),
                        abs(after[c]["rpa"] - (wy * price - d)),
                        abs(after[c]["be_price"] - d / wy * (100 if pulse else 1)))
        report(f"workbook == market_engine on same inputs ({len(order)} SK crops)", worst < 1e-6,
               f"max diff {worst:.2e}")
        expect = whole_yield(records["Lentils"]) * 32.0 / 100
        report("pulse revenue = lb/ac × ¢/lb ÷ 100", abs(after["Lentils"]["revenue"] - expect) < 1e-6,
               f"Red Lentils C {after['Lentils']['revenue']:.2f} vs {expect:.2f}")
        want_rank = std_rank(after)
        top = max(order, key=lambda c: after[c]["rpa"])
        report("RANK re-orders with inputs", all(int(after[c]["rank"]) == want_rank[c] for c in order)
               and after[top]["rank"] == 1,
               f"#1 now {sk_by[top]['display']} ${after[top]['rpa']:.2f}")
        got = hr.Cells(ratio_row, live_col).Value
        want = after["Canola"]["rpa"] / after["Wheat"]["rpa"]
        report("Forecast Canola/Wheat ratio follows inputs", abs(got - want) < 1e-9, f"{got:.4f}x")
        worst = max(abs(summary_ab()[k]["rpa"] - ab_base[k]["rpa"]) for k in ab_base)
        report("SK input change leaves RPA_Summary_AB untouched", worst < 1e-9, f"max move {worst:.2e}")

        # ---- SK CPS / CNHR class: own bid, yield and seed; NPKS + chemicals follow Spring Wheat
        for x in extras:
            parent = sk_by[x]["inherits"]
            cls = layout["cp"]["SK"]["class"][x]
            cps_in = {"price": 7.60, "yield": 64, "seed": 31.00}
            row, col = layout["cp"]["SK"]["bid_cells"][x]
            cp.Cells(row, col).Value = cps_in["price"]
            cp.Cells(*cls["yield_cell"]).Value = cps_in["yield"]
            cp.Cells(*cls["seed_cell"]).Value = cps_in["seed"]
            parent_before = summary()[parent]
            xl.CalculateFull()
            now = summary()
            want = cps_in["yield"] * cps_in["price"] - (cps_in["seed"] + now[parent]["fert"] + now[parent]["chem"])
            diff = abs(now[x]["rpa"] - want)
            report(f"{sk_by[x]['display']} = own yield x bid − (own seed + {display(parent)} fert + chem)",
                   diff < 1e-9, f"RPA ${now[x]['rpa']:.2f} (yield {cps_in['yield']}, ${cps_in['price']:.2f}/bu)")
            report(f"{sk_by[x]['display']} inputs leave {display(parent)} untouched",
                   abs(now[parent]["rpa"] - parent_before["rpa"]) < 1e-9, "")
            yield_cell = wb.Worksheets("Agronomic_Rates").Cells(A_ROW["yield"], FIRST_COL + order.index(x)).Value
            report(f"{sk_by[x]['display']} yield flows Control_Panel -> Agronomic_Rates", yield_cell == cps_in["yield"],
                   f"{yield_cell}")

        # ---- Alberta input change: N quote +10%, canola and red lentil bids
        sk_before = summary()
        ab_q = {r["key"]: r["quote"] for r in layout["ab_fert"]}
        ab_q["N"] *= 1.10
        row, col = layout["cp"]["AB"]["quote_cells"]["N"]
        cp.Cells(row, col).Value = ab_q["N"]
        ab_bids = {"canola": 15.00, "lentils_red": 30.0}
        for k, price in ab_bids.items():
            if k in layout["cp"]["AB"]["bid_cells"]:
                row, col = layout["cp"]["AB"]["bid_cells"][k]
                cp.Cells(row, col).Value = price
        xl.CalculateFull()
        bad = errors()
        report("no formula errors after AB input change", not bad, f"{len(bad)} error cells {bad[:3]}")
        per_lb_new = ab_last_yr_per_lb([{**r, "quote": ab_q[r["key"]]} for r in layout["ab_fert"]])
        ab_after = summary_ab()
        worst = max(abs(ab_after[c["key"]]["rpa"] - crop_last_yr_return(
            {**c, "price": ab_bids.get(c["key"], c["price"])}, per_lb_new)) for c in ab)
        report("RPA_Summary_AB == Python calc after AB change", worst < 1e-6, f"max diff {worst:.2e}")
        moved = {c["display"]: ab_after[c["key"]]["fert"] - ab_base[c["key"]]["fert"] for c in ab}
        print("  [INFO] AB fertilizer move for +10% urea (N credit lowers net $/lb P2O5 and S): "
              + ", ".join(f"{k} {v:+.2f}" for k, v in moved.items()))
        worst = max(abs(summary()[c]["rpa"] - sk_before[c]["rpa"]) for c in order)
        report("AB input change leaves RPA_Summary_SK untouched", worst < 1e-9, f"max move {worst:.2e}")
        static_after = [hr.Cells(r, static_col).Value for r in range(6, 6 + len(BIG7) + 4 + len(RATIOS))]
        report(f"{year} column stays locked after input change", static_after == static_before,
               f"{sum(a != b for a, b in zip(static_before, static_after))} cells moved")
        for c in ("Wheat", "Canola", "Lentils"):
            print(f"         {display(c):<12} RPA {base[c]['rpa']:>8.2f} -> {after[c]['rpa']:>8.2f}  "
                  f"rank #{int(base[c]['rank'])} -> #{int(after[c]['rank'])}")
        wb.Close(SaveChanges=False)
    finally:
        xl.Quit()
        shutil.rmtree(tmp.parent, ignore_errors=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=2026, help="MVS baseline year (default 2026)")
    ap.add_argument("--out", type=Path, default=MASTER_WORKBOOK)
    ap.add_argument("--verify", action="store_true", help="recalculate in Excel and run checks")
    ap.add_argument("--prices-asof", help="fill Current bids from crop_price_template.csv snapshots (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    out, layout = build_workbook(args.year, args.out, args.prices_asof)
    print(f"Wrote {out}  (baseline {args.year}, {date.today():%Y-%m-%d})")
    if args.verify and not verify_in_excel(out, args.year, layout):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
