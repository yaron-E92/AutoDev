# AutoDev Python architecture

AutoDev's Python implementation is organized around one-way responsibility layers and a small set of explicit executable entrypoints.

## Dependency direction

The intended dependency direction is:

```text
CLI / executable entrypoints
        ↓
orchestration / flow modules
        ↓
responsibility modules
        ↓
contracts + storage + process / provider integrations
```

Lower layers must not import orchestration surfaces back upward. Cross-cutting workflow policy is installed lazily at execution boundaries rather than by import-time mutation. Production modules are expected to stay at or below 700 lines; a module approaching that boundary should be split by responsibility rather than renamed into a generic `core` or `utils` bucket.

The retired standalone issue-runner and evaluation layers are not alternate production paths. Supported execution goes through the canonical AutoDev/OpenCode entrypoints or the maintained platform wrappers.

## Module map

### Canonical CLI and coordinator

- `autodev_cli.py` — canonical user CLI and command routing.
- `opencode_entrypoint.py`, `opencode_runtime.py` — OpenCode-facing execution routing.
- `role_coordinator_cli.py` — coordinator CLI contract.
- `role_coordinator_contract.py`, `role_coordinator_state.py` — coordinator state/contract.
- `role_coordinator_runtime.py`, `role_coordinator_stages.py`, `role_coordinator_flow.py` — runtime execution, stage transitions and coordinator flow.
- `coordination_contract.py`, `coordination_state.py` — shared runtime-neutral coordinator primitives.
- `role_runtime.py`, `opencode_role_runtime.py`, `role_resume.py` — runtime abstraction, optional scheduler-worker provisioning/preflight hooks, and durable role resume.

### Scheduling and autonomous queueing

- `scheduler.py` — scheduler command/orchestration surface.
- `scheduler_types.py` — scheduler state and contracts.
- `scheduler_process.py` — process/Git execution.
- `scheduler_backends.py` — native scheduler backends.
- `scheduler_registration.py` — install/uninstall lifecycle.
- `scheduler_health_*` — health state, probes, notification decisions, lifecycle and CLI.
- `queue_contract.py`, `queue_policy.py` — queue state and repository policy.
- `queue_github.py` — GitHub queue I/O.
- `queue_classification.py`, `queue_workflow.py`, `queue_presentation.py`, `queue_cli.py` — queue derivation, reconciliation, presentation and commands.
- `queue_selection.py` — deterministic runnable-issue selection without a facade import cycle.
- `claim_*` — distributed worker identity, Git-ref claim persistence, leases, recovery and CLI.

### Workflow stages

- `workflow_contract.py` — workflow constants, errors and shared contracts.
- `workflow_storage.py`, `workflow_commands.py`, `workflow_workspace.py` — persistence, subprocess/GitHub commands and workspace scope.
- `workflow_prompts.py`, `workflow_diagnostics.py` — prompt rendering and durable diagnostics.
- `workflow_github.py` — commit, PR and CI operations.
- `workflow_preparation.py`, `workflow_verification.py`, `workflow_dispatch.py` — preparation, verification and stage dispatch.
- `workflow_stages.py` — maintained integration surface; policy hooks are resolved lazily when execution begins.
- `windows_workflow_hooks.py` — lazily constructs Windows-aware workflow execution without import-time installation.

### Semantic verification and repair policy

- `semantic_contract.py`, `semantic_configuration.py`, `semantic_schema.py` — verifier contract, settings and schema parsing.
- `semantic_prompts.py`, `semantic_text.py`, `semantic_evidence.py` — prompts, bounded text and repository evidence.
- `semantic_storage.py`, `semantic_artifacts.py`, `semantic_invocation.py` — persistence, artifacts and model invocation.
- `semantic_cli.py` — executable semantic-verification CLI boundary used by platform wrappers.
- `repair_budget_contract.py`, `repair_budget_metrics.py`, `repair_budget_policy.py` — semantic-repair budget rules and sizing.
- `repair_budget_failure.py`, `repair_budget_storage.py`, `repair_budget_manifest.py`, `repair_budget_resume.py` — failure representation, persistence and resume integration.

Resume-budget semantics live in the repair-budget policy layer; workflow orchestration does not monkeypatch policy behavior.

### Model providers and privacy

- `provider_contract.py`, `provider_requests.py` — provider/model contracts and request shaping.
- `provider_command.py`, `provider_http.py`, `provider_headroom.py`, `provider_mock.py` — concrete transports.
- `provider_factory.py` — provider configuration and construction.
- `privacy.py` — provider-neutral privacy policy/evidence primitives and direct-provider policy evaluation.
- `privacy_authorization.py` — runtime-neutral final policy/grant authorization and persistent-grant audit.
- `privacy_grant_contract.py`, `privacy_grant_store.py`, `privacy_grant_matching.py` — durable grant representation and matching.
- `privacy_grant_commands.py`, `privacy_grant_cli.py` — user-facing persistent consent commands.
- `privacy_grant_hooks.py`, `privacy_consent.py` — interactive run-scoped consent integration; persistent grant enforcement does not depend on these hooks.

### OpenCode integration and resume

- `opencode_adapter_contract.py`, `opencode_adapter_assets.py`, `opencode_adapter_models.py` — adapter contract, repository/worker-owned asset provisioning, resolved OpenCode configuration and role/model mapping.
- `opencode_privacy_adapter.py` — OpenCode-specific route/privacy evidence and runtime configuration overlays; it does not own grants.
- `opencode_adapter_storage.py`, `opencode_adapter_handoff.py`, `opencode_adapter_protocol.py` — durable state, handoffs and protocol checks.
- `opencode_adapter_roles.py`, `opencode_adapter_workflow.py`, `opencode_adapter_cli.py` — role preparation/acceptance, workflow integration and CLI.
- `opencode_resume_contract.py`, `opencode_resume_manifest.py`, `opencode_resume_checkpoint.py`, `opencode_resume_status.py`, `opencode_resume_execution.py` — durable resume ownership.

### Windows verification

- `windows_verification_contract.py`, `windows_verification_config.py` — verification contract and configuration.
- `windows_verification_storage.py`, `windows_verification_process.py`, `windows_verification_actions.py` — persistence, process execution and GitHub Actions access.
- `windows_verification_manifest.py`, `windows_verification_obligations.py`, `windows_verification_failure.py` — proof state, deferred obligations and failure classification.
- `windows_verification_execution.py`, `windows_verification_hooks.py` — execution and OpenCode integration.

There is no aggregate Windows-verification facade; callers depend on the owning responsibility module.

### Area Reader

- `area_reader/settings.py`, `area_reader/cli.py`, `area_reader/storage.py` — configuration, CLI parsing and persistence.
- `area_reader/repository.py`, `area_reader/routing.py`, `area_reader/context.py` — repository discovery, routing and context bundles.
- `area_reader/verification.py`, `area_reader/prompts.py`, `area_reader/provider.py` — recommended verification, prompts and provider calls.
- `area_reader/execution.py`, `area_reader/pipeline.py` — execution and pipeline orchestration.
- `area_reader/workflow.py` — supported standalone Area Reader entrypoint with provider/resume integration.

OpenCode context handoff imports the low-level Area Reader responsibility modules directly rather than routing through the standalone workflow.

## Architectural checks

`tests/test_python_architecture.py` permanently guards the boundaries introduced by issue #180. It checks that:

- production modules remain at or below the 700-line ceiling;
- the top-level local import graph is acyclic;
- representative responsibility modules import independently;
- removed compatibility/legacy module paths do not return;
- maintained docs do not point users at retired Python entrypoints; and
- temporary issue-180 migration workflows/scripts or chunk artifacts cannot accidentally ship.

Behavioral tests remain the authority for CLI, workflow, resume, privacy, provider, queue, scheduler and platform semantics. The architecture checks supplement those tests; they do not replace them.
