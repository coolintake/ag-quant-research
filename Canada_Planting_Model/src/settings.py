"""Paths, crop registry, anchor pairs and calibration constants.

Named ``settings`` (not ``config``) so it never shadows ``Canada_Crop_Prices/config.py``, which
``price_aggregator`` imports as a top-level module.

Spec: model_crop_wallet_step1.md (2 data sources and units, 3 anchor tiers, 4 prices, 5 yields,
7 calibration bounds).
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
MODEL_ROOT = Path(__file__).resolve().parents[1]          # Canada_Planting_Model
PYTHON_ROOT = MODEL_ROOT.parent                            # ...\Desktop\Python
RPA_ROOT = PYTHON_ROOT / "RPA_Calculator"
PRICES_ROOT = PYTHON_ROOT / "Canada_Crop_Prices"
STATCAN_CSV = PYTHON_ROOT / "Workspace_CanadaSD" / "Data_Inputs" / "Planting_32100359-eng" / "32100359.csv"

# Variable costs. The master workbook holds the 2026 guide only; 2019-2025 come from
# RPA_Calculator's normalized history tables (the source of the workbook's Historical_Ratios
# sheet, which data_loader.validate_against_workbook reproduces on every run).
RPA_WORKBOOK = RPA_ROOT / "output" / "RPA_Master_Calculator.xlsx"
RPA_HISTORY = {
    "AB": RPA_ROOT / "output" / "ab" / "mvs" / "ab_historical_2019_2026.csv",
    "SK": RPA_ROOT / "output" / "sk" / "mvs" / "sk_historical_2019_2026.csv",
    "MB": RPA_ROOT / "output" / "mb" / "mvs" / "mb_historical_2019_2026.csv",
}
WORKBOOK_YEAR = 2026
HISTORY_YEARS = tuple(range(2019, 2026))

OUTPUT_DIR = MODEL_ROOT / "output"
REPORTS_DIR = OUTPUT_DIR / "reports"
CHARTS_DIR = OUTPUT_DIR / "charts"

# --------------------------------------------------------------------------- #
# Sample and loop order
# --------------------------------------------------------------------------- #
YEARS = tuple(range(2019, 2026))      # calibration sample (planting year = marketing year Y/Y+1 = guide year Y)
PROVINCES = {"MB": "Manitoba", "SK": "Saskatchewan", "AB": "Alberta"}   # outer loop order
WINDOWS = ("RUN1", "RUN2", "RUN3")
WINDOW_LABELS = {
    "RUN1": "Run 1 (Dec 15 - Jan 15)",
    "RUN2": "Run 2 (Jan 15 - Feb 15)",
    "RUN3": "Run 3 (Feb 15 - Mar 15)",
}
# PDQ spatial filter. None = every PDQ zone (provincial average); Durum = the durum belt only.
PDQ_ZONE_FILTER: dict[str, tuple[str, ...] | None] = {
    "CWRS": None,
    "CANOLA": None,
    "Y_PEAS": None,
    "DURUM": ("S ALTA", "SW SASK"),
}

# --------------------------------------------------------------------------- #
# Price pipeline (spec 4.1 prompt rule, 4.3 fallback hierarchy)
# --------------------------------------------------------------------------- #
PRICE_DB = PRICES_ROOT / "data" / "crop_prices.duckdb"      # opened READ_ONLY
PRICE_TABLE = "unified_cash_prices"
PRICE_TEMPLATE = RPA_ROOT / "Input_Prices" / "crop_price_template.csv"
PRICE_YEARS = tuple(range(2019, 2028))   # calibration 2019-2025, forecast 2026/27 and 2027/28

# Window w for planting year T: (start month, day, year offset), (end month, day, year offset).
# Both ends inclusive. The start month (with its year) is the primary prompt delivery period.
PRICE_WINDOWS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "RUN1": ((12, 15, -1), (1, 15, 0)),
    "RUN2": ((1, 15, 0), (2, 15, 0)),
    "RUN3": ((2, 15, 0), (3, 15, 0)),
}
# Prompt roll-forward stops at the last old-crop delivery month (JUL of T): a prompt bid is a
# nearby spot signal and never a new-crop forward price.
PROMPT_LAST_DELIVERY_MONTH = 7
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

# Price tiers and flags (spec 4.3)
TIER_PDQ, TIER_TEMPLATE, TIER_GUIDE, TIER_NULL = "PDQ_PROMPT", "TEMPLATE_PRICE", "BUDGET_FALLBACK", "MISSING_PRICE_DATA"

# Expected yield (spec 5): 21-year OLS trend on T-21 .. T-1 evaluated at T, >= 10 points;
# otherwise the T-5 .. T-1 mean (>= 3 points), flagged INSUFFICIENT_YIELD_HISTORY.
YIELD_TREND_YEARS = 21
YIELD_TREND_MIN_N = 10
YIELD_SMA_YEARS = 5
YIELD_SMA_MIN_N = 3
STATCAN_FIRST_YEAR = YEARS[0] - YIELD_TREND_YEARS - 1     # history needed for the first trend window
BASELINE_LOOKBACK = 5                 # R-bar = mean ratio over t-5 .. t-1 (same run)
BASELINE_MIN_YEARS = 1
MIN_OBS_FIT = 3                       # OLS needs >= 3 points for a standard error
LOW_CONFIDENCE_N = 5
BETA_BOUNDS = (0.10, 2.00)            # spec 7.2

HA_TO_AC = 2.47105
LB_PER_KG = 2.20462
KG_PER_BU = {"bu60": 27.2155, "bu50": 22.6796, "bu56": 25.4012, "bu48": 21.7724, "bu34": 15.4221}

# [REGION-AVG-RULE] (RPA_Calculator/docs/METHODOLOGY.md) for AB's per-zone history rows.
ACTIVE_ZONES = ("brown", "dark_brown", "black")
SOUTHERN_ZONES = ("brown", "dark_brown")
SOUTHERN_CROPS = frozenset({"durum", "lentils_red"})

# --------------------------------------------------------------------------- #
# Crop registry
#   statcan  : CANSIM 32-10-0359 "Type of crop"
#   budget   : RPA_Calculator crop_key
#   workbook : column label in RPA_Summary_<prov> / Control_Panel (dagger stripped)
#   pdq      : Canada_Crop_Prices crop_symbol (None = not tracked; CPSR deliberately unused)
#   unit     : "bu" ($/bu, bu/ac) or "lb" ($/lb, lb/ac)
# --------------------------------------------------------------------------- #
CROPS: dict[str, dict] = {
    "CWRS":     {"label": "CWRS Wheat", "statcan": "Wheat, Canada Western Red Spring (CWRS)",
                 "budget": "wheat_spring", "workbook": "CWRS Wheat", "pdq": "CWRS", "unit": "bu", "bushel": "bu60"},
    "DURUM":    {"label": "Durum Wheat", "statcan": "Wheat, durum",
                 "budget": "durum", "workbook": "Durum Wheat", "pdq": "DURUM", "unit": "bu", "bushel": "bu60"},
    "CANOLA":   {"label": "Canola", "statcan": "Canola (rapeseed)",
                 "budget": "canola", "workbook": "Canola", "pdq": "CANOLA", "unit": "bu", "bushel": "bu50"},
    "Y_PEAS":   {"label": "Yellow Peas", "statcan": "Peas, dry",
                 "budget": "peas_yellow", "workbook": "Yellow Peas", "pdq": "Y_PEAS", "unit": "bu", "bushel": "bu60"},
    "BARLEY":   {"label": "Barley (feed budget)", "statcan": "Barley",
                 "budget": "barley_feed", "workbook": "Feed Barley", "pdq": None, "unit": "bu", "bushel": "bu48"},
    "OATS":     {"label": "Oats", "statcan": "Oats",
                 "budget": "oats", "workbook": "Oats", "pdq": None, "unit": "bu", "bushel": "bu34"},
    # StatCan publishes one "Lentils" series (red + green), in lb/ac. Priced on the red lentil budget.
    "LENTILS":  {"label": "Lentils (all; red lentil budget)", "statcan": "Lentils",
                 "budget": "lentils_red", "workbook": "Red Lentils", "pdq": None, "unit": "lb", "bushel": None},
    "SOYBEANS": {"label": "Soybeans", "statcan": "Soybeans",
                 "budget": "soybeans", "workbook": "Soybeans", "pdq": None, "unit": "bu", "bushel": "bu60"},
    "FLAX":     {"label": "Flax", "statcan": "Flaxseed",
                 "budget": "flax", "workbook": "Flax", "pdq": None, "unit": "bu", "bushel": "bu56"},
}

# Anchor tiers (spec 3). Tier 0: CWRS on the inverted CWRS/Canola ratio (expected beta > 0).
# Tier 1: vs CWRS. Tier 2: vs the tier-1 sub-anchors Durum and Canola. Chickpeas excluded.
TIERS: dict[tuple[str, str], int] = {
    ("CWRS", "CANOLA"): 0,
    ("CANOLA", "CWRS"): 1,
    ("DURUM", "CWRS"): 1,
    ("Y_PEAS", "CWRS"): 1,
    ("BARLEY", "CWRS"): 1,
    ("OATS", "CWRS"): 1,
    ("LENTILS", "DURUM"): 2,
    ("SOYBEANS", "CANOLA"): 2,
    ("FLAX", "CANOLA"): 2,
}
PAIRS: tuple[tuple[str, str], ...] = tuple(TIERS)

# Regional crop exclusions (province, crop): no elasticity regression; forecast acres are held at
# T-1 and tagged EXCLUDED_REGIONAL_CROP. CORN is not a modelled crop; it is listed so the
# land-pool allocator holds AB / SK corn flat under the same tag.
EXCLUDED_STATUS = "EXCLUDED_REGIONAL_CROP"
REGIONAL_EXCLUSIONS: frozenset[tuple[str, str]] = frozenset({
    ("MB", "DURUM"), ("MB", "LENTILS"),
    ("AB", "SOYBEANS"), ("AB", "FLAX"), ("AB", "CORN"),
    ("SK", "CORN"),
})


def is_excluded(province: str, crop: str) -> bool:
    """True when ``crop`` is a regional exclusion in ``province`` (its pair is never estimated)."""
    return (province, crop) in REGIONAL_EXCLUSIONS


# Workbook exports (output/ root)
ELASTICITY_WORKBOOK = OUTPUT_DIR / "rpa_elasticity_matrices.xlsx"
YIELD_WORKBOOK = OUTPUT_DIR / "yield_trend_diagnostics.xlsx"
VINTAGES = tuple(range(2020, 2026))          # calibration "as of" crop year V uses t <= V
AVERAGE_VINTAGES = 3                         # indicative tab: mean beta over the last 3 vintages
YIELD_DIAGNOSTIC_YEARS = tuple(range(1998, 2027))
ANCHORS = ("CWRS", "DURUM", "CANOLA")

NOT_ESTIMABLE: dict[str, str] = {
    "Red vs Green Lentils (separately)": "StatCan 32-10-0359 publishes one Lentils seeded-area series (no red/green "
                                         "split); estimated as Lentils (all) / Durum on the red lentil budget.",
    "Green Lentils / Durum": "No green-lentil seeded-area series in 32-10-0359 and no green-lentil budget in the "
                             "AB/SK/MB guides.",
}
