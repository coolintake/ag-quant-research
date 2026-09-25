"""
Centralized system file paths for the CMFT Engine.
All absolute paths are defined here to ensure consistency across modules.
"""

from pathlib import Path

# StatCan raw inputs directory
STATCAN_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\StatCan_Inputs")

# CIMTD project directory
CIMT_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CIMTD")

# CMFT data directory
CMFT_DATA_DIR = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\CMFT")

# CMFT Main Excel workbook (contains Dictionary_Mapping sheet)
CMFT_MAIN_XLSX = CMFT_DATA_DIR / "CMFT_Main.xlsx"

# Target workbook for output
TARGET_WORKBOOK = Path(r"C:\Users\ahmed\OneDrive\Desktop\Ahmed's Folder\OneKBCapital\Data\olderfiles\CanadaSD_v01.xlsx")

# Engine root directory (UPDATED to CanSD_Analyzer)
ENGINE_ROOT = Path(r"C:\Users\ahmed\OneDrive\Desktop\Python\CanSD_Analyzer\CMFT_Engine")

# Master Fact Table output (CSV)
MASTER_FACT_TABLE = ENGINE_ROOT / "data_store" / "CMFT_Master.csv"

# Config directory
CONFIG_DIR = ENGINE_ROOT / "config"

# Modules directory
MODULES_DIR = ENGINE_ROOT / "modules"

# Data store directory
DATA_STORE_DIR = ENGINE_ROOT / "data_store"


def verify_paths() -> dict:
    """
    Verify that critical input paths exist.
    Returns a dictionary with path names and their existence status.
    """
    critical_paths = {
        "STATCAN_DIR": STATCAN_DIR,
        "CIMT_DIR": CIMT_DIR,
        "CMFT_DATA_DIR": CMFT_DATA_DIR,
        "CMFT_MAIN_XLSX": CMFT_MAIN_XLSX,
        "TARGET_WORKBOOK": TARGET_WORKBOOK,
    }
    
    results = {}
    for name, path in critical_paths.items():
        results[name] = {
            "path": str(path),
            "exists": path.exists(),
            "is_file": path.is_file() if path.exists() else False,
            "is_dir": path.is_dir() if path.exists() else False,
        }
    
    # Output directories (should exist after mkdir)
    output_dirs = {
        "ENGINE_ROOT": ENGINE_ROOT,
        "CONFIG_DIR": CONFIG_DIR,
        "MODULES_DIR": MODULES_DIR,
        "DATA_STORE_DIR": DATA_STORE_DIR,
    }
    
    for name, path in output_dirs.items():
        results[name] = {
            "path": str(path),
            "exists": path.exists(),
            "is_dir": path.is_dir() if path.exists() else False,
        }
    
    return results


if __name__ == "__main__":
    import json
    results = verify_paths()
    print(json.dumps(results, indent=2))