"""
Merge engine history and forecast inputs into Outputs\\CanadaSD_Main.xlsx.

    python Engine/merge_main_workbook.py          (run the pipeline first)

Columns on every annual tab (RULE-5.5):
    D..H   the last MAIN_HISTORY_YEARS crop years of the engine (e.g. 21-22..25-26).
           The window is read from CanadaSD_Engine.xlsx's *_Yr header row, so
           it rolls with the engine scope. Values come from the engine fact
           tables (data_store\\*.csv), the same numbers the engine's SUMIFS
           formulas show.
    I..    the first MAIN_FORECAST_COUNT forecast years of CanSD_Input_wb.xlsx
           (*_Mo_F annual column, saved values), headed '25-26*', '26-27*'.
    then   one delta per forecast year: 'Δ (25-26*, 25-26)' = forecast minus
           the last actual year; negatives get a soft red fill.

Metric lines are values; Total Supply / Disposition, Ending Stocks, STU,
CHECK, ratios, deltas and every summary tab are live formulas. An incomplete
actual year (Soybeans 25-26 before StatCan's Jul 31 release) stays blank.

Tabs: Analytics_Dashboard, Can_Total_Yr, the group tabs, then one *_Yr tab
per crop (Canola_Yr / Soybeans_Yr keep their oil and meal tables, history only).
"""

import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config.paths import (  # noqa: E402
    CANADASD_MAIN_XLSX as ENGINE_XLSX, MASTER_FACT_TABLE, MAIN_OUTPUT_XLSX,
    CANADASD_MANUAL_XLSX, FORECAST_INPUT_XLSX,
)
from config.commodities import (  # noqa: E402
    TIER1_COMMODITIES, GROUP_TABS, MAIN_HISTORY_YEARS, MAIN_FORECAST_COUNT,
    MAIN_TAB_ORDER, TOTAL_TAB_MEMBERS, crop_year_label, get_group, month_labels,
)
from config.styles import (  # noqa: E402
    ENGINE_FONT_NAME, ENGINE_FONT_SIZE, YR_LABEL, YR_BOLD_METRICS, YR_TITLE_ROW_HEIGHT,
    VEGOIL_GROUP_TITLE_SIZE, VEGOIL_SUBTOTAL_TOP, VEGOIL_SUBTOTAL_BOTTOM,
)
from exporter_excel import (  # noqa: E402
    FONT, FONT_BOLD, FONT_HDR, FONT_TITLE, FILL_HDR_YR, LEFT, RIGHT, CENTER,
    FMT_KMT, FMT_PCT, FMT_YIELD_MT, YR_UNIT, YR_FMT, RATIO_GAP, OIL_SPEC, MEAL_SPEC,
    BYPRODUCT_UNIT, BYPRODUCT_FMT, _MONTHS, yr_metrics, standard_ratios, write_ratio_rows,
    load_vegoil, load_meal,
)
from modules.vegoil_builder import oil_start_months  # noqa: E402
from modules.meal_builder import meal_start_months  # noqa: E402
from populate_input_forecast import detect_layout  # noqa: E402

# ------------------------------------------------------------------ styling
MAIN_CHECK_FILL = 'D9EAD3'            # CHECK within tolerance
MAIN_NEG_DELTA_FILL = 'FCE4D6'        # soft red on negative deltas
MAIN_FORECAST_HDR_FONT = 'FFFF00'     # yellow header text on forecast columns (mock)
MAIN_WIDTH_UNIT = 8
MAIN_WIDTH_YEAR = 8
MAIN_WIDTH_DELTA = 12
TAB_BLACK = '000000'                  # every commodity *_Yr tab
TAB_ACCENT = {                        # dark accents for the dashboard and summary tabs
    'Analytics_Dashboard': 'C00000', 'Can_Total_Yr': '1F3864', 'Can_Cereals_Yr': '375623',
    'Can_Oilseeds_Yr': '7F6000', 'Can_Vegoils_Yr': '843C0C', 'Can_ProteinMeal_Yr': '3A3838',
    'Can_Pulses_Yr': '203764',
}
GRAIN_TOL, BYPRODUCT_TOL = 0.0009, 0.000001
TOTAL_TITLE = 'Total Canadian Field Crops (Cereals, Oilseeds & Pulses)'
TOTAL_LABEL = 'Canada field crops'
TOTAL_USE = 'Industrial / Crush'

FORECAST_KEYS = {'planted': 'Planted Area', 'harvested': 'Harvested Area', 'yield': 'Yield',
                 'production': 'Production', 'imports': 'Imports', 'exports': 'Exports',
                 'food': 'Food'}
LABEL_RE = re.compile(r'^\d{2}-\d{2}$')


def cy_of(label: str) -> int:
    return 2000 + int(label[:2])


def num(v):
    return float(v) if isinstance(v, (int, float)) else None


# ------------------------------------------------------------ column plan
class Plan:
    """Column plan shared by every tab: history, forecast, then delta columns."""

    def __init__(self, hist, fc):
        self.hist, self.fc = hist, fc                      # crop years (ints)
        self.base = hist[-1]                               # last actual year
        self.data = [('hist', cy) for cy in hist] + [('fc', cy) for cy in fc]
        self.col = {k: get_column_letter(4 + i) for i, k in enumerate(self.data)}
        self.delta = {cy: get_column_letter(4 + len(self.data) + i) for i, cy in enumerate(fc)}
        self.n_data = len(self.data)
        self.last_col = 3 + self.n_data + len(fc)

    def header(self, key):
        kind, cy = key
        return crop_year_label(cy) + ('*' if kind == 'fc' else '')

    def delta_header(self, cy):
        return f'Δ ({crop_year_label(cy)}*, {crop_year_label(self.base)})'

    def prev(self, key):
        """The data column this one's opening stock chains from, if any."""
        i = self.data.index(key)
        if i == 0 or self.data[i - 1][0] != key[0]:
            return None
        return self.data[i - 1]


def history_window(n: int):
    """Last n crop-year labels on the engine's *_Yr header row (row 2)."""
    if not ENGINE_XLSX.exists():
        sys.exit(f'{ENGINE_XLSX} not found. Run python Engine/run_pipeline.py first.')
    wb = load_workbook(ENGINE_XLSX, read_only=True)
    ws = wb[f'{TIER1_COMMODITIES[0]}_Yr']
    labels = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2))]
    wb.close()
    labels = [v for v in labels if isinstance(v, str) and LABEL_RE.match(v)]
    return [cy_of(v) for v in labels[-n:]]


# ------------------------------------------------------------------ data
def grain_history(master: pd.DataFrame, commodity: str, years):
    """{cy: {metric: value, '_close': ending stocks}} for complete crop years."""
    use = 'Crush' if get_group(commodity) == 'Oilseed' else 'Industrial'
    g = master[master['Commodity_Norm'] == commodity]
    out = {}
    for cy in years:
        y = g[g['Crop_Year'] == cy].sort_values('CY_Month')
        if y['CY_Month'].nunique() != 12:
            continue                                   # incomplete: leave blank
        f = y.iloc[0]
        out[cy] = {'Planted Area': num(f['Planted_MLN_AC']), 'Harvested Area': num(f['Harvested_MLN_AC']),
                   'Yield': num(f['Yield_BU_AC']), 'Beginning Stocks': float(f['Beginning Stocks']),
                   **{m: float(y[m].sum()) for m in ('Production', 'Imports', 'Exports', 'Food', 'FSR')},
                   use: float(y[use].sum()), '_close': float(y['Ending Stocks'].iloc[-1])}
    return out


def grain_forecast(ws, commodity: str, n: int):
    """First n populated crop years of a *_Mo_F tab (annual column, saved
    values). Opening stock = first-month STOCKS minus that month's flows."""
    use = 'Crush' if get_group(commodity) == 'Oilseed' else 'Industrial'
    _, month_cols, ann, targets, _ = detect_layout(ws)
    first = min(month_cols)
    at = {(m, y): r for r, m, y in targets}
    out = {}
    for cy in sorted({y for _, _, y in targets}):
        if len(out) == n:
            break
        v = lambda m, c=ann: num(ws.cell(at[(m, cy)], c).value) if (m, cy) in at else None
        if v('production') is None:
            continue
        if v('stocks') is None:
            sys.exit(f"{ws.title}: no saved value for STOCKS {crop_year_label(cy)}. Open "
                     f"{FORECAST_INPUT_XLSX.name} in Excel and save it so formula results are stored.")
        use_key = 'crush' if ('crush', cy) in at else 'industrial'
        rec = {name: v(k) for k, name in FORECAST_KEYS.items()}
        rec[use] = v(use_key)
        rec['FSR'] = (v('fsr') or 0.0) + (v('seed') or 0.0)
        d = lambda m: v(m, first) or 0.0
        rec['Beginning Stocks'] = d('stocks') - (d('production') + d('imports') - d('exports')
                                                 - d('food_proc') - d('fsr') - d('seed'))
        rec['_close'] = v('stocks')
        out[cy] = rec
    return out


# --------------------------------------------------------------- writing
def _hdr(ws, row, plan):
    labels = ['Commodity', 'Metric', 'Unit'] + [plan.header(k) for k in plan.data] \
        + [plan.delta_header(cy) for cy in plan.fc]
    for c, h in enumerate(labels, 1):
        cell = ws.cell(row, c, h)
        cell.fill, cell.alignment = FILL_HDR_YR, CENTER
        fc = 3 < c <= 3 + plan.n_data and plan.data[c - 4][0] == 'fc'
        cell.font = Font(name=ENGINE_FONT_NAME, size=ENGINE_FONT_SIZE, bold=True,
                         color=MAIN_FORECAST_HDR_FONT if fc else 'FFFFFF')


def _title(ws, row, text, span=None):
    ws.cell(row, 1, text).font = FONT_TITLE
    if span:
        ws.cell(row, 2, span).font = Font(name=ENGINE_FONT_NAME, size=20)
    ws.row_dimensions[row].height = YR_TITLE_ROW_HEIGHT


def _rich_title(ws, head, detail):
    ws['A1'] = CellRichText(
        TextBlock(InlineFont(rFont=ENGINE_FONT_NAME, sz=VEGOIL_GROUP_TITLE_SIZE, b=True), head),
        TextBlock(InlineFont(rFont=ENGINE_FONT_NAME, sz=ENGINE_FONT_SIZE, i=True), detail))
    ws.row_dimensions[1].height = YR_TITLE_ROW_HEIGHT


def _check_cf(ws, row, plan, tol):
    fill = PatternFill('solid', start_color=MAIN_CHECK_FILL, end_color=MAIN_CHECK_FILL)
    ws.conditional_formatting.add(
        f'D{row}:{get_column_letter(3 + plan.n_data)}{row}',
        CellIsRule(operator='between', formula=[f'-{tol}', f'{tol}'], fill=fill))


def _deltas(ws, rows, plan, fmts, skip=('CHECK',)):
    """Δ = forecast minus the last actual year, blank when either side is blank."""
    base = plan.col[('hist', plan.base)]
    first = last = None
    for m, r in rows.items():
        if m.startswith('_') or m in skip:
            continue
        for cy, dcol in plan.delta.items():
            f = plan.col[('fc', cy)]
            cell = ws.cell(r, column_index_from_string(dcol),
                           f'=IF(AND(ISNUMBER({f}{r}),ISNUMBER({base}{r})),{f}{r}-{base}{r},"")')
            cell.font, cell.alignment = FONT_BOLD if m in YR_BOLD_METRICS else FONT, RIGHT
            cell.number_format = fmts.get(m, FMT_KMT)
        first = r if first is None else min(first, r)
        last = r if last is None else max(last, r)
    if plan.fc and first:
        red = PatternFill('solid', start_color=MAIN_NEG_DELTA_FILL, end_color=MAIN_NEG_DELTA_FILL)
        d0, d1 = plan.delta[plan.fc[0]], plan.delta[plan.fc[-1]]
        ws.conditional_formatting.add(f'{d0}{first}:{d1}{last}',
                                      CellIsRule(operator='lessThan', formula=['0'], fill=red))


def _label_cells(ws, r, label, m, unit, bold):
    font = FONT_BOLD if bold else FONT
    for c, v, al in ((1, label, LEFT), (2, m, LEFT), (3, unit, CENTER)):
        cell = ws.cell(r, c, v)
        cell.font, cell.alignment = font, al


def write_crop_tab(ws, commodity, plan, hist, fc):
    """One crop's balance sheet: values + live totals, CHECK against the source
    closing stock (engine *_Mo STOCKS for history, *_Mo_F STOCKS for forecast)."""
    metrics = yr_metrics(commodity)
    use = metrics[11]
    label = YR_LABEL.get(commodity, commodity)
    months = month_labels(commodity)
    _title(ws, 1, label, f'{months[0].title()}/{months[-1].title()}')
    _hdr(ws, 2, plan)
    rows = {m: 3 + i for i, m in enumerate(metrics) if m}
    R = lambda m: rows[m]
    recs = {**{('hist', cy): r for cy, r in hist.items()}, **{('fc', cy): r for cy, r in fc.items()}}
    values = ('Planted Area', 'Harvested Area', 'Yield', 'Imports', 'Production', 'Food', use,
              'FSR', 'Exports')
    for m, r in rows.items():
        _label_cells(ws, r, label, m, YR_UNIT.get(m, 'KMT'), m in YR_BOLD_METRICS)
        for key in plan.data:
            rec = recs.get(key)
            if rec is None:
                continue
            cl = plan.col[key]
            if m in values:
                v = rec.get(m)
            elif m == 'Beginning Stocks':
                p = plan.prev(key)
                v = f'={plan.col[p]}{R("Ending Stocks")}' if p in recs else rec['Beginning Stocks']
            elif m == 'Proportion Harvested':
                v = f'=IF(N({cl}{R("Planted Area")})<>0,{cl}{R("Harvested Area")}/{cl}{R("Planted Area")},"")'
            elif m == 'Total Supply':
                v = f'={cl}{R("Beginning Stocks")}+{cl}{R("Imports")}+{cl}{R("Production")}'
            elif m == 'Total Disposition':
                v = f'={cl}{R("Food")}+{cl}{R(use)}+{cl}{R("FSR")}+{cl}{R("Exports")}'
            elif m == 'Ending Stocks':
                v = f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}'
            elif m in ('Stocks-to-Use', 'STU Days'):
                td, mult = f'{cl}{R("Total Disposition")}', '*365' if m == 'STU Days' else ''
                v = f'=IF({td}<>0,{cl}{R("Ending Stocks")}/{td}{mult},"")'
            elif m == 'CHECK':
                v = f'={cl}{R("Ending Stocks")}-{rec["_close"]!r}'
            if v is None:
                continue
            cell = ws.cell(r, 4 + plan.data.index(key), v)
            cell.font, cell.alignment = FONT_BOLD if m in YR_BOLD_METRICS else FONT, RIGHT
            cell.number_format = YR_FMT.get(m, FMT_KMT)
    ws.cell(R('CHECK'), 2).comment = Comment(
        'Ending Stocks minus the source closing stock: engine *_Mo STOCKS (history) or '
        'CanSD_Input_wb *_Mo_F STOCKS (forecast, *). 0 = the annual lines tie out.', 'CMFT Engine')
    _deltas(ws, rows, plan, YR_FMT)
    _subtotal_lines(ws, rows, plan)
    _check_cf(ws, R('CHECK'), plan, GRAIN_TOL)
    last = write_ratio_rows(ws, R('CHECK') + RATIO_GAP + 1, label, plan.n_data, standard_ratios(rows, use))
    return rows, last


def _subtotal_lines(ws, rows, plan, top='thick', bottom=None):
    side_t = Side(style=top, color='000000') if top else None
    side_b = Side(style=bottom, color='000000') if bottom else None
    for m in ('Total Supply', 'Total Disposition'):
        for c in range(1, plan.last_col + 1):
            ws.cell(rows[m], c).border = Border(top=side_t, bottom=side_b)


def write_byproduct_table(ws, top, spec, key, annual, plan, span):
    """Oil / meal table under its crop (history only: no forecast inputs)."""
    cfg = spec['registry'][key]
    label = cfg['label']
    _title(ws, top, cfg['title'], span)
    _hdr(ws, top + 1, plan)
    rows = {m: top + 2 + i for i, m in enumerate(spec['yr_metrics']) if m}
    R = lambda m: rows[m]
    a = annual[annual[spec['key']] == key].set_index('Crop_Year')
    for m, r in rows.items():
        _label_cells(ws, r, label, m, BYPRODUCT_UNIT.get(m, 'KMT'), m in YR_BOLD_METRICS)
        if m in spec['notes']:
            ws.cell(r, 2).comment = Comment(spec['notes'][m], 'CMFT Engine')
        for key_ in plan.data:
            kind, cy = key_
            if kind != 'hist' or cy not in a.index:
                continue
            cl = plan.col[key_]
            if m == 'Beginning Stocks':
                p = plan.prev(key_)
                v = (f'={plan.col[p]}{R("Ending Stocks")}' if p and p[1] in a.index
                     else float(a.at[cy, 'Beginning Stocks']))
            elif m in spec['column']:
                v = float(a.at[cy, spec['column'][m]])
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
                v = f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}-{cl}{R("Ending Stocks")}'
            cell = ws.cell(r, 4 + plan.data.index(key_), v)
            cell.font, cell.alignment = FONT_BOLD if m in YR_BOLD_METRICS else FONT, RIGHT
            cell.number_format = BYPRODUCT_FMT.get(m, FMT_KMT)
    _subtotal_lines(ws, rows, plan, VEGOIL_SUBTOTAL_TOP, VEGOIL_SUBTOTAL_BOTTOM)
    _check_cf(ws, R('CHECK'), plan, BYPRODUCT_TOL)
    last = write_ratio_rows(ws, R('CHECK') + RATIO_GAP + 1, label, plan.n_data,
                            [(n, [R(x) for x in nums], R('Production')) for n, nums in spec['ratios']])
    return rows, last


def write_sum_tab(ws, title, label, sources, plan, avail, metrics, use, tol, byproduct=None):
    """Summary tab: each line = SUMIFS over `sources` [(sheet, column-A label,
    {metric: source metric name})] in the same column. Columns not in `avail`
    (a member is blank there) stay blank."""
    if isinstance(title, tuple):
        _rich_title(ws, *title)
    else:
        _title(ws, 1, title)
    _hdr(ws, 2, plan)
    rows = {m: 3 + i for i, m in enumerate(metrics) if m}
    R = lambda m: rows[m]
    spec = byproduct
    disp = spec['disp'] if spec else ['Food', use, 'FSR', 'Exports']
    derived = {'Yield', 'Proportion Harvested', 'Total Supply', 'Total Disposition',
               'Stocks-to-Use', 'STU Days', 'CHECK'}
    unit = BYPRODUCT_UNIT if spec else {**YR_UNIT, 'Yield': 'MT/AC'}
    fmts = BYPRODUCT_FMT if spec else {**YR_FMT, 'Yield': FMT_YIELD_MT}
    for m, r in rows.items():
        _label_cells(ws, r, label, m, unit.get(m, 'KMT'), m in YR_BOLD_METRICS)
        for key in plan.data:
            if key not in avail:
                continue
            cl = plan.col[key]
            if m not in derived:
                v = '=' + '+'.join(f"SUMIFS('{s}'!{cl}:{cl},'{s}'!$A:$A,\"{lab}\",'{s}'!$B:$B,\"{names.get(m, m)}\")"
                                   for s, lab, names in sources)
            elif m == 'Yield' and spec:
                v = f'=IF(N({cl}{R("Crush")})<>0,{cl}{R("Production")}/{cl}{R("Crush")},"")'
            elif m == 'Yield':
                h, p = f'{cl}{R("Harvested Area")}', f'{cl}{R("Production")}'
                v = f'=IF(N({h})<>0,{p}/({h}*1000),"")'
            elif m == 'Proportion Harvested':
                v = f'=IF(N({cl}{R("Planted Area")})<>0,{cl}{R("Harvested Area")}/{cl}{R("Planted Area")},"")'
            elif m == 'Total Supply':
                v = f'={cl}{R("Beginning Stocks")}+{cl}{R("Imports")}+{cl}{R("Production")}'
            elif m == 'Total Disposition':
                v = '=' + '+'.join(f'{cl}{R(d)}' for d in disp)
            elif m in ('Stocks-to-Use', 'STU Days'):
                td, mult = f'{cl}{R("Total Disposition")}', '*365' if m == 'STU Days' else ''
                v = f'=IF({td}<>0,{cl}{R("Ending Stocks")}/{td}{mult},"")'
            elif m == 'CHECK':
                v = f'={cl}{R("Total Supply")}-{cl}{R("Total Disposition")}-{cl}{R("Ending Stocks")}'
            cell = ws.cell(r, 4 + plan.data.index(key), v)
            cell.font, cell.alignment = FONT_BOLD if m in YR_BOLD_METRICS else FONT, RIGHT
            cell.number_format = fmts.get(m, FMT_KMT)
    _deltas(ws, rows, plan, fmts)
    if spec:
        _subtotal_lines(ws, rows, plan, VEGOIL_SUBTOTAL_TOP, VEGOIL_SUBTOTAL_BOTTOM)
        ratios = [(n, [R(x) for x in nums], R('Production')) for n, nums in spec['group_ratios']]
    else:
        _subtotal_lines(ws, rows, plan)
        ratios = standard_ratios(rows, use)
    _check_cf(ws, R('CHECK'), plan, tol)
    write_ratio_rows(ws, R('CHECK') + RATIO_GAP + 1, label, plan.n_data, ratios)
    return rows


GRAIN_GROUP_METRICS = lambda use: ['Planted Area', 'Harvested Area', 'Proportion Harvested', 'Yield', '',
                                   'Beginning Stocks', 'Imports', 'Production', 'Total Supply', '',
                                   'Food', use, 'FSR', 'Exports', 'Total Disposition', '',
                                   'Ending Stocks', 'Stocks-to-Use', 'STU Days', 'CHECK']


def write_dashboard(ws, plan, crop_rows, total_rows):
    """Tables of Ending Stocks and Stocks-to-Use (live links) + clustered column charts."""
    ws['A1'] = 'Canada Supply & Disposition — Analytics Dashboard'
    ws['A1'].font = FONT_TITLE
    ws.row_dimensions[1].height = YR_TITLE_ROW_HEIGHT
    ws['A2'] = (f'Generated {date.today():%Y-%m-%d} by merge_main_workbook.py. History = engine '
                f'(StatCan/CIMT); * = forecast (CanSD_Input_wb). #N/A = not yet published.')
    ws['A2'].font = Font(name=ENGINE_FONT_NAME, size=ENGINE_FONT_SIZE, italic=True)
    keys = [('hist', cy) for cy in plan.hist[-2:]] + [('fc', cy) for cy in plan.fc]
    blocks = []
    top = 4
    for metric, fmt, unit in (('Ending Stocks', FMT_KMT, 'KMT'), ('Stocks-to-Use', FMT_PCT, '%')):
        ws.cell(top, 1, f'{metric} ({unit})').font = FONT_BOLD
        hdr = top + 1
        for c, h in enumerate(['Commodity'] + [plan.header(k) for k in keys], 1):
            cell = ws.cell(hdr, c, h)
            cell.fill, cell.alignment = FILL_HDR_YR, CENTER
            cell.font = Font(name=ENGINE_FONT_NAME, size=ENGINE_FONT_SIZE, bold=True,
                             color=MAIN_FORECAST_HDR_FONT if '*' in h else 'FFFFFF')
        items = [(YR_LABEL.get(c, c), f'{c}_Yr', crop_rows[c][metric]) for c in crop_rows]
        items.append((TOTAL_LABEL.title(), 'Can_Total_Yr', total_rows[metric]))
        for i, (name, sheet, row) in enumerate(items):
            r = hdr + 1 + i
            ws.cell(r, 1, name).font = FONT_BOLD if sheet == 'Can_Total_Yr' else FONT
            for j, k in enumerate(keys):
                ref = f"'{sheet}'!{plan.col[k]}{row}"
                cell = ws.cell(r, 2 + j, f'=IF(ISNUMBER({ref}),{ref},NA())')
                cell.number_format, cell.font, cell.alignment = fmt, FONT, RIGHT
        blocks.append((metric, unit, hdr, hdr + len(items) - 1, hdr + len(items) - 2))
        top = hdr + len(items) + 2

    anchor_row = 4
    for metric, unit, hdr, last_all, last_crop in blocks:
        ch = BarChart()
        ch.type, ch.grouping = 'col', 'clustered'
        ch.title = f'{metric} by commodity: recent actuals vs forecast (*)'
        ch.y_axis.title = unit
        ch.x_axis.delete = ch.y_axis.delete = False
        ch.add_data(Reference(ws, min_col=2, max_col=1 + len(keys), min_row=hdr, max_row=last_crop),
                    titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=hdr + 1, max_row=last_crop))
        ch.height, ch.width = 8.5, 20
        if unit == '%':
            ch.y_axis.numFmt = '0%'
        ws.add_chart(ch, f'H{anchor_row}')
        anchor_row += 18
    ws.column_dimensions['A'].width = 22
    for j in range(len(keys)):
        ws.column_dimensions[get_column_letter(2 + j)].width = 10
    ws.sheet_view.showGridLines = False


def _widths(ws, plan):
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 22
    ws.column_dimensions['C'].width = MAIN_WIDTH_UNIT
    for k in plan.data:
        ws.column_dimensions[plan.col[k]].width = MAIN_WIDTH_YEAR
    for d in plan.delta.values():
        ws.column_dimensions[d].width = MAIN_WIDTH_DELTA
    ws.freeze_panes = 'D3'
    ws.sheet_view.showGridLines = True


# ------------------------------------------------------------------- main
def main():
    if MAIN_OUTPUT_XLSX.resolve() == CANADASD_MANUAL_XLSX.resolve():
        sys.exit('Refusing to write the hand-maintained CanadaSD_Main.xlsx (RULE-1.2.A).')
    lock = MAIN_OUTPUT_XLSX.with_name('~$' + MAIN_OUTPUT_XLSX.name)
    if lock.exists():
        sys.exit(f'{MAIN_OUTPUT_XLSX.name} is open in Excel ({lock.name}). Close it and re-run.')
    if not 1 <= MAIN_FORECAST_COUNT <= 3:
        sys.exit('MAIN_FORECAST_COUNT must be 1-3.')

    hist_years = history_window(MAIN_HISTORY_YEARS)
    master = pd.read_csv(MASTER_FACT_TABLE)
    fwb = load_workbook(FORECAST_INPUT_XLSX, data_only=True)
    crops = [c for c in TIER1_COMMODITIES if f'{c}_Mo_F' in fwb.sheetnames]
    hist = {c: grain_history(master, c, hist_years) for c in crops}
    fc = {c: grain_forecast(fwb[f'{c}_Mo_F'], c, MAIN_FORECAST_COUNT) for c in crops}
    fwb.close()
    fc_years = sorted({cy for f in fc.values() for cy in f})[:MAIN_FORECAST_COUNT]
    plan = Plan(hist_years, fc_years)
    avail = {c: {('hist', cy) for cy in hist[c]} | {('fc', cy) for cy in fc[c]} for c in crops}

    oil_m, oil_a = load_vegoil()
    meal_m, meal_a = load_meal()
    fams = [(OIL_SPEC, oil_a, oil_start_months()), (MEAL_SPEC, meal_a, meal_start_months())]

    wb = Workbook()
    wb.remove(wb.active)
    dash = wb.create_sheet('Analytics_Dashboard')
    crop_rows, byp_rows = {}, {}
    for c in crops:
        ws = wb.create_sheet(f'{c}_Yr')
        rows, last = write_crop_tab(ws, c, plan, hist[c], fc[c])
        crop_rows[c] = rows
        for spec, annual, starts in fams:
            for key, cfg in spec['registry'].items():
                if cfg['host'] != c or annual.empty:
                    continue
                s = starts[key]
                span = f'{_MONTHS[s - 1].title()}/{_MONTHS[(s - 2) % 12].title()}'
                brows, last = write_byproduct_table(ws, last + RATIO_GAP + 1, spec, key, annual, plan, span)
                byp_rows[key] = (ws.title, brows)
        _widths(ws, plan)
        ws.sheet_properties.tabColor = TAB_BLACK

    group_avail, group_rows = {}, {}
    for tab, (label, members) in GROUP_TABS.items():
        members = [m for m in members if m in crops]
        use = 'Crush' if all(get_group(m) == 'Oilseed' for m in members) else 'Industrial'
        group_avail[tab] = set.intersection(*(avail[m] for m in members))
        src = [(f'{m}_Yr', YR_LABEL.get(m, m), {}) for m in members]
        group_rows[tab] = write_sum_tab(wb.create_sheet(tab), label, label, src, plan, group_avail[tab],
                                        GRAIN_GROUP_METRICS(use), use, GRAIN_TOL)
    total_members = [t for t in TOTAL_TAB_MEMBERS if t in group_rows]
    src = [(t, GROUP_TABS[t][0],
            {TOTAL_USE: 'Crush' if 'Crush' in group_rows[t] else 'Industrial'}) for t in total_members]
    total_rows = write_sum_tab(wb.create_sheet('Can_Total_Yr'), TOTAL_TITLE, TOTAL_LABEL, src, plan,
                               set.intersection(*(group_avail[t] for t in total_members)),
                               GRAIN_GROUP_METRICS(TOTAL_USE), TOTAL_USE, GRAIN_TOL)
    for spec, annual, _ in fams:
        keys = list(spec['registry'])
        if annual.empty or any(k not in byp_rows for k in keys):
            continue
        both = set.intersection(*({('hist', cy) for cy in annual.loc[annual[spec['key']] == k, 'Crop_Year']}
                                  for k in keys))
        src = [(byp_rows[k][0], spec['registry'][k]['label'], {}) for k in keys]
        write_sum_tab(wb.create_sheet(spec['group_tab']), spec['group_title'], spec['group_label'], src,
                      plan, both & set(plan.data), [m for m in spec['yr_metrics']], None,
                      BYPRODUCT_TOL, byproduct=spec)
    write_dashboard(dash, plan, crop_rows, total_rows)

    for name in wb.sheetnames:
        ws = wb[name]
        if name in TAB_ACCENT:
            ws.sheet_properties.tabColor = TAB_ACCENT[name]
        if name != 'Analytics_Dashboard' and name not in [f'{c}_Yr' for c in crops]:
            _widths(ws, plan)
    order = [t for t in MAIN_TAB_ORDER if t in wb.sheetnames]
    order += [t for t in wb.sheetnames if t not in order]
    wb._sheets = [wb[t] for t in order]
    wb.active = 0
    wb.calculation.fullCalcOnLoad = True           # formulas have no saved results yet

    MAIN_OUTPUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(MAIN_OUTPUT_XLSX)
    wb.close()
    print(f'Saved {MAIN_OUTPUT_XLSX}')
    summary(plan, crops, hist, fc, crop_rows)


# ----------------------------------------------------------------- summary
def summary(plan, crops, hist, fc, crop_rows):
    wb = load_workbook(MAIN_OUTPUT_XLSX, rich_text=True)
    print('\n' + '=' * 72)
    print('CanadaSD_Main SUMMARY')
    print('=' * 72)
    print(f'History (engine): {", ".join(crop_year_label(y) for y in plan.hist)}   '
          f'Forecast (inputs): {", ".join(crop_year_label(y) + "*" for y in plan.fc)}   '
          f'Deltas vs {crop_year_label(plan.base)}: {", ".join(plan.delta.values())}')

    tabs = []
    for i, n in enumerate(wb.sheetnames, 1):
        ws = wb[n]
        tc = ws.sheet_properties.tabColor
        tabs.append({'#': i, 'Tab': n, 'Tab color': tc.rgb[-6:] if tc is not None and tc.rgb else '-',
                     'Charts': len(ws._charts), 'Rows': ws.max_row})
    print('\nRendered tabs (in order):')
    print(pd.DataFrame(tabs).to_string(index=False))

    ws = wb[f'{crops[0]}_Yr']
    w = lambda c: ws.column_dimensions[c].width
    print(f'\nColumn widths on every *_Yr tab: C (Unit) = {w("C")}; year columns '
          f'{", ".join(f"{plan.col[k]}={w(plan.col[k])}" for k in plan.data)}; deltas '
          f'{", ".join(f"{d}={w(d)}" for d in plan.delta.values())}')

    rows, chk = [], []
    for c in crops:
        r = crop_rows[c]
        recs = {**{('hist', cy): v for cy, v in hist[c].items()}, **{('fc', cy): v for cy, v in fc[c].items()}}
        use = 'Crush' if 'Crush' in r else 'Industrial'
        base = recs.get(('hist', plan.base))
        row = {'Crop': c}
        for cy in plan.fc:
            f = recs.get(('fc', cy))
            for m, key in (('Production', 'Production'), ('Ending Stocks', '_close')):
                row[f'd.{m[:4]} {crop_year_label(cy)}*'] = (
                    round(f[key] - base[key], 1) if f and base else '')
            cell = wb[f'{c}_Yr'][f'{plan.delta[cy]}{r["Production"]}'].value
            ok = (f'{plan.col[("fc", cy)]}{r["Production"]}-{plan.col[("hist", plan.base)]}{r["Production"]}'
                  in cell)
            row[f'formula {plan.delta[cy]}'] = 'OK' if ok else 'WRONG'
        rows.append(row)
        for key, v in recs.items():
            g = lambda m: v.get(m) or 0.0
            res = (g('Beginning Stocks') + g('Imports') + g('Production') - g('Food') - g(use)
                   - g('FSR') - g('Exports') - g('_close'))
            chk.append({'Crop': c, 'Column': plan.header(key), 'Residual': res})
        f1 = recs.get(('fc', plan.fc[0])) if plan.fc else None
        prior = hist[c].get(plan.fc[0] - 1) if plan.fc else None
        if f1 and prior:
            chk.append({'Crop': c, 'Column': f'open {plan.header(("fc", plan.fc[0]))} vs close '
                        f'{crop_year_label(plan.fc[0] - 1)}', 'Residual': f1['Beginning Stocks'] - prior['_close']})
    print('\nDelta columns (forecast minus actual; Production and Ending Stocks, KMT):')
    print(pd.DataFrame(rows).to_string(index=False))
    t = pd.DataFrame(chk)
    t['abs'] = t['Residual'].abs()
    print('\nStock tie-out (Beg + Imp + Prod - Food - Ind/Crush - FSR - Exp - source close, and '
          'forecast opening vs last actual close):')
    worst = t.sort_values('abs', ascending=False).head(8)
    print(worst[['Crop', 'Column', 'Residual']].to_string(index=False, float_format=lambda x: f'{x:.6f}'))
    n_bad = int((t['abs'] > GRAIN_TOL).sum())
    print(f'  {len(t)} checks, max |residual| {t["abs"].max():.6f} KMT -> '
          f'{"PASS" if n_bad == 0 else f"{n_bad} outside +/-{GRAIN_TOL}"}')
    blanks = [f'{c} {crop_year_label(cy)}' for c in crops for cy in plan.hist if cy not in hist[c]]
    if blanks:
        print(f'  Blank (incomplete) actual years: {", ".join(blanks)}')
    wb.close()


if __name__ == '__main__':
    main()
