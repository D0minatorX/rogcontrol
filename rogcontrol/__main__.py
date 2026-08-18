"""``python3 -m rogcontrol`` entry point.

Flags:
  --self-test   build every page, refresh each once, exit 0 (non-zero on any
                exception). This is the smoke test for the UI.
  --minimized   start with the window built but not shown.
  --toggle      show the window, or hide it if it is already visible.
  --quit        close a running window and exit; does nothing if the app is
                not running. Used by the tray's Quit item.
"""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
