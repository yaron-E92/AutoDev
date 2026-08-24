from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

path = ROOT / "automation" / "repo_setup.py"
text = path.read_text(encoding="utf-8")
text = text.replace("                    python_command=args.python,\n", "")
text = text.replace("                python_command=args.python,\n", "")
text = text.replace("            python_command=args.python,\n", "")
if "python_command=args.python" in text:
    raise SystemExit("stale repo_setup python_command forwarding remains")
path.write_text(text, encoding="utf-8")

readme = ROOT / "README.md"
text = readme.read_text(encoding="utf-8")
anchor = "A local, user-level automation setup that lets the **Codex desktop app** process GitHub issues into pull requests.\n"
planned = (
    "\nPlanned first-class CLI capabilities also include turning rough task descriptions into "
    "structured GitHub issues. The previous standalone helper was retired during the canonical-CLI cleanup; "
    "the capability itself remains on the product roadmap and will be redesigned as an `autodev` command.\n"
)
if planned.strip() not in text:
    if anchor not in text:
        raise SystemExit("README capability insertion anchor not found")
    text = text.replace(anchor, anchor + planned, 1)
readme.write_text(text, encoding="utf-8")
