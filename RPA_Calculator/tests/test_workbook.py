"""Workbook structure (openpyxl, no Excel needed). Excel recalculation checks live in
src/outputs/excel_verify.py (python -m src.outputs.excel --verify, Windows only)."""

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from src.config import PROVINCES, RULES
from src.normalize.baselines import province_baselines
from src.outputs.excel import AGRO_SHEET, FIRST_COL, S_ROW, build_workbook


class WorkbookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path, cls.layout = build_workbook(2026, Path(cls.tmp.name) / "wb.xlsx")
        cls.wb = load_workbook(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.wb.close()
        cls.tmp.cleanup()

    def test_tabs(self):
        self.assertEqual(self.wb.sheetnames, list(RULES.workbook_tabs))

    def test_one_column_per_baseline_with_trade_labels(self):
        for code in PROVINCES:
            ws = self.wb[f"RPA_Summary_{code}"]
            labels = [b.trade_label for b in province_baselines(code, 2026)]
            got = [ws.cell(S_ROW["crop"], FIRST_COL + i).value.rstrip("†") for i in range(len(labels))]
            self.assertEqual(got, labels)
            self.assertIsNone(ws.cell(S_ROW["crop"], FIRST_COL + len(labels)).value)
            self.assertEqual((ws.cell(S_ROW["crop"], 1).value, ws.cell(S_ROW["crop"], 2).value), ("Metric", "Unit"))

    def test_no_units_inside_value_formats(self):
        """Units live in the Unit column / headers, never in a number format."""
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    fmt = c.number_format or ""
                    self.assertFalse(any(u in fmt for u in ("bu/ac", "lb/ac", "¢/lb", "/bu", '"x"')),
                                     f"{ws.title}!{c.coordinate} {fmt}")

    def test_every_fertilizer_row_uses_the_n_credit(self):
        for code in PROVINCES:
            for n, (row, col) in self.layout["cp"][code]["quote_cells"].items():
                f = self.wb["Control_Panel"].cell(row, col + 2).value       # Current $/lb
                self.assertTrue(f.startswith("=(I"), f)
                self.assertEqual("-F" in f, n in ("P", "K", "S"), f)      # urea / NH3 rows carry no credit

    def test_cps_links_to_cwrs_and_class_inputs(self):
        ws = self.wb[AGRO_SHEET]
        refs = self.layout["rates"]["SK"]
        row = refs["wheat_cps"]["row"]
        self.assertTrue(str(ws.cell(row, 4).value).startswith("=Control_Panel!"))
        for c in (8, 9, 10, 11):                                         # N P K S follow CWRS
            self.assertTrue(str(ws.cell(row, c).value).startswith(f"={AGRO_SHEET}!"))

    def test_history_rows_and_forecast_links(self):
        ws, h = self.wb["Historical_Ratios"], self.layout["hist"]
        bases = [g["base"] for g in RULES.historical["bases"]]
        self.assertEqual(list(dict.fromkeys(r["base"] for r in h["rows"])), bases)
        for r in h["rows"]:
            f = str(ws.cell(r["row"], h["forecast_col"]).value)
            self.assertIn(f"RPA_Summary_{r['province']}!", f)
        pairs = {(r["province"], r["numerator"], r["base"]) for r in h["rows"]}
        self.assertIn(("MB", "soybeans", "canola"), pairs)
        self.assertNotIn(("MB", "wheat_spring", "durum"), pairs)          # MB budgets no durum


if __name__ == "__main__":
    unittest.main()
