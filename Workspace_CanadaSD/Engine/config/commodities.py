"""
Commodity registry - single source of truth for the CMFT Engine.

Before this module existed, commodity normalization and crop-year start
months were defined separately in ingest_statcan.py, engine_balancer.py and
styles.py, and they disagreed (engine used June for cereals, ingest and
styles used August). Every module now imports from here.

Canonical keys: 'Wheat' means wheat EXCLUDING durum. 'Durum' is durum only.
The StatCan aggregate ('All wheat' / 'Wheat, all') is kept under its own key,
WHEAT_ALL, so it can never be confused with Wheat ex-durum.
"""

from typing import Dict
import pandas as pd

WHEAT_ALL = 'Wheat, all'

# Raw label (StatCan S&D, StatCan planting, StatCan crush, CIMT) -> canonical key.
# Labels not listed pass through unchanged.
COMMODITY_ALIASES: Dict[str, str] = {
    # Wheat ex-durum
    'Wheat, excluding durum': 'Wheat',             # 32100013 S&D
    'Wheat, all excluding durum wheat': 'Wheat',   # 32100359 planting
    'Wheat (Ex-Durum)': 'Wheat',                   # CIMT
    # Durum
    'Durum wheat': 'Durum',                        # 32100013 S&D
    'Wheat, durum': 'Durum',                       # 32100359 planting
    'Amber Durum': 'Durum',                        # CIMT
    # All-wheat aggregate: deliberately NOT 'Wheat' (would double count durum)
    'All wheat': WHEAT_ALL,                        # 32100013 S&D
    'Wheat, all': WHEAT_ALL,                       # 32100359 planting
    # Oilseeds
    'Canola (rapeseed)': 'Canola',                 # 32100352 / 32100359
    # Pulses. Engine key for peas is 'Peas' (tabs Peas_Mo / Peas_Yr).
    'Dry peas': 'Peas',                            # 32100013 S&D
    'Peas, dry': 'Peas',                           # 32100359 planting
    'Chick peas': 'Chickpeas',                     # 32100359 planting
    # NOTE: 'Chickpeas' is its own crop in 32100013; it used to be mapped
    # onto peas, which silently mixed two balance sheets.
}

# Crop-year start month. StatCan 32100013 runs Aug-Jul for all grains;
# Mapping_Dictionary lists Sep-Aug for soybeans.
CROP_YEAR_START_MONTH: Dict[str, int] = {
    'Wheat': 8, 'Durum': 8, 'Oats': 8, 'Barley': 8,
    'Canola': 8, 'Peas': 8, 'Lentils': 8, 'Chickpeas': 8,
    WHEAT_ALL: 8,
    'Soybeans': 9, 'Soymeal': 9,
    'Canola Meal': 8,
    # Vegetable oils are NOT listed here: their marketing years (Canola Oil
    # Sep-Aug, Soy Oil Oct-Sep) are read from CMFT_Main.csv (VEG_OILS below).
}
DEFAULT_START_MONTH = 8

# Month of the StatCan release that carries the final crop-year estimate.
ANCHOR_MONTH = 7

COMMODITY_GROUP: Dict[str, str] = {
    'Wheat': 'Cereal', 'Durum': 'Cereal', 'Oats': 'Cereal', 'Barley': 'Cereal',
    'Canola': 'Oilseed', 'Soybeans': 'Oilseed',
    'Peas': 'Pulse', 'Lentils': 'Pulse', 'Chickpeas': 'Pulse',
}

TIER1_COMMODITIES = [
    'Wheat', 'Durum', 'Canola', 'Barley', 'Oats',
    'Soybeans', 'Peas', 'Lentils',
]

# Tonnes -> bushels
BUSHEL_MULTIPLIERS: Dict[str, float] = {
    'Wheat': 36.7437, 'Durum': 36.7437, 'Soybeans': 36.7437,
    'Peas': 36.7437, 'Lentils': 36.7437,
    'Canola': 44.0925, 'Barley': 45.9296, 'Oats': 64.8418,
}


def normalize_commodity(name) -> str:
    name = str(name).strip()
    return COMMODITY_ALIASES.get(name, name)


def crop_year_start(commodity: str) -> int:
    return CROP_YEAR_START_MONTH.get(commodity, DEFAULT_START_MONTH)


def get_crop_year(date, commodity: str):
    """Crop year labelled by its starting calendar year (2002 -> '2002-03')."""
    if pd.isna(date):
        return pd.NA
    return date.year if date.month >= crop_year_start(commodity) else date.year - 1


def get_group(commodity: str) -> str:
    if commodity not in COMMODITY_GROUP:
        raise KeyError(
            f"Commodity '{commodity}' has no Commodity_Group in config/commodities.py. "
            f"Add it to COMMODITY_GROUP before exporting."
        )
    return COMMODITY_GROUP[commodity]


# ---------------------------------------------------------------------------
# Output conventions for the *_Mo / *_Yr tabs (CanadaSD_Main.xlsx layout)
# ---------------------------------------------------------------------------
# Column A label on *_Mo tabs (StatCan 32100013 series name, except Peas,
# shown as 'Peas' rather than StatCan's 'Dry peas').
DISPLAY_NAME = {
    'Wheat': 'Wheat, excluding durum',
    'Durum': 'Durum wheat',
    'Canola': 'Canola',
    'Barley': 'Barley',
    'Oats': 'Oats',
    'Soybeans': 'Soybeans',
    'Peas': 'Peas',
    'Lentils': 'Lentils',
    'Chickpeas': 'Chickpeas',
}

# Historical scope 2002/03 through 2025/26 (RULE-2.1.B). A crop year that is
# not yet complete (e.g. Soybeans 25-26 before StatCan's Jul 31 release) is
# shown month by month on *_Mo but left blank on every annual total.
SCOPE_FIRST_CROP_YEAR = 2002
SCOPE_LAST_CROP_YEAR = 2025

_MONTH_ABBR = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
               'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']


def month_labels(commodity: str):
    """['AUG', ..., 'JUL'] for Aug-start crops, ['SEP', ..., 'AUG'] for soybeans."""
    s = crop_year_start(commodity)
    return [_MONTH_ABBR[(s - 1 + i) % 12] for i in range(12)]


def crop_year_label(cy: int) -> str:
    """2002 -> '02-03' (the *_Mo tab convention)."""
    return f'{cy % 100:02d}-{(cy + 1) % 100:02d}'


# ---------------------------------------------------------------------------
# Trade (Exports / Imports)
# ---------------------------------------------------------------------------
# 'cimt'   : monthly Exports and Imports come ONLY from CIMT actuals. StatCan
#            32100013 trade lines are ignored. (Current setting.)
# 'statcan': Exports/Imports = StatCan cumulative releases split 5/3/4.
TRADE_SOURCE = 'cimt'

# CIMT file key (text between 'CIMT_' and '_YYYYMMDD') -> engine commodity.
# Keys not listed keep their own name and simply match no balance sheet.
CIMT_FILE_COMMODITY = {
    'Wheat_(Ex-Durum)': 'Wheat',
    'Amber_Durum': 'Durum',
    'Barley': 'Barley',
    'Oats': 'Oats',
    'Canola': 'Canola',
    'Soybeans': 'Soybeans',
    'Canola_Meal': 'Canola Meal',
    'Canola_Oil': 'Canola Oil',
    'Soybean_Meal': 'Soybean Meal',
    'Soybean_Oil': 'Soybean Oil',
    'Corn': 'Corn',
    # Pulses: HS8 workbooks from cimt_build.py. Their 'Raw Data' sheet holds
    # TOTAL trade (all classes: Total Peas / Total Lentils / Chickpeas), so
    # it matches StatCan's all-class balance sheets. The Yellow/Green and
    # Red/Green tabs in those workbooks are trade detail only.
    'Total_Peas': 'Peas',
    'Total_Lentils': 'Lentils',
    'Chickpeas': 'Chickpeas',
}

# HS filter per commodity: keep only rows whose HS code starts with one of
# these prefixes (dots/spaces ignored). Commodities not listed keep every
# line in their file.
#
# Oats = raw grain + oat products (StatCan 'Total exports' = 'Grain exports'
# + 'Product exports'). Matches the Oats config in cimt_build.py:
#   1004.00  oats (legacy code, 1988-2011)
#   1004.10  oats, seed            1004.90  oats, other      (2012 onward)
#   1104.12  rolled / flaked oats  1104.22  hulled / pearled / worked oats
# Not oats: 1104.19 / 1104.29 other cereals, 1104.23 maize, 1104.30 germ.
# Deliberately excluded: 1103.12 (expired Dec 2001) and 1103.19 (catch-all
# for other cereals' groats/meal - no oat-specific volume).
CIMT_HS_PREFIX = {
    'Oats': ('100400', '100410', '100490', '110412', '110422'),
}

# Lines that are processed products (reported as product weight, not grain).
CIMT_PRODUCT_HS = {
    'Oats': ('110412', '110422'),
}

# Product weight -> grain equivalent multiplier applied BY THE ENGINE.
# Keep 1.0: CIMT workbooks written by cimt_build.py are ALREADY in grain
# equivalent (it multiplies 110412 / 110422 by 1.65 via hs6_ge_multipliers
# before writing 'Raw Data'). Setting 1.65 here would double count products.
# Only change this if a CIMT file is ever supplied in product weight. The
# balancer's tie-out prints the implied factor (StatCan Product exports /
# CIMT products); about 1.0 confirms the file is already grain equivalent.
CIMT_GRAIN_EQUIV = {
    '110412': 1.0,
    '110422': 1.0,
}


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------
# 'anchored' (current): monthly stocks land exactly on StatCan's surveyed
#            ending stocks at Dec 31, Mar 31 and Jul 31 (Aug 31 for soybeans'
#            July release). FSR is the balancing item within each period and
#            is spread evenly across its months, so stocks interpolate
#            between the survey dates. Any CIMT-vs-StatCan trade difference
#            shows up in FSR, never in stocks.
# 'rolled':  FSR = StatCan's reported cumulative FSR split 5/3/4; stocks roll
#            forward from the flows (reproduces the hand-built Wheat_Mo, but
#            trade differences accumulate in stocks across crop years).
STOCKS_MODE = 'anchored'


# ---------------------------------------------------------------------------
# Vegetable oils (byproduct balance sheets, modules/vegoil_builder.py)
# ---------------------------------------------------------------------------
# oil key -> sources and where its table is drawn. The marketing-year start
# month is NOT set here: it comes from the 'Vegetable Oil' rows of
# CMFT_Main.csv whose 'Source Commodity' equals cmft_source.
#   crush      : Commodity label in StatCan 32100352 (crush statistics)
#   cimt       : engine commodity of the CIMT oil workbook (crude + refined)
#   host       : the oil table is drawn below this commodity's *_Yr table
#   title      : table headline (column A, 20pt); label: column A on each row
#   mo_tab     : the oil's monthly tab (its *_Yr table looks values up here)
VEG_OILS = {
    'Canola Oil': {'cmft_source': 'Canola (rapeseed)', 'crush': 'Canola (rapeseed)',
                   'cimt': 'Canola Oil', 'host': 'Canola', 'mo_tab': 'CanolaOil_Mo',
                   'title': 'Canola Oil', 'label': 'Canola oil'},
    'Soy Oil':    {'cmft_source': 'Soybeans', 'crush': 'Soybeans',
                   'cimt': 'Soybean Oil', 'host': 'Soybeans', 'mo_tab': 'SoyOil_Mo',
                   'title': 'Soy Oil', 'label': 'Soybean oil'},
}
# Combined annual tab (Canola Oil + Soy Oil by marketing-year label; written
# after the last oil's monthly tab). A1 is two-font rich text: headline part
# at 18pt bold, detail part at 10pt italic.
VEGOIL_GROUP_TAB = 'Can_Vegoils_Yr'
VEGOIL_GROUP_TITLE = ('Canada Vegetable Oils ', '- Canola oil (Sep/Aug) & Soyoil (Oct/Sep)')
VEGOIL_GROUP_LABEL = 'Canada veg oils'

# Protein meals (byproduct balance sheets, modules/meal_builder.py). Same keys
# as VEG_OILS; marketing years come from the 'Protein Meal' rows of
# CMFT_Main.csv. History also starts VEG_OIL_FIRST_CROP_YEAR (32100352 meal
# stocks begin Aug 2015). Each meal table is drawn below its oil table.
PROTEIN_MEALS = {
    'Canola Meal': {'cmft_source': 'Canola (rapeseed)', 'crush': 'Canola (rapeseed)',
                    'cimt': 'Canola Meal', 'host': 'Canola', 'mo_tab': 'CanolaMeal_Mo',
                    'title': 'Canola Meal', 'label': 'Canola meal'},
    'Soymeal':     {'cmft_source': 'Soybeans', 'crush': 'Soybeans',
                    'cimt': 'Soybean Meal', 'host': 'Soybeans', 'mo_tab': 'Soymeal_Mo',
                    'title': 'Soymeal', 'label': 'Soymeal'},
}
MEAL_GROUP_TAB = 'Can_ProteinMeal_Yr'
MEAL_GROUP_TITLE = ('Canada Protein Meals ', '- Canola meal (Sep/Aug) & Soymeal (Oct/Sep)')
MEAL_GROUP_LABEL = 'Canada protein meals'
# 32100352 oil stocks start Aug 2015, so 2015-16 is the first oil year that
# opens from a surveyed stock. Earlier years are left blank.
VEG_OIL_FIRST_CROP_YEAR = 2015
# 25100082 'Vegetable oils, total' / 'Inputs' starts Jan 2020. Months before
# that use the average monthly intake of this calendar year (flatlined back).
FEEDSTOCK_BASELINE_YEAR = 2020


# ---------------------------------------------------------------------------
# Group aggregation tabs (*_Yr level, written after the commodity tabs)
# ---------------------------------------------------------------------------
# tab name -> (A1 / column A label, member commodities). Each group tab sums
# its members' *_Yr tabs with live formulas; members with no tab are skipped
# and reported. Yield is shown in MT/AC (metric), not bushels.
GROUP_TABS = {
    'Can_Cereals_Yr':  ('Canada Cereals',  ['Wheat', 'Durum', 'Barley', 'Oats']),
    'Can_Oilseeds_Yr': ('Canada Oilseeds', ['Canola', 'Soybeans']),
    'Can_Pulses_Yr':   ('Canada Pulses',   ['Peas', 'Lentils']),
}

# Workbook tab order: these summary tabs first, in this order, then every
# commodity tab in build order (<Crop>_Mo, <Crop>_Yr, then its oil / meal *_Mo).
SUMMARY_TAB_ORDER = ['Can_Cereals_Yr', 'Can_Oilseeds_Yr', 'Can_Vegoils_Yr',
                     'Can_ProteinMeal_Yr', 'Can_Pulses_Yr']

# ---------------------------------------------------------------------------
# CanadaSD_Main merge (Engine/merge_main_workbook.py -> Outputs/CanadaSD_Main.xlsx)
# ---------------------------------------------------------------------------
# History: the last MAIN_HISTORY_YEARS crop-year columns of the engine's *_Yr
# tabs (read from CanadaSD_Engine.xlsx, so the window rolls with the scope).
# Forecast: the first MAIN_FORECAST_COUNT crop years (max 3) found in the
# *_Mo_F tabs of CanSD_Input_wb.xlsx, headed with '*'. One delta column per
# forecast year = forecast minus the last history (actual) year.
MAIN_HISTORY_YEARS = 5
MAIN_FORECAST_COUNT = 2
MAIN_TAB_ORDER = ['Analytics_Dashboard', 'Can_Total_Yr', 'Can_Cereals_Yr', 'Can_Oilseeds_Yr',
                  'Can_Vegoils_Yr', 'Can_ProteinMeal_Yr', 'Can_Pulses_Yr']
# Can_Total_Yr = these group tabs (field crops only: byproducts would double
# count the crushed seed)
TOTAL_TAB_MEMBERS = ['Can_Cereals_Yr', 'Can_Oilseeds_Yr', 'Can_Pulses_Yr']
