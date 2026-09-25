"""
cimt_legacy_adapter.py
======================
Pre-processes historical Statistics Canada CIMT (Canadian International
Merchandise Trade) CSV files from 2002-2011 and exports a single normalized
Parquet cache (`cimt_legacy_2002_2011.parquet`) that feeds directly into the
post-2012 pipeline in `cimt_build.py` (load_all_zips()).

Data discovery
--------------
1. Recursively globs the CIMTD folder for unzipped legacy CSV files
   (patterns: *Imp*.csv / *imp*.csv / *IMP*.csv for imports,
   *Exp*.csv / *exp*.csv / *EXP*.csv / *Tot*.csv / *tot*.csv for exports).
2. If no unzipped CSVs are found, falls back to reading the HS6-level CSVs
   directly from the legacy ZIP archives (CIMT-CICM_Imp_YYYY.zip and
   CIMT-CICM_Tot_Exp_YYYY.zip for 2002-2011).  The Dom_Exp_2011 archive is
   skipped because it is a domestic-only subset of total exports.

Output schema (matches cimt_build.py's flat frame)
--------------------------------------------------
  Year       int64    4-digit year
  Month      int64    1-12
  TradeType  str      "Export" or "Import"
  HS6        Int64    6-digit HS code (nullable integer)
  Country    str      Full English country name (normalized)
  KMT        float64  Volume in Thousands of Metric Tonnes
  ValueCAD   float64  Dollar value in Canadian Dollars

Dependencies: pandas, pyarrow, pathlib, re (+ stdlib zipfile for the
legacy-ZIP fallback).  No openpyxl / excel writers.
"""

import re
import zipfile
from pathlib import Path

import pandas as pd

# =============================================================================
# CONFIGURATION
# =============================================================================

CIMTD = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD")
OUT_PARQUET = CIMTD / "cimt_legacy_2002_2011.parquet"

LEGACY_YEARS = (2002, 2011)  # inclusive legacy range

# ── Legacy HS6 -> modern split equivalents (vectorized .explode() mapping)
LEGACY_HS_MAP = {
    100110: [100111, 100119],   # Durum Wheat
    100190: [100191, 100199],   # Wheat Ex-Durum
    120500: [120510, 120590],   # Canola Seed (HS 2002 single code)
    120510: [120510],
    120590: [120590],
    120100: [120110, 120190],   # Soybeans
    100300: [100310, 100390],   # Barley (HS 2002 single code)
    100310: [100310],
    100390: [100390],
    100510: [100510],           # Corn
    100590: [100590],
    71340:  [71340],            # Red Lentils
    71341:  [71341],
    71310:  [71310],            # Yellow Peas
    71390:  [71390],
    151411: [151411],           # Canola Oil
    151419: [151419],
    151400: [151411, 151419],
    230641: [230641],           # Canola Meal
    230690: [230641],
    150710: [150710],           # Soybean Oil
    150790: [150790],
    150700: [150710, 150790],
    230400: [230400],           # Soybean Meal

    # ── Oats
    100400: [100410, 100490],   # Legacy 2002-2006: all raw oats → seed + commercial
    100410: [100410],           # Oat seed (passthrough)
    100490: [100490],           # Other oats (passthrough)
    110312: [110312],           # Legacy oat groats (1103.12, stable)
    110412: [110412],           # Rolled/flaked oats (passthrough)
    110422: [110422],           # Other worked oats — UOM may be KGM (handled by pipeline)
    110319: [110319],           # Oat groats/meal/pellets (1103.19.90 at HS10)
}

TARGET_HS6 = {
    100111, 100119, 100191, 100199, 120510, 120590, 120110, 120190,
    100510, 100590, 100310, 100390, 71340, 71341, 71310, 71390,
    151411, 151419, 230641, 150710, 150790, 230400,
    # Oats
    100400,                     # legacy raw oats (pre-2012 single code)
    100410, 100490,             # active raw oats (post-2011 split)
    110412, 110422,             # processed oats (GE x1.65 applied in cimt_build)
    # 110312 removed (expired Dec 2001), 110319 removed (non-oat catch-all)
}

# ── UOM -> KMT conversion (raw quantity per row)
#    TNE/T/MT  = metric tonnes  -> /1_000        (KMT)
#    KGM/KG/KGE = kilograms     -> /1_000_000    (KMT)
#    LBS/LB    = pounds         -> /2_204_623    (KMT: 2,204.623 lbs/KMT)
#    anything else (NMB, LTR, ...) -> KMT = NaN  (non-weight rows)
UOM_CONVERSION = [
    ({"TNE", "T", "MT"}, 1_000),
    ({"KGM", "KG", "KGE"}, 1_000_000),
    ({"LBS", "LB"}, 2_204_623),
]

# ── Legacy country names -> modern equivalents (pass 2 of normalization)
COUNTRY_RENAME = {
    "USSR": "Russian Federation", "Former USSR": "Russian Federation",
    "Soviet Union": "Russian Federation",
    "Czechoslovakia": "Czechia", "Czech Republic": "Czechia",
    "Yugoslavia": "Serbia",
    "East Germany": "Germany", "West Germany": "Germany",
    "Serbia and Montenegro": "Serbia",
    "Sudan (Former)": "Sudan",
    "Korea (South)": "Korea, South", "Korea Republic": "Korea, South",
    "Republic of Korea": "Korea, South",
    "Korea (North)": "Korea, North",
    "Iran (Islamic Republic of)": "Iran",
    "Syria (Arab Republic)": "Syria",
    "Libya (Arab Jamahiriya)": "Libya",
    "Tanzania": "Tanzania, United Republic of",
    "Congo (Brazzaville)": "Congo, Republic of the",
    "Congo (Kinshasa)": "Congo, Democratic Republic of the",
    "Ivory Coast": "Côte d'Ivoire",
    "Viet-Nam": "Viet Nam", "Vietnam": "Viet Nam",
    "Laos (PDR)": "Laos",
    "Burma": "Myanmar",
    "Moldova": "Moldova, Republic of",
    "Macedonia": "Macedonia, North",
    "Palestine": "Palestine, State of",
    "South Africa": "South Africa, Republic of",
    "Reunion": "Réunion",
    "Netherlands Antilles": "Other",
}

# ── 8-line hook to insert inside load_all_zips() in cimt_build.py
HOOK_BLOCK = """\
    # ── Legacy cache (2002-2011) built by cimt_legacy_adapter.py
    legacy_cache = folder / "cimt_legacy_2002_2011.parquet"
    if legacy_cache.exists():
        legacy = pd.read_parquet(legacy_cache)
        print(f"  Legacy cache: {len(legacy):,} rows "
              f"({legacy['Year'].min()}-{legacy['Year'].max()})")
        frames.append(legacy)
        print("  Legacy cache appended to frames")
"""

# =============================================================================
# HEADER / COLUMN HELPERS
# =============================================================================

_ACCENT_MAP = str.maketrans({
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "à": "a", "â": "a", "ä": "a",
    "ô": "o", "ö": "o",
    "î": "i", "ï": "i",
    "û": "u", "ü": "u",
    "ç": "c",
})


def normalize_header(s: str) -> str:
    """Lowercase + fold accents so 'AnnéeMois' -> 'anneemois' etc."""
    return str(s).lower().translate(_ACCENT_MAP)


def find_column(df: pd.DataFrame, *substrings: str) -> str | None:
    """First column whose normalized header contains any substring."""
    for col in df.columns:
        key = normalize_header(col)
        if any(sub in key for sub in substrings):
            return col
    return None


def infer_trade_type(name: str) -> str:
    """Infer TradeType from a file name."""
    low = name.lower()
    if "imp" in low:
        return "Import"
    if "exp" in low or "tot" in low:
        return "Export"
    raise ValueError(f"Cannot infer trade type from: {name}")


# =============================================================================
# COUNTRY LOOKUP (ODPF_6_CtyDesc.TXT)
# =============================================================================

def parse_ctydesc(lines) -> dict:
    """
    Parse ODPF_6_CtyDesc.TXT lines.
    Line format: CC NNN     DDDDDD DDDDDD English Name        French Name
    English name reliably starts after the 6-digit date fields.
    """
    mapping = {}
    for line in lines:
        if len(line) < 30:
            continue
        m = re.match(r'(\w{2})\s+\d+\s+\d{6}\s+\d{6}\s+(.+?)(?:\s{2,}|\s*$)', line)
        if m:
            code, english = m.group(1).strip(), m.group(2).strip()
            if code and english:
                mapping[code] = english
    return mapping


def load_ctydesc(folder: Path) -> dict:
    """
    Build code -> English-name lookup.
    1. Standalone ODPF_6_CtyDesc.TXT in the CIMTD folder (per spec).
    2. Fallback: extract ODPF_6_CtyDesc.TXT from the newest CIMT-CICM_*.zip
       that contains it (legacy 2002-2011 archives do not ship it).
    """
    standalone = folder / "ODPF_6_CtyDesc.TXT"
    if standalone.exists():
        with standalone.open(encoding="latin-1") as f:
            mapping = parse_ctydesc(f)
        if mapping:
            print(f"  Country lookup: {len(mapping)} codes from {standalone.name}")
            return mapping

    for z in sorted(folder.glob("CIMT-CICM_*.zip"), reverse=True):
        try:
            with zipfile.ZipFile(z) as zf:
                fname = next((n for n in zf.namelist() if "CtyDesc" in n), None)
                if not fname:
                    continue
                with zf.open(fname) as f:
                    lines = [ln.decode("latin-1") for ln in f]
                mapping = parse_ctydesc(lines)
                if mapping:
                    print(f"  Country lookup: {len(mapping)} codes "
                          f"from {z.name} ({Path(fname).name})")
                    return mapping
        except Exception:
            continue
    print("  Country lookup: NOT FOUND - 2-letter codes will remain unresolved")
    return {}


# =============================================================================
# FRAME PROCESSING
# =============================================================================

def process_frame(df: pd.DataFrame, trade_type: str,
                  source_name: str, keep_hs6: set) -> pd.DataFrame:
    """
    Normalize one raw chunk into the flat schema
    [Year, Month, TradeType, HS6, Country, KMT, ValueCAD] and filter to the
    legacy HS6 map keys (early memory reduction).
    """
    if df.empty:
        return df

    # ── Export quantity special case (before renaming):
    #    prefer TotalExports; else DomesticExports + ReExports; else generic.
    qty_cols = None
    if trade_type == "Export":
        keys = {c: normalize_header(c) for c in df.columns}
        total_col = next(
            (c for c, k in keys.items() if "total" in k and "export" in k), None)
        if total_col:
            qty_cols = [total_col]
        else:
            dom = next((c for c, k in keys.items() if "domestic" in k), None)
            rex = next((c for c, k in keys.items()
                        if "reexport" in k or "re-export" in k), None)
            if dom and rex:
                qty_cols = [dom, rex]

    # ── Flexible bilingual column matching (case-insensitive)
    rename = {}
    for col in df.columns:
        key = normalize_header(col)
        if "yearmonth" in key or "annee" in key or "periode" in key:
            rename[col] = "YearMonth"
        elif (col.strip().strip('"').upper() == "HS6"
              or "hs6" in key or "commodity" in key):
            rename[col] = "HS6"
        elif "country" in key or "pays" in key or "partner" in key or "cty" in key:
            rename[col] = "Country"
        elif qty_cols is None and ("quantity" in key or "quantit" in key or "vol" in key):
            rename[col] = "Quantity"
        elif "uom" in key or "unit" in key or "mesure" in key:
            rename[col] = "UOM"
        elif "value" in key or "valeur" in key or "val" in key:
            rename[col] = "ValueCAD"
    df = df.rename(columns=rename)

    # ── Year / Month (YYYYMM integer -> Year = YYYYMM // 100, Month % 100)
    if "YearMonth" in df.columns:
        ym = pd.to_numeric(
            df["YearMonth"].astype(str).str.strip().str.strip('"'),
            errors="coerce")
        df["Year"] = (ym // 100).astype("Int64")
        df["Month"] = (ym % 100).astype("Int64")
    elif "Year" in df.columns and "Month" in df.columns:
        df["Year"] = pd.to_numeric(df["Year"], errors="coerce").astype("Int64")
        df["Month"] = pd.to_numeric(df["Month"], errors="coerce").astype("Int64")
    else:
        raise ValueError(f"No year/month column found in {source_name}")
    df = df.dropna(subset=["Year", "Month"])
    if df.empty:
        return df
    df["Year"] = df["Year"].astype(int)
    df["Month"] = df["Month"].astype(int)

    # ── HS6 as integer (leading zeros are significant: '010110' -> 10110)
    df["HS6"] = pd.to_numeric(
        df["HS6"].astype(str).str.strip().str.strip('"'),
        errors="coerce").astype("Int64")
    df = df.dropna(subset=["HS6"])
    if df.empty:
        return df
    df["HS6"] = df["HS6"].astype(int)

    # ── Early filter to legacy map keys + targets (drops ~99% of rows)
    df = df[df["HS6"].isin(keep_hs6)]
    if df.empty:
        return df

    # ── Country (raw code/name; full normalization happens later)
    df["Country"] = df["Country"].astype(str).str.strip().str.strip('"')

    # ── Quantity -> KMT (per-row UOM conversion)
    if qty_cols is not None:
        df["Quantity"] = (
            df[qty_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1))
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")

    uom_col = find_column(df, "uom", "unit", "mesure")
    if uom_col is not None:
        uom = df[uom_col].astype(str).str.strip().str.strip('"').str.upper()
    else:
        uom = pd.Series("TNE", index=df.index)  # fallback: assume tonnes

    kmt = pd.Series(float("nan"), index=df.index)
    for uoms, divisor in UOM_CONVERSION:
        mask = uom.isin(uoms)
        kmt[mask] = df.loc[mask, "Quantity"] / divisor
    df["KMT"] = kmt

    # ── ValueCAD
    df["ValueCAD"] = pd.to_numeric(df["ValueCAD"], errors="coerce").fillna(0)

    df["TradeType"] = trade_type
    return df[["Year", "Month", "TradeType", "HS6", "Country", "KMT", "ValueCAD"]]


def read_csv_source(f, trade_type: str, source_name: str,
                    keep_hs6: set) -> pd.DataFrame:
    """Chunked read of one CSV stream -> normalized flat frame."""
    chunks = []
    for chunk in pd.read_csv(f, encoding="utf-8", low_memory=False,
                             chunksize=500_000):
        c = process_frame(chunk, trade_type, source_name, keep_hs6)
        if not c.empty:
            chunks.append(c)
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def find_hs6_csv(zf: zipfile.ZipFile, trade_type: str) -> str:
    """Locate ODPFN015 (imports) or ODPFN019 (exports) inside a zip."""
    prefix = "ODPFN015" if trade_type == "Import" else "ODPFN019"
    match = next(
        (n for n in zf.namelist()
         if Path(n).name.startswith(prefix) and n.endswith(".csv")), None)
    if not match:
        raise FileNotFoundError(
            f"No HS6 CSV ({prefix}*) found in {zf.filename}")
    return match


# =============================================================================
# HS6 EXPLOSION (vectorized)
# =============================================================================

def explode_hs6(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map legacy HS6 codes to modern split equivalents, explode with
    df.explode(), divide KMT/ValueCAD by the split count, and filter to
    TARGET_HS6.  No iterrows().
    """
    df = df.copy()
    df["_targets"] = df["HS6"].map(lambda c: LEGACY_HS_MAP.get(c, [c]))
    df["_n"] = df["_targets"].map(len)
    df = df.explode("_targets")
    df["KMT"] = df["KMT"] / df["_n"]
    df["ValueCAD"] = df["ValueCAD"] / df["_n"]
    df["HS6"] = df["_targets"]
    df = df[df["HS6"].isin(TARGET_HS6)]
    return df.drop(columns=["_targets", "_n"])


# =============================================================================
# SOURCE DISCOVERY
# =============================================================================

CSV_PATTERNS = [
    "*Imp*.csv", "*imp*.csv", "*IMP*.csv",
    "*Exp*.csv", "*exp*.csv", "*EXP*.csv",
    "*Tot*.csv", "*tot*.csv",
]


def discover_sources(folder: Path):
    """
    Returns (csv_files, zip_files).
    Prefers unzipped legacy CSVs (recursive glob).  Falls back to the
    2002-2011 legacy ZIP archives when no CSVs are present.
    """
    csv_files = sorted({p for pat in CSV_PATTERNS for p in folder.rglob(pat)})
    if csv_files:
        return csv_files, []

    zip_files = []
    for z in sorted(folder.glob("CIMT-CICM_*.zip")):
        m = re.search(r"(?:Imp|Tot_Exp)_(20\d{2})", z.name)
        if (m and LEGACY_YEARS[0] <= int(m.group(1)) <= LEGACY_YEARS[1]
                and "Dom" not in z.name):
            zip_files.append(z)
    return [], zip_files


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 64)
    print("  CIMT Legacy Adapter (2002-2011) -> normalized Parquet cache")
    print("=" * 64 + "\n")

    keep_hs6 = set(LEGACY_HS_MAP.keys()) | TARGET_HS6
    cty_lookup = load_ctydesc(CIMTD)

    csv_files, zip_files = discover_sources(CIMTD)
    if not csv_files and not zip_files:
        raise RuntimeError(
            f"No legacy CIMT CSV files or 2002-2011 ZIP archives found in {CIMTD}")

    frames = []
    if csv_files:
        print(f"Found {len(csv_files)} unzipped legacy CSV file(s)\n")
        for path in csv_files:
            trade_type = infer_trade_type(path.name)
            print(f"  {path.name:<45} [{trade_type}]", end="  ", flush=True)
            with path.open("rb") as f:
                df = read_csv_source(f, trade_type, path.name, keep_hs6)
            print(f"{len(df):>10,} rows")
            frames.append(df)
    else:
        print(f"No unzipped legacy CSVs found - reading {len(zip_files)} "
              f"legacy ZIP archive(s) (2002-2011)\n")
        for z in zip_files:
            trade_type = infer_trade_type(z.name)
            print(f"  {z.name:<45} [{trade_type}]", end="  ", flush=True)
            with zipfile.ZipFile(z) as zf:
                csv_name = find_hs6_csv(zf, trade_type)
                with zf.open(csv_name) as f:
                    df = read_csv_source(f, trade_type, z.name, keep_hs6)
            print(f"{len(df):>10,} rows")
            frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    print(f"\n  Combined raw rows: {len(df):,}")

    # ── Vectorized legacy HS6 mapping + explosion
    df = explode_hs6(df)
    print(f"  After HS6 explosion + target filter: {len(df):,} rows")

    # ── Country normalization (3 passes)
    df["Country"] = df["Country"].str.strip()
    df["Country"] = df["Country"].replace(COUNTRY_RENAME)
    two_letter = df["Country"].str.len() == 2
    if two_letter.any() and cty_lookup:
        resolved = df.loc[two_letter, "Country"].map(cty_lookup)
        df.loc[two_letter, "Country"] = resolved.fillna(
            df.loc[two_letter, "Country"])
        n_left = (df["Country"].str.len() == 2).sum()
        print(f"  Country resolution: {int(two_letter.sum()):,} 2-letter codes "
              f"-> {n_left:,} remaining unresolved")

    # ── Aggregation & export
    df = df.dropna(subset=["KMT"])
    df = (
        df.groupby(["Year", "Month", "TradeType", "HS6", "Country"],
                   as_index=False)
        .agg({"KMT": "sum", "ValueCAD": "sum"})
    )
    df = df.drop_duplicates(
        subset=["Year", "Month", "TradeType", "HS6", "Country"])
    df = df.sort_values(["TradeType", "Year", "Month", "Country"])

    # ── Enforce exact output schema
    df = df[["Year", "Month", "TradeType", "HS6", "Country", "KMT", "ValueCAD"]]
    df["Year"] = df["Year"].astype("int64")
    df["Month"] = df["Month"].astype("int64")
    df["HS6"] = df["HS6"].astype("Int64")
    df["KMT"] = df["KMT"].astype("float64")
    df["ValueCAD"] = df["ValueCAD"].astype("float64")

    df.to_parquet(OUT_PARQUET, engine="pyarrow", compression="snappy",
                  index=False)

    size_mb = OUT_PARQUET.stat().st_size / 1024 / 1024
    print(f"\nLegacy adapter complete: {len(df):,} rows generated | "
          f"Years {df['Year'].min()}–{df['Year'].max()} | "
          f"File size: {size_mb:.2f} MB")

    print("\n" + "=" * 64)
    print("HOOK - insert inside load_all_zips() in cimt_build.py "
          "(after the zip loop, before `if not frames:`):")
    print("=" * 64)
    print(HOOK_BLOCK)


if __name__ == "__main__":
    main()