"""Crop translation, canonical baselines, region rules, overrides and inheritance."""

import unittest

from src.config import province
from src.normalize.baselines import baselines_by_key, province_baselines
from src.normalize.crops import resolve, resolve_votes, translate


class CropTranslationTest(unittest.TestCase):
    def test_sk_aliases(self):
        for raw, key in (("Hard Red Spring Wheat", "wheat_spring"), ("Spring Wheat", "wheat_spring"),
                         ("Hard Red Spring", "wheat_spring"), ("Soybean**", "soybeans"),
                         ("Edible Yellow Peas", "peas_yellow"), ("Durum Wheat", "durum"),
                         ("Hybrid Fall Rye", "rye_hybrid_fall"), ("CNHR Wheat", "wheat_cps")):
            self.assertEqual(resolve("SK", raw), key, raw)

    def test_ab_aliases_and_patterns(self):
        for raw, key in (("Spring Wheat", "wheat_spring"), ("CPS Wheat", "wheat_cps"),
                         ("Argentine HT Canola", "canola"), ("argentine canola", "canola"),
                         ("Field Pea", "peas_yellow"), ("Red Lentil*", "lentils_red"),
                         ("milling oats", "oats"),
                         ("Malt Barley", "barley_malt")):
            self.assertEqual(resolve("AB", raw), key, raw)

    def test_unmapped_stays_none(self):
        for raw in ("Green Lentils", "Soft White Wheat", "Winter Wheat", "Yellow Mustard", "Kabuli Chickpea", ""):
            self.assertIsNone(resolve("AB", raw), raw)

    def test_trade_labels(self):
        self.assertEqual(translate("AB", "Spring Wheat"), ("wheat_spring", "CWRS Wheat"))
        self.assertEqual(translate("SK", "Hard Red Spring Wheat"), ("wheat_spring", "CWRS Wheat"))
        self.assertEqual(translate("SK", "Hybrid Fall Rye"), ("rye_hybrid_fall", "Fall Rye"))

    def test_ocr_votes(self):
        self.assertEqual(resolve_votes("AB", ["Ourum Wheat", "Durum Wheat", "Durum Wheat"]), "durum")


class BaselineTest(unittest.TestCase):
    def test_every_crop_has_a_baseline_in_rules_order(self):
        for code in ("SK", "AB"):
            keys = [b.crop_key for b in province_baselines(code, 2026)]
            self.assertEqual(keys, [k for k in province(code).crops if k in keys])
        self.assertEqual(len(province_baselines("SK", 2026)), 12)
        self.assertEqual(len(province_baselines("AB", 2026)), 9)

    def test_ab_regions(self):
        ab = baselines_by_key("AB", 2026)
        self.assertEqual(ab["durum"].regions, ("brown", "dark_brown"))
        self.assertEqual(ab["lentils_red"].regions, ("brown", "dark_brown"))
        self.assertNotIn("chickpeas_kabuli", ab)                                  # dropped: minor crop
        for key in ("wheat_spring", "canola", "barley_feed", "oats", "peas_yellow"):
            self.assertEqual(ab[key].regions, ("brown", "dark_brown", "black"), key)

    def test_sk_averages_where_published(self):
        """[REGION-AVG-RULE] SK baselines are equal-weight means of the published zone budgets."""
        import json
        from src.normalize.sk_mvs import OUTPUT_JSON
        recs = {(r["year"], r["crop_key"]): r for r in json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))["records"]}
        sk = baselines_by_key("SK", 2026)
        self.assertEqual(sk["canola"].regions, ("brown", "dark_brown", "black"))
        self.assertEqual(sk["durum"].regions, ("brown", "dark_brown"))
        self.assertEqual(sk["lentils_red"].regions, ("brown", "dark_brown"))
        for key in ("oats", "canola", "lentils_red"):
            zones = recs[(2026, key)]["zone_detail"]
            self.assertEqual(len(zones), len(sk[key].regions), key)

    def test_ab_red_lentil_seed_override(self):
        b = baselines_by_key("AB", 2026)["lentils_red"]
        self.assertAlmostEqual(b.seed, 29.40, places=6)
        self.assertEqual(b.seeding_lbs, 60.0)

    def test_sk_cps_inherits_cwrs(self):
        sk = baselines_by_key("SK", 2026)
        cps, cwrs = sk["wheat_cps"], sk["wheat_spring"]
        self.assertEqual(cps.inherits, "wheat_spring")
        self.assertEqual((cps.rates, cps.chem, cps.seed, cps.yield_whole, cps.price),
                         (cwrs.rates, cwrs.chem, cwrs.seed, cwrs.yield_whole, cwrs.price))
        self.assertNotIn("wheat_cps", baselines_by_key("SK", 2025))           # no back-filled history

    def test_whole_yields(self):
        for code in ("SK", "AB"):
            for b in province_baselines(code, 2026):
                self.assertEqual(b.yield_whole, int(b.yield_whole))
                self.assertLessEqual(abs(b.yield_whole - b.yield_exact), 0.5)


if __name__ == "__main__":
    unittest.main()
