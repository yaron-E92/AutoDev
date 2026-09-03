from __future__ import annotations

from automation import ux_resolver
from automation.ux_oci import OCIUXArtifactResolver


def default_registry() -> ux_resolver.UXResolverRegistry:
    registry = ux_resolver.UXResolverRegistry()
    registry.register(OCIUXArtifactResolver())
    return registry
