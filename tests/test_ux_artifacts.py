from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import ux_cache, ux_cli, ux_policy, ux_resolver, ux_workflow
from automation.ux_contract import BUNDLE_SCHEMA, UXBundleError, load_manifest


def write_bundle(root: Path, *, product: str = "demo") -> None:
    (root / "screens").mkdir(parents=True, exist_ok=True)
    (root / "contract.yaml").write_text("product: demo\n", encoding="utf-8")
    (root / "principles.md").write_text("# Principles\n", encoding="utf-8")
    (root / "journeys.yaml").write_text("journeys: []\n", encoding="utf-8")
    (root / "prototype.html").write_text("<script>window.neverExecute = true</script>\n", encoding="utf-8")
    (root / "screens" / "home.png").write_bytes(b"not-executed-reference-bytes")
    (root / "ux-manifest.json").write_text(
        json.dumps(
            {
                "schema": BUNDLE_SCHEMA,
                "product": product,
                "contract": "contract.yaml",
                "principles": "principles.md",
                "prototype": "prototype.html",
                "journeys": "journeys.yaml",
                "references": {"root": "screens"},
                "screens": {"home": "screens/home.png"},
                "states": {"empty": "screens/home.png"},
                "shared": {"artifact": "fake://shared@sha256:parent"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class FakeResolver:
    kind = "fake"

    def __init__(self, root: Path, *, identity: str = "sha256:demo") -> None:
        self.root = root
        self._identity = identity

    def supports(self, reference: str) -> bool:
        return reference.startswith("fake://")

    def resolve(
        self,
        reference: str,
        policy: ux_resolver.ResolutionPolicy,
    ) -> ux_resolver.ResolvedUXArtifact:
        immutable = reference if "@sha256:" in reference else "fake://demo@sha256:demo"
        if policy.require_immutable_reference and "@sha256:" not in reference:
            raise ux_resolver.UXResolutionError(
                "mutable fake reference is not allowed unattended",
                classification=ux_resolver.FAILURE_MUTABLE,
                resolver_kind=self.kind,
            )
        return ux_resolver.ResolvedUXArtifact(
            immutable_identity=self._identity,
            immutable_reference=immutable,
            local_root=self.root,
            manifest=load_manifest(self.root),
            source_reference=reference,
            resolver_kind=self.kind,
            cache_hit=False,
            provenance={"transport": "fake"},
        )

    def inspect(self, reference: str) -> dict[str, object]:
        return {
            "resolver_kind": self.kind,
            "configured_reference": ux_resolver.safe_reference(reference),
            "immutable_identity": self._identity,
        }

    def identity(self, reference: str) -> str:
        return self._identity


class UXArtifactTests(unittest.TestCase):
    def test_v1_bundle_validates_and_selects_targeted_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_bundle(root)

            manifest = load_manifest(root)
            selected = manifest.selected_paths(
                screen_ids=("home",),
                state_ids=("missing",),
                include_journeys=True,
            )

        self.assertEqual(manifest.schema, BUNDLE_SCHEMA)
        self.assertIn("contract.yaml", selected)
        self.assertIn("principles.md", selected)
        self.assertIn("journeys.yaml", selected)
        self.assertIn("screens/home.png", selected)
        self.assertNotIn("prototype.html", selected)

    def test_bundle_rejects_parent_and_windows_drive_paths(self):
        for unsafe in ("../outside", "C:/outside"):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                write_bundle(root)
                manifest_path = root / "ux-manifest.json"
                value = json.loads(manifest_path.read_text(encoding="utf-8"))
                value["contract"] = unsafe
                manifest_path.write_text(json.dumps(value), encoding="utf-8")

                with self.assertRaises(UXBundleError):
                    load_manifest(root)

    def test_fake_resolver_proves_registry_is_transport_neutral(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_bundle(root)
            registry = ux_resolver.UXResolverRegistry()
            registry.register(FakeResolver(root))

            artifact = registry.resolve("fake://demo@sha256:demo")
            identity = registry.identity("fake://demo@sha256:demo")

        self.assertEqual(registry.kinds, ("fake",))
        self.assertEqual(artifact.resolver_kind, "fake")
        self.assertEqual(artifact.immutable_identity, "sha256:demo")
        self.assertEqual(identity, "sha256:demo")

    def test_unattended_resolution_rejects_mutable_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_bundle(root)
            registry = ux_resolver.UXResolverRegistry()
            registry.register(FakeResolver(root))

            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                registry.resolve(
                    "fake://demo:latest",
                    policy=ux_resolver.ResolutionPolicy(
                        unattended=True,
                        require_immutable_reference=True,
                    ),
                )

        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_MUTABLE)

    def test_repository_policy_is_optional_and_product_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            self.assertFalse(ux_policy.load_policy(repo).enabled)

            (repo / ".autodev").mkdir()
            (repo / ".autodev" / "repo.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "ux": {
                            "enabled": True,
                            "artifact": "fake://demo@sha256:demo",
                            "product": "expected",
                        },
                    }
                ),
                encoding="utf-8",
            )
            bundle = Path(temp_dir) / "bundle"
            bundle.mkdir()
            write_bundle(bundle, product="actual")
            registry = ux_resolver.UXResolverRegistry()
            registry.register(FakeResolver(bundle))

            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                ux_workflow.resolve_configured(
                    repo,
                    registry=registry,
                    unattended=True,
                )

        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_IDENTITY)

    def test_cache_population_is_atomic_reusable_and_corruption_aware(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir) / "cache"

            def produce(target: Path) -> None:
                write_bundle(target)

            first, first_hit = ux_cache.populate("sha256:demo", produce, root=cache)
            second, second_hit = ux_cache.populate("sha256:demo", produce, root=cache)
            self.assertFalse(first_hit)
            self.assertTrue(second_hit)
            self.assertEqual(first, second)
            self.assertTrue(ux_cache.validate_entry(first, "sha256:demo"))

            (first / "contract.yaml").write_text("tampered: true\n", encoding="utf-8")
            self.assertFalse(ux_cache.validate_entry(first, "sha256:demo"))

            repaired, repaired_hit = ux_cache.populate("sha256:demo", produce, root=cache)
            self.assertFalse(repaired_hit)
            self.assertTrue(ux_cache.validate_entry(repaired, "sha256:demo"))

    def test_cli_lock_rewrites_only_configured_reference_to_immutable_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            bundle = Path(temp_dir) / "bundle"
            repo.mkdir()
            bundle.mkdir()
            write_bundle(bundle)
            (repo / ".autodev").mkdir()
            config = repo / ".autodev" / "repo.json"
            config.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "other": {"preserved": True},
                        "ux": {
                            "enabled": True,
                            "artifact": "fake://demo:approved",
                            "product": "demo",
                        },
                    }
                ),
                encoding="utf-8",
            )
            registry = ux_resolver.UXResolverRegistry()
            registry.register(FakeResolver(bundle))

            code = ux_cli.run_cli(
                ["lock", "--repo", str(repo), "--json"],
                registry=registry,
            )
            updated = json.loads(config.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(updated["ux"]["artifact"], "fake://demo@sha256:demo")
        self.assertTrue(updated["other"]["preserved"])

    def test_safe_diagnostics_strip_reference_credentials_and_query(self):
        safe = ux_resolver.safe_reference(
            "fake://user:secret@example.test/product?token=secret#fragment"
        )
        self.assertEqual(safe, "fake://example.test/product")
        self.assertNotIn("secret", safe)


if __name__ == "__main__":
    unittest.main()
