from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FACADE_SYMBOLS: dict[str, dict[str, str]] = {
    "model_providers": {
        "ModelConfig": "provider_contract",
        "ModelProvider": "provider_contract",
        "PROVIDER_ALIASES": "provider_contract",
        "ProviderError": "provider_contract",
        "ProviderResponse": "provider_contract",
        "SAFE_HEADER_NAMES": "provider_contract",
        "SENSITIVE_HEADER_NAMES": "provider_contract",
        "SUPPORTED_PROVIDERS": "provider_contract",
        "apply_free_only_routing": "provider_requests",
        "apply_model_selection": "provider_requests",
        "build_chat_completions_body": "provider_requests",
        "build_responses_body": "provider_requests",
        "classify_http_status": "provider_requests",
        "http_failure_message": "provider_requests",
        "response_telemetry": "provider_requests",
        "validate_output_limit": "provider_requests",
        "validate_safe_headers": "provider_requests",
        "validated_request_options": "provider_requests",
        "CommandProvider": "provider_command",
        "quote_shell_argument": "provider_command",
        "ChatCompletionsProvider": "provider_http",
        "ResponsesProvider": "provider_http",
        "_OpenAICompatibleProvider": "provider_http",
        "HeadroomProvider": "provider_headroom",
        "headroom_role_from_prompt": "provider_headroom",
        "with_headroom_role": "provider_headroom",
        "MockProvider": "provider_mock",
        "create_provider": "provider_factory",
        "load_provider_config": "provider_factory",
        "model_config_from_values": "provider_factory",
        "normalize_provider_name": "provider_factory",
        "object_map": "provider_factory",
        "object_string_map": "provider_factory",
        "ollama_command_for_model": "provider_factory",
        "resolve_model_config": "provider_factory",
    },
    "issue_queue": {
        "API_VERSION": "queue_contract",
        "ATTENTION_LABEL": "queue_contract",
        "BLOCKED_LABEL": "queue_contract",
        "Blocker": "queue_contract",
        "CommandResult": "queue_contract",
        "DEFAULT_LIMIT": "queue_contract",
        "LABEL_SPECS": "queue_contract",
        "MANAGED_LABEL": "queue_contract",
        "QUEUE_CONFIG": "queue_contract",
        "QueueError": "queue_contract",
        "QueueIssue": "queue_contract",
        "QueuePolicy": "queue_contract",
        "QueueState": "queue_contract",
        "READY_LABEL": "queue_contract",
        "RUNNING_LABEL": "queue_contract",
        "_label_names": "queue_contract",
        "_milestone_title": "queue_contract",
        "load_policy": "queue_policy",
        "_json_result": "queue_github",
        "_queue_issue": "queue_github",
        "_run_gh": "queue_github",
        "ensure_queue_labels": "queue_github",
        "fetch_issue": "queue_github",
        "list_blockers": "queue_github",
        "list_issues": "queue_github",
        "remove_dependency": "queue_github",
        "resolve_github_repo": "queue_github",
        "_desired_derived_labels": "queue_classification",
        "_split_blockers": "queue_classification",
        "_update_derived_labels": "queue_classification",
        "classify_issue": "queue_classification",
        "inspect_queue": "queue_workflow",
        "reconcile_queue": "queue_workflow",
        "_state_json": "queue_presentation",
        "explain_state": "queue_presentation",
        "queue_summary": "queue_presentation",
        "_parser": "queue_cli",
        "run_cli": "queue_cli",
    },
    "distributed_claims": {
        "CLAIM_MESSAGE": "claim_contract",
        "CLAIM_REF_PREFIX": "claim_contract",
        "CLAIM_SCHEMA": "claim_contract",
        "Claim": "claim_contract",
        "ClaimAttempt": "claim_contract",
        "ClaimError": "claim_contract",
        "ClaimPolicy": "claim_contract",
        "DEFAULT_LEASE_MINUTES": "claim_contract",
        "DEFAULT_MAX_CONCURRENT_ISSUES": "claim_contract",
        "MAX_CONCURRENT_ISSUES": "claim_contract",
        "MAX_LEASE_MINUTES": "claim_contract",
        "MIN_LEASE_MINUTES": "claim_contract",
        "RecoveryResult": "claim_contract",
        "WORKER_ID_ENV": "claim_contract",
        "WORKER_SCHEMA": "claim_contract",
        "WORKER_STATE": "claim_contract",
        "WorkerIdentity": "claim_contract",
        "_WORKER_ID": "claim_contract",
        "_ZERO_SHA": "claim_contract",
        "_iso": "claim_contract",
        "_now": "claim_contract",
        "_parse_time": "claim_contract",
        "claim_ref": "claim_contract",
        "_validate_worker_id": "claim_identity",
        "load_claim_policy": "claim_identity",
        "set_worker_identity": "claim_identity",
        "worker_identity": "claim_identity",
        "worker_state_path": "claim_identity",
        "_git": "claim_process",
        "_is_push_race": "claim_process",
        "_require_ok": "claim_process",
        "_returncode": "claim_process",
        "_run": "claim_process",
        "_stderr": "claim_process",
        "_stdout": "claim_process",
        "_base_commit": "claim_repository",
        "_claim_message": "claim_repository",
        "_claim_metadata": "claim_repository",
        "_create_claim_commit": "claim_repository",
        "_delete_with_lease": "claim_repository",
        "_new_claim": "claim_repository",
        "_parse_claim_message": "claim_repository",
        "_push_with_lease": "claim_repository",
        "_read_claim_from_ref": "claim_repository",
        "_remote_ref_sha": "claim_repository",
        "claim_expired": "claim_repository",
        "get_claim": "claim_repository",
        "list_claims": "claim_repository",
        "_set_running_label": "claim_recovery",
        "reconcile_stale_claims": "claim_recovery",
        "recovery_evidence": "claim_recovery",
        "HeartbeatLease": "claim_lease",
        "acquire_claim": "claim_lease",
        "active_claims": "claim_lease",
        "release_claim": "claim_lease",
        "renew_claim": "claim_lease",
        "run_worker_cli": "claim_cli",
    },
    "privacy_grants": {
        "DEFAULT_STORE": "privacy_grant_contract",
        "DURATIONS": "privacy_grant_contract",
        "DURATION_DELTAS": "privacy_grant_contract",
        "REPOSITORY_ID_ENV": "privacy_grant_contract",
        "SCOPES": "privacy_grant_contract",
        "STORE_ENV": "privacy_grant_contract",
        "STORE_VERSION": "privacy_grant_contract",
        "_BYPASS_DEPTH": "privacy_grant_contract",
        "_iso": "privacy_grant_store",
        "_load_store": "privacy_grant_store",
        "_normalize_github_remote": "privacy_grant_store",
        "_now": "privacy_grant_store",
        "_parse_time": "privacy_grant_store",
        "_save_store": "privacy_grant_store",
        "_store_path": "privacy_grant_store",
        "repository_identity": "privacy_grant_store",
        "_grant_id": "privacy_grant_matching",
        "_grant_matches": "privacy_grant_matching",
        "_policy_fingerprint": "privacy_grant_matching",
        "_provider_identity": "privacy_grant_matching",
        "_route_identity": "privacy_grant_matching",
        "_status": "privacy_grant_matching",
        "bypass_grants": "privacy_grant_matching",
        "matching_grant": "privacy_grant_matching",
        "create_grant": "privacy_grant_commands",
        "current_grants": "privacy_grant_commands",
        "revoke_grants": "privacy_grant_commands",
        "_audit_grant_use": "privacy_grant_hooks",
        "_install_privacy_gate": "privacy_grant_hooks",
        "_install_run_consent_hook": "privacy_grant_hooks",
        "_persistent_duration_from_choice": "privacy_grant_hooks",
        "_read_run_choice": "privacy_grant_hooks",
        "install": "privacy_grant_hooks",
        "_parser": "privacy_grant_cli",
        "_prompt_duration": "privacy_grant_cli",
        "_resolve_requirements": "privacy_grant_cli",
        "_run_consent_cli": "privacy_grant_cli",
        "_run_revoke_cli": "privacy_grant_cli",
        "_run_status_cli": "privacy_grant_cli",
        "_select_scope_decisions": "privacy_grant_cli",
        "run_cli": "privacy_grant_cli",
    },
    "scheduler_health": {
        "HEALTH_FILE": "scheduler_health_contract",
        "HEALTH_SCHEMA": "scheduler_health_contract",
        "HEALTH_STATES": "scheduler_health_contract",
        "HealthSnapshot": "scheduler_health_contract",
        "NOTIFICATION_BACKENDS": "scheduler_health_contract",
        "NOTIFICATION_FILE": "scheduler_health_contract",
        "NOTIFICATION_NATIVE": "scheduler_health_contract",
        "NOTIFICATION_OFF": "scheduler_health_contract",
        "NOTIFICATION_SCHEMA": "scheduler_health_contract",
        "NotificationPolicy": "scheduler_health_contract",
        "NotificationResult": "scheduler_health_contract",
        "REMINDER_STATES": "scheduler_health_contract",
        "SchedulerHealthError": "scheduler_health_contract",
        "_iso": "scheduler_health_contract",
        "_now": "scheduler_health_contract",
        "_parse_time": "scheduler_health_contract",
        "_read_json": "scheduler_health_storage",
        "_write_json": "scheduler_health_storage",
        "health_path": "scheduler_health_storage",
        "load_notification_policy": "scheduler_health_storage",
        "notification_path": "scheduler_health_storage",
        "save_notification_policy": "scheduler_health_storage",
        "_blocker_counts": "scheduler_health_probes",
        "_fingerprint": "scheduler_health_probes",
        "_fingerprint_source": "scheduler_health_probes",
        "_first_issue_number": "scheduler_health_probes",
        "_privacy_grant_summary": "scheduler_health_probes",
        "_privacy_probe": "scheduler_health_probes",
        "_raw_run_status": "scheduler_health_probes",
        "compute_health": "scheduler_health_probes",
        "render_health": "scheduler_health_probes",
        "_native_notify": "scheduler_health_notifications",
        "_notification_message": "scheduler_health_notifications",
        "_should_notify": "scheduler_health_notifications",
        "_snapshot_from_json": "scheduler_health_notifications",
        "observe_health": "scheduler_health_notifications",
        "_location_parser": "scheduler_health_lifecycle",
        "_resolve_registration": "scheduler_health_lifecycle",
        "current_health": "scheduler_health_lifecycle",
        "run_tick": "scheduler_health_lifecycle",
        "_cleanup_health_state": "scheduler_health_cli",
        "run_cli": "scheduler_health_cli",
        "run_health": "scheduler_health_cli",
        "run_notifications": "scheduler_health_cli",
        "run_status": "scheduler_health_cli",
    },
    "semantic_repair_budget": {
        "ADAPTIVE_BASE_ENV": "repair_budget_contract",
        "ADAPTIVE_MAX_ENV": "repair_budget_contract",
        "ADAPTIVE_MIN_ENV": "repair_budget_contract",
        "DEFAULT_ADAPTIVE_BASE": "repair_budget_contract",
        "DEFAULT_ADAPTIVE_MAX": "repair_budget_contract",
        "DEFAULT_ADAPTIVE_MIN": "repair_budget_contract",
        "DEFAULT_LINES_PER_ATTEMPT": "repair_budget_contract",
        "FAILURE_REPAIR_BUDGET_EXHAUSTED": "repair_budget_contract",
        "FIXED_LIMIT_ENV": "repair_budget_contract",
        "FORMULA_VERSION": "repair_budget_contract",
        "LINES_PER_ATTEMPT_ENV": "repair_budget_contract",
        "POLICY_ENV": "repair_budget_contract",
        "ROOT_FAILURE_CLASSIFICATION": "repair_budget_contract",
        "SemanticRepairBudgetError": "repair_budget_contract",
        "_BINARY_SUFFIXES": "repair_budget_contract",
        "_GENERATED_PREFIXES": "repair_budget_contract",
        "_changed_lines": "repair_budget_metrics",
        "_generated": "repair_budget_metrics",
        "_line_count": "repair_budget_metrics",
        "_path_weight": "repair_budget_metrics",
        "change_metrics": "repair_budget_metrics",
        "_nonnegative_int": "repair_budget_policy",
        "_policy": "repair_budget_policy",
        "_positive_int": "repair_budget_policy",
        "_resume_budget": "repair_budget_policy",
        "resolve_budget": "repair_budget_policy",
        "validate_config": "repair_budget_policy",
        "concise_failure_reason": "repair_budget_failure",
        "failure_details": "repair_budget_failure",
        "human_failure_summary": "repair_budget_failure",
        "_read_json": "repair_budget_storage",
        "_write_json": "repair_budget_storage",
        "clear_failure_state": "repair_budget_storage",
        "persist_budget": "repair_budget_storage",
        "persist_failure": "repair_budget_storage",
        "install_run_manifest_hooks": "repair_budget_manifest",
        "_append_resume_metadata": "repair_budget_resume",
        "_status_metadata": "repair_budget_resume",
        "install_opencode_resume_hooks": "repair_budget_resume",
        "maybe_reopen_exhausted_budget": "repair_budget_resume",
    },
}

FACADES = set(FACADE_SYMBOLS)
OWNER_MODULES = sorted({owner for symbols in FACADE_SYMBOLS.values() for owner in symbols.values()})


def rewrite_imports(text: str) -> str:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    replacements: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == "automation":
                aliases = [alias for alias in node.names if alias.name not in FACADES]
                if len(aliases) != len(node.names):
                    rendered = ""
                    if aliases:
                        rendered = "from automation import " + ", ".join(
                            alias.name + (f" as {alias.asname}" if alias.asname else "")
                            for alias in aliases
                        ) + "\n"
                    replacements.append((node.lineno - 1, node.end_lineno or node.lineno, rendered))
            elif node.module and node.module.startswith("automation."):
                facade = node.module.removeprefix("automation.")
                if facade in FACADES:
                    grouped: dict[str, list[ast.alias]] = {}
                    for alias in node.names:
                        owner = FACADE_SYMBOLS[facade].get(alias.name)
                        if owner is None:
                            raise SystemExit(
                                f"unmapped import {node.module}.{alias.name}"
                            )
                        grouped.setdefault(owner, []).append(alias)
                    rendered_parts = []
                    for owner, aliases in sorted(grouped.items()):
                        rendered_parts.append(
                            f"from automation.{owner} import "
                            + ", ".join(
                                alias.name + (f" as {alias.asname}" if alias.asname else "")
                                for alias in aliases
                            )
                            + "\n"
                        )
                    replacements.append(
                        (node.lineno - 1, node.end_lineno or node.lineno, "".join(rendered_parts))
                    )
        elif isinstance(node, ast.Import):
            if any(alias.name.removeprefix("automation.") in FACADES for alias in node.names):
                kept = [alias for alias in node.names if alias.name.removeprefix("automation.") not in FACADES]
                rendered = ""
                if kept:
                    rendered = "import " + ", ".join(
                        alias.name + (f" as {alias.asname}" if alias.asname else "")
                        for alias in kept
                    ) + "\n"
                replacements.append((node.lineno - 1, node.end_lineno or node.lineno, rendered))
    for start, end, rendered in sorted(replacements, reverse=True):
        lines[start:end] = [rendered] if rendered else []
    return "".join(lines)


def transform(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    original = text
    for facade, symbols in FACADE_SYMBOLS.items():
        for symbol, owner in symbols.items():
            text = text.replace(
                f"automation.{facade}.{symbol}",
                f"automation.{owner}.{symbol}",
            )
            text = re.sub(
                rf"\bpatch\.object\(\s*{re.escape(facade)}\s*,\s*(['\"]){re.escape(symbol)}\1",
                lambda match, owner=owner, symbol=symbol: f"patch.object({owner}, {match.group(1)}{symbol}{match.group(1)}",
                text,
            )
            text = re.sub(
                rf"\b{re.escape(facade)}\.{re.escape(symbol)}\b",
                f"{owner}.{symbol}",
                text,
            )
    if text == original and not any(f"automation.{name}" in text for name in FACADES):
        return
    text = rewrite_imports(text)
    needed = [owner for owner in OWNER_MODULES if re.search(rf"\b{re.escape(owner)}\.", text)]
    missing = [owner for owner in needed if not re.search(rf"(?:from automation import[^\n]*\b{re.escape(owner)}\b|import automation\.{re.escape(owner)}\b)", text)]
    if missing:
        insertion = "from automation import " + ", ".join(missing) + "\n"
        future = re.search(r"^from __future__ import annotations\n", text, flags=re.M)
        position = future.end() if future else 0
        text = text[:position] + "\n" + insertion + text[position:]
    path.write_text(text, encoding="utf-8")


def main() -> int:
    roots = (ROOT / "automation", ROOT / "area_reader", ROOT / "tests")
    facade_paths = {ROOT / "automation" / f"{name}.py" for name in FACADES}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path in facade_paths or "__pycache__" in path.parts:
                continue
            transform(path)
    for path in facade_paths:
        path.unlink(missing_ok=True)

    leftovers: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for facade in FACADES:
                if re.search(rf"\b(?:automation\.)?{re.escape(facade)}\b", text):
                    leftovers.append(f"{path.relative_to(ROOT)}: {facade}")
    if leftovers:
        raise SystemExit("facade references remain:\n" + "\n".join(leftovers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
