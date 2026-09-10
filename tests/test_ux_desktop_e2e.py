from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_capture, ux_multimodal, ux_resolver
from automation.ux_contract import UXBundleManifest
from tests.test_ux_multimodal import FakeMultimodalRuntime


PNG_REFERENCE = b"\x89PNG\r\n\x1a\nreference"


@unittest.skipUnless(sys.platform == "win32", "real desktop capture fixture requires Windows")
class DesktopCaptureE2ETests(unittest.TestCase):
    def test_real_window_capture_flows_through_multimodal_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            current = repo / ".autodev-run" / "current"
            (repo / ".autodev").mkdir(parents=True)
            current.mkdir(parents=True)

            artifact_root = root / "ux"
            (artifact_root / "screens").mkdir(parents=True)
            reference_path = artifact_root / "screens" / "home.png"
            reference_path.write_bytes(PNG_REFERENCE)
            manifest = UXBundleManifest(
                schema="autodev.ux.bundle/v1",
                product="desktop-demo",
                contract="contract.yaml",
                prototype="",
                screens={"home": "screens/home.png"},
                states={},
                journey_files={},
            )
            artifact = ux_resolver.ResolvedUXArtifact(
                immutable_identity="sha256:desktop-pinned",
                immutable_reference="fake://desktop@sha256:desktop-pinned",
                local_root=artifact_root,
                manifest=manifest,
                source_reference="fake://desktop@sha256:desktop-pinned",
                resolver_kind="fake",
            )
            (current / "state.json").write_text(
                json.dumps({"UXArtifact": {"immutable_identity": "sha256:desktop-pinned"}}),
                encoding="utf-8",
            )
            (current / "ux-context-verifier.json").write_text(
                json.dumps(
                    {
                        "ux_artifact": {
                            "immutable_identity": "sha256:desktop-pinned",
                            "product": "desktop-demo",
                            "bundle_schema": "autodev.ux.bundle/v1",
                        },
                        "ux_context": {
                            "contract": "contract.yaml",
                            "principles": "",
                            "screens": ["home"],
                            "states": [],
                            "journeys": [],
                        },
                        "ux_context_fingerprint": "desktop-fingerprint",
                    }
                ),
                encoding="utf-8",
            )

            fixture = Path(__file__).resolve().parent / "fixtures" / "win32_ux_window.py"
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "provider": "desktop",
                        "timeout_seconds": 30,
                        "desktop": {
                            "platform": "windows",
                            "application": {
                                "command": [sys.executable, str(fixture)],
                            },
                        },
                        "targets": {
                            "screen:home": {
                                "source_kind": "screen",
                                "source_id": "home",
                                "output": "home.png",
                                "window": {
                                    "process_name": Path(sys.executable).name,
                                    "title": "AutoDev Desktop Fixture",
                                    "class_name": "Static",
                                    "timeout_ms": 15000,
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            runtime = FakeMultimodalRuntime("pass")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                result_path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=lambda *_args, **_kwargs: None,
                )

            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                result["status"],
                "pass",
                str(result.get("diagnostic", "") or "desktop UX capture returned no diagnostic"),
            )
            self.assertEqual(runtime.invocations, 1)
            self.assertEqual(len(runtime.attachments), 2)
            implementation = result["implementation_evidence"][0]
            self.assertEqual(implementation["target_id"], "screen:home")
            self.assertEqual(implementation["platform"], "windows-desktop")
            self.assertRegex(implementation["viewport"], r"^[1-9][0-9]*x[1-9][0-9]*$")
            self.assertTrue(implementation["configured_identity"].startswith("windows-window-config:"))
            self.assertTrue(implementation["runtime_identity"].startswith("windows-window:"))
            capture = current / "ux-captures" / "home.png"
            self.assertEqual(ux_capture.image_mime(capture.read_bytes()), "image/png")


if __name__ == "__main__":
    unittest.main()
