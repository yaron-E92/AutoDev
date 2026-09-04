from __future__ import annotations

from automation import claim_cli, cli_help, config_cli, manage_cli, notification_cli, privacy_grant_cli, product_runtime, scheduler_health_cli, semver_intent, ux_cli, ux_help

import os
import sys

from automation import opencode_entrypoint, repository_identity, user_install


manage_cli.register_help()
ux_help.register_help()

INTERACTIVE_CONSENT_ARG = "--interactive-consent"
INTERACTIVE_CONSENT_ENV = "AUTODEV_INTERACTIVE_CONSENT"
INTERACTIVE_CONSENT_VALUE = "controlling-terminal"
INTERNAL_FORWARD_COMMANDS = {"role", "role-check", "prepare", "accept", "stage"}
GLOBAL_OWNER_ARG = "--owner"
GLOBAL_REPO_ARG = "--repo"


def _consume_interactive_consent_argument(values: list[str]) -> tuple[list[str], bool]:
    forwarded: list[str] = []
    interactive = False
    for value in values:
        if value == INTERACTIVE_CONSENT_ARG:
            interactive = True
            continue
        forwarded.append(value)
    return forwarded, interactive


def _consume_repository_target_arguments(
    values: list[str],
) -> tuple[list[str] | None, tuple[str, str] | None, str]:
    owner = ""
    repo = ""
    index = 0
    while index < len(values) and values[index] in {GLOBAL_OWNER_ARG, GLOBAL_REPO_ARG}:
        option = values[index]
        if index + 1 >= len(values) or not values[index + 1].strip():
            return None, None, f"{option} requires a value"
        value = values[index + 1].strip()
        if option == GLOBAL_OWNER_ARG:
            owner = value
        else:
            repo = value
        index += 2

    if bool(owner) != bool(repo):
        missing = GLOBAL_REPO_ARG if owner else GLOBAL_OWNER_ARG
        return (
            None,
            None,
            f"explicit repository targeting requires both {GLOBAL_OWNER_ARG} and {GLOBAL_REPO_ARG}; missing {missing}",
        )
    if not owner:
        return values, None, ""
    try:
        normalized_owner, normalized_repo = repository_identity.split_github_repository(
            f"{owner}/{repo}", label="CLI repository target"
        )
    except repository_identity.RepositoryIdentityError as exc:
        return None, None, str(exc)
    return values[index:], (normalized_owner, normalized_repo), ""


def _enable_interactive_consent_for_direct_cli(*, explicit: bool = False) -> None:
    if explicit:
        os.environ[INTERACTIVE_CONSENT_ENV] = INTERACTIVE_CONSENT_VALUE
        return
    if os.environ.get("AUTODEV_HEADLESS", "").strip():
        return
    try:
        interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
    except AttributeError:
        interactive = False
    if interactive:
        os.environ.setdefault(INTERACTIVE_CONSENT_ENV, INTERACTIVE_CONSENT_VALUE)


def _help() -> str:
    text = cli_help.render_top_level()
    extra = (
        "Configuration:\n"
        "  autodev --version          Show the installed AutoDev product version.\n"
        "  autodev models             Show effective OpenCode role/model mappings.\n"
        "  --owner OWNER --repo REPO  Override the GitHub repository target for this command.\n"
        "  --runtime NAME             Override role runtime for issue-to-pr/resume.\n"
        "  Repository precedence      CLI target > GITHUB_OWNER/GITHUB_REPO > .autodev/repo.json > remote/fallback.\n"
        "  Runtime precedence         explicit > AUTODEV_ROLE_RUNTIME > repository > user > opencode.\n"
        "  Model routing              AutoDev profiles fill inherited roles; explicit opencode.json / opencode.jsonc agent models win.\n"
        "\n"
        "Contributors:\n"
        "  End-user `autodev` commands are separate from source-development checks.\n"
        "  Validate a checkout with `python -m compileall -q automation area_reader tests` and\n"
        "  `python -m unittest discover -s tests -v`; contributor-only helpers are not public commands.\n"
        "\n"
    )
    return text.replace("Privacy:\n", extra + "Privacy:\n", 1)


def _friendly_error(message: str, *, command: str = "") -> int:
    print(f"autodev: {message}", file=sys.stderr)
    if command and (command,) in cli_help.HELP:
        print(f"Run 'autodev {command} --help' for usage.", file=sys.stderr)
    else:
        print("Run 'autodev --help' to see supported commands.", file=sys.stderr)
    return 2


def _issue_to_pr(values: list[str]) -> tuple[list[str] | None, str]:
    if not values:
        return None, "issue-to-pr requires ISSUE"
    raw_issue = values[0].strip()
    try:
        issue_number = int(raw_issue)
    except ValueError:
        return None, f"ISSUE must be a positive integer, got {raw_issue!r}"
    if issue_number <= 0:
        return None, f"ISSUE must be a positive integer, got {raw_issue!r}"

    forwarded = ["coordinate", "--arguments", str(issue_number)]
    index = 1
    while index < len(values):
        option = values[index]
        if option not in {"--repo", "--runtime", "--semver"}:
            return None, f"unsupported issue-to-pr option {option!r}"
        if index + 1 >= len(values) or not values[index + 1].strip():
            return None, f"{option} requires a value"
        value = values[index + 1]
        if option == "--semver":
            try:
                value = semver_intent.normalize_intent(value, source="explicit")
            except semver_intent.SemVerIntentError as exc:
                return None, str(exc)
        forwarded.extend((option, value))
        index += 2
    return forwarded, ""


def _render_requested_help(values: list[str]) -> tuple[bool, int]:
    try:
        path = cli_help.resolve_path(values)
    except KeyError as exc:
        direct = values[0] if values else ""
        if direct in INTERNAL_FORWARD_COMMANDS and any(
            value in {"-h", "--help"} for value in values
        ):
            return False, 0
        unknown = str(exc).strip("'")
        return True, _friendly_error(f"no public help topic for {unknown!r}")
    if path is None:
        return False, 0
    text = _help() if not path else cli_help.render_command(path)
    print(text, end="")
    return True, 0


def _dispatch(values: list[str], *, explicit_interactive: bool) -> int:
    if values in (["--version"], ["-V"]):
        print(product_runtime.version_text())
        return 0
    if values and values[0] == "verify-local":
        from automation import local_verification_cli

        return local_verification_cli.run_cli(values[1:])

    handled, help_code = _render_requested_help(values)
    if handled:
        return help_code
    if not values:
        print(_help(), end="")
        return 0

    command = values[0]
    rest = values[1:]
    if command not in cli_help.KNOWN_TOP_LEVEL:
        return _friendly_error(f"unknown command {command!r}")

    if command == "install":
        return user_install.run_cli(rest, autodev_root=product_runtime.product_root())
    if command == "config":
        return config_cli.run_cli(rest)
    if command in {"repo", "doctor"}:
        from automation import local_verification_doctor, repo_setup

        local_verification_doctor.install()
        return repo_setup.run_cli((["doctor"] if command == "doctor" else []) + rest)
    if command == "scheduler":
        if rest and rest[0] == "worker-id":
            return claim_cli.run_worker_cli(rest[1:])
        return scheduler_health_cli.run_cli(rest)
    if command == "notifications":
        return notification_cli.run_cli(rest)
    if command == "manage":
        return manage_cli.run_cli(rest)
    if command == "privacy":
        return privacy_grant_cli.run_cli(rest)
    if command == "ux":
        return ux_cli.run_cli(rest)

    _enable_interactive_consent_for_direct_cli(explicit=explicit_interactive)
    if command == "issue-to-pr":
        forwarded, error = _issue_to_pr(rest)
        if forwarded is None:
            return _friendly_error(error, command="issue-to-pr")
        return opencode_entrypoint.run(forwarded)
    if command == "resume":
        return opencode_entrypoint.run(["coordinate", "--resume", *rest])
    return opencode_entrypoint.run(values)


def run(argv: list[str] | None = None) -> int:
    raw_values = list(sys.argv[1:] if argv is None else argv)
    values, explicit_interactive = _consume_interactive_consent_argument(raw_values)
    values, target, error = _consume_repository_target_arguments(values)
    if values is None:
        return _friendly_error(error)

    old_owner = os.environ.get("GITHUB_OWNER")
    old_repo = os.environ.get("GITHUB_REPO")
    try:
        if target is not None:
            os.environ["GITHUB_OWNER"], os.environ["GITHUB_REPO"] = target
        return _dispatch(values, explicit_interactive=explicit_interactive)
    finally:
        if target is not None:
            if old_owner is None:
                os.environ.pop("GITHUB_OWNER", None)
            else:
                os.environ["GITHUB_OWNER"] = old_owner
            if old_repo is None:
                os.environ.pop("GITHUB_REPO", None)
            else:
                os.environ["GITHUB_REPO"] = old_repo


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
