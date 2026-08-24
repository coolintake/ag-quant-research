"""
cgc_engine.py
==============
All mathematical logic, schema normalization, and metric calculations for
CGC Grain Statistics Weekly (GSW) analytics, in one file so it's easy to
scan top-to-bottom: constants -> schema normalization -> metric engine ->
seasonal anomaly / bottleneck tagging.

Ground truth for every formula here is CGC_DB.ipynb. All 4 previously
verified bug fixes are retained (marked inline as BUG FIX 1/3/4 -- item 2,
the export formula itself, was already correct and needed no fix).

No network or file I/O happens in this module -- see ingestion.py for that.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("cgc_engine")

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════

class CGCAnalyticsError(Exception):
    """Base class for all explicit errors raised by this codebase."""


class SchemaError(CGCAnalyticsError):
    """Raised when a GSW CSV's columns cannot be confidently mapped onto
    CANONICAL_COLUMNS (ambiguous or unrecognized layout)."""


class DownloadError(CGCAnalyticsError):
    """Raised when a GSW crop-year CSV cannot be downloaded and no usable
    local/cached fallback exists."""


class CapacityDataError(CGCAnalyticsError):
    """Raised when the licensed-capacity workbook is missing a required
    sheet or column."""


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — SCHEMA / CONSTANTS (ground truth: CGC_DB.ipynb)
# ═══════════════════════════════════════════════════════════════════════════

CANONICAL_COLUMNS: List[str] = [
    "grain_week", "crop_year", "week_ending_date", "worksheet",
    "metric", "period", "grain", "grade", "region", "Ktonnes",
]

CROP_YEAR_PATTERN = re.compile(r"^\d{4}[-/]\d{2,4}$")
GRAIN_WEEK_MIN, GRAIN_WEEK_MAX = 1, 53
SEASON_START_WEEK = 1  # first grain_week a crop year is expected to start at

STOCK_WORKSHEETS: List[str] = ["Terminal Stocks", "Primary", "Process"]
EXCLUDED_GRAIN_PATTERN = r"(?i)buckwheat|imported"

TERMINAL_LOCATIONS: List[str] = [
    "Vancouver", "Prince Rupert", "Churchill",
    "Thunder Bay", "Bay & Lakes", "St. Lawrence",
]

# GSW terminal-region name -> licensed-capacity Station name(s).
REGION_STATION_MAP: Dict[str, List[str]] = {
    "Vancouver":     ["VANCOUVER", "SURREY"],
    "Prince Rupert": ["PRINCE RUPERT"],
    "Churchill":     ["CHURCHILL"],
    "Thunder Bay":   ["THUNDER BAY"],
    "Bay & Lakes":   ["GODERICH", "HAMILTON", "OSHAWA", "PICTON",
                       "PORT COLBORNE", "SARNIA", "WINDSOR"],
    "St. Lawrence":  ["BAIE COMEAU", "MONTREAL", "QUEBEC", "SOREL",
                       "JOHNSTOWN", "HALIFAX"],
}

PORT_CORRIDOR_MAP: Dict[str, str] = {
    "Vancouver": "Pacific", "Prince Rupert": "Pacific", "Churchill": "Churchill",
    "Thunder Bay": "Thunder Bay", "Bay & Lakes": "St. Lawrence", "St. Lawrence": "St. Lawrence",
}

DEFAULT_BASE_URL = (
    "https://www.grainscanada.gc.ca/en/grain-research/statistics/"
    "grain-statistics-weekly/{year}/gsw-shg-en.csv"
)

# CGC's crop year starts in early August (confirmed: week 1 of 2025-26 was
# dated 2025-08-10) and is never a hardcoded "current year" string -- that
# required a manual code edit every August, and this exact class of bug
# (silently missing the new season) is what prompted this fix.
CROP_YEAR_SEASON_START_MONTH: int = 8

# How far back the default historical crop-year window starts. Unlike the
# current year, this is a deliberate one-time scope decision, not
# something that goes stale with the calendar -- it doesn't need to be
# dynamic, just named so it's easy to find if the window is ever widened.
EARLIEST_HISTORICAL_CROP_YEAR_START: int = 2021


def current_crop_year(today: Optional[date] = None) -> str:
    """The short 'YYYY-YY' crop year containing `today` (defaults to the
    actual current date), based on the season starting in August.

    >>> current_crop_year(date(2026, 8, 22))
    '2026-27'
    >>> current_crop_year(date(2026, 3, 1))
    '2025-26'
    """
    today = today or date.today()
    start_year = today.year if today.month >= CROP_YEAR_SEASON_START_MONTH else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def default_historical_crop_years(today: Optional[date] = None) -> List[str]:
    """Every crop year from EARLIEST_HISTORICAL_CROP_YEAR_START up to (but
    excluding) the current in-progress season -- i.e. every season that's
    fully complete. Computed fresh from `today` so this list never needs a
    manual yearly update, and a completed season is never silently dropped
    from a full cache rebuild just because it isn't 'current' anymore.
    """
    current_start_year = int(current_crop_year(today).split("-")[0])
    return [f"{y}-{str(y + 1)[-2:]}" for y in range(EARLIEST_HISTORICAL_CROP_YEAR_START, current_start_year)]


DEFAULT_CROP_YEARS: List[str] = default_historical_crop_years()
DEFAULT_CURRENT_YEAR: str = current_crop_year()
KNOWN_SCHEMA_QUIRK_YEARS: List[str] = ["2025-26"]

UTIL_RED_THRESHOLD: float = 85.0
UTIL_YELLOW_THRESHOLD: float = 75.0
VELOCITY_TARGET_THRESHOLD: float = 0.15
MIN_MATERIAL_STOCKS_KTONNES: float = 0.0  # opt-in floor; 0 = disabled

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2b — DASHBOARD COMMODITY / REGION CONFIG (new: multi-commodity
# stacked capacity + cumulative pacing views)
# ═══════════════════════════════════════════════════════════════════════════

# The 6 core commodities tracked on the dashboard. Fixed set -- never add
# or infer additional commodities here.
CORE_COMMODITIES: List[str] = ["Wheat", "Durum", "Canola", "Soybeans", "Barley", "Oats"]

COMMODITY_COLORS: Dict[str, str] = {
    "Wheat":    "#E69F00",
    "Durum":    "#D55E00",
    "Canola":   "#F0E442",
    "Soybeans": "#009E73",
    "Barley":   "#56B4E9",
    "Oats":     "#CC79A7",
}

# Primary-elevator province grouping. Ground truth (CGC_Capacity.xlsb):
# Province values there are full names ("Alberta", "Saskatchewan", ...).
# GSW's own `region` column for the 'Primary' worksheet is assumed to
# carry the same or an abbreviated form -- both are mapped defensively.
# NOTE: this assumption is UNVERIFIED against a live GSW download (network
# access was unavailable while building this) -- see `stocks_by_node`'s
# docstring for the graceful-degradation behavior if it's wrong.
PRIMARY_PROVINCE_MAP: Dict[str, str] = {
    "Alberta": "AB", "AB": "AB",
    "Saskatchewan": "SK", "SK": "SK",
    "Manitoba": "MB", "MB": "MB",
    "British Columbia": "BC/Peace", "BC": "BC/Peace",
}
PRIMARY_PROVINCE_ORDER: List[str] = ["AB", "SK", "MB", "BC/Peace", "Western Canada Total"]

# Separate from PRIMARY_PROVINCE_MAP: Producer Deliveries uses plain
# province codes (SK/AB/MB/BC), not the "BC/Peace" capacity-context label
# -- that label was specific to the licensed-capacity workbook's Peace
# River grouping, which has no bearing on a GSW stocks/flow metric like
# deliveries. Confirmed against a live 2025-26 GSW export: worksheet==
# 'Primary', metric=='Deliveries' region values are exactly the same 4
# provinces as Primary stocks (Alberta/Saskatchewan/Manitoba/British
# Columbia), so the underlying province set is identical -- only the
# display label for BC differs between the two features.
DELIVERY_PROVINCE_MAP: Dict[str, str] = {
    "Alberta": "AB", "Saskatchewan": "SK", "Manitoba": "MB", "British Columbia": "BC",
}
DELIVERY_PROVINCE_ORDER: List[str] = ["SK", "AB", "MB", "BC"]

# Process-elevator east/west grouping. CGC_Capacity.xlsb currently has no
# Quebec 'Process' rows, but Quebec is still mapped to Eastern Process so
# it's picked up automatically if/when that capacity is added or if GSW
# stocks report Quebec process activity ahead of the capacity workbook.
PROCESS_REGION_MAP: Dict[str, str] = {
    "Alberta": "Western Process", "AB": "Western Process",
    "Saskatchewan": "Western Process", "SK": "Western Process",
    "Manitoba": "Western Process", "MB": "Western Process",
    "British Columbia": "Western Process", "BC": "Western Process",
    "Ontario": "Eastern Process", "ON": "Eastern Process",
    "Quebec": "Eastern Process", "QC": "Eastern Process",
}
PROCESS_REGION_ORDER: List[str] = ["Western Process", "Eastern Process"]

# Collapses the same provinces as PROCESS_REGION_MAP into a single national
# 'Process Elevators' bucket -- used by get_executive_summary /
# get_regional_utilization_matrix, which report Process Elevators as one
# national segment (not split East/West like the dashboard chart does).
PROCESS_NATIONAL_MAP: Dict[str, str] = {k: "Process Elevators" for k in PROCESS_REGION_MAP}

# Segment/node combinations that GSW structurally never reports stocks for
# -- confirmed against a live 2025-26 GSW export: the 'Process' worksheet's
# `region` values are only ever Alberta/Saskatchewan/Manitoba/British
# Columbia, never Ontario or Quebec. Per the Canada Grain Act, eastern
# primary elevators and grain dealers are largely exempt from CGC
# licensing, so this isn't a data gap to fix -- it's a real, permanent
# absence that the dashboard should label honestly rather than show as a
# misleading 0.
UNREPORTED_NODES: Dict[str, set] = {
    "process_east_west": {"Eastern Process"},
}

# The GSW `grain` column doesn't always match the display name used on the
# dashboard. Confirmed against a live 2025-26 GSW export: durum is always
# reported as 'Amber Durum', never plain 'Durum'.
GRAIN_NAME_TO_RAW: Dict[str, str] = {
    "Durum": "Amber Durum",
}
RAW_TO_GRAIN_NAME: Dict[str, str] = {v: k for k, v in GRAIN_NAME_TO_RAW.items()}


def to_raw_grain_name(display_name: str) -> str:
    """Display commodity name (e.g. 'Durum') -> raw GSW `grain` value
    (e.g. 'Amber Durum'). Identity for names that already match."""
    return GRAIN_NAME_TO_RAW.get(display_name, display_name)


def to_display_grain_name(raw_name: str) -> str:
    """Raw GSW `grain` value -> display commodity name. Identity for names
    that already match."""
    return RAW_TO_GRAIN_NAME.get(raw_name, raw_name)


@dataclass(frozen=True)
class OutflowDefinition:
    """'Outflow' definition used by velocity, stocks-to-discharge, and
    seasonal-anomaly metrics. Strictly reproduces CGC_DB.ipynb's validated
    export formula (Chunks #1/#2/#4, identical in all three):
        Terminal Exports (metric='Exports')
      + Primary Shipment Distribution (metric='Shipment Distribution',
                                        region='Export Destinations')
    both on period == 'Crop Year' (cumulative YTD).
    """
    period: str = "Crop Year"
    masks: List[Dict[str, str]] = field(default_factory=lambda: [
        {"worksheet": "Terminal Exports", "metric": "Exports"},
        {"worksheet": "Primary Shipment Distribution",
         "metric": "Shipment Distribution", "region": "Export Destinations"},
    ])

    def select(self, df: pd.DataFrame) -> pd.DataFrame:
        parts = []
        for m in self.masks:
            cond = df["period"] == self.period
            for col, val in m.items():
                cond &= df[col] == val
            parts.append(df[cond])
        return pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]

    def aggregate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Cumulative Ktonnes grouped by ['grain', 'crop_year', 'grain_week']."""
        sliced = self.select(df)
        return (
            sliced.groupby(["grain", "crop_year", "grain_week"], as_index=False)["Ktonnes"]
            .sum()
            .rename(columns={"Ktonnes": "cum_ktonnes"})
        )


DEFAULT_OUTFLOW_DEFINITION = OutflowDefinition()


def normalize_crop_year(value: str) -> str:
    """Canonicalize a crop-year label to the short 'YYYY-YY' form
    ('2023-2024' -> '2023-24'; '2023-24' stays '2023-24'), matching
    DEFAULT_CROP_YEARS / DEFAULT_CURRENT_YEAR and the download URLs.
    """
    s = str(value).strip()
    m = re.match(r"^(\d{4})[-/](\d{2,4})$", s)
    if not m:
        return s
    start, end = m.group(1), m.group(2)
    if len(end) == 4:
        end = end[2:]
    return f"{start}-{end}"


def crop_year_start_int(crop_year: str) -> int:
    """The starting year of a normalized 'YYYY-YY' crop-year label, as an int.
    >>> crop_year_start_int("2025-26")
    2025
    """
    return int(normalize_crop_year(crop_year).split("-")[0])


def crop_years_between(start_crop_year: str, end_crop_year: str) -> List[str]:
    """All crop-year labels from `start_crop_year` (inclusive) up to but
    excluding `end_crop_year`, assuming consecutive seasons. Used to
    determine which seasons need a forced re-fetch after one or more
    crop-year rollovers have happened since the last run -- e.g. if the
    code wasn't run again until two seasons later, both the season that
    was "current" at last run AND the season that was skipped entirely
    need to be caught up, not just the most recent one.

    Returns an empty list if `start_crop_year` is not earlier than
    `end_crop_year` (e.g. clock skew, or nothing to catch up).
    """
    start = crop_year_start_int(start_crop_year)
    end = crop_year_start_int(end_crop_year)
    return [f"{y}-{str(y + 1)[-2:]}" for y in range(start, end)]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — SCHEMA NORMALIZATION (BUG FIX 1: content-sniffed column order,
# never a blind positional swap based on column count alone)
# ═══════════════════════════════════════════════════════════════════════════

_norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
_CANON_NORM = {_norm(c): c for c in CANONICAL_COLUMNS}


def _fraction_matching(series: pd.Series, pattern: re.Pattern) -> float:
    sample = series.dropna().astype(str).head(50)
    return 0.0 if sample.empty else sample.str.match(pattern).mean()


def _fraction_week_like(series: pd.Series) -> float:
    sample = pd.to_numeric(series, errors="coerce").dropna().head(50)
    if sample.empty:
        return 0.0
    return ((sample >= GRAIN_WEEK_MIN) & (sample <= GRAIN_WEEK_MAX) & (sample % 1 == 0)).mean()


def detect_and_fix_column_order(df: pd.DataFrame, year_label: Optional[str] = None) -> pd.DataFrame:
    """Map an arbitrary incoming GSW column layout onto CANONICAL_COLUMNS.

    BUG FIX 1: only swaps columns 0/1 when there is positive CONTENT
    evidence (crop-year-pattern strings vs. small week integers) -- never
    purely because the column count matches and header names don't.

    Raises
    ------
    SchemaError
        If the column count doesn't match, or content-sniffing is ambiguous.
    """
    incoming_norm = [_norm(c) for c in df.columns]

    if set(incoming_norm) == set(_CANON_NORM.keys()) and len(df.columns) == len(CANONICAL_COLUMNS):
        rename = {orig: _CANON_NORM[n] for orig, n in zip(df.columns, incoming_norm)}
        return df.rename(columns=rename)[CANONICAL_COLUMNS]

    if len(df.columns) != len(CANONICAL_COLUMNS):
        raise SchemaError(
            f"GSW CSV for {year_label or 'unknown year'} has {len(df.columns)} columns "
            f"(expected {len(CANONICAL_COLUMNS)}): {list(df.columns)}. Refusing to guess."
        )

    col0, col1 = df.columns[0], df.columns[1]
    col0_is_year = _fraction_matching(df[col0], CROP_YEAR_PATTERN) > 0.5
    col1_is_year = _fraction_matching(df[col1], CROP_YEAR_PATTERN) > 0.5
    col0_is_week = _fraction_week_like(df[col0]) > 0.5
    col1_is_week = _fraction_week_like(df[col1]) > 0.5
    quirk_hint = f" (matches known quirk year {year_label})" if year_label in KNOWN_SCHEMA_QUIRK_YEARS else ""

    if col0_is_year and col1_is_week:
        logger.warning(
            "Column-order swap detected via content sniffing for %s%s: swapping to "
            "canonical [grain_week, crop_year, ...] order.", year_label or "unknown year", quirk_hint,
        )
        out = df.iloc[:, [1, 0, *range(2, len(df.columns))]].copy()
        out.columns = CANONICAL_COLUMNS
        return out

    if col0_is_week and col1_is_year:
        out = df.copy()
        out.columns = CANONICAL_COLUMNS
        return out

    raise SchemaError(
        f"Could not confidently determine column order for {year_label or 'unknown year'} "
        f"from content alone (col0 year-like={col0_is_year}, week-like={col0_is_week}; "
        f"col1 year-like={col1_is_year}, week-like={col1_is_week}). Inspect the file manually."
    )


def clean_types(df: pd.DataFrame) -> pd.DataFrame:
    """Numeric conversion, whitespace stripping, missing-value fills.
    Does not touch crop_year normalization (see `standardize_and_clean`).

    Handles accounting-style negative notation, e.g. '(1.1)' -> -1.1 --
    confirmed present in live GSW data (a 'Summary' worksheet correction
    row). A naive strip-non-numeric-characters approach would silently
    drop the parentheses and turn this into +1.1, flipping the sign.
    """
    out = df.copy()
    raw = out["Ktonnes"].astype(str).str.strip()
    is_paren_negative = raw.str.match(r"^\(.*\)$")
    numeric_str = (
        raw.str.replace(",", "", regex=False)
        .str.replace(r"[()]", "", regex=True)
        .str.replace(r"[^\d.\-]", "", regex=True)
    )
    numeric = pd.to_numeric(numeric_str, errors="coerce")
    numeric = numeric.where(~is_paren_negative, -numeric.abs())
    out["Ktonnes"] = numeric

    out["grain_week"] = pd.to_numeric(out["grain_week"], errors="coerce")
    for col in ("worksheet", "metric", "period", "grain", "region"):
        out[col] = out[col].astype(str).str.strip()
    out["grade"] = out["grade"].fillna("n/a")
    out["region"] = out["region"].replace({"nan": "n/a"}).fillna("n/a")
    out["crop_year"] = out["crop_year"].astype(str).str.strip()
    return out


def standardize_and_clean(raw_df: pd.DataFrame, year_label: str) -> pd.DataFrame:
    """Full pipeline for one raw GSW CSV: column-order detection -> type
    cleaning -> crop_year normalization. Single entry point ingestion.py
    should call for every raw file it reads.
    """
    fixed = detect_and_fix_column_order(raw_df, year_label=year_label)
    cleaned = clean_types(fixed)
    if cleaned["crop_year"].eq("").all() or cleaned["crop_year"].isna().all():
        cleaned["crop_year"] = year_label
    cleaned["crop_year"] = cleaned["crop_year"].map(normalize_crop_year)
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3b — EFFECTIVE PROCESS CAPACITY (Commodity / Industry / Ratios)
#
# The capacity workbook's 'Process' rows aggregate non-interchangeable
# facility types under one 'Capacity (tonnes)' number -- a canola crush
# plant and a barley malting house are not fungible, but were previously
# both counted as generic "Process Elevators" capacity. Optional
# Commodity/Industry/Ratios columns (added to the workbook to fix this)
# let each facility's raw capacity be split into an effective capacity
# per commodity it actually processes.
#
# Parsing rules (confirmed against a live workbook):
#   Commodity blank                     -> not a process-split row, skipped
#   1 commodity,  Ratios blank or '1.0' -> 100% to that commodity
#   N commodities, Ratios == 'equal'    -> 1/N to each (case-insensitive)
#   N commodities, Ratios = comma list  -> positional match
#                   of N numbers summing to ~1.0
# Anything else (count mismatch, bad sum, unparseable) is an ANOMALY:
# never silently guessed at. The row's full raw capacity is assigned to
# UNCLASSIFIED_LABEL instead of a real commodity, so no tonnage is lost or
# double-counted, and the anomaly is reported for manual correction.
# ═══════════════════════════════════════════════════════════════════════════

OPTIONAL_CAPACITY_COLUMNS: List[str] = ["Commodity", "Industry", "Ratios"]
RATIO_SUM_TOLERANCE = 1e-6
UNCLASSIFIED_LABEL = "Unclassified (ratio parse anomaly)"


def parse_commodity_list(commodity_str: str) -> List[str]:
    """'Soybeans, Canola' -> ['Soybeans', 'Canola']. Blank -> []."""
    s = str(commodity_str or "").strip()
    if not s or s.lower() == "nan":
        return []
    return [c.strip() for c in s.split(",") if c.strip()]


def parse_ratios(ratios_str: str, n_commodities: int) -> tuple[Optional[List[float]], Optional[str]]:
    """Parse a Ratios cell for a row with `n_commodities` listed crops.
    Returns (ratio_list, anomaly_reason) -- exactly one is None.
    """
    r = str(ratios_str or "").strip()

    if n_commodities == 1 and (r == "" or r.lower() in ("1.0", "1")):
        return [1.0], None
    if r.lower() == "equal":
        if n_commodities < 1:
            return None, "Ratios='equal' but no commodities listed"
        return [1.0 / n_commodities] * n_commodities, None
    if r == "":
        return None, f"Ratios blank but {n_commodities} commodities listed (ambiguous split)"

    parts = r.split(",")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None, f"Ratios value '{r}' is not numeric, 'equal', or blank"

    if len(vals) != n_commodities:
        return None, (
            f"Ratios has {len(vals)} value(s) ('{r}') but Commodity lists "
            f"{n_commodities} crop(s) -- counts don't match"
        )
    total = sum(vals)
    if abs(total - 1.0) > RATIO_SUM_TOLERANCE:
        return None, f"Ratios '{r}' sum to {total:.4f}, not 1.0"
    return vals, None


def compute_effective_capacity(capacity_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split every Process row's raw capacity across the commodities it
    processes. Rows with no Commodity data (Primary/Terminal, or an
    unclassified Process row) are excluded entirely -- this table is
    Process-only by construction. Commodity names are normalized to
    display form (e.g. 'Amber Durum' -> 'Durum') via `to_display_grain_name`.

    Returns (effective_long, anomalies):
      effective_long : one row per (facility, commodity), with
        'effective_capacity_ktonnes'. Anomalous facilities appear once,
        with their full raw capacity assigned to UNCLASSIFIED_LABEL.
      anomalies : one row per anomalous facility, with 'anomaly_reason',
        for reporting.
    """
    if "Commodity" not in capacity_df.columns:
        return (
            pd.DataFrame(columns=["source_row_id", "Province", "Station", "Company name",
                                   "Elevator type", "Industry", "commodity", "effective_capacity_ktonnes"]),
            pd.DataFrame(),
        )

    effective_rows: List[dict] = []
    anomaly_rows: List[dict] = []

    proc = capacity_df[capacity_df["Elevator type"] == "Process"].reset_index(drop=True)
    for source_row_id, row in proc.iterrows():
        commodity_str = row.get("Commodity", "")
        if not str(commodity_str).strip() or str(commodity_str).strip().lower() == "nan":
            continue

        raw_ktonnes = row["Capacity (Ktonnes)"]
        commodities = parse_commodity_list(commodity_str)
        ratios, anomaly_reason = parse_ratios(row.get("Ratios", ""), len(commodities))

        base = {
            "source_row_id": source_row_id,
            "Province": row.get("Province", ""),
            "Station": row.get("Station", ""),
            "Company name": row.get("Company name", ""),
            "Elevator type": row.get("Elevator type", ""),
            "Industry": row.get("Industry", ""),
            "raw_capacity_ktonnes": raw_ktonnes,
        }

        if anomaly_reason is not None:
            anomaly_rows.append({**base, "Commodity": commodity_str, "Ratios": row.get("Ratios", ""),
                                  "anomaly_reason": anomaly_reason})
            effective_rows.append({**base, "commodity": UNCLASSIFIED_LABEL, "effective_capacity_ktonnes": raw_ktonnes})
            continue

        for commodity, ratio in zip(commodities, ratios):
            effective_rows.append({
                **base,
                "commodity": to_display_grain_name(commodity),
                "effective_capacity_ktonnes": raw_ktonnes * ratio,
            })

    return pd.DataFrame(effective_rows), pd.DataFrame(anomaly_rows)


def effective_process_capacity_by_node(
    capacity_df: pd.DataFrame, region_map: Dict[str, str], commodities: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Effective Process capacity by (node, commodity), where `node` comes
    from mapping `Province` through `region_map` (e.g. PROCESS_REGION_MAP).
    If `commodities` is given, only those commodities' effective capacity
    is included (UNCLASSIFIED_LABEL is always excluded -- it can't be
    attributed to any specific commodity a caller might select).

    Falls back to an empty frame (never raises) if the workbook doesn't
    have the Commodity/Industry/Ratios columns -- callers should detect
    this via .empty and fall back to undifferentiated capacity.

    Returns columns: ['node', 'commodity', 'effective_capacity_ktonnes'].
    """
    effective_long, _ = compute_effective_capacity(capacity_df)
    if effective_long.empty:
        return pd.DataFrame(columns=["node", "commodity", "effective_capacity_ktonnes"])

    df = effective_long[effective_long["commodity"] != UNCLASSIFIED_LABEL].copy()
    if commodities is not None:
        df = df[df["commodity"].isin(commodities)]
    df["node"] = df["Province"].map(region_map)
    df = df[df["node"].notna()]

    return (
        df.groupby(["node", "commodity"], as_index=False)["effective_capacity_ktonnes"].sum()
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — METRIC ENGINE (vectorized: groupby/transform/merge, no
# per-row loops or .apply(axis=1))
# ═══════════════════════════════════════════════════════════════════════════

def stocks_slice(gsw_df: pd.DataFrame) -> pd.DataFrame:
    """Stocks by (grain, crop_year, grain_week, segment). Ground truth:
    CGC_DB.ipynb Chunk #3. Returns columns:
    ['grain','crop_year','grain_week','segment','stocks_ktonnes'].
    """
    df = gsw_df[
        (gsw_df["metric"] == "Stocks")
        & (gsw_df["period"] == "Current Week")
        & (gsw_df["worksheet"].isin(STOCK_WORKSHEETS))
    ].copy()
    df = df[~df["grain"].str.contains(EXCLUDED_GRAIN_PATTERN, regex=True, na=False)]

    segment = df["worksheet"].where(df["worksheet"] != "Primary", "Primary Elevators")
    segment = segment.where(df["worksheet"] != "Process", "Process Elevators")
    segment = segment.mask(df["worksheet"] == "Terminal Stocks", df["region"])
    df["segment"] = segment

    valid_segments = {"Primary Elevators", "Process Elevators", *TERMINAL_LOCATIONS}
    df = df[df["segment"].isin(valid_segments)]

    return (
        df.groupby(["grain", "crop_year", "grain_week", "segment"], as_index=False)["Ktonnes"]
        .sum().rename(columns={"Ktonnes": "stocks_ktonnes"})
    )


def weekly_outflow(
    gsw_df: pd.DataFrame,
    outflow_def: OutflowDefinition = DEFAULT_OUTFLOW_DEFINITION,
    season_start_week: int = SEASON_START_WEEK,
) -> pd.DataFrame:
    """Weekly (non-cumulative) outflow, derived by differencing the
    cumulative 'Crop Year' series.

    BUG FIX 3: a group's first observed week only gets
    `weekly_outflow = cum_ktonnes` if that week equals `season_start_week`
    (the season genuinely just began). If a dataset starts mid-season
    (e.g. week 18), that first row is left NaN instead of being inflated
    to the full year-to-date cumulative total.

    Returns columns: ['grain','crop_year','grain_week','cum_ktonnes',
    'weekly_outflow_ktonnes'].
    """
    cum = outflow_def.aggregate(gsw_df).sort_values(["grain", "crop_year", "grain_week"]).reset_index(drop=True)

    grp = cum.groupby(["grain", "crop_year"])["cum_ktonnes"]
    cum["weekly_outflow_ktonnes"] = grp.diff()

    is_first = grp.transform("cumcount") == 0
    group_min_week = cum.groupby(["grain", "crop_year"])["grain_week"].transform("min")
    starts_at_season_open = group_min_week <= season_start_week

    valid_first = is_first & starts_at_season_open
    invalid_first = is_first & ~starts_at_season_open
    cum.loc[valid_first, "weekly_outflow_ktonnes"] = cum.loc[valid_first, "cum_ktonnes"]
    cum.loc[invalid_first, "weekly_outflow_ktonnes"] = np.nan

    if invalid_first.any():
        for grain, crop_year, wk in cum.loc[invalid_first, ["grain", "crop_year", "grain_week"]].drop_duplicates().itertuples(index=False):
            logger.warning(
                "Partial-season dataset for grain=%s crop_year=%s: first observed "
                "grain_week=%d (expected start=%d). weekly_outflow left NaN, not inflated.",
                grain, crop_year, wk, season_start_week,
            )

    cum["weekly_outflow_ktonnes"] = cum["weekly_outflow_ktonnes"].clip(lower=0)
    return cum


def capacity_utilization(
    stocks_df: pd.DataFrame,
    capacity_by_segment: pd.DataFrame,
    capacity_by_segment_and_grain: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """(Stocks / Licensed Capacity) * 100 by (grain, crop_year, grain_week, segment).

    `capacity_by_segment_and_grain` (columns ['segment','grain','capacity_ktonnes'],
    optional) takes precedence over the flat `capacity_by_segment` total for
    any (segment, grain) pair it explicitly covers -- e.g. Process Elevators
    capacity becomes commodity-specific (canola crush capacity vs. barley
    malting capacity) instead of comparing every commodity against the same
    undifferentiated national total. Segments/grains NOT covered by this
    table fall back to the flat per-segment number, exactly as before.

    IMPORTANT: for a segment where grain-specific data exists at all, the
    caller should include an explicit 0.0 row for every grain that
    genuinely has no classified capacity there (rather than omitting it) --
    otherwise an omitted row silently falls back to the flat total instead
    of correctly showing ~0 capacity for that commodity.
    """
    out = stocks_df.merge(capacity_by_segment, on="segment", how="left")

    if capacity_by_segment_and_grain is not None and not capacity_by_segment_and_grain.empty:
        specific = capacity_by_segment_and_grain.rename(columns={"capacity_ktonnes": "_capacity_specific"})
        out = out.merge(specific, on=["segment", "grain"], how="left")
        out["capacity_ktonnes"] = out["_capacity_specific"].combine_first(out["capacity_ktonnes"])
        out = out.drop(columns=["_capacity_specific"])

    out["capacity_utilization_pct"] = np.where(
        out["capacity_ktonnes"] > 0, out["stocks_ktonnes"] / out["capacity_ktonnes"] * 100.0, np.nan,
    )
    return out


def stocks_to_discharge_ratio(stocks_df: pd.DataFrame, outflow_df: pd.DataFrame) -> pd.DataFrame:
    """Weeks of supply = total stocks / 4-week rolling average weekly outflow."""
    stocks_total = stocks_df.groupby(["grain", "crop_year", "grain_week"], as_index=False)["stocks_ktonnes"].sum()
    outflow = outflow_df.sort_values(["grain", "crop_year", "grain_week"]).copy()
    outflow["outflow_4wk_avg"] = (
        outflow.groupby(["grain", "crop_year"])["weekly_outflow_ktonnes"]
        .transform(lambda s: s.rolling(4, min_periods=1).mean())
    )
    merged = stocks_total.merge(
        outflow[["grain", "crop_year", "grain_week", "outflow_4wk_avg"]],
        on=["grain", "crop_year", "grain_week"], how="left",
    )
    merged["weeks_of_supply"] = np.where(
        merged["outflow_4wk_avg"] > 0, merged["stocks_ktonnes"] / merged["outflow_4wk_avg"], np.nan,
    )
    return merged


def system_velocity(stocks_df: pd.DataFrame, outflow_df: pd.DataFrame) -> pd.DataFrame:
    """Weekly outflow / commercial stocks at national level (outflow isn't
    segment-split in the source data). See `bottleneck_matrix` for how
    this is combined with segment utilization without suppressing local
    signals (BUG FIX 4).
    """
    stocks_total = stocks_df.groupby(["grain", "crop_year", "grain_week"], as_index=False)["stocks_ktonnes"].sum()
    merged = stocks_total.merge(
        outflow_df[["grain", "crop_year", "grain_week", "weekly_outflow_ktonnes"]],
        on=["grain", "crop_year", "grain_week"], how="left",
    )
    merged["velocity"] = np.where(
        merged["stocks_ktonnes"] > 0, merged["weekly_outflow_ktonnes"] / merged["stocks_ktonnes"], np.nan,
    )
    return merged


def stocks_by_node(gsw_df: pd.DataFrame, segment_type: str) -> pd.DataFrame:
    """Stocks by (grain, crop_year, grain_week, node), where `node` is a
    province ('primary_province'), an east/west process bucket
    ('process_east_west'), or a terminal region ('terminal').

    Unlike `stocks_slice` (which collapses Primary/Process worksheets to a
    single national figure for the executive-summary/bottleneck-matrix
    pipeline), this preserves the `region` column's detail -- needed for
    the province- and east/west-level dashboard views.

    IMPORTANT CAVEAT: this assumes the live GSW feed populates `region`
    with a province name for 'Primary'/'Process' worksheet rows. That has
    NOT been verified against a live download in this environment. If
    `region` is unpopulated for these worksheets (e.g. always "n/a"), this
    function returns an EMPTY frame for 'primary_province'/
    'process_east_west' and logs a warning -- capacity bars will still
    render in the dashboard, but stock bars will be empty, making the gap
    visible rather than silently wrong.

    Returns columns: ['grain','crop_year','grain_week','node','stocks_ktonnes'].
    """
    base = gsw_df[(gsw_df["metric"] == "Stocks") & (gsw_df["period"] == "Current Week")]

    if segment_type == "terminal":
        df = base[base["worksheet"] == "Terminal Stocks"].copy()
        df["node"] = df["region"].where(df["region"].isin(TERMINAL_LOCATIONS))
    elif segment_type == "primary_province":
        df = base[base["worksheet"] == "Primary"].copy()
        df["node"] = df["region"].map(PRIMARY_PROVINCE_MAP)
    elif segment_type == "process_east_west":
        df = base[base["worksheet"] == "Process"].copy()
        df["node"] = df["region"].map(PROCESS_REGION_MAP)
    else:
        raise ValueError(f"Unknown segment_type: {segment_type!r} (expected 'primary_province', 'process_east_west', or 'terminal').")

    df = df[df["node"].notna()]
    df = df[~df["grain"].str.contains(EXCLUDED_GRAIN_PATTERN, regex=True, na=False)]
    df["grain"] = df["grain"].replace(RAW_TO_GRAIN_NAME)  # e.g. 'Amber Durum' -> 'Durum'

    if df.empty:
        logger.warning(
            "stocks_by_node(segment_type=%r) produced no rows -- the GSW feed's "
            "'region' column may not carry province-level detail for this "
            "worksheet. Capacity bars will still render; stock bars will be empty.",
            segment_type,
        )
        return pd.DataFrame(columns=["grain", "crop_year", "grain_week", "node", "stocks_ktonnes"])

    out = (
        df.groupby(["grain", "crop_year", "grain_week", "node"], as_index=False)["Ktonnes"]
        .sum().rename(columns={"Ktonnes": "stocks_ktonnes"})
    )

    if segment_type == "primary_province":
        total = out.groupby(["grain", "crop_year", "grain_week"], as_index=False)["stocks_ktonnes"].sum()
        total["node"] = "Western Canada Total"
        out = pd.concat([out, total], ignore_index=True)

    return out


def deliveries_by_province(gsw_df: pd.DataFrame) -> pd.DataFrame:
    """Primary elevator producer deliveries by (grain, crop_year,
    grain_week, node=province), as BOTH a direct weekly flow and the
    cumulative year-to-date total.

    Confirmed against a live 2025-26 GSW export: worksheet=='Primary',
    metric=='Deliveries' has BOTH period=='Current Week' (the weekly flow
    directly, not cumulative) AND period=='Crop Year' (the cumulative YTD
    total, verified monotonically non-decreasing) published directly by
    GSW. Unlike the Exports/Shipment Distribution outflow definition,
    NO DIFFERENCING is needed here -- both figures already exist in the
    source data, so none of `weekly_outflow`'s partial-season handling
    applies. Region values are the same 4 provinces as Primary stocks
    (Alberta/Saskatchewan/Manitoba/British Columbia), mapped via
    DELIVERY_PROVINCE_MAP to plain SK/AB/MB/BC codes.

    Returns columns: ['grain', 'crop_year', 'grain_week', 'node',
    'weekly_delivery_ktonnes', 'cum_delivery_ktonnes'].
    """
    base = gsw_df[(gsw_df["worksheet"] == "Primary") & (gsw_df["metric"] == "Deliveries")].copy()
    base = base[~base["grain"].str.contains(EXCLUDED_GRAIN_PATTERN, regex=True, na=False)]
    base["node"] = base["region"].map(DELIVERY_PROVINCE_MAP)
    base = base[base["node"].notna()]
    base["grain"] = base["grain"].replace(RAW_TO_GRAIN_NAME)  # e.g. 'Amber Durum' -> 'Durum'

    if base.empty:
        logger.warning(
            "deliveries_by_province() produced no rows -- the GSW feed's 'region' "
            "column may not carry province-level detail for the Primary/Deliveries "
            "worksheet in this dataset."
        )
        return pd.DataFrame(columns=["grain", "crop_year", "grain_week", "node",
                                      "weekly_delivery_ktonnes", "cum_delivery_ktonnes"])

    group_cols = ["grain", "crop_year", "grain_week", "node"]
    weekly = (
        base[base["period"] == "Current Week"]
        .groupby(group_cols, as_index=False)["Ktonnes"].sum()
        .rename(columns={"Ktonnes": "weekly_delivery_ktonnes"})
    )
    cum = (
        base[base["period"] == "Crop Year"]
        .groupby(group_cols, as_index=False)["Ktonnes"].sum()
        .rename(columns={"Ktonnes": "cum_delivery_ktonnes"})
    )
    return pd.merge(weekly, cum, on=group_cols, how="outer")


def process_deliveries_national(gsw_df: pd.DataFrame) -> pd.DataFrame:
    """Process elevator producer deliveries (worksheet=='Process',
    metric=='Producer Deliveries'), at NATIONAL level only.

    Confirmed against live 2025-26 GSW data: this metric's `region` field
    is entirely blank for every row -- unlike Primary elevator deliveries,
    it cannot be broken down by province. Both period values ('Current
    Week' and 'Crop Year') are published directly, same as
    `deliveries_by_province`, so no differencing is needed here either.

    Per CGC's own Explanatory Notes, Primary + Process deliveries together
    represent "commercial" (licensed elevator) delivery volume, distinct
    from Producer Car deliveries (explicitly documented as UNLICENSED
    handlings) -- this is why Producer Cars are deliberately excluded from
    the "Total Commercial Deliveries" reconciliation this feeds into.

    Returns columns: ['grain', 'crop_year', 'grain_week',
    'weekly_delivery_ktonnes', 'cum_delivery_ktonnes'].
    """
    base = gsw_df[(gsw_df["worksheet"] == "Process") & (gsw_df["metric"] == "Producer Deliveries")].copy()
    base = base[~base["grain"].str.contains(EXCLUDED_GRAIN_PATTERN, regex=True, na=False)]
    base["grain"] = base["grain"].replace(RAW_TO_GRAIN_NAME)

    group_cols = ["grain", "crop_year", "grain_week"]
    weekly = (
        base[base["period"] == "Current Week"]
        .groupby(group_cols, as_index=False)["Ktonnes"].sum()
        .rename(columns={"Ktonnes": "weekly_delivery_ktonnes"})
    )
    cum = (
        base[base["period"] == "Crop Year"]
        .groupby(group_cols, as_index=False)["Ktonnes"].sum()
        .rename(columns={"Ktonnes": "cum_delivery_ktonnes"})
    )
    return pd.merge(weekly, cum, on=group_cols, how="outer")


def cumulative_pacing_table(
    gsw_df: pd.DataFrame,
    commodity: str,
    crop_year: Optional[str] = None,
    outflow_def: OutflowDefinition = DEFAULT_OUTFLOW_DEFINITION,
    lookback_years: int = 3,
) -> pd.DataFrame:
    """Cumulative YTD outflow (MMT) for one commodity/crop_year, alongside
    the lookback_years historical min/max/average envelope for the same
    grain_weeks. Reuses `OutflowDefinition.aggregate`'s cumulative series
    directly -- no differencing needed for a cumulative chart.

    `crop_year=None` resolves to the latest crop_year present for this
    commodity.

    Returns columns: ['grain','crop_year','grain_week','current_cum_mmt',
    'hist_min_mmt','hist_max_mmt','hist_avg_mmt'].
    """
    cum = outflow_def.aggregate(gsw_df)
    cum = cum[cum["grain"] == to_raw_grain_name(commodity)]
    empty_cols = ["grain", "crop_year", "grain_week", "current_cum_mmt", "hist_min_mmt", "hist_max_mmt", "hist_avg_mmt"]
    if cum.empty:
        return pd.DataFrame(columns=empty_cols)

    all_years: List[str] = sorted(cum["crop_year"].unique())
    crop_year = normalize_crop_year(crop_year) if crop_year is not None else all_years[-1]
    idx = all_years.index(crop_year) if crop_year in all_years else len(all_years)
    hist_pool = all_years[max(0, idx - lookback_years):idx]

    current = cum[cum["crop_year"] == crop_year][["grain_week", "cum_ktonnes"]].rename(
        columns={"cum_ktonnes": "current_cum_mmt"}
    )
    current["current_cum_mmt"] = current["current_cum_mmt"] / 1000.0

    hist = cum[cum["crop_year"].isin(hist_pool)]
    if not hist.empty:
        hist_stats = (
            hist.groupby("grain_week")["cum_ktonnes"]
            .agg(hist_min="min", hist_max="max", hist_avg="mean").reset_index()
        )
        for c in ("hist_min", "hist_max", "hist_avg"):
            hist_stats[c] = hist_stats[c] / 1000.0
        hist_stats = hist_stats.rename(columns={
            "hist_min": "hist_min_mmt", "hist_max": "hist_max_mmt", "hist_avg": "hist_avg_mmt",
        })
    else:
        hist_stats = pd.DataFrame(columns=["grain_week", "hist_min_mmt", "hist_max_mmt", "hist_avg_mmt"])

    out = pd.merge(current, hist_stats, on="grain_week", how="outer").sort_values("grain_week").reset_index(drop=True)
    out.insert(0, "grain", commodity)
    out.insert(1, "crop_year", crop_year)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — SEASONAL ANOMALY & BOTTLENECK MATRIX
# ═══════════════════════════════════════════════════════════════════════════

def seasonal_anomaly(outflow_df: pd.DataFrame, lookback_years: int = 3) -> pd.DataFrame:
    """Current outflow minus N-year historical average for the same
    grain_week, plus a z-score, across every crop year present. Returns
    one row per (grain, crop_year, grain_week) -- i.e. the full weekly
    series, not just a single week.

    Columns: ['grain','grain_week','crop_year','current_outflow_ktonnes',
    'hist_avg','hist_std','anomaly_ktonnes','z_score','hist_pool'].
    """
    all_years: List[str] = sorted(outflow_df["crop_year"].unique())
    results = []
    for i, yr in enumerate(all_years):
        hist_pool = all_years[max(0, i - lookback_years):i]
        if not hist_pool:
            continue
        hist = outflow_df[outflow_df["crop_year"].isin(hist_pool)]
        baseline = (
            hist.groupby(["grain", "grain_week"])["weekly_outflow_ktonnes"]
            .agg(hist_avg="mean", hist_std="std").reset_index()
        )
        cur = outflow_df[outflow_df["crop_year"] == yr][["grain", "grain_week", "weekly_outflow_ktonnes"]] \
            .rename(columns={"weekly_outflow_ktonnes": "current_outflow_ktonnes"})
        merged = cur.merge(baseline, on=["grain", "grain_week"], how="left")
        merged["crop_year"] = yr
        merged["hist_pool"] = ", ".join(hist_pool)
        results.append(merged)

    if not results:
        return pd.DataFrame(columns=[
            "grain", "grain_week", "crop_year", "current_outflow_ktonnes",
            "hist_avg", "hist_std", "anomaly_ktonnes", "z_score", "hist_pool",
        ])

    out = pd.concat(results, ignore_index=True)
    out["anomaly_ktonnes"] = out["current_outflow_ktonnes"] - out["hist_avg"]
    out["z_score"] = np.where(out["hist_std"] > 0, out["anomaly_ktonnes"] / out["hist_std"], np.nan)
    return out


def bottleneck_matrix(
    utilization_df: pd.DataFrame,
    velocity_df: pd.DataFrame,
    util_red: float = UTIL_RED_THRESHOLD,
    util_yellow: float = UTIL_YELLOW_THRESHOLD,
    velocity_target: float = VELOCITY_TARGET_THRESHOLD,
    min_material_stocks_ktonnes: float = MIN_MATERIAL_STOCKS_KTONNES,
) -> pd.DataFrame:
    """Red/Yellow/Green tags per (grain, crop_year, grain_week, segment).

    BUG FIX 4: evaluated PRIMARILY and SUFFICIENTLY on segment-level
    `capacity_utilization_pct` -- national velocity can no longer suppress
    a real local signal (e.g. Vancouver at 90% utilization stays Red even
    if national velocity looks healthy). Velocity is attached only as an
    informational `velocity_context` annotation.

    An optional materiality floor (`min_material_stocks_ktonnes`, default
    0 = disabled) can demote flags for segments with negligible absolute
    stock volume; it is opt-in so it can never silently reproduce the
    suppression bug this function replaced.
    """
    merged = utilization_df.merge(
        velocity_df[["grain", "crop_year", "grain_week", "velocity"]],
        on=["grain", "crop_year", "grain_week"], how="left",
    )
    merged["corridor"] = merged["segment"].map(PORT_CORRIDOR_MAP).fillna(merged["segment"])

    conditions = [merged["capacity_utilization_pct"] > util_red, merged["capacity_utilization_pct"] > util_yellow]
    merged["bottleneck_tag"] = np.select(conditions, ["Red", "Yellow"], default="Green")

    merged["velocity_context"] = np.where(
        merged["velocity"] < velocity_target,
        "Systemic (Low National Velocity)", "Localized (Normal National Velocity)",
    )
    merged.loc[merged["bottleneck_tag"] == "Green", "velocity_context"] = "n/a"

    if min_material_stocks_ktonnes > 0:
        immaterial = merged["stocks_ktonnes"] < min_material_stocks_ktonnes
        demoted = immaterial & merged["bottleneck_tag"].isin(["Red", "Yellow"])
        merged.loc[demoted, "bottleneck_tag"] = "Green"
        merged.loc[demoted, "velocity_context"] = "n/a"

    missing = merged["capacity_utilization_pct"].isna()
    merged.loc[missing, "bottleneck_tag"] = "N/A"
    merged.loc[missing, "velocity_context"] = "n/a"

    cols = [
        "grain", "crop_year", "grain_week", "segment", "corridor",
        "stocks_ktonnes", "capacity_ktonnes", "capacity_utilization_pct",
        "velocity", "velocity_context", "bottleneck_tag",
    ]
    return merged[cols]
