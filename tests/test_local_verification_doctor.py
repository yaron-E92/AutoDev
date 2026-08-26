from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import local_verification_doctor, repo_setup


REPO_ROOT = Path(__file__).resolve().parents[1]


class LocalVerificationDoctorTests(unittest.TestCase):
    def _install_around_stub(self):
        original = repo_setup.doctor

        def stub(repo: Path, **_kwargs):
            return repo_setup.DoctorResult(str(repo), ())

        repo_setup.doctor = stub
        local_verification_doctor.install()
        return original

    def test_doctor_reports_shipped_platform_neutral_default(self):
        original = self._install_around_stub()
        try:
            with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
                os.environ,
                {},
                clear=True,
            ):
                result = repo_setup.doctor(
                    Path(temp_dir),
                    autodev_root=REPO_ROOT,
                    which=lambda _name: None,
                )
        finally:
            repo_setup.doctor = original

        check = next(item for item in result.checks if item.name == "local-verification")
        self.assertEqual(check.state, "ok")
        self.assertIn("autodev verify-local", check.detail)

    def test_doctor_rejects_missing_explicit_verification_executable(self):
        original = self._install_around_stub()
        try:
            with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
                os.environ,
                {"LOCAL_CHECK": "pwsh -NoProfile -Command \"Write-Host ok\""},
                clear=True,
            ):
                result = repo_setup.doctor(
                    Path(temp_dir),
                    autodev_root=REPO_ROOT,
                    which=lambda _name: None,
                )
        finally:
            repo_setup.doctor = original

        check = next(item for item in result.checks if item.name == "local-verification")
        self.assertEqual(check.state, "error")
        self.assertIn("pwsh", check.detail)
        self.assertIn("unavailable", check.detail)


if __name__ == "__main__":
    unittest.main()
