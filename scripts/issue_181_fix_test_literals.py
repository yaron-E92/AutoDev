from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tests" / "test_opencode_integration.py"
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r"\n    def test_install_is_idempotent_and_preserves_user_opencode_config\(self.*?(?=\n    def |\n\nif __name__ ==)",
    re.S,
)
body = r'''
    def test_install_is_idempotent_and_preserves_user_opencode_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            custom = target / ".opencode" / "commands" / "custom.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("user-owned\n", encoding="utf-8")
            project_json = target / "opencode.json"
            project_jsonc = target / "opencode.jsonc"
            project_json.write_text('{"agent":{"autodev-reader":{"model":"provider/reader"}}}\n', encoding="utf-8")
            project_jsonc.write_text('// user-owned\n{"model":"provider/default"}\n', encoding="utf-8")

            first = opencode_adapter_assets.install_assets(target, REPO_ROOT)
            second = opencode_adapter_assets.install_assets(target, REPO_ROOT)

            self.assertEqual(len(first), len(second))
            self.assertEqual(custom.read_text(encoding="utf-8"), "user-owned\n")
            self.assertEqual(project_json.read_text(encoding="utf-8"), '{"agent":{"autodev-reader":{"model":"provider/reader"}}}\n')
            self.assertEqual(project_jsonc.read_text(encoding="utf-8"), '// user-owned\n{"model":"provider/default"}\n')
            self.assertFalse((target / ".opencode" / "autodev.json").exists())
            self.assertFalse((target / ".opencode" / "autodev.py").exists())
            self.assertFalse((target / ".opencode" / "autodev.ps1").exists())
            self.assertTrue((target / ".opencode" / "commands" / "autodev-issue-to-pr.md").is_file())
            self.assertTrue((target / ".opencode" / "agents" / "autodev-coordinator.md").is_file())
'''
text, count = pattern.subn(body.rstrip() + "\n", text, count=1)
if count != 1:
    raise SystemExit("generated idempotent installer test not found")
path.write_text(text, encoding="utf-8")
