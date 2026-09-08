from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_resume_checkpoint, run_manifest, ux_multimodal


class UXMultimodalCheckpointIdentityTests(unittest.TestCase):
    def test_semantic_checkpoint_tracks_multimodal_result_as_identity_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir).resolve()
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="owner/repo",
                issue_number=305,
                mode="issue-to-pr",
                base_sha="base",
                branch="feature",
                role_snapshots={"verifier": {"fingerprint": "verifier-fp"}},
            )
            (current / "state.json").write_text(
                json.dumps(
                    {
                        "SemanticSourceIdentity": "source-id",
                        "LastSemanticVerdict": "pass",
                    }
                ),
                encoding="utf-8",
            )
            (current / "verification-result.json").write_text(
                '{"verdict":"pass"}\n', encoding="utf-8"
            )
            multimodal_result = {
                "schema": ux_multimodal.RESULT_SCHEMA,
                "status": "pass",
                "ux_artifact": {"immutable_identity": "sha256:pinned"},
                "ux_context_fingerprint": "ux-context",
                "capture_config_sha256": "capture-config",
                "capture_identity": "capture-identity",
                "verification_contract": ux_multimodal.MULTIMODAL_CONTRACT.safe_metadata(),
                "runtime": {
                    "name": "fake-vision",
                    "model": "fake/vision",
                    "capability": ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
                },
                "reference_evidence": [],
                "implementation_evidence": [],
                "comparisons": [],
                "findings": [],
                "repair_brief": "",
                "diagnostic": "",
            }
            result_path = current / ux_multimodal.RESULT_FILE
            result_path.write_text(json.dumps(multimodal_result), encoding="utf-8")

            opencode_resume_checkpoint.checkpoint_stage(
                repo,
                "semantic",
                {"state": "CONTINUE"},
                0,
            )

            manifest = run_manifest.load_manifest(manifest_path)
            semantic = manifest["stages"]["semantic-verified"]
            self.assertIn(ux_multimodal.RESULT_FILE, semantic["artifacts"])
            details = semantic["details"]
            self.assertEqual(details["multimodal_status"], "pass")
            self.assertTrue(details["multimodal_evidence_identity"])
            self.assertFalse(run_manifest.validate_artifacts(manifest, current))

            multimodal_result["capture_identity"] = "changed-after-checkpoint"
            result_path.write_text(json.dumps(multimodal_result), encoding="utf-8")
            problems = run_manifest.validate_artifacts(
                run_manifest.load_manifest(manifest_path),
                current,
            )
            self.assertTrue(
                any(
                    ux_multimodal.RESULT_FILE in problem and "artifact drift" in problem
                    for problem in problems
                )
            )


if __name__ == "__main__":
    unittest.main()
