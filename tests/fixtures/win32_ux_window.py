from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


if sys.platform != "win32":
    raise SystemExit("Win32 UX fixture requires Windows")


CLASS_NAME = "Static"
WINDOW_TITLE = "AutoDev Desktop Fixture"
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
SS_CENTER = 0x00000001
SW_SHOW = 5

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def main() -> int:
    # Use the operating system's built-in Static window class. The fixture only
    # needs a deterministic, visible native HWND; relying on a Python WNDPROC
    # callback would make this acceptance proof sensitive to CPython/ctypes
    # callback implementation details rather than AutoDev's capture behavior.
    instance = kernel32.GetModuleHandleW(None)
    hwnd = user32.CreateWindowExW(
        0,
        CLASS_NAME,
        WINDOW_TITLE,
        WS_OVERLAPPEDWINDOW | WS_VISIBLE | SS_CENTER,
        120,
        120,
        720,
        520,
        None,
        None,
        instance,
        None,
    )
    if not hwnd:
        return 3
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.UpdateWindow(hwnd)

    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
