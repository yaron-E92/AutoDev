from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import opencode_adapter_assets, opencode_adapter_contract


REPO_ROOT = Path(__file__).resolve().parents[1]


class SchedulerRuntimeAssetTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "worker"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return repo

    def test_provisioned_assets_are_worker_local_and_git_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            installed = opencode_adapter_assets.provision_scheduler_worker_assets(
                repo, REPO_ROOT
            )

            reader = repo / ".opencode" / "agents" / "autodev-reader.md"
            self.assertTrue(reader.is_file())
            self.assertIn(reader, installed)
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=normal"],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip(),
                "",
            )
            manifest = opencode_adapter_assets.scheduler_managed_assets(repo)
            self.assertIn(".opencode/agents/autodev-reader.md", manifest)

    def test_tracked_repository_asset_is_preserved_and_not_managed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            reader = repo / ".opencode" / "agents" / "autodev-reader.md"
            reader.parent.mkdir(parents=True)
            reader.write_text("repository-owned\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", ".opencode/agents/autodev-reader.md"],
                check=True,
            )

            opencode_adapter_assets.provision_scheduler_worker_assets(repo, REPO_ROOT)

            self.assertEqual(reader.read_text(encoding="utf-8"), "repository-owned\n")
            self.assertNotIn(
                ".opencode/agents/autodev-reader.md",
                opencode_adapter_assets.scheduler_managed_assets(repo),
            )

    def test_unknown_untracked_asset_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            reader = repo / ".opencode" / "agents" / "autodev-reader.md"
            reader.parent.mkdir(parents=True)
            reader.write_text("user-local\n", encoding="utf-8")

            with self.assertRaisesRegex(
                opencode_adapter_contract.OpenCodeAdapterError,
                "not scheduler-owned",
            ):
                opencode_adapter_assets.provision_scheduler_worker_assets(repo, REPO_ROOT)

            self.assertEqual(reader.read_text(encoding="utf-8"), "user-local\n")

    def test_modified_managed_asset_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            opencode_adapter_assets.provision_scheduler_worker_assets(repo, REPO_ROOT)
            reader = repo / ".opencode" / "agents" / "autodev-reader.md"
            reader.write_text("tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(
                opencode_adapter_contract.OpenCodeAdapterError,
                "modified unexpectedly",
            ):
                opencode_adapter_assets.provision_scheduler_worker_assets(repo, REPO_ROOT)

    def test_managed_assets_refresh_when_autodev_assets_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._repo(root)
            fake_root = root / "autodev"
            source = REPO_ROOT / "integrations" / "opencode"
            shutil.copytree(source, fake_root / "integrations" / "opencode")

            opencode_adapter_assets.provision_scheduler_worker_assets(repo, fake_root)
            reader = repo / ".opencode" / "agents" / "autodev-reader.md"
            old = reader.read_text(encoding="utf-8")
            canonical = fake_root / "integrations" / "opencode" / "agents" / "autodev-reader.md"
            canonical.write_text(old + "\nrefreshed-contract\n", encoding="utf-8")

            opencode_adapter_assets.provision_scheduler_worker_assets(repo, fake_root)

            self.assertIn("refreshed-contract", reader.read_text(encoding="utf-8"))

    def test_matching_untracked_canonical_asset_can_be_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            reader = repo / ".opencode" / "agents" / "autodev-reader.md"
            reader.parent.mkdir(parents=True)
            source = REPO_ROOT / "integrations" / "opencode" / "agents" / "autodev-reader.md"
            shutil.copyfile(source, reader)

            opencode_adapter_assets.provision_scheduler_worker_assets(repo, REPO_ROOT)

            self.assertIn(
                ".opencode/agents/autodev-reader.md",
                opencode_adapter_assets.scheduler_managed_assets(repo),
            )


if __name__ == "__main__":
    unittest.main()
