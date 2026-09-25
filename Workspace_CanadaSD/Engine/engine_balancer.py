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
from modules.monthly_builder import build_monthly
from config.commodities import (
    TRADE_SOURCE, STOCKS_MODE,
    BUSHEL_MULTIPLIERS, CROP_YEAR_START_MONTH, ANCHOR_MONTH,
    normalize_commodity, get_crop_year,
)


# ============================================================
# CONSTANTS & CONFIGURATION
# ============================================================

# BUSHEL_MULTIPLIERS and crop-year start months now live in config/commodities.py.
# (The old local CROP_YEAR_START used June for cereals while ingest/styles used
# August, so CIMT/override rows were keyed to a different Crop_Year than StatCan.)
CROP_YEAR_START = CROP_YEAR_START_MONTH

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
    """Delegates to config.commodities."""
    return get_crop_year(date, commodity)


def _normalize_commodity_name(name: str) -> str:
    """Delegates to config.commodities."""
    return normalize_commodity(name)


def _pivot_sd_data(df: pd.DataFrame, value_col: str = 'Scaled_Value') -> pd.DataFrame:
    """Pivot long-format S&D data to wide format with metrics as columns.

    - Restricted to the 32100013 supply & disposition table. Crush-table rows
      for 'Canola (rapeseed)' also normalize to 'Canola' and carry Tier1 labels
      like 'Beginning Stocks' (oil/meal stocks) - they must not land on the
      seed balance sheet.
    - SUM, not 'first': several raw StatCan lines roll up to one Tier1 metric
      (FSR = Seed requirements + Loss in handling + Animal feed, waste and
      dockage). 'first' kept only one component. min_count=1 keeps all-NaN
      groups as NaN instead of 0.
    """
    df_mapped = df.dropna(subset=['Tier1_Commodity_Metric']).copy()
    if 'Source_Table' in df_mapped.columns:
        df_mapped = df_mapped[df_mapped['Source_Table'] == 'supply_disposition']

    if 'Commodity_Norm' not in df_mapped.columns:
        df_mapped['Commodity_Norm'] = df_mapped['Commodity'].apply(_normalize_commodity_name)

    pivot = (
        df_mapped
        .groupby(['Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year', 'Tier1_Commodity_Metric'])[value_col]
        .sum(min_count=1)
        .unstack('Tier1_Commodity_Metric')
        .reset_index()
    )
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
    # Canada-level only, if the planting extract includes provinces
    if 'Geography' in planting.columns:
        planting = planting[planting['Geography'].fillna('Canada').str.strip() == 'Canada']
    # Planting is annual (YYYY-01-01); S&D anchor rows are dated July of the
    # following year. Joining on Date never matched -> yield was always empty.
    # Join on Crop_Year (harvest year YYYY == crop year YYYY-YY+1) instead.
    planting['Crop_Year'] = planting['Date'].dt.year
    harvested = planting.groupby(['Crop_Year', 'Commodity_Norm'])['Scaled_Value'].first().reset_index()
    harvested = harvested.rename(columns={'Scaled_Value': 'Harvested_MLN_AC'})

    result = result.merge(harvested, on=['Crop_Year', 'Commodity_Norm'], how='left')
    # Harvested area belongs on the July anchor row only
    result.loc[result['Date'].dt.month != ANCHOR_MONTH, 'Harvested_MLN_AC'] = np.nan
    if 'Yield_BU_AC' not in result.columns:
        result['Yield_BU_AC'] = np.nan
    
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


def _product_export_diagnostic(statcan_df: pd.DataFrame, cimt_df: pd.DataFrame) -> None:
    """For commodities whose CIMT total includes product lines (e.g. Oats),
    compare CIMT grain / product exports with StatCan 32100013 'Grain exports'
    / 'Product exports' (July = full crop year). The implied factor
    StatCan product / CIMT product weight is what CIMT_GRAIN_EQUIV should hold."""
    prod = cimt_df[cimt_df['Is_Product'].fillna(False).astype(bool)]
    comms = sorted(prod['Commodity'].unique())
    if not comms:
        return
    sc = statcan_df[
        (statcan_df['Source_Table'] == 'supply_disposition') &
        (statcan_df['Date'].dt.month == 7) &
        (statcan_df['Commodity_Norm'].isin(comms)) &
        (statcan_df['StatCan_Raw_Metric'].isin(['Grain exports', 'Product exports', 'Total exports']))
    ].pivot_table(index=['Commodity_Norm', 'Crop_Year'], columns='StatCan_Raw_Metric',
                  values='Scaled_Value', aggfunc='sum')
    ex = cimt_df[(cimt_df['Commodity'].isin(comms)) & (cimt_df['Trade_Type'] == 'Export')].copy()
    ex['Crop_Year'] = ex.apply(lambda r: _get_crop_year(r['Date'], r['Commodity']), axis=1)
    ex['Kind'] = np.where(ex['Is_Product'], 'CIMT_Product_PW', 'CIMT_Grain')
    ci = ex.pivot_table(index=['Commodity', 'Crop_Year'], columns='Kind',
                        values='Value_KMT_Product_Weight', aggfunc='sum')
    ci.index = ci.index.set_names(['Commodity_Norm', 'Crop_Year'])
    t = ci.join(sc, how='inner')
    if t.empty:
        return
    print("    Export tie-out, CIMT vs StatCan 32100013 (KMT; CIMT products as in file - grain equivalent if built by cimt_build.py):")
    for c, g in t.groupby(level=0):
        g = g.droplevel(0).sort_index().tail(6)
        print(f"      {c}:  CropYr  CIMT_grain  SC_grain   CIMT_prod  SC_prod   implied factor")
        for cy, r in g.iterrows():
            cp, sp = r.get('CIMT_Product_PW', np.nan), r.get('Product exports', np.nan)
            f = sp / cp if pd.notna(cp) and cp > 0 and pd.notna(sp) else np.nan
            print(f"             {cy}-{str(cy + 1)[2:]}  {r.get('CIMT_Grain', np.nan):10.1f}"
                  f"  {r.get('Grain exports', np.nan):8.1f}   {cp:9.1f}  {sp:7.1f}   {f:8.2f}")


def _harvested_table(statcan_df: pd.DataFrame) -> pd.DataFrame:
    """Canada seeded (Planted) and harvested area, MLN AC, by Crop_Year
    (= harvest calendar year). Raw labels: 'Seeded area (acres)',
    'Harvested area (acres)'."""
    p = statcan_df[
        (statcan_df['Source_Table'] == 'planting') &
        (statcan_df['Scaled_Unit'] == 'MLN AC')
    ].copy()
    if 'Geography' in p.columns:
        p = p[p['Geography'].fillna('Canada').str.strip() == 'Canada']
    p['Crop_Year'] = p['Date'].dt.year
    metric = p['StatCan_Raw_Metric'].fillna('')
    p['Area'] = np.select(
        [metric.str.contains('Harvested area', case=False),
         metric.str.contains('Seeded area', case=False)],
        ['Harvested_MLN_AC', 'Planted_MLN_AC'], default='')
    p = p[p['Area'] != '']
    out = (p.groupby(['Crop_Year', 'Commodity_Norm', 'Area'])['Scaled_Value'].first()
             .unstack('Area').reset_index())
    out.columns.name = None
    for c in ('Harvested_MLN_AC', 'Planted_MLN_AC'):
        if c not in out.columns:
            out[c] = np.nan
    return out


# NOTE: _apply_bootstrap_and_recursive_linking, _calculate_yield and
# _solve_fsr_carryout are superseded by modules/monthly_builder.py and are no
# longer called. Kept for reference; safe to delete.
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
    
    # 2. PIVOT STATCAN TO WIDE S&D FORMAT (one row per commodity x release)
    print("\n[2/5] Pivoting StatCan S&D releases (Dec/Mar/Jul, cumulative)...")
    sd_wide = _pivot_sd_data(statcan_df, 'Scaled_Value')
    print(f"    {len(sd_wide)} commodity-release rows")
    print(f"    Commodities: {sorted(sd_wide['Commodity_Norm'].unique())}")
    print(f"    Releases by month: {sd_wide['Date'].dt.month.value_counts().sort_index().to_dict()}")

    # 3. ANALYST OVERRIDES (on release rows; currently no overrides sheet)
    if len(overrides_df) > 0:
        key_cols = ['Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year']
        overrides_clean = overrides_df.copy()
        overrides_clean['Commodity_Norm'] = overrides_clean['Commodity'].apply(_normalize_commodity_name)
        overrides_clean['Date'] = pd.to_datetime(overrides_clean['Date'])
        overrides_clean['Date_Str'] = overrides_clean['Date'].dt.strftime('%Y-%m-%d')
        overrides_clean['Crop_Year'] = overrides_clean.apply(lambda r: _get_crop_year(r['Date'], r['Commodity_Norm']), axis=1)
        sd_wide = _merge_waterfall(
            sd_wide, overrides_clean, key_cols,
            ['Beginning Stocks', 'Production', 'Imports', 'Food', 'Industrial', 'Crush', 'FSR', 'Exports', 'Ending Stocks'],
            'StatCan_CANSIM', 'Analyst_Override'
        )
        print(f"    Overrides merged: {len(overrides_df)} records applied")

    # 4. CIMT MONTHLY + HARVESTED AREA
    # CIMT now feeds the monthly builder directly: it fills Exports/Imports
    # only in months StatCan's releases do not cover. (The earlier annual
    # gap-fill into July rows created a phantom Soybeans 2025-26 row whose
    # Sep-Aug year had not been published yet: CHECK = -6,195 KMT.)
    print("\n[3/5] Preparing CIMT monthly trade and harvested area...")
    cimt_monthly = None
    if len(cimt_df) > 0:
        cimt = cimt_df.dropna(subset=['Date']).copy()
        cimt['Commodity_Norm'] = cimt['Commodity'].apply(_normalize_commodity_name)
        cimt_monthly = (
            cimt.groupby(['Date', 'Commodity_Norm', 'Trade_Type'])['Value_KMT']
            .sum().unstack().reset_index()
            .rename(columns={'Export': 'Exports', 'Import': 'Imports'})
        )
        cimt_monthly.columns.name = None
        for c in ('Exports', 'Imports'):
            if c not in cimt_monthly.columns:
                cimt_monthly[c] = np.nan
        print(f"    CIMT monthly rows: {len(cimt_monthly)}  (trade source: {TRADE_SOURCE})")
        cov = cimt_monthly.groupby('Commodity_Norm')['Date'].agg(['min', 'max'])
        for c, r in cov.iterrows():
            print(f"      {c:14} {r['min']:%Y-%m} to {r['max']:%Y-%m}")
        no_cimt = sorted(set(sd_wide['Commodity_Norm']) - set(cov.index))
        if no_cimt and TRADE_SOURCE == 'cimt':
            print(f"    WARNING: no CIMT file mapped for {no_cimt} -> their Exports/Imports stay empty")
    if len(cimt_df) > 0 and 'Is_Product' in cimt_df.columns:
        _product_export_diagnostic(statcan_df, cimt_df)
    harvested = _harvested_table(statcan_df)
    print(f"    Harvested area rows: {len(harvested)}")

    # 5. MONTHLY BUILD (5/3/4 flows, observed stock anchors, residual FSR,
    #    Beg[t] = End[t-1], crop-year bridging)
    print("\n[4/5] Building monthly balance sheets...")
    monthly = build_monthly(sd_wide, harvested, cimt_monthly)
    print(f"    {len(monthly)} commodity-month rows")
    last = monthly.groupby('Commodity_Norm')['Date_Str'].max().to_dict()
    print(f"    Latest month per commodity: {last}")

    # Diagnostic: derived July stocks vs StatCan's surveyed ending stocks.
    # Stocks are rolled from flows, so any CIMT-vs-StatCan trade gap
    # (e.g. StatCan 'Total exports' includes product exports such as flour)
    # accumulates year over year through crop-year bridging.
    obs = sd_wide[sd_wide['Date'].dt.month == 7][['Commodity_Norm', 'Crop_Year', 'Ending Stocks']]
    der = monthly[monthly['CY_Month'] == 12][['Commodity_Norm', 'Crop_Year', 'Ending Stocks']]
    cmp_ = der.merge(obs, on=['Commodity_Norm', 'Crop_Year'], suffixes=('_Derived', '_StatCan')).dropna()
    if len(cmp_):
        cmp_['Gap'] = cmp_['Ending Stocks_Derived'] - cmp_['Ending Stocks_StatCan']
        print("    Derived vs StatCan July ending stocks (KMT), per commodity:")
        for c, g in cmp_.groupby('Commodity_Norm'):
            last = g.sort_values('Crop_Year').iloc[-1]
            print(f"      {c:10} max |gap| {g['Gap'].abs().max():9.1f}   "
                  f"latest {int(last['Crop_Year'])}-{str(int(last['Crop_Year']) + 1)[2:]}: {last['Gap']:9.1f}")

    # Diagnostic: engine FSR vs StatCan reported FSR (seed + loss + feed).
    # In anchored mode FSR absorbs any CIMT-vs-StatCan trade difference, so
    # a persistent gap here points at missing trade lines (e.g. Oats products).
    rep = sd_wide[sd_wide['Date'].dt.month == 7][['Commodity_Norm', 'Crop_Year', 'FSR']]
    eng = monthly.groupby(['Commodity_Norm', 'Crop_Year'])['FSR'].sum().reset_index()
    f = eng.merge(rep, on=['Commodity_Norm', 'Crop_Year'], suffixes=('_Engine', '_StatCan')).dropna()
    if len(f):
        f['Gap'] = f['FSR_Engine'] - f['FSR_StatCan']
        print(f"    Engine FSR vs StatCan reported FSR (KMT/crop year), stocks mode = {STOCKS_MODE}:")
        for c, g in f.groupby('Commodity_Norm'):
            print(f"      {c:10} mean gap {g['Gap'].mean():8.1f}   max |gap| {g['Gap'].abs().max():8.1f}")

    # 6. FINALIZE MASTER FACT TABLE
    print("\n[5/5] Finalizing Master Fact Table...")
    master_df = monthly.copy()
    master_df['Source'] = 'CMFT_Engine'
    master_df['Data_Type'] = 'Balanced'

    print(f"    Master table shape: {master_df.shape}")
    print(f"    Date range: {master_df['Date_Str'].min()} to {master_df['Date_Str'].max()}")
    print(f"    Commodities: {master_df['Commodity_Norm'].nunique()}")

    # Verify S&D balance (CHECK) - Crush included (oilseeds)
    master_df['CHECK'] = (
        master_df['Beginning Stocks'].fillna(0) +
        master_df['Production'].fillna(0) +
        master_df['Imports'].fillna(0) -
        master_df['Food'].fillna(0) -
        master_df['Industrial'].fillna(0) -
        master_df['Crush'].fillna(0) -
        master_df['FSR'].fillna(0) -
        master_df['Exports'].fillna(0) -
        master_df['Ending Stocks'].fillna(0)
    )
    check_max = master_df['CHECK'].abs().max()
    tol = 0.0009
    print(f"    Max CHECK residual: {check_max:.6f} KMT "
          f"({'OK' if check_max <= tol else 'FAIL'} vs +/-{tol} tolerance)")
    neg = master_df[master_df['Ending Stocks'] < 0]
    if len(neg):
        print(f"    WARNING: {len(neg)} months with negative ending stocks "
              f"(even 5/3/4 split can overshoot a thin segment):")
        print(neg[['Commodity_Norm', 'Date_Str', 'Ending Stocks']].head(10).to_string(index=False))

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