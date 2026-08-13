from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import (
    failure_diagnostics,
    opencode_failure_entrypoint,
    workflow_stages,
)


class FailureDiagnosticsTests(unittest.TestCase):
    def test_request_too_large_is_capability_failure(self):
        actual = failure_diagnostics.classify_provider_failure(
            "Request too large. TPM: Limit 8000, Requested 36429",
            workflow_stages.FAILURE_TRANSIENT,
        )
        self.assertEqual(actual, failure_diagnostics.FAILURE_PROVIDER_CAPABILITY)

    def test_plain_rate_limit_stays_transient(self):
        actual = failure_diagnostics.classify_provider_failure(
            "rate limit exceeded; retry later",
            workflow_stages.FAILURE_TRANSIENT,
        )
        self.assertEqual(actual, workflow_stages.FAILURE_TRANSIENT)

    def test_timestamp_duration_and_repo_path_noise_are_normalized(self):
        repo_a = Path("repo-a").resolve()
        repo_b = Path("repo-b").resolve()
        first = failure_diagnostics.local_failure_fingerprint(
            "dotnet build",
            f"{repo_a / 'Foo.cs'}: error CS1001 at 2026-08-13T10:00:00Z after 1200 ms",
            repo_a,
        )
        second = failure_diagnostics.local_failure_fingerprint(
            "dotnet build",
            f"{repo_b / 'Foo.cs'}: error CS1001 at 2026-08-13T11:30:00Z after 9 seconds",
            repo_b,
        )
        self.assertEqual(first, second)

    def test_materially_changed_error_changes_fingerprint(self):
        first = failure_diagnostics.local_failure_fingerprint(
            "dotnet build", "Foo.cs: error CS1001"
        )
        second = failure_diagnostics.local_failure_fingerprint(
            "dotnet build", "Foo.cs: error CS0101"
        )
        self.assertNotEqual(first, second)

    def test_repeated_identical_local_failure_increments_counter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_json(
                current / "state.json",
                {"LocalCheck": "dotnet build", "IssueNumber": 1},
            )
            workflow_stages.write_text(
                current / "local-check.log",
                "Foo.cs: error CS1001",
            )
            payload = {
                "event": "stage",
                "stage": "local-check",
                "state": "REPAIR",
                "reason": "deterministic verification failed",
                "failure_classification": workflow_stages.FAILURE_CODE_REPAIRABLE,
                "failure_fingerprint": "",
                "repeated_failure": False,
            }
            first = opencode_failure_entrypoint._augment_local_failure(repo, payload)
            second = opencode_failure_entrypoint._augment_local_failure(repo, payload)
            diagnostics = workflow_stages.read_json(
                current / workflow_stages.DIAGNOSTICS_FILE
            )

        self.assertTrue(first["failure_fingerprint"])
        self.assertFalse(first["repeated_failure"])
        self.assertTrue(second["repeated_failure"])
        self.assertEqual(diagnostics["repeated_identical_failures"], 1)


if __name__ == "__main__":
    unittest.main()
