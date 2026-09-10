from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from automation import ux_capture, ux_multimodal, ux_resolver
from automation.ux_contract import UXBundleManifest


REFERENCE_PNG = b"\x89PNG\r\n\x1a\npinned-browser-reference"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args) -> None:
        return


class _PassRuntime:
    name = "fake-browser-e2e"

    def __init__(self) -> None:
        self.attachments: tuple[Path, ...] = ()

    def multimodal_verifier_capability(self, _repo, **_kwargs) -> str:
        return ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS

    def invoke_multimodal_verifier(
        self,
        _repo,
        *,
        attachments,
        **_kwargs,
    ) -> ux_multimodal.RuntimeVerificationResult:
        self.attachments = tuple(attachments)
        reference_hash = hashlib.sha256(self.attachments[0].read_bytes()).hexdigest()
        implementation_hash = hashlib.sha256(self.attachments[1].read_bytes()).hexdigest()
        return ux_multimodal.RuntimeVerificationResult(
            payload={
                "verdict": "pass",
                "comparisons": [
                    {
                        "target_id": "journey:checkout-complete",
                        "source_kind": "journey",
                        "source_id": "checkout-complete",
                        "reference_sha256": reference_hash,
                        "implementation_sha256": implementation_hash,
                        "status": "satisfied",
                    }
                ],
                "ux_findings": [],
                "repair_brief": "",
            },
            runtime=self.name,
            model="fake/vision",
            capability=ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
            structured_output_mode="fixture",
        )


class BrowserCaptureE2ETests(unittest.TestCase):
    @staticmethod
    def _browser() -> str | None:
        for candidate in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        ):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None

    def test_real_browser_replays_selected_journey_and_feeds_multimodal_comparison(self) -> None:
        if not (sys.platform.startswith("linux") and os.environ.get("CI")):
            self.skipTest("real browser fixture is enforced on Linux CI")
        browser = self._browser()
        self.assertIsNotNone(
            browser,
            "Linux CI must provide Chrome/Chromium for the first-party browser capture fixture",
        )
        assert browser is not None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                """<!doctype html>
<html>
<head><meta charset=\"utf-8\"><title>AutoDev fixture</title></head>
<body>
  <main id=\"app\">
    <h1>Checkout</h1>
    <button id=\"finish\" type=\"button\">Finish</button>
  </main>
  <script>
    document.querySelector('#finish').addEventListener('click', () => {
      document.querySelector('#app').innerHTML = '<h1 id=\"complete\">Complete</h1><p>Welcome home.</p>';
    });
  </script>
</body>
</html>
""",
                encoding="utf-8",
            )
            handler = functools.partial(_QuietHandler, directory=str(site))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = int(server.server_address[1])
                origin = f"http://127.0.0.1:{port}"
                repo = root / "repo"
                current = repo / ".autodev-run" / "current"
                (repo / ".autodev").mkdir(parents=True)
                current.mkdir(parents=True)

                artifact_root = root / "ux"
                (artifact_root / "screens").mkdir(parents=True)
                (artifact_root / "journeys").mkdir(parents=True)
                (artifact_root / "screens" / "confirmation.png").write_bytes(REFERENCE_PNG)
                (artifact_root / "journeys" / "checkout.json").write_text(
                    json.dumps({"id": "checkout-complete"}) + "\n",
                    encoding="utf-8",
                )
                artifact = ux_resolver.ResolvedUXArtifact(
                    immutable_identity="sha256:pinned-browser-e2e",
                    immutable_reference="fake://browser@sha256:pinned-browser-e2e",
                    local_root=artifact_root,
                    manifest=UXBundleManifest(
                        schema="autodev.ux.bundle/v1",
                        product="browser-demo",
                        contract="contract.yaml",
                        screens={"confirmation": "screens/confirmation.png"},
                        states={},
                        journey_files={"checkout-complete": "journeys/checkout.json"},
                    ),
                    source_reference="fake://browser@sha256:pinned-browser-e2e",
                    resolver_kind="fake",
                )
                (current / "state.json").write_text(
                    json.dumps(
                        {"UXArtifact": {"immutable_identity": artifact.immutable_identity}}
                    ),
                    encoding="utf-8",
                )
                (current / "ux-context-verifier.json").write_text(
                    json.dumps(
                        {
                            "ux_artifact": {
                                "immutable_identity": artifact.immutable_identity,
                                "product": "browser-demo",
                            },
                            "ux_context": {
                                "contract": "contract.yaml",
                                "principles": "",
                                "screens": [],
                                "states": [],
                                "journeys": ["checkout-complete"],
                            },
                            "ux_context_fingerprint": "browser-e2e-context",
                        }
                    ),
                    encoding="utf-8",
                )
                (current / "run-manifest.json").write_text(
                    json.dumps({"ux_artifact": {"immutable_identity": artifact.immutable_identity}})
                    + "\n",
                    encoding="utf-8",
                )
                (repo / ux_capture.CAPTURE_CONFIG).write_text(
                    json.dumps(
                        {
                            "schema": ux_capture.CAPTURE_SCHEMA,
                            "provider": "browser",
                            "timeout_seconds": 45,
                            "browser": {
                                "application": {
                                    "origin": origin,
                                    "ready_path": "/index.html",
                                },
                                "allowed_origins": [origin],
                                "executable": browser,
                            },
                            "targets": {
                                "journey:checkout-complete": {
                                    "source_kind": "journey",
                                    "source_id": "checkout-complete",
                                    "output": "checkout-complete.png",
                                    "reference": "screen:confirmation",
                                    "route": "/index.html",
                                    "viewport": "800x600@1",
                                    "ready_selector": "#app",
                                    "actions": [
                                        {"type": "click", "selector": "#finish"},
                                        {
                                            "type": "wait",
                                            "selector": "#complete",
                                            "timeout_ms": 5000,
                                        },
                                    ],
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                runtime = _PassRuntime()
                with patch.object(
                    ux_multimodal.ux_workflow,
                    "resolve_configured",
                    return_value=artifact,
                ):
                    result_path = ux_multimodal.run_verification(
                        repo,
                        runtime,
                        runner=lambda *_args, **_kwargs: None,
                    )

                result = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(result["status"], "pass")
                self.assertEqual(len(runtime.attachments), 2)
                self.assertEqual(runtime.attachments[0], artifact_root / "screens" / "confirmation.png")
                screenshot = runtime.attachments[1]
                self.assertTrue(screenshot.is_file())
                self.assertTrue(screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(result["reference_evidence"][0]["target_id"], "journey:checkout-complete")
                self.assertEqual(
                    result["reference_evidence"][0]["reference_target_id"],
                    "screen:confirmation",
                )
                self.assertEqual(result["reference_evidence"][0]["path"], "screens/confirmation.png")
                self.assertEqual(
                    result["implementation_evidence"][0]["target_id"],
                    "journey:checkout-complete",
                )
                self.assertEqual(result["implementation_evidence"][0]["viewport"], "800x600@1")
                self.assertTrue(
                    str(result["implementation_evidence"][0]["platform"]).startswith("browser:")
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
