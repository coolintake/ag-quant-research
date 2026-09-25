"""
Centralized file paths for the CMFT Engine.

Layout (everything resolves from this file's location, so the whole
workspace can be moved or renamed without editing paths):

    C:\\Workspace_CanadaSD\\            <- WORKSPACE_ROOT
        CMFT_Main.xlsx                 (config workbook: Mapping_Dictionary, Commodity_Dictionary)
        CanadaSD_Main.xlsx             (hand-maintained workbook - engine never writes here)
        Data_Inputs\\<table folders>\\  (StatCan CSV + MetaData CSV)
        Outputs\\                       (engine-generated workbook)
        data_store\\                    (CMFT_Master.csv)
        Engine\\config\\paths.py         <- this file

The only absolute path is CIMT_DIR, which is external and read-only
(ENGINE_RULES section 2). Override it with the CIMT_DIR environment
variable if it ever moves.
"""

import os
from pathlib import Path

# ---- Roots -------------------------------------------------------------
ENGINE_ROOT = Path(__file__).resolve().parents[1]      # ...\Engine
WORKSPACE_ROOT = ENGINE_ROOT.parent                    # ...\Workspace_CanadaSD
CONFIG_DIR = ENGINE_ROOT / "config"
MODULES_DIR = ENGINE_ROOT / "modules"

# ---- Inputs ------------------------------------------------------------
# StatCan: one sub-folder per CANSIM table, e.g. Data_Inputs\S&D_32100013-eng\32100013.csv
STATCAN_DIR = WORKSPACE_ROOT / "Data_Inputs"

# CIMT: read in place, never copied (ENGINE_RULES section 2)
CIMT_DIR = Path(os.environ.get("CIMT_DIR", r"C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD"))

# Config workbook (Mapping_Dictionary, Commodity_Dictionary, overrides)
CMFT_DATA_DIR = WORKSPACE_ROOT
CMFT_MAIN_XLSX = WORKSPACE_ROOT / "CMFT_Main.xlsx"

# ---- Outputs -----------------------------------------------------------
DATA_STORE_DIR = WORKSPACE_ROOT / "data_store"
MASTER_FACT_TABLE = DATA_STORE_DIR / "CMFT_Master.csv"

# exporter_excel.py builds a NEW workbook and saves it whole, so it must NOT
# point at the hand-maintained CanadaSD_Main.xlsx (it would wipe every tab,
# violating ENGINE_RULES section 6.1). Engine output goes to its own file.
OUTPUTS_DIR = WORKSPACE_ROOT / "Outputs"
CANADASD_MAIN_XLSX = OUTPUTS_DIR / "CanadaSD_Engine.xlsx"
TARGET_WORKBOOK = CANADASD_MAIN_XLSX          # legacy alias

# The workbook you maintain by hand (read-only as far as the engine is concerned)
CANADASD_MANUAL_XLSX = WORKSPACE_ROOT / "CanadaSD_Main.xlsx"


def verify_paths() -> dict:
    """Report which inputs exist. Run: python -m config.paths (from Engine\\)."""
    inputs = {
        "WORKSPACE_ROOT": WORKSPACE_ROOT,
        "STATCAN_DIR": STATCAN_DIR,
        "CIMT_DIR": CIMT_DIR,
        "CMFT_MAIN_XLSX": CMFT_MAIN_XLSX,
    }
    results = {name: {"path": str(p), "exists": p.exists()} for name, p in inputs.items()}

    # Each StatCan table folder should contain its data CSV
    tables = {
        "Planting_32100359-eng": "32100359.csv",
        "S&D_32100013-eng": "32100013.csv",
        "Crush_32100352-eng": "32100352.csv",
        "Feedstock_25100082-eng": "25100082.csv",
    }
    for folder, csv in tables.items():
        p = STATCAN_DIR / folder / csv
        results[f"CSV {csv}"] = {"path": str(p), "exists": p.exists()}

    results["CIMT files found"] = {
        "path": str(CIMT_DIR),
        "count": len(list(CIMT_DIR.glob("CIMT_*.xlsx"))) if CIMT_DIR.exists() else 0,
    }
    return results


if __name__ == "__main__":
    for name, info in verify_paths().items():
        flag = "OK " if info.get("exists", info.get("count", 0)) else "MISSING"
        print(f"  [{flag}] {name}: {info['path']}" + (f"  ({info['count']} files)" if "count" in info else ""))
