"""V2 configuration: every path, crop mapping, window, base period and guardrail (SPECIFICATION_V2.md).

Imported as ``import config as C`` from this folder (run_v2.py puts it first on sys.path). V2 never
imports Canada_Crop_Prices code, so its config.py cannot collide. All sources are read-only.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
V2_ROOT = Path(__file__).resolve().parent                    # .../Canada_Planting_Model/v2_relative_index_model
PYTHON_ROOT = V2_ROOT.parents[1]                             # .../Desktop/Python
STATCAN_CSV = PYTHON_ROOT / "Workspace_CanadaSD" / "Data_Inputs" / "Planting_32100359-eng" / "32100359.csv"
PRICE_DB = PYTHON_ROOT / "Canada_Crop_Prices" / "data" / "crop_prices.duckdb"
PRICE_TABLE = "unified_cash_prices"
RPA_ROOT = PYTHON_ROOT / "RPA_Calculator"
RPA_WORKBOOK = RPA_ROOT / "output" / "RPA_Master_Calculator.xlsx"
RPA_HISTORY = {
    "AB": RPA_ROOT / "output" / "ab" / "mvs" / "ab_historical_2019_2026.csv",
    "SK": RPA_ROOT / "output" / "sk" / "mvs" / "sk_historical_2019_2026.csv",
    "MB": RPA_ROOT / "output" / "mb" / "mvs" / "mb_historical_2019_2026.csv",
}
INPUTS_DIR = V2_ROOT / "inputs"
PLAN_A_CSV = INPUTS_DIR / "plan_a_assignments.csv"
OUTPUT_DIR = V2_ROOT / "output"
PANEL_CSV = OUTPUT_DIR / "v2_panel.csv"
WORKBOOK_PATTERN = "Prairie_RPA_Acreage_Diagnostic_{short}.xlsx"     # one workbook per window (R1, R2, R3)
DIAGNOSTICS_MD = OUTPUT_DIR / "v2_diagnostics.md"

# --------------------------------------------------------------------------- #
# Calendar (crop year T = planting T = marketing year T/T+1 = budget year T)
# --------------------------------------------------------------------------- #
YEARS = tuple(range(2019, 2027))
BASE_YEARS = tuple(range(2019, 2025))          # Base-100 = mean RPA of the same window, 2019-2024
REFERENCE_WINDOW = "RUN3"                      # headline window for the Markdown diagnostics only
MIN_BASE_YEARS = 4
HISTORY_COST_YEARS = tuple(range(2019, 2026))  # normalized history tables
WORKBOOK_COST_YEAR = 2026                      # RPA_Master_Calculator.xlsx

WINDOWS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    # (month, day, year offset vs T) start, end - both inclusive
    "RUN1": ((12, 15, -1), (1, 15, 0)),
    "RUN2": ((1, 15, 0), (2, 15, 0)),
    "RUN3": ((2, 15, 0), (3, 15, 0)),
}
WINDOW_LABELS = {"RUN1": "Run 1 (Dec 15 - Jan 15)", "RUN2": "Run 2 (Jan 15 - Feb 15)",
                 "RUN3": "Run 3 (Feb 15 - Mar 15)"}
WINDOW_SHORT = {"RUN1": "R1", "RUN2": "R2", "RUN3": "R3"}
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
PROMPT_LAST_DELIVERY_MONTH = 7                 # roll-forward never beyond JUL of T (old crop)

# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #
MIN_QUOTED_DAYS = 10
YIELD_TREND_FIRST_YEAR = 2005                  # fit on 2005 .. T-1 (expanding)
YIELD_TREND_MIN_N = 10
HA_TO_AC = 2.47105

# --------------------------------------------------------------------------- #
# Geography and crops
# --------------------------------------------------------------------------- #
PROVINCES = {"MB": "Manitoba", "SK": "Saskatchewan", "AB": "Alberta"}
PRAIRIE = "PRAIRIE"
BENCHMARK = "CWRS"
CWRS_COMPETITOR = "CANOLA"                     # CWRS's own signal = RPA_CWRS / RPA_CANOLA

# crop -> PDQ symbol, CANSIM series, $/bu sanity range, kg per bushel, budget keys, workbook label
CROPS: dict[str, dict] = {
    "CWRS":   {"label": "CWRS Wheat", "pdq": "CWRS", "pdq_name": "1 CWRS 13.5",
               "statcan": "Wheat, Canada Western Red Spring (CWRS)", "price_range": (3.0, 20.0),
               "kg_per_bu": 27.2155, "budget": "wheat_spring", "workbook": "CWRS Wheat"},
    "CPSR":   {"label": "CPSR Wheat", "pdq": "CPSR", "pdq_name": "1 CPSR 11.5",
               "statcan": "Wheat, Canada Prairie Spring Red (CPSR) and Canada Prairie Spring White (CPSW)",
               "price_range": (3.0, 20.0), "kg_per_bu": 27.2155, "budget": "wheat_cps", "workbook": "CPS Wheat"},
    "DURUM":  {"label": "Durum Wheat", "pdq": "DURUM", "pdq_name": "1 CWAD 13.0",
               "statcan": "Wheat, durum", "price_range": (3.0, 25.0),
               "kg_per_bu": 27.2155, "budget": "durum", "workbook": "Durum Wheat"},
    "CANOLA": {"label": "Canola", "pdq": "CANOLA", "pdq_name": "1 CDA CANOLA",
               "statcan": "Canola (rapeseed)", "price_range": (5.0, 35.0),
               "kg_per_bu": 22.6796, "budget": "canola", "workbook": "Canola"},
    "Y_PEAS": {"label": "Yellow Peas", "pdq": "Y_PEAS", "pdq_name": "2 YELLOW PEAS",
               "statcan": "Peas, dry", "price_range": (3.0, 25.0),
               "kg_per_bu": 27.2155, "budget": "peas_yellow", "workbook": "Yellow Peas"},
}
CROP_ORDER = ("CWRS", "CPSR", "DURUM", "CANOLA", "Y_PEAS")

PROVINCE_CROPS = {
    "MB": ("CWRS", "CANOLA", "Y_PEAS"),
    "SK": ("CWRS", "CPSR", "DURUM", "CANOLA", "Y_PEAS"),
    "AB": ("CWRS", "CPSR", "DURUM", "CANOLA", "Y_PEAS"),
}
EXCLUSIONS = {("MB", "DURUM"): "no Durum-belt PDQ zone and no MB Durum budget",
              ("MB", "CPSR"): "zero PDQ CPSR quotes in MB 2019-2026"}

# PDQ zone filter: None = every zone of the province; Durum = the Durum belt only
ZONE_FILTER: dict[str, tuple[str, ...] | None] = {
    "CWRS": None, "CPSR": None, "CANOLA": None, "Y_PEAS": None, "DURUM": ("S ALTA", "SW SASK"),
}

# Cost rules
CPS_COST_PROXY = {"SK": "wheat_spring"}        # SK publishes no CPS budget: CWRS costs, all years
ACTIVE_ZONES = ("brown", "dark_brown", "black")          # AB [REGION-AVG-RULE]
SOUTHERN_ZONES = ("brown", "dark_brown")
SOUTHERN_BUDGETS = frozenset({"durum"})

# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
FLAGS = {
    "THIN_WINDOW": f"fewer than {MIN_QUOTED_DAYS} quoted days in the window; price NULL",
    "UNIT_SUSPECT": "bids outside the crop's plausible $/bu range were rejected",
    "NO_QUOTES": "no PDQ quote in the window",
    "INSUFFICIENT_YIELD_HISTORY": f"fewer than {YIELD_TREND_MIN_N} yields in {YIELD_TREND_FIRST_YEAR}..T-1",
    "NONPOSITIVE_RPA": "RPA <= 0; ratios built from it are NULL",
    "UNSTABLE_BASE": f"base <= 0 or fewer than {MIN_BASE_YEARS} valid base years",
    "CWRS_VS_CANOLA": "CWRS signal = RPA_CWRS / RPA_Canola",
    "PLAN_B_NO_SIGNAL": "pooled slope b <= 0; Plan-B not used for the window",
    "PLAN_A_SIGN_CONFLICT": "analyst Plan-A points opposite to the Plan-B prior; second review",
    "PLAN_A_OUTSIDE_RANGE": "analyst Plan-A exceeds the series' largest historical |% change|; second review",
    "PARTIAL_AGGREGATE": "Prairie value built from fewer than all in-scope provinces",
    "PRELIMINARY_ACRES": "latest CANSIM year: preliminary estimate",
    "COST_PROXY_CWRS": "SK CPSR costed on the SK CWRS budget",
}

# Chart palette (categorical slots in fixed order; CWRS = dark reference line)
CROP_COLORS = {"CWRS": "52514E", "CPSR": "EB6834", "DURUM": "1BAF7A", "CANOLA": "EDA100", "Y_PEAS": "2A78D6"}
