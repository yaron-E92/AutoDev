from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_capture, ux_multimodal, ux_multimodal_resume


PNG_REFERENCE = b"\x89PNG\r\n\x1a\nreference"
PNG_CAPTURE = b"\x89PNG\r\n\x1a\ndesktop"


class DesktopResumeTests(unittest.TestCase):
    def _prepared(self, root: Path):
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        (repo / ".autodev").mkdir(parents=True)
        (current / "ux-captures").mkdir(parents=True)
        reference_path = root / "home.png"
        reference_path.write_bytes(PNG_REFERENCE)
        capture_path = current / "ux-captures" / "home.png"
        capture_path.write_bytes(PNG_CAPTURE)

        config_value = {
            "schema": ux_capture.CAPTURE_SCHEMA,
            "provider": "desktop",
            "desktop": {"platform": "windows"},
            "targets": {
                "screen:home": {
                    "source_kind": "screen",
                    "source_id": "home",
                    "output": "home.png",
                    "viewport": "800x600",
                    "window": {
                        "process_name": "Demo.exe",
                        "title": "Demo Home",
                        "class_name": "DemoWindow",
                    },
                }
            },
        }
        config_path = repo / ux_capture.CAPTURE_CONFIG
        config_path.write_text(json.dumps(config_value), encoding="utf-8")
        config = ux_capture.load_config(repo)
        assert config is not None
        configured_identity = ux_capture.configured_capture_identity(config, "screen:home")

        (current / "state.json").write_text(
            json.dumps({"UXArtifact": {"immutable_identity": "sha256:pinned"}}),
            encoding="utf-8",
        )
        (current / "ux-context-verifier.json").write_text(
            json.dumps({"ux_context_fingerprint": "ux-fingerprint"}),
            encoding="utf-8",
        )
        reference = ux_multimodal.ReferenceImage(
            target_id="screen:home",
            source_kind="screen",
            source_id="home",
            relative_path="screens/home.png",
            path=reference_path,
            sha256=hashlib.sha256(PNG_REFERENCE).hexdigest(),
            mime="image/png",
            size_bytes=len(PNG_REFERENCE),
        )
        capture = ux_capture.CapturedImage(
            target=ux_capture.CaptureTarget(
                source_kind="screen",
                source_id="home",
                output_name="home.png",
                viewport="800x600",
                platform="windows-desktop",
            ),
            path=capture_path,
            sha256=hashlib.sha256(PNG_CAPTURE).hexdigest(),
            mime="image/png",
            size_bytes=len(PNG_CAPTURE),
            configured_identity=configured_identity,
            runtime_identity="windows-window:runtime-proof",
        )
        result = ux_multimodal._base_result(
            status="pass",
            artifact_context={"immutable_identity": "sha256:pinned"},
            ux_fingerprint="ux-fingerprint",
            capture_config_sha256=config.sha256,
            capability=ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
            runtime="fake-vision",
            model="fake/vision",
            references=(reference,),
            captures=(capture,),
            findings=[],
            repair_brief="",
        )
        (current / ux_multimodal.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
        return repo, current, config_path, config_value, reference

    def test_unchanged_desktop_target_identity_is_resume_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _path, _value, reference = self._prepared(Path(temp_dir))
            with patch.object(
                ux_multimodal,
                "selected_reference_images",
                return_value=(reference,),
            ):
                self.assertEqual(ux_multimodal_resume.stale_reasons(repo, current), [])

    def test_changed_window_identity_invalidates_prior_visual_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path, value, reference = self._prepared(Path(temp_dir))
            value["targets"]["screen:home"]["window"]["title"] = "Different Window"  # type: ignore[index]
            path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(
                ux_multimodal,
                "selected_reference_images",
                return_value=(reference,),
            ):
                reasons = ux_multimodal_resume.stale_reasons(repo, current)

            self.assertIn("UX capture configuration changed", reasons)
            self.assertIn("capture configured target identity changed for screen:home", reasons)
            self.assertIn("aggregate implementation capture identity changed", reasons)


if __name__ == "__main__":
    unittest.main()
