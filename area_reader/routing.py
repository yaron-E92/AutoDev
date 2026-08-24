from __future__ import annotations

from area_reader.settings import (
    AREA_HINTS,
    DEFAULT_AUTO_AREAS,
    SUPPORTED_AREAS,
)

def route_areas(issue, areas_arg):
    requested = areas_arg.strip()
    if requested == "all":
        return list(SUPPORTED_AREAS), {
            "mode": "all",
            "matched_keywords": {},
            "defaulted": False,
        }

    if requested != "auto":
        areas = []
        for raw_area in requested.split(","):
            area = raw_area.strip()
            if not area:
                continue
            if area not in SUPPORTED_AREAS:
                raise ValueError(f"Unsupported area: {area}")
            if area not in areas:
                areas.append(area)
        if not areas:
            raise ValueError("--areas explicit list did not include any supported areas")
        return areas, {
            "mode": "explicit",
            "matched_keywords": {},
            "defaulted": False,
        }

    issue_lower = issue.lower()
    matched_keywords = {}
    areas = []
    for area in SUPPORTED_AREAS:
        keywords = [keyword for keyword in AREA_HINTS[area]["keywords"] if keyword in issue_lower]
        if keywords:
            areas.append(area)
            matched_keywords[area] = keywords

    defaulted = not areas
    if defaulted:
        areas = list(DEFAULT_AUTO_AREAS)

    return areas, {
        "mode": "auto",
        "matched_keywords": matched_keywords,
        "defaulted": defaulted,
    }

def area_file_map(files, area):
    selected = [
        item
        for item in files
        if area in item["areas"] or item["priority"]
    ]
    selected.sort(key=lambda item: (area not in item["areas"], not item["priority"], item["path"]))
    return selected

def format_area_file_map(area, files):
    lines = [f"Area file map: {area}"]
    if not files:
        lines.append("- No candidate files matched this area.")
    for item in files:
        flags = []
        if area in item["areas"]:
            flags.append("area-match")
        if item["priority"]:
            flags.append("priority")
        suffix = " [" + "; ".join(flags) + "]" if flags else ""
        lines.append(f"- {item['path']} ({item['bytes']} bytes){suffix}")
    return "\n".join(lines) + "\n"
