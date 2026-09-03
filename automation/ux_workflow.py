from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automation import ux_policy, ux_registry, ux_resolver


@dataclass(frozen=True)
class UXResolutionEvidence:
    configured_reference: str
    resolver_kind: str
    immutable_identity: str
    immutable_reference: str
    bundle_schema: str
    product: str
    cache_hit: bool
    validation: str = "valid"

    def to_json(self) -> dict[str, object]:
        return {
            "configured_reference": self.configured_reference,
            "resolver_kind": self.resolver_kind,
            "immutable_identity": self.immutable_identity,
            "immutable_reference": self.immutable_reference,
            "bundle_schema": self.bundle_schema,
            "product": self.product,
            "cache_hit": self.cache_hit,
            "validation": self.validation,
        }


def resolve_configured(
    repo: Path,
    *,
    registry: ux_resolver.UXResolverRegistry | None = None,
    unattended: bool,
) -> ux_resolver.ResolvedUXArtifact | None:
    policy = ux_policy.load_policy(repo)
    if not policy.enabled:
        return None
    active = registry or ux_registry.default_registry()
    artifact = active.resolve(
        policy.artifact,
        policy=ux_resolver.ResolutionPolicy(
            unattended=unattended,
            require_immutable_reference=unattended,
        ),
    )
    if artifact.manifest.product != policy.product:
        raise ux_resolver.UXResolutionError(
            "resolved UX bundle product does not match .autodev/repo.json: "
            f"{artifact.manifest.product!r} != {policy.product!r}",
            classification=ux_resolver.FAILURE_IDENTITY,
            resolver_kind=artifact.resolver_kind,
        )
    return artifact


def evidence(artifact: ux_resolver.ResolvedUXArtifact | None) -> dict[str, object]:
    return artifact.safe_evidence() if artifact is not None else {}


def validate_resume_identity(
    repo: Path,
    expected: object,
    *,
    registry: ux_resolver.UXResolverRegistry | None = None,
) -> None:
    if not expected:
        if ux_policy.load_policy(repo).enabled:
            raise ux_resolver.UXResolutionError(
                "repository now requires a UX artifact but this durable run has no UX identity; "
                "start a new run so UX meaning is explicit",
                classification=ux_resolver.FAILURE_IDENTITY,
            )
        return
    if not isinstance(expected, dict):
        raise ux_resolver.UXResolutionError(
            "durable UX artifact evidence is malformed",
            classification=ux_resolver.FAILURE_IDENTITY,
        )
    policy = ux_policy.load_policy(repo)
    if not policy.enabled:
        raise ux_resolver.UXResolutionError(
            "repository UX policy was removed during a durable run; start a new run explicitly",
            classification=ux_resolver.FAILURE_IDENTITY,
        )
    artifact = resolve_configured(repo, registry=registry, unattended=True)
    assert artifact is not None
    expected_identity = str(expected.get("immutable_identity", "") or "")
    if artifact.immutable_identity != expected_identity:
        raise ux_resolver.UXResolutionError(
            "resolved UX artifact identity changed since this run was prepared; "
            "resume refuses to silently change design meaning",
            classification=ux_resolver.FAILURE_IDENTITY,
            resolver_kind=artifact.resolver_kind,
        )
