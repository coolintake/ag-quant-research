"""Central configuration for the Canada_Crop_Prices ingestion pipeline.

Every filesystem path, URL, and domain mapping used by the package lives here.
No other module may hardcode an absolute path or a connection string; they all
import from this file. Paths are resolved relative to the project root (the
folder containing this file), so the project can be moved or cloned freely.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- #
# Paths (all relative to the project root)
# --------------------------------------------------------------------------- #
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
RAW_DIR: Final[Path] = DATA_DIR / "raw"
PARQUET_DIR: Final[Path] = DATA_DIR / "parquet"
DUCKDB_PATH: Final[Path] = DATA_DIR / "crop_prices.duckdb"

# Raw snapshot naming: data/raw/YYYY-MM-DD_pdq.json. A second, different PDQ
# import on the same day is kept as data/raw/YYYY-MM-DD_HHMM_pdq.json.
RAW_SNAPSHOT_SUFFIX: Final[str] = "_pdq.json"

# --------------------------------------------------------------------------- #
# PDQ API
# --------------------------------------------------------------------------- #
PDQ_BASE_URL: Final[str] = "https://www.pdqinfo.ca/api/v2"
# The key is part of the URL path. Override with the PDQ_API_KEY env var.
PDQ_API_KEY: Final[str] = os.environ.get("PDQ_API_KEY", "0a67610a92b26118b52a187228afb767")
PDQ_URL: Final[str] = f"{PDQ_BASE_URL}/{PDQ_API_KEY}"

HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
HTTP_MAX_RETRIES: Final[int] = 4
HTTP_BACKOFF_FACTOR: Final[float] = 1.5          # sleeps 0, 1.5, 3, 6 ... seconds
HTTP_RETRY_STATUSES: Final[tuple[int, ...]] = (429, 500, 502, 503, 504)
HTTP_USER_AGENT: Final[str] = "Canada_Crop_Prices/1.0 (+price ingestion)"

# Format of PDQ's top-level "prices_imported_at" field.
PDQ_IMPORTED_AT_FORMAT: Final[str] = "%Y-%m-%d %H:%M"

# --------------------------------------------------------------------------- #
# Zones and provinces
# --------------------------------------------------------------------------- #
# PDQ zone name -> PDQ zone id (used to validate API payloads).
PDQ_ZONE_IDS: Final[dict[str, int]] = {
    "PEACE": 1,
    "N ALTA": 2,
    "S ALTA": 3,
    "NW SASK": 4,
    "SW SASK": 5,
    "NE SASK": 6,
    "SE SASK": 7,
    "W MAN": 8,
    "E MAN": 9,
}

# PDQ zone -> province code, for StatCan (provincial) alignment.
ZONE_TO_PROVINCE: Final[dict[str, str]] = {
    "PEACE": "AB",
    "N ALTA": "AB",
    "S ALTA": "AB",
    "NW SASK": "SK",
    "SW SASK": "SK",
    "NE SASK": "SK",
    "SE SASK": "SK",
    "W MAN": "MB",
    "E MAN": "MB",
}

PROVINCES: Final[tuple[str, ...]] = ("AB", "SK", "MB")

# Optional zone weights for "weighted" provincial averages (e.g. seeded acres
# by zone). Zones missing from this dict, and non-PDQ rows with no zone, get
# weight 1.0. Leave empty to make "weighted" equal to an equal-zone average.
ZONE_WEIGHTS: Final[dict[str, float]] = {}

# --------------------------------------------------------------------------- #
# Commodities
# --------------------------------------------------------------------------- #
# Raw PDQ commodity string -> standardized crop symbol.
PDQ_COMMODITY_MAP: Final[dict[str, str]] = {
    "1 CWRS 13.5": "CWRS",
    "1 CWAD 13.0": "DURUM",
    "1 CDA CANOLA": "CANOLA",
    "2 YELLOW PEAS": "Y_PEAS",
    "1 CPSR 11.5": "CPSR",
}

# Every crop symbol allowed in unified_cash_prices -> kilograms per bushel.
# Used to derive the missing unit when a manual CSV supplies only $/t or $/bu.
# To add a crop, add it here.
CROP_KG_PER_BUSHEL: Final[dict[str, float]] = {
    "CWRS": 27.2155,       # 60 lb
    "CPSR": 27.2155,
    "DURUM": 27.2155,
    "CANOLA": 22.6796,     # 50 lb
    "Y_PEAS": 27.2155,
    "G_PEAS": 27.2155,
    "R_LENTILS": 27.2155,
    "G_LENTILS": 27.2155,
    "CHICKPEAS": 27.2155,
    "SOYBEANS": 27.2155,
    "FLAX": 25.4012,       # 56 lb
    "BARLEY": 21.7724,     # 48 lb
    "FEED_BARLEY": 21.7724,
    "MALT_BARLEY": 21.7724,
    "OATS": 15.4221,       # 34 lb
    "CORN": 25.4012,       # 56 lb
}

# Allowed values of unified_cash_prices.source.
SOURCES: Final[tuple[str, ...]] = ("PDQ", "BARCHART", "CME", "MANUAL")
MANUAL_SOURCES: Final[tuple[str, ...]] = ("BARCHART", "CME", "MANUAL")  # CSV-importable

# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #
MONTHS: Final[tuple[str, ...]] = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)
MONTH_TO_NUM: Final[dict[str, int]] = {m: i for i, m in enumerate(MONTHS, start=1)}

# --------------------------------------------------------------------------- #
# Pricing windows (model_crop_wallet_step1.md, section 3.2)
# --------------------------------------------------------------------------- #
# name -> ((start_month, start_day, year_offset), (end_month, end_day, year_offset))
# year_offset is relative to the planting (crop) year Y, so Run 1 spans
# Dec 15 of Y-1 to Jan 15 of Y. Both endpoints are inclusive.
PRICE_WINDOWS: Final[dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]]] = {
    "RUN1": ((12, 15, -1), (1, 15, 0)),
    "RUN2": ((1, 15, 0), (2, 15, 0)),
    "RUN3": ((2, 15, 0), (3, 15, 0)),
}

# New-crop delivery months, all in delivery_year == planting year. Used for
# crops without a target month below, and as the fallback when a target month
# has no quotes in a window.
NEW_CROP_DELIVERY_MONTHS: Final[tuple[str, ...]] = ("AUG", "SEP", "OCT", "NOV", "DEC")

# Per-crop target delivery month, aligned with the futures contract used for
# each crop: December (Z) for cereals, corn and peas, November (X) for oilseeds.
CROP_TARGET_DELIVERY_MONTHS: Final[dict[str, tuple[str, ...]]] = {
    "CWRS": ("DEC",),
    "CPSR": ("DEC",),
    "DURUM": ("DEC",),
    "CORN": ("DEC",),
    "Y_PEAS": ("DEC",),
    "CANOLA": ("NOV",),
    "SOYBEANS": ("NOV",),
}

# --------------------------------------------------------------------------- #
# DuckDB table names
# --------------------------------------------------------------------------- #
RAW_TABLE: Final[str] = "pdq_raw_prices"
UNIFIED_TABLE: Final[str] = "unified_cash_prices"
