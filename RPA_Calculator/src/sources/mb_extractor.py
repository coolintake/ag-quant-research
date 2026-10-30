"""Manitoba Agriculture "Crop Production Cost Guidelines" (COP workbooks, 2019-2026): pure extraction.

Reads the MB workbooks (docs/AGENT_RULES.json provinces.MB.source) with openpyxl and writes every
crop's raw cell values, with their sheet!cell addresses, to output/mb/intermediate/mb_raw_tables.json:

    Summary                     crops across (row 2, two panels C:J and M:T, or one run C:S
                                before 2024); per crop: Seed & Treatment, Fertilizer, Crop
                                Protection (2023+), Pesticide (2021-2022) or Herbicide +
                                Fungicide + Insecticide (2019-2020), Target Price, Premium, Target Yield, Unit type,
                                Gross Revenue, Total Operating
    Seed, Fertilizer & Chemical seed block (rate, unit, $/unit, $/ac), fertilizer price table
                                ($/tonne, the workbook's $/lb, N and S source usage), per-crop
                                N / P2O5 / K2O / S lb and $/ac, crop protection block (weed /
                                disease / insect / total $/ac; titled "Chemicals" before 2023)

Rows are found by their labels and crop rows by name, never by fixed address: the Summary's
column order and row positions shift between years (see --inspect). No crop mapping or rule is
applied here (src/normalize/mb_cop.py does that).

2019-2021 are legacy .xls files with Excel's default (no-password) protection, which openpyxl
cannot read: they are converted once by Excel itself (read-only, macros disabled, no password
supplied) to output/mb/intermediate/xlsx/ and read from there. .xlsm macros never run.

    python -m src.sources.mb_extractor [--years 2025 2026] [--inspect]
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from ..config import province, source_pdfs

SPEC = province("MB")
EXTRACTOR_VERSION = "1.0.0"
SUMMARY, SFC = "Summary", "Seed, Fertilizer & Chemical"
XLSX_CACHE = SPEC.intermediate_dir / "xlsx"

# Summary row label -> field (first match in the operating-cost / profitability block)
SUMMARY_ROWS = {
    "seed": r"^seed\s*&\s*treatment", "fertilizer": r"^fertilizer$", "crop_protection": r"^(crop\s+protection|pesticide)",
    "herbicide": r"^herbicide", "fungicide": r"^fungicide", "insecticide": r"^insecticide",
    "total_operating": r"^total\s+operating", "target_price": r"^target\s+price", "premium": r"^premium",
    "target_yield": r"^target\s+yield", "yield_unit": r"^unit\s+type", "gross_revenue": r"^gross\s+revenue",
}
FERT_PRODUCT_RE = re.compile(r"^(nitrogen|phosphorus|potash|sulphur|mes)", re.I)
ANALYSIS_RE = re.compile(r"(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?){2,3})")


# ---------------------------------------------------------------------------
# Workbook access
# ---------------------------------------------------------------------------
def readable_copy(path: Path) -> Path:
    """openpyxl-readable workbook: the file itself (.xlsx / .xlsm), or an Excel-converted
    .xlsx copy of a legacy .xls (cached; rebuilt when the source is newer)."""
    if path.suffix.lower() != ".xls":
        return path
    out = XLSX_CACHE / f"{path.stem}.xlsx"
    if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
        return out
    import win32com.client  # noqa: PLC0415  (Windows + Excel, as for the workbook verification)
    out.parent.mkdir(parents=True, exist_ok=True)
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible, xl.DisplayAlerts = False, False
    xl.AutomationSecurity = 3                       # msoAutomationSecurityForceDisable: no macros
    try:
        wb = xl.Workbooks.Open(str(path.resolve()), UpdateLinks=0, ReadOnly=True)
        if wb.HasPassword:
            wb.Close(False)
            raise RuntimeError(f"{path.name} is password-protected; open it in Excel and save a copy")
        wb.SaveAs(str(out.resolve()), FileFormat=51)   # xlOpenXMLWorkbook
        wb.Close(False)
    finally:
        xl.Quit()
    return out


def _grid(ws) -> dict[tuple[int, int], object]:
    return {(r, c): v for r, row in enumerate(ws.iter_rows(values_only=True), start=1)
            for c, v in enumerate(row, start=1) if v not in (None, "")}


def _addr(sheet: str, r: int, c: int) -> str:
    return f"{sheet}!{get_column_letter(c)}{r}"


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v) -> str | None:
    return " ".join(str(v).split()) if isinstance(v, str) and v.strip() else None


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def parse_summary(g: dict) -> dict:
    crops = {c: _text(v) for (r, c), v in g.items() if r == 2 and _text(v)}
    label_cols = sorted({c for (r, c), v in g.items() if isinstance(v, str) and re.match(SUMMARY_ROWS["seed"], v.strip(), re.I)})
    rows: dict[str, int] = {}
    for r in range(3, 46):
        for c in label_cols or [2]:
            label = _text(g.get((r, c)))
            for key, rx in SUMMARY_ROWS.items():
                if label and key not in rows and re.match(rx, label, re.I):
                    rows[key] = r
    out = {}
    for c, name in crops.items():
        cells = {k: {"value": g.get((r, c)), "cell": _addr(SUMMARY, r, c)} for k, r in rows.items()}
        out[name] = {"column": get_column_letter(c), **cells}
    return {"crops": out, "rows": rows, "label_columns": [get_column_letter(c) for c in label_cols]}


# ---------------------------------------------------------------------------
# Seed, Fertilizer & Chemical
# ---------------------------------------------------------------------------
def _section_rows(g: dict, pattern: str) -> list[int]:
    return sorted(r for (r, c), v in g.items() if c == 1 and isinstance(v, str) and re.match(pattern, v.strip(), re.I))


def _crop_rows(g: dict, start: int, stop: int) -> list[tuple[int, str]]:
    return [(r, _text(g[(r, 2)])) for r in range(start, stop) if _text(g.get((r, 2)))
            and not re.match(r"(?i)^(\*|note)", _text(g[(r, 2)]))]


def parse_sfc(g: dict) -> dict:
    fert_top = _section_rows(g, r"^fertilizer$")[0]
    chem_top = _section_rows(g, r"^(crop\s+protection|chemicals)$")[0]
    seed = {}
    for r, name in _crop_rows(g, 4, fert_top):
        if _num(g.get((r, 4))) is None:
            continue
        seed[name] = {"rate": _num(g.get((r, 4))), "rate_unit": _text(g.get((r, 5))),
                      "price": _num(g.get((r, 7))), "price_unit": _text(g.get((r, 8))),
                      "cost": _num(g.get((r, 10))), "cell": _addr(SFC, r, 10)}
    prices = {}
    for r in range(fert_top, fert_top + 12):
        label = _text(g.get((r, 2)))
        if label and FERT_PRODUCT_RE.match(label):
            m = ANALYSIS_RE.search(label)
            prices[label] = {"analysis": m.group(1) if m else None, "per_tonne": _num(g.get((r, 4))),
                             "per_lb": _num(g.get((r, 7))), "n_usage": _num(g.get((r, 10))),
                             "s_usage": _num(g.get((r, 13))), "cell": _addr(SFC, r, 4)}
    head = next(r for r in range(fert_top, chem_top)
                if _text(g.get((r, 3))) == "lbs" and _text(g.get((r, 4))) == "$/acre")
    fert = {}
    for r, name in _crop_rows(g, head + 1, chem_top):
        vals = {k: _num(g.get((r, c))) for k, c in (("n_lbs", 3), ("n_cost", 4), ("p_lbs", 6), ("p_cost", 7),
                                                    ("k_lbs", 9), ("k_cost", 10), ("s_lbs", 12), ("s_cost", 13),
                                                    ("total", 15))}
        if vals["total"] is not None:
            fert[name] = {**vals, "row": r, "cell": _addr(SFC, r, 15)}
    chem = {}
    for r, name in _crop_rows(g, chem_top + 1, chem_top + 30):
        vals = {k: _num(g.get((r, c))) for k, c in (("weed", 4), ("disease", 6), ("insect", 7), ("total", 10))}
        if vals["total"] is not None:
            chem[name] = {**vals, "cell": _addr(SFC, r, 10)}
    return {"seed": seed, "fert_prices": prices, "fert": fert, "chem": chem,
            "anchors": {"fertilizer": fert_top, "fert_crop_header": head, "chem": chem_top,
                        "chem_title": _text(g.get((chem_top, 1)))}}


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
def parse_workbook(path: Path, year: int) -> dict:
    src = readable_copy(path)
    wb = load_workbook(src, read_only=True, data_only=True)
    try:
        doc = {"year": year, "file": path.name, "read_from": src.name, "sheets": list(wb.sheetnames), "warnings": []}
        for sheet in (SUMMARY, SFC):
            if sheet not in wb.sheetnames:
                doc["warnings"].append(f"sheet {sheet!r} not found")
        doc["summary"] = parse_summary(_grid(wb[SUMMARY]))
        doc["sfc"] = parse_sfc(_grid(wb[SFC]))
    finally:
        wb.close()
    for k in ("seed", "target_yield", "target_price"):
        if k not in doc["summary"]["rows"]:
            doc["warnings"].append(f"Summary row {k!r} not found")
    if not (set(doc["summary"]["rows"]) & {"crop_protection", "herbicide"}):
        doc["warnings"].append("Summary chemical rows not found")
    return doc


def inspect(docs: list[dict]) -> str:
    """Layout report: tabs, crop columns and anchor rows per year (discovery pass)."""
    lines = ["# MB COP workbook layout (discovery)", ""]
    for d in docs:
        s, f = d["summary"], d["sfc"]
        lines += [f"## {d['year']}  {d['file']}" + (f"  (read from {d['read_from']})" if d["read_from"] != d["file"] else ""),
                  f"- tabs: {', '.join(d['sheets'])}",
                  f"- Summary crop columns: " + ", ".join(f"{v['column']}={k}" for k, v in s["crops"].items()),
                  f"- Summary label rows: " + ", ".join(f"{k}={r}" for k, r in s["rows"].items())
                  + f" (labels in column {'/'.join(s['label_columns'])})",
                  f"- {SFC}: seed rows 4-{f['anchors']['fertilizer'] - 1} (D rate, E unit, G $/unit, J $/ac); "
                  f"fertilizer prices from row {f['anchors']['fertilizer'] + 3} (D $/t, G $/lb, J N usage, M S usage); "
                  f"per-crop fertilizer from row {f['anchors']['fert_crop_header'] + 1} (C/D N, F/G P2O5, I/J K2O, L/M S, O total); "
                  f"'{f['anchors']['chem_title']}' block row {f['anchors']['chem']} (D weed, F disease, G insect, J total)",
                  f"- N sources: " + ", ".join(f"{k.split(':')[1].strip() if ':' in k else k} usage {v['n_usage']}"
                                               for k, v in f["fert_prices"].items() if v["n_usage"] is not None),
                  *(f"- WARNING: {w}" for w in d["warnings"]), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument("--inspect", action="store_true", help="print the layout report")
    args = ap.parse_args(argv)
    files = source_pdfs(SPEC)
    years = sorted(y for y in files if not args.years or y in args.years)
    if not years:
        print(f"No MB workbooks in {SPEC.input_dir}")
        return 1
    docs = []
    for y in years:
        doc = parse_workbook(files[y], y)
        docs.append(doc)
        print(f"  {y}  {doc['file']:<32} crops={len(doc['summary']['crops']):2}  "
              f"seed={len(doc['sfc']['seed']):2} fert={len(doc['sfc']['fert']):2} chem={len(doc['sfc']['chem']):2}"
              + (f"  warnings: {doc['warnings']}" if doc["warnings"] else ""))
    report = inspect(docs)
    SPEC.intermediate_dir.mkdir(parents=True, exist_ok=True)
    (SPEC.intermediate_dir / "mb_layout_report.md").write_text(report, encoding="utf-8")
    meta = {"extractor_version": EXTRACTOR_VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_dir": str(SPEC.input_dir), "years": years}
    SPEC.raw_json.write_text(json.dumps({"meta": meta, "documents": docs}, indent=1, ensure_ascii=False, default=str),
                             encoding="utf-8")
    if args.inspect:
        print(report)
    print(f"  wrote {SPEC.raw_json}\n  wrote {SPEC.intermediate_dir / 'mb_layout_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
