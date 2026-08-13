from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
