from __future__ import annotations

import base64
import errno
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote, urlsplit

from automation import ux_browser_cdp


DEFAULT_VIEWPORT = "1280x720"
DEFAULT_ACTION_TIMEOUT_MS = 10_000
MAX_ACTIONS = 32
MAX_ALLOWED_ORIGINS = 16
PROFILE_CLEANUP_ATTEMPTS = 10
PROFILE_CLEANUP_RETRY_SECONDS = 0.1
_SAFE_BROWSER_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_VIEWPORT = re.compile(r"^(?P<width>[0-9]{2,4})x(?P<height>[0-9]{2,4})(?:@(?P<scale>[0-9](?:\.[0-9]+)?))?$")


class BrowserCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserAction:
    kind: str
    selector: str = ""
    value: str = ""
    timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS


@dataclass(frozen=True)
class BrowserTargetConfig:
    route: str
    viewport: str
    ready_selector: str
    actions: tuple[BrowserAction, ...]


@dataclass(frozen=True)
class BrowserProviderConfig:
    origin: str
    allowed_origins: tuple[str, ...]
    application_command: tuple[str, ...]
    ready_path: str
    executable: str
    targets: dict[str, BrowserTargetConfig]


def parse_config(value: dict[str, object], target_ids: set[str]) -> BrowserProviderConfig:
    raw_browser = value.get("browser", {})
    if not isinstance(raw_browser, dict):
        raise BrowserCaptureError("browser UX capture configuration must be a JSON object")
    raw_application = raw_browser.get("application", {})
    if not isinstance(raw_application, dict):
        raise BrowserCaptureError("browser.application must be a JSON object")
    origin = _normalize_origin(raw_application.get("origin"))
    ready_path = str(raw_application.get("ready_path", "/") or "/").strip()
    _validate_route(ready_path, field="browser.application.ready_path")
    command = _string_array(
        raw_application.get("command", []),
        field="browser.application.command",
        allow_empty=True,
        max_items=32,
    )
    executable = str(raw_browser.get("executable", "") or "").strip()
    if len(executable) > 512 or "\x00" in executable:
        raise BrowserCaptureError("browser.executable is invalid")

    raw_allowed = raw_browser.get("allowed_origins", [origin])
    if not isinstance(raw_allowed, list) or not raw_allowed or len(raw_allowed) > MAX_ALLOWED_ORIGINS:
        raise BrowserCaptureError(
            f"browser.allowed_origins must contain 1-{MAX_ALLOWED_ORIGINS} explicit origins"
        )
    allowed = tuple(dict.fromkeys(_normalize_origin(item) for item in raw_allowed))
    if origin not in allowed:
        raise BrowserCaptureError("browser.application.origin must be present in browser.allowed_origins")

    raw_targets = value.get("targets", {})
    if not isinstance(raw_targets, dict):
        raise BrowserCaptureError("browser UX capture targets must be a JSON object")
    browser_targets: dict[str, BrowserTargetConfig] = {}
    for raw_key, raw_target in raw_targets.items():
        if not isinstance(raw_target, dict):
            continue
        source_kind = str(raw_target.get("source_kind", "") or "").strip().casefold()
        source_id = str(raw_target.get("source_id", "") or "").strip()
        target_id = f"{source_kind}:{source_id}"
        if target_id not in target_ids:
            continue
        route = str(raw_target.get("route", "/") or "/").strip()
        _validate_route(route, field=f"targets.{raw_key}.route")
        resolved = _target_url(origin, route)
        if _origin(resolved) not in allowed:
            raise BrowserCaptureError(
                f"browser target {target_id} resolves outside browser.allowed_origins"
            )
        viewport = str(raw_target.get("viewport", DEFAULT_VIEWPORT) or DEFAULT_VIEWPORT).strip()
        _parse_viewport(viewport)
        ready_selector = str(raw_target.get("ready_selector", "") or "").strip()
        _validate_selector(ready_selector, field=f"targets.{raw_key}.ready_selector", allow_empty=True)
        actions = _parse_actions(raw_target.get("actions", []), target_id=target_id)
        browser_targets[target_id] = BrowserTargetConfig(
            route=route,
            viewport=viewport,
            ready_selector=ready_selector,
            actions=actions,
        )
    if set(browser_targets) != target_ids:
        missing = sorted(target_ids - set(browser_targets))
        raise BrowserCaptureError(
            "browser capture configuration is missing deterministic target replay for: "
            + ", ".join(missing)
        )
    return BrowserProviderConfig(
        origin=origin,
        allowed_origins=allowed,
        application_command=command,
        ready_path=ready_path,
        executable=executable,
        targets=browser_targets,
    )


def capture(
    repo: Path,
    current: Path,
    provider: BrowserProviderConfig,
    target_id: str,
    output: Path,
    *,
    timeout_seconds: int,
    popen: Callable[..., object] = subprocess.Popen,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, str]:
    target = provider.targets.get(target_id)
    if target is None:
        raise BrowserCaptureError(f"browser provider has no declared target {target_id}")
    if output.suffix.casefold() != ".png":
        raise BrowserCaptureError("first-party browser capture currently emits PNG screenshots only")
    repo = repo.expanduser().resolve()
    current = current.expanduser().resolve()
    application = None
    browser = None
    session = None
    profile = None
    deadline = time.monotonic() + timeout_seconds
    try:
        if provider.application_command:
            application = _launch_application(repo, provider.application_command, popen=popen)
        _wait_for_application(provider, application, deadline=deadline)
        executable = _resolve_browser_executable(provider.executable, which=which)
        port = _free_local_port()
        profile_root = current / "ux-browser-profiles"
        profile_root.mkdir(parents=True, exist_ok=True)
        profile = tempfile.TemporaryDirectory(prefix="capture-", dir=profile_root)
        browser = _launch_browser(
            executable,
            port=port,
            profile=Path(profile.name),
            popen=popen,
        )
        version = _wait_for_devtools(port, browser, deadline=deadline)
        page = _new_page(port, deadline=deadline)
        websocket_url = str(page.get("webSocketDebuggerUrl", "") or "")
        if not websocket_url:
            raise BrowserCaptureError("Chrome DevTools did not return a page websocket endpoint")
        remaining = max(1.0, deadline - time.monotonic())
        session = ux_browser_cdp.CDPSession(
            ux_browser_cdp.WebSocketClient(
                websocket_url,
                timeout_seconds=min(10.0, remaining),
            )
        )
        _capture_page(
            session,
            provider,
            target,
            output,
            deadline=deadline,
        )
        product = str(version.get("Browser", "browser") or "browser")
        platform = ("browser:" + product)[:64]
        width, height, scale = _parse_viewport(target.viewport)
        viewport_identity = f"{width}x{height}@{scale:g}"
        return platform, viewport_identity[:64]
    except ux_browser_cdp.BrowserCDPError as exc:
        raise BrowserCaptureError(str(exc)) from exc
    finally:
        if session is not None:
            session.close()
        _stop_process(browser)
        if profile is not None:
            _cleanup_profile(profile)
        _stop_process(application)


def _capture_page(
    session: ux_browser_cdp.CDPSession,
    provider: BrowserProviderConfig,
    target: BrowserTargetConfig,
    output: Path,
    *,
    deadline: float,
) -> None:
    allowed = set(provider.allowed_origins)

    def events(message: dict[str, object], active: ux_browser_cdp.CDPSession) -> None:
        if message.get("method") != "Fetch.requestPaused":
            return
        params = message.get("params", {})
        if not isinstance(params, dict):
            return
        request_id = str(params.get("requestId", "") or "")
        request = params.get("request", {})
        url = str(request.get("url", "") or "") if isinstance(request, dict) else ""
        if not request_id:
            return
        if _request_allowed(url, allowed):
            active.fire_and_forget("Fetch.continueRequest", {"requestId": request_id})
        else:
            active.fire_and_forget(
                "Fetch.failRequest",
                {"requestId": request_id, "errorReason": "BlockedByClient"},
            )

    session.command("Page.enable", timeout_seconds=_remaining(deadline))
    session.command("Runtime.enable", timeout_seconds=_remaining(deadline))
    session.command(
        "Fetch.enable",
        {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
        timeout_seconds=_remaining(deadline),
    )
    width, height, scale = _parse_viewport(target.viewport)
    session.command(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": width,
            "height": height,
            "deviceScaleFactor": scale,
            "mobile": False,
        },
        timeout_seconds=_remaining(deadline),
        event_handler=events,
    )
    target_url = _target_url(provider.origin, target.route)
    session.command(
        "Page.navigate",
        {"url": target_url},
        timeout_seconds=_remaining(deadline),
        event_handler=events,
    )
    _wait_expression(
        session,
        "document.readyState === 'interactive' || document.readyState === 'complete'",
        timeout_ms=min(DEFAULT_ACTION_TIMEOUT_MS, int(_remaining(deadline) * 1000)),
        event_handler=events,
    )
    if target.ready_selector:
        _wait_selector(
            session,
            target.ready_selector,
            timeout_ms=min(DEFAULT_ACTION_TIMEOUT_MS, int(_remaining(deadline) * 1000)),
            event_handler=events,
        )
    for action in target.actions:
        _run_action(session, action, event_handler=events, deadline=deadline)
    screenshot = session.command(
        "Page.captureScreenshot",
        {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": False,
        },
        timeout_seconds=_remaining(deadline),
        event_handler=events,
    )
    encoded = str(screenshot.get("data", "") or "")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise BrowserCaptureError("Chrome DevTools returned malformed screenshot bytes") from exc
    if not data:
        raise BrowserCaptureError("Chrome DevTools returned an empty screenshot")
    output.write_bytes(data)


def _run_action(
    session: ux_browser_cdp.CDPSession,
    action: BrowserAction,
    *,
    event_handler,
    deadline: float,
) -> None:
    timeout_ms = min(action.timeout_ms, int(_remaining(deadline) * 1000))
    selector = json.dumps(action.selector, ensure_ascii=False)
    value = json.dumps(action.value, ensure_ascii=False)
    if action.kind == "wait":
        _wait_selector(
            session,
            action.selector,
            timeout_ms=timeout_ms,
            event_handler=event_handler,
        )
        return
    if action.kind == "click":
        expression = (
            "(() => { const el = document.querySelector(" + selector + "); "
            "if (!el) return false; el.click(); return true; })()"
        )
    elif action.kind == "fill":
        expression = (
            "(() => { const el = document.querySelector(" + selector + "); "
            "if (!el || !('value' in el)) return false; el.focus(); el.value = " + value + "; "
            "el.dispatchEvent(new Event('input', {bubbles:true})); "
            "el.dispatchEvent(new Event('change', {bubbles:true})); return true; })()"
        )
    elif action.kind == "select":
        expression = (
            "(() => { const el = document.querySelector(" + selector + "); "
            "if (!el || el.tagName !== 'SELECT') return false; el.value = " + value + "; "
            "el.dispatchEvent(new Event('change', {bubbles:true})); return true; })()"
        )
    else:
        raise BrowserCaptureError(f"unsupported browser action: {action.kind}")
    if not _evaluate_bool(
        session,
        expression,
        event_handler=event_handler,
        timeout_seconds=max(1.0, timeout_ms / 1000),
    ):
        raise BrowserCaptureError(
            f"browser action {action.kind} could not find or operate selector {action.selector!r}"
        )


def _wait_selector(session, selector: str, *, timeout_ms: int, event_handler) -> None:
    encoded = json.dumps(selector, ensure_ascii=False)
    _wait_expression(
        session,
        f"document.querySelector({encoded}) !== null",
        timeout_ms=timeout_ms,
        event_handler=event_handler,
    )


def _wait_expression(session, expression: str, *, timeout_ms: int, event_handler) -> None:
    deadline = time.monotonic() + max(0.1, timeout_ms / 1000)
    while time.monotonic() < deadline:
        if _evaluate_bool(
            session,
            expression,
            event_handler=event_handler,
            timeout_seconds=max(0.5, min(2.0, deadline - time.monotonic())),
        ):
            return
        time.sleep(0.05)
    raise BrowserCaptureError(f"browser readiness condition timed out after {timeout_ms} ms")


def _evaluate_bool(session, expression: str, *, event_handler, timeout_seconds: float) -> bool:
    result = session.command(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout_seconds=max(0.5, timeout_seconds),
        event_handler=event_handler,
    )
    remote = result.get("result", {})
    if not isinstance(remote, dict):
        return False
    return remote.get("value") is True


def _launch_application(repo: Path, command: tuple[str, ...], *, popen):
    try:
        return popen(
            list(command),
            cwd=repo,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BrowserCaptureError(f"browser application command could not be launched: {exc}") from exc


def _wait_for_application(
    provider: BrowserProviderConfig,
    process: object | None,
    *,
    deadline: float,
) -> None:
    url = _target_url(provider.origin, provider.ready_path)
    last = ""
    while time.monotonic() < deadline:
        if process is not None and getattr(process, "poll", lambda: None)() is not None:
            raise BrowserCaptureError("browser application process exited before becoming ready")
        try:
            request = urlrequest.Request(url, method="GET", headers={"User-Agent": "AutoDev-UX-Capture/1"})
            with urlrequest.urlopen(request, timeout=min(2.0, _remaining(deadline))) as response:
                status = int(getattr(response, "status", 200))
                if 200 <= status < 400:
                    return
                last = f"HTTP {status}"
        except (OSError, urlerror.URLError) as exc:
            last = str(exc)
        time.sleep(0.1)
    raise BrowserCaptureError(
        f"browser application did not become ready at {url}" + (f": {last}" if last else "")
    )


def _launch_browser(executable: str, *, port: int, profile: Path, popen):
    command = [
        executable,
        "--headless=new",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-default-browser-check",
        "--no-first-run",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--remote-allow-origins=http://127.0.0.1:{port}",
        f"--user-data-dir={profile}",
        "about:blank",
    ]
    try:
        return popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BrowserCaptureError(f"headless browser could not be launched: {exc}") from exc


def _wait_for_devtools(port: int, browser: object, *, deadline: float) -> dict[str, object]:
    last = ""
    while time.monotonic() < deadline:
        if getattr(browser, "poll", lambda: None)() is not None:
            raise BrowserCaptureError("headless browser exited before DevTools became ready")
        try:
            value = _json_request(f"http://127.0.0.1:{port}/json/version", timeout=min(2.0, _remaining(deadline)))
            if value:
                return value
        except BrowserCaptureError as exc:
            last = str(exc)
        time.sleep(0.05)
    raise BrowserCaptureError("Chrome DevTools endpoint did not become ready" + (f": {last}" if last else ""))


def _new_page(port: int, *, deadline: float) -> dict[str, object]:
    return _json_request(
        f"http://127.0.0.1:{port}/json/new?{quote('about:blank', safe='')}",
        method="PUT",
        timeout=min(3.0, _remaining(deadline)),
    )


def _json_request(url: str, *, method: str = "GET", timeout: float) -> dict[str, object]:
    try:
        request = urlrequest.Request(url, method=method)
        with urlrequest.urlopen(request, timeout=max(0.2, timeout)) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except (OSError, urlerror.URLError) as exc:
        raise BrowserCaptureError(f"DevTools HTTP request failed: {exc}") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise BrowserCaptureError("DevTools HTTP response exceeds safety limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserCaptureError("DevTools HTTP endpoint returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise BrowserCaptureError("DevTools HTTP endpoint returned non-object JSON")
    return value


def _resolve_browser_executable(configured: str, *, which) -> str:
    if configured:
        if "/" in configured or "\\" in configured:
            path = Path(configured).expanduser()
            if not path.is_absolute() or not path.is_file():
                raise BrowserCaptureError("browser.executable path must be an existing absolute file")
            return str(path)
        if not _SAFE_BROWSER_NAME.fullmatch(configured):
            raise BrowserCaptureError("browser.executable command name is unsafe")
        resolved = which(configured)
        if not resolved:
            raise BrowserCaptureError(f"configured browser executable was not found on PATH: {configured}")
        return str(resolved)
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "microsoft-edge",
    ):
        resolved = which(candidate)
        if resolved:
            return str(resolved)
    raise BrowserCaptureError(
        "first-party browser capture requires Chrome, Chromium, or Edge on PATH, or browser.executable"
    )


def _parse_actions(value: object, *, target_id: str) -> tuple[BrowserAction, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or len(value) > MAX_ACTIONS:
        raise BrowserCaptureError(
            f"browser target {target_id} actions must be an array of at most {MAX_ACTIONS} entries"
        )
    actions: list[BrowserAction] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise BrowserCaptureError(f"browser target {target_id} action {index} must be an object")
        kind = str(raw.get("type", "") or "").strip().casefold()
        if kind not in {"click", "fill", "select", "wait"}:
            raise BrowserCaptureError(
                f"browser target {target_id} action {index} has unsupported type {kind!r}"
            )
        selector = str(raw.get("selector", "") or "").strip()
        _validate_selector(selector, field=f"browser target {target_id} action {index}.selector")
        value_text = str(raw.get("value", "") or "")
        if kind in {"fill", "select"} and len(value_text) > 4096:
            raise BrowserCaptureError(f"browser target {target_id} action {index}.value is too long")
        timeout_ms = int(raw.get("timeout_ms", DEFAULT_ACTION_TIMEOUT_MS) or DEFAULT_ACTION_TIMEOUT_MS)
        if not 100 <= timeout_ms <= 60_000:
            raise BrowserCaptureError(
                f"browser target {target_id} action {index}.timeout_ms must be 100-60000"
            )
        actions.append(
            BrowserAction(
                kind=kind,
                selector=selector,
                value=value_text,
                timeout_ms=timeout_ms,
            )
        )
    return tuple(actions)


def _string_array(value: object, *, field: str, allow_empty: bool, max_items: int) -> tuple[str, ...]:
    if value in (None, "") and allow_empty:
        return ()
    if not isinstance(value, list) or (not value and not allow_empty) or len(value) > max_items:
        raise BrowserCaptureError(f"{field} must be a JSON string array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise BrowserCaptureError(f"{field} contains an invalid argument")
        result.append(item.strip())
    return tuple(result)


def _normalize_origin(value: object) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserCaptureError("browser origins must be explicit http(s) origins")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BrowserCaptureError("browser origins must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise BrowserCaptureError("browser origins must not contain an application path")
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port
    suffix = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


def _origin(url: str) -> str:
    try:
        return _normalize_origin_from_parts(urlsplit(url))
    except (ValueError, BrowserCaptureError):
        return ""


def _normalize_origin_from_parts(parsed) -> str:
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserCaptureError("URL has no http(s) origin")
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port
    suffix = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


def _target_url(origin: str, route: str) -> str:
    _validate_route(route, field="route")
    return origin.rstrip("/") + route


def _validate_route(route: str, *, field: str) -> None:
    if not route.startswith("/") or route.startswith("//") or len(route) > 2048 or "\x00" in route:
        raise BrowserCaptureError(f"{field} must be a bounded origin-relative route beginning with /")
    parsed = urlsplit(route)
    if parsed.scheme or parsed.netloc:
        raise BrowserCaptureError(f"{field} must not specify another origin")


def _validate_selector(selector: str, *, field: str, allow_empty: bool = False) -> None:
    if not selector and allow_empty:
        return
    if not selector or len(selector) > 512 or "\x00" in selector:
        raise BrowserCaptureError(f"{field} must be a non-empty bounded CSS selector")


def _parse_viewport(value: str) -> tuple[int, int, float]:
    match = _VIEWPORT.fullmatch(value)
    if match is None:
        raise BrowserCaptureError("browser viewport must use WIDTHxHEIGHT or WIDTHxHEIGHT@SCALE")
    width = int(match.group("width"))
    height = int(match.group("height"))
    scale = float(match.group("scale") or "1")
    if not 320 <= width <= 3840 or not 240 <= height <= 2160 or not 0.5 <= scale <= 4:
        raise BrowserCaptureError("browser viewport dimensions/scale are outside supported bounds")
    return width, height, scale


def _request_allowed(url: str, allowed_origins: set[str]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme in {"about", "data", "blob"}:
        return True
    return _origin(url) in allowed_origins


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BrowserCaptureError("browser capture exceeded its overall timeout")
    return remaining


def _stop_process(process: object | None) -> None:
    if process is None:
        return
    try:
        if getattr(process, "poll", lambda: 0)() is not None:
            return
        getattr(process, "terminate")()
        try:
            getattr(process, "wait")(timeout=3)
        except (subprocess.TimeoutExpired, TimeoutError):
            getattr(process, "kill")()
            getattr(process, "wait")(timeout=3)
    except (OSError, AttributeError):
        pass


def _cleanup_profile(profile: object) -> None:
    for attempt in range(PROFILE_CLEANUP_ATTEMPTS):
        try:
            getattr(profile, "cleanup")()
            return
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY or attempt + 1 >= PROFILE_CLEANUP_ATTEMPTS:
                raise
            time.sleep(PROFILE_CLEANUP_RETRY_SECONDS)
