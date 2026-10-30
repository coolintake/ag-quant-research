"""Cell layout of Matrix_FOB_Calculator.xlsx, shared by build_template.py (openpyxl) and
wire_workbook.py (Excel COM) so the two build steps always agree on addresses."""

from __future__ import annotations

import sys

import config

sys.path.insert(0, str(config.PROJECT_DIR / "Hammersmith"))
import hammersmith_scraper as hs  # noqa: E402  (CSV drop column order = the Power Query contract)

# Sheet names (tab order)
CONTROL, FOB, BASIS, HIST = "Control", "FOB_Summary", "Basis", "Historical_Charts"
MANUAL, CALC, CFG = "Manual_Inputs", "Calc", "Config"
D_PDQ, D_FX, D_COST, D_HIST = "Data_PDQ", "Data_FX", "Data_Costing", "Data_History"
SHEETS = [CONTROL, FOB, BASIS, HIST, MANUAL, CALC, CFG, D_PDQ, D_FX, D_COST, D_HIST]
HIDDEN = [D_HIST]
# World (Hammersmith) tabs: created and wired through Excel COM by wire_world.py (never by openpyxl)
W_MTX, W_CIF, W_IND, W_FRT = "World_FOB_Matrix", "Global_CIF_Matrix", "FOB_Indications", "Freight_Rates"
W_CFG, D_WORLD, D_FRT = "World_Config", "Data_World", "Data_Freight"

# Data tabs: Power Query tables at A1; column order fixed by each query's SelectColumns
PDQ_COLS = ["key", "pdq_code", "zone", "delivery_ym", "price_date", "cash_cad_mt", "futures_code", "futures_px",
            "local_basis"]
FX_COLS = ["as_of", "run_month", "pdq_date", "pdq_age_days", "fx_date", "usdcad", "fx_source", "costing_file",
           "costing_modified", "generated_at",
           # Hammersmith step status (run_pull.world_step), columns K..Q
           "world_report_date", "world_age_days", "world_status", "world_rows", "world_freight_rows",
           "world_outliers", "world_unmapped"]
COST_COLS = ["key", "commodity", "zone", "route", "primary", "rail", "port_elev", "margin", "other_fees"]
HIST_COLS = ["week", "crop", "zone", "horizon", "delivery_ym", "cash_cad_mt", "futures_code", "futures_px",
             "local_basis", "usdcad", "cost_vc", "cost_tbay", "cost_stl", "fob_vc_usd_mt", "fob_tbay_usd_mt",
             "fob_stl_usd_mt", "xbasis_vc", "xbasis_tbay", "xbasis_stl", "cost_basis"]
WORLD_COLS, FREIGHT_COLS = hs.PRICE_DROP_COLS, hs.FREIGHT_DROP_COLS
PDQ_MAX, COST_MAX, HIST_MAX, WORLD_MAX = 5000, 3000, 100000, 2000
QUERIES = {  # query name -> (sheet, table name)
    "Q_PDQ": (D_PDQ, "tPDQ"), "Q_FX": (D_FX, "tFX"), "Q_Costing": (D_COST, "tCosting"),
    "Q_History": (D_HIST, "tHistory"), "Q_World": (D_WORLD, "tWorld"), "Q_Freight": (D_FRT, "tFreight"),
}

# Calc: one row per crop x zone x route x window month
CALC_HDR = 4
CALC_FIRST = 5
CALC_ROWS = [(c["crop"], z, r["route"], k) for c in config.CROPS for z in config.ZONES
             for r in config.ROUTES for k in range(1, config.WINDOW_MONTHS + 1)]
CALC_LAST = CALC_FIRST + len(CALC_ROWS) - 1
CALC_COLS = [  # (column letter, header)
    ("A", "Crop"), ("B", "PDQ Zone"), ("C", "Route"), ("D", "Window Month #"), ("E", "Key"),
    ("F", "PDQ Code"), ("G", "Cost Commodity"), ("H", "Bushels per MT"), ("I", "Basis Type"), ("J", "Zone Allowed"),
    ("K", "Delivery YM"), ("L", "Delivery Month"), ("M", "PDQ Cash (CAD/MT)"), ("N", "Manual Cash (CAD/MT)"),
    ("O", "Cash Used (CAD/MT)"), ("P", "Price Source"), ("Q", "Costing Route"), ("R", "Primary Elevation"),
    ("S", "Rail Freight"), ("T", "Port Elevation"), ("U", "Export Margin"), ("V", "Other Fees"),
    ("W", "Cost Stack (CAD/MT)"), ("X", "FOB (CAD/MT)"), ("Y", "FOB (USD/MT)"), ("Z", "FOB (USD/bu)"),
    ("AA", "Futures Contract"), ("AB", "Futures Price"), ("AC", "Export Basis"), ("AD", "Basis Unit"),
    ("AE", "Export Basis (USD/MT)"),
]

# Manual_Inputs: input columns A-H, formula columns I-M
MAN_HDR, MAN_FIRST, MAN_LAST = 5, 6, 505
MAN_INPUT_COLS = ["Entry Date", "Crop", "PDQ Zone (or ALL)", "Delivery Month", "Cash Price", "Unit",
                  "Source / Broker", "Notes"]
MAN_CALC_COLS = ["Price (CAD/MT)", "Delivery YM", "Key", "Status", "Valid"]

# Config tab blocks
CFG_CROP_HDR = 4          # A..G, rows 5..13
CFG_ZONE_COL = "I"        # I5:I13 zones, I14 'ALL'
CFG_ROUTE_COL = "K"       # K..M rows 5..7
CFG_WIN_COL = "O"         # O..S rows 5..10
CFG_UNIT_COL = "U"        # U5:U7 FOB units; W5:W6 manual units
CFG_SET_ROW = 10          # U10/V10 settings
CFG_METRIC_HDR = 17       # A..I rows 18..25
CFG_LISTS_HDR = 17        # K (compare), M/N (horizon), P/Q (range)

# FOB_Summary / Basis matrices
FOB_CTRL_ROW = 4
FOB_BLOCK_START = 6       # block = title, header, 9 crops, CHECK, blank
FOB_BLOCK_SIZE = 13
BASIS_LINES = [  # (label, crop, Calc column for value)
    ("CWRS export basis", "CWRS", "AC", "USD/bu"),
    ("CPSR export basis", "CPSR", "AC", "USD/bu"),
    ("Canola export basis", "Canola", "AC", "CAD/MT"),
    ("Canola export basis", "Canola", "AE", "USD/MT"),
]
BASIS_BLOCK_START = 6
BASIS_BLOCK_SIZE = len(BASIS_LINES) + 4

# Historical_Charts
HC_CTRL_LABEL_ROW, HC_CTRL_ROW = 4, 5      # controls in A..G
HC_TITLE_CELL, HC_UNIT_CELL, HC_NOTE_CELL = "A7", "A8", "A9"
HC_CHART_ANCHOR = ("A11", "N40")
HC_HELP_COL = "P"                          # helper grid P..Y
HC_SLOT_ROW, HC_ZONE_ROW, HC_ROUTE_ROW, HC_NAME_ROW, HC_COLIDX_ROW, HC_SPILL_ROW = 15, 16, 17, 18, 19, 21
HC_SLOT_COLS = ["Q", "R", "S", "T", "U", "V", "W", "X", "Y"]

METRICS = [  # name, base col, vc, tbay, stl (Data_History 1-based col), per-bu, unit wheat, unit canola, unit other
    ("Prairie Cash Bid (CAD/MT)", 6, 0, 0, 0, "N", "CAD/MT", "CAD/MT", "CAD/MT"),
    ("Prairie Cash Bid (CAD/bu)", 6, 0, 0, 0, "Y", "CAD/bu", "CAD/bu", "CAD/bu"),
    ("Futures Settle", 8, 0, 0, 0, "N", "USD/bu", "CAD/MT", "n/a (non-hedgeable)"),
    ("Local Basis (PDQ published)", 9, 0, 0, 0, "N", "$/bu (CAD cash - USD futures)", "CAD/MT", "n/a (non-hedgeable)"),
    ("Export Basis", 0, 17, 18, 19, "N", "USD/bu", "CAD/MT", "n/a (non-hedgeable)"),
    ("FOB Price (USD/MT)", 0, 14, 15, 16, "N", "USD/MT", "USD/MT", "USD/MT"),
    ("FOB Price (USD/bu)", 0, 14, 15, 16, "Y", "USD/bu", "USD/bu", "USD/bu"),
    ("Freight & Costing Stack (CAD/MT)", 0, 11, 12, 13, "N", "CAD/MT", "CAD/MT", "CAD/MT"),
]
COMPARE = ["None", "Zones", "Routes"]
HORIZONS = [("Nearby", 0), ("+1 month", 1), ("+2 months", 2), ("+3 months", 3)]
RANGES = [("Since 2019", 99), ("Last 5 years", 5), ("Last 3 years", 3), ("Last 12 months", 1)]
FOB_UNITS = ["USD/MT", "USD/bu", "CAD/MT"]
MANUAL_UNITS = ["CAD/MT", "CAD/bu"]


def data_names() -> dict[str, str]:
    """Named ranges over the Power Query tables (fixed addresses, re-applied after each table load because
    Excel shifts names when a query table inserts cells)."""
    rng = lambda sh, col, a, b: f"='{sh}'!${col}${a}:${col}${b}"
    out = {}
    for nm, col in [("PDQ_Key", "A"), ("PDQ_Cash", "F"), ("PDQ_FutCode", "G"), ("PDQ_FutPx", "H")]:
        out[nm] = rng(D_PDQ, col, 2, PDQ_MAX)
    for nm, col in [("AsOfDate", "A"), ("RunMonth", "B"), ("PDQ_Date", "C"), ("PDQ_Age", "D"), ("FX_Date", "E"),
                    ("FX_USDCAD", "F"), ("FX_Source", "G"), ("Costing_Modified", "I"), ("Drop_Generated", "J")]:
        out[nm] = f"='{D_FX}'!${col}$2"
    for nm, col in [("Cost_Key", "A"), ("Cost_Primary", "E"), ("Cost_Rail", "F"), ("Cost_Port", "G"),
                    ("Cost_Margin", "H"), ("Cost_Other", "I")]:
        out[nm] = rng(D_COST, col, 2, COST_MAX)
    for nm, col in [("H_Week", "A"), ("H_Crop", "B"), ("H_Zone", "C"), ("H_H", "D"), ("H_CostBasis", "T")]:
        out[nm] = rng(D_HIST, col, 2, HIST_MAX)
    out["H_Block"] = f"='{D_HIST}'!$A$2:$T${HIST_MAX}"
    for nm, col in [("World_Date", "K"), ("World_Age", "L"), ("World_Status", "M"), ("World_Rows", "N"),
                    ("World_FreightRows", "O"), ("World_Outliers", "P"), ("World_Unmapped", "Q")]:
        out[nm] = f"='{D_FX}'!${col}$2"
    for nm, field in [("W_Key", "key"), ("W_Report", "report_date"), ("W_Series", "series_id"),
                      ("W_Commodity", "commodity"), ("W_YM", "delivery_ym"), ("W_Deriv", "derivation"),
                      ("W_USDMT", "fob_usd_mt"), ("W_Flag", "quality_flag")]:
        out[nm] = rng(D_WORLD, col_letter(WORLD_COLS.index(field) + 1), 2, WORLD_MAX)
    out["W_Block"] = f"='{D_WORLD}'!$A$2:${col_letter(len(WORLD_COLS))}${WORLD_MAX}"
    for nm, field in [("F_Key", "key"), ("F_OriginKey", "origin_key"), ("F_Dest", "destination"),
                      ("F_Vessel", "vessel_class"), ("F_USDMT", "freight_usd_mt"), ("F_Flag", "quality_flag"),
                      ("F_Pref", "cif_pref")]:
        out[nm] = rng(D_FRT, col_letter(FREIGHT_COLS.index(field) + 1), 2, WORLD_MAX)
    out["F_Block"] = f"='{D_FRT}'!$A$2:${col_letter(len(FREIGHT_COLS))}${WORLD_MAX}"
    return out


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
