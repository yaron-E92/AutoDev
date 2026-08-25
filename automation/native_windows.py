from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from automation import native_packaging, package_release


WIX_NAMESPACE = "http://schemas.microsoft.com/wix/2006/wi"
GUID_NAMESPACE = uuid.UUID("89d3842a-bd63-4e31-b85d-1c6b33b75e0f")
UPGRADE_CODE = uuid.uuid5(GUID_NAMESPACE, "autodev/windows/upgrade")


class WindowsPackagingError(RuntimeError):
    pass


def artifact_name(version: str) -> str:
    return f"AutoDev-{_msi_version(version)}-Setup.msi"


def _msi_version(version: str) -> str:
    value = native_packaging.package_version(version)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise WindowsPackagingError(
            "Windows MSI releases require a canonical vMAJOR.MINOR.PATCH version"
        )
    return value


def _guid(name: str) -> str:
    return "{" + str(uuid.uuid5(GUID_NAMESPACE, name)).upper() + "}"


def _id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _payload_files(payload: Path) -> list[Path]:
    values = [path for path in payload.rglob("*") if path.is_file() and not path.is_symlink()]
    if not values:
        raise WindowsPackagingError(f"native Windows payload is empty: {payload}")
    return sorted(values, key=lambda path: path.relative_to(payload).as_posix())


def _directory_tree(payload: Path, files: list[Path]) -> dict[str, object]:
    root: dict[str, object] = {"files": [], "dirs": {}}
    for file_path in files:
        relative = file_path.relative_to(payload)
        node = root
        for part in relative.parts[:-1]:
            dirs = node["dirs"]
            assert isinstance(dirs, dict)
            node = dirs.setdefault(part, {"files": [], "dirs": {}})  # type: ignore[assignment]
            assert isinstance(node, dict)
        node_files = node["files"]
        assert isinstance(node_files, list)
        node_files.append(file_path)
    return root


def _directory_cleanup_component(
    directory_id: str,
    relative_dir: str,
    indent: str,
    component_ids: list[str],
) -> list[str]:
    identity = relative_dir or "INSTALLFOLDER"
    component_id = _id("CDIR", identity)
    remove_id = _id("RDIR", identity)
    component_ids.append(component_id)
    return [
        f'{indent}<Component Id="{component_id}" Guid="{_guid("autodev/windows/directory/" + identity)}">',
        f'{indent}  <RegistryValue Root="HKCU" Key="Software\\AutoDev\\Directories" Name="{component_id}" Type="integer" Value="1" KeyPath="yes" />',
        f'{indent}  <RemoveFolder Id="{remove_id}" Directory="{directory_id}" On="uninstall" />',
        f"{indent}</Component>",
    ]


def _render_directory(
    payload: Path,
    name: str,
    node: dict[str, object],
    relative_dir: str,
    indent: str,
    component_ids: list[str],
) -> list[str]:
    lines: list[str] = []
    directory_id = "INSTALLFOLDER" if not relative_dir else _id("D", relative_dir)
    if relative_dir:
        lines.append(f'{indent}<Directory Id="{directory_id}" Name="{escape(name)}">')
        indent += "  "

    # Every directory below LocalAppDataFolder gets an installer-owned component
    # so the RemoveFile table can remove the empty directory on uninstall. The
    # HKCU registry key path also satisfies Windows Installer's per-user component
    # rules without treating installed payload files as key paths.
    lines.extend(
        _directory_cleanup_component(
            directory_id,
            relative_dir,
            indent,
            component_ids,
        )
    )

    files = node.get("files", [])
    assert isinstance(files, list)
    for file_path in files:
        assert isinstance(file_path, Path)
        relative = file_path.relative_to(payload).as_posix()
        component_id = _id("C", relative)
        file_id = _id("F", relative)
        component_ids.append(component_id)
        windows_relative = relative.replace("/", "\\")
        source = escape(f"$(var.PayloadRoot)\\{windows_relative}", {'"': '&quot;'})
        lines.extend(
            [
                f'{indent}<Component Id="{component_id}" Guid="{_guid("autodev/windows/component/" + relative)}">',
                f'{indent}  <File Id="{file_id}" Source="{source}" Checksum="yes" />',
                f'{indent}  <RegistryValue Root="HKCU" Key="Software\\AutoDev\\Payload" Name="{component_id}" Type="integer" Value="1" KeyPath="yes" />',
                f"{indent}</Component>",
            ]
        )

    dirs = node.get("dirs", {})
    assert isinstance(dirs, dict)
    for child_name in sorted(dirs):
        child = dirs[child_name]
        assert isinstance(child, dict)
        child_relative = f"{relative_dir}/{child_name}" if relative_dir else child_name
        lines.extend(
            _render_directory(
                payload,
                child_name,
                child,
                child_relative,
                indent,
                component_ids,
            )
        )

    if relative_dir:
        indent = indent[:-2]
        lines.append(f"{indent}</Directory>")
    return lines


def render_wix_source(payload: Path, version: str, commit: str) -> str:
    payload = payload.expanduser().resolve()
    msi_version = _msi_version(version)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise WindowsPackagingError(f"expected full release commit SHA, got {commit!r}")
    files = _payload_files(payload)
    tree = _directory_tree(payload, files)
    component_ids: list[str] = []
    product_code = _guid(f"autodev/windows/product/{msi_version}")
    package_code = _guid(f"autodev/windows/package/{msi_version}/{commit.lower()}")

    payload_lines = _render_directory(
        payload,
        "",
        tree,
        "",
        "            ",
        component_ids,
    )
    programs_cleanup = "C_ProgramsFolderCleanup"
    path_component = "C_ProductPath"
    component_ids.extend((programs_cleanup, path_component))

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<Wix xmlns="{WIX_NAMESPACE}">',
        f'  <Product Id="{product_code}" Name="AutoDev" Language="1033" Version="{msi_version}" Manufacturer="AutoDev" UpgradeCode="{{{str(UPGRADE_CODE).upper()}}}">',
        f'    <Package Id="{package_code}" InstallerVersion="500" Compressed="yes" InstallScope="perUser" InstallPrivileges="limited" Description="AutoDev autonomous issue-to-PR automation" />',
        '    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />',
        '    <MajorUpgrade DowngradeErrorMessage="A newer version of AutoDev is already installed." Schedule="afterInstallInitialize" />',
        '    <Property Id="ARPNOREPAIR" Value="1" />',
        '    <Property Id="INSTALLFOLDER">',
        '      <RegistrySearch Id="AutoDevInstallPathSearch" Root="HKCU" Key="Software\\AutoDev" Name="InstallPath" Type="directory" />',
        '    </Property>',
        '    <Directory Id="TARGETDIR" Name="SourceDir">',
        '      <Directory Id="LocalAppDataFolder">',
        '        <Directory Id="AutoDevProgramsFolder" Name="Programs">',
        f'          <Component Id="{programs_cleanup}" Guid="{_guid("autodev/windows/directory/AutoDevProgramsFolder")}">',
        f'            <RegistryValue Root="HKCU" Key="Software\\AutoDev\\Directories" Name="{programs_cleanup}" Type="integer" Value="1" KeyPath="yes" />',
        '            <RemoveFolder Id="R_ProgramsFolderCleanup" Directory="AutoDevProgramsFolder" On="uninstall" />',
        '          </Component>',
        '          <Directory Id="INSTALLFOLDER" Name="AutoDev">',
        *payload_lines,
        f'            <Component Id="{path_component}" Guid="{_guid("autodev/windows/path-component")}">',
        '              <RegistryValue Root="HKCU" Key="Software\\AutoDev" Name="InstallPath" Type="string" Value="[INSTALLFOLDER]" KeyPath="yes" />',
        '              <Environment Id="AutoDevUserPath" Name="PATH" Action="set" Part="last" System="no" Permanent="no" Value="[INSTALLFOLDER]" />',
        '            </Component>',
        '          </Directory>',
        '        </Directory>',
        '      </Directory>',
        '    </Directory>',
        '    <Feature Id="Complete" Title="AutoDev" Level="1" Absent="disallow">',
    ]
    lines.extend(f'      <ComponentRef Id="{component_id}" />' for component_id in component_ids)
    lines.extend(
        [
            '    </Feature>',
            '  </Product>',
            '</Wix>',
            '',
        ]
    )
    return "\n".join(lines)


def find_wix_tool(name: str, *, which: Callable[[str], str | None] = shutil.which) -> str:
    direct = which(name)
    if direct:
        return direct
    wix = os.environ.get("WIX", "").strip()
    candidates = []
    if wix:
        candidates.append(Path(wix) / "bin" / name)
        candidates.append(Path(wix) / name)
    candidates.extend(
        [
            Path(r"C:\Program Files (x86)\WiX Toolset v3.14\bin") / name,
            Path(r"C:\Program Files\WiX Toolset v3.14\bin") / name,
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return os.fspath(candidate)
    raise WindowsPackagingError(f"WiX Toolset 3.14 tool is unavailable: {name}")


def build_msi(
    repo: Path,
    payload: Path,
    out_dir: Path,
    version: str,
    commit: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    repo = repo.expanduser().resolve()
    payload = payload.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".native-build" / "windows"
    work.mkdir(parents=True, exist_ok=True)
    wxs = work / "autodev.wxs"
    wixobj = work / "autodev.wixobj"
    destination = out_dir / artifact_name(version)
    wxs.write_text(render_wix_source(payload, version, commit), encoding="utf-8", newline="\n")

    candle = find_wix_tool("candle.exe", which=which)
    light = find_wix_tool("light.exe", which=which)
    # Package/@Id is intentionally deterministic for one exact release identity,
    # and ICE91 describes the expected per-user directory layout. Suppress only
    # those two known warnings; all other WiX validation remains enabled.
    commands = (
        [
            candle,
            "-nologo",
            "-sw1091",
            f"-dPayloadRoot={payload}",
            "-out",
            os.fspath(wixobj),
            os.fspath(wxs),
        ],
        [
            light,
            "-nologo",
            "-sice:ICE91",
            "-spdb",
            "-out",
            os.fspath(destination),
            os.fspath(wixobj),
        ],
    )
    env = dict(os.environ)
    env.setdefault("SOURCE_DATE_EPOCH", str(native_packaging.source_date_epoch(repo, commit)))
    for command in commands:
        completed = runner(command, cwd=repo, env=env, check=False)
        if int(getattr(completed, "returncode", 1)) != 0:
            detail = getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or ""
            raise WindowsPackagingError(f"WiX command failed: {detail}")
    if not destination.is_file():
        raise WindowsPackagingError(f"WiX did not create expected MSI: {destination}")
    return destination


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the native AutoDev Windows MSI.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    try:
        destination = build_msi(
            Path(args.repo),
            Path(args.payload),
            Path(args.out),
            args.version,
            args.commit,
        )
    except (WindowsPackagingError, native_packaging.NativePackagingError, package_release.ReleasePackagingError) as exc:
        parser.error(str(exc))
    print(destination)
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
