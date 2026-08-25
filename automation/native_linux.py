from __future__ import annotations

import argparse
import gzip
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Callable

from automation import native_packaging, package_release


PACKAGE_NAME = "autodev"
ARCH_DEB = "amd64"
ARCH_RPM = "x86_64"
HOMEPAGE = "https://github.com/yaron-E92/AutoDev"
LICENSE_DECLARATION = "GPL-3.0-only"


class LinuxPackagingError(RuntimeError):
    pass


def deb_artifact_name(version: str) -> str:
    return f"autodev_{native_packaging.package_version(version)}_{ARCH_DEB}.deb"


def rpm_artifact_name(version: str) -> str:
    return f"autodev-{native_packaging.package_version(version)}-1.{ARCH_RPM}.rpm"


def deb_control(version: str) -> str:
    package_version = native_packaging.package_version(version)
    return (
        "Package: autodev\n"
        f"Version: {package_version}\n"
        "Section: devel\n"
        "Priority: optional\n"
        f"Architecture: {ARCH_DEB}\n"
        "Maintainer: AutoDev <noreply@users.noreply.github.com>\n"
        "Depends: libc6, git, gh\n"
        f"Homepage: {HOMEPAGE}\n"
        "Description: Autonomous GitHub issue-to-PR automation\n"
        " AutoDev coordinates bounded model roles, durable checkpoints, verification,\n"
        " privacy policy, and optional native scheduling. The package includes the\n"
        " AutoDev Python runtime; model/runtime integrations remain separately configured.\n"
        f"X-AutoDev-License: {LICENSE_DECLARATION}\n"
    )


def rpm_spec(version: str) -> str:
    package_version = native_packaging.package_version(version)
    return f"""Name:           autodev
Version:        {package_version}
Release:        1
Summary:        Autonomous GitHub issue-to-PR automation
License:        {LICENSE_DECLARATION}
URL:            {HOMEPAGE}
Source0:        autodev-{package_version}.tar.gz
BuildArch:      {ARCH_RPM}
AutoReqProv:    no
Requires:       glibc
Requires:       git
Requires:       gh

%description
AutoDev coordinates bounded model roles, durable checkpoints, verification,
privacy policy, and optional native scheduling. The package includes the AutoDev
Python runtime; model/runtime integrations remain separately configured.

%prep
%setup -q

%build

%install
rm -rf "%{{buildroot}}"
mkdir -p "%{{buildroot}}/opt/autodev" "%{{buildroot}}/usr/bin"
cp -a . "%{{buildroot}}/opt/autodev/"
ln -s ../../opt/autodev/autodev "%{{buildroot}}/usr/bin/autodev"

%files
/opt/autodev
/usr/bin/autodev
"""


def _copy_payload(payload: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(payload, destination, symlinks=True)


def _installed_size_kib(root: Path) -> int:
    size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink())
    return max(1, (size + 1023) // 1024)


def stage_deb(payload: Path, staging: Path, version: str, epoch: int) -> Path:
    payload = payload.expanduser().resolve()
    staging = staging.expanduser().resolve()
    if staging.exists():
        shutil.rmtree(staging)
    product = staging / "opt" / "autodev"
    _copy_payload(payload, product)
    bin_dir = staging / "usr" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "autodev").symlink_to("../../opt/autodev/autodev")
    debian = staging / "DEBIAN"
    debian.mkdir(parents=True)
    control = deb_control(version)
    control = control.replace(
        "Description: Autonomous GitHub issue-to-PR automation\n",
        f"Installed-Size: {_installed_size_kib(product)}\nDescription: Autonomous GitHub issue-to-PR automation\n",
    )
    (debian / "control").write_text(control, encoding="utf-8", newline="\n")
    native_packaging.normalize_tree_mtimes(staging, epoch)
    return staging


def _tar_info(name: str, mode: int, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = mode
    info.mtime = epoch
    return info


def write_payload_tar(payload: Path, destination: Path, version: str, epoch: int) -> None:
    payload = payload.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"autodev-{native_packaging.package_version(version)}"
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                root_info = _tar_info(prefix, 0o755, epoch)
                root_info.type = tarfile.DIRTYPE
                archive.addfile(root_info)
                for path in sorted(payload.rglob("*"), key=lambda item: item.relative_to(payload).as_posix()):
                    relative = path.relative_to(payload).as_posix()
                    arcname = f"{prefix}/{relative}"
                    metadata = path.lstat()
                    mode = stat.S_IMODE(metadata.st_mode)
                    info = _tar_info(arcname, mode, epoch)
                    if path.is_symlink():
                        info.type = tarfile.SYMTYPE
                        info.linkname = os.readlink(path)
                        archive.addfile(info)
                    elif path.is_dir():
                        info.type = tarfile.DIRTYPE
                        archive.addfile(info)
                    elif path.is_file():
                        info.size = metadata.st_size
                        with path.open("rb") as handle:
                            archive.addfile(info, handle)


def build_deb(
    payload: Path,
    out_dir: Path,
    version: str,
    epoch: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    staging = out_dir / ".native-build" / "debian" / "root"
    stage_deb(payload, staging, version, epoch)
    destination = out_dir / deb_artifact_name(version)
    command = [
        "dpkg-deb",
        "--root-owner-group",
        "-Zxz",
        "-z9",
        "--build",
        os.fspath(staging),
        os.fspath(destination),
    ]
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    completed = runner(command, env=env, check=False)
    if int(getattr(completed, "returncode", 1)) != 0:
        detail = getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or ""
        raise LinuxPackagingError(f"dpkg-deb failed: {detail}")
    if not destination.is_file():
        raise LinuxPackagingError(f"dpkg-deb did not create expected package: {destination}")
    return destination


def rpm_build_command(topdir: Path, spec: Path, epoch: int) -> list[str]:
    return [
        "rpmbuild",
        "-bb",
        "--define",
        f"_topdir {topdir}",
        "--define",
        "_buildhost autodev.invalid",
        "--define",
        "source_date_epoch_from_changelog 0",
        "--define",
        "use_source_date_epoch_as_buildtime 1",
        "--define",
        f"_buildtime {epoch}",
        "--define",
        "build_mtime_policy clamp_to_source_date_epoch",
        "--define",
        "_binary_payload w9.xzdio",
        "--define",
        "_build_id_links none",
        "--define",
        "__os_install_post %{nil}",
        os.fspath(spec),
    ]


def build_rpm(
    payload: Path,
    out_dir: Path,
    version: str,
    epoch: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    topdir = out_dir / ".native-build" / "rpm"
    if topdir.exists():
        shutil.rmtree(topdir)
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (topdir / name).mkdir(parents=True, exist_ok=True)
    package_version = native_packaging.package_version(version)
    source = topdir / "SOURCES" / f"autodev-{package_version}.tar.gz"
    write_payload_tar(payload, source, version, epoch)
    spec = topdir / "SPECS" / "autodev.spec"
    spec.write_text(rpm_spec(version), encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    completed = runner(rpm_build_command(topdir, spec, epoch), env=env, check=False)
    if int(getattr(completed, "returncode", 1)) != 0:
        detail = getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or ""
        raise LinuxPackagingError(f"rpmbuild failed: {detail}")
    built = topdir / "RPMS" / ARCH_RPM / rpm_artifact_name(version)
    if not built.is_file():
        matches = sorted((topdir / "RPMS").rglob("*.rpm"))
        if len(matches) != 1:
            raise LinuxPackagingError(f"rpmbuild did not create one expected RPM under {topdir / 'RPMS'}")
        built = matches[0]
    destination = out_dir / rpm_artifact_name(version)
    shutil.copy2(built, destination)
    return destination


def build_linux_packages(
    repo: Path,
    payload: Path,
    out_dir: Path,
    version: str,
    commit: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[Path, Path]:
    repo = repo.expanduser().resolve()
    commit_sha = package_release.resolve_commit(repo, commit)
    epoch = native_packaging.source_date_epoch(repo, commit_sha)
    deb = build_deb(payload, out_dir, version, epoch, runner=runner)
    rpm = build_rpm(payload, out_dir, version, epoch, runner=runner)
    return deb, rpm


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build native AutoDev Debian and RPM packages.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    try:
        deb, rpm = build_linux_packages(
            Path(args.repo),
            Path(args.payload),
            Path(args.out),
            args.version,
            args.commit,
        )
    except (LinuxPackagingError, native_packaging.NativePackagingError, package_release.ReleasePackagingError) as exc:
        parser.error(str(exc))
    print(deb)
    print(rpm)
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())