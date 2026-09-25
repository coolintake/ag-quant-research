"""
CMFT Engine - single entry point.

    cd C:\\Workspace_CanadaSD\\Engine
    python run_pipeline.py              # check paths -> balance -> export
    python run_pipeline.py --check      # only verify inputs exist
    python run_pipeline.py --balance    # only rebuild data_store\\CMFT_Master.csv
    python run_pipeline.py --export     # only rebuild Outputs\\CanadaSD_Engine.xlsx
"""

import argparse
import sys
from pathlib import Path

# Make 'config' and 'modules' importable no matter where this is launched from
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.paths import verify_paths  # noqa: E402


def check() -> bool:
    print("Checking inputs...")
    ok = True
    for name, info in verify_paths().items():
        present = info.get("exists", info.get("count", 0))
        ok &= bool(present)
        extra = f"  ({info['count']} files)" if "count" in info else ""
        print(f"  [{'OK ' if present else 'MISSING'}] {name}: {info['path']}{extra}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--export", action="store_true")
    a = ap.parse_args()
    run_all = not (a.check or a.balance or a.export)

    if (a.check or run_all) and not check():
        sys.exit("Fix the MISSING inputs above before running.")

    if a.balance or run_all:
        from engine_balancer import run_balancer
        run_balancer()

    if a.export or run_all:
        from exporter_excel import run_exporter
        run_exporter()


if __name__ == "__main__":
    main()
