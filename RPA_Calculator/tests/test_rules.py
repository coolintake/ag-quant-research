"""docs/AGENT_RULES.json: loads, validates, and refuses inconsistent rules."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.config import PROVINCES, RULES, RULES_PATH, RulesError, crop, load_rules, province
from src.pricing.templates import check_benchmark_template


def _load_mutated(mutate) -> None:
    raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    mutate(raw)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "rules.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        load_rules(path)


class RulesTest(unittest.TestCase):
    def test_rules_load(self):
        self.assertEqual(set(PROVINCES), {"SK", "AB", "MB"})
        self.assertEqual(crop("wheat_spring").trade_label, "CWRS Wheat")
        self.assertEqual(province("AB").active_regions, ("brown", "dark_brown", "black"))

    def test_active_regions_exclude_peace_grey_irrigated(self):
        ab = province("AB")
        for excluded in ("peace", "grey_wooded", "irrigated"):
            self.assertIn(excluded, ab.regions)
            self.assertNotIn(excluded, ab.active_regions)
        for key in ("durum", "lentils_red"):
            self.assertEqual(ab.crop(key).regions, ("brown", "dark_brown"))
        for key in ("wheat_spring", "canola", "barley_feed", "oats", "peas_yellow"):
            self.assertEqual(ab.crop(key).regions, ("brown", "dark_brown", "black"))

    def test_sk_cps_inherits_cwrs(self):
        cps = province("SK").crop("wheat_cps")
        self.assertEqual((cps.inherits, cps.start_year), ("wheat_spring", 2026))

    def test_trade_labels(self):
        want = {"CWRS Wheat", "CPS Wheat", "Durum Wheat", "Feed Barley", "Malt Barley", "Oats", "Yellow Peas",
                "Red Lentils", "Canola", "Soybeans", "Corn", "Flax", "Fall Rye"}
        self.assertTrue(want <= {c.trade_label for c in RULES.crops.values()})

    def test_benchmark_template_agrees_with_rules(self):
        self.assertEqual(check_benchmark_template(), [])

    def test_region_avg_rule_is_unified(self):
        """[REGION-AVG-RULE] one rule for both provinces: southern crops Brown + Dark Brown,
        everything else every active zone."""
        southern = set(RULES.raw["region_averaging"]["southern_crops"])
        for code in ("SK", "AB"):              # soil-zone provinces; MB is province-wide (test_mb)
            spec = province(code)
            for key, pc in spec.crops.items():
                want = ("brown", "dark_brown") if key in southern else spec.active_regions
                self.assertEqual(pc.regions, want, f"{code} {key}")
        self.assertEqual(province("SK").crop("oats").regions, ("brown", "dark_brown", "black"))
        self.assertEqual(province("SK").crop("durum").regions, ("brown", "dark_brown"))

    def test_rejects_per_crop_regions(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["provinces"]["SK"]["crops"]["oats"].update(regions=["black"]))

    def test_rejects_inactive_southern_region(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["region_averaging"].update(southern_regions=["brown", "peace"]))

    def test_rejects_unknown_crop_key(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["provinces"]["SK"]["crops"].update(mystery={"source_aliases": ["X"]}))

    def test_rejects_inheritance_chain(self):
        def mutate(r):
            r["provinces"]["SK"]["crops"]["wheat_spring"]["inherits"] = "canola"
            r["provinces"]["SK"]["crops"]["wheat_spring"]["start_year"] = 2026
        with self.assertRaises(RulesError):
            _load_mutated(mutate)

    def test_rejects_bad_fertilizer_grade_and_n_credit_on_urea(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["provinces"]["SK"]["fertilizer"]["products"]["P"].update(grade=1.5))
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["provinces"]["SK"]["fertilizer"]["products"]["N"].update(n_content=0.1))

    def test_rejects_duplicate_alias(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["provinces"]["SK"]["crops"]["canola"]["source_aliases"].append("Flax"))

    def test_rejects_override_without_reason_or_unknown_crop(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["source_overrides"][0].update(reason=""))
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["source_overrides"][0].update(crop_key="soybeans"))

    def test_rejects_unknown_quote_unit(self):
        with self.assertRaises(RulesError):
            _load_mutated(lambda r: r["provinces"]["AB"]["fertilizer"].update(quote_unit="$/bag"))

    def test_unchanged_rules_still_load(self):
        _load_mutated(lambda r: copy.deepcopy(r))


if __name__ == "__main__":
    unittest.main()
