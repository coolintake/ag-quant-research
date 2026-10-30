"""Manitoba COP workbooks: rules, extraction layout, normalization, N blend, baselines."""

import json
import unittest

from src.config import RULES, province
from src.engine.market import last_yr
from src.normalize.baselines import baselines_by_key, last_yr_quotes, n_shares, province_baselines
from src.normalize.crops import resolve
from src.pricing import per_lb

MB = province("MB")
RAW = json.loads(MB.raw_json.read_text(encoding="utf-8"))
MVS = json.loads(MB.baseline_path("json").read_text(encoding="utf-8"))
LEGUMES = ("soybeans", "peas_yellow")


class MBRulesTest(unittest.TestCase):
    def test_province_wide_scope(self):
        self.assertEqual((MB.region_scope, MB.active_regions), ("province_wide", ("province_wide",)))
        for pc in MB.crops.values():
            self.assertEqual(pc.regions, ("province_wide",))           # [REGION-AVG-RULE] one budget
        self.assertIn("RPA_Summary_MB", RULES.workbook_tabs)

    def test_crop_map(self):
        for raw, key in (("Wheat - Hard Red Spring", "wheat_spring"), ("Wheat - Prairie Spring", "wheat_cps"),
                         ("Barley", "barley_feed"), ("Fall Rye", "rye_hybrid_fall"),
                         ("Hybrid Fall Rye", "rye_hybrid_fall"), ("Flaxseed", "flax"), ("Peas", "peas_yellow"),
                         ("Corn", "corn"), ("Soybeans", "soybeans")):
            self.assertEqual(resolve("MB", raw), key, raw)
        for raw in ("Wheat - Northern Hard Red", "Wheat - Winter", "Beans - Pinto", "Sunflower - Oilseed", "Your Farm"):
            self.assertIsNone(resolve("MB", raw), raw)

    def test_quote_keys(self):
        self.assertEqual(MB.quote_keys, ("N", "P", "K", "S", "NH3"))
        self.assertEqual(province("SK").quote_keys, ("N", "P", "K", "S"))


class MBExtractionTest(unittest.TestCase):
    def test_every_year_has_both_sheets(self):
        years = [d["year"] for d in RAW["documents"]]
        self.assertEqual(years, list(range(2019, 2027)))
        for d in RAW["documents"]:
            self.assertEqual(d["warnings"], [], d["year"])
            rows = d["summary"]["rows"]
            self.assertTrue({"seed", "fertilizer", "target_price", "target_yield", "yield_unit"} <= set(rows), d["year"])
            self.assertTrue({"crop_protection", "herbicide"} & set(rows), d["year"])

    def test_layout_shifts_are_handled(self):
        docs = {d["year"]: d for d in RAW["documents"]}
        self.assertEqual(docs[2019]["summary"]["rows"]["target_yield"], 37)   # one row lower in 2019-20
        self.assertEqual(docs[2026]["summary"]["rows"]["target_yield"], 36)
        self.assertEqual(docs[2019]["summary"]["crops"]["Canola"]["column"], "C")
        self.assertEqual(docs[2026]["summary"]["crops"]["Canola"]["column"], "M")
        self.assertEqual(docs[2019]["sfc"]["anchors"]["chem_title"], "Chemicals")
        self.assertEqual(docs[2026]["sfc"]["anchors"]["chem_title"], "Crop Protection")


class MBNormalizeTest(unittest.TestCase):
    def test_all_records_verified(self):
        self.assertEqual(len(MVS["records"]), 80)
        self.assertEqual([(r["year"], r["crop_key"]) for r in MVS["records"] if not r["verified"]], [])

    def test_n_blend_shares(self):
        self.assertEqual(n_shares("MB", 2019), {"NH3": 0.5})
        self.assertEqual(n_shares("MB", 2026), {"NH3": 0.25})
        self.assertEqual(n_shares("SK", 2026), {})

    def test_guide_dollar_per_lb_reproduced(self):
        """[NCREDIT-MB] P / K / S credited at urea; the guide converts at 0.4536 kg/lb."""
        for y, prices in MVS["meta"]["fert_prices"].items():
            lb = per_lb({k: v["per_tonne"] for k, v in prices.items()}, MB)
            for n in ("N", "P", "K", "S"):
                self.assertAlmostEqual(lb[n], prices[n]["per_lb"], delta=2e-4, msg=f"{y} {n}")

    def test_engine_fertilizer_equals_guide(self):
        """Every crop-year's fertilizer $/ac == the workbook's, except legumes from 2022, where the
        workbook caps the MAP N credit at the crop's N rate (documented, engine keeps [NCREDIT])."""
        for year in range(2019, 2027):
            for b in province_baselines("MB", year):
                gap = last_yr(b)["fert"] - b.guide_fertilizer
                if b.crop_key in LEGUMES and year >= 2022:
                    self.assertLess(gap, -1.0, f"{year} {b.crop_key}")
                    self.assertTrue(any("caps the MAP/AS N credit" in f for f in b.flags))
                else:
                    self.assertLess(abs(gap), 0.05, f"{year} {b.crop_key} gap {gap:.4f}")

    def test_peas_converted_to_pulse_units(self):
        b = baselines_by_key("MB", 2026)["peas_yellow"]
        rec = next(r for r in MVS["records"] if r["year"] == 2026 and r["crop_key"] == "peas_yellow")
        self.assertEqual(b.yield_whole, round(rec["yield_"] * 60))
        self.assertAlmostEqual(b.price, rec["price"] / 60 * 100, places=9)

    def test_baselines_and_last_yr_quotes(self):
        self.assertEqual(len(province_baselines("MB", 2026)), 10)
        self.assertEqual(set(last_yr_quotes("MB", 2026)), set(MB.quote_keys))
        corn = baselines_by_key("MB", 2026)["corn"]
        self.assertIsNone(corn.seeding_lbs)                                  # plants/ac [MB-SEED-UNITS]
        self.assertIn("plants", corn.seeding_other)


if __name__ == "__main__":
    unittest.main()
