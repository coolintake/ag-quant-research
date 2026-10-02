"""Provincial 30-day window averages of new-crop cash prices.

Windows (model_crop_wallet_step1.md, section 3.2), for planting year Y:

    RUN1  Dec 15, Y-1  ->  Jan 15, Y
    RUN2  Jan 15, Y    ->  Feb 15, Y
    RUN3  Feb 15, Y    ->  Mar 15, Y

Both endpoints are inclusive. Prices are new-crop bids for delivery year Y
(the fall after planting).

Delivery months
---------------
target (default)
    Each crop uses its month from ``config.CROP_TARGET_DELIVERY_MONTHS`` (DEC
    for cereals, corn and peas; NOV for canola and soybeans). Crops without a
    target use ``config.NEW_CROP_DELIVERY_MONTHS`` (AUG-DEC).
fallback
    If a crop's target month has no quotes in a window, that crop and window
    fall back to AUG-DEC and a warning is logged.
explicit
    Passing ``delivery_months`` (``--months``) applies those months to every
    crop with no fallback.

The ``delivery_months`` and ``month_basis`` output columns record which rule
produced each row.

Averaging methods
-----------------
simple
    Mean of every quoted observation (zone x day x delivery month) in the
    province. Days or zones with more quotes count for more.
weighted
    Per day, each zone's price is the mean over the selected delivery months;
    the provincial daily price is the zone-weight-weighted mean across zones
    that quoted (weights from ``config.ZONE_WEIGHTS``, default 1.0); the window
    price is the mean of the daily provincial prices, so each trading day
    counts equally.

NULL (non-quoted) prices are excluded everywhere, never treated as 0. Rows
are kept separate by ``source`` so PDQ and manual prices are never blended.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd

import config
from database import PriceDatabase

log = logging.getLogger(__name__)

Method = Literal["simple", "weighted"]

OUTPUT_COLUMNS: tuple[str, ...] = (
    "crop_year", "window", "window_start", "window_end", "province", "crop_symbol",
    "source", "method", "delivery_months", "month_basis",
    "avg_price_cad_mt", "avg_price_cad_bu", "n_obs", "n_days",
)


def target_delivery_months(crop_symbol: str) -> tuple[str, ...]:
    """Default new-crop delivery months for a crop (its target, else AUG-DEC)."""
    return config.CROP_TARGET_DELIVERY_MONTHS.get(
        crop_symbol.strip().upper(), config.NEW_CROP_DELIVERY_MONTHS
    )


def _normalize_months(months: Sequence[str]) -> list[str]:
    normalized = [m.strip().upper()[:3] for m in months]
    bad = [m for m in normalized if m not in config.MONTH_TO_NUM]
    if bad:
        raise ValueError(f"Unknown delivery month(s): {bad}")
    return normalized


def window_bounds(crop_year: int, window: str) -> tuple[date, date]:
    """Return the inclusive (start, end) dates of a pricing window for a planting year."""
    try:
        (sm, sd, sy), (em, ed, ey) = config.PRICE_WINDOWS[window]
    except KeyError as exc:
        raise ValueError(f"Unknown window {window!r}; choose from {list(config.PRICE_WINDOWS)}") from exc
    return date(crop_year + sy, sm, sd), date(crop_year + ey, em, ed)


def _filters(
    crop_symbols: Sequence[str] | None,
) -> tuple[str, list[object]]:
    if not crop_symbols:
        return "", []
    return " AND list_contains(?, crop_symbol)", [[s.upper() for s in crop_symbols]]


def _crops_in_range(db: PriceDatabase, start: date, end: date, delivery_year: int) -> list[str]:
    """Crops with any quoted price for ``delivery_year`` between ``start`` and ``end``."""
    rows = db.con.execute(
        f"""
        SELECT DISTINCT crop_symbol FROM {config.UNIFIED_TABLE}
        WHERE price_date BETWEEN ? AND ? AND delivery_year = ?
          AND (price_cad_mt IS NOT NULL OR price_cad_bu IS NOT NULL)
        ORDER BY 1
        """,
        [start, end, delivery_year],
    ).fetchall()
    return [r[0] for r in rows]


def _simple_sql(extra: str) -> str:
    return f"""
        SELECT province, crop_symbol, source,
               avg(price_cad_mt) AS avg_price_cad_mt,
               avg(price_cad_bu) AS avg_price_cad_bu,
               count(price_cad_mt) AS n_obs,
               count(DISTINCT price_date) FILTER (WHERE price_cad_mt IS NOT NULL) AS n_days
        FROM {config.UNIFIED_TABLE}
        WHERE price_date BETWEEN ? AND ?
          AND delivery_year = ?
          AND list_contains(?, delivery_month){extra}
        GROUP BY province, crop_symbol, source
    """


def _weighted_sql(extra: str) -> str:
    return f"""
        WITH zone_day AS (
            SELECT price_date, province, coalesce(pdq_zone, '') AS zone_key, crop_symbol, source,
                   avg(price_cad_mt) AS mt, avg(price_cad_bu) AS bu
            FROM {config.UNIFIED_TABLE}
            WHERE price_date BETWEEN ? AND ?
              AND delivery_year = ?
              AND list_contains(?, delivery_month){extra}
            GROUP BY ALL
        ),
        prov_day AS (
            SELECT d.price_date, d.province, d.crop_symbol, d.source,
                   sum(coalesce(w.weight, 1.0) * d.mt)
                     / sum(coalesce(w.weight, 1.0)) FILTER (WHERE d.mt IS NOT NULL) AS mt,
                   sum(coalesce(w.weight, 1.0) * d.bu)
                     / sum(coalesce(w.weight, 1.0)) FILTER (WHERE d.bu IS NOT NULL) AS bu,
                   count(d.mt) AS n_zones
            FROM zone_day d
            LEFT JOIN zone_weights w ON w.zone_key = d.zone_key
            GROUP BY ALL
        )
        SELECT province, crop_symbol, source,
               avg(mt) AS avg_price_cad_mt,
               avg(bu) AS avg_price_cad_bu,
               sum(n_zones) AS n_obs,
               count(mt) AS n_days
        FROM prov_day
        GROUP BY province, crop_symbol, source
    """


def compute_window_averages(
    db: PriceDatabase,
    crop_year: int,
    windows: Iterable[str] = tuple(config.PRICE_WINDOWS),
    method: Method = "simple",
    delivery_months: Sequence[str] | None = None,
    delivery_year: int | None = None,
    crop_symbols: Sequence[str] | None = None,
    zone_weights: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Average provincial cash prices over the pricing windows of a planting year.

    Args:
        db: Open price database.
        crop_year: Planting year Y (RUN1 starts Dec 15 of Y-1).
        windows: Window names from ``config.PRICE_WINDOWS``.
        method: ``"simple"`` or ``"weighted"`` (see module docstring).
        delivery_months: Explicit delivery months for every crop. ``None``
            (default) uses each crop's target month, falling back to AUG-DEC
            when the target has no quotes in a window.
        delivery_year: Delivery year of the new crop (default ``crop_year``).
        crop_symbols: Restrict to these crops (default all).
        zone_weights: PDQ zone -> weight for ``"weighted"`` (default
            ``config.ZONE_WEIGHTS``; missing zones get 1.0).

    Returns:
        One row per (window, province, crop_symbol, source) with
        ``OUTPUT_COLUMNS``. Groups with no quotes in a window are absent.
    """
    if method not in ("simple", "weighted"):
        raise ValueError(f"method must be 'simple' or 'weighted', got {method!r}")
    explicit = None if delivery_months is None else _normalize_months(delivery_months)
    year = crop_year if delivery_year is None else delivery_year
    window_names = list(windows)
    bounds = {w: window_bounds(crop_year, w) for w in window_names}

    weights = pd.DataFrame(
        {
            "zone_key": pd.Series(list((zone_weights or config.ZONE_WEIGHTS).keys()), dtype="string"),
            "weight": pd.Series(list((zone_weights or config.ZONE_WEIGHTS).values()), dtype="float64"),
        }
    )

    def run(start: date, end: date, months: list[str], crops: Sequence[str] | None) -> pd.DataFrame:
        extra, extra_params = _filters(crops)
        sql = _simple_sql(extra) if method == "simple" else _weighted_sql(extra)
        return db.query_df(sql, [start, end, year, months, *extra_params])

    def tag(part: pd.DataFrame, window: str, months: list[str], basis: str) -> pd.DataFrame:
        start, end = bounds[window]
        part.insert(0, "crop_year", crop_year)
        part.insert(1, "window", window)
        part.insert(2, "window_start", start)
        part.insert(3, "window_end", end)
        part["method"] = method
        part["delivery_months"] = ",".join(months)
        part["month_basis"] = basis
        return part

    frames: list[pd.DataFrame] = []
    fallback = list(config.NEW_CROP_DELIVERY_MONTHS)
    db.con.register("zone_weights", weights)
    try:
        for window in window_names:
            start, end = bounds[window]
            if explicit is not None:
                part = run(start, end, explicit, crop_symbols)
                if part.empty:
                    log.info("No %s quotes for %s (%s to %s)", method, window, start, end)
                else:
                    frames.append(tag(part, window, explicit, "explicit"))
                continue

            crops = (
                [c.strip().upper() for c in crop_symbols] if crop_symbols
                else _crops_in_range(db, start, end, year)
            )
            if not crops:
                log.info("No %s quotes for %s (%s to %s)", method, window, start, end)
            for crop in crops:
                months = list(target_delivery_months(crop))
                part = run(start, end, months, [crop])
                basis = "target" if crop in config.CROP_TARGET_DELIVERY_MONTHS else "default"
                if part.empty and months != fallback:
                    log.warning(
                        "%s %s: no %s quotes for target month %s/%d (%s to %s); "
                        "falling back to %s",
                        window, crop, method, ",".join(months), year, start, end,
                        "-".join((fallback[0], fallback[-1])),
                    )
                    months, basis = fallback, "fallback"
                    part = run(start, end, months, [crop])
                if part.empty:
                    log.info("%s %s: no quotes in %s", window, crop, ",".join(months))
                    continue
                frames.append(tag(part, window, months, basis))
    finally:
        db.con.unregister("zone_weights")

    if not frames:
        return pd.DataFrame(columns=list(OUTPUT_COLUMNS))
    result = pd.concat(frames, ignore_index=True)[list(OUTPUT_COLUMNS)]
    result["n_obs"] = result["n_obs"].astype("int64")
    result["n_days"] = result["n_days"].astype("int64")
    return result.sort_values(["window", "crop_symbol", "province", "source"], ignore_index=True)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provincial new-crop window averages.")
    parser.add_argument("--crop-year", type=int, required=True, help="Planting year Y")
    parser.add_argument("--windows", nargs="+", default=list(config.PRICE_WINDOWS),
                        choices=list(config.PRICE_WINDOWS))
    parser.add_argument("--method", choices=("simple", "weighted"), default="simple")
    parser.add_argument("--months", nargs="+", default=None,
                        help="Delivery months for every crop (default: per-crop target "
                             "month, falling back to AUG-DEC when unquoted)")
    parser.add_argument("--delivery-year", type=int, default=None)
    parser.add_argument("--crops", nargs="+", default=None, help="Crop symbols to include")
    parser.add_argument("--csv", type=Path, default=None, help="Also write the result to this CSV")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: print (and optionally save) window averages."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    with PriceDatabase(read_only=True) as db:
        result = compute_window_averages(
            db, args.crop_year, args.windows, args.method, args.months,
            args.delivery_year, args.crops,
        )
    if result.empty:
        print("No quotes in the requested windows.")
        return 1
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        print(result.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    if args.csv:
        result.to_csv(args.csv, index=False)
        print(f"Saved {len(result)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
