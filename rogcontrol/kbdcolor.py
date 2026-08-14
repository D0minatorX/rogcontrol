"""Keyboard lighting: the modes, and the colour arithmetic behind them.

Standard library only -- no GTK, no sysfs -- for the same reason
``fancurve.py`` is: everything here is arithmetic over plain (r, g, b)
tuples, so it can be tested without a display, without the keyboard
attached, and without a privileged helper to write to.

The split that matters is between *deciding a colour* and *sending it*. A
temperature becomes a colour here; whether that colour reaches the keyboard
is the page's problem. That is what makes the parts worth pinning with
tests -- a gradient that ramps the wrong way, or a restore token dropped
while saving, is invisible until a user notices their keyboard is wrong.

Colours are 0-255 integer triples throughout, which is what the config
stores and what the helper's arguments are. The GTK side deals in 0.0-1.0
floats; :func:`byte_to_float` and :func:`float_to_byte` are the only place
that conversion happens, so a colour cannot drift a point every time the
page is loaded and saved.
"""

# The backlight brightness the LED class accepts, and rogauracore's speed
# range. Both are mirrored from the helper's own validation, so the UI
# offers exactly what will be accepted rather than values that only fail
# once they get there.
KBD_MIN, KBD_MAX = 0, 3
SPEED_MIN, SPEED_MAX = 1, 3

# Addressable zones on a four-zone Aura keyboard. Both the gradient and the
# ambient sampler paint per zone, so they agree on how many there are.
KBD_ZONES = 4

# Keyboard RGB via rogauracore, which has confirmed support for this
# laptop's N-Key controller (USB ID 0b05:19b6, verified via `lsusb` on this
# exact machine) -- these map friendly UI names to rogauracore's actual
# command names, not guessed raw mode numbers like the old sysfs approach.
#
# Insertion order is the order the picker offers them in.
KBD_RGB_MODES = {
    "Static": "single_static",
    "Breathing": "single_breathing",
    "Pulse": "single_pulsing",      # rogauracore's real pulse effect -- a
                                    # distinct sharp flash, not a breathing
                                    # fade with a different colour
    "Color Cycle": "single_colorcycle",
    "Rainbow": "rainbow",
    "Gradient Static": "gradient_static",
    "GPU Temp Color": "gpu_temp_color",
    "CPU Temp Color": "cpu_temp_color",
    "Battery Level": "battery_color",
    "Ambient": "ambient",           # follows what is on the primary monitor
}

# Needs a controller with four addressable zones.
MULTI_ZONE_MODES = ("Gradient Static",)
# Modes that read the first colour picker. The rest either animate through
# every colour themselves (Rainbow, Color Cycle) or take their colour from
# something being measured, so showing a picker for them would be showing a
# control that does nothing.
COLOUR_MODES = ("Static", "Breathing", "Pulse", "Gradient Static")
# Breathing fades between the two; Gradient Static blends across the zones.
SECOND_COLOUR_MODES = ("Breathing", "Gradient Static")
# The animations the firmware runs itself, all of which take a speed.
SPEED_MODES = ("Breathing", "Pulse", "Color Cycle", "Rainbow")
# Colour comes from a live reading, re-sent as the reading moves.
LIVE_COLOUR_MODES = ("GPU Temp Color", "CPU Temp Color", "Battery Level")

# Blackbody-ish gradient used by GPU/CPU Temp Color modes: cool blue at low
# temp, up through green/yellow, to red at high temp. Bounds chosen around
# typical laptop CPU/GPU operating ranges under load.
TEMP_COLOR_MIN_C = 40
TEMP_COLOR_MAX_C = 90

# Region averages come out dim; the brightest channel of a zone is scaled up
# to this, which preserves the hue and only raises the level.
AMBIENT_TARGET_LEVEL = 200
# Below this the region is treated as genuinely dark and left alone, rather
# than amplifying near-black into colour noise.
AMBIENT_DARK_LEVEL = 12
# Below this much change, the keyboard is left alone: every update is a USB
# round trip through rogauracore, and repainting on sampling noise makes the
# keyboard flicker while costing wakeups.
AMBIENT_MIN_DELTA = 12

# Keys in a stored ``kbd_rgb`` block that this page does not own, and so must
# carry across a save rather than rebuild. Losing the token would make the
# desktop ask for screen permission again; the third and fourth colours
# belong to a mode that no longer exists, and are kept only so downgrading
# does not lose them.
CARRIED_KEYS = ("ambient_restore_token", "r3", "g3", "b3",
                "r4", "g4", "b4", "color_count")

DEFAULT_COLOR = (255, 0, 0)
DEFAULT_COLOR2 = (0, 0, 255)
DEFAULT_SPEED = 2


# -- capabilities ------------------------------------------------------------

def supported_modes(caps):
    """The RGB modes this machine can actually perform, in picker order.

    Detection runs once at startup, so this is deliberately generous: a mode
    is only dropped when something it strictly requires is known to be
    absent. The alternative -- listing everything and letting unsupported
    picks fail silently -- is what makes an app look broken on hardware the
    author never had."""
    caps = caps or {}
    modes = []
    for name in KBD_RGB_MODES:
        if name in MULTI_ZONE_MODES and not caps.get("kbd_rgb_zones", True):
            continue
        if name == "Battery Level" and not caps.get("kbd_battery", True):
            continue
        if name == "GPU Temp Color" and not caps.get("nvidia", True):
            continue
        if name == "Ambient" and not caps.get("kbd_ambient", True):
            continue
        modes.append(name)
    return modes


# -- values ------------------------------------------------------------------

def clamp_byte(value, fallback=0):
    """A channel as an int in 0-255. Anything unusable becomes ``fallback``.

    The config is a text file a user can edit, so a channel that is a string,
    a float or missing entirely has to land somewhere sane rather than take
    the page down on load."""
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        value = int(fallback)
    return max(0, min(255, value))


def clamp_speed(value):
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        value = DEFAULT_SPEED
    return max(SPEED_MIN, min(SPEED_MAX, value))


def byte_to_float(value):
    """0-255 as GTK's 0.0-1.0."""
    return clamp_byte(value) / 255.0


def float_to_byte(value):
    """GTK's 0.0-1.0 back to 0-255.

    Rounding rather than truncating, so a colour survives the round trip
    through a colour button unchanged. Truncation loses a point on most
    channels every time the page is loaded and saved, and a pink that walks
    towards red over a week is a bug nobody can reproduce on demand."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(255, int(round(value * 255.0))))


def saved_color(saved, suffix="", default=DEFAULT_COLOR):
    """One colour out of a stored ``kbd_rgb`` block, clamped.

    ``suffix`` is "" for the first colour and "2" for the second, matching
    the r/g/b and r2/g2/b2 keys the config has used since the GTK3 app."""
    saved = saved or {}
    return tuple(
        clamp_byte(saved.get(f"{channel}{suffix}", fallback), fallback)
        for channel, fallback in zip("rgb", default))


def merge_kbd_rgb(saved, mode, color1, color2, speed):
    """The ``kbd_rgb`` block to store, keeping what this page does not own.

    Built from ``saved`` rather than replacing it, because the restore token
    for Ambient lives in the same block and is written by the portal, not by
    any control on the page. Rebuilding the block from the widgets alone --
    which is what a naive save does -- silently drops it, and the next launch
    asks the user for screen permission again."""
    saved = saved or {}
    block = {
        "mode": mode,
        "r": clamp_byte(color1[0]), "g": clamp_byte(color1[1]),
        "b": clamp_byte(color1[2]),
        "r2": clamp_byte(color2[0]), "g2": clamp_byte(color2[1]),
        "b2": clamp_byte(color2[2]),
        "speed": clamp_speed(speed),
    }
    for key in CARRIED_KEYS:
        if key in saved:
            block[key] = saved[key]
    return block


# -- colour maths ------------------------------------------------------------

def temp_to_rgb(temp_c, lo=TEMP_COLOR_MIN_C, hi=TEMP_COLOR_MAX_C):
    """Maps a temperature to a blue-green-yellow-red gradient. Below lo is
    pure blue, above hi is pure red, in between sweeps through the hue
    range like a simplified thermal gradient."""
    t = max(0.0, min(1.0, (temp_c - lo) / max(1, (hi - lo))))
    if t < 0.5:
        # blue -> green
        frac = t / 0.5
        return 0, round(255 * frac), round(255 * (1 - frac))
    # green -> red
    frac = (t - 0.5) / 0.5
    return round(255 * frac), round(255 * (1 - frac)), 0


def battery_to_rgb(percent, charging):
    """Colour for Battery Level mode.

    Discharging runs green -> yellow -> red as it empties, the convention
    everyone already reads without being told. Charging runs blue -> green
    instead, so a glance tells you whether it is filling or draining without
    having to remember which shade of green meant what."""
    pct = max(0, min(100, percent))
    if charging:
        frac = pct / 100
        return 0, round(255 * frac), round(255 * (1 - frac))
    if pct >= 50:
        frac = (100 - pct) / 50          # 100% green -> 50% yellow
        return round(255 * frac), 255, 0
    frac = (50 - pct) / 50               # 50% yellow -> 0% red
    return 255, round(255 * (1 - frac)), 0


def gradient_zone_colors(color1, color2, zones=KBD_ZONES):
    """Zone colours forming an even ramp from one colour to the other.

    Walks from one colour to the other in equal steps across the zones,
    which is what people mean by a gradient: the first zone is exactly
    colour 1 and the last is exactly colour 2, so a gradient between two
    identical colours is indistinguishable from Static."""
    zones = max(1, int(zones))
    if zones == 1:
        return [tuple(clamp_byte(c) for c in color1)]
    return [tuple(max(0, min(255, round(a + (b - a) * (i / (zones - 1)))))
                  for a, b in zip(color1, color2))
            for i in range(zones)]


def boost_ambient(color):
    """Lift a screen average to something a keyboard can actually show.

    Averaging a whole region lands most colours in the 30-70 range, which
    the keys render as a barely-lit smudge. Scaling the brightest channel
    up to AMBIENT_TARGET_LEVEL keeps the hue exactly and only changes how
    bright it is. A genuinely dark region is left dark rather than being
    amplified into colour noise."""
    color = tuple(color)
    peak = max(color)
    if peak < AMBIENT_DARK_LEVEL or peak >= AMBIENT_TARGET_LEVEL:
        return color
    gain = AMBIENT_TARGET_LEVEL / peak
    return tuple(min(255, round(c * gain)) for c in color)


def average_color(zones):
    """One colour standing in for several, for a keyboard without zones."""
    zones = list(zones)
    if not zones:
        return (0, 0, 0)
    return tuple(sum(zone[i] for zone in zones) // len(zones)
                 for i in range(3))


# -- screen frames -----------------------------------------------------------
#
# Used only by Ambient mode, but kept here rather than beside the capture
# pipeline so it can be exercised on a hand-built buffer -- without a portal,
# a compositor, a keyboard or GTK. The pipeline half needs a live desktop;
# this half is arithmetic.

def zones_from_frame(data, width, height, stride, zones=KBD_ZONES):
    """Average each vertical band of a packed RGB frame into a zone colour.

    ``stride`` is bytes per row, which is not always ``width * 3``:
    GStreamer pads each row to a 4-byte boundary, and using the wrong one
    skews the colours progressively down the frame."""
    zones = max(1, int(zones))
    band = max(1, width // zones)
    out = []
    for z in range(zones):
        x0 = z * band
        x1 = width if z == zones - 1 else (z + 1) * band
        r = g = b = n = 0
        for y in range(height):
            row = y * stride
            for x in range(x0, x1):
                i = row + x * 3
                r += data[i]
                g += data[i + 1]
                b += data[i + 2]
                n += 1
        if not n:
            return None
        out.append(boost_ambient((r // n, g // n, b // n)))
    return out


def changed_enough(colors, last, threshold=AMBIENT_MIN_DELTA):
    """True if new zone colours are far enough from the old to be worth a
    write.

    Every update is a USB round trip through rogauracore, so repainting on
    sampling noise makes the keyboard flicker and costs wakeups for a change
    nobody can see."""
    if last is None:
        return True
    return max(abs(a - b)
               for zone, was in zip(colors, last)
               for a, b in zip(zone, was)) >= threshold


# -- helper arguments --------------------------------------------------------

def static_args(color):
    """The helper call that paints the whole keyboard one colour."""
    return ("kbdrgb", "single_static", *(clamp_byte(c) for c in color))


def helper_args(mode, color1=DEFAULT_COLOR, color2=DEFAULT_COLOR2,
                speed=DEFAULT_SPEED):
    """Arguments for ``run_helper`` that put ``mode`` on the keyboard.

    ``None`` for the modes whose colour is not known here: Ambient is driven
    by a live sampler, and the temperature and battery modes need a reading
    first -- those callers take the reading and use :func:`static_args`.

    Speed is passed for every mode that has one. It used to be omitted for
    Breathing, Rainbow and Color Cycle, which meant the speed slider moved
    and the animation did not."""
    color1 = tuple(clamp_byte(c) for c in color1)
    color2 = tuple(clamp_byte(c) for c in color2)
    speed = clamp_speed(speed)
    command = KBD_RGB_MODES.get(mode, "single_static")
    if command in ("ambient", "gpu_temp_color", "cpu_temp_color",
                   "battery_color"):
        return None
    if command == "rainbow":
        return ("kbdrgb", "rainbow", speed)
    if command == "single_colorcycle":
        # Colour Cycle takes no colour at all -- only a speed.
        return ("kbdrgb", "single_colorcycle", speed)
    if command == "single_breathing":
        return ("kbdrgb", "single_breathing", *color1, *color2, speed)
    if command == "single_pulsing":
        return ("kbdrgb", "single_pulsing", *color1, speed)
    if command == "gradient_static":
        zones = gradient_zone_colors(color1, color2)
        return ("kbdrgb", "multi_static",
                *[channel for zone in zones for channel in zone])
    return static_args(color1)
