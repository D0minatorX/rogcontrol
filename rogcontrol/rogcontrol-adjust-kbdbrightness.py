#!/usr/bin/env python3
"""
rogcontrol-adjust-kbdbrightness.py up|down
Raises or lowers keyboard backlight brightness by one step (range 0-3),
without needing the GUI open. Bind "up" and "down" to two separate
keyboard shortcuts.
"""
import json
import os
import subprocess
import sys

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")
KBD_MIN, KBD_MAX = 0, 3


def run_helper(*args):
    result = subprocess.run(
        ["sudo", "-n", "/usr/local/bin/rogcontrol-helper", *[str(a) for a in args]],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        print("Usage: rogcontrol-adjust-kbdbrightness.py up|down", file=sys.stderr)
        sys.exit(1)
    direction = sys.argv[1]

    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}

    current = config.get("kbd_brightness", 2)
    new_level = current + 1 if direction == "up" else current - 1
    new_level = max(KBD_MIN, min(KBD_MAX, new_level))

    if new_level == current:
        # Already at the limit -- still notify so the key press feels
        # acknowledged rather than silently doing nothing.
        notify("ROG Control", f"Keyboard brightness already at {'max' if direction == 'up' else 'min'}")
        return

    ok = run_helper("kbd", new_level)
    if ok:
        config["kbd_brightness"] = new_level
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
        notify("ROG Control", f"Keyboard brightness: {new_level}/{KBD_MAX}")
    else:
        notify("ROG Control", "Failed to change keyboard brightness")


if __name__ == "__main__":
    main()
