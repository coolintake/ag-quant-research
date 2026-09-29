"""
CIMT Trade Actuals Ingestion Module (ENGINE_RULES section 2)

Reads CIMT_<Key>_<YYYYMMDD>.xlsx workbooks from CIMT_DIR (read-only), strictly
from the flat 'Raw Data' sheet, and returns one standardized long table.

  * File key -> engine commodity via config.commodities.CIMT_FILE_COMMODITY
    (e.g. 'Amber_Durum' -> 'Durum', 'Total_Peas' -> 'Peas',
    'Total_Lentils' -> 'Lentils', 'Chickpeas' -> 'Chickpeas').
  * Latest file wins: if several dated files exist for one key, only the
    newest date is read (reading both would double count).
  * Raw Data columns: Year | Month | TradeType | HS6 | Country | KMT | ValueCAD
    (pulse workbooks add HS8 at the end; kept as HS8_Code). 'Quantity_MT'
    (metric tonnes, /1,000) is accepted in place of 'KMT'.
  * CIMT_HS_PREFIX filters, CIMT_PRODUCT_HS flags and CIMT_GRAIN_EQUIV
    factors are applied here (only Oats uses them today).
"""

import re
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from config.paths import CIMT_DIR
from config.commodities import (
    CIMT_FILE_COMMODITY, CIMT_HS_PREFIX, CIMT_PRODUCT_HS, CIMT_GRAIN_EQUIV,
)

RAW_SHEET = "Raw Data"
REQUIRED_COLUMNS = ["Year", "Month", "TradeType", "HS6", "Country", "ValueCAD"]
FILE_RE = re.compile(r"^CIMT_(.+)_(\d{8})\.xlsx$")


def _norm_hs(v) -> str:
    """'1004.9', '1004.90', 100490, 100490.0 -> '100490' (digits only).
    Integer HS codes lose their leading zero (071310 -> 71310); callers
    compare against prefixes normalised the same way."""
    if pd.isna(v):
        return ""
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0+", s):          # float artefact 100490.0
        s = s.split(".")[0]
    elif re.fullmatch(r"\d{4}\.\d{1,2}", s):   # dotted 1004.9 / 1004.90
        a, b = s.split(".")
        s = a + b.ljust(2, "0")
    return re.sub(r"\D", "", s)


def _discover_cimt_files() -> List[Tuple[str, str, Path]]:
    """Return (file_key, commodity, path), newest date per key only."""
    latest = {}
    for p in CIMT_DIR.glob("CIMT_*.xlsx"):
        if p.name.startswith("~$"):              # Excel lock file
            continue
        m = FILE_RE.match(p.name)
        if not m:
            print(f"  WARNING: could not parse commodity from filename: {p.name}")
            continue
        key, stamp = m.group(1), m.group(2)
        if key not in latest or stamp > latest[key][0]:
            if key in latest:
                print(f"  Latest file wins: {p.name} supersedes {latest[key][1].name}")
            latest[key] = (stamp, p)
        else:
            print(f"  Latest file wins: skipping older {p.name}")
    out = []
    for key, (_, p) in latest.items():
        commodity = CIMT_FILE_COMMODITY.get(key)
        if commodity is None:
            commodity = key.replace("_", " ")
            print(f"  NOTE: {p.name} has no CIMT_FILE_COMMODITY entry -> '{commodity}'")
        out.append((key, commodity, p))
    return sorted(out, key=lambda t: t[1])


def _read_raw(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path, engine="openpyxl")
    try:
        if RAW_SHEET not in xls.sheet_names:
            raise ValueError(
                f"No '{RAW_SHEET}' sheet in {path.name}. Sheets: {xls.sheet_names}. "
                f"Rebuild it with cimt_build.py.")
        return pd.read_excel(xls, sheet_name=RAW_SHEET)
    finally:
        xls.close()


def ingest_cimt_file(commodity: str, path: Path) -> pd.DataFrame:
    print(f"  Ingesting {commodity:<14} from {path.name}...")
    df = _read_raw(path)

    # Drop the italic usage note written under the table
    df = df[pd.to_numeric(df.get("Year"), errors="coerce").notna()].copy()

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}. Found {list(df.columns)}")
    if "KMT" in df.columns:
        kmt = pd.to_numeric(df["KMT"], errors="coerce")
    elif "Quantity_MT" in df.columns:
        kmt = pd.to_numeric(df["Quantity_MT"], errors="coerce") / 1_000
    else:
        raise ValueError(f"{path.name}: no quantity column (KMT or Quantity_MT)")

    out = pd.DataFrame({
        "Year": pd.to_numeric(df["Year"], errors="coerce").astype(int),
        "Month": pd.to_numeric(df["Month"], errors="coerce").astype(int),
        "Commodity": commodity,
        "Trade_Type": df["TradeType"].astype(str).str.strip().str.capitalize(),
        "HS_Code": df["HS6"].map(_norm_hs),
        "HS8_Code": df["HS8"].map(_norm_hs) if "HS8" in df.columns else "",
        "Country": df["Country"],
        "Value_KMT": kmt,
        "Value_CAD": pd.to_numeric(df["ValueCAD"], errors="coerce"),
    })
    n_raw = len(out)

    # HS filter (commodities listed in CIMT_HS_PREFIX only)
    prefixes = tuple(_norm_hs(p) for p in CIMT_HS_PREFIX.get(commodity, ()))
    if prefixes:
        keep = out["HS_Code"].str.startswith(prefixes)
        dropped = out.loc[~keep, "HS_Code"].unique()
        out = out[keep].copy()
        if len(dropped):
            print(f"    HS filter dropped lines: {sorted(dropped)}")

    # Product lines + grain-equivalent factor (1.0 = file already in GE)
    prod_hs = tuple(_norm_hs(p) for p in CIMT_PRODUCT_HS.get(commodity, ()))
    out["Is_Product"] = out["HS_Code"].str.startswith(prod_hs) if prod_hs else False
    out["Value_KMT_Product_Weight"] = out["Value_KMT"]
    for hs, factor in CIMT_GRAIN_EQUIV.items():
        m = out["HS_Code"].str.startswith(_norm_hs(hs)) & out["Is_Product"]
        out.loc[m, "Value_KMT"] = out.loc[m, "Value_KMT"] * factor

    out["Date"] = pd.to_datetime(dict(year=out["Year"], month=out["Month"], day=1),
                                 errors="coerce")
    out["Date_Str"] = out["Date"].dt.strftime("%Y-%m-%d")
    out["Unit"] = "KMT"
    out["Source"] = "CIMT_Actual"
    out["Data_Type"] = "Actual"

    exp = out.loc[out["Trade_Type"] == "Export", "Value_KMT"].sum()
    imp = out.loc[out["Trade_Type"] == "Import", "Value_KMT"].sum()
    print(f"    rows {n_raw:,} -> {len(out):,} | "
          f"{out['Date'].min():%Y-%m} to {out['Date'].max():%Y-%m} | "
          f"exports {exp:,.0f} KMT, imports {imp:,.0f} KMT (all years)")
    return out


def ingest_all_cimt() -> pd.DataFrame:
    print("Discovering CIMT files...")
    files = _discover_cimt_files()
    print(f"Found {len(files)} CIMT files (latest per commodity)")
    frames, failed = [], []
    for key, commodity, path in files:
        try:
            frames.append(ingest_cimt_file(commodity, path))
        except Exception as e:
            print(f"    ERROR ingesting {path.name}: {e}")
            failed.append(path.name)
    if not frames:
        raise ValueError("No CIMT files successfully ingested")
    combined = pd.concat(frames, ignore_index=True)
    print(f"\nTotal CIMT rows ingested: {len(combined):,}")
    if failed:
        print(f"  WARNING: {len(failed)} CIMT file(s) not ingested: {failed}")
    return combined


if __name__ == "__main__":
    # Run from Engine\:  python -m modules.ingest_cimt
    df = ingest_all_cimt()
    summary = (df.groupby(["Commodity", "Trade_Type"])
                 .agg(rows=("Value_KMT", "size"), kmt=("Value_KMT", "sum"),
                      cad=("Value_CAD", "sum"), first=("Date", "min"), last=("Date", "max")))
    print(summary.to_string())
