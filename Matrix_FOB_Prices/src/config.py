"""Central configuration for Matrix_FOB_Prices.

Every path, crop rule and constant used by the scripts lives here. The scripts live in
Matrix_FOB_Prices/src/; paths resolve from there, so the project folder can move as a unit. The Excel workbook
carries the same crop/zone/route rules on its 'Config' tab; build_template.py writes them from here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SRC_DIR: Final[Path] = Path(__file__).resolve().parent          # Matrix_FOB_Prices/src
PROJECT_DIR: Final[Path] = SRC_DIR.parent                          # Matrix_FOB_Prices
PYTHON_ROOT: Final[Path] = PROJECT_DIR.parent
PDQ_DB: Final[Path] = PYTHON_ROOT / "Canada_Crop_Prices" / "data" / "crop_prices.duckdb"   # opened read-only
COSTING_XLSX: Final[Path] = PYTHON_ROOT / "CAN_Costing_Input.xlsx"                          # read, never written
COSTING_SHEET: Final[str] = "Raw"

WORKBOOK: Final[Path] = PROJECT_DIR / "Matrix_FOB_Calculator.xlsx"
DATA_DROP: Final[Path] = PROJECT_DIR / "data_drop"
PDQ_LATEST_CSV: Final[Path] = DATA_DROP / "pdq_latest.csv"
FX_CSV: Final[Path] = DATA_DROP / "fx.csv"
HISTORY_CSV: Final[Path] = DATA_DROP / "history_chart.csv"
CACHE_DIR: Final[Path] = PROJECT_DIR / "cache"
FX_CACHE: Final[Path] = CACHE_DIR / "fx_history.parquet"
HISTORY_DIR: Final[Path] = DATA_DROP / "history"                 # recorded snapshots - keep, never regenerated
FOB_HISTORY: Final[Path] = HISTORY_DIR / "pdq_fob_history.parquet"
MANUAL_HISTORY: Final[Path] = HISTORY_DIR / "manual_indications_history.parquet"

# --------------------------------------------------------------------------- #
# Market constants
# --------------------------------------------------------------------------- #
BU_PER_MT_WHEAT: Final[float] = 36.74371      # 60 lb bushel (wheat, durum, peas, lentils, soybeans)
BU_PER_MT_CANOLA: Final[float] = 44.0925      # 50 lb bushel
BU_PER_MT_BARLEY: Final[float] = 45.9296      # 48 lb bushel

FX_SERIES: Final[str] = "FXUSDCAD"            # Bank of Canada Valet, CAD per USD
FX_URL: Final[str] = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
FX_DECIMALS: Final[int] = 4
HISTORY_START: Final[str] = "2019-01-01"
HISTORY_HORIZONS: Final[tuple[int, ...]] = (0, 1, 2, 3)   # nearby, +1, +2, +3 months
WINDOW_MONTHS: Final[int] = 6

ZONES: Final[tuple[str, ...]] = ("PEACE", "N ALTA", "S ALTA", "NW SASK", "SW SASK", "NE SASK",
                                 "SE SASK", "W MAN", "E MAN")

# PDQ raw commodity -> PDQ code (matches Canada_Crop_Prices/config.py)
PDQ_COMMODITY_MAP: Final[dict[str, str]] = {
    "1 CWRS 13.5": "CWRS", "1 CPSR 11.5": "CPSR", "1 CWAD 13.0": "DURUM",
    "1 CDA CANOLA": "CANOLA", "2 YELLOW PEAS": "Y_PEAS",
}

# Display crop -> rules. basis: USD_BU (US wheat futures), CAD_MT (ICE canola), NONE (flat price only)
CROPS: Final[list[dict]] = [
    {"crop": "CWRS", "pdq": "CWRS", "cost": "Wheat", "bu": BU_PER_MT_WHEAT, "basis": "USD_BU",
     "futures": "MIAX/MGEX HRS (MW)", "zones": "ALL"},
    {"crop": "CPSR", "pdq": "CPSR", "cost": "Wheat", "bu": BU_PER_MT_WHEAT, "basis": "USD_BU",
     "futures": "KC HRW (KE)", "zones": "ALL"},
    {"crop": "Durum", "pdq": "DURUM", "cost": "Durum", "bu": BU_PER_MT_WHEAT, "basis": "NONE",
     "futures": "none (non-hedgeable)", "zones": "S ALTA;SW SASK"},
    {"crop": "Canola", "pdq": "CANOLA", "cost": "Canola", "bu": BU_PER_MT_CANOLA, "basis": "CAD_MT",
     "futures": "ICE Canola (RS)", "zones": "ALL"},
    {"crop": "Yellow Peas", "pdq": "Y_PEAS", "cost": "Peas", "bu": BU_PER_MT_WHEAT, "basis": "NONE",
     "futures": "none (non-hedgeable)", "zones": "ALL"},
    {"crop": "Red Lentils", "pdq": "", "cost": "Lentils", "bu": BU_PER_MT_WHEAT, "basis": "NONE",
     "futures": "none (manual only)", "zones": "ALL"},
    {"crop": "Feed Barley", "pdq": "", "cost": "Barley", "bu": BU_PER_MT_BARLEY, "basis": "NONE",
     "futures": "none (manual only)", "zones": "ALL"},
    {"crop": "Malt Barley", "pdq": "", "cost": "Barley", "bu": BU_PER_MT_BARLEY, "basis": "NONE",
     "futures": "none (manual only)", "zones": "ALL"},
    {"crop": "Soybeans", "pdq": "", "cost": "Soybeans", "bu": BU_PER_MT_WHEAT, "basis": "NONE",
     "futures": "none (manual only)", "zones": "ALL"},
]
PDQ_TO_CROP: Final[dict[str, str]] = {c["pdq"]: c["crop"] for c in CROPS if c["pdq"]}
CROP_RULES: Final[dict[str, dict]] = {c["crop"]: c for c in CROPS}

# Report route -> row name in CAN_Costing_Input.xlsx / Raw. FOB TBAY uses the TBAY+Laker row's
# components WITHOUT the laker and St. Lawrence transfer legs.
ROUTES: Final[list[dict]] = [
    {"route": "FOB VC", "code": "VC", "raw": "VC"},
    {"route": "FOB TBAY", "code": "TBAY", "raw": "TBAY+Laker"},
    {"route": "FOB STL Direct", "code": "STL", "raw": "STL Direct"},
]
COST_COMPONENTS: Final[tuple[str, ...]] = ("primary", "rail", "port_elev", "margin", "other_fees")

MANUAL_STALE_DAYS: Final[int] = 7
PDQ_STALE_DAYS: Final[int] = 5
