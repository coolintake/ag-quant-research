"""Alberta AgriProfit$ reference tables -> output/AB_AgriProfit_Reference_Tables.xlsx (inspection only).

    Table1_Seeding_Rates       Table 1 as printed: 6 soil zones x (lbs/ac, bu/ac, $/ac), then per
                               zone the budget-table seed $/ac and delta (budget - Table 1), and a flag
    Table2_Chemical_Breakdown  Table 2 as printed: 6 soil zones x (PreSeed, In Crop, F/I/PH/D), then
                               per zone the Table 2 total, the budget chemical line and delta, and a flag

Each guide year is a block (newest first). The grids are re-read from the PDFs cell by cell: every
number is placed in its zone x sub-column by x position (the MVS pipeline keeps only the values in
print order). Letter-spaced / image pages are OCR'd in three rendered variants that vote per cell;
cells the variants disagree on are shaded and carry a comment. Budget values come from the budget
tables (Tables 3-8), OCR-repaired by src/normalize/ab_cop.py where the crop is in the registry.

Nothing here feeds the engine; the only rule applied downstream is [OVR-AB-LENTIL-SEED].

    python -m src.outputs.export_ab_reference_tables [--years 2025 2026]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import Counter
from pathlib import Path

import pdfplumber
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..config import OUTPUT_DIR, YEARS, overrides_for, province, source_pdfs
from ..normalize.crops import resolve
from ..sources.ab_extractor import TABLE_ZONE_ORDER, broken_text, image_only, ocr_number, ocr_words, text_words
from ..sources.sk_extractor import group_lines, to_number
from .theme import (BASE_FILL, CURRENCY, GRID, HEADER_FILL, HEADER_FONT, NOTE_FONT, NUM_FONT, RED_FONT,
                    SECTION_FILL, SECTION_FONT, SUBTITLE_FONT, TEXT_FONT, TITLE_FONT)

SPEC = province("AB")
OUT = OUTPUT_DIR / "AB_AgriProfit_Reference_Tables.xlsx"
EXPORT_VERSION = "1.0.0"

# Rows of Tables 1 and 2 in print order, with the other spellings the budget tables use.
ROWS = {
    "Spring Wheat": [], "CPS Wheat": [], "Durum Wheat": ["durum"], "Soft White Wheat": ["soft wheat"],
    "Winter Wheat": [], "Malt Barley": ["malt"], "Feed Barley": [], "Milling Oats": ["oats"],
    "Argentine Canola": ["argentine ht canola", "ht canola", "argentine canola ht"], "Polish Canola": [],
    "Field Pea": ["field peas", "peas"], "Yellow Mustard": ["mustard"], "Chickpeas": ["kabuli chickpea", "kabuli"],
    "Green Lentils": [], "Red Lentils": ["red lentil"], "Flax": [], "Dry Beans": ["beans"],
    "Cereal Silage": ["silage"], "Mixed Hay": [], "Alfalfa Hay": [],
}
SUBS = {"seed": ("lbs/ac", "bu/ac", "$/ac"), "chem": ("PreSeed", "In Crop", "F/I/PH/D")}
TOL = {"seed": 0.015, "chem": 0.03}          # same tolerances as the normalizer's cross-checks
VARIANTS = ("render", "bin3", "sharp3")

DISAGREE_FILL = PatternFill("solid", fgColor="FDE68A")
FLAG_FILL = PatternFill("solid", fgColor="FECACA")
OVERRIDE_FILL = PatternFill("solid", fgColor="DBEAFE")


def _ymid(w: dict) -> float:
    return (w["top"] + w["bottom"]) / 2


def _norm(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.lower()).strip()


def match_rows(labels: list[str], cutoff: float = 0.6) -> dict[str, str]:
    """{printed label: row name}. Greedy best-first, so an OCR-damaged 'lentils' takes whichever
    lentil row the exact match left free."""
    names = {name: [_norm(name)] + aliases for name, aliases in ROWS.items()}
    scored = sorted(((max(difflib.SequenceMatcher(None, _norm(lab), a).ratio() for a in al), lab, name)
                     for lab in labels for name, al in names.items()), reverse=True)
    out, taken = {}, set()
    for score, lab, name in scored:
        if score >= cutoff and lab not in out and name not in taken:
            out[lab] = name
            taken.add(name)
    return out


# ---------------------------------------------------------------------------
# Table 1 / 2 grids, cell by cell
# ---------------------------------------------------------------------------
def _centers(lines: list[list[dict]], kind: str, values: list[dict]) -> tuple[list[float] | None, bool]:
    """(x centre of the 18 sub-columns, read from the unit header?): the unit header when it reads
    cleanly, else the value clusters."""
    rx = r"^(lbs/ac|bu/ac|\$/ac)$" if kind == "seed" else r"^(preseed|f/i/ph/d)$"
    for ln in lines:
        if kind == "chem":   # "In Crop" is two words; merge so each sub-column is one token
            merged = []
            for w in ln:
                if w["text"].lower() == "crop" and merged and merged[-1]["text"].lower() == "in":
                    merged[-1] = {**merged[-1], "text": "preseed", "x1": w["x1"]}   # any match token
                else:
                    merged.append(w)
            ln = merged
        subs = [w for w in ln if re.match(rx, w["text"].lower())]
        if len(subs) == 18:
            return [(w["x0"] + w["x1"]) / 2 for w in subs], True
    xs = sorted((w["x0"] + w["x1"]) / 2 for w in values)
    clusters: list[list[float]] = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 12:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return ([sum(c) / len(c) for c in clusters] if len(clusters) == 18 else None), False


def _number(text: str, ocr: bool) -> float | None:
    v = to_number(text)
    if v is None and ocr:
        fixed = ocr_number(text.replace("_", "."))
        v = to_number(fixed) if fixed else None
    return v


def parse_grid(words: list[dict], y_from: float, y_to: float, kind: str, fallback: list[float] | None = None,
               ocr: bool = False) -> tuple[dict, list[float] | None, bool, list[str]]:
    """({row: {(zone, sub): value}}, sub-column centres, centres from the header?, warnings)."""
    region = [w for w in words if y_from < w["top"] < y_to]
    lines = group_lines(region)
    numeric = [w for w in region if _number(w["text"], ocr) is not None]
    centers, header_ok = _centers(lines, kind, numeric)
    centers = centers or fallback
    if not centers:
        return {}, None, False, [f"{kind}: sub-columns not found"]
    pitch = (centers[-1] - centers[0]) / 17
    first_x = centers[0] - 0.6 * pitch
    head = next((ln for ln in lines if sum(bool(re.search(r"(?i)black|brown|wooded|irrigat", w["text"]))
                                           for w in ln) >= 3), None)
    top = max(w["bottom"] for w in head) if head else y_from
    body = [w for w in region if w["top"] > top]
    label_lines = [{"text": " ".join(w["text"] for w in ln).strip(), "y": sum(_ymid(w) for w in ln) / len(ln)}
                   for ln in group_lines([w for w in body if (w["x0"] + w["x1"]) / 2 < first_x])]
    gaps = sorted(b["y"] - a["y"] for a, b in zip(label_lines, label_lines[1:]))
    reach = 0.5 * (gaps[len(gaps) // 2] if gaps else 10)    # half the typical row spacing
    cells_of: dict[int, dict] = {}
    for w in body:
        c, v = (w["x0"] + w["x1"]) / 2, _number(w["text"], ocr)
        if c < first_x or v is None or not label_lines:
            continue
        i = min(range(len(label_lines)), key=lambda k: abs(_ymid(w) - label_lines[k]["y"]))
        j = min(range(18), key=lambda k: abs(c - centers[k]))
        if abs(_ymid(w) - label_lines[i]["y"]) <= reach and abs(c - centers[j]) <= 0.5 * pitch:
            cells_of.setdefault(i, {})[(TABLE_ZONE_ORDER[j // 3], j % 3)] = v
    rows, warnings = [(label_lines[i]["text"], cells) for i, cells in sorted(cells_of.items())], []
    names = match_rows([lab for lab, _ in rows])
    grid = {}
    for label, cells in rows:
        name = names.get(label)
        if name is None:
            if not re.search(r"(?i)cropping|classification|f/i/ph/d|table|^\d+$", label):
                warnings.append(f"{kind}: unmatched row {label!r} {cells}")
            continue
        grid[name] = {**grid.get(name, {}), **cells}
    return grid, centers, header_ok, warnings


def _read_page(words: list[dict], height: float, ocr: bool) -> dict[str, tuple[dict, bool, list[str]]]:
    """{kind: (grid, header_ok, warnings)} for whichever of Tables 1 / 2 start on this page."""
    t1, t2 = _table_top(words, "1"), _table_top(words, "2")
    out, seed_centers = {}, None
    if t1 is not None:
        g, seed_centers, ok, w = parse_grid(words, t1, t2 or height, "seed", ocr=ocr)
        out["seed"] = (g, ok, w)
    if t2 is not None:
        g, _c, ok, w = parse_grid(words, t2, height, "chem", fallback=seed_centers, ocr=ocr)
        out["chem"] = (g, ok, w)
    return out


def _cells(grid: dict) -> int:
    return sum(len(c) for c in grid.values())


def _table_top(words: list[dict], n: str) -> float | None:
    return next((ln[0]["top"] for ln in group_lines(words) if re.fullmatch(r"(?i)table", ln[0]["text"])
                 and len(ln) > 1 and ln[1]["text"].startswith(n)), None)


def read_rate_tables(pdf: Path) -> dict:
    """Table 1 and Table 2 of one guide: {kind: {"grid", "page", "source", "disagree", "warnings"}}.

    Each table is read from the text layer when its unit header parses and it yields at least as
    many cells as OCR; otherwise (image pages, letter-spaced text) the three OCR variants vote."""
    out: dict[str, dict] = {}
    with pdfplumber.open(pdf) as doc:
        for pno, page in enumerate(doc.pages, start=1):
            head = " ".join((page.extract_text() or "").split()[:40]).lower()
            if not (re.search(r"table\s*[12]\.?\s*(seeding|chemical)", head)
                    or re.search(r"t\s*a\s*b\s*l\s*e\s*[12]", head[:40])):
                continue
            text = _read_page(text_words(page), page.height, ocr=False)
            ocr = ([_read_page(ocr_words(page, pdf.name, pno, v), page.height, ocr=True) for v in VARIANTS]
                   if image_only(page) or broken_text(page) else [])
            for kind in ("seed", "chem"):
                t_grid, t_ok, t_warn = text.get(kind, ({}, False, []))
                o_grids = [r[kind][0] for r in ocr if r.get(kind) and r[kind][0]]
                if t_grid and t_ok and _cells(t_grid) >= max(map(_cells, o_grids), default=0):
                    entry = {"grid": t_grid, "source": "text layer", "disagree": {}, "warnings": t_warn}
                elif o_grids:
                    grid, disagree = vote(o_grids)
                    warn = {w for r in ocr for w in r.get(kind, ({}, False, []))[2]
                            if "sub-columns not found" not in w}
                    entry = {"grid": grid, "source": f"OCR ({len(o_grids)}-variant vote)", "disagree": disagree,
                             "warnings": sorted(warn)}
                else:
                    continue
                if _cells(entry["grid"]) > _cells(out.get(kind, {}).get("grid", {})):
                    out[kind] = {**entry, "page": pno}
    return out


def vote(grids: list[dict]) -> tuple[dict, dict]:
    """Per-cell majority over OCR variants (first variant breaks ties); {cell: readings} where they differ."""
    grid, disagree = {}, {}
    for name in dict.fromkeys(n for g in grids for n in g):
        cells = dict.fromkeys(c for g in grids for c in g.get(name, {}))
        for cell in cells:
            reads = [g.get(name, {}).get(cell) for g in grids]
            seen = [r for r in reads if r is not None]
            best = Counter(seen).most_common()
            top = [v for v, n in best if n == best[0][1]]
            value = next(r for r in seen if r in top)
            grid.setdefault(name, {})[cell] = value
            if len(set(reads)) > 1:
                disagree[(name, cell)] = reads
    return grid, disagree


# ---------------------------------------------------------------------------
# Budget tables (Tables 3-8): seed and chemical $/ac per row x zone
# ---------------------------------------------------------------------------
def budget_values(raw_doc: dict, mvs: dict) -> dict[tuple[str, str], dict]:
    """{(row, zone): {"seed", "chem", "note"}}; OCR-repaired MVS values where the crop is mapped."""
    out = {}
    for table in raw_doc["budgets"]:
        if "error" in table:
            continue
        rows = {r["key"]: r["values"] for r in table["rows"] if r["key"]}
        for col in table["columns"]:
            names = [n for n in (col.get("crop_name_variants") or [col["crop_name"]]) if n]
            votes = Counter(match_rows([n], cutoff=0.75).get(n) for n in names)
            votes.pop(None, None)
            if not votes:
                continue
            name = votes.most_common(1)[0][0]
            if (name, table["zone"]) in out:
                continue
            i = col["index"]

            def v(field: str):
                vals = rows.get(field) or []
                return vals[i] if i < len(vals) and isinstance(vals[i], (int, float)) else None

            entry = {"seed": v("seed"), "chem": v("chemical"), "note": None, "ocr": table["ocr"]}
            rec = mvs.get((raw_doc["year"], table["zone"], resolve("AB", name)))
            if rec is not None:
                entry["seed"] = rec["seed_printed"]
                unread = any("chemical unread" in n for n in rec["notes"])
                entry["chem"] = None if unread else rec["chemical"]
                if unread:
                    entry["note"] = "budget chemical unreadable (OCR)"
            if entry["ocr"] and not entry["note"]:
                entry["note"] = f"budget table p.{table['page']} read by OCR"
            out[(name, table["zone"])] = entry
    return out


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------
def _cell(ws, row: int, col: int, value=None, *, fmt: str | None = None, font=NUM_FONT, fill=None,
          align: str = "right"):
    c = ws.cell(row, col, value)
    c.font, c.border, c.alignment = font, GRID, Alignment(horizontal=align, vertical="center")
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    return c


def _write_block(ws, top: int, year: int, pdf_name: str, kind: str, table: dict | None,
                 budget: dict) -> tuple[int, dict]:
    """One guide year; returns (next free row, stats)."""
    zones = TABLE_ZONE_ORDER
    comp = ("T2 Total", "Budget", "Δ") if kind == "chem" else ("Budget", "Δ")
    cmp0 = 2 + 18 + 1                       # comparison block starts after one spacer column
    flag_col = cmp0 + len(comp) * 6
    stats = {"rows": 0, "cells": 0, "flags": 0, "disagree": 0}
    title = ("Table 1. Seeding Rate by Alberta Soil Zones" if kind == "seed"
             else "Table 2. Chemical Application by Alberta Soil Zones ($/acre)")
    src = f"{table['source']}, p.{table['page']}" if table else "not found in the guide"
    ws.cell(top, 1, f"{year} — {title}").font = SECTION_FONT
    ws.cell(top, 8, f"{pdf_name}, {src}").font = NOTE_FONT
    for c in range(1, flag_col + 1):
        ws.cell(top, c).fill = SECTION_FILL
    h1, h2 = top + 1, top + 2
    _cell(ws, h1, 1, "Crop", font=HEADER_FONT, fill=HEADER_FILL, align="left")
    _cell(ws, h2, 1, None, font=HEADER_FONT, fill=HEADER_FILL)
    for z, zone in enumerate(zones):
        c0 = 2 + 3 * z
        ws.merge_cells(start_row=h1, start_column=c0, end_row=h1, end_column=c0 + 2)
        _cell(ws, h1, c0, SPEC.regions[zone], font=HEADER_FONT, fill=HEADER_FILL, align="center")
        for s, sub in enumerate(SUBS[kind]):
            _cell(ws, h2, c0 + s, sub, font=HEADER_FONT, fill=HEADER_FILL, align="center")
        k0 = cmp0 + len(comp) * z
        ws.merge_cells(start_row=h1, start_column=k0, end_row=h1, end_column=k0 + len(comp) - 1)
        _cell(ws, h1, k0, SPEC.regions[zone], font=HEADER_FONT, fill=HEADER_FILL, align="center")
        for s, sub in enumerate(comp):
            _cell(ws, h2, k0 + s, sub, font=HEADER_FONT, fill=HEADER_FILL, align="center")
    ws.merge_cells(start_row=h1, start_column=flag_col, end_row=h2, end_column=flag_col)
    _cell(ws, h1, flag_col, "Flag", font=HEADER_FONT, fill=HEADER_FILL, align="left")

    grid = table["grid"] if table else {}
    disagree = table["disagree"] if table else {}
    names = [n for n in ROWS if n in grid or any(k[0] == n for k in budget)]
    r = h2 + 1
    for name in names:
        cells = grid.get(name, {})
        _cell(ws, r, 1, name, font=TEXT_FONT, align="left")
        for z, zone in enumerate(zones):
            for s in range(3):
                v = cells.get((zone, s))
                fmt = CURRENCY if (kind == "chem" or s == 2) else "0.00"
                c = _cell(ws, r, 2 + 3 * z + s, v, fmt=fmt)
                if (name, (zone, s)) in disagree:
                    reads = ", ".join("—" if x is None else f"{x:g}" for x in disagree[(name, (zone, s))])
                    c.fill = DISAGREE_FILL
                    c.comment = Comment(f"OCR variants {'/'.join(VARIANTS)} read: {reads}", "export")
                    stats["disagree"] += 1
                stats["cells"] += v is not None
        flags = []
        for z, zone in enumerate(zones):
            b = budget.get((name, zone), {})
            bval = b.get("seed" if kind == "seed" else "chem")
            k0 = cmp0 + len(comp) * z
            src = [get_column_letter(2 + 3 * z + s) for s in range(3)]
            if kind == "seed":
                ref, tval = f"{src[2]}{r}", cells.get((zone, 2))
            else:
                present = [cells.get((zone, s)) for s in range(3) if cells.get((zone, s)) is not None]
                tval = round(sum(present), 2) if present else None
                ref = f"{get_column_letter(k0)}{r}"
                _cell(ws, r, k0, None if tval is None else f"=SUM({src[0]}{r}:{src[2]}{r})", fmt=CURRENCY,
                      fill=BASE_FILL)
            bcol = k0 + len(comp) - 2
            dcol = k0 + len(comp) - 1
            _cell(ws, r, bcol, bval, fmt=CURRENCY)
            if b.get("note"):
                ws.cell(r, bcol).comment = Comment(b["note"], "export")
            if bval is not None and tval is not None:
                delta = round(bval - tval, 2)
                d = _cell(ws, r, dcol, f"=ROUND({get_column_letter(bcol)}{r}-{ref},2)", fmt=CURRENCY)
                if abs(delta) > TOL[kind]:
                    d.fill, d.font = FLAG_FILL, RED_FONT
                    flags.append(f"{SPEC.regions[zone]} {delta:+.2f}{'*' if b.get('ocr') else ''}")
            else:
                _cell(ws, r, dcol, None, fmt=CURRENCY)
                if bval is not None and tval is None:
                    flags.append(f"{SPEC.regions[zone]}: no Table {1 if kind == 'seed' else 2} value")
        text = ("Δ " + "; ".join(flags)) if flags and "no Table" not in flags[0] else "; ".join(flags)
        key = resolve("AB", name)
        rule = next((o for o in overrides_for("AB", key, "seed") if o["use"] == "table1_cost"), None) if key else None
        if kind == "seed" and rule:
            text = (text + " — " if text else "") + f"engine uses Table 1 [{rule['id']}]"
        f = _cell(ws, r, flag_col, text or None, font=TEXT_FONT, align="left")
        if flags:
            f.fill = OVERRIDE_FILL if (kind == "seed" and rule) else FLAG_FILL
            stats["flags"] += 1
        stats["rows"] += 1
        r += 1
    for w in (table or {}).get("warnings", []):
        ws.cell(r, 1, f"parser: {w}").font = NOTE_FONT
        r += 1
    return r + 1, stats


def build(years: list[int], out: Path = OUT) -> tuple[Path, dict]:
    raw = json.loads((SPEC.intermediate_dir / f"{SPEC.code.lower()}_raw_tables.json").read_text(encoding="utf-8"))
    docs = {d["year"]: d for d in raw["documents"]}
    mvs_json = json.loads(SPEC.baseline_path("json").read_text(encoding="utf-8"))
    mvs = {(r["year"], r["zone"], r["crop_key"]): r for r in mvs_json["records"]}
    pdfs = source_pdfs(SPEC)

    wb = Workbook()
    sheets = {"seed": wb.active, "chem": wb.create_sheet()}
    sheets["seed"].title, sheets["chem"].title = "Table1_Seeding_Rates", "Table2_Chemical_Breakdown"
    subtitle = {
        "seed": "AgriProfit$ Cropping Alternatives, Table 1 as printed. Δ = budget-table seed $/ac − Table 1 $/ac "
                f"(flag above ${TOL['seed']:.3f}).",
        "chem": "AgriProfit$ Cropping Alternatives, Table 2 as printed. T2 Total = PreSeed + In Crop + F/I/PH/D; "
                f"Δ = budget-table chemical $/ac − T2 Total (flag above ${TOL['chem']:.2f}).",
    }
    legend = ("Shaded yellow: OCR variants disagree (comment lists the readings). Red Δ: outside tolerance. "
              "* budget table read by OCR; check the PDF before trusting a large Δ.")
    summary: dict[int, dict] = {}
    rows = {k: 4 for k in sheets}
    for kind, ws in sheets.items():
        ws["A1"] = ws.title.replace("_", " ")
        ws["A1"].font = TITLE_FONT
        ws["A2"], ws["A3"] = subtitle[kind], legend
        ws["A2"].font = ws["A3"].font = SUBTITLE_FONT
        rows[kind] = 5
    for year in sorted(years, reverse=True):
        tables = read_rate_tables(pdfs[year])
        budget = budget_values(docs[year], mvs) if year in docs else {}
        summary[year] = {}
        for kind, ws in sheets.items():
            rows[kind], stats = _write_block(ws, rows[kind], year, pdfs[year].name, kind,
                                              tables.get(kind), budget)
            summary[year][kind] = stats
    for ws in sheets.values():
        ws.column_dimensions["A"].width = 18
        for c in range(2, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(c)].width = 9.5
        ws.column_dimensions[get_column_letter(2 + 18)].width = 2.5
        ws.column_dimensions[get_column_letter(ws.max_column)].width = 48
        ws.freeze_panes = "B5"
        ws.sheet_view.zoomScale = 90
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    wb.close()
    return out, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="*", help=f"default: every guide year ({min(YEARS)}-{max(YEARS)})")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    years = sorted(args.years or source_pdfs(SPEC))
    path, summary = build(years, args.out)
    print(f"{'year':>6} | {'T1 rows':>7} {'cells':>5} {'flags':>5} {'OCR≠':>4} | {'T2 rows':>7} {'cells':>5} {'flags':>5} {'OCR≠':>4}")
    for y, s in sorted(summary.items(), reverse=True):
        a, b = s["seed"], s["chem"]
        print(f"{y:>6} | {a['rows']:>7} {a['cells']:>5} {a['flags']:>5} {a['disagree']:>4} | "
              f"{b['rows']:>7} {b['cells']:>5} {b['flags']:>5} {b['disagree']:>4}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
