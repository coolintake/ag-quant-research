"""Pricing layer: N-credit conversion, units, province isolation and template readers."""

import tempfile
import unittest
from pathlib import Path

from src.config import province
from src.normalize.baselines import guide_published_per_lb, last_yr_quotes
from src.pricing import convert_quote, load_crop_bids, load_fertilizer_quotes, per_lb, quotes_from_per_lb, round_yield

SAMPLE_LB = {"N": 0.80, "P": 0.90, "K": 0.50, "S": 0.45}


class FertilizerTest(unittest.TestCase):
    def test_round_trip_both_provinces_and_units(self):
        for code in ("SK", "AB"):
            spec = province(code)
            for unit in ("$/t", "$/sh.t"):
                back = per_lb(quotes_from_per_lb(SAMPLE_LB, spec, unit), spec, unit)
                for n, v in SAMPLE_LB.items():
                    self.assertAlmostEqual(back[n], v, places=12)

    def test_tonne_and_short_ton_give_the_same_vector(self):
        spec = province("AB")
        t = {"N": 832.50, "P": 1256.23, "K": 685.95, "S": 624.16}
        sh = {n: convert_quote(v, "$/t", "$/sh.t") for n, v in t.items()}
        a, b = per_lb(t, spec, "$/t"), per_lb(sh, spec, "$/sh.t")
        for n in t:
            self.assertAlmostEqual(a[n], b[n], places=12)

    def test_n_credit_formula(self):
        spec = province("SK")
        q = {"N": 800.0, "P": 1200.0, "K": 600.0, "S": 700.0}
        got = per_lb(q, spec)
        n = 800.0 / 2204.62 / 0.46
        self.assertAlmostEqual(got["N"], n, places=12)
        self.assertAlmostEqual(got["P"], (1200.0 / 2204.62 - 0.11 * n) / 0.52, places=12)
        self.assertAlmostEqual(got["S"], (700.0 / 2204.62 - 0.21 * n) / 0.24, places=12)
        self.assertAlmostEqual(got["K"], 600.0 / 2204.62 / 0.60, places=12)

    def test_ab_guide_quotes_reproduce_published_per_lb(self):
        got = per_lb(last_yr_quotes("AB", 2026), province("AB"))
        for n, v in guide_published_per_lb("AB", 2026).items():
            self.assertAlmostEqual(got[n], v, delta=0.005)

    def test_province_isolation(self):
        """Same quotes, different products (AS 21% vs 20.5% N) -> different $/lb S."""
        q = {"N": 800.0, "P": 1200.0, "K": 600.0, "S": 700.0}
        sk, ab = per_lb(q, province("SK")), per_lb(q, province("AB"))
        self.assertAlmostEqual(sk["N"], ab["N"], places=12)
        self.assertNotAlmostEqual(sk["S"], ab["S"], places=6)
        ab_freight = per_lb({**q, "N": 850.0}, province("AB"))
        self.assertNotAlmostEqual(ab_freight["N"], sk["N"], places=6)


class UnitsAndTemplatesTest(unittest.TestCase):
    def test_round_yield_half_up(self):
        self.assertEqual([round_yield(v) for v in (55.23, 40.5, 41.5, 1482.4, 2688.0)], [55, 41, 42, 1482, 2688])

    def test_fertilizer_quotes_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.csv"
            p.write_text("Date,Province,Nutrient,Product,Price,Unit,Notes\n"
                         "2027-01-15,AB,N,Urea,760,$/sh.t,retail\n"
                         "2027-02-15,AB,N,Urea,850,$/t,retail\n"
                         "2027-02-15,SK,P,MAP,1200,$/t,retail\n", encoding="utf-8")
            early = load_fertilizer_quotes("AB", "2027-01-31", p)
            self.assertAlmostEqual(early["N"]["price"], convert_quote(760, "$/sh.t", "$/t"), places=9)
            self.assertEqual(load_fertilizer_quotes("AB", None, p)["N"]["price"], 850)
            self.assertEqual(set(load_fertilizer_quotes("SK", None, p)), {"P"})

    def test_crop_bids_with_yield_and_unknown_crop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.csv"
            p.write_text("Date,Province,Crop_Key,Display_Name,Basis,Futures,Price,Unit,Notes,Yield,Yield_Unit\n"
                         "2027-03-01,SK,wheat_cps,CPS Wheat,,,7.6,$/bu,x,64,bu/ac\n"
                         "2027-03-01,AB,lentils_red,Red Lentils,,,0.31,$/lb,x,,\n", encoding="utf-8")
            sk = load_crop_bids("SK", None, p)["wheat_cps"]
            self.assertEqual((sk["price"], sk["yield"]), (7.6, 64))
            self.assertAlmostEqual(load_crop_bids("AB", None, p)["lentils_red"]["price"], 31.0)
            p.write_text("Date,Province,Crop_Key,Display_Name,Basis,Futures,Price,Unit,Notes\n"
                         "2027-03-01,AB,soybeans,Soy,,,13,$/bu,x\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_crop_bids("AB", None, p)


if __name__ == "__main__":
    unittest.main()
