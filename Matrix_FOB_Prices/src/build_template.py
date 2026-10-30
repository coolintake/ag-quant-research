"""Step 2 of the build: write the STATIC template of Matrix_FOB_Calculator.xlsx with openpyxl.

Writes layout, Design styling, Config tables, the Calc row skeleton, the Manual_Inputs grid, data
validation and named ranges. It writes NO formulas: Excel's dynamic-array formulas (FILTER, XLOOKUP
with array logic, LET/LAMBDA) must be entered by Excel itself, which wire_workbook.py does next.

    python src/build_template.py        # refuses to overwrite a wired workbook
    python src/build_template.py --force    # rebuild from scratch (Manual_Inputs entries are lost!)

Never run openpyxl on the workbook after wiring: it would strip the Power Query connections.
"""

from __future__ import annotations

import argparse
import sys

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

import config
import fob_styles as S
import layout as L


def name(wb, nm: str, ref: str) -> None:
    wb.defined_names[nm] = DefinedName(nm, attr_text=ref)


def col_range(sheet: str, col: str, first: int, last: int) -> str:
    return f"'{sheet}'!${col}${first}:${col}${last}"


def list_validation(ws, rng: str, source: str, prompt: str | None = None) -> None:
    dv = DataValidation(type="list", formula1=f"={source}", allow_blank=True, showDropDown=False)
    if prompt:
        dv.promptTitle, dv.prompt, dv.showInputMessage = "Select", prompt, True
    ws.add_data_validation(dv)
    dv.add(rng)


def widths(ws, spec: dict[str, float]) -> None:
    for col, w in spec.items():
        ws.column_dimensions[col].width = w


# --------------------------------------------------------------------------- #
def build_config(wb, ws) -> None:
    S.title(ws, "Config", "Rules used by every formula. Edit with care: crops, zones, routes and the 6-month window.")
    # Crops
    hdr = ["Crop", "PDQ Code", "Cost Commodity", "Bushels per MT", "Basis Type", "Futures", "Allowed Zones"]
    S.header_row(ws, L.CFG_CROP_HDR, hdr)
    for i, c in enumerate(config.CROPS, start=L.CFG_CROP_HDR + 1):
        vals = [c["crop"], c["pdq"] or "none", c["cost"], c["bu"], c["basis"], c["futures"], c["zones"]]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row=i, column=j, value=v)
            S.data_cell(cell, "0.00000" if j == 4 else None, align=S.RIGHT if j == 4 else S.LEFT)
    first, last = L.CFG_CROP_HDR + 1, L.CFG_CROP_HDR + len(config.CROPS)
    for nm, col in [("Cfg_Crop", "A"), ("Cfg_PDQ", "B"), ("Cfg_CostCom", "C"), ("Cfg_Bu", "D"),
                    ("Cfg_BasisType", "E"), ("Cfg_Futures", "F"), ("Cfg_Zones", "G")]:
        name(wb, nm, col_range(L.CFG, col, first, last))
    # Zones
    zc = L.CFG_ZONE_COL
    S.header_row(ws, 4, ["PDQ Zone"], start_col=9)
    for i, z in enumerate(list(config.ZONES) + ["ALL"], start=5):
        S.data_cell(ws[f"{zc}{i}"], align=S.LEFT)
        ws[f"{zc}{i}"] = z
    name(wb, "Cfg_ZoneList", col_range(L.CFG, zc, 5, 4 + len(config.ZONES)))
    name(wb, "Cfg_ZoneAll", col_range(L.CFG, zc, 5, 5 + len(config.ZONES)))
    # Routes
    S.header_row(ws, 4, ["Route", "Code", "Costing Row (Raw)"], start_col=11)
    for i, r in enumerate(config.ROUTES, start=5):
        for j, v in enumerate([r["route"], r["code"], r["raw"]], start=11):
            S.data_cell(ws.cell(row=i, column=j, value=v), align=S.LEFT)
    for nm, col in [("Rt_Display", "K"), ("Rt_Code", "L"), ("Rt_Raw", "M")]:
        name(wb, nm, col_range(L.CFG, col, 5, 4 + len(config.ROUTES)))
    # Window (formulas for Date/YM/Label are written by wire_workbook.py)
    S.header_row(ws, 4, ["Month #", "Selector", "Delivery Month", "Delivery YM", "Label"], start_col=15)
    for k in range(1, config.WINDOW_MONTHS + 1):
        r = 4 + k
        S.data_cell(ws.cell(row=r, column=15, value=k), align=S.CENTER)
        S.data_cell(ws.cell(row=r, column=16, value=f"Month {k}"), align=S.LEFT)
        S.data_cell(ws.cell(row=r, column=17), S.FMT_MONTH)
        S.data_cell(ws.cell(row=r, column=18), align=S.LEFT)
        S.data_cell(ws.cell(row=r, column=19), align=S.LEFT)
    wl = 4 + config.WINDOW_MONTHS
    for nm, col in [("Win_K", "O"), ("Win_Sel", "P"), ("Win_Date", "Q"), ("Win_YM", "R"), ("Win_Label", "S")]:
        name(wb, nm, col_range(L.CFG, col, 5, wl))
    # Units & settings
    S.header_row(ws, 4, ["FOB Unit"], start_col=21)
    S.header_row(ws, 4, ["Manual Unit"], start_col=23)
    for i, u in enumerate(L.FOB_UNITS, start=5):
        S.data_cell(ws.cell(row=i, column=21, value=u), align=S.LEFT)
    for i, u in enumerate(L.MANUAL_UNITS, start=5):
        S.data_cell(ws.cell(row=i, column=23, value=u), align=S.LEFT)
    name(wb, "Cfg_FOBUnits", col_range(L.CFG, "U", 5, 4 + len(L.FOB_UNITS)))
    name(wb, "Cfg_ManUnits", col_range(L.CFG, "W", 5, 4 + len(L.MANUAL_UNITS)))
    S.header_row(ws, L.CFG_SET_ROW - 1, ["Setting", "Value"], start_col=21)
    for i, (lab, v, nm) in enumerate([("Manual entry stale after (days)", config.MANUAL_STALE_DAYS, "Set_ManualStale"),
                                      ("PDQ snapshot stale after (days)", config.PDQ_STALE_DAYS, "Set_PDQStale")]):
        r = L.CFG_SET_ROW + i
        S.data_cell(ws.cell(row=r, column=21, value=lab), align=S.LEFT)
        c = ws.cell(row=r, column=22, value=v)
        c.font, c.alignment = S.INPUT_FONT, S.RIGHT
        name(wb, nm, f"'{L.CFG}'!$V${r}")
    # Chart metrics
    mh = L.CFG_METRIC_HDR
    S.header_row(ws, mh, ["Chart Metric", "Base Col", "VC Col", "TBAY Col", "STL Col", "Per Bushel",
                          "Unit (wheat)", "Unit (canola)", "Unit (other)"])
    for i, m in enumerate(L.METRICS, start=mh + 1):
        for j, v in enumerate(m, 1):
            S.data_cell(ws.cell(row=i, column=j, value=v), align=S.LEFT if j in (1, 7, 8, 9) else S.CENTER)
    ml = mh + len(L.METRICS)
    for nm, col in [("Mt_Name", "A"), ("Mt_Base", "B"), ("Mt_VC", "C"), ("Mt_TBAY", "D"), ("Mt_STL", "E"),
                    ("Mt_PerBu", "F"), ("Mt_UnitW", "G"), ("Mt_UnitC", "H"), ("Mt_UnitO", "I")]:
        name(wb, nm, col_range(L.CFG, col, mh + 1, ml))
    S.header_row(ws, mh, ["Compare By"], start_col=11)
    S.header_row(ws, mh, ["Horizon", "Months Ahead"], start_col=13)
    S.header_row(ws, mh, ["Date Range", "Years"], start_col=16)
    for i, v in enumerate(L.COMPARE, start=mh + 1):
        S.data_cell(ws.cell(row=i, column=11, value=v), align=S.LEFT)
    for i, (lab, v) in enumerate(L.HORIZONS, start=mh + 1):
        S.data_cell(ws.cell(row=i, column=13, value=lab), align=S.LEFT)
        S.data_cell(ws.cell(row=i, column=14, value=v), align=S.CENTER)
    for i, (lab, v) in enumerate(L.RANGES, start=mh + 1):
        S.data_cell(ws.cell(row=i, column=16, value=lab), align=S.LEFT)
        S.data_cell(ws.cell(row=i, column=17, value=v), align=S.CENTER)
    name(wb, "Cmp_List", col_range(L.CFG, "K", mh + 1, mh + len(L.COMPARE)))
    name(wb, "Hz_List", col_range(L.CFG, "M", mh + 1, mh + len(L.HORIZONS)))
    name(wb, "Hz_Val", col_range(L.CFG, "N", mh + 1, mh + len(L.HORIZONS)))
    name(wb, "Rg_List", col_range(L.CFG, "P", mh + 1, mh + len(L.RANGES)))
    name(wb, "Rg_Years", col_range(L.CFG, "Q", mh + 1, mh + len(L.RANGES)))
    widths(ws, {"A": 32, "B": 11, "C": 15, "D": 14, "E": 11, "F": 22, "G": 26, "H": 15, "I": 20, "K": 15, "L": 8,
                "M": 17, "N": 13, "O": 9, "P": 14, "Q": 15, "R": 12, "S": 9, "U": 31, "V": 9, "W": 13})


def build_data_names(wb) -> None:
    for nm, ref in L.data_names().items():
        name(wb, nm, ref.lstrip("="))


def build_calc(wb, ws) -> None:
    S.title(ws, "Calc", "Engine: one row per crop x zone x route x window month. Formulas only - do not type here.")
    S.header_row(ws, L.CALC_HDR, [h for _, h in L.CALC_COLS], height=45)
    money = {"M", "N", "O", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AE"}
    for i, (crop, zone, route, k) in enumerate(L.CALC_ROWS, start=L.CALC_FIRST):
        for j, v in enumerate([crop, zone, route, k], 1):
            S.data_cell(ws.cell(row=i, column=j, value=v), align=S.LEFT if j < 4 else S.CENTER)
    for col, _ in L.CALC_COLS[4:]:
        fmt = (S.FMT_CURRENCY if col in money else S.FMT_MONTH if col == "L" else "0.00000" if col == "H"
               else "0.0000" if col in ("AB", "AC") else None)
        for r in range(L.CALC_FIRST, L.CALC_LAST + 1):
            c = ws[f"{col}{r}"]
            c.font = S.DATA_FONT
            c.alignment = S.LEFT if col in ("E", "F", "G", "I", "K", "P", "Q", "AA", "AD") else S.RIGHT
            if fmt:
                c.number_format = fmt
    names = {"Calc_Crop": "A", "Calc_Zone": "B", "Calc_Route": "C", "Calc_K": "D", "Calc_Key": "E",
             "Calc_Cash": "O", "Calc_Source": "P", "Calc_Cost": "W", "Calc_CADMT": "X", "Calc_USDMT": "Y",
             "Calc_USDBU": "Z", "Calc_FutCode": "AA", "Calc_FutPx": "AB", "Calc_XBasis": "AC", "Calc_XBasisUSD": "AE"}
    for nm, col in names.items():
        name(wb, nm, col_range(L.CALC, col, L.CALC_FIRST, L.CALC_LAST))
    ws.freeze_panes = f"E{L.CALC_FIRST}"
    ws.auto_filter.ref = f"A{L.CALC_HDR}:AE{L.CALC_LAST}"
    for col, _ in L.CALC_COLS:
        ws.column_dimensions[col].width = 13
    widths(ws, {"A": 13, "B": 11, "C": 15, "E": 34, "P": 11, "Q": 13})


def build_manual(wb, ws) -> None:
    S.title(ws, "Manual Indications",
            "Elevator cash bids for crops or months PDQ does not quote. They only fill gaps - PDQ quotes always win.")
    ws["A3"] = ("Blue cells are yours to fill (columns A-H). Example: 2026-10-15 | Red Lentils | SE SASK | "
                "<window month> | 33.50 | CAD/bu | Broker X | firm bid.  Zone 'ALL' applies to every zone without "
                "its own entry. Latest entry date wins. Grey columns are calculated.")
    ws["A3"].font = S.NOTE_FONT
    S.header_row(ws, L.MAN_HDR, L.MAN_INPUT_COLS + L.MAN_CALC_COLS, height=36)
    for r in range(L.MAN_FIRST, L.MAN_LAST + 1):
        for j in range(1, 9):
            c = ws.cell(row=r, column=j)
            c.font = S.INPUT_FONT
            c.alignment = S.RIGHT if j in (1, 5) else S.LEFT
            c.number_format = S.FMT_DATE if j == 1 else (S.FMT_NUMBER2 if j == 5 else "General")
        for j in range(9, 14):
            c = ws.cell(row=r, column=j)
            c.font = S.NOTE_FONT
            c.alignment = S.RIGHT if j == 9 else S.LEFT
            if j == 9:
                c.number_format = S.FMT_CURRENCY
    rng = lambda col: f"{col}{L.MAN_FIRST}:{col}{L.MAN_LAST}"
    list_validation(ws, rng("B"), "Cfg_Crop", "Crop")
    list_validation(ws, rng("C"), "Cfg_ZoneAll", "PDQ zone, or ALL")
    list_validation(ws, rng("D"), "Win_Label", "Pick a month of the current 6-month window (or type a date)")
    list_validation(ws, rng("F"), "Cfg_ManUnits", "CAD/MT or CAD/bu")
    dv = DataValidation(type="decimal", operator="greaterThan", formula1="0", allow_blank=True)
    dv.error, dv.errorTitle, dv.showErrorMessage = "Price must be a positive number.", "Invalid price", True
    ws.add_data_validation(dv)
    dv.add(rng("E"))
    dd = DataValidation(type="date", operator="greaterThan", formula1="43466", allow_blank=True)  # > 2019-01-01
    dd.error, dd.errorTitle, dd.showErrorMessage = "Enter a date (yyyy-mm-dd).", "Invalid date", True
    ws.add_data_validation(dd)
    dd.add(rng("A"))
    for nm, col in [("Man_Date", "A"), ("Man_Crop", "B"), ("Man_Zone", "C"), ("Man_Month", "D"), ("Man_Price", "E"),
                    ("Man_Unit", "F"), ("Man_PriceMT", "I"), ("Man_YM", "J"), ("Man_Key", "K"),
                    ("Man_Status", "L"), ("Man_Valid", "M")]:
        name(wb, nm, col_range(L.MANUAL, col, L.MAN_FIRST, L.MAN_LAST))
    ws.freeze_panes = f"A{L.MAN_FIRST}"
    widths(ws, {"A": 12, "B": 14, "C": 16, "D": 14, "E": 11, "F": 9, "G": 20, "H": 30, "I": 13, "J": 11,
                "K": 30, "L": 15, "M": 8})


def build_fob(wb, ws) -> None:
    S.title(ws, "FOB Export Price Matrix")
    r = L.FOB_CTRL_ROW
    for col, text in [("A", "Delivery month"), ("E", "Unit")]:
        ws[f"{col}{r}"] = text
        ws[f"{col}{r}"].font = S.LABEL_BOLD
    ws[f"B{r}"], ws[f"F{r}"] = "Month 1", "USD/MT"
    for c in (ws[f"B{r}"], ws[f"F{r}"]):
        c.font, c.alignment = S.INPUT_FONT, S.CENTER
    ws[f"C{r}"].font = S.LABEL_BOLD
    list_validation(ws, f"B{r}", "Win_Sel", "Window month")
    list_validation(ws, f"F{r}", "Cfg_FOBUnits", "Display unit")
    name(wb, "Sel_Month", f"'{L.FOB}'!$B${r}")
    name(wb, "Sel_Unit", f"'{L.FOB}'!$F${r}")
    name(wb, "Sel_K", f"'{L.FOB}'!$H${r}")
    name(wb, "Sel_UnitIdx", f"'{L.FOB}'!$I${r}")
    ws[f"H{r}"].font = ws[f"I{r}"].font = S.NOTE_FONT
    for b, route in enumerate(config.ROUTES):
        top = L.FOB_BLOCK_START + b * L.FOB_BLOCK_SIZE
        ws[f"A{top}"] = route["route"]
        ws[f"A{top}"].font = S.SECTION_FONT
        S.header_row(ws, top + 1, ["Crop"] + list(config.ZONES))
        for i, c in enumerate(config.CROPS):
            rr = top + 2 + i
            S.data_cell(ws.cell(row=rr, column=1, value=c["crop"]), align=S.LEFT)
            for j in range(2, 11):
                S.data_cell(ws.cell(row=rr, column=j), S.FMT_CURRENCY)
        S.check_row(ws, top + 2 + len(config.CROPS), 1, 10, "CHECK")
    note = L.FOB_BLOCK_START + len(config.ROUTES) * L.FOB_BLOCK_SIZE
    for k, t in enumerate([
        "* = manual indication (Manual_Inputs); unmarked = PDQ quote.  N/A = no quote for that zone/month, route not "
        "costed (Barley/Lentils at TBAY & STL, Alberta STL), or crop outside its belt (Durum: S ALTA & SW SASK only).",
        "FOB = cash + primary elevation + rail + port elevation + export margin + other fees (CAN_Costing_Input.xlsx / "
        "Raw), converted at BoC FXUSDCAD.  FOB TBAY excludes laker and St. Lawrence transfer.",
    ]):
        ws[f"A{note + k}"] = t
        ws[f"A{note + k}"].font = S.NOTE_FONT
    widths(ws, {"A": 16, **{get_column_letter(j): 13 for j in range(2, 11)}})
    ws.freeze_panes = "B6"


def build_basis(wb, ws) -> None:
    S.title(ws, "Export Basis Matrix")
    r = L.FOB_CTRL_ROW
    ws[f"A{r}"] = "Delivery month"
    ws[f"A{r}"].font = S.LABEL_BOLD
    ws[f"B{r}"] = "Month 1"
    ws[f"B{r}"].font, ws[f"B{r}"].alignment = S.INPUT_FONT, S.CENTER
    ws[f"C{r}"].font = S.LABEL_BOLD
    list_validation(ws, f"B{r}", "Win_Sel", "Window month")
    name(wb, "Sel_BMonth", f"'{L.BASIS}'!$B${r}")
    name(wb, "Sel_BK", f"'{L.BASIS}'!$E${r}")
    ws[f"E{r}"].font = S.NOTE_FONT
    for b, route in enumerate(config.ROUTES):
        top = L.BASIS_BLOCK_START + b * L.BASIS_BLOCK_SIZE
        ws[f"A{top}"] = route["route"]
        ws[f"A{top}"].font = S.SECTION_FONT
        S.header_row(ws, top + 1, ["Basis Line", "Unit"] + list(config.ZONES) + ["Futures Contract", "Futures Price"])
        for i, (lab, crop, col, unit) in enumerate(L.BASIS_LINES):
            rr = top + 2 + i
            S.data_cell(ws.cell(row=rr, column=1, value=lab), align=S.LEFT)
            S.data_cell(ws.cell(row=rr, column=2, value=unit), align=S.LEFT)
            for j in range(3, 12):
                S.data_cell(ws.cell(row=rr, column=j), S.FMT_BASIS)
            S.data_cell(ws.cell(row=rr, column=12), align=S.CENTER)
            S.data_cell(ws.cell(row=rr, column=13), "0.0000")
        S.check_row(ws, top + 2 + len(L.BASIS_LINES), 1, 13, "CHECK")
    note = L.BASIS_BLOCK_START + len(config.ROUTES) * L.BASIS_BLOCK_SIZE
    for k, t in enumerate([
        "Wheat export basis (USD/bu) = (FOB USD/MT - futures USD/MT) / 36.74371, vs MIAX/MGEX HRS (CWRS) or KC HRW (CPSR).",
        "Canola export basis = FOB CAD/MT - ICE Canola (CAD/MT); USD/MT line = same basis / USDCAD.  Futures prices are "
        "implied from the PDQ snapshot (cash - PDQ basis).  Durum and peas are non-hedgeable (flat price only).",
    ]):
        ws[f"A{note + k}"] = t
        ws[f"A{note + k}"].font = S.NOTE_FONT
    widths(ws, {"A": 21, "B": 9, **{get_column_letter(j): 11 for j in range(3, 12)}, "L": 17, "M": 15})
    ws.freeze_panes = "C6"


def build_hist(wb, ws) -> None:
    S.title(ws, "Historical Charts")
    labels = ["Commodity", "Focus Zone", "Route", "Metric", "Compare By", "Horizon", "Date Range"]
    defaults = ["CWRS", "NE SASK", "FOB VC", "FOB Price (USD/MT)", "Routes", "Nearby", "Since 2019"]
    sources = ["Cfg_Crop", "Cfg_ZoneList", "Rt_Display", "Mt_Name", "Cmp_List", "Hz_List", "Rg_List"]
    names = ["Hc_Crop", "Hc_Zone", "Hc_Route", "Hc_Metric", "Hc_Cmp", "Hc_H", "Hc_Range"]
    for j, (lab, d, src, nm) in enumerate(zip(labels, defaults, sources, names), start=1):
        col = get_column_letter(j)
        S.header_row(ws, L.HC_CTRL_LABEL_ROW, [lab], start_col=j, height=22)
        c = ws[f"{col}{L.HC_CTRL_ROW}"]
        c.value, c.font, c.alignment = d, S.INPUT_FONT, S.CENTER
        list_validation(ws, f"{col}{L.HC_CTRL_ROW}", src, lab)
        name(wb, nm, f"'{L.HIST}'!${col}${L.HC_CTRL_ROW}")
    for cell, nm, font in [(L.HC_TITLE_CELL, "Hc_Title", S.SECTION_FONT), (L.HC_UNIT_CELL, "Hc_Unit", S.LABEL_FONT),
                           (L.HC_NOTE_CELL, "Hc_Note", S.NOTE_FONT)]:
        ws[cell].font = font
        name(wb, nm, f"'{L.HIST}'!${cell[0]}${cell[1:]}")
    # helper grid (formulas by wire_workbook.py)
    hc = L.HC_HELP_COL
    ws[f"{hc}3"] = "Chart helper (formulas - do not edit)"
    ws[f"{hc}3"].font = S.NOTE_FONT
    for i, (lab, nm) in enumerate([("Start date", "Hc_Start"), ("Horizon value", "Hc_HVal"), ("Bushels per MT", "Hc_Bu"),
                                   ("Basis type", "Hc_Type"), ("Metric row", "Hc_MRow"), ("Per bushel", "Hc_PerBu"),
                                   ("Route-dependent", "Hc_RouteDep"), ("Range years", "Hc_RangeYears")], start=4):
        ws[f"{hc}{i}"] = lab
        ws[f"{hc}{i}"].font = S.NOTE_FONT
        ws[f"Q{i}"].font = S.NOTE_FONT
        name(wb, nm, f"'{L.HIST}'!$Q${i}")
    ws["Q4"].number_format = S.FMT_DATE
    for row, lab in [(L.HC_SLOT_ROW, "Slot"), (L.HC_ZONE_ROW, "Zone"), (L.HC_ROUTE_ROW, "Route"),
                     (L.HC_NAME_ROW, "Series name"), (L.HC_COLIDX_ROW, "Data column"), (L.HC_SPILL_ROW - 1, "Week")]:
        ws[f"{hc}{row}"] = lab
        ws[f"{hc}{row}"].font = S.LABEL_BOLD
    for s, col in enumerate(L.HC_SLOT_COLS, start=1):
        ws[f"{col}{L.HC_SLOT_ROW}"] = s
        ws[f"{col}{L.HC_SLOT_ROW}"].alignment = S.CENTER
    ws.column_dimensions[hc].width = 16
    widths(ws, {"A": 14, "B": 13, "C": 15, "D": 30, "E": 13, "F": 12, "G": 15})


def build_control(wb, ws) -> None:
    S.title(ws, "Matrix FOB Calculator", "Canadian grain export FOB prices - VC, Thunder Bay, St. Lawrence Direct")
    S.header_row(ws, 4, ["Run Status", "Value", "Check"])
    for i, lab in enumerate(["As-of date", "Run month (window start)", "PDQ snapshot date", "PDQ snapshot age (days)",
                             "USD/CAD (Bank of Canada)", "FX date", "FX source", "Costing file last saved",
                             "Data drop generated"], start=5):
        S.data_cell(ws.cell(row=i, column=1, value=lab), align=S.LEFT)
        S.data_cell(ws.cell(row=i, column=2), align=S.RIGHT)
        S.data_cell(ws.cell(row=i, column=3), align=S.LEFT)
    S.header_row(ws, 15, ["Data Checks", "Value", "Status"])
    for i, lab in enumerate(["PDQ rows loaded", "Costing rows loaded", "History rows loaded", "Matrix cells from PDQ",
                             "Matrix cells from manual entries", "Matrix cells N/A or excluded",
                             "Manual entries OK / stale / rejected", "Formula errors in Calc"], start=16):
        S.data_cell(ws.cell(row=i, column=1, value=lab), align=S.LEFT)
        S.data_cell(ws.cell(row=i, column=2), S.FMT_QTY)
        S.data_cell(ws.cell(row=i, column=3), align=S.LEFT)
    S.check_row(ws, 24, 1, 3, "CHECK")
    steps = [
        "HOW TO USE",
        "1. Download the latest PDQ price CSV into Canada_Crop_Prices\\input and run  python backfill_pdq.py  there.",
        "2. Double-click Refresh_FOB.bat (data drops + Refresh All + save + verify), or run the scripts and use "
        "Data > Refresh All.",
        "3. Enter elevator bids for gaps on Manual_Inputs (they never override PDQ). Matrices update instantly.",
        "4. Save, then double-click Save_Snapshot.bat to record today's matrix in pdq_fob_history.parquet.",
        "Logic, sources and formulas: README_FOB_Logic.md.  Rules: Config tab.",
    ]
    for k, t in enumerate(steps, start=26):
        ws[f"A{k}"] = t
        ws[f"A{k}"].font = S.LABEL_BOLD if k == 26 else S.LABEL_FONT
    widths(ws, {"A": 36, "B": 22, "C": 44})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if config.WORKBOOK.exists() and not args.force:
        print(f"{config.WORKBOOK.name} exists. Re-running would discard Manual_Inputs and the Power Query wiring; "
              "use --force to rebuild from scratch.")
        return 1
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {n: wb.create_sheet(n) for n in L.SHEETS}
    build_config(wb, sheets[L.CFG])
    build_data_names(wb)
    build_calc(wb, sheets[L.CALC])
    build_manual(wb, sheets[L.MANUAL])
    build_fob(wb, sheets[L.FOB])
    build_basis(wb, sheets[L.BASIS])
    build_hist(wb, sheets[L.HIST])
    build_control(wb, sheets[L.CONTROL])
    for n in L.HIDDEN:
        sheets[n].sheet_state = "hidden"
    for n in (L.D_PDQ, L.D_FX, L.D_COST, L.D_HIST):
        sheets[n].sheet_properties.tabColor = "595959"
    wb.active = 0
    wb.save(config.WORKBOOK)
    print(f"template written: {config.WORKBOOK} ({len(L.CALC_ROWS)} Calc rows, {len(wb.defined_names)} named ranges)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
