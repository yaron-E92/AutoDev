from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from automation import ux_cache, ux_policy, ux_registry, ux_resolver, ux_workflow
from automation.ux_contract import manifest_summary


def _reference(repo: Path, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    policy = ux_policy.load_policy(repo)
    if not policy.enabled:
        raise ux_policy.UXPolicyError("repository UX support is not enabled")
    return policy.artifact


def _emit(value: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
        return
    print(value)


def run_cli(
    argv: list[str] | None = None,
    *,
    registry: ux_resolver.UXResolverRegistry | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev ux")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("inspect", "resolve"):
        command = sub.add_parser(name)
        command.add_argument("reference", nargs="?", default="")
        command.add_argument("--repo", default=".")
        command.add_argument("--json", action="store_true")

    lock = sub.add_parser("lock")
    lock.add_argument("reference", nargs="?", default="")
    lock.add_argument("--repo", default=".")
    lock.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo", default=".")
    doctor.add_argument("--json", action="store_true")

    prune = sub.add_parser("cache-prune")
    prune.add_argument("--max-entries", type=int, default=20)
    prune.add_argument("--json", action="store_true")

    publish = sub.add_parser("publish")
    publish.add_argument("bundle")
    publish.add_argument("--to", required=True)
    publish.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    active = registry or ux_registry.default_registry()

    try:
        if args.command == "cache-prune":
            corrupt = ux_cache.remove_corrupt_entries()
            removed = ux_cache.prune(max_entries=args.max_entries)
            _emit(
                {
                    "cache_root": str(ux_cache.cache_root()),
                    "corrupt_removed": corrupt,
                    "entries_removed": len(removed),
                },
                as_json=args.json,
            )
            return 0

        if args.command == "publish":
            published = active.publish(
                Path(args.bundle).expanduser().resolve(),
                args.to,
            )
            _emit(
                {
                    **published.safe_evidence(),
                    "published": True,
                },
                as_json=args.json,
            )
            return 0

        repo = Path(args.repo).expanduser().resolve()
        if args.command == "doctor":
            policy = ux_policy.load_policy(repo)
            result: dict[str, object] = {
                "enabled": policy.enabled,
                "product": policy.product,
                "configured_reference": ux_resolver.safe_reference(policy.artifact),
                "registered_resolvers": list(active.kinds),
                "cache_root": str(ux_cache.cache_root()),
            }
            if not policy.enabled:
                result["state"] = "disabled"
                _emit(result, as_json=args.json)
                return 0
            try:
                resolver = active.resolver_for(policy.artifact)
                result["resolver_kind"] = resolver.kind
                diagnostics = getattr(resolver, "doctor", None)
                resolver_status = (
                    diagnostics(policy.artifact)
                    if callable(diagnostics)
                    else {"supported": True}
                )
                result["resolver"] = resolver_status
                if resolver_status.get("available") is False:
                    result["state"] = "error"
                    result["failure_classification"] = ux_resolver.FAILURE_TOOL
                    result["reason"] = str(resolver_status.get("reason", "resolver tool is unavailable"))
                    code = 2
                elif resolver_status.get("supported") is False:
                    result["state"] = "error"
                    result["failure_classification"] = ux_resolver.FAILURE_TOOL_VERSION
                    result["reason"] = str(resolver_status.get("reason", "resolver tool is unsupported"))
                    code = 2
                elif resolver_status.get("reference_error"):
                    result["state"] = "error"
                    result["failure_classification"] = ux_resolver.FAILURE_MALFORMED
                    result["reason"] = str(resolver_status["reference_error"])
                    code = 2
                else:
                    result["state"] = "ready"
                    code = 0
            except ux_resolver.UXResolutionError as exc:
                result["state"] = "error"
                result["failure_classification"] = exc.classification
                result["reason"] = str(exc)
                code = 2
            _emit(result, as_json=args.json)
            return code

        reference = _reference(repo, args.reference)
        if args.command == "inspect":
            result = active.inspect(reference)
            result.setdefault("configured_reference", ux_resolver.safe_reference(reference))
            _emit(result, as_json=args.json)
            return 0

        artifact = active.resolve(
            reference,
            policy=ux_resolver.ResolutionPolicy(
                unattended=False,
                require_immutable_reference=False,
            ),
        )
        result = {
            **artifact.safe_evidence(),
            "manifest": manifest_summary(artifact.manifest),
            "local_root": str(artifact.local_root),
        }
        if args.command == "lock":
            policy = ux_policy.load_policy(repo)
            if not policy.enabled:
                raise ux_policy.UXPolicyError("repository UX support is not enabled")
            if policy.artifact != reference:
                raise ux_policy.UXPolicyError(
                    "lock requires the configured repository UX reference"
                )
            ux_policy.update_locked_reference(
                repo,
                expected_reference=reference,
                immutable_reference=artifact.immutable_reference,
            )
            result["locked"] = True
        _emit(result, as_json=args.json)
        return 0
    except (ux_policy.UXPolicyError, ux_resolver.UXResolutionError, ux_cache.UXCacheError) as exc:
        classification = getattr(exc, "classification", "configuration")
        print(f"autodev ux: {classification}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())
