"""
CMFT Engine - Excel Exporter Module (REWRITTEN)
Reads CMFT_Master.csv, runs S&D mathematical audit, and exports paired
formula-linked *_Mo and *_Yr sheets to CanadaSD_main.xlsx.

New format:
- *_Mo tabs: 16 columns (A-P) with stacked crop years
  A: Commodity, B: Metric, C: Crop Year, D-O: 12 months, P: Total (SUM)
- *_Yr tabs: Crop year columns with formulas linking to *_Mo tabs
  A: Commodity, B: Metric, C: Unit, D+: Crop years (02-03, 03-04, etc.)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
import warnings
warnings.filterwarnings('ignore')

from config.paths import ENGINE_ROOT, DATA_STORE_DIR
from config.styles import (
    TIER1_COMMODITIES, CROP_YEAR_START_MONTH,
    FONT_DEFAULT, FONT_BOLD, FONT_HEADER, FONT_TITLE,
    FILL_WHITE, FILL_HEADER_BG, FILL_SECTION_BG, FILL_SUMMARY_BG,
    FILL_CHECK_OK, FILL_CHECK_WARN, FONT_CHECK_OK, FONT_CHECK_WARN,
    ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_METRIC,
    THIN_BORDER,
    FMT_MLN_AC, FMT_KMT, FMT_YIELD, FMT_PCT, FMT_DAYS, FMT_CHECK,
    COL_WIDTH_COMMODITY, COL_WIDTH_METRIC, COL_WIDTH_UNITS, COL_WIDTH_CROP_YEAR, COL_WIDTH_MONTH, COL_WIDTH_SUMMARY,
    apply_cell_style, apply_metric_row, apply_spacer_row, apply_check_row,
    set_column_widths_mo, set_column_widths_yr, apply_conditional_check_formatting,
    write_annual_template, write_monthly_template,
    ANNUAL_METRIC_SEQUENCE, MONTHLY_METRIC_SEQUENCE,
    METRIC_ORDER
)
from config.mappings import load_dictionary_mapping


# Commodity to group mapping from Mapping_Dictionary
COMMODITY_GROUP_MAP = {
    'Wheat': 'Cereal',
    'Durum': 'Cereal',
    'Oats': 'Cereal',
    'Barley': 'Cereal',
    'Canola': 'Oilseed',
    'Soybeans': 'Oilseed',
    'Dry peas': 'Pulse',
    'Lentils': 'Pulse',
}


def _filter_metric_sequence(sequence: List[Dict], commodity: str) -> List[Dict]:
    """Filter metric sequence to only include metrics for the commodity's group."""
    group = COMMODITY_GROUP_MAP.get(commodity, 'Cereal')
    filtered = []
    for m in sequence:
        metric_group = m.get('group', '')
        # Include if: spacer (no group), calculated (group='Calculated'), or matches commodity group
        if m['tier1_metric'] == '' or metric_group == 'Calculated' or metric_group == group:
            filtered.append(m)
    return filtered


# ============================================================
# TARGET WORKBOOK PATH
# ============================================================
TARGET_WORKBOOK = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CanadaSD_Prj\CanadaSD_main.xlsx")

# Date filter: August 1, 2002 onwards
FILTER_START_DATE = pd.Timestamp('2002-08-01')


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def load_master_data() -> pd.DataFrame:
    """Load and filter CMFT_Master.csv for Date >= 2002-08-01."""
    master_path = DATA_STORE_DIR / "CMFT_Master.csv"
    df = pd.read_csv(master_path)
    df['Date'] = pd.to_datetime(df['Date_Str'])
    df = df[df['Date'] >= FILTER_START_DATE].copy()
    df = df.sort_values(['Commodity_Norm', 'Date']).reset_index(drop=True)
    return df


def get_crop_years(df: pd.DataFrame) -> List[int]:
    """Get sorted list of crop years present in filtered data."""
    return sorted(df['Crop_Year'].unique())


def build_monthly_data_lookup(df_comm: pd.DataFrame, commodity: str) -> Dict[Tuple[int, int], Dict]:
    """
    Build a lookup dictionary for monthly data values.
    Key: (crop_year, month_num) -> dict of metric values
    """
    df_comm = df_comm.copy()
    df_comm['Month_Num'] = df_comm['Date'].dt.month
    df_comm['Crop_Year'] = df_comm['Crop_Year'].astype(int)
    
    data_lookup = {}
    for _, row in df_comm.iterrows():
        cy = row['Crop_Year']
        month = row['Month_Num']
        key = (cy, month)
        
        beg = row['Beginning Stocks'] if pd.notna(row['Beginning Stocks']) else 0
        prod = row['Production'] if pd.notna(row['Production']) else 0
        imp = row['Imports'] if pd.notna(row['Imports']) else 0
        food = row['Food'] if pd.notna(row['Food']) else 0
        ind = row['Industrial'] if pd.notna(row['Industrial']) else 0
        fsr = row['FSR'] if pd.notna(row['FSR']) else 0
        exp = row['Exports'] if pd.notna(row['Exports']) else 0
        end = row['Ending Stocks'] if pd.notna(row['Ending Stocks']) else 0
        yield_bu = row['Yield_BU_AC'] if pd.notna(row['Yield_BU_AC']) else np.nan
        harvested = row['Harvested_MLN_AC'] if pd.notna(row['Harvested_MLN_AC']) else np.nan
        
        total_supply = beg + prod + imp
        total_dom_use = food + ind + fsr
        total_use = total_dom_use + exp
        stu_ratio = end / total_use if total_use > 0 else np.nan
        days_supply = (end / total_use * 365) if total_use > 0 else np.nan
        check_val = beg + prod + imp - food - ind - fsr - exp - end
        
        data_lookup[key] = {
            'Seeded Area': np.nan,
            'Harvested Area': harvested,
            'Yield': yield_bu,
            'Beginning Stocks': beg,
            'Production': prod,
            'Imports': imp,
            'Total Supply': total_supply,
            'Food': food,
            'Industrial': ind,
            'FSR': fsr,
            'Total Domestic Use': total_dom_use,
            'Exports': exp,
            'Total Use': total_use,
            'Ending Stocks': end,
            'Stocks-to-Use': stu_ratio,
            'STU Days': days_supply,
            'CHECK': check_val,
        }
    
    return data_lookup


def write_monthly_tab(ws, df_comm: pd.DataFrame, commodity: str, crop_years: List[int],
                      mo_sheet_name: str, yr_sheet_name: str):
    """
    Write monthly tab (*_Mo) with 16-column stacked crop-year format:
    A: Commodity, B: Metric, C: Crop Year, D-O: 12 months, P: Total
    """
    start_month = CROP_YEAR_START_MONTH.get(commodity, 8)
    data_lookup = build_monthly_data_lookup(df_comm, commodity)
    
    # Filter metric sequence for this commodity's group
    filtered_monthly_seq = _filter_metric_sequence(MONTHLY_METRIC_SEQUENCE, commodity)
    
    # Write template using styles.py function (but with filtered sequence)
    # We'll write the template manually to use filtered sequence
    current_row = 1
    
    # Title row
    ws.cell(row=current_row, column=1, value=commodity)
    ws.cell(row=current_row, column=1).font = FONT_TITLE
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=3)
    current_row += 1
    
    # Column headers
    month_names = []
    for m_offset in range(12):
        month_num = (start_month + m_offset - 1) % 12 + 1
        month_names.append(f'{month_num:02d}')
    
    headers = ['Commodity', 'Metric', 'Crop Year'] + month_names + ['Total']
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=current_row, column=col_idx, value=header)
        apply_cell_style(cell, font=FONT_HEADER, fill=FILL_HEADER_BG, alignment=ALIGN_CENTER)
    current_row += 1
    
    # Write metric rows for each crop year (stacked) using filtered sequence
    for cy in crop_years:
        for metric_info in filtered_monthly_seq:
            metric_name = metric_info['tier1_metric']
            
            if metric_name == '':
                apply_spacer_row(ws, current_row, len(headers))
            elif metric_name == 'CHECK':
                apply_check_row(ws, current_row, len(headers))
            else:
                unit = metric_info['unit']
                fmt = metric_info['fmt']
                is_calculated = metric_info.get('is_calculated', False)
                is_total = metric_name in ('Total Supply', 'Total Disposition')
                apply_metric_row(ws, current_row, len(headers), metric_name, unit, fmt, 
                               is_total=is_total, is_calculated=is_calculated)
            
            # Fill Crop Year column (C)
            ws.cell(row=current_row, column=3, value=f'{cy}-{str(cy+1)[2:]}')
            ws.cell(row=current_row, column=3).font = FONT_DEFAULT
            ws.cell(row=current_row, column=3).fill = FILL_WHITE
            ws.cell(row=current_row, column=3).alignment = ALIGN_CENTER
            ws.cell(row=current_row, column=3).border = THIN_BORDER
            
            # Fill Commodity column (A)
            ws.cell(row=current_row, column=1, value=commodity)
            ws.cell(row=current_row, column=1).font = FONT_DEFAULT
            ws.cell(row=current_row, column=1).fill = FILL_WHITE
            ws.cell(row=current_row, column=1).alignment = ALIGN_CENTER
            ws.cell(row=current_row, column=1).border = THIN_BORDER
            
            current_row += 1
    
    last_row = current_row - 1
    
    # Now fill in the data values for each row
    current_row = 3  # After title and header rows
    for cy in crop_years:
        for metric_info in filtered_monthly_seq:
            metric_name = metric_info['tier1_metric']
            
            if metric_name == '':
                current_row += 1
                continue
            
            unit = metric_info['unit']
            fmt = metric_info['fmt']
            is_calculated = metric_info.get('is_calculated', False)
            
            # Fill month columns (D-O, columns 4-15)
            for m_offset in range(12):
                month_num = (start_month + m_offset - 1) % 12 + 1
                col_idx = 4 + m_offset
                key = (cy, month_num)
                
                if key in data_lookup:
                    val = data_lookup[key].get(metric_name, np.nan)
                    if pd.notna(val):
                        cell = ws.cell(row=current_row, column=col_idx, value=val)
                        cell.number_format = fmt
            
            # Fill Total column (P, column 16) with SUM formula
            total_col = 16
            start_col_letter = get_column_letter(4)
            end_col_letter = get_column_letter(15)
            formula = f'=SUM({start_col_letter}{current_row}:{end_col_letter}{current_row})'
            cell = ws.cell(row=current_row, column=total_col, value=formula)
            cell.number_format = fmt
            apply_cell_style(cell, font=FONT_BOLD, fill=FILL_SUMMARY_BG, alignment=ALIGN_RIGHT)
            
            current_row += 1
    
    # Apply column widths
    set_column_widths_mo(ws, 16)
    
    # Apply conditional formatting to CHECK rows
    for row in range(3, last_row + 1):
        cell_b = ws.cell(row=row, column=2)
        if cell_b.value and str(cell_b.value).strip() == 'CHECK':
            apply_conditional_check_formatting(ws, row, 16)
    
    return last_row


def write_annual_tab(ws, commodity: str, crop_years: List[int], mo_sheet_name: str):
    """
    Write annual tab (*_Yr) with formula links to *_Mo tab.
    Columns: A: Commodity, B: Metric, C: Unit, D+: Crop years
    """
    # Filter metric sequences for this commodity's group
    filtered_annual_seq = _filter_metric_sequence(ANNUAL_METRIC_SEQUENCE, commodity)
    filtered_monthly_seq = _filter_metric_sequence(MONTHLY_METRIC_SEQUENCE, commodity)
    
    # Write template manually using filtered sequence
    current_row = 1
    
    # Title row
    ws.cell(row=current_row, column=1, value=commodity)
    ws.cell(row=current_row, column=1).font = FONT_TITLE
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=3)
    current_row += 1
    
    # Column headers: Commodity, Metric, Unit, then crop years
    headers = ['Commodity', 'Metric', 'Unit'] + [f'{cy}-{str(cy+1)[2:]}' for cy in crop_years]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=current_row, column=col_idx, value=header)
        apply_cell_style(cell, font=FONT_HEADER, fill=FILL_HEADER_BG, alignment=ALIGN_CENTER)
    current_row += 1
    
    # Write metric rows following filtered ANNUAL_METRIC_SEQUENCE
    check_row = None
    for metric_info in filtered_annual_seq:
        metric_name = metric_info['tier1_metric']
        
        if metric_name == '':
            apply_spacer_row(ws, current_row, len(headers))
        elif metric_name == 'CHECK':
            check_row = current_row
            apply_check_row(ws, current_row, len(headers))
        else:
            unit = metric_info['unit']
            fmt = metric_info['fmt']
            is_calculated = metric_info.get('is_calculated', False)
            is_total = metric_name in ('Total Supply', 'Total Disposition')
            apply_metric_row(ws, current_row, len(headers), metric_name, unit, fmt, 
                           is_total=is_total, is_calculated=is_calculated)
        
        # Fill Commodity column (A)
        ws.cell(row=current_row, column=1, value=commodity)
        ws.cell(row=current_row, column=1).font = FONT_DEFAULT
        ws.cell(row=current_row, column=1).fill = FILL_WHITE
        ws.cell(row=current_row, column=1).alignment = ALIGN_CENTER
        ws.cell(row=current_row, column=1).border = THIN_BORDER
        
        current_row += 1
    
    last_row = current_row - 1
    
    # Now fill in formulas linking to monthly tab
    # Build a map of metric_name -> row in annual tab
    annual_metric_rows = {}
    for row in range(3, last_row + 1):  # Skip title and header rows
        cell_b = ws.cell(row=row, column=2)
        if cell_b.value:
            metric_name = str(cell_b.value).strip()
            if metric_name:
                annual_metric_rows[metric_name] = row
    
    # For each crop year column (D onwards), write formulas
    for cy_idx, cy in enumerate(crop_years):
        col_idx = 4 + cy_idx  # Column D onwards
        col_letter = get_column_letter(col_idx)
        
        # In monthly tab, each crop year has filtered_monthly_seq metrics (excluding spacers)
        metrics_per_cy = len([m for m in filtered_monthly_seq if m['tier1_metric'] != ''])
        mo_base_row = 3 + (cy_idx * metrics_per_cy)
        
        # Build metric name to monthly row offset mapping
        mo_metric_offsets = {}
        offset = 0
        for metric_info in filtered_monthly_seq:
            metric_name = metric_info['tier1_metric']
            if metric_name == '':
                offset += 1
                continue
            mo_metric_offsets[metric_name] = offset
            offset += 1
        
        for metric_name, annual_row in annual_metric_rows.items():
            if metric_name == 'CHECK':
                # CHECK formula: Beg + Prod + Imp - Food - Ind - FSR - Exp - End
                beg_row = annual_metric_rows.get('Beginning Stocks')
                prod_row = annual_metric_rows.get('Production')
                imp_row = annual_metric_rows.get('Imports')
                food_row = annual_metric_rows.get('Food')
                ind_row = annual_metric_rows.get('Industrial')
                fsr_row = annual_metric_rows.get('FSR')
                exp_row = annual_metric_rows.get('Exports')
                end_row = annual_metric_rows.get('Ending Stocks')
                
                if all(r is not None for r in [beg_row, prod_row, imp_row, food_row, ind_row, fsr_row, exp_row, end_row]):
                    formula = (f'={col_letter}{beg_row}+{col_letter}{prod_row}+{col_letter}{imp_row}'
                               f'-{col_letter}{food_row}-{col_letter}{ind_row}-{col_letter}{fsr_row}'
                               f'-{col_letter}{exp_row}-{col_letter}{end_row}')
                    cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                    apply_cell_style(cell, font=FONT_BOLD, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                                   number_format=FMT_CHECK, border=THIN_BORDER)
            elif metric_name == 'Stocks-to-Use':
                # STU Ratio = Ending Stocks / Total Use
                end_row = annual_metric_rows.get('Ending Stocks')
                use_row = annual_metric_rows.get('Total Use')
                if end_row and use_row:
                    formula = f'=IF({col_letter}{use_row}<>0,{col_letter}{end_row}/{col_letter}{use_row},"")'
                    cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                    apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                                   number_format=FMT_PCT, border=THIN_BORDER)
            elif metric_name == 'STU Days':
                # Days of Supply = Ending Stocks / Total Use * 365
                end_row = annual_metric_rows.get('Ending Stocks')
                use_row = annual_metric_rows.get('Total Use')
                if end_row and use_row:
                    formula = f'=IF({col_letter}{use_row}<>0,{col_letter}{end_row}/{col_letter}{use_row}*365,"")'
                    cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                    apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                                   number_format=FMT_DAYS, border=THIN_BORDER)
            elif metric_name == 'Proportion Harvested':
                # Proportion Harvested = Harvested Area / Planted Area
                harv_row = annual_metric_rows.get('Harvested Area')
                plant_row = annual_metric_rows.get('Planted Area')
                if harv_row and plant_row:
                    formula = f'=IF({col_letter}{plant_row}<>0,{col_letter}{harv_row}/{col_letter}{plant_row},"")'
                    cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                    apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                                   number_format=FMT_PCT, border=THIN_BORDER)
            elif metric_name == 'Yield':
                # Yield - link to monthly tab harvest month (last month of crop year)
                mo_row_offset = mo_metric_offsets.get('Yield', 2)
                mo_row = mo_base_row + mo_row_offset
                mo_col = 4 + 11  # Column O (July for Aug-start, August for Sep-start)
                mo_col_letter = get_column_letter(mo_col)
                formula = f'=\'{mo_sheet_name}\'!{mo_col_letter}{mo_row}'
                cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                               number_format=FMT_YIELD, border=THIN_BORDER)
            elif metric_name in ('Total Supply', 'Total Disposition'):
                # These are sums - link to monthly tab Total column (P)
                mo_row_offset = mo_metric_offsets.get(metric_name, 0)
                mo_row = mo_base_row + mo_row_offset
                mo_col_letter = get_column_letter(16)  # Column P = Total
                formula = f'=\'{mo_sheet_name}\'!{mo_col_letter}{mo_row}'
                cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                apply_cell_style(cell, font=FONT_BOLD, fill=FILL_SUMMARY_BG, alignment=ALIGN_RIGHT, 
                               number_format=FMT_KMT, border=THIN_BORDER)
            else:
                # Direct link to monthly tab Total column (P) for annual total
                mo_row_offset = mo_metric_offsets.get(metric_name, 0)
                mo_row = mo_base_row + mo_row_offset
                mo_col_letter = get_column_letter(16)  # Column P = Total
                formula = f'=\'{mo_sheet_name}\'!{mo_col_letter}{mo_row}'
                cell = ws.cell(row=annual_row, column=col_idx, value=formula)
                apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                               number_format=FMT_KMT, border=THIN_BORDER)
    
    # Apply column widths
    set_column_widths_yr(ws, len(crop_years) + 3)
    
    # Apply conditional formatting to CHECK row
    if check_row:
        apply_conditional_check_formatting(ws, check_row, len(crop_years) + 3)
    
    return last_row


def run_exporter():
    """Main entry point for Excel exporter."""
    print("=" * 60)
    print("CMFT ENGINE - EXCEL EXPORTER (NEW FORMAT)")
    print("=" * 60)
    
    # Load data
    print("\n[1/4] Loading CMFT_Master.csv...")
    df = load_master_data()
    print(f"    Loaded {len(df)} rows from {FILTER_START_DATE.date()} onwards")
    
    crop_years = get_crop_years(df)
    print(f"    Crop years: {crop_years[0]}-{crop_years[-1]} ({len(crop_years)} years)")
    
    # Create workbook
    print("\n[2/4] Creating workbook...")
    wb = Workbook()
    
    # Remove default sheet
    default_ws = wb.active
    wb.remove(default_ws)
    
    # Process each Tier 1 commodity
    print("\n[3/4] Building paired tabs...")
    for commodity in TIER1_COMMODITIES:
        if commodity not in df['Commodity_Norm'].values:
            print(f"    Skipping {commodity} (no data)")
            continue
        
        print(f"    Processing {commodity}...")
        df_comm = df[df['Commodity_Norm'] == commodity].copy()
        
        mo_sheet_name = f"{commodity}_Mo"
        yr_sheet_name = f"{commodity}_Yr"
        
        # Create monthly tab
        ws_mo = wb.create_sheet(title=mo_sheet_name)
        write_monthly_tab(ws_mo, df_comm, commodity, crop_years, mo_sheet_name, yr_sheet_name)
        
        # Create annual tab
        ws_yr = wb.create_sheet(title=yr_sheet_name)
        write_annual_tab(ws_yr, commodity, crop_years, mo_sheet_name)
        
        print(f"      {mo_sheet_name}: 16 columns (A-P) x {len(crop_years)} crop years stacked")
        print(f"      {yr_sheet_name}: {len(crop_years)} crop year columns with formulas")
    
    # Save workbook
    print("\n[4/4] Saving workbook...")
    TARGET_WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
    wb.save(TARGET_WORKBOOK)
    print(f"    Saved to: {TARGET_WORKBOOK}")
    print(f"    Sheets created: {wb.sheetnames}")
    
    # Verify formulas
    print("\n[5/5] Verifying formula links...")
    verify_formulas(wb)
    
    print("\n" + "=" * 60)
    print("EXCEL EXPORT COMPLETE")
    print("=" * 60)
    return wb


def verify_formulas(wb: Workbook):
    """Verify that annual tabs have formula links to monthly tabs."""
    mo_sheets = [s for s in wb.sheetnames if s.endswith('_Mo')]
    yr_sheets = [s for s in wb.sheetnames if s.endswith('_Yr')]
    
    print(f"    Monthly tabs: {len(mo_sheets)}")
    print(f"    Annual tabs: {len(yr_sheets)}")
    
    for yr_sheet in yr_sheets:
        ws = wb[yr_sheet]
        formula_count = 0
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
            for cell in row:
                if cell.value and isinstance(cell.value, str) and cell.value.startswith('='):
                    formula_count += 1
        print(f"      {yr_sheet}: {formula_count} formula cells")


if __name__ == "__main__":
    try:
        wb = run_exporter()
    except Exception as e:
        print(f"\n[FAIL] Exporter failed: {e}")
        import traceback
        traceback.print_exc()
        raise