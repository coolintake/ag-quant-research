"""Master Excel workbook: output/RPA_Master_Calculator.xlsx, built from the province registry.

    RPA_Prairie         acreage-weighted Prairie benchmark (StatCan 32-10-0359) beside MB / SK / AB returns
    Control_Panel       two tables across every province: A. fertilizer retail quotes -> $/lb
                        nutrient (N credit), B. grain bids + class inputs (SK CPS)
    RPA_Summary_<code>  crops across, metrics down (Metric | Unit | crops): C, D, C - D, Last Yr,
                        delta, rank, breakevens
    Agronomic_Rates     one table, rows grouped by province: whole target yields, seeding rates,
                        N / P2O5 / K2O / S
    Historical_Ratios   return ratios numerator / base crop by province, each guide year + Forecast

Styling lives in src/outputs/theme.py (CanadaSD_Engine look: black header row, unfilled body,
units in a Unit column or header, never inside a value). Every number comes from CropBaseline
records (src/normalize/baselines.py) and the engine (src/engine/market.py); every calculated
cell is a live formula. Tabs, provinces and crops are generated from docs/AGENT_RULES.json.

    python -m src.outputs.excel [--year 2026] [--prices-asof 2027-03-25] [--verify]
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from ..config import BASELINE_YEAR, MASTER_WORKBOOK, NUTRIENT_KEYS, PROVINCES, RULES, crop, lb_per_unit, province
from ..engine.market import last_yr
from ..engine.prairie_engine import load_weights, prairie_last_yr
from ..normalize.baselines import (CropBaseline, guide_published_per_lb, history, last_yr_quotes, n_shares,
                                   province_baselines)
from ..pricing.templates import load_crop_bids, load_fertilizer_quotes
from .theme import (BASE_FILL, BLOCK_END, BOLD_FONT, CENTER, CROP_WIDTH, CURRENCY, GRID, HEADER_FILL, HEADER_FONT,
                    INPUT_FILL, INPUT_FONT, INT, KEY_FILL, LABEL_WIDTH, LEFT, LIVE_FILL, MONEY, NOTE_FONT, NUM_FONT,
                    PER_LB, PERCENT, PRICE, RANK, RANK_FILL, RANK_FONT, RATE, RATIO, RED_FONT, RETURN, RIGHT,
                    SECTION_FILL, SECTION_FONT, SECTION_RULE, SMALL_FONT, SUBTITLE_FONT, TEXT_FONT, THIN, TITLE_FONT,
                    TITLE_ROW_HEIGHT, UNIT_WIDTH, YIELD, YIELD_DEC, font)

# RPA_Summary rows (the RPA_Summary_SK mock-up): blank rows separate revenue / costs / returns / rank
S_ROW = {"crop": 4, "zone": 5, "yield": 6, "price": 7, "revenue": 8, "seed": 10, "fert": 11, "chem": 12,
         "cost": 13, "rpa": 15, "base": 16, "delta": 17, "rank": 19, "be_price": 20, "be_yield": 21}
S_SPACERS = (9, 14, 18)
S_BOLD = ("revenue", "cost", "rpa", "rank")
S_BLOCK_END = ("price", "chem")
FIRST_COL = 3   # crops start in column C (A Metric, B Unit)
UNIT_LABEL = {"$/t": "tonne", "$/sh.t": "short ton"}
UNIT_TEXT = {"$/bu": "$/bu", "c/lb": "¢/lb"}
YIELD_TEXT = {"$/bu": "bu/ac", "c/lb": "lb/ac"}
PULSE_MARK = "†"

# Control_Panel columns
F_COLS = ["Province", "Product", "Analysis", "Nutrient", "Nutrient Content", "N Credit (lb N / lb)", "Quote Unit",
          "Last Yr Quote", "Current Quote", "Last Yr $/lb", "Current $/lb", "Change"]
B_COLS = ["Province", "Crop", "Variety / Class", "Price Unit", "Last Yr Bid", "Current Bid", "Change",
          "Yield Unit", "Class Yield Last Yr", "Class Yield Current", "Class Seed $/ac Last Yr",
          "Class Seed $/ac Current"]
A_COLS = ["Province", "Crop", "Soil Zone Target", "Target Yield", "Yield Unit", "Seeding Rate", "Seeding Unit",
          "N\nlb/ac", "P2O5\nlb/ac", "K2O\nlb/ac", "S\nlb/ac"]
A_COL = {"yield": 4, "seed": 6, "N": 8, "P": 9, "K": 10, "S": 11}
AGRO_SHEET = "Agronomic_Rates"


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------
def _title(ws, title: str, subtitle: str) -> None:
    ws["A1"], ws["A2"] = title, subtitle
    ws["A1"].font, ws["A2"].font = TITLE_FONT, SUBTITLE_FONT
    ws.row_dimensions[1].height = TITLE_ROW_HEIGHT
    ws.sheet_view.showGridLines = False


def _header(ws, row: int, labels: list[str], first: int = 1) -> None:
    for j, text in enumerate(labels):
        cell = ws.cell(row, first + j, text)
        cell.fill, cell.font, cell.border, cell.alignment = HEADER_FILL, HEADER_FONT, GRID, CENTER
    ws.cell(row, first).alignment = LEFT
    ws.row_dimensions[row].height = 30


def _section(ws, row: int, text: str, first: int, last: int) -> None:
    ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    cell = ws.cell(row, first, text)
    cell.font, cell.alignment = SECTION_FONT, Alignment(vertical="center")
    for c in range(first, last + 1):
        ws.cell(row, c).border, ws.cell(row, c).fill = SECTION_RULE, SECTION_FILL
    ws.row_dimensions[row].height = 20


def _put(ws, row: int, col: int, value=None, fmt: str | None = None, *, kind: str = "num", align: str | None = None):
    """Bordered body cell. kind: num | bold | text | label | input | key | base."""
    cell = ws.cell(row, col, value)
    cell.border = GRID
    cell.font = {"bold": BOLD_FONT, "key": BOLD_FONT, "input": INPUT_FONT}.get(kind, TEXT_FONT)
    if kind == "input":
        cell.fill = INPUT_FILL
    elif kind == "key":
        cell.fill = LIVE_FILL
    if fmt:
        cell.number_format = fmt
    if kind in ("text", "label"):
        cell.alignment = LEFT if kind == "label" or align is None else Alignment(horizontal=align, vertical="center",
                                                                                   wrap_text=True)
    else:
        is_text = isinstance(value, str) and not value.startswith("=")
        cell.alignment = Alignment(horizontal=align or ("center" if is_text or value is None else "right"),
                                   vertical="center")
    return cell


def _rule_above(ws, row: int, first: int, last: int) -> None:
    """Medium rule on top of `row` (start of a new province / group)."""
    for c in range(first, last + 1):
        b = ws.cell(row, c).border
        ws.cell(row, c).border = Border(left=b.left, right=b.right, top=BLOCK_END, bottom=b.bottom)


def _rule_below(ws, row: int, first: int, last: int) -> None:
    for c in range(first, last + 1):
        b = ws.cell(row, c).border
        ws.cell(row, c).border = Border(left=b.left, right=b.right, top=b.top, bottom=BLOCK_END)


def _notes(ws, row: int, lines: list[str], last_col: int, title: str = "Definitions") -> int:
    _section(ws, row, title, 1, last_col)
    for j, text in enumerate(lines):
        ws.cell(row + 1 + j, 1, text).font = NOTE_FONT
    return row + 1 + len(lines)


def _widths(ws, widths: dict[int, float]) -> None:
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w


def _col(i: int) -> str:
    return get_column_letter(FIRST_COL + i)


# ---------------------------------------------------------------------------
# Control_Panel: A. fertilizer (every province), B. grain bids + class inputs (every province)
# ---------------------------------------------------------------------------
def _fert_rows(spec) -> int:
    return len(spec.quote_keys) + (1 if spec.n_blend else 0)


def _fert_table(ws, wb: Workbook, top: int, year: int, lb_cells: dict, current: dict) -> tuple[dict, int]:
    """[NCREDIT] $/lb = (quote / lb per unit - N content x $/lb N) / content, per province.
    [NCREDIT-MB] a blend row prices applied N at the NH3 share (H Last Yr, I current); the
    MAP / AS credit stays at the urea row."""
    _section(ws, top, "A. Fertilizer — retail quotes → active nutrient cost", 1, len(F_COLS))
    _header(ws, top + 1, F_COLS)
    refs: dict = {}
    r = top + 2
    for p_i, code in enumerate(PROVINCES):
        spec = province(code)
        quotes, cur = last_yr_quotes(code, year), current[code]["quotes"]
        lb = lb_cells[spec.quote_unit]
        rows = {n: r + i for i, n in enumerate(spec.quote_keys)}
        n_row = rows["N"]
        pr: dict = {"nutrient": {}, "nutrient_base": {}, "quote_cells": {}}
        for n, row in rows.items():
            prod = spec.products.get(n) or spec.n_blend[n]
            _put(ws, row, 1, spec.name, kind="label")
            _put(ws, row, 2, prod.product, kind="label")
            _put(ws, row, 3, prod.analysis)
            _put(ws, row, 4, RULES.raw["nutrients"][prod.nutrient]["label"], kind="label")
            _put(ws, row, 5, prod.grade, PERCENT)
            _put(ws, row, 6, prod.n_content, "0.000")
            _put(ws, row, 7, spec.quote_unit)
            _put(ws, row, 8, quotes[n], MONEY)
            _put(ws, row, 9, cur.get(n, quotes[n]), MONEY, kind="input")
            for q, out in (("H", 10), ("I", 11)):
                col = get_column_letter(out)
                credit = f"-F{row}*{col}${n_row}" if n in ("P", "K", "S") else ""
                _put(ws, row, out, f"=({q}{row}/{lb}{credit})/E{row}", PER_LB)
            _put(ws, row, 12, f'=IFERROR(K{row}/J{row}-1,"")', PERCENT)
            pr["quote_cells"][n] = (row, 9)
            if n in NUTRIENT_KEYS:
                pr["nutrient"][n], pr["nutrient_base"][n] = f"Control_Panel!$K${row}", f"Control_Panel!$J${row}"
        r += len(rows)
        if spec.n_blend:
            (src, _prod), = spec.n_blend.items()
            share, s_row = n_shares(code, year)[src], rows[src]
            _put(ws, r, 1, spec.name, kind="label")
            _put(ws, r, 2, f"Applied N (urea + {src} blend)", kind="label")
            _put(ws, r, 3, "—")
            _put(ws, r, 4, "Nitrogen (N), applied", kind="label")
            _put(ws, r, 5)
            _put(ws, r, 6)
            _put(ws, r, 7, f"{src} share")
            _put(ws, r, 8, share, PERCENT)
            _put(ws, r, 9, cur.get(f"share_{src}", share), PERCENT, kind="input")
            _put(ws, r, 10, f"=(1-H{r})*J{n_row}+H{r}*J{s_row}", PER_LB)
            _put(ws, r, 11, f"=(1-I{r})*K{n_row}+I{r}*K{s_row}", PER_LB)
            _put(ws, r, 12, f'=IFERROR(K{r}/J{r}-1,"")', PERCENT)
            pr["nutrient"]["N"], pr["nutrient_base"]["N"] = f"Control_Panel!$K${r}", f"Control_Panel!$J${r}"
            pr["share_cell"] = (r, 9)
            r += 1
        if p_i:
            _rule_above(ws, n_row, 1, len(F_COLS))
        for n in NUTRIENT_KEYS:
            wb.defined_names[f"Price_{n}_{code}"] = DefinedName(f"Price_{n}_{code}", attr_text=pr["nutrient"][n])
        refs[code] = pr
    return refs, r


def _bid_table(ws, top: int, crops: dict[str, list[CropBaseline]], current: dict) -> tuple[dict, int]:
    _section(ws, top, "B. Grain bids and class inputs", 1, len(B_COLS))
    _header(ws, top + 1, B_COLS)
    refs: dict = {}
    r = top + 2
    for p_i, code in enumerate(PROVINCES):
        spec = province(code)
        cur = current[code]
        pr: dict = {"bid": {}, "bid_base": {}, "bid_cells": {}, "class": {}}
        first = r
        for b in crops[code]:
            fmt = PRICE[b.price_basis]
            _put(ws, r, 1, spec.name, kind="label")
            _put(ws, r, 2, b.trade_label, kind="label")
            _put(ws, r, 3, b.variety, kind="label")
            _put(ws, r, 4, UNIT_TEXT[b.price_basis])
            _put(ws, r, 5, b.price, fmt)
            _put(ws, r, 6, cur["bids"].get(b.crop_key, b.price), fmt, kind="input")
            _put(ws, r, 7, f'=IFERROR(F{r}/E{r}-1,"")', PERCENT)
            _put(ws, r, 8, YIELD_TEXT[b.price_basis] if b.inherits else None)
            if b.inherits:   # [CROP-INHERIT] no guide history: own target yield and seed cost
                yfmt = YIELD[b.price_basis]
                _put(ws, r, 9, b.yield_whole, yfmt)
                _put(ws, r, 10, cur["class"].get((b.crop_key, "yield"), b.yield_whole), yfmt, kind="input")
                _put(ws, r, 11, b.seed, MONEY)
                _put(ws, r, 12, cur["class"].get((b.crop_key, "seed"), b.seed), MONEY, kind="input")
                pr["class"][b.crop_key] = {
                    "yield": f"Control_Panel!$J${r}", "yield_base": f"Control_Panel!$I${r}", "yield_cell": (r, 10),
                    "seed": f"Control_Panel!$L${r}", "seed_base": f"Control_Panel!$K${r}", "seed_cell": (r, 12)}
            else:
                for c in range(9, 13):
                    _put(ws, r, c)
            pr["bid"][b.crop_key], pr["bid_base"][b.crop_key] = f"Control_Panel!$F${r}", f"Control_Panel!$E${r}"
            pr["bid_cells"][b.crop_key] = (r, 6)
            r += 1
        if p_i:
            _rule_above(ws, first, 1, len(B_COLS))
        refs[code] = pr
    return refs, r


def build_control_panel(wb: Workbook, year: int, crops: dict[str, list[CropBaseline]], current: dict) -> dict:
    ws = wb.active
    ws.title = "Control_Panel"
    names = ", ".join(province(c).name for c in PROVINCES)
    _title(ws, "RPA Master Calculator — Control Panel",
           f"{names} · Last Yr = {year} provincial guides · edit the yellow cells; everything else recalculates")
    fert_end = 6 + sum(_fert_rows(province(c)) for c in PROVINCES)
    const = fert_end + 1
    lb_cells = {u: f"$B${const + k}" for k, u in enumerate(RULES.lb_per_unit)}
    fert, r = _fert_table(ws, wb, 4, year, lb_cells, current)
    for k, u in enumerate(RULES.lb_per_unit):
        _put(ws, const + k, 1, f"lb per {UNIT_LABEL.get(u, u)}", kind="label")
        _put(ws, const + k, 2, lb_per_unit(u), "#,##0.00")
    lines = ["$/lb nutrient = (quote ÷ lb per unit − N credit × $/lb N) ÷ nutrient content; the N in MAP and AS is "
             "credited at the province's own $/lb N [NCREDIT]."]
    for code in PROVINCES:
        spec = province(code)
        pub = guide_published_per_lb(code, year)
        how = {"implied_from_guide": f"{spec.name}: Last Yr quotes reproduce the {year} guide's implied $/lb (K "
                                     f"defaults to ${spec.default_per_lb.get('K', 0):.2f}/lb K2O where the guide "
                                     "budgets none).",
               "guide_published": f"{spec.name}: Last Yr quotes are the {year} guide's published {spec.quote_unit} "
                                  "quotes" + (" (guide $/lb: " + ", ".join(f"{k} ${v:.2f}" for k, v in pub.items())
                                              + ")" if pub else "") + "."}[spec.last_yr_quotes]
        if spec.n_blend:
            src = next(iter(spec.n_blend))
            how += (f" Applied N is the guide's urea / {src} blend (share editable); the MAP / AS credit stays at "
                    "urea [NCREDIT-MB].")
        lines.append(how)
    end = const + len(RULES.lb_per_unit) + 1
    for j, text in enumerate(lines):
        ws.cell(end + j, 1, text).font = NOTE_FONT
    bids, r = _bid_table(ws, end + len(lines) + 1, crops, current)
    ws.cell(r, 1, "Pulses are bid in ¢/lb, all other crops in $/bu. Yellow = input. Class columns are for crops with "
                  "no guide history (Last Yr mirrors the parent crop until a bid, yield and seed cost are "
                  "entered here or in Input_Prices/crop_price_template.csv) [CROP-INHERIT].").font = NOTE_FONT
    refs = {code: {**fert[code], **bids[code]} for code in PROVINCES}
    ws.freeze_panes = "B4"
    _widths(ws, {1: 16, 2: 26, 3: 24, 4: 20, 5: 11, 6: 11, 7: 10, 8: 11, 9: 11, 10: 12, 11: 12, 12: 12})
    return refs


# ---------------------------------------------------------------------------
# Agronomic_Rates: one table, rows grouped by province
# ---------------------------------------------------------------------------
def build_agronomic_rates(wb: Workbook, crops: dict[str, list[CropBaseline]], cp: dict, year: int) -> dict:
    ws = wb.create_sheet(AGRO_SHEET)
    _title(ws, "Agronomic Rates — Physical Inputs per Acre",
           f"{year} provincial guides · whole target yields and applied nutrients feed the RPA_Summary tabs")
    _header(ws, 4, A_COLS)
    refs: dict = {}
    seed_notes, flags = [], []
    r = 5
    for p_i, code in enumerate(PROVINCES):
        spec, pr, first = province(code), {}, r
        for b in crops[code]:
            _put(ws, r, 1, spec.name, kind="label")
            _put(ws, r, 2, b.trade_label, kind="label")
            _put(ws, r, 3, b.region_label, kind="label")
            _put(ws, r, 5, YIELD_TEXT[b.price_basis])
            parent = pr.get(b.inherits or "")
            if parent:   # [CROP-INHERIT] yield from the Control_Panel class input, NPKS / seeding follow the parent
                _put(ws, r, 4, f"={cp[code]['class'][b.crop_key]['yield']}", YIELD[b.price_basis], kind="bold")
                _put(ws, r, 6, f"={parent['seed']}", RATE)
                _put(ws, r, 7, f"=G{parent['row']}")
                for n in NUTRIENT_KEYS:
                    _put(ws, r, A_COL[n], f"={parent[n]}", RATE)
            else:
                _put(ws, r, 4, b.yield_whole, YIELD[b.price_basis], kind="bold")
                if b.seeding_lbs is not None:
                    _put(ws, r, 6, b.seeding_lbs, RATE)
                    _put(ws, r, 7, "lb/ac")
                else:
                    other = (b.seeding_other or "—").replace(" to ", "–")
                    _put(ws, r, 6, other.split(" @ ")[0], kind="text", align="center")
                    _put(ws, r, 7, "as printed")
                for n in NUTRIENT_KEYS:
                    _put(ws, r, A_COL[n], b.rates.get(n), RATE)
            pr[b.crop_key] = {k: f"{AGRO_SHEET}!${get_column_letter(c)}${r}" for k, c in A_COL.items()}
            pr[b.crop_key]["row"] = r
            if b.seeding_note:
                seed_notes.append(f"{spec.code} {b.trade_label}: {b.seeding_note}")
            flags += [f"{spec.code} {b.trade_label} — {f}" for f in b.flags if not b.inherits]
            r += 1
        if p_i:
            _rule_above(ws, first, 1, len(A_COLS))
        refs[code] = pr
    rule_flags = [f for f in flags if ("[" in f or f.split(" — ", 1)[1].startswith(("unverified", "core", "seed $")))
                  and "[SK-S-IMPLIED-ZERO]" not in f]           # routine rule, applied to every SK crop
    lines = ["Target yields are the guide targets rounded to whole bu/ac (lb/ac for pulses) [YIELD-ROUND]. Soil-zone "
             "provinces average every active zone where published; durum and red lentils Brown + Dark "
             "Brown only; Manitoba is province-wide [REGION-AVG-RULE].",
             "P as lb P2O5/ac, K as lb K2O/ac. Rules and overrides: docs/AGENT_RULES.json, docs/METHODOLOGY.md.",
             "Seeding rate basis:", *(f"    • {n}" for n in seed_notes)]
    if rule_flags:
        lines += ["Guide data flags:", *(f"    • {f}" for f in rule_flags[:12])]
    _notes(ws, r + 1, lines, len(A_COLS), "Notes")
    ws.freeze_panes = "C5"
    _widths(ws, {1: 16, 2: 18, 3: 30, 4: 11, 5: 9, 6: 14, 7: 11, 8: 9, 9: 9, 10: 9, 11: 9})
    return refs


# ---------------------------------------------------------------------------
# RPA_Summary_<code>
# ---------------------------------------------------------------------------
def build_summary(wb: Workbook, code: str, crops: list[CropBaseline], cp: dict, rates: dict, year: int) -> dict:
    spec, sheet = province(code), f"RPA_Summary_{code}"
    ws = wb.create_sheet(sheet)
    _title(ws, f"RPA Summary — {spec.name} (Return Over Direct Cash Inputs)",
           f"Live against Control_Panel and {AGRO_SHEET} · Last Yr = {year} guide at guide prices")
    pulses = [b.trade_label for b in crops if b.pulse]
    mark = PULSE_MARK if pulses else ""
    labels = {"zone": "Soil Zone Target", "yield": "Target Yield", "price": "Farm Price", "revenue": "Gross Revenue",
              "seed": "Seed Cost", "fert": "Fertilizer Cost", "chem": "Chemical Cost", "cost": "Direct Cash Costs",
              "rpa": "Return Over Cash Inputs", "base": "Last Yr RPA", "delta": "Delta vs Last Yr", "rank": "Rank",
              "be_price": "Breakeven Price", "be_yield": "Breakeven Yield"}
    y_unit = f"bu/ac · lb/ac{mark}" if pulses else "bu/ac"
    p_unit = f"$/bu · ¢/lb{mark}" if pulses else "$/bu"
    units = {"zone": "", "yield": y_unit, "price": p_unit, "rank": "", "be_price": p_unit, "be_yield": y_unit}
    last = FIRST_COL + len(crops) - 1
    _header(ws, S_ROW["crop"], ["Metric", "Unit"] + [b.trade_label + (PULSE_MARK if b.pulse else "") for b in crops])
    for key, row in S_ROW.items():
        if key == "crop":
            continue
        kind = "bold" if key in S_BOLD else "num"
        _put(ws, row, 1, labels[key], kind="label").font = BOLD_FONT if key in S_BOLD else TEXT_FONT
        _put(ws, row, 2, units.get(key, "$/ac"), kind=kind)
    for row in S_SPACERS:
        for c in range(1, last + 1):
            _put(ws, row, c)
    first_l, last_l = _col(0), _col(len(crops) - 1)
    rpa_range = f"${first_l}${S_ROW['rpa']}:${last_l}${S_ROW['rpa']}"
    col_of = {b.crop_key: _col(i) for i, b in enumerate(crops)}
    for i, b in enumerate(crops):
        col, L, k = FIRST_COL + i, _col(i), b.crop_key
        rate, scale = rates[k], "/100" if b.pulse else ""
        R = {name: f"{L}{v}" for name, v in S_ROW.items()}

        def fert(prices: dict) -> str:
            return "+".join(f"{rate[n]}*{prices[n]}" for n in NUTRIENT_KEYS)

        z = _put(ws, S_ROW["zone"], col, b.region_label.replace(" + ", " +\n"),
                 kind="text", align="center")
        z.font = SMALL_FONT
        _put(ws, S_ROW["yield"], col, f"={rate['yield']}", YIELD[b.price_basis])
        _put(ws, S_ROW["price"], col, f"={cp['bid'][k]}", PRICE[b.price_basis])
        _put(ws, S_ROW["revenue"], col, f"={R['yield']}*{R['price']}{scale}", MONEY, kind="bold")
        cls = cp["class"].get(k)
        if cls:
            _put(ws, S_ROW["seed"], col, f"={cls['seed']}", MONEY)
            _put(ws, S_ROW["chem"], col, f"={col_of[b.inherits]}{S_ROW['chem']}", MONEY)
            y_base, seed_base = cls["yield_base"], cls["seed_base"]
        else:
            _put(ws, S_ROW["seed"], col, b.seed, MONEY)
            _put(ws, S_ROW["chem"], col, b.chem, MONEY)
            y_base, seed_base = R["yield"], R["seed"]
        _put(ws, S_ROW["fert"], col, f"={fert(cp['nutrient'])}", MONEY)
        _put(ws, S_ROW["cost"], col, f"={R['seed']}+{R['fert']}+{R['chem']}", MONEY, kind="bold")
        _put(ws, S_ROW["rpa"], col, f"={R['revenue']}-{R['cost']}", RETURN, kind="bold")
        _put(ws, S_ROW["base"], col,
             f"={y_base}*{cp['bid_base'][k]}{scale}-({seed_base}+{fert(cp['nutrient_base'])}+{R['chem']})", RETURN)
        _put(ws, S_ROW["delta"], col, f"={R['rpa']}-{R['base']}", MONEY)
        rank = _put(ws, S_ROW["rank"], col, f"=RANK({R['rpa']},{rpa_range},0)", RANK)
        rank.fill, rank.font = RANK_FILL, RANK_FONT
        be_price = f"{R['cost']}/{R['yield']}*100" if b.pulse else f"{R['cost']}/{R['yield']}"
        be_yield = f"{R['cost']}/({R['price']}/100)" if b.pulse else f"{R['cost']}/{R['price']}"
        _put(ws, S_ROW["be_price"], col, f'=IFERROR({be_price},"")', PRICE[b.price_basis])
        _put(ws, S_ROW["be_yield"], col, f'=IFERROR({be_yield},"")', YIELD_DEC[b.price_basis])
    for key in S_BLOCK_END:
        _rule_below(ws, S_ROW[key], 1, last)
    ws.row_dimensions[S_ROW["zone"]].height = 42
    for key in ("rpa", "base", "delta"):
        ws.conditional_formatting.add(f"{first_l}{S_ROW[key]}:{last_l}{S_ROW[key]}",
                                      CellIsRule(operator="lessThan", formula=["0"], font=RED_FONT))
    ws.conditional_formatting.add(f"{first_l}{S_ROW['rank']}:{last_l}{S_ROW['rank']}",
                                  CellIsRule(operator="lessThanOrEqual", formula=["3"], font=font(10, True, color="006100")))
    lines = [
        "Gross Revenue (C) = Target Yield × Farm Price; pulses: lb/ac × ¢/lb ÷ 100 [YIELD-ROUND: whole targets, "
        f"edit on {AGRO_SHEET}].",
        "Direct Cash Costs (D) = Seed (incl. treatment) + Fertilizer (N + P + K + S) + Chemicals [DCASH]. Fuel, repairs, "
        "insurance, trucking, labour, interest and overhead are excluded.",
        f"Fertilizer = Σ lb/ac ({AGRO_SHEET}) × $/lb nutrient (Control_Panel, [NCREDIT]); seed and chemicals are the "
        f"{year} guide $/ac.",
        f"Last Yr RPA = the same calculation at the {year} guide's bids and nutrient prices; Delta = (C − D) − Last Yr "
        "RPA. Rank 1 = highest return over cash inputs (ties share a rank; top 3 in bold).",
        "Breakeven Price = D ÷ Target Yield; Breakeven Yield = D ÷ Farm Price. Rules: docs/METHODOLOGY.md.",
    ]
    if pulses:
        lines.append(f"{PULSE_MARK} Pulses ({', '.join(pulses)}): yields in lb/ac, prices in ¢/lb.")
    _notes(ws, S_ROW["be_yield"] + 2, lines, last)
    ws.freeze_panes = ws.cell(S_ROW["zone"], FIRST_COL)
    _widths(ws, {1: LABEL_WIDTH, 2: UNIT_WIDTH, **{FIRST_COL + i: CROP_WIDTH for i in range(len(crops))}})
    ws.page_setup.orientation, ws.page_setup.fitToWidth = "landscape", 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return {b.crop_key: {k: f"{sheet}!${_col(i)}${r}" for k, r in S_ROW.items() if k not in ("crop", "zone")}
            for i, b in enumerate(crops)}


# ---------------------------------------------------------------------------
# RPA_Prairie: acreage-weighted Prairie benchmark beside the provincial returns [PRAIRIE-WEIGHTED-RPA]
# ---------------------------------------------------------------------------
P_ROW_KEYS = ("yield", "price", "revenue", None, "seed", "fert", "chem", "cost", None, "rpa", "base", "delta", None,
              "rank", "be_price", "be_yield")


def build_prairie(wb: Workbook, year: int, live: dict[str, dict[str, dict[str, str]]],
                  crops: dict[str, list[CropBaseline]]) -> dict:
    ws = wb.create_sheet("RPA_Prairie")
    block = load_weights()
    base = prairie_last_yr(year)["crops"]           # Last Yr: the same aggregation at Last Yr inputs
    keys = [k for k in RULES.crops if any(k in live[c] for c in PROVINCES)]
    present = {k: [c for c in PROVINCES if k in live[c]] for k in keys}
    y0, y1 = block["years"][0], block["years"][-1]
    _title(ws, "RPA Prairie — Acreage-Weighted Benchmark (Manitoba, Saskatchewan, Alberta)",
           f"Last Yr = {year} provincial guides, live against the Control_Panel · weights = StatCan 32-10-0359 seeded "
           f"area, {y0}–{y1} average")
    pulses = [crop(k).trade_label for k in keys if crop(k).pulse]
    mark = PULSE_MARK if pulses else ""
    y_unit, p_unit = f"bu/ac · lb/ac{mark}", f"$/bu · ¢/lb{mark}"
    last = FIRST_COL + len(keys) - 1
    _header(ws, 4, ["Metric", "Unit"] + [crop(k).trade_label + (PULSE_MARK if crop(k).pulse else "") for k in keys])
    rows: dict[str, int] = {}
    r = 5
    for code in PROVINCES:
        rows[f"w_{code}"] = r
        _put(ws, r, 1, f"Acreage Weight — {code}", kind="label")
        _put(ws, r, 2, "% budgeted acres")
        r += 1
    rows["coverage"] = r
    _put(ws, r, 1, "Acreage Coverage", kind="label")
    _put(ws, r, 2, "% Prairie acres")
    r += 1
    labels = {"yield": ("Prairie Yield", y_unit), "price": ("Prairie Price", p_unit),
              "revenue": ("Gross Revenue", "$/ac"), "seed": ("Seed Cost", "$/ac"),
              "fert": ("Fertilizer Cost (N-credited)", "$/ac"), "chem": ("Chemical Cost", "$/ac"),
              "cost": ("Direct Cash Costs", "$/ac"), "rpa": ("Return Over Cash Inputs", "$/ac"),
              "base": ("Last Yr RPA", "$/ac"), "delta": ("Delta vs Last Yr", "$/ac"), "rank": ("Rank", ""), "be_price": ("Breakeven Price", p_unit), "be_yield": ("Breakeven Yield", y_unit)}
    spacers = []
    for key in P_ROW_KEYS:
        if key is None:
            spacers.append(r)
        else:
            rows[key] = r
            bold = key in ("revenue", "cost", "rpa", "rank")
            _put(ws, r, 1, labels[key][0], kind="label").font = BOLD_FONT if bold else TEXT_FONT
            _put(ws, r, 2, labels[key][1], kind="bold" if bold else "num")
        r += 1
    for row in spacers:
        for c in range(1, last + 1):
            _put(ws, row, c)
    rank_range = f"${_col(0)}${rows['rpa']}:${_col(len(keys) - 1)}${rows['rpa']}"
    cols = {}
    for i, k in enumerate(keys):
        col, L, c = FIRST_COL + i, _col(i), crop(k)
        cols[k] = L
        provs, raw = present[k], (block["crops"].get(k) or {}).get("weights", {})
        coverage = sum(raw.get(p, 0.0) for p in provs)
        for code in PROVINCES:
            if code in provs:
                w = raw.get(code, 0.0) / coverage if coverage else 1 / len(provs)
                _put(ws, rows[f"w_{code}"], col, w, PERCENT)
            else:
                _put(ws, rows[f"w_{code}"], col, "—")
        _put(ws, rows["coverage"], col, coverage, PERCENT)

        def wsum(metric: str) -> str:
            return "+".join(f"{L}{rows['w_' + p]}*{live[p][k][metric]}" for p in provs)

        R = {m: f"{L}{rows[m]}" for m in ("yield", "price", "revenue", "seed", "fert", "chem", "cost", "rpa")}
        scale = "/100" if c.pulse else ""
        _put(ws, rows["yield"], col, f"={wsum('yield')}", YIELD_DEC[c.price_basis])
        _put(ws, rows["price"], col, f"={wsum('price')}", PRICE[c.price_basis])
        _put(ws, rows["revenue"], col, f"={R['yield']}*{R['price']}{scale}", MONEY, kind="bold")
        for m in ("seed", "fert", "chem"):
            _put(ws, rows[m], col, f"={wsum(m)}", MONEY)
        _put(ws, rows["cost"], col, f"={R['seed']}+{R['fert']}+{R['chem']}", MONEY, kind="bold")
        _put(ws, rows["rpa"], col, f"={R['revenue']}-{R['cost']}", RETURN, kind="bold")
        rk = _put(ws, rows["rank"], col, f"=RANK({R['rpa']},{rank_range},0)", RANK)
        rk.fill, rk.font = RANK_FILL, RANK_FONT
        be_p = f"{R['cost']}/{R['yield']}*100" if c.pulse else f"{R['cost']}/{R['yield']}"
        be_y = f"{R['cost']}/({R['price']}/100)" if c.pulse else f"{R['cost']}/{R['price']}"
        _put(ws, rows["be_price"], col, f'=IFERROR({be_p},"")', PRICE[c.price_basis])
        _put(ws, rows["be_yield"], col, f'=IFERROR({be_y},"")', YIELD_DEC[c.price_basis])
        _put(ws, rows["base"], col, base[k]["rpa"], RETURN)
        _put(ws, rows["delta"], col, f"={R['rpa']}-{L}{rows['base']}", MONEY)
    for key in ("coverage", "price", "chem"):
        _rule_below(ws, rows[key], 1, last)
    lo, hi = _col(0), _col(len(keys) - 1)
    for key in ("rpa", "base", "delta"):
        ws.conditional_formatting.add(f"{lo}{rows[key]}:{hi}{rows[key]}",
                                      CellIsRule(operator="lessThan", formula=["0"], font=RED_FONT))
    ws.conditional_formatting.add(f"{lo}{rows['rank']}:{hi}{rows['rank']}",
                                  CellIsRule(operator="lessThanOrEqual", formula=["3"], font=font(10, True, color="006100")))
    notes = [
        f"Acreage weights [PRAIRIE-WEIGHTED-RPA]: each province's share of the {y0}–{y1} average seeded area "
        "(StatCan Table 32-10-0359, Seeded area in acres), rescaled over the provinces whose guide budgets the crop; "
        "Acreage Coverage = the share of MB + SK + AB acres those provinces represent.",
        "Prairie Yield and Price = Σ weight × provincial whole target yield and farm price; Gross Revenue = Prairie "
        "Yield × Prairie Price (pulses ÷ 100); Seed, Fertilizer (each province's N-credited NPKS cost) and Chemical = "
        "Σ weight × provincial $/ac; Direct Cash Costs = Seed + Fertilizer + Chemical; Return = C − D.",
        f"Last Yr RPA = the same aggregation at each province's {year} guide bids and Last Yr nutrient prices; Delta = "
        "Return − Last Yr RPA. Rank 1 = highest Prairie return (ties share a rank; top 3 in bold).",
        "StatCan classes: CWRS, CPSR + CPSW, durum, canola, barley (feed and malt both use total barley), oats, dry "
        "peas, lentils (all), soybeans, grain corn, flaxseed, fall rye. Rebuild the weights with "
        "python -m src.pricing.statcan_weights.",
    ]
    notes += [f"StatCan: {n}." for n in block.get("notes", [])]
    if pulses:
        notes.append(f"{PULSE_MARK} Pulses ({', '.join(pulses)}): yields in lb/ac, prices in ¢/lb.")
    _notes(ws, r + 2, notes, last)
    ws.freeze_panes = "C5"
    _widths(ws, {1: LABEL_WIDTH + 2, 2: UNIT_WIDTH + 2, **{FIRST_COL + i: CROP_WIDTH for i in range(len(keys))}})
    ws.page_setup.orientation, ws.page_setup.fitToWidth = "landscape", 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return {"rows": rows, "cols": cols, "keys": keys}


# ---------------------------------------------------------------------------
# Historical_Ratios (rules: outputs.historical_ratios)
# ---------------------------------------------------------------------------
def workbook_history(code: str, year: int) -> dict[int, dict[str, float]]:
    """Static columns: each guide year's own return before `year`; `year` locked to Last Yr RPA."""
    hist = {y: v for y, v in history(code, range(RULES.years[0], year)).items()}
    hist[year] = {b.crop_key: last_yr(b)["rpa"] for b in province_baselines(code, year)}
    return dict(sorted(hist.items()))


def ratio_value(num: float | None, den: float | None):
    """[HIST-RATIOS] numerator ÷ base return; 'n/m' when either return is ≤ 0, None when absent."""
    if num is None or den is None:
        return None
    return num / den if num > 0 and den > 0 else "n/m"


def build_historical(wb: Workbook, year: int, live: dict[str, dict[str, str]],
                     crops: dict[str, list[CropBaseline]]) -> dict:
    ws = wb.create_sheet("Historical_Ratios")
    cfg = RULES.historical
    codes = [c for c in cfg["provinces"] if c in PROVINCES]
    hist = {code: workbook_history(code, year) for code in codes}
    years = sorted({y for h in hist.values() for y in h})
    heads = ["Base", "Ratio", "Province"] + [str(y) for y in years] + ["Forecast"]
    last = len(heads)
    _title(ws, "Historical Margin Ratios — return over direct cash inputs, numerator ÷ base crop",
           f"Each guide year's own prices and costs at whole-number yields ({years[0]}–{years[-2]}); {year} = Last "
           "Yr RPA; Forecast recalculates from the Control_Panel")
    _header(ws, 4, heads)
    rows, r = [], 5
    for g_i, g in enumerate(cfg["bases"]):
        base, first = g["base"], r
        for num in g["numerators"]:
            for code in codes:
                have = {b.crop_key for b in crops[code]}
                if not {base, num} <= have:
                    continue
                _put(ws, r, 1, crop(base).trade_label if r == first else None, kind="label").font = BOLD_FONT
                _put(ws, r, 2, f"{crop(num).trade_label} / {crop(base).trade_label}", kind="label")
                _put(ws, r, 3, code)
                for j, y in enumerate(years):
                    v = ratio_value(hist[code].get(y, {}).get(num), hist[code].get(y, {}).get(base))
                    _put(ws, r, 4 + j, "—" if v is None else v, RATIO)
                n, d = live[code][num]["rpa"], live[code][base]["rpa"]
                _put(ws, r, last, f'=IF(AND(ISNUMBER({n}),ISNUMBER({d}),{n}>0,{d}>0),{n}/{d},"n/m")', RATIO, kind="key")
                rows.append({"row": r, "province": code, "numerator": num, "base": base})
                r += 1
        if g_i and r > first:
            _rule_above(ws, first, 1, last)
    _notes(ws, r + 1, [
        "Ratio = the numerator crop's return over direct cash inputs ÷ the base crop's, same province and guide year; "
        "above 1.00 the numerator earns more per acre. n/m = not meaningful (a return ≤ 0); — = not budgeted.",
        f"{years[0]}–{years[-2]}: each guide year's bid × whole target yield − its own seed, fertilizer and chemical "
        f"lines [HIST-RATIOS]; {year} = Last Yr RPA (RPA_Summary at Last Yr inputs); Forecast links to the live "
        "RPA_Summary tabs.",
    ], last)
    ws.freeze_panes = "D5"
    _widths(ws, {1: 14, 2: 28, 3: 9, **{4 + j: 9 for j in range(len(years))}, last: 11})
    return {"history": hist, "years": years, "rows": rows, "forecast_col": last}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def current_inputs(code: str, crops: list[CropBaseline], as_of: str | None) -> dict:
    """Current (yellow) values: Last Yr unless Input_Prices/ has a snapshot on or before `as_of`."""
    cur = {"quotes": {}, "bids": {}, "class": {}}
    if not as_of:
        return cur
    cur["quotes"] = {n: q["price"] for n, q in load_fertilizer_quotes(code, as_of).items()}
    for key, bid in load_crop_bids(code, as_of).items():
        if bid["price"] is not None:
            cur["bids"][key] = bid["price"]
        if bid["yield"] is not None and any(b.crop_key == key and b.inherits for b in crops):
            cur["class"][(key, "yield")] = bid["yield"]
    return cur


def build_workbook(year: int = BASELINE_YEAR, out: Path = MASTER_WORKBOOK, prices_asof: str | None = None) -> tuple[Path, dict]:
    crops = {code: list(province_baselines(code, year)) for code in PROVINCES}
    current = {code: current_inputs(code, crops[code], prices_asof) for code in PROVINCES}
    wb = Workbook()
    cp = build_control_panel(wb, year, crops, current)
    rates = build_agronomic_rates(wb, crops, cp, year)
    live = {code: build_summary(wb, code, crops[code], cp[code], rates[code], year) for code in PROVINCES}
    hist = build_historical(wb, year, live, crops)
    prairie = build_prairie(wb, year, live, crops)
    order = [t for t in RULES.workbook_tabs if t in wb.sheetnames] + \
            [t for t in wb.sheetnames if t not in RULES.workbook_tabs]
    wb._sheets = [wb[t] for t in order]
    wb.active = 0
    wb.calculation.fullCalcOnLoad = True
    wb.properties.title = "RPA Master Calculator"
    wb.properties.subject = f"Return per acre ({', '.join(PROVINCES)}), Last Yr {year}"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    wb.close()
    return out, {"year": year, "cp": cp, "crops": crops, "rates": rates, "hist": hist, "prairie": prairie,
                 "tabs": order}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", type=int, default=BASELINE_YEAR)
    ap.add_argument("--out", type=Path, default=MASTER_WORKBOOK)
    ap.add_argument("--prices-asof", help="fill Current inputs from Input_Prices/ snapshots (YYYY-MM-DD)")
    ap.add_argument("--verify", action="store_true", help="recalculate in Excel and check against the engine")
    args = ap.parse_args(argv)
    out, layout = build_workbook(args.year, args.out, args.prices_asof)
    print(f"Wrote {out}  (Last Yr {args.year}, tabs: {', '.join(layout['tabs'])}, {date.today():%Y-%m-%d})")
    if args.verify:
        from .excel_verify import verify_in_excel  # noqa: PLC0415
        return 0 if verify_in_excel(out, layout) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
