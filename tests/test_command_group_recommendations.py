import importlib.util
import tempfile
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


def base_facts(**overrides):
    facts = {
        "solutions": [],
        "package_roots": [],
        "api_client_package_roots": [],
        "web_package_roots": [],
        "maui_projects": [],
        "maui_helper_scripts": [],
        "markdown_file_count": 0,
        "workflow_files": [],
    }
    facts.update(overrides)
    return facts


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

    def test_docs_architecture_boundary_issue_does_not_recommend_maui_groups(self):
        result = recommend_command_groups(
            issue_text=(
                "Define the architectural boundary between PHOODAB inventory-native "
                "actions and SecondBrain cross-domain orchestration. Document which "
                "actions remain in PHOODAB and which future workflows are handed off "
                "to SecondBrain. Mention web and MAUI/mobile as consumers, but do not "
                "implement integration logic."
            ),
            changed_paths=[],
            available_command_groups=[
                "env",
                "dotnet-solution",
                "node-root",
                "api-client-generate",
                "web-app",
                "maui-android-doctor",
                "maui-android-build",
                "markdown-smoke",
                "ci-manual-reference",
            ],
            android_sdk_available=True,
        )

        self.assertNotIn("maui-android-doctor", result["recommended_command_groups"])
        self.assertNotIn("maui-android-build", result["recommended_command_groups"])

    def test_maui_text_build_scope_recommends_android_groups_when_sdk_available(self):
        result = recommend_command_groups(
            issue_text="Build the MAUI Android app locally.",
            changed_paths=[],
            android_sdk_available=True,
        )

        self.assertIn("maui-android-doctor", result["recommended_command_groups"])
        self.assertIn("maui-android-build", result["recommended_command_groups"])

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

    def test_dotnet_solution_group_restores_builds_and_tests_solutions(self):
        bench = load_area_reader_bench()

        groups = bench.build_verification_command_groups(
            base_facts(solutions=["App.sln"]),
            ["backend"],
        )

        dotnet_group = next(group for group in groups if group["name"] == "dotnet-solution")
        self.assertEqual(
            [item["argv"] for item in dotnet_group["commands"]],
            [
                ["dotnet", "restore", "App.sln"],
                ["dotnet", "build", "App.sln", "--no-restore", "--verbosity", "minimal"],
                ["dotnet", "test", "App.sln", "--no-build", "--verbosity", "minimal"],
            ],
        )

    def test_markdown_smoke_group_validates_tracked_markdown_whitespace(self):
        bench = load_area_reader_bench()

        groups = bench.build_verification_command_groups(
            base_facts(markdown_file_count=2),
            ["docs"],
        )

        markdown_group = next(group for group in groups if group["name"] == "markdown-smoke")
        self.assertEqual(len(markdown_group["commands"]), 1)
        argv = markdown_group["commands"][0]["argv"]
        self.assertEqual(argv[:2], ["bash", "-lc"])
        self.assertIn("git ls-files '*.md'", argv[2])
        self.assertIn("grep -nE", argv[2])
        self.assertIn("Markdown smoke check failed", argv[2])

    def test_maui_android_groups_prefer_detected_repo_helper_script(self):
        bench = load_area_reader_bench()
        helper = "phoodab/apps/mobile/scripts/maui-android-ubuntu.sh"

        groups = bench.build_verification_command_groups(
            base_facts(
                maui_projects=[
                    {
                        "path": "phoodab/apps/mobile/PHOODAB.Mobile.csproj",
                        "target_frameworks": ["net9.0-android"],
                        "android_target_frameworks": ["net9.0-android"],
                    }
                ],
                maui_helper_scripts=[helper],
            ),
            ["maui"],
        )

        doctor_group = next(group for group in groups if group["name"] == "maui-android-doctor")
        build_group = next(group for group in groups if group["name"] == "maui-android-build")
        self.assertEqual(doctor_group["commands"][0]["argv"], ["bash", helper, "doctor"])
        self.assertEqual(build_group["commands"][0]["argv"], ["bash", helper, "build", "-c", "Debug"])

    def test_detect_repo_facts_records_maui_helper_scripts(self):
        bench = load_area_reader_bench()
        helper = "phoodab/apps/mobile/scripts/maui-android-ubuntu.sh"
        project = "phoodab/apps/mobile/PHOODAB.Mobile.csproj"

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            helper_path = repo / helper
            helper_path.parent.mkdir(parents=True)
            helper_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            project_path = repo / project
            project_path.write_text(
                "<Project><PropertyGroup><UseMaui>true</UseMaui>"
                "<TargetFrameworks>net9.0-android</TargetFrameworks></PropertyGroup></Project>",
                encoding="utf-8",
            )

            facts = bench.detect_repo_facts(
                repo,
                [
                    {"path": helper, "areas": ["ci"]},
                    {"path": project, "areas": ["maui"]},
                ],
                ["maui"],
                {},
            )

        self.assertEqual(facts["maui_helper_scripts"], [helper])

    def test_render_verification_script_detects_repo_root_dynamically(self):
        bench = load_area_reader_bench()

        script = bench.render_verification_script(Path("/fallback/repo"), [])

        self.assertIn("if git rev-parse --show-toplevel >/dev/null 2>&1; then", script)
        self.assertIn('REPO_ROOT="$(git rev-parse --show-toplevel)"', script)
        self.assertIn("REPO_ROOT=/fallback/repo", script)

    def test_api_client_area_matching_excludes_generic_ts_tsx_and_cs_paths(self):
        bench = load_area_reader_bench()

        self.assertFalse(bench.area_for_file("apps/web/src/App.tsx", "api-client"))
        self.assertFalse(bench.area_for_file("apps/api/Controllers/FooController.cs", "api-client"))
        self.assertTrue(bench.area_for_file("packages/api-client/src/generated.ts", "api-client"))
        self.assertTrue(bench.area_for_file("apps/api/openapi.json", "api-client"))

if __name__ == "__main__":
    unittest.main()
