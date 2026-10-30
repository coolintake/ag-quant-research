"""Hammersmith world FOB + ocean freight rules (hammersmithltd.blogspot.com, weekly report).

All Hammersmith-specific settings live here, beside the scripts that use them (Hammersmith/ folder).
Shared project paths and bushel factors come from src/config.py, so there is one source of truth for each.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

HS_DIR: Final[Path] = Path(__file__).resolve().parent                # Matrix_FOB_Prices/Hammersmith
sys.path.insert(0, str(HS_DIR.parent / "src"))
import config as fob  # noqa: E402  (src/config.py)

PROJECT_DIR = fob.PROJECT_DIR
DATA_DROP, CACHE_DIR, HISTORY_DIR = fob.DATA_DROP, fob.CACHE_DIR, fob.HISTORY_DIR
BU_PER_MT_WHEAT, BU_PER_MT_BARLEY = fob.BU_PER_MT_WHEAT, fob.BU_PER_MT_BARLEY

HAMMERSMITH_FEED: Final[str] = "https://hammersmithltd.blogspot.com/feeds/posts/default"
HAMMERSMITH_UA: Final[str] = "Matrix_FOB_Prices/1.0 (internal research; weekly fetch)"
HAMMERSMITH_START: Final[str] = "2019-01-01"
HAMMERSMITH_CACHE: Final[Path] = CACHE_DIR / "hammersmith"                 # raw posts (re-downloadable)
WORLD_FOB_CSV: Final[Path] = DATA_DROP / "world_fob_latest.csv"
WORLD_FREIGHT_CSV: Final[Path] = DATA_DROP / "world_freight_latest.csv"
WORLD_FOB_HISTORY: Final[Path] = HISTORY_DIR / "world_fob_history.parquet"
WORLD_FREIGHT_HISTORY: Final[Path] = HISTORY_DIR / "world_freight_history.parquet"

# Table location: Word cell shading. Yellow = cash commodity FOBs, pink = ocean freight.
HS_YELLOW: Final[tuple[str, ...]] = ("ffff99", "ffffcc")
HS_PINK: Final[tuple[str, ...]] = ("fed6f5",)

# Review flags only - outliers stay in the data and in CIF (never excluded).
HS_PRICE_RANGE: Final[tuple[float, float]] = (50.0, 2000.0)     # USD/MT, commodity FOB
HS_FREIGHT_RANGE: Final[tuple[float, float]] = (10.0, 200.0)    # USD/MT, ocean freight ($6 Gulf-China = typo)
HS_WEEKLY_MOVE: Final[float] = 0.15                             # |w/w change| above this -> OUTLIER

BU_PER_MT_CORN: Final[float] = 39.3679        # 56 lb bushel (corn, sorghum)

# Price rows (yellow box). First regex match on the normalised label wins (lower case, single spaces).
# peer = calculator crops this series competes with (";"-list); bu = bushels per MT (None: meals/feeds).
WORLD_SERIES: Final[list[dict]] = [
    {"id": "WHT_US_SRW", "re": r"^wheat, usa soft red winter", "commodity": "Wheat", "origin": "USA",
     "port": "US Gulf", "peer": "", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_US_HRW", "re": r"^wheat, usa hard red winter", "commodity": "Wheat", "origin": "USA",
     "port": "US Gulf", "peer": "CPSR;CWRS", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_UA_MILL", "re": r"^wheat, ukraine", "commodity": "Wheat", "origin": "Ukraine",
     "port": "Black Sea", "peer": "CPSR", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_RU_MILL", "re": r"^wheat, russia", "commodity": "Wheat", "origin": "Russia",
     "port": "Black Sea", "peer": "CWRS;CPSR", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_RO_MILL", "re": r"^wheat, romania", "commodity": "Wheat", "origin": "Romania",
     "port": "Black Sea", "peer": "CWRS;CPSR", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_FR_SUP", "re": r"^wheat, superior, france", "commodity": "Wheat", "origin": "France",
     "port": "Rouen", "peer": "CWRS;CPSR", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_FR_MED", "re": r"^wheat, medium, france", "commodity": "Wheat", "origin": "France",
     "port": "Rouen", "peer": "", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_AR_MILL", "re": r"^wheat, milling.*argentina", "commodity": "Wheat", "origin": "Argentina",
     "port": "Upriver", "peer": "CPSR", "bu": BU_PER_MT_WHEAT},
    {"id": "WHT_BS_FEED", "re": r"^wheat, feed, black sea", "commodity": "Wheat", "origin": "Black Sea",
     "port": "Black Sea", "peer": "", "bu": BU_PER_MT_WHEAT},
    {"id": "BAR_FR", "re": r"^barley, france", "commodity": "Barley", "origin": "France",
     "port": "Rouen", "peer": "Feed Barley", "bu": BU_PER_MT_BARLEY},
    {"id": "BAR_AR_FEED", "re": r"^barley, feed, argentina", "commodity": "Barley", "origin": "Argentina",
     "port": "Upriver", "peer": "Feed Barley", "bu": BU_PER_MT_BARLEY},
    {"id": "BAR_BS_FEED", "re": r"^barley, feed, black sea", "commodity": "Barley", "origin": "Black Sea",
     "port": "Black Sea", "peer": "Feed Barley", "bu": BU_PER_MT_BARLEY},
    {"id": "BAR_AU_FEED", "re": r"^barley, feed, australia", "commodity": "Barley", "origin": "Australia",
     "port": "", "peer": "Feed Barley", "bu": BU_PER_MT_BARLEY},
    {"id": "BAR_US_PNW_FEED", "re": r"^barley, feed, usa pacific", "commodity": "Barley", "origin": "USA",
     "port": "US PNW", "peer": "Feed Barley", "bu": BU_PER_MT_BARLEY},
    {"id": "CRN_US_NOLA", "re": r"^corn, .*nola", "commodity": "Corn", "origin": "USA",
     "port": "NOLA", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_US_PNW", "re": r"^corn, .*pacific", "commodity": "Corn", "origin": "USA",
     "port": "US PNW", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_AR", "re": r"^corn, .*argentina", "commodity": "Corn", "origin": "Argentina",
     "port": "Upriver", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_BR", "re": r"^corn, .*brazil", "commodity": "Corn", "origin": "Brazil",
     "port": "", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_FR", "re": r"^corn, .*france", "commodity": "Corn", "origin": "France",
     "port": "", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_RO", "re": r"^corn, .*romania", "commodity": "Corn", "origin": "Romania",
     "port": "Black Sea", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_RU", "re": r"^corn, .*russia", "commodity": "Corn", "origin": "Russia",
     "port": "Black Sea", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "CRN_UA", "re": r"^corn, .*ukraine", "commodity": "Corn", "origin": "Ukraine",
     "port": "Black Sea", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "SOR_AR", "re": r"^sorghum, .*argentina", "commodity": "Sorghum", "origin": "Argentina",
     "port": "", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "SOR_US_TX", "re": r"^sorghum, .*(texas|gulf)", "commodity": "Sorghum", "origin": "USA",
     "port": "Texas", "peer": "", "bu": BU_PER_MT_CORN},
    {"id": "SBM_US_RTM", "re": r"^soymeal, .*usa, rotterdam", "commodity": "Soymeal", "origin": "USA",
     "port": "Rotterdam (delivered)", "peer": "", "bu": None},
    {"id": "SBM_BR_RTM", "re": r"^soymeal, .*brazil, rotterdam", "commodity": "Soymeal", "origin": "Brazil",
     "port": "Rotterdam (delivered)", "peer": "", "bu": None},
    {"id": "SBM_AR_RTM", "re": r"^soymeal, .*argentina, rotterdam", "commodity": "Soymeal",
     "origin": "Argentina", "port": "Rotterdam (delivered)", "peer": "", "bu": None},
    {"id": "SBM_US_NOLA", "re": r"^soymeal, .*nola", "commodity": "Soymeal", "origin": "USA",
     "port": "NOLA", "peer": "", "bu": None},
    {"id": "SBM_AR", "re": r"^soymeal, .*argentina", "commodity": "Soymeal", "origin": "Argentina",
     "port": "", "peer": "", "bu": None},
    {"id": "SBM_BR", "re": r"^soymeal, .*brazil", "commodity": "Soymeal", "origin": "Brazil",
     "port": "", "peer": "", "bu": None},
    {"id": "SOY_US_NOLA", "re": r"^soybeans, .*nola", "commodity": "Soybeans", "origin": "USA",
     "port": "NOLA", "peer": "Soybeans", "bu": BU_PER_MT_WHEAT},
    {"id": "SOY_AR", "re": r"^soybeans, argentina", "commodity": "Soybeans", "origin": "Argentina",
     "port": "", "peer": "Soybeans", "bu": BU_PER_MT_WHEAT},
    {"id": "SOY_BR", "re": r"^soybeans, brazil", "commodity": "Soybeans", "origin": "Brazil",
     "port": "", "peer": "Soybeans", "bu": BU_PER_MT_WHEAT},
    {"id": "SOY_BS", "re": r"^soybeans, black sea", "commodity": "Soybeans", "origin": "Black Sea",
     "port": "Black Sea", "peer": "Soybeans", "bu": BU_PER_MT_WHEAT},
    {"id": "SOY_RTM", "re": r"^soybeans, rotterdam", "commodity": "Soybeans", "origin": "Various",
     "port": "Rotterdam (delivered)", "peer": "", "bu": BU_PER_MT_WHEAT},
    {"id": "CGM_US", "re": r"^corn gluten meal", "commodity": "Corn Gluten Meal", "origin": "USA",
     "port": "NOLA", "peer": "", "bu": None},
    {"id": "CGF_US", "re": r"^corn gluten feed", "commodity": "Corn Gluten Feed", "origin": "USA",
     "port": "NOLA", "peer": "", "bu": None},
    {"id": "DDGS_US", "re": r"^ddgs", "commodity": "DDGS", "origin": "USA",
     "port": "NOLA", "peer": "", "bu": None},
]

# Freight rows (pink box). Origins are matched longest-first at the start of the label and kept whole
# ("France/Germany" stays one origin). Black Sea rows name the country in the rate cell.
FREIGHT_ORIGINS: Final[dict[str, str]] = {   # label prefix (lower case) -> origin
    "us lakes, duluth": "US Lakes (Duluth)", "us lakes": "US Lakes (Duluth)", "us east coast": "US East Coast", "us gulf": "US Gulf",
    "us pnw": "US PNW", "france/germany": "France/Germany", "argentina": "Argentina", "brazil": "Brazil",
    "australia": "Australia", "south africa": "South Africa", "black sea": "Black Sea", "russia": "Russia",
    "ukraine": "Ukraine", "romania": "Romania", "canada": "Canada",
}
# Rows whose label matches are paper indices, not physical freight: dropped entirely.
FREIGHT_INDEX_RE: Final[str] = r"baltic|freightos|container index|\bindex\b"
# Multi-destination strings: split on "/" into one row per destination; the raw string is kept.
# Aliases give one spelling per destination; anything else is kept as published (title case).
DESTINATION_ALIASES: Final[dict[str, str]] = {
    "saudi": "Saudi Arabia", "east spain": "Spain (East)", "s. africa": "South Africa",
    "s africa": "South Africa", "other med": "Other Med",
}
VESSEL_CLASSES: Final[dict[str, str]] = {    # label token -> class (only when the label names one)
    "handisize": "Handysize", "handysize": "Handysize", "handi": "Handysize", "handy": "Handysize",
    "supramax": "Supramax", "supermax": "Supramax", "panamax": "Panamax", "capesize": "Capesize",
    "coaster": "Coaster",
}

# --------------------------------------------------------------------------- #
# Workbook view + CIF proxy rules (guardrails; documented in README_FOB_Logic.md section 8)
# --------------------------------------------------------------------------- #
# series id -> (column header on World_FOB_Matrix / Global_CIF_Matrix, freight origin key "origin|country").
# The freight key picks the ocean freight row used for the landed (CIF) cost; "" = no CIF (already delivered).
# Black Sea feed grains and soybeans use Russian freight (the largest Black Sea shipper).
SERIES_META: Final[dict[str, tuple[str, str]]] = {
    "WHT_US_SRW": ("US SRW Gulf", "US Gulf|"), "WHT_US_HRW": ("US HRW Gulf", "US Gulf|"),
    "WHT_UA_MILL": ("Ukraine 11.5", "Black Sea|Ukraine"), "WHT_RU_MILL": ("Russia 12.5", "Black Sea|Russia"),
    "WHT_RO_MILL": ("Romania 12.5", "Black Sea|Romania"), "WHT_FR_SUP": ("France Sup. Rouen", "France/Germany|"),
    "WHT_FR_MED": ("France Med. Rouen", "France/Germany|"), "WHT_AR_MILL": ("Argentina 12.0", "Argentina|"),
    "WHT_BS_FEED": ("Black Sea feed", "Black Sea|Russia"),
    "BAR_FR": ("France Rouen", "France/Germany|"), "BAR_AR_FEED": ("Argentina feed", "Argentina|"),
    "BAR_BS_FEED": ("Black Sea feed", "Black Sea|Russia"), "BAR_AU_FEED": ("Australia feed", "Australia|"),
    "BAR_US_PNW_FEED": ("US PNW feed", "US PNW|"),
    "CRN_US_NOLA": ("US NOLA 2YC", "US Gulf|"), "CRN_US_PNW": ("US PNW 3YC", "US PNW|"),
    "CRN_AR": ("Argentina", "Argentina|"), "CRN_BR": ("Brazil", "Brazil|"), "CRN_FR": ("France", "France/Germany|"),
    "CRN_RO": ("Romania", "Black Sea|Romania"), "CRN_RU": ("Russia", "Black Sea|Russia"),
    "CRN_UA": ("Ukraine", "Black Sea|Ukraine"),
    "SOR_AR": ("Argentina", "Argentina|"), "SOR_US_TX": ("US Texas", "US Gulf|"),
    "SBM_US_NOLA": ("US NOLA 48", "US Gulf|"), "SBM_US_RTM": ("US del. Rotterdam", ""),
    "SBM_BR_RTM": ("Brazil del. Rotterdam", ""), "SBM_AR_RTM": ("Argentina del. Rotterdam", ""),
    "SBM_AR": ("Argentina 47", "Argentina|"), "SBM_BR": ("Brazil 48", "Brazil|"),
    "SOY_US_NOLA": ("US NOLA", "US Gulf|"), "SOY_AR": ("Argentina", "Argentina|"), "SOY_BR": ("Brazil", "Brazil|"),
    "SOY_BS": ("Black Sea", "Black Sea|Russia"), "SOY_RTM": ("del. Rotterdam", ""),
    "CGM_US": ("US NOLA", "US Gulf|"), "CGF_US": ("US NOLA", "US Gulf|"), "DDGS_US": ("US NOLA", "US Gulf|"),
}
# Canadian route -> freight proxy: Vancouver ~ US PNW (same Pacific corridor); Thunder Bay ~ Duluth salties
# (same Great Lakes / Seaway leg). Hammersmith has no St. Lawrence route: STL Direct CIF stays N/A.
CANADA_FREIGHT_PROXY: Final[dict[str, str]] = {"FOB VC": "US PNW|", "FOB TBAY": "US Lakes (Duluth)|",
                                               "FOB STL Direct": ""}
# Commodities shown on the world tabs (the ones with a Canadian peer crop).
WORLD_COMMODITIES: Final[tuple[str, ...]] = ("Wheat", "Barley", "Soybeans")
# One freight row per origin key x destination feeds CIF: preferred vessel class, then the lower rate.
VESSEL_PREFERENCE: Final[tuple[str, ...]] = ("Panamax", "Supramax", "", "Handysize", "Coaster", "Capesize")
CIF_DESTINATIONS: Final[tuple[str, ...]] = (
    "China", "Japan", "Indonesia", "Philippines", "Bangladesh", "Egypt", "Morocco", "Algeria", "Tunisia", "Saudi Arabia",
    "Jordan", "Turkey", "Europe", "Spain", "South Africa", "Nigeria")

# CSV drop columns (parser columns + these). Read by Power Query; order is the contract with wire_workbook.py.
PRICE_EXTRA: Final[list[str]] = ["display_name", "freight_origin_key", "review_note", "fetched_at"]
FREIGHT_EXTRA: Final[list[str]] = ["origin_key", "cif_pref", "review_note", "fetched_at"]
WORLD_STALE_DAYS: Final[int] = 9             # weekly report + 2 days slack
# Freight origin keys ("origin|country") shown as rows on Freight_Rates, with their display labels.
FREIGHT_ORIGIN_KEYS: Final[list[tuple[str, str]]] = [
    ("US Gulf|", "US Gulf"), ("US PNW|", "US PNW"), ("US Lakes (Duluth)|", "US Lakes (Duluth)"),
    ("US East Coast|", "US East Coast"), ("Black Sea|Russia", "Black Sea · Russia"),
    ("Black Sea|Ukraine", "Black Sea · Ukraine"), ("Black Sea|Romania", "Black Sea · Romania"),
    ("Russia|", "Russia (coaster)"), ("Ukraine|", "Ukraine (coaster)"), ("France/Germany|", "France/Germany"),
    ("Argentina|", "Argentina"), ("Brazil|", "Brazil"), ("Australia|", "Australia"), ("South Africa|", "South Africa"),
]
FREIGHT_VESSEL_FILTER: Final[tuple[str, ...]] = ("All", "Panamax", "Supramax", "Handysize", "Coaster", "Size only")
