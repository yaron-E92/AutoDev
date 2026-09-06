from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitFlowPromotionSemVerDocsTests(unittest.TestCase):
    def test_promotion_contract_documents_derived_and_fallback_intent(self):
        text = (ROOT / "docs" / "git-flow-promotion-semver.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("does **not** need its own `+semver` directive", text)
        self.assertIn("`+semver: none`", text)
        self.assertIn("cannot suppress a derived", text)
        self.assertIn("If no integration PR contributes", text)
        self.assertIn("must contain exactly one explicit `+semver` directive", text)


if __name__ == "__main__":
    unittest.main()
