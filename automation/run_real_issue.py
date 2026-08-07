from __future__ import annotations

import json
import shutil
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from automation import run_real_issue_core as _core
from automation.run_real_issue_core import *  # noqa: F401,F403
from automation.model_providers import ModelConfig, ModelProvider, ProviderResponse, create_provider, load_provider_config
from automation.model_roles import (
    MODEL_ROLES,
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    model_config_to_dict,
    resolve_role_configs,
    safe_role_metadata,
)
from automation.prompt_policies import (
    compose_prompt,
    resolve_prompt_policies,
    role_policy_metadata,
    safe_prompt_policy_metadata,
)
from automation.semantic_verifier import (
    SemanticSettings,
    SemanticVerifierError,
    build_schema_repair_prompt,
    build_semantic_prompt,
    build_semantic_repair_prompt,
    collect_changed_files,
    collect_current_diff,
    parse_semantic_output,
    resolve_semantic_settings,
    safe_semantic_metadata,
    write_final_verdict,
    write_semantic_result,
)

_ACTIVE_ROLES: ContextVar[dict[str, ModelConfig | None] | None] = ContextVar("active_roles", default=None)
_ACTIVE_FACTORY: ContextVar[Callable[[ModelConfig], ModelProvider] | None] = ContextVar("active_factory", default=None)
_ACTIVE_POLICIES: ContextVar[dict[str, str] | None] = ContextVar("active_policies", default=None)
_ACTIVE_SEMANTIC: ContextVar[SemanticSettings | None] = ContextVar("active_semantic", default=None)
_ACTIVE_DEBUG_ARTIFACTS: ContextVar[bool] = ContextVar("active_debug_artifacts", default=False)
_CORE_WRITE_OPERATIONAL_OUTPUTS = _core.write_operational_outputs
_CORE_CREATE_DRAFT_PR = _core.create_draft_pr


class _DeferredProvider(ModelProvider):
    def __init__(self, config: ModelConfig, factory: Callable[[ModelConfig], ModelProvider]):
        self.config = config
        self.factory = factory
        self.provider: ModelProvider | None = None

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if self.provider is None:
            self.provider = self.factory(self.config)
        return self.provider.invoke(prompt, model=model, timeout_seconds=timeout_seconds)

    def generate(self, prompt: str, *, model: str, timeout_seconds: int) -> str:
        return self.invoke(prompt, model=model, timeout_seconds=timeout_seconds).text


def run(argv=None, *, stdout=None, stderr=None, provider_factory=None):
    args = _core.parse_args(argv)
    roles = resolve_role_provider_configs(args)
    policies = resolve_prompt_policy_configs(args)
    semantic = resolve_semantic_verification_settings(args, roles)
    actual_factory = provider_factory or create_provider
    role_token = _ACTIVE_ROLES.set(roles)
    factory_token = _ACTIVE_FACTORY.set(actual_factory)
    policy_token = _ACTIVE_POLICIES.set(policies)
    semantic_token = _ACTIVE_SEMANTIC.set(semantic)
    debug_token = _ACTIVE_DEBUG_ARTIFACTS.set(bool(args.debug_artifacts))
    originals = {
        "resolve_provider_configs": _core.resolve_provider_configs,
        "run_area_reader": _core.run_area_reader,
        "write_operational_outputs": _core.write_operational_outputs,
        "run_implementation_loop": _core.run_implementation_loop,
        "write_provider_metadata": _core.write_provider_metadata,
        "create_draft_pr": _core.create_draft_pr,
        "build_pr_body": _core.build_pr_body,
    }
    for name in ("require_tools", "select_issue", "fetch_issue_text", "ensure_clean_worktree", "ensure_issue_branch"):
        originals[name] = getattr(_core, name)
        setattr(_core, name, globals()[name])
    try:
        _core.resolve_provider_configs = resolve_provider_configs
        _core.run_area_reader = run_area_reader
        _core.write_operational_outputs = write_operational_outputs
        _core.run_implementation_loop = run_implementation_loop
        _core.write_provider_metadata = write_provider_metadata
        _core.create_draft_pr = create_draft_pr
        _core.build_pr_body = build_pr_body
        return _core.run(
            argv,
            stdout=stdout,
            stderr=stderr,
            provider_factory=lambda config: _DeferredProvider(config, actual_factory),
        )
    finally:
        for name, value in originals.items():
            setattr(_core, name, value)
        _ACTIVE_ROLES.reset(role_token)
        _ACTIVE_FACTORY.reset(factory_token)
        _ACTIVE_POLICIES.reset(policy_token)
        _ACTIVE_SEMANTIC.reset(semantic_token)
        _ACTIVE_DEBUG_ARTIFACTS.reset(debug_token)


def main(argv=None):
    return run(argv)


def update_issue_labels(repo, github_repo, issue, *, add, remove, stream):
    for label in add:
        run_command(
            ["gh", "issue", "edit", str(issue), "--repo", github_repo, "--add-label", label],
            cwd=repo,
            stream=stream,
        )
    for label in remove:
        run_command(
            ["gh", "issue", "edit", str(issue), "--repo", github_repo, "--remove-label", label],
            cwd=repo,
            stream=stream,
        )


def resolve_role_provider_configs(args) -> dict[str, ModelConfig | None]:
    file_config = load_provider_config(args.provider_config)
    defaults = {
        "reader": _core.default_ollama_command_config(_core.DEFAULT_READER_MODEL),
        "coder": _core.default_ollama_command_config(_core.DEFAULT_CODER_MODEL),
    }
    cli_values = {
        role: _core.provider_cli_values(args, role, file_config, defaults[role])
        for role in ("reader", "coder")
    }
    return resolve_role_configs(defaults=defaults, file_config=file_config, cli_values=cli_values)


def resolve_prompt_policy_configs(args) -> dict[str, str]:
    return resolve_prompt_policies(load_provider_config(args.provider_config))


def resolve_semantic_verification_settings(
    args,
    roles: dict[str, ModelConfig | None],
) -> SemanticSettings:
    return resolve_semantic_settings(
        load_provider_config(args.provider_config),
        verifier_configured=roles.get("verifier") is not None,
    )


def resolve_provider_configs(args):
    roles = _ACTIVE_ROLES.get() or resolve_role_provider_configs(args)
    reader = roles["reader"]
    implementer = roles["implementer"]
    assert reader is not None and implementer is not None
    return reader, implementer


def run_area_reader(repo, issue_text, reader_config, coder_config, out_dir, stream):
    roles = _roles_or_legacy(reader_config, coder_config)
    policies = _policies_or_default()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "resolved-provider-roles.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 2,
                "roles": {
                    role: model_config_to_dict(config)
                    for role, config in roles.items()
                    if config is not None
                },
                "prompt_policy": {
                    "enabled": any(mode != "off" for mode in policies.values()),
                    "roles": policies,
                },
                "semantic_verification": safe_semantic_metadata(_semantic_settings_or_disabled()),
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    argv = [
        "--repo", str(repo),
        "--reader-model", reader_config.model,
        "--coder-model", coder_config.model,
        "--provider-config", str(config_path),
        "--issue", issue_text,
        "--out", str(out_dir),
    ]
    print("Running shared area-reader v2 planner", file=stream)
    exit_code = area_reader_runner.main(argv)  # noqa: F405
    if exit_code:
        raise RunnerError(f"area-reader v2 planner failed with exit code {exit_code}", exit_code)  # noqa: F405


def write_operational_outputs(issue_text, area_out, out_dir, keep_debug):
    source = area_out / "model-invocations.json"
    if source.is_file():
        shutil.copyfile(source, out_dir / "model-invocations.json")
        if keep_debug:
            try:
                records = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                records = []
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict):
                        write_compression_debug_artifact(out_dir, record)
    _CORE_WRITE_OPERATIONAL_OUTPUTS(issue_text, area_out, out_dir, keep_debug)


def run_implementation_loop(
    *, repo, out_dir, issue_text, branch_name,
    coder_provider=None, coder_config=None,
    implementer_provider=None, implementer_config=None,
    fixer_provider=None, fixer_config=None,
    max_fix_attempts, dry_run, stream,
):
    roles = _roles_or_legacy(None, coder_config)
    policies = _policies_or_default()
    implementer_config = implementer_config or roles["implementer"] or coder_config
    fixer_config = fixer_config or roles["fixer"] or implementer_config
    factory = _ACTIVE_FACTORY.get() or create_provider
    implementer_provider = implementer_provider or coder_provider or factory(implementer_config)

    prompt = build_implementation_prompt(  # noqa: F405
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
        coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        recommended_command_groups=read_optional_text(out_dir / "recommended-command-groups.json"),  # noqa: F405
        constraints=read_optional_text(PROMPT_TEMPLATE_DIR / "implementer.md"),  # noqa: F405
        branch_name=branch_name,
    )
    prompt = compose_prompt("implementer", prompt, policies["implementer"])
    write_text(out_dir / "implementation-prompt.md", prompt)  # noqa: F405
    response = call_coder(implementer_provider, implementer_config, prompt, out_dir, 0, role="implementer")
    patch = process_model_response(response, out_dir, 0)  # noqa: F405
    if patch is None:
        verification = VerificationResult(0, 0, "no-change", "NO_CHANGES_REQUIRED", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
    elif dry_run:
        return VerificationResult(0, 0, "dry-run", "Dry-run implementation did not apply patch.", "", out_dir / "verification" / "attempt-0.md")  # noqa: F405
    else:
        apply_patch_file(repo, patch, stream)  # noqa: F405
        verification = run_recommended_verification(out_dir, repo, 0, stream)  # noqa: F405
        write_verification_result(out_dir, verification)  # noqa: F405
        attempt = 1
        while not verification.passed and attempt <= max_fix_attempts:
            if fixer_provider is None:
                fixer_provider = factory(fixer_config)
            fix_prompt = build_fix_prompt(  # noqa: F405
                issue_text=issue_text,
                synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
                coder_plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
                previous_response=read_optional_text(out_dir / "model-responses" / f"attempt-{attempt - 1}.txt"),  # noqa: F405
                current_diff=current_diff(repo, stream),  # noqa: F405
                verification=verification,
            )
            fix_prompt = compose_prompt("fixer", fix_prompt, policies["fixer"])
            write_text(out_dir / "fix-prompt.md", fix_prompt)  # noqa: F405
            response = call_coder(fixer_provider, fixer_config, fix_prompt, out_dir, attempt, role="fixer")
            patch = process_model_response(response, out_dir, attempt)  # noqa: F405
            if patch is None:
                break
            apply_patch_file(repo, patch, stream)  # noqa: F405
            verification = run_recommended_verification(out_dir, repo, attempt, stream)  # noqa: F405
            write_verification_result(out_dir, verification)  # noqa: F405
            attempt += 1

    if not verification.passed:
        return verification
    return run_semantic_verification_gate(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verification=verification,
        roles=roles,
        fixer_provider=fixer_provider,
        fixer_config=fixer_config,
        factory=factory,
        stream=stream,
    )


def run_semantic_verification_gate(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    verification,
    roles: dict[str, ModelConfig | None],
    fixer_provider,
    fixer_config,
    factory,
    stream,
):
    settings = _semantic_settings_or_disabled()
    if not settings.enabled:
        return verification

    verifier_config = roles.get("verifier")
    if verifier_config is None:
        raise RunnerError("semantic verification is enabled but verifier role is unavailable")  # noqa: F405
    verifier_provider = factory(verifier_config)
    result = _invoke_semantic_attempt(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        semantic_attempt=0,
        call_attempt=0,
        settings=settings,
    )
    write_semantic_result(out_dir, 0, result)

    if result["verdict"] == "pass":
        write_final_verdict(out_dir, result)
        return verification
    if result["verdict"] == "blocked":
        write_final_verdict(out_dir, result)
        raise RunnerError("semantic verification blocked the run: " + str(result.get("repair_brief", "")))  # noqa: F405
    if settings.max_repair_attempts < 1:
        write_final_verdict(out_dir, result)
        raise RunnerError("semantic verification requested repair but semantic repair is disabled")  # noqa: F405

    repair_brief_path = out_dir / "verification" / "repair-brief.md"
    write_text(repair_brief_path, str(result.get("repair_brief", "")).strip() + "\n")  # noqa: F405
    changed_files = collect_changed_files(repo)
    repair_prompt = build_semantic_repair_prompt(
        issue_text=issue_text,
        plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        semantic_result=result,
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        template=read_optional_text(PROMPT_TEMPLATE_DIR / "verification-repair.md"),  # noqa: F405
    )
    write_text(out_dir / "verification" / "semantic-repair-prompt.md", repair_prompt)  # noqa: F405
    if fixer_provider is None:
        fixer_provider = factory(fixer_config)
    repair_response = call_coder(
        fixer_provider,
        fixer_config,
        repair_prompt,
        out_dir,
        0,
        role="fixer",
        response_name="semantic-fixer-attempt-0.txt",
    )
    if parse_no_changes_required(repair_response) is not None:  # noqa: F405
        write_final_verdict(out_dir, result)
        raise RunnerError("semantic fixer returned NO_CHANGES_REQUIRED without a final semantic pass")  # noqa: F405
    patch_text = extract_unified_diff(repair_response)  # noqa: F405
    if not patch_text:
        raise RunnerError("semantic fixer did not return a valid patch")  # noqa: F405
    patch_path = out_dir / "verification" / "semantic-repair-attempt-0.patch"
    write_text(patch_path, patch_text)  # noqa: F405
    apply_patch_file(repo, patch_path, stream)  # noqa: F405

    deterministic_attempt = int(getattr(verification, "attempt", 0)) + 1
    verification = run_recommended_verification(out_dir, repo, deterministic_attempt, stream)  # noqa: F405
    write_verification_result(out_dir, verification)  # noqa: F405
    if not verification.passed:
        blocked = dict(result)
        blocked["verdict"] = "blocked"
        blocked["repair_brief"] = "Deterministic verification failed after semantic repair."
        write_final_verdict(out_dir, blocked)
        return verification

    final_result = _invoke_semantic_attempt(
        repo=repo,
        out_dir=out_dir,
        issue_text=issue_text,
        verifier_provider=verifier_provider,
        verifier_config=verifier_config,
        semantic_attempt=1,
        call_attempt=settings.max_schema_retries + 1,
        settings=settings,
    )
    write_semantic_result(out_dir, 1, final_result)
    write_final_verdict(out_dir, final_result)
    if final_result["verdict"] != "pass":
        raise RunnerError("semantic verification did not pass after the targeted repair")  # noqa: F405
    return verification


def _invoke_semantic_attempt(
    *,
    repo: Path,
    out_dir: Path,
    issue_text: str,
    verifier_provider,
    verifier_config,
    semantic_attempt: int,
    call_attempt: int,
    settings: SemanticSettings,
) -> dict[str, object]:
    changed_files = collect_changed_files(repo)
    prompt = build_semantic_prompt(
        issue_text=issue_text,
        synthesized_handoff=read_optional_text(out_dir / "synthesized-handoff.md"),  # noqa: F405
        plan=read_optional_text(out_dir / "coder-plan.md"),  # noqa: F405
        changed_files=changed_files,
        diff=collect_current_diff(repo, changed_files),
        deterministic_evidence=(
            read_optional_text(out_dir / "verification-result-summary.md")  # noqa: F405
            + "\n\n"
            + read_optional_text(out_dir / "recommended-command-groups.json")  # noqa: F405
        ),
        uncertainty_notes=read_optional_text(out_dir / "verification-notes.md"),  # noqa: F405
        template=read_optional_text(PROMPT_TEMPLATE_DIR / "verifier.md"),  # noqa: F405
    )
    write_text(out_dir / "verification" / f"semantic-prompt-{semantic_attempt}.md", prompt)  # noqa: F405
    current_prompt = prompt
    for schema_attempt in range(settings.max_schema_retries + 1):
        response = call_coder(
            verifier_provider,
            verifier_config,
            current_prompt,
            out_dir,
            call_attempt + schema_attempt,
            role="verifier",
            response_name=f"semantic-verifier-{semantic_attempt}-schema-{schema_attempt}.txt",
        )
        try:
            return parse_semantic_output(response)
        except SemanticVerifierError as exc:
            if schema_attempt >= settings.max_schema_retries:
                raise RunnerError(f"semantic verifier output remained malformed: {exc}") from exc  # noqa: F405
            current_prompt = build_schema_repair_prompt(prompt, response, str(exc))
    raise RunnerError("semantic verifier did not return a valid verdict")  # noqa: F405


def call_coder(provider, config, prompt, out_dir, attempt, *, role="implementer", response_name=None):
    metadata_path = out_dir / "model-invocations.json"
    policies = _policies_or_default()
    prompt = compose_prompt(role, prompt, policies[role])
    policy_metadata = role_policy_metadata(role, policies)
    try:
        response, record = invoke_model(provider, config, prompt, role=role, attempt=attempt)
    except ModelInvocationError as exc:
        exc.record.update(policy_metadata)
        append_invocation_metadata(metadata_path, exc.record)
        raise
    record.update(policy_metadata)
    append_invocation_metadata(metadata_path, record)
    if _ACTIVE_DEBUG_ARTIFACTS.get():
        write_compression_debug_artifact(out_dir, record)
    name = response_name or f"attempt-{attempt}.txt"
    write_text(out_dir / "model-responses" / name, response)  # noqa: F405
    return response


def write_compression_debug_artifact(out_dir: Path, record: dict[str, object]) -> None:
    compression = record.get("compression")
    if not isinstance(compression, dict):
        return
    role = str(record.get("role", "unknown"))
    attempt = record.get("attempt", 0)
    payload = {
        "role": role,
        "attempt": attempt,
        "transport": record.get("transport", record.get("provider", "")),
        "model": record.get("model", ""),
        **compression,
    }
    write_json(out_dir / "compression" / f"{role}-attempt-{attempt}.json", payload)  # noqa: F405


def create_draft_pr(repo, github_repo, issue, issue_text, out_dir, reader_config, coder_config, stream):
    return _CORE_CREATE_DRAFT_PR(repo, github_repo, issue, issue_text, out_dir, reader_config, coder_config, stream)


def build_pr_body(issue, issue_text, out_dir, reader_config, coder_config):
    roles = _roles_or_legacy(reader_config, coder_config)
    semantic_verdict = read_optional_text(out_dir / "verification" / "final-verdict.json").strip()  # noqa: F405
    return "\n".join(
        [
            f"Closes #{issue}", "", "Generated by AutoDev.", "", "## Summary", "",
            read_optional_text(out_dir / "coder-plan.md").strip() or "See implementation diff.",  # noqa: F405
            "", "## Deterministic Verification", "",
            read_optional_text(out_dir / "verification-result-summary.md").strip(),  # noqa: F405
            "", "## Semantic Verification", "",
            "```json", semantic_verdict or json.dumps({"enabled": False}, indent=2), "```",
            "", "## Provider Roles", "", "```json",
            json.dumps(safe_role_metadata(roles), indent=2, sort_keys=True),
            "```", "", "## Prompt Policy", "", "```json",
            json.dumps(safe_prompt_policy_metadata(_policies_or_default()), indent=2, sort_keys=True),
            "```", "",
        ]
    )


def write_provider_metadata(out_dir, reader_config, coder_config):
    roles = _roles_or_legacy(reader_config, coder_config)
    write_json(  # noqa: F405
        out_dir / "provider-metadata.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reader": reader_config.safe_metadata(),
            "coder": coder_config.safe_metadata(),
            "roles": safe_role_metadata(roles),
            "prompt_policy": safe_prompt_policy_metadata(_policies_or_default()),
            "semantic_verification": safe_semantic_metadata(_semantic_settings_or_disabled()),
        },
    )


def _roles_or_legacy(reader_config, coder_config):
    roles = _ACTIVE_ROLES.get()
    if roles is not None:
        return roles
    return {
        "reader": reader_config,
        "synthesizer": reader_config,
        "planner": coder_config,
        "implementer": coder_config,
        "fixer": coder_config,
        "verifier": None,
    }


def _policies_or_default() -> dict[str, str]:
    return _ACTIVE_POLICIES.get() or resolve_prompt_policies({})


def _semantic_settings_or_disabled() -> SemanticSettings:
    return _ACTIVE_SEMANTIC.get() or SemanticSettings(False)


if __name__ == "__main__":
    raise SystemExit(main())
