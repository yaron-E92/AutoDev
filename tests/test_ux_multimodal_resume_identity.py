from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_capture, ux_resolver, ux_role_context
from automation.ux_contract import UXBundleManifest


PNG_A = b"\x89PNG\r\n\x1a\nreference-a"
PNG_B = b"\x89PNG\r\n\x1a\nreference-b"


class UXMultimodalResumeIdentityTests(unittest.TestCase):
    def _prepared(self, root: Path):
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        (repo / ".autodev").mkdir(parents=True)
        current.mkdir(parents=True)
        artifact_root = root / "ux"
        (artifact_root / "screens").mkdir(parents=True)
        (artifact_root / "screens" / "home.png").write_bytes(PNG_A)
        (artifact_root / "contract.yaml").write_text("layout: pinned\n", encoding="utf-8")
        manifest = UXBundleManifest(
            schema="autodev.ux.bundle/v1",
            product="demo",
            contract="contract.yaml",
            screens={"home": "screens/home.png"},
            states={},
            journey_files={},
        )
        artifact = ux_resolver.ResolvedUXArtifact(
            immutable_identity="sha256:pinned",
            immutable_reference="fake://demo@sha256:pinned",
            local_root=artifact_root,
            manifest=manifest,
            source_reference="fake://demo@sha256:pinned",
            resolver_kind="fake",
        )
        (current / "state.json").write_text(
            json.dumps({"UXArtifact": {"immutable_identity": "sha256:pinned"}}),
            encoding="utf-8",
        )
        (current / "run-manifest.json").write_text(
            json.dumps({"ux_artifact": {"immutable_identity": "sha256:pinned"}}),
            encoding="utf-8",
        )
        return repo, current, artifact

    def _write_capture(self, repo: Path, command: str) -> None:
        (repo / ux_capture.CAPTURE_CONFIG).write_text(
            json.dumps(
                {
                    "schema": ux_capture.CAPTURE_SCHEMA,
                    "command": [command],
                    "targets": {
                        "home": {
                            "source_kind": "screen",
                            "source_id": "home",
                            "output": "home.png",
                        }
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def test_verifier_fingerprint_changes_when_capture_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            self._write_capture(repo, "capture-v1")
            with patch.object(
                ux_role_context.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                _, first = ux_role_context.prepare_role_context(
                    repo, current, "verifier", "Verify home"
                )
                self._write_capture(repo, "capture-v2")
                _, second = ux_role_context.prepare_role_context(
                    repo, current, "verifier", "Verify home"
                )
            self.assertNotEqual(
                first["ux_context_fingerprint"], second["ux_context_fingerprint"]
            )
            self.assertNotEqual(
                first["ux_context"]["capture_config_sha256"],
                second["ux_context"]["capture_config_sha256"],
            )

    def test_verifier_fingerprint_changes_when_selected_reference_bytes_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            self._write_capture(repo, "capture")
            with patch.object(
                ux_role_context.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                _, first = ux_role_context.prepare_role_context(
                    repo, current, "verifier", "Verify home"
                )
                (artifact.local_root / "screens" / "home.png").write_bytes(PNG_B)
                _, second = ux_role_context.prepare_role_context(
                    repo, current, "verifier", "Verify home"
                )
            self.assertNotEqual(
                first["ux_context_fingerprint"], second["ux_context_fingerprint"]
            )
            self.assertNotEqual(
                first["ux_context"]["file_sha256"]["screens/home.png"],
                second["ux_context"]["file_sha256"]["screens/home.png"],
            )

    def test_non_verifier_role_does_not_bind_capture_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            self._write_capture(repo, "capture-v1")
            with patch.object(
                ux_role_context.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                _, first = ux_role_context.prepare_role_context(
                    repo, current, "planner", "Plan home"
                )
                self._write_capture(repo, "capture-v2")
                _, second = ux_role_context.prepare_role_context(
                    repo, current, "planner", "Plan home"
                )
            self.assertEqual(
                first["ux_context_fingerprint"], second["ux_context_fingerprint"]
            )
            self.assertNotIn("capture_config_sha256", first["ux_context"])


if __name__ == "__main__":
    unittest.main()
