from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_role_context, ux_resolver
from automation.ux_contract import load_manifest


def write_bundle(root: Path) -> ux_resolver.ResolvedUXArtifact:
    (root / "screens").mkdir(parents=True)
    (root / "contract.yaml").write_text("navigation: task-first\n", encoding="utf-8")
    (root / "principles.md").write_text("# Principle\nPrefer direct manipulation.\n", encoding="utf-8")
    (root / "journey-create.yaml").write_text("id: create-task\n", encoding="utf-8")
    (root / "screens" / "editor.png").write_bytes(b"png-reference")
    (root / "ux-manifest.json").write_text(
        json.dumps(
            {
                "schema": "autodev.ux.bundle/v1",
                "product": "demo",
                "contract": "contract.yaml",
                "principles": "principles.md",
                "screens": {"task-editor": "screens/editor.png"},
                "states": {"task-editor-empty": "screens/editor.png"},
                "journey_files": {"create-task": "journey-create.yaml"},
            }
        ),
        encoding="utf-8",
    )
    return ux_resolver.ResolvedUXArtifact(
        immutable_identity="sha256:pinned",
        immutable_reference="fake://demo@sha256:pinned",
        local_root=root,
        manifest=load_manifest(root),
        source_reference="fake://demo@sha256:pinned",
        resolver_kind="fake",
    )


class UXRoleContextTests(unittest.TestCase):
    def _prepared(self, root: Path) -> tuple[Path, Path, ux_resolver.ResolvedUXArtifact]:
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        artifact_root = root / "bundle"
        artifact_root.mkdir()
        artifact = write_bundle(artifact_root)
        (current / "state.json").write_text(
            json.dumps(
                {
                    "UXArtifact": {
                        "immutable_identity": "sha256:pinned",
                        "product": "demo",
                    }
                }
            ),
            encoding="utf-8",
        )
        (current / "run-manifest.json").write_text(
            json.dumps({"ux_artifact": {"immutable_identity": "sha256:pinned"}}),
            encoding="utf-8",
        )
        return repo, current, artifact

    def test_disabled_run_adds_no_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (current / "state.json").write_text("{}", encoding="utf-8")
            prompt, evidence = ux_role_context.prepare_role_context(
                repo, current, "planner", "UI issue"
            )
        self.assertEqual(prompt, "")
        self.assertEqual(evidence, {})
        self.assertFalse((current / "ux-context-planner.json").exists())

    def test_planner_gets_baseline_and_targeted_context_with_durable_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            issue = "Implement the create-task journey for task-editor and task-editor-empty."
            with patch.object(ux_role_context.ux_workflow, "resolve_configured", return_value=artifact):
                prompt, evidence = ux_role_context.prepare_role_context(
                    repo, current, "planner", issue
                )

            self.assertIn("navigation: task-first", prompt)
            self.assertIn("Prefer direct manipulation", prompt)
            self.assertIn("id: create-task", prompt)
            self.assertIn("screens/editor.png", prompt)
            self.assertNotIn("png-reference", prompt)
            context = evidence["ux_context"]
            self.assertEqual(context["journeys"], ["create-task"])
            self.assertEqual(context["screens"], ["task-editor"])
            self.assertEqual(context["states"], ["task-editor-empty"])
            self.assertIn("contract.yaml", context["selected_paths"])
            self.assertIn("principles.md", context["selected_paths"])
            self.assertIn("screens/editor.png", context["non_text_or_truncated_references"])
            self.assertTrue(evidence["ux_context_fingerprint"])
            persisted = json.loads((current / "ux-context-planner.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, evidence)
            manifest = json.loads((current / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["ux_role_contexts"]["planner"], evidence)

    def test_fingerprint_changes_when_selected_file_bytes_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            with patch.object(ux_role_context.ux_workflow, "resolve_configured", return_value=artifact):
                _, first = ux_role_context.prepare_role_context(
                    repo, current, "implementer", "Implement create-task"
                )
            (artifact.local_root / "contract.yaml").write_text(
                "navigation: workspace-first\n", encoding="utf-8"
            )
            changed = ux_resolver.ResolvedUXArtifact(
                immutable_identity=artifact.immutable_identity,
                immutable_reference=artifact.immutable_reference,
                local_root=artifact.local_root,
                manifest=load_manifest(artifact.local_root),
                source_reference=artifact.source_reference,
                resolver_kind=artifact.resolver_kind,
            )
            with patch.object(ux_role_context.ux_workflow, "resolve_configured", return_value=changed):
                _, second = ux_role_context.prepare_role_context(
                    repo, current, "implementer", "Implement create-task"
                )
            self.assertNotEqual(
                first["ux_context_fingerprint"], second["ux_context_fingerprint"]
            )

    def test_fingerprint_changes_when_selected_ids_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            with patch.object(ux_role_context.ux_workflow, "resolve_configured", return_value=artifact):
                _, baseline = ux_role_context.prepare_role_context(
                    repo, current, "planner", "Improve the UI"
                )
                _, targeted = ux_role_context.prepare_role_context(
                    repo, current, "planner", "Improve task-editor"
                )
            self.assertNotEqual(
                baseline["ux_context_fingerprint"], targeted["ux_context_fingerprint"]
            )

    def test_identity_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, artifact = self._prepared(Path(temp_dir))
            changed = ux_resolver.ResolvedUXArtifact(
                immutable_identity="sha256:changed",
                immutable_reference=artifact.immutable_reference,
                local_root=artifact.local_root,
                manifest=artifact.manifest,
                source_reference=artifact.source_reference,
                resolver_kind=artifact.resolver_kind,
            )
            with patch.object(ux_role_context.ux_workflow, "resolve_configured", return_value=changed):
                with self.assertRaises(ux_role_context.UXRoleContextError):
                    ux_role_context.prepare_role_context(
                        repo, current, "verifier", "Verify task-editor"
                    )


if __name__ == "__main__":
    unittest.main()
