from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


_DIGEST_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_REPOSITORY_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$")


class OCIReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class OCIReference:
    registry: str
    repository: str
    tag: str = ""
    digest: str = ""

    @property
    def immutable(self) -> bool:
        return bool(self.digest)

    @property
    def oras_target(self) -> str:
        suffix = f"@{self.digest}" if self.digest else f":{self.tag}"
        return f"{self.registry}/{self.repository}{suffix}"

    @property
    def source_reference(self) -> str:
        return "oci://" + self.oras_target

    def with_digest(self, digest: str) -> "OCIReference":
        normalized = normalize_digest(digest)
        return OCIReference(
            registry=self.registry,
            repository=self.repository,
            digest=normalized,
        )

    def immutable_reference(self, digest: str | None = None) -> str:
        return self.with_digest(digest or self.digest).source_reference


def normalize_digest(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not _DIGEST_RE.fullmatch(text):
        raise OCIReferenceError(
            "OCI artifact digest must be sha256 followed by 64 hexadecimal characters"
        )
    return text


def parse_oci_reference(value: object) -> OCIReference:
    text = str(value or "").strip()
    if not text.startswith("oci://"):
        raise OCIReferenceError("OCI UX artifact references must start with oci://")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise OCIReferenceError(f"invalid OCI artifact reference: {text!r}") from exc
    if parts.scheme != "oci" or not parts.netloc:
        raise OCIReferenceError(f"invalid OCI artifact reference: {text!r}")
    if parts.username or parts.password:
        raise OCIReferenceError("OCI artifact references must not contain credentials")
    if parts.query or parts.fragment:
        raise OCIReferenceError("OCI artifact references must not contain query or fragment data")

    registry = parts.netloc.strip()
    if not registry or any(char.isspace() for char in registry):
        raise OCIReferenceError("OCI artifact registry must be non-empty")
    raw = parts.path.lstrip("/")
    if not raw:
        raise OCIReferenceError("OCI artifact repository path must be non-empty")

    if "@" in raw:
        repository, digest = raw.rsplit("@", 1)
        if ":" in repository.rsplit("/", 1)[-1]:
            raise OCIReferenceError("OCI artifact references must use either a tag or digest, not both")
        tag = ""
        digest = normalize_digest(digest)
    else:
        slash = raw.rfind("/")
        colon = raw.rfind(":")
        if colon <= slash:
            raise OCIReferenceError(
                "OCI artifact reference must include an explicit tag or immutable digest"
            )
        repository = raw[:colon]
        tag = raw[colon + 1 :]
        digest = ""
        if not _TAG_RE.fullmatch(tag):
            raise OCIReferenceError(f"invalid OCI artifact tag: {tag!r}")

    repository = repository.strip("/")
    if not repository:
        raise OCIReferenceError("OCI artifact repository path must be non-empty")
    if repository.casefold() != repository:
        raise OCIReferenceError("OCI artifact repository path must be lowercase")
    segments = repository.split("/")
    if any(
        not segment
        or segment in {".", ".."}
        or not _REPOSITORY_SEGMENT_RE.fullmatch(segment)
        for segment in segments
    ):
        raise OCIReferenceError(f"invalid OCI artifact repository path: {repository!r}")

    return OCIReference(
        registry=registry,
        repository=repository,
        tag=tag,
        digest=digest,
    )
