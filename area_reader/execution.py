from __future__ import annotations

import sys

from area_reader.context import (
    build_area_bundle,
)
from area_reader.prompts import (
    build_area_reader_prompt,
)
from area_reader.provider import (
    build_metrics,
    call_provider,
    extract_message,
)
from area_reader.routing import (
    area_file_map,
)
from area_reader.storage import (
    write_json,
    write_text,
)

def run_area_reader(args, repo, out, area, repo_map, files, *, call_provider_fn=call_provider):
    area_dir = out / f"area-{area}"
    selected_files = area_file_map(files, area)
    bundle, metadata, file_map_text = build_area_bundle(
        repo,
        area,
        args.issue,
        repo_map,
        selected_files,
        args.max_chars_per_area,
    )
    reader_prompt = build_area_reader_prompt(args.issue, area, bundle, metadata)

    write_text(area_dir / "file-map.txt", file_map_text)
    write_text(area_dir / "input-bundle.txt", bundle)
    write_text(area_dir / "reader-prompt.txt", reader_prompt)

    raw, wall_seconds = call_provider_fn(args, "reader", reader_prompt, args.reader_num_predict)
    brief, thinking = extract_message(raw)
    metrics = build_metrics(raw, wall_seconds, brief)

    write_json(area_dir / "reader-raw.json", raw)
    write_text(area_dir / "reader-brief.md", brief)
    write_text(area_dir / "reader-thinking.md", thinking)
    write_json(area_dir / "metrics.json", metrics)

    reader_error = ""
    if metrics["done_reason"] == "length" and len(brief) < 500:
        reader_error = (
            "Area reader response ended because of length with very little output; "
            "the prompt likely filled the context window. Lower --max-chars-per-area "
            "or use a reader model with a larger context window."
        )
        write_text(area_dir / "reader-error.txt", reader_error + "\n")
        print(f"{area}: {reader_error}", file=sys.stderr)

    return {
        "area": area,
        "brief": brief,
        "metrics": metrics,
        "metadata": metadata,
        "reader_error": reader_error,
    }
