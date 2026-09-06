from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitFlowPromotionSemVerDocsTests(unittest.TestCase):
    def test_canonical_policy_documents_derived_and_fallback_promotion_intent(self):
        text = (ROOT / "docs" / "version-policy.md").read_text(encoding="utf-8")
        self.assertIn("does not need to restate a bump", text)
        self.assertIn("`+semver: none` on the promotion cannot suppress", text)
        self.assertIn("If no integration PR contributes an intent", text)
        self.assertIn("must contain exactly one explicit `+semver` directive", text)
        self.assertIn("promotion-level directive is optional and non-authoritative", text)


if __name__ == "__main__":
    unittest.main()
