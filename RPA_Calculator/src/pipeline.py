"""End-to-end run: extract -> normalize -> workbook (+ engine samples), for every registered province.

Stage modules are looked up by each province's source type (docs/AGENT_RULES.json
provinces.<code>.source.type), so adding a province means registering its extractor and
normalizer here and in src/normalize/baselines.py BUILDERS; nothing else branches on province.

    python -m src.pipeline                          # all stages, all provinces
                                                    # (extract, normalize, weights, workbook, engine, analytics)
    python -m src.pipeline --stage normalize        # re-normalize from cached raw tables
    python -m src.pipeline --province AB --stage extract
    python -m src.pipeline --verify                 # + Excel recalculation checks (Windows)
"""

from __future__ import annotations

import argparse
import importlib

from .config import PROVINCES, province

STAGES = {
    "sk_crop_planning_guide": {"extract": "src.sources.sk_extractor", "normalize": "src.normalize.sk_mvs"},
    "ab_agriprofit_cropping_alternatives": {"extract": "src.sources.ab_extractor", "normalize": "src.normalize.ab_cop"},
    "mb_cop_workbook": {"extract": "src.sources.mb_extractor", "normalize": "src.normalize.mb_cop"},
}
ORDER = ("extract", "normalize", "weights", "workbook", "engine", "analytics")


def run(stages: list[str], provinces: list[str], verify: bool = False) -> int:
    for stage in ("extract", "normalize"):
        if stage not in stages:
            continue
        for code in provinces:
            source_type = province(code).source["type"]
            module = STAGES.get(source_type, {}).get(stage)
            if module is None:
                raise NotImplementedError(f"{code}: no {stage} module registered for {source_type!r}")
            print(f"\n=== {stage}: {code} ({module})")
            if importlib.import_module(module).main([]) != 0:
                return 1
    if "weights" in stages:   # [PRAIRIE-WEIGHTED-RPA] StatCan 32-10-0359 acreage weights -> AGENT_RULES.json
        from .pricing.statcan_weights import main as weights_main  # noqa: PLC0415
        print("\n=== weights (StatCan 32-10-0359)")
        if weights_main([]) != 0:
            return 1
    if "workbook" in stages:
        from .outputs.excel import main as workbook_main  # noqa: PLC0415
        print("\n=== workbook")
        if workbook_main(["--verify"] if verify else []) != 0:
            return 1
    if "engine" in stages:
        from .engine.market import main as engine_main  # noqa: PLC0415
        for code in provinces:
            print(f"\n=== engine sample: {code}")
            engine_main(["--province", code])
    if "analytics" in stages:
        from .engine.prairie_engine import main as prairie_main  # noqa: PLC0415
        from .outputs.analytics_json import main as analytics_main  # noqa: PLC0415
        print("\n=== Prairie benchmark + analytics JSON")
        prairie_main([])
        if analytics_main([]) != 0:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=ORDER, nargs="*", help="default: all")
    ap.add_argument("--province", nargs="*", help=f"default: all ({', '.join(PROVINCES)})")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    return run(list(args.stage or ORDER), [province(p).code for p in (args.province or PROVINCES)], args.verify)


if __name__ == "__main__":
    raise SystemExit(main())
