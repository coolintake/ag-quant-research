"""Backfill historical PDQ prices from website CSV exports in ``input/``.

PDQ's API only serves the current snapshot, so history comes from the CSV
exports on pdqinfo.ca (Price History -> export). Each file has the columns::

    MONTH, COMMODITY, ZONE, IMPORT DATE, CASH, CASH CHANGE,
    FUTURES MONTH, BASIS, BASIS CHANGE

e.g. ``JAN '16, 1 CDA CANOLA, PEACE, 01/04/2016, 10.22, -0.11, RSH16, -0.71, 0.00``.

Mapping into ``pdq_raw_prices``
-------------------------------
- ``IMPORT DATE`` (MM/DD/YYYY) -> quote date, stored as ``imported_at`` at
  00:00 because exports carry no time of day.
- ``MONTH`` (``JAN '25``) -> ``delivery_month`` / ``delivery_year``.
- ``FUTURES MONTH`` (``RSH25``) -> ``futures_code`` / ``futures_month``
  (``MAR``) / ``futures_year`` (2025); ``-`` -> NULL.
- ``CASH`` ($/bu) -> ``bus_price``; ``ton_price`` = bus x 1000 / kg-per-bushel.
- ``BASIS`` ($/bu) -> ``basis_bu``; ``basis_mt`` converted the same way.
- ``CASH - CASH CHANGE`` -> ``bus_price_prev`` / ``ton_price_prev``.

Cleaning uses the ``pdq_client`` rules: ``-``, blanks and zero prices -> NULL;
zero basis -> NULL for flat-price commodities (durum, yellow peas); basis is
NULL whenever the cash price is.

Idempotency: rows are upserted on (quote date, zone, commodity, delivery
month, delivery year), so re-running replaces rather than duplicates. The
``unified_cash_prices`` PDQ rows and Parquet partitions for the affected
months are rebuilt afterwards.

Usage::

    python backfill_pdq.py                 # every input/*.csv
    python backfill_pdq.py --input-dir DIR
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from database import PriceDatabase
from pdq_client import clean_basis, clean_price, parse_export_month, parse_futures_code

log = logging.getLogger("backfill_pdq")

EXPORT_COLUMNS: tuple[str, ...] = (
    "MONTH", "COMMODITY", "ZONE", "IMPORT DATE", "CASH", "CASH CHANGE",
    "FUTURES MONTH", "BASIS", "BASIS CHANGE",
)
_KEY = ["imported_at", "zone", "commodity", "delivery_month", "delivery_year"]


class BackfillError(ValueError):
    """Raised when an export file cannot be parsed. Nothing is loaded."""


def _map_unique(series: pd.Series, func) -> pd.Series:
    """Apply a scalar parser once per distinct value (exports repeat values heavily)."""
    lookup = {value: func(value) for value in series.unique()}
    return series.map(lookup)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.str.strip().replace({"-": None, "": None}), errors="coerce")


def parse_export(path: Path) -> pd.DataFrame:
    """Read one PDQ CSV export and normalize it to ``RAW_COLUMNS + FUTURES_COLUMNS``.

    Raises:
        BackfillError: on missing columns or unrecognised dates, zones,
            commodities or delivery months.
    """
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise BackfillError(f"{path.name}: cannot read CSV: {exc}") from exc
    raw.columns = [c.strip().upper() for c in raw.columns]
    missing = [c for c in EXPORT_COLUMNS if c not in raw.columns]
    if missing:
        raise BackfillError(f"{path.name}: missing column(s) {missing}")
    if raw.empty:
        return pd.DataFrame()
    raw = raw.apply(lambda s: s.str.strip())

    problems: list[str] = []

    quote_date = pd.to_datetime(raw["IMPORT DATE"], format="%m/%d/%Y", errors="coerce")
    if quote_date.isna().any():
        bad = raw.loc[quote_date.isna(), "IMPORT DATE"].unique()[:5]
        problems.append(f"unparseable IMPORT DATE values {list(bad)}")

    zone = raw["ZONE"].str.upper()
    bad_zones = sorted(set(zone) - set(config.PDQ_ZONE_IDS))
    if bad_zones:
        problems.append(f"unknown zones {bad_zones}")

    commodity = raw["COMMODITY"]
    bad_commodities = sorted(set(commodity) - set(config.PDQ_COMMODITY_MAP))
    if bad_commodities:
        problems.append(f"unknown commodities {bad_commodities}")

    def safe_month(value: str) -> tuple[str, int] | None:
        try:
            return parse_export_month(value)
        except ValueError:
            return None

    month_year = _map_unique(raw["MONTH"], safe_month)
    if month_year.isna().any():
        problems.append(f"unparseable MONTH values {list(raw.loc[month_year.isna(), 'MONTH'].unique()[:5])}")

    if problems:
        raise BackfillError(f"{path.name}: " + "; ".join(problems) + "; nothing loaded")

    symbol = commodity.map(config.PDQ_COMMODITY_MAP)
    to_mt = 1000.0 / symbol.map(config.CROP_KG_PER_BUSHEL)

    bus_price = _map_unique(raw["CASH"], clean_price).astype("float64")
    change = _numeric(raw["CASH CHANGE"])
    bus_prev = (bus_price - change).round(2)
    bus_prev = bus_prev.where(bus_prev > 0)

    # Basis rule is per commodity, so parse distinct (basis, commodity) pairs.
    pairs = list(zip(raw["BASIS"], commodity))
    basis_lookup = {pair: clean_basis(*pair) for pair in set(pairs)}
    basis_bu = pd.Series([basis_lookup[p] for p in pairs], index=raw.index, dtype="float64")
    basis_bu = basis_bu.where(bus_price.notna())

    futures = _map_unique(raw["FUTURES MONTH"], parse_futures_code)

    frame = pd.DataFrame({
        "imported_at": quote_date,
        "zone": zone,
        "zone_id": zone.map(config.PDQ_ZONE_IDS).astype("int32"),
        "delivery_month": month_year.str[0],
        "delivery_year": month_year.str[1].astype("int32"),
        "commodity": commodity,
        "basis_mt": (basis_bu * to_mt).round(2),
        "basis_bu": basis_bu,
        "ton_price": (bus_price * to_mt).round(2),
        "bus_price": bus_price,
        "ton_price_prev": (bus_prev * to_mt).round(2),
        "bus_price_prev": bus_prev,
        "futures_code": futures.str[0],
        "futures_month": futures.str[1],
        "futures_year": pd.array(futures.str[2], dtype="Int32"),
    })
    return frame


def load_exports(paths: Sequence[Path]) -> pd.DataFrame:
    """Parse every export and resolve keys that appear in more than one file."""
    frames = []
    for path in paths:
        frame = parse_export(path)
        if frame.empty:
            log.warning("%s: no data rows (header only); skipped", path.name)
            continue
        log.info("%s: %d rows, %s to %s", path.name, len(frame),
                 frame["imported_at"].min().date(), frame["imported_at"].max().date())
        frames.append(frame.assign(_order=len(frames)))
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    dupes = combined.duplicated(_KEY, keep="last")
    if dupes.any():
        log.warning("%d rows repeat a key from another export; the later file wins", int(dupes.sum()))
        combined = combined[~dupes]
    return combined.drop(columns="_order").reset_index(drop=True)


def summarize(db: PriceDatabase) -> pd.DataFrame:
    """Per-crop coverage of the PDQ rows in ``unified_cash_prices``."""
    return db.query_df(
        f"""
        SELECT crop_symbol, count(*) AS n_rows, count(price_cad_mt) AS quoted,
               count(DISTINCT price_date) AS n_days,
               min(price_date) AS first_date, max(price_date) AS last_date,
               count(futures_code) AS with_futures
        FROM {config.UNIFIED_TABLE}
        WHERE source = 'PDQ'
        GROUP BY crop_symbol ORDER BY crop_symbol
        """
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill PDQ history from CSV exports.")
    parser.add_argument("--input-dir", type=Path, default=config.INPUT_DIR,
                        help="Folder of PDQ CSV exports (default: input/)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse all exports, upsert them, refresh Parquet, and print a summary."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    paths = sorted(Path(args.input_dir).glob("*.csv"))
    if not paths:
        log.error("No .csv files in %s", args.input_dir)
        return 1

    try:
        history = load_exports(paths)
    except BackfillError as exc:
        log.error("%s", exc)
        return 1
    if history.empty:
        log.error("The exports contain no rows")
        return 1

    started = datetime.now()
    try:
        with PriceDatabase() as db:
            raw_n, unified_n = db.upsert_pdq_history(history)
            months = {(d.year, d.month) for d in history["imported_at"].dt.date.unique()}
            files = db.export_parquet(months=months)
            summary = summarize(db)
            totals = db.query_df(
                f"SELECT count(*) AS n, min(imported_at) AS first, max(imported_at) AS last "
                f"FROM {config.RAW_TABLE}"
            ).iloc[0]
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\nUpserted {raw_n:,} rows from {len(paths)} file(s) in {elapsed:.1f}s; "
          f"rebuilt {unified_n:,} unified rows; wrote {len(files)} Parquet file(s).")
    print(f"{config.RAW_TABLE}: {int(totals.n):,} rows, {totals['first']} to {totals['last']}\n")
    with pd.option_context("display.width", 160):
        print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
