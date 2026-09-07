from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import role_output_contract, role_runtime


class _NoStructuredRuntime:
    name = "plain"


class _NativeRuntime:
    name = "native"

    def structured_output_capability(self, context, *, runner, which=None):
        self.context = context
        return role_output_contract.CAPABILITY_NATIVE_VALIDATED


class _BadRuntime:
    name = "bad"

    def structured_output_capability(self, context, *, runner, which=None):
        return "provider-specific-magic"


class RoleRuntimeStructuredOutputTests(unittest.TestCase):
    def test_runtime_without_capability_is_unsupported_not_broken(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = role_runtime.RoleInvocationContext(
                repo=Path(temp_dir),
                role="verifier",
                prompt="verify",
                output_contract=role_output_contract.contract_for_role("verifier"),
            )
            capability = role_runtime.structured_output_capability(
                _NoStructuredRuntime(), context, runner=lambda *a, **k: None
            )
            self.assertEqual(capability, role_output_contract.CAPABILITY_UNSUPPORTED)

    def test_runtime_reports_provider_neutral_capability(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = _NativeRuntime()
            context = role_runtime.RoleInvocationContext(
                repo=Path(temp_dir),
                role="verifier",
                prompt="verify",
                output_contract=role_output_contract.contract_for_role("verifier"),
                ux_context_fingerprint="autodev-owned-fingerprint",
            )
            capability = role_runtime.structured_output_capability(
                runtime, context, runner=lambda *a, **k: None
            )
            self.assertEqual(capability, role_output_contract.CAPABILITY_NATIVE_VALIDATED)
            self.assertEqual(runtime.context.ux_context_fingerprint, "autodev-owned-fingerprint")

    def test_unknown_capability_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = role_runtime.RoleInvocationContext(
                repo=Path(temp_dir),
                role="verifier",
                prompt="verify",
                output_contract=role_output_contract.contract_for_role("verifier"),
            )
            with self.assertRaises(role_runtime.RoleRuntimeError):
                role_runtime.structured_output_capability(
                    _BadRuntime(), context, runner=lambda *a, **k: None
                )


if __name__ == "__main__":
    unittest.main()
