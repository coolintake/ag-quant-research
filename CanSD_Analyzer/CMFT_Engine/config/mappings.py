"""
Dynamic configuration loader for CMFT Engine.
Parses the Dictionary_Mapping sheet from CMFT_Main.xlsx to build
commodity-to-metric mappings for downstream ingestion scripts.
"""

import pandas as pd
from functools import lru_cache
from .paths import CMFT_MAIN_XLSX


# Expected column names in the Dictionary_Mapping sheet
REQUIRED_COLUMNS = [
    "Source Commodity",
    "StatCan_Raw_Metric",
    "Tier1_Commodity_Metric",
    "Tier2_National_Metric",
]

# Alternative sheet names (observed variants in workbook)
ALTERNATIVE_SHEET_NAMES = [
    "Dictionary_Mapping",
    "Dicitonary_Mapping",  # Common typo in workbook
    "Mapping_Dictionary",  # Actual sheet name in CMFT_Main.xlsx
]


def _find_mapping_sheet(xlsx_path: str) -> str:
    """
    Detect the correct sheet name for Dictionary_Mapping.
    Tries exact match first, then falls back to known typo variant.
    """
    xls = pd.ExcelFile(xlsx_path, engine="openpyxl")
    available_sheets = xls.sheet_names
    
    for sheet_name in ALTERNATIVE_SHEET_NAMES:
        if sheet_name in available_sheets:
            return sheet_name
    
    raise ValueError(
        f"Dictionary_Mapping sheet not found in {xlsx_path}. "
        f"Available sheets: {available_sheets}. "
        f"Expected one of: {ALTERNATIVE_SHEET_NAMES}"
    )


def _clean_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Strip leading/trailing whitespace from all text columns to prevent join failures.
    """
    df_clean = df.copy()
    text_columns = df_clean.select_dtypes(include=["object"]).columns
    
    for col in text_columns:
        df_clean[col] = df_clean[col].astype(str).str.strip()
    
    # Replace 'nan' strings from NaN conversion back to actual NaN
    df_clean = df_clean.replace("nan", pd.NA)
    
    return df_clean


def _validate_columns(df: pd.DataFrame) -> None:
    """
    Ensure all required columns are present in the mapping dataframe.
    """
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns in Dictionary_Mapping: {missing}. "
            f"Available columns: {list(df.columns)}"
        )


@lru_cache(maxsize=1)
def load_dictionary_mapping() -> pd.DataFrame:
    """
    Load and cache the Dictionary_Mapping sheet from CMFT_Main.xlsx.
    
    Returns:
        pd.DataFrame: Cleaned mapping dataframe with columns:
            - Source Commodity
            - StatCan_Raw_Metric
            - Tier1_Commodity_Metric
            - Tier2_National_Metric
            (plus any additional columns present in the sheet)
    
    Raises:
        FileNotFoundError: If CMFT_Main.xlsx does not exist.
        ValueError: If Dictionary_Mapping sheet or required columns are missing.
    """
    if not CMFT_MAIN_XLSX.exists():
        raise FileNotFoundError(f"CMFT_Main.xlsx not found at: {CMFT_MAIN_XLSX}")
    
    sheet_name = _find_mapping_sheet(str(CMFT_MAIN_XLSX))
    
    df = pd.read_excel(
        CMFT_MAIN_XLSX,
        sheet_name=sheet_name,
        engine="openpyxl",
        dtype=str,  # Read all as string to preserve leading zeros, then clean
    )
    
    _validate_columns(df)
    df = _clean_text_columns(df)
    
    return df


def get_mapping_stats() -> dict:
    """
    Get summary statistics about the loaded mapping.
    Useful for verification and logging.
    """
    df = load_dictionary_mapping()
    
    return {
        "total_rows": len(df),
        "unique_source_commodities": df["Source Commodity"].nunique(),
        "unique_statcan_metrics": df["StatCan_Raw_Metric"].nunique(),
        "unique_tier1_metrics": df["Tier1_Commodity_Metric"].nunique(),
        "unique_tier2_metrics": df["Tier2_National_Metric"].nunique(),
        "columns": list(df.columns),
        "sheet_source": _find_mapping_sheet(str(CMFT_MAIN_XLSX)),
    }


if __name__ == "__main__":
    # Quick test run
    try:
        stats = get_mapping_stats()
        print("Dictionary Mapping Loaded Successfully")
        print("=" * 50)
        for key, value in stats.items():
            print(f"  {key}: {value}")
    except Exception as e:
        print(f"ERROR: {e}")
        raise