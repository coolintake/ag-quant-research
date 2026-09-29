"""
CMFT Engine - single entry point.

    cd C:\\Users\\ahmed\\OneDrive\\Desktop\\Python\\Workspace_CanadaSD\\Engine
    python run_pipeline.py                   # check -> balance -> export
    python run_pipeline.py --check           # verify inputs + validate CMFT_Main.csv
    python run_pipeline.py --balance         # only rebuild data_store\\CMFT_Master.csv
    python run_pipeline.py --export          # only rebuild Outputs\\CanadaSD_Engine.xlsx
"""

import argparse
import sys
from pathlib import Path

# Make 'config' and 'modules' importable no matter where this is launched from
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.paths import verify_paths  # noqa: E402


def check_inputs() -> bool:
    print("Checking inputs...")
    ok = True
    for name, info in verify_paths().items():
        present = info.get("exists", info.get("count", 0))
        ok &= bool(present)
        extra = f"  ({info['count']} files)" if "count" in info else ""
        print(f"  [{'OK ' if present else 'MISSING'}] {name}: {info['path']}{extra}")
    return ok


def check_mapping() -> bool:
    from config.mappings import (
        validate_mapping, mapping_fingerprint, MappingValidationError, MAPPING_CSV,
    )
    print("\nValidating mapping CSV...")
    try:
        warnings = validate_mapping()
    except FileNotFoundError as e:
        print(f"  [MISSING] {e}")
        return False
    except MappingValidationError as e:
        print(f"  [FAIL] {e}")
        return False
    for w in warnings:
        print(f"  [WARN] {w}")
    print(f"  [OK ] {MAPPING_CSV.name}: duplicates, core lines, StatCan coverage and crop years "
          f"all pass (fingerprint {mapping_fingerprint()})")
    return True


def check() -> bool:
    inputs_ok = check_inputs()
    mapping_ok = check_mapping()
    return inputs_ok and mapping_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--export", action="store_true")
    a = ap.parse_args()

    run_all = not (a.check or a.balance or a.export)

    if (a.check or run_all) and not check():
        sys.exit("\nFix the MISSING / FAIL items above before running.")

    if a.balance or run_all:
        from engine_balancer import run_balancer
        run_balancer()

    if a.export or run_all:
        from exporter_excel import run_exporter
        run_exporter()


if __name__ == "__main__":
    main()
