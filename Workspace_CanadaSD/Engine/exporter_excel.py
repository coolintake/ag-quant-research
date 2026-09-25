"""
CMFT Engine - Excel Exporter

Writes paired *_Mo / *_Yr tabs matching the CanadaSD_Main.xlsx reference
layout (Wheat_Mo).

*_Mo (monthly) - metric-major, contiguous, no spacer rows (ENGINE_RULES 6.2):
    A Commodity (StatCan series name) | B Metric | C Crop Year ('02-03')
    D..O the 12 crop-year months ('AUG'..'JUL', or 'SEP'..'AUG' for soybeans)
    P 'AUG/JUL' total
  Blocks, each with one row per crop year in scope (2002/03-2024/25):
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

from pathlib import Path
from typing import Dict, List
import warnings

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

from config.paths import DATA_STORE_DIR, CANADASD_MAIN_XLSX
from config.styles import (
    ENGINE_FONT_NAME, ENGINE_FONT_SIZE, ENGINE_TITLE_SIZE,
    MO_WIDTH_LABEL, MO_WIDTH_DATA, MO_HEADER_FILL,
    YR_HEADER_FILL, YR_CHECK_OK_FILL, YR_WIDTH_COMMODITY, YR_WIDTH_METRIC,
    YR_WIDTH_UNIT, YR_WIDTH_YEAR, YR_LABEL, YR_BOLD_METRICS,
    YR_SUBTOTAL_ROWS, YR_SUBTOTAL_LINE,
)
from config.commodities import (
    TIER1_COMMODITIES, DISPLAY_NAME, GROUP_TABS, SCOPE_FIRST_CROP_YEAR, SCOPE_LAST_CROP_YEAR,
    month_labels, crop_year_label, get_group,
)

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


def write_annual_tab(ws, commodity: str, crop_years: List[int], mo_sheet: str):
    """*_Yr tab: plain white cells with gridlines, black header row, 10pt.
    Values are looked up from *_Mo by metric name and crop-year label.

    Ending Stocks = Total Supply - Total Disposition. Because that makes the
    classic balance identity zero by construction, CHECK instead compares it
    with the closing STOCKS on the *_Mo tab: a non-zero CHECK means the
    annual and monthly tabs disagree."""
    metrics = yr_metrics(commodity)
    use = metrics[11]
    mo = f"'{mo_sheet}'"
    label = YR_LABEL.get(commodity, commodity)

    ws.cell(1, 1, label).font = FONT_TITLE
    headers = ['Commodity', 'Metric', 'Unit'] + [crop_year_label(cy) for cy in crop_years]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(2, c, h)
        cell.font, cell.fill, cell.alignment = FONT_HDR, FILL_HDR_YR, CENTER

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


# ------------------------------------------------------- group *_Yr tabs
FMT_YIELD_MT = '0.000'


def write_group_tab(ws, label: str, members: List[str], crop_years: List[int]):
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
    sheets = [f"'{m}_Yr'" for m in members]
    unit = {**YR_UNIT, 'Yield': 'MT/AC'}
    fmt = {**YR_FMT, 'Yield': FMT_YIELD_MT}

    ws.cell(1, 1, label).font = FONT_TITLE
    headers = ['Commodity', 'Metric', 'Unit'] + [crop_year_label(cy) for cy in crop_years]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(2, c, h)
        cell.font, cell.fill, cell.alignment = FONT_HDR, FILL_HDR_YR, CENTER

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
        for j in range(len(crop_years)):
            cl = get_column_letter(4 + j)
            if m in summed:
                # same crop-year column on every member tab, row found by metric name
                f = '=' + '+'.join(f'SUMIFS({s}!{cl}:{cl},{s}!$B:$B,"{m}")' for s in sheets)
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


# ------------------------------------------------------------------- run
def run_exporter():
    print('=' * 60)
    print('CMFT ENGINE - EXCEL EXPORTER (metric-major *_Mo)')
    print('=' * 60)
    df = load_master()
    crop_years = list(range(SCOPE_FIRST_CROP_YEAR, SCOPE_LAST_CROP_YEAR + 1))
    print(f'    Scope: {crop_year_label(crop_years[0])} to {crop_year_label(crop_years[-1])} '
          f'({len(crop_years)} crop years), {len(df)} master rows')

    wb = Workbook()
    wb.remove(wb.active)
    for commodity in TIER1_COMMODITIES:
        df_c = df[df['Commodity_Norm'] == commodity].rename(columns={'Beginning Stocks': 'Beginning_Stocks'})
        if df_c.empty:
            print(f'    Skipping {commodity} (no data)')
            continue
        mo_name, yr_name = f'{commodity}_Mo', f'{commodity}_Yr'
        write_monthly_tab(wb.create_sheet(mo_name), df_c, commodity, crop_years)
        write_annual_tab(wb.create_sheet(yr_name), commodity, crop_years, mo_name)
        print(f'    {mo_name} / {yr_name}')

    # Group aggregate tabs, from whichever member *_Yr tabs exist
    for tab, (label, members) in GROUP_TABS.items():
        present = [m for m in members if f'{m}_Yr' in wb.sheetnames]
        missing = [m for m in members if m not in present]
        if not present:
            print(f'    Skipping {tab} (no member tabs)')
            continue
        write_group_tab(wb.create_sheet(tab), label, present, crop_years)
        print(f'    {tab}: {" + ".join(present)}'
              + (f'   (WARNING: missing {missing})' if missing else ''))

    TARGET_WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
    wb.save(TARGET_WORKBOOK)
    wb.close()                                   # ENGINE_RULES 6.3
    print(f'    Saved to: {TARGET_WORKBOOK}')
    return TARGET_WORKBOOK


if __name__ == '__main__':
    run_exporter()
