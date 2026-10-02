"""HTTP client and response parser for the PDQinfo price API.

The API returns one JSON document per call::

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
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

log = logging.getLogger(__name__)


class PDQAPIError(RuntimeError):
    """Raised when the PDQ API cannot be reached or returns an unusable payload."""


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


def parse_imported_at(value: Any) -> datetime:
    """Parse PDQ's ``prices_imported_at`` (``YYYY-MM-DD HH:MM``)."""
    if not isinstance(value, str):
        raise PDQAPIError(f"prices_imported_at missing or not a string: {value!r}")
    text = value.strip()
    try:
        return datetime.strptime(text, config.PDQ_IMPORTED_AT_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(text)
        except ValueError as exc:
            raise PDQAPIError(f"Unparseable prices_imported_at: {value!r}") from exc


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
        PDQAPIError: if the payload is not a usable PDQ response.
    """
    if not isinstance(payload, dict):
        raise PDQAPIError(f"Expected a JSON object, got {type(payload).__name__}")
    status = str(payload.get("status", "")).lower()
    if status != "ok":
        raise PDQAPIError(f"PDQ returned status={status!r} message={payload.get('message')!r}")

    imported_at = parse_imported_at(payload.get("prices_imported_at"))
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise PDQAPIError("PDQ payload has no data rows")

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
        raise PDQAPIError("No PDQ rows passed validation")
    return PDQSnapshot(imported_at=imported_at, records=records, raw=payload)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class PDQClient:
    """Thin ``requests`` wrapper with retries and timeouts for the PDQ API."""

    def __init__(
        self,
        url: str = config.PDQ_URL,
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
        max_retries: int = config.HTTP_MAX_RETRIES,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.session = session or self._build_session(max_retries)

    @staticmethod
    def _build_session(max_retries: int) -> requests.Session:
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=config.HTTP_BACKOFF_FACTOR,
            status_forcelist=config.HTTP_RETRY_STATUSES,
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        session.headers.update({"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"})
        return session

    def fetch_json(self) -> dict[str, Any]:
        """GET the endpoint and return the decoded JSON body.

        Raises:
            PDQAPIError: on network failure, non-2xx status, or invalid JSON.
        """
        try:
            response = self.session.get(self.url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PDQAPIError(f"PDQ request failed: {exc}") from exc
        if not response.ok:
            raise PDQAPIError(f"PDQ returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise PDQAPIError(f"PDQ returned invalid JSON: {response.text[:200]}") from exc

    def fetch(self) -> PDQSnapshot:
        """Fetch and parse the current PDQ price snapshot."""
        return parse_payload(self.fetch_json())

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def __enter__(self) -> PDQClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
