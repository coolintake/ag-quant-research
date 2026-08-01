"""
extract_hammer.py — Extract (E) layer for the Hammersmith price sheet.

Delivery window anchoring
─────────────────────────
Hammersmith encodes a 3-window price curve using directional notation:
    "USD 235<<240"     rising range  → Prompt < Mid < Deferred
    "USD 240>>235"     falling range → Prompt > Mid > Deferred
    "USD 230>>230"     steady        → all three windows equal
    "USD 239/242 spot" spot only     → single Spot window, no curve

The report does not state which calendar months these windows correspond
to. The caller (run_pipeline.py) asks the user to supply the prompt month
(e.g. "Jun-26"), and this extractor maps:
    Prompt   → prompt_month
    Mid      → prompt_month + 1
    Deferred → prompt_month + 2
    Spot     → "Spot"  (no anchor needed; immediate shipment)

Both raw_hammer_fob and global_wheat_summary therefore store calendar
labels (e.g. "Jun-26") rather than the generic positional labels, enabling
apples-to-apples joins with PDQ and USW rows.
"""

import sqlite3
import pandas as pd
import re
import sys
import logging
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import HAMMER_XLSX, DB_HAMMER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [HAMMER-EXTRACT]  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CREATE_FOB = """
CREATE TABLE IF NOT EXISTS raw_hammer_fob (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT,       -- e.g. "30May26"
    description     TEXT,       -- full original description
    commodity       TEXT,       -- parsed commodity category (e.g. "Wheat")
    origin          TEXT,       -- text after first comma in description
    price_raw       TEXT,       -- original price string verbatim
    delivery_window TEXT,       -- calendar label e.g. "Jun-26", or "Spot"
    price_usd       REAL,       -- parsed USD/MT for this window (NULL if unparseable)
    prompt_month    TEXT,       -- the prompt window supplied at runtime (e.g. "Jun-26")
    extracted_at    TEXT DEFAULT (datetime('now'))
);
"""

CREATE_FREIGHT = """
CREATE TABLE IF NOT EXISTS raw_hammer_freight (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT,
    route           TEXT,
    rate_usd        TEXT,
    change_note     TEXT,
    extracted_at    TEXT DEFAULT (datetime('now'))
);
"""

def _detect_hammer_sheets(xl: pd.ExcelFile) -> tuple[str, str]:
    """
    Find the FOB and Freight sheet names regardless of the date prefix.
    Hammer sheets follow the pattern '<date>_FOB' and '<date>_Freight'.
    Raises ValueError if either sheet cannot be found.
    """
    sheets    = xl.sheet_names
    fob_sheet = next((s for s in sheets if s.upper().endswith("_FOB")),    None)
    frt_sheet = next((s for s in sheets if s.upper().endswith("_FREIGHT")), None)

    if not fob_sheet:
        raise ValueError(
            f"Cannot find a FOB sheet in {xl.io}.\n"
            f"Expected a sheet ending in '_FOB'. Available: {sheets}"
        )
    if not frt_sheet:
        raise ValueError(
            f"Cannot find a Freight sheet in {xl.io}.\n"
            f"Expected a sheet ending in '_FREIGHT'. Available: {sheets}"
        )
    return fob_sheet, frt_sheet

# Matches: "USD 235<<240"  "USD 240>>235"  "USD 230>>230"  "USD 239/242 spot"
_RANGE_RE  = re.compile(
    r"USD\s+(\d+(?:\.\d+)?)\s*[/<>]{1,2}\s*(\d+(?:\.\d+)?)", re.I
)
_SINGLE_RE = re.compile(r"USD\s+(\d+(?:\.\d+)?)", re.I)


def _month_label(base: datetime, offset: int) -> str:
    """Return 'Mon-YY' label offset months from base. e.g. Jun-26, Jul-26."""
    dt = base + relativedelta(months=offset)
    return dt.strftime("%b-%y")


def _parse_windows(
    price_raw: str, prompt_dt: datetime
) -> list[tuple[str, float]]:
    """
    Parse a Hammersmith price string into (delivery_window, price_usd) tuples.

    Four cases:

    1. SPOT   "USD 239/242 spot"  — keyword 'spot': single Spot row at mid.
    2. FLAT   "USD 285 flat to Sep" — keyword 'flat': steady price across
              3 calendar windows (same price for Prompt, Mid, Deferred).
    3. RANGE  "USD 235<<240" / "USD 242>>235" — rising or falling curve:
              3 rows: Prompt=lo, Mid=interpolated, Deferred=hi.
    4. SINGLE "USD 285"  — no range, no keyword: treat as Spot.

    Note: 'flat' and 'spot' are intentionally distinct.
      'flat' = price is steady across the forward curve  → 3 rows
      'spot' = immediate shipment indication             → 1 row
    """
    is_spot = bool(re.search(r'\bspot\b', price_raw, re.I))
    is_flat = bool(re.search(r'\bflat\b', price_raw, re.I))

    m = _RANGE_RE.search(price_raw)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        mid = (lo + hi) / 2.0
        if is_spot:
            # Bid/offer spread for spot shipment → single Spot at mid
            return [("Spot", mid)]
        # Forward curve (rising, falling, or steady) → always 3 windows
        return [
            (_month_label(prompt_dt, 0), lo),
            (_month_label(prompt_dt, 1), mid),
            (_month_label(prompt_dt, 2), hi),
        ]

    # No range found — single price value
    m2 = _SINGLE_RE.search(price_raw)
    if m2:
        val = float(m2.group(1))
        if is_flat:
            # "USD 285 flat to Sep" = steady across 3 windows
            return [
                (_month_label(prompt_dt, 0), val),
                (_month_label(prompt_dt, 1), val),
                (_month_label(prompt_dt, 2), val),
            ]
        # Bare single price or spot → treat as Spot
        return [("Spot", val)]

    return []


def _parse_commodity_and_origin(description: str) -> tuple[str, str]:
    parts = [p.strip() for p in description.split(",", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return description, ""


def run(prompt_month: str | None = None):
    """
    prompt_month: calendar label for the Hammersmith prompt window,
                  e.g. "Jun-26".  If None the function asks interactively.
    """
    if not HAMMER_XLSX.exists():
        raise FileNotFoundError(f"Hammer source file not found: {HAMMER_XLSX}")

    # ── Resolve prompt month to a datetime for offset arithmetic ─────────────
    if prompt_month is None:
        raise ValueError("prompt_month must be supplied by run_pipeline.py")

    try:
        prompt_dt = datetime.strptime(prompt_month, "%b-%y")
    except ValueError:
        raise ValueError(
            f"prompt_month '{prompt_month}' is not in 'Mon-YY' format "
            f"(e.g. 'Jun-26')."
        )

    log.info(
        "Reading Hammer workbook: %s  |  Prompt window: %s", HAMMER_XLSX, prompt_month
    )

    xl                    = pd.ExcelFile(HAMMER_XLSX)
    fob_sheet, frt_sheet  = _detect_hammer_sheets(xl)
    report_date           = fob_sheet.split("_")[0]   # e.g. "06Jun26"
    log.info("Sheets detected — FOB: %s  |  Freight: %s", fob_sheet, frt_sheet)

    # ── FOB sheet ─────────────────────────────────────────────────────────────
    fob_df   = xl.parse(fob_sheet, header=None, dtype=str)
    fob_rows = []

    for _, row in fob_df.iterrows():
        desc  = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        price = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
        if not desc or desc.lower() == "nan":
            continue

        commodity, origin = _parse_commodity_and_origin(desc)
        windows = _parse_windows(price, prompt_dt)

        if not windows:
            # Store unparseable rows with NULL price — no data silently lost
            fob_rows.append((
                report_date, desc, commodity, origin,
                price, "Unknown", None, prompt_month,
            ))
        else:
            for window_label, price_usd in windows:
                fob_rows.append((
                    report_date, desc, commodity, origin,
                    price, window_label, price_usd, prompt_month,
                ))

    log.info("FOB rows (exploded): %d", len(fob_rows))

    # ── Freight sheet ──────────────────────────────────────────────────────────
    freight_df   = xl.parse(frt_sheet, header=None, dtype=str)
    freight_rows = []

    for _, row in freight_df.iterrows():
        route  = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""
        rate   = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
        change = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
        if not route or route.lower() == "nan":
            continue
        freight_rows.append((report_date, route, rate, change))

    log.info("Freight rows: %d", len(freight_rows))

    # ── Write to SQLite ───────────────────────────────────────────────────────
    log.info("Writing to database: %s", DB_HAMMER)
    try:
        con = sqlite3.connect(DB_HAMMER)
        cur = con.cursor()
        cur.executescript(
            "DROP TABLE IF EXISTS raw_hammer_fob;\n"
            "DROP TABLE IF EXISTS raw_hammer_freight;\n"
            + CREATE_FOB + "\n" + CREATE_FREIGHT
        )
        cur.executemany(
            """
            INSERT INTO raw_hammer_fob
                (report_date, description, commodity, origin,
                 price_raw, delivery_window, price_usd, prompt_month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fob_rows,
        )
        cur.executemany(
            """
            INSERT INTO raw_hammer_freight
                (report_date, route, rate_usd, change_note)
            VALUES (?, ?, ?, ?)
            """,
            freight_rows,
        )
        con.commit()
        log.info(
            "Inserted %d FOB rows and %d freight rows.",
            len(fob_rows), len(freight_rows),
        )
    except sqlite3.Error as exc:
        log.error("Database error: %s", exc)
        raise
    finally:
        con.close()

    log.info("Hammer extraction complete.")


if __name__ == "__main__":
    run()
