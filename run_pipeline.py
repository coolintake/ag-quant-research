"""
run_pipeline.py — Master entry point for the Wheat Arbitrage Analysis System.

Runtime prompts (collected before any file is touched):
    1. USD/CAD exchange rate       — e.g. 1.3941
    2. Hammersmith prompt window   — anchors Hammer delivery curve
                                     e.g. Jul-26 → Hammer shows Jul/Aug/Sep
    3. Comparison window           — which month to use as the spread baseline
                                     in the arbitrage matrix report
                                     shown after PDQ windows are extracted

Pipeline stages:
    1. extract_pdq.py    → db_pdq.db
    2. extract_usw.py    → db_usw.db
    3. extract_hammer.py → db_hammer.db
    4. transformer.py    → global_wheat_summary.db + global_wheat_history.db

Usage:
    python run_pipeline.py
"""

import sys
import re
import sqlite3
import logging
import importlib.util
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [PIPELINE]  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_MONTH_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}$", re.I
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_month(raw: str) -> str | None:
    """Normalise and validate a Mon-YY string. Returns clean string or None."""
    raw = raw.strip()
    if len(raw) == 6 and "-" in raw:
        parts = raw.split("-")
        raw   = parts[0].capitalize() + "-" + parts[1]
    if _MONTH_RE.match(raw):
        try:
            datetime.strptime(raw, "%b-%y")
            return raw
        except ValueError:
            pass
    return None


def prompt_hammer_window() -> str:
    banner = (
        "\n"
        "╔══════════════════════════════════════════════════════╗\n"
        "║      Hammersmith Prompt Delivery Window              ║\n"
        "╠══════════════════════════════════════════════════════╣\n"
        "║  Enter the calendar month for the PROMPT window.    ║\n"
        "║  This is the nearest forward shipment month in the  ║\n"
        "║  Hammersmith report — NOT the publication date.     ║\n"
        "║                                                      ║\n"
        "║  Format: Mon-YY   e.g.  Jul-26  Aug-26  Sep-26     ║\n"
        "║  Mid will be Prompt+1 month, Deferred Prompt+2.     ║\n"
        "╚══════════════════════════════════════════════════════╝"
    )
    print(banner)
    for attempt in range(1, 4):
        val = _parse_month(input(f"  Hammer prompt window (attempt {attempt}/3): "))
        if val:
            print(f"  ✓  Prompt={val}  Mid={val}+1  Deferred={val}+2\n")
            return val
        print(f"  ✗  Not a valid Mon-YY value (e.g. Jul-26). Try again.")
    raise RuntimeError("Hammer prompt window not provided. Pipeline aborted.")


def prompt_comparison_window(db_pdq: Path) -> str:
    """
    Ask the user which delivery window to use as the spread baseline in the
    arbitrage matrix.

    Reads the PDQ raw DB (just extracted) to show which windows actually
    have Canadian bids, so the user can make an informed choice.
    The default suggestion is the first window with a wheat bid.
    """
    # Read PDQ windows that have actual wheat bids (not No-Bid)
    pdq_wheat_windows = []
    try:
        con = sqlite3.connect(db_pdq)
        rows = con.execute(
            """
            SELECT DISTINCT delivery_window
            FROM raw_pdq
            WHERE commodity LIKE '%CWRS%' OR commodity LIKE '%CPSR%'
              AND cash != '-'
            ORDER BY delivery_window
            """
        ).fetchall()
        con.close()
        from mappings import PDQ_WINDOW_MAP
        pdq_wheat_windows = [
            PDQ_WINDOW_MAP.get(r[0], r[0]) for r in rows
        ]
    except Exception:
        pass

    # Build the banner
    if pdq_wheat_windows:
        windows_str = "  |  ".join(pdq_wheat_windows)
        suggestion  = pdq_wheat_windows[0]
        pdq_line    = (
            f"║  PDQ Canadian wheat windows:  {windows_str:<22}║\n"
            f"║  Suggested (first PDQ window): {suggestion:<21}║\n"
        )
        enter_hint = f"Enter for {suggestion}"
    else:
        suggestion = "Jul-26"
        pdq_line   = f"║  (PDQ windows not available)                         ║\n"
        enter_hint = f"Enter for {suggestion}"

    banner = (
        "\n"
        "╔══════════════════════════════════════════════════════╗\n"
        "║      Arbitrage Matrix — Comparison Window            ║\n"
        "╠══════════════════════════════════════════════════════╣\n"
        "║  Choose the primary window for spread calculation.  ║\n"
        "║  This becomes the centre column of the matrix and   ║\n"
        "║  the basis for all 'vs baseline' spreads.           ║\n"
        "║                                                      ║\n"
       f"{pdq_line}"
        "╚══════════════════════════════════════════════════════╝"
    )
    print(banner)

    for attempt in range(1, 4):
        raw = input(
            f"  Comparison window (attempt {attempt}/3, press Enter for {suggestion}): "
        ).strip()

        # Accept blank → use suggestion
        if raw == "":
            print(f"  ✓  Using {suggestion}\n")
            return suggestion

        val = _parse_month(raw)
        if val:
            print(f"  ✓  Using {val}\n")
            return val
        print(f"  ✗  Not a valid Mon-YY value (e.g. Jul-26). Try again.")

    raise RuntimeError("Comparison window not provided. Pipeline aborted.")


def main():
    root = Path(__file__).resolve().parent

    print("\n" + "═" * 58)
    print("   Wheat Arbitrage Analysis System — ETL Pipeline")
    print("═" * 58)

    # ── Step 1: collect FX rate and Hammer window up-front ────────────────────
    transformer  = _load("transformer", root / "transformer.py")
    usdcad       = transformer.prompt_usdcad_rate()
    prompt_month = prompt_hammer_window()

    # ── Steps 2-4: extract all three sources ──────────────────────────────────
    for stage_name, stage_path, kwargs in [
        ("extract_pdq",    root / "extract_pdq.py",    {}),
        ("extract_usw",    root / "extract_usw.py",    {}),
        ("extract_hammer", root / "extract_hammer.py", {"prompt_month": prompt_month}),
    ]:
        log.info("══ Stage: %s ══", stage_name)
        try:
            mod = _load(stage_name, stage_path)
            mod.run(**kwargs)
        except Exception as exc:
            log.error("Pipeline FAILED at '%s': %s", stage_name, exc)
            sys.exit(1)

    # ── Step 5: comparison window prompt — AFTER PDQ is extracted ────────────
    # PDQ is now in db_pdq.db so we can show the user which windows have bids
    from config import DB_PDQ
    comparison_window = prompt_comparison_window(DB_PDQ)

    # ── Step 6: transform + load ──────────────────────────────────────────────
    log.info("══ Stage: transformer ══")
    try:
        transformer.run(
            usdcad            = usdcad,
            prompt_window     = prompt_month,
            comparison_window = comparison_window,
        )
    except Exception as exc:
        log.error("Pipeline FAILED at 'transformer': %s", exc)
        sys.exit(1)

    print("\n" + "═" * 58)
    print("   ✓  Pipeline complete — global_wheat_summary.db ready")
    print("═" * 58 + "\n")


if __name__ == "__main__":
    main()
