import tempfile
import unittest
from pathlib import Path

from area_reader_v2.runner_core import (
    PREFERRED_SOLUTION_FILTER_MARKERS,
    build_verification_command_groups,
    collect_repo_files,
    detect_repo_facts,
    preferred_solution_filter,
)


def base_facts(**overrides):
    facts = {
        "solutions": [],
        "solution_filters": [],
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


class SolutionFilterPreferenceTests(unittest.TestCase):
    def test_preferred_filter_markers_match_linux_verify_policy(self):
        expected = ("no-gui", "nogui", "headless", "backend", "server", "api", "ci", "test")
        self.assertEqual(PREFERRED_SOLUTION_FILTER_MARKERS, expected)

        for marker in expected:
            with self.subTest(marker=marker):
                path = f"verification/Foo.{marker}.slnf"
                self.assertEqual(preferred_solution_filter([path]), path)

        self.assertIsNone(preferred_solution_filter(["Foo.desktop.slnf"]))

    def test_events_shape_prefers_ci_filter_and_preserves_maui_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "Yaref92.Events.sln").write_text("", encoding="utf-8")
            (repo / "Yaref92.Events.ci.slnf").write_text("{}\n", encoding="utf-8")

            sample = repo / "samples" / "EventMessenger" / "EventMessenger.csproj"
            sample.parent.mkdir(parents=True)
            sample.write_text(
                "<Project><PropertyGroup><UseMaui>true</UseMaui>"
                "<TargetFrameworks>net10.0-android</TargetFrameworks>"
                "</PropertyGroup></Project>",
                encoding="utf-8",
            )

            files, _, _ = collect_repo_files(repo)
            facts = detect_repo_facts(repo, files, ["backend", "maui"], {})

            self.assertEqual(facts["solutions"], ["Yaref92.Events.sln"])
            self.assertEqual(facts["solution_filters"], ["Yaref92.Events.ci.slnf"])

            groups = build_verification_command_groups(facts, ["backend", "maui"])
            dotnet_group = next(group for group in groups if group["name"] == "dotnet-solution")
            targets = [command["argv"][2] for command in dotnet_group["commands"]]
            self.assertEqual(targets, ["Yaref92.Events.ci.slnf"] * 3)
            self.assertNotIn("Yaref92.Events.sln", targets)

            maui_group = next(group for group in groups if group["name"] == "maui-android-build")
            self.assertTrue(
                any("samples/EventMessenger/EventMessenger.csproj" in command["argv"] for command in maui_group["commands"])
            )

    def test_nonpreferred_filter_does_not_replace_full_solution(self):
        groups = build_verification_command_groups(
            base_facts(
                solutions=["App.sln"],
                solution_filters=["App.desktop.slnf"],
            ),
            ["backend"],
        )

        dotnet_group = next(group for group in groups if group["name"] == "dotnet-solution")
        self.assertEqual(
            [command["argv"][2] for command in dotnet_group["commands"]],
            ["App.sln", "App.sln", "App.sln"],
        )

    def test_filter_is_used_when_it_is_the_only_solution_input(self):
        groups = build_verification_command_groups(
            base_facts(solution_filters=["App.desktop.slnf"]),
            ["backend"],
        )

        dotnet_group = next(group for group in groups if group["name"] == "dotnet-solution")
        self.assertEqual(
            [command["argv"][2] for command in dotnet_group["commands"]],
            ["App.desktop.slnf", "App.desktop.slnf", "App.desktop.slnf"],
        )


if __name__ == "__main__":
    unittest.main()
