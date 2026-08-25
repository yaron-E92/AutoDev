from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


SCHEMA_VERSION = 2
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
COMMON_ROOTS = (
    "automation",
    "area_reader",
    "integrations",
    "promptTemplates",
    "agentFiles",
    "examples",
    "docs",
    "README.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "codex-profiles.json",
)
PLATFORM_ROOTS = {
    "windows": ("windows",),
}


class ReleasePackagingError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git failed").strip()
        raise ReleasePackagingError(detail)
    return completed.stdout.strip()


def run_git_bytes(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or b"git failed").decode(
            "utf-8", errors="replace"
        ).strip()
        raise ReleasePackagingError(detail)
    return completed.stdout


def resolve_commit(repo: Path, requested: str = "") -> str:
    head = run_git(repo, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        raise ReleasePackagingError(f"HEAD is not a full Git SHA: {head!r}")
    if requested:
        resolved = run_git(repo, "rev-parse", requested)
        if resolved != head:
            raise ReleasePackagingError(
                f"requested release commit {resolved} does not match checked-out HEAD {head}"
            )
    return head.lower()


def validate_version(version: str) -> str:
    value = version.strip()
    if not VERSION_RE.fullmatch(value):
        raise ReleasePackagingError(
            "release version must be a v-prefixed semantic version such as v1.2.3"
        )
    return value


def native_artifact_names(version: str) -> dict[str, str]:
    plain = validate_version(version).removeprefix("v")
    return {
        "windows-msi": f"AutoDev-{plain}-Setup.msi",
        "debian-amd64": f"autodev_{plain}_amd64.deb",
        "rpm-x86_64": f"autodev-{plain}-1.x86_64.rpm",
    }


def commit_files(repo: Path, commit: str, roots: tuple[str, ...]) -> list[str]:
    raw = run_git_bytes(
        repo,
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        commit,
        "--",
        *roots,
    )
    values = [
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    ]
    return sorted(dict.fromkeys(values))


def blob_bytes(repo: Path, commit: str, relative: str) -> bytes:
    return run_git_bytes(repo, "show", f"{commit}:{relative}")


def source_manifest(repo: Path, commit: str, files: list[str]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for relative in files:
        data = blob_bytes(repo, commit, relative)
        result.append(
            {
                "path": relative.replace("\\", "/"),
                "size": len(data),
                "sha256": sha256_bytes(data),
            }
        )
    return result


def write_deterministic_zip(
    repo: Path,
    commit: str,
    destination: Path,
    files: list[str],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative in files:
            data = blob_bytes(repo, commit, relative)
            info = zipfile.ZipInfo(relative.replace("\\", "/"), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, data)


def bundle_name(version: str, kind: str) -> str:
    return f"autodev-{version}-{kind}.zip"


def collect_native_installers(
    native_dir: Path,
    out_dir: Path,
    version: str,
) -> tuple[dict[str, object], list[Path]]:
    native_dir = native_dir.expanduser().resolve()
    values: dict[str, object] = {}
    subjects: list[Path] = []
    for kind, name in native_artifact_names(version).items():
        source = native_dir / name
        if not source.is_file():
            raise ReleasePackagingError(f"missing native installer {kind}: {source}")
        destination = out_dir / name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        values[kind] = {
            "artifact": destination.name,
            "sha256": sha256_file(destination),
            "size": destination.stat().st_size,
        }
        subjects.append(destination)
    return values, subjects


def build_release(
    repo: Path,
    out_dir: Path,
    version: str,
    commit: str = "",
    *,
    native_dir: Path | None = None,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    version = validate_version(version)
    commit_sha = resolve_commit(repo, commit)
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle_roots = {"common": COMMON_ROOTS, **PLATFORM_ROOTS}
    bundles: dict[str, object] = {}
    for kind, roots in bundle_roots.items():
        files = commit_files(repo, commit_sha, tuple(roots))
        if not files:
            raise ReleasePackagingError(f"release bundle {kind} contains no tracked files")
        archive_path = out_dir / bundle_name(version, kind)
        write_deterministic_zip(repo, commit_sha, archive_path, files)
        bundles[kind] = {
            "archive": archive_path.name,
            "sha256": sha256_file(archive_path),
            "size": archive_path.stat().st_size,
            "files": source_manifest(repo, commit_sha, files),
        }

    native_installers: dict[str, object] = {}
    native_subjects: list[Path] = []
    if native_dir is not None:
        native_installers, native_subjects = collect_native_installers(native_dir, out_dir, version)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "commit_sha": commit_sha,
        "archive_format": "zip-stored",
        "bundles": bundles,
        "native_installers": native_installers,
    }
    manifest_path = out_dir / "autodev-release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    subjects = [out_dir / str(bundle["archive"]) for bundle in bundles.values()]
    subjects.extend(native_subjects)
    subjects.append(manifest_path)
    checksums = "".join(
        f"{sha256_file(path)}  {path.name}\n"
        for path in sorted(subjects, key=lambda value: value.name)
    )
    (out_dir / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic AutoDev release bundles from the exact checked-out commit."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--native-dir", default="")
    args = parser.parse_args(argv)
    try:
        manifest = build_release(
            Path(args.repo),
            Path(args.out),
            args.version,
            args.commit,
            native_dir=Path(args.native_dir) if args.native_dir else None,
        )
    except ReleasePackagingError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "version": manifest["version"],
                "commit_sha": manifest["commit_sha"],
                "bundles": {
                    key: value["sha256"]
                    for key, value in manifest["bundles"].items()
                },
                "native_installers": {
                    key: value["sha256"]
                    for key, value in manifest["native_installers"].items()
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
