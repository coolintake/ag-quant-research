"""CLI for the StatCan release-table pipeline.

    python main.py run                          # Phase 1: planting tabs -> DuckDB + Parquet
    python main.py run --dry-run                # extract + transform + validate, write nothing
    python main.py run --topics planting production   # once production tabs are ready
    python main.py sheets                       # which tabs are selected / skipped, and why
    python main.py march-vs-june --crop Canola
    python main.py revisions --crop Canola --year 2021
    python main.py snapshot --release Jun2026
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

import analytics
import config
import extractor
import loader
import transformer

log = logging.getLogger("pipeline")


def cmd_run(args) -> int:
    topics = args.topics or [t for t, s in config.TOPICS.items() if s.enabled]
    sheets, _ = extractor.extract(topics, Path(args.workbook))
    records = transformer.transform(sheets)
    log.info("Transformed %d records from %d tab(s)", len(records), len(sheets))
    if args.dry_run:
        print(records.groupby(["metric_type", "release_code"], sort=False).size().to_string())
        print(f"Dry run: {len(records)} records validated; nothing written.")
        return 0
    con = loader.connect()
    try:
        for t in topics:
            spec = config.TOPICS[t]
            loader.upsert(con, records, spec)
            if not args.no_parquet:
                loader.export_parquet(con, spec)
            print(f"\n{spec.table}:")
            print(loader.table_summary(con, spec).to_string(index=False))
    finally:
        con.close()
    print(f"\nDuckDB: {config.DUCKDB_PATH}\nParquet: {config.PARQUET_DIR}")
    return 0


def cmd_sheets(args) -> int:
    names = extractor.list_sheets(Path(args.workbook))
    for t in args.topics or list(config.TOPICS):
        spec = config.TOPICS[t]
        print(f"\n[{t}] enabled={spec.enabled} block: {spec.start_marker!r} .. {spec.end_marker!r}")
        try:
            for d in extractor.select_sheets(names, spec):
                print(f"  {'+' if d.selected else '-'} {d.name:<12} {d.reason}")
        except ValueError as e:
            print(f"  ! {e}")
    return 0


def _show(df: pd.DataFrame) -> int:
    with pd.option_context("display.width", 200, "display.max_rows", 200, "display.float_format", "{:,.3f}".format):
        print(df.to_string(index=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="StatCan field crop release tables -> DuckDB / Parquet")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract, transform and load")
    r.add_argument("--workbook", default=str(config.SOURCE_WORKBOOK))
    r.add_argument("--topics", nargs="+", choices=list(config.TOPICS), help="default: enabled topics")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--no-parquet", action="store_true")
    s = sub.add_parser("sheets", help="show tab selection")
    s.add_argument("--workbook", default=str(config.SOURCE_WORKBOOK))
    s.add_argument("--topics", nargs="+", choices=list(config.TOPICS))
    mj = sub.add_parser("march-vs-june", help="March vs June estimates per crop year")
    mj.add_argument("--crop", required=True)
    rv = sub.add_parser("revisions", help="all estimates of one crop year")
    rv.add_argument("--crop", required=True)
    rv.add_argument("--year", type=int, required=True)
    sn = sub.add_parser("snapshot", help="one release, all crops")
    sn.add_argument("--release", default=None)
    for q in (r, s, mj, rv, sn):
        q.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=config.LOG_FORMAT)

    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "sheets":
        return cmd_sheets(args)
    if args.cmd == "march-vs-june":
        return _show(analytics.march_vs_june(args.crop))
    if args.cmd == "revisions":
        return _show(analytics.revision_path(args.crop, args.year))
    return _show(analytics.release_snapshot(args.release))


if __name__ == "__main__":
    sys.exit(main())
