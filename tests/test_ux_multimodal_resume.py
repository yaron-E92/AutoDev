from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_resume_status,
    run_manifest,
    tui_model,
    tui_terminal,
    ux_capture,
    ux_multimodal,
    ux_multimodal_resume,
    ux_resolver,
)
from automation.ux_contract import UXBundleManifest


PNG_REFERENCE = b"\x89PNG\r\n\x1a\nreference"
PNG_CAPTURE = b"\x89PNG\r\n\x1a\ncapture"


class UXMultimodalResumeTests(unittest.TestCase):
    def _prepared(self, root: Path):
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        (repo / ".autodev").mkdir(parents=True)
        (current / "ux-captures").mkdir(parents=True)

        artifact_root = root / "ux"
        (artifact_root / "screens").mkdir(parents=True)
        reference_path = artifact_root / "screens" / "home.png"
        reference_path.write_bytes(PNG_REFERENCE)
        manifest = UXBundleManifest(
            schema="autodev.ux.bundle/v1",
            product="demo",
            contract="contract.yaml",
            prototype="",
            screens={"home": "screens/home.png"},
            states={},
            journey_files={},
        )
        artifact = ux_resolver.ResolvedUXArtifact(
            immutable_identity="sha256:pinned",
            immutable_reference="fake://demo@sha256:pinned",
            local_root=artifact_root,
            manifest=manifest,
            source_reference="fake://demo@sha256:pinned",
            resolver_kind="fake",
        )
        (current / "state.json").write_text(
            json.dumps({"UXArtifact": {"immutable_identity": "sha256:pinned"}}),
            encoding="utf-8",
        )
        (current / "ux-context-verifier.json").write_text(
            json.dumps(
                {
                    "ux_artifact": {"immutable_identity": "sha256:pinned"},
                    "ux_context": {
                        "contract": "contract.yaml",
                        "principles": "",
                        "screens": ["home"],
                        "states": [],
                        "journeys": [],
                    },
                    "ux_context_fingerprint": "ux-fingerprint",
                }
            ),
            encoding="utf-8",
        )
        (repo / ux_capture.CAPTURE_CONFIG).write_text(
            json.dumps(
                {
                    "schema": ux_capture.CAPTURE_SCHEMA,
                    "command": ["capture-demo"],
                    "targets": {
                        "screen:home": {
                            "source_kind": "screen",
                            "source_id": "home",
                            "output": "home.png",
                            "viewport": "1280x720",
                            "platform": "web",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        config = ux_capture.load_config(repo)
        assert config is not None
        capture_path = current / "ux-captures" / "home.png"
        capture_path.write_bytes(PNG_CAPTURE)
        reference = ux_multimodal.ReferenceImage(
            target_id="screen:home",
            source_kind="screen",
            source_id="home",
            relative_path="screens/home.png",
            path=reference_path,
            sha256=hashlib.sha256(PNG_REFERENCE).hexdigest(),
            mime="image/png",
            size_bytes=len(PNG_REFERENCE),
        )
        target = config.targets["screen:home"]
        capture = ux_capture.CapturedImage(
            target=target,
            path=capture_path,
            sha256=hashlib.sha256(PNG_CAPTURE).hexdigest(),
            mime="image/png",
            size_bytes=len(PNG_CAPTURE),
        )
        result = ux_multimodal._base_result(
            status="pass",
            artifact_context={"immutable_identity": "sha256:pinned"},
            ux_fingerprint="ux-fingerprint",
            capture_config_sha256=config.sha256,
            capability=ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
            runtime="fake-vision",
            model="fake/vision",
            references=(reference,),
            captures=(capture,),
            comparisons=[
                {
                    "target_id": "screen:home",
                    "source_kind": "screen",
                    "source_id": "home",
                    "reference_sha256": reference.sha256,
                    "implementation_sha256": capture.sha256,
                    "status": "satisfied",
                }
            ],
            findings=[
                {
                    "source_kind": "screen",
                    "source_id": "home",
                    "status": "satisfied",
                    "evidence": "matched",
                    "required_change": "",
                    "category": "visual",
                }
            ],
            repair_brief="",
            structured_output_mode="native-validated",
        )
        (current / ux_multimodal.RESULT_FILE).write_text(
            json.dumps(result), encoding="utf-8"
        )
        return repo, current, artifact, reference_path, capture_path

    def test_unchanged_multimodal_evidence_is_resume_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact, _reference, _capture = self._prepared(Path(temp_dir))
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                self.assertEqual(ux_multimodal_resume.stale_reasons(repo, current), [])

            value = ux_multimodal_resume.summary(current)
            self.assertEqual(value["status"], "pass")
            self.assertEqual(value["targets"], ["screen:home"])
            self.assertTrue(value["evidence_identity"])

    def test_changed_capture_bytes_make_prior_visual_pass_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact, _reference, capture = self._prepared(Path(temp_dir))
            capture.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                reasons = ux_multimodal_resume.stale_reasons(repo, current)
            self.assertTrue(any("implementation capture bytes changed" in item for item in reasons))

    def test_changed_reference_bytes_make_prior_visual_pass_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact, reference, _capture = self._prepared(Path(temp_dir))
            reference.write_bytes(b"\x89PNG\r\n\x1a\nchanged-reference")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                reasons = ux_multimodal_resume.stale_reasons(repo, current)
            self.assertTrue(any("selected UX reference bytes changed" in item for item in reasons))

    def test_changed_viewport_and_capture_config_make_prior_pass_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact, _reference, _capture = self._prepared(Path(temp_dir))
            config_path = repo / ux_capture.CAPTURE_CONFIG
            value = json.loads(config_path.read_text(encoding="utf-8"))
            value["targets"]["screen:home"]["viewport"] = "390x844"
            config_path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                reasons = ux_multimodal_resume.stale_reasons(repo, current)
            self.assertIn("UX capture configuration changed", reasons)
            self.assertTrue(any("capture viewport changed" in item for item in reasons))

    def test_not_applicable_result_ignores_irrelevant_existing_capture_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact, _reference, _capture = self._prepared(Path(temp_dir))
            context = json.loads((current / "ux-context-verifier.json").read_text(encoding="utf-8"))
            context["ux_context"]["screens"] = []
            (current / "ux-context-verifier.json").write_text(json.dumps(context), encoding="utf-8")
            result = ux_multimodal._base_result(
                status="not-applicable",
                artifact_context={"immutable_identity": "sha256:pinned"},
                ux_fingerprint="ux-fingerprint",
                capture_config_sha256="",
                capability=ux_multimodal.CAPABILITY_UNSUPPORTED,
                runtime="",
                model="",
                references=(),
                captures=(),
                findings=[],
                repair_brief="",
            )
            (current / ux_multimodal.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            with patch.object(
                ux_multimodal.ux_workflow,
                "resolve_configured",
                return_value=artifact,
            ):
                self.assertEqual(ux_multimodal_resume.stale_reasons(repo, current), [])

    def test_reconcile_clears_semantic_and_pr_checkpoints_when_visual_evidence_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / ".autodev-run" / "current"
            current.mkdir(parents=True)
            path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                path,
                repo_path=root,
                github_repo="owner/repo",
                issue_number=305,
                mode="issue-to-pr",
                base_sha="base",
                branch="feature",
                role_snapshots={"verifier": {"fingerprint": "fp"}},
            )
            run_manifest.complete_stage(path, "semantic-verified", run_root=current)
            run_manifest.complete_stage(path, "pr-created", run_root=current)
            manifest = run_manifest.load_manifest(path)
            manifest["ux_multimodal_verification"] = {"status": "pass"}
            run_manifest.save_manifest(path, manifest)

            with patch.object(
                ux_multimodal_resume,
                "stale_reasons",
                return_value=["implementation capture bytes changed for screen:home"],
            ):
                reasons = ux_multimodal_resume.reconcile_completed_verification(
                    root, current, path
                )

            self.assertTrue(reasons)
            after = run_manifest.load_manifest(path)
            self.assertNotIn("semantic-verified", after["completed_stages"])
            self.assertNotIn("pr-created", after["completed_stages"])
            self.assertTrue(
                any(
                    item.get("role") == "verifier"
                    and "multimodal UX evidence stale" in str(item.get("reason", ""))
                    for item in after["invalidations"]
                )
            )

    def test_resume_status_blocks_stale_multimodal_checkpoint_until_verifier_invalidation(self) -> None:
        repo = Path("/tmp/repo").resolve()
        current = repo / ".autodev-run" / "current"
        manifest = {
            "target": {
                "repo_path": str(repo),
                "github_repo": "owner/repo",
                "issue_number": 305,
                "base_sha": "base",
                "branch": "feature",
            },
            "completed_stages": ["semantic-verified"],
            "stages": {},
            "ux_multimodal_verification": {"status": "pass"},
        }
        state = {
            "RepoFullName": "owner/repo",
            "IssueNumber": 305,
            "BaseSha": "base",
            "BranchName": "feature",
        }
        runner = lambda *_args, **_kwargs: SimpleNamespace(stdout="base\n", returncode=0)
        with (
            patch.object(run_manifest, "validate_artifacts", return_value=[]),
            patch.object(ux_multimodal_resume, "tracked", return_value=True),
            patch.object(
                ux_multimodal_resume,
                "stale_reasons",
                return_value=["implementation capture bytes changed for screen:home"],
            ),
            patch.object(opencode_resume_status.workflow_stages, "workspace_changes", return_value=False),
        ):
            problems = opencode_resume_status._resume_problems(
                repo,
                current,
                manifest,
                state,
                runner=runner,
                validate_remote=False,
            )
        self.assertTrue(any("--invalidate-role verifier" in item for item in problems))

    def test_tui_renders_multimodal_checkpoint_and_evidence_identity(self) -> None:
        snapshot = tui_model.TuiSnapshot(
            repository="owner/repo",
            repo_path="/repo",
            observed_at="now",
            run=tui_model.TuiRun(
                state="RESUME_EXISTING",
                multimodal_status="pass",
                multimodal_checkpoint_valid=True,
                multimodal_evidence_identity="0123456789abcdef",
                multimodal_targets=("screen:home",),
                multimodal_violations=0,
                multimodal_unverifiable=0,
                multimodal_runtime="opencode",
                multimodal_model="openai/vision",
                multimodal_capability="image-attachments",
            ),
        )
        rendered = tui_terminal.render(
            snapshot,
            tui_terminal.ViewState(),
            width=120,
            height=40,
        )
        self.assertIn("UX visual: pass | checkpoint=yes", rendered)
        self.assertIn("evidence=0123456789ab", rendered)
        self.assertIn("screen:home", rendered)


if __name__ == "__main__":
    unittest.main()
