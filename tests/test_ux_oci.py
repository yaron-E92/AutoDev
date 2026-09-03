from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import ux_cli, ux_resolver
from automation.ux_oci import OCIUXArtifactResolver
from automation.ux_contract import BUNDLE_SCHEMA, UXBundleError
from automation.ux_oci_bundle import (
    ARTIFACT_TYPE,
    LAYER_MEDIA_TYPE,
    safe_extract_bundle_archive,
    write_bundle_archive,
)
from automation.ux_oci_reference import OCIReferenceError, parse_oci_reference
from automation.ux_oras import OrasClient


def write_bundle(root: Path, *, schema: str = BUNDLE_SCHEMA) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "contract.yaml").write_text("product: demo\n", encoding="utf-8")
    (root / "principles.md").write_text("# Principles\n", encoding="utf-8")
    (root / "prototype.html").write_text("<script>never()</script>\n", encoding="utf-8")
    (root / "ux-manifest.json").write_text(
        json.dumps(
            {
                "schema": schema,
                "product": "demo",
                "contract": "contract.yaml",
                "principles": "principles.md",
                "prototype": "prototype.html",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def archive_bytes(bundle: Path) -> bytes:
    with tempfile.TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / "bundle.tar.gz"
        write_bundle_archive(bundle, target)
        return target.read_bytes()


def manifest_bytes(
    layer: bytes,
    *,
    artifact_type: str = ARTIFACT_TYPE,
    media_type: str = LAYER_MEDIA_TYPE,
    layer_digest: str = "",
) -> bytes:
    value = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": artifact_type,
        "layers": [
            {
                "mediaType": media_type,
                "digest": layer_digest or digest_bytes(layer),
                "size": len(layer),
            }
        ],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FakeOrasRunner:
    def __init__(
        self,
        *,
        manifest: bytes = b"",
        layer: bytes = b"",
        resolved_digest: str = "",
        version: str = "1.3.2",
        fail_command: str = "",
        fail_stderr: str = "",
        omit_push_format: bool = False,
        push_digest: str = "sha256:" + "a" * 64,
    ) -> None:
        self.manifest = manifest
        self.layer = layer
        self.resolved_digest = resolved_digest or (
            digest_bytes(manifest) if manifest else "sha256:" + "b" * 64
        )
        self.version = version
        self.fail_command = fail_command
        self.fail_stderr = fail_stderr
        self.omit_push_format = omit_push_format
        self.push_digest = push_digest
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    @staticmethod
    def _result(code: int = 0, stdout: str = "", stderr: str = ""):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def __call__(self, argv, **kwargs):
        args = list(argv)
        self.calls.append((args, dict(kwargs)))
        command = " ".join(args[1:3])
        if args[1:] == ["version"]:
            return self._result(stdout=f"Version:        {self.version}\n")
        if args[-1] == "--help":
            if args[1] == "resolve":
                return self._result(stdout="resolve target\n")
            if args[1:3] == ["manifest", "fetch"]:
                return self._result(stdout="manifest fetch --output\n")
            if args[1:3] == ["blob", "fetch"]:
                return self._result(stdout="blob fetch --output\n")
            if args[1] == "push":
                text = "push --artifact-type"
                if not self.omit_push_format:
                    text += " --format"
                return self._result(stdout=text + "\n")

        if self.fail_command and (
            args[1] == self.fail_command or command == self.fail_command
        ):
            return self._result(1, stderr=self.fail_stderr)

        if args[1] == "resolve":
            return self._result(stdout=self.resolved_digest + "\n")
        if args[1:3] == ["manifest", "fetch"]:
            output = Path(args[args.index("--output") + 1])
            output.write_bytes(self.manifest)
            return self._result()
        if args[1:3] == ["blob", "fetch"]:
            output = Path(args[args.index("--output") + 1])
            output.write_bytes(self.layer)
            return self._result()
        if args[1] == "push":
            return self._result(
                stdout=json.dumps(
                    {
                        "digest": self.push_digest,
                        "artifactType": ARTIFACT_TYPE,
                    }
                )
                + "\n"
            )
        raise AssertionError(f"unexpected ORAS command: {args}")


def resolver_with(
    runner: FakeOrasRunner,
    cache: Path,
    *,
    environ: dict[str, str] | None = None,
) -> OCIUXArtifactResolver:
    client = OrasClient(
        executable="oras",
        runner=runner,
        environ=environ or {},
    )
    return OCIUXArtifactResolver(client=client, cache_root=cache)


def registry_with(resolver: OCIUXArtifactResolver) -> ux_resolver.UXResolverRegistry:
    registry = ux_resolver.UXResolverRegistry()
    registry.register(resolver)
    return registry


class OCIReferenceTests(unittest.TestCase):
    def test_parses_tag_and_digest_references(self):
        tagged = parse_oci_reference("oci://ghcr.io/yaron-e92/ux/demo:v1")
        immutable = parse_oci_reference(
            "oci://registry.example:5000/team/ux/demo@sha256:" + "a" * 64
        )

        self.assertEqual(tagged.registry, "ghcr.io")
        self.assertEqual(tagged.repository, "yaron-e92/ux/demo")
        self.assertEqual(tagged.tag, "v1")
        self.assertFalse(tagged.immutable)
        self.assertEqual(immutable.registry, "registry.example:5000")
        self.assertTrue(immutable.immutable)

    def test_rejects_credentials_implicit_latest_and_uppercase_repository(self):
        invalid = (
            "oci://user:secret@ghcr.io/team/ux/demo:v1",
            "oci://ghcr.io/team/ux/demo",
            "oci://ghcr.io/Team/ux/demo:v1",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(OCIReferenceError):
                parse_oci_reference(value)


class OCIResolverTests(unittest.TestCase):
    def _fixture(self, root: Path, *, schema: str = BUNDLE_SCHEMA):
        bundle = root / "bundle"
        write_bundle(bundle, schema=schema)
        layer = archive_bytes(bundle)
        manifest = manifest_bytes(layer)
        digest = digest_bytes(manifest)
        return bundle, layer, manifest, digest

    def test_immutable_pull_verifies_and_reuses_content_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, layer, manifest, digest = self._fixture(root)
            runner = FakeOrasRunner(manifest=manifest, layer=layer)
            resolver = resolver_with(runner, root / "cache")
            registry = registry_with(resolver)
            reference = f"oci://registry.example/team/ux/demo@{digest}"

            first = registry.resolve(reference)
            second = registry.resolve(reference)

        self.assertEqual(first.immutable_identity, digest)
        self.assertEqual(first.manifest.product, "demo")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        manifest_calls = [
            call for call, _kwargs in runner.calls if call[1:3] == ["manifest", "fetch"]
        ]
        blob_calls = [
            call for call, _kwargs in runner.calls if call[1:3] == ["blob", "fetch"]
        ]
        self.assertEqual(len(manifest_calls), 1)
        self.assertEqual(len(blob_calls), 1)

    def test_tag_resolves_interactively_but_unattended_requires_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, layer, manifest, digest = self._fixture(root)
            runner = FakeOrasRunner(
                manifest=manifest,
                layer=layer,
                resolved_digest=digest,
            )
            resolver = resolver_with(runner, root / "cache")
            registry = registry_with(resolver)
            tagged = "oci://registry.example/team/ux/demo:approved"

            artifact = registry.resolve(tagged)
            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                registry.resolve(
                    tagged,
                    policy=ux_resolver.ResolutionPolicy(
                        unattended=True,
                        require_immutable_reference=True,
                    ),
                )

        self.assertEqual(
            artifact.immutable_reference,
            f"oci://registry.example/team/ux/demo@{digest}",
        )
        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_MUTABLE)

    def test_manifest_digest_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, layer, manifest, _digest = self._fixture(root)
            runner = FakeOrasRunner(manifest=manifest, layer=layer)
            registry = registry_with(resolver_with(runner, root / "cache"))
            wrong = "sha256:" + "f" * 64

            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                registry.resolve(
                    f"oci://registry.example/team/ux/demo@{wrong}"
                )

        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_IDENTITY)

    def test_wrong_artifact_type_is_rejected_before_blob_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            write_bundle(bundle)
            layer = archive_bytes(bundle)
            manifest = manifest_bytes(layer, artifact_type="application/example")
            digest = digest_bytes(manifest)
            runner = FakeOrasRunner(manifest=manifest, layer=layer)
            registry = registry_with(resolver_with(runner, root / "cache"))

            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                registry.resolve(
                    f"oci://registry.example/team/ux/demo@{digest}"
                )

        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_MALFORMED)
        self.assertFalse(
            any(call[1:3] == ["blob", "fetch"] for call, _kwargs in runner.calls)
        )

    def test_layer_digest_mismatch_fails_closed_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            write_bundle(bundle)
            layer = archive_bytes(bundle)
            wrong_layer_digest = "sha256:" + "c" * 64
            manifest = manifest_bytes(layer, layer_digest=wrong_layer_digest)
            digest = digest_bytes(manifest)
            runner = FakeOrasRunner(manifest=manifest, layer=layer)
            registry = registry_with(resolver_with(runner, root / "cache"))

            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                registry.resolve(
                    f"oci://registry.example/team/ux/demo@{digest}"
                )

        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_IDENTITY)

    def test_malformed_bundle_schema_has_specific_classification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _bundle, layer, _manifest, _digest = self._fixture(
                root,
                schema="autodev.ux.bundle/v999",
            )
            manifest = manifest_bytes(layer)
            digest = digest_bytes(manifest)
            runner = FakeOrasRunner(manifest=manifest, layer=layer)
            registry = registry_with(resolver_with(runner, root / "cache"))

            with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                registry.resolve(
                    f"oci://registry.example/team/ux/demo@{digest}"
                )

        self.assertEqual(raised.exception.classification, ux_resolver.FAILURE_SCHEMA)

    def test_hostile_tar_member_never_escapes_staging_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "hostile.tar.gz"
            with archive.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
                    with tarfile.open(fileobj=zipped, mode="w") as tar:
                        info = tarfile.TarInfo("../escape.txt")
                        payload = b"owned"
                        info.size = len(payload)
                        tar.addfile(info, io.BytesIO(payload))
            destination = root / "out"

            with self.assertRaises(UXBundleError) as raised:
                safe_extract_bundle_archive(archive, destination)

            self.assertIn("unsafe", str(raised.exception))
            self.assertFalse((root / "escape.txt").exists())

    def test_missing_old_or_incapable_oras_has_actionable_classification(self):
        missing = OrasClient(which=lambda _name: None, environ={})
        with self.assertRaises(ux_resolver.UXResolutionError) as missing_error:
            missing.require_tool()
        self.assertEqual(missing_error.exception.classification, ux_resolver.FAILURE_TOOL)

        old_runner = FakeOrasRunner(version="1.1.0")
        old = OrasClient(executable="oras", runner=old_runner, environ={})
        with self.assertRaises(ux_resolver.UXResolutionError) as old_error:
            old.require_tool()
        self.assertEqual(
            old_error.exception.classification,
            ux_resolver.FAILURE_TOOL_VERSION,
        )

        incapable_runner = FakeOrasRunner(omit_push_format=True)
        incapable = OrasClient(executable="oras", runner=incapable_runner, environ={})
        with self.assertRaises(ux_resolver.UXResolutionError) as incapable_error:
            incapable.require_tool()
        self.assertEqual(
            incapable_error.exception.classification,
            ux_resolver.FAILURE_TOOL_VERSION,
        )

    def test_auth_and_not_found_are_distinct(self):
        for stderr, expected in (
            ("Error response from registry: unauthorized", ux_resolver.FAILURE_AUTH),
            ("manifest unknown: not found", ux_resolver.FAILURE_NOT_FOUND),
        ):
            with self.subTest(stderr=stderr):
                runner = FakeOrasRunner(
                    fail_command="resolve",
                    fail_stderr=stderr,
                )
                client = OrasClient(executable="oras", runner=runner, environ={})
                with self.assertRaises(ux_resolver.UXResolutionError) as raised:
                    client.invoke(
                        ["resolve"],
                        ["registry.example/team/ux/demo:v1"],
                        registry="registry.example",
                    )
                self.assertEqual(raised.exception.classification, expected)

    def test_github_token_uses_stdin_and_is_redacted(self):
        secret = "github_pat_secret-value-that-must-never-leak"
        runner = FakeOrasRunner(
            fail_command="resolve",
            fail_stderr=f"unauthorized token={secret}",
        )
        client = OrasClient(
            executable="oras",
            runner=runner,
            environ={
                "GITHUB_TOKEN": secret,
                "GITHUB_ACTOR": "ci-user",
            },
        )

        with self.assertRaises(ux_resolver.UXResolutionError) as raised:
            client.invoke(
                ["resolve"],
                ["ghcr.io/example/ux/demo:v1"],
                registry="ghcr.io",
            )

        command, kwargs = next(
            (call, kwargs)
            for call, kwargs in runner.calls
            if len(call) > 1 and call[1] == "resolve" and "--help" not in call
        )
        self.assertNotIn(secret, command)
        self.assertEqual(kwargs["input"], secret + "\n")
        self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
        self.assertNotIn(secret, str(raised.exception))

    def test_plain_http_is_loopback_only(self):
        runner = FakeOrasRunner()
        local = OrasClient(
            executable="oras",
            runner=runner,
            environ={"AUTODEV_OCI_PLAIN_HTTP": "1"},
        )
        local.invoke(
            ["resolve"],
            ["127.0.0.1:5000/team/ux/demo:v1"],
            registry="127.0.0.1:5000",
        )
        command, _kwargs = next(
            (call, kwargs)
            for call, kwargs in runner.calls
            if len(call) > 1 and call[1] == "resolve" and "--help" not in call
        )
        self.assertIn("--plain-http", command)

        remote = OrasClient(
            executable="oras",
            runner=FakeOrasRunner(),
            environ={"AUTODEV_OCI_PLAIN_HTTP": "1"},
        )
        with self.assertRaises(ux_resolver.UXResolutionError) as raised:
            remote.invoke(
                ["resolve"],
                ["registry.example/team/ux/demo:v1"],
                registry="registry.example",
            )
        self.assertEqual(
            raised.exception.classification,
            ux_resolver.FAILURE_TRANSPORT,
        )

    def test_paths_with_spaces_remain_atomic_argv_elements(self):
        with tempfile.TemporaryDirectory(prefix="autodev ux test ") as temp_dir:
            root = Path(temp_dir)
            _bundle, layer, manifest, digest = self._fixture(root)
            runner = FakeOrasRunner(manifest=manifest, layer=layer)
            registry = registry_with(resolver_with(runner, root / "cache with spaces"))

            registry.resolve(
                f"oci://registry.example/team/ux/demo@{digest}"
            )

        blob_call, kwargs = next(
            (call, kwargs)
            for call, kwargs in runner.calls
            if call[1:3] == ["blob", "fetch"]
        )
        output_path = blob_call[blob_call.index("--output") + 1]
        self.assertIn(" ", output_path)
        self.assertIsInstance(blob_call, list)
        self.assertNotIn("shell", kwargs)

    def test_publish_validates_bundle_and_returns_immutable_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "bundle"
            write_bundle(bundle)
            runner = FakeOrasRunner()
            resolver = resolver_with(runner, root / "cache")
            registry = registry_with(resolver)

            result = registry.publish(
                bundle,
                "oci://ghcr.io/yaron-e92/ux/demo:v1",
            )

        self.assertEqual(result.immutable_identity, "sha256:" + "a" * 64)
        self.assertEqual(
            result.immutable_reference,
            "oci://ghcr.io/yaron-e92/ux/demo@sha256:" + "a" * 64,
        )
        push_call, kwargs = next(
            (call, kwargs)
            for call, kwargs in runner.calls
            if len(call) > 1 and call[1] == "push" and "--help" not in call
        )
        self.assertIn("--artifact-type", push_call)
        self.assertIn(ARTIFACT_TYPE, push_call)
        self.assertTrue(any(LAYER_MEDIA_TYPE in item for item in push_call))
        self.assertIsNotNone(kwargs["cwd"])


class OCIUXCliTests(unittest.TestCase):
    def test_doctor_reports_missing_oras_for_configured_oci_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            (repo / ".autodev").mkdir()
            (repo / ".autodev" / "repo.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "ux": {
                            "enabled": True,
                            "artifact": "oci://ghcr.io/example/ux/demo@sha256:"
                            + "a" * 64,
                            "product": "demo",
                        },
                    }
                ),
                encoding="utf-8",
            )
            resolver = OCIUXArtifactResolver(
                client=OrasClient(which=lambda _name: None, environ={}),
                cache_root=Path(temp_dir) / "cache",
            )
            registry = registry_with(resolver)

            code = ux_cli.run_cli(
                ["doctor", "--repo", str(repo), "--json"],
                registry=registry,
            )

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
