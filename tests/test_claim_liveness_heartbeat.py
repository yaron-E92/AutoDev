from __future__ import annotations

import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from automation import claim_contract, claim_lease


class ActiveInvocationHeartbeatTests(unittest.TestCase):
    def test_long_single_invocation_heartbeats_do_not_consume_no_progress_budget(self):
        owned = claim_contract.Claim(
            repository="owner/repo",
            issue_number=42,
            worker_id="worker-a",
            run_id="run-42",
            claim_id="claim-42",
            acquired_at="2026-09-05T12:00:00Z",
            heartbeat_at="2026-09-05T12:00:00Z",
            lease_seconds=900,
            ref=claim_contract.claim_ref(42),
            sha="a" * 40,
            progress_id="b" * 64,
            progress_at="2026-09-05T12:00:00Z",
            progress_summary="status=WaitingForCI; progress=bbbbbbbbbbbb",
            no_progress_attempts=3,
        )
        calls = 0

        def renew(_repo, current, **_kwargs):
            nonlocal calls
            calls += 1
            return replace(
                current,
                heartbeat_at=f"2026-09-05T12:00:{calls:02d}Z",
                sha=(f"{calls:x}" * 40)[:40],
            )

        with patch.object(claim_lease, "renew_claim", side_effect=renew):
            with claim_lease.HeartbeatLease(
                Path("."),
                owned,
                interval_seconds=0.01,
            ) as lease:
                time.sleep(0.055)
            latest = lease.latest_claim()

        self.assertGreaterEqual(calls, 2)
        self.assertEqual(latest.progress_id, owned.progress_id)
        self.assertEqual(latest.progress_at, owned.progress_at)
        self.assertEqual(latest.no_progress_attempts, owned.no_progress_attempts)
        self.assertNotEqual(latest.heartbeat_at, owned.heartbeat_at)


class ClaimLivenessDocumentationTests(unittest.TestCase):
    def test_liveness_doc_distinguishes_heartbeat_progress_and_supported_recovery(self):
        text = (Path(__file__).parents[1] / "docs" / "claim-liveness.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "Lease freshness is not workflow progress",
            "claim_max_no_progress_attempts",
            "claim_max_no_progress_minutes",
            "RUN_STALLED",
            "STALE_PROTECTED",
            "autodev resume",
            "Do not delete",
            "--force-with-lease",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
