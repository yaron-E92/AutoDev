from __future__ import annotations

import unittest
from pathlib import Path

from automation import failure_diagnostics, workflow_stages


class FailureDiagnosticsTests(unittest.TestCase):
    def test_request_too_large_is_capability_failure(self):
        actual = failure_diagnostics.classify_provider_failure(
            "Request too large. TPM: Limit 8000, Requested 36429",
            workflow_stages.FAILURE_TRANSIENT,
        )
        self.assertEqual(
            actual,
            failure_diagnostics.FAILURE_PROVIDER_CAPABILITY,
        )

    def test_plain_rate_limit_stays_transient(self):
        actual = failure_diagnostics.classify_provider_failure(
            "rate limit exceeded; retry later",
            workflow_stages.FAILURE_TRANSIENT,
        )
        self.assertEqual(actual, workflow_stages.FAILURE_TRANSIENT)

    def test_timestamp_duration_and_repo_path_noise_are_normalized(self):
        first = failure_diagnostics.local_failure_fingerprint(
            "dotnet build",
            "/repo/a/Foo.cs: error CS1001 at 2026-08-13T10:00:00Z after 1200 ms",
            Path("/repo/a"),
        )
        second = failure_diagnostics.local_failure_fingerprint(
            "dotnet build",
            "/repo/b/Foo.cs: error CS1001 at 2026-08-13T11:30:00Z after 9 seconds",
            Path("/repo/b"),
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


if __name__ == "__main__":
    unittest.main()
