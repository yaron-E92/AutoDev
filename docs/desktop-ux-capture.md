# First-party desktop UX capture

AutoDev can capture an explicitly declared Windows application window as implementation evidence for the existing multimodal UX verification flow. Configure `.autodev/ux-capture.json` with `provider: "desktop"`.

The first-party desktop provider is intentionally narrower than a general desktop automation tool. It currently supports Windows top-level application windows only. It does not take full-screen screenshots, inspect unrelated windows, infer target applications, or fall back to ambient desktop pixels.

## Example

```json
{
  "schema": "autodev.ux.capture/v1",
  "provider": "desktop",
  "timeout_seconds": 30,
  "desktop": {
    "platform": "windows",
    "application": {
      "command": ["dotnet", "run", "--project", "src/App/App.csproj"]
    }
  },
  "targets": {
    "state:signed-in": {
      "source_kind": "state",
      "source_id": "signed-in",
      "output": "signed-in.png",
      "reference": "screen:home",
      "viewport": "1280x800",
      "launch_args": ["--autodev-state", "signed-in"],
      "window": {
        "process_name": "App.exe",
        "title": "App - Signed in",
        "class_name": "AppWindow",
        "timeout_ms": 15000
      }
    }
  }
}
```

`desktop.application.command` is optional. When present, AutoDev launches it before looking for the declared window and terminates the launched process tree after capture. Per-target `launch_args` are appended to that command and provide a deterministic application-owned state hook. They are data passed to the declared application; the desktop provider does not turn model prose into desktop actions.

This pattern works well for Windows targets produced by MAUI, WPF, WinForms, WinUI, and similar frameworks when the application can expose a stable test/startup state and a deterministic top-level window identity.

## Window identity

Every desktop target must declare:

- `window.process_name`: the exact executable process name, with `.exe` normalization supported;
- `window.title`: the exact top-level window title;
- optionally `window.class_name`: an exact Win32 window-class match;
- optionally `window.timeout_ms`: how long AutoDev may wait for the target window;
- optionally `viewport`: the required final outer-window dimensions as `WIDTHxHEIGHT`.

AutoDev enumerates only visible, non-minimized top-level windows and accepts the capture only when exactly one window matches the declared process/title/class identity. Zero matches eventually fail. Multiple matches fail immediately as ambiguous. A minimized window is not promoted or captured implicitly.

The process lookup also records the matched PID and process-start timestamp so a stale PID reuse cannot masquerade as the same runtime window.

## Capture boundary

After AutoDev proves the unique target HWND, it captures that HWND with the Win32 `PrintWindow` API into an isolated bitmap and encodes the result as PNG. The provider deliberately has no `BitBlt`, full-screen, monitor, clipboard, arbitrary-window, or ambient desktop fallback.

If the declared window cannot be uniquely identified or `PrintWindow` cannot capture it, the evidence is unavailable. The multimodal verifier therefore reports the selected visual authority as `unverifiable`; it does not silently pass the UX check or broaden the capture scope.

This is the key privacy boundary: repository configuration may identify one intended application window, but it cannot ask the first-party provider to scrape the user's desktop, notifications, other applications, or unrelated OS UI.

## Evidence and invalidation

Desktop captures use the same `CapturedImage` and multimodal comparison path as the `command` and `browser` providers. In addition to the image hash, logical source ID, platform, and viewport, desktop evidence records two identities:

- **configured identity** — a hash of the declared platform, application command, process/title/class selector, target launch arguments, timeout, and expected viewport;
- **runtime identity** — a hash of the actual PID, HWND, process-start timestamp, process name, exact title/class, and captured dimensions.

The configured identity is recomputed during resume. Changing the declared target window or startup state invalidates an earlier visual pass. The runtime identity remains bound to the historical screenshot, so the evidence still records exactly which concrete window instance produced those bytes without requiring that application to remain running during a later resume.

## Mobile and other desktop platforms

`desktop.platform: "mobile"` is reserved for a future emulator/device provider under the same capture contract, but it is intentionally unsupported today. Selecting it fails closed. AutoDev does not substitute a host-screen screenshot for a missing mobile capture path.

The first-party desktop provider also does not currently support macOS or Linux windows. Repositories on unsupported platforms can still use the existing `command` provider if they supply their own deterministic app-scoped capture executable; in that case the repository owns that executable's platform-specific capture trust boundary.

Adding a future macOS, Linux, Android-emulator, or iOS-simulator backend must preserve the same invariant: explicit application/device identity, bounded state acquisition, no unrelated ambient capture, stable evidence identities, and `unverifiable` behavior when those guarantees cannot be established.

## Relationship to browser capture and verifier privacy

Desktop capture is only the implementation-image acquisition step. It does not change which pinned UX references are selected, which model/runtime may receive images, or the privacy/retention authorization applied to the multimodal verifier. Those remain part of the existing #268 verification contract.

For web applications, prefer the first-party browser provider described in `docs/browser-ux-capture.md`; it provides stronger route/origin/action semantics than treating a browser as a generic desktop window.
