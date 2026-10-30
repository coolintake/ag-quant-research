"""Parsers for locally stored PDQ price data (raw JSON snapshots and website CSV exports).

The PDQ API subscription has ended; nothing here makes network calls. Saved snapshots in
``data/raw/*.json`` (replayed by ``ingest.py --reload-raw``) have this shape::

    {"status": "ok", "message": "", "prices_imported_at": "2026-09-30 14:30",
     "data": [{"zone": "PEACE", "zone_id": 1, "month": "OCT",
               "commodity": "1 CDA CANOLA", "basis": -56.81,
               "basis_bushels": -1.29, "ton_price": 766.89, "bus_price": 17.39,
               "ton_price_prev": 766.89, "bus_price_prev": 17.39}, ...]}

PDQ reports a non-quoted cell as 0 (for example a zone with no bid for a
delivery month). Those zeros are converted to ``None`` here so they land in
DuckDB as NULL and never pull an average down.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import config

log = logging.getLogger(__name__)


class PDQParseError(RuntimeError):
    """Raised when a saved PDQ payload is unusable."""


@dataclass(frozen=True)
class PDQPriceRecord:
    """One parsed row of the PDQ ``data`` array (matches ``pdq_raw_prices``)."""

    imported_at: datetime
    zone: str
    zone_id: int
    delivery_month: str
    delivery_year: int
    commodity: str
    basis_mt: float | None
    basis_bu: float | None
    ton_price: float | None
    bus_price: float | None
    ton_price_prev: float | None
    bus_price_prev: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a plain dict."""
        return asdict(self)


@dataclass(frozen=True)
class PDQSnapshot:
    """A full parsed PDQ response."""

    imported_at: datetime
    records: list[PDQPriceRecord]
    raw: dict[str, Any]


# --------------------------------------------------------------------------- #
# Value cleaning
# --------------------------------------------------------------------------- #
def _to_float(value: Any) -> float | None:
    """Coerce a JSON value to float, mapping blanks and junk to ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _price_or_none(value: Any) -> float | None:
    """Return a price, treating PDQ's 0 / non-positive "no quote" as ``None``."""
    number = _to_float(value)
    if number is None or number <= 0:
        return None
    return number


def resolve_delivery_year(delivery_month: str, imported_at: datetime) -> int:
    """Infer the calendar year of a PDQ delivery month.

    PDQ lists the current month and the months that follow it, without a year.
    A month on or after the import month is in the import year; an earlier
    month has rolled into the next year (a JAN bid imported in SEP 2026 is
    January 2027).
    """
    month_num = config.MONTH_TO_NUM[delivery_month]
    return imported_at.year if month_num >= imported_at.month else imported_at.year + 1


_FUTURES_CODE_RE = re.compile(r"^([A-Z]{1,3})([FGHJKMNQUVXZ])(\d{2}|\d{4})$")
_EXPORT_MONTH_RE = re.compile(r"^([A-Z]{3})\s*'?\s*(\d{2}|\d{4})$")


def parse_futures_code(value: Any) -> tuple[str | None, str | None, int | None]:
    """Split a futures contract code such as ``RSX25`` into its parts.

    Returns:
        (futures_code, futures_month, futures_year), e.g.
        ``("RSX25", "NOV", 2025)``. ``futures_month`` is the 3-letter month
        the contract letter stands for (``config.FUTURES_MONTH_CODES``). A
        missing, dashed or unrecognised code returns ``(None, None, None)``.
    """
    if value is None:
        return None, None, None
    code = str(value).strip().upper()
    match = _FUTURES_CODE_RE.match(code)
    if not match:
        return None, None, None
    _, letter, year = match.groups()
    full_year = int(year) if len(year) == 4 else 2000 + int(year)
    return code, config.FUTURES_MONTH_CODES[letter], full_year


def parse_export_month(value: Any) -> tuple[str, int]:
    """Parse a PDQ export ``MONTH`` cell such as ``JAN '25`` into ``("JAN", 2025)``.

    Raises:
        ValueError: if the cell is not a recognised month/year.
    """
    text = str(value).strip().upper()
    match = _EXPORT_MONTH_RE.match(text)
    if not match or match.group(1) not in config.MONTH_TO_NUM:
        raise ValueError(f"Unrecognised delivery month {value!r}")
    month, year = match.groups()
    return month, int(year) if len(year) == 4 else 2000 + int(year)


def clean_price(value: Any) -> float | None:
    """Public form of the price rule: blanks, ``-``, zero and negatives -> ``None``."""
    return _price_or_none(value)


def clean_basis(value: Any, commodity: str) -> float | None:
    """Basis rule: blanks and ``-`` -> ``None``; zero -> ``None`` for flat-price commodities."""
    basis = _to_float(value)
    if basis == 0 and commodity in config.ZERO_BASIS_IS_NULL_COMMODITIES:
        return None
    return basis


def parse_imported_at(value: Any) -> datetime:
    """Parse PDQ's ``prices_imported_at`` (``YYYY-MM-DD HH:MM``)."""
    if not isinstance(value, str):
        raise PDQParseError(f"prices_imported_at missing or not a string: {value!r}")
    text = value.strip()
    try:
        return datetime.strptime(text, config.PDQ_IMPORTED_AT_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(text)
        except ValueError as exc:
            raise PDQParseError(f"Unparseable prices_imported_at: {value!r}") from exc


def parse_record(row: dict[str, Any], imported_at: datetime) -> PDQPriceRecord:
    """Parse and validate one ``data`` row.

    Raises:
        ValueError: if the zone, zone id, month, or commodity is unknown.
    """
    zone = str(row.get("zone", "")).strip().upper()
    if zone not in config.PDQ_ZONE_IDS:
        raise ValueError(f"Unknown PDQ zone {zone!r}")
    zone_id = int(row.get("zone_id", 0))
    if zone_id != config.PDQ_ZONE_IDS[zone]:
        raise ValueError(f"Zone {zone!r} has id {zone_id}, expected {config.PDQ_ZONE_IDS[zone]}")

    month = str(row.get("month", "")).strip().upper()[:3]
    if month not in config.MONTH_TO_NUM:
        raise ValueError(f"Unknown delivery month {row.get('month')!r}")

    commodity = str(row.get("commodity", "")).strip()
    if commodity not in config.PDQ_COMMODITY_MAP:
        raise ValueError(f"Unknown PDQ commodity {commodity!r}")

    ton_price = _price_or_none(row.get("ton_price"))
    bus_price = _price_or_none(row.get("bus_price"))
    # Basis may legitimately be zero or negative, but it is meaningless when
    # there is no cash price to anchor it.
    basis_mt = _to_float(row.get("basis")) if ton_price is not None else None
    basis_bu = _to_float(row.get("basis_bushels")) if bus_price is not None else None
    # Flat-price commodities (e.g. durum) have no futures basis; PDQ's 0.00
    # there means "not applicable", not a zero basis.
    if commodity in config.ZERO_BASIS_IS_NULL_COMMODITIES:
        basis_mt = None if basis_mt == 0 else basis_mt
        basis_bu = None if basis_bu == 0 else basis_bu

    return PDQPriceRecord(
        imported_at=imported_at,
        zone=zone,
        zone_id=zone_id,
        delivery_month=month,
        delivery_year=resolve_delivery_year(month, imported_at),
        commodity=commodity,
        basis_mt=basis_mt,
        basis_bu=basis_bu,
        ton_price=ton_price,
        bus_price=bus_price,
        # A prev of 0 means "no previous import" per the PDQ docs; NULL keeps
        # any price-change calculation from running against it.
        ton_price_prev=_price_or_none(row.get("ton_price_prev")),
        bus_price_prev=_price_or_none(row.get("bus_price_prev")),
    )


def parse_payload(payload: dict[str, Any]) -> PDQSnapshot:
    """Validate and parse a full PDQ response.

    Rows that fail validation are logged and skipped so one bad row does not
    lose the whole snapshot; a payload with an error status or no usable rows
    raises.

    Raises:
        PDQParseError: if the payload is not a usable PDQ response.
    """
    if not isinstance(payload, dict):
        raise PDQParseError(f"Expected a JSON object, got {type(payload).__name__}")
    status = str(payload.get("status", "")).lower()
    if status != "ok":
        raise PDQParseError(f"PDQ returned status={status!r} message={payload.get('message')!r}")

    imported_at = parse_imported_at(payload.get("prices_imported_at"))
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise PDQParseError("PDQ payload has no data rows")

    records: list[PDQPriceRecord] = []
    seen: set[tuple[int, str, str]] = set()
    for i, row in enumerate(rows):
        try:
            record = parse_record(row, imported_at)
        except (ValueError, TypeError) as exc:
            log.warning("Skipping PDQ row %d: %s (%r)", i, exc, row)
            continue
        key = (record.zone_id, record.delivery_month, record.commodity)
        if key in seen:
            log.warning("Skipping duplicate PDQ row %d: %s", i, key)
            continue
        seen.add(key)
        records.append(record)

    if not records:
        raise PDQParseError("No PDQ rows passed validation")
    return PDQSnapshot(imported_at=imported_at, records=records, raw=payload)
