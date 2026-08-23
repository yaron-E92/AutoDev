from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from automation import run_manifest

from automation.evaluation_contract import (
    EvalError,
    REPO_ROOT,
    SCHEMA_VERSION,
    UNKNOWN,
)
from automation.evaluation_profiles import (
    read_json,
)
from automation.evaluation_scoring import (
    score_record,
    unavailable_result,
)

def load_replay(case: dict[str, object], profile: dict[str, object], *, cases_path: Path) -> dict[str, object]:
    source = case["source"]
    assert isinstance(source, dict)
    replay_file = str(source.get("replay_file", "")).strip()
    if not replay_file:
        return unavailable_result(case, profile, "case has no replay file")
    path = (cases_path.parent / replay_file).resolve()
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise EvalError(f"invalid replay fixture: {path}")
    profiles = value.get("profiles", {})
    record = profiles.get(profile["name"]) if isinstance(profiles, dict) else None
    if not isinstance(record, dict):
        return unavailable_result(case, profile, "no replay recorded for this profile")
    manifest = record.get("run_manifest", {})
    if not isinstance(manifest, dict):
        raise EvalError(f"replay {path} for {profile['name']} has no run_manifest")
    run_manifest.validate_manifest(manifest)
    diagnostics = record.get("run_diagnostics", {})
    return score_record(
        case,
        profile,
        manifest=manifest,
        semantic=record.get("semantic_result", {}),
        diff_text=str(record.get("diff", "")),
        diagnostics=diagnostics if isinstance(diagnostics, dict) else {},
        replay_meta=record,
        mode="replay",
    )

def live_plan(case: dict[str, object], profile: dict[str, object]) -> dict[str, object]:
    source = case.get("source", {})
    live = source.get("live", {}) if isinstance(source, dict) else {}
    if not isinstance(live, dict) or not live:
        raise EvalError(f"case {case['id']} has no live runner target")
    repo = str(live.get("repo", "")).strip()
    repo_env = str(live.get("repo_env", "")).strip()
    if not repo and repo_env:
        repo = os.environ.get(repo_env, "").strip()
    if not repo:
        raise EvalError(f"case {case['id']} requires local repo path or {repo_env or 'repo_env'}")
    github_repo = str(live.get("github_repo", "")).strip()
    issue = int(live.get("issue", 0) or 0)
    if not github_repo or issue <= 0:
        raise EvalError(f"case {case['id']} live target requires github_repo and issue")
    return {
        "repo": str(Path(repo).expanduser().resolve()),
        "github_repo": github_repo,
        "issue": issue,
        "provider_config": str(profile["provider_path"]),
        "roles": profile["provider_summary"]["roles"],
    }

def run_live_case(
    case: dict[str, object],
    profile: dict[str, object],
    *,
    output_dir: Path,
    timeout_seconds: int,
    sandbox_pr: bool,
) -> dict[str, object]:
    plan = live_plan(case, profile)
    for role, value in plan["roles"].items():
        if isinstance(value, dict) and "REPLACE_WITH" in str(value.get("model", "")):
            raise EvalError(f"profile {profile['name']} role {role} still contains a placeholder model")
    run_dir = output_dir / "run"
    command = [
        sys.executable,
        "-m",
        "automation.run_real_issue",
        "--repo",
        str(plan["repo"]),
        "--github-repo",
        str(plan["github_repo"]),
        "--issue",
        str(plan["issue"]),
        "--mode",
        "pr" if sandbox_pr else "implement",
        "--provider-config",
        str(plan["provider_config"]),
        "--out",
        str(run_dir),
        "--max-fix-attempts",
        str((profile.get("evaluation", {}) or {}).get("max_fix_attempts", 2)),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    manifest_path = run_dir / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return unavailable_result(case, profile, "runner did not produce run-manifest.json") | {
            "mode": "live",
            "status": "provider-failed" if completed.returncode else "failed",
        }
    manifest = run_manifest.load_manifest(manifest_path)
    result = score_record(
        case,
        profile,
        manifest=manifest,
        semantic=read_optional_json(run_dir / "verification" / "final-verdict.json"),
        diff_text=git_diff(Path(str(plan["repo"]))),
        diagnostics=read_optional_json(run_dir / "run-diagnostics.json"),
        replay_meta={
            "autodev_commit": git_rev_parse(REPO_ROOT),
            "case_version": case.get("version", 1),
            "profile_fingerprint": profile["fingerprint"],
            "patch_applies_cleanly": completed.returncode == 0,
            "os": platform.platform(),
        },
        mode="live",
    )
    if completed.returncode != 0 and result["status"] == "completed":
        result["status"] = "failed"
    return result

def read_optional_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def git_diff(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else ""

def git_rev_parse(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else UNKNOWN
