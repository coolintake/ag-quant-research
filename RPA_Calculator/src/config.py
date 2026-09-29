"""Phase 1.1 project configuration for the Western Canada Return Per Acre (RPA) calculator.

Tiers:
    1. Dynamic market vectors  ($/lb N/P/K, live futures, local basis)
    2. Static agronomic refs   (nutrient rates lbs/ac, seeding rates, target yields)
    3. Processing engine       (revenues, variable costs, breakevens)

This module holds paths, output specs and the MVS commodity -> soil zone rules.
Run it directly to perform the startup environment check.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

INPUT_PDF_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\RPA_Calculator\RPA_SK_Input")

OUTPUT_DIR = PROJECT_ROOT / "output"
PROVINCE_OUTPUT_DIR = OUTPUT_DIR / "sk"
INTERMEDIATE_DIR = PROVINCE_OUTPUT_DIR / "intermediate"
MVS_DIR = PROVINCE_OUTPUT_DIR / "mvs"
SCENARIO_DIR = PROVINCE_OUTPUT_DIR / "scenarios"   # Phase 2 Tier 1 market scenarios

OUTPUT_STEM = "sk_historical_mvs_2019_2026"
OUTPUT_JSON = MVS_DIR / f"{OUTPUT_STEM}.json"   # machine readable
OUTPUT_XLSX = MVS_DIR / f"{OUTPUT_STEM}.xlsx"   # human audit / review
OUTPUT_CSV = MVS_DIR / f"{OUTPUT_STEM}.csv"     # flat analysis

# Phase 1.2 intermediate: raw PDF table structures (pre-normalization)
RAW_TABLES_JSON = INTERMEDIATE_DIR / "raw_extracted_tables.json"

YEAR_START = 2019
YEAR_END = 2026
YEARS = tuple(range(YEAR_START, YEAR_END + 1))

# Input file naming, e.g. RPA-SK-2024.pdf
PDF_YEAR_PATTERN = re.compile(r"(20\d{2})")

# ---------------------------------------------------------------------------
# Soil zones
# ---------------------------------------------------------------------------
BROWN = "Brown"
DARK_BROWN = "Dark Brown"
BLACK = "Black"
SOIL_ZONES = (BROWN, DARK_BROWN, BLACK)

# ---------------------------------------------------------------------------
# MVS commodity -> soil zone mapping
#   zones   : zone(s) the commodity's MVS figures are drawn from
#   method  : "single" = take that zone as-is; "blend" = simple (equal-weight)
#             average across the listed zones
#   variety : crop class the zone budget refers to, where it matters
#   source_names : crop names as printed in the guides (2019 column headers,
#             2020+ page titles), matched case-insensitively with '*' stripped
#   price_basis : native cash price unit, "$/bu" or "c/lb" (pulses). Pulses also
#             carry bu/ac and $/bu via PULSE_LBS_PER_BU.
# ---------------------------------------------------------------------------
MVS_COMMODITY_ZONE_MAP: dict[str, dict] = {
    "Wheat":       {"zones": (BROWN, DARK_BROWN, BLACK), "method": "blend",  "variety": "Hard Red Spring",
                    "source_names": ("Spring Wheat", "Hard Red Spring Wheat")},
    "Canola":      {"zones": (DARK_BROWN,),              "method": "single", "variety": None,
                    "source_names": ("Canola",)},
    "Durum":       {"zones": (BROWN,),                   "method": "single", "variety": "Amber Durum",
                    "source_names": ("Durum", "Amber Durum", "Durum Wheat")},
    "Lentils":     {"zones": (BROWN,),                   "method": "single", "variety": "Red Lentils",
                    "source_names": ("Red Lentils", "Large Red Lentils"), "price_basis": "c/lb"},
    "Peas":        {"zones": (DARK_BROWN,),              "method": "single", "variety": "Yellow Peas",
                    "source_names": ("Yellow Peas", "Edible Yellow Peas"), "price_basis": "c/lb"},
    "Barley":      {"zones": (DARK_BROWN,),              "method": "single", "variety": "Feed Barley",
                    "source_names": ("Feed Barley",)},
    "Malt Barley": {"zones": (DARK_BROWN,),              "method": "single", "variety": None,
                    "source_names": ("Malt Barley",)},
    "Oats":        {"zones": (BLACK,),                   "method": "single", "variety": None,
                    "source_names": ("Oats",)},
    "Flax":        {"zones": (DARK_BROWN,),              "method": "single", "variety": None,
                    "source_names": ("Flax",)},
    "Rye":         {"zones": (BROWN, DARK_BROWN),        "method": "blend",  "variety": "Hybrid Fall Rye",
                    "source_names": ("Hybrid Fall Rye",)},
    "Soybeans":    {"zones": (BLACK,),                   "method": "single", "variety": None,
                    "source_names": ("Soybean", "Soybeans")},
}


def _validate_zone_map() -> None:
    for commodity, rule in MVS_COMMODITY_ZONE_MAP.items():
        zones = rule["zones"]
        unknown = set(zones) - set(SOIL_ZONES)
        if unknown:
            raise ValueError(f"{commodity}: unknown soil zone(s) {sorted(unknown)}")
        if rule["method"] == "single" and len(zones) != 1:
            raise ValueError(f"{commodity}: 'single' method needs exactly one zone")
        if rule["method"] == "blend" and len(zones) < 2:
            raise ValueError(f"{commodity}: 'blend' method needs two or more zones")
        if not rule.get("source_names"):
            raise ValueError(f"{commodity}: no source_names")
        if rule.get("price_basis", "$/bu") not in ("$/bu", "c/lb"):
            raise ValueError(f"{commodity}: price_basis must be '$/bu' or 'c/lb'")


for _rule in MVS_COMMODITY_ZONE_MAP.values():
    _rule.setdefault("price_basis", "$/bu")

PRICE_BASES = ("$/bu", "c/lb")
PULSE_LBS_PER_BU = 60.0   # lb/bu used for pulse bu <-> lb conversions


def is_pulse(commodity: str) -> bool:
    return MVS_COMMODITY_ZONE_MAP[commodity]["price_basis"] == "c/lb"


_validate_zone_map()


# ---------------------------------------------------------------------------
# Startup check
# ---------------------------------------------------------------------------
def detect_input_pdfs(input_dir: Path = INPUT_PDF_DIR) -> dict[int, Path]:
    """Return {year: pdf_path} for PDFs in input_dir whose name holds a 2019-2026 year."""
    found: dict[int, Path] = {}
    for pdf in sorted(input_dir.glob("*.pdf")):
        match = PDF_YEAR_PATTERN.search(pdf.stem)
        if match and int(match.group(1)) in YEARS:
            year = int(match.group(1))
            if year in found:
                print(f"  WARNING: duplicate file for {year}: {pdf.name} (keeping {found[year].name})")
                continue
            found[year] = pdf
    return found


def verify_environment(input_dir: Path = INPUT_PDF_DIR) -> dict[int, Path]:
    """Confirm the input folder exists, list detected 2019-2026 PDFs, flag missing years.

    Creates the output folders if absent. Raises FileNotFoundError if input_dir is missing.
    """
    print(f"RPA input folder : {input_dir}")
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")

    for folder in (INTERMEDIATE_DIR, MVS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    print(f"Output folder    : {PROVINCE_OUTPUT_DIR}")

    pdfs = detect_input_pdfs(input_dir)
    print(f"Detected {len(pdfs)} PDF(s) for {YEAR_START}-{YEAR_END}:")
    for year, path in sorted(pdfs.items()):
        print(f"  {year}  {path.name}  ({path.stat().st_size / 1_048_576:.1f} MB)")

    missing = [y for y in YEARS if y not in pdfs]
    if missing:
        print(f"  MISSING years: {', '.join(map(str, missing))}")
    return pdfs


if __name__ == "__main__":
    verify_environment()
