import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace



class OpenCodeCliResolutionTests(unittest.TestCase):
    def test_resolved_cli_path_is_used_for_introspection(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=json.dumps({}), stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            opencode_adapter_models.resolve_opencode_model_mappings(
                Path(temp_dir),
                runner=runner,
                which=lambda command: r"C:\\Users\\user\\AppData\\Roaming\\npm\\opencode.CMD",
            )

        self.assertEqual(
            calls,
            [[r"C:\\Users\\user\\AppData\\Roaming\\npm\\opencode.CMD", "debug", "config"]],
        )

    def test_missing_cli_has_specific_discovery_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(opencode_adapter_contract.OpenCodeAdapterError) as raised:
                opencode_adapter_models.resolve_opencode_model_mappings(
                    Path(temp_dir),
                    runner=lambda *args, **kwargs: None,
                    which=lambda command: None,
                )

        self.assertIn("not found on PATH", str(raised.exception))

    def test_resolved_but_unlaunchable_cli_has_specific_launch_error(self):
        def runner(command, **kwargs):
            raise FileNotFoundError(2, "The system cannot find the file specified")

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(opencode_adapter_contract.OpenCodeAdapterError) as raised:
                opencode_adapter_models.resolve_opencode_model_mappings(
                    Path(temp_dir),
                    runner=runner,
                    which=lambda command: r"C:\\npm\\opencode.CMD",
                )

        message = str(raised.exception)
        self.assertIn("resolved to", message)
        self.assertIn("could not be launched", message)
        self.assertIn("opencode.CMD", message)

    def test_nonzero_debug_config_reports_exit_code_and_stderr(self):
        def runner(command, **kwargs):
            return SimpleNamespace(returncode=7, stdout="", stderr="bad OpenCode config")

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(opencode_adapter_contract.OpenCodeAdapterError) as raised:
                opencode_adapter_models.resolve_opencode_model_mappings(
                    Path(temp_dir),
                    runner=runner,
                    which=lambda command: "/usr/local/bin/opencode",
                )

        message = str(raised.exception)
        self.assertIn("exit code 7", message)
        self.assertIn("bad OpenCode config", message)


if __name__ == "__main__":
    unittest.main()

from automation import opencode_adapter_contract

from automation import opencode_adapter_models
