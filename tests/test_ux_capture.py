from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import ux_capture


PNG_A = b"\x89PNG\r\n\x1a\nreference-a"


class UXCaptureTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / ".autodev").mkdir(parents=True)
        return repo

    def test_command_provider_captures_only_declared_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "provider": "command",
                        "command": ["capture-ui", "--deterministic"],
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
            calls: list[list[str]] = []

            def runner(command, **kwargs):
                calls.append(list(command))
                output = Path(kwargs["env"]["AUTODEV_UX_CAPTURE_OUTPUT"])
                self.assertTrue(str(output).startswith(str(current / "ux-captures")))
                self.assertEqual(kwargs["env"]["AUTODEV_UX_CAPTURE_TARGET"], "screen:home")
                output.write_bytes(PNG_A)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            captured = ux_capture.capture_target(
                repo,
                current,
                config,
                "screen:home",
                runner=runner,
            )

            self.assertEqual(calls, [["capture-ui", "--deterministic"]])
            self.assertEqual(captured.mime, "image/png")
            self.assertEqual(captured.target.viewport, "1280x720")
            self.assertTrue(captured.sha256)
            self.assertEqual(captured.path.read_bytes(), PNG_A)

    def test_unsafe_output_path_is_rejected_before_command_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "command": ["capture-ui"],
                        "targets": {
                            "home": {
                                "source_kind": "screen",
                                "source_id": "home",
                                "output": "../ambient.png",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ux_capture.UXCaptureError, "single safe"):
                ux_capture.load_config(repo)

    def test_capture_rejects_missing_or_non_image_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "command": ["capture-ui"],
                        "targets": {
                            "home": {
                                "source_kind": "screen",
                                "source_id": "home",
                                "output": "home.png",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = ux_capture.load_config(repo)
            assert config is not None

            def missing(_command, **_kwargs):
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with self.assertRaisesRegex(ux_capture.UXCaptureError, "did not produce"):
                ux_capture.capture_target(repo, current, config, "screen:home", runner=missing)

            def invalid(_command, **kwargs):
                Path(kwargs["env"]["AUTODEV_UX_CAPTURE_OUTPUT"]).write_text(
                    "not an image", encoding="utf-8"
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with self.assertRaisesRegex(ux_capture.UXCaptureError, "not a supported"):
                ux_capture.capture_target(repo, current, config, "screen:home", runner=invalid)

    def test_capture_command_is_argv_not_shell_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (repo / ux_capture.CAPTURE_CONFIG).write_text(
                json.dumps(
                    {
                        "schema": ux_capture.CAPTURE_SCHEMA,
                        "command": ["capture-ui", "--url", "http://127.0.0.1:3000"],
                        "targets": {
                            "home": {
                                "source_kind": "screen",
                                "source_id": "home",
                                "output": "home.png",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = ux_capture.load_config(repo)
            assert config is not None

            def runner(command, **kwargs):
                self.assertIsInstance(command, list)
                self.assertNotIn("shell", kwargs)
                Path(kwargs["env"]["AUTODEV_UX_CAPTURE_OUTPUT"]).write_bytes(PNG_A)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            ux_capture.capture_target(repo, current, config, "screen:home", runner=runner)


if __name__ == "__main__":
    unittest.main()
