"""
transformer.py — Transform (T) + Load (L) layer.

All label normalisation is delegated to mappings.py.  This script
contains only business logic (FX conversion, cost addition) and
orchestration.

Summary table schema:
    Report_Date      TEXT   — YYYY-MM-DD
    Origin           TEXT   — e.g. "Canada – VC", "US Gulf", "US PNW"
    Commodity        TEXT   — e.g. "CWRS Wheat", "HRW Wheat"
    Delivery_Window  TEXT   — e.g. "Jun-26", "Prompt"
    Price_USD        REAL   — NULL for No-Bid
"""

import re
import sqlite3
import pandas as pd
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    CAN_COSTING_XLSX, DB_PDQ, DB_USW, DB_HAMMER, DB_SUMMARY, DB_ARCHIVE,
    PDQ_CSV, USW_XLSX, HAMMER_XLSX,
)
from mappings import (
    PDQ_ZONE_ORIGIN_MAP, PDQ_CLASS_LABEL, PDQ_WINDOW_MAP,
    PDQ_REPRESENTATIVE_ZONE, PDQ_REPRESENTATIVE_ZONE_DEFAULT,
    USW_CLASS_LABEL, USW_SPECIAL_GRADES, USW_WINDOW_MAP,
    HAMMER_COMMODITY_MAP,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [TRANSFORMER]  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CREATE_SUMMARY = """
CREATE TABLE IF NOT EXISTS wheat_summary (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    Report_Date      TEXT NOT NULL,
    Origin           TEXT NOT NULL,
    Commodity        TEXT NOT NULL,
    Delivery_Window  TEXT NOT NULL,
    Price_USD        REAL,
    Price_CAD        REAL,               -- FOB cost in CAD/MT (Canada rows only, NULL for USD-priced origins)
    Freight_Basis    TEXT,
    transformed_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_metadata (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at             TEXT,
    usdcad             REAL NOT NULL,
    prompt_window      TEXT,   -- Hammer prompt month e.g. "Jul-26"
    comparison_window  TEXT,   -- User-chosen spread baseline e.g. "Jul-26"
    report_date        TEXT
);
"""


# ═══════════════════════════════════════════════════════════════════════════
#  USD/CAD rate prompt
# ═══════════════════════════════════════════════════════════════════════════

def prompt_usdcad_rate() -> float:
    banner = (
        "\n"
        "╔══════════════════════════════════════════════════════╗\n"
        "║          USD / CAD Exchange Rate Required            ║\n"
        "╠══════════════════════════════════════════════════════╣\n"
        "║  Enter the current USD/CAD rate (CAD per 1 USD).    ║\n"
        "║  Example: 1.3625  means 1 USD = 1.3625 CAD          ║\n"
        "╚══════════════════════════════════════════════════════╝"
    )
    print(banner)
    for attempt in range(1, 4):
        try:
            raw  = input(f"  USDCAD Rate (attempt {attempt}/3): ").strip()
            rate = float(raw)
            if not (0.5 <= rate <= 2.5):
                print(f"  ⚠  {rate:.4f} is outside 0.50–2.50. Please re-enter.")
                continue
            print(f"  ✓  Using USDCAD = {rate:.4f}\n")
            return rate
        except ValueError:
            print(f"  ✗  '{raw}' is not a valid number.")
    raise RuntimeError("USD/CAD rate not provided after 3 attempts. Pipeline aborted.")


# ═══════════════════════════════════════════════════════════════════════════
#  Coefficient loader
# ═══════════════════════════════════════════════════════════════════════════

def load_coefficients(usdcad: float) -> dict:
    if not CAN_COSTING_XLSX.exists():
        raise FileNotFoundError(f"CAN Costing Input not found: {CAN_COSTING_XLSX}")

    xl = pd.ExcelFile(CAN_COSTING_XLSX)

    # ── PDQ Rail freight (CAD/MT by zone) ────────────────────────────────────
    rail       = xl.parse("PDQ_Rail")
    rail.columns = [c.strip() for c in rail.columns]
    freight_vc  = {}
    freight_stl = {}
    for _, row in rail.iterrows():
        zone = str(row["ZONE"]).strip().upper()
        if pd.notna(row.get("Freight_VC")):
            freight_vc[zone]  = float(row["Freight_VC"])
        if pd.notna(row.get("Freight_STL")):
            freight_stl[zone] = float(row["Freight_STL"])

    # ── Trading margin (CAD/MT by commodity) ─────────────────────────────────
    margins_df = xl.parse("Margins")
    margins_df.columns = [c.strip() for c in margins_df.columns]
    mc = [c for c in margins_df.columns if "margin" in c.lower()][0]
    margin = {str(r["Commodity"]).strip().upper(): float(r[mc])
              for _, r in margins_df.iterrows()
              if pd.notna(r["Commodity"]) and pd.notna(r[mc])}

    # ── Other fees / admin (CAD/MT by commodity) ─────────────────────────────
    fees_df = xl.parse("OtherFees")
    fees_df.columns = [c.strip() for c in fees_df.columns]
    fc = [c for c in fees_df.columns if "margin" in c.lower()][0]
    other_fee = {str(r["Commodity"]).strip().upper(): float(r[fc])
                 for _, r in fees_df.iterrows()
                 if pd.notna(r["Commodity"]) and pd.notna(r[fc])}

    # ── Primary elevation / handling (CAD/MT by commodity) ───────────────────
    # CGC_PrimaryElevation: col 0 = Commodity, col 4 = Average
    prim_df = xl.parse("CGC_PrimaryElevation", header=None)
    prim_elev = {}
    for _, row in prim_df.iterrows():
        comm = str(row.iloc[0]).strip().upper()
        val  = row.iloc[4]
        if comm not in ("COMMODITY", "NAN") and pd.notna(val):
            prim_elev[comm] = float(val)

    # ── Port / terminal elevation (CAD/MT by commodity) ──────────────────────
    # CGC_ElevationVC:  Viterra/G3/Richardson average — used for VC routing
    # CGC_ElevationSTL: average — used for STL routing
    elev_vc_df = xl.parse("CGC_ElevationVC", header=None)
    elev_vc = {}
    for _, row in elev_vc_df.iterrows():
        comm = str(row.iloc[0]).strip().upper()
        val  = row.iloc[4]        # Average column
        if comm not in ("COMMODITY", "NAN") and pd.notna(val):
            elev_vc[comm] = float(val)

    elev_stl_df = xl.parse("CGC_ElevationSTL", header=None)
    elev_stl = {}
    for _, row in elev_stl_df.iterrows():
        comm = str(row.iloc[0]).strip().upper()
        val  = row.iloc[4]        # Average column
        if comm not in ("COMMODITY", "NAN") and pd.notna(val):
            elev_stl[comm] = float(val)

    log.info(
        "Coefficients — VC zones: %d | STL zones: %d | "
        "Primary elev commodities: %d | VC elev: %d | STL elev: %d | USDCAD: %.4f",
        len(freight_vc), len(freight_stl),
        len(prim_elev), len(elev_vc), len(elev_stl), usdcad,
    )
    return {
        "usdcad":     usdcad,
        "freight_vc": freight_vc,
        "freight_stl": freight_stl,
        "margin":     margin,
        "other_fee":  other_fee,
        "prim_elev":  prim_elev,
        "elev_vc":    elev_vc,
        "elev_stl":   elev_stl,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _to_float(val) -> float | None:
    s = str(val).strip()
    if s in ("", "-", "NA", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _lookup(d: dict, *keys: str) -> float:
    for k in keys:
        if k.upper() in d:
            return d[k.upper()]
    return 0.0



# ── Protein-aware commodity label builders ────────────────────────────────────

_PRO_RE = re.compile(r'(\d+\.\d+|\d+)(?:\s*\()')

def _pdq_commodity_label(raw: str) -> tuple[str | None, str | None]:
    """
    Parse a PDQ commodity string into (class_key, commodity_label).
    '1 CWRS 13.5' → ('CWRS', 'CWRS 13.5%')
    '1 CWAD 13.0' → ('CWAD', 'CWAD 13.0%')
    Returns (None, None) if not a recognised wheat class.
    """
    upper = raw.upper()
    for key in PDQ_CLASS_LABEL:
        if key in upper:
            # Extract protein number that follows the class code
            idx   = upper.index(key) + len(key)
            after = upper[idx:].strip()
            m     = re.match(r'([\d]+\.?[\d]*)', after)
            if m:
                protein = float(m.group(1))
                label   = f"{key} {protein:.1f}%"
            else:
                label = key
            return key, label
    return None, None


def _usw_commodity_label(grade_raw: str) -> str:
    """
    Parse a USW wheat_class string into a protein-specific commodity label.

    Examples:
        'HRS 13.5 (15.3) Min'          → 'HRS 13.5%'
        'HRS 14.0 (15.9) Min (50 DHV)' → 'HRS 14.0% (50 DHV)'
        'SW 9.5 (10.8) Min'            → 'SW 9.5% Min'
        'SW 9.5 (10.8) Max'            → 'SW 9.5% Max'
        'SW 10.5 (11.9) Max'           → 'SW 10.5% Max'
        'HRW Ord'                      → 'HRW Ord'
        'SRW'                          → 'SRW'
        'SW Unspecified'               → 'SW Unspecified'
        'WW 10% Club'                  → 'WW 10% Club'

    Min/Max qualifiers are preserved because SW 9.5 Min and SW 9.5 Max
    are distinct grades with different prices (quality floor vs ceiling).
    DHV (Dark Hard Vitreous) is preserved as a separate quality spec.
    Multi-grade header rows (joined with ' / ') are filtered upstream.
    """
    grade = grade_raw.strip()

    # Check fixed special-case grades first (no protein number)
    if grade in USW_SPECIAL_GRADES:
        return USW_SPECIAL_GRADES[grade]

    # Identify class prefix
    class_label = None
    after = grade
    for prefix in USW_CLASS_LABEL:
        if grade.upper().startswith(prefix):
            class_label = USW_CLASS_LABEL[prefix]
            after       = grade[len(prefix):].strip()
            break
    if class_label is None:
        return grade   # unknown — return as-is

    # Extract 12%-moisture-basis protein (first number before the parenthesis)
    m = re.match(r'(\d+\.?\d*)\s*\(', after)
    if not m:
        return f"{class_label} {after}".strip()

    protein = float(m.group(1))

    # DHV qualifier (e.g. "50 DHV")
    dhv = re.search(r'\((\d+\s*DHV)\)', after)
    if dhv:
        return f"{class_label} {protein:.1f}% ({dhv.group(1)})"

    # Min / Max qualifier — only preserved for SW where Min ≠ Max means
    # different price levels. For HRS/HRW all grades are minimums so the
    # qualifier is redundant and omitted to keep labels clean.
    if class_label == "SW":
        qual = re.search(r'\b(Min|Max)\b', after, re.IGNORECASE)
        if qual:
            return f"{class_label} {protein:.1f}% {qual.group(1).capitalize()}"

    return f"{class_label} {protein:.1f}%"


def _hammer_commodity_label(origin_raw: str) -> str | None:
    """
    Return the commodity label for a Hammersmith origin string.
    Returns None if the origin is not in HAMMER_COMMODITY_MAP (→ log warning).
    """
    entry = HAMMER_COMMODITY_MAP.get(origin_raw)
    if entry is None:
        return None
    return entry["commodity"]


def _hammer_freight_basis(origin_raw: str) -> str:
    """Return the freight basis string for future C&F calculations."""
    entry = HAMMER_COMMODITY_MAP.get(origin_raw)
    return entry["freight_basis"] if entry else ""

# ═══════════════════════════════════════════════════════════════════════════
#  PDQ transform
# ═══════════════════════════════════════════════════════════════════════════

def transform_pdq(coeffs: dict) -> list[tuple]:
    log.info("Transforming PDQ …")
    con = sqlite3.connect(DB_PDQ)
    df  = pd.read_sql("SELECT * FROM raw_pdq", con)
    con.close()

    results          = []
    skipped_no_bid   = 0
    skipped_other    = 0
    skipped_zone     = 0

    for _, row in df.iterrows():
        zone_raw  = str(row["zone"]).strip().upper()
        comm_raw  = str(row["commodity"]).strip()

        # ── Commodity: parse class + protein from raw string ──────────────
        class_key, comm_norm = _pdq_commodity_label(comm_raw)
        if comm_norm is None:
            skipped_other += 1
            continue

        # ── Representative zone filter ────────────────────────────────────
        # When multiple zones are downloaded, only the designated zone per
        # commodity class is used for the FOB summary. All zones are stored
        # in raw_pdq. Edit PDQ_REPRESENTATIVE_ZONE in mappings.py to change.
        rep_zone = PDQ_REPRESENTATIVE_ZONE.get(class_key, PDQ_REPRESENTATIVE_ZONE_DEFAULT)
        if zone_raw != rep_zone:
            skipped_zone += 1
            continue

        # ── Origin from zone ──────────────────────────────────────────────
        origin = PDQ_ZONE_ORIGIN_MAP.get(zone_raw)
        if origin is None:
            log.warning("Unknown PDQ zone '%s' — add to mappings.py.", zone_raw)
            origin = f"Canada – {zone_raw}"

        # ── Delivery window ───────────────────────────────────────────────
        raw_window   = str(row["delivery_window"]).strip()
        clean_window = PDQ_WINDOW_MAP.get(raw_window, raw_window)

        # ── Date ──────────────────────────────────────────────────────────
        try:
            report_date = pd.to_datetime(row["import_date"], dayfirst=False).strftime("%Y-%m-%d")
        except Exception:
            report_date = str(row["import_date"])

        # ── No-Bid ────────────────────────────────────────────────────────
        cash_val = _to_float(row["cash"])
        if cash_val is None:
            skipped_no_bid += 1
            results.append((report_date, origin, comm_norm, clean_window, None, None, None))
            continue

        # ── Choose freight and port elevation based on routing ────────────────
        if "STL" in origin or "TBAY" in origin:
            freight   = coeffs["freight_stl"].get(zone_raw, 0.0)
            port_elev = _lookup(coeffs["elev_stl"], class_key, "WHEAT")
        else:
            freight   = coeffs["freight_vc"].get(zone_raw, 0.0)
            port_elev = _lookup(coeffs["elev_vc"],  class_key, "WHEAT")

        if freight == 0.0:
            log.warning("No freight for zone '%s' / origin '%s'.", zone_raw, origin)

        margin    = _lookup(coeffs["margin"],    class_key, "WHEAT")
        other_fee = _lookup(coeffs["other_fee"], class_key, "WHEAT")
        prim_elev = _lookup(coeffs["prim_elev"], class_key, "WHEAT")

        # ── Full inland-to-FOB formula ────────────────────────────────────────
        # FOB_CAD = CASH + Rail_Freight + Trading_Margin + Other_Fee
        #         + Primary_Elevation + Port/Terminal_Elevation
        # FOB_USD = FOB_CAD / USDCAD
        fob_cad = cash_val + freight + margin + other_fee + prim_elev + port_elev
        fob_usd = fob_cad / coeffs["usdcad"]

        # Store both CAD and USD — CAD enables FX sensitivity without re-running pipeline
        results.append((report_date, origin, comm_norm, clean_window,
                        round(fob_usd, 4), round(fob_cad, 4), None))

    log.info(
        "PDQ: %d rows | %d No-Bid | %d non-wheat skipped | %d non-representative zones skipped.",
        len(results), skipped_no_bid, skipped_other, skipped_zone,
    )
    return results


# ═══════════════════════════════════════════════════════════════════════════
#  USW transform
# ═══════════════════════════════════════════════════════════════════════════

def transform_usw() -> list[tuple]:
    log.info("Transforming USW …")
    con = sqlite3.connect(DB_USW)
    df  = pd.read_sql("SELECT * FROM raw_usw_prices", con)
    con.close()

    results = []
    skipped = 0

    for _, row in df.iterrows():
        price = _to_float(row["fob_usd_per_mt"])
        if price is None:
            skipped += 1
            continue

        report_date = str(row["report_date"])
        try:
            report_date = pd.to_datetime(report_date).strftime("%Y-%m-%d")
        except Exception:
            pass

        # Origin is already clean from the extractor (via mappings.USW_REGION_MAP)
        origin = str(row["export_region"]).strip()

        # Commodity: parse class + protein from grade string
        grade_raw = str(row["wheat_class"]).strip()
        # Skip multi-grade header rows (joined with ' / ' by extractor)
        if " / " in grade_raw:
            skipped += 1
            continue
        comm_norm = _usw_commodity_label(grade_raw)

        # Delivery window already cleaned by extractor
        window = str(row["delivery_window"]).strip()

        results.append((report_date, origin, comm_norm, window, round(price, 4), None, None))

    log.info("USW: %d rows | %d skipped (NA).", len(results), skipped)
    return results


# ═══════════════════════════════════════════════════════════════════════════
#  Hammer transform
# ═══════════════════════════════════════════════════════════════════════════

def transform_hammer() -> list[tuple]:
    log.info("Transforming Hammer …")
    con = sqlite3.connect(DB_HAMMER)
    df  = pd.read_sql("SELECT * FROM raw_hammer_fob", con)
    con.close()

    results = []
    skipped = 0

    for _, row in df.iterrows():
        if "wheat" not in str(row["commodity"]).lower():
            skipped += 1
            continue

        price = row["price_usd"]
        if price is None or pd.isna(price):
            skipped += 1
            continue

        origin_raw = str(row["origin"]).strip()
        entry      = HAMMER_COMMODITY_MAP.get(origin_raw)

        # ── exclude_fob filter ────────────────────────────────────────────
        # US Gulf wheat is covered in superior detail by USW; skip it here
        # to avoid duplicating less-granular entries in the summary.
        if entry and entry.get("exclude_fob", False):
            skipped += 1
            log.debug("Excluded (covered by USW): %s", origin_raw)
            continue

        try:
            report_date = pd.to_datetime(
                str(row["report_date"]), format="%d%b%y"
            ).strftime("%Y-%m-%d")
        except Exception:
            report_date = str(row["report_date"])

        origin = "International (Hammer)"

        comm_norm = _hammer_commodity_label(origin_raw)
        if comm_norm is None:
            log.warning("Unmapped Hammer origin '%s' — add to mappings.py.", origin_raw)
            comm_norm = f"Wheat – {origin_raw}"

        # delivery_window is already a calendar label (set by extractor)
        window        = str(row["delivery_window"]).strip()
        freight_basis = _hammer_freight_basis(origin_raw)

        results.append((report_date, origin, comm_norm, window, round(float(price), 4), None, freight_basis))

    log.info("Hammer: %d wheat rows loaded | %d skipped (non-wheat / no-price / excluded).", len(results), skipped)
    return results


CREATE_ARCHIVE = """
CREATE TABLE IF NOT EXISTS archive_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at        TEXT NOT NULL,
    source_hash   TEXT NOT NULL UNIQUE,
    pdq_file      TEXT,
    usw_file      TEXT,
    hammer_file   TEXT,
    usdcad        REAL,
    prompt_window TEXT,
    report_date   TEXT,
    row_count     INTEGER
);

-- Transformed summary (FOB USD + CAD prices, all origins)
CREATE TABLE IF NOT EXISTS archive_prices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES archive_runs(run_id),
    Report_Date      TEXT NOT NULL,
    Origin           TEXT NOT NULL,
    Commodity        TEXT NOT NULL,
    Delivery_Window  TEXT NOT NULL,
    Price_USD        REAL,
    Price_CAD        REAL,
    Freight_Basis    TEXT
);

-- Raw PDQ (Canadian elevator bids, all zones)
CREATE TABLE IF NOT EXISTS archive_pdq (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES archive_runs(run_id),
    delivery_window  TEXT,
    commodity        TEXT,
    zone             TEXT,
    import_date      TEXT,
    cash             TEXT,
    cash_change      TEXT,
    futures_month    TEXT,
    basis            TEXT,
    basis_change     TEXT
);

-- Raw USW prices (FOB USD/MT per grade per delivery window)
CREATE TABLE IF NOT EXISTS archive_usw_prices (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 INTEGER NOT NULL REFERENCES archive_runs(run_id),
    report_date            TEXT,
    export_region          TEXT,
    wheat_class            TEXT,
    futures_symbol         TEXT,
    delivery_window        TEXT,
    fob_usd_per_mt         TEXT,
    nearby_fob_usd_per_bu  TEXT,
    week_change_usd_per_bu TEXT
);

-- Raw USW futures settlements
CREATE TABLE IF NOT EXISTS archive_usw_futures (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES archive_runs(run_id),
    report_date      TEXT,
    futures_month    TEXT,
    exchange_symbol  TEXT,
    price_usd_per_mt TEXT
);

-- Raw Hammersmith FOB prices (with delivery window anchor)
CREATE TABLE IF NOT EXISTS archive_hammer_fob (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES archive_runs(run_id),
    report_date      TEXT,
    description      TEXT,
    commodity        TEXT,
    origin           TEXT,
    price_raw        TEXT,
    delivery_window  TEXT,
    price_usd        REAL,
    prompt_month     TEXT
);

-- Raw Hammersmith freight rates
CREATE TABLE IF NOT EXISTS archive_hammer_freight (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES archive_runs(run_id),
    report_date      TEXT,
    route            TEXT,
    rate_usd         TEXT,
    change_note      TEXT
);
"""


def _source_hash() -> str:
    """
    MD5 fingerprint of all three source files combined.
    Identical files = identical hash regardless of when the pipeline is run.
    Different files (new week) = different hash → triggers archive write.
    """
    import hashlib
    md5 = hashlib.md5()
    for path in (PDQ_CSV, USW_XLSX, HAMMER_XLSX):
        if path and path.exists():
            md5.update(path.read_bytes())
    return md5.hexdigest()


def write_archive(all_rows: list, usdcad: float,
                  prompt_window: str | None, report_date: str | None):
    """
    Append this run to global_wheat_history.db only if the source files
    have changed since the last archived run.

    Deduplication rule:
        Same source files → hash matches → skip (re-run, no new data)
        New source files  → hash differs → write new archive entry

    Writes:
        archive_runs           — one metadata row per unique run
        archive_prices         — transformed FOB summary (all origins)
        archive_pdq            — raw PDQ elevator bids (all zones)
        archive_usw_prices     — raw USW FOB prices (full forward curve)
        archive_usw_futures    — raw USW futures settlements
        archive_hammer_fob     — raw Hammer FOB prices
        archive_hammer_freight — raw Hammer freight rates

    The live summary DB is never affected by this function.
    Archive failures are logged as warnings only — pipeline always completes.
    """
    import datetime as _dt

    source_hash = _source_hash()
    con = sqlite3.connect(DB_ARCHIVE)

    try:
        con.executescript(CREATE_ARCHIVE)

        # ── Deduplication check ───────────────────────────────────────────────
        existing = con.execute(
            "SELECT run_id, run_at FROM archive_runs WHERE source_hash = ?",
            (source_hash,)
        ).fetchone()

        if existing:
            log.info(
                "Archive: source files unchanged since run #%d (%s) — "
                "skipping duplicate write.",
                existing[0], existing[1],
            )
            return

        # ── Insert run metadata ───────────────────────────────────────────────
        run_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur    = con.cursor()
        cur.execute(
            """
            INSERT INTO archive_runs
                (run_at, source_hash, pdq_file, usw_file, hammer_file,
                 usdcad, prompt_window, report_date, row_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_at, source_hash,
                PDQ_CSV.name     if PDQ_CSV     else None,
                USW_XLSX.name    if USW_XLSX    else None,
                HAMMER_XLSX.name if HAMMER_XLSX else None,
                usdcad, prompt_window, report_date, len(all_rows),
            ),
        )
        run_id = cur.lastrowid

        # ── Transformed summary prices ────────────────────────────────────────
        cur.executemany(
            """
            INSERT INTO archive_prices
                (run_id, Report_Date, Origin, Commodity, Delivery_Window,
                 Price_USD, Price_CAD, Freight_Basis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(run_id, r[0], r[1], r[2], r[3], r[4], r[5], r[6])
             for r in all_rows],
        )

        # ── Raw PDQ ───────────────────────────────────────────────────────────
        raw = sqlite3.connect(DB_PDQ)
        pdq_rows = raw.execute(
            "SELECT delivery_window, commodity, zone, import_date, "
            "cash, cash_change, futures_month, basis, basis_change "
            "FROM raw_pdq"
        ).fetchall()
        raw.close()
        cur.executemany(
            """
            INSERT INTO archive_pdq
                (run_id, delivery_window, commodity, zone, import_date,
                 cash, cash_change, futures_month, basis, basis_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(run_id,) + r for r in pdq_rows],
        )

        # ── Raw USW prices ────────────────────────────────────────────────────
        raw = sqlite3.connect(DB_USW)
        usw_price_rows = raw.execute(
            "SELECT report_date, export_region, wheat_class, futures_symbol, "
            "delivery_window, fob_usd_per_mt, "
            "nearby_fob_usd_per_bu, week_change_usd_per_bu "
            "FROM raw_usw_prices"
        ).fetchall()
        usw_fut_rows = raw.execute(
            "SELECT report_date, futures_month, exchange_symbol, price_usd_per_mt "
            "FROM raw_usw_futures"
        ).fetchall()
        raw.close()
        cur.executemany(
            """
            INSERT INTO archive_usw_prices
                (run_id, report_date, export_region, wheat_class,
                 futures_symbol, delivery_window, fob_usd_per_mt,
                 nearby_fob_usd_per_bu, week_change_usd_per_bu)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(run_id,) + r for r in usw_price_rows],
        )
        cur.executemany(
            """
            INSERT INTO archive_usw_futures
                (run_id, report_date, futures_month,
                 exchange_symbol, price_usd_per_mt)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(run_id,) + r for r in usw_fut_rows],
        )

        # ── Raw Hammer FOB + freight ──────────────────────────────────────────
        raw = sqlite3.connect(DB_HAMMER)
        hammer_fob_rows = raw.execute(
            "SELECT report_date, description, commodity, origin, "
            "price_raw, delivery_window, price_usd, prompt_month "
            "FROM raw_hammer_fob"
        ).fetchall()
        hammer_frt_rows = raw.execute(
            "SELECT report_date, route, rate_usd, change_note "
            "FROM raw_hammer_freight"
        ).fetchall()
        raw.close()
        cur.executemany(
            """
            INSERT INTO archive_hammer_fob
                (run_id, report_date, description, commodity, origin,
                 price_raw, delivery_window, price_usd, prompt_month)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(run_id,) + r for r in hammer_fob_rows],
        )
        cur.executemany(
            """
            INSERT INTO archive_hammer_freight
                (run_id, report_date, route, rate_usd, change_note)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(run_id,) + r for r in hammer_frt_rows],
        )

        con.commit()
        log.info(
            "Archive run #%d complete — "
            "prices: %d | pdq: %d | usw_prices: %d | usw_futures: %d | "
            "hammer_fob: %d | hammer_freight: %d  (files: %s | %s | %s)",
            run_id,
            len(all_rows), len(pdq_rows),
            len(usw_price_rows), len(usw_fut_rows),
            len(hammer_fob_rows), len(hammer_frt_rows),
            PDQ_CSV.name     if PDQ_CSV     else "—",
            USW_XLSX.name    if USW_XLSX    else "—",
            HAMMER_XLSX.name if HAMMER_XLSX else "—",
        )

    except sqlite3.Error as exc:
        log.warning(
            "Archive write failed — live summary is unaffected. Error: %s", exc
        )
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

def run(usdcad: float | None = None, prompt_window: str | None = None,
        comparison_window: str | None = None):
    if usdcad is None:
        usdcad = prompt_usdcad_rate()

    # Store prompt_window as function attribute so the DB write can access it
    run._prompt_window     = prompt_window
    run._comparison_window = comparison_window

    coeffs   = load_coefficients(usdcad)
    all_rows = transform_pdq(coeffs) + transform_usw() + transform_hammer()
    log.info("Total rows: %d", len(all_rows))

    try:
        # Delete the existing DB file entirely so we always start with a
        # clean schema. This prevents column-mismatch errors when the schema
        # has changed since the last run. All source data is re-extracted
        # from the raw databases on every run, so nothing is lost.
        if DB_SUMMARY.exists():
            DB_SUMMARY.unlink()
            log.info("Removed stale summary DB — rebuilding with current schema.")

        con = sqlite3.connect(DB_SUMMARY)
        cur = con.cursor()
        cur.executescript(CREATE_SUMMARY)
        cur.executemany(
            """
            INSERT INTO wheat_summary
                (Report_Date, Origin, Commodity, Delivery_Window,
                 Price_USD, Price_CAD, Freight_Basis)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            all_rows,
        )
        # Store run parameters so the report can read them without prompting
        report_date_val = all_rows[0][0] if all_rows else None
        import datetime as _dt
        cur.execute(
            """
            INSERT INTO run_metadata
                (run_at, usdcad, prompt_window, comparison_window, report_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                usdcad,
                getattr(run, "_prompt_window",     None),
                getattr(run, "_comparison_window", None),
                report_date_val,
            ),
        )
        con.commit()
        log.info("Summary populated with %d rows.", len(all_rows))
    except sqlite3.Error as exc:
        log.error("Database error: %s", exc)
        raise
    finally:
        con.close()

    # ── Archive write (append-only, deduplicated by source file hash) ─────────
    write_archive(all_rows, usdcad, prompt_window, report_date_val)

    log.info("Transformation complete.")


if __name__ == "__main__":
    run()
