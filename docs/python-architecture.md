# AutoDev Python architecture

AutoDev's Python implementation is organized around one-way responsibility layers rather than feature-sized monoliths.

## Dependency direction

The intended dependency direction is:

```text
CLI / compatibility facades
        ↓
orchestration / flow modules
        ↓
responsibility modules
        ↓
contracts + storage + process / provider integrations
```

Lower layers must not import orchestration facades back upward. Cross-cutting workflow policy is installed lazily at execution boundaries rather than by import-time mutation. Compatibility facades preserve older import and monkeypatch seams while delegating implementation to the responsibility modules.

Production modules are expected to stay below 700 lines. A module approaching that boundary should be split by responsibility rather than renamed to `*_core.py` or moved into a generic utility bucket. `*_core.py` files retained for compatibility are shims, not implementation dumping grounds.

## Module map

### Scheduling and autonomous queueing

- `scheduler.py` — scheduler CLI and tick orchestration.
- `scheduler_types.py` — scheduler state and contracts.
- `scheduler_process.py` — process/Git execution.
- `scheduler_backends.py` — native scheduler backends.
- `scheduler_registration.py` — install/uninstall lifecycle.
- `scheduler_health_*` — health state, probes, notification decisions, lifecycle and CLI.
- `queue_contract.py`, `queue_policy.py` — queue state and repository policy.
- `queue_github.py` — GitHub queue I/O.
- `queue_classification.py`, `queue_workflow.py`, `queue_presentation.py`, `queue_cli.py` — queue state derivation, reconciliation, presentation and commands.
- `queue_selection.py` — deterministic runnable-issue selection; depends on queue responsibility layers, not the `issue_queue` facade.
- `claim_*` — distributed worker identity, Git-ref claim persistence, leases, recovery and CLI.

### Issue-to-PR execution

The issue runner has two layers. `issue_runner_*` contains the legacy issue-runner responsibilities; `issue_run_*` contains the durable/resumable execution path.

- contracts/configuration: `issue_runner_contract.py`, `issue_runner_config.py`
- repository/process/storage: `issue_runner_repository.py`, `issue_runner_commands.py`, `issue_runner_storage.py`
- prompt/reader/artifact work: `issue_runner_prompting.py`, `issue_runner_reader.py`, `issue_runner_artifacts.py`
- implementation/verification/PR work: `issue_runner_implementation.py`, `issue_runner_verification.py`, `issue_runner_pr.py`
- durable session/resume/runtime: `issue_run_session.py`, `issue_run_resume.py`, `issue_run_repository.py`, `issue_run_runtime.py`, `issue_run_models.py`
- checkpoints and semantic/implementation shipment: `issue_run_checkpoints.py`, `issue_run_semantic.py`, `issue_run_implementation.py`, `issue_run_pull_request.py`, `issue_run_entrypoint.py`

`run_real_issue.py` and `run_real_issue_core.py` remain compatibility surfaces only.

### Workflow stages

- `workflow_contract.py` — workflow constants, errors and small shared contract helpers.
- `workflow_storage.py`, `workflow_commands.py`, `workflow_workspace.py` — persistence, subprocess/GitHub commands and workspace scope.
- `workflow_prompts.py`, `workflow_diagnostics.py` — prompt rendering and durable diagnostics.
- `workflow_github.py` — commit, PR and CI operations.
- `workflow_preparation.py`, `workflow_verification.py`, `workflow_dispatch.py` — preparation, verification and stage dispatch.
- `workflow_stages.py` — compatibility/integration facade. Policy hooks are resolved lazily when execution begins.
- `workflow_stages_core.py` — legacy compatibility shim only.

### Semantic verification and repair policy

- `semantic_contract.py`, `semantic_configuration.py`, `semantic_schema.py` — semantic verification contract, configuration and schema parsing.
- `semantic_prompts.py`, `semantic_text.py`, `semantic_evidence.py` — prompts, bounded text and repository evidence.
- `semantic_storage.py`, `semantic_artifacts.py`, `semantic_invocation.py`, `semantic_cli.py` — persistence, artifacts, model invocation and CLI.
- `repair_budget_contract.py`, `repair_budget_metrics.py`, `repair_budget_policy.py` — semantic-repair budget rules and sizing.
- `repair_budget_failure.py`, `repair_budget_storage.py`, `repair_budget_manifest.py`, `repair_budget_resume.py` — failure representation, persistence and resume integration.

Resume-budget semantics live in the repair-budget policy layer; workflow orchestration does not monkeypatch policy behavior.

### Model providers and role runtimes

- `provider_contract.py`, `provider_requests.py` — provider/model contracts and request shaping.
- `provider_command.py`, `provider_http.py`, `provider_headroom.py`, `provider_mock.py` — concrete provider transports.
- `provider_factory.py` — provider configuration and construction.
- `role_runtime.py` — runtime abstraction and runtime selection.
- `opencode_role_runtime.py` — OpenCode implementation of that abstraction.

### OpenCode integration

- `opencode_adapter_contract.py`, `opencode_adapter_assets.py`, `opencode_adapter_models.py` — adapter contract, installed assets and role/model mapping.
- `opencode_adapter_storage.py`, `opencode_adapter_handoff.py`, `opencode_adapter_protocol.py` — durable adapter state, handoffs and protocol checks.
- `opencode_adapter_roles.py`, `opencode_adapter_workflow.py`, `opencode_adapter_cli.py` — role preparation/acceptance, workflow integration and CLI.
- `opencode_resume_*` — resume contract, manifest reconciliation, checkpoints, status and execution.
- `coordination_contract.py`, `coordination_state.py` — runtime-neutral coordinator primitives shared by both coordinator implementations.
- `role_coord_*` — generic role-runtime coordinator.
- `opencode_coord_*` — OpenCode-specific process coordinator.

`role_coordinator.py`, `opencode_coordinator.py` and `opencode_adapter.py` are compatibility facades over these modules.

### Windows verification

- `windows_verification_contract.py`, `windows_verification_config.py` — Windows verification contract and configuration.
- `windows_verification_storage.py`, `windows_verification_process.py`, `windows_verification_actions.py` — persistence, process execution and GitHub Actions access.
- `windows_verification_manifest.py`, `windows_verification_obligations.py`, `windows_verification_failure.py` — proof state, deferred obligations and failure classification.
- `windows_verification_execution.py`, `windows_verification_hooks.py` — execution and OpenCode integration.
- `windows_workflow_hooks.py` — lazily constructs the Windows-aware workflow executor; importing workflow modules does not execute/install the Windows lane.

### Area Reader

- `area_reader_settings.py`, `area_reader_cli.py`, `area_reader_storage.py` — configuration, CLI and persistence.
- `area_reader_repository.py`, `area_reader_routing.py`, `area_reader_context.py` — repository discovery, area routing and context bundles.
- `area_reader_verification.py`, `area_reader_prompts.py`, `area_reader_provider.py` — recommended verification, prompts and provider calls.
- `area_reader_execution.py`, `area_reader_workflow.py` — execution and top-level orchestration.

`area_reader/workflow.py` remains a compatibility shim.

### Evaluation and privacy

- `evaluation_contract.py`, `evaluation_profiles.py`, `evaluation_scoring.py`, `evaluation_execution.py`, `evaluation_reporting.py`, `evaluation_cli.py` — evaluation configuration, execution, scoring and reports.
- `privacy_grant_contract.py`, `privacy_grant_store.py`, `privacy_grant_matching.py`, `privacy_grant_commands.py`, `privacy_grant_hooks.py`, `privacy_grant_cli.py` — persistent privacy grants and consent integration.

## Architectural checks

`tests/test_python_architecture.py` permanently guards the boundaries introduced by issue #180. It checks that:

- production modules remain at or below the 700-line ceiling;
- the top-level local import graph is acyclic;
- legacy `*_core.py` compatibility files remain small;
- representative responsibility modules import independently; and
- temporary issue-180 migration workflows/scripts or chunk artifacts cannot accidentally ship.

Behavioral tests remain the authority for CLI, workflow, resume, privacy, provider, queue, scheduler and platform semantics. The architecture checks supplement those tests; they do not replace them.
