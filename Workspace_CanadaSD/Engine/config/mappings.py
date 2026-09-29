"""
Mapping_Dictionary loader and validator for the CMFT Engine.

Source of truth: CMFT_Main.csv in the workspace root (a flat copy of the old
Mapping_Dictionary sheet). Archive\\old_CMFT_Main.xlsx is kept for reference only
and is never read.

    python run_pipeline.py --check      # paths + mapping validation

Columns: Source Commodity | Crop_Year | Commodity_Group | StatCan_Raw_Metric |
Tier1_Commodity_Metric | Tier2_National_Metric | Calculation_Notes
  * StatCan_Raw_Metric 'N-A' = the line has no StatCan source (notes for the
    vegetable-oil / meal sheets). It never matches a StatCan row.
  * A BLANK Tier1_Commodity_Metric = "known StatCan line, deliberately not
    used". It counts as mapped for validation; ingest drops it.
"""

import hashlib
from functools import lru_cache
from typing import List

import pandas as pd

from .paths import CMFT_MAIN_CSV, STATCAN_DIR

MAPPING_CSV = CMFT_MAIN_CSV            # single mapping file (name kept for callers)

REQUIRED_COLUMNS = [
    "Source Commodity",
    "Commodity_Group",
    "StatCan_Raw_Metric",
    "Tier1_Commodity_Metric",
    "Tier2_National_Metric",
]

# Groups whose rows map StatCan 32100013 (the table that feeds the *_Mo/*_Yr
# balance sheets). Vegetable Oil / Protein Meal rows describe other sheets.
SD_GROUPS = ("Cereal", "Oilseed", "Pulse")

# Every active (Tier 1) commodity must map a StatCan line to each of these
CORE_METRICS = (
    "Production", "Beginning Stocks", "Ending Stocks",
    "Exports", "Imports", "FSR",
)

# StatCan 32100013 lines that are subtotals or breakdowns of lines already
# mapped (hierarchy from 32100013_MetaData.csv). Mapping them as well would
# double count, so they are never expected in the CSV.
STATCAN_SD_STRUCTURAL = {
    "Total supplies",                            # = beginning stocks + production + imports
    "Total disposition",                         # = exports + domestic + ending stocks
    "Total domestic disappearance",              # = sum of the domestic use lines
    "Beginning stocks on farms",                 # part of Total beginning stocks
    "Beginning stocks in commercial positions",  # part of Total beginning stocks
    "Ending stocks on farms",                    # part of Total ending stocks
    "Ending stocks in commercial positions",     # part of Total ending stocks
    "Grain exports",                             # part of Total exports
    "Product exports",                           # part of Total exports
}

NO_SOURCE = "N-A"
CROP_YEAR_LABEL_TO_MONTH = {"Aug-Jul": 8, "Sep-Aug": 9, "Oct-Sep": 10}
KEY = ["Commodity_Norm", "StatCan_Raw_Metric"]


class MappingValidationError(Exception):
    """Raised by validate_mapping(); the message lists every problem found."""


# ---------------------------------------------------------------- helpers
def _clean_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace from text columns; blanks become NA."""
    out = df.copy()
    for col in out.select_dtypes(include=["object"]).columns:
        out[col] = out[col].astype(str).str.strip()
    return out.replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})


def _validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise MappingValidationError(
            f"{MAPPING_CSV.name}: missing required columns {missing}. Found: {list(df.columns)}")


# ------------------------------------------------------------------ loader
@lru_cache(maxsize=1)
def load_dictionary_mapping() -> pd.DataFrame:
    """Load and cache CMFT_Main.csv (all columns as text)."""
    if not MAPPING_CSV.exists():
        raise FileNotFoundError(f"Mapping CSV not found: {MAPPING_CSV}")
    try:
        df = pd.read_csv(MAPPING_CSV, dtype=str, encoding="utf-8-sig")
    except UnicodeDecodeError:                      # saved from Excel as ANSI
        df = pd.read_csv(MAPPING_CSV, dtype=str, encoding="cp1252")
    # Drop the empty trailing columns Excel leaves behind (Unnamed: 7, ...)
    stray = [c for c in df.columns if str(c).startswith("Unnamed") and df[c].isna().all()]
    df = df.drop(columns=stray).dropna(how="all")
    _validate_columns(df)
    return _clean_text_columns(df)


def mapping_fingerprint() -> str:
    """Short hash of the CSV, printed in logs so mapping changes are traceable."""
    return hashlib.sha256(MAPPING_CSV.read_bytes()).hexdigest()[:12]


def get_mapping_stats() -> dict:
    df = load_dictionary_mapping()
    return {
        "total_rows": len(df),
        "unique_source_commodities": df["Source Commodity"].nunique(),
        "unique_statcan_metrics": df["StatCan_Raw_Metric"].nunique(),
        "unique_tier1_metrics": df["Tier1_Commodity_Metric"].nunique(),
        "unique_tier2_metrics": df["Tier2_National_Metric"].nunique(),
        "columns": list(df.columns),
        "source": str(MAPPING_CSV),
        "fingerprint": mapping_fingerprint(),
    }


# -------------------------------------------------------------- validation
def _statcan_sd_lines() -> pd.DataFrame:
    """One row per (Commodity, StatCan_Raw_Metric) in StatCan 32100013 with
    the number of non-zero values and their absolute sum (KMT)."""
    from modules.ingest_statcan import STATCAN_FOLDERS, CSV_FILES   # lazy: avoids import cycle
    path = STATCAN_DIR / STATCAN_FOLDERS["supply_disposition"] / CSV_FILES["supply_disposition"]
    if not path.exists():
        raise FileNotFoundError(f"StatCan 32100013 CSV not found: {path}")
    cols = {"Type of crop": "Commodity",
            "Supply and disposition of grains": "StatCan_Raw_Metric",
            "VALUE": "Value"}
    df = pd.read_csv(path, dtype=str, usecols=list(cols), low_memory=False).rename(columns=cols)
    df["Commodity"] = df["Commodity"].str.strip()
    df["StatCan_Raw_Metric"] = df["StatCan_Raw_Metric"].str.strip()
    v = pd.to_numeric(df["Value"], errors="coerce").fillna(0.0)
    df["NonZero"] = v.ne(0)
    df["AbsKMT"] = v.abs()
    return (df.groupby(["Commodity", "StatCan_Raw_Metric"], as_index=False)
              .agg(NonZero=("NonZero", "sum"), AbsKMT=("AbsKMT", "sum")))


def validate_mapping(check_statcan: bool = True) -> List[str]:
    """Validate CMFT_Main.csv. Raises MappingValidationError listing every
    problem; returns a list of non-fatal warnings if it passes.

    Errors (balance-sheet rows only, i.e. SD_GROUPS):
      1. [duplicate] A (commodity, raw metric) pair mapped to more than one
         target, or also claimed by a Vegetable Oil / Protein Meal row -
         ingest would duplicate those StatCan rows (double counting).
      2. [core]      An active (Tier 1) commodity missing a CORE_METRICS line.
      3. [unmapped]  A StatCan 32100013 line with non-zero data for an active
         commodity that has no mapping row (its volume would drop out of the
         balance sheet and land in FSR). Subtotals are exempt.
      4. [crop year] Crop_Year label disagrees with config/commodities.py.
    Warnings: harmless repeats, duplicates on non-balance-sheet rows, unmapped
    lines that carry no data.
    """
    from .commodities import normalize_commodity, crop_year_start, TIER1_COMMODITIES

    m = load_dictionary_mapping().copy()
    m["Commodity_Norm"] = m["Source Commodity"].map(
        lambda v: normalize_commodity(v) if pd.notna(v) else v)
    errors, warnings = [], []

    blank = m["Source Commodity"].isna() | m["StatCan_Raw_Metric"].isna()
    if blank.any():
        warnings.append(f"{int(blank.sum())} row(s) with a blank Source Commodity or "
                        f"StatCan_Raw_Metric (ignored)")
    m = m[~blank]
    has_source = m["StatCan_Raw_Metric"] != NO_SOURCE
    sd = m[m["Commodity_Group"].isin(SD_GROUPS) & has_source]
    other = m[~m["Commodity_Group"].isin(SD_GROUPS) & has_source]

    # 1a. Duplicate pairs within the balance-sheet rows (normalised commodity,
    #     exactly as ingest matches them)
    targets = ["Tier1_Commodity_Metric", "Tier2_National_Metric"]
    for (comm, raw), g in sd[sd.duplicated(KEY, keep=False)].groupby(KEY):
        distinct = g[targets].fillna("(blank)").drop_duplicates()
        if len(distinct) > 1:
            shown = "; ".join(" / ".join(r) for r in distinct.itertuples(index=False))
            errors.append(f"[duplicate] {comm} | {raw}: {len(distinct)} different targets "
                          f"({shown}) -> these StatCan rows would be counted {len(distinct)}x")
        else:
            warnings.append(f"{comm} | {raw}: listed {len(g)} times with the same target "
                            f"(harmless; delete the extra rows)")
    # 1b. A Vegetable Oil / Protein Meal row claiming a balance-sheet key
    clash = other.merge(sd[KEY].drop_duplicates(), on=KEY)
    for (comm, raw), g in clash.groupby(KEY):
        errors.append(f"[duplicate] {comm} | {raw}: also mapped by "
                      f"{sorted(g['Commodity_Group'].unique())} rows -> StatCan 32100013 "
                      f"rows would be duplicated")
    # 1c. Duplicates among non-balance-sheet rows: only crush-table rows are
    #     affected (never the balance sheets), so warn
    dup_other = other[other.duplicated(KEY, keep=False)].drop_duplicates(KEY)
    if len(dup_other):
        pairs = ", ".join(f"{c} | {r}" for c, r in dup_other[KEY].itertuples(index=False))
        warnings.append(f"Vegetable Oil / Protein Meal rows reuse a (commodity, raw metric) "
                        f"pair for several targets: {pairs}. Not used by the balance sheets; "
                        f"fix before those sheets are built.")

    # 2. Core lines for active commodities
    for comm in TIER1_COMMODITIES:
        mapped = set(sd.loc[sd["Commodity_Norm"] == comm, "Tier1_Commodity_Metric"].dropna())
        if not mapped:
            errors.append(f"[core] {comm}: no Cereal/Oilseed/Pulse mapping rows at all")
            continue
        missing = [c for c in CORE_METRICS if c not in mapped]
        if missing:
            errors.append(f"[core] {comm}: no StatCan line mapped to {missing}")

    # 3. StatCan 32100013 lines for active commodities with no mapping row
    if check_statcan:
        lines = _statcan_sd_lines()
        lines["Commodity_Norm"] = lines["Commodity"].map(normalize_commodity)
        lines = lines[lines["Commodity_Norm"].isin(TIER1_COMMODITIES)
                      & ~lines["StatCan_Raw_Metric"].isin(STATCAN_SD_STRUCTURAL)]
        known = set(zip(sd["Commodity_Norm"], sd["StatCan_Raw_Metric"]))
        lines = lines[[k not in known for k in zip(lines["Commodity_Norm"],
                                                   lines["StatCan_Raw_Metric"])]]
        for comm, g in lines.groupby("Commodity_Norm"):
            live = g[g["NonZero"] > 0]
            if len(live):
                shown = ", ".join(f"{r.StatCan_Raw_Metric} ({int(r.NonZero)} values, "
                                  f"{r.AbsKMT:,.0f} KMT)" for r in live.itertuples())
                errors.append(f"[unmapped] {comm}: StatCan line(s) with data but no mapping "
                              f"row: {shown}")
            empty = sorted(g.loc[g["NonZero"] == 0, "StatCan_Raw_Metric"])
            if empty:
                warnings.append(f"{comm}: unmapped StatCan line(s) with no data: {empty}")

    # 4. Crop-year labels vs config/commodities.py
    for comm, g in sd.dropna(subset=["Crop_Year"]).groupby("Commodity_Norm"):
        for label in g["Crop_Year"].unique():
            month = CROP_YEAR_LABEL_TO_MONTH.get(label)
            if month is None:
                warnings.append(f"{comm}: unrecognised Crop_Year label '{label}'")
            elif comm in TIER1_COMMODITIES and month != crop_year_start(comm):
                errors.append(f"[crop year] {comm}: CSV says {label}, config/commodities.py "
                              f"starts the crop year in month {crop_year_start(comm)}")

    if errors:
        raise MappingValidationError(
            f"{MAPPING_CSV.name} failed {len(errors)} check(s):\n"
            + "\n".join(f"    - {e}" for e in errors)
            + "\n  Fix: edit CMFT_Main.csv. To mark a StatCan line as deliberately unused, "
              "add a row for it with a blank Tier1_Commodity_Metric.")
    return warnings


if __name__ == "__main__":
    for k, v in get_mapping_stats().items():
        print(f"  {k}: {v}")
