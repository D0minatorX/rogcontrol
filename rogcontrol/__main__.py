"""``python3 -m rogcontrol`` entry point.

Flags:
  --self-test        build every page, refresh each once, exit 0 (non-zero
                     on any exception). This is the smoke test for the UI.
  --hardware-report  write hardware.hardware_report_text() to Downloads and
                     print the path. No window, no hardware writes, exits 0.
                     See docs/INTEL-SUPPORT-PLAN.txt.
  --minimized        start with the window built but not shown.
  --toggle           show the window, or hide it if it is already visible.
  --quit             close a running window and exit; does nothing if the
                     app is not running. Used by the tray's Quit item.
"""

import sys

# Handled here, before .app is imported, on purpose: .app pulls in GTK4/
# libadwaita at module load, and this flag's whole point is to work on a
# machine where that import might not even succeed -- a headless install, or
# one still mid-troubleshoot over the reason the window will not start. It
# needs neither.
if "--hardware-report" in sys.argv[1:]:
    from . import hardware

    print(hardware.write_hardware_report())
    sys.exit(0)

from .app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
