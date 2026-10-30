"""The Desk | Chilmeran Research design tokens (source: Python/Design/DesignSkills.md).

One place for every colour, font and number format used by the workbook builder (openpyxl) and the
Excel wiring step (COM). Do not hardcode styles elsewhere.
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# --------------------------------------------------------------------------- #
# Palette (DesignSkills.md section 1)
# --------------------------------------------------------------------------- #
HEADER_BLACK = "000000"
HEADER_TEXT_WHITE = "FFFFFF"
CHECK_GREEN_FILL = "C6EFCE"
CHECK_GREEN_TEXT = "006100"
BAR_FILL_GREEN = "D4F1D6"
MUTED_AXIS_GRAY = "595959"
GRIDLINE_GRAY = "E5E5E5"
HIST_GRAY = "A6A6A6"        # 5-yr / historical context lines
LIGHT_GRAY = "D9D9D9"
HIGHLIGHT_GREEN = "16A34A"  # secondary / highlight line (high contrast)
INPUT_BLUE = "0000FF"       # financial-model convention for hand-entered inputs

FONT = "Calibri"

# --------------------------------------------------------------------------- #
# Fonts, fills, borders (section 2)
# --------------------------------------------------------------------------- #
TITLE_FONT = Font(name=FONT, size=18, bold=True)
SUBTITLE_FONT = Font(name=FONT, size=11, italic=True, color=MUTED_AXIS_GRAY)
SECTION_FONT = Font(name=FONT, size=13, bold=True)
HEADER_FONT = Font(name=FONT, size=11, bold=True, color=HEADER_TEXT_WHITE)
LABEL_FONT = Font(name=FONT, size=11)
LABEL_BOLD = Font(name=FONT, size=11, bold=True)
DATA_FONT = Font(name=FONT, size=11)
INPUT_FONT = Font(name=FONT, size=11, color=INPUT_BLUE)
CHECK_FONT = Font(name=FONT, size=11, bold=True, color=CHECK_GREEN_TEXT)
NOTE_FONT = Font(name=FONT, size=10, italic=True, color=MUTED_AXIS_GRAY)

HEADER_FILL = PatternFill(start_color=HEADER_BLACK, end_color=HEADER_BLACK, fill_type="solid")
CHECK_FILL = PatternFill(start_color=CHECK_GREEN_FILL, end_color=CHECK_GREEN_FILL, fill_type="solid")

THIN = Side(style="thin", color="000000")
MEDIUM = Side(style="medium", color="000000")
SUMMARY_TOP = Border(top=THIN)
SUMMARY_BOTTOM = Border(top=THIN, bottom=MEDIUM)

LEFT = Alignment(horizontal="left", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")
CENTER = Alignment(horizontal="center", vertical="center")
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

# --------------------------------------------------------------------------- #
# Number formats (section 2 + currency rule agreed for FOB work: 2 decimals)
# --------------------------------------------------------------------------- #
FMT_CURRENCY = '$#,##0.00;($#,##0.00);"-"'
FMT_CURRENCY_MANUAL = '$#,##0.00"*";($#,##0.00)"*";"-"'   # manual-indication marker
FMT_NUMBER2 = '#,##0.00;(#,##0.00);"-"'
FMT_BASIS = '+#,##0.00;-#,##0.00;0.00'
FMT_QTY = "#,##0"
FMT_FX = "0.0000"
FMT_DATE = "yyyy-mm-dd"
FMT_MONTH = "mmm-yy"


def title(ws, text: str, subtitle: str | None = None) -> None:
    """A1 title (18pt bold, left) and optional A2-adjacent subtitle."""
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = LEFT
    ws.row_dimensions[1].height = 28
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = SUBTITLE_FONT


def header_row(ws, row: int, labels: list[str], start_col: int = 1, height: float = 30) -> None:
    """Black header row, white bold text, centred vertically."""
    for j, text in enumerate(labels, start=start_col):
        c = ws.cell(row=row, column=j, value=text)
        c.font, c.fill, c.alignment = HEADER_FONT, HEADER_FILL, HEADER_ALIGN
    ws.row_dimensions[row].height = height


def check_row(ws, row: int, first_col: int, last_col: int, label: str | None = None) -> None:
    """Green validation (CHECK) row across the populated columns."""
    for j in range(first_col, last_col + 1):
        c = ws.cell(row=row, column=j)
        c.fill, c.font, c.border = CHECK_FILL, CHECK_FONT, SUMMARY_BOTTOM
    if label:
        ws.cell(row=row, column=first_col, value=label)


def data_cell(c, fmt: str | None = None, bold: bool = False, align=RIGHT) -> None:
    c.font = LABEL_BOLD if bold else DATA_FONT
    c.alignment = align
    if fmt:
        c.number_format = fmt


# --------------------------------------------------------------------------- #
# Chart rules (section 3) - consumed by the COM wiring step
# --------------------------------------------------------------------------- #
def rgb_int(hex_color: str) -> int:
    """Excel COM colour integer (BGR) from an RRGGBB hex string."""
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return r + (g << 8) + (b << 16)


# Line hierarchy for comparison charts: slot 1 = focus (hero), slot 2 = highlight, then muted context.
# (color, width_pt, dash) with dash per Excel msoLineDashStyle: 1 solid, 4 dash, 3 round dot, 7 long dash
LINE_SLOTS: list[tuple[str, float, int]] = [
    (HEADER_BLACK, 2.5, 1),
    (HIGHLIGHT_GREEN, 2.0, 1),
    (MUTED_AXIS_GRAY, 1.5, 7),
    (HIST_GRAY, 1.25, 4),
    (MUTED_AXIS_GRAY, 1.25, 3),
    (HIST_GRAY, 1.25, 7),
    ("7F7F7F", 1.25, 1),
    (HIST_GRAY, 1.25, 3),
    (LIGHT_GRAY, 1.5, 1),
]
CHART_TITLE_PT = 18
AXIS_PT = 10
