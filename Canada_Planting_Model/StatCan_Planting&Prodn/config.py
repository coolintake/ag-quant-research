"""Configuration for the StatCan release-table pipeline (plantings now, production later).

Everything that describes *where* data lives and *which* tabs belong to which topic is here, so a
new topic (e.g. production) is enabled by editing ``TOPICS``, not the extract/transform code.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths (override the workbook with the STATCAN_WORKBOOK environment variable)
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_WORKBOOK = Path(os.environ.get(
    "STATCAN_WORKBOOK",
    r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\GT\Useful\StatCan Acres & Prodn.xlsx",
))
DATA_DIR = PROJECT_ROOT / "data"
DUCKDB_PATH = DATA_DIR / "crop_data.duckdb"
PARQUET_DIR = DATA_DIR / "parquet"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# --------------------------------------------------------------------------- #
# Release calendar
# --------------------------------------------------------------------------- #
MONTHS: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_NAMES = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June", 7: "July",
               8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}
MONTH_ABBR = {k: v[:3] for k, v in MONTH_NAMES.items()}

# Tab names seen in the workbook: "Mar2015", "March2019", "June2026" (month first) and
# "2022July" (year first, production tabs). Both orders are accepted.
SHEET_NAME_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*(?P<month>[A-Za-z]+)\s*(?P<year>(?:19|20)\d{2})\s*$"),
    re.compile(r"^\s*(?P<year>(?:19|20)\d{2})\s*(?P<month>[A-Za-z]+)\s*$"),
)

# --------------------------------------------------------------------------- #
# Table parsing
# --------------------------------------------------------------------------- #
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})")             # first 4-digit year in a header
COMPARISON_RE = re.compile(r"%|\bto\b|change", re.IGNORECASE)  # "% change", "2019 to 2020"
PRELIMINARY_RE = re.compile(r"\{\s*p\s*\}|(?<=\d)p\b|(?<=\d)p$", re.IGNORECASE)   # "{p}", "20151p"
FOOTNOTE_RE = re.compile(r"\{[^}]*\}")                     # "{1}", "{2}", "{p}"
TRAILING_FOOTNOTE_DIGITS_RE = re.compile(r"(?<=[A-Za-z)])\d{1,2}$")   # "Winter wheat3", "...remaining)2"
MIN_YEAR_HEADERS = 2                                       # a header row has at least this many year columns
MAX_HEADER_SCAN_ROWS = 10

# unit text in the row under the header (footnotes stripped, lower case) -> (value column, multiplier
# to that column's unit). Some releases report raw units, e.g. Sep2017 'metric tonnes{1}'.
UNIT_MAP: dict[str, tuple[str, float]] = {
    "thousands of acres": ("acres_thousand", 1.0),
    "acres": ("acres_thousand", 0.001),
    "thousands of hectares": ("acres_thousand", 2.47105),
    "hectares": ("acres_thousand", 2.47105 / 1000),
    "thousands of tonnes": ("tonnes_thousand", 1.0),
    "thousands of metric tonnes": ("tonnes_thousand", 1.0),
    "metric tonnes": ("tonnes_thousand", 0.001),
    "tonnes": ("tonnes_thousand", 0.001),
}

# Optional explicit renames applied after normalization (normalized name -> canonical name).
# Normalization already handles footnotes, spacing and case ("Fall Rye", "Fall rye3" -> "Fall rye").
CROP_ALIASES: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Topics
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TopicSpec:
    """Which tabs belong to a topic and where its records are loaded.

    Sheets are taken from the block strictly between ``start_marker`` and ``end_marker`` (either may
    be None = workbook start / end), must have a parseable release name, and must pass the month and
    year filters.
    """
    name: str
    metric_type: str
    table: str
    value_column: str
    enabled: bool
    start_marker: str | None
    end_marker: str | None
    release_months: frozenset[int] = field(default_factory=frozenset)   # empty = any month
    first_release_year: int | None = None
    last_release_year: int | None = None


TOPICS: dict[str, TopicSpec] = {
    # Phase 1: seeded-area (planting) releases, March and June, 2015-2026.
    # The workbook places them between the "<--Production" and "<--Seeded" marker tabs.
    "planting": TopicSpec(
        name="planting", metric_type="Planting", table="plantings_estimates", value_column="acres_thousand",
        enabled=True, start_marker="<--Production", end_marker="<--Seeded",
        release_months=frozenset({3, 6}), first_release_year=2015, last_release_year=2026,
    ),
    # Phase 2 (disabled until the production tabs are complete): July / September / November-December
    # production releases, placed before the "<--Production" marker. Enable with enabled=True or
    # `python main.py run --topics planting production`.
    "production": TopicSpec(
        name="production", metric_type="Production", table="production_estimates", value_column="tonnes_thousand",
        enabled=False, start_marker=None, end_marker="<--Production",
        release_months=frozenset({7, 9, 11, 12}),
    ),
}
