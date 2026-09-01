from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import opencode_adapter_models


class ModelProfileTests(unittest.TestCase):
    def test_profile_fills_inherited_roles_but_not_explicit_opencode_roles(self) -> None:
        mappings = {
            "reader": {
                "agent": "autodev-reader",
                "source": "explicit",
                "model": "openai/repo-reader",
                "inherits_from": "",
            },
            "planner": {
                "agent": "autodev-planner",
                "source": "inherited",
                "model": "openai/global",
                "inherits_from": "OpenCode global/default model",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.json"
            config.write_text(
                json.dumps({
                    "version": 1,
                    "active_model_profile": "mixed",
                    "model_profiles": {
                        "mixed": {
                            "reader": "ollama/profile-reader",
                            "planner": "openai/profile-planner",
                        }
                    },
                }),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"AUTODEV_USER_CONFIG": str(config)}, clear=False):
                resolved = opencode_adapter_models.apply_autodev_model_profile(
                    Path(temp_dir), mappings
                )
        self.assertEqual(resolved["reader"]["model"], "openai/repo-reader")
        self.assertEqual(resolved["reader"]["source"], "explicit")
        self.assertEqual(resolved["planner"]["model"], "openai/profile-planner")
        self.assertEqual(resolved["planner"]["source"], "autodev-profile:user:mixed")


if __name__ == "__main__":
    unittest.main()
