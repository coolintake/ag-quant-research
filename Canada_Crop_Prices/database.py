"""DuckDB storage layer and Parquet export for crop cash prices.

Tables
------
``pdq_raw_prices``
    One row per (imported_at, zone_id, commodity, delivery_month) straight from
    PDQ. Re-ingesting the same import replaces its rows, so it is idempotent.
``unified_cash_prices``
    One row per (price_date, province, pdq_zone, crop_symbol, source,
    delivery_month, delivery_year) in CAD/t and CAD/bu, for PDQ and non-PDQ
    sources alike. PDQ rows are derived from the latest PDQ import of each day.

Both tables carry ``delivery_year`` in addition to the specified columns: PDQ
delivery months have no year (JAN bid in September means next January), and
new-crop window averages cannot be computed without it.

Parquet layout (hive style, one file per table per partition)::

    data/parquet/year=YYYY/month=MM/pdq_raw_prices.parquet
    data/parquet/year=YYYY/month=MM/unified_cash_prices.parquet
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

import config
from pdq_client import PDQPriceRecord

log = logging.getLogger(__name__)

RAW_COLUMNS: tuple[str, ...] = (
    "imported_at", "zone", "zone_id", "delivery_month", "delivery_year", "commodity",
    "basis_mt", "basis_bu", "ton_price", "bus_price", "ton_price_prev", "bus_price_prev",
    "ingested_at",
)
UNIFIED_COLUMNS: tuple[str, ...] = (
    "price_date", "province", "pdq_zone", "crop_symbol", "source",
    "delivery_month", "delivery_year", "price_cad_mt", "price_cad_bu",
)
# Underlying futures contract (from PDQ CSV exports; the live API has none).
FUTURES_COLUMNS: tuple[str, ...] = ("futures_code", "futures_month", "futures_year")
_FUTURES_DDL: tuple[tuple[str, str], ...] = (
    ("futures_code", "VARCHAR"), ("futures_month", "VARCHAR"), ("futures_year", "INTEGER"),
)

# Key of historical CSV rows: one quote per day, zone, commodity and delivery
# period. backfill_pdq.py upserts on it, replacing any row with the same key
# whatever its time of day.
HISTORY_KEY: tuple[str, ...] = (
    "quote_date", "zone", "commodity", "delivery_month", "delivery_year",
)

# Natural key of unified_cash_prices (pdq_zone may be NULL, so no PRIMARY KEY).
UNIFIED_KEY: tuple[str, ...] = (
    "price_date", "province", "pdq_zone", "crop_symbol", "source",
    "delivery_month", "delivery_year",
)

# Date expression used to partition each table.
_PARTITION_DATE: dict[str, str] = {
    config.RAW_TABLE: "CAST(imported_at AS DATE)",
    config.UNIFIED_TABLE: "price_date",
}

_DDL = f"""
CREATE TABLE IF NOT EXISTS {config.RAW_TABLE} (
    imported_at     TIMESTAMP NOT NULL,
    zone            VARCHAR   NOT NULL,
    zone_id         INTEGER   NOT NULL,
    delivery_month  VARCHAR   NOT NULL,
    delivery_year   INTEGER   NOT NULL,
    commodity       VARCHAR   NOT NULL,
    basis_mt        DOUBLE,
    basis_bu        DOUBLE,
    ton_price       DOUBLE,
    bus_price       DOUBLE,
    ton_price_prev  DOUBLE,
    bus_price_prev  DOUBLE,
    ingested_at     TIMESTAMP NOT NULL,
    futures_code    VARCHAR,
    futures_month   VARCHAR,
    futures_year    INTEGER,
    PRIMARY KEY (imported_at, zone_id, commodity, delivery_month)
);

CREATE TABLE IF NOT EXISTS {config.UNIFIED_TABLE} (
    price_date      DATE    NOT NULL,
    province        VARCHAR NOT NULL,
    pdq_zone        VARCHAR,
    crop_symbol     VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,
    delivery_month  VARCHAR NOT NULL,
    delivery_year   INTEGER NOT NULL,
    price_cad_mt    DOUBLE,
    price_cad_bu    DOUBLE,
    futures_code    VARCHAR,
    futures_month   VARCHAR,
    futures_year    INTEGER
);

CREATE TABLE IF NOT EXISTS pdq_zone_map (
    zone      VARCHAR PRIMARY KEY,
    province  VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS pdq_commodity_map (
    commodity    VARCHAR PRIMARY KEY,
    crop_symbol  VARCHAR NOT NULL
);

-- Price change vs the previous PDQ import. Prev values of 0 are stored as
-- NULL, so the change is NULL rather than a phantom jump.
CREATE OR REPLACE VIEW pdq_price_changes AS
SELECT imported_at, zone, commodity, delivery_month, delivery_year,
       ton_price, ton_price_prev, ton_price - ton_price_prev AS ton_change,
       bus_price, bus_price_prev, bus_price - bus_price_prev AS bus_change
FROM {config.RAW_TABLE};
"""


def _sql_path(path: Path) -> str:
    """Render a path as a single-quoted DuckDB string literal."""
    return "'" + path.as_posix().replace("'", "''") + "'"


class PriceDatabase:
    """Connection manager and data access for ``crop_prices.duckdb``.

    Use as a context manager::

        with PriceDatabase() as db:
            db.upsert_pdq_records(records)
    """

    def __init__(
        self,
        db_path: Path = config.DUCKDB_PATH,
        parquet_dir: Path = config.PARQUET_DIR,
        read_only: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.parquet_dir = Path(parquet_dir)
        self.read_only = read_only
        self._con: duckdb.DuckDBPyConnection | None = None

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """The open DuckDB connection (opens on first use)."""
        if self._con is None:
            self.connect()
        assert self._con is not None
        return self._con

    def connect(self) -> PriceDatabase:
        """Open the database file, creating tables and lookup maps if needed."""
        if self._con is not None:
            return self
        if not self.read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._con = duckdb.connect(str(self.db_path), read_only=self.read_only)
        except duckdb.IOException as exc:
            raise RuntimeError(
                f"Cannot open {self.db_path} (is another process holding it?): {exc}"
            ) from exc
        if not self.read_only:
            self._init_schema()
        return self

    def close(self) -> None:
        """Close the connection."""
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> PriceDatabase:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Run a block atomically; rolls back on any exception."""
        con = self.con
        con.begin()
        try:
            yield con
        except BaseException:
            con.rollback()
            raise
        else:
            con.commit()

    def _init_schema(self) -> None:
        self.con.execute(_DDL)
        # Databases created before the futures columns existed gain them here.
        for table in (config.RAW_TABLE, config.UNIFIED_TABLE):
            for name, sql_type in _FUTURES_DDL:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {sql_type}")
        # Refresh the lookup maps from config so edits there take effect.
        with self.transaction() as con:
            con.execute("DELETE FROM pdq_zone_map")
            con.executemany(
                "INSERT INTO pdq_zone_map VALUES (?, ?)", list(config.ZONE_TO_PROVINCE.items())
            )
            con.execute("DELETE FROM pdq_commodity_map")
            con.executemany(
                "INSERT INTO pdq_commodity_map VALUES (?, ?)",
                list(config.PDQ_COMMODITY_MAP.items()),
            )

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert_pdq_records(
        self, records: Iterable[PDQPriceRecord], ingested_at: datetime | None = None
    ) -> tuple[int, int]:
        """Upsert PDQ rows into ``pdq_raw_prices`` and refresh ``unified_cash_prices``.

        Returns:
            (raw rows written, unified PDQ rows rebuilt for the affected dates).
        """
        rows = [r.to_dict() for r in records]
        if not rows:
            return 0, 0
        stamp = ingested_at or datetime.now().replace(microsecond=0)
        frame = pd.DataFrame(rows)
        frame["ingested_at"] = stamp
        frame = frame[list(RAW_COLUMNS)]
        dates = sorted({r["imported_at"].date() for r in rows})

        cols = ", ".join(RAW_COLUMNS)
        with self.transaction() as con:
            con.register("incoming_raw", frame)
            try:
                con.execute(
                    f"INSERT OR REPLACE INTO {config.RAW_TABLE} ({cols}) "
                    f"SELECT {cols} FROM incoming_raw"
                )
            finally:
                con.unregister("incoming_raw")
            unified = self._rebuild_unified_pdq(con, dates)
        return len(frame), unified

    def _rebuild_unified_pdq(self, con: duckdb.DuckDBPyConnection, dates: list[date]) -> int:
        """Re-derive PDQ rows of ``unified_cash_prices`` for the given dates.

        Each day uses the latest PDQ import of that day.
        """
        if not dates:
            return 0
        con.execute(
            f"DELETE FROM {config.UNIFIED_TABLE} "
            f"WHERE source = 'PDQ' AND list_contains(?, price_date)",
            [dates],
        )
        con.execute(
            f"""
            INSERT INTO {config.UNIFIED_TABLE} ({", ".join(UNIFIED_COLUMNS + FUTURES_COLUMNS)})
            SELECT CAST(r.imported_at AS DATE), z.province, r.zone, c.crop_symbol, 'PDQ',
                   r.delivery_month, r.delivery_year, r.ton_price, r.bus_price,
                   r.futures_code, r.futures_month, r.futures_year
            FROM {config.RAW_TABLE} r
            JOIN pdq_zone_map z USING (zone)
            JOIN pdq_commodity_map c USING (commodity)
            WHERE list_contains(?, CAST(r.imported_at AS DATE))
            QUALIFY row_number() OVER (
                PARTITION BY CAST(r.imported_at AS DATE), r.zone_id, r.commodity, r.delivery_month
                ORDER BY r.imported_at DESC
            ) = 1
            """,
            [dates],
        )
        result = con.execute(
            f"SELECT count(*) FROM {config.UNIFIED_TABLE} "
            f"WHERE source = 'PDQ' AND list_contains(?, price_date)",
            [dates],
        ).fetchone()
        return int(result[0]) if result else 0

    def upsert_pdq_history(self, frame: pd.DataFrame, ingested_at: datetime | None = None) -> tuple[int, int]:
        """Upsert historical PDQ rows (from CSV exports) into ``pdq_raw_prices``.

        ``frame`` needs the ``RAW_COLUMNS`` (``ingested_at`` optional) plus
        ``FUTURES_COLUMNS``. Rows are keyed on ``HISTORY_KEY``: any stored row
        with the same quote date, zone, commodity and delivery period is
        replaced, so re-running a backfill never duplicates rows. The PDQ rows
        of ``unified_cash_prices`` are then rebuilt for the affected dates.

        Returns:
            (raw rows written, unified PDQ rows rebuilt for the affected dates).
        """
        if frame.empty:
            return 0, 0
        frame = frame.copy()
        stamp = ingested_at or datetime.now().replace(microsecond=0)
        frame["ingested_at"] = frame["ingested_at"].fillna(stamp) if "ingested_at" in frame else stamp
        cols = list(RAW_COLUMNS + FUTURES_COLUMNS)
        missing = set(cols) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing columns for {config.RAW_TABLE}: {sorted(missing)}")
        frame = frame[cols]
        dates = sorted(pd.to_datetime(frame["imported_at"]).dt.date.unique())

        col_list = ", ".join(cols)
        with self.transaction() as con:
            con.register("incoming_history", frame)
            try:
                con.execute(
                    f"""
                    DELETE FROM {config.RAW_TABLE} r USING incoming_history i
                    WHERE CAST(r.imported_at AS DATE) = CAST(i.imported_at AS DATE)
                      AND r.zone = i.zone AND r.commodity = i.commodity
                      AND r.delivery_month = i.delivery_month
                      AND r.delivery_year = i.delivery_year
                    """
                )
                con.execute(
                    f"INSERT INTO {config.RAW_TABLE} ({col_list}) "
                    f"SELECT {col_list} FROM incoming_history"
                )
            finally:
                con.unregister("incoming_history")
            unified = self._rebuild_unified_pdq(con, dates)
        return len(frame), unified

    def rebuild_unified_pdq_all(self) -> int:
        """Re-derive every PDQ row of ``unified_cash_prices`` from the raw table."""
        dates = [
            row[0]
            for row in self.con.execute(
                f"SELECT DISTINCT CAST(imported_at AS DATE) FROM {config.RAW_TABLE}"
            ).fetchall()
        ]
        with self.transaction() as con:
            return self._rebuild_unified_pdq(con, dates)

    def upsert_unified(self, frame: pd.DataFrame) -> int:
        """Upsert non-PDQ rows into ``unified_cash_prices`` on its natural key.

        ``frame`` must have exactly the ``UNIFIED_COLUMNS`` (validated upstream by
        ``manual_override``). Existing rows with the same key are replaced.
        """
        missing = set(UNIFIED_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing columns for {config.UNIFIED_TABLE}: {sorted(missing)}")
        if frame.empty:
            return 0
        if (frame["source"] == "PDQ").any():
            raise ValueError("PDQ rows must come from the PDQ API, not upsert_unified()")

        frame = frame[list(UNIFIED_COLUMNS)]
        match = " AND ".join(f"u.{c} IS NOT DISTINCT FROM i.{c}" for c in UNIFIED_KEY)
        cols = ", ".join(UNIFIED_COLUMNS)
        with self.transaction() as con:
            con.register("incoming_unified", frame)
            try:
                con.execute(
                    f"DELETE FROM {config.UNIFIED_TABLE} u "
                    f"WHERE EXISTS (SELECT 1 FROM incoming_unified i WHERE {match})"
                )
                con.execute(
                    f"""
                    INSERT INTO {config.UNIFIED_TABLE} ({cols})
                    SELECT CAST(price_date AS DATE), province, pdq_zone, crop_symbol, source,
                           delivery_month, CAST(delivery_year AS INTEGER),
                           CAST(price_cad_mt AS DOUBLE), CAST(price_cad_bu AS DOUBLE)
                    FROM incoming_unified
                    """
                )
            finally:
                con.unregister("incoming_unified")
        return len(frame)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def query_df(self, sql: str, params: list | None = None) -> pd.DataFrame:
        """Run a query and return a DataFrame."""
        return self.con.execute(sql, params or []).df()

    def latest_imported_at(self) -> datetime | None:
        """Most recent PDQ ``imported_at`` stored, or ``None`` if empty."""
        row = self.con.execute(f"SELECT max(imported_at) FROM {config.RAW_TABLE}").fetchone()
        return row[0] if row else None

    def has_import(self, imported_at: datetime) -> bool:
        """Whether a PDQ import with this timestamp is already stored."""
        row = self.con.execute(
            f"SELECT count(*) FROM {config.RAW_TABLE} WHERE imported_at = ?", [imported_at]
        ).fetchone()
        return bool(row and row[0])

    # ------------------------------------------------------------------ #
    # Parquet
    # ------------------------------------------------------------------ #
    def _partitions(self, table: str) -> list[tuple[int, int]]:
        expr = _PARTITION_DATE[table]
        rows = self.con.execute(
            f"SELECT DISTINCT year({expr}), month({expr}) FROM {table} ORDER BY 1, 2"
        ).fetchall()
        return [(int(y), int(m)) for y, m in rows]

    def _partition_file(self, table: str, year: int, month: int) -> Path:
        return self.parquet_dir / f"year={year:04d}" / f"month={month:02d}" / f"{table}.parquet"

    def export_parquet(
        self,
        tables: Iterable[str] = (config.RAW_TABLE, config.UNIFIED_TABLE),
        months: Iterable[tuple[int, int]] | None = None,
    ) -> list[Path]:
        """Export tables to hive-partitioned Parquet (``year=YYYY/month=MM``).

        Args:
            tables: Tables to export.
            months: (year, month) partitions to write. ``None`` writes every
                partition and removes partition files whose month no longer
                has rows (a full export).

        Returns:
            Paths of the files written.
        """
        written: list[Path] = []
        full = months is None
        wanted = None if full else set(months)  # type: ignore[arg-type]
        for table in tables:
            expr = _PARTITION_DATE[table]
            present = self._partitions(table)
            targets = present if full else [p for p in present if p in wanted]
            for year, month in targets:
                path = self._partition_file(table, year, month)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".parquet.tmp")
                self.con.execute(
                    f"COPY (SELECT * FROM {table} "
                    f"WHERE year({expr}) = {year} AND month({expr}) = {month} "
                    f"ORDER BY ALL) TO {_sql_path(tmp)} (FORMAT parquet, COMPRESSION zstd)"
                )
                os.replace(tmp, path)
                written.append(path)
            if full:
                keep = {self._partition_file(table, y, m) for y, m in present}
                for stale in self.parquet_dir.glob(f"year=*/month=*/{table}.parquet"):
                    if stale not in keep:
                        stale.unlink()
                        log.info("Removed stale partition %s", stale)
        return written

    def read_parquet(self, table: str) -> pd.DataFrame:
        """Load a table back from the Parquet lake (adds ``year`` / ``month`` columns)."""
        pattern = self.parquet_dir / "year=*" / "month=*" / f"{table}.parquet"
        if not any(self.parquet_dir.glob(f"year=*/month=*/{table}.parquet")):
            return pd.DataFrame()
        return self.con.execute(
            f"SELECT * FROM read_parquet({_sql_path(pattern)}, hive_partitioning = true)"
        ).df()
