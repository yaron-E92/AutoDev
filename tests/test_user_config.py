from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import user_config


class UserConfigTests(unittest.TestCase):
    def test_profile_round_trip_and_global_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            value: dict[str, object] = {}
            value = user_config.set_model_profile(
                value,
                "mixed",
                {
                    "reader": "ollama/gpt-oss:20b-autodev",
                    "planner": "openai/gpt-5.6-terra",
                },
            )
            value = user_config.select_profile(value, "mixed")
            user_config.save(value, path)
            loaded = user_config.load(path)
            self.assertEqual(loaded["version"], 1)
            self.assertEqual(loaded["active_model_profile"], "mixed")

    def test_existing_runtime_only_config_remains_valid_without_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text('{"role_runtime":"opencode"}\n', encoding="utf-8")
            self.assertEqual(user_config.load(path)["role_runtime"], "opencode")

    def test_remote_identity_accepts_ssh_and_https(self) -> None:
        expected = "yaron-E92/PHOODAB"
        self.assertEqual(
            user_config.github_repository_from_remote("git@github.com:yaron-E92/PHOODAB.git"),
            expected,
        )
        self.assertEqual(
            user_config.github_repository_from_remote("https://github.com/yaron-E92/PHOODAB.git"),
            expected,
        )
        self.assertEqual(
            user_config.github_repository_from_remote("ssh://git@github.com/yaron-E92/PHOODAB.git"),
            expected,
        )

    def test_repository_selection_overrides_user_default(self) -> None:
        value: dict[str, object] = {
            "model_profiles": {
                "local": {"reader": "ollama/local"},
                "mixed": {"reader": "openai/cloud"},
            },
            "active_model_profile": "local",
            "repositories": {"yaron-E92/PHOODAB": {"model_profile": "mixed"}},
        }
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": "git@github.com:yaron-E92/PHOODAB.git\n",
        })()
        name, source, models = user_config.effective_model_profile(
            Path("."),
            value=value,
            runner=lambda *args, **kwargs: completed,
        )
        self.assertEqual((name, source), ("mixed", "repository:yaron-E92/PHOODAB"))
        self.assertEqual(models["reader"], "openai/cloud")

    def test_invalid_model_is_rejected(self) -> None:
        with self.assertRaises(user_config.UserConfigError):
            user_config.set_model_profile({}, "bad", {"reader": "not-a-route"})


if __name__ == "__main__":
    unittest.main()
