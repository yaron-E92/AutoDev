from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SplitSpec:
    source: Path
    groups: dict[str, set[str]]


SPECS: dict[str, SplitSpec] = {
    "semantic": SplitSpec(
        Path("automation/semantic_verifier.py"),
        {
            "semantic_contract": {
                "ALLOWED_VERDICTS", "ALLOWED_REQUIREMENT_STATUSES", "ALLOWED_FINDING_SEVERITIES",
                "DEFAULT_MAX_SCHEMA_RETRIES", "DEFAULT_MAX_REPAIR_ATTEMPTS", "MAX_SCHEMA_RETRIES",
                "MAX_REPAIR_ATTEMPTS", "MAX_DIFF_CHARS", "MAX_EVIDENCE_CHARS",
                "MAX_REGRESSION_EVIDENCE_CHARS", "MAX_REGRESSION_SYMBOLS", "MAX_REGRESSION_REFERENCES",
                "MAX_REGRESSION_FILE_BYTES", "SEMANTIC_SOURCE_SUFFIXES", "SEMANTIC_IGNORED_PARTS",
                "_TEMPLATE_PLACEHOLDER", "_LEGACY_ONLY_PLACEHOLDERS", "_DECLARATION_PATTERNS",
                "SemanticVerifierError", "ChangedFileList", "SemanticSettings",
            },
            "semantic_configuration": {
                "resolve_semantic_settings", "safe_semantic_metadata", "_bounded_count", "_config_error",
            },
            "semantic_schema": {
                "semantic_result_template", "parse_semantic_output", "_semantic_schema_errors",
                "_parse_requirements", "_parse_findings", "_malformed",
            },
            "semantic_text": {"render_template", "_bounded"},
            "semantic_prompts": {
                "extract_acceptance_criteria", "build_schema_repair_prompt", "build_semantic_prompt",
                "build_semantic_repair_prompt", "default_semantic_template", "default_repair_template",
            },
            "semantic_evidence": {
                "collect_changed_files", "collect_current_diff", "collect_cross_file_regression_evidence",
                "collect_deterministic_evidence", "_removed_symbol_candidates", "_git_lines", "_git_text",
                "_is_tracked",
            },
            "semantic_storage": {"_read_text", "_read_json"},
            "semantic_artifacts": {
                "semantic_artifact_path", "write_semantic_result", "write_final_verdict",
                "render_semantic_summary", "_write_result_pair",
            },
            "semantic_invocation": {
                "invoke_semantic_verifier", "prepare_semantic_prompt", "prepare_semantic_repair_prompt",
                "resolve_profile_roles",
            },
            "semantic_cli": {"build_parser", "run"},
        },
    ),
    "evaluation": SplitSpec(
        Path("automation/eval_harness_core.py"),
        {
            "evaluation_contract": {
                "REPO_ROOT", "DEFAULT_CASES", "DEFAULT_PROFILES", "DEFAULT_RESULTS_ROOT", "SCHEMA_VERSION",
                "UNKNOWN", "DEPENDENCY_NAMES", "EvalError", "utc_now",
            },
            "evaluation_profiles": {
                "read_json", "load_cases", "load_profiles", "safe_provider_summary", "safe_fallbacks",
                "safe_headroom", "redact", "sanitized_url", "ensure_free_route_safety", "fingerprint",
                "selected_cases",
            },
            "evaluation_scoring": {
                "parse_diff", "path_matches", "stage_record", "repair_count", "invocation_metrics",
                "stage_timing", "semantic_metrics", "score_record", "unavailable_result", "estimate_model_calls",
            },
            "evaluation_execution": {
                "load_replay", "live_plan", "run_live_case", "read_optional_json", "git_diff", "git_rev_parse",
            },
            "evaluation_reporting": {"aggregate", "render_markdown", "write_results"},
            "evaluation_cli": {"build_parser", "validate_budgets", "print_live_plan", "main"},
        },
    ),
    "privacy-grants": SplitSpec(
        Path("automation/privacy_grants.py"),
        {
            "privacy_grant_contract": {
                "STORE_VERSION", "STORE_ENV", "REPOSITORY_ID_ENV", "DEFAULT_STORE", "DURATION_DELTAS",
                "DURATIONS", "SCOPES", "_BYPASS_DEPTH",
            },
            "privacy_grant_store": {
                "_now", "_iso", "_parse_time", "_store_path", "_load_store", "_save_store",
                "_normalize_github_remote", "repository_identity",
            },
            "privacy_grant_matching": {
                "_policy_fingerprint", "_route_identity", "_provider_identity", "_grant_id", "_status",
                "_grant_matches", "matching_grant", "bypass_grants",
            },
            "privacy_grant_commands": {"create_grant", "revoke_grants", "current_grants"},
            "privacy_grant_hooks": {
                "_audit_grant_use", "_install_privacy_gate", "_persistent_duration_from_choice",
                "_read_run_choice", "_install_run_consent_hook", "install",
            },
            "privacy_grant_cli": {
                "_resolve_requirements", "_select_scope_decisions", "_prompt_duration", "_run_consent_cli",
                "_run_status_cli", "_run_revoke_cli", "_parser", "run_cli",
            },
        },
    ),
    "claims": SplitSpec(
        Path("automation/distributed_claims.py"),
        {
            "claim_contract": {
                "CLAIM_SCHEMA", "WORKER_SCHEMA", "CLAIM_MESSAGE", "CLAIM_REF_PREFIX", "WORKER_STATE",
                "DEFAULT_MAX_CONCURRENT_ISSUES", "DEFAULT_LEASE_MINUTES", "MIN_LEASE_MINUTES",
                "MAX_LEASE_MINUTES", "MAX_CONCURRENT_ISSUES", "WORKER_ID_ENV", "_WORKER_ID", "_ZERO_SHA",
                "ClaimError", "ClaimPolicy", "Claim", "ClaimAttempt", "RecoveryResult", "WorkerIdentity",
                "_now", "_iso", "_parse_time", "claim_ref",
            },
            "claim_identity": {
                "_validate_worker_id", "worker_state_path", "set_worker_identity", "worker_identity",
                "load_claim_policy",
            },
            "claim_process": {
                "_run", "_returncode", "_stdout", "_stderr", "_require_ok", "_git", "_is_push_race",
            },
            "claim_repository": {
                "_remote_ref_sha", "_claim_message", "_parse_claim_message", "_read_claim_from_ref",
                "get_claim", "list_claims", "claim_expired", "_base_commit", "_create_claim_commit",
                "_claim_metadata", "_push_with_lease", "_delete_with_lease", "_new_claim",
            },
            "claim_recovery": {"recovery_evidence", "_set_running_label", "reconcile_stale_claims"},
            "claim_lease": {"acquire_claim", "renew_claim", "release_claim", "active_claims", "HeartbeatLease"},
            "claim_cli": {"run_worker_cli"},
        },
    ),
    "scheduler-health": SplitSpec(
        Path("automation/scheduler_health.py"),
        {
            "scheduler_health_contract": {
                "HEALTH_SCHEMA", "NOTIFICATION_SCHEMA", "HEALTH_FILE", "NOTIFICATION_FILE", "NOTIFICATION_OFF",
                "NOTIFICATION_NATIVE", "NOTIFICATION_BACKENDS", "REMINDER_STATES", "HEALTH_STATES",
                "SchedulerHealthError", "NotificationPolicy", "HealthSnapshot", "NotificationResult", "_now",
                "_iso", "_parse_time",
            },
            "scheduler_health_storage": {
                "health_path", "notification_path", "_read_json", "_write_json", "load_notification_policy",
                "save_notification_policy",
            },
            "scheduler_health_probes": {
                "_privacy_grant_summary", "_privacy_probe", "_raw_run_status", "_blocker_counts",
                "_first_issue_number", "_fingerprint_source", "_fingerprint", "compute_health", "render_health",
            },
            "scheduler_health_notifications": {
                "_notification_message", "_native_notify", "_should_notify", "_snapshot_from_json", "observe_health",
            },
            "scheduler_health_lifecycle": {
                "_location_parser", "_resolve_registration", "current_health", "run_tick",
            },
            "scheduler_health_cli": {
                "run_status", "run_health", "run_notifications", "_cleanup_health_state", "run_cli",
            },
        },
    ),
}


def node_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def node_start_line(node: ast.AST) -> int:
    start = int(getattr(node, "lineno", 1))
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        start = min(start, *(int(decorator.lineno) for decorator in decorators))
    return start


def segment(lines: list[str], node: ast.AST) -> str:
    start = node_start_line(node) - 1
    end = int(getattr(node, "end_lineno", start + 1))
    return "".join(lines[start:end]).rstrip() + "\n"


def import_bindings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}
    return set()


def loaded_names(nodes: list[ast.AST]) -> set[str]:
    return {
        item.id
        for node in nodes
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def selective_imports(lines: list[str], tree: ast.Module, nodes: list[ast.AST]) -> str:
    used = loaded_names(nodes)
    selected: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if import_bindings(node) & used:
            selected.append(segment(lines, node).rstrip())
    return "\n".join(selected) + ("\n" if selected else "")


def original_imports(lines: list[str], tree: ast.Module) -> str:
    return "\n".join(
        segment(lines, node).rstrip()
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ) + "\n"


def assert_acyclic(deps: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: list[str]) -> None:
        if module in visited:
            return
        if module in visiting:
            raise SystemExit("split dependency cycle: " + " -> ".join([*trail, module]))
        visiting.add(module)
        for dep in sorted(deps.get(module, ())):
            visit(dep, [*trail, module])
        visiting.remove(module)
        visited.add(module)

    for module in deps:
        visit(module, [])


def compatibility_block(aliases: list[str]) -> str:
    modules = ",\n    ".join(aliases)
    return f'''_COMPAT_MODULES = (\n    {modules},\n)\n_COMPAT_MISSING = object()\n_COMPAT_ORIGINALS = dict(\n    (\n        module,\n        dict(\n            (name, value)\n            for name, value in module.__dict__.items()\n            if name in globals() and not name.startswith("__")\n        ),\n    )\n    for module in _COMPAT_MODULES\n)\n_COMPAT_BASELINE: dict[str, object] = {{}}\n\n\ndef _sync_compat_overrides() -> None:\n    facade = globals()\n    for module, originals in _COMPAT_ORIGINALS.items():\n        namespace = module.__dict__\n        for name, original in originals.items():\n            current = facade.get(name, _COMPAT_MISSING)\n            if current is _COMPAT_MISSING:\n                continue\n            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)\n            namespace[name] = original if current is baseline else current\n\n\ndef _compat_entrypoint(target):\n    @functools.wraps(target)\n    def invoke(*args, **kwargs):\n        _sync_compat_overrides()\n        return target(*args, **kwargs)\n    return invoke\n\n\ndef _install_compat_entrypoints() -> None:\n    facade = globals()\n    wrapped: set[str] = set()\n    for module in _COMPAT_MODULES:\n        for name in tuple(module.__dict__):\n            if name in wrapped or name.startswith("__") or name not in facade:\n                continue\n            value = facade[name]\n            if inspect.isfunction(value) and value.__module__.startswith("automation."):\n                facade[name] = _compat_entrypoint(value)\n                wrapped.add(name)\n\n\n_install_compat_entrypoints()\n_COMPAT_BASELINE.update(globals())\n'''


def split(spec: SplitSpec) -> None:
    source = spec.source
    if "_COMPAT_ORIGINALS" in source.read_text(encoding="utf-8") and all(
        (source.parent / f"{module}.py").is_file() for module in spec.groups
    ):
        print(f"{source} already split")
        return

    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    owner = {name: module for module, names in spec.groups.items() for name in names}
    if len(owner) != sum(len(names) for names in spec.groups.values()):
        raise SystemExit(f"duplicate assignments in split spec for {source}")

    nodes: dict[str, list[ast.AST]] = defaultdict(list)
    cross: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    seen: set[str] = set()
    known = set(owner)
    for node in tree.body:
        names = node_names(node)
        if not names:
            continue
        modules = {owner.get(name) for name in names}
        if None in modules:
            missing = sorted(name for name in names if name not in owner)
            raise SystemExit(f"unassigned definitions in {source}: {', '.join(missing)}")
        if len(modules) != 1:
            raise SystemExit(f"one node spans split groups in {source}: {sorted(names)}")
        module = modules.pop()
        assert module is not None
        nodes[module].append(node)
        seen.update(names)
        for dep in (loaded_names([node]) & known) - names:
            dep_module = owner[dep]
            if dep_module != module:
                cross[module][dep_module].add(dep)
    missing = sorted(known - seen)
    if missing:
        raise SystemExit(f"missing assigned definitions in {source}: {', '.join(missing)}")
    assert_acyclic({module: set(cross[module]) for module in spec.groups})

    exports: dict[str, list[str]] = {}
    for module in spec.groups:
        module_nodes = nodes[module]
        parts = ["from __future__ import annotations\n\n", selective_imports(lines, tree, module_nodes), "\n"]
        for dep_module, names in sorted(cross[module].items()):
            rendered = ",\n    ".join(sorted(names))
            parts.append(f"from automation.{dep_module} import (\n    {rendered},\n)\n")
        parts.append("\n")
        exports[module] = sorted({name for node in module_nodes for name in node_names(node)})
        for node in module_nodes:
            parts.append(segment(lines, node).rstrip() + "\n\n")
        target = source.parent / f"{module}.py"
        target.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
        print(f"wrote {target} ({len(target.read_text(encoding='utf-8').splitlines())} lines)")

    aliases = [f"_m{i}" for i in range(len(exports))]
    module_import_lines = "\n".join(
        f"from automation import {module} as {alias}" for alias, module in zip(aliases, exports)
    )
    export_import_lines = "\n\n".join(
        f"from automation.{module} import (\n    " + ",\n    ".join(names) + "\n)"
        for module, names in exports.items()
    )
    facade = f'''from __future__ import annotations\n\nimport functools\nimport inspect\n\n{original_imports(lines, tree)}\n{module_import_lines}\n\n{export_import_lines}\n\n{compatibility_block(aliases)}\n'''
    if any(
        isinstance(node, ast.If)
        and any(isinstance(item, ast.Name) and item.id == "__name__" for item in ast.walk(node))
        for node in tree.body
    ):
        entry = "main" if "main" in owner else "run_cli" if "run_cli" in owner else "run"
        facade += f'\nif __name__ == "__main__":\n    raise SystemExit({entry}())\n'
    source.write_text(facade.rstrip() + "\n", encoding="utf-8")
    print(f"wrote facade {source} ({len(facade.splitlines())} lines)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=tuple(SPECS))
    args = parser.parse_args()
    split(SPECS[args.target])


if __name__ == "__main__":
    main()
