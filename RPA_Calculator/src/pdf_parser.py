"""Phase 1.2 - raw table extraction from the Saskatchewan Crop Planning Guide PDFs.

Reads every RPA-SK-<year>.pdf detected by config.verify_environment() and writes the
raw table structures to output/raw_extracted_tables.json. Nothing is normalized or
mapped to MVS commodities here; cells keep their printed strings (a parsed float is
carried alongside for convenience). Normalization is Phase 1.3.

Two source layouts:
    zone_per_page  (2019)       one budget page per soil zone, crops as columns;
                                some pages are two-panel spreads and carry a
                                seeding-rate / fertilizer side list on the left.
    crop_per_page  (2020-2026)  one page per crop, soil zones as columns in the
                                left "Economics" panel, prose in the right
                                "Agronomics" panel.

Rows are rebuilt from word coordinates rather than pdfplumber's ruled-table finder,
which drops the label on every shaded row of these budgets.

Usage:
    python src/pdf_parser.py                 # all detected years
    python src/pdf_parser.py --years 2019 2024
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from config import INPUT_PDF_DIR, RAW_TABLES_JSON, verify_environment

PARSER_VERSION = "1.2.0"

LINE_TOLERANCE = 3.0        # pt; words whose tops differ by less share a line
SPLIT_ROW_GAP = 12.0        # pt; max gap when re-joining a label split from its values
COLUMN_SPAN_PAD = 8.0       # pt; slack when matching header words to a value column
PANEL_GUTTER = 4.0          # pt; gap left of a panel's first label
MARGIN_GAP = 8.0          # pt; words this far right of the last column are margin notes

NUM_RE = re.compile(
    r"^[-−–]?\$?(?:\(\d[\d,]*(?:\.\d+)?\)|\d[\d,]*(?:\.\d+)?)%?\**$"
)
YEAR_TITLE_RE = re.compile(r"^(20\d{2})\s+(.+)$")
ZONE_TITLE_RE = re.compile(r"(DARK BROWN|BROWN|BLACK) SOIL ZONE", re.I)
SECTION_RE = re.compile(
    r"^(revenues? per acre|expenses per acre|(variable|other) expenses/acre|"
    r"returns? per acre|break[- ]even (yield|price)|yield sensitivity)",
    re.I,
)
HEADER_LABELS = {"", "crop", "crops", "economics", "economicsmyfarm", "myfarm", "revenueperacre", "revenuesperacre"}
HEADER_NOISE = {"crop", "crops"}   # centred over the whole column block, not one column
PAGE_NUMBER_BAND = 50.0            # pt from the bottom edge


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------
def is_number(text: str) -> bool:
    return bool(NUM_RE.match(text))


def to_number(text: str) -> float | None:
    if not is_number(text):
        return None
    s = text.replace("−", "-").replace("–", "-")
    s = s.replace("$", "").replace(",", "").replace("%", "").rstrip("*")
    negative = s.startswith("-") or s.startswith("(")
    s = s.strip("-()")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _center(word: dict) -> float:
    return (word["x0"] + word["x1"]) / 2


def _join(words: list[dict]) -> str:
    return " ".join(w["text"] for w in sorted(words, key=lambda w: w["x0"])).strip()


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def page_words(page) -> tuple[list[dict], int]:
    """Words inside the visible page box. Some 2019 pages also carry the previous
    page's content at negative x (off the crop box); that is dropped here."""
    words = page.extract_words()
    inside = [w for w in words
              if w["x0"] >= 0 and w["x1"] <= page.width and w["top"] >= 0 and w["bottom"] <= page.height]
    return inside, len(words) - len(inside)


def title_cutoff(words: list[dict]) -> float:
    """y below which a word is past the page's first visual line (the page title).

    Uses the title line's top, not its bottom: the large title font overlaps the
    small "Dark" of the "Dark Brown" column header on several pages.
    """
    lines = group_lines(words)
    return lines[0][0]["top"] + LINE_TOLERANCE if lines else 0.0


def group_lines(words: list[dict]) -> list[list[dict]]:
    """Cluster words into visual lines by their top coordinate."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= LINE_TOLERANCE:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def split_label_values(line: list[dict]) -> tuple[list[dict], list[dict]]:
    """Label = leading words; values = the trailing run of numeric tokens."""
    i = len(line)
    while i > 0 and is_number(line[i - 1]["text"]):
        i -= 1
    return line[:i], line[i:]


# ---------------------------------------------------------------------------
# Panel -> table
# ---------------------------------------------------------------------------
def build_table(words: list[dict], table_id: str, bbox: tuple, min_y: float = 0.0,
                page_height: float = 1e9, text_cells: bool = True) -> dict:
    """Turn the words of one budget panel into a table of labelled rows.

    min_y drops the page title; single numbers in the bottom band are page numbers.
    text_cells lets non-numeric rows carry per-column text (2019 attribute rows).
    """
    lines = [ln for ln in group_lines([w for w in words if w["top"] > min_y])
             if not (len(ln) == 1 and is_number(ln[0]["text"])
                     and ln[0]["top"] > page_height - PAGE_NUMBER_BAND)]
    parsed = [(line, *split_label_values(line)) for line in lines]
    warnings: list[str] = []

    # Value columns from the rows carrying the most numbers.
    counts = [len(v) for _, _, v in parsed if v]
    if not counts:
        return {"table_id": table_id, "kind": "economics", "bbox": bbox, "columns": [],
                "rows": [], "warnings": ["no numeric rows"]}
    n_cols = max(counts)
    ref = [v for _, _, v in parsed if len(v) == n_cols]
    centers = [statistics.median(_center(v[i]) for v in ref) for i in range(n_cols)]
    # Spans only from values near each column's median, so a stray number in the
    # label area cannot stretch a column leftwards.
    spans = []
    for i, c in enumerate(centers):
        members = [v[i] for v in ref if abs(_center(v[i]) - c) <= 25] or [v[i] for v in ref]
        spans.append((min(w["x0"] for w in members), max(w["x1"] for w in members)))
    region_x0 = spans[0][0] - COLUMN_SPAN_PAD * 2

    # Words right of the last column are margin notes (2019 footnotes share lines
    # with the last budget rows); split them off and re-parse what is left.
    right_edge = spans[-1][1] + MARGIN_GAP
    margin = [w for line, _, _ in parsed for w in line if w["x0"] > right_edge]
    parsed = [(kept, *split_label_values(kept)) for kept in
              ([w for w in line if w["x0"] <= right_edge] for line, _, _ in parsed) if kept]

    # A number sitting in the label area (footnote marker, stray digit) is label text.
    for k, (line, label_w, value_w) in enumerate(parsed):
        stray = [w for w in value_w if _center(w) < region_x0]
        if stray:
            parsed[k] = (line, label_w + stray, [w for w in value_w if w not in stray])

    def column_of(word: dict, strict: bool) -> int | None:
        c = _center(word)
        if strict:
            for i, (a, b) in enumerate(spans):
                if a - COLUMN_SPAN_PAD <= c <= b + COLUMN_SPAN_PAD:
                    return i
            return None
        return min(range(n_cols), key=lambda i: abs(centers[i] - c))

    first_numeric_y = next(line[0]["top"] for line, _, v in parsed if v)
    header_words: list[list[dict]] = [[] for _ in range(n_cols)]
    orphan_headers: list[str] = []
    rows: list[dict] = []
    started = False

    for line, label_w, value_w in parsed:
        y = round(line[0]["top"], 1)
        if value_w:
            started = True
            cells: dict[int, str] = {}
            if len(value_w) == n_cols:
                cells = {i: w["text"] for i, w in enumerate(value_w)}
            else:
                for w in value_w:
                    i = column_of(w, strict=False)
                    if i in cells:
                        warnings.append(f"y={y}: two values in column {i} ({cells[i]}, {w['text']})")
                        cells[i] += " " + w["text"]
                    else:
                        cells[i] = w["text"]
            rows.append({"y": y, "label": _join(label_w), "cells": cells, "text_row": False})
            continue

        # Text-only line above the first numbers: possibly a column-header line.
        # "My Farm" is the guide's blank fill-in column; it never carries values.
        if line[0]["top"] < first_numeric_y:
            words_h = [w for w in line if w["text"].lower() not in HEADER_NOISE]
            for a, b in zip(line, line[1:]):
                if a["text"] == "My" and b["text"] == "Farm":
                    words_h = [w for w in words_h if w is not a and w is not b]
                    orphan_headers.append("My Farm")
            in_col = [w for w in words_h if column_of(w, strict=True) is not None]
            rest = _join([w for w in words_h if w not in in_col])
            if _norm(rest) in HEADER_LABELS:
                started = True
                for w in in_col:
                    header_words[column_of(w, strict=True)].append(w)
                if SECTION_RE.match(rest):
                    rows.append({"y": y, "label": rest, "cells": {}, "text_row": False})
                continue

        # Other text-only line. With text_cells (2019 attribute rows such as
        # "Soil Zone Brown DkBrown ..."), words over the value columns are cells.
        if not started:
            continue  # page title / preamble above the table
        if rows and all(_center(w) >= region_x0 for w in line):
            margin.extend(line)  # wrapped footnote text over the columns, no row label
            continue
        cells = {}
        if text_cells:
            for w in line:
                if _center(w) >= region_x0:
                    i = column_of(w, strict=False)
                    cells[i] = (cells.get(i, "") + " " + w["text"]).strip()
        label = _join([w for w in line if _center(w) < region_x0]) if cells else _join(line)
        rows.append({"y": y, "label": label, "cells": cells, "text_row": bool(cells)})

    rows = _merge_split_rows(rows, warnings)

    # Footnotes: from the first "*..." line, anything after the last numeric row,
    # and margin notes.
    star = next((k for k, r in enumerate(rows) if r["label"].startswith("*")), len(rows))
    rows_before = rows[:star]
    last = max((k for k, r in enumerate(rows_before) if r["cells"] and not r["text_row"]),
               default=len(rows_before) - 1)
    footnotes = [" ".join([r["label"], *r["cells"].values()]).strip() for r in rows[last + 1:]]
    footnotes += [_join(ln) for ln in group_lines(margin)]
    rows = rows[:last + 1]

    columns = []
    for i, ws in enumerate(header_words):
        ws = sorted(ws, key=lambda w: (round(w["top"]), w["x0"]))
        name = " ".join(w["text"] for w in ws).strip() or f"col{i + 1}"
        columns.append(name)

    section = None
    out_rows = []
    for r in rows:
        is_section = not r["cells"] and bool(SECTION_RE.match(r["label"]))
        if is_section:
            section = r["label"]
        cells = {columns[i]: v for i, v in sorted(r["cells"].items())}
        out_rows.append({
            "y": r["y"],
            "section": section,
            "label": r["label"],
            "row_type": "section" if is_section else
                        "text" if r["text_row"] else
                        "values" if cells else "label_only",
            "cells": cells,
            "numbers": {} if r["text_row"] else
                       {k: to_number(v) for k, v in cells.items() if to_number(v) is not None},
            **({"flags": r["flags"]} if r.get("flags") else {}),
        })

    table = {
        "table_id": table_id,
        "kind": "economics",
        "bbox": [round(v, 1) for v in bbox],
        "columns": columns,
        "column_centers": [round(c, 1) for c in centers],
        "rows": out_rows,
    }
    if orphan_headers:
        table["headers_without_values"] = list(dict.fromkeys(orphan_headers))
    if footnotes:
        table["footnotes"] = footnotes
    table["checks"] = arithmetic_checks(table)
    if warnings:
        table["warnings"] = warnings
    return table


def _merge_split_rows(rows: list[dict], warnings: list[str]) -> list[dict]:
    """Re-join a numbers-only line with a label-only line printed just above/below it."""
    out: list[dict] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r["cells"] and not r["text_row"] and not r["label"]:
            for j in (i + 1, i - 1):
                if not 0 <= j < len(rows):
                    continue
                n = rows[j]
                if (n["label"] and not n["cells"] and abs(n["y"] - r["y"]) <= SPLIT_ROW_GAP
                        and not SECTION_RE.match(n["label"])):
                    merged = {**r, "label": n["label"], "flags": ["merged_split_row"]}
                    if j == i + 1:
                        out.append(merged)
                        i += 2
                    else:
                        out[-1] = merged
                        i += 1
                    break
            else:
                warnings.append(f"y={r['y']}: values without a label")
                out.append(r)
                i += 1
            continue
        out.append(r)
        i += 1
    return out


# ---------------------------------------------------------------------------
# Arithmetic cross-checks (extraction QA, not validation of the guide itself)
# ---------------------------------------------------------------------------
def _find(rows: list[dict], pattern: str) -> int | None:
    rx = re.compile(pattern, re.I)
    return next((i for i, r in enumerate(rows) if rx.search(r["label"])), None)


def arithmetic_checks(table: dict) -> list[dict]:
    rows = table["rows"]
    checks: list[dict] = []

    def add(name: str, col: str, expected: float, reported: float, tol: float) -> None:
        diff = round(reported - expected, 4)
        checks.append({"check": name, "column": col, "expected": round(expected, 4),
                       "reported": reported, "diff": diff, "ok": abs(diff) <= tol})

    i_var = _find(rows, r"^variable expenses/acre")
    i_d = _find(rows, r"^total variable expenses")
    i_oth = _find(rows, r"^other expenses/acre")
    i_e = _find(rows, r"^total other expenses")
    i_g = _find(rows, r"^total expenses")
    i_a = _find(rows, r"\(A\)")
    i_b = _find(rows, r"\(B\)")
    i_c = _find(rows, r"\(C\)")
    i_rv = _find(rows, r"^return over variable")
    i_rt = _find(rows, r"^return over total")

    def val(i: int | None, col: str) -> float | None:
        return None if i is None else rows[i]["numbers"].get(col)

    for col in table["columns"]:
        if i_var is not None and i_d is not None and val(i_d, col) is not None:
            items = [r["numbers"].get(col) for r in rows[i_var + 1:i_d]]
            items = [v for v in items if v is not None]
            add("sum_variable_expenses=D", col, sum(items), val(i_d, col), 0.1)
        if i_oth is not None and i_e is not None and val(i_e, col) is not None:
            items = [r["numbers"].get(col) for r in rows[i_oth + 1:i_e]]
            items = [v for v in items if v is not None]
            add("sum_other_expenses=E", col, sum(items), val(i_e, col), 0.1)
        d, e, g = val(i_d, col), val(i_e, col), val(i_g, col)
        if None not in (d, e, g):
            add("D+E=G", col, d + e, g, 0.02)
        a, b, c = val(i_a, col), val(i_b, col), val(i_c, col)
        if None not in (a, b, c):
            # A and B are printed rounded (B to the cent), so allow for that.
            add("AxB=C", col, a * b, c, 0.005 * abs(a) + 0.05 * abs(b) + 0.02)
        rv, rt = val(i_rv, col), val(i_rt, col)
        if None not in (c, d, rv):
            add("C-D=ROVE", col, c - d, rv, 0.02)
        if None not in (c, g, rt):
            add("C-G=ROTE", col, c - g, rt, 0.02)
    return checks


# ---------------------------------------------------------------------------
# Page handlers
# ---------------------------------------------------------------------------
def _page_lines_text(page) -> list[str]:
    return [ln for ln in (page.extract_text() or "").splitlines() if ln.strip()]


def side_list(words: list[dict], table_id: str, bbox: tuple) -> dict:
    """Raw line dump of a 2019 seeding-rate / fertilizer side list."""
    rows = []
    for line in group_lines(words):
        rows.append({"y": round(line[0]["top"], 1), "text": _join(line),
                     "tokens": [w["text"] for w in line]})
    return {"table_id": table_id, "kind": "side_list", "bbox": [round(v, 1) for v in bbox],
            "rows": rows}


def parse_crop_page(page, pno: int, year: int, title: str) -> dict:
    """2020+ layout: one crop, zones as columns, prose on the right."""
    words, dropped = page_words(page)
    agro = [w for w in words if w["text"].lower() == "agronomics" and w["top"] < 120]
    record = {"page": pno, "page_type": "crop_budget", "title": title,
              "crop": title.rstrip("*").strip(), "zone": None, "tables": []}
    if agro:
        split_x = min(w["x0"] for w in agro) - 2
    else:
        split_x = page.width * 0.55
        record.setdefault("warnings", []).append("Agronomics header not found; split at 55% width")
    econ = [w for w in words if w["x1"] <= split_x + 1]
    table = build_table(econ, f"{year}_p{pno}_econ", (0, 0, split_x, page.height),
                        min_y=title_cutoff(words), page_height=page.height, text_cells=False)
    record["tables"].append(table)
    record["agronomics_text"] = (page.crop((split_x, 0, page.width, page.height))
                                 .extract_text() or "").strip()
    if dropped:
        record["offpage_words_dropped"] = dropped
    return record


def parse_zone_page(page, pno: int, year: int, title: str) -> dict:
    """2019 layout: budget panel(s) with crops as columns, optional side list."""
    words, dropped = page_words(page)
    zone_match = ZONE_TITLE_RE.search(title)
    zone = zone_match.group(1).title() if zone_match else None
    record = {"page": pno, "page_type": "zone_budget", "title": title,
              "crop": None, "zone": zone if zone else ("Specialty" if "SPECIALTY" in title.upper() else None),
              "tables": []}
    if dropped:
        record["offpage_words_dropped"] = dropped

    # Each budget panel starts at a "REVENUE(S) PER ACRE" label.
    starts: list[float] = []
    by_line = group_lines(words)
    for line in by_line:
        for a, b in zip(line, line[1:]):
            if a["text"].upper() in ("REVENUE", "REVENUES") and b["text"].upper() == "PER":
                if not any(abs(a["x0"] - s) < 20 for s in starts):
                    starts.append(a["x0"])
    starts.sort()
    if not starts:
        record["page_type"] = "text"
        record["text"] = page.extract_text() or ""
        return record

    # Labels can start a little left of the REVENUE anchor, so a panel only begins
    # at the anchor when a real side list (seeding / fertilizer) sits to its left.
    top = title_cutoff(words)
    bounds = [s - PANEL_GUTTER for s in starts] + [page.width + 1]
    side = [w for w in words if w["x1"] <= bounds[0] and w["top"] > top]
    if len(side) > 20:
        record["tables"].append(side_list(side, f"{year}_p{pno}_side", (0, top, bounds[0], page.height)))
    else:
        bounds[0] = 0.0
    for k in range(len(starts)):
        x0, x1 = bounds[k], bounds[k + 1]
        panel = [w for w in words if x0 <= w["x0"] < x1]
        record["tables"].append(build_table(panel, f"{year}_p{pno}_panel{k + 1}", (x0, 0, x1, page.height),
                                            min_y=top, page_height=page.height))
    return record


def parse_pdf(path: Path, year: int) -> dict:
    doc = {"year": year, "file": path.name, "pages": []}
    with pdfplumber.open(path) as pdf:
        doc["page_count"] = len(pdf.pages)
        layout = None
        for pno, page in enumerate(pdf.pages, start=1):
            lines = _page_lines_text(page)
            first = lines[0].strip() if lines else ""
            if len(" ".join(lines)) < 10:
                doc["pages"].append({"page": pno, "page_type": "blank"})
                continue
            m = YEAR_TITLE_RE.match(first)
            head = " ".join(lines[:6]).lower()
            if m and int(m.group(1)) == year and re.search(r"\bagronomics\b|\be ?conomics\b", head):
                layout = "crop_per_page"
                doc["pages"].append(parse_crop_page(page, pno, year, m.group(2).strip()))
            elif re.search(r"SOIL ZONE \d{4}|SPECIALTY CROPS \d{4}", " ".join(lines[:2]), re.I):
                layout = layout or "zone_per_page"
                doc["pages"].append(parse_zone_page(page, pno, year, first))
            else:
                doc["pages"].append({"page": pno, "page_type": "text", "title": first,
                                     "text": page.extract_text() or ""})
    doc["layout"] = layout
    doc["edition_conflicts"] = find_edition_conflicts(doc)
    return doc


def find_edition_conflicts(doc: dict) -> list[dict]:
    """Same zone + same crop columns printed twice (2019 spreads) with different numbers."""
    seen: dict[tuple, dict] = {}
    conflicts = []
    for page in doc["pages"]:
        for t in page.get("tables", []):
            if t["kind"] != "economics" or not t["columns"]:
                continue
            key = (page.get("zone"), page.get("crop"), tuple(t["columns"]))
            if key not in seen:
                seen[key] = t
                continue
            first = seen[key]
            a = {r["label"]: r["numbers"] for r in first["rows"] if r["numbers"]}
            b = {r["label"]: r["numbers"] for r in t["rows"] if r["numbers"]}
            diffs = [{"label": lbl, "column": c, first["table_id"]: a[lbl].get(c), t["table_id"]: b[lbl].get(c)}
                     for lbl in a.keys() & b.keys() for c in t["columns"]
                     if a[lbl].get(c) != b[lbl].get(c)]
            conflicts.append({"zone": key[0], "crop": key[1], "columns": list(key[2]),
                              "tables": [first["table_id"], t["table_id"]],
                              "labels_only_in_first": sorted(a.keys() - b.keys()),
                              "labels_only_in_second": sorted(b.keys() - a.keys()),
                              "differing_cells": len(diffs), "diffs": diffs})
    return conflicts


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def summarize(doc: dict) -> str:
    tables = [t for p in doc["pages"] for t in p.get("tables", []) if t["kind"] == "economics"]
    rows = sum(len(t["rows"]) for t in tables)
    checks = [c for t in tables for c in t.get("checks", [])]
    failed = [c for c in checks if not c["ok"]]
    warns = sum(len(t.get("warnings", [])) for t in tables)
    return (f"  {doc['year']}  {doc['layout'] or '?':<14} pages={doc['page_count']:<3} "
            f"econ_tables={len(tables):<3} rows={rows:<5} checks={len(checks) - len(failed)}/{len(checks)} ok"
            f"  warnings={warns}  edition_conflicts={len(doc['edition_conflicts'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="*", help="limit to these years")
    ap.add_argument("--out", type=Path, default=RAW_TABLES_JSON)
    args = ap.parse_args(argv)

    pdfs = verify_environment(INPUT_PDF_DIR)
    if args.years:
        pdfs = {y: p for y, p in pdfs.items() if y in args.years}
    if not pdfs:
        print("No PDFs to parse.")
        return 1

    print("\nParsing:")
    documents = []
    for year, path in sorted(pdfs.items()):
        doc = parse_pdf(path, year)
        documents.append(doc)
        print(summarize(doc))

    payload = {
        "meta": {
            "parser_version": PARSER_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_dir": str(INPUT_PDF_DIR),
            "years": [d["year"] for d in documents],
            "notes": "Raw strings as printed; 'numbers' holds parsed floats. "
                     "'checks' are arithmetic cross-checks of the extraction.",
        },
        "documents": documents,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
