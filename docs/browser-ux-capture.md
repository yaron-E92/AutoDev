# First-party browser UX capture

AutoDev's `browser` UX capture provider reproduces declared web screens, states, and journey steps without requiring each repository to write a capture executable. It plugs into the same `.autodev/ux-capture.json`, `CapturedImage`, hashing, multimodal verification, repair, and resume contracts as the original `command` provider.

## Example

```json
{
  "schema": "autodev.ux.capture/v1",
  "provider": "browser",
  "timeout_seconds": 45,
  "browser": {
    "application": {
      "origin": "http://127.0.0.1:4173",
      "ready_path": "/health",
      "command": ["npm", "run", "dev", "--", "--port", "4173"]
    },
    "allowed_origins": [
      "http://127.0.0.1:4173"
    ]
  },
  "targets": {
    "screen:home": {
      "source_kind": "screen",
      "source_id": "home",
      "output": "home.png",
      "route": "/",
      "viewport": "1280x720@1",
      "ready_selector": "#app"
    },
    "state:task-editor-empty": {
      "source_kind": "state",
      "source_id": "task-editor-empty",
      "output": "task-editor-empty.png",
      "reference": "screen:task-editor",
      "route": "/tasks/new",
      "viewport": "1280x720@1",
      "ready_selector": "#task-editor",
      "actions": [
        {
          "type": "wait",
          "selector": "[data-state='empty']"
        }
      ]
    },
    "journey:checkout-complete": {
      "source_kind": "journey",
      "source_id": "checkout-complete",
      "output": "checkout-complete.png",
      "reference": "screen:confirmation",
      "route": "/checkout",
      "viewport": "1280x720@1",
      "ready_selector": "#checkout",
      "actions": [
        {
          "type": "fill",
          "selector": "#name",
          "value": "Fixture User"
        },
        {
          "type": "select",
          "selector": "#delivery",
          "value": "standard"
        },
        {
          "type": "click",
          "selector": "#finish"
        },
        {
          "type": "wait",
          "selector": "#confirmation",
          "timeout_ms": 5000
        }
      ]
    }
  }
}
```

`browser.executable` is optional. AutoDev searches for Chrome, Chromium, or Edge when it is omitted. If supplied as an absolute path it must name an existing file; if supplied as a command name it must be a bounded safe name resolvable on `PATH`.

`browser.application.command` is optional. Omit it when AutoDev should attach to an application that is already running at the declared origin. When present, AutoDev launches the argv directly without a shell, waits for `ready_path`, and terminates the process after capture.

## Deterministic target replay

Every selected capture target is declared in repository configuration and bound to its AutoDev UX source ID:

```text
screen:<id>
state:<id>
journey:<id>
```

The browser provider does not infer routes, selectors, or journeys from model prose. A target declares:

- an origin-relative `route`;
- a deterministic `viewport` (`WIDTHxHEIGHT` or `WIDTHxHEIGHT@SCALE`);
- an optional `ready_selector`;
- zero or more bounded actions.

Supported actions are deliberately narrow:

- `click` — click one declared CSS selector;
- `fill` — set one form control value and emit input/change events;
- `select` — select one option value and emit a change event;
- `wait` — wait for one CSS selector to exist.

There is no arbitrary JavaScript/evaluate action in repository configuration. Each target is capped at 32 actions, selectors and values are bounded, action timeouts are bounded, and the whole capture remains subject to the capture-level timeout.

## Journey/state references

A selected state or journey may be described by JSON/Markdown rather than have its own reference image. Such a target can explicitly name one pinned visual reference with `reference`:

```json
{
  "source_kind": "journey",
  "source_id": "checkout-complete",
  "reference": "screen:confirmation"
}
```

AutoDev keeps `journey:checkout-complete` as the comparison/source identity while resolving only the declared `screen:confirmation` image from the pinned UX artifact. The durable multimodal evidence records both `target_id` and `reference_target_id`. Missing, unknown, non-image, or unpinned reference targets fail closed; AutoDev never scans or sends the whole UX bundle to guess a picture.

Direct image mappings in the UX manifest remain preferred. The `reference` fallback is consulted when the selected state/journey source itself does not resolve to an image.

## Browser and network boundary

Browser capture creates an isolated temporary browser profile under the current AutoDev run and opens a fresh `about:blank` page through Chrome DevTools Protocol. It does not enumerate or attach to the user's existing tabs, windows, profiles, desktop, notifications, or credential dialogs.

Navigation is limited to the declared application origin and origin-relative target routes. DevTools request interception fails HTTP(S) requests whose exact normalized origin is not in `browser.allowed_origins`. `about:`, `data:`, and `blob:` page resources are allowed because they do not create a new network origin. Additional application/API origins must therefore be listed explicitly rather than discovered dynamically.

This protects the capture boundary; it is not a general browser sandbox for hostile application code. The repository application under test is trusted to run as repository code already would. Pinned UX prototype HTML/JavaScript is never used as the application under test.

## Evidence and privacy

Browser screenshots are written only under `.autodev-run/current/ux-captures/`, validated as bounded images, hashed, and fed into the existing multimodal verifier contract. The capture records stable source IDs plus browser product and effective viewport metadata.

The verifier's image-route capability and privacy authorization remain independent of browser capture. A successful browser screenshot does not imply that the configured verifier can consume image input; unsupported or unknown verifier routes still fail closed as `unverifiable`.

## CI coverage

AutoDev's Linux CI runs a localhost-only real-browser fixture. The fixture serves a small application, replays a declared journey with headless Chrome/Chromium, captures the resulting PNG, resolves an indirect pinned screen reference, and feeds the reference/capture pair through the production multimodal comparison wiring. Unit coverage separately checks configuration validation, provider dispatch, origin filtering, state/journey declarations, indirect-reference failure modes, and rejection of arbitrary JavaScript actions.
