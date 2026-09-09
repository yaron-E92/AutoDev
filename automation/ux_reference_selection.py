from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automation import ux_capture


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class UXReferenceSelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceSpec:
    source_kind: str
    source_id: str
    reference_target_id: str
    relative_path: str

    @property
    def target_id(self) -> str:
        return f"{self.source_kind}:{self.source_id}"


def selected_specs(
    repo: Path,
    ux_context: dict[str, object],
    manifest: object,
) -> tuple[ReferenceSpec, ...]:
    manifest_maps = {
        "screen": dict(getattr(manifest, "screens", {}) or {}),
        "state": dict(getattr(manifest, "states", {}) or {}),
        "journey": dict(getattr(manifest, "journey_files", {}) or {}),
    }
    selections: list[ReferenceSpec] = []
    capture_config: ux_capture.CaptureConfig | None = None
    capture_config_loaded = False

    for kind, key in (
        ("screen", "screens"),
        ("state", "states"),
        ("journey", "journeys"),
    ):
        values = ux_context.get(key, [])
        if not isinstance(values, list):
            continue
        mapping = manifest_maps[kind]
        for raw_id in values:
            source_id = str(raw_id or "").strip()
            target_id = f"{kind}:{source_id}"
            relative = str(mapping.get(source_id, "") or "").strip()
            reference_target_id = target_id

            if not _is_image(relative):
                if not capture_config_loaded:
                    try:
                        capture_config = ux_capture.load_config(repo)
                    except ux_capture.UXCaptureError as exc:
                        raise UXReferenceSelectionError(
                            "cannot resolve indirect UX reference from invalid capture configuration: "
                            f"{exc}"
                        ) from exc
                    capture_config_loaded = True
                mapped = (
                    ux_capture.capture_reference_target(capture_config, target_id)
                    if capture_config is not None
                    else ""
                )
                if not mapped:
                    continue
                reference_kind, separator, reference_id = mapped.partition(":")
                reference_kind = reference_kind.strip().casefold()
                reference_id = reference_id.strip()
                reference_mapping = manifest_maps.get(reference_kind)
                if not separator or reference_mapping is None:
                    raise UXReferenceSelectionError(
                        f"UX capture reference {mapped!r} is not a supported screen/state/journey target"
                    )
                relative = str(reference_mapping.get(reference_id, "") or "").strip()
                if not relative:
                    raise UXReferenceSelectionError(
                        f"UX capture reference {mapped!r} is not present in the pinned UX artifact"
                    )
                if not _is_image(relative):
                    raise UXReferenceSelectionError(
                        f"UX capture reference {mapped!r} does not resolve to a supported reference image"
                    )
                reference_target_id = mapped

            selections.append(
                ReferenceSpec(
                    source_kind=kind,
                    source_id=source_id,
                    reference_target_id=reference_target_id,
                    relative_path=relative,
                )
            )

    return tuple(
        sorted(
            set(selections),
            key=lambda item: (
                item.source_kind,
                item.source_id,
                item.reference_target_id,
                item.relative_path,
            ),
        )
    )


def _is_image(relative_path: str) -> bool:
    return bool(relative_path) and Path(relative_path).suffix.casefold() in _IMAGE_SUFFIXES
