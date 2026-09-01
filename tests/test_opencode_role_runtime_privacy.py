from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from automation import (
    opencode_privacy_adapter,
    opencode_role_runtime,
    privacy,
    privacy_authorization,
    privacy_consent,
    role_runtime,
)


class OpenCodeRoleRuntimePrivacyTests(unittest.TestCase):
    def _runtime(self) -> opencode_role_runtime.OpenCodeRoleRuntime:
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        runtime._mappings = {
            "reader": {
                "agent": "autodev-reader",
                "model": "vendor/model",
                "source": "explicit",
                "inherits_from": "",
            }
        }
        return runtime

    def test_run_consent_is_resolved_before_authorization_and_process_launch(self):
        runtime = self._runtime()
        order: list[str] = []
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        decision = SimpleNamespace(safe_metadata=lambda: {"outcome": "ALLOW"})

        def consent(*args, **kwargs):
            order.append("consent")

        def evaluate(*args, **kwargs):
            order.append("evidence")
            return decision, kwargs["base_env"]

        def authorize(*args, **kwargs):
            order.append("authorize")
            return decision

        def runner(*args, **kwargs):
            order.append("launch")
            return completed

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_role_runtime.opencode_cli,
            "resolve_opencode_cli",
            return_value="/usr/bin/opencode",
        ), patch.object(
            privacy,
            "load_policy",
            return_value=SimpleNamespace(enabled=True),
        ), patch.object(
            privacy_consent,
            "ensure_run_consent",
            side_effect=consent,
        ), patch.object(
            opencode_privacy_adapter,
            "evaluate_role",
            side_effect=evaluate,
        ), patch.object(
            privacy_authorization,
            "authorize_evaluated",
            side_effect=authorize,
        ):
            result = runtime.invoke(
                role_runtime.RoleInvocationContext(
                    repo=Path(temp_dir),
                    role="reader",
                    prompt="role prompt",
                ),
                runner=runner,
            )

        self.assertEqual(order, ["consent", "evidence", "authorize", "launch"])
        self.assertEqual(result.returncode, 0)

    def test_denied_run_consent_prevents_process_launch(self):
        runtime = self._runtime()
        runner = Mock()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_role_runtime.opencode_cli,
            "resolve_opencode_cli",
            return_value="/usr/bin/opencode",
        ), patch.object(
            privacy,
            "load_policy",
            return_value=SimpleNamespace(enabled=True),
        ), patch.object(
            privacy_consent,
            "ensure_run_consent",
            side_effect=privacy.PrivacyError("privacy consent denied"),
        ):
            with self.assertRaises(role_runtime.RoleRuntimeError) as raised:
                runtime.invoke(
                    role_runtime.RoleInvocationContext(
                        repo=Path(temp_dir),
                        role="reader",
                        prompt="secret repository content",
                    ),
                    runner=runner,
                )

        runner.assert_not_called()
        self.assertIn("privacy consent denied", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
