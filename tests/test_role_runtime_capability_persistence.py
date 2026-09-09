from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import role_runtime_capabilities


class RoleRuntimeCapabilityPersistenceTests(unittest.TestCase):
    def test_persist_checkpoints_safe_route_evidence_in_sidecar_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            manifest_path = current / "run-manifest.json"
            manifest_path.write_text(json.dumps({"run_id": "run-1"}) + "\n", encoding="utf-8")
            capability = role_runtime_capabilities.ImageInputCapability(
                state=role_runtime_capabilities.STATE_SUPPORTED,
                runtime="fake-runtime",
                provider="fake-provider",
                model="vision-model",
                source="fake authoritative metadata",
                detail="safe diagnostic",
                metadata_sha256="abc123",
            )

            evidence_path = role_runtime_capabilities.persist(repo, capability)

            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            checkpoint = manifest[role_runtime_capabilities.MANIFEST_KEY]
            self.assertEqual(checkpoint, evidence)
            self.assertEqual(checkpoint["route"], "fake-provider/vision-model")
            self.assertEqual(checkpoint["source"], "fake authoritative metadata")
            self.assertEqual(checkpoint["metadata_sha256"], "abc123")
            self.assertTrue(checkpoint["fingerprint"])
            self.assertEqual(manifest["run_id"], "run-1")


if __name__ == "__main__":
    unittest.main()
