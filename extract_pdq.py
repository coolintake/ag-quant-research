"""
extract_pdq.py — Extract (E) layer for the PDQ price feed.

Reads the raw PDQ CSV export exactly as it appears on disk and writes
every field into db_pdq.db → table raw_pdq.  No transformations are
applied; all values are stored as TEXT to preserve the original strings
(e.g. '-', blanks, formatted numbers).

The MONTH column (e.g. "JUN '26") IS the delivery window for PDQ rows
and is stored verbatim as `delivery_window`.
"""

import sqlite3
import pandas as pd
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PDQ_CSV, DB_PDQ

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [PDQ-EXTRACT]  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS raw_pdq (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_window TEXT,       -- e.g. "JUN '26"  — the PDQ MONTH column
    commodity       TEXT,       -- e.g. "1 CWRS 13.5"
    zone            TEXT,       -- e.g. "NE SASK"
    import_date     TEXT,       -- e.g. "05/27/2026"
    cash            TEXT,       -- CAD/MT numeric string, or '-' for No-Bid
    cash_change     TEXT,
    futures_month   TEXT,
    basis           TEXT,
    basis_change    TEXT,
    extracted_at    TEXT DEFAULT (datetime('now'))
);
"""


def run():
    if not PDQ_CSV.exists():
        raise FileNotFoundError(f"PDQ source file not found: {PDQ_CSV}")

    log.info("Reading PDQ CSV: %s", PDQ_CSV)
    df = pd.read_csv(PDQ_CSV, dtype=str)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    expected = {"month", "commodity", "zone", "import_date",
                "cash", "cash_change", "futures_month", "basis", "basis_change"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"PDQ CSV is missing expected columns: {missing}")

    log.info("Rows read: %d", len(df))

    log.info("Writing to database: %s", DB_PDQ)
    try:
        con = sqlite3.connect(DB_PDQ)
        cur = con.cursor()
        cur.executescript("DROP TABLE IF EXISTS raw_pdq;\n" + CREATE_TABLE)

        rows = [
            (
                row["month"],           # delivery_window
                row["commodity"],
                row["zone"],
                row["import_date"],
                row["cash"],
                row["cash_change"],
                row["futures_month"],
                row["basis"],
                row["basis_change"],
            )
            for _, row in df.iterrows()
        ]

        cur.executemany(
            """
            INSERT INTO raw_pdq
                (delivery_window, commodity, zone, import_date,
                 cash, cash_change, futures_month, basis, basis_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.commit()
        log.info("Inserted %d rows into raw_pdq.", len(rows))

    except sqlite3.Error as exc:
        log.error("Database error: %s", exc)
        raise
    finally:
        con.close()

    log.info("PDQ extraction complete.")


if __name__ == "__main__":
    run()
