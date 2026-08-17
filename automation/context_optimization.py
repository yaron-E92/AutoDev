from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from automation import headroom, opencode_adapter, semantic_verifier, workflow_stages
from automation.model_providers import load_provider_config
from automation.prompt_policies import compose_prompt


PROFILE_FILE = "context-profile.jsonl"
VERIFIER_EVIDENCE_FILE = "verification-evidence.json"
VERIFIER_DIFF_FILE = "verification-diff.patch"
HEAVY_ROLES = ("planner", "implementer", "fixer", "verifier")
SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def approximate_tokens(chars: int) -> int:
    """Provider-neutral prompt estimate used only for sizing comparisons."""
    return int(math.ceil(max(0, int(chars)) / 4.0))


def _repo_relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _artifact_component(repo: Path, path: Path, *, required: bool, purpose: str) -> dict[str, object]:
    value = _read_text(path)
    return {
        "artifact": _repo_relative(repo, path),
        "required": required,
        "purpose": purpose,
        "characters": len(value),
        "utf8_bytes": len(value.encode("utf-8", errors="replace")),
        "approx_tokens": approximate_tokens(len(value)),
        "sha256": _sha256_text(value) if value else "",
        "exists": path.is_file(),
    }


def _profile_path(current: Path) -> Path:
    return current / PROFILE_FILE


def _append_profile(current: Path, record: dict[str, object]) -> None:
    path = _profile_path(current)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def _provider_profile_config(repo: Path, state: dict[str, object]) -> dict[str, object]:
    raw = str(state.get("ProviderProfile", "")).strip()
    if not raw:
        return {}
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = repo / path
    try:
        value = load_provider_config(str(path))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _headroom_metadata(repo: Path, state: dict[str, object], role: str) -> dict[str, object]:
    config = _provider_profile_config(repo, state)
    try:
        resolved = headroom.resolve_headroom_values(config, role)
        selected = headroom.headroom_config_from_values(resolved)
    except (headroom.HeadroomError, ValueError):
        return {
            "configured": False,
            "applied_to_opencode_role": False,
            "status": "invalid-or-unavailable-config",
        }
    return {
        "configured": bool(selected.enabled),
        "applied_to_opencode_role": False,
        "status": "not-in-direct-opencode-transport" if selected.enabled else "disabled",
        "reason": (
            "OpenCode owns the provider transport for direct role runs; AutoDev does not "
            "route exact durable role evidence through the Headroom proxy."
        ),
    }


def _current_policy(repo: Path, current: Path, role: str) -> str:
    state = opencode_adapter._read_state(current)
    return str(opencode_adapter._resolved_policies(repo, state).get(role, "off"))


def _compose(role: str, raw: str, repo: Path, current: Path) -> tuple[str, str]:
    mode = _current_policy(repo, current, role)
    return compose_prompt(role, raw, mode), mode


def _planner_prompt() -> str:
    return """Use the issue-to-pr-automation skill.

You are the Planner for this repository. Operating mode: PLAN ONLY - NO CODE.

Read authoritative durable evidence from these repository-relative artifacts:
1. `.autodev-run/current/issue.md` — read once; it is the source of truth for requirements and acceptance criteria.
2. `.autodev-run/current/synthesized-handoff.md` — read once; it is the primary repository-evidence handoff.
3. `.autodev-run/current/detected-facts.json` and `.autodev-run/current/workspace-snapshot.json` — consult only to validate paths/facts you actually use.
4. `.autodev-run/current/recommended-command-groups.json` — consult only for verification scope.
5. `.autodev-run/current/coder-plan.md` — normally DO NOT read. It is a prior planning artifact and is redundant with your role. Read it only if the synthesized handoff explicitly says synthesis is unavailable/fallback or a critical ambiguity remains.

Do not re-copy durable evidence into scratch artifacts and do not reread an artifact unless needed to resolve a concrete ambiguity.

Use `.autodev-run/current/plan.template.md` as the exact six-section output structure. Ground paths in durable evidence, use at most four implementation steps, prefer the smallest safe complete change, and do not invent architecture or unrelated work. Write only `.autodev-run/current/plan.md`; no preamble or scratchpad.
"""


def _implementer_prompt() -> str:
    return """Use the issue-to-pr-automation skill.

You are the Implementer editing this repository in FAST PATCH MODE.

Read these durable artifacts once:
1. `.autodev-run/current/issue.md` — authoritative requirements and acceptance criteria.
2. `.autodev-run/current/plan.md` — authoritative implementation constraints; if absent, use `.autodev-run/current/coder-plan.md`.
3. `.autodev-run/current/synthesized-handoff.md` — optional; read only when the plan cites unresolved repository evidence or you must resolve a concrete ambiguity.
4. `.autodev-run/current/recommended-command-groups.json` — optional; verification is owned by AutoDev after the edit.

Then inspect only the repository source files needed by the plan. Do not reread the issue/plan through other artifacts. Preserve existing behavior and boundaries unless the issue explicitly requires a change. Avoid unrelated refactors, broad formatting, TODO-only work, speculative abstractions, and dependency churn.

Edit the workspace directly. Write one concise imperative commit-message line to `.autodev-run/current/commit-message.txt`. Leave branch, commit, push, issue mutation, PR, CI, and final verification to AutoDev.
"""


def _fixer_prompt(source_relative: str) -> str:
    return f"""You are the AutoDev Fixer. Apply one targeted repair only.

Read the authoritative repair instructions once from `{source_relative}`. Do not reload the original issue, plan, or broad repository handoffs unless that repair artifact explicitly tells you to inspect a named fact. Inspect only the source files necessary to make the repair.

Do not create workflow state, commits, branches, PRs, or issue mutations. Leave re-verification and durable acceptance to AutoDev.
"""


def _verifier_prompt() -> str:
    return f"""You are the independent AutoDev semantic Verifier. Review only; do not edit source.

Read these exact durable artifacts:
1. `.autodev-run/current/issue.md` — authoritative acceptance criteria.
2. `.autodev-run/current/{VERIFIER_EVIDENCE_FILE}` — changed-file list plus deterministic and cross-file evidence.
3. `.autodev-run/current/{VERIFIER_DIFF_FILE}` — exact current implementation diff.
4. `.autodev-run/current/verification-result.template.json` — preserve every pre-populated criterion verbatim.

Read `.autodev-run/current/plan.md` only when needed to resolve intended scope. Read `.autodev-run/current/synthesized-handoff.md` only when a concrete repository fact cannot be resolved from the issue, diff, evidence, or inspected source. Read `.autodev-run/current/verification-notes.md` if it exists. Do not reread the same evidence through another artifact.

Independently decide whether the implementation satisfies the issue. Use only verdict `pass|repair|blocked`, requirement status `met|missing|uncertain`, and finding severity `blocking|warning`. Evidence fields are string arrays. Write JSON only to `.autodev-run/current/verification-result.json`.
"""


def _write_verifier_evidence(repo: Path, current: Path) -> None:
    changed_files = semantic_verifier.collect_changed_files(repo)
    diff = semantic_verifier.collect_current_diff(repo, changed_files)
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "changed_files": changed_files,
        "deterministic_evidence": semantic_verifier.collect_deterministic_evidence(current),
        "cross_file_regression_evidence": semantic_verifier.collect_cross_file_regression_evidence(
            repo, changed_files, diff
        ),
    }
    _write_json(current / VERIFIER_EVIDENCE_FILE, evidence)
    _write_text(current / VERIFIER_DIFF_FILE, diff)


def _components_for_role(
    repo: Path,
    current: Path,
    role: str,
    *,
    fixer_source: Path | None = None,
) -> list[dict[str, object]]:
    if role == "planner":
        specs = [
            ("issue.md", True, "requirements"),
            ("synthesized-handoff.md", True, "primary repository evidence"),
            ("detected-facts.json", False, "path/fact validation"),
            ("workspace-snapshot.json", False, "path grounding"),
            ("recommended-command-groups.json", False, "verification scope"),
            ("coder-plan.md", False, "fallback-only prior plan"),
        ]
    elif role == "implementer":
        plan_name = "plan.md" if (current / "plan.md").is_file() else "coder-plan.md"
        specs = [
            ("issue.md", True, "requirements"),
            (plan_name, True, "implementation constraints"),
            ("synthesized-handoff.md", False, "ambiguity-only repository evidence"),
            ("recommended-command-groups.json", False, "verification scope"),
        ]
    elif role == "verifier":
        specs = [
            ("issue.md", True, "acceptance criteria"),
            (VERIFIER_EVIDENCE_FILE, True, "deterministic and cross-file evidence"),
            (VERIFIER_DIFF_FILE, True, "exact implementation diff"),
            ("verification-result.template.json", True, "output schema and criteria"),
            ("plan.md", False, "scope clarification"),
            ("synthesized-handoff.md", False, "repository fact clarification"),
            ("verification-notes.md", False, "uncertainty/skipped checks"),
        ]
    elif role == "fixer" and fixer_source is not None:
        return [
            _artifact_component(
                repo,
                fixer_source,
                required=True,
                purpose="targeted repair instructions",
            )
        ]
    else:
        return []
    return [
        _artifact_component(repo, current / name, required=required, purpose=purpose)
        for name, required, purpose in specs
        if (current / name).is_file()
    ]


def _record(
    repo: Path,
    current: Path,
    role: str,
    baseline: str,
    raw_control: str,
    effective_control: str,
    policy_mode: str,
    components: list[dict[str, object]],
) -> dict[str, object]:
    required_chars = sum(
        int(item.get("characters", 0) or 0)
        for item in components
        if bool(item.get("required"))
    )
    optional_chars = sum(
        int(item.get("characters", 0) or 0)
        for item in components
        if not bool(item.get("required"))
    )
    state = opencode_adapter._read_state(current)
    record = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": _utc_now(),
        "issue_number": int(state.get("IssueNumber", 0) or 0),
        "role": role,
        "measurement": "prepared-role-context",
        "baseline": {
            "characters": len(baseline),
            "approx_tokens": approximate_tokens(len(baseline)),
            "sha256": _sha256_text(baseline),
        },
        "optimized_control": {
            "raw_characters": len(raw_control),
            "raw_approx_tokens": approximate_tokens(len(raw_control)),
            "effective_characters": len(effective_control),
            "effective_approx_tokens": approximate_tokens(len(effective_control)),
            "sha256": _sha256_text(effective_control),
        },
        "evidence": {
            "required_characters": required_chars,
            "required_approx_tokens": approximate_tokens(required_chars),
            "optional_characters": optional_chars,
            "optional_approx_tokens": approximate_tokens(optional_chars),
            "components": components,
        },
        "projection": {
            "initial_prompt_characters_saved": max(0, len(baseline) - len(effective_control)),
            "initial_prompt_reduction_ratio": (
                round(1.0 - (len(effective_control) / len(baseline)), 4)
                if baseline
                else 0.0
            ),
            "required_context_upper_bound_characters": len(effective_control) + required_chars,
            "required_context_upper_bound_approx_tokens": approximate_tokens(
                len(effective_control) + required_chars
            ),
            "all_listed_evidence_upper_bound_characters": (
                len(effective_control) + required_chars + optional_chars
            ),
            "all_listed_evidence_upper_bound_approx_tokens": approximate_tokens(
                len(effective_control) + required_chars + optional_chars
            ),
        },
        "ponytail_prompt_policy": {
            "mode": policy_mode,
            "raw_characters": len(raw_control),
            "effective_characters": len(effective_control),
            "character_delta": len(effective_control) - len(raw_control),
        },
        "headroom": _headroom_metadata(repo, state, role),
    }
    _append_profile(current, record)
    return record


def optimize_prepared_role(
    repo: Path,
    role: str,
    *,
    arguments: str = "",
) -> Path:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    if role not in HEAVY_ROLES:
        return current / f"{role}.md"

    path = current / f"{role}.md"
    baseline = _read_text(path)
    if not baseline:
        return path

    fixer_source: Path | None = None
    if role == "planner":
        raw = _planner_prompt()
    elif role == "implementer":
        raw = _implementer_prompt()
    elif role == "fixer":
        fixer_source = opencode_adapter._fixer_source(current, arguments)
        raw = _fixer_prompt(_repo_relative(repo, fixer_source))
    else:
        _write_verifier_evidence(repo, current)
        raw = _verifier_prompt()

    effective, policy_mode = _compose(role, raw, repo, current)
    components = _components_for_role(
        repo,
        current,
        role,
        fixer_source=fixer_source,
    )
    _write_text(path, effective)
    _record(
        repo,
        current,
        role,
        baseline,
        raw,
        effective,
        policy_mode,
        components,
    )
    return path


def latest_profiles(repo: Path) -> dict[str, dict[str, object]]:
    current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
    result: dict[str, dict[str, object]] = {}
    try:
        lines = _profile_path(current).read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        role = str(value.get("role", ""))
        if role in HEAVY_ROLES:
            result[role] = value
    return result


def render_report(repo: Path) -> str:
    profiles = latest_profiles(repo)
    if not profiles:
        return "No AutoDev context profiles have been recorded for this run."
    lines = [
        "AutoDev prepared-role context profile",
        "(approximate tokens use a provider-neutral 4 characters/token estimate)",
        "",
        "role         baseline    control     required-upper-bound   initial-reduction   policy",
    ]
    for role in HEAVY_ROLES:
        item = profiles.get(role)
        if not item:
            continue
        baseline = item.get("baseline", {})
        control = item.get("optimized_control", {})
        projection = item.get("projection", {})
        policy = item.get("ponytail_prompt_policy", {})
        lines.append(
            f"{role:<12} "
            f"{int(baseline.get('approx_tokens', 0) or 0):>8}t "
            f"{int(control.get('effective_approx_tokens', 0) or 0):>9}t "
            f"{int(projection.get('required_context_upper_bound_approx_tokens', 0) or 0):>20}t "
            f"{float(projection.get('initial_prompt_reduction_ratio', 0.0) or 0.0):>16.1%} "
            f"{str(policy.get('mode', 'off'))}"
        )
    return "\n".join(lines)


_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    current_prepare = opencode_adapter.prepare_role
    if not getattr(current_prepare, "_autodev_context_optimized", False):
        original_prepare = current_prepare

        def prepare_role(
            role: str,
            repo: Path,
            arguments: str,
            *,
            autodev_root: Path = opencode_adapter.AUTODEV_ROOT,
        ) -> Path:
            path = original_prepare(
                role,
                repo,
                arguments,
                autodev_root=autodev_root,
            )
            if role in HEAVY_ROLES:
                return optimize_prepared_role(repo, role, arguments=arguments)
            return path

        prepare_role._autodev_context_optimized = True  # type: ignore[attr-defined]
        opencode_adapter.prepare_role = prepare_role

    current_execute = workflow_stages.execute_stage
    if not getattr(current_execute, "_autodev_context_optimized", False):
        original_execute = current_execute

        def execute_stage(
            name: str,
            repo: Path,
            *,
            arguments: str = "",
            autodev_root: Path = workflow_stages.AUTODEV_ROOT,
            attempt: int = 0,
            reason: str = "",
            runner=None,
            which=None,
        ):
            kwargs = {
                "arguments": arguments,
                "autodev_root": autodev_root,
                "attempt": attempt,
                "reason": reason,
            }
            if runner is not None:
                kwargs["runner"] = runner
            if which is not None:
                kwargs["which"] = which
            code, payload = original_execute(name, repo, **kwargs)
            if (
                name == "render-implementer"
                and isinstance(payload, dict)
                and payload.get("state") == "CONTINUE"
            ):
                optimize_prepared_role(repo, "implementer")
            return code, payload

        execute_stage._autodev_context_optimized = True  # type: ignore[attr-defined]
        workflow_stages.execute_stage = execute_stage

    _INSTALLED = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report AutoDev prepared-role context contributions for the current run."
    )
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    print(render_report(Path(args.repo)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
