"""
CMFT Engine - Excel Styling Module
Defines reusable openpyxl styles for CanadaSD workbook presentation layer.
Loads metric order dynamically from Mapping_Dictionary in CMFT_Main.xlsx.
"""

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from config.mappings import load_dictionary_mapping
from config.paths import CMFT_MAIN_XLSX
from typing import Dict, List, Tuple
import pandas as pd


# ============================================================
# FONT DEFINITIONS
# ============================================================
FONT_DEFAULT = Font(name='Arial', size=10, color='000000')
FONT_BOLD = Font(name='Arial', size=10, bold=True, color='000000')
FONT_HEADER = Font(name='Arial', size=10, bold=True, color='FFFFFF')
FONT_TITLE = Font(name='Arial', size=12, bold=True, color='000000')
FONT_CHECK_OK = Font(name='Arial', size=10, color='006100')  # Dark green
FONT_CHECK_WARN = Font(name='Arial', size=10, bold=True, color='9C0006')  # Dark red


# ============================================================
# FILL DEFINITIONS
# ============================================================
FILL_WHITE = PatternFill(start_color='FFFFFF', end_color='FFFFFF', fill_type='solid')
FILL_LIGHT_GRAY = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
FILL_DARK_GRAY = PatternFill(start_color='D9D9D9', end_color='D9D9D9', fill_type='solid')
FILL_HEADER_BG = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')  # Blue header
FILL_SECTION_BG = PatternFill(start_color='D6E4F0', end_color='D6E4F0', fill_type='solid')  # Light blue section
FILL_SUMMARY_BG = PatternFill(start_color='E2EFDA', end_color='E2EFDA', fill_type='solid')  # Light green summary
FILL_CHECK_OK = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')  # Green check
FILL_CHECK_WARN = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')  # Red check


# ============================================================
# ALIGNMENT DEFINITIONS
# ============================================================
ALIGN_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
ALIGN_LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal='right', vertical='center', wrap_text=True)
ALIGN_METRIC = Alignment(horizontal='left', vertical='center', indent=1)


# ============================================================
# BORDER DEFINITIONS
# ============================================================
THIN_BORDER = Border(
    left=Side(style='thin', color='B4B4B4'),
    right=Side(style='thin', color='B4B4B4'),
    top=Side(style='thin', color='B4B4B4'),
    bottom=Side(style='thin', color='B4B4B4')
)
BOTTOM_BORDER = Border(bottom=Side(style='medium', color='4472C4'))
TOP_BORDER = Border(top=Side(style='medium', color='4472C4'))
SECTION_BORDER = Border(
    left=Side(style='thin', color='4472C4'),
    right=Side(style='thin', color='4472C4'),
    top=Side(style='thin', color='4472C4'),
    bottom=Side(style='thin', color='4472C4')
)


# ============================================================
# NUMBER FORMATS
# ============================================================
FMT_MLN_AC = '0.000'           # Million Acres
FMT_KMT = '#,##0'              # Thousand Metric Tonnes
FMT_YIELD = '0.0'              # Bushels per Acre
FMT_PCT = '0.0%'               # Percentages
FMT_DAYS = '#,##0'             # Days
FMT_CHECK = '#,##0'            # Check row (integer)
FMT_GENERAL = 'General'


# ============================================================
# COLUMN WIDTHS
# ============================================================
COL_WIDTH_COMMODITY = 18       # Column A: Commodity
COL_WIDTH_METRIC = 28          # Column B: Metric name
COL_WIDTH_UNITS = 10           # Column C: Units
COL_WIDTH_CROP_YEAR = 7        # Crop year columns (D onwards)
COL_WIDTH_MONTH = 7            # Month columns (D-O)
COL_WIDTH_SUMMARY = 10         # Summary column (P)


# ============================================================
# COMMODITY CONFIG - re-exported from config/commodities.py
# (single source of truth; do not redefine here)
# ============================================================
from config.commodities import CROP_YEAR_START_MONTH, TIER1_COMMODITIES  # noqa: E402


# ============================================================
# DYNAMIC METRIC ORDER FROM MAPPING DICTIONARY
# ============================================================

def _load_metric_order_from_mapping() -> Dict[str, List[Dict]]:
    """
    Load metric order and structure from Mapping_Dictionary sheet.
    Returns dict with commodity_group -> list of metric dicts with:
    - tier1_metric: Tier1_Commodity_Metric
    - tier2_metric: Tier2_National_Metric
    - statcan_metric: StatCan_Raw_Metric
    - unit: inferred unit
    - fmt: number format
    - block: block number (1=Acreage, 2=Supply, 3=Disposition, 4=Ending)
    """
    mapping_df = load_dictionary_mapping()
    
    # Define block assignments based on ACTUAL Tier2_National_Metric values
    block_map = {
        'Beginning Stocks': 2,      # Supply
        'Production': 2,            # Supply
        'Imports': 2,               # Supply
        'Food & Processing': 3,     # Disposition
        'FSR': 3,                   # Disposition
        'Exports': 3,               # Disposition
        'Ending Stocks': 4,         # Ending Stocks
        'Feed & Residual': 3,       # Disposition
    }
    
    # Unit and format mapping by Tier2
    unit_fmt_map = {
        'Beginning Stocks': ('KMT', FMT_KMT),
        'Production': ('KMT', FMT_KMT),
        'Imports': ('KMT', FMT_KMT),
        'Food & Processing': ('KMT', FMT_KMT),
        'FSR': ('KMT', FMT_KMT),
        'Exports': ('KMT', FMT_KMT),
        'Ending Stocks': ('KMT', FMT_KMT),
        'Feed & Residual': ('KMT', FMT_KMT),
    }
    
    # Special cases for specific metrics
    special_metrics = {
        'Proportion Harvested': ('%', FMT_PCT),
        'Yield': ('BU/AC', FMT_YIELD),
        'Stocks-to-Use': ('%', FMT_PCT),
        'STU Days': ('Days', FMT_DAYS),
        'CHECK': ('KMT', FMT_CHECK),
    }
    
    metric_order = {}
    
    for group in ['Cereal', 'Oilseed', 'Vegetable Oil', 'Protein Meal', 'Pulse']:
        group_df = mapping_df[mapping_df['Commodity_Group'] == group].copy()
        if len(group_df) == 0:
            continue
        
        # Get unique Tier1 metrics preserving order of first appearance
        seen = set()
        ordered = []
        for _, row in group_df.iterrows():
            tier1 = row['Tier1_Commodity_Metric']
            tier2 = row['Tier2_National_Metric']
            statcan = row['StatCan_Raw_Metric']
            
            if tier1 not in seen and pd.notna(tier1):
                seen.add(tier1)
                
                # Determine block
                block = block_map.get(tier2, 0)
                
                # Determine unit and format
                if tier1 in special_metrics:
                    unit, fmt = special_metrics[tier1]
                else:
                    unit, fmt = unit_fmt_map.get(tier2, ('KMT', FMT_KMT))
                
                ordered.append({
                    'tier1_metric': tier1,
                    'tier2_metric': tier2,
                    'statcan_metric': statcan,
                    'unit': unit,
                    'fmt': fmt,
                    'block': block,
                })
        
        metric_order[group] = ordered
    
    return metric_order


# Load metric order at module import
METRIC_ORDER = _load_metric_order_from_mapping()


# Build flat list of all Tier1 metrics in order for annual tabs
def _build_annual_metric_sequence() -> List[Dict]:
    """Build the complete metric sequence for annual tabs following Mapping_Dictionary order."""
    sequence = []
    
    # Block 1: Acreage & Yield (for Cereals, Oilseeds, and Pulses)
    # These are calculated from StatCan planting data, not in Mapping_Dictionary
    for group in ['Cereal', 'Oilseed', 'Pulse']:
        sequence.append({
            'tier1_metric': 'Planted Area',
            'tier2_metric': 'Acreage & Yield',
            'statcan_metric': None,
            'unit': 'MLN AC',
            'fmt': FMT_MLN_AC,
            'block': 1,
            'group': group,
            'is_calculated': True,
            'calc_type': 'planted_area',
        })
        sequence.append({
            'tier1_metric': 'Harvested Area',
            'tier2_metric': 'Acreage & Yield',
            'statcan_metric': None,
            'unit': 'MLN AC',
            'fmt': FMT_MLN_AC,
            'block': 1,
            'group': group,
            'is_calculated': True,
            'calc_type': 'harvested_area',
        })
    
    # Add Proportion Harvested and Yield as calculated rows
    for group in ['Cereal', 'Oilseed', 'Pulse']:
        sequence.append({
            'tier1_metric': 'Proportion Harvested',
            'tier2_metric': 'Acreage & Yield',
            'statcan_metric': None,
            'unit': '%',
            'fmt': FMT_PCT,
            'block': 1,
            'group': group,
            'is_calculated': True,
            'calc_type': 'proportion_harvested',
        })
        sequence.append({
            'tier1_metric': 'Yield',
            'tier2_metric': 'Acreage & Yield',
            'statcan_metric': None,
            'unit': 'BU/AC',
            'fmt': FMT_YIELD,
            'block': 1,
            'group': group,
            'is_calculated': True,
            'calc_type': 'yield',
        })
    
    # Spacer
    sequence.append({'tier1_metric': '', 'block': 0})
    
    # Block 2: Supply
    for group in ['Cereal', 'Oilseed', 'Pulse']:
        for m in METRIC_ORDER.get(group, []):
            if m['block'] == 2:
                sequence.append({**m, 'group': group})
    
    # Spacer
    sequence.append({'tier1_metric': '', 'block': 0})
    
    # Block 3: Disposition
    for group in ['Cereal', 'Oilseed', 'Pulse']:
        for m in METRIC_ORDER.get(group, []):
            if m['block'] == 3:
                sequence.append({**m, 'group': group})
    
    # Spacer
    sequence.append({'tier1_metric': '', 'block': 0})
    
    # Block 4: Ending Stocks & Metrics
    for group in ['Cereal', 'Oilseed', 'Pulse']:
        for m in METRIC_ORDER.get(group, []):
            if m['block'] == 4:
                sequence.append({**m, 'group': group})
    
    # Add calculated metrics for Block 4
    sequence.append({
        'tier1_metric': 'Stocks-to-Use',
        'tier2_metric': 'Ending Stocks',
        'statcan_metric': None,
        'unit': '%',
        'fmt': FMT_PCT,
        'block': 4,
        'group': 'Calculated',
        'is_calculated': True,
        'calc_type': 'stu_ratio',
    })
    sequence.append({
        'tier1_metric': 'STU Days',
        'tier2_metric': 'Ending Stocks',
        'statcan_metric': None,
        'unit': 'Days',
        'fmt': FMT_DAYS,
        'block': 4,
        'group': 'Calculated',
        'is_calculated': True,
        'calc_type': 'stu_days',
    })
    sequence.append({
        'tier1_metric': 'CHECK',
        'tier2_metric': 'Ending Stocks',
        'statcan_metric': None,
        'unit': 'KMT',
        'fmt': FMT_CHECK,
        'block': 4,
        'group': 'Calculated',
        'is_calculated': True,
        'calc_type': 'check',
    })
    
    return sequence


ANNUAL_METRIC_SEQUENCE = _build_annual_metric_sequence()


# Monthly tab metric sequence (same as annual but with monthly columns)
MONTHLY_METRIC_SEQUENCE = ANNUAL_METRIC_SEQUENCE


# ============================================================
# STYLING FUNCTIONS
# ============================================================

def apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_CENTER, 
                     border=THIN_BORDER, number_format=None):
    """Apply complete style to a single cell."""
    cell.font = font
    cell.fill = fill
    cell.alignment = alignment
    cell.border = border
    if number_format:
        cell.number_format = number_format


def apply_header_row(ws, row, max_col, title=None):
    """Apply header styling to a row."""
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        apply_cell_style(cell, font=FONT_HEADER, fill=FILL_HEADER_BG, alignment=ALIGN_CENTER)
    if title:
        ws.cell(row=row, column=1, value=title)
        ws.cell(row=row, column=1).font = FONT_TITLE
        ws.cell(row=row, column=1).fill = FILL_WHITE
        ws.cell(row=row, column=1).border = THIN_BORDER


def apply_metric_row(ws, row, max_col, metric_name, unit, number_format, is_total=False, is_calculated=False):
    """Apply styling to a metric row."""
    font = FONT_BOLD if (is_total or is_calculated) else FONT_DEFAULT
    fill = FILL_SUMMARY_BG if (is_total or is_calculated) else FILL_WHITE
    
    # Column A: Commodity (will be filled by caller)
    apply_cell_style(ws.cell(row=row, column=1), font=font, fill=fill, alignment=ALIGN_CENTER)
    
    # Column B: Metric name
    cell_b = ws.cell(row=row, column=2, value=metric_name)
    apply_cell_style(cell_b, font=font, fill=fill, alignment=ALIGN_METRIC)
    
    # Column C: Unit
    cell_c = ws.cell(row=row, column=3, value=unit)
    apply_cell_style(cell_c, font=font, fill=fill, alignment=ALIGN_CENTER)
    
    # Data columns (D onwards)
    for col in range(4, max_col + 1):
        cell = ws.cell(row=row, column=col)
        apply_cell_style(cell, font=font, fill=fill, alignment=ALIGN_RIGHT, number_format=number_format)


def apply_spacer_row(ws, row, max_col):
    """Apply empty spacer row."""
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_CENTER, border=Border())


def apply_check_row(ws, row, max_col, formula=None):
    """Apply CHECK row with conditional formatting based on value."""
    # Column A
    apply_cell_style(ws.cell(row=row, column=1), font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_CENTER)
    
    # Column B: CHECK label
    cell_b = ws.cell(row=row, column=2, value='CHECK')
    apply_cell_style(cell_b, font=FONT_BOLD, fill=FILL_WHITE, alignment=ALIGN_METRIC)
    
    # Column C: Unit
    cell_c = ws.cell(row=row, column=3, value='KMT')
    apply_cell_style(cell_c, font=FONT_BOLD, fill=FILL_WHITE, alignment=ALIGN_CENTER)
    
    # Data columns
    for col in range(4, max_col + 1):
        cell = ws.cell(row=row, column=col)
        if formula and col == 4:
            cell.value = formula
        apply_cell_style(cell, font=FONT_DEFAULT, fill=FILL_WHITE, alignment=ALIGN_RIGHT, 
                         number_format=FMT_CHECK, border=THIN_BORDER)


def set_column_widths_mo(ws, max_col):
    """Set column widths for monthly tab (16 columns: A-P)."""
    ws.column_dimensions['A'].width = COL_WIDTH_COMMODITY
    ws.column_dimensions['B'].width = COL_WIDTH_METRIC
    ws.column_dimensions['C'].width = COL_WIDTH_UNITS
    for col_idx in range(4, max_col + 1):
        col_letter = get_column_letter(col_idx)
        if col_idx <= 15:  # D-O: 12 month columns
            ws.column_dimensions[col_letter].width = COL_WIDTH_MONTH
        else:  # P: Summary column
            ws.column_dimensions[col_letter].width = COL_WIDTH_SUMMARY


def set_column_widths_yr(ws, max_col):
    """Set column widths for annual tab (crop year columns)."""
    ws.column_dimensions['A'].width = COL_WIDTH_COMMODITY
    ws.column_dimensions['B'].width = COL_WIDTH_METRIC
    ws.column_dimensions['C'].width = COL_WIDTH_UNITS
    for col_idx in range(4, max_col + 1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = COL_WIDTH_CROP_YEAR


def apply_conditional_check_formatting(ws, check_row, max_col):
    """Apply conditional formatting to CHECK row (green if 0, red if != 0)."""
    from openpyxl.formatting.rule import CellIsRule
    
    for col in range(4, max_col + 1):
        cell = ws.cell(row=check_row, column=col)
        col_letter = get_column_letter(col)
        cell_ref = f'{col_letter}{check_row}'
        
        # Green fill when 0
        ws.conditional_formatting.add(cell_ref,
            CellIsRule(operator='equal', formula=['0'], 
                       font=FONT_CHECK_OK, fill=FILL_CHECK_OK))
        # Red fill when not 0
        ws.conditional_formatting.add(cell_ref,
            CellIsRule(operator='notEqual', formula=['0'], 
                       font=FONT_CHECK_WARN, fill=FILL_CHECK_WARN))


def write_annual_template(ws, commodity: str, crop_years: List[int], start_row=1) -> Tuple[int, int]:
    """
    Write the annual tab template structure using ANNUAL_METRIC_SEQUENCE.
    Returns (check_row, last_row).
    """
    current_row = start_row
    
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
    
    # Write metric rows following ANNUAL_METRIC_SEQUENCE
    check_row = None
    for metric_info in ANNUAL_METRIC_SEQUENCE:
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
        current_row += 1
    
    return check_row, current_row - 1


def write_monthly_template(ws, commodity: str, crop_years: List[int], start_row=1) -> Tuple[int, int]:
    """
    Write the monthly tab template structure (16 columns A-P).
    Returns (check_row, last_row).
    """
    current_row = start_row
    start_month = CROP_YEAR_START_MONTH.get(commodity, 8)
    
    # Title row
    ws.cell(row=current_row, column=1, value=commodity)
    ws.cell(row=current_row, column=1).font = FONT_TITLE
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=3)
    current_row += 1
    
    # Column headers: Commodity, Metric, Crop Year, then 12 months, then Summary
    month_names = []
    for m_offset in range(12):
        month_num = (start_month + m_offset - 1) % 12 + 1
        month_names.append(f'{month_num:02d}')
    
    headers = ['Commodity', 'Metric', 'Crop Year'] + month_names + ['Total']
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=current_row, column=col_idx, value=header)
        apply_cell_style(cell, font=FONT_HEADER, fill=FILL_HEADER_BG, alignment=ALIGN_CENTER)
    current_row += 1
    
    # Write metric rows for each crop year (stacked)
    check_row = None
    for cy in crop_years:
        for metric_info in MONTHLY_METRIC_SEQUENCE:
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
    
    return check_row, current_row - 1


from typing import List, Tuple
from openpyxl.styles import Border



# ============================================================
# CanadaSD_Engine.xlsx LAYOUT (used by exporter_excel.py)
# Change cosmetics here; the exporter reads these constants.
# ============================================================
ENGINE_FONT_NAME = 'Calibri'
ENGINE_FONT_SIZE = 10                 # all data and header cells, *_Mo and *_Yr
ENGINE_TITLE_SIZE = 20                # *_Yr cell A1 headline (bold)

# *_Mo tabs: A (Commodity) and B (Metric) = 20; every other column = 7
MO_WIDTH_LABEL = 20
MO_WIDTH_DATA = 7
MO_HEADER_FILL = '4472C4'             # blue header row, white bold text

# *_Yr tabs: plain white cells, black header row, green CHECK highlight only
YR_HEADER_FILL = '000000'             # row 2: solid black, bold white text
YR_CHECK_OK_FILL = 'C6EFCE'           # CHECK within +/-0.0009 KMT
YR_WIDTH_COMMODITY = 18
YR_WIDTH_METRIC = 22
YR_WIDTH_UNIT = 9
YR_WIDTH_YEAR = 10

# Commodity label on *_Yr tabs (A1 headline and column A); default = key
YR_LABEL = {
    'Wheat': 'Wheat Ex-Durum',
}

# *_Yr body cells are left UNFILLED on purpose: a solid white fill would hide
# Excel's gridlines. Unfilled cells display white with gridlines visible.
# Rule line drawn across the TOP of these rows (label + all crop years).
YR_SUBTOTAL_ROWS = ('Total Supply', 'Total Disposition')
YR_SUBTOTAL_LINE = 'thick'            # openpyxl style: 'thin' | 'medium' | 'thick'

# Rows shown in bold (label and values) on *_Yr tabs
YR_BOLD_METRICS = {'Total Supply', 'Total Disposition', 'Ending Stocks', 'CHECK'}


if __name__ == "__main__":
    # Quick test
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Test"
    
    crop_years = list(range(2002, 2027))
    check_row, last_row = write_annual_template(ws, "Wheat", crop_years)
    set_column_widths_yr(ws, len(crop_years) + 3)
    apply_conditional_check_formatting(ws, check_row, len(crop_years) + 3)
    
    wb.save("C:/temp/test_styles.xlsx")
    print("Styles test saved to C:/temp/test_styles.xlsx")
    print(f"Metric order loaded for groups: {list(METRIC_ORDER.keys())}")
    print(f"Annual metric sequence length: {len(ANNUAL_METRIC_SEQUENCE)}")
    for m in ANNUAL_METRIC_SEQUENCE:
        if m['tier1_metric']:
            print(f"  Block {m['block']}: {m['tier1_metric']} ({m['unit']})")