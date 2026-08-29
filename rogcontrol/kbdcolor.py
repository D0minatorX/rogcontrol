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

from .profiles import default_kbd_color

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
    "Profile Color": "profile_color",  # follows the active power profile
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

# The two modes that are not a single write the firmware then animates by
# itself, and that therefore must never both be running.
#
# Ambient needs a screen-capture session and a sampling thread; Profile Color
# needs something to notice a profile switch. They are mutually exclusive by
# construction rather than by a rule anyone has to remember, because there is
# exactly one saved ``mode`` and it can only hold one of them -- every reader
# below asks whether the saved mode is *its* mode before it writes anything.
PROFILE_COLOR_MODE = "Profile Color"
AMBIENT_MODE = "Ambient"
EXCLUSIVE_MODES = (AMBIENT_MODE, PROFILE_COLOR_MODE)

# Where a profile keeps the colour Profile Color paints it in. Beside the
# profile's cpu/gpu/fans rather than in the global kbd_rgb block, because it
# is a property OF the profile: it moves with an export, it is copied when a
# profile is branched from another, and it goes away when the profile does.
PROFILE_COLOR_KEY = "kbd_color"

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
        if (name == PROFILE_COLOR_MODE
                and not caps.get("kbd_rgb", True)):
            # The only mode here gated on the controller itself. Every other
            # one is left in the picker on the doctrine in pages/keyboard.py
            # -- try it and report what went wrong -- but this one is also
            # read by the enforcer, the hotkey cycler and the login apply,
            # none of which has a window to report into. Withholding it is
            # what keeps a machine with no Aura controller from ever having
            # it saved, and so from ever painting from those three.
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


# -- profile colour ----------------------------------------------------------
#
# Read on every profile switch, and most of those happen with no window open
# -- the tray, the hotkey cycler, the AC auto-switch and the OS power menu
# all switch profiles through processes that have no UI to report into. So
# everything here is tolerant by design: a profile with no stored colour, a
# colour a user hand-edited into nonsense, or a name that no longer exists
# must all land on something paintable rather than take a profile switch
# down with them.


def profile_color(cfg, name=None):
    """The colour Profile Color paints for one profile.

    ``name`` defaults to whichever profile is current, which is what every
    caller on a switch path wants and saves each of them repeating the
    lookup.

    A profile with no stored colour falls back to the stock colour for its
    name, and a name with no stock colour to the neutral one -- so this
    answers for a profile that was imported, hand-written, or created before
    the key existed, without the config having to be migrated first."""
    cfg = cfg or {}
    if name is None:
        name = cfg.get("current_profile")
    profile = (cfg.get("profiles") or {}).get(name)
    fallback = default_kbd_color(name)
    stored = (profile.get(PROFILE_COLOR_KEY)
              if isinstance(profile, dict) else None)
    if not isinstance(stored, (list, tuple)) or len(stored) != 3:
        stored = fallback
    return tuple(clamp_byte(value, default)
                 for value, default in zip(stored, fallback))


def profile_color_enabled(cfg):
    """True when the keyboard is currently the profile's to paint.

    The one question every switch path asks before touching the keyboard.
    Answering it from the saved mode -- rather than from a flag of its own --
    is what makes Profile Color and Ambient exclusive: there is a single
    ``mode``, so the keyboard is never both."""
    return ((cfg or {}).get("kbd_rgb") or {}).get("mode") == PROFILE_COLOR_MODE


def profile_color_args(cfg, name=None, caps=None):
    """The helper call that repaints the keyboard for a profile switch, or
    ``None`` when this switch must not touch the keyboard at all.

    ``None`` rather than a colour, and for two different reasons that both
    have to be honoured by callers with no window to report into:

    * the user is on some other lighting mode. Repainting anyway would take
      the keyboard away from Ambient's sampler (which would then paint over
      it half a second later, and the two would fight for as long as both
      believed they owned the keys), or wipe a Rainbow the user chose.
    * this machine has no controllable keyboard colour. The saved mode
      cannot be Profile Color on such a machine -- supported_modes never
      offered it -- but a config copied from another laptop can say
      otherwise, and that is exactly the case that has no window open."""
    if not profile_color_enabled(cfg):
        return None
    if not (caps or {}).get("kbd_rgb", True):
        return None
    return static_args(profile_color(cfg, name))


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
    if command in ("ambient", "profile_color", "gpu_temp_color",
                   "cpu_temp_color", "battery_color"):
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


# -- charger-connect flash ---------------------------------------------------
#
# An opt-in acknowledgement: the charger moves, the keys blink one colour for
# a moment, and then go back to *exactly* what they were doing. Everything
# below is the decision half -- which colour, and whether to blink at all.
# The blink itself (two helper calls with a sleep between them) belongs to
# whoever owns the USB device at that moment, which in practice is the
# enforcer, because the plug usually moves with no window open.
#
# THE RESTORE IS THE WHOLE FEATURE. A flash that cannot be undone is not a
# flash, it is the app changing the user's lighting and not telling them. So
# the rule here is inverted from the rest of the file: rather than trying to
# paint something for every mode, this refuses to flash at all unless the
# state it is about to overwrite can be reconstructed from the config alone.
# See :func:`flash_restore_args` for the three modes where it cannot be.

CHARGER_FLASH_KEY = "charger_flash"
CHARGER_FLASH_COLOR_KEY = "charger_flash_color"

# Cyan: not one of the stock profile colours (see profiles.PROFILE_KBD_COLORS)
# and not either of the two stock effect colours, so the default flash reads
# as a deliberate blink rather than as the lighting glitching towards a
# colour that was already in play.
DEFAULT_FLASH_COLOR = (0, 255, 255)

# How long the flash colour is held between the two writes.
#
# Not the length of the blink. Each helper call is a ~270 ms USB round trip
# through rogauracore, and the flash colour is on the keys for the whole of
# the *second* one as well -- so the blink the user sees is roughly
# FLASH_HOLD_SECONDS + 270 ms, about 450 ms here, and the keyboard is busy
# for about 720 ms in total. That is long enough to register as a blink and
# short enough that it is over before anyone looks up; anything shorter is
# swallowed by the round trips either side, and anything longer stops reading
# as an acknowledgement and starts reading as the lighting having changed.
FLASH_HOLD_SECONDS = 0.18

# How many times the flash colour is written before the final restore. Two
# reads as a deliberate double-blink rather than a single "the lighting
# glitched" flicker; the gap between the two is the restore write sitting
# between them (see charger_flash in the enforcer), not an extra sleep here.
FLASH_BLINK_COUNT = 2

# The minimum gap between the START of one flash and the next, in either
# direction.
#
# A charger with a flaky contact, or a dock renegotiating its power
# delivery, produces a burst of genuine connect/disconnect transitions --
# every one of them real, which is why this cannot be solved by being
# stricter about what counts as a transition. Five seconds is far longer
# than the ~720 ms a flash occupies, so a burst costs exactly one blink and
# the keyboard is never queued behind a backlog. Flashes lost to this are
# DROPPED, not deferred: a queue is precisely the machine-gun being avoided,
# and the acknowledgement is worthless once it is late.
FLASH_DEBOUNCE_SECONDS = 5.0

# The one mode a flash must never interrupt, because nothing anywhere can
# say what the keys were showing a moment ago: Ambient is repainted by a
# sampling thread in the GUI process, and only when the screen colour
# actually moves (see AMBIENT_MIN_DELTA). Flashing would leave the keys
# stuck on the flash colour until the desktop happened to change enough --
# indefinitely, on a still screen -- and the enforcer has no screen to
# sample even if it wanted to guess. Refusing is also what keeps the flash
# from fighting the sampler for the USB device, and means the sampler is
# never stopped and restarted.
#
# The three live-reading modes (LIVE_COLOUR_MODES) are handled differently,
# in :func:`flash_restore_args` below: rather than reconstructing what was
# LAST painted, the restore recomputes what the mode would paint RIGHT NOW
# from a fresh sensor reading. That is not a compromise -- it is what these
# modes are defined to show. "Frozen on whatever was last written" was
# already true of them before any flash existed, once the window closes;
# the flash does not make that truer.
FLASH_UNRESTORABLE_MODES = (AMBIENT_MODE,)


def charger_flash_enabled(cfg):
    """True when the user has asked for the flash. Opt-in, so a missing key
    is off -- an existing config must not start blinking after an update."""
    return bool((cfg or {}).get(CHARGER_FLASH_KEY))


def charger_flash_color(cfg):
    """The colour to blink, clamped, falling back to the stock one.

    Tolerant for the same reason :func:`profile_color` is: this is read on a
    path with no window to report into, and a hand-edited config must land on
    something paintable rather than take the plug event down with it."""
    stored = (cfg or {}).get(CHARGER_FLASH_COLOR_KEY)
    if not isinstance(stored, (list, tuple)) or len(stored) != 3:
        stored = DEFAULT_FLASH_COLOR
    return tuple(clamp_byte(value, default)
                 for value, default in zip(stored, DEFAULT_FLASH_COLOR))


def live_restore_color(mode, reading):
    """The colour a live-reading mode would paint right now, from a reading
    the caller already took, or ``None`` if that reading says there is
    nothing to show.

    Pure: reuses the exact functions :meth:`KeyboardPage._live_color` calls,
    so the enforcer's idea of "what Battery Level looks like" can never
    disagree with the GUI's. ``reading`` is mode-shaped rather than a single
    number because Battery Level needs both the percentage and the
    charging flag to pick a direction on the gradient:

    * ``"Battery Level"`` -- ``(percent, charging)``, as from
      :func:`hardware.read_battery`.
    * ``"CPU Temp Color"`` / ``"GPU Temp Color"`` -- a temperature in
      Celsius, or ``None`` for "no reading yet"."""
    if mode == "Battery Level":
        if reading is None:
            return None
        percent, charging = reading
        if percent is None:
            return None
        return battery_to_rgb(percent, charging)
    if mode in ("CPU Temp Color", "GPU Temp Color"):
        if reading is None:
            return None
        return temp_to_rgb(reading)
    return None


def flash_restore_args(cfg, caps=None, live_reading=None):
    """The helper call that puts the saved lighting mode back after a flash,
    or ``None`` when this mode's state cannot be reconstructed.

    ``None`` is not a failure and is not rare -- it is the guard that decides
    whether a flash may happen at all, and every caller is expected to give
    up on the flash entirely when it comes back. Working out the restore
    *first*, before anything is written, is the whole reason a flash can
    never strand the keyboard on the flash colour: there is no path that
    writes the first colour without already holding the second.

    Mode by mode:

    * Static, Breathing, Pulse, Colour Cycle, Rainbow and Gradient Static are
      one write the firmware then animates by itself, and every argument of
      that write is in the config. Re-sending it restores the mode exactly.
      A firmware animation does restart from the top rather than resuming
      mid-phase, which is not observable on a rainbow or a colour cycle and
      is the only part of the state that is genuinely not carried.
    * Profile Color resolves from the config as well, through the same call
      every other painter uses, so the flash cannot disagree with them about
      which colour the profile wears.
    * The three live-reading modes resolve through :func:`live_restore_color`
      using ``live_reading`` -- deliberately NOT read here, since this
      function is pure and a sensor read is not. The caller (the enforcer)
      takes the one reading this mode needs before calling in, and passes
      None here to mean "no reading was taken", which this treats the same
      as "the reading came back empty": no flash. That covers both the
      caller not having bothered (the mode is something else) and the
      hardware genuinely having nothing to say (no battery, no sensor yet).
    * Ambient returns None -- see FLASH_UNRESTORABLE_MODES for why it has no
      equivalent to a live reading.
    * No saved mode at all (a config written before the keyboard page was
      ever opened) returns None: there is nothing to go back to, so there is
      nothing to flash over.
    * A mode name this build does not know -- an older config, or one from a
      newer build -- returns None rather than falling through to
      :func:`helper_args`, which would answer single_static in the first
      colour and repaint a keyboard that was never on Static.
    """
    saved = (cfg or {}).get("kbd_rgb") or {}
    mode = saved.get("mode")
    if not mode or mode not in KBD_RGB_MODES:
        return None
    if mode in FLASH_UNRESTORABLE_MODES:
        return None
    if mode == PROFILE_COLOR_MODE:
        return profile_color_args(cfg, caps=caps)
    if mode in LIVE_COLOUR_MODES:
        color = live_restore_color(mode, live_reading)
        return None if color is None else static_args(color)
    return helper_args(mode, saved_color(saved),
                       saved_color(saved, "2", DEFAULT_COLOR2),
                       saved.get("speed"))


# How bright the keys are pushed for the wake pulse and the blink itself.
# Full, not some intermediate step: this is on screen for well under a
# second, so there is no reason to make it a dim, easy-to-miss version of an
# acknowledgement.
FLASH_VISIBLE_BRIGHTNESS = KBD_MAX


def kbd_brightness_args(level):
    """The helper call that sets the backlight LED class to ``level``."""
    return ("kbd", level)


def brightness_wake_args(current):
    """The one or two ``kbd`` writes that get the backlight physically lit
    before a flash, in order.

    Confirmed on real hardware (Strix G16 G614PR, N-Key controller) that
    this is necessary, and that a colour write alone is not: the keyboard
    has an EC-level idle timeout, independent of the LED class value, that
    physically cuts backlight power after inactivity. A ``kbdrgb`` colour
    write reaches the RGB controller either way and the helper call
    succeeds either way -- but if the EC has cut power, nothing is visible,
    because rogauracore and the LED class are talking to hardware that
    isn't listening. Reading the LED class back afterwards still shows
    whatever value was last written to it; it does not reveal whether the
    EC has since cut the physical power on its own idle timer, so there is
    no way to detect "already awake" from software and skip this.

    What wakes it, confirmed by direct test, is a WRITE to the LED class --
    any write, to any value, including the value already held. One write is
    enough when it changes the number (``current`` is not already
    :data:`FLASH_VISIBLE_BRIGHTNESS`). When it is already at that level, a
    same-value write was never tested and is not assumed to work, so a
    genuine round trip through a different level (down to :data:`KBD_MIN`,
    then up) is used instead -- two proven-good transitions rather than one
    unproven no-op."""
    current = KBD_MIN if current is None else current
    if current == FLASH_VISIBLE_BRIGHTNESS:
        return (kbd_brightness_args(KBD_MIN),
                kbd_brightness_args(FLASH_VISIBLE_BRIGHTNESS))
    return (kbd_brightness_args(FLASH_VISIBLE_BRIGHTNESS),)


def brightness_restore_args(original):
    """The single ``kbd`` write that puts the backlight level back to
    exactly what it was before :func:`brightness_wake_args` touched it.
    ``None`` (unreadable) restores to :data:`KBD_MIN` rather than guessing
    high, on the same reasoning as everywhere else in this module: a
    hand-edited or unreadable value must land somewhere valid, and off is
    the less surprising side to land on."""
    level = KBD_MIN if original is None else original
    level = max(KBD_MIN, min(KBD_MAX, level))
    return kbd_brightness_args(level)


def charger_flash_plan(cfg, caps=None, brightness=None, live_reading=None):
    """``(flash_args, restore_args, brightness_wake, brightness_restore)``
    for a charger transition, or ``None`` for "do not touch the keyboard".

    Pure, and deliberately the only place any of these five questions is
    asked, so a caller cannot honour four of them and forget the fifth:

    * the flash is opt-in, and off is the default;
    * a machine with no controllable keyboard colour never flashes. The
      switch is withheld from the page on such a machine, but a config
      copied from another laptop can still say yes, and that is exactly the
      case with no window open to report the failure into;
    * the saved mode has to be restorable -- see :func:`flash_restore_args`.
      ``live_reading`` is a pass-through for the three live-reading modes:
      the caller takes the one sensor reading the saved mode actually needs
      (or none, if it is not one of those three) before calling in, since
      that is the one piece of this decision that is not pure;
    * a flash colour identical to what the keys are already showing is
      invisible by definition, so it is skipped rather than spending
      ~720 ms of bus time on a blink nobody can see. This still applies
      even though brightness is bracketed on every flash now: the colour
      not moving is still nothing worth a blink for, on its own;
    * the backlight has to be woken first -- see :func:`brightness_wake_args`
      for why this cannot be made conditional on the current reading.
      ``brightness_wake`` and ``brightness_restore`` are always present
      (never ``None``) once every other check has passed: unlike the colour
      restore, there is no readable "already awake" state to skip this on.
    """
    if not charger_flash_enabled(cfg):
        return None
    if not (caps or {}).get("kbd_rgb", True):
        return None
    restore = flash_restore_args(cfg, caps, live_reading=live_reading)
    if restore is None:
        return None
    flash = static_args(charger_flash_color(cfg))
    if flash == restore:
        return None
    return (flash, restore, brightness_wake_args(brightness),
            brightness_restore_args(brightness))
