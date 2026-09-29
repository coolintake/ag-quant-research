"""
Populate the monthly forecast tabs (*_Mo_F) in CanSD_Input_wb.xlsx for
engine-merge testing.

  * 25-26 crop year  <- actuals from data_store\\CMFT_Master.csv (the engine's
                        monthly balance built from StatCan 32100013/32100359
                        + CIMT; run the pipeline first so it is current).
  * 26-27 planted    <- StatCan 32100359 seeded area for 2026 (Canada), when
                        published.
  * 26-27, 27-28, 28-29 <- flatline of the 25-26 monthly values
                        (26-27 planted area uses the 2026 estimate instead).

Target tabs: Barley_Mo_F, Oats_Mo_F, Soybeans_Mo_F, Peas_Mo_F, Lentils_Mo_F.
Never touched: Wheat_Mo_F, Durum_Mo_F, Canola_Mo_F.

Safety:
  * Dry run by default: prints what it would write. Add --write to save.
  * Refuses to save while the workbook is open in Excel (~$ lock file).
  * Writes a timestamped backup next to the workbook before saving.
  * Never overwrites a formula cell; only month columns and the annual
    column (when that cell is not a formula) are written.

Usage (from Engine\\):
    python populate_input_forecast.py --inspect   # show detected layout only
    python populate_input_forecast.py             # dry run
    python populate_input_forecast.py --write     # save
"""

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.comments import Comment

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.paths import STATCAN_DIR, MASTER_FACT_TABLE, CIMT_DIR  # noqa: E402

WORKBOOK = STATCAN_DIR / "CanSD_Input_wb" / "CanSD_Input_wb.xlsx"
TARGET_SHEETS = ["Barley_Mo_F", "Oats_Mo_F", "Soybeans_Mo_F", "Peas_Mo_F", "Lentils_Mo_F"]
PROTECTED_SHEETS = {"Wheat_Mo_F", "Durum_Mo_F", "Canola_Mo_F"}

ACTUAL_CY = 2025                      # 25-26
OUTYEARS = [2026, 2027, 2028]         # 26-27, 27-28, 28-29

# StatCan 32100359 crop label per engine commodity (for 26-27 planted area)
PLANTING_LABEL = {"Barley": "Barley", "Oats": "Oats", "Soybeans": "Soybeans",
                  "Peas": "Peas, dry", "Lentils": "Lentils"}

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS)}

# Tab label (normalised, units in brackets removed) -> metric key
METRIC_ALIASES = {
    "planted area": "planted", "seeded area": "planted", "planted": "planted",
    "harvested area": "harvested", "harvested": "harvested",
    "yield": "yield",
    "production": "production",
    "exports": "exports", "imports": "imports",
    "food": "food",
    "industrial": "industrial", "crush": "crush",
    "seed": "seed",
    "food & processing": "food_proc", "food and processing": "food_proc",
    "fsr": "fsr", "feed, seed & residual": "fsr", "feed seed & residual": "fsr",
    "stocks": "stocks", "ending stocks": "stocks",
    "beginning stocks": "beg_stocks",
}
FLOW_METRICS = {"production", "exports", "imports", "food", "industrial", "crush",
                "seed", "food_proc", "fsr"}
LEVEL_METRICS = {"planted", "harvested", "yield"}      # one value per crop year

CY_RE = re.compile(r"(?<!\d)(?:20)?(\d{2})\s*[-/]\s*(\d{2})(?!\d)")


# ------------------------------------------------------------------ helpers
def norm_label(v) -> str:
    s = re.sub(r"\(.*?\)", "", str(v)).strip().lower()
    return re.sub(r"\s+", " ", s)


def is_formula(cell) -> bool:
    return cell.data_type == "f" or (isinstance(cell.value, str) and cell.value.startswith("="))


def parse_cy(v):
    if not isinstance(v, str):
        return None
    m = CY_RE.search(v)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return 2000 + a if (a + 1) % 100 == b else None


def cy_label(y: int) -> str:
    return f"{y % 100:02d}-{(y + 1) % 100:02d}"


# ------------------------------------------------------------ layout detect
def detect_layout(ws):
    """Return (header_row, {col: month}, annual_col, [(row, metric, cy)], unknown_labels)."""
    header_row, month_cols, annual_col = None, {}, None
    for r in range(1, min(ws.max_row, 20) + 1):
        found, ann = {}, None
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if not isinstance(v, str):
                continue
            t = v.strip().upper().replace(" ", "")
            if t in ("AUG/JUL", "SEP/AUG", "OCT/SEP"):
                ann = c
            elif t[:3] in MONTH_NUM and len(t) <= 9 and "/" not in t:
                found[c] = t[:3]
        if len(found) >= 12:
            header_row, month_cols, annual_col = r, found, ann
            break
    if header_row is None:
        raise ValueError(f"{ws.title}: no header row with 12 month columns in the first 20 rows")

    label_cols = range(1, min(month_cols))
    raw, unknown = [], set()
    for r in range(header_row + 1, ws.max_row + 1):
        metric, cy, texts = None, None, []
        for c in label_cols:
            v = ws.cell(r, c).value
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            texts.append(str(v).strip())
            key = METRIC_ALIASES.get(norm_label(v))
            if key and metric is None:
                metric = key
            y = parse_cy(v)
            if y and cy is None:
                cy = y
        if metric or cy:
            raw.append((r, metric, cy))
        elif texts:
            unknown.add(" | ".join(texts))

    targets = [(r, m, y) for r, m, y in raw if m and y]
    if not targets:                            # block layouts
        cur_m = cur_y = None
        metric_blocks = next(((m is not None) for _, m, _ in raw), False)
        for r, m, y in raw:
            if metric_blocks:
                if m:
                    cur_m = m
                elif y and cur_m:
                    targets.append((r, cur_m, y))
            else:
                if y:
                    cur_y = y
                elif m and cur_y:
                    targets.append((r, m, cur_y))
    return header_row, month_cols, annual_col, targets, sorted(unknown)


# ------------------------------------------------------------- data sources
def load_master() -> pd.DataFrame:
    if not MASTER_FACT_TABLE.exists():
        sys.exit(f"Master table not found: {MASTER_FACT_TABLE}. Run python run_pipeline.py first.")
    inputs = [STATCAN_DIR / "S&D_32100013-eng" / "32100013.csv",
              STATCAN_DIR / "Planting_32100359-eng" / "32100359.csv"]
    inputs += [p for p in CIMT_DIR.glob("CIMT_*.xlsx") if not p.name.startswith("~$")]
    newest = max((p.stat().st_mtime for p in inputs if p.exists()), default=0)
    if MASTER_FACT_TABLE.stat().st_mtime < newest:
        print("WARNING: CMFT_Master.csv is older than a StatCan/CIMT input. "
              "Run python run_pipeline.py first for current actuals.\n")
    return pd.read_csv(MASTER_FACT_TABLE, parse_dates=["Date"])


def load_cimt_trade(commodities) -> dict:
    """{commodity: {(month Timestamp, 'exports'|'imports'): KMT}} from the CIMT workbooks.
    Used only for months the master does not cover yet (after the latest
    StatCan release), where CIMT already has observed trade."""
    from modules.ingest_cimt import ingest_all_cimt
    from config.commodities import normalize_commodity
    df = ingest_all_cimt().dropna(subset=["Date"])
    df["Commodity_Norm"] = df["Commodity"].apply(normalize_commodity)
    df = df[df["Commodity_Norm"].isin(commodities)]
    g = df.groupby(["Commodity_Norm", "Date", "Trade_Type"])["Value_KMT"].sum()
    out = {}
    for (comm, d, kind), v in g.items():
        key = {"Export": "exports", "Import": "imports"}.get(kind)
        if key:
            out.setdefault(comm, {})[(pd.Timestamp(d), key)] = float(v)
    return out


def load_planting_2026() -> dict:
    """Seeded area 2026 (MLN acres, Canada) per engine commodity."""
    path = STATCAN_DIR / "Planting_32100359-eng" / "32100359.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str, low_memory=False,
                     usecols=["REF_DATE", "GEO", "Harvest disposition", "Type of crop", "VALUE"])
    df = df[(df["GEO"] == "Canada") & (df["Harvest disposition"] == "Seeded area (acres)")
            & (df["REF_DATE"].str[:4] == "2026")]
    out = {}
    for comm, label in PLANTING_LABEL.items():
        v = pd.to_numeric(df.loc[df["Type of crop"] == label, "VALUE"], errors="coerce").dropna()
        if len(v):
            out[comm] = float(v.iloc[-1]) / 1e6
    return out


def master_value(row: pd.Series, metric: str):
    def g(col):
        v = row.get(col)
        return None if v is None or pd.isna(v) else float(v)

    simple = {"planted": "Planted_MLN_AC", "harvested": "Harvested_MLN_AC",
              "yield": "Yield_BU_AC", "production": "Production", "exports": "Exports",
              "imports": "Imports", "food": "Food", "fsr": "FSR", "crush": "Crush",
              "stocks": "Ending Stocks", "beg_stocks": "Beginning Stocks"}
    if metric in simple:
        return g(simple[metric])
    if metric == "industrial":
        v = g("Industrial")
        return v if v is not None else g("Crush")
    if metric == "seed":
        return 0.0                              # engine carries seed inside FSR
    if metric == "food_proc":
        parts = [p for p in (g("Food"), g("Industrial"), g("Crush")) if p is not None]
        return sum(parts) if parts else None
    return None


def annual_value(metric, values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    if metric in FLOW_METRICS:
        return sum(vals)
    if metric in LEVEL_METRICS:
        return vals[0]
    if metric == "stocks":
        return vals[-1]
    if metric == "beg_stocks":
        return vals[0]
    return None


# --------------------------------------------------------------------- main
def process_sheet(ws, commodity, master, planted26, trade, write, log):
    header_row, month_cols, annual_col, targets, unknown = detect_layout(ws)
    cols = sorted(month_cols)
    start_month = MONTH_NUM[month_cols[cols[0]]]
    stats = {"sheet": ws.title, "rows_25_26": 0, "cells_25_26": 0, "cells_outyears": 0,
             "formula_skipped": 0, "cimt_gap_cells": 0, "kept_existing": 0,
             "planted_26_27": None, "opening_stock": None, "source_range": ""}

    mc = master[master["Commodity_Norm"] == commodity].set_index("Date")
    if mc.empty:
        log.append(f"  {ws.title}: no '{commodity}' rows in CMFT_Master.csv - skipped")
        return stats

    def date_for(cy, col):
        mo = MONTH_NUM[month_cols[col]]
        return pd.Timestamp(cy if mo >= start_month else cy + 1, mo, 1)

    dates = [date_for(ACTUAL_CY, c) for c in cols]
    have = [d for d in dates if d in mc.index]
    stats["source_range"] = f"{have[0]:%Y-%m} to {have[-1]:%Y-%m}" if have else "none"
    if len(have) < 12:
        miss = [f"{d:%Y-%m}" for d in dates if d not in mc.index]
        log.append(f"  {ws.title}: master has no rows for {miss} (CIMT trade where observed; other metrics keep existing tab values)")

    by_metric = {}                              # metric -> {cy: row}
    for r, metric, cy in targets:
        by_metric.setdefault(metric, {})[cy] = r

    def put(cell, value):
        if is_formula(cell):
            stats["formula_skipped"] += 1
            return False
        if write:
            cell.value = value
        return True

    actual_vals = {}
    for metric, rows in by_metric.items():
        r = rows.get(ACTUAL_CY)
        if r is None:
            continue
        stats["rows_25_26"] += 1
        vals = []
        for c, d in zip(cols, dates):
            cell = ws.cell(r, c)
            if d in mc.index:
                v = master_value(mc.loc[d], metric)
                if v is None and metric in FLOW_METRICS:
                    v = 0.0     # engine has no such flow here (e.g. pulse food sits in FSR)
            elif metric in ("exports", "imports") and (d, metric) in trade:
                v = trade[(d, metric)]      # CIMT observed, past the latest StatCan release
                stats["cimt_gap_cells"] += 1
            else:
                v = None
            if v is None:
                # No actual: keep what the tab already holds, and flatline from it
                old = None if is_formula(cell) else cell.value
                if isinstance(old, (int, float)):
                    stats["kept_existing"] += 1
                    vals.append(float(old))
                else:
                    vals.append(None)
                continue
            if put(cell, round(v, 6)):
                stats["cells_25_26"] += 1
            vals.append(v)
        actual_vals[metric] = vals

    # 25-26 opening stocks: the tab's first STOCKS formula points at a side table
    # these tabs do not have (#REF!). Use the engine's 24-25 closing stock.
    stocks_row = by_metric.get("stocks", {}).get(ACTUAL_CY)
    if stocks_row is not None and dates[0] in mc.index:
        cell = ws.cell(stocks_row, cols[0])
        if isinstance(cell.value, str) and "#REF!" in cell.value:
            beg = master_value(mc.loc[dates[0]], "beg_stocks")
            if beg is not None:
                stats["opening_stock"] = round(beg, 3)
                if write:
                    cell.value = cell.value.replace("#REF!", f"{round(beg, 6)}", 1)
                    cell.comment = Comment(
                        f"25-26 opening stocks = {beg:,.1f} KMT: engine closing stock for "
                        f"{cy_label(ACTUAL_CY - 1)} (data_store\\CMFT_Master.csv, anchored to "
                        f"StatCan 32100013 Total ending stocks). Replaced a #REF! link.",
                        "populate_input_forecast.py")
        a_val = annual_value(metric, vals) if annual_col else None
        if a_val is not None:
            put(ws.cell(r, annual_col), round(a_val, 6))

    for metric, rows in by_metric.items():
        base = actual_vals.get(metric)
        if base is None:
            continue
        for cy in OUTYEARS:
            r = rows.get(cy)
            if r is None:
                continue
            vals = list(base)
            if metric == "planted" and cy == 2026 and commodity in planted26:
                idx = next((i for i, v in enumerate(base) if v is not None), 0)
                vals = [None] * 12
                vals[idx] = planted26[commodity]        # same month slot as the 25-26 area
                stats["planted_26_27"] = round(planted26[commodity], 4)
            for c, v in zip(cols, vals):
                if v is not None and put(ws.cell(r, c), round(v, 6)):
                    stats["cells_outyears"] += 1
            a_val = annual_value(metric, vals) if annual_col else None
            if a_val is not None:
                put(ws.cell(r, annual_col), round(a_val, 6))

    missing_rows = [f"{m} {cy_label(y)}" for m in by_metric
                    for y in [ACTUAL_CY] + OUTYEARS if y not in by_metric[m]]
    log.append(f"  {ws.title}: header row {header_row}, months "
               f"{month_cols[cols[0]]}..{month_cols[cols[-1]]}, annual col "
               f"{annual_col or 'none'}, metrics {sorted(by_metric)}")
    if commodity == "Soybeans" and start_month != 9:
        log.append(f"    NOTE: tab months start {month_cols[cols[0]]}, but soybeans' StatCan crop "
                   f"year is Sep-Aug; values are placed by calendar month.")
    if missing_rows:
        log.append(f"    no row found for: {missing_rows}")
    if unknown:
        log.append(f"    labels not recognised (left alone): {unknown[:12]}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="save changes (default: dry run)")
    ap.add_argument("--inspect", action="store_true", help="only print the detected layout")
    a = ap.parse_args()

    if not WORKBOOK.exists():
        sys.exit(f"Workbook not found: {WORKBOOK}")
    lock = WORKBOOK.with_name("~$" + WORKBOOK.name)
    if a.write and lock.exists():
        sys.exit(f"{WORKBOOK.name} looks open in Excel ({lock.name} exists). "
                 f"Close it and re-run. (If Excel is closed, delete the stale {lock.name}.)")

    wb = load_workbook(WORKBOOK)                # formulas kept as formulas
    missing = [s for s in TARGET_SHEETS if s not in wb.sheetnames]
    if missing:
        print(f"WARNING: target sheets not in workbook: {missing}")

    if a.inspect:
        for s in TARGET_SHEETS:
            if s in wb.sheetnames:
                h, mcols, ann, tg, unk = detect_layout(wb[s])
                cols = sorted(mcols)
                print(f"{s}: header row {h}, months {mcols[cols[0]]}..{mcols[cols[-1]]} "
                      f"(cols {cols[0]}-{cols[-1]}), annual col {ann}")
                for r, m, y in tg:
                    print(f"    row {r:>3}: {m:<11} {cy_label(y)}")
                if unk:
                    print(f"    unrecognised labels: {unk}")
        return

    master = load_master()
    planted26 = load_planting_2026()
    # CIMT gap fill only matters where the master stops before the crop year ends
    commodities = [s.replace("_Mo_F", "") for s in TARGET_SHEETS]
    last = master[master["Commodity_Norm"].isin(commodities)].groupby("Commodity_Norm")["CY_Month"].last()
    short = [c for c in commodities if last.get(c, 12) < 12
             and master.loc[master["Commodity_Norm"] == c, "Crop_Year"].max() == ACTUAL_CY]
    cimt = load_cimt_trade(short) if short else {}

    log, stats = [], []
    for s in TARGET_SHEETS:
        if s in PROTECTED_SHEETS or s not in wb.sheetnames:
            continue
        comm = s.replace("_Mo_F", "")
        stats.append(process_sheet(wb[s], comm, master, planted26, cimt.get(comm, {}),
                                   a.write, log))

    print("Layout / notes:")
    print("\n".join(log))
    print()
    print(pd.DataFrame(stats).to_string(index=False))

    if a.write:
        backup = WORKBOOK.with_name(f"{WORKBOOK.stem}_backup_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        shutil.copy2(WORKBOOK, backup)
        wb.save(WORKBOOK)
        wb.close()
        print(f"\nSaved {WORKBOOK}  (backup: {backup.name})")
        verify(master)
    else:
        print("\nDry run - nothing saved. Re-run with --write to apply.")


def verify(master):
    """Re-open the saved workbook, count populated cells, and roll the tab's
    STOCKS formula in Python to check it ties to the engine's ending stocks."""
    wb = load_workbook(WORKBOOK)
    rows = []
    for s in TARGET_SHEETS:
        ws = wb[s]
        comm = s.replace("_Mo_F", "")
        _, month_cols, _, targets, _ = detect_layout(ws)
        cols = sorted(month_cols)
        by = {(m, y): r for r, m, y in targets}
        cys = [ACTUAL_CY] + OUTYEARS
        filled = sum(1 for (m, y), r in by.items() if y in cys for c in cols
                     if ws.cell(r, c).value is not None and not is_formula(ws.cell(r, c)))

        def num(m, c):
            v = ws.cell(by[(m, ACTUAL_CY)], c).value if (m, ACTUAL_CY) in by else None
            return float(v) if isinstance(v, (int, float)) else 0.0

        mc = master[(master["Commodity_Norm"] == comm) & (master["Crop_Year"] == ACTUAL_CY)]
        stock = float(mc["Beginning Stocks"].iloc[0]) if len(mc) else float("nan")
        ind = "crush" if (("crush", ACTUAL_CY) in by) else "industrial"
        gaps, end_stock = [], None
        for i, c in enumerate(cols):
            stock += (num("production", c) + num("imports", c) - num("exports", c)
                      - num("food", c) - num(ind, c) - num("fsr", c) - num("seed", c))
            if i < len(mc):
                gaps.append(abs(stock - float(mc["Ending Stocks"].iloc[i])))
            end_stock = stock
        pl = ws.cell(by[("planted", 2026)], cols[0]).value if ("planted", 2026) in by else None
        rows.append({
            "Sheet Name": s,
            "Crop Years Updated": ", ".join(cy_label(y) for y in cys),
            "Planted Area '26-27' (MLN AC)": pl,
            "Non-Null Cells Populated": filled,
            "Actual months": f"{len(mc)}/12",
            "Stocks tie-out max |gap| KMT": round(max(gaps), 6) if gaps else None,
            "25-26 closing stock KMT": round(end_stock, 1),
        })
    wb.close()
    print("\nSUMMARY")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
