from __future__ import annotations

from automation import cli_help


def register_help() -> None:
    entries = {
        ("ux",): cli_help.HelpEntry(
            usage="autodev ux <inspect|resolve|lock|publish|doctor|cache-prune> [options]",
            summary="Inspect, resolve, lock, and maintain external UX artifacts without model calls.",
            description=(
                "UX artifacts are transport-neutral, immutable design evidence. The core resolver contract "
                "does not assume GHCR, ORAS, S3, or another backend; concrete transports register separately."
            ),
            subcommands=(
                ("inspect", "Inspect a configured or explicit UX artifact reference."),
                ("resolve", "Resolve and validate a UX artifact into the immutable local cache."),
                ("lock", "Resolve the configured reference and replace it with the immutable reference."),
                ("publish", "Validate and publish one UX bundle through a resolver publication capability."),
                ("doctor", "Check UX repository policy, resolver availability, and cache location."),
                ("cache-prune", "Remove corrupt entries and bound the user-level UX cache."),
            ),
            examples=(
                "autodev ux doctor",
                "autodev ux resolve --json",
                "autodev ux lock",
                "autodev ux publish ./ux-bundle --to oci://ghcr.io/owner/ux/product:v1",
            ),
        ),
        ("ux", "inspect"): cli_help.HelpEntry(
            usage="autodev ux inspect [REFERENCE] [--repo PATH] [--json]",
            summary="Inspect UX artifact metadata without invoking a model.",
        ),
        ("ux", "resolve"): cli_help.HelpEntry(
            usage="autodev ux resolve [REFERENCE] [--repo PATH] [--json]",
            summary="Resolve and validate a UX artifact through the registered transport.",
        ),
        ("ux", "lock"): cli_help.HelpEntry(
            usage="autodev ux lock [REFERENCE] [--repo PATH] [--json]",
            summary="Lock the repository UX reference to the resolver's immutable reference.",
        ),
        ("ux", "publish"): cli_help.HelpEntry(
            usage="autodev ux publish BUNDLE --to REFERENCE [--json]",
            summary="Validate and publish a UX bundle, returning its immutable reference.",
            arguments=(("BUNDLE", "Local UX bundle directory containing ux-manifest.json."),),
            options=(
                ("--to REFERENCE", "Tagged publication target, currently supported through oci:// resolvers."),
                ("--json", "Emit safe publication metadata as JSON."),
            ),
            examples=(
                "autodev ux publish ./ux-bundle --to oci://ghcr.io/yaron-e92/ux/shuffletask:v1",
            ),
        ),
        ("ux", "doctor"): cli_help.HelpEntry(
            usage="autodev ux doctor [--repo PATH] [--json]",
            summary="Check UX policy, resolver availability, and cache location.",
        ),
        ("ux", "cache-prune"): cli_help.HelpEntry(
            usage="autodev ux cache-prune [--max-entries N] [--json]",
            summary="Remove corrupt UX cache entries and keep only the newest N valid entries.",
        ),
    }
    for path, entry in entries.items():
        cli_help.HELP.setdefault(path, entry)
