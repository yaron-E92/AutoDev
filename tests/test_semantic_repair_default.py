from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "integrations" / "opencode" / "autodev.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("autodev_bridge_semantic_default", BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SemanticRepairDefaultTests(unittest.TestCase):
    def test_default_is_two(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runner = lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="")
            with patch.dict(os.environ, {}, clear=True):
                env = bridge._bridge_environment("python3", root, root, runner=runner)
        self.assertEqual(env["MAX_SEMANTIC_REPAIR_ATTEMPTS"], "2")

    def test_explicit_override_is_preserved(self):
        bridge = load_bridge()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runner = lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="")
            with patch.dict(os.environ, {"MAX_SEMANTIC_REPAIR_ATTEMPTS": "5"}, clear=True):
                env = bridge._bridge_environment("python3", root, root, runner=runner)
        self.assertEqual(env["MAX_SEMANTIC_REPAIR_ATTEMPTS"], "5")


if __name__ == "__main__":
    unittest.main()
