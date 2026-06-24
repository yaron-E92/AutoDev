import importlib.util
import unittest
from pathlib import Path

from area_reader_v2.command_group_recommendations import (
    ALL_COMMAND_GROUPS,
    recommend_command_groups,
)


BENCHMARK_PROMPT = (
    "Analyze the complete repository structure, including backend, web, "
    "MAUI/mobile/desktop if present, tests, and CI. Propose the safest local "
    "verification approach for a small issue-to-PR automation run. Do not edit files."
)


def load_area_reader_bench():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "local-llm" / "area_reader_bench.py"
    spec = importlib.util.spec_from_file_location("area_reader_bench", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandGroupRecommendationTests(unittest.TestCase):
    def test_generic_issue_uses_conservative_default_recommendations(self):
        result = recommend_command_groups(
            issue_text="Run local verification before issue-to-PR readiness.",
            changed_paths=[],
        )

        self.assertEqual(result["available_command_groups"], ALL_COMMAND_GROUPS)
        self.assertEqual(
            result["recommended_command_groups"],
            [
                "env",
                "dotnet-solution",
                "node-root",
                "markdown-smoke",
            ],
        )
        self.assertNotIn("maui-android-doctor", result["recommended_command_groups"])
        self.assertNotIn("maui-android-build", result["recommended_command_groups"])
        self.assertNotIn("ci-manual-reference", result["recommended_command_groups"])
        self.assertIn("conditional_command_groups", result)

    def test_api_client_group_is_recommended_for_openapi_issue_text(self):
        result = recommend_command_groups(
            issue_text="Regenerate the TypeScript client from the OpenAPI contract.",
            changed_paths=[],
        )

        self.assertIn("api-client-generate", result["recommended_command_groups"])

    def test_api_client_group_is_recommended_for_backend_contract_paths(self):
        result = recommend_command_groups(
            issue_text="Update backend behavior.",
            changed_paths=["phoodab/apps/api/openapi.json"],
        )

        self.assertIn("api-client-generate", result["recommended_command_groups"])

    def test_web_group_is_recommended_for_web_scope(self):
        result = recommend_command_groups(
            issue_text="Fix React component rendering.",
            changed_paths=["phoodab/apps/web/src/App.tsx"],
        )

        self.assertIn("web-app", result["recommended_command_groups"])

    def test_maui_doctor_is_recommended_only_for_mobile_scope(self):
        generic = recommend_command_groups(
            issue_text="Fix backend validation.",
            changed_paths=["phoodab/apps/api/Program.cs"],
        )
        mobile = recommend_command_groups(
            issue_text="Fix Android emulator startup for the MAUI mobile app.",
            changed_paths=["phoodab/apps/mobile/MainPage.xaml"],
        )

        self.assertNotIn("maui-android-doctor", generic["recommended_command_groups"])
        self.assertIn("maui-android-doctor", mobile["recommended_command_groups"])

    def test_maui_build_requires_mobile_scope_and_android_availability(self):
        missing_android = recommend_command_groups(
            issue_text="Fix MAUI mobile build.",
            changed_paths=["phoodab/apps/mobile/PHOODAB.Mobile.csproj"],
            android_sdk_available=False,
        )
        with_android = recommend_command_groups(
            issue_text="Fix MAUI mobile build.",
            changed_paths=["phoodab/apps/mobile/PHOODAB.Mobile.csproj"],
            android_sdk_available=True,
        )

        self.assertNotIn("maui-android-build", missing_android["recommended_command_groups"])
        self.assertIn("maui-android-build", with_android["recommended_command_groups"])

    def test_ci_manual_reference_remains_conditional_reference_only(self):
        result = recommend_command_groups(
            issue_text="Inspect remote CI state for this PR.",
            changed_paths=[],
        )

        self.assertIn("ci-manual-reference", result["available_command_groups"])
        self.assertIn("ci-manual-reference", result["conditional_command_groups"])
        self.assertNotIn("ci-manual-reference", result["recommended_command_groups"])

    def test_inventory_prompt_does_not_recommend_maui_groups(self):
        result = recommend_command_groups(
            issue_text=BENCHMARK_PROMPT,
            changed_paths=[],
            available_command_groups=ALL_COMMAND_GROUPS,
            android_sdk_available=True,
        )

        self.assertNotIn("maui-android-doctor", result["recommended_command_groups"])
        self.assertNotIn("maui-android-build", result["recommended_command_groups"])

    def test_recommendations_are_filtered_to_available_groups(self):
        result = recommend_command_groups(
            issue_text="Fix the MAUI mobile build.",
            changed_paths=["phoodab/apps/mobile/PHOODAB.Mobile.csproj"],
            available_command_groups=["env", "dotnet-solution"],
            android_sdk_available=True,
        )

        self.assertEqual(result["available_command_groups"], ["env", "dotnet-solution"])
        self.assertEqual(result["recommended_command_groups"], ["env", "dotnet-solution"])

    def test_api_client_group_is_recommended_for_api_client_paths(self):
        result = recommend_command_groups(
            issue_text="Update generated client output.",
            changed_paths=["phoodab/packages/api-client/src/generated.ts"],
        )

        self.assertIn("api-client-generate", result["recommended_command_groups"])

    def test_benchmark_recommendation_wrapper_returns_metadata_shape(self):
        bench = load_area_reader_bench()
        command_groups = [
            {"name": "env", "recommended": True, "commands": []},
            {"name": "dotnet-solution", "recommended": True, "commands": []},
            {"name": "node-root", "recommended": True, "commands": []},
            {"name": "markdown-smoke", "recommended": True, "commands": []},
            {"name": "maui-android-doctor", "recommended": True, "commands": []},
            {"name": "maui-android-build", "recommended": True, "commands": []},
        ]

        result = bench.recommended_command_groups(
            command_groups,
            issue_text=BENCHMARK_PROMPT,
            changed_paths=[],
            android_sdk_available=True,
        )
        coder_prompt = bench.build_coder_prompt("Issue", "Brief", {}, result, command_groups)

        self.assertEqual(
            result["recommended_command_groups"],
            ["env", "dotnet-solution", "node-root", "markdown-smoke"],
        )
        self.assertIn("available_command_groups", result)
        self.assertIn("conditional_command_groups", result)
        self.assertIn("available_command_groups", coder_prompt)


if __name__ == "__main__":
    unittest.main()
