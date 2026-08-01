"""
report_arbitrage.py — Global Wheat Arbitrage Matrix (ex-Durum)

Reads from global_wheat_summary.db and prints a formatted matrix
showing Canadian wheat benchmarks vs international competition,
organised by protein tier.

Usage:
    python report_arbitrage.py              # print to console
    python report_arbitrage.py --save       # also save to .txt file
    python report_arbitrage.py --db PATH    # use a specific DB file
"""

import sqlite3
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_SUMMARY
from mappings import (
    MATRIX_COMMODITIES,
    MATRIX_BASELINES,
    SPOT_WINDOW,
)
from report_utils import get_comparison_window, derive_display_windows

# ── Layout constants ───────────────────────────────────────────────────────────
W_ORIGIN = 14
W_GRADE  = 18
W_PRICE  =  9   # per window column
W_SPREAD =  9
DIVIDER  = " " + "─" * 66
BORDER   = " " + "═" * 66

TIER_LABELS = {
    "High": "HIGH PRO",
    "Mid":  "MID PRO",
    "Low":  "LOW PRO",
}

MOISTURE_NOTE = (
    "Note: Canadian grades quoted at 13.5% moisture basis. "
    "US grades at 12% moisture basis. "
    "International grades typically DMB."
)


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_prices(db_path: Path, display_windows: list[str]) -> dict:
    """
    Returns nested dict: prices[(origin, commodity)][window] = price or None.
    Spot prices are mapped to the first display window with a _spot flag.
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"Summary database not found: {db_path}\n"
            f"Run python run_pipeline.py first."
        )
    active     = {k: v for k, v in MATRIX_COMMODITIES.items()
                  if not v.get("suppress", False)}
    prompt_col = display_windows[0] if display_windows else "Jul-26"

    con  = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT Origin, Commodity, Delivery_Window, Price_USD "
        "FROM wheat_summary WHERE Price_USD IS NOT NULL"
    ).fetchall()
    con.close()

    prices = {}
    for origin, commodity, window, price in rows:
        if commodity not in active:
            continue
        key = (origin, commodity)
        if key not in prices:
            prices[key] = {}
        col = prompt_col if window == SPOT_WINDOW else window
        if window == SPOT_WINDOW:
            prices[key]["_spot"] = True
        prices[key][col] = price
    return prices


def load_metadata(db_path: Path) -> dict:
    """Read usdcad rate and prompt_window from run_metadata table."""
    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT usdcad, prompt_window, report_date "
            "FROM run_metadata ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row:
            return {"usdcad": row[0], "prompt_window": row[1], "report_date": row[2]}
    except Exception:
        pass
    return {"usdcad": None, "prompt_window": None, "report_date": None}


def get_report_date(db_path: Path) -> str:
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT Report_Date FROM wheat_summary ORDER BY Report_Date DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        return "Unknown"
    try:
        return datetime.strptime(row[0], "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        return row[0]


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _price(val, spot=False) -> str:
    if val is None:
        return f"{'—':>{W_PRICE}}"
    s = f"${val:.2f}"
    if spot:
        s += "*"
    return f"{s:>{W_PRICE}}"


def _spread(val) -> str:
    if val is None:
        return f"{'—':>{W_SPREAD}}"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:>.2f}".rjust(W_SPREAD)


def _col_header(windows: list[str], spread_window: str, has_spread: bool) -> str:
    wins = "".join(f"{w:>{W_PRICE}}" for w in windows)
    spread_hdr = f"  vs {spread_window}" if has_spread else ""
    return (
        f" {'Origin':{W_ORIGIN}}"
        f"{'Grade':{W_GRADE}}"
        f"{wins}"
        f"{spread_hdr}"
    )


# ── Tier renderer ──────────────────────────────────────────────────────────────

def render_tier(tier: str, prices: dict, windows: list[str],
                spread_window: str, lines: list):
    baseline_cfg = MATRIX_BASELINES[tier]
    has_baseline = baseline_cfg is not None

    lines.append(f" {TIER_LABELS[tier]}")
    lines.append(DIVIDER)
    lines.append(_col_header(windows, spread_window, has_baseline))
    lines.append(DIVIDER)

    active = {k: v for k, v in MATRIX_COMMODITIES.items()
              if v.get("tier") == tier and not v.get("suppress", False)}

    # Baseline spread-window price
    baseline_spread_price = None
    if has_baseline:
        for (origin, commodity), wp in prices.items():
            if commodity == baseline_cfg["commodity"]:
                baseline_spread_price = wp.get(spread_window)
                break

    data_rows    = []
    baseline_row = None

    for commodity, cfg in active.items():
        origin_short = cfg["origin_short"]

        matched = {}
        is_spot = False
        for (origin, comm), wp in prices.items():
            if comm == commodity and origin_short.lower() in origin.lower():
                matched = wp; is_spot = wp.get("_spot", False); break
        if not matched:
            for (origin, comm), wp in prices.items():
                if comm == commodity:
                    matched = wp; is_spot = wp.get("_spot", False); break
        if not matched:
            continue

        # Pull price for each display window
        win_prices = [matched.get(w) for w in windows]
        spread_price = matched.get(spread_window)

        spread = None
        if has_baseline and baseline_spread_price is not None and spread_price is not None:
            spread = round(spread_price - baseline_spread_price, 2)

        row = (origin_short, cfg["display_name"],
               win_prices, spread, is_spot, commodity)

        if has_baseline and commodity == baseline_cfg["commodity"]:
            baseline_row = row
        else:
            data_rows.append(row)

    # Sort by spread_window price ascending; None last; baseline always last
    data_rows.sort(key=lambda r: (r[2][1] is None if len(r[2]) > 1 else True,
                                   r[2][1] or 9999 if len(r[2]) > 1 else 9999))
    if baseline_row:
        data_rows.append(baseline_row)

    for origin_s, grade_s, win_prices, spread, is_spot, commodity in data_rows:
        is_baseline = has_baseline and commodity == baseline_cfg["commodity"]

        prices_str = "".join(
            _price(p, spot=(is_spot and i == 0))
            for i, p in enumerate(win_prices)
        )

        if is_baseline:
            spread_str = f"{'BASELINE':>{W_SPREAD}}"
        elif has_baseline:
            spread_str = _spread(spread)
        else:
            spread_str = ""

        lines.append(
            f" {origin_s:{W_ORIGIN}}"
            f"{grade_s:{W_GRADE}}"
            f"{prices_str}"
            f"{spread_str}"
        )

    if not data_rows:
        lines.append(f"  (no data)")

    lines.append(DIVIDER)


# ── Main report ────────────────────────────────────────────────────────────────

def build_report(db_path: Path) -> str:
    comparison_window = get_comparison_window(db_path)
    windows           = derive_display_windows(db_path, comparison_window)
    spread_window     = comparison_window
    prices            = load_prices(db_path, windows)
    meta          = load_metadata(db_path)
    report_date   = get_report_date(db_path)

    lines = []
    lines.append("")
    lines.append(f"  GLOBAL WHEAT ARBITRAGE MATRIX")
    lines.append(f"  Report Date : {report_date}")
    lines.append(f"  Units       : USD / MT  FOB")
    if meta["usdcad"]:
        lines.append(f"  USD/CAD     : {meta['usdcad']:.4f}")
    lines.append(
        f"  Spread      : Competitor {spread_window} minus Canadian baseline {spread_window}"
    )
    lines.append(
        f"                Negative = cheaper than Canada  |  "
        f"Positive = more expensive"
    )
    lines.append(BORDER)
    lines.append("")

    for tier in ["High", "Mid", "Low"]:
        render_tier(tier, prices, windows, spread_window, lines)
        lines.append("")

    lines.append(f"  * Spot indication (not a named forward month)")
    lines.append(f"  {MOISTURE_NOTE}")
    lines.append(BORDER)
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Print the Global Wheat Arbitrage Matrix (ex-Durum)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save output to arbitrage_matrix_<timestamp>.txt"
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Override path to global_wheat_summary.db"
    )
    args    = parser.parse_args()
    db_path = Path(args.db) if args.db else DB_SUMMARY
    report  = build_report(db_path)
    print(report)

    if args.save:
        ts       = datetime.now().strftime("%Y%m%d_%H%M")
        out_path = Path(__file__).parent / f"arbitrage_matrix_{ts}.txt"
        out_path.write_text(report, encoding="utf-8")
        print(f"  Saved → {out_path}")


if __name__ == "__main__":
    main()
