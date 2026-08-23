from __future__ import annotations

import sys

from area_reader_v2.area_reader_cli import (
    expand_user_path,
    parse_args,
)
from area_reader_v2.area_reader_execution import (
    run_area_reader,
)
from area_reader_v2.area_reader_prompts import (
    build_coder_prompt,
    build_synthesis_prompt,
)
from area_reader_v2.area_reader_provider import (
    build_metrics,
    call_provider,
    extract_message,
)
from area_reader_v2.area_reader_repository import (
    build_repo_map,
    collect_repo_files,
    detect_repo_facts,
)
from area_reader_v2.area_reader_routing import (
    route_areas,
)
from area_reader_v2.area_reader_settings import (
    SUPPORTED_AREAS,
)
from area_reader_v2.area_reader_storage import (
    write_executable_text,
    write_json,
    write_text,
)
from area_reader_v2.area_reader_verification import (
    apply_recommended_command_groups,
    build_verification_command_groups,
    recommended_command_groups,
    render_verification_script,
)

def main(argv=None):
    args = parse_args(argv)
    if args.reader_model is None:
        args.reader_model = args.reader
    if args.coder_model is None:
        args.coder_model = args.coder
    if args.synthesizer is None:
        args.synthesizer = args.reader_model

    repo = expand_user_path(args.repo)
    out = expand_user_path(args.out)

    if not repo.is_dir():
        print(f"--repo is not a directory: {repo}", file=sys.stderr)
        return 2

    try:
        areas, routing_detail = route_areas(args.issue, args.areas)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.max_chars_per_area <= 0:
        print("--max-chars-per-area must be greater than zero", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    write_text(out / "issue.txt", args.issue + "\n")

    files, skipped_large_files, skipped_unreadable_files = collect_repo_files(repo)
    repo_map = build_repo_map(repo, files, skipped_large_files, skipped_unreadable_files)
    write_text(out / "repo-map.txt", repo_map)

    routing = {
        "requested": args.areas,
        "areas": areas,
        "supported_areas": list(SUPPORTED_AREAS),
        "detail": routing_detail,
    }
    write_json(out / "routing.json", routing)

    detected_facts = detect_repo_facts(repo, files, areas, routing)
    command_groups = build_verification_command_groups(detected_facts, areas)
    recommended_groups = recommended_command_groups(
        command_groups,
        issue_text=args.issue,
        changed_paths=[],
    )
    apply_recommended_command_groups(command_groups, recommended_groups)
    write_json(out / "detected-facts.json", detected_facts)
    write_json(out / "verification-command-groups.json", command_groups)
    write_json(out / "recommended-command-groups.json", recommended_groups)
    write_executable_text(
        out / "verification-commands.sh",
        render_verification_script(repo, command_groups),
    )

    area_results = []
    for area in areas:
        try:
            area_results.append(run_area_reader(args, repo, out, area, repo_map, files))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    synthesis_prompt = build_synthesis_prompt(
        args.issue,
        areas,
        area_results,
        detected_facts,
        command_groups,
    )
    write_text(out / "synthesis-prompt.txt", synthesis_prompt)
    try:
        synthesis_raw, synthesis_wall_seconds = call_provider(
            args,
            "reader",
            synthesis_prompt,
            args.synth_num_predict,
            model_override=args.synthesizer,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    synthesis_brief, synthesis_thinking = extract_message(synthesis_raw)
    synthesis_metrics = build_metrics(synthesis_raw, synthesis_wall_seconds, synthesis_brief)
    write_json(out / "synthesis-raw.json", synthesis_raw)
    write_text(out / "synthesis-brief.md", synthesis_brief)
    write_text(out / "synthesis-thinking.md", synthesis_thinking)
    write_json(out / "synthesis-metrics.json", synthesis_metrics)

    coder_prompt = build_coder_prompt(
        args.issue,
        synthesis_brief,
        detected_facts,
        recommended_groups,
        command_groups,
    )
    write_text(out / "coder-prompt.txt", coder_prompt)
    try:
        coder_raw, coder_wall_seconds = call_provider(
            args,
            "coder",
            coder_prompt,
            args.coder_num_predict,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    coder_plan, coder_thinking = extract_message(coder_raw)
    coder_metrics = build_metrics(coder_raw, coder_wall_seconds, coder_plan)
    write_json(out / "coder-raw.json", coder_raw)
    write_text(out / "coder-plan.md", coder_plan)
    write_text(out / "coder-thinking.md", coder_thinking)
    write_json(out / "coder-metrics.json", coder_metrics)

    area_metrics = {result["area"]: result["metrics"] for result in area_results}
    summary = {
        "repo": str(repo),
        "out": str(out),
        "reader": args.reader_model,
        "synthesizer": args.synthesizer,
        "coder": args.coder_model,
        "reader_provider": args.reader_provider,
        "coder_provider": args.coder_provider,
        "max_chars_per_area": args.max_chars_per_area,
        "areas": areas,
        "routing": routing,
        "repo_file_count": len(files),
        "skipped_large_files": skipped_large_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "detected_facts": detected_facts,
        "recommended_command_groups": recommended_groups,
        "verification_command_groups": command_groups,
        "area_metadata": {result["area"]: result["metadata"] for result in area_results},
        "area_metrics": area_metrics,
        "area_errors": {
            result["area"]: result["reader_error"]
            for result in area_results
            if result["reader_error"]
        },
        "synthesis_metrics": synthesis_metrics,
        "coder_metrics": coder_metrics,
        "outputs": [
            "issue.txt",
            "repo-map.txt",
            "routing.json",
            "detected-facts.json",
            "recommended-command-groups.json",
            "verification-command-groups.json",
            "verification-commands.sh",
            *[
                f"area-{area}/{name}"
                for area in areas
                for name in (
                    "file-map.txt",
                    "input-bundle.txt",
                    "reader-prompt.txt",
                    "reader-brief.md",
                    "reader-raw.json",
                    "reader-thinking.md",
                    "metrics.json",
                )
            ],
            "synthesis-prompt.txt",
            "synthesis-brief.md",
            "synthesis-raw.json",
            "synthesis-thinking.md",
            "synthesis-metrics.json",
            "coder-prompt.txt",
            "coder-plan.md",
            "coder-raw.json",
            "coder-thinking.md",
            "coder-metrics.json",
            "summary.json",
        ],
    }
    write_json(out / "summary.json", summary)
    return 0
