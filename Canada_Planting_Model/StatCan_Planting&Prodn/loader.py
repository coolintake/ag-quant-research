"""Load: upsert flat records into DuckDB and export partitioned Parquet.

One table per topic (``config.TopicSpec.table``); the value column comes from the topic
(``acres_thousand`` for plantings, ``tonnes_thousand`` for production). Rows are keyed on
(release_code, crop_name, target_crop_year), so re-running the pipeline replaces rather than
duplicates.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import duckdb
import pandas as pd

import config

log = logging.getLogger(__name__)

KEY = ("release_code", "crop_name", "target_crop_year")


def ddl(spec: config.TopicSpec) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {spec.table} (
            release_code      VARCHAR NOT NULL,
            release_date      DATE    NOT NULL,
            release_month     VARCHAR NOT NULL,
            release_year      INTEGER NOT NULL,
            crop_name         VARCHAR NOT NULL,
            target_crop_year  INTEGER NOT NULL,
            {spec.value_column} DOUBLE,
            metric_type       VARCHAR NOT NULL,
            is_preliminary    BOOLEAN,
            unit              VARCHAR,
            source_sheet      VARCHAR,
            source_label      VARCHAR,
            source_header     VARCHAR,
            ingested_at       TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY ({", ".join(KEY)})
        )"""


def connect(path: Path = config.DUCKDB_PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def upsert(con: duckdb.DuckDBPyConnection, records: pd.DataFrame, spec: config.TopicSpec) -> int:
    """Insert or replace one topic's records. Returns the number of rows written."""
    con.execute(ddl(spec))
    df = records[records["metric_type"] == spec.metric_type].rename(columns={"value": spec.value_column})
    if df.empty:
        return 0
    cols = ["release_code", "release_date", "release_month", "release_year", "crop_name", "target_crop_year",
            spec.value_column, "metric_type", "is_preliminary", "unit", "source_sheet", "source_label",
            "source_header"]
    con.register("incoming", df[cols])
    try:
        con.execute(f"INSERT OR REPLACE INTO {spec.table} ({', '.join(cols)}, ingested_at) "
                    f"SELECT {', '.join(cols)}, current_timestamp FROM incoming")
    finally:
        con.unregister("incoming")
    log.info("%s: upserted %d rows", spec.table, len(df))
    return len(df)


def export_parquet(con: duckdb.DuckDBPyConnection, spec: config.TopicSpec, root: Path = config.PARQUET_DIR) -> Path:
    """Write the whole table to Parquet partitioned by release_year (directory rebuilt each run)."""
    out = root / spec.table
    if out.exists():
        shutil.rmtree(out)            # generated output only; avoids stale partitions
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM {spec.table} ORDER BY release_date, crop_name, target_crop_year) "
                f"TO '{out.as_posix()}' (FORMAT PARQUET, PARTITION_BY (release_year))")
    log.info("%s: Parquet written to %s", spec.table, out)
    return out


def table_summary(con: duckdb.DuckDBPyConnection, spec: config.TopicSpec) -> pd.DataFrame:
    return con.execute(f"""
        SELECT release_code, any_value(release_date) AS release_date, count(*) AS rows,
               count(DISTINCT crop_name) AS crops, min(target_crop_year) AS first_year, max(target_crop_year) AS last_year
        FROM {spec.table} GROUP BY release_code ORDER BY any_value(release_date)""").df()
