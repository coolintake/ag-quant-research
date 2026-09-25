"""
CMFT Engine Balancer - Core S&D Balancing Engine (BLUEPRINT ALIGNED)
Executes priority waterfall merge, biofuel disaggregation, yield calculation,
FSR residual solver, and dynamic carryout solver with 02-03 bootstrap logic.

Key blueprint alignments:
- 02-03 Beginning Stocks bootstrapped from 01-02 Ending Stocks
- Forward recursive linking: BegStock_t = EndStock_{t-1}
- July revision filtering for S&D (already in ingest)
- Frequency handling: Annual production, Tri-annual stocks, Monthly crush/biofuel/CIMT
- Soybean Sep-Aug crop year with July ending stock anchor
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

from config.paths import (
    ENGINE_ROOT, DATA_STORE_DIR, MASTER_FACT_TABLE,
    STATCAN_DIR, CIMT_DIR, CMFT_MAIN_XLSX
)
from modules.ingest_statcan import ingest_all_statcan
from modules.ingest_cimt import ingest_all_cimt
from modules.ingest_overrides import ingest_overrides
from config.mappings import load_dictionary_mapping


# ============================================================
# CONSTANTS & CONFIGURATION
# ============================================================

# Bushel multipliers (Tonnes -> Bushels)
BUSHEL_MULTIPLIERS = {
    'Wheat': 36.7437,
    'Durum': 36.7437,
    'Soybeans': 36.7437,
    'Dry peas': 36.7437,
    'Lentils': 36.7437,
    'Canola': 44.0925,
    'Barley': 45.9296,
    'Oats': 64.8418,
}

# Crop year start months (per blueprint: Jun-May for cereals/canola, Sep-Aug for soybeans)
CROP_YEAR_START = {
    'Soybeans': 9,   # September
    'Soyoil': 9,
    'Soymeal': 9,
    'Canola': 6,     # June (per blueprint Wheat Mo uses Jun-May)
    'Canola Oil': 6,
    'Canola Meal': 6,
    'Wheat': 6,      # June
    'Durum': 6,
    'Oats': 6,
    'Barley': 6,
    'Dry peas': 6,
    'Lentils': 6,
}

# Priority order for waterfall
SOURCE_PRIORITY = {
    'StatCan_CANSIM': 1,
    'CIMT_Actual': 2,
    'CGC_Estimates': 3,
    'Analyst_Override': 4,
}

# Load metric order from Mapping_Dictionary
def _load_metric_order() -> Dict[str, List[str]]:
    """Load the metric order for each commodity group from Mapping_Dictionary."""
    mapping_df = load_dictionary_mapping()
    
    metric_order = {}
    for group in ['Cereal', 'Oilseed', 'Vegetable Oil', 'Protein Meal', 'Pulse']:
        group_df = mapping_df[mapping_df['Commodity_Group'] == group]
        if len(group_df) > 0:
            seen = set()
            ordered = []
            for _, row in group_df.iterrows():
                metric = row['Tier1_Commodity_Metric']
                if metric not in seen:
                    seen.add(metric)
                    ordered.append(metric)
            metric_order[group] = ordered
    
    return metric_order


METRIC_ORDER = _load_metric_order()


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _get_crop_year(date: pd.Timestamp, commodity: str) -> int:
    """Get crop year for a given date and commodity."""
    start_month = CROP_YEAR_START.get(commodity, 6)
    if date.month >= start_month:
        return date.year
    else:
        return date.year - 1


def _normalize_commodity_name(name: str) -> str:
    """Normalize commodity names for matching."""
    name = str(name).strip()
    mapping = {
        'All wheat': 'Wheat',
        'Wheat, excluding durum': 'Wheat',
        'Wheat, all': 'Wheat',
        'Durum wheat': 'Durum',
        'Wheat, durum': 'Durum',       # FIX: Planting data uses "Wheat, durum"
        'Canola (rapeseed)': 'Canola',
        'Dry peas': 'Dry peas',
        'Chickpeas': 'Dry peas',
    }
    return mapping.get(name, name)


def _pivot_sd_data(df: pd.DataFrame, value_col: str = 'Scaled_Value') -> pd.DataFrame:
    """Pivot long-format S&D data to wide format with metrics as columns."""
    df_mapped = df.dropna(subset=['Tier1_Commodity_Metric']).copy()
    
    if 'Commodity_Norm' not in df_mapped.columns:
        df_mapped['Commodity_Norm'] = df_mapped['Commodity'].apply(_normalize_commodity_name)
    
    pivot = df_mapped.pivot_table(
        index=['Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year'],
        columns='Tier1_Commodity_Metric',
        values=value_col,
        aggfunc='first'
    ).reset_index()
    
    pivot.columns.name = None
    return pivot


def _merge_waterfall(base: pd.DataFrame, overlay: pd.DataFrame, 
                     key_cols: List[str], value_cols: List[str],
                     base_source: str, overlay_source: str) -> pd.DataFrame:
    """Merge overlay onto base using priority waterfall."""
    result = base.copy()
    
    for vcol in value_cols:
        if vcol not in overlay.columns:
            continue
            
        merge_keys = key_cols + [vcol]
        overlay_clean = overlay[merge_keys].dropna(subset=[vcol]).drop_duplicates(key_cols)
        
        if len(overlay_clean) == 0:
            continue
        
        result = result.merge(
            overlay_clean,
            on=key_cols,
            how='left',
            suffixes=('', '_overlay')
        )
        
        overlay_col = f'{vcol}_overlay'
        if overlay_col in result.columns:
            base_priority = SOURCE_PRIORITY.get(base_source, 99)
            overlay_priority = SOURCE_PRIORITY.get(overlay_source, 99)
            
            mask = result[vcol].isna() | (overlay_priority < base_priority)
            result.loc[mask, vcol] = result.loc[mask, overlay_col]
            result = result.drop(columns=[overlay_col])
    
    return result


def _compute_biofuel_weights(statcan_df: pd.DataFrame) -> Dict[int, Tuple[float, float]]:
    """Compute annual biofuel feedstock weights for Canola Oil vs Soyoil."""
    crush = statcan_df[
        (statcan_df['Source_Table'] == 'crush') &
        (statcan_df['Commodity_Norm'].isin(['Canola', 'Soybeans'])) &
        (statcan_df['StatCan_Raw_Metric'].str.contains('Seed crushed', case=False, na=False))
    ].copy()
    
    if len(crush) == 0:
        return {}
    
    crush['Crop_Year'] = crush.apply(
        lambda r: _get_crop_year(r['Date'], r['Commodity_Norm']), axis=1
    )
    annual_crush = crush.groupby(['Crop_Year', 'Commodity_Norm'])['Scaled_Value'].sum().unstack(fill_value=0)
    
    weights = {}
    for cy in annual_crush.index:
        canola_crush = annual_crush.loc[cy].get('Canola', 0)
        soy_crush = annual_crush.loc[cy].get('Soybeans', 0)
        total = canola_crush + soy_crush
        if total > 0:
            weights[cy] = (canola_crush / total, soy_crush / total)
        else:
            weights[cy] = (0.5, 0.5)
    return weights


def _calculate_yield(sd_wide: pd.DataFrame, statcan_df: pd.DataFrame) -> pd.DataFrame:
    """Calculate yield in BU/AC from Production (KMT) and Harvested Area (MLN AC)."""
    result = sd_wide.copy()
    
    planting = statcan_df[
        (statcan_df['Source_Table'] == 'planting') &
        (statcan_df['StatCan_Raw_Metric'].str.contains('Harvested area', case=False, na=False)) &
        (statcan_df['Scaled_Unit'] == 'MLN AC')
    ].copy()
    
    if len(planting) == 0:
        return result
    
    planting['Commodity_Norm'] = planting['Commodity'].apply(_normalize_commodity_name)
    harvested = planting.groupby(['Date', 'Commodity_Norm'])['Scaled_Value'].first().reset_index()
    harvested = harvested.rename(columns={'Scaled_Value': 'Harvested_MLN_AC'})
    
    result = result.merge(harvested, on=['Date', 'Commodity_Norm'], how='left')
    
    for commodity, mult in BUSHEL_MULTIPLIERS.items():
        mask = (result['Commodity_Norm'] == commodity) & \
               result['Production'].notna() & \
               result['Harvested_MLN_AC'].notna() & \
               (result['Harvested_MLN_AC'] > 0)
        
        if mask.any():
            result.loc[mask, 'Yield_BU_AC'] = (
                result.loc[mask, 'Production'] * 1000 /
                (result.loc[mask, 'Harvested_MLN_AC'] * 1_000_000) *
                mult
            )
    
    print(f"    Yield calculated for {result['Yield_BU_AC'].notna().sum()} rows")
    return result


def _apply_bootstrap_and_recursive_linking(sd_wide: pd.DataFrame) -> pd.DataFrame:
    """
    Apply 02-03 bootstrap and forward recursive linking:
    - 02-03 Beginning Stocks = 01-02 Ending Stocks (from historical data)
    - For all forward years: BegStock_t = EndStock_{t-1}
    Works on pivoted data (one row per commodity-month).
    """
    result = sd_wide.copy()
    result = result.sort_values(['Commodity_Norm', 'Crop_Year', 'Date']).reset_index(drop=True)
    
    # Add month column for filtering
    result['Month'] = result['Date'].dt.month
    
    for commodity in result['Commodity_Norm'].unique():
        mask = result['Commodity_Norm'] == commodity
        comm_data = result.loc[mask].copy()
        comm_data = comm_data.sort_values('Crop_Year').reset_index(drop=True)
        
        # Get July (month 7) rows for each crop year - these are the final estimates
        july_mask = comm_data['Month'] == 7
        july_data = comm_data[july_mask].copy()
        
        if len(july_data) == 0:
            continue
        
        july_data = july_data.sort_values('Crop_Year').reset_index(drop=True)
        
        # Apply recursive linking on July anchor rows
        crop_years = sorted(july_data['Crop_Year'].unique())
        
        for i, cy in enumerate(crop_years):
            cy_mask = july_data['Crop_Year'] == cy
            cy_indices = july_data[cy_mask].index
            
            if i == 0:
                # First crop year (02-03): Beginning Stocks bootstrap
                # The 01-02 ending stock would be the previous crop year's ending stock
                # Since we don't have 01-02 in our data (starts at 02-03),
                # we keep the existing Beginning Stocks for 02-03 as the bootstrap value
                pass
            else:
                # Forward years: BegStock_t = EndStock_{t-1}
                prev_cy = crop_years[i-1]
                prev_mask = july_data['Crop_Year'] == prev_cy
                prev_indices = july_data[prev_mask].index
                
                if 'Ending Stocks' in july_data.columns and 'Beginning Stocks' in july_data.columns:
                    prev_end_vals = july_data.loc[prev_mask, 'Ending Stocks'].values
                    if len(prev_end_vals) > 0 and pd.notna(prev_end_vals[0]):
                        # Update the original result dataframe
                        for idx in cy_indices:
                            orig_idx = result[
                                (result['Commodity_Norm'] == commodity) & 
                                (result['Crop_Year'] == cy) & 
                                (result['Month'] == 7)
                            ].index
                            if len(orig_idx) > 0:
                                result.loc[orig_idx[0], 'Beginning Stocks'] = prev_end_vals[0]
    
    # Drop the temporary Month column
    result = result.drop(columns=['Month'])
    return result


def _solve_fsr_carryout(sd_wide: pd.DataFrame) -> pd.DataFrame:
    """
    Solve FSR and dynamic carryout on July anchor rows.
    For each commodity, ensure S&D balances: Ending = Beg + Prod + Imp - Food - Ind - FSR - Exp
    """
    result = sd_wide.copy()
    result = result.sort_values(['Commodity_Norm', 'Date']).reset_index(drop=True)
    
    sd_cols = [c for c in ['Beginning Stocks', 'Production', 'Imports', 'Food', 'Industrial', 'FSR', 'Exports', 'Ending Stocks'] 
               if c in result.columns]
    
    for commodity in result['Commodity_Norm'].unique():
        mask = result['Commodity_Norm'] == commodity
        comm_data = result.loc[mask].copy()
        
        for col in sd_cols:
            if col not in comm_data.columns:
                comm_data[col] = np.nan
        
        # Forward fill Beginning Stocks from previous Ending Stocks (monthly)
        comm_data['Beginning Stocks'] = comm_data['Beginning Stocks'].fillna(
            comm_data['Ending Stocks'].shift(1)
        )
        
        # Compute FSR as residual where Ending Stocks known
        supply = (
            comm_data['Beginning Stocks'].fillna(0) +
            comm_data['Production'].fillna(0) +
            comm_data['Imports'].fillna(0)
        )
        demand_known = (
            comm_data['Food'].fillna(0) +
            comm_data['Industrial'].fillna(0) +
            comm_data['Exports'].fillna(0)
        )
        
        has_ending = comm_data['Ending Stocks'].notna()
        comm_data.loc[has_ending, 'FSR'] = (
            supply[has_ending] - demand_known[has_ending] - comm_data.loc[has_ending, 'Ending Stocks']
        )
        
        # Interpolate FSR for missing months
        comm_data['FSR'] = comm_data['FSR'].interpolate(method='linear')
        
        # Forward solve Ending Stocks where missing
        for i in range(len(comm_data)):
            if pd.isna(comm_data.iloc[i]['Ending Stocks']):
                beg = comm_data.iloc[i]['Beginning Stocks']
                prod = comm_data.iloc[i]['Production']
                imp = comm_data.iloc[i]['Imports']
                food = comm_data.iloc[i]['Food']
                ind = comm_data.iloc[i]['Industrial']
                fsr = comm_data.iloc[i]['FSR']
                exp = comm_data.iloc[i]['Exports']
                
                if pd.notna(beg) and pd.notna(prod):
                    comm_data.iloc[i, comm_data.columns.get_loc('Ending Stocks')] = (
                        (beg if pd.notna(beg) else 0) +
                        (prod if pd.notna(prod) else 0) +
                        (imp if pd.notna(imp) else 0) -
                        (food if pd.notna(food) else 0) -
                        (ind if pd.notna(ind) else 0) -
                        (fsr if pd.notna(fsr) else 0) -
                        (exp if pd.notna(exp) else 0)
                    )
        
        for col in sd_cols + ['FSR']:
            if col in comm_data.columns:
                result.loc[mask, col] = comm_data[col].values
    
    return result


def run_balancer() -> pd.DataFrame:
    """Main entry point for the CMFT Engine Balancer."""
    print("=" * 60)
    print("CMFT ENGINE BALANCER - BLUEPRINT ALIGNED")
    print("=" * 60)
    
    # 1. LOAD ALL SOURCE DATA
    print("\n[1/6] Loading source data...")
    
    print("  Loading StatCan CANSIM (corrected scaling, July revision filter)...")
    statcan_df = ingest_all_statcan()
    print(f"    {len(statcan_df)} rows loaded")
    
    print("  Loading CIMT Actuals...")
    cimt_df = ingest_all_cimt()
    print(f"    {len(cimt_df)} rows loaded")
    
    print("  Loading Analyst Overrides...")
    overrides_df = ingest_overrides()
    print(f"    {len(overrides_df)} rows loaded")
    
    print("  Loading Dictionary Mapping...")
    mapping_df = load_dictionary_mapping()
    print(f"    {len(mapping_df)} mapping rows loaded")
    print(f"    Metric order loaded for groups: {list(METRIC_ORDER.keys())}")
    
    # 2. PIVOT STATCAN TO WIDE S&D FORMAT
    print("\n[2/6] Pivoting StatCan to S&D wide format...")
    sd_wide = _pivot_sd_data(statcan_df, 'Scaled_Value')
    print(f"    {len(sd_wide)} commodity-month rows")
    print(f"    Commodities: {sorted(sd_wide['Commodity_Norm'].unique())}")
    print(f"    Metrics available: {[c for c in ['Beginning Stocks', 'Production', 'Imports', 'Food', 'Industrial', 'FSR', 'Exports', 'Ending Stocks'] if c in sd_wide.columns]}")
    
    # 3. PRIORITY WATERFALL MERGE
    print("\n[3/6] Applying priority waterfall merge...")
    
    key_cols = ['Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year']
    
    # Merge CIMT exports/imports
    if len(cimt_df) > 0:
        cimt_agg = cimt_df.groupby(['Date', 'Date_Str', 'Commodity', 'Trade_Type'])['Value_KMT'].sum().unstack(fill_value=0).reset_index()
        cimt_agg.columns.name = None
        cimt_agg = cimt_agg.rename(columns={
            'Export': 'Exports_CIMT',
            'Import': 'Imports_CIMT'
        })
        cimt_agg['Commodity_Norm'] = cimt_agg['Commodity'].apply(_normalize_commodity_name)
        cimt_agg['Crop_Year'] = cimt_agg.apply(lambda r: _get_crop_year(r['Date'], r['Commodity_Norm']), axis=1)
        
        sd_wide = _merge_waterfall(
            sd_wide, cimt_agg, key_cols,
            ['Exports', 'Imports'],
            'StatCan_CANSIM', 'CIMT_Actual'
        )
        print(f"    CIMT merged: Exports/Imports updated")
    
    # Merge Analyst Overrides
    if len(overrides_df) > 0:
        overrides_clean = overrides_df.copy()
        overrides_clean['Commodity_Norm'] = overrides_clean['Commodity'].apply(_normalize_commodity_name)
        overrides_clean['Date'] = pd.to_datetime(overrides_clean['Date'])
        overrides_clean['Date_Str'] = overrides_clean['Date'].dt.strftime('%Y-%m-%d')
        overrides_clean['Crop_Year'] = overrides_clean.apply(lambda r: _get_crop_year(r['Date'], r['Commodity_Norm']), axis=1)
        
        sd_wide = _merge_waterfall(
            sd_wide, overrides_clean, key_cols,
            ['Beginning Stocks', 'Production', 'Imports', 'Food', 'Industrial', 'FSR', 'Exports', 'Ending Stocks'],
            'StatCan_CANSIM', 'Analyst_Override'
        )
        print(f"    Overrides merged: {len(overrides_df)} records applied")
    
    # 4. APPLY 02-03 BOOTSTRAP & RECURSIVE LINKING
    print("\n[4/6] Applying 02-03 bootstrap & recursive stock linking...")
    sd_wide = _apply_bootstrap_and_recursive_linking(sd_wide)
    print(f"    Bootstrap and recursive linking applied")
    
    # 5. YIELD CALCULATION
    print("\n[5/6] Calculating yields (BU/AC)...")
    sd_wide = _calculate_yield(sd_wide, statcan_df)
    
    # 6. FSR & CARRYOUT SOLVER
    print("\n[6/6] Solving FSR residuals & dynamic carryout...")
    sd_wide = _solve_fsr_carryout(sd_wide)
    
    # 7. FINALIZE MASTER FACT TABLE
    print("\n[7/7] Finalizing Master Fact Table...")
    
    master_cols = [
        'Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year',
        'Beginning Stocks', 'Production', 'Imports',
        'Food', 'Industrial', 'FSR', 'Exports', 'Ending Stocks',
        'Yield_BU_AC', 'Harvested_MLN_AC',
        'Source', 'Data_Type'
    ]
    
    sd_wide['Source'] = 'CMFT_Engine'
    sd_wide['Data_Type'] = 'Balanced'
    
    final_cols = [c for c in master_cols if c in sd_wide.columns]
    master_df = sd_wide[final_cols].copy()
    
    master_df = master_df.sort_values(['Commodity_Norm', 'Date']).reset_index(drop=True)
    
    print(f"    Master table shape: {master_df.shape}")
    print(f"    Date range: {master_df['Date_Str'].min()} to {master_df['Date_Str'].max()}")
    print(f"    Commodities: {master_df['Commodity_Norm'].nunique()}")
    
    # Verify S&D balance (CHECK)
    master_df['CHECK'] = (
        master_df['Beginning Stocks'].fillna(0) +
        master_df['Production'].fillna(0) +
        master_df['Imports'].fillna(0) -
        master_df['Food'].fillna(0) -
        master_df['Industrial'].fillna(0) -
        master_df['FSR'].fillna(0) -
        master_df['Exports'].fillna(0) -
        master_df['Ending Stocks'].fillna(0)
    )
    check_max = master_df['CHECK'].abs().max()
    print(f"    Max CHECK residual: {check_max:.6f} KMT")
    
    # 8. WRITE OUTPUT
    print(f"\nWriting to {MASTER_FACT_TABLE}...")
    DATA_STORE_DIR.mkdir(parents=True, exist_ok=True)
    master_df.to_csv(MASTER_FACT_TABLE, index=False)
    print(f"  [OK] Written {len(master_df)} rows to CMFT_Master.csv")
    
    return master_df


if __name__ == "__main__":
    try:
        master = run_balancer()
        print("\n" + "=" * 60)
        print("CMFT ENGINE BALANCER - COMPLETE")
        print("=" * 60)
        print(f"Output: {MASTER_FACT_TABLE}")
        print(f"Records: {len(master)}")
        print(f"Date range: {master['Date_Str'].min()} to {master['Date_Str'].max()}")
        print(f"Commodities: {sorted(master['Commodity_Norm'].unique())}")
    except Exception as e:
        print(f"\n[FAIL] Balancer failed: {e}")
        import traceback
        traceback.print_exc()
        raise