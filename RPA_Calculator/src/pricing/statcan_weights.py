"""Prairie acreage weights from StatCan Table 32-10-0359 (seeded area, acres) [PRAIRIE-WEIGHTED-RPA].

Reads the newest local CANSIM file `32100359.csv` found under docs/AGENT_RULES.json
`statcan_acreage.search_root` (../Workspace_CanadaSD/Data_Inputs, where CANSIM downloads are dumped;
override with the env var STATCAN_ACREAGE_CSV; read-only), takes seeded acres for MB, SK and AB over the latest
`window_years` reference years (2024-2026), and per crop_key computes

    mean acres_p   = average of the window's published values (suppressed F / x cells skipped)
    weight_p       = mean acres_p / sum over MB, SK, AB of mean acres

i.e. each province's share of the 3-year average Prairie acreage. The result is written back to
docs/AGENT_RULES.json `prairie_acreage_weights` (the only block of the rules file this module
writes; the rest of the file is left byte-for-byte unchanged).

    python -m src.pricing.statcan_weights [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import date
from pathlib import Path

from ..config import PROJECT_ROOT, RULES, RULES_PATH

BLOCK_KEY = "prairie_acreage_weights"


def source_csv() -> Path:
    """The CANSIM file to read: env STATCAN_ACREAGE_CSV, else the newest `<table id>.csv` anywhere under
    `statcan_acreage.search_root` (CANSIM downloads are dumped there as `<name>_<table>-eng/<table>.csv`),
    else the configured `path`."""
    env = os.environ.get("STATCAN_ACREAGE_CSV")
    if env:
        return Path(env)
    spec = RULES.raw["statcan_acreage"]
    root = (PROJECT_ROOT / spec.get("search_root", "")).resolve() if spec.get("search_root") else None
    if root and root.is_dir():
        found = [f for f in root.rglob(spec["file"]) if f.is_file()]
        if found:
            return max(found, key=lambda f: f.stat().st_mtime)
    return (PROJECT_ROOT / spec["path"]).resolve()


def read_series(path: Path | None = None) -> tuple[dict, list[int]]:
    """({crop_key: {code: {year: acres or None}}}, every reference year in the file) for the rules'
    crops and provinces; suppressed (F / x) cells are None."""
    spec = RULES.raw["statcan_acreage"]
    path = path or source_csv()
    if not path.exists():
        raise FileNotFoundError(f"StatCan 32-10-0359 CSV not found: {path}")
    geo = {name: code for code, name in spec["provinces"].items()}
    by_name: dict[str, list[str]] = {}
    for key, name in spec["crops"].items():
        by_name.setdefault(name, []).append(key)
    raw: dict[str, dict[str, dict[int, float | None]]] = {}
    years: set[int] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["Harvest disposition"] != spec["series"] or row["GEO"] not in geo:
                continue
            keys = by_name.get(row["Type of crop"])
            if not keys:
                continue
            y = int(row["REF_DATE"])
            years.add(y)
            scale = {"units": 1, "tens": 10, "hundreds": 100, "thousands": 1000}[row["SCALAR_FACTOR"]]
            v = float(row["VALUE"]) * scale if row["VALUE"] not in ("", None) else None
            for key in keys:
                raw.setdefault(key, {}).setdefault(geo[row["GEO"]], {})[y] = v
    return raw, sorted(years)


def read_acres(path: Path | None = None) -> tuple[dict, list[int], list[str]]:
    """({crop_key: {code: {year: acres}}}, window years, notes) for the rules' crops and provinces."""
    spec = RULES.raw["statcan_acreage"]
    raw, years = read_series(path)
    window = [y for y in years if y <= RULES.baseline_year][-spec["window_years"]:]
    notes = []
    acres = {}
    for key in spec["crops"]:
        acres[key] = {}
        for code in spec["provinces"]:
            series = raw.get(key, {}).get(code, {})
            acres[key][code] = {y: series.get(y) for y in window}
            missing = [y for y in window if series.get(y) is None]
            if missing:
                notes.append(f"{key} {code}: no published seeded area for {missing} (suppressed or not surveyed)")
    return acres, window, notes


def compute_weights(acres: dict, window: list[int]) -> dict:
    """{crop_key: {"mean_acres": {code: acres}, "weights": {code: share}}} (shares over MB + SK + AB)."""
    out = {}
    for key, provs in acres.items():
        means = {}
        for code, series in provs.items():
            vals = [v for v in series.values() if v is not None]
            means[code] = sum(vals) / len(vals) if vals else 0.0
        total = sum(means.values())
        out[key] = {"mean_acres": {c: round(m, 1) for c, m in means.items()},
                    "weights": {c: (round(m / total, 6) if total else 0.0) for c, m in means.items()}}
    return out


def block_json(weights: dict, window: list[int], path: Path, notes: list[str]) -> str:
    spec = RULES.raw["statcan_acreage"]
    source = f"StatCan Table {spec['table']}, {spec['series']} ({path.name})"
    rule_id = spec["rule_id"]
    lines = [f'  "{BLOCK_KEY}": {{',
             f'    "rule_id": "{rule_id}",',
             f'    "generated": "{date.today().isoformat()}",',
             f'    "source": {json.dumps(source)},',
             f'    "years": {json.dumps(window)},',
             '    "method": "weight = province mean seeded acres over the window / sum of MB + SK + AB means",',
             f'    "notes": {json.dumps(notes, ensure_ascii=False)},',
             '    "crops": {']
    items = list(weights.items())
    for i, (key, w) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f'      "{key}": {{"weights": {json.dumps(w["weights"])}, "mean_acres": {json.dumps(w["mean_acres"])}}}{comma}')
    lines += ["    }", "  },", ""]
    return "\n".join(lines) + "\n"


def write_rules(block: str, rules_path: Path = RULES_PATH) -> None:
    """Replace (or insert before "outputs") the prairie_acreage_weights block, keeping the rest as is."""
    text = rules_path.read_text(encoding="utf-8")
    m = re.search(rf'^  "{BLOCK_KEY}": \{{.*?^  \}},\n\n', text, flags=re.S | re.M)
    if m:
        text = text[:m.start()] + block + text[m.end():]
    else:
        anchor = '  "outputs": {'
        if anchor not in text:
            raise ValueError(f"{rules_path.name}: no outputs block to insert {BLOCK_KEY} before")
        text = text.replace(anchor, block + anchor, 1)
    json.loads(text)                                   # never write an invalid rules file
    rules_path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the weights without writing the rules file")
    args = ap.parse_args(argv)
    path = source_csv()
    acres, window, notes = read_acres(path)
    weights = compute_weights(acres, window)
    print(f"StatCan 32-10-0359 seeded area {window[0]}-{window[-1]} ({path})")
    print(f"  {'crop_key':<17}" + "".join(f"{c:>9}" for c in RULES.raw['statcan_acreage']['provinces']))
    for key, w in weights.items():
        print(f"  {key:<17}" + "".join(f"{v:>9.1%}" for v in w["weights"].values()))
    for n in notes:
        print(f"  note: {n}")
    if not args.dry_run:
        write_rules(block_json(weights, window, path, notes))
        print(f"  wrote {BLOCK_KEY} to {RULES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
