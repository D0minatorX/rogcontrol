"""The user's config file: defaults, forward-migration, and disk I/O.

Extracted from the GTK3 app so the helper scripts, the enforcer service and
the tests can all read the same config the same way. Standard library plus
the profile tables -- no GTK, no hardware access.

The file lives at CONFIG_PATH and holds everything the user has set: their
profiles, which one is current, charge limit, window size. It is the only
thing here that cannot be regenerated, so the rules are conservative:
migration never overwrites a value, a save never truncates the old file, and
a config that cannot be parsed is moved aside rather than replaced.
"""

import json
import os
import sys
import time

from .profiles import DEFAULT_PROFILES, tailored_default_profiles

CONFIG_PATH = os.path.expanduser("~/.config/rogcontrol.json")

# Stamped into the config file. Nothing reads it yet: it exists so a future
# release that needs a genuine one-time step -- a rename or a split, something
# that cannot be detected by looking at the file -- has something to gate on.
CONFIG_VERSION = 1

DEFAULT_CONFIG = {
    "current_profile": "Balanced Performance",
    "kbd_brightness": 2,
    "charge_limit": 100,
    "ac_profile": "Performance",
    "battery_profile": "Quiet",
    "window_size": [600, 700],
    "fan_display_unit": "percent",
}


def migrate_config(cfg, gpu_min_w=1, gpu_max_w=140):
    """Bring a config from any older version up to date WITHOUT touching
    anything the user has set.

    Only missing keys are filled in. Every existing value is left exactly as
    it is, including ones this version doesn't recognise -- an unknown key
    is more likely to be from a newer build than junk, and silently dropping
    it would lose the user's settings on a downgrade/upgrade cycle.

    ``gpu_min_w``/``gpu_max_w`` describe the card actually fitted and are
    used only when stock profiles are created from scratch. They are
    arguments rather than module globals because nothing in here may depend
    on hardware detection having run; the defaults describe the 140W card
    the built-in numbers were chosen against.

    There is deliberately no migration chain here. config_version is only a
    stamp, so that a future release which needs a genuine one-time step -- a
    rename, a split, anything that cannot be detected from the file itself --
    has something to gate on."""
    for key, value in DEFAULT_CONFIG.items():
        # Deep-copied per config: window_size is a list, and handing the same
        # object to every config would let one of them edit the defaults.
        cfg.setdefault(key, json.loads(json.dumps(value)))

    # Profiles: keep the user's, add the stock ones only if there are none
    # at all. A user who deleted "Quiet" should not silently get it back.
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        # Only reached on a fresh install (or a config with no profiles at
        # all), so this is the one place hardware-tailored defaults apply.
        # An update never gets here, which is what keeps existing profiles
        # from being replaced.
        cfg["profiles"] = tailored_default_profiles(gpu_min_w, gpu_max_w)
    else:
        # Fill in only sections a profile is missing entirely, so profiles
        # written by an older version still load.
        for name, prof in profiles.items():
            if not isinstance(prof, dict):
                continue
            base = DEFAULT_PROFILES.get(name) or DEFAULT_PROFILES["Balanced Performance"]
            for section in ("cpu", "gpu", "fans"):
                if section not in prof:
                    prof[section] = json.loads(json.dumps(base[section]))

    # current_profile must name a profile that exists
    if cfg.get("current_profile") not in cfg["profiles"]:
        cfg["current_profile"] = next(iter(cfg["profiles"]))

    cfg["config_version"] = CONFIG_VERSION
    return cfg


def load_config(path=None):
    """Load the user's config, migrating it forward in place. A config that
    cannot be parsed is preserved as a .corrupt-<timestamp> copy rather than
    being silently replaced -- the previous behaviour overwrote it on the
    next save, destroying the user's profiles with no way back.

    ``path`` defaults to CONFIG_PATH, and is read when called rather than
    bound into the signature so that tests (and anything else with its own
    config) can point this somewhere else."""
    path = CONFIG_PATH if path is None else path
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return migrate_config(cfg)
            raise ValueError("config is not a JSON object")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            stamp = int(time.time())
            backup = f"{path}.corrupt-{stamp}"
            # Two failures in the same second must not overwrite the first
            # backup -- that would lose the very thing we are saving.
            n = 1
            while os.path.exists(backup):
                backup = f"{path}.corrupt-{stamp}-{n}"
                n += 1
            try:
                os.replace(path, backup)
                print(f"rogcontrol: could not read config ({e}); "
                      f"kept a copy at {backup}", file=sys.stderr)
            except OSError:
                pass
    return migrate_config({})


def save_config(cfg, path=None):
    """Write the config out atomically.

    The new file is written alongside the old one and renamed over it, so a
    reader only ever sees the complete old config or the complete new one.
    Writing in place -- which this used to do -- truncates the file the
    moment it is opened, so a crash, a full disk or a value that will not
    serialise left the user with an empty or half-written config and no
    profiles."""
    path = CONFIG_PATH if path is None else path
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
            # The rename is only atomic with respect to the file's contents
            # if those contents have actually reached the disk first.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a half-written .tmp sitting next to the real config.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
