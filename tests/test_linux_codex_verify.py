import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "linux" / "scripts" / "codex-verify.sh"


@unittest.skipIf(os.name == "nt", "Linux verifier regression tests require a POSIX host")
class LinuxCodexVerifyTests(unittest.TestCase):
    def _run_auto_web_repo(self, npm_exit_code: int):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "package.json").write_text("{}\n", encoding="utf-8")

            fake_bin = repo / "fake-bin"
            fake_bin.mkdir()

            fake_npm = fake_bin / "npm"
            fake_npm.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    printf 'fake npm invoked\n'
                    exit {npm_exit_code}
                    """
                ),
                encoding="utf-8",
            )
            fake_npm.chmod(0o755)

            fake_jq = fake_bin / "jq"
            fake_jq.write_text(
                "#!/usr/bin/env bash\nexit 1\n",
                encoding="utf-8",
            )
            fake_jq.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            return subprocess.run(
                ["bash", str(VERIFY_SCRIPT), "--profiles", "auto"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_auto_succeeds_when_applicable_checks_pass_without_python_project(self):
        completed = self._run_auto_web_repo(npm_exit_code=0)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("fake npm invoked", completed.stdout)

    def test_auto_propagates_applicable_check_failure(self):
        completed = self._run_auto_web_repo(npm_exit_code=23)

        self.assertEqual(completed.returncode, 23)
        self.assertIn("fake npm invoked", completed.stdout)

    def test_maui_profile_builds_android_app_and_defers_windows_test_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            app = repo / "src" / "Sample.App" / "Sample.App.csproj"
            app.parent.mkdir(parents=True)
            app.write_text(
                textwrap.dedent(
                    """\
                    <Project Sdk="Microsoft.NET.Sdk">
                      <PropertyGroup>
                        <TargetFrameworks>net10.0-android</TargetFrameworks>
                        <UseMaui>true</UseMaui>
                      </PropertyGroup>
                    </Project>
                    """
                ),
                encoding="utf-8",
            )

            windows_tests = repo / "tests" / "Sample.GUI.UnitTests" / "Sample.GUI.UnitTests.csproj"
            windows_tests.parent.mkdir(parents=True)
            windows_tests.write_text(
                textwrap.dedent(
                    """\
                    <Project Sdk="Microsoft.NET.Sdk">
                      <PropertyGroup>
                        <TargetFramework>net10.0-windows10.0.19041.0</TargetFramework>
                        <IsTestProject>true</IsTestProject>
                      </PropertyGroup>
                      <ItemGroup>
                        <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.10.0" />
                        <PackageReference Include="Microsoft.Maui.Controls" Version="10.0.0" />
                        <ProjectReference Include="..\\..\\src\\Sample.App\\Sample.App.csproj" />
                      </ItemGroup>
                    </Project>
                    """
                ),
                encoding="utf-8",
            )

            fake_bin = repo / "fake-bin"
            fake_bin.mkdir()
            dotnet_log = repo / "dotnet.log"
            fake_dotnet = fake_bin / "dotnet"
            fake_dotnet.write_text(
                "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$DOTNET_LOG\"\n",
                encoding="utf-8",
            )
            fake_dotnet.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["DOTNET_LOG"] = str(dotnet_log)
            completed = subprocess.run(
                ["bash", str(VERIFY_SCRIPT), "--profiles", "maui"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("DEFERRED: Windows-targeted test project", completed.stdout)
            self.assertIn("Sample.GUI.UnitTests.csproj", completed.stdout)
            self.assertIn("MAUI Linux verification: building", completed.stdout)
            self.assertIn("net10.0-android", completed.stdout)

            invocations = dotnet_log.read_text(encoding="utf-8")
            self.assertIn("build ./src/Sample.App/Sample.App.csproj -f net10.0-android", invocations)
            self.assertNotIn("Sample.GUI.UnitTests.csproj", invocations)
            self.assertNotIn("EnableWindowsTargeting", invocations)

    def test_maui_profile_defers_windows_only_maui_app_without_invoking_dotnet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            app = repo / "src" / "WindowsOnly.App" / "WindowsOnly.App.csproj"
            app.parent.mkdir(parents=True)
            app.write_text(
                textwrap.dedent(
                    """\
                    <Project Sdk="Microsoft.NET.Sdk">
                      <PropertyGroup>
                        <TargetFramework>net10.0-windows10.0.19041.0</TargetFramework>
                        <UseMaui>true</UseMaui>
                      </PropertyGroup>
                    </Project>
                    """
                ),
                encoding="utf-8",
            )

            fake_bin = repo / "fake-bin"
            fake_bin.mkdir()
            dotnet_log = repo / "dotnet.log"
            fake_dotnet = fake_bin / "dotnet"
            fake_dotnet.write_text(
                "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$DOTNET_LOG\"\n",
                encoding="utf-8",
            )
            fake_dotnet.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["DOTNET_LOG"] = str(dotnet_log)
            completed = subprocess.run(
                ["bash", str(VERIFY_SCRIPT), "--profiles", "maui"],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("DEFERRED: MAUI project", completed.stdout)
            self.assertIn("has no Android target runnable on Linux", completed.stdout)
            self.assertFalse(dotnet_log.exists())


if __name__ == "__main__":
    unittest.main()
