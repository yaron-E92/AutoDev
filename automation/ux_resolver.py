from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from automation.ux_contract import UXBundleManifest, UXBundleError, load_manifest


FAILURE_UNSUPPORTED = "unsupported_resolver"
FAILURE_AUTH = "authentication"
FAILURE_NOT_FOUND = "artifact_not_found"
FAILURE_MUTABLE = "mutable_reference_disallowed"
FAILURE_TRANSPORT = "transport_failure"
FAILURE_IDENTITY = "identity_mismatch"
FAILURE_MALFORMED = "malformed_bundle"
FAILURE_SCHEMA = "unsupported_bundle_schema"
FAILURE_UNSAFE = "unsafe_bundle"
FAILURE_TOOL = "missing_tool"
FAILURE_TOOL_VERSION = "unsupported_tool_version"


class UXResolutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str,
        resolver_kind: str = "",
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.resolver_kind = resolver_kind


@dataclass(frozen=True)
class ResolutionPolicy:
    unattended: bool = False
    require_immutable_reference: bool = False


@dataclass(frozen=True)
class PublishedUXArtifact:
    immutable_identity: str
    immutable_reference: str
    source_reference: str
    resolver_kind: str
    provenance: dict[str, object] = field(default_factory=dict)

    def safe_evidence(self) -> dict[str, object]:
        return {
            "configured_reference": safe_reference(self.source_reference),
            "resolver_kind": self.resolver_kind,
            "immutable_identity": self.immutable_identity,
            "immutable_reference": safe_reference(self.immutable_reference),
        }


@dataclass(frozen=True)
class ResolvedUXArtifact:
    immutable_identity: str
    immutable_reference: str
    local_root: Path
    manifest: UXBundleManifest
    source_reference: str
    resolver_kind: str
    cache_hit: bool = False
    provenance: dict[str, object] = field(default_factory=dict)

    def safe_evidence(self) -> dict[str, object]:
        return {
            "configured_reference": safe_reference(self.source_reference),
            "resolver_kind": self.resolver_kind,
            "immutable_identity": self.immutable_identity,
            "immutable_reference": safe_reference(self.immutable_reference),
            "bundle_schema": self.manifest.schema,
            "product": self.manifest.product,
            "cache_hit": self.cache_hit,
            "validation": "valid",
        }

    def selected_paths(
        self,
        *,
        screen_ids: tuple[str, ...] = (),
        state_ids: tuple[str, ...] = (),
        journey_ids: tuple[str, ...] = (),
        include_journeys: bool = False,
    ) -> tuple[Path, ...]:
        return tuple(
            self.local_root / relative
            for relative in self.manifest.selected_paths(
                screen_ids=screen_ids,
                state_ids=state_ids,
                journey_ids=journey_ids,
                include_journeys=include_journeys,
            )
        )


class UXArtifactResolver(Protocol):
    kind: str

    def supports(self, reference: str) -> bool:
        ...

    def resolve(self, reference: str, policy: ResolutionPolicy) -> ResolvedUXArtifact:
        ...

    def inspect(self, reference: str) -> dict[str, object]:
        ...

    def identity(self, reference: str) -> str:
        ...


class UXResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: list[UXArtifactResolver] = []

    def register(self, resolver: UXArtifactResolver) -> None:
        kind = str(getattr(resolver, "kind", "") or "").strip()
        if not kind:
            raise ValueError("UX artifact resolver kind must be non-empty")
        if any(existing.kind == kind for existing in self._resolvers):
            raise ValueError(f"UX artifact resolver kind is already registered: {kind}")
        self._resolvers.append(resolver)

    def resolver_for(self, reference: str) -> UXArtifactResolver:
        for resolver in self._resolvers:
            try:
                if resolver.supports(reference):
                    return resolver
            except Exception as exc:
                raise UXResolutionError(
                    f"UX resolver {resolver.kind!r} failed while checking reference support",
                    classification=FAILURE_UNSUPPORTED,
                    resolver_kind=resolver.kind,
                ) from exc
        scheme = reference.split(":", 1)[0] if ":" in reference else "<none>"
        raise UXResolutionError(
            f"no UX artifact resolver is registered for scheme {scheme!r}",
            classification=FAILURE_UNSUPPORTED,
        )

    def resolve(
        self,
        reference: str,
        *,
        policy: ResolutionPolicy | None = None,
    ) -> ResolvedUXArtifact:
        resolver = self.resolver_for(reference)
        active_policy = policy or ResolutionPolicy()
        try:
            artifact = resolver.resolve(reference, active_policy)
        except UXResolutionError:
            raise
        except UXBundleError as exc:
            raise UXResolutionError(
                str(exc),
                classification=_bundle_error_classification(exc),
                resolver_kind=resolver.kind,
            ) from exc
        except Exception as exc:
            raise UXResolutionError(
                f"UX artifact resolution failed through {resolver.kind}: {exc}",
                classification=FAILURE_TRANSPORT,
                resolver_kind=resolver.kind,
            ) from exc
        return _validated_artifact(artifact, resolver.kind, active_policy)

    def inspect(self, reference: str) -> dict[str, object]:
        resolver = self.resolver_for(reference)
        try:
            return dict(resolver.inspect(reference))
        except UXResolutionError:
            raise
        except Exception as exc:
            raise UXResolutionError(
                f"UX artifact inspection failed through {resolver.kind}: {exc}",
                classification=FAILURE_TRANSPORT,
                resolver_kind=resolver.kind,
            ) from exc

    def identity(self, reference: str) -> str:
        resolver = self.resolver_for(reference)
        try:
            identity = str(resolver.identity(reference) or "").strip()
        except UXResolutionError:
            raise
        except Exception as exc:
            raise UXResolutionError(
                f"UX artifact identity lookup failed through {resolver.kind}: {exc}",
                classification=FAILURE_TRANSPORT,
                resolver_kind=resolver.kind,
            ) from exc
        if not identity:
            raise UXResolutionError(
                "UX resolver returned no immutable artifact identity",
                classification=FAILURE_IDENTITY,
                resolver_kind=resolver.kind,
            )
        return identity

    def publish(
        self,
        bundle_root: Path,
        reference: str,
    ) -> PublishedUXArtifact:
        resolver = self.resolver_for(reference)
        publisher = getattr(resolver, "publish", None)
        if not callable(publisher):
            raise UXResolutionError(
                f"UX resolver {resolver.kind!r} does not support publication",
                classification=FAILURE_UNSUPPORTED,
                resolver_kind=resolver.kind,
            )
        try:
            result = publisher(bundle_root, reference)
        except UXResolutionError:
            raise
        except UXBundleError as exc:
            raise UXResolutionError(
                str(exc),
                classification=_bundle_error_classification(exc),
                resolver_kind=resolver.kind,
            ) from exc
        except Exception as exc:
            raise UXResolutionError(
                f"UX artifact publication failed through {resolver.kind}: {exc}",
                classification=FAILURE_TRANSPORT,
                resolver_kind=resolver.kind,
            ) from exc
        if not isinstance(result, PublishedUXArtifact):
            raise UXResolutionError(
                f"UX resolver {resolver.kind!r} returned an invalid publication result",
                classification=FAILURE_IDENTITY,
                resolver_kind=resolver.kind,
            )
        if result.resolver_kind != resolver.kind or not result.immutable_identity.strip():
            raise UXResolutionError(
                f"UX resolver {resolver.kind!r} returned inconsistent publication identity",
                classification=FAILURE_IDENTITY,
                resolver_kind=resolver.kind,
            )
        return result

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(resolver.kind for resolver in self._resolvers)


def _validated_artifact(
    artifact: ResolvedUXArtifact,
    resolver_kind: str,
    policy: ResolutionPolicy,
) -> ResolvedUXArtifact:
    identity = artifact.immutable_identity.strip()
    immutable_reference = artifact.immutable_reference.strip()
    if not identity:
        raise UXResolutionError(
            "UX resolver returned no immutable artifact identity",
            classification=FAILURE_IDENTITY,
            resolver_kind=resolver_kind,
        )
    if policy.require_immutable_reference and not immutable_reference:
        raise UXResolutionError(
            "unattended UX resolution requires a locked immutable reference",
            classification=FAILURE_MUTABLE,
            resolver_kind=resolver_kind,
        )
    try:
        manifest = load_manifest(artifact.local_root)
    except UXBundleError as exc:
        classification = _bundle_error_classification(exc)
        raise UXResolutionError(
            str(exc),
            classification=classification,
            resolver_kind=resolver_kind,
        ) from exc
    artifact = ResolvedUXArtifact(
        immutable_identity=identity,
        immutable_reference=immutable_reference,
        local_root=artifact.local_root.expanduser().resolve(),
        manifest=manifest,
        source_reference=artifact.source_reference,
        resolver_kind=artifact.resolver_kind,
        cache_hit=artifact.cache_hit,
        provenance=dict(artifact.provenance),
    )
    if artifact.resolver_kind != resolver_kind:
        raise UXResolutionError(
            "UX resolver returned an artifact owned by a different resolver kind",
            classification=FAILURE_IDENTITY,
            resolver_kind=resolver_kind,
        )
    return artifact


def _bundle_error_classification(exc: UXBundleError) -> str:
    text = str(exc)
    if "unsupported UX bundle schema" in text:
        return FAILURE_SCHEMA
    if any(
        token in text
        for token in (
            "unsafe",
            "escapes destination",
            "size limit",
            "file-count limit",
            "unsupported non-file member",
            "duplicate member",
        )
    ):
        return FAILURE_UNSAFE
    return FAILURE_MALFORMED


def safe_reference(reference: str) -> str:
    value = str(reference or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "configured"
    if not parts.scheme:
        return "configured"
    hostname = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None:
        hostname += f":{port}"
    return urlunsplit((parts.scheme, hostname, parts.path, "", ""))


def default_registry() -> UXResolverRegistry:
    """Return the provider-neutral core registry.

    Concrete transports register here in their own implementation modules. #252 intentionally
    ships no OCI/GHCR/ORAS behavior; that first adapter is implemented by #253.
    """
    return UXResolverRegistry()
