from __future__ import annotations

import unittest
from pathlib import Path


class StructuredOutputDocsTests(unittest.TestCase):
    def test_authority_document_keeps_native_schema_and_workflow_authority_separate(self):
        text = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "structured-output-contracts.md"
        ).read_text(encoding="utf-8")
        self.assertIn("schema-valid value is only a transport/protocol success", text)
        self.assertIn("does not consume AutoDev's one protocol-correction attempt", text)
        self.assertIn("must not contain issue text", text)
        self.assertIn("cannot assert, replace, or prove that fingerprint", text)
        self.assertIn("opencode run --format json", text)
        self.assertIn("headless server/session API", text)


if __name__ == "__main__":
    unittest.main()
