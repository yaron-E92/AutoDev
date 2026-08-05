import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib import error

from automation.ollama_cloud_preflight import (
    MIN_OLLAMA_VERSION,
    load_cloud_profile,
    run_preflight,
)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"models": []}'


class OllamaCloudPreflightTests(unittest.TestCase):
    def profile_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "providers"
            / "ollama-cloud-nemotron-minimax.json"
        )

    def test_checked_in_profile_maps_roles_and_timeouts(self):
        configs = load_cloud_profile(self.profile_path())

        self.assertEqual(configs["reader"].model, "nemotron-3-super:cloud")
        self.assertEqual(configs["synthesizer"].model, "nemotron-3-super:cloud")
        self.assertEqual(configs["planner"].model, "nemotron-3-super:cloud")
        self.assertEqual(configs["implementer"].model, "minimax-m3:cloud")
        self.assertEqual(configs["fixer"].model, "minimax-m3:cloud")
        self.assertEqual(configs["verifier"].model, "nemotron-3-super:cloud")
        self.assertEqual(configs["reader"].command, "ollama run nemotron-3-super:cloud")
        self.assertEqual(configs["implementer"].command, "ollama run minimax-m3:cloud")
        self.assertEqual(configs["reader"].timeout_seconds, 1800)
        self.assertEqual(configs["implementer"].timeout_seconds, 2400)

    def test_success_checks_each_unique_model_once(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, "ollama version is 0.12.1\n", "")
            return subprocess.CompletedProcess(argv, 0, "success\n", "")

        result = run_preflight(
            self.profile_path(),
            which=lambda name: "/usr/bin/ollama",
            run_command=fake_run,
            urlopen=lambda url, timeout: FakeResponse(),
        )

        self.assertEqual(result["status"], "success")
        pull_models = [argv[-1] for argv in calls if len(argv) > 1 and argv[1] == "pull"]
        self.assertEqual(pull_models, ["nemotron-3-super:cloud", "minimax-m3:cloud"])
        self.assertEqual(result["ollama_version"], "0.12.1")

    def test_missing_ollama_is_reported(self):
        result = run_preflight(
            self.profile_path(),
            which=lambda name: None,
        )

        self.assertEqual(result["failure_type"], "missing_ollama")

    def test_outdated_version_is_reported(self):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "ollama version is 0.11.11\n", "")

        result = run_preflight(
            self.profile_path(),
            which=lambda name: "ollama",
            run_command=fake_run,
        )

        self.assertEqual(MIN_OLLAMA_VERSION, (0, 12, 0))
        self.assertEqual(result["failure_type"], "outdated_ollama")

    def test_unreachable_service_is_reported_before_model_pulls(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "ollama version is 0.12.0\n", "")

        def unavailable(url, timeout):
            raise error.URLError("offline")

        result = run_preflight(
            self.profile_path(),
            which=lambda name: "ollama",
            run_command=fake_run,
            urlopen=unavailable,
        )

        self.assertEqual(result["failure_type"], "service_unreachable")
        self.assertFalse(any(len(argv) > 1 and argv[1] == "pull" for argv in calls))

    def test_signin_required_is_distinct(self):
        result = self.run_with_pull_failure("Error: authentication required; run ollama signin")

        self.assertEqual(result["failure_type"], "signin_required")
        self.assertIn("ollama signin", result["message"])

    def test_upgrade_required_is_distinct(self):
        result = self.run_with_pull_failure("Error: Upgrade required for this model")

        self.assertEqual(result["failure_type"], "upgrade_required")
        self.assertIn("plan upgrade", result["message"])

    def test_generic_pull_failure_is_reported(self):
        result = self.run_with_pull_failure("Error: registry temporarily unavailable")

        self.assertEqual(result["failure_type"], "model_failure")

    def test_invalid_profile_writes_no_secret_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            path.write_text('{"version": 2, "roles": {}}', encoding="utf-8")
            result = run_preflight(path, which=lambda name: "ollama")

        self.assertEqual(result["failure_type"], "invalid_profile")
        self.assertNotIn("environment", json.dumps(result).casefold())

    def run_with_pull_failure(self, message):
        def fake_run(argv, **kwargs):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, "ollama version is 0.12.0\n", "")
            return subprocess.CompletedProcess(argv, 1, "", message)

        return run_preflight(
            self.profile_path(),
            which=lambda name: "ollama",
            run_command=fake_run,
            urlopen=lambda url, timeout: FakeResponse(),
        )


if __name__ == "__main__":
    unittest.main()
