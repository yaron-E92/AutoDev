from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TuiToolkitContractTests(unittest.TestCase):
    def test_tui_runtime_uses_only_stdlib_and_autodev_modules(self) -> None:
        allowed_roots = {
            "argparse",
            "dataclasses",
            "datetime",
            "io",
            "json",
            "os",
            "pathlib",
            "select",
            "shutil",
            "subprocess",
            "sys",
            "time",
            "typing",
            "webbrowser",
            "automation",
            "msvcrt",
            "termios",
            "tty",
        }
        for name in ("tui_model.py", "tui_actions.py", "tui_terminal.py", "tui_cli.py"):
            path = REPO_ROOT / "automation" / name
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots.add(node.module.split(".", 1)[0])
            self.assertEqual(roots - allowed_roots, set(), msg=f"unexpected TUI dependency in {name}")

    def test_tui_renderer_does_not_require_unicode_or_color_sequences(self) -> None:
        text = (REPO_ROOT / "automation" / "tui_terminal.py").read_text(encoding="utf-8")
        self.assertNotIn("rich", text.casefold())
        self.assertNotIn("textual", text.casefold())
        self.assertNotIn("prompt_toolkit", text.casefold())
        self.assertNotIn("\x1b[3", text)


if __name__ == "__main__":
    unittest.main()
