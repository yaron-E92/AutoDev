from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_capture, ux_desktop_capture


PNG = b"\x89PNG\r\n\x1a\ndesktop-capture"


class DesktopCaptureTests(unittest.TestCase):
    @staticmethod
    def _value() -> dict[str, object]:
        return {
            "schema": ux_capture.CAPTURE_SCHEMA,
            "provider": "desktop",
            "timeout_seconds": 30,
            "desktop": {
                "platform": "windows",
                "application": {
                    "command": ["dotnet", "run", "--project", "Demo.csproj"],
                },
            },
            "targets": {
                "state:signed-in": {
                    "source_kind": "state",
                    "source_id": "signed-in",
                    "output": "signed-in.png",
                    "reference": "screen:home",
                    "viewport": "800x600",
                    "launch_args": ["--autodev-state", "signed-in"],
                    "window": {
                        "process_name": "Demo.exe",
                        "title": "Demo - Signed in",
                        "class_name": "DemoWindow",
                        "timeout_ms": 5000,
                    },
                }
            },
        }

    @staticmethod
    def _write(repo: Path, value: dict[str, object]) -> None:
        (repo / ".autodev").mkdir(parents=True, exist_ok=True)
        (repo / ux_capture.CAPTURE_CONFIG).write_text(json.dumps(value), encoding="utf-8")

    def test_desktop_provider_parses_explicit_window_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            self._write(repo, self._value())

            config = ux_capture.load_config(repo)
            assert config is not None
            self.assertEqual(config.provider, "desktop")
            provider = config.desktop_config
            self.assertIsInstance(provider, ux_desktop_capture.DesktopProviderConfig)
            assert isinstance(provider, ux_desktop_capture.DesktopProviderConfig)
            target = provider.targets["state:signed-in"]
            self.assertEqual(target.process_name, "Demo.exe")
            self.assertEqual(target.title, "Demo - Signed in")
            self.assertEqual(target.class_name, "DemoWindow")
            self.assertEqual(target.launch_args, ("--autodev-state", "signed-in"))
            self.assertEqual(target.expected_viewport, "800x600")
            self.assertTrue(ux_capture.configured_capture_identity(config, "state:signed-in"))

    def test_mobile_is_reserved_but_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            value = self._value()
            value["desktop"]["platform"] = "mobile"  # type: ignore[index]
            self._write(repo, value)
            with self.assertRaisesRegex(ux_capture.UXCaptureError, "mobile UX capture is not yet supported"):
                ux_capture.load_config(repo)

    def test_explicit_process_and_exact_title_are_required(self) -> None:
        for field in ("process_name", "title"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir) / "repo"
                value = self._value()
                target = value["targets"]["state:signed-in"]  # type: ignore[index]
                target["window"][field] = ""  # type: ignore[index]
                self._write(repo, value)
                with self.assertRaises(ux_capture.UXCaptureError):
                    ux_capture.load_config(repo)

    def test_invalid_viewport_and_unbound_launch_args_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            value = self._value()
            target = value["targets"]["state:signed-in"]  # type: ignore[index]
            target["viewport"] = "all-screens"  # type: ignore[index]
            self._write(repo, value)
            with self.assertRaisesRegex(ux_capture.UXCaptureError, "WIDTHxHEIGHT"):
                ux_capture.load_config(repo)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            value = self._value()
            value["desktop"]["application"] = {}  # type: ignore[index]
            self._write(repo, value)
            with self.assertRaisesRegex(ux_capture.UXCaptureError, "launch_args require"):
                ux_capture.load_config(repo)

    def test_configured_identity_changes_with_declared_window_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            value = self._value()
            self._write(repo, value)
            first = ux_capture.load_config(repo)
            assert first is not None
            first_identity = ux_capture.configured_capture_identity(first, "state:signed-in")

            target = value["targets"]["state:signed-in"]  # type: ignore[index]
            target["window"]["title"] = "Demo - Other state"  # type: ignore[index]
            self._write(repo, value)
            second = ux_capture.load_config(repo)
            assert second is not None
            second_identity = ux_capture.configured_capture_identity(second, "state:signed-in")
            self.assertNotEqual(first_identity, second_identity)

    def test_unique_window_is_required(self) -> None:
        target = ux_desktop_capture.WindowTarget(process_name="Demo.exe", title="Demo")
        match = ux_desktop_capture.WindowMatch(
            hwnd=1,
            pid=2,
            process_name="Demo.exe",
            title="Demo",
            class_name="DemoWindow",
            width=800,
            height=600,
            process_started_100ns=123,
        )
        with patch.object(ux_desktop_capture, "_matching_windows", return_value=[match, match]):
            with self.assertRaisesRegex(ux_desktop_capture.DesktopCaptureError, "ambiguous"):
                ux_desktop_capture._wait_for_unique_window(target, 999999999.0)

    def test_non_windows_host_fails_closed_without_launch_or_capture(self) -> None:
        config = ux_desktop_capture.DesktopProviderConfig(
            platform="windows",
            application_command=(),
            targets={
                "screen:home": ux_desktop_capture.WindowTarget(
                    process_name="Demo.exe",
                    title="Demo",
                )
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            ux_desktop_capture.sys, "platform", "linux"
        ):
            with self.assertRaisesRegex(
                ux_desktop_capture.DesktopCaptureError,
                "ambient desktop fallback is forbidden",
            ):
                ux_desktop_capture.capture(
                    Path(temp_dir),
                    Path(temp_dir),
                    config,
                    "screen:home",
                    Path(temp_dir) / "home.png",
                    timeout_seconds=1,
                )

    def test_desktop_provider_dispatch_preserves_dynamic_window_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            self._write(repo, self._value())
            config = ux_capture.load_config(repo)
            assert config is not None

            def capture(_repo, _current, _provider, target_id, output, **_kwargs):
                self.assertEqual(target_id, "state:signed-in")
                Path(output).write_bytes(PNG)
                return ux_desktop_capture.DesktopCaptureResult(
                    platform="windows-desktop",
                    viewport="800x600",
                    configured_identity="windows-window-config:abc",
                    runtime_identity="windows-window:def",
                )

            with patch.object(ux_desktop_capture, "capture", side_effect=capture):
                result = ux_capture.capture_target(
                    repo,
                    current,
                    config,
                    "state:signed-in",
                )

            self.assertEqual(result.target.platform, "windows-desktop")
            self.assertEqual(result.target.viewport, "800x600")
            self.assertEqual(result.configured_identity, "windows-window-config:abc")
            self.assertEqual(result.runtime_identity, "windows-window:def")
            self.assertEqual(result.mime, "image/png")

    def test_desktop_capture_source_has_no_ambient_pixel_fallback(self) -> None:
        source = inspect.getsource(ux_desktop_capture)
        self.assertIn("PrintWindow", source)
        self.assertNotIn("BitBlt", source)
        self.assertNotIn("ImageGrab", source)
        self.assertNotIn("pyautogui", source)


if __name__ == "__main__":
    unittest.main()
