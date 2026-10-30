"""AB Table 1 / Table 2 inspection export (2026 guide: text layer, no OCR needed)."""

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from src.config import province, source_pdfs
from src.outputs.export_ab_reference_tables import build, match_rows, read_rate_tables


class ReferenceTablesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = read_rate_tables(source_pdfs(province("AB"))[2026])

    def test_ocr_damaged_labels(self):
        got = match_rows(["sprin wheat", "ar entine canola", "reen lentils", "red lentils", "d beans", "2024"])
        self.assertEqual(got, {"sprin wheat": "Spring Wheat", "ar entine canola": "Argentine Canola",
                               "reen lentils": "Green Lentils", "red lentils": "Red Lentils", "d beans": "Dry Beans"})

    def test_table1_cells_by_position(self):
        g = self.tables["seed"]["grid"]
        self.assertEqual(self.tables["seed"]["source"], "text layer")
        self.assertEqual(g["Red Lentils"], {("brown", 0): 60.0, ("brown", 2): 29.4,
                                            ("dark_brown", 0): 60.0, ("dark_brown", 2): 29.4})
        self.assertEqual(g["Spring Wheat"][("black", 1)], 2.25)             # bu/ac column
        self.assertEqual(g["Spring Wheat"][("black", 2)], 32.4)

    def test_table2_cells_by_position(self):
        g = self.tables["chem"]["grid"]
        self.assertEqual([g["Spring Wheat"][("black", s)] for s in range(3)], [3.05, 33.05, 19.19])
        self.assertNotIn(("black", 2), g["CPS Wheat"])                      # F/I/PH/D blank for CPS

    def test_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, summary = build([2026], Path(tmp) / "ref.xlsx")
            wb = load_workbook(path)
            self.assertEqual(wb.sheetnames, ["Table1_Seeding_Rates", "Table2_Chemical_Breakdown"])
            ws = wb["Table2_Chemical_Breakdown"]
            formulas = [c.value for row in ws.iter_rows() for c in row if str(c.value).startswith("=SUM(")]
            self.assertTrue(formulas)
            wb.close()
        self.assertGreater(summary[2026]["seed"]["cells"], 100)


if __name__ == "__main__":
    unittest.main()
