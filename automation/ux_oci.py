from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from automation import ux_cache, ux_resolver
from automation.ux_contract import load_manifest, manifest_summary
from automation.ux_oci_bundle import (
    ARTIFACT_TYPE,
    LAYER_MEDIA_TYPE,
    MAX_ARCHIVE_BYTES,
    safe_extract_bundle_archive,
    sha256_file,
    write_bundle_archive,
)
from automation.ux_oci_reference import (
    OCIReference,
    OCIReferenceError,
    normalize_digest,
    parse_oci_reference,
)
from automation.ux_oras import OrasClient


class OCIUXArtifactResolver:
    kind = "oci"

    def __init__(
        self,
        *,
        client: OrasClient | None = None,
        cache_root: Path | None = None,
    ) -> None:
        self.client = client or OrasClient()
        self.cache_root = cache_root.expanduser().resolve() if cache_root else None

    def supports(self, reference: str) -> bool:
        return str(reference or "").strip().startswith("oci://")

    def _parse(self, reference: str) -> OCIReference:
        try:
            return parse_oci_reference(reference)
        except OCIReferenceError as exc:
            raise ux_resolver.UXResolutionError(
                str(exc),
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            ) from exc

    def _resolve_digest(self, reference: OCIReference) -> str:
        if reference.digest:
            return reference.digest
        completed = self.client.invoke(
            ["resolve"],
            [reference.oras_target],
            registry=reference.registry,
        )
        output = str(getattr(completed, "stdout", "") or "").strip()
        candidate = output.splitlines()[-1].strip() if output else ""
        try:
            return normalize_digest(candidate)
        except OCIReferenceError as exc:
            raise ux_resolver.UXResolutionError(
                "ORAS resolve did not return a valid sha256 manifest digest",
                classification=ux_resolver.FAILURE_IDENTITY,
                resolver_kind=self.kind,
            ) from exc

    @staticmethod
    def _manifest_digest(payload: bytes) -> str:
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def _fetch_manifest(
        self,
        reference: OCIReference,
        digest: str,
    ) -> dict[str, object]:
        target = reference.with_digest(digest)
        with tempfile.TemporaryDirectory(prefix="autodev-ux-manifest-") as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            self.client.invoke(
                ["manifest", "fetch", "--output", str(path)],
                [target.oras_target],
                registry=reference.registry,
            )
            try:
                payload = path.read_bytes()
            except OSError as exc:
                raise ux_resolver.UXResolutionError(
                    "ORAS manifest fetch did not produce the requested manifest file",
                    classification=ux_resolver.FAILURE_TRANSPORT,
                    resolver_kind=self.kind,
                ) from exc
        if self._manifest_digest(payload) != digest:
            raise ux_resolver.UXResolutionError(
                "OCI manifest digest mismatch after fetch",
                classification=ux_resolver.FAILURE_IDENTITY,
                resolver_kind=self.kind,
            )
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ux_resolver.UXResolutionError(
                "OCI UX artifact manifest is not valid UTF-8 JSON",
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            ) from exc
        if not isinstance(value, dict) or value.get("schemaVersion") != 2:
            raise ux_resolver.UXResolutionError(
                "OCI UX artifact manifest must be a schemaVersion 2 object",
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            )
        artifact_type = str(value.get("artifactType", "") or "")
        if artifact_type != ARTIFACT_TYPE:
            raise ux_resolver.UXResolutionError(
                f"unexpected OCI artifact type {artifact_type!r}; expected {ARTIFACT_TYPE!r}",
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            )
        layers = value.get("layers")
        if not isinstance(layers, list) or len(layers) != 1 or not isinstance(layers[0], dict):
            raise ux_resolver.UXResolutionError(
                "OCI UX artifact must contain exactly one bundle layer",
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            )
        layer = layers[0]
        media_type = str(layer.get("mediaType", "") or "")
        if media_type != LAYER_MEDIA_TYPE:
            raise ux_resolver.UXResolutionError(
                f"unexpected OCI UX layer media type {media_type!r}",
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            )
        try:
            layer_digest = normalize_digest(layer.get("digest"))
        except OCIReferenceError as exc:
            raise ux_resolver.UXResolutionError(
                "OCI UX bundle layer has an invalid digest",
                classification=ux_resolver.FAILURE_IDENTITY,
                resolver_kind=self.kind,
            ) from exc
        size = layer.get("size")
        if not isinstance(size, int) or size < 0 or size > MAX_ARCHIVE_BYTES:
            raise ux_resolver.UXResolutionError(
                f"OCI UX bundle layer exceeds archive size limit ({MAX_ARCHIVE_BYTES} bytes)",
                classification=ux_resolver.FAILURE_UNSAFE,
                resolver_kind=self.kind,
            )
        return {
            "artifact_type": artifact_type,
            "layer_digest": layer_digest,
            "layer_size": size,
        }

    def _cached_provenance(self, digest: str) -> dict[str, object]:
        meta = ux_cache.entry_metadata(digest, root=self.cache_root)
        raw = meta.get("metadata", {}) if isinstance(meta, dict) else {}
        value = dict(raw) if isinstance(raw, dict) else {}
        if (
            value.get("resolver_kind") != self.kind
            or value.get("artifact_type") != ARTIFACT_TYPE
            or not value.get("layer_digest")
        ):
            return {}
        return value

    def _pull_into_cache(
        self,
        reference: OCIReference,
        digest: str,
        descriptor: dict[str, object],
    ) -> tuple[Path, bool]:
        layer_digest = str(descriptor["layer_digest"])
        layer_size = int(descriptor["layer_size"])
        layer_target = f"{reference.registry}/{reference.repository}@{layer_digest}"

        def producer(staging: Path) -> None:
            archive = staging.parent / (staging.name + ".tar.gz")
            try:
                self.client.invoke(
                    ["blob", "fetch", "--output", str(archive)],
                    [layer_target],
                    registry=reference.registry,
                )
                try:
                    actual_size = archive.stat().st_size
                except OSError as exc:
                    raise ux_resolver.UXResolutionError(
                        "ORAS blob fetch did not produce the UX bundle layer",
                        classification=ux_resolver.FAILURE_TRANSPORT,
                        resolver_kind=self.kind,
                    ) from exc
                if actual_size != layer_size:
                    raise ux_resolver.UXResolutionError(
                        "OCI UX bundle layer size mismatch after fetch",
                        classification=ux_resolver.FAILURE_IDENTITY,
                        resolver_kind=self.kind,
                    )
                if sha256_file(archive) != layer_digest:
                    raise ux_resolver.UXResolutionError(
                        "OCI UX bundle layer digest mismatch after fetch",
                        classification=ux_resolver.FAILURE_IDENTITY,
                        resolver_kind=self.kind,
                    )
                safe_extract_bundle_archive(archive, staging)
            finally:
                archive.unlink(missing_ok=True)

        metadata = {
            "resolver_kind": self.kind,
            "artifact_type": ARTIFACT_TYPE,
            "layer_digest": layer_digest,
            "layer_size": layer_size,
            "registry": reference.registry,
            "repository": reference.repository,
        }
        return ux_cache.populate(
            digest,
            producer,
            root=self.cache_root,
            metadata=metadata,
        )

    def resolve(
        self,
        reference: str,
        policy: ux_resolver.ResolutionPolicy,
    ) -> ux_resolver.ResolvedUXArtifact:
        parsed = self._parse(reference)
        if not parsed.immutable and (policy.unattended or policy.require_immutable_reference):
            raise ux_resolver.UXResolutionError(
                "unattended OCI UX resolution requires an immutable digest reference; "
                "run autodev ux lock interactively first",
                classification=ux_resolver.FAILURE_MUTABLE,
                resolver_kind=self.kind,
            )
        digest = self._resolve_digest(parsed)
        cached = self._cached_provenance(digest)
        cache_path = ux_cache.entry_path(digest, root=self.cache_root)
        if cached and ux_cache.validate_entry(cache_path, digest):
            root = cache_path
            cache_hit = True
            descriptor = cached
        else:
            descriptor = self._fetch_manifest(parsed, digest)
            root, cache_hit = self._pull_into_cache(parsed, digest, descriptor)
        manifest = load_manifest(root)
        status = self.client.require_tool()
        immutable = parsed.with_digest(digest)
        provenance = {
            "registry": parsed.registry,
            "repository": parsed.repository,
            "tag": parsed.tag,
            "artifact_type": str(descriptor.get("artifact_type", ARTIFACT_TYPE)),
            "layer_digest": str(descriptor.get("layer_digest", "")),
            "oras_version": status.version,
            "credential_source": self.client.credential_source(parsed.registry),
        }
        return ux_resolver.ResolvedUXArtifact(
            immutable_identity=digest,
            immutable_reference=immutable.source_reference,
            local_root=root,
            manifest=manifest,
            source_reference=parsed.source_reference,
            resolver_kind=self.kind,
            cache_hit=cache_hit,
            provenance=provenance,
        )

    def inspect(self, reference: str) -> dict[str, object]:
        parsed = self._parse(reference)
        artifact = self.resolve(
            reference,
            ux_resolver.ResolutionPolicy(
                unattended=False,
                require_immutable_reference=False,
            ),
        )
        files = [
            path
            for path in artifact.local_root.rglob("*")
            if path.is_file() and path.name != ux_cache.CACHE_META
        ]
        return {
            **artifact.safe_evidence(),
            "registry": parsed.registry,
            "repository": parsed.repository,
            "tag": parsed.tag,
            "resolved_digest": artifact.immutable_identity,
            "artifact_type": artifact.provenance.get("artifact_type", ""),
            "layer_digest": artifact.provenance.get("layer_digest", ""),
            "bundle": manifest_summary(artifact.manifest),
            "bundle_files": len(files),
            "cache_root": str(artifact.local_root),
        }

    def identity(self, reference: str) -> str:
        return self._resolve_digest(self._parse(reference))

    def doctor(self, reference: str = "") -> dict[str, object]:
        status = self.client.status()
        result = {
            "tool": "oras",
            **status.to_json(),
            "artifact_type": ARTIFACT_TYPE,
            "layer_media_type": LAYER_MEDIA_TYPE,
        }
        if reference:
            try:
                parsed = self._parse(reference)
                result["registry"] = parsed.registry
                result["repository"] = parsed.repository
                result["credential_source"] = self.client.credential_source(parsed.registry)
            except ux_resolver.UXResolutionError as exc:
                result["reference_error"] = str(exc)
        return result

    def publish(
        self,
        bundle_root: Path,
        reference: str,
    ) -> ux_resolver.PublishedUXArtifact:
        parsed = self._parse(reference)
        if parsed.digest or not parsed.tag:
            raise ux_resolver.UXResolutionError(
                "OCI UX publication target must use a human-readable tag, not a digest",
                classification=ux_resolver.FAILURE_MALFORMED,
                resolver_kind=self.kind,
            )
        bundle_root = bundle_root.expanduser().resolve()
        manifest = load_manifest(bundle_root)
        with tempfile.TemporaryDirectory(prefix="autodev-ux-publish-") as temp_dir:
            temp = Path(temp_dir)
            archive = temp / "ux-bundle.tar.gz"
            write_bundle_archive(bundle_root, archive)
            layer_digest = sha256_file(archive)
            completed = self.client.invoke(
                [
                    "push",
                    "--artifact-type",
                    ARTIFACT_TYPE,
                    "--format",
                    "json",
                ],
                [
                    parsed.oras_target,
                    f"{archive.name}:{LAYER_MEDIA_TYPE}",
                ],
                registry=parsed.registry,
                cwd=temp,
            )
        stdout = str(getattr(completed, "stdout", "") or "")
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ux_resolver.UXResolutionError(
                "ORAS push did not return JSON publication metadata",
                classification=ux_resolver.FAILURE_TRANSPORT,
                resolver_kind=self.kind,
            ) from exc
        if not isinstance(response, dict):
            raise ux_resolver.UXResolutionError(
                "ORAS push returned malformed publication metadata",
                classification=ux_resolver.FAILURE_TRANSPORT,
                resolver_kind=self.kind,
            )
        try:
            digest = normalize_digest(response.get("digest"))
        except OCIReferenceError as exc:
            raise ux_resolver.UXResolutionError(
                "ORAS push did not return an immutable manifest digest",
                classification=ux_resolver.FAILURE_IDENTITY,
                resolver_kind=self.kind,
            ) from exc
        returned_type = str(response.get("artifactType", ARTIFACT_TYPE) or ARTIFACT_TYPE)
        if returned_type != ARTIFACT_TYPE:
            raise ux_resolver.UXResolutionError(
                "ORAS push returned an unexpected artifact type",
                classification=ux_resolver.FAILURE_IDENTITY,
                resolver_kind=self.kind,
            )
        status = self.client.require_tool()
        return ux_resolver.PublishedUXArtifact(
            immutable_identity=digest,
            immutable_reference=parsed.with_digest(digest).source_reference,
            source_reference=parsed.source_reference,
            resolver_kind=self.kind,
            provenance={
                "registry": parsed.registry,
                "repository": parsed.repository,
                "tag": parsed.tag,
                "product": manifest.product,
                "bundle_schema": manifest.schema,
                "artifact_type": ARTIFACT_TYPE,
                "layer_digest": layer_digest,
                "oras_version": status.version,
                "credential_source": self.client.credential_source(parsed.registry),
            },
        )
