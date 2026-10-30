"""Price pipeline: PDQ prompt bids and the 4-tier fallback hierarchy (model_crop_wallet_step1.md 4.1-4.3).

Prompt rule (4.1), per crop x zone x price date inside window w of planting year T:
    1. primary   - the bid for the window's start delivery month (Run 1: DEC of T-1, Run 2: JAN of T,
                   Run 3: FEB of T);
    2. fallback  - if that bid is NULL or unlisted, the earliest later delivery month with a bid,
                   no later than JUL of T (old crop: prompt bids are a spot momentum signal);
    3. otherwise the zone has no price that day.
Provincial daily price = mean over quoting zones (Durum: belt zones only); window price = mean of
the daily prices. Missing quotes are never interpolated (4.2).

Fallback hierarchy (4.3), per crop x province x window x year:
    PDQ_PROMPT -> TEMPLATE_PRICE (crop_price_template.csv, Date = window end + 1 day)
               -> BUDGET_FALLBACK (that year's provincial guide price) -> NULL (MISSING_PRICE_DATA)

PDQ field mapping: IMPORT DATE -> price_date, MONTH -> delivery_month + delivery_year,
CASH -> price_cad_bu ('-', blank, 0 -> NULL at ingestion; re-checked here).

    python src/prompt_prices.py              # resolve all years, write output/reports/prices_resolved.csv
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

import settings as S

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["province", "year", "run", "crop", "price", "price_unit", "tier", "detail"]


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def window_bounds(year: int, run: str) -> tuple[date, date]:
    """Inclusive (start, end) dates of window ``run`` for planting year ``year``."""
    (sm, sd, sy), (em, ed, ey) = S.PRICE_WINDOWS[run]
    return date(year + sy, sm, sd), date(year + ey, em, ed)


def release_date(year: int, run: str) -> date:
    """Publication date of a release: the day after its window closes (e.g. Jan 16 for Run 1)."""
    return window_bounds(year, run)[1] + timedelta(days=1)


def _period_key(year: int, month: int) -> int:
    return year * 12 + (month - 1)


# --------------------------------------------------------------------------- #
# Tier 1: PDQ prompt bids
# --------------------------------------------------------------------------- #
def _connect(db_path=S.PRICE_DB) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"ATTACH '{db_path.as_posix()}' AS src (READ_ONLY)")
    return con


def _zone_condition() -> str:
    """SQL restricting zone-filtered crops (Durum belt) to their zones; other crops keep all zones."""
    parts = []
    for crop, zones in S.PDQ_ZONE_FILTER.items():
        if zones:
            zone_list = ", ".join(f"'{z}'" for z in zones)
            parts.append(f"(crop_symbol <> '{S.CROPS[crop]['pdq']}' OR pdq_zone IN ({zone_list}))")
    return " AND ".join(parts) or "TRUE"


_DAILY_SQL = """
WITH quotes AS (
    SELECT price_date, province, pdq_zone, crop_symbol, delivery_month, delivery_year, price_cad_bu,
           CAST(delivery_year * 12 + list_position({months}, delivery_month) - 1 AS BIGINT) AS period
    FROM {table}
    WHERE source = 'PDQ'
      AND crop_symbol IN ({symbols})
      AND price_date BETWEEN DATE '{start}' AND DATE '{end}'
      AND price_cad_bu IS NOT NULL AND price_cad_bu > 0
      AND {zones}
),
eligible AS (
    SELECT * FROM quotes WHERE period BETWEEN {first} AND {last}
)
-- earliest eligible delivery period per crop x zone x day (arg_min: one bid per zone-day)
SELECT price_date, any_value(province) AS province, pdq_zone, crop_symbol,
       min(period) AS period, arg_min(price_cad_bu, period) AS price_cad_bu,
       min(period) = {first} AS is_primary
FROM eligible
GROUP BY price_date, pdq_zone, crop_symbol
"""


def prompt_zone_days(con: duckdb.DuckDBPyConnection, year: int, run: str,
                     table: str = f"src.{S.PRICE_TABLE}") -> pd.DataFrame:
    """One selected prompt bid per crop x zone x price date in window ``run`` of ``year``."""
    start, end = window_bounds(year, run)
    first = _period_key(start.year, start.month)
    last = _period_key(year, S.PROMPT_LAST_DELIVERY_MONTH)
    symbols = [c["pdq"] for c in S.CROPS.values() if c["pdq"]]
    sql = _DAILY_SQL.format(table=table, zones=_zone_condition(),
                            months="[" + ", ".join(f"'{m}'" for m in S.MONTHS) + "]",
                            symbols=", ".join(f"'{s}'" for s in symbols),
                            start=start.isoformat(), end=end.isoformat(), first=int(first), last=int(last))
    return con.execute(sql).df()


def prompt_window_prices(con: duckdb.DuckDBPyConnection, years=S.PRICE_YEARS,
                         table: str = f"src.{S.PRICE_TABLE}") -> pd.DataFrame:
    """Provincial prompt window prices ($/bu) per province x year x run x model crop.

    Columns: province, year, run, crop, price, n_days, n_zone_days, primary_share, periods.
    Groups with no quoted day are absent (they become NULL at tier 1).
    """
    symbol_to_crop = {c["pdq"]: k for k, c in S.CROPS.items() if c["pdq"]}
    frames = []
    for year in years:
        for run in S.WINDOWS:
            zd = prompt_zone_days(con, year, run, table)
            if zd.empty:
                continue
            zd["crop"] = zd["crop_symbol"].map(symbol_to_crop)
            daily = (zd.groupby(["province", "crop", "price_date"])
                       .agg(price=("price_cad_bu", "mean"), zones=("pdq_zone", "count"))
                       .reset_index())
            win = (daily.groupby(["province", "crop"])
                        .agg(price=("price", "mean"), n_days=("price_date", "nunique"), n_zone_days=("zones", "sum"))
                        .reset_index())
            meta = (zd.groupby(["province", "crop"])
                      .agg(primary_share=("is_primary", "mean"),
                           periods=("period", lambda p: ", ".join(
                               f"{S.MONTHS[k % 12]} {k // 12}" for k in sorted(set(p)))))
                      .reset_index())
            win = win.merge(meta, on=["province", "crop"])
            win.insert(1, "year", year)
            win.insert(2, "run", run)
            frames.append(win)
    cols = ["province", "year", "run", "crop", "price", "n_days", "n_zone_days", "primary_share", "periods"]
    return pd.concat(frames, ignore_index=True)[cols] if frames else pd.DataFrame(columns=cols)


# --------------------------------------------------------------------------- #
# Tier 2: crop_price_template.csv
# --------------------------------------------------------------------------- #
def to_model_price(value: float, unit: str, crop: str) -> float | None:
    """Convert a quoted price to the crop's model unit ($/bu, or $/lb for Lentils). None if unknown unit."""
    spec = S.CROPS[crop]
    u = str(unit or "").strip().lower().replace("¢", "ct").replace(" ", "")
    lb_per_bu = S.KG_PER_BU[spec["bushel"]] * S.LB_PER_KG if spec["bushel"] else 60.0
    per_lb = {"$/lb": value, "ct/lb": value / 100.0, "c/lb": value / 100.0}.get(u)
    if spec["unit"] == "lb":
        if per_lb is not None:
            return per_lb
        return {"$/mt": value / (1000.0 * S.LB_PER_KG), "$/t": value / (1000.0 * S.LB_PER_KG),
                "$/bu": value / 60.0}.get(u)
    if u == "$/bu":
        return value
    if u in ("$/mt", "$/t"):
        return value * S.KG_PER_BU[spec["bushel"]] / 1000.0      # $/MT / (bu/MT), bu/MT = 1000 / kg-per-bu
    if per_lb is not None:
        return per_lb * lb_per_bu
    return None


def load_template(path=S.PRICE_TEMPLATE) -> pd.DataFrame:
    """Template rows mapped to model crops and converted to model units.

    Columns: date, province, crop, price, raw_price, raw_unit. Rows for crops outside the model
    (e.g. Malt Barley, Corn) are dropped; rows with an unknown unit are dropped with a warning.
    """
    cols = ["date", "province", "crop", "price", "raw_price", "raw_unit"]
    if not path.exists():
        log.warning("Price template not found: %s", path)
        return pd.DataFrame(columns=cols)
    raw = pd.read_csv(path)
    label_to_crop = {c["workbook"]: k for k, c in S.CROPS.items()}
    out = []
    for _, r in raw.iterrows():
        crop = label_to_crop.get(str(r["Display_Name"]).strip())
        if crop is None or pd.isna(r["Price"]):
            continue
        price = to_model_price(float(r["Price"]), r["Unit"], crop)
        if price is None:
            log.warning("Template %s %s %s: unknown unit %r -> row ignored",
                        r["Date"], r["Province"], r["Display_Name"], r["Unit"])
            continue
        out.append({"date": pd.to_datetime(r["Date"]).date(), "province": str(r["Province"]).strip(),
                    "crop": crop, "price": price, "raw_price": r["Price"], "raw_unit": r["Unit"]})
    tpl = pd.DataFrame(out, columns=cols)
    dupes = tpl[tpl.duplicated(["date", "province", "crop"], keep=False)]
    if not dupes.empty:
        log.warning("Template has duplicate date/province/crop rows; the first is used:\n%s", dupes)
    return tpl.drop_duplicates(["date", "province", "crop"])


# --------------------------------------------------------------------------- #
# Hierarchy
# --------------------------------------------------------------------------- #
def resolve_prices(prompt: pd.DataFrame, template: pd.DataFrame, guide: pd.DataFrame,
                   years=S.PRICE_YEARS) -> pd.DataFrame:
    """Apply the 4-tier hierarchy to every province x year x run x model crop.

    Args:
        prompt: ``prompt_window_prices`` output.
        template: ``load_template`` output.
        guide: province, year, crop, guide_price (model unit) - the provincial guide farm price.

    Returns one row per cell with ``PRICE_COLUMNS``; ``tier`` is the flag (PDQ_PROMPT,
    TEMPLATE_PRICE, BUDGET_FALLBACK or MISSING_PRICE_DATA) and ``detail`` records the evidence.
    """
    p_idx = prompt.set_index(["province", "year", "run", "crop"]) if not prompt.empty else None
    t_idx = template.set_index(["date", "province", "crop"])["price"] if not template.empty else pd.Series(dtype=float)
    g_idx = guide.dropna(subset=["guide_price"]).set_index(["province", "year", "crop"])["guide_price"]

    rows = []
    for prov in S.PROVINCES:
        for year in years:
            for run in S.WINDOWS:
                released = release_date(year, run)
                for crop, spec in S.CROPS.items():
                    unit = "$/lb" if spec["unit"] == "lb" else "$/bu"
                    row = {"province": prov, "year": year, "run": run, "crop": crop, "price_unit": unit}
                    key = (prov, year, run, crop)
                    if spec["pdq"] and p_idx is not None and key in p_idx.index and pd.notna(p_idx.loc[key, "price"]):
                        q = p_idx.loc[key]
                        rows.append({**row, "price": q["price"], "tier": S.TIER_PDQ,
                                     "detail": f"{int(q['n_days'])} days, {int(q['n_zone_days'])} zone-days, "
                                               f"primary {q['primary_share']:.0%}, periods {q['periods']}"})
                    elif (released, prov, crop) in t_idx.index:
                        rows.append({**row, "price": float(t_idx.loc[(released, prov, crop)]), "tier": S.TIER_TEMPLATE,
                                     "detail": f"template row dated {released.isoformat()}"})
                    elif (prov, year, crop) in g_idx.index:
                        rows.append({**row, "price": float(g_idx.loc[(prov, year, crop)]), "tier": S.TIER_GUIDE,
                                     "detail": f"{year} provincial guide price"})
                    else:
                        why = "no PDQ quotes, template row or guide price"
                        if crop == "DURUM" and prov not in ("AB", "SK"):
                            why = "outside the Durum belt and no guide budget"
                        rows.append({**row, "price": np.nan, "tier": S.TIER_NULL, "detail": why})
    return pd.DataFrame(rows, columns=PRICE_COLUMNS)


def build_prices(guide: pd.DataFrame, years=S.PRICE_YEARS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve every price cell. Returns (resolved prices, prompt window detail)."""
    con = _connect()
    try:
        prompt = prompt_window_prices(con, years)
    finally:
        con.close()
    return resolve_prices(prompt, load_template(), guide, years), prompt


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    import data_loader  # noqa: PLC0415  (guide prices come from the validated cost tables)

    costs, _ = data_loader.load_costs()
    guide = costs[["province", "year", "crop", "budget_price"]].rename(columns={"budget_price": "guide_price"})
    prices, prompt = build_prices(guide)
    S.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    prices.to_csv(S.REPORTS_DIR / "prices_resolved.csv", index=False)
    prompt.to_csv(S.REPORTS_DIR / "prices_prompt_windows.csv", index=False)
    summary = prices.pivot_table(index=["year"], columns="tier", values="price", aggfunc="size", fill_value=0)
    print(summary.to_string())
    print(f"Wrote {S.REPORTS_DIR / 'prices_resolved.csv'} ({len(prices)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
