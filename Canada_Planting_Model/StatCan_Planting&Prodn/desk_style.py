"""The Desk | Chilmeran Research house style for openpyxl workbooks.

Implements ``Desktop/Python/Design/DesignSkills.md``: Calibri, 18pt bold sheet titles, black header
rows with white bold text, green CHECK rows, ``0.000`` acreage / ``0.0%`` percentages, and charts
with a black outline, white plot, horizontal #E5E5E5 gridlines only, #595959 titles and axes,
#D4F1D6 bars with thin black borders, and the black / green / gray line hierarchy.
Same helpers as ``v2_relative_index_model/export_excel.py``, kept here so this side project stands alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.title import Title
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties, RegularTextRun, RichTextProperties
from openpyxl.drawing.text import Font as DrawingFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------- #
# Palette and type (DesignSkills.md section 1)
# --------------------------------------------------------------------------- #
BLACK, WHITE, MUTED, GRID = "000000", "FFFFFF", "595959", "E5E5E5"
CHECK_FILL, CHECK_TEXT, BAR_FILL = "C6EFCE", "006100", "D4F1D6"
AVG_GRAY, LIGHT_GRAY, HIGHLIGHT_GREEN = "A6A6A6", "D9D9D9", "16A34A"
FONT = "Calibri"
EMU_PER_PT = 12700

F_TITLE = Font(name=FONT, size=18, bold=True)
F_HEAD = Font(name=FONT, size=11, bold=True, color=WHITE)
F_BLOCK = Font(name=FONT, size=11, bold=True)
F_LABEL = Font(name=FONT, size=11)
F_LABEL_B = Font(name=FONT, size=11, bold=True)
F_DATA = Font(name=FONT, size=11)
F_NOTE = Font(name=FONT, size=9, italic=True, color=MUTED)
F_TEXT = Font(name=FONT, size=11)
F_CHECK = Font(name=FONT, size=11, bold=True, color=CHECK_TEXT)
FILL_HEAD = PatternFill(start_color=BLACK, end_color=BLACK, fill_type="solid")
FILL_CHECK = PatternFill(start_color=CHECK_FILL, end_color=CHECK_FILL, fill_type="solid")
A_HEAD = Alignment(horizontal="center", vertical="center", wrap_text=True)
A_LEFT = Alignment(horizontal="left", vertical="center")
A_RIGHT = Alignment(horizontal="right", vertical="center")
A_TEXT = Alignment(horizontal="left", vertical="center", indent=1)   # text in a data column
A_WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)
ROW_LINE = Border(bottom=Side(style="thin", color=GRID))
TABLE_END = Border(bottom=Side(style="medium", color=BLACK))

FMT_QTY, FMT_ACRES, FMT_PCT, FMT_RATIO, FMT_INDEX = "#,##0", "0.000", "0.0%", "0%", "0.0"


# --------------------------------------------------------------------------- #
# Tables (DesignSkills.md section 2)
# --------------------------------------------------------------------------- #
def _num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
        return None
    return v.item() if hasattr(v, "item") else v


def sheet(wb, name: str, title: str, note: str | None = None):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    c = ws.cell(1, 1, title)
    c.font, c.alignment = F_TITLE, A_LEFT
    if note:
        ws.cell(2, 1, note).font = F_NOTE
    return ws


def heading(ws, row: int, text: str):
    ws.cell(row, 1, text).font = F_BLOCK


def header(ws, row: int, labels: list[str], left: int = 1):
    for j, text in enumerate(labels):
        c = ws.cell(row, left + j, text)
        c.font, c.fill, c.alignment = F_HEAD, FILL_HEAD, A_HEAD
    ws.row_dimensions[row].height = 30


def table(ws, top: int, df: pd.DataFrame, formats: dict, left: int = 1, label_cols: int = 1,
          bold_rows: set[int] | None = None) -> int:
    """Black header + rows. First ``label_cols`` columns are left-aligned labels, the rest right-aligned.
    Returns the first free row after a one-row gap."""
    header(ws, top, [str(c) for c in df.columns], left)
    for i, rec in enumerate(df.itertuples(index=False), start=1):
        bold = bold_rows is not None and (i - 1) in bold_rows
        for j, (col, v) in enumerate(zip(df.columns, rec)):
            c = ws.cell(top + i, left + j, _num(v))
            label = j < label_cols
            c.font = (F_LABEL_B if bold else F_LABEL) if label else F_DATA
            c.alignment, c.border = (A_LEFT if label else A_TEXT if isinstance(v, str) else A_RIGHT), ROW_LINE
            if col in formats:
                c.number_format = formats[col]
    for j in range(len(df.columns)):
        ws.cell(top + len(df), left + j).border = TABLE_END
    return top + len(df) + 2


def check_row(ws, row: int, values: list, formats: list[str | None], left: int = 1):
    """Green validation / summary row (CHECK fill, dark-green bold text)."""
    for j, (v, fmt) in enumerate(zip(values, formats)):
        c = ws.cell(row, left + j, _num(v))
        c.fill, c.font = FILL_CHECK, F_CHECK
        c.alignment = A_LEFT if j == 0 else A_RIGHT
        if fmt:
            c.number_format = fmt


def bullets(ws, top: int, items: list[tuple[str, str]], width_cols: int = 10, height: float = 33) -> int:
    """Narrative bullets: bold lead-in + plain text, merged across ``width_cols`` columns."""
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    bold = InlineFont(rFont=FONT, sz=11, b=True)
    plain = InlineFont(rFont=FONT, sz=11)
    for i, (lead, text) in enumerate(items):
        r = top + i
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=width_cols)
        c = ws.cell(r, 1)
        c.value = CellRichText(TextBlock(bold, f"• {lead}: "), TextBlock(plain, text))
        c.alignment = A_WRAP
        ws.row_dimensions[r].height = height
    return top + len(items) + 1


def fit_bullet_heights(ws, rows: range, width_cols: int, line_pt: float = 15):
    """Row height for merged bullet rows from text length vs the merged width (call after autofit)."""
    import math
    width = sum((ws.column_dimensions[get_column_letter(j)].width or 8.43) for j in range(1, width_cols + 1))
    for r in rows:
        n = len(str(ws.cell(r, 1).value or ""))
        ws.row_dimensions[r].height = line_pt * max(1, math.ceil(n * 0.95 / width)) + 4


def autofit(ws, min_width: float = 8, max_width: float = 44, skip_rows: int = 2, fixed: dict | None = None):
    """Width per column from the longest displayed value (titles, notes and merged text excluded)."""
    widths: dict[int, float] = {}
    merged = {(r, c) for rng in ws.merged_cells.ranges for r in range(rng.min_row, rng.max_row + 1)
              for c in range(rng.min_col, rng.max_col + 1)}
    for row in ws.iter_rows(min_row=skip_rows + 1):
        for c in row:
            if c.value is None or (c.row, c.column) in merged:
                continue
            if c.font == F_BLOCK and c.fill.fill_type is None:      # section heading spills across columns
                continue
            v = c.value
            if isinstance(v, float):
                fmt = c.number_format
                text = f"{v:.1%}" if "%" in fmt else f"{v:,.3f}" if "0.000" in fmt else f"{v:,.1f}"
            else:
                text = str(v)
            factor = 1.15 if c.font is not None and c.font.bold else 1.0
            if c.fill.fill_type == "solid" and c.fill.start_color.rgb.endswith(BLACK):
                factor = 0.6                                        # wrapped header text
            widths[c.column] = max(widths.get(c.column, 0), len(text) * factor)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(max_width, w + 2))
    for col, w in (fixed or {}).items():
        ws.column_dimensions[col].width = w


# --------------------------------------------------------------------------- #
# Charts (DesignSkills.md section 3)
# --------------------------------------------------------------------------- #
def _rich(size_pt: float, color: str = MUTED, bold: bool = False, rot: int | None = None) -> RichText:
    cp = CharacterProperties(sz=int(size_pt * 100), b=bold, solidFill=color, latin=DrawingFont(typeface=FONT))
    body = RichTextProperties(rot=rot, vert="horz") if rot is not None else RichTextProperties()
    return RichText(bodyPr=body, p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), endParaRPr=cp)])


def _title(text: str, size_pt: float = 18) -> Title:
    cp = CharacterProperties(sz=int(size_pt * 100), b=False, solidFill=MUTED, latin=DrawingFont(typeface=FONT))
    para = Paragraph(pPr=ParagraphProperties(defRPr=cp), r=[RegularTextRun(rPr=cp, t=text)])
    return Title(tx=Text(rich=RichText(p=[para])), overlay=False)


def desk_chart(chart, title: str, y_fmt: str, y_title: str, width: float = 24, height: float = 9,
               legend: bool = False):
    chart.title = _title(title)
    chart.width, chart.height = width, height
    chart.roundedCorners = False                                    # clean single black outline
    chart.graphical_properties = GraphicalProperties(ln=LineProperties(solidFill=BLACK, w=EMU_PER_PT))
    chart.plot_area.graphicalProperties = GraphicalProperties(solidFill=WHITE)
    for ax in (chart.x_axis, chart.y_axis):
        ax.delete = False
        ax.graphicalProperties = GraphicalProperties(ln=LineProperties(solidFill=GRID))
    chart.y_axis.majorGridlines = ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill=GRID)))
    chart.x_axis.majorGridlines = None
    chart.y_axis.number_format = y_fmt
    chart.y_axis.txPr = _rich(10)
    chart.x_axis.txPr = _rich(10, rot=-5400000)                    # 90 degrees upward
    chart.x_axis.tickLblPos = "low"
    chart.y_axis.title = _title(y_title.upper(), 10)
    if legend:
        chart.legend.position = "r"
        chart.legend.overlay = False
        chart.legend.txPr = _rich(10)
    else:
        chart.legend = None
    return chart


def style_line(series, color: str, width_pt: float, dash: str = "solid", marker: bool = False):
    series.smooth = False
    series.graphicalProperties.line.solidFill = color
    series.graphicalProperties.line.width = int(width_pt * EMU_PER_PT)
    series.graphicalProperties.line.prstDash = dash
    if marker:
        series.marker.symbol = "circle"
        series.marker.size = 4
        series.marker.graphicalProperties = GraphicalProperties(solidFill=color, ln=LineProperties(solidFill=color))
    else:
        series.marker.symbol = "none"


def style_bars(chart, fill: str = BAR_FILL):
    chart.gapWidth = 90
    for s in chart.series:
        s.graphicalProperties = GraphicalProperties(solidFill=fill, ln=LineProperties(solidFill=BLACK, w=9525))
        s.invertIfNegative = False
