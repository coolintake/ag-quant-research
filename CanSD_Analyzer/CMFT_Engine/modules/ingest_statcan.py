"""
StatCan CANSIM CSV Ingestion Module
Reads raw CANSIM CSV files from 4 folders, standardizes columns,
applies CORRECT unit scaling, and merges with Dictionary_Mapping for Tier 1/2 metrics.

Key fixes:
- S&D data (32100013) already in KMT (Metric tonnes, thousands) - NO division
- Planting area in Acres -> MLN AC (divide by 1,000,000)
- Crush data in Tonnes -> KMT (divide by 1,000)
- Feedstock in Metric tonnes -> KMT (divide by 1,000)
- July revision filtering for S&D production/stocks
- Soybean Sep-Aug crop year with July ending stock anchor
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
from functools import lru_cache

from config.paths import STATCAN_DIR
from config.mappings import load_dictionary_mapping


# Folder names in STATCAN_DIR
STATCAN_FOLDERS = {
    "planting": "Planting_32100359-eng",
    "supply_disposition": "S&D_32100013-eng",
    "crush": "Crush_32100352-eng",
    "feedstock": "Feedstock_25100082-eng",
}

# CSV file names within each folder
CSV_FILES = {
    "planting": "32100359.csv",
    "supply_disposition": "32100013.csv",
    "crush": "32100352.csv",
    "feedstock": "25100082.csv",
}

# Column mapping from raw CANSIM to standardized names
RAW_TO_STANDARD = {
    "REF_DATE": "Date",
    "GEO": "Geography",
    "DGUID": "DGUID",
    "Harvest disposition": "StatCan_Raw_Metric",  # Planting
    "Type of crop": "Commodity",  # Planting, S&D
    "Supply and disposition of grains": "StatCan_Raw_Metric",  # S&D
    "Process": "StatCan_Raw_Metric",  # Crush
    "Commodity": "Commodity",  # Crush
    "Supply and disposition": "StatCan_Raw_Metric",  # Feedstock
    "Products": "Commodity",  # Feedstock
    "UOM": "Unit",
    "UOM_ID": "UOM_ID",
    "SCALAR_FACTOR": "Scalar_Factor",
    "SCALAR_ID": "Scalar_ID",
    "VECTOR": "Vector",
    "COORDINATE": "Coordinate",
    "VALUE": "Value",
    "STATUS": "Status",
    "SYMBOL": "Symbol",
    "TERMINATED": "Terminated",
    "DECIMALS": "Decimals",
}

# Unit scaling rules - CORRECTED
# S&D data: Metric tonnes with SCALAR_FACTOR=thousands -> already KMT (divisor=1)
# Planting: Acres -> MLN AC (divide by 1,000,000)
# Crush: Tonnes -> KMT (divide by 1,000)
# Feedstock: Metric tonnes -> KMT (divide by 1,000)
UNIT_SCALING = {
    # (raw_unit, scalar_factor, target_unit, divisor)
    ("Acres", "units"): ("MLN AC", 1_000_000),
    ("Hectares", "units"): ("MLN HA", 1_000_000),  # 1 MLN HA = 1,000,000 HA
    ("Metric tonnes", "thousands"): ("KMT", 1),  # Already in KMT!
    ("Metric tonnes", "units"): ("KMT", 1_000),  # Feedstock: metric tonnes -> KMT
    ("Tonnes", "units"): ("KMT", 1_000),  # Crush: tonnes -> KMT
    ("Kilograms", "units"): ("KMT", 1_000_000),
}


def _read_cansim_csv(folder_key: str) -> pd.DataFrame:
    """Read a single CANSIM CSV file."""
    folder = STATCAN_DIR / STATCAN_FOLDERS[folder_key]
    csv_file = folder / CSV_FILES[folder_key]
    
    if not csv_file.exists():
        raise FileNotFoundError(f"CANSIM CSV not found: {csv_file}")
    
    df = pd.read_csv(csv_file, dtype=str, low_memory=False)
    return df


def _standardize_columns(df: pd.DataFrame, folder_key: str) -> pd.DataFrame:
    """Rename columns to standardized names."""
    df_std = df.rename(columns=RAW_TO_STANDARD)
    
    # Ensure required columns exist
    required = ["Date", "Commodity", "StatCan_Raw_Metric", "Value", "Unit", "Scalar_Factor"]
    for col in required:
        if col not in df_std.columns:
            df_std[col] = pd.NA
    
    return df_std


def _clean_value_column(df: pd.DataFrame) -> pd.DataFrame:
    """Convert Value column to numeric, handling special codes."""
    df_clean = df.copy()
    
    # Replace special StatCan codes with NaN
    special_codes = ["..", "...", "x", "F", "U", "E"]
    df_clean["Value"] = df_clean["Value"].replace(special_codes, np.nan)
    
    # Convert to numeric
    df_clean["Value"] = pd.to_numeric(df_clean["Value"], errors="coerce")
    
    return df_clean


def _filter_july_revision(df: pd.DataFrame, folder_key: str) -> pd.DataFrame:
    """
    Filter S&D data to keep only the July revision (final estimate) for each crop year.
    For S&D table, REF_DATE format is YYYY-MM where MM is 12, 03, 07, 12 (quarterly releases).
    July release (MM=07) is the final estimate for the crop year.
    """
    if folder_key != "supply_disposition":
        return df
    
    df_filtered = df.copy()
    
    # Parse year and month from Date
    df_filtered["_year"] = df_filtered["Date"].dt.year
    df_filtered["_month"] = df_filtered["Date"].dt.month
    
    # For each commodity and crop year, keep only July (month=7) release
    # Crop year for cereals/oilseeds (except soybeans) starts in August
    # So July release belongs to the crop year that started previous August
    
    # Group by commodity and crop year, keep July release
    def get_crop_year(row):
        # Standard crop year: Aug-Jul
        if row["_month"] >= 8:
            return row["_year"]
        else:
            return row["_year"] - 1
    
    df_filtered["_crop_year"] = df_filtered.apply(get_crop_year, axis=1)
    
    # Keep only July (month=7) releases for production, stocks, supply/disposition metrics
    # These are the final estimates
    july_mask = df_filtered["_month"] == 7
    
    # For metrics that have quarterly updates, keep July
    # For metrics that are only annual, keep as-is
    # We'll keep July for all S&D metrics to get final estimates
    
    df_july = df_filtered[july_mask].copy()
    print(f"  July revision filter: {len(df_filtered)} -> {len(df_july)} rows")
    
    # Drop helper columns
    df_july = df_july.drop(columns=["_year", "_month", "_crop_year"])
    
    return df_july


def _apply_unit_scaling(df: pd.DataFrame) -> pd.DataFrame:
    """Apply CORRECT unit scaling based on Unit and Scalar_Factor columns."""
    df_scaled = df.copy()
    
    # Initialize scaled columns
    df_scaled["Scaled_Value"] = df_scaled["Value"]
    df_scaled["Scaled_Unit"] = df_scaled["Unit"]
    
    for (raw_unit, scalar_factor), (target_unit, divisor) in UNIT_SCALING.items():
        mask = (
            (df_scaled["Unit"].str.strip().str.lower() == raw_unit.lower()) &
            (df_scaled["Scalar_Factor"].str.strip().str.lower() == scalar_factor.lower())
        )
        df_scaled.loc[mask, "Scaled_Value"] = df_scaled.loc[mask, "Value"] / divisor
        df_scaled.loc[mask, "Scaled_Unit"] = target_unit
    
    # For units not in scaling rules, keep original (yield, percentages, etc.)
    
    return df_scaled


def _normalize_date(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Date column to YYYY-MM-DD format."""
    df_norm = df.copy()
    
    def parse_date(val):
        if pd.isna(val):
            return pd.NaT
        val = str(val).strip()
        # Try YYYY-MM
        if len(val) == 7 and val[4] == "-":
            return pd.to_datetime(val + "-01", errors="coerce")
        # Try YYYY
        if len(val) == 4 and val.isdigit():
            return pd.to_datetime(val + "-01-01", errors="coerce")
        # Try full date
        return pd.to_datetime(val, errors="coerce")
    
    df_norm["Date"] = df_norm["Date"].apply(parse_date)
    df_norm["Date_Str"] = df_norm["Date"].dt.strftime("%Y-%m-%d")
    
    return df_norm


def _get_crop_year(date: pd.Timestamp, commodity: str) -> int:
    """Get crop year for a given date and commodity."""
    # Soybeans and byproducts: Sep-Aug (start month 9)
    # All others: Aug-Jul (start month 8)
    start_month = 9 if commodity in ["Soybeans", "Soyoil", "Soymeal"] else 8
    if date.month >= start_month:
        return date.year
    else:
        return date.year - 1


def _normalize_commodity_name(name: str) -> str:
    """Normalize commodity names for matching."""
    name = str(name).strip()
    mapping = {
        "All wheat": "Wheat",
        "Wheat, excluding durum": "Wheat",
        "Wheat, all": "Wheat",
        "Durum wheat": "Durum",
        "Wheat, durum": "Durum",       # FIX: Planting data uses "Wheat, durum"
        "Canola (rapeseed)": "Canola",
        "Dry peas": "Dry peas",
        "Chickpeas": "Dry peas",
    }
    return mapping.get(name, name)


def _merge_with_mapping(df: pd.DataFrame, mapping_df: pd.DataFrame) -> pd.DataFrame:
    """Merge with Dictionary_Mapping to get Tier 1 and Tier 2 metrics."""
    map_cols = ["Source Commodity", "StatCan_Raw_Metric", "Tier1_Commodity_Metric", "Tier2_National_Metric"]
    mapping_clean = mapping_df[map_cols].drop_duplicates()
    
    # Normalize Source Commodity in mapping for matching
    mapping_clean["Source_Commodity_Norm"] = mapping_clean["Source Commodity"].apply(_normalize_commodity_name)
    df["Commodity_Norm"] = df["Commodity"].apply(_normalize_commodity_name)
    
    df_merged = df.merge(
        mapping_clean,
        left_on=["Commodity_Norm", "StatCan_Raw_Metric"],
        right_on=["Source_Commodity_Norm", "StatCan_Raw_Metric"],
        how="left",
        indicator=True
    )
    
    unmatched = df_merged[df_merged["_merge"] == "left_only"]
    if len(unmatched) > 0:
        print(f"  WARNING: {len(unmatched)} rows unmatched in Dictionary_Mapping")
        for _, row in unmatched[["Commodity_Norm", "StatCan_Raw_Metric"]].drop_duplicates().iterrows():
            print(f"    {row['Commodity_Norm']} | {row['StatCan_Raw_Metric']}")
    
    df_merged = df_merged.drop(columns=["_merge", "Source Commodity", "Source_Commodity_Norm"])
    
    return df_merged


def _add_metadata(df: pd.DataFrame, folder_key: str) -> pd.DataFrame:
    """Add source metadata columns."""
    df_meta = df.copy()
    df_meta["Source"] = "StatCan_CANSIM"
    df_meta["Data_Type"] = "Historical"
    df_meta["Source_Table"] = folder_key
    return df_meta


def ingest_statcan_folder(folder_key: str, mapping_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Ingest a single StatCan folder with correct scaling and July revision filtering.
    """
    if mapping_df is None:
        mapping_df = load_dictionary_mapping()
    
    print(f"Ingesting {folder_key}...")
    
    # Read raw CSV
    df = _read_cansim_csv(folder_key)
    print(f"  Raw rows: {len(df)}")
    
    # Standardize columns
    df = _standardize_columns(df, folder_key)
    
    # Clean value column
    df = _clean_value_column(df)
    
    # Normalize dates
    df = _normalize_date(df)
    
    # Apply July revision filter for S&D
    df = _filter_july_revision(df, folder_key)
    
    # Apply CORRECT unit scaling
    df = _apply_unit_scaling(df)
    
    # Merge with dictionary mapping
    df = _merge_with_mapping(df, mapping_df)
    
    # Add metadata
    df = _add_metadata(df, folder_key)
    
    # Add crop year
    df["Crop_Year"] = df.apply(lambda r: _get_crop_year(r["Date"], r["Commodity_Norm"]), axis=1)
    
    # Select final columns
    final_cols = [
        "Date", "Date_Str", "Geography", "Commodity", "Commodity_Norm", "StatCan_Raw_Metric",
        "Tier1_Commodity_Metric", "Tier2_National_Metric",
        "Value", "Unit", "Scalar_Factor", "Scaled_Value", "Scaled_Unit",
        "Crop_Year", "Source", "Data_Type", "Source_Table"
    ]
    
    final_cols = [c for c in final_cols if c in df.columns]
    df = df[final_cols]
    
    print(f"  Final rows: {len(df)}")
    return df


def ingest_all_statcan(mapping_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Ingest all 4 StatCan folders and concatenate."""
    if mapping_df is None:
        mapping_df = load_dictionary_mapping()
    
    all_dfs = []
    for folder_key in STATCAN_FOLDERS.keys():
        try:
            df = ingest_statcan_folder(folder_key, mapping_df)
            all_dfs.append(df)
        except Exception as e:
            print(f"  ERROR ingesting {folder_key}: {e}")
            raise
    
    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\nTotal StatCan rows ingested: {len(combined)}")
    return combined


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING StatCan INGESTION (FIXED)")
    print("=" * 60)
    
    try:
        df = ingest_all_statcan()
        print(f"\n[OK] StatCan ingestion successful: {len(df)} total rows")
        print(f"  Columns: {list(df.columns)}")
        print(f"  Date range: {df['Date_Str'].min()} to {df['Date_Str'].max()}")
        print(f"  Unique commodities: {df['Commodity_Norm'].nunique()}")
        print(f"  Unique raw metrics: {df['StatCan_Raw_Metric'].nunique()}")
        print(f"  Tier1 mapped: {df['Tier1_Commodity_Metric'].notna().sum()}")
        print(f"  Tier2 mapped: {df['Tier2_National_Metric'].notna().sum()}")
        print(f"  Scaled units: {df['Scaled_Unit'].unique()}")
        
        # Check Wheat production values
        wheat = df[(df['Commodity_Norm'] == 'Wheat') & (df['Tier1_Commodity_Metric'] == 'Production')]
        print(f"\nWheat Production samples:")
        print(wheat[['Date_Str', 'Crop_Year', 'Scaled_Value', 'Scaled_Unit']].head(10).to_string())
        
    except Exception as e:
        print(f"\n[FAIL] StatCan ingestion failed: {e}")
        raise