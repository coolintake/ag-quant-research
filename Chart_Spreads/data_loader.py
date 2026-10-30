"""Load Barchart futures history and parse contract symbols.

Input files carry: Date, Symbol, Name, Close, Open, High, Low, Volume, OpenInterest.
Supported formats: .csv, .xlsx / .xls (every sheet with those columns is read).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

DEFAULT_DATA_PATH = Path(r"C:\Users\ahmed\Downloads\Other Python\MW_KW_W_C.csv")

REQUIRED_COLUMNS = ["Date", "Symbol", "Close"]

# Futures month codes (F=Jan ... Z=Dec).
MONTH_CODES = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}
MONTH_NAMES = {
    "F": "Jan", "G": "Feb", "H": "Mar", "J": "Apr", "K": "May", "M": "Jun",
    "N": "Jul", "Q": "Aug", "U": "Sep", "V": "Oct", "X": "Nov", "Z": "Dec",
}

# Root + month code + 2- or 4-digit year, e.g. KEH02, ZCZ2023, MWZ23.
SYMBOL_RE = re.compile(r"^(?P<Root>[A-Z]{1,3}?)(?P<Month>[FGHJKMNQUVXZ])(?P<Year>\d{2}|\d{4})$")

# Canonical roots as used in this project. Barchart prints some roots differently
# (KC HRW wheat = KE, CBOT SRW wheat = ZW, CBOT corn = ZC); aliases resolve both ways.
COMMODITIES = {
    "MW": {"name": "Spring Wheat (MGEX/MIAX)", "group": "Grains"},
    "KW": {"name": "HRW Wheat (KC)", "group": "Grains"},
    "W":  {"name": "SRW Wheat (CBOT)", "group": "Grains"},
    "C":  {"name": "Corn (CBOT)", "group": "Grains"},
    "RS": {"name": "Canola (ICE)", "group": "Oilseeds"},
    "ZS": {"name": "Soybeans (CBOT)", "group": "Oilseeds"},
    "ZL": {"name": "Soybean Oil (CBOT)", "group": "Oilseed Products"},
    "ZM": {"name": "Soybean Meal (CBOT)", "group": "Oilseed Products"},
}
ROOT_ALIASES = {
    "KE": "KW", "ZW": "W", "ZC": "C", "S": "ZS", "BO": "ZL", "SM": "ZM",
}


def canonical_root(root: str) -> str:
    """Map any known spelling of a root (KE, KW, ZC, C, ...) to the canonical code."""
    root = root.strip().upper()
    return ROOT_ALIASES.get(root, root)


def expand_year(year: str | int, pivot: int = 50) -> int:
    """Expand a 2-digit contract year: 98 -> 1998, 02 -> 2002, 24 -> 2024.

    Years below `pivot` map to the 2000s, the rest to the 1900s. 4-digit years pass through.
    """
    y = int(year)
    if y >= 100:
        return y
    return 2000 + y if y < pivot else 1900 + y


def parse_symbol(symbol: str) -> dict | None:
    """Split a contract symbol into Root, Month, MonthNum and ContractYear.

    Returns None for non-contract symbols (cash/nearby series like ZSY00, FX like ^CADUSD).
    """
    m = SYMBOL_RE.match(str(symbol).strip().upper())
    if not m:
        return None
    return {
        "BarchartRoot": m["Root"],
        "Root": canonical_root(m["Root"]),
        "Month": m["Month"],
        "MonthNum": MONTH_CODES[m["Month"]],
        "ContractYear": expand_year(m["Year"]),
    }


def _read_raw(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(path, sheet_name=None)
        frames = [s for s in sheets.values() if set(REQUIRED_COLUMNS) <= set(s.columns)]
        if not frames:
            raise ValueError(f"No sheet in {path.name} has columns {REQUIRED_COLUMNS}")
        return pd.concat(frames, ignore_index=True)
    raise ValueError(f"Unsupported file type: {path.name}")


def find_data_files(path: str | Path) -> list[Path]:
    """Resolve a file, or a folder of .csv/.xlsx files, to a list of data files."""
    path = Path(path)
    if path.is_file():
        return [path]
    if path.is_dir():
        files = sorted(p for p in path.iterdir()
                       if p.suffix.lower() in (".csv", ".xlsx", ".xlsm", ".xls")
                       and not p.name.startswith("~$"))
        if not files:
            raise FileNotFoundError(f"No .csv/.xlsx files in {path}")
        return files
    # Allow passing the folder-style name of a single file (".../MW_KW_W_C").
    for ext in (".csv", ".xlsx", ".xls"):
        candidate = path.with_suffix(ext)
        if candidate.is_file():
            return [candidate]
    raise FileNotFoundError(path)


def load_futures(path: str | Path = DEFAULT_DATA_PATH, verbose: bool = True) -> pd.DataFrame:
    """Load, parse and clean Barchart futures history.

    Returns one row per (Symbol, Date) for real contracts only, with added columns
    Root, BarchartRoot, Month, MonthNum, ContractYear. Sorted by Root, Symbol, Date.
    """
    files = find_data_files(path)
    raw = pd.concat([_read_raw(f) for f in files], ignore_index=True)
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df = raw.copy()
    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ["Close", "Open", "High", "Low", "Volume", "OpenInterest"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    parsed = df["Symbol"].map(parse_symbol)
    skipped = sorted(df.loc[parsed.isna(), "Symbol"].unique())
    df = df[parsed.notna()].copy()
    df = pd.concat([df, pd.DataFrame(parsed.dropna().tolist(), index=df.index)], axis=1)

    n0 = len(df)
    df = df.dropna(subset=["Date", "Close"])
    df = df[df["Close"] > 0]
    n_bad = n0 - len(df)
    n1 = len(df)
    df = df.drop_duplicates(subset=["Symbol", "Date"], keep="last")
    n_dup = n1 - len(df)

    df = df.sort_values(["Root", "Symbol", "Date"]).reset_index(drop=True)
    if verbose:
        print(f"Loaded {len(df):,} rows from {', '.join(f.name for f in files)}")
        print(f"  dropped: {n_bad:,} missing/zero Close, {n_dup:,} duplicate Symbol/Date rows")
        if skipped:
            print(f"  skipped non-contract symbols: {', '.join(skipped)}")
    return df


def available_roots(df: pd.DataFrame) -> pd.DataFrame:
    """Summarize roots in the data: months listed, contract-year span, row count."""
    order = "FGHJKMNQUVXZ"
    out = (df.groupby("Root")
             .agg(BarchartRoot=("BarchartRoot", "first"),
                  Months=("Month", lambda s: "".join(sorted(set(s), key=order.index))),
                  FirstYear=("ContractYear", "min"),
                  LastYear=("ContractYear", "max"),
                  Contracts=("Symbol", "nunique"),
                  Rows=("Close", "size"),
                  FirstDate=("Date", "min"),
                  LastDate=("Date", "max")))
    out.insert(0, "Name", [COMMODITIES.get(r, {}).get("name", r) for r in out.index])
    out.insert(1, "Group", [COMMODITIES.get(r, {}).get("group", "Other") for r in out.index])
    return out.sort_values(["Group", "Name"])
