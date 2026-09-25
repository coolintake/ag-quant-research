"""
CIMT Trade Actuals Ingestion Module
Reads monthly Excel files from CIMT_DIR, extracts trade data from 'Raw Data' sheet,
standardizes columns, and tags with source metadata.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

from config.paths import CIMT_DIR


# Commodity name mapping from filename to standardized name
FILENAME_TO_COMMODITY = {
    "CIMT_Wheat_(Ex-Durum)": "Wheat (Ex-Durum)",
    "CIMT_Amber_Durum": "Amber Durum",
    "CIMT_Barley": "Barley",
    "CIMT_Canola": "Canola",
    "CIMT_Canola_Meal": "Canola Meal",
    "CIMT_Canola_Oil": "Canola Oil",
    "CIMT_Corn": "Corn",
    "CIMT_Red_Lentils": "Red Lentils",
    "CIMT_Soybeans": "Soybeans",
    "CIMT_Soybean_Meal": "Soybean Meal",
    "CIMT_Soybean_Oil": "Soybean Oil",
    "CIMT_Yellow_Peas": "Yellow Peas",
}

# Expected columns in Raw Data sheet
EXPECTED_RAW_COLUMNS = ["Year", "Month", "TradeType", "HS6", "Country", "KMT", "ValueCAD"]


def _discover_cimt_files() -> List[Tuple[str, Path]]:
    """
    Discover CIMT Excel files in CIMT_DIR.
    Returns list of (commodity_name, file_path) tuples.
    """
    files = []
    for file_path in CIMT_DIR.glob("CIMT_*.xlsx"):
        # Skip legacy parquet and old versions
        if "legacy" in file_path.name.lower() or "pivot" in file_path.name.lower():
            continue
        
        # Extract commodity name from filename
        # Pattern: CIMT_<Commodity>_<Date>.xlsx
        match = re.match(r"CIMT_(.+)_\d{8}\.xlsx$", file_path.name)
        if match:
            commodity_key = match.group(1)
            commodity_name = FILENAME_TO_COMMODITY.get(commodity_key, commodity_key.replace("_", " "))
            files.append((commodity_name, file_path))
        else:
            print(f"  WARNING: Could not parse commodity from filename: {file_path.name}")
    
    return sorted(files, key=lambda x: x[0])


def _read_cimt_raw_data(file_path: Path) -> pd.DataFrame:
    """Read the 'Raw Data' sheet from a CIMT Excel file."""
    try:
        df = pd.read_excel(file_path, sheet_name="Raw Data", engine="openpyxl")
    except ValueError as e:
        # Try alternative sheet names
        xls = pd.ExcelFile(file_path, engine="openpyxl")
        raw_sheets = [s for s in xls.sheet_names if "raw" in s.lower() or "data" in s.lower()]
        if raw_sheets:
            df = pd.read_excel(file_path, sheet_name=raw_sheets[0], engine="openpyxl")
        else:
            raise ValueError(f"No 'Raw Data' sheet found in {file_path}. Available: {xls.sheet_names}")
    
    return df


def _standardize_cimt_columns(df: pd.DataFrame, commodity_name: str) -> pd.DataFrame:
    """Standardize CIMT column names and add commodity."""
    df_std = df.copy()
    
    # Check required columns
    missing = [c for c in EXPECTED_RAW_COLUMNS if c not in df_std.columns]
    if missing:
        raise ValueError(f"Missing expected columns in {commodity_name}: {missing}. Found: {list(df_std.columns)}")
    
    # Rename columns
    column_map = {
        "Year": "Year",
        "Month": "Month",
        "TradeType": "Trade_Type",
        "HS6": "HS_Code",
        "Country": "Country",
        "KMT": "Value_KMT",
        "ValueCAD": "Value_CAD",
    }
    df_std = df_std.rename(columns=column_map)
    
    # Add commodity
    df_std["Commodity"] = commodity_name
    
    # Construct Date (first of month) - specify format to avoid warning
    df_std["Date"] = pd.to_datetime(
        dict(year=df_std["Year"], month=df_std["Month"], day=1),
        errors="coerce"
    )
    # Generate Date_Str only for valid dates
    df_std["Date_Str"] = df_std["Date"].dt.strftime("%Y-%m-%d")
    df_std.loc[df_std["Date"].isna(), "Date_Str"] = pd.NA
    
    # Clean Trade_Type
    df_std["Trade_Type"] = df_std["Trade_Type"].astype(str).str.strip().str.capitalize()
    
    # Ensure numeric
    df_std["Value_KMT"] = pd.to_numeric(df_std["Value_KMT"], errors="coerce")
    df_std["Value_CAD"] = pd.to_numeric(df_std["Value_CAD"], errors="coerce")
    
    return df_std


def _add_cimt_metadata(df: pd.DataFrame) -> pd.DataFrame:
    """Add source metadata columns."""
    df_meta = df.copy()
    df_meta["Source"] = "CIMT_Actual"
    df_meta["Data_Type"] = "Actual"
    df_meta["Unit"] = "KMT"
    return df_meta


def ingest_cimt_file(commodity_name: str, file_path: Path) -> pd.DataFrame:
    """
    Ingest a single CIMT Excel file.
    
    Args:
        commodity_name: Standardized commodity name
        file_path: Path to the Excel file
    
    Returns:
        Standardized DataFrame
    """
    print(f"  Ingesting {commodity_name} from {file_path.name}...")
    
    # Read raw data
    df = _read_cimt_raw_data(file_path)
    print(f"    Raw rows: {len(df)}")
    
    # Standardize
    df = _standardize_cimt_columns(df, commodity_name)
    
    # Add metadata
    df = _add_cimt_metadata(df)
    
    # Select final columns
    final_cols = [
        "Date", "Date_Str", "Year", "Month", "Commodity",
        "Trade_Type", "HS_Code", "Country",
        "Value_KMT", "Value_CAD", "Unit",
        "Source", "Data_Type"
    ]
    
    final_cols = [c for c in final_cols if c in df.columns]
    df = df[final_cols]
    
    print(f"    Final rows: {len(df)}")
    return df


def ingest_all_cimt() -> pd.DataFrame:
    """
    Ingest all CIMT Excel files in CIMT_DIR.
    
    Returns:
        Combined DataFrame with all CIMT trade actuals
    """
    print("Discovering CIMT files...")
    files = _discover_cimt_files()
    print(f"Found {len(files)} CIMT files")
    
    all_dfs = []
    for commodity_name, file_path in files:
        try:
            df = ingest_cimt_file(commodity_name, file_path)
            all_dfs.append(df)
        except Exception as e:
            print(f"    ERROR ingesting {commodity_name}: {e}")
            # Continue with other files
            continue
    
    if not all_dfs:
        raise ValueError("No CIMT files successfully ingested")
    
    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\nTotal CIMT rows ingested: {len(combined)}")
    return combined


if __name__ == "__main__":
    # Quick test run
    print("=" * 60)
    print("TESTING CIMT INGESTION")
    print("=" * 60)
    
    try:
        df = ingest_all_cimt()
        print(f"\n[OK] CIMT ingestion successful: {len(df)} total rows")
        print(f"  Columns: {list(df.columns)}")
        valid_dates = df['Date_Str'].dropna()
        if len(valid_dates) > 0:
            print(f"  Date range: {valid_dates.min()} to {valid_dates.max()}")
        else:
            print(f"  Date range: No valid dates")
        print(f"  Unique commodities: {df['Commodity'].nunique()}")
        print(f"  Commodities: {sorted(df['Commodity'].unique())}")
        print(f"  Trade types: {df['Trade_Type'].unique()}")
        print(f"  Unique countries: {df['Country'].nunique()}")
    except Exception as e:
        print(f"\n[FAIL] CIMT ingestion failed: {e}")
        raise