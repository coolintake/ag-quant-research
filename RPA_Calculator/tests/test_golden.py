"""Financial outputs match the golden snapshot.

tests/golden/rpa_2026.json holds the master workbook's recalculated values (Excel) at Last Yr
inputs and at the sample inputs. The engine computes exactly what the workbook shows, so it must
reproduce those C, D and C - D values. Every intended change to them is a methodology decision
logged under the snapshot's revisions (P0: none; [REGION-AVG-RULE]: SK re-baselined).
"""

import json
import unittest
from pathlib import Path

from src.config import crop, province
from src.engine.market import Scenario, last_yr, run_scenario
from src.normalize.baselines import province_baselines
from src.pricing import quotes_from_per_lb

GOLDEN = json.loads((Path(__file__).parent / "golden" / "rpa_2026.json").read_text(encoding="utf-8"))
FIELDS = ("revenue", "cost", "rpa")


def _scenario(code: str) -> dict:
    inp = GOLDEN["sample_inputs"][code]
    quotes = (quotes_from_per_lb(inp["nutrient_per_lb"], province(code)) if "nutrient_per_lb" in inp
              else inp["quotes"])
    return {"name": "golden", "province": code, "nutrient_quotes": quotes,
            "crop_prices": {k: {"farm_price": p, "unit": crop(k).price_basis}
                            for k, p in inp["bids"].items()}}


class GoldenTest(unittest.TestCase):
    def test_last_yr_matches_golden_workbook(self):
        for code in GOLDEN["workbook"]["last_yr"]:
            got = {b.crop_key: last_yr(b) for b in province_baselines(code, 2026)}
            want = GOLDEN["workbook"]["last_yr"][code]
            self.assertEqual(set(got), set(want), code)
            for key, row in want.items():
                for f in FIELDS:
                    self.assertAlmostEqual(got[key][f], row[f], places=9, msg=f"{code} {key} {f}")

    def test_sample_inputs_match_golden_workbook(self):
        for code in GOLDEN["workbook"]["sample"]:
            res = {r["crop_key"]: r["scenario"] for r in run_scenario(Scenario.from_dict(_scenario(code), 2026))["results"]}
            for key, row in GOLDEN["workbook"]["sample"][code].items():
                for f in FIELDS:
                    self.assertAlmostEqual(res[key][f], row[f], places=9, msg=f"{code} {key} {f}")

    def test_null_scenario_is_last_yr(self):
        for code in GOLDEN["workbook"]["last_yr"]:
            for r in run_scenario(Scenario.from_dict({"name": "null", "province": code}, 2026))["results"]:
                for f in FIELDS:
                    self.assertAlmostEqual(r["scenario"][f], r["last_yr"][f], places=12)

    def test_engine_yield_convention_change_is_rounding_only(self):
        """Pre-refactor engine used exact guide yields; the gap is revenue on (whole - exact) yield.
        Only crops whose soil zones are unchanged since then (AB, SK CWRS / CPS) are comparable."""
        rezoned = {k for r in GOLDEN["revisions"] if r["rule_id"] == "REGION-AVG-RULE"
                   for k in r["changed_crops"] if k not in ("wheat_spring", "wheat_cps")}
        for code in ("SK", "AB"):
            base = {b.crop_key: b for b in province_baselines(code, 2026)}
            for key, row in GOLDEN["engine_pre_refactor"][code].items():
                if code == "SK" and key in rezoned:
                    continue
                b = base[key]
                ly = last_yr(b)
                yield_gap = (b.yield_whole - b.yield_exact) * b.price / (100 if b.pulse else 1)
                old_rpa = row["baseline"]["return_over_direct"]
                self.assertLess(abs((ly["rpa"] - old_rpa) - yield_gap), 0.25, f"{code} {key}")


class RegionAverageRevisionTest(unittest.TestCase):
    """[REGION-AVG-RULE]: AB untouched; SK moved only through the zone change (and, for CWRS / CPS,
    the Last Yr quotes implied from the re-averaged guide, a fraction of a cent)."""

    def test_sk_revision_logged(self):
        rev = next(r for r in GOLDEN["revisions"] if r["rule_id"] == "REGION-AVG-RULE")
        self.assertEqual(rev["province"], "SK")
        prev = rev["previous_workbook"]["last_yr"]["SK"]
        for key in ("wheat_spring", "wheat_cps"):
            self.assertLess(abs(prev[key]["rpa"] - GOLDEN["workbook"]["last_yr"]["SK"][key]["rpa"]), 0.01, key)
        self.assertGreater(abs(prev["oats"]["rpa"] - GOLDEN["workbook"]["last_yr"]["SK"]["oats"]["rpa"]), 1)


if __name__ == "__main__":
    unittest.main()
