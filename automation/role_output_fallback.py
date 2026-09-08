from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from automation import role_output_contract
from automation.model_output_sanitizer import sanitize_model_output


MAX_FALLBACK_ROLE_CHARS = 30_000
SUPPORTED_ROLES = {"reader", "synthesizer", "planner", "verifier"}


class RoleOutputFallbackError(ValueError):
    """A bounded fallback role result cannot be parsed/materialized safely."""


@dataclass(frozen=True)
class FallbackCandidate:
    role: str
    canonical_text: str
    parsed_value: object


def supported(contract: role_output_contract.RoleOutputContract | None) -> bool:
    return contract is not None and contract.role in SUPPORTED_ROLES


def parse_candidate(
    repo: Path,
    contract: role_output_contract.RoleOutputContract,
    text: object,
) -> FallbackCandidate:
    """Parse bounded fallback text without granting it workflow authority."""

    if not supported(contract):
        raise RoleOutputFallbackError(
            f"fallback-text materialization is not supported for role {contract.role}"
        )
    cleaned = sanitize_model_output(text).strip()
    if not cleaned:
        raise RoleOutputFallbackError(f"fallback-text {contract.role} result is empty")
    if len(cleaned) > MAX_FALLBACK_ROLE_CHARS:
        raise RoleOutputFallbackError(
            f"fallback-text {contract.role} result exceeds the "
            f"{MAX_FALLBACK_ROLE_CHARS}-character AutoDev role-result limit"
        )

    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"

    if contract.role in {"reader", "synthesizer"}:
        return FallbackCandidate(
            role=contract.role,
            canonical_text=cleaned + "\n",
            parsed_value={"handoff_markdown": cleaned},
        )

    if contract.role == "planner":
        from automation.planner_output import PlannerOutputError, sanitize_planner_output

        try:
            plan = sanitize_planner_output(cleaned)
        except PlannerOutputError as exc:
            raise RoleOutputFallbackError(str(exc)) from exc
        return FallbackCandidate(
            role=contract.role,
            canonical_text=plan,
            parsed_value=plan,
        )

    if contract.role == "verifier":
        from automation.semantic_contract import SemanticVerifierError
        from automation.semantic_prompts import extract_acceptance_criteria
        from automation.semantic_schema import parse_semantic_output

        issue_path = current / "issue.md"
        issue_text = issue_path.read_text(encoding="utf-8") if issue_path.is_file() else ""
        try:
            value = parse_semantic_output(
                cleaned,
                expected_criteria=extract_acceptance_criteria(issue_text) or None,
                current=current,
                role="verifier",
            )
        except SemanticVerifierError as exc:
            raise RoleOutputFallbackError(str(exc)) from exc
        return FallbackCandidate(
            role=contract.role,
            canonical_text=json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            parsed_value=value,
        )

    raise RoleOutputFallbackError(
        f"fallback-text materialization is not implemented for role {contract.role}"
    )


def parse_existing(
    repo: Path,
    contract: role_output_contract.RoleOutputContract,
) -> FallbackCandidate | None:
    repo = repo.expanduser().resolve()
    target = repo / ".autodev-run" / "current" / contract.output_artifact
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoleOutputFallbackError(
            f"cannot read existing fallback artifact for {contract.role}: {exc}"
        ) from exc
    return parse_candidate(repo, contract, text)


def materialize_candidate(
    repo: Path,
    contract: role_output_contract.RoleOutputContract,
    candidate: FallbackCandidate,
) -> Path:
    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    current.mkdir(parents=True, exist_ok=True)
    target = current / contract.output_artifact

    try:
        # Fallback text carries only the established textual protocol result. It
        # must not manufacture native Structured Output sidecars (especially empty
        # UX evidence) that would later be interpreted as malformed structured
        # metadata. Reader/Synthesizer/Planner therefore materialize only their
        # canonical durable text artifact here; ordinary acceptance remains the
        # authority boundary. Verifier uses the structured materializer because its
        # parsed semantic JSON is the established durable verifier artifact and it
        # does not create a UX sidecar.
        if contract.role in {"reader", "synthesizer", "planner"}:
            target.write_text(candidate.canonical_text, encoding="utf-8")
            return target
        materialized = role_output_contract.materialize_structured_output(
            repo,
            contract,
            candidate.parsed_value,
        )
    except (OSError, role_output_contract.RoleOutputContractError) as exc:
        raise RoleOutputFallbackError(
            f"fallback-text {contract.role} result could not be materialized: {exc}"
        ) from exc

    if materialized is None or materialized.resolve() != target.resolve():
        raise RoleOutputFallbackError(
            f"fallback-text {contract.role} result did not produce {contract.output_artifact}"
        )
    return materialized
