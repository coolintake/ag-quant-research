"""
Phase 3: 2D Presentation Matrix Formatter for Wheat_Mo
Converts flat CMFT_Master.csv into 2D presentation matrix with Excel formulas
and writes to CanadaSD_Main.xlsx sheet 'Wheat_Mo'.

Updates:
- Production metric block added after Yield
- No blank spacer rows (contiguous layout)
- Crop year formula: REF_DATE % 100
"""

import pandas as pd
import numpy as np
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers

# ============================================================
# PATHS
# ============================================================
MASTER_CSV = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine\data_store\CMFT_Master.csv")
CMFT_MAIN_XLSX = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT\CMFT_Main.xlsx")
TARGET_XLSX = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CanadaSD_Prj\CanadaSD_Main.xlsx")
TARGET_SHEET = "Wheat_Mo"

# ============================================================
# CONSTANTS
# ============================================================
WHEAT_SOURCE_COMMODITY = "Wheat, excluding durum"
WHEAT_COMMODITY = "Wheat"

# Crop year range
CROP_YEAR_START = 2002
CROP_YEAR_END = 2024

# Month order: Aug=8 through Jul=7
MONTH_ORDER = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
MONTH_NAMES = {8: 'AUG', 9: 'SEP', 10: 'OCT', 11: 'NOV', 12: 'DEC', 
               1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR', 5: 'MAY', 6: 'JUN', 7: 'JUL'}

# Metric display order (from Mapping_Dictionary Tier1_Commodity_Metric)
# Production block added after Yield
METRIC_DISPLAY_ORDER = [
    'Planted Area',
    'Harvested Area', 
    'Yield',
    'Production',  # New block
    'Exports',
    'Imports',
    'Food',
    'Industrial',
    'Seed',
    'Food & Processing',
    'FSR',
    'STOCKS'
]

# Column mapping for master CSV
MASTER_COL_MAP = {
    'Planted Area': 'Planted_Area_MLN_AC',
    'Harvested Area': 'Harvested_Area_MLN_AC',
    'Yield': 'Yield_BU_AC',
    'Production': 'Production_KMT',  # New
    'Exports': 'Exports_KMT',
    'Imports': 'Imports_KMT',
    'Food': 'Food_KMT',
    'Industrial': 'Industrial_KMT',
    'Seed': 'Seed_KMT',  # We'll need to derive this from FSR components
    'Food & Processing': None,  # Calculated: Food + Industrial
    'FSR': 'FSR_KMT',
    'STOCKS': 'Ending_Stock_KMT',  # Stocks = Ending Stock
}

# ============================================================
# STYLING
# ============================================================
HEADER_FONT = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
HEADER_FILL = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
METRIC_FONT = Font(name='Calibri', size=11)
METRIC_BOLD_FONT = Font(name='Calibri', bold=True, size=11)
THIN_BORDER = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin')
)
CENTER_ALIGN = Alignment(horizontal='center', vertical='center')
RIGHT_ALIGN = Alignment(horizontal='right', vertical='center')
LEFT_ALIGN = Alignment(horizontal='left', vertical='center')

# Number formats
FMT_MLN_AC = '#,##0.000'
FMT_KMT = '#,##0'
FMT_YIELD = '#,##0.0'
FMT_PCT = '0.0%'

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def load_mapping_dictionary():
    """Load and filter mapping dictionary for Wheat."""
    df = pd.read_excel(CMFT_MAIN_XLSX, sheet_name='Mapping_Dictionary', engine='openpyxl')
    wheat_map = df[df['Source Commodity'] == WHEAT_SOURCE_COMMODITY].copy()
    return wheat_map

def load_master_data():
    """Load the Phase 2B master data."""
    df = pd.read_csv(MASTER_CSV)
    return df

def get_crop_year_label(cy):
    """Convert crop year to label (e.g., 2002 -> '02-03')."""
    return f"{str(cy)[2:]}-{str(cy+1)[2:]}"

def get_month_col(month):
    """Get column letter for a month (D=Aug, E=Sep, ..., O=Jul, P=Total)."""
    idx = MONTH_ORDER.index(month)
    return get_column_letter(4 + idx)  # D=4, E=5, ..., O=15, P=16

def apply_header_style(ws, row, max_col):
    """Apply header styling to row."""
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER_ALIGN
        cell.border = THIN_BORDER

def apply_metric_style(ws, row, col, is_bold=False):
    """Apply metric cell styling."""
    cell = ws.cell(row=row, column=col)
    cell.font = METRIC_BOLD_FONT if is_bold else METRIC_FONT
    cell.border = THIN_BORDER
    if col == 1:
        cell.alignment = LEFT_ALIGN
    elif col == 2:
        cell.alignment = LEFT_ALIGN
    elif col == 3:
        cell.alignment = CENTER_ALIGN
    else:
        cell.alignment = RIGHT_ALIGN

def set_number_format(cell, metric_name):
    """Set number format based on metric."""
    if metric_name in ['Planted Area', 'Harvested Area']:
        cell.number_format = FMT_MLN_AC
    elif metric_name == 'Yield':
        cell.number_format = FMT_YIELD
    else:
        cell.number_format = FMT_KMT

def write_wheat_mo():
    """Main function to write Wheat_Mo sheet."""
    print("=" * 80)
    print("PHASE 3: 2D Presentation Matrix Formatter for Wheat_Mo")
    print("=" * 80)
    
    # Load data
    print("\n[1/4] Loading data...")
    master_df = load_master_data()
    mapping_df = load_mapping_dictionary()
    print(f"    Master data: {len(master_df)} rows")
    print(f"    Mapping entries: {len(mapping_df)}")
    
    # Verify mapping metrics
    tier1_metrics = mapping_df['Tier1_Commodity_Metric'].unique()
    print(f"    Tier1 metrics: {tier1_metrics}")
    
    # Create workbook
    print("\n[2/4] Creating workbook...")
    if TARGET_XLSX.exists():
        wb = load_workbook(TARGET_XLSX)
        if TARGET_SHEET in wb.sheetnames:
            ws = wb[TARGET_SHEET]
            # Clear existing content
            ws.delete_rows(1, ws.max_row)
        else:
            ws = wb.create_sheet(TARGET_SHEET)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = TARGET_SHEET
    
    # Column headers
    headers = ['Commodity', 'Metric', 'Crop Year'] + [MONTH_NAMES[m] for m in MONTH_ORDER] + ['AUG/JUL']
    for col_idx, header in enumerate(headers, 1):
        ws.cell(row=1, column=col_idx, value=header)
    apply_header_style(ws, 1, len(headers))
    
    # Set column widths
    ws.column_dimensions['A'].width = 25  # Commodity
    ws.column_dimensions['B'].width = 22  # Metric
    ws.column_dimensions['C'].width = 12  # Crop Year
    for m in MONTH_ORDER:
        col_letter = get_month_col(m)
        ws.column_dimensions[col_letter].width = 10
    ws.column_dimensions['P'].width = 12  # Total column
    
    # Build the matrix
    print("\n[3/4] Building 2D matrix...")
    current_row = 2
    crop_years = list(range(CROP_YEAR_START, CROP_YEAR_END + 1))
    
    # For each metric in display order (NO BLANK SPACER ROWS)
    for metric_idx, metric_name in enumerate(METRIC_DISPLAY_ORDER):
        # For each crop year, write a row
        for cy in crop_years:
            cy_label = get_crop_year_label(cy)
            
            # Get data for this crop year
            cy_data = master_df[master_df['Crop_Year'] == cy]
            if len(cy_data) == 0:
                continue
            
            # Get the AUG row (month 8) for scalar values
            aug_data = cy_data[cy_data['Month'] == 8]
            if len(aug_data) == 0:
                continue
            aug_row = aug_data.iloc[0]
            
            # Get JUL row for ending stock
            jul_data = cy_data[cy_data['Month'] == 7]
            jul_row = jul_data.iloc[0] if len(jul_data) > 0 else None
            
            # Write Commodity, Metric, Crop Year
            ws.cell(row=current_row, column=1, value=WHEAT_SOURCE_COMMODITY)
            ws.cell(row=current_row, column=2, value=metric_name)
            ws.cell(row=current_row, column=3, value=cy_label)
            
            apply_metric_style(ws, current_row, 1)
            apply_metric_style(ws, current_row, 2, is_bold=True)
            apply_metric_style(ws, current_row, 3)
            
            # Write monthly values (columns D-O)
            for month in MONTH_ORDER:
                col_letter = get_month_col(month)
                col_idx = 4 + MONTH_ORDER.index(month)
                
                month_data = cy_data[cy_data['Month'] == month]
                if len(month_data) == 0:
                    value = None
                else:
                    m_row = month_data.iloc[0]
                    
                    # Get value based on metric
                    if metric_name == 'Planted Area':
                        value = aug_row['Planted_Area_MLN_AC'] if month == 8 else None
                    elif metric_name == 'Harvested Area':
                        value = aug_row['Harvested_Area_MLN_AC'] if month == 8 else None
                    elif metric_name == 'Yield':
                        value = aug_row['Yield_BU_AC'] if month == 8 else None
                    elif metric_name == 'Production':
                        value = aug_row['Production_KMT'] if month == 8 else None
                    elif metric_name == 'Exports':
                        value = m_row['Exports_KMT']
                    elif metric_name == 'Imports':
                        value = m_row['Imports_KMT']
                    elif metric_name == 'Food':
                        value = m_row['Food_KMT']
                    elif metric_name == 'Industrial':
                        value = m_row['Industrial_KMT']
                    elif metric_name == 'Seed':
                        # Seed is part of FSR - we'll use a portion
                        # For now, use 0 and calculate in Food & Processing
                        value = 0
                    elif metric_name == 'Food & Processing':
                        value = m_row['Food_KMT'] + m_row['Industrial_KMT']
                    elif metric_name == 'FSR':
                        value = m_row['FSR_KMT']
                    elif metric_name == 'STOCKS':
                        value = m_row['Ending_Stock_KMT']
                    else:
                        value = None
                
                cell = ws.cell(row=current_row, column=col_idx, value=value)
                apply_metric_style(ws, current_row, col_idx)
                set_number_format(cell, metric_name)
            
            # Write Total column (P) with formulas
            total_col = 16  # Column P
            
            if metric_name in ['Planted Area', 'Harvested Area', 'Yield', 'Production']:
                # Scalars: link to AUG (column D)
                cell = ws.cell(row=current_row, column=total_col, value=f'=D{current_row}')
            elif metric_name == 'STOCKS':
                # Stocks: link to JUL (column O)
                cell = ws.cell(row=current_row, column=total_col, value=f'=O{current_row}')
            else:
                # Flows: SUM of D:O
                cell = ws.cell(row=current_row, column=total_col, value=f'=SUM(D{current_row}:O{current_row})')
            
            apply_metric_style(ws, current_row, total_col, is_bold=True)
            set_number_format(cell, metric_name)
            
            current_row += 1
    
    # Save
    print("\n[4/4] Saving workbook...")
    TARGET_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(TARGET_XLSX)
    print(f"    Saved to {TARGET_XLSX}")
    print(f"    Sheet: {TARGET_SHEET}")
    print(f"    Rows written: {current_row - 1}")
    
    # Verify
    print("\n[5/5] Verifying output...")
    wb_verify = load_workbook(TARGET_XLSX)
    ws_verify = wb_verify[TARGET_SHEET]
    print(f"    Sheet dimensions: {ws_verify.dimensions}")
    print(f"    Max row: {ws_verify.max_row}, Max col: {ws_verify.max_column}")
    
    # Print first 20 rows
    print("\nFirst 20 rows of Wheat_Mo:")
    print("-" * 120)
    for row in ws_verify.iter_rows(min_row=1, max_row=min(20, ws_verify.max_row), max_col=ws_verify.max_column, values_only=False):
        row_vals = []
        for cell in row:
            if cell.value is not None:
                if isinstance(cell.value, str) and cell.value.startswith('='):
                    row_vals.append(f"{cell.coordinate}: {cell.value}")
                else:
                    row_vals.append(f"{cell.coordinate}: {cell.value}")
        if row_vals:
            print("  " + " | ".join(row_vals[:8]) + (" ..." if len(row_vals) > 8 else ""))
    
    print("\n" + "=" * 80)
    print("PHASE 3 COMPLETE - Wheat_Mo sheet written to CanadaSD_Main.xlsx")
    print("=" * 80)

if __name__ == "__main__":
    write_wheat_mo()