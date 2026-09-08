from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from automation import (
    opencode_adapter_protocol,
    opencode_adapter_roles,
    opencode_resume_status,
    revision_hooks,
    role_coordinator_flow,
    run_manifest,
    workflow_stages,
)


class RevisionHooksTests(unittest.TestCase):
    def _install_with_patches(self, repo: Path):
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True, exist_ok=True)
        prompt = current / "synthesizer.md"

        def prepare(role, resolved, arguments, **kwargs):
            prompt.write_text("base prompt\n", encoding="utf-8")
            return prompt

        def accept_once(role, active, input_path):
            handoff = active / "synthesized-handoff.md"
            handoff.write_text("revised handoff\n", encoding="utf-8")
            return [handoff]

        stack = [
            patch.object(opencode_adapter_roles, "prepare_role", side_effect=prepare),
            patch.object(opencode_adapter_roles, "_accept_role_once", side_effect=accept_once),
            patch.object(opencode_adapter_protocol, "_mark_role_accepted"),
            patch.object(opencode_adapter_roles, "_mark_role_accepted"),
            patch.object(
                opencode_resume_status,
                "_resume_problems",
                return_value=[revision_hooks.PRE_PATCH_WORKTREE_BLOCKER],
            ),
            patch.object(opencode_resume_status, "status_text", return_value="base status\n"),
            patch.object(role_coordinator_flow, "coordinate", return_value={"state": "PR_READY"}),
        ]
        entered = [item.start() for item in stack]
        # MagicMock fabricates arbitrary attributes on access, so the hook
        # idempotency marker would otherwise look truthy even though these
        # freshly patched callables have never been wrapped. Make the test seam
        # explicit so revision_hooks.install() exercises the real wrappers.
        for mocked in entered:
            mocked._autodev_revision = False
        self.addCleanup(lambda: [item.stop() for item in reversed(stack)])
        revision_hooks.install()
        return prompt, entered

    def test_active_revision_context_is_appended_to_prepared_role_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            prompt, _ = self._install_with_patches(repo)
            with patch.object(
                revision_hooks.revision,
                "role_prompt_context",
                return_value="\n# AutoDev operator-directed revision\nUse the delta.\n",
            ):
                path = opencode_adapter_roles.prepare_role("synthesizer", repo, "42")

            self.assertEqual(path, prompt)
            rendered = prompt.read_text(encoding="utf-8")
            self.assertIn("base prompt", rendered)
            self.assertIn("operator-directed revision", rendered)
            self.assertIn("Use the delta.", rendered)

    def test_synthesizer_acceptance_checks_operator_authority_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            self._install_with_patches(repo)
            with patch.object(
                revision_hooks.revision,
                "validate_synthesizer_result",
            ) as validate:
                outputs = opencode_adapter_roles._accept_role_once(
                    "synthesizer",
                    current,
                    None,
                )

            self.assertEqual(outputs, [current / "synthesized-handoff.md"])
            validate.assert_called_once_with(repo.resolve(), "revised handoff\n")

    def test_accepted_revision_role_records_effective_manifest_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            _, entered = self._install_with_patches(repo)
            original_mark = entered[2]
            with patch.object(
                revision_hooks.revision,
                "load_active",
                return_value={"status": "active", "revision_id": "r1"},
            ), patch.object(
                revision_hooks.run_manifest,
                "load_manifest",
                return_value={
                    "roles": {
                        "planner": {
                            "fingerprint": "planner-fp",
                            "safe_metadata": {},
                        }
                    }
                },
            ), patch.object(
                revision_hooks.revision,
                "record_role_use",
            ) as record:
                opencode_adapter_protocol._mark_role_accepted(current, "planner", [])

            original_mark.assert_called_once_with(current, "planner", [])
            record.assert_called_once_with(
                repo.resolve(),
                "planner",
                {"fingerprint": "planner-fp", "safe_metadata": {}},
            )

    def test_revision_start_worktree_is_allowed_but_later_drift_is_not(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            self._install_with_patches(repo)
            manifest = {"completed_stages": []}
            active = {
                "status": "active",
                "revision_id": "r1",
                "implementation_source_identity": "source-at-revision",
            }
            with patch.object(revision_hooks.revision, "load_active", return_value=active), patch.object(
                revision_hooks.workflow_stages,
                "read_state",
                return_value={},
            ), patch.object(
                revision_hooks.workflow_stages,
                "source_identity",
                return_value={"identity": "source-at-revision"},
            ):
                problems = opencode_resume_status._resume_problems(
                    repo,
                    current,
                    manifest,
                    {},
                    runner=MagicMock(),
                    validate_remote=False,
                )
            self.assertEqual(problems, [])

            with patch.object(revision_hooks.revision, "load_active", return_value=active), patch.object(
                revision_hooks.workflow_stages,
                "read_state",
                return_value={},
            ), patch.object(
                revision_hooks.workflow_stages,
                "source_identity",
                return_value={"identity": "unexpected-drift"},
            ):
                problems = opencode_resume_status._resume_problems(
                    repo,
                    current,
                    manifest,
                    {},
                    runner=MagicMock(),
                    validate_remote=False,
                )
            self.assertEqual(len(problems), 1)
            self.assertIn("revision implementation/worktree drift", problems[0])

    def test_status_and_completion_surface_revision_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._install_with_patches(repo)
            with patch.object(
                revision_hooks.revision,
                "status",
                return_value={
                    "present": True,
                    "revision_id": "r-status",
                    "status": "active",
                    "trigger": "manual+issue-refresh",
                    "current_revised_stage": "planner",
                    "next_action": "resume",
                    "superseded_stages": ["handoff-synthesized", "plan-created"],
                },
            ):
                rendered = opencode_resume_status.status_text(repo, {})
            self.assertIn("Revision: r-status", rendered)
            self.assertIn("trigger=manual+issue-refresh", rendered)
            self.assertIn("Revision stage: planner", rendered)
            self.assertIn("handoff-synthesized, plan-created", rendered)

            with patch.object(revision_hooks.revision, "mark_complete") as complete:
                payload = role_coordinator_flow.coordinate(repo)
            self.assertEqual(payload["state"], "PR_READY")
            complete.assert_called_once_with(repo.resolve())


if __name__ == "__main__":
    unittest.main()
