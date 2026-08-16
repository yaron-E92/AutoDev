from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import privacy, privacy_consent, run_manifest, workflow_stages


class PrivacyConsentEnforcementDriftTests(unittest.TestCase):
    def test_effective_control_drift_changes_consent_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            config = repo / ".autodev" / "privacy.json"
            config.parent.mkdir()
            config.write_text(
                json.dumps({"profile": "strict-confidential", "consent_mode": "explicit"}),
                encoding="utf-8",
            )
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            run_manifest.create_manifest(
                current / run_manifest.MANIFEST_NAME,
                repo_path=repo,
                github_repo="owner/repo",
                issue_number=128,
                mode="issue-to-pr",
                base_sha="base",
                branch="branch",
                role_snapshots={},
            )
            policy = privacy.load_policy(repo)
            verified = privacy.PrivacyDecision(
                "CONSENT_REQUIRED",
                "planner",
                "openrouter/vendor/model",
                "openrouter",
                "vendor/model",
                "routed-cloud",
                training="unknown",
                retention="unknown",
                policy_source="openrouter-policy",
                enforcement_state="request-verified",
                controls=["provider.data_collection=\"deny\"", "provider.zdr=true"],
                reason="account policy remains unverified",
            )
            unverified = privacy.PrivacyDecision(
                "CONSENT_REQUIRED",
                "planner",
                "openrouter/vendor/model",
                "openrouter",
                "vendor/model",
                "routed-cloud",
                training="unknown",
                retention="unknown",
                policy_source="openrouter-policy",
                enforcement_state="unverified",
                controls=["provider.data_collection=\"deny\"", "provider.zdr=true"],
                reason="account policy remains unverified",
            )

            verified_identity, _ = privacy_consent.consent_identity(repo, policy, verified)
            unverified_identity, _ = privacy_consent.consent_identity(repo, policy, unverified)

            self.assertNotEqual(verified_identity, unverified_identity)


if __name__ == "__main__":
    unittest.main()
