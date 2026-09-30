"""Alberta AgriProfit$ "Cropping Alternatives" parser (2019-2026).

Reads RPA_AB_Input/RPA-AB-<year>.pdf and extracts, per year x soil zone x crop:

    budget (Tables 3-8)  expected yield and market price, crop sales, seed, fertilizer $
                         and its NPKS blend (lb N-P2O5-K2O-S /ac), chemical, total direct
                         expense, contribution margin, break-even yield
    Table 1              seeding rate (bu/ac or lb/ac) and seed $/ac by zone
    Table 2              chemical $/ac by zone (pre-seed + in-crop + F/I/PH/D)
    methodology          published fertilizer prices ($/lb N, P, K, S and $/tonne quotes)

Every budget column is cross-checked against the page's own arithmetic and the side
tables: sales = yield x price, line items sum to total direct expense, fertilizer $ =
NPKS x published $/lb, seed $ = Table 1, chemical $ = Table 2.

2019's budget tables are embedded images. They are read with the Windows OCR engine
(Windows.Media.Ocr via PowerShell, cached under output/ab/intermediate/ocr/); OCR'd
values that fail a cross-check are corrected only from an independent source (Table 1
seed, Table 2 chemical, NPKS x price) and flagged, otherwise left flagged, never guessed.

Outputs
    output/ab/intermediate/ab_raw_tables.json          every column of every table
    output/ab/mvs/ab_historical_cop_2019_2026.{json,csv}  mapped crops (config.AB_CROPS)

Usage (from RPA_Calculator/):
    python -m src.pdf_parser_ab
    python -m src.pdf_parser_ab --years 2025 2026
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # flat imports under `python -m src.…`

import pdfplumber  # noqa: E402

from config import (AB_CROPS, AB_INPUT_PDF_DIR, AB_INTERMEDIATE_DIR, AB_OCR_CACHE_DIR,  # noqa: E402
                    AB_OUTPUT_CSV, AB_OUTPUT_JSON, AB_RAW_JSON, AB_ZONES, PDF_YEAR_PATTERN, YEARS)
from pdf_parser import group_lines, to_number  # noqa: E402

PARSER_VERSION = "1.0.0"
OCR_DPI = 400
TABLE_ZONE_ORDER = ["black", "brown", "dark_brown", "grey_wooded", "peace", "irrigated"]   # Tables 1 & 2

NUMBER_RE = re.compile(r"^\(?-?\$?\d[\d,]*(?:\.\d+)?\)?$")
NPKS_RE = re.compile(r"^\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?){3}$")
FERT_PRICE_RE = re.compile(
    r"(Nitrogen|Phosphorous|Phosphorus|Potassium|Sulphur|Sulfur)\s*\$\s*([\d.]+)\s*/\s*lb\s+actual\s+[NPKS]"
    r"\s*\(\s*\$\s*([\d,.]+)\s*/\s*tonne\s+for\s+([\d.\-]+)\s*\)", re.I)
NUTRIENT_KEY = {"nitrogen": "N", "phosphorous": "P", "phosphorus": "P", "potassium": "K",
                "sulphur": "S", "sulfur": "S"}

# Budget row label -> field key (lower-cased label; tolerant of OCR slips).
ROW_KEYS = [
    ("yield", r"y\S{0,2}ld\s*per|^ex\S*\s*y\S{0,2}ld"), ("price", r"market|price|ma\S*t\s*\S*ce"),
    ("sales", r"crop\s*sales|sales"), ("seed", r"^seed|cleaning|treatment"), ("npks", r"npks|blend"),
    ("fertilizer", r"^fe\S{2,8}er"), ("chemical", r"^ch\s*emical|^chemi"),
    ("hail", r"hail|ha\S*\s*crop|insurance"), ("trucking", r"trucking|marketing"), ("fuel", r"fuel|oil\s*&|lube"),
    ("machinery", r"machinery"), ("building", r"building|bldg"), ("pumping", r"pumping"), ("custom", r"custom"),
    ("labour", r"labou?r|unpaid"), ("utilities", r"utilities|miscellaneous"), ("interest", r"interest"),
    ("total_direct", r"total\s*\S*\s*expense"), ("contribution", r"contribution|margin"),
    ("cost_per_unit", r"total\s*cost|per\s*unit"), ("break_even", r"break"),
]
# The budgets always print their rows in this order; used to key OCR rows whose
# label could not be read.
ROW_ORDER = ["yield", "price", "sales", "seed", "fertilizer", "npks", "chemical", "hail", "trucking", "fuel",
             "machinery", "building", "pumping", "custom", "labour", "utilities", "interest", "total_direct",
             "contribution", "cost_per_unit", "break_even"]
DIRECT_ITEMS = ["seed", "fertilizer", "chemical", "hail", "trucking", "fuel", "machinery", "building",
                "pumping", "custom", "labour", "utilities", "interest"]
CROP_WORDS = {"spring", "wheat", "cps", "durum", "soft", "white", "winter", "feed", "malt", "barley", "milling",
              "oats", "argentine", "ht", "polish", "canola", "field", "yellow", "peas", "pea", "red", "green",
              "lentil", "lentils", "kabuli", "desi", "chickpea", "chickpeas", "mustard", "mixed", "alfalfa", "hay",
              "cereal", "silage", "flax", "dry", "beans", "bean", "triticale", "timothy", "hybrid", "fall", "rye"}
LB_SEEDED = {"canola", "lentils_red", "chickpeas_kabuli"}   # Table 1 rate is lb/ac for these
SEED_FROM_TABLE1 = {"lentils_red"}   # budget seed $ known to be wrong; Table 1 is authoritative


# ---------------------------------------------------------------------------
# Words (text layer, wide-spaced text, or Windows OCR)
# ---------------------------------------------------------------------------
def crop_key(text: str) -> str | None:
    t = text.lower().replace("*", "").strip()
    for key, spec in AB_CROPS.items():
        if any(re.search(p, t) for p in spec["patterns"]):
            return key
    return None


def text_words(page) -> list[dict]:
    return page.extract_words()


def image_only(page) -> bool:
    """Table printed as an embedded image with no usable text layer (2019)."""
    big = [im for im in page.images if im["width"] * im["height"] > 0.3 * page.width * page.height]
    return bool(big) and len(page.chars) < 400


def broken_text(page) -> bool:
    """Letter-spaced text layer (2024-25 charts pasted as text): words come out as
    single letters and some columns collapse; the vector render is clean, so OCR it."""
    words = page.extract_words()
    alpha = [w for w in words if re.search(r"[A-Za-z]", w["text"])]
    return bool(alpha) and sum(len(w["text"]) <= 2 for w in alpha) / len(alpha) > 0.5


OCR_SCRIPT = r"""
param([string]$ImagePath, [string]$OutJson)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$words = foreach ($line in $result.Lines) { foreach ($w in $line.Words) {
  [pscustomobject]@{ text = $w.Text; x = $w.BoundingRect.X; y = $w.BoundingRect.Y; w = $w.BoundingRect.Width; h = $w.BoundingRect.Height } } }
[pscustomobject]@{ width = $bitmap.PixelWidth; height = $bitmap.PixelHeight; words = @($words) } | ConvertTo-Json -Depth 4 -Compress | Out-File -Encoding utf8 $OutJson
$stream.Dispose()
"""


def _variant_image(page, bbox: tuple, variant: str):
    """Rendered page region prepared for OCR."""
    from PIL import Image, ImageFilter  # noqa: PLC0415  (Pillow ships with pdfplumber)
    if variant == "render":
        return page.crop(bbox).to_image(resolution=OCR_DPI).original
    base = page.crop(bbox).to_image(resolution=150).original.convert("L")
    up = base.resize((base.width * 3, base.height * 3), Image.LANCZOS)
    sharp = up.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return sharp if variant == "sharp3" else sharp.point(lambda v: 255 if v > 160 else 0)


def ocr_words(page, pdf_name: str, pno: int, variant: str = "render") -> list[dict]:
    """Windows OCR of the page's table (its image, or the whole page for vector pages),
    cached per variant; words in PDF coordinates."""
    AB_OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if image_only(page):
        img = max(page.images, key=lambda im: im["width"] * im["height"])
        bbox = (img["x0"], img["top"], img["x1"], img["bottom"])
    else:
        bbox = (0, 0, page.width, page.height)
    stem = f"{Path(pdf_name).stem}_p{pno}_{variant}"
    png, cache = AB_OCR_CACHE_DIR / f"{stem}.png", AB_OCR_CACHE_DIR / f"{stem}.json"
    if not cache.exists():
        _variant_image(page, bbox, variant).save(png)
        script = AB_OCR_CACHE_DIR / "ocr.ps1"
        script.write_text(OCR_SCRIPT, encoding="utf-8")
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-ImagePath", str(png.resolve()), "-OutJson", str(cache.resolve())],
                       check=True, capture_output=True, timeout=300)
    data = json.loads(cache.read_text(encoding="utf-8-sig"))
    sx, sy = (bbox[2] - bbox[0]) / data["width"], (bbox[3] - bbox[1]) / data["height"]
    return [{"text": w["text"], "x0": bbox[0] + w["x"] * sx, "x1": bbox[0] + (w["x"] + w["w"]) * sx,
             "top": bbox[1] + w["y"] * sy, "bottom": bbox[1] + (w["y"] + w["h"]) * sy, "ocr": True}
            for w in data["words"]]


def ocr_number(tok: str) -> str | None:
    """Repair a numeric OCR token: o/O->0, B->8, I/l->1, a lost decimal point."""
    t = tok.strip().rstrip(".,")
    t = re.sub(r"(?<=\d)[oO]|[oO](?=\d)|^[oO]+$", "0", t)
    t = t.replace("B", "8").replace("I", "1").replace("l", "1").replace("|", "1")
    t = re.sub(r"(?<=\d)-(?=[0oO]{2}$)", ".", t).replace("o", "0").replace("O", "0")
    t = re.sub(r"(?<=\d),(?=\d{2}$)", ".", t)
    if re.fullmatch(r"\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?|\(?\d+\.\d{2}\)?", t):
        return t
    if re.fullmatch(r"\d{3,6}", t):
        return t[:-2] + "." + t[-2:]          # every value in these budgets has two decimals
    return None


def value_of(tok: str, ocr: bool) -> float | None:
    if ocr:
        fixed = ocr_number(tok)
        return None if fixed is None else to_number(fixed)
    return to_number(tok) if NUMBER_RE.match(tok) else None


# ---------------------------------------------------------------------------
# Budget tables (Tables 3-8)
# ---------------------------------------------------------------------------
def _center(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2


def _ymid(w: dict) -> float:
    return (w["top"] + w["bottom"]) / 2


def parse_budget(words: list[dict], ocr: bool) -> dict:
    lines = group_lines(words)
    sales_line = next((ln for ln in lines if re.search(r"crop\s*sales", " ".join(w["text"] for w in ln).lower())), None)
    if sales_line is None:
        return {"error": "Crop Sales row not found"}
    label_end = max(w["x1"] for w in sales_line if re.search(r"(?i)crop|sales|acre", w["text"]))
    # Columns from the fullest numeric row (a forage column can be blank on Crop Sales).
    best = []
    for ln in lines:
        nums = [w for w in ln if w["x0"] > label_end and value_of(w["text"], ocr) is not None]
        if len(nums) > len(best):
            best = nums
    rights = sorted(w["x1"] for w in best)
    rights = [r for k, r in enumerate(rights) if k == 0 or r - rights[k - 1] > 8]
    if len(rights) < 2:
        return {"error": "too few columns"}
    width = statistics.median(b - a for a, b in zip(rights, rights[1:]))
    left_bound = rights[0] - 0.95 * width

    # Row anchors: label text left of the value columns.
    label_lines = []
    for ln in lines:
        lw = [w for w in ln if _center(w) < left_bound]
        if lw:
            label_lines.append({"text": " ".join(w["text"] for w in lw), "y": statistics.mean(_ymid(w) for w in lw),
                                "top": min(w["top"] for w in lw)})
    rows, header_top, stop_y = [], None, None
    for ll in label_lines:
        low = ll["text"].lower()
        if header_top is None and re.search(r"^ex\S*\s*y\S{0,2}ld|y\S{0,2}ld\s*per", low):
            header_top = ll["top"]
        if re.search(r"capital\s*costs", low):
            stop_y = ll["y"]
            break
        key = next((k for k, rx in ROW_KEYS if re.search(rx, low)), None)
        rows.append({"label": ll["text"], "key": key, "y": ll["y"]})
    if header_top is None:
        return {"error": "Expected Yield row not found"}
    rows = [r for r in rows if r["y"] >= header_top - 1]
    pitch = statistics.median(b["y"] - a["y"] for a, b in zip(rows, rows[1:])) if len(rows) > 2 else 10

    def col_of(w: dict) -> int | None:
        best = min(range(len(rights)), key=lambda i: abs(rights[i] - w["x1"]))
        return best if abs(rights[best] - w["x1"]) <= 0.6 * width else None

    # Header: words between the title / "Stubble Seeded Crops" banner and the first data row,
    # split at column right edges.
    banner = [w for w in words if w["top"] < header_top and
              re.search(r"(?i)^(stubble|seeded|returns|production|\(\$/acre\))$", w["text"])]
    header_floor = max((w["bottom"] for w in banner), default=header_top - 7 * pitch)
    header_words = [[] for _ in rights]
    for w in words:
        if w["bottom"] <= header_top + 0.5 and _center(w) >= left_bound and w["top"] > header_floor - 0.5:
            if re.search(r"(?i)stubble|seeded|crops|production|costs|returns|soils|table|\$/acre", w["text"]):
                continue
            i = next((i for i, r in enumerate(rights) if _center(w) <= r + 0.15 * width), None)
            if i is not None:
                header_words[i].append(w)
    columns = []
    for i, ws in enumerate(header_words):
        ws.sort(key=lambda w: (round(w["top"]), w["x0"]))
        name = " ".join(w["text"] for w in ws if w["text"].lower().strip("*") in CROP_WORDS)
        grade = " ".join(w["text"] for w in ws if w["text"].lower().strip("*") not in CROP_WORDS)
        columns.append({"index": i, "header": " ".join(w["text"] for w in ws), "crop_name": name,
                        "grade": grade, "crop_key": crop_key(name) if name else None,
                        "footnote": "*" in "".join(w["text"] for w in ws)})

    # Values: each word right of the labels goes to the nearest row (by y) and column (by right edge).
    cells: dict[tuple[int, int], list[dict]] = {}
    for w in words:
        if _center(w) < left_bound or _ymid(w) < header_top or (stop_y and _ymid(w) > stop_y - pitch / 2):
            continue
        r = min(range(len(rows)), key=lambda k: abs(rows[k]["y"] - _ymid(w)))
        if abs(rows[r]["y"] - _ymid(w)) > 0.75 * pitch:
            continue
        c = col_of(w)
        if c is None and rows[r]["key"] == "npks":
            c = min(range(len(rights)), key=lambda i: abs(rights[i] - w["x1"]))
        if c is not None:
            cells.setdefault((r, c), []).append(w)

    out_rows = []
    for r, row in enumerate(rows):
        vals = []
        for c in range(len(rights)):
            ws = sorted(cells.get((r, c), []), key=lambda w: w["x0"])
            if row["key"] == "npks":
                digits = re.findall(r"\d+(?:\.\d+)?", " ".join(w["text"].replace("o", "0") for w in ws))
                vals.append("-".join(digits) if len(digits) == 4 else (" ".join(w["text"] for w in ws) or None))
                continue
            nums = [(w, value_of(w["text"], ocr)) for w in ws]
            nums = [(w, v) for w, v in nums if v is not None]
            if not nums:
                vals.append("NA" if any(w["text"].upper() == "NA" for w in ws) else None)
                continue
            w, v = min(nums, key=lambda wv: abs(rights[c] - wv[0]["x1"]))
            vals.append(v)
        out_rows.append({"label": row["label"], "key": row["key"], "values": vals})

    # Duplicate or unreadable labels: fall back on the budgets' fixed row order.
    seen: set[str] = set()
    for r in out_rows:
        if r["key"] in seen:
            r["key"] = None
        if r["key"]:
            seen.add(r["key"])
    prev = None
    for r in out_rows:
        if r["key"]:
            prev = r["key"]
            continue
        filled = sum(v is not None for v in r["values"])
        if prev and filled >= max(2, len(rights) // 3):
            nxt = next((k for k in ROW_ORDER[ROW_ORDER.index(prev) + 1:]
                        if k not in seen and (k != "pumping" or "pump" in r["label"].lower())), None)
            if nxt:
                r.update(key=nxt, keyed_by="row order")
                seen.add(nxt)
                prev = nxt
    return {"columns": columns, "rows": out_rows, "column_rights": [round(r, 1) for r in rights], "ocr": ocr}


def merge_variants(tables: list[dict]) -> dict:
    """Cell-by-cell majority vote across OCR variants of the same budget table."""
    tables = [t for t in tables if "error" not in t]
    if not tables:
        return {"error": "no OCR variant parsed"}
    primary = max(tables, key=lambda t: (len(t["columns"]), sum(r["key"] is not None for r in t["rows"])))
    rights = primary["column_rights"]
    width = statistics.median(b - a for a, b in zip(rights, rights[1:]))

    def col_map(t: dict) -> dict[int, int]:
        out = {}
        for j, r in enumerate(t["column_rights"]):
            i = min(range(len(rights)), key=lambda i: abs(rights[i] - r))
            if abs(rights[i] - r) <= 0.4 * width:
                out[j] = i
        return out

    maps = [col_map(t) for t in tables]
    keyed = [{r["key"]: r for r in t["rows"] if r["key"]} for t in tables]
    rows = []
    for prow in primary["rows"]:
        key = prow["key"]
        if not key:
            rows.append(prow)
            continue
        votes: list[list] = [[] for _ in rights]
        for t, m, kr in zip(tables, maps, keyed):
            if key in kr:
                for j, v in enumerate(kr[key]["values"]):
                    if v is not None and j in m:
                        votes[m[j]].append(v)
        vals = []
        for i, cand in enumerate(votes):
            nums = [c for c in cand if c != "NA"]
            if not nums:
                vals.append(cand[0] if cand else None)
                continue
            tally = {}
            for c in nums:
                tally[c] = tally.get(c, 0) + 1
            best = max(tally.values())
            winners = [c for c, n in tally.items() if n == best]
            pv = prow["values"][i]
            vals.append(pv if pv in winners else winners[0])
        rows.append({**prow, "values": vals, "votes": [len(v) for v in votes],
                     "candidates": [sorted({c for c in v if c != "NA"}, key=str) for v in votes]})
    columns = []
    for i, col in enumerate(primary["columns"]):
        keys = [t["columns"][j]["crop_key"] for t, m in zip(tables, maps) for j, ii in m.items()
                if ii == i and j < len(t["columns"]) and t["columns"][j]["crop_key"]]
        best = max(set(keys), key=keys.count) if keys else None
        columns.append({**col, "crop_key": best})
    return {**primary, "columns": columns, "rows": rows, "variants": len(tables)}


# ---------------------------------------------------------------------------
# Tables 1 and 2 (zone grids)
# ---------------------------------------------------------------------------
def parse_zone_grid(words: list[dict], y_from: float, y_to: float, kind: str) -> dict[str, dict[str, list[float]]]:
    """{crop_label: {zone_key: [values in order]}} for Table 1 (seed) or Table 2 (chemical)."""
    region = [w for w in words if y_from < w["top"] < y_to]
    sub_rx = r"^(lbs/ac|bu/ac|\$/ac)$" if kind == "seed" else r"^(preseed|in|crop|f/i/ph/d)$"
    lines = group_lines(region)
    unit_line = next((ln for ln in lines if sum(bool(re.match(sub_rx, w["text"].lower())) for w in ln) >= 12), None)
    if unit_line is None:
        return {}
    subs = [w for w in unit_line if re.match(sub_rx, w["text"].lower())]
    if kind == "chem":   # "In Crop" is two words
        merged = []
        for w in subs:
            if w["text"].lower() == "crop" and merged and merged[-1]["text"].lower() == "in":
                merged[-1] = {**merged[-1], "text": "In Crop", "x1": w["x1"]}
            else:
                merged.append(w)
        subs = merged
    if len(subs) != 18:
        return {}
    zone_spans = [(subs[3 * i]["x0"], subs[3 * i + 2]["x1"]) for i in range(6)]
    first_x = zone_spans[0][0] - 4
    unit_bottom = max(w["bottom"] for w in unit_line)

    labels, values = [], []
    for w in region:
        if w["top"] <= unit_bottom:
            continue
        if _center(w) < first_x and not NUMBER_RE.match(w["text"]):
            labels.append(w)
        elif NUMBER_RE.match(w["text"]):
            values.append(w)
    label_lines = [{"text": " ".join(x["text"] for x in ln), "top": min(x["top"] for x in ln)}
                   for ln in group_lines(labels)]
    cells: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for w in sorted(values, key=lambda w: (w["top"], w["x0"])):
        above = [ll for ll in label_lines if ll["top"] <= w["top"] + 2.5]
        if not above:
            continue
        label = max(above, key=lambda ll: ll["top"])["text"]
        c = _center(w)
        z = next((i for i, (a, b) in enumerate(zone_spans) if a - 6 <= c <= b + 6), None)
        if z is None:
            z = min(range(6), key=lambda i: min(abs(c - zone_spans[i][0]), abs(c - zone_spans[i][1])))
        cells.setdefault(label, {}).setdefault(TABLE_ZONE_ORDER[z], []).append((w["top"], to_number(w["text"])))
    # Keep only the values on the first line under each label: when OCR drops a crop's
    # label, its values would otherwise pile onto the crop above.
    grid: dict[str, dict[str, list[float]]] = {}
    for label, zones in cells.items():
        for zone, vals in zones.items():
            first = min(t for t, _ in vals)
            grid.setdefault(label, {})[zone] = [v for t, v in vals if t - first <= 3]
    return grid


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
def zone_of(title: str) -> str | None:
    t = title.lower()
    if "peace" in t:
        return "peace"
    for key, rx in (("grey_wooded", r"grey"), ("dark_brown", r"dark\s*brown"), ("brown", r"brown"),
                    ("black", r"black"), ("irrigated", r"irrigat")):
        if re.search(rx, t):
            return key
    return None


def parse_fert_prices(text: str) -> dict[str, dict]:
    out = {}
    for m in FERT_PRICE_RE.finditer(" ".join(text.split())):
        out[NUTRIENT_KEY[m.group(1).lower()]] = {"per_lb": float(m.group(2)),
                                                 "per_tonne": float(m.group(3).replace(",", "")),
                                                 "product": m.group(4)}
    return out


def parse_pdf(path: Path, year: int) -> dict:
    doc = {"year": year, "file": path.name, "budgets": [], "seed_table": {}, "chem_table": {}, "warnings": []}
    with pdfplumber.open(path) as pdf:
        doc["page_count"] = len(pdf.pages)
        method = " ".join((pdf.pages[i].extract_text() or "") for i in range(min(7, len(pdf.pages))))
        doc["fert_prices"] = parse_fert_prices(method)
        if len(doc["fert_prices"]) != 4:
            doc["warnings"].append(f"fertilizer prices found for {sorted(doc['fert_prices'])} only")
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            head = " ".join(text.split()[:40]).lower()
            words = None
            if re.search(r"table\s*1\.?\s*seeding", head.replace(" ", " ")) or \
                    re.search(r"t\s*a\s*b\s*l\s*e\s*1", head[:40]):
                words = ocr_words(page, path.name, pno) if broken_text(page) else text_words(page)
                lines = group_lines(words)
                t1 = next((ln[0]["top"] for ln in lines if re.search(r"(?i)^table$", ln[0]["text"])
                           and len(ln) > 1 and ln[1]["text"].startswith("1")), 0)
                t2 = next((ln[0]["top"] for ln in lines if re.search(r"(?i)^table$", ln[0]["text"])
                           and len(ln) > 1 and ln[1]["text"].startswith("2")), None)
                doc["seed_table"] = parse_zone_grid(words, t1, t2 or page.height, "seed") or doc["seed_table"]
                if t2:
                    doc["chem_table"] = parse_zone_grid(words, t2, page.height, "chem")
                doc.setdefault("rate_pages", []).append(pno)
                continue
            if re.search(r"table\s*2\.?\s*chemical", head) and not doc["chem_table"]:
                words = ocr_words(page, path.name, pno) if broken_text(page) else text_words(page)
                doc["chem_table"] = parse_zone_grid(words, 0, page.height, "chem")
                doc.setdefault("rate_pages", []).append(pno)
                continue
            m = re.search(r"production costs and returns.*?\(\$/acre\)\s*(.+?soils|grey-wooded\s*\(peace\s*region\)|"
                          r"grey-wooded\s*\(peace)", head)
            if not m:
                continue
            zone = zone_of(m.group(1))
            if image_only(page):        # low-resolution scan: three OCR variants vote
                table = merge_variants([parse_budget(ocr_words(page, path.name, pno, v), True)
                                        for v in ("render", "bin3", "sharp3")])
                source = "ocr (image, 3-variant vote)"
            elif broken_text(page):     # letter-spaced text layer: OCR the vector render, 3-variant vote
                table = merge_variants([parse_budget(ocr_words(page, path.name, pno, v), True)
                                        for v in ("render", "bin3", "sharp3")])
                source = "ocr (vector render, 3-variant vote)"
            else:
                table = parse_budget(text_words(page), False)
                source = "text layer"
            table.update(page=pno, zone=zone, title=m.group(0), source=source)
            doc["budgets"].append(table)
    for name in ("seed_table", "chem_table"):
        if not doc[name]:
            doc["warnings"].append(f"{name} not parsed")
    return doc


# ---------------------------------------------------------------------------
# Normalize + cross-check
# ---------------------------------------------------------------------------
def _grid_lookup(grid: dict, key: str) -> dict | None:
    for label, zones in grid.items():
        if crop_key(label) == key:
            return zones
    return None


def normalize(doc: dict) -> list[dict]:
    prices = {k: v["per_lb"] for k, v in doc["fert_prices"].items()}
    records = []
    for table in doc["budgets"]:
        if "error" in table:
            continue
        zone = table["zone"]
        rows = {r["key"]: r["values"] for r in table["rows"] if r["key"]}
        cands = {r["key"]: r.get("candidates") for r in table["rows"] if r["key"]}
        used_keys: set[str] = set()
        for col in table["columns"]:
            key = col["crop_key"]
            if key is None or key in used_keys:
                continue
            used_keys.add(key)
            i = col["index"]

            def v(field: str):
                vals = rows.get(field)
                x = vals[i] if vals and i < len(vals) else None
                return x if isinstance(x, (int, float)) else None

            rec = {"year": doc["year"], "province": "AB", "zone": zone, "zone_display": AB_ZONES.get(zone),
                   "crop_key": key, "display": AB_CROPS[key]["display"], "crop_header": col["header"],
                   "grade": col["grade"], "source": {"file": doc["file"], "page": table["page"]},
                   "ocr": table["ocr"], "notes": [], "checks": []}
            y, p = v("yield"), v("price")
            lb_yield = y is not None and y > 300
            rec.update(yield_=y, yield_unit="lb/ac" if lb_yield else "bu/ac", price=p,
                       price_unit="$/lb" if lb_yield else "$/bu")
            for f in ("sales", "seed", "fertilizer", "chemical", "total_direct", "contribution", "break_even"):
                rec[f] = v(f)
            npks_raw = rows.get("npks", [None] * (i + 1))[i] if rows.get("npks") else None
            rec["npks_blend"] = npks_raw
            npks = [float(x) for x in npks_raw.split("-")] if isinstance(npks_raw, str) and NPKS_RE.match(npks_raw) else None
            rec.update(n_lbs_ac=npks[0] if npks else None, p2o5_lbs_ac=npks[1] if npks else None,
                       k2o_lbs_ac=npks[2] if npks else None, s_lbs_ac=npks[3] if npks else None)
            present = [k for k in DIRECT_ITEMS if k in rows]
            items = {k: v(k) for k in present}

            seed_grid = (_grid_lookup(doc["seed_table"], key) or {}).get(zone) or []
            chem_grid = (_grid_lookup(doc["chem_table"], key) or {}).get(zone) or []
            t1_cost = seed_grid[-1] if len(seed_grid) >= 2 else None
            rate = seed_grid[0] if len(seed_grid) >= 2 else None
            unit = "lb/ac" if key in LB_SEEDED else "bu/ac"
            rec["seeding_rate"] = rate
            rec["seeding_rate_unit"] = unit if rate is not None else None
            rec["seeding_rate_lbs_ac"] = (None if rate is None else
                                          rate if unit == "lb/ac" else rate * AB_CROPS[key]["bu_weight"])
            rec["chemical_table2"] = round(sum(chem_grid), 2) if chem_grid else None
            fert_calc = (sum(a * prices.get(k, 0) for a, k in zip(npks, "NPKS")) if npks and len(prices) == 4
                         else None)

            if rec["ocr"]:
                alts = {k: (cands.get(k) or [None] * (i + 1))[i] if cands.get(k) and i < len(cands[k]) else []
                        for k in present}
                repair_ocr(rec, items, t1_cost, fert_calc, alts)
            y, p = rec["yield_"], rec["price"]
            if y is not None:
                lb_yield = y > 300
                rec.update(yield_unit="lb/ac" if lb_yield else "bu/ac", price_unit="$/lb" if lb_yield else "$/bu")

            def check(name: str, got, want, tol: float, info: bool = False) -> None:
                entry = {"check": name, "ok": None if got is None or want is None else abs(got - want) <= tol}
                if entry["ok"] is not None:
                    entry.update(printed=got, expected=round(want, 4))
                if info:
                    entry["informational"] = True
                rec["checks"].append(entry)

            total_items = sum(x for x in items.values() if x is not None) if items else None
            check("sales = yield x price", rec["sales"], None if None in (y, p) else y * p,
                  max(0.51, 0.005 * (rec["sales"] or 0)))
            check("sum of direct items = total", rec["total_direct"],
                  total_items if all(x is not None for x in items.values()) else None, 0.06)
            check("sales - total = contribution", rec["contribution"],
                  None if None in (rec["sales"], rec["total_direct"]) else rec["sales"] - rec["total_direct"], 0.02)
            # The guides cost the physical product blend and round up, so this is a sanity bound.
            check("fertilizer ~ NPKS x published $/lb", rec["fertilizer"], fert_calc,
                  max(1.5, 0.012 * (fert_calc or 0)))
            check("seed vs Table 1 $/ac", rec["seed"], t1_cost, 0.015, info=True)
            seed_c = rec["checks"][-1]
            if seed_c["ok"] is False:
                rec["notes"].append(f"budget seed ${rec['seed']:.2f} differs from Table 1 ${t1_cost:.2f} "
                                    "(guide inconsistency; budget value kept)")
            check("chemical vs Table 2 total", rec["chemical"], rec["chemical_table2"], 0.03, info=True)

            failed = [c["check"] for c in rec["checks"] if c["ok"] is False and not c.get("informational")]
            if failed:
                rec["notes"].append("unverified: " + "; ".join(failed))
            core = ("yield_", "price", "seed", "chemical")
            if any(rec.get(f) is None for f in core) or npks is None:
                rec["notes"].append("core field missing: " + ", ".join(
                    [f.rstrip("_") for f in core if rec.get(f) is None] + (["npks"] if npks is None else [])))
            rec["verified"] = not failed and not any(n.startswith("core field missing") for n in rec["notes"])
            # Every year the budget prices red lentil seed with the green-lentil Table 1 cost
            # (90 lb rate); use Table 1's red-lentil rate and cost. Checks above ran on the
            # printed figure, which is kept in seed_printed.
            rec["seed_printed"] = rec["seed"]
            if key in SEED_FROM_TABLE1 and t1_cost is not None and rec["seed"] != t1_cost:
                rec["notes"] = [n for n in rec["notes"] if not n.startswith("budget seed")]
                rec["notes"].append(f"seed ${t1_cost:.2f} from Table 1 red lentils ({rate:g} lb/ac); budget "
                                    f"printed ${rec['seed']:.2f} (green-lentil seed cost)")
                rec["seed"] = t1_cost
                rec["seed_source"] = "Table 1"
            else:
                rec["seed_source"] = "budget"
            records.append(rec)
    return records


def repair_ocr(rec: dict, items: dict, t1_cost: float | None, fert_calc: float | None,
               alts: dict | None = None) -> None:
    """Fill or correct OCR'd values only from an independent source or a table identity."""
    notes = rec["notes"]
    sales, contrib, total = rec["sales"], rec["contribution"], rec["total_direct"]
    t_alt = None if None in (sales, contrib) else round(sales - contrib, 2)
    targets = [t for t in (total, t_alt) if t is not None]

    def adds_up(vals: dict) -> bool:
        return all(x is not None for x in vals.values()) and any(abs(sum(vals.values()) - t) <= 0.06
                                                                  for t in targets)

    if rec["seed"] is None and t1_cost is not None:
        notes.append(f"OCR seed unread -> Table 1 ${t1_cost:.2f}")
        rec["seed"] = items["seed"] = t1_cost
    elif t1_cost is not None and abs(rec["seed"] - t1_cost) > 0.015 and not adds_up(items)             and adds_up({**items, "seed": t1_cost}):
        notes.append(f"OCR seed {rec['seed']} -> Table 1 ${t1_cost:.2f} (table then adds up)")
        rec["seed"] = items["seed"] = t1_cost
    # A cell the OCR variants disagreed on: try their other readings against the total.
    if targets and not adds_up(items) and alts:
        for k, options in alts.items():
            for alt in options or []:
                if isinstance(alt, (int, float)) and alt != items.get(k) and adds_up({**items, k: alt}):
                    notes.append(f"OCR {k} {items.get(k)} -> {alt} (other OCR reading; table then adds up)")
                    items[k] = alt
                    if k in rec:
                        rec[k] = alt
                    break
            if adds_up(items):
                break
    known = [x for x in items.values() if x is not None]
    missing = [k for k, x in items.items() if x is None]
    candidates = [t for t in (total, t_alt) if t is not None]
    if not missing and known:
        match = next((t for t in candidates if abs(sum(known) - t) <= 0.06), None)
        if match is not None and match != total:
            notes.append(f"OCR total {total} -> {match:.2f} (sales - contribution)")
            rec["total_direct"] = match
    elif len(missing) == 1 and candidates:
        t = t_alt if (total is None or (t_alt is not None and t_alt != total)) and t_alt is not None else total
        fill = round(t - sum(known), 2)
        if fill >= 0:
            notes.append(f"OCR {missing[0]} unread -> {fill:.2f} (total - other items)")
            items[missing[0]] = fill
            if missing[0] in ("seed", "chemical", "fertilizer"):
                rec[missing[0]] = fill
            if rec["total_direct"] is None:
                rec["total_direct"] = t
    if rec["sales"] is None and None not in (rec["total_direct"], contrib):
        rec["sales"] = round(rec["total_direct"] + contrib, 2)
        notes.append("OCR sales unread -> total + contribution")
    y, p, s = rec["yield_"], rec["price"], rec["sales"]
    if s is not None:
        if y is None and p:
            rec["yield_"] = round(s / p, 2)
            notes.append("OCR yield unread -> sales / price")
        elif p is None and y:
            rec["price"] = round(s / y, 2 if y < 300 else 4)
            notes.append("OCR price unread -> sales / yield")
        elif y and p and abs(y * p - s) > max(0.51, 0.005 * s):
            p2, y2 = round(s / y, 2 if y < 300 else 2), round(s / p, 2)
            if abs(p2 - p) / p <= abs(y2 - y) / y:
                notes.append(f"OCR price {p} -> {p2} (sales / yield)")
                rec["price"] = p2
            else:
                notes.append(f"OCR yield {y} -> {y2} (sales / price)")
                rec["yield_"] = y2
    if fert_calc is not None and (rec["fertilizer"] is None or
                                  abs(rec["fertilizer"] - fert_calc) > max(1.5, 0.012 * fert_calc)):
        notes.append(f"OCR fertilizer {rec['fertilizer']} -> NPKS x $/lb {fert_calc:.2f} (approximate)")
        rec["fertilizer"] = round(fert_calc, 2)
    if rec["chemical"] is None and rec.get("chemical_table2") is not None:
        notes.append(f"OCR chemical unread -> Table 2 ${rec['chemical_table2']:.2f} (approximate)")
        rec["chemical"] = rec["chemical_table2"]


CSV_FIELDS = ["year", "province", "zone", "zone_display", "crop_key", "display", "grade", "yield_", "yield_unit",
              "price", "price_unit", "sales", "seed", "seed_source", "seed_printed", "fertilizer", "npks_blend", "n_lbs_ac", "p2o5_lbs_ac",
              "k2o_lbs_ac", "s_lbs_ac", "chemical", "chemical_table2", "total_direct", "contribution",
              "break_even", "seeding_rate", "seeding_rate_unit", "seeding_rate_lbs_ac", "ocr", "verified", "notes"]


def detect_pdfs() -> dict[int, Path]:
    found = {}
    for pdf in sorted(AB_INPUT_PDF_DIR.glob("*.pdf")):
        m = PDF_YEAR_PATTERN.search(pdf.stem)
        if m and int(m.group(1)) in YEARS:
            found[int(m.group(1))] = pdf
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="*")
    args = ap.parse_args(argv)
    pdfs = detect_pdfs()
    if not AB_INPUT_PDF_DIR.is_dir() or not pdfs:
        print(f"No AB PDFs in {AB_INPUT_PDF_DIR}")
        return 1
    if args.years:
        pdfs = {y: p for y, p in pdfs.items() if y in args.years}
    print(f"AB input folder: {AB_INPUT_PDF_DIR}")
    docs, records = [], []
    for year, path in sorted(pdfs.items()):
        doc = parse_pdf(path, year)
        recs = normalize(doc)
        docs.append(doc)
        records += recs
        n_ok = sum(r["verified"] for r in recs)
        fp = ", ".join(f"{k} ${v['per_lb']:.2f}" for k, v in sorted(doc["fert_prices"].items()))
        print(f"  {year}  budgets={len(doc['budgets'])}  crop-zones={len(recs):3}  verified={n_ok:3}  "
              f"ocr={'yes' if any(b.get('ocr') for b in doc['budgets']) else 'no ':3}  fert: {fp}"
              + (f"  WARN {doc['warnings']}" if doc["warnings"] else ""))

    AB_INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    AB_OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    meta = {"parser_version": PARSER_VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_dir": str(AB_INPUT_PDF_DIR), "years": sorted(pdfs)}
    AB_RAW_JSON.write_text(json.dumps({"meta": meta, "documents": docs}, indent=1, ensure_ascii=False), encoding="utf-8")
    fert = {d["year"]: d["fert_prices"] for d in docs}
    AB_OUTPUT_JSON.write_text(json.dumps({"meta": {**meta, "zones": AB_ZONES, "fert_prices": fert},
                                          "records": records}, indent=1, ensure_ascii=False), encoding="utf-8")
    with AB_OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({**r, "notes": "; ".join(r["notes"])})
    print(f"  wrote {AB_RAW_JSON}\n  wrote {AB_OUTPUT_JSON}\n  wrote {AB_OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
