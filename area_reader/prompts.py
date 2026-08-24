from __future__ import annotations

import json


def build_area_reader_prompt(issue, area, bundle, metadata):
    return f"""You are the area reader model for area: {area}.

You are not the coder. Do not edit files. Do not design a patch. Read only the provided repository context and produce a factual handoff brief for a later synthesis reader and coder.

Your brief must:
- Include exact file paths for every repository fact you mention.
- Distinguish visible facts from inference.
- Stay factual; do not invent shell commands or implementation steps.
- Name local verification needs for this area conceptually, not as freehand command lines.
- Identify uncertainties and missing files.
- If this area is placeholder-only or not actually present, say so clearly.

Original issue:
{issue}

Area bundle metadata:
{json.dumps(metadata, indent=2, sort_keys=True)}

Area input bundle:
{bundle}
"""

def build_synthesis_prompt(issue, areas, area_results, detected_facts, command_groups):
    brief_blocks = []
    for result in area_results:
        brief_blocks.append(
            f"""## Area: {result['area']}

Reader metadata:
{json.dumps(result['metadata'], indent=2, sort_keys=True)}

Reader brief:
{result['brief']}
"""
        )

    return f"""You are the synthesis reader model in an area-based local LLM benchmark.

You are not the coder. Combine the area reader briefs into one compact coder handoff.

Your handoff must:
- Preserve area-specific details.
- List routed areas.
- List repo/application surfaces.
- List relevant files by area.
- Use the deterministic facts below as the source of truth for repository structure.
- Refer to named verification command groups instead of inventing shell commands.
- Include cross-area risks.
- Include constraints and uncertainties.
- Do not invent files or commands.

Original issue:
{issue}

Routed areas:
{", ".join(areas)}

Deterministic repository facts:
{json.dumps(detected_facts, indent=2, sort_keys=True)}

Available verification command groups:
{json.dumps(command_groups, indent=2, sort_keys=True)}

Area reader briefs:
{"".join(brief_blocks)}
"""

def build_coder_prompt(issue, synthesis_brief, detected_facts, recommended_groups, command_groups):
    return f"""You are the coder model in an area-based local LLM benchmark.

Consume the original issue and the synthesized handoff. Produce a minimal issue-scoped implementation or verification plan.

Rules:
- For verification-only issues, list "files to inspect," not "files likely needing changes."
- Name exact files only when supported by the handoff.
- Select verification by named command group from the deterministic command group list.
- Do not write freehand shell commands.
- Do not use placeholder commands.
- Do not invent test projects or paths.
- Do not refactor unrelated code.
- Be strict about uncertainty.

Original issue:
{issue}

Synthesized handoff:
{synthesis_brief}

Deterministic repository facts:
{json.dumps(detected_facts, indent=2, sort_keys=True)}

Recommended verification command groups:
{json.dumps(recommended_groups, indent=2, sort_keys=True)}

All available verification command groups:
{json.dumps(command_groups, indent=2, sort_keys=True)}
"""
