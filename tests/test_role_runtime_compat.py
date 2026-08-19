from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import (
    opencode_adapter,
    opencode_resume,
    opencode_role_runtime,
    role_runtime,
)


class RoleRuntimeCompatibilityTests(unittest.TestCase):
    def test_opencode_runtime_snapshots_match_legacy_resume_fingerprints(self):
        mappings = {
            role: {
                "agent": f"autodev-{role}",
                "model": f"vendor/{role}",
                "source": "explicit",
                "inherits_from": "",
            }
            for role in opencode_adapter.ROLE_NAMES
        }
        expected = opencode_resume.role_snapshots(mappings)
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        with patch.object(
            opencode_adapter,
            "resolve_opencode_model_mappings",
            return_value=mappings,
        ):
            actual = runtime.role_snapshots(
                Path("."),
                runner=lambda *args, **kwargs: None,
            )
        self.assertEqual(actual, expected)

    def test_user_config_is_below_repository_config_and_above_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            user_config = root / "user-config.json"
            user_config.write_text(
                json.dumps({"role_runtime": "user-runtime"}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {role_runtime.USER_CONFIG_ENV: str(user_config)},
                clear=True,
            ):
                self.assertEqual(
                    role_runtime.resolve_runtime_name(repo)[0],
                    "user-runtime",
                )
                repo_config = repo / ".autodev" / "config.json"
                repo_config.parent.mkdir(parents=True)
                repo_config.write_text(
                    json.dumps({"role_runtime": "repo-runtime"}),
                    encoding="utf-8",
                )
                self.assertEqual(
                    role_runtime.resolve_runtime_name(repo)[0],
                    "repo-runtime",
                )

    def test_opencode_runtime_owns_legacy_per_run_model_override_rejection(self):
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        with self.assertRaises(opencode_adapter.OpenCodeAdapterError):
            runtime.validate_arguments("123 --model vendor/model")


if __name__ == "__main__":
    unittest.main()
