"""Installed graphics mode backend.

The installer writes one value for the user's installation. Existing installs
without this file continue to use supergfxctl.
"""

from pathlib import Path


BACKEND_FILE = Path.home() / ".config" / "rogcontrol-graphics-backend"


def selected_backend():
    try:
        value = BACKEND_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "supergfxctl"
    return value if value in ("supergfxctl", "cardwire") else "supergfxctl"


def using_cardwire():
    return selected_backend() == "cardwire"
