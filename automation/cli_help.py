from __future__ import annotations

from dataclasses import dataclass

from automation.privacy_grant_contract import DURATIONS, SCOPES
from automation.queue_contract import DEFAULT_LIMIT
from automation.scheduler_types import (
    BACKEND_AUTO,
    BACKEND_CRON,
    BACKEND_SYSTEMD,
    BACKEND_WINDOWS,
    DEFAULT_CADENCE_MINUTES,
    MAX_CADENCE_MINUTES,
    MIN_CADENCE_MINUTES,
)


@dataclass(frozen=True)
class HelpEntry:
    usage: str
    summary: str
    description: str = ""
    arguments: tuple[tuple[str, str], ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    examples: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    privacy_note: str = ""
    subcommands: tuple[tuple[str, str], ...] = ()


CLOUD_MODEL_NOTE = (
    "This command can invoke configured model providers. AutoDev applies the repository "
    "privacy policy before model work; routes that do not satisfy it require an explicit "
    "consent grant. Headless scheduler runs can consume existing grants but cannot create them."
)


def _entry(
    usage: str,
    summary: str,
    *,
    description: str = "",
    arguments: tuple[tuple[str, str], ...] = (),
    options: tuple[tuple[str, str], ...] = (),
    examples: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
    privacy_note: str = "",
    subcommands: tuple[tuple[str, str], ...] = (),
) -> HelpEntry:
    return HelpEntry(
        usage=usage,
        summary=summary,
        description=description,
        arguments=arguments,
        options=options,
        examples=examples,
        aliases=aliases,
        privacy_note=privacy_note,
        subcommands=subcommands,
    )


COMMON_LOCATION_OPTIONS = (
    ("--repo PATH", "Repository root to operate on. Default: current directory (.)."),
    ("--github-repo OWNER/REPO", "GitHub repository identity. Default: detect from the Git remote."),
)

REGISTRATION_OPTIONS = COMMON_LOCATION_OPTIONS + (
    ("--registration PATH", "Use an explicit scheduler registration file instead of auto-discovery."),
    ("--json", "Emit stable machine-readable JSON where supported."),
)


HELP: dict[tuple[str, ...], HelpEntry] = {
    ("install",): _entry(
        "autodev install --user [options]",
        "Install or remove the user-local AutoDev launcher.",
        description=(
            "User installation is separate from target-repository setup. This command installs "
            "the `autodev` launcher for the current AutoDev checkout; run `autodev repo install` "
            "inside each repository that should use AutoDev."
        ),
        options=(
            ("--user", "Required. Install for the current user rather than modifying a system installation."),
            ("--uninstall", "Remove the recorded user-local launcher and AutoDev-managed PATH profile block."),
            ("--bin-dir PATH", "Launcher directory. Default: platform-specific user bin directory."),
            ("--python EXE", "Python interpreter used by the launcher. Default: current Python executable."),
            ("--add-to-path", "Add the launcher directory to an AutoDev-managed shell profile block."),
            ("--profile PATH", "Profile file to edit; repeatable. Requires --add-to-path."),
            ("--json", "Emit installation state as JSON."),
        ),
        examples=(
            "autodev install --user --add-to-path",
            "autodev install --user --json",
            "autodev install --user --uninstall",
        ),
    ),
    ("repo",): _entry(
        "autodev repo <command> [options]",
        "Configure and maintain AutoDev assets in a target repository.",
        description=(
            "Repository setup owns AutoDev policy/configuration files, queue labels, and optional "
            "OpenCode assets. It does not install the user-level `autodev` launcher."
        ),
        subcommands=(
            ("install", "Create/refresh repository policy, labels, and optional OpenCode assets."),
            ("ensure-labels", "Create any missing canonical AutoDev queue labels."),
            ("doctor", "Check repository setup; alias: top-level `autodev doctor`."),
        ),
        examples=("autodev repo install", "autodev repo doctor"),
    ),
    ("repo", "install"): _entry(
        "autodev repo install [options]",
        "Configure AutoDev for a target repository.",
        options=COMMON_LOCATION_OPTIONS
        + (
            ("--no-opencode", "Do not install the optional OpenCode command/agent integration."),
            ("--json", "Emit installation results as JSON."),
        ),
        examples=("autodev repo install", "autodev repo install --no-opencode"),
    ),
    ("repo", "ensure-labels"): _entry(
        "autodev repo ensure-labels [options]",
        "Ensure the canonical AutoDev queue labels exist on GitHub.",
        options=COMMON_LOCATION_OPTIONS + (("--json", "Emit the created-label list as JSON."),),
        examples=("autodev repo ensure-labels",),
    ),
    ("repo", "doctor"): _entry(
        "autodev repo doctor [options]",
        "Check AutoDev repository setup and optionally repair fixable drift.",
        options=COMMON_LOCATION_OPTIONS
        + (
            ("--fix", "Repair fixable repository assets before re-running checks."),
            ("--json", "Emit structured health details as JSON."),
        ),
        aliases=("autodev doctor",),
        examples=("autodev repo doctor", "autodev repo doctor --fix"),
    ),
    ("doctor",): _entry(
        "autodev doctor [options]",
        "Check the current AutoDev installation/repository and optionally repair fixable drift.",
        description="Canonical convenience spelling for the repository doctor.",
        options=COMMON_LOCATION_OPTIONS
        + (
            ("--fix", "Repair fixable repository assets before re-running checks."),
            ("--json", "Emit structured health details as JSON."),
        ),
        aliases=("autodev repo doctor",),
        examples=("autodev doctor", "autodev doctor --fix", "autodev doctor --json"),
    ),
    ("issue-to-pr",): _entry(
        "autodev issue-to-pr ISSUE [--repo PATH] [--runtime NAME]",
        "Work one GitHub issue through AutoDev's implementation, verification, and PR flow.",
        description=(
            "This is the normal interactive entrypoint for a specific issue. AutoDev prepares or "
            "resumes durable run state, executes configured roles, verifies the result, and ships a PR."
        ),
        arguments=(("ISSUE", "Required positive GitHub issue number."),),
        options=(
            ("--repo PATH", "Repository root. Default: current directory (.)."),
            ("--runtime NAME", "Role runtime override. Default: repository/user configuration, then opencode."),
        ),
        aliases=("autodev coordinate --arguments ISSUE",),
        examples=(
            "autodev issue-to-pr 123",
            "autodev issue-to-pr 123 --runtime opencode",
            "autodev issue-to-pr 123 --repo ../my-project",
        ),
        privacy_note=CLOUD_MODEL_NOTE,
    ),
    ("resume",): _entry(
        "autodev resume [--repo PATH] [--runtime NAME]",
        "Resume the current durable AutoDev run from its checkpoint.",
        description=(
            "Resume reuses accepted role artifacts and completed stages when their fingerprints still "
            "match. Use this after an interruption, waiting state, repaired prerequisite, or explicit "
            "privacy grant."
        ),
        options=(
            ("--repo PATH", "Repository root. Default: current directory (.)."),
            ("--runtime NAME", "Role runtime override. Default: repository/user configuration, then opencode."),
            ("--arguments TEXT", "Advanced coordinator arguments preserved for integration compatibility."),
        ),
        aliases=("autodev coordinate --resume",),
        examples=("autodev resume", "autodev resume --runtime opencode"),
        privacy_note=CLOUD_MODEL_NOTE,
    ),
    ("status",): _entry(
        "autodev status [options]",
        "Show the current durable AutoDev run/checkpoint status.",
        options=(
            ("--repo PATH", "Repository root. Default: current directory (.)."),
            ("--invalidate-role ROLE", "Preview status with a completed role invalidated; repeatable."),
        ),
        examples=("autodev status",),
    ),
    ("models",): _entry(
        "autodev models [--repo PATH]",
        "Show effective OpenCode model mappings for AutoDev roles.",
        description=(
            "This is a read-only configuration/introspection command. Repository OpenCode configuration "
            "remains authoritative for OpenCode model mappings."
        ),
        options=(("--repo PATH", "Repository root. Default: current directory (.)."),),
        examples=("autodev models",),
    ),
    ("coordinate",): _entry(
        "autodev coordinate [options]",
        "Advanced direct spelling for the shared role coordinator.",
        description=(
            "Prefer `autodev issue-to-pr ISSUE` for normal issue work and `autodev resume` for an "
            "interrupted run. `coordinate` remains available for integrations that pass an opaque "
            "--arguments string."
        ),
        options=(
            ("--repo PATH", "Repository root. Default: current directory (.)."),
            ("--arguments TEXT", "Coordinator argument payload; an issue number selects that GitHub issue."),
            ("--resume", "Resume existing durable run state instead of preparing a new issue run."),
            ("--runtime NAME", "Role runtime override. Default: configured runtime, then opencode."),
        ),
        aliases=("autodev issue-to-pr ISSUE", "autodev resume"),
        examples=("autodev coordinate --arguments 123",),
        privacy_note=CLOUD_MODEL_NOTE,
    ),
    ("notifications",): _entry(
        "autodev notifications <enable|disable|status> [options]",
        "Configure issue-to-PR outcome notifications.",
        description=(
            "Notification policy is user-local per GitHub repository and applies to both manual "
            "issue-to-PR/resume outcomes and installed scheduler health events. Notifications "
            "remain opt-in and native-only in this release."
        ),
        subcommands=(
            ("enable", "Enable native notifications for manual and scheduled outcomes."),
            ("disable", "Disable AutoDev notifications for this repository."),
            ("status", "Show notification policy and last observed notification events."),
        ),
        examples=("autodev notifications enable", "autodev notifications status --json"),
    ),
    ("notifications", "enable"): _entry(
        "autodev notifications enable [options]",
        "Enable native AutoDev notifications.",
        options=COMMON_LOCATION_OPTIONS
        + (
            ("--reminder-hours N", "Repeat unresolved blocked/attention reminders every N hours. Default: 0."),
            ("--json", "Emit policy and event state as JSON."),
        ),
        examples=("autodev notifications enable --reminder-hours 24",),
    ),
    ("notifications", "disable"): _entry(
        "autodev notifications disable [options]",
        "Disable AutoDev notifications.",
        options=COMMON_LOCATION_OPTIONS + (("--json", "Emit policy and event state as JSON."),),
    ),
    ("notifications", "status"): _entry(
        "autodev notifications status [options]",
        "Show shared manual/scheduled notification policy and event state.",
        options=COMMON_LOCATION_OPTIONS + (("--json", "Emit policy and event state as JSON."),),
    ),
    ("scheduler",): _entry(
        "autodev scheduler <command> [options]",
        "Install, inspect, and run autonomous AutoDev scheduling.",
        description=(
            "Schedulers wake a dedicated worker checkout; they do not run AutoDev inside an arbitrary "
            "interactive working tree. Autonomous model work remains subject to repository privacy policy."
        ),
        subcommands=(
            ("install", "Register a persistent scheduler backend and dedicated worker."),
            ("status", "Show scheduler registration, backend state, health, and notifications."),
            ("health", "Show scheduler/queue/privacy health only."),
            ("notifications", "Configure native attention-state notifications."),
            ("worker-id", "Show or set the stable distributed worker identity."),
            ("run-once", "Run one scheduler tick immediately."),
            ("uninstall", "Remove AutoDev's scheduler registration/backend artifacts."),
        ),
        examples=("autodev scheduler install", "autodev scheduler status", "autodev scheduler run-once"),
    ),
    ("scheduler", "install"): _entry(
        "autodev scheduler install [options]",
        "Install an autonomous scheduler for the target repository.",
        options=COMMON_LOCATION_OPTIONS
        + (
            (
                "--backend NAME",
                "Backend: "
                + "|".join((BACKEND_AUTO, BACKEND_SYSTEMD, BACKEND_CRON, BACKEND_WINDOWS))
                + f". Default: {BACKEND_AUTO}.",
            ),
            (
                "--cadence-minutes N",
                f"Wake cadence in minutes ({MIN_CADENCE_MINUTES}-{MAX_CADENCE_MINUTES}). Default: {DEFAULT_CADENCE_MINUTES}.",
            ),
            ("--launcher PATH", "Explicit AutoDev launcher used by the scheduler worker."),
            ("--json", "Emit scheduler registration as JSON."),
        ),
        examples=(
            "autodev scheduler install",
            f"autodev scheduler install --backend {BACKEND_CRON} --cadence-minutes {DEFAULT_CADENCE_MINUTES}",
        ),
    ),
    ("scheduler", "status"): _entry(
        "autodev scheduler status [options]",
        "Show scheduler registration, backend state, health, and notification policy.",
        options=REGISTRATION_OPTIONS,
        examples=("autodev scheduler status", "autodev scheduler status --json"),
    ),
    ("scheduler", "health"): _entry(
        "autodev scheduler health [options]",
        "Show scheduler, queue, worker, and privacy readiness.",
        options=REGISTRATION_OPTIONS,
        examples=("autodev scheduler health",),
    ),
    ("scheduler", "notifications"): _entry(
        "autodev scheduler notifications <enable|disable|status> [options]",
        "Supported alias for the shared repository notification policy.",
        subcommands=(
            ("enable", "Enable native notifications, optionally with periodic attention reminders."),
            ("disable", "Disable AutoDev scheduler notifications."),
            ("status", "Show the current notification policy."),
        ),
        examples=("autodev scheduler notifications enable", "autodev scheduler notifications status"),
    ),
    ("scheduler", "notifications", "enable"): _entry(
        "autodev scheduler notifications enable [options]",
        "Enable native scheduler notifications.",
        options=REGISTRATION_OPTIONS
        + (("--reminder-hours N", "Repeat attention reminders every N hours. Default: 0 (transition-only)."),),
        examples=("autodev scheduler notifications enable --reminder-hours 24",),
    ),
    ("scheduler", "notifications", "disable"): _entry(
        "autodev scheduler notifications disable [options]",
        "Disable scheduler notifications.",
        options=REGISTRATION_OPTIONS,
    ),
    ("scheduler", "notifications", "status"): _entry(
        "autodev scheduler notifications status [options]",
        "Show the current scheduler notification policy.",
        options=REGISTRATION_OPTIONS,
    ),
    ("scheduler", "worker-id"): _entry(
        "autodev scheduler worker-id [--set NAME] [--json]",
        "Show or set the stable identity used for distributed claim ownership.",
        options=(
            ("--set NAME", "Persist an explicit worker identity instead of the generated user-local identity."),
            ("--json", "Emit worker identity as JSON."),
        ),
        examples=("autodev scheduler worker-id", "autodev scheduler worker-id --set mega-beast"),
    ),
    ("scheduler", "run-once"): _entry(
        "autodev scheduler run-once [options]",
        "Run one autonomous scheduler tick immediately.",
        options=REGISTRATION_OPTIONS,
        examples=("autodev scheduler run-once",),
        privacy_note=(
            "A scheduler tick may dispatch model work. Headless runs never manufacture consent; they can "
            "only use routes already allowed by policy or consume an existing valid privacy grant."
        ),
    ),
    ("scheduler", "uninstall"): _entry(
        "autodev scheduler uninstall [options]",
        "Remove AutoDev's scheduler registration and backend artifacts.",
        options=REGISTRATION_OPTIONS,
        examples=("autodev scheduler uninstall",),
    ),
    ("privacy",): _entry(
        "autodev privacy <command> [options]",
        "Inspect, grant, and revoke explicit privacy-consent exceptions.",
        description=(
            "Privacy commands do not weaken repository policy. Consent can authorize only routes that "
            "the configured policy explicitly permits through consent."
        ),
        subcommands=(
            ("status", "List privacy grants for the current repository."),
            ("consent", "Interactively create a run-scoped or time-bounded grant."),
            ("revoke", "Immediately revoke one or all persistent grants."),
        ),
        examples=("autodev privacy status", "autodev privacy consent", "autodev privacy revoke --all"),
    ),
    ("privacy", "status"): _entry(
        "autodev privacy status [--json]",
        "Show privacy grants for the current repository.",
        options=(("--json", "Emit repository identity and grant metadata as JSON."),),
    ),
    ("privacy", "consent"): _entry(
        "autodev privacy consent [options]",
        "Interactively create an explicit privacy consent grant.",
        options=(
            ("--duration VALUE", "Grant duration: " + "|".join(DURATIONS) + ". Default: prompt interactively."),
            ("--scope VALUE", "Grant scope: " + "|".join(SCOPES) + ". Default: configured."),
            ("--role ROLE", "Limit exact/configured consent selection to one configured AutoDev role."),
        ),
        privacy_note=(
            "Consent creation requires an interactive terminal. Headless/scheduled runs may consume valid "
            "existing grants but cannot create new grants."
        ),
        examples=("autodev privacy consent", "autodev privacy consent --duration 7d --scope configured"),
    ),
    ("privacy", "revoke"): _entry(
        "autodev privacy revoke [GRANT_ID | --all]",
        "Immediately revoke one or all persistent privacy grants.",
        arguments=(("GRANT_ID", "Optional persistent grant id to revoke."),),
        options=(("--all", "Revoke all active persistent grants for the repository."),),
        examples=("autodev privacy revoke grant-abc123", "autodev privacy revoke --all"),
    ),
    ("queue",): _entry(
        "autodev queue <command> [options]",
        "Inspect, reconcile, explain, and select AutoDev-managed GitHub issues without model calls.",
        subcommands=(
            ("status", "Summarize managed issue states without mutation."),
            ("reconcile", "Reconcile labels/dependencies to authoritative queue state."),
            ("explain", "Explain why one issue is ready, blocked, excluded, or attention-required."),
            ("next", "Select the next autonomous issue or surface the existing run."),
        ),
        examples=("autodev queue status", "autodev queue next --dry-run"),
    ),
    ("queue", "status"): _entry(
        "autodev queue status [options]",
        "Summarize the AutoDev-managed issue queue without mutation.",
        options=(
            ("--github-repo OWNER/REPO", "GitHub repository. Default: detect from current Git remote."),
            ("--limit N", f"Maximum issues to inspect. Default: {DEFAULT_LIMIT}."),
            ("--json", "Emit the summary as JSON."),
        ),
    ),
    ("queue", "reconcile"): _entry(
        "autodev queue reconcile [options]",
        "Reconcile AutoDev queue labels/dependencies to authoritative issue state.",
        options=(
            ("--github-repo OWNER/REPO", "GitHub repository. Default: detect from current Git remote."),
            ("--limit N", f"Maximum issues to inspect. Default: {DEFAULT_LIMIT}."),
            ("--json", "Emit reconciliation details as JSON."),
        ),
    ),
    ("queue", "explain"): _entry(
        "autodev queue explain ISSUE [options]",
        "Explain the current queue classification for one issue.",
        arguments=(("ISSUE", "Required GitHub issue number."),),
        options=(
            ("--github-repo OWNER/REPO", "GitHub repository. Default: detect from current Git remote."),
            ("--json", "Emit classification details as JSON."),
        ),
        examples=("autodev queue explain 123",),
    ),
    ("queue", "next"): _entry(
        "autodev queue next [options]",
        "Select the next autonomous issue or surface an existing durable run.",
        options=(
            ("--github-repo OWNER/REPO", "GitHub repository. Default: detect from current Git remote."),
            ("--limit N", f"Maximum issues to inspect. Default: {DEFAULT_LIMIT}."),
            ("--dry-run", "Select deterministically without mutating queue/run state."),
            ("--json", "Emit selection details as JSON."),
        ),
        examples=("autodev queue next --dry-run",),
    ),
}


TOP_LEVEL_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Common workflows",
        (
            ("issue-to-pr ISSUE", "Work one GitHub issue through implementation, verification, and PR."),
            ("resume", "Resume the current durable AutoDev run."),
            ("status", "Inspect the current run/checkpoint."),
            ("doctor", "Check installation/repository health."),
        ),
    ),
    (
        "Setup",
        (
            ("install --user", "Install or remove the user-local `autodev` launcher."),
            ("repo install", "Configure AutoDev assets and policy in a target repository."),
            ("repo ensure-labels", "Ensure canonical AutoDev queue labels exist."),
        ),
    ),
    (
        "Automation and operations",
        (
            ("scheduler", "Install, inspect, and run autonomous scheduling."),
            ("notifications", "Configure native ready/blocked/failed outcome notifications."),
            ("queue", "Inspect/reconcile/select managed issues without model calls."),
            ("privacy", "Inspect, grant, or revoke explicit privacy consent."),
            ("models", "Show effective OpenCode role/model mappings."),
        ),
    ),
    (
        "Advanced",
        (
            ("coordinate", "Direct shared-coordinator spelling; prefer issue-to-pr/resume."),
            ("repo doctor", "Supported alias of top-level `autodev doctor`."),
        ),
    ),
)


KNOWN_TOP_LEVEL = {
    "install",
    "repo",
    "doctor",
    "issue-to-pr",
    "scheduler",
    "notifications",
    "status",
    "resume",
    "models",
    "coordinate",
    "privacy",
    "queue",
    # Maintained integration/internal surfaces are intentionally not advertised.
    "role",
    "role-check",
    "prepare",
    "accept",
    "stage",
}


def _section(title: str, rows: tuple[tuple[str, str], ...]) -> list[str]:
    if not rows:
        return []
    width = max(len(name) for name, _ in rows)
    lines = [title + ":"]
    for name, description in rows:
        lines.append(f"  {name:<{width}}  {description}")
    return lines


def render_top_level() -> str:
    lines = [
        "AutoDev autonomously turns GitHub issues into reviewed pull requests.",
        "",
        "Usage:",
        "  autodev <command> [options]",
        "  autodev <command> --help",
        "",
    ]
    for index, (title, rows) in enumerate(TOP_LEVEL_GROUPS):
        lines.extend(_section(title, rows))
        lines.append("")
    lines.extend(
        [
            "Privacy:",
            "  Commands that can invoke model providers enforce the repository privacy policy before model work.",
            "  Use `autodev privacy --help` to inspect explicit consent/grant controls.",
            "",
            "Run 'autodev <command> --help' for command-specific help.",
            "Run 'autodev help <command>' for the same help without executing the command.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_command(path: tuple[str, ...]) -> str:
    entry = HELP[path]
    lines = [entry.summary, "", "Usage:", f"  {entry.usage}"]
    if entry.description:
        lines.extend(("", entry.description))
    if entry.subcommands:
        lines.extend(("", *_section("Commands", entry.subcommands)))
    if entry.arguments:
        lines.extend(("", *_section("Arguments", entry.arguments)))
    if entry.options:
        lines.extend(("", *_section("Options", entry.options)))
    if entry.aliases:
        lines.extend(("", "Aliases:"))
        lines.extend(f"  {value}" for value in entry.aliases)
    if entry.privacy_note:
        lines.extend(("", "Privacy:", f"  {entry.privacy_note}"))
    if entry.examples:
        lines.extend(("", "Examples:"))
        lines.extend(f"  {example}" for example in entry.examples)
    if entry.subcommands:
        lines.extend(("", f"Run 'autodev {' '.join(path)} <command> --help' for command-specific help."))
    return "\n".join(lines) + "\n"


def _command_tokens(values: list[str]) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in values:
        if value.startswith("-"):
            break
        tokens.append(value)
    return tuple(tokens)


def resolve_path(values: list[str]) -> tuple[str, ...] | None:
    """Return a public help path when *values* request help, else None.

    Raises KeyError when help was explicitly requested for an unknown public path.
    """
    if not values:
        return ()
    if values[0] == "help":
        before_help = values[1:]
    else:
        indexes = [index for index, value in enumerate(values) if value in {"-h", "--help"}]
        if not indexes:
            return None
        before_help = values[: indexes[0]]
    if not before_help:
        return ()

    tokens = _command_tokens(before_help)
    for size in range(min(3, len(tokens)), 0, -1):
        candidate = tokens[:size]
        if candidate in HELP:
            return candidate
    raise KeyError(" ".join(tokens) or "<top-level>")


def render(values: list[str]) -> str | None:
    path = resolve_path(values)
    if path is None:
        return None
    return render_top_level() if not path else render_command(path)
