from __future__ import annotations

from pathlib import Path


PATH = Path("automation/model_providers.py")
MARKER = "def _compat_headroom_invoke("


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("provider compatibility wrapper already installed")
        return

    anchor = "_COMPAT_BASELINE.update(globals())\n"
    if anchor not in text:
        raise SystemExit("generated model-provider facade compatibility block is missing")

    wrapper = '''\n\n_ORIGINAL_HEADROOM_INVOKE = HeadroomProvider.invoke\n\ndef _compat_headroom_invoke(self, *args, **kwargs):\n    _sync_compat_overrides()\n    return _ORIGINAL_HEADROOM_INVOKE(self, *args, **kwargs)\n\nHeadroomProvider.invoke = _compat_headroom_invoke\n'''
    text = text.replace(anchor, anchor + wrapper, 1)
    PATH.write_text(text, encoding="utf-8")
    print("installed HeadroomProvider facade compatibility wrapper")


if __name__ == "__main__":
    main()
