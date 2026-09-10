from __future__ import annotations

import re
import unittest
from pathlib import Path


class NativePackagingWorkflowTests(unittest.TestCase):
    def test_linux_native_tool_install_uses_only_ubuntu_sources(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "native-packaging.yml"
        ).read_text(encoding="utf-8")

        install_blocks = re.findall(
            r"      - name: Install native build tools\n"
            r"        shell: bash\n"
            r"        run: \|\n"
            r"(?P<body>(?:          .*\n)+?)"
            r"(?=\n      - name: )",
            workflow,
        )
        self.assertEqual(len(install_blocks), 2)

        for block in install_blocks:
            self.assertIn(
                "ubuntu_sources=/etc/apt/sources.list.d/ubuntu.sources",
                block,
            )
            self.assertIn('test -f "$ubuntu_sources"', block)
            self.assertIn('Dir::Etc::sourcelist=$ubuntu_sources', block)
            self.assertIn('Dir::Etc::sourceparts=-', block)
            self.assertIn('sudo apt-get "${apt_args[@]}" update', block)
            self.assertNotIn("sudo apt-get update", block)


if __name__ == "__main__":
    unittest.main()
