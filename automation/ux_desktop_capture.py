from __future__ import annotations

import ctypes
import hashlib
import json
import re
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


MAX_ACTION_ARGS = 32
MAX_ARG_CHARS = 2048
MAX_WINDOW_PIXELS = 16_777_216
DEFAULT_WINDOW_TIMEOUT_MS = 15_000
MAX_WINDOW_TIMEOUT_MS = 120_000
_SAFE_PROCESS = re.compile(r"^[A-Za-z0-9_. -]{1,128}$")
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class DesktopCaptureError(RuntimeError):
    pass


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


@dataclass(frozen=True)
class WindowTarget:
    process_name: str
    title: str
    class_name: str = ""
    launch_args: tuple[str, ...] = ()
    timeout_ms: int = DEFAULT_WINDOW_TIMEOUT_MS
    expected_viewport: str = ""


@dataclass(frozen=True)
class DesktopProviderConfig:
    platform: str
    application_command: tuple[str, ...]
    targets: dict[str, WindowTarget]


@dataclass(frozen=True)
class WindowMatch:
    hwnd: int
    pid: int
    process_name: str
    title: str
    class_name: str
    width: int
    height: int
    process_started_100ns: int

    @property
    def runtime_identity(self) -> str:
        payload = {
            "platform": "windows",
            "pid": self.pid,
            "hwnd": self.hwnd,
            "process_name": _normalized_process_name(self.process_name),
            "process_started_100ns": self.process_started_100ns,
            "title": self.title,
            "class_name": self.class_name,
            "width": self.width,
            "height": self.height,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"windows-window:{digest}"


@dataclass(frozen=True)
class DesktopCaptureResult:
    platform: str
    viewport: str
    configured_identity: str
    runtime_identity: str


def parse_config(value: dict[str, object], target_ids: set[str]) -> DesktopProviderConfig:
    raw = value.get("desktop", {})
    if not isinstance(raw, dict):
        raise DesktopCaptureError("desktop capture provider configuration must be an object")
    platform = str(raw.get("platform", "windows") or "windows").strip().casefold()
    if platform not in {"windows", "mobile"}:
        raise DesktopCaptureError("desktop.platform must be 'windows' or reserved 'mobile'")
    if platform == "mobile":
        raise DesktopCaptureError(
            "mobile UX capture is not yet supported by the first-party provider; no ambient fallback is permitted"
        )

    application = raw.get("application", {})
    if application in (None, ""):
        application = {}
    if not isinstance(application, dict):
        raise DesktopCaptureError("desktop.application must be a JSON object")
    application_command = _argv(
        application.get("command"),
        field="desktop.application.command",
        allow_empty=True,
    )

    raw_targets = value.get("targets", {})
    if not isinstance(raw_targets, dict):
        raise DesktopCaptureError("desktop capture targets must be a JSON object")
    targets: dict[str, WindowTarget] = {}
    for target_id in sorted(target_ids):
        raw_target = raw_targets.get(target_id)
        if raw_target is None:
            _, _, source_id = target_id.partition(":")
            raw_target = raw_targets.get(source_id)
        if not isinstance(raw_target, dict):
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} is missing provider configuration"
            )
        raw_window = raw_target.get("window", {})
        if not isinstance(raw_window, dict):
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} window must be a JSON object"
            )
        process_name = str(raw_window.get("process_name", "") or "").strip()
        title = str(raw_window.get("title", "") or "").strip()
        class_name = str(raw_window.get("class_name", "") or "").strip()
        if not process_name or not _SAFE_PROCESS.fullmatch(process_name):
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} requires a bounded explicit window.process_name"
            )
        if not title or len(title) > 512:
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} requires an exact bounded window.title"
            )
        if len(class_name) > 256:
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} window.class_name is too long"
            )
        launch_args = _argv(
            raw_target.get("launch_args"),
            field=f"desktop target {target_id} launch_args",
            allow_empty=True,
        )
        if launch_args and not application_command:
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} launch_args require desktop.application.command"
            )
        timeout_ms = _bounded_int(
            raw_window.get("timeout_ms", DEFAULT_WINDOW_TIMEOUT_MS),
            field=f"desktop target {target_id} window.timeout_ms",
            minimum=100,
            maximum=MAX_WINDOW_TIMEOUT_MS,
        )
        expected_viewport = str(raw_target.get("viewport", "") or "").strip()
        if expected_viewport and not _viewport(expected_viewport):
            raise DesktopCaptureError(
                f"desktop capture target {target_id!r} viewport must use WIDTHxHEIGHT"
            )
        targets[target_id] = WindowTarget(
            process_name=process_name,
            title=title,
            class_name=class_name,
            launch_args=launch_args,
            timeout_ms=timeout_ms,
            expected_viewport=expected_viewport,
        )

    return DesktopProviderConfig(
        platform=platform,
        application_command=application_command,
        targets=targets,
    )


def configured_target_identity(config: DesktopProviderConfig, target_id: str) -> str:
    target = config.targets.get(target_id)
    if target is None:
        return ""
    payload = {
        "platform": config.platform,
        "application_command": list(config.application_command),
        "process_name": _normalized_process_name(target.process_name),
        "title": target.title,
        "class_name": target.class_name,
        "launch_args": list(target.launch_args),
        "timeout_ms": target.timeout_ms,
        "expected_viewport": target.expected_viewport,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"windows-window-config:{digest}"


def capture(
    repo: Path,
    current: Path,
    config: DesktopProviderConfig,
    target_id: str,
    output: Path,
    *,
    timeout_seconds: int,
    popen: Callable[..., object] = subprocess.Popen,
) -> DesktopCaptureResult:
    del current
    if config.platform != "windows":
        raise DesktopCaptureError(f"desktop capture platform {config.platform!r} is unsupported")
    if sys.platform != "win32":
        raise DesktopCaptureError(
            "first-party desktop UX capture currently requires Windows; ambient desktop fallback is forbidden"
        )
    target = config.targets.get(target_id)
    if target is None:
        raise DesktopCaptureError(f"desktop capture target is not declared: {target_id}")

    process = None
    command = [*config.application_command, *target.launch_args]
    if command:
        try:
            process = popen(
                command,
                cwd=repo,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            raise DesktopCaptureError(
                f"desktop application could not be launched for {target_id}: {exc}"
            ) from exc

    deadline = time.monotonic() + min(float(timeout_seconds), target.timeout_ms / 1000.0)
    try:
        match = _wait_for_unique_window(target, deadline)
        viewport = f"{match.width}x{match.height}"
        if target.expected_viewport and target.expected_viewport != viewport:
            raise DesktopCaptureError(
                f"desktop target {target_id} window viewport is {viewport}, expected {target.expected_viewport}"
            )
        _capture_window_png(match, output)
        return DesktopCaptureResult(
            platform="windows-desktop",
            viewport=viewport,
            configured_identity=configured_target_identity(config, target_id),
            runtime_identity=match.runtime_identity,
        )
    finally:
        if process is not None:
            _stop_launched_process(process)


def _wait_for_unique_window(target: WindowTarget, deadline: float) -> WindowMatch:
    while True:
        matches = _matching_windows(target)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise DesktopCaptureError(
                "desktop capture target is ambiguous: multiple visible non-minimized windows match the explicit process/title/class identity"
            )
        if time.monotonic() >= deadline:
            raise DesktopCaptureError(
                "desktop capture target window was not found before timeout "
                f"(process={target.process_name!r}, title={target.title!r})"
            )
        time.sleep(0.1)


def _matching_windows(target: WindowTarget) -> list[WindowMatch]:
    api = _Win32()
    matches: list[WindowMatch] = []
    callback_errors: list[DesktopCaptureError] = []

    @api.WNDENUMPROC
    def callback(hwnd, _lparam):
        try:
            if not api.user32.IsWindowVisible(hwnd) or api.user32.IsIconic(hwnd):
                return True
            title = api.window_title(hwnd)
            if title != target.title:
                return True
            class_name = api.window_class(hwnd)
            if target.class_name and class_name != target.class_name:
                return True
            pid = api.window_pid(hwnd)
            process_name, started = api.process_identity(pid)
            if not process_name or started <= 0:
                return True
            if _normalized_process_name(process_name) != _normalized_process_name(target.process_name):
                return True
            left, top, right, bottom = api.window_rect(hwnd)
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0 or width * height > MAX_WINDOW_PIXELS:
                return True
            matches.append(
                WindowMatch(
                    hwnd=int(hwnd),
                    pid=pid,
                    process_name=process_name,
                    title=title,
                    class_name=class_name,
                    width=width,
                    height=height,
                    process_started_100ns=started,
                )
            )
            return True
        except DesktopCaptureError as exc:
            callback_errors.append(exc)
            return False

    enum_ok = api.user32.EnumWindows(callback, 0)
    if callback_errors:
        raise callback_errors[0]
    if not enum_ok:
        raise DesktopCaptureError(
            f"EnumWindows failed with Windows error {ctypes.get_last_error()}"
        )
    return matches


def _capture_window_png(match: WindowMatch, output: Path) -> None:
    api = _Win32()
    screen_dc = api.user32.GetDC(None)
    if not screen_dc:
        raise DesktopCaptureError("GetDC failed while preparing explicit window capture")
    memory_dc = api.gdi32.CreateCompatibleDC(screen_dc)
    if not memory_dc:
        api.user32.ReleaseDC(None, screen_dc)
        raise DesktopCaptureError("CreateCompatibleDC failed for explicit window capture")

    bits = ctypes.c_void_p()
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = match.width
    info.bmiHeader.biHeight = -match.height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    bitmap = api.gdi32.CreateDIBSection(
        memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
    )
    if not bitmap or not bits.value:
        api.gdi32.DeleteDC(memory_dc)
        api.user32.ReleaseDC(None, screen_dc)
        raise DesktopCaptureError("CreateDIBSection failed for explicit window capture")

    old = api.gdi32.SelectObject(memory_dc, bitmap)
    try:
        # PW_RENDERFULLCONTENT is useful for modern composited windows but is not
        # uniformly supported. Retrying with the standard PrintWindow flag stays
        # scoped to the exact same proven HWND and never broadens capture to the
        # ambient desktop.
        if not api.user32.PrintWindow(match.hwnd, memory_dc, 2):
            if not api.user32.PrintWindow(match.hwnd, memory_dc, 0):
                raise DesktopCaptureError(
                    "PrintWindow failed for the explicit target HWND; AutoDev will not fall back to ambient screen capture"
                )
        raw = ctypes.string_at(bits, match.width * match.height * 4)
        _write_bgra_png(output, match.width, match.height, raw)
    finally:
        if old:
            api.gdi32.SelectObject(memory_dc, old)
        api.gdi32.DeleteObject(bitmap)
        api.gdi32.DeleteDC(memory_dc)
        api.user32.ReleaseDC(None, screen_dc)


def _write_bgra_png(path: Path, width: int, height: int, raw: bytes) -> None:
    stride = width * 4
    rows = bytearray()
    for row in range(height):
        rows.append(0)
        start = row * stride
        for offset in range(start, start + stride, 4):
            blue, green, red, alpha = raw[offset : offset + 4]
            rows.extend((red, green, blue, alpha or 255))
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _wait_for_process_exit(process: object, *, timeout: float = 5.0) -> None:
    wait = getattr(process, "wait", None)
    if not callable(wait):
        return
    try:
        wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _stop_launched_process(process: object) -> None:
    pid = int(getattr(process, "pid", 0) or 0)
    if pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            _wait_for_process_exit(process)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        try:
            terminate()
        except OSError:
            pass
    _wait_for_process_exit(process)


def _argv(value: object, *, field: str, allow_empty: bool) -> tuple[str, ...]:
    if value in (None, "") and allow_empty:
        return ()
    if not isinstance(value, list) or not value:
        raise DesktopCaptureError(f"{field} must be a JSON string array")
    if len(value) > MAX_ACTION_ARGS:
        raise DesktopCaptureError(f"{field} contains too many arguments")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > MAX_ARG_CHARS or "\x00" in item:
            raise DesktopCaptureError(f"{field} contains an invalid argument")
        result.append(item)
    return tuple(result)


def _viewport(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([1-9][0-9]{0,4})x([1-9][0-9]{0,4})", value)
    if match is None:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width * height > MAX_WINDOW_PIXELS:
        return None
    return width, height


def _bounded_int(value: object, *, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DesktopCaptureError(f"{field} must be an integer") from exc
    if not minimum <= number <= maximum:
        raise DesktopCaptureError(f"{field} must be between {minimum} and {maximum}")
    return number


def _normalized_process_name(value: str) -> str:
    name = Path(str(value or "").strip()).name.casefold()
    return name[:-4] if name.endswith(".exe") else name


class _Win32:
    WNDENUMPROC = _WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise DesktopCaptureError("Win32 APIs are unavailable on this platform")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure()

    def _configure(self) -> None:
        self.user32.EnumWindows.argtypes = [self.WNDENUMPROC, ctypes.c_void_p]
        self.user32.EnumWindows.restype = ctypes.c_bool
        self.user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        self.user32.IsWindowVisible.restype = ctypes.c_bool
        self.user32.IsIconic.argtypes = [ctypes.c_void_p]
        self.user32.IsIconic.restype = ctypes.c_bool
        self.user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        self.user32.GetClassNameW.restype = ctypes.c_int
        self.user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        self.user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
        self.user32.GetWindowRect.restype = ctypes.c_bool
        self.user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        self.user32.PrintWindow.restype = ctypes.c_bool
        self.user32.GetDC.argtypes = [ctypes.c_void_p]
        self.user32.GetDC.restype = ctypes.c_void_p
        self.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self.gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self.gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.SelectObject.restype = ctypes.c_void_p
        self.gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteObject.restype = ctypes.c_bool
        self.gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteDC.restype = ctypes.c_bool
        self.kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
        self.kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = ctypes.c_bool
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = ctypes.c_bool

    def window_title(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def window_class(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(257)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def window_pid(self, hwnd: int) -> int:
        pid = ctypes.c_uint32()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = _RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise DesktopCaptureError("GetWindowRect failed for explicit window target")
        return rect.left, rect.top, rect.right, rect.bottom

    def process_identity(self, pid: int) -> tuple[str, int]:
        process = self.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return "", 0
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_uint32(len(buffer))
            if not self.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return "", 0
            created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            if not self.kernel32.GetProcessTimes(
                process,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return "", 0
            started = (int(created.high) << 32) | int(created.low)
            return Path(buffer.value).name, started
        finally:
            self.kernel32.CloseHandle(process)
