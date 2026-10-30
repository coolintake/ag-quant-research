"""[PRAIRIE-WEIGHTED-RPA] StatCan acreage weights, Prairie aggregation, analytics JSON."""

import json
import unittest

from src.config import RULES, RulesError, load_rules
from src.engine.prairie_engine import aggregate, load_weights, prairie_last_yr, provincial_last_yr
from src.outputs.analytics_json import build
from src.pricing.statcan_weights import compute_weights, read_acres, source_csv


class WeightsTest(unittest.TestCase):
    def test_compute_weights_skips_suppressed_cells(self):
        acres = {"lentils_red": {"SK": {2024: 300.0, 2025: 300.0}, "AB": {2024: 100.0, 2025: None},
                                 "MB": {2024: None, 2025: None}}}
        w = compute_weights(acres, [2024, 2025])["lentils_red"]
        self.assertEqual(w["mean_acres"], {"SK": 300.0, "AB": 100.0, "MB": 0.0})
        self.assertEqual(w["weights"], {"SK": 0.75, "AB": 0.25, "MB": 0.0})

    def test_rules_block_matches_statcan_csv(self):
        if not source_csv().exists():
            self.skipTest("StatCan 32-10-0359 CSV not available")
        acres, window, _ = read_acres()
        self.assertEqual(window, [2024, 2025, 2026])
        fresh = compute_weights(acres, window)
        block = load_weights()
        self.assertEqual(block["years"], window)
        for key, w in fresh.items():
            self.assertEqual(block["crops"][key]["weights"], w["weights"], key)

    def test_weights_cover_the_rules_crop_map(self):
        block = load_weights()
        self.assertEqual(set(block["crops"]), set(RULES.raw["statcan_acreage"]["crops"]))
        for key, w in block["crops"].items():
            self.assertAlmostEqual(sum(w["weights"].values()), 1.0, places=5, msg=key)

    def test_rules_reject_bad_weights(self):
        raw = json.loads(json.dumps(RULES.raw))
        raw["prairie_acreage_weights"]["crops"]["canola"]["weights"]["SK"] = 0.9
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(raw, fh)
        try:
            with self.assertRaises(RulesError):
                load_rules(fh.name)
        finally:
            os.unlink(fh.name)


class OrderTest(unittest.TestCase):
    def test_provinces_always_mb_sk_ab(self):
        from src.config import PROVINCES
        self.assertEqual(PROVINCES, ("MB", "SK", "AB"))
        summaries = [t for t in RULES.workbook_tabs if t.startswith("RPA_Summary_")]
        self.assertEqual(summaries, ["RPA_Summary_MB", "RPA_Summary_SK", "RPA_Summary_AB"])
        self.assertEqual(list(load_weights()["crops"]["canola"]["weights"]), ["MB", "SK", "AB"])
        self.assertNotIn("chickpeas_kabuli", load_weights()["crops"])


class AggregateTest(unittest.TestCase):
    WEIGHTS = {"years": [2024], "source": "test", "crops": {
        "canola": {"weights": {"SK": 0.5, "AB": 0.3, "MB": 0.2}},
        "durum": {"weights": {"SK": 0.8, "AB": 0.15, "MB": 0.05}}}}

    @staticmethod
    def econ(y, p, seed, fert, chem):
        return {"yield": y, "price": p, "seed": seed, "fert": fert, "chem": chem,
                "rpa": y * p - (seed + fert + chem)}

    def test_weighted_math(self):
        e = {"SK": {"canola": self.econ(40, 13, 90, 100, 60)}, "AB": {"canola": self.econ(50, 14, 80, 110, 70)},
             "MB": {"canola": self.econ(45, 14.5, 82, 120, 65)}}
        r = aggregate(e, self.WEIGHTS)["crops"]["canola"]
        self.assertAlmostEqual(r["yield"], 0.5 * 40 + 0.3 * 50 + 0.2 * 45)
        self.assertAlmostEqual(r["price"], 0.5 * 13 + 0.3 * 14 + 0.2 * 14.5)
        self.assertAlmostEqual(r["revenue"], r["yield"] * r["price"])
        self.assertAlmostEqual(r["fert"], 0.5 * 100 + 0.3 * 110 + 0.2 * 120)
        self.assertAlmostEqual(r["rpa"], r["revenue"] - (r["seed"] + r["fert"] + r["chem"]))
        self.assertEqual(r["coverage"], 1.0)

    def test_renormalized_over_budgeting_provinces(self):
        e = {"SK": {"durum": self.econ(48, 8, 30, 90, 45)}, "AB": {"durum": self.econ(42, 7.9, 35, 70, 50)}, "MB": {}}
        r = aggregate(e, self.WEIGHTS)["crops"]["durum"]
        self.assertAlmostEqual(r["coverage"], 0.95)
        self.assertAlmostEqual(r["weights"]["SK"], 0.8 / 0.95)
        self.assertNotIn("MB", r["weights"])


class PrairieLastYrTest(unittest.TestCase):
    def test_every_budgeted_crop_aggregated(self):
        res = prairie_last_yr(2026)
        econ = provincial_last_yr(2026)
        budgeted = {k for prov in econ.values() for k in prov}
        self.assertEqual(set(res["crops"]), budgeted)
        corn = res["crops"]["corn"]
        self.assertEqual(corn["weights"], {"MB": 1.0})                    # only MB budgets corn
        self.assertAlmostEqual(corn["rpa"], econ["MB"]["corn"]["rpa"], places=9)

    def test_analytics_has_four_levels(self):
        data = build(2026)
        self.assertEqual(set(data["provinces"]), {"SK", "AB", "MB"})
        self.assertIn("prairie_weighted_benchmark", data)
        self.assertEqual(data["meta"]["levels"], ["MB", "SK", "AB", "Prairie"])
        self.assertEqual(list(data["provinces"]), ["MB", "SK", "AB"])
        self.assertEqual(data["prairie_weighted_benchmark"]["weights_years"], [2024, 2025, 2026])


if __name__ == "__main__":
    unittest.main()
