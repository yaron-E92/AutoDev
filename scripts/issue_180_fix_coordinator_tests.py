from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def fix_role_runtime_tests() -> None:
    path = TESTS / "test_role_runtime.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "role_coordinator_flow,\n    role_coordinator_runtime,\n            \"",
        "role_coordinator_flow,\n            \"",
    )
    for attribute in ("_prepare_role", "_accept_role", "role_acceptance"):
        text = text.replace(
            f'patch.object(\n            role_coordinator_flow,\n            "{attribute}"',
            f'patch.object(\n            role_coordinator_runtime,\n            "{attribute}"',
        )
    path.write_text(text, encoding="utf-8")


def fix_diagnostics_tests() -> None:
    path = TESTS / "test_role_runtime_diagnostics.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "opencode_role_runtime,\n    role_coordinator_contract,\n    role_coordinator_runtime,\n    role_runtime,\n                \"role_acceptance\"",
        "role_coordinator_runtime,\n                \"role_acceptance\"",
    )
    helper = '''\n\nclass _DiagnosticRuntime:\n    name = "opencode"\n\n    def invoke(self, context, *, runner, which=None):\n        completed = runner(["opencode-test-role"], capture_output=True)\n        returncode = int(getattr(completed, "returncode", 1))\n        return role_runtime.RoleInvocationResult(\n            runtime=self.name,\n            role=context.role,\n            phase=context.phase,\n            returncode=returncode,\n            elapsed_ms=1,\n            stdout=str(getattr(completed, "stdout", "") or ""),\n            stderr=str(getattr(completed, "stderr", "") or ""),\n            termination="completed" if returncode == 0 else "runtime-nonzero",\n            model="test/model",\n        )\n\n\ndef _diagnostic_snapshots():\n    return {\n        "reader": role_runtime.build_role_snapshot(\n            runtime="opencode",\n            role="reader",\n            configured={"model": "test/model"},\n        )\n    }\n'''
    marker = "\n\nclass RoleRuntimeDiagnosticsTests(unittest.TestCase):"
    if helper not in text:
        if marker not in text:
            raise SystemExit("role diagnostics test class marker missing")
        text = text.replace(marker, helper + marker, 1)

    pattern = re.compile(
        r'(role_coordinator_runtime\.run_role\(\n(?P<indent>\s+)repo,\n(?P=indent)"reader",\n)'
    )
    text, count = pattern.subn(
        lambda match: match.group(1)
        + match.group("indent")
        + "_DiagnosticRuntime(),\n"
        + match.group("indent")
        + "_diagnostic_snapshots(),\n",
        text,
    )
    if count < 3:
        raise SystemExit(f"expected at least three canonical diagnostic run_role calls, found {count}")
    if "opencode_coordinator" in text:
        raise SystemExit("obsolete OpenCode coordinator remains in diagnostics tests")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    fix_role_runtime_tests()
    fix_diagnostics_tests()


if __name__ == "__main__":
    main()
