"""Workbook theme: every font, colour, border and number format used by the Excel outputs.

Edit this file to restyle all workbooks (master calculator, reference tables) at once. The
defaults follow the CanadaSD_Engine *_Yr tabs (Calibri 10, solid black header row with bold
white text, unfilled body, bold key rows, rule lines between blocks, Engine green / red check
palette) and the RPA_Summary_SK mock-up:

    * units live in a Unit column or in the header, never inside a value's number format
    * key rows (Gross Revenue, Direct Cash Costs, Return) bold; a medium rule closes each block
    * inputs are the only filled cells (soft yellow, blue text); the live Forecast column is blue
"""

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ---- palette
INK = "000000"            # header fill, body text
SLATE = "1E293B"          # titles
MUTED = "475569"          # subtitles, notes
RULE = "B4B4B4"           # thin cell borders (CanadaSD_Engine THIN_BORDER)
SECTION_BG = "E2E8F0"     # section / definitions banner
INPUT_BG, INPUT_INK = "FFF2CC", "1F4E9D"
LIVE_BG = "DBEAFE"        # live / forecast cells
GOOD_BG, GOOD_INK = "C6EFCE", "006100"   # Engine CHECK_OK: rank cells
BAD_INK = "9C0006"                       # Engine CHECK_WARN: negative returns

FONT_NAME = "Calibri"


def font(size: float = 10, bold: bool = False, italic: bool = False, color: str = INK) -> Font:
    return Font(name=FONT_NAME, size=size, bold=bold, italic=italic, color=color)


def fill(rgb: str) -> PatternFill:
    return PatternFill("solid", fgColor=rgb)


TITLE_FONT = font(16, bold=True, color=SLATE)
SUBTITLE_FONT = font(10, italic=True, color=MUTED)
SECTION_FONT = font(11, bold=True, color=SLATE)
HEADER_FONT = font(10, bold=True, color="FFFFFF")
TEXT_FONT = font(10)
BOLD_FONT = font(10, bold=True)
SMALL_FONT = font(8)
NOTE_FONT = font(9, italic=True, color=MUTED)
INPUT_FONT = font(10, bold=True, color=INPUT_INK)
RANK_FONT = font(10, color=GOOD_INK)
RED_FONT = font(10, color=BAD_INK)
NUM_FONT, BOLD_NUM_FONT = TEXT_FONT, BOLD_FONT          # numbers share the body font

HEADER_FILL = fill(INK)
SECTION_FILL = fill(SECTION_BG)
INPUT_FILL = fill(INPUT_BG)
LIVE_FILL = KEY_FILL = fill(LIVE_BG)
RANK_FILL = fill(GOOD_BG)
BASE_FILL = PatternFill(fill_type=None)                  # Last Yr cells stay unfilled

THIN = Side(style="thin", color=RULE)
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BLOCK_END = Side(style="medium", color=INK)              # closes a block of rows
SECTION_RULE = Border(bottom=Side(style="medium", color=SLATE))

LEFT = Alignment(horizontal="left", vertical="center", indent=1)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")

# ---- number formats (no units: the Unit column / header carries them)
MONEY = "$#,##0.00"
RETURN = '_-"$"* #,##0_-;\\-"$"* #,##0_-;_-"$"* "-"??_-;_-@_-'   # accounting, whole dollars
PER_LB = "$#,##0.000"
PRICE = {"$/bu": "$#,##0.00", "c/lb": "#,##0.00"}
YIELD = {"$/bu": "#,##0", "c/lb": "#,##0"}
YIELD_DEC = {"$/bu": "#,##0.0", "c/lb": "#,##0"}
RATE = "#,##0.0"
PERCENT = "0.0%"
RATIO = "0.00"
RANK = "0"
INT = "#,##0"
CURRENCY = MONEY                                          # alias kept for older callers

# ---- sizes
TITLE_ROW_HEIGHT = 24
LABEL_WIDTH, UNIT_WIDTH, CROP_WIDTH = 28, 14, 12
