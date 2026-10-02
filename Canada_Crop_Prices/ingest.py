"""Command-line entry point: fetch -> clean -> load prices into DuckDB / Parquet.

Examples::

    python ingest.py --fetch-pdq                    # daily PDQ pull
    python ingest.py --import-csv lentils.csv       # manual / non-PDQ prices
    python ingest.py --export-parquet               # full Parquet rebuild
    python ingest.py --reload-raw                   # replay data/raw/*.json into DuckDB

Flags can be combined; they run in the order fetch, reload, import, export.
Exit code is 0 on success and 1 if any step failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from database import PriceDatabase
from manual_override import ManualOverrideError, import_csv
from pdq_client import PDQAPIError, PDQClient, PDQSnapshot, parse_payload

log = logging.getLogger("ingest")


# --------------------------------------------------------------------------- #
# Raw lakehouse snapshots
# --------------------------------------------------------------------------- #
def save_raw_snapshot(
    payload: dict[str, Any], imported_at: datetime, raw_dir: Path = config.RAW_DIR
) -> Path | None:
    """Write a PDQ payload to ``raw_dir/YYYY-MM-DD_pdq.json``.

    Returns the file written, or ``None`` if this exact import is already
    saved. A different import on the same day goes to
    ``YYYY-MM-DD_HHMM_pdq.json`` so no snapshot is overwritten.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = payload.get("prices_imported_at")
    daily = raw_dir / f"{imported_at:%Y-%m-%d}{config.RAW_SNAPSHOT_SUFFIX}"
    intraday = raw_dir / f"{imported_at:%Y-%m-%d_%H%M}{config.RAW_SNAPSHOT_SUFFIX}"

    for existing in (daily, intraday):
        if existing.exists():
            try:
                if json.loads(existing.read_text(encoding="utf-8")).get("prices_imported_at") == stamp:
                    return None
            except (OSError, json.JSONDecodeError):
                log.warning("Unreadable snapshot %s; not treating it as a duplicate", existing)

    target = intraday if daily.exists() else daily
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def iter_raw_snapshots(raw_dir: Path = config.RAW_DIR) -> list[Path]:
    """All raw PDQ snapshot files, oldest first."""
    return sorted(raw_dir.glob(f"*{config.RAW_SNAPSHOT_SUFFIX}"))


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
def _load_snapshot(db: PriceDatabase, snapshot: PDQSnapshot) -> None:
    raw_n, unified_n = db.upsert_pdq_records(snapshot.records)
    quoted = sum(r.ton_price is not None for r in snapshot.records)
    log.info(
        "PDQ %s: %d raw rows (%d quoted, %d NULL), %d unified rows for the day",
        snapshot.imported_at, raw_n, quoted, raw_n - quoted, unified_n,
    )


def fetch_pdq(db: PriceDatabase) -> set[tuple[int, int]]:
    """Fetch PDQ, save the raw snapshot, and upsert. Returns touched (year, month)s."""
    with PDQClient() as client:
        payload = client.fetch_json()
    snapshot = parse_payload(payload)
    path = save_raw_snapshot(payload, snapshot.imported_at)
    log.info("Raw snapshot: %s", path or "unchanged (already saved)")
    if db.has_import(snapshot.imported_at):
        log.info("PDQ import %s already loaded; refreshing it", snapshot.imported_at)
    _load_snapshot(db, snapshot)
    return {(snapshot.imported_at.year, snapshot.imported_at.month)}


def reload_raw(db: PriceDatabase) -> set[tuple[int, int]]:
    """Replay every saved raw snapshot into DuckDB."""
    months: set[tuple[int, int]] = set()
    files = iter_raw_snapshots()
    if not files:
        log.warning("No raw snapshots in %s", config.RAW_DIR)
    for path in files:
        try:
            snapshot = parse_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, PDQAPIError) as exc:
            log.error("Skipping %s: %s", path.name, exc)
            continue
        _load_snapshot(db, snapshot)
        months.add((snapshot.imported_at.year, snapshot.imported_at.month))
    return months


def import_manual(db: PriceDatabase, paths: Sequence[Path]) -> set[tuple[int, int]]:
    """Import manual price CSVs."""
    months: set[tuple[int, int]] = set()
    for path in paths:
        frame = import_csv(path, db)
        log.info("Imported %d rows from %s", len(frame), path)
        months.update((d.year, d.month) for d in frame["price_date"])
    return months


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canada crop price ingestion (PDQ + manual).")
    parser.add_argument("--fetch-pdq", action="store_true",
                        help="Pull PDQ prices, save the JSON snapshot, upsert into DuckDB/Parquet")
    parser.add_argument("--import-csv", type=Path, nargs="+", metavar="PATH",
                        help="Ingest manual non-PDQ price CSV file(s)")
    parser.add_argument("--export-parquet", action="store_true",
                        help="Full export of DuckDB tables to partitioned Parquet")
    parser.add_argument("--reload-raw", action="store_true",
                        help="Rebuild DuckDB from every saved raw JSON snapshot")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args(argv)
    if not (args.fetch_pdq or args.import_csv or args.export_parquet or args.reload_raw):
        parser.error("choose at least one of --fetch-pdq, --import-csv, --export-parquet, --reload-raw")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested ingestion steps."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ok = True
    touched: set[tuple[int, int]] = set()
    try:
        with PriceDatabase() as db:
            if args.fetch_pdq:
                try:
                    touched |= fetch_pdq(db)
                except PDQAPIError as exc:
                    log.error("PDQ fetch failed: %s", exc)
                    ok = False
            if args.reload_raw:
                touched |= reload_raw(db)
            if args.import_csv:
                try:
                    touched |= import_manual(db, args.import_csv)
                except (ManualOverrideError, FileNotFoundError) as exc:
                    log.error("CSV import failed: %s", exc)
                    ok = False

            if args.export_parquet:
                files = db.export_parquet()
                log.info("Full Parquet export: %d files under %s", len(files), config.PARQUET_DIR)
            elif touched:
                files = db.export_parquet(months=touched)
                log.info("Parquet: refreshed %d partition file(s)", len(files))
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
