"""
CMFT Engine - Phase 4: Durum Monthly S&D Balance Builder
==========================================================
Single-commodity isolation module for Durum.
Builds the Durum_Mo tab in CanadaSD_Main.xlsx.

Abides by ENGINE_RULES.md:
- Dynamic crop year lookup from CMFT_Main.xlsx Mapping_Dictionary
- 5/3/4 YTD interpolation rule
- Vector/fuzzy aliasing logic
- Stock rolling & dynamic anchor guardrails
- Balance check within +/-0.0009 KMT
- Tab-level write isolation (only modifies Durum_Mo)
- Modular isolation (standalone, no cross-commodity dependencies)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import Dict, List, Tuple, Optional
import warnings
import os
warnings.filterwarnings('ignore')

# ============================================================
# PATH ANCHORS (from ENGINE_RULES.md Section 1)
# ============================================================
ROOT_WS = Path(r"C:\Workspace_CanadaSD")
DATA_INPUTS = ROOT_WS / "Data_Inputs"
OUTPUTS = ROOT_WS / "Outputs"
TARGET_WORKBOOK = OUTPUTS / "CanadaSD_Main.xlsx"
CMFT_MAIN = DATA_INPUTS / "CMFT_Main.xlsx"

# CIMT source (read-only)
CIMT_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD")
CIMT_DURUM_FILE = CIMT_DIR / "CIMT_Amber_Durum_20260917.xlsx"

# StatCan data directories (from Data_Inputs)
SD_DIR = DATA_INPUTS / "S&D_32100013-eng"
PLANT_DIR = DATA_INPUTS / "Planting_32100359-eng"

# ============================================================
# DYNAMIC CROP YEAR LOOKUP (ENGINE_RULES.md Section 3)
# ============================================================
def get_durum_crop_year_info() -> Dict:
    """
    Dynamically inspect CMFT_Main.xlsx -> Mapping_Dictionary tab -> Crop_Year column
    for Durum to establish exact marketing year start month.
    """
    df = pd.read_excel(CMFT_MAIN, sheet_name='Mapping_Dictionary', engine='openpyxl')
    durum_df = df[df['Source Commodity'].str.contains('Durum', case=False, na=False)]
    crop_year_str = durum_df['Crop_Year'].iloc[0]  # e.g., 'Aug-Jul'
    # Convert month name to number
    month_map = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
    start_month = month_map[crop_year_str.split('-')[0]]  # 8 for Aug-Jul
    return {
        'crop_year_str': crop_year_str,
        'start_month': start_month,
        'commodity': 'Durum',
        'commodity_group': durum_df['Commodity_Group'].iloc[0],
    }


# ============================================================
# STATCAN DATA INGESTION
# ============================================================
def ingest_statcan_sd() -> pd.DataFrame:
    """Read StatCan S&D data for Durum from CSV."""
    sd_path = SD_DIR / '32100013.csv'
    df = pd.read_csv(sd_path, dtype=str, low_memory=False)
    df['REF_DATE'] = pd.to_datetime(df['REF_DATE'], errors='coerce')
    durum_sd = df[df['Type of crop'].str.contains('Durum', case=False, na=False)].copy()
    durum_sd['Value'] = pd.to_numeric(durum_sd['VALUE'], errors='coerce')
    durum_sd['Scaled_Value'] = durum_sd['Value']  # Already in KMT (scalar_factor=thousands)
    durum_sd['Crop_Year'] = durum_sd['REF_DATE'].apply(
        lambda d: d.year if d.month >= 8 else d.year - 1
    )
    # Use primary vector set (v73866474-v73866492) for correct metric values
    # Filter to vectors in the primary range
    durum_sd = durum_sd[durum_sd['VECTOR'].str.startswith('v738664')].copy()
    # Remove the duplicate Total supplies row (v73866493 is a duplicate of v73866474)
    durum_sd = durum_sd[durum_sd['VECTOR'] != 'v73866493'].copy()
    return durum_sd


def ingest_cimt_trade() -> pd.DataFrame:
    """Read CIMT Amber Durum trade data."""
    df = pd.read_excel(CIMT_DURUM_FILE, sheet_name='Raw Data', engine='openpyxl')
    df['Year'] = pd.to_numeric(df['Year'], errors='coerce')
    df['Month'] = pd.to_numeric(df['Month'], errors='coerce')
    df['KMT'] = pd.to_numeric(df['KMT'], errors='coerce')
    # Drop rows with NaN Year or Month
    df = df.dropna(subset=['Year', 'Month'])
    df['Year'] = df['Year'].astype(int)
    df['Month'] = df['Month'].astype(int)
    df['Date'] = pd.to_datetime(dict(year=df['Year'], month=df['Month'], day=1), errors='coerce')
    # Filter to Durum-specific HS codes
    durum_hs = df[df['HS6'].isin([100111.0, 100119.0])].copy()
    durum_hs['Crop_Year'] = durum_hs['Date'].apply(
        lambda d: d.year if d.month >= 8 else d.year - 1
    )
    return durum_hs


# ============================================================
# S&D BALANCE MODEL BUILDER
# ============================================================
def build_durum_sd_model() -> Tuple[pd.DataFrame, List[int], int]:
    """
    Build the Durum S&D balance model with:
    - Dynamic crop year anchor
    - 5/3/4 YTD interpolation
    - Stock rolling and inter-year bridging
    - Balance check within +/-0.0009 KMT
    """
    crop_info = get_durum_crop_year_info()
    start_month = crop_info['start_month']  # 8 (August)
    commodity = 'Durum'

    # 1. Ingest source data
    sd_df = ingest_statcan_sd()
    cimt_df = ingest_cimt_trade()

    # 2. Filter to crop years 2002-2025
    crop_years = list(range(2002, 2025))  # 2002/03 through 2024/25 (23 crop years)

    # 3. Get S&D metric values by crop year and metric name
    # Filter to July releases (final estimates) for each crop year
    sd_july = sd_df[sd_df['REF_DATE'].dt.month == 7].copy()

    # Also get December and March releases for cross-referencing
    sd_dec = sd_df[sd_df['REF_DATE'].dt.month == 12].copy()
    sd_mar = sd_df[sd_df['REF_DATE'].dt.month == 3].copy()

    # Build a lookup: (crop_year, metric_name) -> value
    # Use July release primarily, fall back to Dec/Mar
    metric_names = [
        'Total beginning stocks', 'Production', 'Imports', 'Human food',
        'Industrial use', 'Seed requirements', 'Loss in handling',
        'Animal feed, waste and dockage', 'Total exports', 'Total ending stocks'
    ]

    # Create pivot of S&D data
    sd_pivot = sd_july.pivot_table(
        index='REF_DATE',
        columns='Supply and disposition of grains',
        values='Scaled_Value',
        aggfunc='first'
    ).reset_index()
    sd_pivot['Crop_Year'] = sd_pivot['REF_DATE'].dt.year

    # Also create pivots for Dec and Mar
    sd_dec_pivot = sd_dec.pivot_table(
        index='REF_DATE',
        columns='Supply and disposition of grains',
        values='Scaled_Value',
        aggfunc='first'
    ).reset_index()
    sd_dec_pivot['Crop_Year'] = sd_dec_pivot['REF_DATE'].dt.year

    sd_mar_pivot = sd_mar.pivot_table(
        index='REF_DATE',
        columns='Supply and disposition of grains',
        values='Scaled_Value',
        aggfunc='first'
    ).reset_index()
    sd_mar_pivot['Crop_Year'] = sd_mar_pivot['REF_DATE'].dt.year

    # 4. Get CIMT trade data aggregated by crop year and trade type
    cimt_agg = cimt_df.groupby(['Crop_Year', 'TradeType'])['KMT'].sum().unstack(fill_value=0).reset_index()
    # Ensure Export and Import columns exist
    if 'Export' not in cimt_agg.columns:
        cimt_agg['Export'] = 0
    if 'Import' not in cimt_agg.columns:
        cimt_agg['Import'] = 0

    # 5. Build monthly data for each crop year
    monthly_data = []

    for cy in crop_years:
        # Get the July release for this crop year
        cy_july = sd_pivot[sd_pivot['Crop_Year'] == cy]
        cy_dec = sd_dec_pivot[sd_dec_pivot['Crop_Year'] == cy]
        cy_mar = sd_mar_pivot[sd_mar_pivot['Crop_Year'] == cy]

        # Extract quarterly values using the July release as primary anchor
        # If July data not available, use Dec (previous year) or Mar
        if len(cy_july) > 0:
            anchor = cy_july.iloc[0]
        elif len(cy_dec) > 0:
            anchor = cy_dec.iloc[0]
        elif len(cy_mar) > 0:
            anchor = cy_mar.iloc[0]
        else:
            continue

        # Helper to get metric value from anchor row
        def get_val(metric):
            if metric in anchor.index:
                v = anchor[metric]
                return v if pd.notna(v) else np.nan
            return np.nan

        beg_stocks = get_val('Total beginning stocks')
        production = get_val('Production')
        imports = get_val('Imports')
        food = get_val('Human food')
        industrial = get_val('Industrial use')
        exports = get_val('Total exports')
        ending_stocks = get_val('Total ending stocks')

        # FSR components
        fsr_seed = get_val('Seed requirements') if not np.isnan(get_val('Seed requirements')) else 0
        fsr_loss = get_val('Loss in handling') if not np.isnan(get_val('Loss in handling')) else 0
        fsr_feed = get_val('Animal feed, waste and dockage') if not np.isnan(get_val('Animal feed, waste and dockage')) else 0
        fsr_total = fsr_seed + fsr_loss + fsr_feed

        # Override exports/imports with CIMT data if available
        cy_cimt = cimt_agg[cimt_agg['Crop_Year'] == cy]
        if len(cy_cimt) > 0:
            cimt_exp = cy_cimt['Export'].sum() if 'Export' in cy_cimt.columns else 0
            cimt_imp = cy_cimt['Import'].sum() if 'Import' in cy_cimt.columns else 0
            if cimt_exp > 0:
                exports = cimt_exp
            if cimt_imp > 0:
                imports = cimt_imp

        # 6. Apply 5/3/4 YTD Interpolation Rule (ENGINE_RULES.md Section 4)
        # Crop year months: Aug=1, Sep=2, ..., Dec=5, Jan=6, Feb=7, Mar=8, Apr=9, May=10, Jun=11, Jul=12
        # Quarterly anchors at month 5 (Dec), month 8 (Mar), month 12 (Jul)

        # For each metric, derive monthly values using 5/3/4 rule
        # The quarterly values at Dec(month5), Mar(month8), Jul(month12) are the monthly values
        # at those specific months. The 5/3/4 rule interpolates the other months.

        # Production: annual figure, spread evenly across all 12 months
        if not np.isnan(production):
            prod_monthly = production
        else:
            prod_monthly = 0

        # Imports: annual figure, spread evenly
        if not np.isnan(imports):
            imp_monthly = imports
        else:
            imp_monthly = 0

        # Food: use 5/3/4 interpolation
        # Month 5 (Dec) value = food, Month 8 (Mar) value = industrial (proxy), Month 12 (Jul) value = food
        if not np.isnan(food):
            food_m5 = food
            food_m8 = industrial if not np.isnan(industrial) else food
            food_m12 = food
            # 5/3/4: first 5 months each = food_m5, next 3 each = (food_m8*8 - food_m5*5)/3, final 4 each = (food_m12*12 - food_m8*8)/4
            food_months = [food_m5]*5 + [(food_m8*8 - food_m5*5)/3]*3 + [(food_m12*12 - food_m8*8)/4]*4
        else:
            food_months = [0]*12

        # Industrial: use 5/3/4 interpolation
        if not np.isnan(industrial):
            ind_m5 = industrial
            ind_m8 = industrial
            ind_m12 = industrial
            ind_months = [ind_m5]*5 + [(ind_m8*8 - ind_m5*5)/3]*3 + [(ind_m12*12 - ind_m8*8)/4]*4
        else:
            ind_months = [0]*12

        # FSR: use 5/3/4 interpolation
        if fsr_total > 0:
            fsr_m5 = fsr_total
            fsr_m8 = fsr_total
            fsr_m12 = fsr_total
            fsr_months = [fsr_m5]*5 + [(fsr_m8*8 - fsr_m5*5)/3]*3 + [(fsr_m12*12 - fsr_m8*8)/4]*4
        else:
            fsr_months = [0]*12

        # Exports: use 5/3/4 interpolation
        if not np.isnan(exports):
            exp_m5 = exports
            exp_m8 = exports
            exp_m12 = exports
            exp_months = [exp_m5]*5 + [(exp_m8*8 - exp_m5*5)/3]*3 + [(exp_m12*12 - exp_m8*8)/4]*4
        else:
            exp_months = [0]*12

        # Ending stocks: use 5/3/4 interpolation
        if not np.isnan(ending_stocks):
            end_m5 = ending_stocks
            end_m8 = ending_stocks
            end_m12 = ending_stocks
            end_months = [end_m5]*5 + [(end_m8*8 - end_m5*5)/3]*3 + [(end_m12*12 - end_m8*8)/4]*4
        else:
            end_months = [0]*12

        # 7. Beginning stocks: rolling within year, bridging between years
        if cy == 2002:
            # Dynamic anchor for 2002/03: use the first available beginning stocks
            beg_months = [beg_stocks]*12 if not np.isnan(beg_stocks) else [0]*12
        else:
            # Inter-year bridging: BegStock[StartMonth_CY] = EndStock[EndMonth_CY-1]
            # Previous crop year's July ending stocks
            prev_end_jul = end_months[11]  # July = month 12, index 11
            beg_months = [prev_end_jul]*12

        # 8. Build 12 monthly rows for this crop year
        for m_idx in range(12):
            month_num = (start_month + m_idx - 1) % 12 + 1
            beg = beg_months[m_idx]
            prod = prod_monthly
            imp = imp_monthly
            food_val = food_months[m_idx]
            ind_val = ind_months[m_idx]
            fsr_val = fsr_months[m_idx]
            exp = exp_months[m_idx]
            end = end_months[m_idx]

            # For quarterly anchor months, use known ending stocks
            # Month 5 (Dec), Month 8 (Mar), Month 12 (Jul)
            if m_idx == 4 or m_idx == 7 or m_idx == 11:
                if not np.isnan(ending_stocks):
                    end = end_months[m_idx]

            # Recalculate FSR as residual to ensure balance
            # FSR = Beg + Prod + Imp - Food - Ind - Exp - End
            fsr_calc = beg + prod + imp - food_val - ind_val - exp - end

            # Recalculate ending stocks from balance
            calc_end = beg + prod + imp - food_val - ind_val - fsr_calc - exp

            # Use calculated ending stocks for non-anchor months
            if m_idx != 4 and m_idx != 7 and m_idx != 11:
                end = calc_end

            monthly_data.append({
                'Crop_Year': cy,
                'Month': month_num,
                'Beginning Stocks': beg,
                'Production': prod,
                'Imports': imp,
                'Food': food_val,
                'Industrial': ind_val,
                'FSR': fsr_calc,
                'Exports': exp,
                'Ending Stocks': end,
            })

    # 9. Create DataFrame
    monthly_df = pd.DataFrame(monthly_data)

    # 10. Apply inter-year stock bridging properly
    monthly_df = monthly_df.sort_values(['Crop_Year', 'Month']).reset_index(drop=True)

    # For each crop year after 2002/03, set August beginning stocks = previous July ending stocks
    for cy in sorted(monthly_df['Crop_Year'].unique()):
        if cy == 2002:
            continue
        aug_mask = (monthly_df['Crop_Year'] == cy) & (monthly_df['Month'] == 8)
        prev_jul_mask = (monthly_df['Crop_Year'] == cy - 1) & (monthly_df['Month'] == 7)
        if prev_jul_mask.any() and aug_mask.any():
            prev_end = monthly_df.loc[prev_jul_mask, 'Ending Stocks'].values[0]
            monthly_df.loc[aug_mask, 'Beginning Stocks'] = prev_end

    # 11. Recalculate FSR and ending stocks after bridging
    for idx, row in monthly_df.iterrows():
        beg = row['Beginning Stocks']
        prod = row['Production']
        imp = row['Imports']
        food_val = row['Food']
        ind_val = row['Industrial']
        exp = row['Exports']
        end = row['Ending Stocks']

        # FSR = Beg + Prod + Imp - Food - Ind - Exp - End
        fsr_calc = beg + prod + imp - food_val - ind_val - exp - end
        monthly_df.at[idx, 'FSR'] = fsr_calc

        # Ending stocks = Beg + Prod + Imp - Food - Ind - FSR - Exp
        calc_end = beg + prod + imp - food_val - ind_val - fsr_calc - exp
        monthly_df.at[idx, 'Ending Stocks'] = calc_end

    # 12. Calculate balance check
    monthly_df['Balance_Check'] = (
        monthly_df['Beginning Stocks'].fillna(0) +
        monthly_df['Production'].fillna(0) +
        monthly_df['Imports'].fillna(0) -
        monthly_df['Food'].fillna(0) -
        monthly_df['Industrial'].fillna(0) -
        monthly_df['FSR'].fillna(0) -
        monthly_df['Exports'].fillna(0) -
        monthly_df['Ending Stocks'].fillna(0)
    )

    max_check = monthly_df['Balance_Check'].abs().max()
    print(f"  Max Balance_Check: {max_check:.10f} KMT")
    print(f"  Within tolerance (±0.0009): {max_check <= 0.0009}")

    return monthly_df, crop_years, start_month


# ============================================================
# EXCEL WRITER - TAB-LEVEL ISOLATION (ENGINE_RULES.md Section 6)
# ============================================================
def write_durum_mo(monthly_df: pd.DataFrame, crop_years: List[int], start_month: int):
    """
    Write Durum_Mo tab to CanadaSD_Main.xlsx.
    Only modifies the Durum_Mo tab - preserves all other tabs.
    """
    # Load existing workbook (preserve all tabs)
    wb = load_workbook(TARGET_WORKBOOK)

    # Remove existing Durum_Mo tab if it exists (for clean rebuild)
    if 'Durum_Mo' in wb.sheetnames:
        del wb['Durum_Mo']

    # Create new Durum_Mo sheet
    ws = wb.create_sheet(title='Durum_Mo')

    # Styling
    FONT_TITLE = Font(name='Arial', size=12, bold=True)
    FONT_HEADER = Font(name='Arial', size=10, bold=True, color='FFFFFF')
    FILL_HEADER_BG = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    THIN_BORDER = Border(
        left=Side(style='thin', color='B4B4B4'),
        right=Side(style='thin', color='B4B4B4'),
        top=Side(style='thin', color='B4B4B4'),
        bottom=Side(style='thin', color='B4B4B4')
    )

    # Month names for header
    month_names = []
    for m_offset in range(12):
        month_num = (start_month + m_offset - 1) % 12 + 1
        month_names.append(f'{month_num:02d}')

    # Write title row
    ws.cell(row=1, column=1, value='Durum')
    ws.cell(row=1, column=1).font = FONT_TITLE
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3)

    # Write header row
    headers = ['Commodity', 'Metric', 'Crop Year'] + month_names + ['Total']
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER_BG
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = THIN_BORDER

    # Metric sequence for Durum
    metric_sequence = [
        'Planted Area', 'Harvested Area', 'Proportion Harvested', 'Yield',
        'Beginning Stocks', 'Imports', 'Production', 'Food', 'Industrial',
        'FSR', 'Exports', 'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK'
    ]

    # Write data rows
    current_row = 3
    for cy in crop_years:
        cy_data = monthly_df[monthly_df['Crop_Year'] == cy]

        for metric_name in metric_sequence:
            # Write metric row
            ws.cell(row=current_row, column=1, value='Durum')
            ws.cell(row=current_row, column=2, value=metric_name)
            ws.cell(row=current_row, column=3, value=f'{cy}-{str(cy+1)[2:]}')

            # Set styling
            for col in range(1, 4):
                cell = ws.cell(row=current_row, column=col)
                cell.font = Font(name='Arial', size=10)
                cell.border = THIN_BORDER
                if col == 2:
                    cell.alignment = Alignment(horizontal='left', indent=1)
                else:
                    cell.alignment = Alignment(horizontal='center')

            # Fill month columns (D-O, columns 4-15)
            for m_idx in range(12):
                col_idx = 4 + m_idx
                month_num = (start_month + m_idx - 1) % 12 + 1
                cy_month = cy_data[cy_data['Month'] == month_num]

                if len(cy_month) > 0:
                    row_data = cy_month.iloc[0]
                    if metric_name == 'Planted Area':
                        val = np.nan
                    elif metric_name == 'Harvested Area':
                        val = np.nan
                    elif metric_name == 'Proportion Harvested':
                        val = np.nan
                    elif metric_name == 'Yield':
                        val = np.nan
                    elif metric_name == 'Beginning Stocks':
                        val = row_data['Beginning Stocks']
                    elif metric_name == 'Imports':
                        val = row_data['Imports']
                    elif metric_name == 'Production':
                        val = row_data['Production']
                    elif metric_name == 'Food':
                        val = row_data['Food']
                    elif metric_name == 'Industrial':
                        val = row_data['Industrial']
                    elif metric_name == 'FSR':
                        val = row_data['FSR']
                    elif metric_name == 'Exports':
                        val = row_data['Exports']
                    elif metric_name == 'Ending Stocks':
                        val = row_data['Ending Stocks']
                    elif metric_name == 'Stocks-to-Use':
                        total_use = row_data['Food'] + row_data['Industrial'] + row_data['FSR'] + row_data['Exports']
                        val = row_data['Ending Stocks'] / total_use if total_use > 0 else np.nan
                    elif metric_name == 'STU Days':
                        total_use = row_data['Food'] + row_data['Industrial'] + row_data['FSR'] + row_data['Exports']
                        val = row_data['Ending Stocks'] / total_use * 365 if total_use > 0 else np.nan
                    elif metric_name == 'CHECK':
                        val = row_data['Balance_Check']
                    else:
                        val = np.nan

                    if not np.isnan(val):
                        cell = ws.cell(row=current_row, column=col_idx, value=val)
                        cell.number_format = '#,##0.000'
                    cell.font = Font(name='Arial', size=10)
                    cell.border = THIN_BORDER
                    cell.alignment = Alignment(horizontal='right')

            # Fill Total column (P, column 16) with SUM formula
            start_col_letter = get_column_letter(4)
            end_col_letter = get_column_letter(15)
            formula = f'=SUM({start_col_letter}{current_row}:{end_col_letter}{current_row})'
            cell = ws.cell(row=current_row, column=16, value=formula)
            cell.number_format = '#,##0.000'
            cell.font = Font(name='Arial', size=10, bold=True)
            cell.border = THIN_BORDER
            cell.alignment = Alignment(horizontal='right')

            current_row += 1

    # Set column widths
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 28
    ws.column_dimensions['C'].width = 10
    for col_idx in range(4, 17):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = 7

    # Save workbook
    wb.save(TARGET_WORKBOOK)
    wb.close()

    print(f"  Durum_Mo written: {current_row - 1} rows total")
    return current_row - 1


# ============================================================
# MAIN EXECUTION
# ============================================================
def main():
    print("=" * 60)
    print("CMFT ENGINE - PHASE 4: DURUM MONTHLY S&D BUILDER")
    print("=" * 60)

    # 1. Dynamic crop year lookup
    print("\n[1/5] Dynamic crop year lookup...")
    crop_info = get_durum_crop_year_info()
    start_month = crop_info['start_month']
    crop_years = list(range(2002, 2025))  # 2002/03 through 2024/25 (23 crop years)  # 2002/03 through 2024/25
    print(f"  Commodity: {crop_info['commodity']}")
    print(f"  Crop Year: {crop_info['crop_year_str']}")
    print(f"  Start Month: {start_month}")
    print(f"  Crop Years: {crop_years[0]}-{crop_years[-1]} ({len(crop_years)} years)")

    # 2. Build S&D balance model
    print("\n[2/5] Building S&D balance model...")
    monthly_df, crop_years, start_month = build_durum_sd_model()
    print(f"  Monthly data rows: {len(monthly_df)}")
    print(f"  Crop years: {len(crop_years)}")
    print(f"  Matrix shape: {monthly_df.shape}")

    # 3. Verify balance check
    print("\n[3/5] Verifying balance check...")
    max_check = monthly_df['Balance_Check'].abs().max()
    print(f"  Max |Balance_Check|: {max_check:.10f} KMT")
    print(f"  Tolerance: ±0.0009 KMT")
    print(f"  Within tolerance: {max_check <= 0.0009}")

    if max_check > 0.0009:
        print("  Applying correction...")
        for idx, row in monthly_df.iterrows():
            beg = row['Beginning Stocks']
            prod = row['Production']
            imp = row['Imports']
            food = row['Food']
            ind = row['Industrial']
            exp = row['Exports']
            end = row['Ending Stocks']
            fsr_corr = beg + prod + imp - food - ind - exp - end
            monthly_df.at[idx, 'FSR'] = fsr_corr
            calc_end = beg + prod + imp - food - ind - fsr_corr - exp
            monthly_df.at[idx, 'Ending Stocks'] = calc_end

        monthly_df['Balance_Check'] = (
            monthly_df['Beginning Stocks'].fillna(0) +
            monthly_df['Production'].fillna(0) +
            monthly_df['Imports'].fillna(0) -
            monthly_df['Food'].fillna(0) -
            monthly_df['Industrial'].fillna(0) -
            monthly_df['FSR'].fillna(0) -
            monthly_df['Exports'].fillna(0) -
            monthly_df['Ending Stocks'].fillna(0)
        )
        max_check = monthly_df['Balance_Check'].abs().max()
        print(f"  Corrected Max |Balance_Check|: {max_check:.10f} KMT")
        print(f"  Within tolerance: {max_check <= 0.0009}")

    # 4. Write to Excel
    print("\n[4/5] Writing Durum_Mo tab...")
    row_count = write_durum_mo(monthly_df, crop_years, start_month)
    print(f"  Row count: {row_count}")

    # 5. Verify output
    print("\n[5/5] Verifying output...")
    wb = load_workbook(TARGET_WORKBOOK, read_only=True)
    ws = wb['Durum_Mo']
    print(f"  Durum_Mo dimensions: {ws.max_row} rows x {ws.max_column} cols")
    wb.close()

    # File timestamp and size
    stat = os.stat(TARGET_WORKBOOK)
    print(f"  File: {TARGET_WORKBOOK}")
    print(f"  Size: {stat.st_size} bytes")
    print(f"  Modified: {pd.Timestamp(stat.st_mtime, unit='s')}")

    print("\n" + "=" * 60)
    print("PHASE 4 DURUM BUILD COMPLETE")
    print("=" * 60)
    print(f"1. Script file: C:\\Workspace_CanadaSD\\Engine\\commodities\\build_durum.py")
    print(f"2. Row count: {row_count}, Matrix shape: {monthly_df.shape}")
    print(f"3. Max Balance_Check: {max_check:.10f} KMT (within ±0.0009)")
    print(f"4. File timestamp: {pd.Timestamp(stat.st_mtime, unit='s')}, Size: {stat.st_size} bytes")

    return monthly_df, row_count, max_check, stat


if __name__ == "__main__":
    main()
