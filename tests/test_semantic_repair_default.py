from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import workflow_stages

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "integrations" / "opencode" / "autodev.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("autodev_bridge_semantic_default", BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def semantic_result(verdict: str, message: str = "") -> str:
    passed = verdict == "pass"
    return json.dumps(
        {
            "verdict": verdict,
            "requirements": [
                {
                    "criterion": "The requested behavior is implemented",
                    "status": "met" if passed else "missing",
                    "evidence": ["src/a.py"],
                }
            ],
            "findings": []
            if passed
            else [
                {
                    "severity": "blocking",
                    "message": message or "Concrete semantic finding",
                    "path": "src/a.py",
                }
            ],
            "repair_brief": "" if passed else (message or "Repair src/a.py"),
        }
    )


class SemanticRepairDefaultTests(unittest.TestCase):
    def test_two_distinct_repairs_can_be_followed_by_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_json(
                current / "state.json",
                {
                    "IssueNumber": 1,
                    "BranchName": "autodev/issue-1",
                    "IssueText": "# Issue",
                },
            )
            result_path = current / "verification-result.json"

            def render_repair(repo_arg, current_arg, template_path, output_path):
                output_path.write_text("repair\n", encoding="utf-8")

            with (
                patch.dict(os.environ, {"MAX_SEMANTIC_REPAIR_ATTEMPTS": "2"}, clear=False),
                patch("automation.workflow_dispatch._require_accepted_role"),
                patch(
                    "automation.workflow_dispatch.prepare_semantic_repair_prompt",
                    side_effect=render_repair,
                ),
            ):
                result_path.write_text(
                    semantic_result("repair", "First distinct finding"),
                    encoding="utf-8",
                )
                _, first = workflow_stages.execute_stage(
                    "semantic", repo, autodev_root=REPO_ROOT, attempt=0
                )

                result_path.write_text(
                    semantic_result("repair", "Second distinct finding"),
                    encoding="utf-8",
                )
                _, second = workflow_stages.execute_stage(
                    "semantic", repo, autodev_root=REPO_ROOT, attempt=1
                )

                result_path.write_text(semantic_result("pass"), encoding="utf-8")
                _, passed = workflow_stages.execute_stage(
                    "semantic", repo, autodev_root=REPO_ROOT, attempt=2
                )

        self.assertEqual(first["state"], "REPAIR")
        self.assertEqual(second["state"], "REPAIR")
        self.assertEqual(passed["state"], "CONTINUE")
        self.assertEqual(passed["max_semantic_repair_attempts"], 2)

    def test_third_repair_verdict_blocks_after_two_repairs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_json(
                current / "state.json",
                {
                    "IssueNumber": 1,
                    "BranchName": "autodev/issue-1",
                    "IssueText": "# Issue",
                },
            )
            (current / "verification-result.json").write_text(
                semantic_result("repair", "Still missing after two repairs"),
                encoding="utf-8",
            )

            with (
                patch.dict(os.environ, {"MAX_SEMANTIC_REPAIR_ATTEMPTS": "2"}, clear=False),
                patch("automation.workflow_dispatch._require_accepted_role"),
            ):
                _, blocked = workflow_stages.execute_stage(
                    "semantic", repo, autodev_root=REPO_ROOT, attempt=2
                )

        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertEqual(blocked["max_semantic_repair_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
