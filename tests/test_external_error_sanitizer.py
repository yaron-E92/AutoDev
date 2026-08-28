from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    external_error_sanitizer,
    non_success_report,
    opencode_resume_status,
    role_coordinator_contract,
    role_coordinator_runtime,
    role_runtime,
    role_runtime_diagnostics,
    run_manifest,
    workflow_github,
    workflow_stages,
)
from automation.role_coordinator_stages import terminal_payload


SENTINELS = {
    "bearer": "BEARER-SENTINEL-225",
    "turn": "TURNSTATE-SENTINEL-225",
    "cookie": "COOKIE-SENTINEL-225",
    "api": "APIKEY-SENTINEL-225",
    "proxy": "PROXY-SENTINEL-225",
    "signature": "SIGNATURE-SENTINEL-225",
    "account": "ACCOUNT-SENTINEL-225",
}


def provider_failure_text() -> str:
    return (
        "UpstreamProviderError: request failed\n"
        "headers={"
        f"'Authorization': 'Bearer {SENTINELS['bearer']}', "
        f"'X-Codex-Turn-State': '{SENTINELS['turn']}', "
        f"'Set-Cookie': 'session={SENTINELS['cookie']}; Path=/', "
        f"'X-Api-Key': '{SENTINELS['api']}', "
        f"'Proxy-Authorization': 'Basic {SENTINELS['proxy']}', "
        f"'X-Account-Metadata': '{SENTINELS['account']}'"
        "}\n"
        "retry URL: "
        "https://provider.example/v1/run?"
        f"token={SENTINELS['api']}&X-Amz-Signature={SENTINELS['signature']}&safe=visible\n"
        "provider message: service temporarily unavailable"
    )


class NestedProviderException(RuntimeError):
    def __str__(self) -> str:
        return (
            "NestedProviderException(response={"
            f"'headers': {{'Cookie': 'sid={SENTINELS['cookie']}', "
            f"'X-Codex-Turn-State': '{SENTINELS['turn']}'}}"
            "}, message='temporary provider failure')"
        )


class _FailingVerifierRuntime:
    name = "opencode"

    def invoke(self, context, *, runner, which=None):
        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=73,
            elapsed_ms=321,
            stdout="",
            stderr=provider_failure_text(),
            termination="runtime-nonzero",
            model="provider/verifier",
        )


class ExternalErrorSanitizerTests(unittest.TestCase):
    def assertNoSentinels(self, text: str) -> None:
        for value in SENTINELS.values():
            self.assertNotIn(value, text)

    def test_header_maps_sensitive_headers_and_signed_urls_are_redacted(self):
        sanitized = external_error_sanitizer.sanitize_external_text(
            provider_failure_text()
        )

        self.assertNoSentinels(sanitized)
        self.assertIn("headers=<redacted>", sanitized)
        self.assertIn("safe=visible", sanitized)
        self.assertIn("service temporarily unavailable", sanitized)

    def test_case_insensitive_header_names_outside_map_are_redacted(self):
        text = (
            f"aUtHoRiZaTiOn: Bearer {SENTINELS['bearer']}\n"
            f"x-CoDeX-TuRn-StAtE={SENTINELS['turn']}\n"
            f"COOKIE: sid={SENTINELS['cookie']}\n"
            f"x-api-key: {SENTINELS['api']}\n"
            f"proxy-authorization: Basic {SENTINELS['proxy']}\n"
        )
        sanitized = external_error_sanitizer.sanitize_external_text(text)

        self.assertNoSentinels(sanitized)
        self.assertIn("x-CoDeX-TuRn-StAtE=<redacted>", sanitized)

    def test_nested_exception_string_drops_header_state(self):
        sanitized = external_error_sanitizer.sanitize_external_text(
            NestedProviderException()
        )

        self.assertNoSentinels(sanitized)
        self.assertIn("<redacted>", sanitized)

    def test_controls_lines_and_characters_are_bounded_with_marker(self):
        raw = "\x00\x01first\n" + "\n".join(
            f"line-{index}-" + ("x" * 90) for index in range(30)
        )
        sanitized = external_error_sanitizer.sanitize_external_text(
            raw,
            max_chars=320,
            max_lines=6,
        )

        self.assertNotIn("\x00", sanitized)
        self.assertNotIn("\x01", sanitized)
        self.assertLessEqual(len(sanitized), 320)
        self.assertLessEqual(len(sanitized.splitlines()), 6)
        self.assertIn(external_error_sanitizer.TRUNCATION_MARKER, sanitized)

    def test_structured_error_is_allowlisted(self):
        error = external_error_sanitizer.safe_external_error(
            category="runtime-nonzero",
            message=provider_failure_text(),
            role="verifier",
            runtime="opencode",
            phase="work",
            returncode=73,
            retry_classification=workflow_stages.FAILURE_TRANSIENT,
            termination="runtime-nonzero",
        )
        payload = error.to_json()

        self.assertEqual(
            set(payload),
            {
                "category",
                "message",
                "role",
                "runtime",
                "phase",
                "returncode",
                "retry_classification",
                "termination",
            },
        )
        self.assertNoSentinels(json.dumps(payload, sort_keys=True))


class ProviderFailureBoundaryTests(unittest.TestCase):
    def assertNoSentinels(self, text: str) -> None:
        for value in SENTINELS.values():
            self.assertNotIn(value, text)

    def _repo(self, root: str) -> tuple[Path, Path, dict[str, object]]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state = {
            "IssueNumber": 225,
            "Status": "Patched",
            "RepoFullName": "owner/repo",
            "BranchName": "autodev/issue-225",
            "BaseSha": "base-sha",
            "BaseTreeSha": "base-tree",
            "PreparedSnapshotHash": "snapshot",
            "VerifiedSourceIdentity": "verified-source",
            "VerifiedParentSha": "base-sha",
            "LastCommitSha": "",
            "PrUrl": "",
            "PrNumber": 0,
            "PrHeadSha": "",
            "AcceptedRoleArtifacts": {},
        }
        workflow_stages.write_json(current / "state.json", state)
        workflow_stages.write_json(
            current / workflow_stages.DIAGNOSTICS_FILE,
            {"role_invocations": {"verifier": 1}},
        )
        (current / "issue.md").write_text("# Issue 225\n", encoding="utf-8")
        (current / "local-check.log").write_text("verification passed\n", encoding="utf-8")

        manifest_path = current / run_manifest.MANIFEST_NAME
        run_manifest.create_manifest(
            manifest_path,
            repo_path=repo,
            github_repo="owner/repo",
            issue_number=225,
            mode="issue-to-pr",
            base_sha="base-sha",
            branch="autodev/issue-225",
            role_snapshots={},
        )
        for stage in (
            "issue-selected",
            "repository-read",
            "handoff-synthesized",
            "plan-created",
            "implementation-generated",
            "patch-applied",
        ):
            run_manifest.complete_stage(
                manifest_path,
                stage,
                run_root=current,
                details={
                    "source_identity": "verified-source",
                    "parent_sha": "base-sha",
                },
            )
        run_manifest.complete_stage(
            manifest_path,
            "deterministic-verified",
            run_root=current,
            artifacts=[current / "local-check.log"],
            details={
                "attempt": 0,
                "source_identity": "verified-source",
                "parent_sha": "base-sha",
            },
        )
        return repo, current, state

    def test_verifier_provider_failure_is_sanitized_across_all_durable_surfaces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(temp_dir)
            snapshots = {
                "verifier": role_runtime.build_role_snapshot(
                    runtime="opencode",
                    role="verifier",
                    configured={"model": "provider/verifier"},
                )
            }

            with self.assertRaises(
                role_coordinator_contract.RoleCoordinatorError
            ) as raised:
                role_coordinator_runtime.run_role(
                    repo,
                    "verifier",
                    _FailingVerifierRuntime(),
                    snapshots,
                    already_prepared=True,
                    runner=lambda *_args, **_kwargs: SimpleNamespace(
                        returncode=0,
                        stdout="",
                        stderr="",
                    ),
                    which=lambda _name: "/usr/bin/opencode",
                )

            self.assertEqual(
                raised.exception.classification,
                workflow_stages.FAILURE_TRANSIENT,
            )
            self.assertNoSentinels(str(raised.exception))

            payload = terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "failed_stage": "verifier",
                    "reason": str(raised.exception),
                    "failure_classification": raised.exception.classification,
                    "artifact": raised.exception.diagnostic_path,
                },
                arguments="225",
            )
            payload, _ = non_success_report.update_report(repo, payload)

            rendered_payload = json.dumps(payload, sort_keys=True)
            self.assertNoSentinels(rendered_payload)
            self.assertEqual(
                payload["failure_classification"],
                workflow_stages.FAILURE_TRANSIENT,
            )

            attempt_path = repo / raised.exception.diagnostic_path
            attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
            self.assertEqual(attempt["role"], "verifier")
            self.assertEqual(attempt["runtime"], "opencode")
            self.assertEqual(attempt["returncode"], 73)
            self.assertEqual(
                attempt["external_error"]["retry_classification"],
                workflow_stages.FAILURE_TRANSIENT,
            )
            self.assertNoSentinels(json.dumps(attempt, sort_keys=True))

            manifest = run_manifest.load_manifest(
                current / run_manifest.MANIFEST_NAME
            )
            self.assertEqual(
                manifest["failure"]["classification"],
                workflow_stages.FAILURE_TRANSIENT,
            )
            self.assertNoSentinels(json.dumps(manifest, sort_keys=True))
            self.assertNoSentinels(run_manifest.render_status(manifest))

            report = (current / non_success_report.REPORT_NAME).read_text(
                encoding="utf-8"
            )
            self.assertNoSentinels(report)
            self.assertIn("transient", report.casefold())

            for path in current.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertNoSentinels(text)

            resumed_manifest = run_manifest.load_manifest(
                current / run_manifest.MANIFEST_NAME
            )
            self.assertEqual(
                opencode_resume_status.resume_action(resumed_manifest, state),
                "verifier",
            )
            self.assertEqual(
                opencode_resume_status.repair_attempts(resumed_manifest)[
                    "semantic"
                ],
                0,
            )

    def test_blocked_github_comment_uses_same_sanitizer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(temp_dir)
            calls: list[list[str]] = []

            def runner(argv, **_kwargs):
                calls.append([str(value) for value in argv])
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            workflow_github.mark_blocked(
                current,
                state,
                provider_failure_text(),
                runner=runner,
            )

            comment = next(
                argv[argv.index("--body") + 1]
                for argv in calls
                if "comment" in argv and "--body" in argv
            )
            self.assertNoSentinels(comment)
            self.assertIn("<redacted>", comment)


if __name__ == "__main__":
    unittest.main()
