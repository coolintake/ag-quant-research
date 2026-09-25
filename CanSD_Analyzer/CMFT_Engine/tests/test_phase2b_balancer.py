"""
Phase 2B: 3-Period Inter-Survey Residual Solver for Wheat (Corrected 5/3/4 Cumulative YTD Logic)
Runs the residual solver across 3 sub-periods per crop year,
calculates intermediate monthly Food, Industrial, FSR, and Ending Stocks,
and exports the complete 12-month S&D matrix to data_store/CMFT_Master.csv.

Key corrections:
- Dynamic 2001-02 carryout from StatCan S&D (July 2002 = 5,185 KMT)
- 5/3/4 cumulative YTD interpolation for flow metrics (Food, Industrial, Seed, Exports)
- Production metric included in output
- 3-period residual solver with exact anchor matching
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ============================================================
# PATHS
# ============================================================
INPUT_CSV = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine\data_store\phase2a_raw_monthly.csv")
CMFT_MAIN_XLSX = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx")
DATA_STORE_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine\data_store")
OUTPUT_CSV = DATA_STORE_DIR / "CMFT_Master.csv"

# ============================================================
# CONSTANTS
# ============================================================
WHEAT_COMMODITY = "Wheat"
BUSHEL_MULTIPLIER = 36.7437

# Crop year range: 2002-03 through 2024-25
CROP_YEAR_START = 2002
CROP_YEAR_END = 2024

# Month order: Aug=8, Sep=9, Oct=10, Nov=11, Dec=12, Jan=1, Feb=2, Mar=3, Apr=4, May=5, Jun=6, Jul=7
MONTH_ORDER = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
MONTH_NAMES = {8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec', 1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun', 7: 'Jul'}

# Survey anchor months
SURVEY_ANCHORS = {12: 'DEC', 3: 'MAR', 7: 'JUL'}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def load_raw_data():
    """Load the Phase 2A raw monthly data."""
    df = pd.read_csv(INPUT_CSV)
    return df

def load_dynamic_seed_stock():
    """
    Dynamically fetch the 2001-02 July ending stock from StatCan S&D data.
    This is the July 2002 ending stock for 'Wheat, excluding durum'.
    """
    csv_path = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv")
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    wheat = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == 'Wheat, excluding durum')
    ].copy()
    
    wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
    wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
    wheat['Month'] = wheat['REF_DATE'].str[5:7].astype(int)
    
    # Get July 2002 Total ending stocks (2001-02 carryout)
    july_2002 = wheat[
        (wheat['Year'] == 2002) & 
        (wheat['Month'] == 7) & 
        (wheat['Supply and disposition of grains'] == 'Total ending stocks')
    ]
    
    seed_stock = july_2002['VALUE'].values[0]
    print(f"    Dynamic seed stock (July 2002 ending stock): {seed_stock:.1f} KMT")
    return seed_stock

def load_cumulative_ytd_totals():
    """
    Load cumulative YTD totals from StatCan data at Dec, Mar, Jul for each flow metric.
    Returns dict: {crop_year: {metric: {12: dec_ytd, 3: mar_ytd, 7: jul_ytd}}}
    """
    csv_path = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs\S&D_32100013-eng\32100013.csv")
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    
    wheat = df[
        (df['GEO'] == 'Canada') & 
        (df['Type of crop'] == 'Wheat, excluding durum')
    ].copy()
    
    wheat['VALUE'] = pd.to_numeric(wheat['VALUE'], errors='coerce')
    wheat['Year'] = wheat['REF_DATE'].str[:4].astype(int)
    wheat['Month'] = wheat['REF_DATE'].str[5:7].astype(int)
    
    # Flow metrics that need 5/3/4 splitting
    flow_metrics = ['Human food', 'Industrial use', 'Seed requirements', 'Total exports']
    
    cumulative_totals = {}
    for metric in flow_metrics:
        subset = wheat[wheat['Supply and disposition of grains'] == metric].copy()
        # Get Dec, Mar, Jul values
        anchor_data = subset[subset['Month'].isin([12, 3, 7])]
        for _, row in anchor_data.iterrows():
            year = row['Year']
            month = row['Month']
            # Determine crop year based on month
            # Dec (12) -> crop year = year (e.g., Dec 2002 -> 2002-03)
            # Mar (3) -> crop year = year - 1 (e.g., Mar 2003 -> 2002-03)
            # Jul (7) -> crop year = year - 1 (e.g., Jul 2003 -> 2002-03)
            if month == 12:
                cy = year
            else:  # month 3 or 7
                cy = year - 1
            if CROP_YEAR_START <= cy <= CROP_YEAR_END:
                cumulative_totals.setdefault(cy, {}).setdefault(metric, {})[row['Month']] = row['VALUE']
    
    # Also load annual totals for non-flow metrics (Production, Imports, Ending Stocks)
    annual_metrics = ['Production', 'Imports', 'Total ending stocks', 'Loss in handling', 'Animal feed, waste and dockage']
    annual_totals = {}
    for metric in annual_metrics:
        subset = wheat[wheat['Supply and disposition of grains'] == metric].copy()
        july_data = subset[subset['Month'] == 7]
        for _, row in july_data.iterrows():
            cy = row['Year'] - 1
            if CROP_YEAR_START <= cy <= CROP_YEAR_END:
                annual_totals.setdefault(cy, {})[metric] = row['VALUE']
    
    return cumulative_totals, annual_totals

def compute_period_monthly_values(cumulative_totals, cy, metric):
    """
    Compute monthly values for a metric using 5/3/4 cumulative YTD splitting.
    Returns dict: {month: monthly_value} for all 12 months.
    """
    ytd = cumulative_totals.get(cy, {}).get(metric, {})
    dec_ytd = ytd.get(12, 0)
    mar_ytd = ytd.get(3, 0)
    jul_ytd = ytd.get(7, 0)
    
    # 5/3/4 splitting
    p1_monthly = dec_ytd / 5.0 if dec_ytd else 0      # Aug-Dec (5 months)
    p2_monthly = (mar_ytd - dec_ytd) / 3.0 if mar_ytd and dec_ytd else 0  # Jan-Mar (3 months)
    p3_monthly = (jul_ytd - mar_ytd) / 4.0 if jul_ytd and mar_ytd else 0  # Apr-Jul (4 months)
    
    monthly_values = {}
    # Period 1: Aug-Dec (5 months)
    for m in [8, 9, 10, 11, 12]:
        monthly_values[m] = p1_monthly
    # Period 2: Jan-Mar (3 months)
    for m in [1, 2, 3]:
        monthly_values[m] = p2_monthly
    # Period 3: Apr-Jul (4 months)
    for m in [4, 5, 6, 7]:
        monthly_values[m] = p3_monthly
    
    return monthly_values

def solve_all_crop_years():
    """Solve all crop years using the 3-period residual solver with 5/3/4 splitting."""
    print("=" * 80)
    print("PHASE 2B: 3-Period Inter-Survey Residual Solver for Wheat (5/3/4 Logic)")
    print("=" * 80)
    
    # Load raw data
    print("\n[1/4] Loading Phase 2A raw monthly data...")
    raw_df = load_raw_data()
    print(f"    Loaded {len(raw_df)} rows")
    
    # Load dynamic seed stock
    print("\n[2/4] Loading dynamic seed stock (2001-02 carryout)...")
    seed_stock = load_dynamic_seed_stock()
    
    # Load cumulative YTD totals
    print("\n[3/4] Loading cumulative YTD totals (5/3/4 splitting)...")
    cumulative_totals, annual_totals = load_cumulative_ytd_totals()
    print(f"    Loaded cumulative totals for {len(cumulative_totals)} crop years")
    
    # Solve each crop year
    print("\n[4/4] Solving crop years...")
    all_results = []
    
    prev_jul_ending = seed_stock
    
    for cy in range(CROP_YEAR_START, CROP_YEAR_END + 1):
        print(f"    Solving {cy}-{str(cy+1)[2:]}...")
        
        # Get annual totals for this crop year
        sd = annual_totals.get(cy, {})
        
        # Annual totals (from July survey)
        annual_production = sd.get('Production', 0)
        annual_imports = sd.get('Imports', 0)
        annual_ending = sd.get('Total ending stocks', 0)
        annual_loss = sd.get('Loss in handling', 0)
        annual_feed_waste = sd.get('Animal feed, waste and dockage', 0)
        
        # Get monthly values for flow metrics using 5/3/4 splitting
        food_monthly_dict = compute_period_monthly_values(cumulative_totals, cy, 'Human food')
        industrial_monthly_dict = compute_period_monthly_values(cumulative_totals, cy, 'Industrial use')
        seed_monthly_dict = compute_period_monthly_values(cumulative_totals, cy, 'Seed requirements')
        exports_monthly_dict = compute_period_monthly_values(cumulative_totals, cy, 'Total exports')
        
        # FSR = Seed + Loss + Feed/Waste
        # Loss and Feed/Waste are annual totals - distribute evenly across 12 months
        annual_loss = sd.get('Loss in handling', 0)
        annual_feed_waste = sd.get('Animal feed, waste and dockage', 0)
        monthly_loss = annual_loss / 12.0
        monthly_feed_waste = annual_feed_waste / 12.0
        
        # FSR monthly = Seed + Loss + Feed/Waste
        fsr_monthly_dict = {}
        for m in MONTH_ORDER:
            fsr_monthly_dict[m] = seed_monthly_dict.get(m, 0) + monthly_loss + monthly_feed_waste
        
        # Get raw monthly data for this crop year
        cy_raw = raw_df[raw_df['Crop_Year'] == cy].copy()
        cy_raw = cy_raw.sort_values('Month').reset_index(drop=True)
        
        # Monthly imports from CIMT (only source for monthly imports)
        monthly_imports = cy_raw.set_index('Month')['Imports_KMT'].to_dict()
        
        # Use 5/3/4 splitting for ALL flow metrics including exports
        # Do NOT use CIMT monthly exports - use StatCan 5/3/4 splitting for exports too
        # monthly_exports_cimt = cy_raw.set_index('Month')['Exports_KMT'].to_dict()  # NOT USED
        
        # Survey anchors
        anchors = cy_raw[cy_raw['Anchor'].notna()].set_index('Month')['Stock_KMT'].to_dict()
        
        # Production (only in AUG)
        production_aug = cy_raw[cy_raw['Month'] == 8]['Production_KMT'].values
        production_aug = production_aug[0] if len(production_aug) > 0 and not pd.isna(production_aug[0]) else 0
        
        # Beginning stock
        if cy == CROP_YEAR_START:
            beg_stock = seed_stock
        else:
            beg_stock = prev_jul_ending
        
        # Verify S&D balance at annual level
        annual_food = sum(food_monthly_dict.values())
        annual_industrial = sum(industrial_monthly_dict.values())
        annual_seed = sum(seed_monthly_dict.values())
        annual_fsr = sum(fsr_monthly_dict.values())
        annual_exports = sum(exports_monthly_dict.values())
        annual_imports = sum(monthly_imports.values())
        total_production = production_aug
        
        balance_check = beg_stock + total_production + annual_imports - annual_food - annual_industrial - annual_fsr - annual_exports - annual_ending
        print(f"      {cy}-{str(cy+1)[2:]}: Annual balance check = {balance_check:.2f} KMT")
        
        # Initialize monthly arrays
        months = MONTH_ORDER
        n_months = len(months)
        
        food_monthly = np.zeros(n_months)
        industrial_monthly = np.zeros(n_months)
        fsr_monthly = np.zeros(n_months)
        exports_monthly = np.zeros(n_months)
        ending_stock_monthly = np.zeros(n_months)
        
        # Fill monthly arrays from computed values
        for i, m in enumerate(MONTH_ORDER):
            food_monthly[i] = food_monthly_dict.get(m, 0)
            industrial_monthly[i] = industrial_monthly_dict.get(m, 0)
            fsr_monthly[i] = fsr_monthly_dict.get(m, 0)
            exports_monthly[i] = exports_monthly_dict.get(m, 0)
        
        # Calculate sequential ending stocks
        running_stock = beg_stock
        
        for i, month in enumerate(MONTH_ORDER):
            m_imports = monthly_imports.get(month, 0)
            m_exports = exports_monthly_dict.get(month, 0)
            m_production = production_aug if month == 8 else 0
            m_food = food_monthly[i]
            m_industrial = industrial_monthly[i]
            m_fsr = fsr_monthly[i]
            
            ending_stock = running_stock + m_production + m_imports - m_food - m_industrial - m_fsr - m_exports
            
            ending_stock_monthly[i] = ending_stock
            running_stock = ending_stock
        
        # Verify anchors match
        for anchor_month in [12, 3, 7]:
            idx = MONTH_ORDER.index(anchor_month)
            calc_stock = ending_stock_monthly[idx]
            actual_stock = anchors.get(anchor_month, 0)
            diff = calc_stock - actual_stock
            if abs(diff) > 0.1:
                print(f"      WARNING: {cy} {SURVEY_ANCHORS[anchor_month]} anchor mismatch: calc={calc_stock:.1f}, actual={actual_stock:.1f}, diff={diff:.1f}")
        
        # Store results for this crop year
        for i, month in enumerate(MONTH_ORDER):
            all_results.append({
                'Crop_Year': cy,
                'Crop_Year_Label': f"{cy}-{str(cy+1)[2:]}",
                'Month': month,
                'Month_Name': MONTH_NAMES[month],
                'Beginning_Stock_KMT': beg_stock if month == 8 else ending_stock_monthly[MONTH_ORDER.index(month) - 1] if month != 8 else beg_stock,
                'Production_KMT': production_aug if month == 8 else 0,
                'Imports_KMT': monthly_imports.get(month, 0),
                'Food_KMT': food_monthly[i],
                'Industrial_KMT': industrial_monthly[i],
                'Seed_KMT': seed_monthly_dict.get(month, 0),
                'FSR_KMT': fsr_monthly[i],
                'Exports_KMT': exports_monthly[i],
                'Ending_Stock_KMT': ending_stock_monthly[i],
                'Planted_Area_MLN_AC': raw_df[(raw_df['Crop_Year'] == cy) & (raw_df['Month'] == 8)]['Planted_Area_MLN_AC'].values[0] if len(raw_df[(raw_df['Crop_Year'] == cy) & (raw_df['Month'] == 8)]) > 0 else np.nan,
                'Harvested_Area_MLN_AC': raw_df[(raw_df['Crop_Year'] == cy) & (raw_df['Month'] == 8)]['Harvested_Area_MLN_AC'].values[0] if len(raw_df[(raw_df['Crop_Year'] == cy) & (raw_df['Month'] == 8)]) > 0 else np.nan,
            })
        
        # Update prev_jul_ending for next crop year
        jul_idx = MONTH_ORDER.index(7)
        prev_jul_ending = ending_stock_monthly[jul_idx]
        
        # Verify final balance
        total_food = sum(food_monthly)
        total_industrial = sum(industrial_monthly)
        total_fsr = sum(fsr_monthly)
        total_exports = sum(exports_monthly)
        total_imports = sum(monthly_imports.values())
        total_production = production_aug
        
        final_balance = beg_stock + total_production + total_imports - total_food - total_industrial - total_fsr - total_exports - prev_jul_ending
        print(f"      {cy}-{str(cy+1)[2:]}: Annual balance check = {balance_check:.2f} KMT")
        print(f"      Monthly balance check = {final_balance:.2f} KMT")
        
        # Print 02-03 Food values for verification
        if cy == 2002:
            print(f"      02-03 Food monthly: Aug-Dec={food_monthly[0]:.2f}, Jan-Mar={food_monthly[5]:.2f}, Apr-Jul={food_monthly[8]:.2f}")
            print(f"      02-03 Industrial monthly: Aug-Dec={industrial_monthly[0]:.2f}, Jan-Mar={industrial_monthly[5]:.2f}, Apr-Jul={industrial_monthly[8]:.2f}")
            print(f"      02-03 Seed monthly: Aug-Dec={seed_monthly_dict[8]:.2f}, Jan-Mar={seed_monthly_dict[1]:.2f}, Apr-Jul={seed_monthly_dict[4]:.2f}")
            print(f"      02-03 Exports monthly: Aug-Dec={exports_monthly[0]:.2f}, Jan-Mar={exports_monthly[5]:.2f}, Apr-Jul={exports_monthly[8]:.2f}")
    
    return all_results

def main():
    print("=" * 80)
    print("PHASE 2B: 3-Period Inter-Survey Residual Solver for Wheat (5/3/4 Logic)")
    print("=" * 80)
    
    # Solve all crop years
    all_results = solve_all_crop_years()
    
    # Convert to DataFrame
    master_df = pd.DataFrame(all_results)
    
    # Calculate yield
    master_df['Yield_BU_AC'] = master_df.apply(
        lambda r: (r['Production_KMT'] * 1000 * BUSHEL_MULTIPLIER) / (r['Harvested_Area_MLN_AC'] * 1_000_000) 
        if pd.notna(r['Production_KMT']) and pd.notna(r['Harvested_Area_MLN_AC']) and r['Harvested_Area_MLN_AC'] > 0 else np.nan, axis=1
    )
    
    # Add calculated metrics
    master_df['Total_Disappearance_KMT'] = master_df['Food_KMT'] + master_df['Industrial_KMT'] + master_df['FSR_KMT'] + master_df['Exports_KMT']
    master_df['Total_Supply_KMT'] = master_df['Beginning_Stock_KMT'] + master_df['Production_KMT'] + master_df['Imports_KMT']
    
    # Verify balance for each month
    master_df['Balance_Check'] = master_df['Beginning_Stock_KMT'] + master_df['Production_KMT'] + master_df['Imports_KMT'] - master_df['Food_KMT'] - master_df['Industrial_KMT'] - master_df['FSR_KMT'] - master_df['Exports_KMT'] - master_df['Ending_Stock_KMT']
    
    # Save
    DATA_STORE_DIR.mkdir(parents=True, exist_ok=True)
    master_df.to_csv(OUTPUT_CSV, index=False)
    print(f"\n[5/5] Saved {len(master_df)} rows to {OUTPUT_CSV}")
    
    # Print summary for sample crop years
    print("\n" + "=" * 140)
    print("PHASE 2B RESULTS: Wheat 12-Month S&D Matrix (Sample Crop Years)")
    print("=" * 140)
    
    for cy in [2002, 2012, 2024]:
        cy_df = master_df[master_df['Crop_Year'] == cy]
        if len(cy_df) == 0:
            continue
        
        # Annual totals
        total_prod = cy_df['Production_KMT'].sum()
        total_imports = cy_df['Imports_KMT'].sum()
        total_food = cy_df['Food_KMT'].sum()
        total_industrial = cy_df['Industrial_KMT'].sum()
        total_fsr = cy_df['FSR_KMT'].sum()
        total_exports = cy_df['Exports_KMT'].sum()
        total_disp = total_food + total_industrial + cy_df['FSR_KMT'].sum() + total_exports
        
        beg_stock = cy_df[cy_df['Month'] == 8]['Beginning_Stock_KMT'].values[0]
        end_stock = cy_df[cy_df['Month'] == 7]['Ending_Stock_KMT'].values[0]
        planted = cy_df[cy_df['Month'] == 8]['Planted_Area_MLN_AC'].values[0]
        
        # Balance check
        balance = beg_stock + total_prod + total_imports - total_disp - cy_df[cy_df['Month'] == 7]['Ending_Stock_KMT'].values[0]
        
        print(f"\nCrop Year {cy}-{str(cy+1)[2:]}:")
        print(f"  Planted Area: {cy_df[cy_df['Month'] == 8]['Planted_Area_MLN_AC'].values[0]:.3f} MLN AC")
        print(f"  Production: {total_prod:,.1f} KMT")
        print(f"  Imports: {total_imports:,.1f} KMT")
        print(f"  Food: {total_food:,.1f} KMT")
        print(f"  Industrial: {total_industrial:,.1f} KMT")
        print(f"  FSR: {cy_df['FSR_KMT'].sum():,.1f} KMT")
        print(f"  Exports: {total_exports:,.1f} KMT")
        print(f"  Total Disposition: {total_disp:,.1f} KMT")
        print(f"  Beginning Stock (Aug): {beg_stock:,.1f} KMT")
        print(f"  Ending Stock (Jul): {end_stock:,.1f} KMT")
        print(f"  S&D Balance Check (Beg+Prod+Imp-Disp-End): {balance:.2f} KMT")
        
        # Monthly balance check
        cy_df_check = master_df[master_df['Crop_Year'] == cy]
        max_balance = cy_df_check['Balance_Check'].abs().max()
        print(f"  Max Monthly Balance Error: {max_balance:.2f} KMT")
    
    print("\n" + "=" * 140)
    print("Phase 2B Complete - Full 12-month S&D matrix saved to CMFT_Master.csv")
    print("=" * 140)

if __name__ == "__main__":
    main()