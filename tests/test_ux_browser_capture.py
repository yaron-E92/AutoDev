from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import ux_browser_capture, ux_capture


PNG = b"\x89PNG\r\n\x1a\nbrowser-capture"


class BrowserCaptureConfigTests(unittest.TestCase):
    @staticmethod
    def _write_config(repo: Path, value: dict[str, object]) -> None:
        (repo / ".autodev").mkdir(parents=True, exist_ok=True)
        (repo / ux_capture.CAPTURE_CONFIG).write_text(
            json.dumps(value),
            encoding="utf-8",
        )

    @staticmethod
    def _browser_config(*, target_kind: str = "journey") -> dict[str, object]:
        source_id = "checkout-complete" if target_kind == "journey" else "signed-in"
        target_id = f"{target_kind}:{source_id}"
        return {
            "schema": ux_capture.CAPTURE_SCHEMA,
            "provider": "browser",
            "timeout_seconds": 30,
            "browser": {
                "application": {
                    "origin": "http://127.0.0.1:4173",
                    "ready_path": "/health",
                },
                "allowed_origins": ["http://127.0.0.1:4173"],
                "executable": "google-chrome",
            },
            "targets": {
                target_id: {
                    "source_kind": target_kind,
                    "source_id": source_id,
                    "output": f"{source_id}.png",
                    "reference": "screen:confirmation",
                    "route": "/checkout",
                    "viewport": "1024x768@1",
                    "ready_selector": "#app",
                    "actions": [
                        {"type": "fill", "selector": "#name", "value": "Ada"},
                        {"type": "select", "selector": "#plan", "value": "home"},
                        {"type": "click", "selector": "#finish"},
                        {"type": "wait", "selector": "#complete", "timeout_ms": 5000},
                    ],
                }
            },
        }

    def test_browser_provider_parses_declarative_journey_replay_and_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            self._write_config(repo, self._browser_config())

            config = ux_capture.load_config(repo)
            assert config is not None

            self.assertEqual(config.provider, "browser")
            self.assertEqual(config.command, ())
            target = config.targets["journey:checkout-complete"]
            self.assertEqual(target.reference_target_id, "screen:confirmation")
            provider = config.browser_config
            self.assertIsInstance(provider, ux_browser_capture.BrowserProviderConfig)
            assert isinstance(provider, ux_browser_capture.BrowserProviderConfig)
            replay = provider.targets[target.target_id]
            self.assertEqual(replay.route, "/checkout")
            self.assertEqual(replay.viewport, "1024x768@1")
            self.assertEqual(
                [action.kind for action in replay.actions],
                ["fill", "select", "click", "wait"],
            )

    def test_state_target_uses_same_bounded_replay_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            self._write_config(repo, self._browser_config(target_kind="state"))

            config = ux_capture.load_config(repo)
            assert config is not None

            target = config.targets["state:signed-in"]
            self.assertEqual(target.source_kind, "state")
            self.assertEqual(target.reference_target_id, "screen:confirmation")
            provider = config.browser_config
            assert isinstance(provider, ux_browser_capture.BrowserProviderConfig)
            self.assertIn("state:signed-in", provider.targets)

    def test_browser_provider_rejects_arbitrary_javascript_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            value = self._browser_config()
            target = value["targets"]["journey:checkout-complete"]  # type: ignore[index]
            target["actions"] = [  # type: ignore[index]
                {"type": "javascript", "value": "document.body.innerHTML = 'owned'"}
            ]
            self._write_config(repo, value)

            with self.assertRaisesRegex(ux_capture.UXCaptureError, "unsupported type"):
                ux_capture.load_config(repo)

    def test_browser_provider_rejects_navigation_to_an_undeclared_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            value = self._browser_config()
            target = value["targets"]["journey:checkout-complete"]  # type: ignore[index]
            target["route"] = "https://example.com/ambient"  # type: ignore[index]
            self._write_config(repo, value)

            with self.assertRaisesRegex(ux_capture.UXCaptureError, "origin-relative route"):
                ux_capture.load_config(repo)

    def test_browser_request_filter_allows_only_declared_origins_and_local_page_schemes(self) -> None:
        allowed = {"http://127.0.0.1:4173"}
        self.assertTrue(
            ux_browser_capture._request_allowed(
                "http://127.0.0.1:4173/assets/app.js",
                allowed,
            )
        )
        self.assertTrue(ux_browser_capture._request_allowed("data:image/png;base64,AA==", allowed))
        self.assertTrue(ux_browser_capture._request_allowed("blob:http://127.0.0.1:4173/id", allowed))
        self.assertFalse(
            ux_browser_capture._request_allowed(
                "https://notifications.example.test/private",
                allowed,
            )
        )

    def test_capture_target_dispatches_to_browser_provider_without_command_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            self._write_config(repo, self._browser_config())
            config = ux_capture.load_config(repo)
            assert config is not None

            def capture(_repo, _current, _provider, target_id, output, **_kwargs):
                self.assertEqual(target_id, "journey:checkout-complete")
                Path(output).write_bytes(PNG)
                return "browser:Chrome/fixture", "1024x768@1"

            def forbidden_runner(*_args, **_kwargs):
                raise AssertionError("browser provider must not invoke the command capture runner")

            with patch.object(ux_browser_capture, "capture", side_effect=capture):
                result = ux_capture.capture_target(
                    repo,
                    current,
                    config,
                    "journey:checkout-complete",
                    runner=forbidden_runner,
                    which=lambda _name: "google-chrome",
                )

            self.assertEqual(result.mime, "image/png")
            self.assertEqual(result.target.source_kind, "journey")
            self.assertEqual(result.target.viewport, "1024x768@1")
            self.assertEqual(result.target.platform, "browser:Chrome/fixture")
            self.assertEqual(result.target.reference_target_id, "screen:confirmation")


if __name__ == "__main__":
    unittest.main()
