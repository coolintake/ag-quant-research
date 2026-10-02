"""Ingest manual / non-PDQ crop price CSVs into ``unified_cash_prices``.

CSV columns (header names are case-insensitive)
-----------------------------------------------
Required:
    price_date      ISO date (YYYY-MM-DD)
    province        AB, SK or MB
    crop_symbol     a symbol in ``config.CROP_KG_PER_BUSHEL`` (e.g. R_LENTILS)
    delivery_month  JAN..DEC (full names and 1-12 are accepted)
    price_cad_mt and/or price_cad_bu   (at least one of the two columns)
Optional:
    pdq_zone        a PDQ zone name, if the price is zone-specific
    source          BARCHART, CME or MANUAL (default MANUAL)
    delivery_year   defaults to the next occurrence of delivery_month on or
                    after price_date (same rule as PDQ)

If only one unit is supplied, the other is derived from the crop's bushel
weight. Blank prices stay NULL; zero and negative prices are rejected.
Re-importing a row with the same key replaces it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config
from database import UNIFIED_COLUMNS, UNIFIED_KEY, PriceDatabase
from pdq_client import resolve_delivery_year

log = logging.getLogger(__name__)

REQUIRED_COLUMNS: tuple[str, ...] = ("price_date", "province", "crop_symbol", "delivery_month")
PRICE_COLUMNS: tuple[str, ...] = ("price_cad_mt", "price_cad_bu")
_MAX_REPORTED_ERRORS = 20


class ManualOverrideError(ValueError):
    """Raised when a manual price CSV fails validation. Nothing is loaded."""


def _normalize_month(value: object) -> str | None:
    text = str(value).strip().upper()
    if text.isdigit() and 1 <= int(text) <= 12:
        return config.MONTHS[int(text) - 1]
    return text[:3] if text[:3] in config.MONTH_TO_NUM else None


def bu_to_mt(price_bu: float, crop_symbol: str) -> float:
    """Convert $/bu to $/t using the crop's bushel weight."""
    return price_bu * 1000.0 / config.CROP_KG_PER_BUSHEL[crop_symbol]


def mt_to_bu(price_mt: float, crop_symbol: str) -> float:
    """Convert $/t to $/bu using the crop's bushel weight."""
    return price_mt * config.CROP_KG_PER_BUSHEL[crop_symbol] / 1000.0


def validate_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate and standardize a manual price frame into ``UNIFIED_COLUMNS``.

    Raises:
        ManualOverrideError: listing every invalid row (up to a limit).
    """
    frame = raw.copy()
    frame.columns = [str(c).strip().lower() for c in frame.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ManualOverrideError(f"Missing required column(s): {', '.join(missing)}")
    if not any(c in frame.columns for c in PRICE_COLUMNS):
        raise ManualOverrideError("CSV needs price_cad_mt and/or price_cad_bu")
    for col in PRICE_COLUMNS + ("pdq_zone", "source", "delivery_year"):
        if col not in frame.columns:
            frame[col] = None

    errors: list[str] = []
    records: list[dict[str, object]] = []
    for idx, row in frame.iterrows():
        line = int(idx) + 2  # 1-based, after the header row
        problems: list[str] = []

        try:
            price_date: date = pd.to_datetime(str(row["price_date"]).strip()).date()
        except (ValueError, TypeError):
            problems.append(f"bad price_date {row['price_date']!r}")
            price_date = None  # type: ignore[assignment]

        province = str(row["province"]).strip().upper()
        if province not in config.PROVINCES:
            problems.append(f"province {row['province']!r} not in {config.PROVINCES}")

        symbol = str(row["crop_symbol"]).strip().upper()
        if symbol not in config.CROP_KG_PER_BUSHEL:
            problems.append(f"unknown crop_symbol {row['crop_symbol']!r} (add it to config)")

        month = _normalize_month(row["delivery_month"])
        if month is None:
            problems.append(f"bad delivery_month {row['delivery_month']!r}")

        zone = None if pd.isna(row["pdq_zone"]) or not str(row["pdq_zone"]).strip() \
            else str(row["pdq_zone"]).strip().upper()
        if zone is not None:
            if zone not in config.ZONE_TO_PROVINCE:
                problems.append(f"unknown pdq_zone {row['pdq_zone']!r}")
            elif config.ZONE_TO_PROVINCE[zone] != province:
                problems.append(f"pdq_zone {zone} is in {config.ZONE_TO_PROVINCE[zone]}, not {province}")

        source = "MANUAL" if pd.isna(row["source"]) or not str(row["source"]).strip() \
            else str(row["source"]).strip().upper()
        if source not in config.MANUAL_SOURCES:
            problems.append(f"source {source!r} not in {config.MANUAL_SOURCES}")

        prices: dict[str, float | None] = {}
        for col in PRICE_COLUMNS:
            value = row[col]
            if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                prices[col] = None
                continue
            try:
                number = float(str(value).replace(",", "").replace("$", ""))
            except ValueError:
                problems.append(f"{col} {value!r} is not a number")
                continue
            if number <= 0:
                problems.append(f"{col} must be positive (leave blank for no quote), got {number}")
                continue
            prices[col] = number

        year_value = row["delivery_year"]
        delivery_year: int | None = None
        if not pd.isna(year_value) and str(year_value).strip():
            try:
                delivery_year = int(float(year_value))
            except ValueError:
                problems.append(f"bad delivery_year {year_value!r}")

        if problems:
            errors.append(f"line {line}: " + "; ".join(problems))
            continue

        mt, bu = prices.get("price_cad_mt"), prices.get("price_cad_bu")
        if mt is None and bu is not None:
            mt = bu_to_mt(bu, symbol)
        elif bu is None and mt is not None:
            bu = mt_to_bu(mt, symbol)
        if mt is None:
            log.warning("line %d: no price quoted; stored as NULL", line)
        if delivery_year is None:
            delivery_year = resolve_delivery_year(
                month, datetime(price_date.year, price_date.month, 1)  # type: ignore[arg-type]
            )

        records.append({
            "price_date": price_date,
            "province": province,
            "pdq_zone": zone,
            "crop_symbol": symbol,
            "source": source,
            "delivery_month": month,
            "delivery_year": delivery_year,
            "price_cad_mt": None if mt is None else round(mt, 4),
            "price_cad_bu": None if bu is None else round(bu, 4),
        })

    if errors:
        shown = errors[:_MAX_REPORTED_ERRORS]
        more = len(errors) - len(shown)
        tail = f"\n... and {more} more" if more else ""
        raise ManualOverrideError(
            f"{len(errors)} invalid row(s); nothing loaded:\n" + "\n".join(shown) + tail
        )

    result = pd.DataFrame.from_records(records, columns=list(UNIFIED_COLUMNS))
    dupes = result[result.duplicated(list(UNIFIED_KEY), keep=False)]
    if not dupes.empty:
        raise ManualOverrideError(
            f"{len(dupes)} rows share a key ({', '.join(UNIFIED_KEY)}); nothing loaded:\n"
            + dupes.to_string(index=False)
        )
    return result


def load_csv(path: Path) -> pd.DataFrame:
    """Read and validate a manual price CSV."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=True, skip_blank_lines=True)
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ManualOverrideError(f"Cannot parse {path}: {exc}") from exc
    if raw.empty:
        raise ManualOverrideError(f"{path} has no data rows")
    return validate_frame(raw)


def import_csv(path: Path, db: PriceDatabase) -> pd.DataFrame:
    """Validate a manual CSV and upsert it into ``unified_cash_prices``.

    Returns:
        The standardized rows that were loaded.
    """
    frame = load_csv(path)
    db.upsert_unified(frame)
    log.info("Loaded %d manual rows from %s", len(frame), path)
    return frame
