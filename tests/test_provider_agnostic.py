import json
import unittest
from pathlib import Path



REPO_ROOT = Path(__file__).resolve().parents[1]


class ProviderAgnosticTests(unittest.TestCase):


    def test_mixed_profile_keeps_openrouter_roles_free_only(self):
        profile = json.loads(
            (REPO_ROOT / "examples" / "providers" / "groq-openrouter-free.json").read_text(encoding="utf-8")
        )

        self.assertEqual(profile["roles"]["planner"]["api_key_env"], "GROQ_API_KEY")
        for role in ("implementer", "fixer"):
            self.assertTrue(profile["roles"][role]["model"].endswith(":free"))
            self.assertTrue(profile["roles"][role]["free_only"])
            self.assertEqual(profile["roles"][role]["api_key_env"], "OPENROUTER_API_KEY")


if __name__ == "__main__":
    unittest.main()
