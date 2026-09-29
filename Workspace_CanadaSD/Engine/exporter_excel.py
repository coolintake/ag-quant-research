"""
CMFT Engine - Excel Exporter

Writes paired *_Mo / *_Yr tabs matching the CanadaSD_Main.xlsx reference
layout (Wheat_Mo).

*_Mo (monthly) - metric-major, contiguous, no spacer rows (ENGINE_RULES 6.2):
    A Commodity (StatCan series name) | B Metric | C Crop Year ('02-03')
    D..O the 12 crop-year months ('AUG'..'JUL', or 'SEP'..'AUG' for soybeans)
    P 'AUG/JUL' total
  Blocks, each with one row per crop year in scope (2002/03-2025/26):
    Planted Area, Harvested Area, Yield, Production  -> first month; P = D
    Exports, Imports  -> CIMT monthly actuals (observed value per calendar
                         month; blank if CIMT does not cover the crop year)
    Food, Industrial|Crush, Seed                     -> monthly;     P = SUM
    Food & Processing  = Food + Industrial|Crush (formula)
    FSR                                              -> monthly;     P = SUM
    STOCKS = prior month STOCKS + Prod + Imp - Exp - Food&Proc - FSR - Seed
             (live formula); P = last month
    The first crop year's opening stocks are StatCan's reported beginning
    stocks (ENGINE_RULES 5.1), shown as a commented constant. Every later
    crop year opens from the prior July STOCKS cell (5.3).

*_Yr (annual) - looks values up in *_Mo by metric name and crop-year label
  (SUMIFS), so it cannot point at the wrong row if *_Mo rows move.
"""

import calendar
import re
from pathlib import Path
from typing import Dict, List
import warnings

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

from config.paths import DATA_STORE_DIR, CANADASD_MAIN_XLSX, VEGOIL_FACT_TABLE, MEAL_FACT_TABLE
from config.styles import (
    ENGINE_FONT_NAME, ENGINE_FONT_SIZE, ENGINE_TITLE_SIZE,
    MO_WIDTH_LABEL, MO_WIDTH_DATA, MO_HEADER_FILL,
    YR_HEADER_FILL, YR_CHECK_OK_FILL, YR_WIDTH_COMMODITY, YR_WIDTH_METRIC,
    YR_WIDTH_UNIT, YR_WIDTH_YEAR, YR_LABEL, YR_BOLD_METRICS,
    YR_SUBTOTAL_ROWS, YR_SUBTOTAL_LINE, YR_TITLE_ROW_HEIGHT,
    VEGOIL_SUBTOTAL_TOP, VEGOIL_SUBTOTAL_BOTTOM, VEGOIL_CHECK_OK_FILL, VEGOIL_CHECK_TOL,
    VEGOIL_GROUP_TITLE_SIZE,
)
from config.commodities import (
    TIER1_COMMODITIES, DISPLAY_NAME, GROUP_TABS, SCOPE_FIRST_CROP_YEAR, SCOPE_LAST_CROP_YEAR,
    VEG_OILS, VEG_OIL_FIRST_CROP_YEAR, VEGOIL_GROUP_TAB, VEGOIL_GROUP_TITLE, VEGOIL_GROUP_LABEL,
    PROTEIN_MEALS, MEAL_GROUP_TAB, MEAL_GROUP_TITLE, MEAL_GROUP_LABEL, SUMMARY_TAB_ORDER,
    month_labels, crop_year_label, get_group,
)
from modules.vegoil_builder import annual_vegoil, oil_start_months, load_crush
from modules.meal_builder import annual_meal, meal_start_months

TARGET_WORKBOOK = CANADASD_MAIN_XLSX

# ---------------------------------------------------------------- styling
# Matches the CanadaSD_Main.xlsx reference tabs (Calibri 11, blue header).
# Cosmetic settings live in config/styles.py
FONT = Font(name=ENGINE_FONT_NAME, size=ENGINE_FONT_SIZE)
FONT_BOLD = Font(name=ENGINE_FONT_NAME, size=ENGINE_FONT_SIZE, bold=True)
FONT_HDR = Font(name=ENGINE_FONT_NAME, size=ENGINE_FONT_SIZE, bold=True, color='FFFFFF')
FONT_TITLE = Font(name=ENGINE_FONT_NAME, size=ENGINE_TITLE_SIZE, bold=True)
FILL_HDR = PatternFill('solid', start_color=MO_HEADER_FILL, end_color=MO_HEADER_FILL)
FILL_HDR_YR = PatternFill('solid', start_color=YR_HEADER_FILL, end_color=YR_HEADER_FILL)
FILL_SUM = PatternFill('solid', start_color='E2EFDA', end_color='E2EFDA')
FILL_OK = PatternFill('solid', start_color=YR_CHECK_OK_FILL, end_color=YR_CHECK_OK_FILL)
THIN = Side(style='thin', color='B4B4B4')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
LEFT = Alignment(horizontal='left', vertical='center')
RIGHT = Alignment(horizontal='right', vertical='center')
CENTER = Alignment(horizontal='center', vertical='center')

FMT_AREA, FMT_YIELD, FMT_KMT = '#,##0.000', '#,##0.0', '#,##0'
FMT_PCT, FMT_DAYS = '0.0%', '#,##0'


# ------------------------------------------------------------- *_Mo layout
def mo_metrics(commodity: str) -> List[str]:
    """Metric blocks for a commodity's *_Mo tab, in order."""
    use = 'Crush' if get_group(commodity) == 'Oilseed' else 'Industrial'
    return ['Planted Area', 'Harvested Area', 'Yield', 'Production',
            'Exports', 'Imports', 'Food', use, 'Seed',
            'Food & Processing', 'FSR', 'STOCKS']


FIRST_MONTH_ONLY = {'Planted Area', 'Harvested Area', 'Yield', 'Production'}
MASTER_COL = {
    'Planted Area': 'Planted_MLN_AC', 'Harvested Area': 'Harvested_MLN_AC',
    'Yield': 'Yield_BU_AC', 'Production': 'Production',
    'Exports': 'Exports', 'Imports': 'Imports', 'Food': 'Food',
    'Industrial': 'Industrial', 'Crush': 'Crush', 'FSR': 'FSR',
}
FMT = {'Planted Area': FMT_AREA, 'Harvested Area': FMT_AREA, 'Yield': FMT_YIELD}


def load_master() -> pd.DataFrame:
    df = pd.read_csv(DATA_STORE_DIR / 'CMFT_Master.csv')
    df['Date'] = pd.to_datetime(df['Date_Str'])
    df['Crop_Year'] = df['Crop_Year'].astype(int)
    return df[df['Crop_Year'].between(SCOPE_FIRST_CROP_YEAR, SCOPE_LAST_CROP_YEAR)].copy()


def _style(cell, font=FONT, align=RIGHT, fmt=None, fill=None):
    cell.font, cell.alignment, cell.border = font, align, BORDER
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill


def write_monthly_tab(ws, df_comm: pd.DataFrame, commodity: str, crop_years: List[int]):
    metrics = mo_metrics(commodity)
    months = month_labels(commodity)
    n_cy = len(crop_years)
    use = metrics[7]                       # 'Industrial' or 'Crush'

    # Header
    headers = ['Commodity', 'Metric', 'Crop Year'] + months + [f'{months[0]}/{months[-1]}']
    for c, h in enumerate(headers, 1):
        _style(ws.cell(1, c, h), font=FONT_HDR, align=CENTER, fill=FILL_HDR)

    # Row address of (metric, crop-year index)
    block_start = {m: 2 + i * n_cy for i, m in enumerate(metrics)}
    row = lambda m, i: block_start[m] + i

    # Data by (crop_year, crop-year month 1..12)
    data = {(int(r.Crop_Year), int(r.CY_Month)): r for r in df_comm.itertuples(index=False)}
    has_cy = {cy: any((cy, k) in data for k in range(1, 13)) for cy in crop_years}
    complete_cy = {cy: all((cy, k) in data for k in range(1, 13)) for cy in crop_years}

    label = DISPLAY_NAME.get(commodity, commodity)
    for m in metrics:
        fmt = FMT.get(m, FMT_KMT)
        for i, cy in enumerate(crop_years):
            r = row(m, i)
            _style(ws.cell(r, 1, label), align=LEFT)
            _style(ws.cell(r, 2, m), align=LEFT)
            _style(ws.cell(r, 3, crop_year_label(cy)), align=LEFT)
            for k in range(1, 13):
                _style(ws.cell(r, 3 + k), fmt=fmt)
            _style(ws.cell(r, 16), fmt=fmt, font=FONT_BOLD, fill=FILL_SUM)
            if not has_cy[cy]:
                continue

            if m in FIRST_MONTH_ONLY:
                rec = data.get((cy, 1))
                v = getattr(rec, MASTER_COL[m], np.nan) if rec is not None else np.nan
                if pd.notna(v):
                    ws.cell(r, 4).value = float(v)
                ws.cell(r, 16).value = f'=D{r}'

            elif m == 'Seed':
                # Seed is inside FSR (StatCan seed + loss + feed); kept as a
                # separate zero row to match the reference layout.
                for k in range(1, 13):
                    if (cy, k) in data:
                        ws.cell(r, 3 + k).value = 0
                ws.cell(r, 16).value = f'=SUM(D{r}:O{r})'

            elif m == 'Food & Processing':
                for k in range(1, 13):
                    if (cy, k) in data:
                        col = get_column_letter(3 + k)
                        ws.cell(r, 3 + k).value = f'={col}{row("Food", i)}+{col}{row(use, i)}'
                ws.cell(r, 16).value = f'=SUM(D{r}:O{r})'

            elif m == 'STOCKS':
                for k in range(1, 13):
                    if (cy, k) not in data:
                        continue
                    col = get_column_letter(3 + k)
                    if k > 1:
                        opening = f'{get_column_letter(2 + k)}{r}'
                    elif i > 0 and complete_cy[crop_years[i - 1]]:
                        opening = f'O{r - 1}'                    # prior July (5.3)
                    else:
                        beg = getattr(data[(cy, 1)], 'Beginning_Stocks', np.nan)
                        opening = f'{0.0 if pd.isna(beg) else float(beg)!r}'
                        ws.cell(r, 4).comment = Comment(
                            f'Opening stocks {crop_year_label(cy)}: StatCan 32100013 '
                            f'"Total beginning stocks" (ENGINE_RULES 5.1).', 'CMFT Engine')
                    ws.cell(r, 3 + k).value = (
                        f'={opening}+{col}{row("Production", i)}+{col}{row("Imports", i)}'
                        f'-{col}{row("Exports", i)}-{col}{row("Food & Processing", i)}'
                        f'-{col}{row("FSR", i)}-{col}{row("Seed", i)}'
                    )
                ws.cell(r, 16).value = f'=O{r}' if complete_cy[cy] else \
                    f'=IFERROR(LOOKUP(2,1/(D{r}:O{r}<>""),D{r}:O{r}),0)'

            else:  # monthly flows
                for k in range(1, 13):
                    rec = data.get((cy, k))
                    if rec is None:
                        continue
                    v = getattr(rec, MASTER_COL[m], np.nan)
                    if pd.isna(v) and m in ('Exports', 'Imports'):
                        continue      # no CIMT coverage: leave blank, never estimate
                    ws.cell(r, 3 + k).value = 0.0 if pd.isna(v) else float(v)
                ws.cell(r, 16).value = f'=SUM(D{r}:O{r})'

            if not complete_cy[cy]:
                # Incomplete crop year (e.g. the current year before StatCan's
                # July release): months shown, annual total left blank - never
                # a partial-year sum (RULE-2.1.B)
                ws.cell(r, 16).value = None

    ws.column_dimensions['A'].width = MO_WIDTH_LABEL
    ws.column_dimensions['B'].width = MO_WIDTH_LABEL
    for c in range(3, 17):                  # C (Crop Year) .. P (Total)
        ws.column_dimensions[get_column_letter(c)].width = MO_WIDTH_DATA
    ws.freeze_panes = 'D2'
    return block_start


# ------------------------------------------------------------- *_Yr layout
def _yr_finish(ws, rows: Dict[str, int], n_years: int):
    """Shared *_Yr finishing: subtotal rule lines, green CHECK, gridlines, widths.
    No fills or cell borders are applied to body cells (gridlines stay visible)."""
    last_col = 3 + n_years
    top = Border(top=Side(style=YR_SUBTOTAL_LINE, color='000000'))
    for name in YR_SUBTOTAL_ROWS:
        if name in rows:
            for c in range(1, last_col + 1):
                ws.cell(rows[name], c).border = top
    chk = rows['CHECK']
    rng = f'D{chk}:{get_column_letter(last_col)}{chk}'
    ws.conditional_formatting.add(
        rng, CellIsRule(operator='between', formula=['-0.0009', '0.0009'], fill=FILL_OK))
    ws.sheet_view.showGridLines = True
    ws.column_dimensions['A'].width = YR_WIDTH_COMMODITY
    ws.column_dimensions['B'].width = YR_WIDTH_METRIC
    ws.column_dimensions['C'].width = YR_WIDTH_UNIT
    for j in range(n_years):
        ws.column_dimensions[get_column_letter(4 + j)].width = YR_WIDTH_YEAR
    ws.freeze_panes = 'D3'


def yr_metrics(commodity: str) -> List[str]:
    use = 'Crush' if get_group(commodity) == 'Oilseed' else 'Industrial'
    return ['Planted Area', 'Harvested Area', 'Proportion Harvested', 'Yield', '',
            'Beginning Stocks', 'Imports', 'Production', 'Total Supply', '',
            'Food', use, 'FSR', 'Exports', 'Total Disposition', '',
            'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK']


YR_UNIT = {'Planted Area': 'MLN AC', 'Harvested Area': 'MLN AC', 'Proportion Harvested': '%',
           'Yield': 'BU/AC', 'Stocks-to-Use': '%', 'STU Days': 'Days'}
YR_FMT = {'Planted Area': FMT_AREA, 'Harvested Area': FMT_AREA, 'Proportion Harvested': FMT_PCT,
          'Yield': FMT_YIELD, 'Stocks-to-Use': FMT_PCT, 'STU Days': FMT_DAYS}
FMT_RATIO = '0%'
RATIO_GAP = 2                            # blank rows between CHECK and the ratio rows


def _title(ws, row: int, text: str, span: str = None):
    """Table headline: bold 20pt label in A, marketing-year span (e.g. 'Aug/Jul') in B."""
    ws.cell(row, 1, text).font = FONT_TITLE
    if span:
        ws.cell(row, 2, span).font = Font(name=ENGINE_FONT_NAME, size=ENGINE_TITLE_SIZE)
    ws.row_dimensions[row].height = YR_TITLE_ROW_HEIGHT


def _header(ws, row: int, crop_years: List[int]):
    headers = ['Commodity', 'Metric', 'Unit'] + [crop_year_label(cy) for cy in crop_years]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row, c, h)
        cell.font, cell.fill, cell.alignment = FONT_HDR, FILL_HDR_YR, CENTER


def write_ratio_rows(ws, first_row: int, label: str, n_years: int, ratios) -> int:
    """Ratio rows under a *_Yr table. ratios: [(name, numerator cells, denominator
    row)], numerator rows summed. Blank when the denominator is 0/blank.
    Returns the last row written."""
    for i, (name, num_rows, den_row) in enumerate(ratios):
        r = first_row + i
        ws.cell(r, 1, label).font, ws.cell(r, 1).alignment = FONT, LEFT
        ws.cell(r, 2, name).font, ws.cell(r, 2).alignment = FONT, LEFT
        for j in range(n_years):
            cl = get_column_letter(4 + j)
            num = '+'.join(f'{cl}{x}' for x in num_rows)
            cell = ws.cell(r, 4 + j, f'=IF(N({cl}${den_row})<>0,({num})/{cl}${den_row},"")')
            cell.font, cell.alignment, cell.number_format = FONT, RIGHT, FMT_RATIO
    return first_row + len(ratios) - 1


def standard_ratios(rows: Dict[str, int], use: str):
    """Ratio_F&I/Prod, Ratio_FSR/Prod, Ratio_Exp/Prod (grain / oilseed / pulse tables)."""
    p = rows['Production']
    return [('Ratio_F&I/Prod', [rows['Food'], rows[use]], p),
            ('Ratio_FSR/Prod', [rows['FSR']], p),
            ('Ratio_Exp/Prod', [rows['Exports']], p)]


def write_annual_tab(ws, commodity: str, crop_years: List[int], mo_sheet: str,
                     complete_years=None):
    """*_Yr tab: plain white cells with gridlines, black header row, 10pt.
    Values are looked up from *_Mo by metric name and crop-year label.
    Columns of crop years not in complete_years (default: all) stay blank.

    Ending Stocks = Total Supply - Total Disposition. Because that makes the
    classic balance identity zero by construction, CHECK instead compares it
    with the closing STOCKS on the *_Mo tab: a non-zero CHECK means the
    annual and monthly tabs disagree."""
    metrics = yr_metrics(commodity)
    use = metrics[11]
    mo = f"'{mo_sheet}'"
    label = YR_LABEL.get(commodity, commodity)

    months = month_labels(commodity)
    _title(ws, 1, label, f'{months[0].title()}/{months[-1].title()}')
    _header(ws, 2, crop_years)

    rows = {m: 3 + i for i, m in enumerate(metrics) if m}
    R = lambda m: rows[m]

    def lookup(metric, col, cl):
        return (f'SUMIFS({mo}!${col}:${col},{mo}!$B:$B,"{metric}",'
                f'{mo}!$C:$C,RIGHT({cl}$2,5))')

    for i, m in enumerate(metrics):
        r = 3 + i
        if not m:
            continue
        font = FONT_BOLD if m in YR_BOLD_METRICS else FONT
        for c, v, al in ((1, label, LEFT), (2, m, LEFT), (3, YR_UNIT.get(m, 'KMT'), CENTER)):
            cell = ws.cell(r, c, v)
            cell.font, cell.alignment = font, al
        for j, cy in enumerate(crop_years):
            if complete_years is not None and cy not in complete_years:
                continue
            c = 4 + j
            cl = get_column_letter(c)
            prev = get_column_letter(c - 1)
            if m in ('Planted Area', 'Harvested Area', 'Yield', 'Production',
                     'Imports', 'Food', use, 'Exports'):
                f = '=' + lookup(m, 'P', cl)
            elif m == 'FSR':
                f = '=' + lookup('FSR', 'P', cl) + '+' + lookup('Seed', 'P', cl)
            elif m == 'Beginning Stocks':
                if j == 0:
                    # Opening stocks recovered from the first month of *_Mo
                    f = ('=' + lookup('STOCKS', 'D', cl)
                         + '-' + lookup('Production', 'D', cl) + '-' + lookup('Imports', 'D', cl)
                         + '+' + lookup('Exports', 'D', cl) + '+' + lookup('Food & Processing', 'D', cl)
                         + '+' + lookup('FSR', 'D', cl) + '+' + lookup('Seed', 'D', cl))
                else:
                    f = f'={prev}{R("Ending Stocks")}'          # ENGINE_RULES 5.5
            elif m == 'Total Supply':
                f = f'={cl}{R("Beginning Stocks")}+{cl}{R("Imports")}+{cl}{R("Production")}'
            elif m == 'Total Disposition':
                f = f'={cl}{R("Food")}+{cl}{R(use)}+{cl}{R("FSR")}+{cl}{R("Exports")}'
            elif m == 'Ending Stocks':
                f = f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}'
            elif m == 'Proportion Harvested':
                f = f'=IF({cl}{R("Planted Area")}<>0,{cl}{R("Harvested Area")}/{cl}{R("Planted Area")},"")'
            elif m in ('Stocks-to-Use', 'STU Days'):
                td = f'{cl}{R("Total Disposition")}'
                mult = '*365' if m == 'STU Days' else ''
                f = f'=IF({td}<>0,{cl}{R("Ending Stocks")}/{td}{mult},"")'
            elif m == 'CHECK':
                # annual Ending Stocks vs *_Mo closing STOCKS
                f = f'={cl}{R("Ending Stocks")}-' + lookup('STOCKS', 'P', cl)
            cell = ws.cell(r, c, f)
            cell.font, cell.alignment = font, RIGHT
            cell.number_format = YR_FMT.get(m, FMT_KMT)

    _yr_finish(ws, rows, len(crop_years))
    return write_ratio_rows(ws, rows['CHECK'] + RATIO_GAP + 1, label, len(crop_years),
                            standard_ratios(rows, use))


# ------------------------------------------- byproduct tables (oils, meals)
# Vegetable oils and protein meals share one table engine, driven by a spec:
#   * a <Product>_Mo tab (metric-major, like the grain *_Mo tabs),
#   * a table under the host crop's *_Yr tab (looked up from <Product>_Mo),
#   * a combined *_Yr tab summing the two products by marketing-year label.
FMT_KMT_1 = '#,##0.0'
BYPRODUCT_UNIT = {'Yield': '%', 'Stocks-to-Use': '%', 'STU Days': 'Days'}
BYPRODUCT_FMT = {'Yield': FMT_PCT, 'Stocks-to-Use': FMT_PCT, 'STU Days': '0'}
_NOTE_CHECK = 'Total Supply - Total Disposition - Ending Stocks (0 when balanced).'

OIL_SPEC = {
    'family': 'Vegetable oils', 'registry': VEG_OILS, 'key': 'Oil', 'stocks': 'Stocks',
    'plug': 'Food (Residual)',
    'yr_metrics': ['Crush', 'Yield', '',
                   'Beginning Stocks', 'Imports', 'Production', 'Total Supply', '',
                   'Food (Residual)', 'Industrial', 'Exports', 'Total Disposition', '',
                   'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK'],
    'mo_metrics': ['Crush', 'Yield', 'Beginning Stocks', 'Imports', 'Production', 'Total Supply',
                   'Exports', 'Industrial', 'Food (Residual)', 'Total Disposition',
                   'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK'],
    'disp': ['Food (Residual)', 'Industrial', 'Exports'],
    # sheet metric -> fact-table column
    'column': {'Crush': 'Crush', 'Imports': 'Imports', 'Production': 'Production',
               'Exports': 'Exports', 'Industrial': 'Industrial', 'Food (Residual)': 'Food',
               'Ending Stocks': 'Ending Stocks', 'Beginning Stocks': 'Beginning Stocks'},
    'ratios': [('Ratio_I/Prod', ['Industrial']), ('Ratio_FSR/Prod', ['Food (Residual)']),
               ('Ratio_Exp/Prod', ['Exports'])],
    'group_ratios': [('Ratio_I/Prod', ['Industrial']), ('Ratio_FSR/Prod', ['Food (Residual)']),
                     ('Ratio_Exp/Prod', ['Exports'])],
    'notes': {
        'Crush': 'Seed crushed in the oil marketing year, StatCan 32100352 (tonnes x 0.001).',
        'Production': 'Crush x oil yield = StatCan 32100352 "Oil produced".',
        'Industrial': ('Biofuel feedstock: StatCan 25100082 "Vegetable oils, total" Inputs x proxy '
                       'weight (apparent supply = Production + Imports - Exports, canola vs soy, '
                       'monthly). Before Jan 2020: 2020 average monthly intake flatlined back.'),
        'Food (Residual)': ('Balancing plug: Total Supply - Exports - Industrial - Ending Stocks '
                            '(monthly, then summed).'),
        'Ending Stocks': 'StatCan 32100352 "Oil produced (stocks)" at the marketing-year end.',
        'CHECK': _NOTE_CHECK},
    'group_tab': VEGOIL_GROUP_TAB, 'group_title': VEGOIL_GROUP_TITLE,
    'group_label': VEGOIL_GROUP_LABEL,
}
MEAL_SPEC = {
    'family': 'Protein meals', 'registry': PROTEIN_MEALS, 'key': 'Meal', 'stocks': 'Meal Stocks',
    'plug': 'Feed & Residual',
    'yr_metrics': ['Crush', 'Yield', '',
                   'Beginning Stocks', 'Imports', 'Production', 'Total Supply', '',
                   'Feed & Residual', 'Exports', 'Total Disposition', '',
                   'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK'],
    'mo_metrics': ['Crush', 'Yield', 'Beginning Stocks', 'Imports', 'Production', 'Total Supply',
                   'Exports', 'Feed & Residual', 'Total Disposition',
                   'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK'],
    'disp': ['Feed & Residual', 'Exports'],
    'column': {'Crush': 'Crush', 'Imports': 'Imports', 'Production': 'Production',
               'Exports': 'Exports', 'Feed & Residual': 'Feed & Residual',
               'Ending Stocks': 'Ending Stocks', 'Beginning Stocks': 'Beginning Stocks'},
    'ratios': [('Ratio_F&R/Prod', ['Feed & Residual']), ('Ratio_Exp/Prod', ['Exports'])],
    'group_ratios': [('Ratio_Feed/Prod', ['Feed & Residual']), ('Ratio_Exp/Prod', ['Exports'])],
    'notes': {
        'Crush': 'Seed crushed in the meal marketing year, StatCan 32100352 (tonnes x 0.001).',
        'Production': 'Crush x meal yield = StatCan 32100352 "Meal produced".',
        'Feed & Residual': ('Balancing plug = domestic disappearance (mostly feed): Total Supply - '
                            'Exports - Ending Stocks (monthly, then summed).'),
        'Ending Stocks': 'StatCan 32100352 "Meal produced (stocks)" at the marketing-year end.',
        'CHECK': _NOTE_CHECK},
    'group_tab': MEAL_GROUP_TAB, 'group_title': MEAL_GROUP_TITLE,
    'group_label': MEAL_GROUP_LABEL,
}
for _s in (OIL_SPEC, MEAL_SPEC):
    _s['lookup'] = [m for m in _s['column'] if m != 'Beginning Stocks'] + ['Beginning Stocks']
    _s['required'] = [_s['column'][m] for m in _s['disp'] + ['Production', 'Imports', 'Ending Stocks']]


def _attr(col: str) -> str:
    """Fact-table column -> itertuples attribute ('Feed & Residual' -> 'Feed___Residual')."""
    return re.sub(r'\W', '_', col)


def _check_rule(ws, rng: str):
    ok = PatternFill('solid', start_color=VEGOIL_CHECK_OK_FILL, end_color=VEGOIL_CHECK_OK_FILL)
    ws.conditional_formatting.add(
        rng, CellIsRule(operator='between', formula=[f'-{VEGOIL_CHECK_TOL}', VEGOIL_CHECK_TOL], fill=ok))


def byproduct_crop_years(crop_years: List[int]) -> List[int]:
    return [cy for cy in crop_years if cy >= VEG_OIL_FIRST_CROP_YEAR]


def write_byproduct_monthly_tab(ws, spec: Dict, key: str, monthly: pd.DataFrame,
                                crop_years: List[int], start_month: int) -> Dict:
    """CanolaOil_Mo / SoyOil_Mo / CanolaMeal_Mo / Soymeal_Mo: metric-major like
    the grain *_Mo tabs, one row per marketing year from 2015-16. A month is
    filled only when every flow and its closing stock are known; the total
    column (P) only for complete years. Stocks roll Beg[t] = End[t-1]; End is
    StatCan's month-end stock."""
    cfg = spec['registry'][key]
    metrics, column = spec['mo_metrics'], spec['column']
    months = [_MONTHS[(start_month - 1 + i) % 12] for i in range(12)]
    n_cy = len(crop_years)
    headers = ['Commodity', 'Metric', 'Crop Year'] + months + [f'{months[0]}/{months[-1]}']
    for c, h in enumerate(headers, 1):
        _style(ws.cell(1, c, h), font=FONT_HDR, align=CENTER, fill=FILL_HDR)

    block = {m: 2 + i * n_cy for i, m in enumerate(metrics)}
    row = lambda m, i: block[m] + i
    g = monthly[monthly[spec['key']] == key]
    ok = g[spec['required']].notna().all(axis=1)
    data = {(int(r.Crop_Year), int(r.CY_Month)): r
            for r in g[ok].rename(columns=_attr).itertuples(index=False)}
    complete = {cy: all((cy, k) in data for k in range(1, 13)) for cy in crop_years}

    def days(cy, k):                       # days in crop-year month k
        month = (start_month + k - 2) % 12 + 1
        return calendar.monthrange(cy if month >= start_month else cy + 1, month)[1]

    values = [m for m in metrics if m in column and m != 'Beginning Stocks']
    for m in metrics:
        fmt = BYPRODUCT_FMT.get(m, FMT_KMT_1)
        bold = m in YR_BOLD_METRICS
        for i, cy in enumerate(crop_years):
            r = row(m, i)
            R = lambda name: row(name, i)
            font = FONT_BOLD if bold else FONT
            _style(ws.cell(r, 1, cfg['label']), align=LEFT, font=font)
            _style(ws.cell(r, 2, m), align=LEFT, font=font)
            _style(ws.cell(r, 3, crop_year_label(cy)), align=LEFT, font=font)
            for k in range(1, 14):
                _style(ws.cell(r, 3 + k), fmt=fmt, font=FONT_BOLD if (bold or k == 13) else FONT,
                       fill=FILL_SUM if k == 13 else None)
            cols = [(k, get_column_letter(3 + k)) for k in range(1, 13) if (cy, k) in data]
            if complete[cy]:
                cols.append((13, 'P'))
            for k, cl in cols:
                if m in values and k < 13:
                    v = float(getattr(data[(cy, k)], _attr(column[m])))
                elif m in values:
                    v = f'=O{r}' if m == 'Ending Stocks' else f'=SUM(D{r}:O{r})'
                elif m == 'Beginning Stocks':
                    if k == 13:
                        v = f'=D{r}'
                    elif k > 1:
                        v = f'={get_column_letter(2 + k)}{R("Ending Stocks")}'
                    elif i > 0 and complete[crop_years[i - 1]]:
                        v = f'=O{row("Ending Stocks", i - 1)}'       # prior year's close
                    else:
                        v = float(data[(cy, 1)].Beginning_Stocks)
                        ws.cell(r, 4).comment = Comment(
                            f'Opening stocks {crop_year_label(cy)}: StatCan 32100352 month-end '
                            f'stock of the prior month.', 'CMFT Engine')
                elif m == 'Yield':
                    v = f'=IF(N({cl}{R("Crush")})<>0,{cl}{R("Production")}/{cl}{R("Crush")},"")'
                elif m == 'Total Supply':
                    v = f'={cl}{R("Beginning Stocks")}+{cl}{R("Imports")}+{cl}{R("Production")}'
                elif m == 'Total Disposition':
                    v = '=' + '+'.join(f'{cl}{R(d)}' for d in spec['disp'])
                elif m in ('Stocks-to-Use', 'STU Days'):
                    td = f'{cl}{R("Total Disposition")}'
                    mult = '' if m == 'Stocks-to-Use' else f'*{365 if k == 13 else days(cy, k)}'
                    v = f'=IF({td}<>0,{cl}{R("Ending Stocks")}/{td}{mult},"")'
                elif m == 'CHECK':
                    v = (f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}'
                         f'-{cl}{R("Ending Stocks")}')
                ws.cell(r, 3 + k).value = v

    # Subtotal blocks: thin rule above, thick rule below; green CHECK
    rule_top = Side(style=VEGOIL_SUBTOTAL_TOP, color='000000')
    rule_bottom = Side(style=VEGOIL_SUBTOTAL_BOTTOM, color='000000')
    for m in ('Total Supply', 'Total Disposition'):
        first, end = block[m], block[m] + n_cy - 1
        for c in range(1, 17):
            a = ws.cell(first, c)
            a.border = Border(left=THIN, right=THIN, top=rule_top, bottom=a.border.bottom)
            b = ws.cell(end, c)
            b.border = Border(left=THIN, right=THIN, top=b.border.top, bottom=rule_bottom)
    _check_rule(ws, f'D{block["CHECK"]}:P{block["CHECK"] + n_cy - 1}')
    ws.column_dimensions['A'].width = MO_WIDTH_LABEL
    ws.column_dimensions['B'].width = MO_WIDTH_LABEL
    for c in range(3, 17):
        ws.column_dimensions[get_column_letter(c)].width = MO_WIDTH_DATA + 1
    ws.freeze_panes = 'D2'
    return {'block': block, 'n_cy': n_cy, 'complete': complete, 'crop_years': crop_years}


def _byproduct_body(ws, spec: Dict, first_row: int, label: str, crop_years: List[int],
                    years, value, beginning, ratios) -> Dict[str, int]:
    """Byproduct table rows (from 'Crush' down to the ratio rows). value(metric, cl)
    and beginning(cy, cl, prev, rows) return the formula for a looked-up cell."""
    rows = {m: first_row + i for i, m in enumerate(spec['yr_metrics']) if m}
    R = lambda m: rows[m]
    for m, r in rows.items():
        font = FONT_BOLD if m in YR_BOLD_METRICS else FONT
        for c, v, al in ((1, label, LEFT), (2, m, LEFT), (3, BYPRODUCT_UNIT.get(m, 'KMT'), CENTER)):
            cell = ws.cell(r, c, v)
            cell.font, cell.alignment = font, al
        if m in spec['notes']:
            ws.cell(r, 2).comment = Comment(spec['notes'][m], 'CMFT Engine')
        for j, cy in enumerate(crop_years):
            if cy not in years:
                continue
            cl, prev = get_column_letter(4 + j), get_column_letter(3 + j)
            if m == 'Beginning Stocks':
                v = beginning(cy, cl, prev, rows)
            elif m in spec['lookup']:
                v = value(m, cl)
            elif m == 'Yield':
                v = f'=IF(N({cl}{R("Crush")})<>0,{cl}{R("Production")}/{cl}{R("Crush")},"")'
            elif m == 'Total Supply':
                v = f'={cl}{R("Beginning Stocks")}+{cl}{R("Imports")}+{cl}{R("Production")}'
            elif m == 'Total Disposition':
                v = '=' + '+'.join(f'{cl}{R(d)}' for d in spec['disp'])
            elif m in ('Stocks-to-Use', 'STU Days'):
                td, mult = f'{cl}{R("Total Disposition")}', '*365' if m == 'STU Days' else ''
                v = f'=IF({td}<>0,{cl}{R("Ending Stocks")}/{td}{mult},"")'
            elif m == 'CHECK':
                v = (f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}'
                     f'-{cl}{R("Ending Stocks")}')
            cell = ws.cell(r, 4 + j, v)
            cell.font, cell.alignment = font, RIGHT
            cell.number_format = BYPRODUCT_FMT.get(m, FMT_KMT)

    last_col = 3 + len(crop_years)
    line = Border(top=Side(style=VEGOIL_SUBTOTAL_TOP, color='000000'),
                  bottom=Side(style=VEGOIL_SUBTOTAL_BOTTOM, color='000000'))
    for m in ('Total Supply', 'Total Disposition'):
        for c in range(1, last_col + 1):
            ws.cell(rows[m], c).border = line
    _check_rule(ws, f'D{R("CHECK")}:{get_column_letter(last_col)}{R("CHECK")}')

    p = R('Production')
    rows['_ratio_last'] = write_ratio_rows(
        ws, R('CHECK') + RATIO_GAP + 1, label, len(crop_years),
        [(name, [R(m) for m in nums], p) for name, nums in ratios])
    return rows


def write_byproduct_table(ws, spec: Dict, top: int, key: str, years, crop_years: List[int],
                          span: str) -> Dict[str, int]:
    """Byproduct balance sheet below a crop's *_Yr table, looked up from the
    product's *_Mo tab total column (P) by metric name and marketing-year
    label. Only complete marketing years are filled; earlier years stay blank."""
    cfg = spec['registry'][key]
    mo, hdr = f"'{cfg['mo_tab']}'", top + 1
    _title(ws, top, cfg['title'], span)
    _header(ws, hdr, crop_years)

    def value(m, cl):
        return (f'=SUMIFS({mo}!$P:$P,{mo}!$B:$B,"{m}",'
                f'{mo}!$C:$C,RIGHT({cl}${hdr},5))')

    def beginning(cy, cl, prev, rows):
        # prior year's closing stock when that year is on the sheet (RULE-4.1.B)
        return f'={prev}{rows["Ending Stocks"]}' if (cy - 1) in years else value('Beginning Stocks', cl)

    return _byproduct_body(ws, spec, top + 2, cfg['label'], crop_years, years, value, beginning,
                           spec['ratios'])


def write_byproduct_group_tab(ws, spec: Dict, table_rows: Dict, crop_years: List[int], years,
                              memo: Dict = None):
    """Can_Vegoils_Yr / Can_ProteinMeal_Yr: the two products summed under each
    shared marketing-year label (e.g. Canola Sep-Aug + Soy Oct-Sep). Each line
    sums the two product tables, found by column A label and metric name in
    the same crop-year column. Filled only where both products have a
    complete year."""
    head, detail = spec['group_title']
    ws['A1'] = CellRichText(
        TextBlock(InlineFont(rFont=ENGINE_FONT_NAME, sz=VEGOIL_GROUP_TITLE_SIZE, b=True), head),
        TextBlock(InlineFont(rFont=ENGINE_FONT_NAME, sz=ENGINE_FONT_SIZE, i=True), detail))
    ws.row_dimensions[1].height = YR_TITLE_ROW_HEIGHT
    _header(ws, 2, crop_years)
    srcs = [(f"'{sheet}'", spec['registry'][key]['label']) for key, (sheet, _) in table_rows.items()]

    def value(m, cl):
        return '=' + '+'.join(f'SUMIFS({s}!{cl}:{cl},{s}!$A:$A,"{lab}",{s}!$B:$B,"{m}")'
                              for s, lab in srcs)

    label = spec['group_label']
    rows = _byproduct_body(ws, spec, 3, label, crop_years, years, value,
                           lambda cy, cl, prev, rows: value('Beginning Stocks', cl),
                           spec['group_ratios'])
    if memo is not None:
        ws.cell(rows['Industrial'], 2).comment = Comment(
            'Canola Oil + Soy Oil industrial use = StatCan 25100082 "Vegetable oils, total" Inputs, '
            'each share summed over its own marketing year (Canola Sep-Aug, Soy Oct-Sep). The memo '
            'row below shows the unsplit 25100082 total for Sep-Aug; the two differ only by the '
            'one-month offset of the soy share. Before Jan 2020 the total is the flatlined 2020 '
            'average.', 'CMFT Engine')
        r = rows['_ratio_last'] + RATIO_GAP + 1
        ws.cell(r, 1, label).font = FONT
        ws.cell(r, 2, 'Memo: 25100082 Feedstock (Sep/Aug)').font = FONT
        ws.cell(r, 3, 'KMT').font, ws.cell(r, 3).alignment = FONT, CENTER
        for j, cy in enumerate(crop_years):
            if cy in years and cy in memo:
                cell = ws.cell(r, 4 + j, float(memo[cy]))
                cell.font, cell.alignment, cell.number_format = FONT, RIGHT, FMT_KMT
        rows['_memo'] = r

    ws.sheet_view.showGridLines = True
    ws.column_dimensions['A'].width = YR_WIDTH_COMMODITY + 2
    ws.column_dimensions['B'].width = YR_WIDTH_METRIC + 8
    ws.column_dimensions['C'].width = YR_WIDTH_UNIT
    for j in range(len(crop_years)):
        ws.column_dimensions[get_column_letter(4 + j)].width = YR_WIDTH_YEAR
    ws.freeze_panes = 'D3'
    return rows


# ------------------------------------------------------- group *_Yr tabs
FMT_YIELD_MT = '0.000'


def write_group_tab(ws, label: str, members: List[str], crop_years: List[int],
                    complete_years=None):
    """Group aggregate (e.g. Can_Cereals_Yr): live sums of the members'
    *_Yr tabs, same styling as the commodity *_Yr tabs.
      Sums:     areas, stocks, supply/disposition lines, totals
      Derived:  Yield (MT/AC) = Production KMT / (Harvested MLN AC x 1,000)
                Proportion Harvested, Stocks-to-Use, STU Days
      CHECK  =  Total Supply - Total Disposition - Ending Stocks (must be 0)"""
    use = 'Crush' if all(get_group(m) == 'Oilseed' for m in members) else 'Industrial'
    metrics = ['Planted Area', 'Harvested Area', 'Proportion Harvested', 'Yield', '',
               'Beginning Stocks', 'Imports', 'Production', 'Total Supply', '',
               'Food', use, 'FSR', 'Exports', 'Total Disposition', '',
               'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK']
    summed = {'Planted Area', 'Harvested Area', 'Beginning Stocks', 'Imports', 'Production',
              'Total Supply', 'Food', use, 'FSR', 'Exports', 'Total Disposition', 'Ending Stocks'}
    # (sheet, column-A label): the label keeps the crop's own table apart from
    # the oil / meal tables drawn below it with the same metric names
    sheets = [(f"'{m}_Yr'", YR_LABEL.get(m, m)) for m in members]
    unit = {**YR_UNIT, 'Yield': 'MT/AC'}
    fmt = {**YR_FMT, 'Yield': FMT_YIELD_MT}

    _title(ws, 1, label)
    _header(ws, 2, crop_years)

    rows = {m: 3 + i for i, m in enumerate(metrics) if m}
    R = lambda m: rows[m]

    for i, m in enumerate(metrics):
        r = 3 + i
        if not m:
            continue
        font = FONT_BOLD if m in YR_BOLD_METRICS else FONT
        for c, v, al in ((1, label, LEFT), (2, m, LEFT), (3, unit.get(m, 'KMT'), CENTER)):
            cell = ws.cell(r, c, v)
            cell.font, cell.alignment = font, al
        for j, cy in enumerate(crop_years):
            if complete_years is not None and cy not in complete_years:
                continue
            cl = get_column_letter(4 + j)
            if m in summed:
                # same crop-year column on every member tab, row found by label + metric
                f = '=' + '+'.join(f'SUMIFS({s}!{cl}:{cl},{s}!$A:$A,"{lab}",{s}!$B:$B,"{m}")'
                                   for s, lab in sheets)
            elif m == 'Yield':
                h, p = f'{cl}{R("Harvested Area")}', f'{cl}{R("Production")}'
                f = f'=IF({h}<>0,{p}/({h}*1000),"")'
            elif m == 'Proportion Harvested':
                f = f'=IF({cl}{R("Planted Area")}<>0,{cl}{R("Harvested Area")}/{cl}{R("Planted Area")},"")'
            elif m in ('Stocks-to-Use', 'STU Days'):
                td = f'{cl}{R("Total Disposition")}'
                mult = '*365' if m == 'STU Days' else ''
                f = f'=IF({td}<>0,{cl}{R("Ending Stocks")}/{td}{mult},"")'
            elif m == 'CHECK':
                f = f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}-{cl}{R("Ending Stocks")}'
            cell = ws.cell(r, 4 + j, f)
            cell.font, cell.alignment = font, RIGHT
            cell.number_format = fmt.get(m, FMT_KMT)

    _yr_finish(ws, rows, len(crop_years))
    write_ratio_rows(ws, rows['CHECK'] + RATIO_GAP + 1, label, len(crop_years),
                     standard_ratios(rows, use))


# ------------------------------------------------------------------- run
def run_exporter():
    print('=' * 60)
    print('CMFT ENGINE - EXCEL EXPORTER (metric-major *_Mo)')
    print('=' * 60)
    df = load_master()
    crop_years = list(range(SCOPE_FIRST_CROP_YEAR, SCOPE_LAST_CROP_YEAR + 1))
    print(f'    Scope: {crop_year_label(crop_years[0])} to {crop_year_label(crop_years[-1])} '
          f'({len(crop_years)} crop years), {len(df)} master rows')

    # Byproduct families (vegetable oils, protein meals)
    fams = []
    for spec, loader, starts in ((OIL_SPEC, load_vegoil, oil_start_months),
                                 (MEAL_SPEC, load_meal, meal_start_months)):
        monthly, annual = loader()
        fams.append({
            'spec': spec, 'monthly': monthly, 'annual': annual, 'starts': starts(),
            'complete': {k: (set(annual.loc[annual[spec['key']] == k, 'Crop_Year']) & set(crop_years)
                             if not annual.empty else set()) for k in spec['registry']},
            'tables': {}, 'mo': {}, 'group': None})
    by_years = byproduct_crop_years(crop_years)

    crop_complete = {}                 # commodity -> crop years with all 12 months
    wb = Workbook()
    wb.remove(wb.active)
    for commodity in TIER1_COMMODITIES:
        df_c = df[df['Commodity_Norm'] == commodity].rename(columns={'Beginning Stocks': 'Beginning_Stocks'})
        if df_c.empty:
            print(f'    Skipping {commodity} (no data)')
            continue
        mo_name, yr_name = f'{commodity}_Mo', f'{commodity}_Yr'
        write_monthly_tab(wb.create_sheet(mo_name), df_c, commodity, crop_years)
        ws_yr = wb.create_sheet(yr_name)
        n = df_c.groupby('Crop_Year')['CY_Month'].nunique()
        crop_complete[commodity] = set(n[n == 12].index) & set(crop_years)
        last = write_annual_tab(ws_yr, commodity, crop_years, mo_name, crop_complete[commodity])
        print(f'    {mo_name} / {yr_name}')

        hosted = []                      # oil table, then meal table, under the crop table
        for fam in fams:
            spec = fam['spec']
            for key, cfg in spec['registry'].items():
                if cfg['host'] != commodity or fam['annual'].empty:
                    continue
                s = fam['starts'][key]
                span = f"{_MONTHS[s - 1].title()}/{_MONTHS[(s - 2) % 12].title()}"
                rows = write_byproduct_table(ws_yr, spec, last + RATIO_GAP + 1, key,
                                             fam['complete'][key], crop_years, span)
                last = rows['_ratio_last']
                fam['tables'][key] = (yr_name, rows)
                hosted.append((fam, key, s))
                print(f'      + {cfg["title"]} table ({span}) at rows {rows["Crush"] - 2}-{last}')
        for fam, key, s in hosted:
            cfg = fam['spec']['registry'][key]
            fam['mo'][key] = write_byproduct_monthly_tab(
                wb.create_sheet(cfg['mo_tab']), fam['spec'], key, fam['monthly'], by_years, s)
            print(f'    {cfg["mo_tab"]}')

    for fam in fams:
        spec = fam['spec']
        if len(fam['tables']) != len(spec['registry']):
            continue
        both = set.intersection(*fam['complete'].values())
        memo = None
        if spec is OIL_SPEC:
            c = fam['monthly'][fam['monthly']['Oil'] == 'Canola Oil']
            c = c.groupby('Crop_Year')['Feedstock_Total'].agg(['sum', 'count'])
            memo = c.loc[c['count'] == 12, 'sum'].to_dict()
        fam['group'] = write_byproduct_group_tab(wb.create_sheet(spec['group_tab']), spec,
                                                 fam['tables'], crop_years, both, memo)
        print(f'    {spec["group_tab"]}: {" + ".join(spec["registry"])}')

    # Group aggregate tabs, from whichever member *_Yr tabs exist
    for tab, (label, members) in GROUP_TABS.items():
        present = [m for m in members if f'{m}_Yr' in wb.sheetnames]
        missing = [m for m in members if m not in present]
        if not present:
            print(f'    Skipping {tab} (no member tabs)')
            continue
        both = set.intersection(*(crop_complete[m] for m in present))
        write_group_tab(wb.create_sheet(tab), label, present, crop_years, both)
        print(f'    {tab}: {" + ".join(present)}'
              + (f'   (WARNING: missing {missing})' if missing else ''))

    # Tab order: summary tabs first (SUMMARY_TAB_ORDER), then commodity tabs in build order
    order = [t for t in SUMMARY_TAB_ORDER if t in wb.sheetnames]
    order += [t for t in wb.sheetnames if t not in order]
    wb._sheets = [wb[t] for t in order]
    wb.active = 0

    TARGET_WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
    wb.save(TARGET_WORKBOOK)
    wb.close()                                   # ENGINE_RULES 6.3
    print(f'    Saved to: {TARGET_WORKBOOK}')
    byproduct_summary(fams, crop_years)
    return TARGET_WORKBOOK


# ------------------------------------------------------- byproduct summary
_MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']


def _load_fact(path, annual_fn):
    """(monthly, annual) from a data_store fact table; empty if not built yet."""
    if not path.exists():
        print(f'    WARNING: {path.name} not found - run the balancer; its tables are skipped')
        return pd.DataFrame(), pd.DataFrame()
    monthly = pd.read_csv(path, parse_dates=['Date'])
    return monthly, annual_fn(monthly)


def load_vegoil():
    return _load_fact(VEGOIL_FACT_TABLE, annual_vegoil)


def load_meal():
    return _load_fact(MEAL_FACT_TABLE, annual_meal)


def _filled_rows(ws, first_row: int, last_row: int, first_col: int, last_col: int) -> int:
    return sum(1 for r in range(first_row, last_row + 1)
               if any(ws.cell(r, c).value is not None for c in range(first_col, last_col + 1)))


def _mo_tieout(ws, info, spec, start: int, surveyed: pd.Series) -> Dict:
    """Roll the written *_Mo values in Python (openpyxl does not evaluate
    formulas): residual per month, closing stocks vs StatCan 32100352, and
    the Beg[t] = End[t-1] chain across marketing years."""
    block, cys = info['block'], info['crop_years']
    val = lambda m, i, k: ws.cell(block[m] + i, 3 + k).value
    worst_res = worst_survey = 0.0
    months = chain_breaks = 0
    prev_end = None
    for i, cy in enumerate(cys):
        for k in range(1, 13):
            end = val('Ending Stocks', i, k)
            if not isinstance(end, (int, float)):
                prev_end = None
                continue
            beg = val('Beginning Stocks', i, k)
            if isinstance(beg, str):                  # formula -> prior month's close
                if prev_end is None:
                    chain_breaks += 1
                    continue
                beg = prev_end
            flows = val('Imports', i, k) + val('Production', i, k) - sum(val(d, i, k) for d in spec['disp'])
            worst_res = max(worst_res, abs(beg + flows - end))
            month = (start + k - 2) % 12 + 1
            d = pd.Timestamp(cy if month >= start else cy + 1, month, 1)
            if d in surveyed.index and pd.notna(surveyed[d]):
                worst_survey = max(worst_survey, abs(end - surveyed[d]))
            months += 1
            prev_end = end
    return {'months': months, 'res': worst_res, 'survey': worst_survey, 'breaks': chain_breaks}


def byproduct_summary(fams, crop_years):
    """Re-open the saved workbook and print: sheets rendered, filled rows,
    zero-variance / stock tie-out checks, proxy weights and final tab order."""
    from openpyxl import load_workbook
    wb = load_workbook(TARGET_WORKBOOK, rich_text=True)
    crush = load_crush()
    tol = float(VEGOIL_CHECK_TOL)

    print('\n' + '=' * 60)
    print('BYPRODUCT SUMMARY (vegetable oils, protein meals)')
    print('=' * 60)
    tbl = []
    for fam in fams:
        spec, annual = fam['spec'], fam['annual']
        col = spec['column']
        for key, cfg in spec['registry'].items():
            if key not in fam['mo']:
                continue
            ws, info = wb[cfg['mo_tab']], fam['mo'][key]
            t = _mo_tieout(ws, info, spec, fam['starts'][key], crush.loc[cfg['crush'], spec['stocks']])
            done = [crop_year_label(cy) for cy, ok in info['complete'].items() if ok]
            tbl.append({'Sheet': cfg['mo_tab'],
                        'Rows': _filled_rows(ws, 2, ws.max_row, 4, 16),
                        'Complete years': f'{done[0]} to {done[-1]} ({len(done)})' if done else 'none',
                        'Max |residual| KMT': t['res'],
                        'Max |End - StatCan| KMT': t['survey'],
                        'Chain breaks': t['breaks']})
            sheet, rows = fam['tables'][key]
            a = annual[(annual[spec['key']] == key) & annual['Crop_Year'].isin(crop_years)]
            tbl.append({'Sheet': f'{sheet} ({cfg["title"]}, rows {rows["Crush"] - 2}-{rows["_ratio_last"]})',
                        'Rows': _filled_rows(wb[sheet], rows['Crush'], rows['_ratio_last'],
                                             4, 3 + len(crop_years)),
                        'Complete years': f'{crop_year_label(a.Crop_Year.min())} to '
                                          f'{crop_year_label(a.Crop_Year.max())} ({len(a)})',
                        'Max |residual| KMT': a['CHECK'].abs().max(),
                        'Max |End - StatCan| KMT': float('nan'), 'Chain breaks': 0})
        if fam['group']:
            both = annual[annual['Crop_Year'].isin(set.intersection(*fam['complete'].values()))]
            s = both.groupby('Crop_Year')[[col[m] for m in
                                           ['Beginning Stocks', 'Production', 'Imports', 'Ending Stocks']
                                           + spec['disp']]].sum()
            res = (s[col['Beginning Stocks']] + s[col['Production']] + s[col['Imports']]
                   - s[[col[d] for d in spec['disp']]].sum(axis=1) - s[col['Ending Stocks']]).abs().max()
            ws = wb[spec['group_tab']]
            tbl.append({'Sheet': spec['group_tab'],
                        'Rows': _filled_rows(ws, 3, ws.max_row, 4, 3 + len(crop_years)),
                        'Complete years': f'{crop_year_label(s.index.min())} to '
                                          f'{crop_year_label(s.index.max())} ({len(s)})',
                        'Max |residual| KMT': res, 'Max |End - StatCan| KMT': float('nan'),
                        'Chain breaks': 0})
    t = pd.DataFrame(tbl)
    t['Zero variance'] = np.where((t['Max |residual| KMT'] <= tol) & (t['Chain breaks'] == 0),
                                  'PASS', 'FAIL')
    for c in ('Max |residual| KMT', 'Max |End - StatCan| KMT'):
        t[c] = t[c].map(lambda x: '-' if pd.isna(x) else f'{x:.1e}')
    print(t.to_string(index=False))
    print(f'  Tolerance +/-{VEGOIL_CHECK_TOL} KMT; residual = Beg + Prod + Imp - disposition - End '
          f'(rolled from written values / engine totals).')

    for fam in fams:
        if fam['group']:
            a1 = wb[fam['spec']['group_tab']]['A1'].value
            runs = ([(str(b.text), b.font.sz, bool(b.font.b), bool(b.font.i)) for b in a1]
                    if isinstance(a1, CellRichText) else [])
            print(f"\n{fam['spec']['group_tab']}!A1 runs (text, pt, bold, italic): {runs}")

    oil = next(f for f in fams if f['spec'] is OIL_SPEC)['monthly']
    if not oil.empty:
        w = oil[(oil['Oil'] == 'Canola Oil') & (oil['Date'].dt.year >= 2020)].copy()
        w['Year'] = w['Date'].dt.year
        g = w.groupby('Year').agg(Months=('Weight', 'count'), Feedstock_KMT=('Feedstock_Total', 'sum'),
                                  W_Canola=('Weight', 'mean'),
                                  Carried=('Industrial_Basis', lambda s: s.str.contains('carried').sum()))
        g['W_Soy'] = 1 - g['W_Canola']
        print('\nOil proxy weights, 25100082 feedstock (calendar-year mean of monthly weights):')
        print(g[['Months', 'Feedstock_KMT', 'W_Canola', 'W_Soy', 'Carried']].round(3).to_string())

    names = wb.sheetnames
    head = names[:len([t for t in SUMMARY_TAB_ORDER if t in names])]
    ok = head == [t for t in SUMMARY_TAB_ORDER if t in names]
    print(f"\nTab order ({len(names)} tabs) - summary tabs first: {'VERIFIED' if ok else 'WRONG'}")
    for i, n in enumerate(names, 1):
        print(f'  {i:2}. {n}')
    wb.close()


if __name__ == '__main__':
    run_exporter()
