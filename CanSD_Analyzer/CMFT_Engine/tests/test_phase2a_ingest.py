"""
Phase 2A: Raw Monthly Data Consolidation for Wheat
Extracts monthly CIMT trade data, StatCan survey stock anchors, and annual production
for crop years 2002-03 through 2024-25. No interpolation yet - raw consolidation only.
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ============================================================
# PATHS
# ============================================================
STATCAN_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs")
CIMTD_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD")
CMFT_MAIN_XLSX = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx")
DATA_STORE_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine\data_store")
OUTPUT_CSV = DATA_STORE_DIR / "phase2a_raw_monthly.csv"

# ============================================================
# CONSTANTS
# ============================================================
WHEAT_SOURCE_COMMODITY_PLANTING = "Wheat, all excluding durum wheat"
WHEAT_SOURCE_COMMODITY_SD = "Wheat, excluding durum"
WHEAT_COMMODITY = "Wheat"
BUSHEL_MULTIPLIER = 36.7437

# Crop year range: 2002-03 through 2024-25
CROP_YEAR_START = 2002
CROP_YEAR_END = 2024

# Month mapping: Crop year months 8-7 (Aug=8, Sep=9, ..., Jul=7)
# Month 8 = Aug, 9 = Sep, 10 = Oct, 11 = Nov, 12 = Dec, 1 = Jan, 2 = Feb, 3 = Mar, 4 = Apr, 5 = May, 6 = Jun, 7 = Jul
MONTH_NAMES = {8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec', 1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun', 7: 'Jul'}

# Survey anchor months
SURVEY_ANCHORS = {12: 'DEC', 3: 'MAR', 7: 'JUL'}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_crop_year(year, month):
    """Determine crop year from calendar year and month. Wheat: Aug-Jul."""
    if month >= 8:
        return year
    else:
        return year - 1

def load_cimt_trade():
    """Load monthly CIMT trade data for Wheat (Ex-Durum)."""
    cimt_path = CIMTD_DIR / "CIMT_Wheat_(Ex-Durum)_20260917.xlsx"
    df = pd.read_excel(cimt_path, sheet_name='Raw Data')
    
    # Clean data
    df = df.dropna(subset=['Year', 'Month', 'TradeType', 'KMT'])
    df['Year'] = df['Year'].astype(int)
    df['Month'] = df['Month'].astype(int)
    df['KMT'] = pd.to_numeric(df['KMT'], errors='coerce')
    
    # Filter for valid months (1-12)
    df = df[(df['Month'] >= 1) & (df['Month'] <= 12)]
    
    # Aggregate by Year, Month, TradeType
    trade_agg = df.groupby(['Year', 'Month', 'TradeType'])['KMT'].sum().reset_index()
    
    # Pivot to get Imports and Exports columns
    trade_pivot = trade_agg.pivot_table(
        index=['Year', 'Month'], 
        columns='TradeType', 
        values='KMT', 
        aggfunc='sum'
    ).reset_index()
    
    trade_pivot.columns.name = None
    
    # Ensure both Import and Export columns exist
    if 'Import' not in trade_pivot.columns:
        trade_pivot['Import'] = 0
    if 'Export' not in trade_pivot.columns:
        trade_pivot['Export'] = 0
    
    trade_pivot = trade_pivot.fillna(0)
    trade_pivot = trade_pivot.rename(columns={'Import': 'Imports_KMT', 'Export': 'Exports_KMT'})
    
    # Add crop year
    trade_pivot['Crop_Year'] = trade_pivot.apply(lambda r: get_crop_year(r['Year'], r['Month']), axis=1)
    
    return trade_pivot

def load_statcan_survey_anchors():
    """Load StatCan survey stock anchors (DEC, MAR, JUL) from S&D data."""
    csv_path = STATCAN_DIR / "S&D_32100013-eng" / "32100013.csv"
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    # Filter for Canada and Wheat, excluding durum
    wheat = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == 'Wheat, excluding durum')
    ].copy()
    
    # Convert VALUE to numeric
    wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
    
    # Parse REF_DATE
    wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
    wheat['Month'] = wheat['REF_DATE'].str[5:7].astype(int)
    
    # Filter for Total ending stocks only
    anchors = wheat[wheat['Supply and disposition of grains'] == 'Total ending stocks'].copy()
    
    # Add crop year
    anchors['Crop_Year'] = anchors.apply(lambda r: get_crop_year(r['Year'], r['Month']), axis=1)
    
    # Keep only survey anchor months (DEC=12, MAR=3, JUL=7)
    anchors = anchors[anchors['Month'].isin([12, 3, 7])].copy()
    
    # Rename VALUE to Stock_KMT
    anchors = anchors.rename(columns={'VALUE': 'Stock_KMT'})
    
    # Add anchor label
    anchors['Anchor'] = anchors['Month'].map(SURVEY_ANCHORS)
    
    return anchors[['Crop_Year', 'Month', 'Anchor', 'Stock_KMT']]

def load_annual_production():
    """Load annual production from planting data and assign to Month 8 (AUG)."""
    csv_path = STATCAN_DIR / "Planting_32100359-eng" / "32100359.csv"
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    # Filter for Canada and Wheat, excluding durum
    wheat = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == 'Wheat, all excluding durum wheat')
    ].copy()
    
    # Convert VALUE to numeric
    wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
    
    # Parse REF_DATE
    wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
    
    # Filter for Production (metric tonnes)
    prod = wheat[wheat['Harvest disposition'] == 'Production (metric tonnes)'].copy()
    
    # Convert to KMT (divide by 1000)
    prod['Production_KMT'] = prod['VALUE'] / 1000.0
    
    # Add crop year (production year = crop year for wheat)
    prod['Crop_Year'] = prod['Year']
    
    # Assign to Month 8 (AUG)
    prod['Month'] = 8
    
    return prod[['Crop_Year', 'Month', 'Production_KMT']]

def load_annual_area():
    """Load annual planted and harvested area from planting data."""
    csv_path = STATCAN_DIR / "Planting_32100359-eng" / "32100359.csv"
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    # Filter for Canada and Wheat, excluding durum
    wheat = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == 'Wheat, all excluding durum wheat')
    ].copy()
    
    # Convert VALUE to numeric
    wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
    
    # Parse REF_DATE
    wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
    
    # Planted area (acres) -> MLN AC
    planted = wheat[wheat['Harvest disposition'] == 'Seeded area (acres)'].copy()
    planted['Planted_Area_MLN_AC'] = planted['VALUE'] / 1_000_000.0
    planted['Crop_Year'] = planted['Year']
    
    # Harvested area (acres) -> MLN AC
    harvested = wheat[wheat['Harvest disposition'] == 'Harvested area (acres)'].copy()
    harvested['Harvested_Area_MLN_AC'] = harvested['VALUE'] / 1_000_000.0
    harvested['Crop_Year'] = harvested['Year']
    
    return (
        planted[['Crop_Year', 'Planted_Area_MLN_AC']],
        harvested[['Crop_Year', 'Harvested_Area_MLN_AC']]
    )

def main():
    print("=" * 80)
    print("PHASE 2A: Raw Monthly Data Consolidation for Wheat")
    print("=" * 80)
    
    # Load all data
    print("\n[1/5] Loading CIMT monthly trade data...")
    trade_df = load_cimt_trade()
    print(f"    Loaded {len(trade_df)} monthly trade records")
    print(f"    Crop years: {trade_df['Crop_Year'].min()}-{trade_df['Crop_Year'].max()}")
    
    print("\n[2/5] Loading StatCan survey stock anchors...")
    anchors_df = load_statcan_survey_anchors()
    print(f"    Loaded {len(anchors_df)} survey anchor records")
    print(f"    Crop years: {anchors_df['Crop_Year'].min()}-{anchors_df['Crop_Year'].max()}")
    
    print("\n[3/5] Loading annual production...")
    prod_df = load_annual_production()
    print(f"    Loaded {len(prod_df)} annual production records")
    
    print("\n[4/5] Loading annual area...")
    planted_df, harvested_df = load_annual_area()
    print(f"    Loaded {len(planted_df)} planted area records")
    print(f"    Loaded {len(harvested_df)} harvested area records")
    
    # Build master monthly table
    print("\n[5/5] Building consolidated monthly table...")
    
    # Create base grid: all crop years x 12 months
    crop_years = list(range(CROP_YEAR_START, CROP_YEAR_END + 1))
    months = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
    
    rows = []
    for cy in crop_years:
        for m in months:
            row = {
                'Crop_Year': cy,
                'Crop_Year_Label': f"{cy}-{str(cy+1)[2:]}",
                'Month': m,
                'Month_Name': MONTH_NAMES[m],
            }
            rows.append(row)
    
    master_df = pd.DataFrame(rows)
    
    # Merge trade data
    trade_df = trade_df.rename(columns={'Year': 'Cal_Year'})
    master_df = master_df.merge(
        trade_df[['Crop_Year', 'Month', 'Imports_KMT', 'Exports_KMT']],
        on=['Crop_Year', 'Month'],
        how='left'
    )
    
    # Merge survey anchors
    master_df = master_df.merge(
        anchors_df[['Crop_Year', 'Month', 'Anchor', 'Stock_KMT']],
        on=['Crop_Year', 'Month'],
        how='left'
    )
    
    # Merge production (only Month 8)
    master_df = master_df.merge(
        prod_df[['Crop_Year', 'Month', 'Production_KMT']],
        on=['Crop_Year', 'Month'],
        how='left'
    )
    
    # Merge area data
    master_df = master_df.merge(planted_df, on='Crop_Year', how='left')
    master_df = master_df.merge(harvested_df, on='Crop_Year', how='left')
    
    # Fill NaN trade with 0
    master_df['Imports_KMT'] = master_df['Imports_KMT'].fillna(0)
    master_df['Exports_KMT'] = master_df['Exports_KMT'].fillna(0)
    
    # Sort
    master_df = master_df.sort_values(['Crop_Year', 'Month']).reset_index(drop=True)
    
    # Save
    DATA_STORE_DIR.mkdir(parents=True, exist_ok=True)
    master_df.to_csv(OUTPUT_CSV, index=False)
    print(f"    Saved {len(master_df)} rows to {OUTPUT_CSV}")
    
    # Print summary for 3 sample crop years
    print("\n" + "=" * 120)
    print("PHASE 2A RESULTS: Wheat Raw Monthly Consolidation (Sample Crop Years)")
    print("=" * 120)
    
    for cy in [2002, 2012, 2024]:
        cy_df = master_df[master_df['Crop_Year'] == cy].copy()
        if len(cy_df) == 0:
            continue
            
        print(f"\nCrop Year {cy}-{str(cy+1)[2:]}:")
        print(f"{'Month':<6} {'Name':<4} {'Imports':>10} {'Exports':>10} {'Production':>12} {'Stock_KMT':>12} {'Anchor':<6}")
        print("-" * 70)
        
        for _, row in cy_df.iterrows():
            anchor_str = row['Anchor'] if pd.notna(row['Anchor']) else ''
            stock_str = f"{row['Stock_KMT']:,.1f}" if pd.notna(row['Stock_KMT']) else ''
            prod_str = f"{row['Production_KMT']:,.1f}" if pd.notna(row['Production_KMT']) else ''
            print(f"{row['Month']:<6} {row['Month_Name']:<4} {row['Imports_KMT']:>10,.1f} {row['Exports_KMT']:>10,.1f} {prod_str:>12} {stock_str:>12} {anchor_str:<6}")
        
        # Annual totals
        total_imports = cy_df['Imports_KMT'].sum()
        total_exports = cy_df['Exports_KMT'].sum()
        total_prod = cy_df['Production_KMT'].sum()
        planted = cy_df['Planted_Area_MLN_AC'].iloc[0] if 'Planted_Area_MLN_AC' in cy_df.columns else np.nan
        harvested = cy_df['Harvested_Area_MLN_AC'].iloc[0] if 'Harvested_Area_MLN_AC' in cy_df.columns else np.nan
        
        print(f"\n  Annual Totals: Planted={planted:.3f} MLN AC, Harvested={harvested:.3f} MLN AC, Prod={total_prod:,.1f} KMT, Imports={total_imports:,.1f} KMT, Exports={total_exports:,.1f} KMT")
        
        # Survey anchors
        anchors = cy_df[cy_df['Anchor'].notna()]
        for _, a in anchors.iterrows():
            print(f"  {a['Anchor']} Anchor (Month {a['Month']}): {a['Stock_KMT']:,.1f} KMT")
    
    print("\n" + "=" * 120)
    print("Phase 2A Complete - Raw monthly data consolidated")
    print("=" * 120)
    
    return master_df

if __name__ == "__main__":
    main()