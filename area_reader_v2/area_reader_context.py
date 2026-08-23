from __future__ import annotations

from area_reader_v2.area_reader_routing import (
    format_area_file_map,
)
from area_reader_v2.area_reader_settings import (
    AREA_HINTS,
)

def read_file_for_bundle(repo, relative_path):
    path = repo / relative_path
    return path.read_text(encoding="utf-8", errors="replace")

def build_area_bundle(repo, area, issue, repo_map, files, max_chars):
    if max_chars <= 0:
        raise ValueError("--max-chars-per-area must be greater than zero")

    file_map_text = format_area_file_map(area, files)
    header = f"""Issue:
{issue}

Routed area: {area}

Area hint keywords:
{", ".join(AREA_HINTS[area]["keywords"])}

{file_map_text}
Repository map:
{repo_map}
File excerpts:
"""
    parts = [header]
    remaining = max_chars - len(header)
    included_files = []
    skipped_unreadable_files = []
    truncated = remaining < 0

    if remaining > 0:
        for item in files:
            relative_path = item["path"]
            try:
                content = read_file_for_bundle(repo, relative_path)
            except OSError as exc:
                skipped_unreadable_files.append({"path": relative_path, "reason": str(exc)})
                continue

            entry = f"\n\n===== FILE: {relative_path} =====\n{content.rstrip()}\n"
            if len(entry) > remaining:
                if remaining > len(f"\n\n===== FILE: {relative_path} =====\n"):
                    parts.append(entry[:remaining])
                    included_files.append(relative_path)
                truncated = True
                break

            parts.append(entry)
            included_files.append(relative_path)
            remaining -= len(entry)
            if remaining <= 0:
                truncated = True
                break

    bundle = "".join(parts)
    metadata = {
        "area": area,
        "max_chars": max_chars,
        "bundle_chars": len(bundle),
        "candidate_file_count": len(files),
        "included_file_count": len(included_files),
        "included_files": included_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "truncated": truncated,
        "placeholder_or_absent": not any(area in item["areas"] for item in files),
    }
    return bundle, metadata, file_map_text
