from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_multimodal,
    opencode_structured_output,
    role_runtime_capabilities,
    ux_multimodal,
    ux_multimodal_runtime,
)


PNG = b"\x89PNG\r\n\x1a\nimage"


class _Process:
    def poll(self):
        return None

    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0


class _OpenCodeRuntime:
    name = "opencode"

    def _resolve_mappings(self, _repo, **_kwargs):
        return {
            "verifier": {
                "agent": "autodev-verifier",
                "model": "openai/vision-model",
                "source": "test",
                "inherits_from": "",
            }
        }

    def image_input_capability(self, _repo, *, role, **_kwargs):
        self_route = self._resolve_mappings(_repo)[role]["model"]
        provider, model = self_route.split("/", 1)
        return role_runtime_capabilities.ImageInputCapability(
            state=role_runtime_capabilities.STATE_SUPPORTED,
            runtime=self.name,
            provider=provider,
            model=model,
            source="test runtime metadata",
            detail="fixture declares authoritative image input",
        )


class OpenCodeMultimodalTests(unittest.TestCase):
    def test_session_message_contains_ordered_image_file_parts_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.png"
            implementation = root / "implementation.png"
            reference.write_bytes(PNG + b"-reference")
            implementation.write_bytes(PNG + b"-implementation")
            message_payloads: list[dict[str, object]] = []

            def request(_base, path, *, method, payload, **_kwargs):
                if path == "/session" and method == "POST":
                    return {"id": "session-1"}
                if path.endswith("/message"):
                    assert isinstance(payload, dict)
                    message_payloads.append(payload)
                    return {
                        "info": {
                            "structured_output": {
                                "verdict": "pass",
                                "comparisons": [],
                                "ux_findings": [],
                                "repair_brief": "",
                            }
                        }
                    }
                return {}

            with (
                patch.object(opencode_structured_output, "_ephemeral_port", return_value=32123),
                patch.object(opencode_structured_output, "_wait_for_health"),
                patch.object(opencode_structured_output, "_request_json", side_effect=request),
                patch.object(opencode_structured_output, "_stop_process"),
            ):
                result = opencode_multimodal.invoke(
                    executable="opencode",
                    repo=root,
                    model="openai/vision-model",
                    prompt="compare",
                    attachments=(reference, implementation),
                    contract=ux_multimodal.MULTIMODAL_CONTRACT,
                    environment={},
                    timeout_seconds=30,
                    popen=lambda *_args, **_kwargs: _Process(),
                )

            self.assertEqual(result.value["verdict"], "pass")
            self.assertEqual(len(message_payloads), 1)
            payload = message_payloads[0]
            parts = payload["parts"]
            self.assertEqual(parts[0]["type"], "text")
            self.assertEqual(parts[1]["type"], "file")
            self.assertEqual(parts[1]["filename"], "reference.png")
            self.assertEqual(parts[2]["filename"], "implementation.png")
            prefix = "data:image/png;base64,"
            self.assertTrue(parts[1]["url"].startswith(prefix))
            decoded = base64.b64decode(parts[1]["url"][len(prefix):])
            self.assertEqual(decoded, reference.read_bytes())
            self.assertEqual(payload["format"]["type"], "json_schema")
            self.assertEqual(
                payload["format"]["schema"]["title"],
                "AutoDev multimodal UX verifier output v1",
            )

    def test_privacy_authorization_completes_before_any_image_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            reference = repo / "reference.png"
            implementation = repo / "implementation.png"
            reference.write_bytes(PNG)
            implementation.write_bytes(PNG + b"2")
            adapter = ux_multimodal_runtime.RuntimeAdapter(_OpenCodeRuntime())
            order: list[str] = []

            def authorize(*_args, **_kwargs):
                order.append("authorize")
                return SimpleNamespace()

            def invoke(**_kwargs):
                order.append("invoke-images")
                self.assertEqual(order, ["authorize", "invoke-images"])
                return opencode_structured_output.NativeStructuredOutput(
                    {
                        "verdict": "pass",
                        "comparisons": [],
                        "ux_findings": [],
                        "repair_brief": "",
                    }
                )

            with (
                patch(
                    "automation.opencode_cli.resolve_opencode_cli",
                    return_value="opencode",
                ),
                patch(
                    "automation.privacy.load_policy",
                    return_value=SimpleNamespace(enabled=True),
                ),
                patch("automation.privacy_consent.ensure_run_consent"),
                patch(
                    "automation.opencode_privacy_adapter.evaluate_role",
                    return_value=(SimpleNamespace(), {}),
                ),
                patch(
                    "automation.privacy_authorization.authorize_evaluated",
                    side_effect=authorize,
                ),
                patch(
                    "automation.opencode_multimodal.invoke",
                    side_effect=invoke,
                ),
            ):
                result = adapter.invoke_multimodal_verifier(
                    repo,
                    prompt="compare",
                    attachments=(reference, implementation),
                    contract=ux_multimodal.MULTIMODAL_CONTRACT,
                    runner=lambda *_args, **_kwargs: None,
                    which=lambda _name: "opencode",
                )

            self.assertEqual(order, ["authorize", "invoke-images"])
            self.assertEqual(result.model, "openai/vision-model")
            self.assertEqual(result.capability, ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS)

    def test_non_opencode_runtime_is_explicitly_unsupported(self) -> None:
        adapter = ux_multimodal_runtime.RuntimeAdapter(SimpleNamespace(name="other"))
        self.assertEqual(
            adapter.multimodal_verifier_capability(
                Path("."),
                runner=lambda *_args, **_kwargs: None,
            ),
            ux_multimodal.CAPABILITY_UNSUPPORTED,
        )


if __name__ == "__main__":
    unittest.main()
