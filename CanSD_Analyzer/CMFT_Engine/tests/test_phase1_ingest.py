"""
Phase 1: Raw Data Extraction & Mapping Verification for Wheat
Extracts Wheat (excluding durum) metrics from StatCan CANSIM tables,
applies Mapping_Dictionary rules, verifies unit scaling, calculates yield,
and saves flat table to data_store/CMFT_Master.csv

Crop Year Formula: Crop_Year = f"{REF_DATE % 100:02d}-{(REF_DATE + 1) % 100:02d}"
Example: REF_DATE=2002 -> "02-03", REF_DATE=2003 -> "03-04", REF_DATE=2025 -> "25-26"
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ============================================================
# PATHS
# ============================================================
STATCAN_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs")
CMFT_MAIN_XLSX = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx")
DATA_STORE_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine\data_store")
MASTER_CSV = DATA_STORE_DIR / "CMFT_Master.csv"

# ============================================================
# CONSTANTS
# ============================================================
WHEAT_SOURCE_COMMODITY_PLANTING = "Wheat, all excluding durum wheat"
WHEAT_SOURCE_COMMODITY_SD = "Wheat, excluding durum"
WHEAT_COMMODITY = "Wheat"
BUSHEL_MULTIPLIER = 36.7437  # Tonnes -> Bushels for Wheat

# Crop year range: 02-03 through 24-25 (Aug 2002 to Jul 2025)
CROP_YEAR_START = 2002
CROP_YEAR_END = 2024

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def load_mapping_dictionary():
    """Load and filter mapping dictionary for Wheat."""
    df = pd.read_excel(CMFT_MAIN_XLSX, sheet_name='Mapping_Dictionary', engine='openpyxl')
    wheat_map = df[df['Source Commodity'] == WHEAT_SOURCE_COMMODITY_SD].copy()
    return wheat_map

def load_planting_data():
    """Load planting data (Table 32100359) for Wheat."""
    csv_path = STATCAN_DIR / "Planting_32100359-eng" / "32100359.csv"
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    # Filter for Canada and Wheat, excluding durum
    df = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == WHEAT_SOURCE_COMMODITY_PLANTING)
    ].copy()
    
    # Convert VALUE to numeric
    df['VALUE'] = pd.to_numeric(df['VALUE'], errors='coerce')
    
    # Parse REF_DATE to year
    df['Year'] = df['REF_DATE'].str[:4].astype(int)
    
    return df

def load_sd_data():
    """Load supply & disposition data (Table 32100013) for Wheat."""
    csv_path = STATCAN_DIR / "S&D_32100013-eng" / "32100013.csv"
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    # Filter for Canada and Wheat, excluding durum
    df = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == WHEAT_SOURCE_COMMODITY_SD)
    ].copy()
    
    # Convert VALUE to numeric
    df['VALUE'] = pd.to_numeric(df['VALUE'], errors='coerce')
    
    # Parse REF_DATE to year-month
    df['Year'] = df['REF_DATE'].str[:4].astype(int)
    df['Month'] = df['REF_DATE'].str[5:7].astype(int)
    
    return df

def get_crop_year_from_ref_date(ref_date):
    """
    Determine crop year from REF_DATE using CANSIM convention.
    Crop_Year = f"{REF_DATE % 100:02d}-{(REF_DATE + 1) % 100:02d}"
    Example: REF_DATE=2002 -> "02-03", REF_DATE=2003 -> "03-04", REF_DATE=2025 -> "25-26"
    """
    year = int(ref_date)
    return f"{year % 100:02d}-{(year + 1) % 100:02d}"

def get_crop_year_from_year_month(year, month, commodity='Wheat'):
    """Determine crop year from year and month. Wheat: Aug-Jul."""
    # Wheat crop year: Aug-Jul. If month >= 8, it's the current year crop.
    # If month < 8, it's the previous year crop.
    if month >= 8:
        return year
    else:
        return year - 1

def extract_planting_metrics(planting_df):
    """Extract seeded area, harvested area, yield, production from planting data."""
    metrics = {}
    
    # Seeded area (acres) -> MLN AC
    seeded = planting_df[planting_df['Harvest disposition'] == 'Seeded area (acres)']
    for _, row in seeded.iterrows():
        cy = get_crop_year_from_year_month(row['Year'], 8)  # Planting is annual, use Aug as reference
        if CROP_YEAR_START <= cy <= CROP_YEAR_END:
            metrics.setdefault(cy, {})['Seeded_Area_Acres'] = row['VALUE']
    
    # Harvested area (acres)
    harvested = planting_df[planting_df['Harvest disposition'] == 'Harvested area (acres)']
    for _, row in harvested.iterrows():
        cy = get_crop_year_from_year_month(row['Year'], 8)
        if CROP_YEAR_START <= cy <= CROP_YEAR_END:
            metrics.setdefault(cy, {})['Harvested_Area_Acres'] = row['VALUE']
    
    # Average yield (bushels per acre) - StatCan reported
    yield_reported = planting_df[planting_df['Harvest disposition'] == 'Average yield (bushels per acre)']
    for _, row in yield_reported.iterrows():
        cy = get_crop_year_from_year_month(row['Year'], 8)
        if CROP_YEAR_START <= cy <= CROP_YEAR_END:
            metrics.setdefault(cy, {})['StatCan_Yield_BU_AC'] = row['VALUE']
    
    # Production (metric tonnes) -> KMT (divide by 1000)
    prod = planting_df[planting_df['Harvest disposition'] == 'Production (metric tonnes)']
    for _, row in prod.iterrows():
        cy = get_crop_year_from_year_month(row['Year'], 8)
        if CROP_YEAR_START <= cy <= CROP_YEAR_END:
            metrics.setdefault(cy, {})['Production_Tonnes'] = row['VALUE'] / 1000.0
    
    return metrics

def extract_sd_metrics(sd_df):
    """Extract S&D metrics from supply & disposition data."""
    metrics = {}
    
    # Map StatCan metrics to our names
    metric_map = {
        'Total beginning stocks': 'Beginning_Stocks',
        'Production': 'Production_SD',
        'Imports': 'Imports',
        'Human food': 'Food',
        'Industrial use': 'Industrial',
        'Seed requirements': 'Seed_Req',
        'Loss in handling': 'Loss_Handling',
        'Animal feed, waste and dockage': 'Feed_Waste_Dockage',
        'Total exports': 'Exports',
        'Total ending stocks': 'Ending_Stocks',
    }
    
    for statcan_metric, our_metric in metric_map.items():
        subset = sd_df[sd_df['Supply and disposition of grains'] == statcan_metric]
        for _, row in subset.iterrows():
            cy = get_crop_year_from_year_month(row['Year'], row['Month'])
            if CROP_YEAR_START <= cy <= CROP_YEAR_END:
                # S&D data: SCALAR_FACTOR = "thousands", UOM = "Metric tonnes"
                # So VALUE is already in KMT (thousands of tonnes)
                metrics.setdefault(cy, {})[our_metric] = row['VALUE']
    
    return metrics

def compute_yield(production_tonnes, harvested_acres):
    """Calculate yield in BU/AC from production (KMT) and harvested area (acres)."""
    if pd.isna(production_tonnes) or pd.isna(harvested_acres) or harvested_acres == 0:
        return np.nan
    # production_tonnes is in KMT (thousands of tonnes)
    # harvested_acres is in acres
    # Yield (BU/AC) = (Production_KMT * 1000 * 36.7437) / Harvested_Acres
    return (production_tonnes * 1000.0 * BUSHEL_MULTIPLIER) / harvested_acres

def main():
    print("=" * 80)
    print("PHASE 1: Wheat Raw Data Extraction & Mapping Verification")
    print("=" * 80)
    
    # Load mapping dictionary
    print("\n[1/5] Loading Mapping Dictionary...")
    wheat_map = load_mapping_dictionary()
    print(f"    Found {len(wheat_map)} mappings for '{WHEAT_SOURCE_COMMODITY_SD}'")
    print(f"    Tier1 Metrics: {wheat_map['Tier1_Commodity_Metric'].tolist()}")
    
    # Load raw data
    print("\n[2/5] Loading Planting Data (32100359)...")
    planting_df = load_planting_data()
    print(f"    Loaded {len(planting_df)} rows for Canada, {WHEAT_SOURCE_COMMODITY_PLANTING}")
    
    print("\n[3/5] Loading Supply & Disposition Data (32100013)...")
    sd_df = load_sd_data()
    print(f"    Loaded {len(sd_df)} rows for Canada, {WHEAT_SOURCE_COMMODITY_SD}")
    
    # Extract metrics
    print("\n[4/5] Extracting Metrics...")
    planting_metrics = extract_planting_metrics(planting_df)
    sd_metrics = extract_sd_metrics(sd_df)
    
    # Combine and compute
    print("\n[5/5] Computing Yield & Building Master Table...")
    rows = []
    
    for cy in range(CROP_YEAR_START, CROP_YEAR_END + 1):
        pm = planting_metrics.get(cy, {})
        sm = sd_metrics.get(cy, {})
        
        # Get values
        seeded_acres = pm.get('Seeded_Area_Acres', np.nan)
        harvested_acres = pm.get('Harvested_Area_Acres', np.nan)
        statcan_yield = pm.get('StatCan_Yield_BU_AC', np.nan)
        prod_tonnes = pm.get('Production_Tonnes', np.nan)
        
        # Production from S&D (should match)
        prod_sd = sm.get('Production_SD', np.nan)
        
        # Use planting production for yield calc (more direct)
        production_tonnes = prod_tonnes if not pd.isna(prod_tonnes) else prod_sd
        
        # Calculate yield
        calc_yield = compute_yield(production_tonnes, harvested_acres)
        
        # July ending stocks (end of crop year for wheat)
        july_ending_stocks = sm.get('Ending_Stocks', np.nan)
        
        # Verification: compare calculated vs StatCan reported yield
        if not pd.isna(calc_yield) and not pd.isna(statcan_yield):
            diff = abs(calc_yield - statcan_yield)
            status = "PASS" if diff < 0.5 else "FAIL"  # Allow 0.5 BU/AC tolerance
        else:
            status = "N/A"
        
        row = {
            'Crop_Year': f"{cy}-{str(cy+1)[2:]}",
            'Planted_Area_MLN_AC': round(seeded_acres / 1_000_000, 3) if not pd.isna(seeded_acres) else np.nan,
            'Harvested_Area_MLN_AC': round(harvested_acres / 1_000_000, 3) if not pd.isna(harvested_acres) else np.nan,
            'Production_KMT': round(production_tonnes, 1) if not pd.isna(production_tonnes) else np.nan,
            'Calculated_Yield_BU_AC': round(calc_yield, 1) if not pd.isna(calc_yield) else np.nan,
            'StatCan_Reported_Yield_BU_AC': round(statcan_yield, 1) if not pd.isna(statcan_yield) else np.nan,
            'July_Ending_Stocks_KMT': round(july_ending_stocks, 1) if not pd.isna(july_ending_stocks) else np.nan,
            'Yield_Verification': status,
        }
        rows.append(row)
    
    master_df = pd.DataFrame(rows)
    
    # Save to CSV
    DATA_STORE_DIR.mkdir(parents=True, exist_ok=True)
    master_df.to_csv(MASTER_CSV, index=False)
    print(f"    Saved {len(master_df)} rows to {MASTER_CSV}")
    
    # Print summary table
    print("\n" + "=" * 120)
    print("PHASE 1 RESULTS: Wheat (excluding durum) - Crop Years 02-03 through 24-25")
    print("=" * 120)
    print(f"{'Crop Year':<10} {'Planted (MLN AC)':>16} {'Harvested (MLN AC)':>18} {'Prod (KMT)':>12} {'Calc Yield':>12} {'StatCan Yield':>14} {'Jul End Stks (KMT)':>18} {'Verify':>8}")
    print("-" * 120)
    
    for _, row in master_df.iterrows():
        print(f"{row['Crop_Year']:<10} {row['Planted_Area_MLN_AC']:>16.3f} {row['Harvested_Area_MLN_AC']:>18.3f} "
              f"{row['Production_KMT']:>12.1f} {row['Calculated_Yield_BU_AC']:>12.1f} {row['StatCan_Reported_Yield_BU_AC']:>14.1f} "
              f"{row['July_Ending_Stocks_KMT']:>18.1f} {row['Yield_Verification']:>8}")
    
    print("=" * 120)
    
    # Summary stats
    pass_count = (master_df['Yield_Verification'] == 'PASS').sum()
    total_count = len(master_df[master_df['Yield_Verification'] != 'N/A'])
    print(f"\nYield Verification: {pass_count}/{total_count} PASS")
    
    return master_df

if __name__ == "__main__":
    main()