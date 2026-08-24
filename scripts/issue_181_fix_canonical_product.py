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
