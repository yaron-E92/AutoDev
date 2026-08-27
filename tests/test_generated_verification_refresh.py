from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from area_reader import repository as area_reader_repository
from area_reader import verification as area_reader_verification
from automation import (
    local_verification,
    repair_lineage,
    run_manifest,
    verification_discovery,
    workflow_stages,
)
from automation.workflow_storage import read_json, write_json, write_state


REPO_ROOT = Path(__file__).resolve().parents[1]


class GeneratedVerificationRefreshTests(unittest.TestCase):
    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        )
        return completed.stdout.strip()

    def test_next_generated_package_roots_never_become_node_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write(
                repo / "package.json",
                json.dumps(
                    {
                        "scripts": {"build": "next build"},
                        "dependencies": {"next": "16.0.0"},
                    }
                ),
            )
            self._write(repo / "package-lock.json", "{}")
            self._write(repo / ".next" / "package.json", '{"name":"generated"}')
            self._write(repo / ".next" / "dev" / "package.json", '{"name":"generated-dev"}')

            files, _, _ = area_reader_repository.collect_repo_files(repo)
            paths = {item["path"] for item in files}
            facts = area_reader_repository.detect_repo_facts(repo, files, ["web"], {})
            groups = area_reader_verification.build_verification_command_groups(
                facts,
                ["web"],
            )

        self.assertIn("package.json", paths)
        self.assertFalse(any(path.startswith(".next/") for path in paths))
        self.assertEqual([item["root"] for item in facts["package_roots"]], ["."])
        node = next(group for group in groups if group["name"] == "node-root")
        self.assertEqual([item["cwd"] for item in node["commands"]], ["."])
        self.assertFalse(any(item["cwd"].startswith(".next") for item in node["commands"]))

    def test_supported_framework_generated_roots_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            for generated in (".next", ".nuxt", ".svelte-kit", ".turbo", "out"):
                self._write(repo / generated / "package.json", '{"name":"generated"}')

            files, _, _ = area_reader_repository.collect_repo_files(repo)

        paths = {item["path"] for item in files}
        self.assertFalse(paths)
        for generated in (".next", ".nuxt", ".svelte-kit", ".turbo", "out"):
            self.assertTrue(
                area_reader_repository.is_generated_relative_path(
                    f"{generated}/package.json"
                )
            )

    def test_gitignored_generated_manifest_is_not_a_source_package_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._git(repo, "init")
            self._write(repo / ".gitignore", "cache-generated/\n")
            self._write(repo / "package.json", '{"name":"root"}')
            self._write(repo / "packages" / "app" / "package.json", '{"name":"app"}')
            self._write(
                repo / "cache-generated" / "package.json",
                '{"name":"ignored-generated"}',
            )
            self._git(repo, "add", ".gitignore", "package.json")

            files, _, _ = area_reader_repository.collect_repo_files(repo)
            facts = area_reader_repository.detect_repo_facts(repo, files, ["web"], {})

        roots = [item["root"] for item in facts["package_roots"]]
        self.assertEqual(roots, [".", "packages/app"])
        self.assertNotIn("cache-generated", roots)

    def _stale_run(self, repo: Path) -> Path:
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        self._write(repo / "package.json", '{"name":"root"}')
        self._write(repo / "package-lock.json", "{}")
        self._write(repo / "src" / "kept.txt", "implementation edit\n")
        self._write(current / "issue.md", "Fix the web behavior.\n")
        self._write(current / "reader-brief.md", "reader semantic output\n")
        self._write(current / "synthesized-handoff.md", "synth semantic output\n")
        self._write(current / "plan.md", "planner semantic output\n")
        self._write(current / "commit-message.txt", "implementation checkpoint\n")
        write_json(
            current / "routed-areas.json",
            {"areas": ["web"], "source": "preserved-reader-routing"},
        )
        write_json(
            current / "detected-facts.json",
            {
                "package_roots": [
                    {
                        "path": ".next/package.json",
                        "root": ".next",
                        "package_manager": "npm",
                        "install_command": ["npm", "ci"],
                        "scripts": [],
                        "is_web": True,
                        "has_api_client_generate": False,
                    }
                ]
            },
        )
        write_json(
            current / "verification-command-groups.json",
            [
                {
                    "name": "node-root",
                    "manual": False,
                    "commands": [
                        {
                            "label": "generated dev install",
                            "cwd": ".next/dev",
                            "argv": ["npm", "install"],
                            "optional": True,
                        },
                        {
                            "label": "generated install",
                            "cwd": ".next",
                            "argv": ["npm", "ci"],
                            "optional": False,
                        },
                    ],
                }
            ],
        )
        write_json(
            current / "recommended-command-groups.json",
            {
                "available_command_groups": ["node-root"],
                "recommended_command_groups": ["node-root"],
                "conditional_command_groups": {},
            },
        )

        manifest_path = current / run_manifest.MANIFEST_NAME
        run_manifest.create_manifest(
            manifest_path,
            repo_path=repo,
            github_repo="Tax-Technology/goldilocks",
            issue_number=6,
            mode="issue-to-pr",
            base_sha="base-sha",
            branch="autodev/issue-6",
            role_snapshots={},
        )
        run_manifest.complete_stage(
            manifest_path,
            "repository-read",
            run_root=current,
            artifacts=[
                current / "reader-brief.md",
                current / "routed-areas.json",
                current / "detected-facts.json",
                current / "verification-command-groups.json",
                current / "recommended-command-groups.json",
            ],
        )
        run_manifest.complete_stage(
            manifest_path,
            "handoff-synthesized",
            run_root=current,
            artifacts=[current / "synthesized-handoff.md"],
        )
        run_manifest.complete_stage(
            manifest_path,
            "plan-created",
            run_root=current,
            artifacts=[current / "plan.md"],
        )
        run_manifest.complete_stage(
            manifest_path,
            "implementation-generated",
            run_root=current,
            artifacts=[current / "commit-message.txt"],
            details={"source_identity": "implementation-source"},
        )
        run_manifest.complete_stage(
            manifest_path,
            "patch-applied",
            run_root=current,
            details={
                "kind": "implementation",
                "attempt": 0,
                "source_identity": "implementation-source",
            },
        )
        return current

    def test_stale_generated_cwd_refresh_preserves_implementation_and_semantic_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._stale_run(repo)
            before = {
                name: (current / name).read_bytes()
                for name in (
                    "reader-brief.md",
                    "synthesized-handoff.md",
                    "plan.md",
                    "commit-message.txt",
                )
            }
            before_manifest = run_manifest.load_manifest(
                current / run_manifest.MANIFEST_NAME
            )
            patch_details = dict(
                before_manifest["stages"]["patch-applied"]["details"]
            )

            reason = verification_discovery.refresh_stale_verification_discovery(
                repo,
                current,
            )

            facts = read_json(current / "detected-facts.json")
            groups = read_json(current / "verification-command-groups.json")
            after_manifest = run_manifest.load_manifest(
                current / run_manifest.MANIFEST_NAME
            )

        self.assertIn("generated", reason)
        self.assertEqual([item["root"] for item in facts["package_roots"]], ["."])
        node = next(group for group in groups if group["name"] == "node-root")
        self.assertEqual([item["cwd"] for item in node["commands"]], ["."])
        self.assertEqual(
            before,
            {
                name: (current / name).read_bytes()
                for name in before
            },
        )
        self.assertEqual(
            after_manifest["stages"]["patch-applied"]["details"],
            patch_details,
        )
        self.assertEqual(
            after_manifest["completed_stages"],
            before_manifest["completed_stages"],
        )
        refreshable = after_manifest["stages"]["repository-read"]["details"][
            "refreshable_artifacts"
        ]
        self.assertIn("detected-facts.json", refreshable)
        self.assertEqual(
            run_manifest.validate_artifacts(after_manifest, current),
            [],
        )
        self.assertEqual(
            (repo / "src" / "kept.txt").read_text(encoding="utf-8"),
            "implementation edit\n",
        )

    def test_native_local_verifier_auto_refreshes_deleted_generated_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._stale_run(repo)
            calls = []

            def runner(argv, **kwargs):
                calls.append((list(argv), Path(kwargs["cwd"])))
                return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

            result = local_verification.run_recommended_verification(
                repo,
                current,
                runner=runner,
                which=lambda name: f"/tools/{name}",
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Refreshed deterministic verification discovery", result.output)
        self.assertTrue(
            any(argv == ["npm", "ci"] and cwd == repo for argv, cwd in calls)
        )
        self.assertFalse(any(".next" in cwd.as_posix() for _, cwd in calls))

    def test_old_setup_repair_count_does_not_exhaust_distinct_failure_lineage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            write_state(
                current,
                {
                    "LocalCheck": "failing-check",
                    "Status": "Implemented",
                    "RunDir": str(current),
                },
            )
            self._write(current / "issue.md", "Issue\n")
            self._write(current / "workspace-snapshot.json", "{}\n")

            def runner(*_args, **_kwargs):
                return SimpleNamespace(
                    returncode=1,
                    stdout="new post-#215 code failure\n",
                    stderr="",
                )

            with patch.dict(os.environ, {"MAX_REPAIR_ATTEMPTS": "2"}, clear=False):
                _, first = workflow_stages.execute_stage(
                    "local-check",
                    repo,
                    autodev_root=REPO_ROOT,
                    attempt=3,
                    runner=runner,
                )
                _, same_lineage = workflow_stages.execute_stage(
                    "local-check",
                    repo,
                    autodev_root=REPO_ROOT,
                    attempt=3,
                    runner=runner,
                )

        self.assertEqual(first["state"], "REPAIR")
        self.assertEqual(first["repair_attempt"], 0)
        self.assertTrue(first["failure_fingerprint"])
        self.assertEqual(same_lineage["state"], "BLOCKED")
        state = workflow_stages.read_state(current)
        self.assertEqual(
            repair_lineage.current_local_repair_attempt(state),
            0,
        )

    def test_repair_lineage_counter_is_bounded_per_fingerprint(self):
        state: dict[str, object] = {}
        first = repair_lineage.local_failure_fingerprint(
            "autodev verify-local",
            "+ (.) npm test\nfailed A\n",
            1,
        )
        second = repair_lineage.local_failure_fingerprint(
            "autodev verify-local",
            "+ (.) npm test\nfailed B\n",
            1,
        )

        self.assertEqual(repair_lineage.register_local_failure(state, first), 0)
        self.assertEqual(repair_lineage.consume_local_repair_attempt(state), 1)
        self.assertEqual(repair_lineage.register_local_failure(state, first), 1)
        self.assertEqual(repair_lineage.register_local_failure(state, second), 0)
        self.assertEqual(repair_lineage.current_local_repair_attempt(state), 0)


if __name__ == "__main__":
    unittest.main()
