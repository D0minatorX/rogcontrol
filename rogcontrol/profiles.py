"""The stock power profiles and the power-profiles-daemon mapping.

Pure data plus one scaling helper -- no GTK, no hardware access, so it can
be imported and tested anywhere.

This is where the app, the config module, the System page and the tray read
these tables from; it is not yet the only copy. rogcontrol-enforcer.py still
defines its own PROFILE_TO_PPD_MODE/PPD_MODE_TO_PROFILE at the top of the
file even though it imports this package, so a name added or a mapping
changed here has to be changed there too or the service will disagree with
the window about which OS mode a profile means.

The numbers here are field-tuned against real hardware; treat a change to
one as a hardware retune, not a tidy-up.
"""

import json

# Our profile name -> power-profiles-daemon's fixed mode names. PPD only
# ever has exactly these three modes; anything else is invalid to set. More
# than one profile may map to the same mode -- the two Balanced profiles
# differ in EPP, which PPD has no concept of.
#
# Selecting a profile sets PPD's mode to match. The reverse direction is not
# a revert: the enforcer treats a mode set from elsewhere as a request to
# switch profile and adopts it (see PPD_MODE_TO_PROFILE below), and only
# forces PPD back when the mode maps to no profile that still exists.
PROFILE_TO_PPD_MODE = {
    "Performance": "performance",
    "Balanced Performance": "balanced",
    "Balanced Power": "balanced",
    "Quiet": "power-saver",
}

# The same mapping backwards, for adopting a power-mode change made outside
# this app (GNOME's power menu, a keyboard key, powerprofilesctl).
#
# First name wins, so "balanced" from the OS resolves to "Balanced
# Performance". A plain dict comprehension would have silently made it
# "Balanced Power", since the later key overwrites the earlier one.
PPD_MODE_TO_PROFILE = {}
for _name, _mode in PROFILE_TO_PPD_MODE.items():
    PPD_MODE_TO_PROFILE.setdefault(_mode, _name)
# The loop variables would otherwise stay behind as module attributes --
# rogcontrol.profiles._name is not something this module means to export.
del _name, _mode


def expected_ppd_mode(profile_name):
    """The OS power mode this profile is supposed to sit on, or None.

    None is not a failure: the mapping only covers the four stock names, so
    a profile the user invented has no expected mode and cannot be in or out
    of sync with the OS. Reporting it as out of sync -- which comparing
    against a default would do -- would put a permanent warning on the page
    for a profile that is behaving perfectly."""
    return PROFILE_TO_PPD_MODE.get(profile_name)


def ppd_modes_agree(profile_name, actual_mode):
    """True/False if the two can be compared, None if they cannot.

    Three states rather than two, for the same reason as above: "no opinion"
    and "disagrees" are different things to put on screen."""
    expected = expected_ppd_mode(profile_name)
    if expected is None or actual_mode is None:
        return None
    return expected == actual_mode

# Ordered coolest/quietest first, which is the order they appear in the
# profile menu and the tray.
#
# Each profile carries an energy preference (EPP), applied for you when the
# profile becomes active -- there is no control for it in the window. That is
# the whole point of the two Balanced profiles: identical power limits and
# fan curve, differing only in how hard the CPU chases clocks. Both map to
# the OS "balanced" power mode, and the reverse mapping above resolves that
# mode to "Balanced Performance".
DEFAULT_PROFILES = {
    "Quiet": {
        "cpu": {"stapm": 25000, "fast": 35000, "slow": 25000, "temp": 85, "coall": 0,
                "epp": "power"},
        "gpu": {"watts": 65, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 25], [60, 40], [75, 60], [90, 80]],
            "2": [[40, 25], [60, 40], [75, 60], [90, 80]],
            "3": [[40, 25], [60, 40], [75, 60], [90, 80]],
        },
    },
    "Balanced Power": {
        "cpu": {"stapm": 55000, "fast": 65000, "slow": 55000, "temp": 90, "coall": 0,
                "epp": "balance_power"},
        "gpu": {"watts": 100, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "2": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "3": [[40, 30], [60, 55], [75, 75], [90, 90]],
        },
    },
    "Balanced Performance": {
        "cpu": {"stapm": 55000, "fast": 65000, "slow": 55000, "temp": 90, "coall": 0,
                "epp": "balance_performance"},
        "gpu": {"watts": 100, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "2": [[40, 30], [60, 55], [75, 75], [90, 90]],
            "3": [[40, 30], [60, 55], [75, 75], [90, 90]],
        },
    },
    "Performance": {
        "cpu": {"stapm": 75000, "fast": 90000, "slow": 75000, "temp": 95, "coall": 0,
                "epp": "performance"},
        "gpu": {"watts": 140, "clock_offset": 0, "mem_clock_offset": 0},
        "fans": {
            "1": [[40, 45], [55, 70], [70, 85], [85, 100]],
            "2": [[40, 45], [55, 70], [70, 85], [85, 100]],
            "3": [[40, 45], [55, 70], [70, 85], [85, 100]],
        },
    },
}


def tailored_default_profiles(gpu_min_w, gpu_max_w):
    """The stock profiles, with GPU wattage scaled to the GPU actually
    fitted rather than the 140W one this was written on.

    The card's limits come in as arguments rather than being read from a
    module global, so nothing here depends on hardware detection having run.

    The tiers keep their relative shape (Quiet ~46%, Balanced ~71%,
    Performance 100% of the card's maximum), so a 60W card gets a sensible
    28/43/60W spread instead of three profiles asking for wattages the
    driver will simply refuse.

    CPU limits are deliberately NOT scaled: nothing here can read a chip's
    real ceiling, and inventing one would be worse than leaving a
    conservative starting value the user can tune. They stay inside the
    range the privileged helper validates, and the firmware clamps anything
    it dislikes.

    The result is a deep copy, so a caller that edits it -- it is handed
    straight to the config -- cannot reach back into DEFAULT_PROFILES."""
    profiles = json.loads(json.dumps(DEFAULT_PROFILES))
    reference_max = 140.0  # what the built-in numbers were chosen against
    for prof in profiles.values():
        gpu = prof.get("gpu")
        if not gpu or "watts" not in gpu:
            continue
        ratio = gpu["watts"] / reference_max
        gpu["watts"] = max(gpu_min_w, min(gpu_max_w, round(ratio * gpu_max_w)))
    return profiles
