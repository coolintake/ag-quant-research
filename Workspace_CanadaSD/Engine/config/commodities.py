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
    # Pulses
    'Peas, dry': 'Dry peas',                       # 32100359 planting
    'Chick peas': 'Chickpeas',                     # 32100359 planting
    # NOTE: 'Chickpeas' is its own crop in 32100013; it used to be mapped
    # onto 'Dry peas', which silently mixed two balance sheets.
    # NOTE: CIMT 'Yellow Peas' / 'Red Lentils' are sub-classes, not totals,
    # so they are intentionally left unmapped.
}

# Crop-year start month. StatCan 32100013 runs Aug-Jul for all grains;
# Mapping_Dictionary lists Sep-Aug for soybeans.
CROP_YEAR_START_MONTH: Dict[str, int] = {
    'Wheat': 8, 'Durum': 8, 'Oats': 8, 'Barley': 8,
    'Canola': 8, 'Dry peas': 8, 'Lentils': 8, 'Chickpeas': 8,
    WHEAT_ALL: 8,
    'Soybeans': 9, 'Soyoil': 9, 'Soymeal': 9,
    'Canola Oil': 8, 'Canola Meal': 8,
}
DEFAULT_START_MONTH = 8

# Month of the StatCan release that carries the final crop-year estimate.
ANCHOR_MONTH = 7

COMMODITY_GROUP: Dict[str, str] = {
    'Wheat': 'Cereal', 'Durum': 'Cereal', 'Oats': 'Cereal', 'Barley': 'Cereal',
    'Canola': 'Oilseed', 'Soybeans': 'Oilseed',
    'Dry peas': 'Pulse', 'Lentils': 'Pulse', 'Chickpeas': 'Pulse',
}

TIER1_COMMODITIES = [
    'Wheat', 'Durum', 'Canola', 'Barley', 'Oats',
    'Soybeans', 'Dry peas', 'Lentils',
]

# Tonnes -> bushels
BUSHEL_MULTIPLIERS: Dict[str, float] = {
    'Wheat': 36.7437, 'Durum': 36.7437, 'Soybeans': 36.7437,
    'Dry peas': 36.7437, 'Lentils': 36.7437,
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
# Column A label on *_Mo tabs = the StatCan 32100013 series name.
DISPLAY_NAME = {
    'Wheat': 'Wheat, excluding durum',
    'Durum': 'Durum wheat',
    'Canola': 'Canola',
    'Barley': 'Barley',
    'Oats': 'Oats',
    'Soybeans': 'Soybeans',
    'Dry peas': 'Dry peas',
    'Lentils': 'Lentils',
}

# ENGINE_RULES section 3: historical scope 2002/03 through 2024/25.
SCOPE_FIRST_CROP_YEAR = 2002
SCOPE_LAST_CROP_YEAR = 2024

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
    # Sub-classes, NOT the StatCan totals - deliberately not mapped to
    # 'Dry peas' / 'Lentils'. Map them here if you accept the undercount.
    'Yellow_Peas': 'Yellow Peas',
    'Red_Lentils': 'Red Lentils',
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
# Group aggregation tabs (*_Yr level, written after the commodity tabs)
# ---------------------------------------------------------------------------
# tab name -> (A1 / column A label, member commodities). Each group tab sums
# its members' *_Yr tabs with live formulas; members with no tab are skipped
# and reported. Yield is shown in MT/AC (metric), not bushels.
GROUP_TABS = {
    'Can_Cereals_Yr':  ('Canada Cereals',  ['Wheat', 'Durum', 'Barley', 'Oats']),
    'Can_Oilseeds_Yr': ('Canada Oilseeds', ['Canola', 'Soybeans']),
    'Can_Pulses_Yr':   ('Canada Pulses',   ['Dry peas', 'Lentils']),
}
