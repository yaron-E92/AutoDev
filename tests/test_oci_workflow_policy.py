from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
OCI_WORKFLOW = ROOT / ".github" / "workflows" / "oci-ux-integration.yml"


class OCIWorkflowPolicyTests(unittest.TestCase):
    def test_real_oci_smoke_is_change_gated_in_normal_ci(self):
        text = CI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("oci-ux-changes:", text)
        self.assertIn("Detect OCI UX integration changes", text)
        self.assertIn("automation/ux[^/]*\\.py", text)
        self.assertIn("tests/test_ux_oci\\.py", text)
        self.assertIn(
            "if: needs.oci-ux-changes.outputs.run == 'true'",
            text,
        )
        self.assertIn('OCI_UX_REQUIRED:', text)
        self.assertIn(
            'elif [[ "$OCI_UX_INTEGRATION" != "skipped" && '
            '"$OCI_UX_INTEGRATION" != "success" ]]',
            text,
        )

    def test_real_oci_smoke_remains_reusable_manual_and_periodic(self):
        text = OCI_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_call:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("schedule:", text)
        self.assertIn("cron: '23 4 * * 1'", text)
        self.assertIn("oras.land/oras/cmd/oras@v1.3.2", text)
        self.assertIn("registry:2.8.3@sha256:", text)
        self.assertIn("Publish and resolve a real OCI UX bundle", text)


if __name__ == "__main__":
    unittest.main()
