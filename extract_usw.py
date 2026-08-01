"""
extract_usw.py — Extract (E) layer for the USW Weekly Price Report.

Reads from the manually cleaned 'Table_FOB' sheet, which has:
  - Col 0: full region name on every row (no split merged cells)
  - Col 5: individual grade string per row (no multi-grade merged headers)
  - Col 22,29,35,38,43,45,50: FOB $/MT for each delivery window

One raw DB row is written per (region, grade, delivery_window) combination.
Region → clean Origin label mapping is handled by mappings.USW_REGION_MAP.
Delivery window labels are cleaned via mappings.USW_WINDOW_MAP.
"""

import sqlite3
import pandas as pd
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import USW_XLSX, DB_USW
from mappings import USW_REGION_ALIASES, USW_WINDOW_MAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [USW-EXTRACT]  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Sheet to read from
FOB_SHEET = "Table_FOB"

# Data rows: row 4 onwards (rows 0-3 are title / header)
DATA_ROW_START = 4

# Column positions that are stable across USW report versions
COL_REGION         = 0
COL_GRADE          = 5
COL_FUTURES_SYMBOL = 10
COL_NEARBY_BU      = 11
COL_WEEK_CHANGE_BU = 13

# NOTE: FOB $/MT delivery window columns are NOT hardcoded here.
# USW periodically adds or shifts columns between report versions.
# The _detect_delivery_cols() function reads the column headers at
# runtime to find the correct positions automatically each week.

CREATE_PRICES = """
CREATE TABLE IF NOT EXISTS raw_usw_prices (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date             TEXT,
    export_region           TEXT,   -- clean Origin label e.g. "US Gulf"
    wheat_class             TEXT,   -- grade string e.g. "HRS 13.5 (15.3) Min"
    futures_symbol          TEXT,
    delivery_window         TEXT,   -- clean label e.g. "Jun-26"
    fob_usd_per_mt          TEXT,   -- numeric string or "NA"
    nearby_fob_usd_per_bu   TEXT,
    week_change_usd_per_bu  TEXT,
    extracted_at            TEXT DEFAULT (datetime('now'))
);
"""

CREATE_FUTURES = """
CREATE TABLE IF NOT EXISTS raw_usw_futures (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date      TEXT,
    futures_month    TEXT,
    exchange_symbol  TEXT,
    price_usd_per_mt TEXT,
    extracted_at     TEXT DEFAULT (datetime('now'))
);
"""


def _clean(val) -> str:
    if pd.isna(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none") else s


def _resolve_region(raw: str) -> str:
    """
    Map any reasonable spelling of a USW export region to the canonical
    Origin label, using USW_REGION_ALIASES in mappings.py.

    Lookup is case-insensitive and strips leading/trailing whitespace so
    "Gulf of Mexico", "gulf of mexico", "GM", "GOM" all resolve to "US Gulf".

    If no alias matches, the raw string is returned unchanged and a warning
    is logged — add the new spelling to mappings.USW_REGION_ALIASES to fix.
    """
    key = raw.strip().upper()
    label = USW_REGION_ALIASES.get(key)
    if label:
        return label
    log.warning(
        "Unknown USW region %r — add to mappings.USW_REGION_ALIASES.", raw
    )
    return raw.strip()


def _detect_delivery_cols(df: pd.DataFrame) -> dict[int, str]:
    """
    Scan the header rows (2 and 3) of Table_FOB to locate every column
    that has:
        row 2 — a delivery month label  e.g. 'JUN (N26)'
        row 3 — 'FOB\n$/MT' or similar

    Returns {col_index: raw_window_label}.

    This makes the extractor immune to USW shifting or adding columns
    between weekly report versions — no hardcoded positions needed.
    """
    cols = {}
    for ci in range(df.shape[1]):
        month = _clean(df.iloc[2, ci])
        sub   = _clean(df.iloc[3, ci])
        if month and "(" in month and "FOB" in sub and "MT" in sub:
            cols[ci] = month
    return cols


def run():
    if not USW_XLSX.exists():
        raise FileNotFoundError(f"USW source file not found: {USW_XLSX}")

    xl = pd.ExcelFile(USW_XLSX)
    if FOB_SHEET not in xl.sheet_names:
        raise ValueError(
            f"Sheet '{FOB_SHEET}' not found in {USW_XLSX.name}.\n"
            f"Available sheets: {xl.sheet_names}\n"
            f"Please add the 'Table_FOB' sheet to the workbook."
        )

    log.info("Reading USW workbook: %s  |  Sheet: %s", USW_XLSX, FOB_SHEET)
    df = xl.parse(FOB_SHEET, header=None, dtype=str)

    # ── Auto-detect delivery window columns from headers ──────────────────────
    delivery_cols = _detect_delivery_cols(df)
    if not delivery_cols:
        raise ValueError(
            f"Could not detect any FOB $/MT delivery window columns in "
            f"'{FOB_SHEET}'. Check that rows 2-3 contain month labels "
            f"(e.g. 'JUN (N26)') and 'FOB\\n$/MT' sub-headers."
        )
    log.info(
        "Detected %d delivery window columns: %s",
        len(delivery_cols),
        {v: k for k, v in delivery_cols.items()},
    )

    # ── Parse report date from title cell ────────────────────────────────────
    title_cell  = _clean(df.iloc[0, 0])
    report_date = title_cell.replace("Weekly Price Report", "").strip()
    log.info("Report date: %s", report_date)

    # ── Parse price rows ──────────────────────────────────────────────────────
    price_rows = []
    skipped    = 0

    for row_idx in range(DATA_ROW_START, df.shape[0]):
        row = df.iloc[row_idx]

        region_raw = _clean(row.iloc[COL_REGION])
        grade      = _clean(row.iloc[COL_GRADE])

        # Skip rows with no grade (blank separators, footnote rows etc.)
        if not grade:
            skipped += 1
            continue

        # Skip multi-grade header cells (contain newlines from merged cells)
        if "\n" in grade:
            skipped += 1
            continue

        # Clean region → Origin label
        region = _resolve_region(region_raw) if region_raw else ""

        futures_sym = _clean(row.iloc[COL_FUTURES_SYMBOL])
        nearby_bu   = _clean(row.iloc[COL_NEARBY_BU])   or "NA"
        chg_bu      = _clean(row.iloc[COL_WEEK_CHANGE_BU]) or "NA"

        # One row per delivery window
        for col_idx, raw_window in delivery_cols.items():
            clean_window = USW_WINDOW_MAP.get(raw_window, raw_window)
            fob_mt       = _clean(row.iloc[col_idx]) if col_idx < len(row) else ""
            price_str    = fob_mt if fob_mt else "NA"

            price_rows.append((
                report_date,
                region,
                grade,
                futures_sym,
                clean_window,
                price_str,
                nearby_bu,
                chg_bu,
            ))

    log.info(
        "Price rows extracted: %d  |  Rows skipped (blank/header): %d",
        len(price_rows), skipped,
    )

    # ── Futures settlement rows (Table 1 rows 34-42) ──────────────────────────
    # Still read from Table 1 since Table_FOB doesn't include them
    futures_rows = []
    try:
        df1 = xl.parse("Table 1", header=None, dtype=str)
        futures_months = {}
        for ci in range(df1.shape[1]):
            cell = _clean(df1.iloc[35, ci])
            if cell and "(" in cell:
                futures_months[ci] = cell.replace("\n", " ")
        for row_idx in range(37, min(45, df1.shape[0])):
            sym = _clean(df1.iloc[row_idx, 10])
            if not sym:
                continue
            for ci, month_label in futures_months.items():
                price = _clean(df1.iloc[row_idx, ci])
                if price:
                    futures_rows.append((report_date, month_label, sym, price))
        log.info("Futures settlement rows: %d", len(futures_rows))
    except Exception as exc:
        log.warning("Could not extract futures settlements: %s", exc)

    # ── Write to SQLite ───────────────────────────────────────────────────────
    log.info("Writing to database: %s", DB_USW)
    try:
        con = sqlite3.connect(DB_USW)
        cur = con.cursor()
        cur.executescript(
            "DROP TABLE IF EXISTS raw_usw_prices;\n"
            "DROP TABLE IF EXISTS raw_usw_futures;\n"
            + CREATE_PRICES + "\n" + CREATE_FUTURES
        )
        cur.executemany(
            """
            INSERT INTO raw_usw_prices
                (report_date, export_region, wheat_class, futures_symbol,
                 delivery_window, fob_usd_per_mt,
                 nearby_fob_usd_per_bu, week_change_usd_per_bu)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            price_rows,
        )
        cur.executemany(
            """
            INSERT INTO raw_usw_futures
                (report_date, futures_month, exchange_symbol, price_usd_per_mt)
            VALUES (?, ?, ?, ?)
            """,
            futures_rows,
        )
        con.commit()
        log.info(
            "Inserted %d price rows and %d futures rows.",
            len(price_rows), len(futures_rows),
        )
    except sqlite3.Error as exc:
        log.error("Database error: %s", exc)
        raise
    finally:
        con.close()

    log.info("USW extraction complete.")


if __name__ == "__main__":
    run()
