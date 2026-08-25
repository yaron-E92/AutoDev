from __future__ import annotations

import ctypes
import os
from pathlib import Path


ERROR_SUCCESS = 0
VT_FILETIME = 64
PID_CREATE_DTM = 12
PID_LASTSAVE_DTM = 13
WINDOWS_EPOCH_OFFSET_SECONDS = 11_644_473_600


class MsiNormalizationError(RuntimeError):
    pass


class FileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


def filetime_from_unix(epoch: int) -> FileTime:
    if epoch < 0:
        raise MsiNormalizationError(f"invalid SOURCE_DATE_EPOCH: {epoch}")
    ticks = (epoch + WINDOWS_EPOCH_OFFSET_SECONDS) * 10_000_000
    return FileTime(ticks & 0xFFFFFFFF, (ticks >> 32) & 0xFFFFFFFF)


def normalize_summary_timestamps(path: Path, epoch: int) -> None:
    """Make WiX 3 MSI creation/save timestamps depend only on source identity."""
    if os.name != "nt":
        raise MsiNormalizationError("MSI summary normalization requires Windows")
    path = path.expanduser().resolve()
    if not path.is_file():
        raise MsiNormalizationError(f"MSI does not exist: {path}")

    msi = ctypes.WinDLL("msi.dll")  # type: ignore[attr-defined]
    handle = ctypes.c_uint(0)
    get_summary = msi.MsiGetSummaryInformationW
    get_summary.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
    get_summary.restype = ctypes.c_uint
    set_property = msi.MsiSummaryInfoSetPropertyW
    set_property.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.POINTER(FileTime),
        ctypes.c_wchar_p,
    ]
    set_property.restype = ctypes.c_uint
    persist = msi.MsiSummaryInfoPersist
    persist.argtypes = [ctypes.c_uint]
    persist.restype = ctypes.c_uint
    close = msi.MsiCloseHandle
    close.argtypes = [ctypes.c_uint]
    close.restype = ctypes.c_uint

    result = get_summary(0, os.fspath(path), 2, ctypes.byref(handle))
    if result != ERROR_SUCCESS:
        raise MsiNormalizationError(f"MsiGetSummaryInformationW failed with code {result}")
    try:
        timestamp = filetime_from_unix(epoch)
        for property_id in (PID_CREATE_DTM, PID_LASTSAVE_DTM):
            result = set_property(
                handle.value,
                property_id,
                VT_FILETIME,
                0,
                ctypes.byref(timestamp),
                None,
            )
            if result != ERROR_SUCCESS:
                raise MsiNormalizationError(
                    f"MsiSummaryInfoSetPropertyW({property_id}) failed with code {result}"
                )
        result = persist(handle.value)
        if result != ERROR_SUCCESS:
            raise MsiNormalizationError(f"MsiSummaryInfoPersist failed with code {result}")
    finally:
        close(handle.value)
