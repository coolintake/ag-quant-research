"""
Analyst Forecast Overrides Ingestion Module
Loads forecast overrides from CMFT_Main.xlsx (Forecast_Overrides or Overrides sheet).
If sheet doesn't exist, creates a template structure for manual entry.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, List

from config.paths import CMFT_MAIN_XLSX


# Expected columns in the overrides sheet
OVERRIDE_COLUMNS = [
    "Date",           # YYYY-MM-DD (first of month for monthly, or annual YYYY-01-01)
    "Commodity",      # Must match Tier1_Commodity_Metric or Tier2_National_Metric
    "Metric_Type",    # 'Tier1' or 'Tier2' - which metric tier this applies to
    "Metric_Name",    # The specific Tier1_Commodity_Metric or Tier2_National_Metric name
    "Value",          # Numeric override value
    "Unit",           # 'MLN AC', 'KMT', 'MT/AC', '%', etc.
    "Source_Note",    # Analyst name, model reference, report date
    "Confidence",     # 'High', 'Medium', 'Low'
    "Effective_From", # When this override takes effect (YYYY-MM-DD)
    "Effective_To",   # When this override expires (YYYY-MM-DD or blank for indefinite)
    "Status",         # 'Active', 'Superseded', 'Draft'
]

# Alternative sheet names
OVERRIDE_SHEET_NAMES = [
    "Forecast_Overrides",
    "Overrides",
    "Analyst_Overrides",
    "Forecast_Override",
]


def _find_overrides_sheet(xlsx_path: Path) -> Optional[str]:
    """Find the overrides sheet in the workbook."""
    if not xlsx_path.exists():
        return None
    
    xls = pd.ExcelFile(xlsx_path, engine="openpyxl")
    for sheet_name in OVERRIDE_SHEET_NAMES:
        if sheet_name in xls.sheet_names:
            return sheet_name
    return None


def _create_overrides_template() -> pd.DataFrame:
    """Create an empty template DataFrame with the correct structure."""
    return pd.DataFrame(columns=OVERRIDE_COLUMNS)


def _validate_overrides_df(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and clean the overrides dataframe."""
    df_clean = df.copy()
    
    # Ensure all required columns exist
    for col in OVERRIDE_COLUMNS:
        if col not in df_clean.columns:
            df_clean[col] = pd.NA
    
    # Reorder columns
    df_clean = df_clean[OVERRIDE_COLUMNS]
    
    # Clean text columns
    text_cols = ["Commodity", "Metric_Type", "Metric_Name", "Unit", "Source_Note", "Confidence", "Status"]
    for col in text_cols:
        if col in df_clean.columns:
            df_clean[col] = df_clean[col].astype(str).str.strip().replace("nan", pd.NA)
    
    # Parse dates
    date_cols = ["Date", "Effective_From", "Effective_To"]
    for col in date_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_datetime(df_clean[col], errors="coerce")
            df_clean[f"{col}_Str"] = df_clean[col].dt.strftime("%Y-%m-%d")
    
    # Ensure numeric Value
    df_clean["Value"] = pd.to_numeric(df_clean["Value"], errors="coerce")
    
    # Default Status to 'Active' if blank
    df_clean["Status"] = df_clean["Status"].fillna("Active")
    
    # Default Confidence to 'Medium' if blank
    df_clean["Confidence"] = df_clean["Confidence"].fillna("Medium")
    
    # Default Metric_Type to 'Tier1' if blank
    df_clean["Metric_Type"] = df_clean["Metric_Type"].fillna("Tier1")
    
    return df_clean


def _add_overrides_metadata(df: pd.DataFrame) -> pd.DataFrame:
    """Add source metadata columns."""
    df_meta = df.copy()
    df_meta["Source"] = "Analyst_Override"
    df_meta["Data_Type"] = "Forecast"
    return df_meta


def load_overrides() -> pd.DataFrame:
    """
    Load forecast overrides from CMFT_Main.xlsx.
    
    Returns:
        DataFrame with override records (empty template if sheet doesn't exist)
    """
    sheet_name = _find_overrides_sheet(CMFT_MAIN_XLSX)
    
    if sheet_name is None:
        print(f"  No overrides sheet found in {CMFT_MAIN_XLSX.name}")
        print(f"  Expected one of: {OVERRIDE_SHEET_NAMES}")
        print(f"  Available sheets: {pd.ExcelFile(CMFT_MAIN_XLSX, engine='openpyxl').sheet_names}")
        print("  Returning empty template...")
        return _create_overrides_template()
    
    print(f"  Loading overrides from sheet: {sheet_name}")
    df = pd.read_excel(CMFT_MAIN_XLSX, sheet_name=sheet_name, engine="openpyxl", dtype=str)
    
    df = _validate_overrides_df(df)
    df = _add_overrides_metadata(df)
    
    print(f"  Loaded {len(df)} override records")
    return df


def save_overrides(df: pd.DataFrame, sheet_name: str = "Forecast_Overrides") -> None:
    """
    Save overrides back to CMFT_Main.xlsx.
    WARNING: This modifies the source workbook. Use with caution.
    
    Args:
        df: Overrides DataFrame
        sheet_name: Sheet name to write to
    """
    # Read all existing sheets
    xls = pd.ExcelFile(CMFT_MAIN_XLSX, engine="openpyxl")
    sheets_dict = {}
    for s in xls.sheet_names:
        if s != sheet_name:
            sheets_dict[s] = pd.read_excel(CMFT_MAIN_XLSX, sheet_name=s, engine="openpyxl")
    
    # Prepare overrides for writing (drop metadata columns)
    write_cols = [c for c in OVERRIDE_COLUMNS if c in df.columns]
    df_write = df[write_cols].copy()
    
    # Convert dates back to string for Excel
    for col in ["Date", "Effective_From", "Effective_To"]:
        if col in df_write.columns:
            df_write[col] = df_write[col].dt.strftime("%Y-%m-%d") if df_write[col].dtype == "datetime64[ns]" else df_write[col]
    
    sheets_dict[sheet_name] = df_write
    
    # Write all sheets back
    with pd.ExcelWriter(CMFT_MAIN_XLSX, engine="openpyxl", mode="w") as writer:
        for s, data in sheets_dict.items():
            data.to_excel(writer, sheet_name=s, index=False)
    
    print(f"  Saved {len(df_write)} overrides to {sheet_name}")


def ingest_overrides() -> pd.DataFrame:
    """
    Main ingestion function for forecast overrides.
    
    Returns:
        Standardized DataFrame with override records
    """
    print("Ingesting Analyst Forecast Overrides...")
    df = load_overrides()
    
    if len(df) == 0:
        print("  No overrides found (empty template returned)")
        return df
    
    # Select final columns
    final_cols = [
        "Date", "Date_Str", "Commodity", "Metric_Type", "Metric_Name",
        "Value", "Unit", "Source_Note", "Confidence",
        "Effective_From", "Effective_From_Str", "Effective_To", "Effective_To_Str",
        "Status", "Source", "Data_Type"
    ]
    
    final_cols = [c for c in final_cols if c in df.columns]
    df = df[final_cols]
    
    print(f"  Final override rows: {len(df)}")
    return df


if __name__ == "__main__":
    # Quick test run
    print("=" * 60)
    print("TESTING OVERRIDES INGESTION")
    print("=" * 60)
    
    try:
        df = ingest_overrides()
        print(f"\n[OK] Overrides ingestion complete: {len(df)} rows")
        print(f"  Columns: {list(df.columns)}")
        if len(df) > 0:
            print(f"  Date range: {df['Date_Str'].min()} to {df['Date_Str'].max()}")
            print(f"  Commodities: {df['Commodity'].unique()}")
            print(f"  Metric types: {df['Metric_Type'].unique()}")
            print(f"  Statuses: {df['Status'].unique()}")
        else:
            print("  (Empty template - no overrides sheet found in CMFT_Main.xlsx)")
    except Exception as e:
        print(f"\n[FAIL] Overrides ingestion failed: {e}")
        raise