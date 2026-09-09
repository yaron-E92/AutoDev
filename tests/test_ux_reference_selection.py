from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_capture, ux_multimodal, ux_resolver
from automation.ux_contract import UXBundleManifest


PNG = b"\x89PNG\r\n\x1a\nreference"


class UXReferenceSelectionTests(unittest.TestCase):
    def _prepared(self, root: Path):
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        (repo / ".autodev").mkdir(parents=True)
        current.mkdir(parents=True)
        artifact_root = root / "ux"
        (artifact_root / "screens").mkdir(parents=True)
        (artifact_root / "journeys").mkdir(parents=True)
        (artifact_root / "screens" / "confirmation.png").write_bytes(PNG)
        (artifact_root / "journeys" / "checkout.json").write_text("{}\n", encoding="utf-8")
        manifest = UXBundleManifest(
            schema="autodev.ux.bundle/v1",
            product="demo",
            contract="contract.yaml",
            screens={"confirmation": "screens/confirmation.png"},
            states={},
            journey_files={"checkout-complete": "journeys/checkout.json"},
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
        (current / "ux-context-verifier.json").write_text(
            json.dumps(
                {
                    "ux_artifact": {"immutable_identity": "sha256:pinned"},
                    "ux_context": {
                        "contract": "contract.yaml",
                        "principles": "",
                        "screens": [],
                        "states": [],
                        "journeys": ["checkout-complete"],
                    },
                    "ux_context_fingerprint": "ux-fingerprint",
                }
            ),
            encoding="utf-8",
        )
        return repo, current, artifact

    def test_selected_journey_can_use_explicit_screen_reference_without_changing_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "provider": "command",
                        "command": ["capture-ui"],
                        "targets": {
                            "journey:checkout-complete": {
                                "source_kind": "journey",
                                "source_id": "checkout-complete",
                                "output": "checkout.png",
                                "reference": "screen:confirmation",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                references = ux_multimodal.selected_reference_images(repo, current)

            self.assertEqual(len(references), 1)
            reference = references[0]
            self.assertEqual(reference.target_id, "journey:checkout-complete")
            self.assertEqual(reference.source_kind, "journey")
            self.assertEqual(reference.source_id, "checkout-complete")
            self.assertEqual(reference.reference_target_id, "screen:confirmation")
            self.assertEqual(reference.relative_path, "screens/confirmation.png")
            self.assertEqual(reference.path.read_bytes(), PNG)

    def test_invalid_indirect_reference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "provider": "command",
                        "command": ["capture-ui"],
                        "targets": {
                            "journey:checkout-complete": {
                                "source_kind": "journey",
                                "source_id": "checkout-complete",
                                "output": "checkout.png",
                                "reference": "screen:missing",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ), self.assertRaisesRegex(
                ux_multimodal.UXMultimodalError,
                "not present in the pinned UX artifact",
            ):
                ux_multimodal.selected_reference_images(repo, current)


if __name__ == "__main__":
    unittest.main()
