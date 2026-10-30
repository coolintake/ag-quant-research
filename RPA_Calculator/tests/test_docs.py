"""docs/METHODOLOGY.md documents every rule ID used by the rules file and the code."""

import re
import unittest

from src.config import PROJECT_ROOT, RULES

DOC = (PROJECT_ROOT / "docs" / "METHODOLOGY.md").read_text(encoding="utf-8")


class DocsTest(unittest.TestCase):
    def test_rules_file_ids_are_documented(self):
        ids = {RULES.raw["conventions"]["rule_id"], RULES.historical["rule_id"],
               RULES.raw["region_averaging"]["rule_id"]}
        for spec in RULES.provinces.values():
            ids |= {spec.rule_id, RULES.raw["provinces"][spec.code]["fertilizer"]["rule_id"]}
            ids |= {r["id"] for r in spec.agronomy_rules}
        ids |= {o["id"] for o in RULES.source_overrides}
        missing = sorted(i for i in ids if f"[{i}]" not in DOC)
        self.assertEqual(missing, [])

    def test_rule_tags_cited_in_code_are_documented(self):
        tags = set()
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            tags |= set(re.findall(r"\[([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+|CONV|DCASH|NCREDIT)\]", path.read_text(encoding="utf-8")))
        missing = sorted(t for t in tags if f"[{t}]" not in DOC)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
