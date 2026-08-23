from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from automation import run_manifest

from automation.evaluation_contract import (
    DEFAULT_CASES,
    DEFAULT_PROFILES,
    DEFAULT_RESULTS_ROOT,
    EvalError,
)
from automation.evaluation_execution import (
    load_replay,
    run_live_case,
)
from automation.evaluation_profiles import (
    load_cases,
    load_profiles,
    selected_cases,
)
from automation.evaluation_reporting import (
    aggregate,
    write_results,
)
from automation.evaluation_scoring import (
    estimate_model_calls,
)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay-first AutoDev workflow evaluation harness.")
    parser.add_argument("--cases-file", default=str(DEFAULT_CASES))
    parser.add_argument("--profiles-file", default=str(DEFAULT_PROFILES))
    parser.add_argument("--profile", action="append", dest="profiles", required=True)
    parser.add_argument("--case", action="append", dest="cases", default=[])
    parser.add_argument("--tag", action="append", dest="tags", default=[])
    parser.add_argument("--out")
    parser.add_argument("--live", action="store_true", help="Permit normal AutoDev runner/model execution.")
    parser.add_argument("--apply", action="store_true", help="Permit target working-tree edits for live runs.")
    parser.add_argument("--sandbox-pr", action="store_true", help="Permit PR mode for an explicitly sandboxed live target.")
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument("--max-model-calls", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--max-reported-cost", type=float)
    return parser

def validate_budgets(
    args: argparse.Namespace,
    cases: list[dict[str, object]],
    profiles: list[dict[str, object]],
) -> None:
    if len(cases) > args.max_cases:
        raise EvalError(f"selected {len(cases)} cases exceeds --max-cases {args.max_cases}")
    planned_calls = sum(estimate_model_calls(profile) for profile in profiles) * len(cases)
    if args.live and planned_calls > args.max_model_calls:
        raise EvalError(
            f"conservative planned model-call bound {planned_calls} exceeds --max-model-calls {args.max_model_calls}"
        )
    if args.timeout_seconds <= 0:
        raise EvalError("--timeout-seconds must be greater than zero")
    if args.max_reported_cost is not None and args.max_reported_cost < 0:
        raise EvalError("--max-reported-cost must be zero or greater")
    if args.sandbox_pr and not args.live:
        raise EvalError("--sandbox-pr requires --live")
    if args.live and not args.apply:
        raise EvalError("--live requires --apply because normal implement mode verifies applied working-tree changes")

def print_live_plan(cases: list[dict[str, object]], profiles: list[dict[str, object]], *, sandbox_pr: bool) -> None:
    print("Planned live AutoDev evaluation:")
    for profile in profiles:
        print(f"- profile {profile['name']}: {profile['provider_config']}")
        roles = profile["provider_summary"].get("roles", {})
        if isinstance(roles, dict):
            for role, value in roles.items():
                if not isinstance(value, dict):
                    continue
                print(
                    f"    {role}: {value.get('transport', '')} {value.get('model', '')} "
                    f"endpoint={value.get('endpoint', '') or 'command/local'} "
                    f"fallbacks={value.get('fallbacks', []) or 'none'}"
                )
    print("Cases: " + ", ".join(str(case["id"]) for case in cases))
    print("PR creation: explicit sandbox enabled" if sandbox_pr else "PR creation: disabled")

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases_path = Path(args.cases_file).expanduser().resolve()
        profiles_path = Path(args.profiles_file).expanduser().resolve()
        all_cases = load_cases(cases_path)
        all_profiles = load_profiles(profiles_path)
        missing_cases = sorted(set(args.cases) - set(all_cases))
        missing_profiles = sorted(set(args.profiles) - set(all_profiles))
        if missing_cases:
            raise EvalError("unknown evaluation case(s): " + ", ".join(missing_cases))
        if missing_profiles:
            raise EvalError("unknown evaluation profile(s): " + ", ".join(missing_profiles))
        cases = selected_cases(all_cases, args.cases, args.tags)
        profiles = [all_profiles[name] for name in args.profiles]
        if len(profiles) < 2:
            raise EvalError("compare at least two --profile values")
        if not cases:
            raise EvalError("no evaluation cases selected")
        validate_budgets(args, cases, profiles)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_root = Path(args.out).expanduser().resolve() if args.out else DEFAULT_RESULTS_ROOT / timestamp
        results: list[dict[str, object]] = []
        if args.live:
            print_live_plan(cases, profiles, sandbox_pr=args.sandbox_pr)
        cumulative_reported_cost = 0.0
        for case in cases:
            for profile in profiles:
                if args.max_reported_cost is not None and cumulative_reported_cost >= args.max_reported_cost:
                    raise EvalError(
                        f"reported-cost ceiling {args.max_reported_cost} reached before the next live run"
                    )
                result = (
                    run_live_case(
                        case,
                        profile,
                        output_dir=output_root / str(case["id"]) / str(profile["name"]),
                        timeout_seconds=args.timeout_seconds,
                        sandbox_pr=args.sandbox_pr,
                    )
                    if args.live
                    else load_replay(case, profile, cases_path=cases_path)
                )
                results.append(result)
                efficiency = result.get("efficiency", {})
                cost = efficiency.get("reported_cost") if isinstance(efficiency, dict) else None
                if isinstance(cost, (int, float)):
                    cumulative_reported_cost += float(cost)
        aggregate_value = aggregate(results, all_cases)
        write_results(output_root, results, aggregate_value)
        print(output_root)
        return 0
    except (EvalError, run_manifest.ManifestError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
