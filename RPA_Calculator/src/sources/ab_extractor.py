"""Alberta AgriProfit$ "Cropping Alternatives" extractor (2019-2026): pure extraction.

Reads the AB guides (docs/AGENT_RULES.json provinces.AB.source) and writes every table as
printed to output/ab/intermediate/ab_raw_tables.json:

    budget tables (Tables 3-8)  one per region: per column the header / crop name / grade and
                                every row (yield, price, sales, seed, fertilizer $, NPKS blend,
                                chemical, ... total direct expense, contribution, break-even)
    Table 1 / Table 2           seeding rate + seed $/ac, chemical $/ac, by region
    methodology                 published fertilizer prices ($/lb and $/tonne product quotes)

No crop mapping, region rule or override happens here (see src/normalize/ab_cop.py).
2019's budget tables are embedded images and the 2024-25 pages have a letter-spaced text
layer: both are read with the Windows OCR engine (3 rendered variants that vote cell by cell),
cached under output/ab/intermediate/ocr/.

    python -m src.sources.ab_extractor [--years 2025 2026]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from ..config import province, source_pdfs
from .sk_extractor import group_lines, to_number

SPEC = province("AB")
PARSER_VERSION = "2.0.0"
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


# ---------------------------------------------------------------------------
# Words (text layer, wide-spaced text, or Windows OCR)
# ---------------------------------------------------------------------------
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
    SPEC.ocr_cache_dir.mkdir(parents=True, exist_ok=True)
    if image_only(page):
        img = max(page.images, key=lambda im: im["width"] * im["height"])
        bbox = (img["x0"], img["top"], img["x1"], img["bottom"])
    else:
        bbox = (0, 0, page.width, page.height)
    stem = f"{Path(pdf_name).stem}_p{pno}_{variant}"
    png, cache = SPEC.ocr_cache_dir / f"{stem}.png", SPEC.ocr_cache_dir / f"{stem}.json"
    if not cache.exists():
        _variant_image(page, bbox, variant).save(png)
        script = SPEC.ocr_cache_dir / "ocr.ps1"
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
                        "grade": grade, "crop_name_variants": [name] if name else [],
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
        names = [n for t, m in zip(tables, maps) for j, ii in m.items()
                 if ii == i and j < len(t["columns"]) for n in t["columns"][j]["crop_name_variants"]]
        columns.append({**col, "crop_name_variants": names})
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
# Driver
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="*")
    args = ap.parse_args(argv)
    pdfs = source_pdfs(SPEC)
    if args.years:
        pdfs = {y: p for y, p in pdfs.items() if y in args.years}
    if not pdfs:
        print(f"No AB PDFs in {SPEC.input_dir}")
        return 1
    print(f"AB input folder: {SPEC.input_dir}")
    docs = []
    for year, path in sorted(pdfs.items()):
        doc = parse_pdf(path, year)
        docs.append(doc)
        ocr = any(b.get("source", "").startswith("ocr") for b in doc["budgets"])
        fp = ", ".join(f"{k} ${v['per_lb']:.2f}" for k, v in sorted(doc["fert_prices"].items()))
        print(f"  {year}  budgets={len(doc['budgets'])}  ocr={'yes' if ocr else 'no ':3}  fert: {fp}"
              + (f"  WARN {doc['warnings']}" if doc["warnings"] else ""))
    SPEC.intermediate_dir.mkdir(parents=True, exist_ok=True)
    meta = {"parser_version": PARSER_VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_dir": str(SPEC.input_dir), "years": sorted(pdfs)}
    SPEC.raw_json.write_text(json.dumps({"meta": meta, "documents": docs}, indent=1, ensure_ascii=False),
                             encoding="utf-8")
    print(f"  wrote {SPEC.raw_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
