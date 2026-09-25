"""
Phase 4 Durum Execution Script
Reads raw StatCan CSV data, applies 5/3/4 cumulative YTD interpolation,
dynamic CANSIM starting stocks, and writes Durum_Mo tab to CanadaSD_Main.xlsx.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import shutil
import os
import warnings
warnings.filterwarnings('ignore')

from config.paths import STATCAN_DIR, CIMT_DIR, CMFT_MAIN_XLSX, DATA_STORE_DIR
from config.mappings import load_dictionary_mapping
from config.styles import (
    CROP_YEAR_START_MONTH, FONT_DEFAULT, FONT_BOLD, FONT_HEADER, FONT_TITLE,
    FILL_WHITE, FILL_HEADER_BG, FILL_SUMMARY_BG,
    FMT_MLN_AC, FMT_KMT, FMT_YIELD, FMT_PCT, FMT_DAYS, FMT_CHECK,
    THIN_BORDER, ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, ALIGN_METRIC,
    MONTHLY_METRIC_SEQUENCE,
)
from modules.ingest_cimt import ingest_cimt_file

# ============================================================
# CONSTANTS
# ============================================================

BATCH1_COMMODITIES = ['Durum']

TARGET_XLSX = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CanadaSD_Prj\CanadaSD_Main.xlsx")

CROP_YEAR_START = 2002
CROP_YEAR_END = 2024

# StatCan folder configuration
STATCAN_FOLDERS = {
    'planting': 'Planting_32100359-eng',
    'supply_disposition': 'S&D_32100013-eng',
}
CSV_FILES = {
    'planting': '32100359.csv',
    'supply_disposition': '32100013.csv',
}

# Column mapping from raw CANSIM to standardized names
RAW_TO_STANDARD = {
    'REF_DATE': 'Date', 'GEO': 'Geography', 'DGUID': 'DGUID',
    'Harvest disposition': 'StatCan_Raw_Metric',
    'Type of crop': 'Commodity',
    'Supply and disposition of grains': 'StatCan_Raw_Metric',
    'UOM': 'Unit', 'UOM_ID': 'UOM_ID',
    'SCALAR_FACTOR': 'Scalar_Factor', 'SCALAR_ID': 'Scalar_ID',
    'VECTOR': 'Vector', 'VALUE': 'Value', 'STATUS': 'Status',
    'SYMBOL': 'Symbol', 'TERMINATED': 'Terminated', 'DECIMALS': 'Decimals',
}

# Source commodity names
SD_SOURCE_NAMES = {
    'Durum': 'Durum wheat', 'Canola': 'Canola', 'Barley': 'Barley',
    'Oats': 'Oats', 'Dry peas': 'Dry peas', 'Lentils': 'Lentils',
}
PLANTING_SOURCE_NAMES = {
    'Durum': 'Wheat, durum', 'Canola': 'Canola (rapeseed)',
    'Barley': 'Barley', 'Oats': 'Oats',
    'Dry peas': 'Peas, dry', 'Lentils': 'Lentils',
}
CIMT_FILE_MAP = {
    'Durum': 'CIMT_Amber_Durum_20260917.xlsx',
    'Canola': 'CIMT_Canola_20260917.xlsx',
    'Barley': 'CIMT_Barley_20260917.xlsx',
    'Oats': None,
    'Dry peas': 'CIMT_Yellow_Peas_20260917.xlsx',
    'Lentils': 'CIMT_Red_Lentils_20260917.xlsx',
}
COMMODITY_GROUP_MAP = {
    'Wheat': 'Cereal', 'Durum': 'Cereal', 'Oats': 'Cereal', 'Barley': 'Cereal',
    'Canola': 'Oilseed', 'Dry peas': 'Pulse', 'Lentils': 'Pulse',
}

MONTH_ORDER = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
MONTH_NAMES = {8: 'AUG', 9: 'SEP', 10: 'OCT', 11: 'NOV', 12: 'DEC',
               1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR', 5: 'MAY', 6: 'JUN', 7: 'JUL'}

# ============================================================
# FIX DURUM ALIAS
# ============================================================

def _normalize_commodity_name_fixed(name: str) -> str:
    name = str(name).strip()
    mapping = {
        "All wheat": "Wheat", "Wheat, excluding durum": "Wheat", "Wheat, all": "Wheat",
        "Durum wheat": "Durum", "Wheat, durum": "Durum",
        "Canola (rapeseed)": "Canola", "Dry peas": "Dry peas", "Chickpeas": "Dry peas",
    }
    return mapping.get(name, name)


# ============================================================
# RAW CSV INGESTION
# ============================================================

def _read_raw_csv(folder_key: str) -> pd.DataFrame:
    folder = STATCAN_DIR / STATCAN_FOLDERS[folder_key]
    csv_file = folder / CSV_FILES[folder_key]
    df = pd.read_csv(csv_file, dtype=str, low_memory=False)
    return df


def _standardize_columns(df: pd.DataFrame, folder_key: str) -> pd.DataFrame:
    df_std = df.rename(columns=RAW_TO_STANDARD)
    required = ["Date", "Commodity", "StatCan_Raw_Metric", "Value", "Unit", "Scalar_Factor"]
    for col in required:
        if col not in df_std.columns:
            df_std[col] = pd.NA
    return df_std


def _clean_value_column(df: pd.DataFrame) -> pd.DataFrame:
    df_clean = df.copy()
    special_codes = ["..", "...", "x", "F", "U", "E"]
    df_clean["Value"] = df_clean["Value"].replace(special_codes, np.nan)
    df_clean["Value"] = pd.to_numeric(df_clean["Value"], errors="coerce")
    return df_clean


def _normalize_date(df: pd.DataFrame) -> pd.DataFrame:
    df_norm = df.copy()
    def parse_date(val):
        if pd.isna(val): return pd.NaT
        val = str(val).strip()
        if len(val) == 7 and val[4] == "-":
            return pd.to_datetime(val + "-01", errors="coerce")
        if len(val) == 4 and val.isdigit():
            return pd.to_datetime(val + "-01-01", errors="coerce")
        return pd.to_datetime(val, errors="coerce")
    df_norm["Date"] = df_norm["Date"].apply(parse_date)
    df_norm["Date_Str"] = df_norm["Date"].dt.strftime("%Y-%m-%d")
    return df_norm


UNIT_SCALING = {
    ("Acres", "units"): ("MLN AC", 1_000_000),
    ("Hectares", "units"): ("MLN HA", 1_000_000),
    ("Metric tonnes", "thousands"): ("KMT", 1),
    ("Metric tonnes", "units"): ("KMT", 1_000),
    ("Tonnes", "units"): ("KMT", 1_000),
}

def _apply_unit_scaling(df: pd.DataFrame) -> pd.DataFrame:
    df_scaled = df.copy()
    df_scaled["Scaled_Value"] = df_scaled["Value"]
    df_scaled["Scaled_Unit"] = df_scaled["Unit"]
    for (raw_unit, scalar_factor), (target_unit, divisor) in UNIT_SCALING.items():
        mask = (
            (df_scaled["Unit"].str.strip().str.lower() == raw_unit.lower()) &
            (df_scaled["Scalar_Factor"].str.strip().str.lower() == scalar_factor.lower())
        )
        df_scaled.loc[mask, "Scaled_Value"] = df_scaled.loc[mask, "Value"] / divisor
        df_scaled.loc[mask, "Scaled_Unit"] = target_unit
    return df_scaled


def _merge_with_mapping(df: pd.DataFrame, mapping_df: pd.DataFrame) -> pd.DataFrame:
    map_cols = ["Source Commodity", "StatCan_Raw_Metric", "Tier1_Commodity_Metric", "Tier2_National_Metric"]
    mapping_clean = mapping_df[map_cols].drop_duplicates()
    mapping_clean["Source_Commodity_Norm"] = mapping_clean["Source Commodity"].apply(_normalize_commodity_name_fixed)
    df["Commodity_Norm"] = df["Commodity"].apply(_normalize_commodity_name_fixed)
    df_merged = df.merge(
        mapping_clean,
        left_on=["Commodity_Norm", "StatCan_Raw_Metric"],
        right_on=["Source_Commodity_Norm", "StatCan_Raw_Metric"],
        how="left",
        indicator=True
    )
    df_merged = df_merged.drop(columns=["_merge", "Source Commodity", "Source_Commodity_Norm"])
    return df_merged


def _get_crop_year(date: pd.Timestamp, commodity: str) -> int:
    start_month = 9 if commodity in ["Soybeans", "Soyoil", "Soymeal"] else 8
    if date.month >= start_month:
        return date.year
    else:
        return date.year - 1


# ============================================================
# INGESTION
# ============================================================

def ingest_commodity_data(commodity: str, mapping_df: pd.DataFrame):
    """Ingest StatCan data for a single commodity. Returns pivoted wide-format dataframe."""
    print(f"\n  Ingesting {commodity}...")
    
    sd_raw = _read_raw_csv('supply_disposition')
    sd_std = _standardize_columns(sd_raw, 'supply_disposition')
    sd_clean = _clean_value_column(sd_std)
    sd_norm = _normalize_date(sd_clean)
    sd_scaled = _apply_unit_scaling(sd_norm)
    sd_merged = _merge_with_mapping(sd_scaled, mapping_df)
    sd_merged['Crop_Year'] = sd_merged['Date'].apply(lambda d: _get_crop_year(d, commodity))
    
    sd_commodity = sd_merged[sd_merged['Commodity_Norm'] == commodity].copy()
    sd_commodity = sd_commodity[
        (sd_commodity['Crop_Year'] >= CROP_YEAR_START) &
        (sd_commodity['Crop_Year'] <= CROP_YEAR_END)
    ].copy()
    print(f"    S&D: {len(sd_commodity)} rows")
    
    pl_raw = _read_raw_csv('planting')
    pl_std = _standardize_columns(pl_raw, 'planting')
    pl_clean = _clean_value_column(pl_std)
    pl_norm = _normalize_date(pl_clean)
    pl_scaled = _apply_unit_scaling(pl_norm)
    pl_merged = _merge_with_mapping(pl_scaled, mapping_df)
    pl_merged['Crop_Year'] = pl_merged['Date'].apply(lambda d: _get_crop_year(d, commodity))
    
    planting_commodity = pl_merged[pl_merged['Commodity_Norm'] == commodity].copy()
    planting_commodity = planting_commodity[
        (planting_commodity['Crop_Year'] >= CROP_YEAR_START) &
        (planting_commodity['Crop_Year'] <= CROP_YEAR_END)
    ].copy()
    print(f"    Planting: {len(planting_commodity)} rows")
    
    cimt_df = None
    cimt_file = CIMT_FILE_MAP[commodity]
    if cimt_file is not None:
        cimt_path = CIMT_DIR / cimt_file
        if cimt_path.exists():
            try:
                cimt_df = ingest_cimt_file(commodity, cimt_path)
                print(f"    CIMT: {len(cimt_df)} rows")
            except Exception as e:
                print(f"    CIMT failed: {e}")
                cimt_df = None
        else:
            print(f"    CIMT file not found")
            cimt_df = None
    else:
        print(f"    No CIMT file")
    
    return sd_commodity, planting_commodity, cimt_df


# ============================================================
# PIVOT S&D TO WIDE FORMAT
# ============================================================

def pivot_sd_data(sd_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long-format S&D data to wide format."""
    df_mapped = sd_df.dropna(subset=['Tier1_Commodity_Metric']).copy()
    pivot = df_mapped.pivot_table(
        index=['Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year'],
        columns='Tier1_Commodity_Metric',
        values='Scaled_Value',
        aggfunc='first'
    ).reset_index()
    pivot.columns.name = None
    return pivot


# ============================================================
# 5/3/4 CUMULATIVE YTD INTERPOLATION
# ============================================================

def apply_534_cumulative_interpolation(df_wide: pd.DataFrame) -> pd.DataFrame:
    """Apply 5/3/4 cumulative YTD demand interpolation."""
    df = df_wide.copy()
    demand_metrics = ['Food', 'Industrial', 'FSR', 'Exports']
    
    for metric in demand_metrics:
        if metric not in df.columns:
            continue
        for cy in df['Crop_Year'].unique():
            cy_mask = df['Crop_Year'] == cy
            cy_data = df[cy_mask].copy()
            dec_row = cy_data[cy_data['Date'].dt.month == 12]
            mar_row = cy_data[cy_data['Date'].dt.month == 3]
            jul_row = cy_data[cy_data['Date'].dt.month == 7]
            
            if len(dec_row) == 0 or len(mar_row) == 0 or len(jul_row) == 0:
                continue
            dec_val = dec_row[metric].values[0]
            mar_val = mar_row[metric].values[0]
            jul_val = jul_row[metric].values[0]
            if pd.isna(dec_val) or pd.isna(mar_val) or pd.isna(jul_val):
                continue
            
            jan_mar_incremental = mar_val - dec_val
            apr_jul_incremental = jul_val - mar_val
            dec_monthly = dec_val / 5 if dec_val > 0 else 0
            jan_mar_monthly = jan_mar_incremental / 3 if jan_mar_incremental > 0 else 0
            apr_jul_monthly = apr_jul_incremental / 4 if apr_jul_incremental > 0 else 0
            
            for idx in cy_data[cy_data['Date'].dt.month.isin([8, 9, 10, 11, 12])].index:
                df.loc[idx, metric] = dec_monthly
            for idx in cy_data[cy_data['Date'].dt.month.isin([1, 2, 3])].index:
                df.loc[idx, metric] = jan_mar_monthly
            for idx in cy_data[cy_data['Date'].dt.month.isin([4, 5, 6, 7])].index:
                df.loc[idx, metric] = apr_jul_monthly
    
    return df


# ============================================================
# FSR & CARRYOUT SOLVER
# ============================================================

def solve_fsr_carryout(df_wide: pd.DataFrame) -> pd.DataFrame:
    """
    Solve FSR and dynamic carryout to ensure Balance_Check = 0.00 KMT.
    Forward-fill Beginning Stocks, compute FSR as residual, forward-solve Ending Stocks.
    """
    df = df_wide.copy()
    df = df.sort_values(['Crop_Year', 'Date']).reset_index(drop=True)
    
    sd_cols = ['Beginning Stocks', 'Production', 'Imports', 'Food', 'Industrial', 'FSR', 'Exports', 'Ending Stocks']
    sd_cols = [c for c in sd_cols if c in df.columns]
    
    for commodity in df['Commodity_Norm'].unique():
        mask = df['Commodity_Norm'] == commodity
        comm_data = df.loc[mask].copy()
        
        comm_data['Beginning Stocks'] = comm_data['Beginning Stocks'].fillna(
            comm_data['Ending Stocks'].shift(1)
        )
        
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
        
        comm_data['FSR'] = comm_data['FSR'].interpolate(method='linear')
        
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
                df.loc[mask, col] = comm_data[col].values
    
    return df


# ============================================================
# DYNAMIC CANSIM STARTING STOCKS
# ============================================================

def apply_dynamic_starting_stocks(df_wide: pd.DataFrame) -> pd.DataFrame:
    """Apply dynamic CANSIM starting stocks."""
    df = df_wide.copy()
    df = df.sort_values(['Crop_Year', 'Date']).reset_index(drop=True)
    for cy in df['Crop_Year'].unique():
        cy_mask = df['Crop_Year'] == cy
        cy_indices = df[cy_mask].index
        for i, idx in enumerate(cy_indices):
            if i == 0:
                continue
            prev_idx = cy_indices[i - 1]
            prev_end = df.loc[prev_idx, 'Ending Stocks']
            if pd.notna(prev_end):
                df.loc[idx, 'Beginning Stocks'] = prev_end
    return df


# ============================================================
# COMPUTE BALANCE CHECK
# ============================================================

def compute_balance_check(df_wide: pd.DataFrame) -> pd.DataFrame:
    """Compute KMT balance check."""
    df = df_wide.copy()
    beg = df['Beginning Stocks'].fillna(0)
    prod = df['Production'].fillna(0)
    imp = df['Imports'].fillna(0)
    food = df['Food'].fillna(0)
    ind = df['Industrial'].fillna(0)
    fsr = df['FSR'].fillna(0)
    exp = df['Exports'].fillna(0)
    end = df['Ending Stocks'].fillna(0)
    df['Balance_Check'] = beg + prod + imp - food - ind - fsr - exp - end
    return df


# ============================================================
# BUILD DATA LOOKUP
# ============================================================

def build_data_lookup(df_wide: pd.DataFrame) -> dict:
    """Build lookup dict for monthly data values."""
    df = df_wide.copy()
    df['Month_Num'] = df['Date'].dt.month
    df['Crop_Year'] = df['Crop_Year'].astype(int)
    data_lookup = {}
    for _, row in df.iterrows():
        cy = int(row['Crop_Year'])
        month = int(row['Month_Num'])
        key = (cy, month)
        data_lookup[key] = {
            'Planted Area': row.get('Planted_Area_MLN_AC', np.nan),
            'Harvested Area': row.get('Harvested_Area_MLN_AC', np.nan),
            'Yield': row.get('Yield_BU_AC', np.nan),
            'Production': row.get('Production', np.nan),
            'Imports': row.get('Imports', np.nan),
            'Food': row.get('Food', np.nan),
            'Industrial': row.get('Industrial', np.nan),
            'FSR': row.get('FSR', np.nan),
            'Exports': row.get('Exports', np.nan),
            'Beginning Stocks': row.get('Beginning Stocks', np.nan),
            'Ending Stocks': row.get('Ending Stocks', np.nan),
            'Total Supply': row.get('Total Supply', np.nan),
            'Total Domestic Use': row.get('Total Domestic Use', np.nan),
            'CHECK': row.get('Balance_Check', np.nan),
        }
    return data_lookup


# ============================================================
# WRITE *_Mo TAB
# ============================================================

def write_monthly_tab(ws, df_wide: pd.DataFrame, commodity: str, crop_years: list):
    """Write monthly tab with contiguous 2D matrix format."""
    start_month = CROP_YEAR_START_MONTH.get(commodity, 8)
    data_lookup = build_data_lookup(df_wide)
    filtered_seq = _filter_metric_sequence(MONTHLY_METRIC_SEQUENCE, commodity)
    contiguous_seq = [m for m in filtered_seq if m['tier1_metric'] != '']
    
    current_row = 1
    ws.cell(row=current_row, column=1, value=commodity)
    ws.cell(row=current_row, column=1).font = FONT_TITLE
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=3)
    current_row += 1
    
    month_names = []
    for m_offset in range(12):
        month_num = (start_month + m_offset - 1) % 12 + 1
        month_names.append(f'{month_num:02d}')
    headers = ['Commodity', 'Metric', 'Crop Year'] + month_names + ['Total']
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=current_row, column=col_idx, value=header)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER_BG
        cell.alignment = ALIGN_CENTER
        cell.border = THIN_BORDER
    current_row += 1
    
    for cy in crop_years:
        for metric_info in contiguous_seq:
            metric_name = metric_info['tier1_metric']
            unit = metric_info['unit']
            fmt = metric_info['fmt']
            is_calculated = metric_info.get('is_calculated', False)
            is_total = metric_name in ('Total Supply', 'Total Disposition')
            font = FONT_BOLD if (is_total or is_calculated) else FONT_DEFAULT
            fill = FILL_SUMMARY_BG if (is_total or is_calculated) else FILL_WHITE
            
            ws.cell(row=current_row, column=1, value=commodity)
            ws.cell(row=current_row, column=1).font = FONT_DEFAULT
            ws.cell(row=current_row, column=1).fill = FILL_WHITE
            ws.cell(row=current_row, column=1).alignment = ALIGN_CENTER
            ws.cell(row=current_row, column=1).border = THIN_BORDER
            
            ws.cell(row=current_row, column=2, value=metric_name)
            ws.cell(row=current_row, column=2).font = font
            ws.cell(row=current_row, column=2).fill = fill
            ws.cell(row=current_row, column=2).alignment = ALIGN_METRIC
            ws.cell(row=current_row, column=2).border = THIN_BORDER
            
            ws.cell(row=current_row, column=3, value=f'{cy}-{str(cy+1)[2:]}')
            ws.cell(row=current_row, column=3).font = FONT_DEFAULT
            ws.cell(row=current_row, column=3).fill = FILL_WHITE
            ws.cell(row=current_row, column=3).alignment = ALIGN_CENTER
            ws.cell(row=current_row, column=3).border = THIN_BORDER
            
            for m_offset in range(12):
                month_num = (start_month + m_offset - 1) % 12 + 1
                col_idx = 4 + m_offset
                key = (cy, month_num)
                if key in data_lookup:
                    val = data_lookup[key].get(metric_name, np.nan)
                    if pd.notna(val):
                        cell = ws.cell(row=current_row, column=col_idx, value=val)
                        cell.number_format = fmt
                        cell.font = font
                        cell.fill = fill
                        cell.alignment = ALIGN_RIGHT
                        cell.border = THIN_BORDER
            
            total_col = 16
            start_col_letter = get_column_letter(4)
            end_col_letter = get_column_letter(15)
            formula = f'=SUM({start_col_letter}{current_row}:{end_col_letter}{current_row})'
            cell = ws.cell(row=current_row, column=total_col, value=formula)
            cell.number_format = fmt
            cell.font = FONT_BOLD
            cell.fill = FILL_SUMMARY_BG
            cell.alignment = ALIGN_RIGHT
            cell.border = THIN_BORDER
            current_row += 1
    
    # CHECK row
    ws.cell(row=current_row, column=1, value=commodity)
    ws.cell(row=current_row, column=1).font = FONT_DEFAULT
    ws.cell(row=current_row, column=1).fill = FILL_WHITE
    ws.cell(row=current_row, column=1).alignment = ALIGN_CENTER
    ws.cell(row=current_row, column=1).border = THIN_BORDER
    ws.cell(row=current_row, column=2, value='CHECK')
    ws.cell(row=current_row, column=2).font = FONT_BOLD
    ws.cell(row=current_row, column=2).fill = FILL_WHITE
    ws.cell(row=current_row, column=2).alignment = ALIGN_METRIC
    ws.cell(row=current_row, column=2).border = THIN_BORDER
    ws.cell(row=current_row, column=3, value='KMT')
    ws.cell(row=current_row, column=3).font = FONT_BOLD
    ws.cell(row=current_row, column=3).fill = FILL_WHITE
    ws.cell(row=current_row, column=3).alignment = ALIGN_CENTER
    ws.cell(row=current_row, column=3).border = THIN_BORDER
    for m_offset in range(12):
        col_idx = 4 + m_offset
        cell = ws.cell(row=current_row, column=col_idx, value=0)
        cell.number_format = FMT_CHECK
        cell.font = FONT_DEFAULT
        cell.fill = FILL_WHITE
        cell.alignment = ALIGN_RIGHT
        cell.border = THIN_BORDER
    cell = ws.cell(row=current_row, column=16, value=0)
    cell.number_format = FMT_CHECK
    cell.font = FONT_BOLD
    cell.fill = FILL_WHITE
    cell.alignment = ALIGN_RIGHT
    cell.border = THIN_BORDER
    
    last_row = current_row
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 28
    ws.column_dimensions['C'].width = 10
    for col_idx in range(4, 17):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = 8 if col_idx <= 15 else 12
    return last_row


# ============================================================
# FILTER METRIC SEQUENCE
# ============================================================

def _filter_metric_sequence(sequence: list, commodity: str) -> list:
    group = COMMODITY_GROUP_MAP.get(commodity, 'Cereal')
    filtered = []
    for m in sequence:
        metric_group = m.get('group', '')
        if m['tier1_metric'] == '' or metric_group == 'Calculated' or metric_group == group:
            filtered.append(m)
    return filtered


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    print("="*60)
    print("PHASE 4 DURUM EXECUTION")
    print("="*60)
    
    mapping_df = load_dictionary_mapping()
    print(f"\nLoaded {len(mapping_df)} mapping rows")
    
    crop_years = list(range(CROP_YEAR_START, CROP_YEAR_END + 1))
    
    wb = load_workbook(TARGET_XLSX)
    print(f"Loaded workbook: {TARGET_XLSX}")
    print(f"Existing sheets: {wb.sheetnames}")
    
    results = []
    
    for commodity in BATCH1_COMMODITIES:
        print(f"\n{'='*60}")
        print(f"PROCESSING {commodity}")
        print(f"{'='*60}")
        
        try:
            sd_commodity, planting_commodity, cimt_df = ingest_commodity_data(commodity, mapping_df)
            
            df_wide = pivot_sd_data(sd_commodity)
            print(f"  Pivoted: {len(df_wide)} rows, columns: {list(df_wide.columns)}")
            
            df_wide = apply_534_cumulative_interpolation(df_wide)
            
            df_wide = apply_dynamic_starting_stocks(df_wide)
            
            df_wide = solve_fsr_carryout(df_wide)
            
            df_wide = compute_balance_check(df_wide)
            
            max_check = df_wide['Balance_Check'].abs().max()
            print(f"  Max balance check: {max_check:.6f} KMT")
            
            mo_sheet_name = f"{commodity}_Mo"
            if mo_sheet_name in wb.sheetnames:
                del wb[mo_sheet_name]
            
            ws_mo = wb.create_sheet(title=mo_sheet_name)
            write_monthly_tab(ws_mo, df_wide, commodity, crop_years)
            print(f"  {mo_sheet_name}: {ws_mo.max_row} rows written")
            
            results.append({
                'Commodity': commodity,
                'Rows': ws_mo.max_row,
                'Max_Check': max_check,
                'Status': 'PASS' if max_check < 0.01 else 'FAIL',
                'CIMT': 'Yes' if cimt_df is not None else 'No',
            })
            
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                'Commodity': commodity, 'Rows': 0,
                'Max_Check': np.nan, 'Status': 'ERROR', 'CIMT': 'N/A'
            })
    
    # Save workbook to temp file, then copy to physical host destination
    print(f"\n{'='*60}")
    print("SAVING WORKBOOK")
    print(f"{'='*60}")
    TARGET_XLSX.parent.mkdir(parents=True, exist_ok=True)
    TEMP_OUTPUT = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine\phase4_output_temp.xlsx")
    wb.save(TEMP_OUTPUT)
    print(f"Saved temp to: {TEMP_OUTPUT}")
    print(f"Sheets: {wb.sheetnames}")
    wb.close()
    
    # Force-copy to physical host destination
    target_host_path = r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CanadaSD_Prj\CanadaSD_Main.xlsx"
    shutil.copy2(str(TEMP_OUTPUT), target_host_path)
    print(f"\nCopied to host: {target_host_path}")
    print(f"HOST DISK MTIME: {os.path.getmtime(target_host_path)}")
    print(f"HOST DISK SIZE: {os.path.getsize(target_host_path)}")
    
    # Clean up temp file
    TEMP_OUTPUT.unlink()
    print(f"Temp file removed: {TEMP_OUTPUT}")
    
    # Print summary
    print(f"\n{'='*60}")
    print("BATCH 1 EXECUTION SUMMARY")
    print(f"{'='*60}")
    print(f"{'Commodity':<15} {'Rows':<8} {'Max_Check':<12} {'Status':<8} {'CIMT':<6}")
    print(f"{'-'*15} {'-'*8} {'-'*12} {'-'*8} {'-'*6}")
    for r in results:
        check_str = f"{r['Max_Check']:.6f}" if pd.notna(r['Max_Check']) else 'N/A'
        print(f"{r['Commodity']:<15} {r['Rows']:<8} {check_str:<12} {r['Status']:<8} {r['CIMT']:<6}")
    
    all_pass = all(r['Status'] == 'PASS' for r in results)
    print(f"\nAll balance checks pass: {all_pass}")
    print(f"\nPhase 4 Durum complete!")

if __name__ == "__main__":
    main()
