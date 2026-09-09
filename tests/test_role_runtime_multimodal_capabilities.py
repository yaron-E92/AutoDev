from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_model_capabilities,
    role_runtime_capabilities,
    ux_multimodal,
    ux_multimodal_runtime,
)


class _RouteRuntime:
    name = "opencode"

    def __init__(self, route: str) -> None:
        self.route = route

    def _resolve_mappings(self, _repo, **_kwargs):
        return {
            "verifier": {
                "agent": "autodev-verifier",
                "model": self.route,
                "source": "test",
                "inherits_from": "",
            }
        }


class _HookRuntime:
    name = "fake-runtime"

    def __init__(self, state: str = role_runtime_capabilities.STATE_SUPPORTED) -> None:
        self.state = state

    def image_input_capability(self, _repo, *, role, **_kwargs):
        self.assert_role = role
        return role_runtime_capabilities.ImageInputCapability(
            state=self.state,
            runtime=self.name,
            provider="fake-provider",
            model="fake-model",
            source="fake runtime capability metadata",
            detail="model-free fixture",
        )


class _MutableSupportedRuntime(_RouteRuntime):
    def image_input_capability(self, _repo, *, role, **_kwargs):
        provider, model = self.route.split("/", 1)
        return role_runtime_capabilities.ImageInputCapability(
            state=role_runtime_capabilities.STATE_SUPPORTED,
            runtime=self.name,
            provider=provider,
            model=model,
            source="mutable test runtime metadata",
            detail=f"authoritative for {role}",
        )


class RuntimeMultimodalCapabilityTests(unittest.TestCase):
    @staticmethod
    def _verbose(route: str, image: object = True) -> str:
        metadata: dict[str, object] = {
            "id": route.split("/", 1)[1],
            "capabilities": {"input": {}},
        }
        if image is not None:
            metadata["capabilities"]["input"]["image"] = image  # type: ignore[index]
        return route + "\n" + json.dumps(metadata, indent=2) + "\n"

    @staticmethod
    def _runner(stdout: str):
        def runner(command, **_kwargs):
            if command[1:3] != ["models", "openai"]:
                raise AssertionError(f"unexpected command: {command}")
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        return runner

    def test_opencode_catalog_reports_supported_effective_route(self) -> None:
        runtime = _RouteRuntime("openai/vision")
        with patch.object(
            opencode_model_capabilities.opencode_adapter_models,
            "resolve_opencode_config",
            return_value={},
        ):
            evidence = opencode_model_capabilities.resolve_image_input_capability(
                runtime,
                Path("."),
                role="verifier",
                runner=self._runner(self._verbose("openai/vision", True)),
                which=lambda _name: "opencode",
            )

        self.assertEqual(evidence.state, role_runtime_capabilities.STATE_SUPPORTED)
        self.assertEqual(evidence.route, "openai/vision")
        self.assertEqual(evidence.source, opencode_model_capabilities.SOURCE_CATALOG)
        self.assertTrue(evidence.metadata_sha256)

    def test_opencode_catalog_reports_unsupported_effective_route(self) -> None:
        runtime = _RouteRuntime("openai/text")
        with patch.object(
            opencode_model_capabilities.opencode_adapter_models,
            "resolve_opencode_config",
            return_value={},
        ):
            evidence = opencode_model_capabilities.resolve_image_input_capability(
                runtime,
                Path("."),
                role="verifier",
                runner=self._runner(self._verbose("openai/text", False)),
                which=lambda _name: "opencode",
            )

        self.assertEqual(evidence.state, role_runtime_capabilities.STATE_UNSUPPORTED)
        self.assertEqual(evidence.route, "openai/text")

    def test_missing_boolean_capability_is_unknown_not_supported(self) -> None:
        runtime = _RouteRuntime("openai/ambiguous")
        with patch.object(
            opencode_model_capabilities.opencode_adapter_models,
            "resolve_opencode_config",
            return_value={},
        ):
            evidence = opencode_model_capabilities.resolve_image_input_capability(
                runtime,
                Path("."),
                role="verifier",
                runner=self._runner(self._verbose("openai/ambiguous", None)),
                which=lambda _name: "opencode",
            )

        self.assertEqual(evidence.state, role_runtime_capabilities.STATE_UNKNOWN)
        self.assertIn("capabilities.input.image", evidence.detail)

    def test_explicit_modalities_are_authoritative_without_catalog_guessing(self) -> None:
        runtime = _RouteRuntime("openai/custom-vision")
        config = {
            "provider": {
                "openai": {
                    "models": {
                        "custom-vision": {
                            "modalities": {
                                "input": ["text", "image"],
                                "output": ["text"],
                            }
                        }
                    }
                }
            }
        }

        def forbidden_runner(*_args, **_kwargs):
            raise AssertionError("explicit modalities must not require model-catalog probing")

        with patch.object(
            opencode_model_capabilities.opencode_adapter_models,
            "resolve_opencode_config",
            return_value=config,
        ):
            evidence = opencode_model_capabilities.resolve_image_input_capability(
                runtime,
                Path("."),
                role="verifier",
                runner=forbidden_runner,
                which=lambda _name: "opencode",
            )

        self.assertEqual(evidence.state, role_runtime_capabilities.STATE_SUPPORTED)
        self.assertEqual(evidence.source, opencode_model_capabilities.SOURCE_EXPLICIT)

    def test_explicit_custom_model_without_modalities_is_unknown_even_if_fallback_looks_visual(self) -> None:
        runtime = _RouteRuntime("openai/custom")
        config = {
            "provider": {
                "openai": {
                    "models": {
                        "custom": {
                            "name": "Custom model without declared modalities",
                        }
                    }
                }
            }
        }

        def forbidden_runner(*_args, **_kwargs):
            raise AssertionError("fallback catalog metadata must not override missing explicit modalities")

        with patch.object(
            opencode_model_capabilities.opencode_adapter_models,
            "resolve_opencode_config",
            return_value=config,
        ):
            evidence = opencode_model_capabilities.resolve_image_input_capability(
                runtime,
                Path("."),
                role="verifier",
                runner=forbidden_runner,
                which=lambda _name: "opencode",
            )

        self.assertEqual(evidence.state, role_runtime_capabilities.STATE_UNKNOWN)
        self.assertIn("fallback assumptions", evidence.detail)

    def test_provider_neutral_runtime_hook_uses_same_capability_contract(self) -> None:
        runtime = _HookRuntime()
        evidence = role_runtime_capabilities.image_input_capability(
            runtime,
            Path("."),
            role="verifier",
            runner=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(runtime.assert_role, "verifier")
        self.assertEqual(evidence.state, role_runtime_capabilities.STATE_SUPPORTED)
        self.assertEqual(evidence.runtime, "fake-runtime")
        self.assertEqual(evidence.route, "fake-provider/fake-model")

    def test_capability_evidence_is_persisted_with_source_and_route_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".autodev-run" / "current").mkdir(parents=True)
            adapter = ux_multimodal_runtime.RuntimeAdapter(_HookRuntime())

            capability = adapter.multimodal_verifier_capability(
                repo,
                runner=lambda *_args, **_kwargs: None,
            )

            self.assertEqual(capability, ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS)
            path = repo / ".autodev-run" / "current" / role_runtime_capabilities.EVIDENCE_FILE
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "supported")
            self.assertEqual(payload["route"], "fake-provider/fake-model")
            self.assertEqual(payload["source"], "fake runtime capability metadata")
            self.assertTrue(payload["fingerprint"])

    def test_unknown_runtime_capability_fails_closed_before_image_invocation(self) -> None:
        runtime = _HookRuntime(role_runtime_capabilities.STATE_UNKNOWN)
        adapter = ux_multimodal_runtime.RuntimeAdapter(runtime)
        capability = adapter.multimodal_verifier_capability(
            Path("."),
            runner=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(capability, ux_multimodal.CAPABILITY_UNSUPPORTED)
        with self.assertRaisesRegex(
            ux_multimodal_runtime.UXMultimodalRuntimeError,
            "image-input capability is unknown",
        ):
            adapter._assert_capability_route("fake-provider/fake-model")

    def test_stale_effective_route_is_rejected_before_image_submission(self) -> None:
        runtime = _MutableSupportedRuntime("openai/vision-a")
        adapter = ux_multimodal_runtime.RuntimeAdapter(runtime)
        self.assertEqual(
            adapter.multimodal_verifier_capability(
                Path("."),
                runner=lambda *_args, **_kwargs: None,
            ),
            ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
        )

        runtime.route = "openai/vision-b"
        with self.assertRaisesRegex(
            ux_multimodal_runtime.UXMultimodalRuntimeError,
            "route changed after image-input capability discovery",
        ):
            adapter.invoke_multimodal_verifier(
                Path("."),
                prompt="compare",
                attachments=(),
                contract=ux_multimodal.MULTIMODAL_CONTRACT,
                runner=lambda *_args, **_kwargs: None,
                which=lambda _name: "opencode",
            )


if __name__ == "__main__":
    unittest.main()
