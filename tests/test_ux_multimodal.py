from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import ux_capture, ux_multimodal, ux_resolver
from automation.ux_contract import UXBundleManifest


PNG_REFERENCE = b"\x89PNG\r\n\x1a\nreference"
PNG_IMPLEMENTATION = b"\x89PNG\r\n\x1a\nimplementation"
PNG_IMPLEMENTATION_2 = b"\x89PNG\r\n\x1a\nimplementation-2"


class FakeMultimodalRuntime:
    name = "fake-vision"

    def __init__(self, verdict: str = "pass", *, invented_source: bool = False) -> None:
        self.verdict = verdict
        self.invented_source = invented_source
        self.attachments: tuple[Path, ...] = ()
        self.prompt = ""
        self.invocations = 0

    def multimodal_verifier_capability(self, _repo, **_kwargs) -> str:
        return ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS

    def invoke_multimodal_verifier(
        self,
        _repo,
        *,
        prompt,
        attachments,
        contract,
        **_kwargs,
    ) -> ux_multimodal.RuntimeVerificationResult:
        self.invocations += 1
        self.prompt = prompt
        self.attachments = tuple(attachments)
        reference_hash = hashlib.sha256(self.attachments[0].read_bytes()).hexdigest()
        implementation_hash = hashlib.sha256(self.attachments[1].read_bytes()).hexdigest()
        source_id = "invented-screen" if self.invented_source else "home"
        status = {
            "pass": "satisfied",
            "repair": "violated",
            "unverifiable": "unverifiable",
        }[self.verdict]
        finding = {
            "source_kind": "screen",
            "source_id": source_id,
            "status": status,
            "evidence": "Compared the pinned reference and deterministic implementation capture.",
            "required_change": "Align the primary action and hierarchy." if status == "violated" else "",
            "category": "visual",
        }
        payload = {
            "verdict": self.verdict,
            "comparisons": [
                {
                    "target_id": "screen:home",
                    "source_kind": "screen",
                    "source_id": "home",
                    "reference_sha256": reference_hash,
                    "implementation_sha256": implementation_hash,
                    "status": status,
                }
            ],
            "ux_findings": [finding],
            "repair_brief": "Restore the pinned home-screen hierarchy." if self.verdict == "repair" else "",
        }
        return ux_multimodal.RuntimeVerificationResult(
            payload=payload,
            runtime=self.name,
            model="fake/vision",
            capability=ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
            structured_output_mode="native-validated",
            schema_retry_count=0,
        )


class UnsupportedRuntime:
    name = "text-only"
    invoked = False

    def multimodal_verifier_capability(self, _repo, **_kwargs) -> str:
        return ux_multimodal.CAPABILITY_UNSUPPORTED

    def invoke_multimodal_verifier(self, *_args, **_kwargs):
        self.invoked = True
        raise AssertionError("unsupported runtime must not receive images")


class UXMultimodalTests(unittest.TestCase):
    def _prepared(self, root: Path) -> tuple[Path, Path, ux_resolver.ResolvedUXArtifact]:
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        (repo / ".autodev").mkdir(parents=True)
        current.mkdir(parents=True)
        artifact_root = root / "ux"
        (artifact_root / "screens").mkdir(parents=True)
        (artifact_root / "screens" / "home.png").write_bytes(PNG_REFERENCE)
        (artifact_root / "screens" / "unrelated.png").write_bytes(
            b"\x89PNG\r\n\x1a\nunrelated"
        )
        (artifact_root / "prototype.html").write_text(
            "<script>throw new Error('must never execute');</script>", encoding="utf-8"
        )
        manifest = UXBundleManifest(
            schema="autodev.ux.bundle/v1",
            product="demo",
            contract="contract.yaml",
            prototype="prototype.html",
            screens={
                "home": "screens/home.png",
                "unrelated": "screens/unrelated.png",
            },
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
        (current / "ux-context-verifier.json").write_text(
            json.dumps(
                {
                    "ux_artifact": {
                        "immutable_identity": "sha256:pinned",
                        "product": "demo",
                        "bundle_schema": "autodev.ux.bundle/v1",
                    },
                    "ux_context": {
                        "contract": "contract.yaml",
                        "principles": "",
                        "screens": ["home"],
                        "states": [],
                        "journeys": [],
                    },
                    "ux_context_fingerprint": "ux-fingerprint",
                }
            ),
            encoding="utf-8",
        )
        (current / "run-manifest.json").write_text("{}\n", encoding="utf-8")
        (repo / ux_capture.CAPTURE_CONFIG).write_text(
            json.dumps(
                {
                    "schema": ux_capture.CAPTURE_SCHEMA,
                    "command": ["capture-demo"],
                    "targets": {
                        "screen:home": {
                            "source_kind": "screen",
                            "source_id": "home",
                            "output": "home.png",
                            "viewport": "1280x720",
                            "platform": "web",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return repo, current, artifact

    @staticmethod
    def _capture_runner(image: bytes = PNG_IMPLEMENTATION):
        def runner(_command, **kwargs):
            Path(kwargs["env"]["AUTODEV_UX_CAPTURE_OUTPUT"]).write_bytes(image)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return runner

    def test_no_visual_context_is_not_applicable_without_runtime_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            runtime = FakeMultimodalRuntime()
            path = ux_multimodal.run_verification(
                repo,
                runtime,
                runner=lambda *_args, **_kwargs: None,
            )
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "not-applicable")
            self.assertEqual(runtime.invocations, 0)

    def test_selected_reference_and_capture_are_compared_and_unrelated_files_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            runtime = FakeMultimodalRuntime("pass")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                result_path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(),
                )
            result = json.loads(result_path.read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "pass")
            self.assertEqual(runtime.invocations, 1)
            self.assertEqual(len(runtime.attachments), 2)
            self.assertEqual(runtime.attachments[0].name, "home.png")
            self.assertNotIn("unrelated.png", {path.name for path in runtime.attachments})
            self.assertNotIn("prototype.html", {path.name for path in runtime.attachments})
            self.assertEqual(result["reference_evidence"][0]["source_id"], "home")
            self.assertEqual(result["implementation_evidence"][0]["viewport"], "1280x720")
            manifest = json.loads((current / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["ux_multimodal_verification"]["status"], "pass")
            self.assertEqual(
                manifest["ux_multimodal_verification"]["ux_context_fingerprint"],
                "ux-fingerprint",
            )

    def test_text_only_runtime_cannot_false_pass_visual_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, artifact = self._prepared(Path(temp_dir))
            runtime = UnsupportedRuntime()
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(),
                )
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "unverifiable")
            self.assertFalse(runtime.invoked)
            self.assertEqual(result["findings"][0]["status"], "unverifiable")

    def test_violation_produces_validated_bounded_repair_brief(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            runtime = FakeMultimodalRuntime("repair")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(),
                )
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "repair")
            repair = ux_multimodal.repair_brief(current)
            self.assertIn("Restore the pinned home-screen hierarchy", repair)
            self.assertIn("screen `home`", repair)

    def test_model_cannot_smuggle_unselected_ux_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, artifact = self._prepared(Path(temp_dir))
            runtime = FakeMultimodalRuntime("repair", invented_source=True)
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(),
                )
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "unverifiable")
            self.assertIn("outside the effective selected UX context", result["diagnostic"])

    def test_changed_implementation_capture_changes_capture_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, artifact = self._prepared(Path(temp_dir))
            runtime = FakeMultimodalRuntime("pass")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                first_path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(PNG_IMPLEMENTATION),
                )
                first = json.loads(first_path.read_text(encoding="utf-8"))
                second_path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(PNG_IMPLEMENTATION_2),
                )
                second = json.loads(second_path.read_text(encoding="utf-8"))
            self.assertNotEqual(first["capture_identity"], second["capture_identity"])
            self.assertNotEqual(
                first["implementation_evidence"][0]["sha256"],
                second["implementation_evidence"][0]["sha256"],
            )

    def test_missing_capture_configuration_is_explicitly_unverifiable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, artifact = self._prepared(Path(temp_dir))
            (repo / ux_capture.CAPTURE_CONFIG).unlink()
            runtime = FakeMultimodalRuntime("pass")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                path = ux_multimodal.run_verification(
                    repo,
                    runtime,
                    runner=self._capture_runner(),
                )
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "unverifiable")
            self.assertIn("ux-capture.json", result["diagnostic"])
            self.assertEqual(runtime.invocations, 0)


if __name__ == "__main__":
    unittest.main()
