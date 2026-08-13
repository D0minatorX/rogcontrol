"""Fan curve mathematics, shared between the app and helper scripts.

This module provides functions for interpolating fan curves and converting
between percentages, PWM values, and RPM measurements.
"""

# rpm = floor + slope * curve_percent, per channel.
#
# Fan speed is NOT a straight fraction of max rpm, which is what this app
# assumed. Every channel idles at a hard floor of roughly 1650-1750 rpm --
# 0% on the curve does not stop the fan, and never did. Measured on this
# machine with flat curves at 11/31/50/70/100%, each held ~22s to settle,
# with the enforcer paused so nothing else could re-push a curve mid-test.
# Least-squares fit over those five points lands within 22-56 rpm on every
# channel, well under the 100 rpm granularity the hardware reports.
#
# This is what made the displayed numbers wrong: asking for a curve the app
# labelled 3400 rpm (50%) actually delivered ~4100, because the real
# mapping starts at the floor rather than at zero.
# Measured ceilings at a flat 100% curve, settled a full minute, were
# 6600 / 6500 / 7800 rpm -- the CPU and GPU fans physically top out a few
# hundred rpm below the 6800 nominal spec figure this app used to assume.
#
# NOTE FOR OTHER MACHINES: these numbers were measured on one ROG Strix
# G614PR. The *shape* of the relationship (an rpm floor plus a linear
# response, rather than a plain fraction of maximum) should hold on any
# ASUS laptop using this fan interface, but the exact floor and slope will
# differ per model and per fan. If your reported rpm looks off, re-measure:
# set a flat curve at a few known percentages, note the settled rpm for
# each, and fit floor/slope. Only the numbers below need changing --
# nothing else depends on the specific values.
FAN_RPM_CAL = {
    "1": (1655, 49.3),   # CPU Fan:   0% = 1655 rpm, 100% = 6585 rpm
    "2": (1643, 48.8),   # GPU Fan:   0% = 1643 rpm, 100% = 6523 rpm
    "3": (1734, 60.8),   # Mid Fan:   0% = 1734 rpm, 100% = 7814 rpm
}


def get_rpm_cal(config, channel):
    """Calibration for one fan: the user's own measured values if they have
    run the calibration, otherwise the built-in ones. The built-ins came
    off a single ROG Strix G614PR, so on any other machine they are a rough
    guess until Calibrate is run -- which is exactly why the button exists."""
    saved = (config or {}).get("fan_rpm_cal", {}).get(channel)
    if isinstance(saved, (list, tuple)) and len(saved) == 2:
        try:
            floor, slope = float(saved[0]), float(saved[1])
            if slope > 0:
                return (floor, slope)
        except (TypeError, ValueError):
            pass
    return FAN_RPM_CAL.get(channel)


def interpolate_curve(points, n=8):
    """Expand a user curve to exactly n points for the firmware.

    The user's own points are preserved verbatim whenever they fit. Extra
    slots are filled by bisecting the widest temperature gap, so the added
    points sit on the straight line the user already drew between their own
    points.

    This is now a load-time conversion rather than an apply-time one: the
    editor holds eight points, so a curve saved by the old six-point editor
    is expanded once, on load, and the user sees all eight. Given eight
    points it returns them unchanged, which is what makes re-loading a
    converted curve stable -- it does not drift a little further every time
    the profile is opened.

    The previous version resampled by *index*, which silently moved every
    interior point: a 6-point curve came back as 8 points at completely
    different temperatures, so a point placed at 60C ended up as steps at
    57C and 61C and the curve the firmware ran was not the one on screen.
    That matters because the EC steps between points rather than
    interpolating, so a moved point moves where the fan audibly changes.
    """
    pts = sorted({(int(t), int(p)) for t, p in points})
    if len(pts) >= n:
        return pts[:n]

    while len(pts) < n:
        # widest gap first, so added points are spread out evenly
        gaps = [(pts[i + 1][0] - pts[i][0], i) for i in range(len(pts) - 1)]
        gap, i = max(gaps) if gaps else (0, 0)
        if gap >= 2:
            t = (pts[i][0] + pts[i + 1][0]) // 2
            p = round((pts[i][1] + pts[i + 1][1]) / 2)
            pts.insert(i + 1, (t, p))
            continue
        # No gap left to split (points are adjacent degrees). Extend past
        # the top point instead, holding its percentage, so temps stay
        # strictly increasing -- the firmware needs 8 distinct entries.
        last_t, last_p = pts[-1]
        if last_t < 100:
            pts.append((min(100, last_t + 1), last_p))
        else:
            first_t, first_p = pts[0]
            if first_t <= 0:
                break  # nowhere left to go; return what we have
            pts.insert(0, (first_t - 1, first_p))
    return pts[:n]


def pct_to_pwm255(pct):
    """Convert fan percentage (0-100) to PWM value (0-255)."""
    return round(max(0, min(100, pct)) / 100 * 255)


def curve_to_flat(points, n=8):
    """The exact argument list ``rogcontrol-helper fan <channel>`` wants:
    ``[temp1, pwm1, ... temp8, pwm8]``.

    Every caller that writes a curve did this expansion by hand -- the old
    app, the enforcer and the apply script each had their own copy of the
    same three lines. The helper rejects anything that is not exactly 16
    values, so getting it wrong is a failed write rather than a wrong curve,
    but there is no reason for three copies of it to exist."""
    flat = []
    for temp, pct in interpolate_curve(points, n):
        flat.append(int(temp))
        flat.append(pct_to_pwm255(pct))
    return flat


def curve_matches_hardware(points, hardware_pairs, n=8):
    """True if ``hardware_pairs`` is already this curve.

    ``hardware_pairs`` is what the driver reports back as (temp, pwm) --
    pwm, not percent, because that is what is written and what is read.
    Comparing in pwm rather than percent is deliberate: 0-100 does not map
    onto 0-255 one to one, so a curve rounded to pwm and back would differ
    from itself and the page would claim an unsaved change forever.

    Caveat worth restating from the old app: those sysfs files are the
    kernel driver's cache of what was last written, not a read of the EC, so
    a match proves "we wrote this", not "the fan is running it". That is why
    the page checks ``pwmN_enable`` alongside this -- the EC dropping the
    curve leaves the points matching and the enable flag at 2."""
    if not hardware_pairs or len(hardware_pairs) != n:
        return False
    want = curve_to_flat(points, n)
    got = []
    for pair in hardware_pairs:
        if pair is None or len(pair) != 2 or None in pair:
            return False
        got.append(int(pair[0]))
        got.append(int(pair[1]))
    return want == got


# The editor works in a fixed number of points, and this is it: eight, the
# same eight the embedded controller stores. Every handle on screen is one
# firmware slot, so what the user drags is what the fan runs.
#
# The old editor offered six and let interpolate_curve invent the other two
# at apply time. Those two were real steps in the curve the fan ran, placed
# by an algorithm, and they were not on screen -- so the curve being tuned
# and the curve being executed were never quite the same object. Eight
# handles are fiddlier with a mouse, which is why the arrow keys exist.
EDITOR_POINTS = 8

# Coldest and hottest a point may sit at, and the closest two points may get.
# The gap is not cosmetic: the firmware wants eight strictly increasing
# temperatures, and two points sharing one leaves interpolate_curve nothing
# to bisect.
TEMP_MIN, TEMP_MAX = 0, 100
PCT_MIN, PCT_MAX = 0, 100
MIN_TEMP_GAP = 1


def _clamp(value, low, high):
    return max(low, min(high, value))


def editor_points(points, count=EDITOR_POINTS, min_gap=MIN_TEMP_GAP):
    """Exactly ``count`` ordered points, whatever came in.

    The editor's invariant lives here rather than in the widget so it can be
    tested without a display: integers, inside the axes, sorted, and with
    strictly increasing temperatures. Everything the editor loads goes
    through it -- a stock profile carries four points, curves saved by the
    old six-point editor carry six, and an imported one could carry anything.

    Fewer points than ``count`` are filled in by interpolate_curve, which
    keeps the user's own points and bisects the widest gaps, so expanding a
    six point curve to eight leaves all six exactly where they were: the two
    added points land in the middle of the widest gaps, on the line already
    drawn between them, and nothing the user tuned moves."""
    pts = [[int(t), int(p)] for t, p in points]
    if not pts:
        # A profile with no curve at all. A flat mid-range ramp is a curve
        # the user can drag, where an empty graph is one they cannot.
        pts = [[40, 20], [90, 90]]
    pts = [[_clamp(int(t), TEMP_MIN, TEMP_MAX), _clamp(int(p), PCT_MIN, PCT_MAX)]
           for t, p in interpolate_curve(pts, count)]

    # interpolate_curve gives up rather than inventing points when a curve is
    # squeezed into fewer degrees than it has points (six points between 50C
    # and 53C, say). Pad by hand so the editor always has its full set.
    while len(pts) < count:
        last_t, last_p = pts[-1]
        if last_t < TEMP_MAX:
            pts.append([min(TEMP_MAX, last_t + min_gap), last_p])
        else:
            first_t, first_p = pts[0]
            pts.insert(0, [max(TEMP_MIN, first_t - min_gap), first_p])

    # Push forwards, then pull backwards. One pass alone is not enough: the
    # forward pass can shove the hottest point past 100C, and the backward
    # pass is what walks that back down through the ones behind it.
    for i in range(1, len(pts)):
        pts[i][0] = max(pts[i][0], min(TEMP_MAX, pts[i - 1][0] + min_gap))
    for i in range(len(pts) - 2, -1, -1):
        pts[i][0] = min(pts[i][0], max(TEMP_MIN, pts[i + 1][0] - min_gap))
    return pts


def move_point(points, index, temp, pct, min_gap=MIN_TEMP_GAP):
    """``points`` with point ``index`` dragged to (``temp``, ``pct``).

    A point may not overtake its neighbours: the curve is a function of
    temperature, and letting one cross another would reorder the list under
    the hand still dragging it -- the drag would jump to a different point
    mid-gesture. Instead the point stops one degree short of the neighbour,
    which is what makes a hard drag against the point beside it feel like a
    wall rather than a swap."""
    pts = [[int(t), int(p)] for t, p in points]
    if not 0 <= index < len(pts):
        return pts
    low = TEMP_MIN if index == 0 else pts[index - 1][0] + min_gap
    high = TEMP_MAX if index == len(pts) - 1 else pts[index + 1][0] - min_gap
    # Only reachable if the incoming list already broke the invariant, in
    # which case the neighbour on the left wins: the alternative silently
    # reorders the curve.
    high = max(low, high)
    pts[index] = [_clamp(int(round(temp)), low, high),
                  _clamp(int(round(pct)), PCT_MIN, PCT_MAX)]
    return pts


def pct_to_rpm(pct, floor, slope):
    """Fan percentage to rpm using this machine's measured calibration.

    NOTE: the argument order differs from the older ``pct_to_rpm(rpm_cal, pct)``
    in rogcontrol.py -- this one takes the percentage first and the calibration
    unpacked, because that is what callers of this module have to hand. The
    percentage is clamped to 0-100 exactly as the older function does: the
    calibration is a straight line fitted over the 0-100 curve range only, so
    extrapolating past either end reports an rpm the fan cannot physically
    reach (150% would claim 9050 rpm on a fan that tops out at 6585).
    """
    return round(floor + slope * max(0, min(100, pct)))


def rpm_to_pct(rpm, floor, slope):
    """The curve percentage a live rpm reading corresponds to.

    The inverse of pct_to_rpm, and clamped for the same reason at the bottom
    end: a fan idling a few rpm below the fitted floor would otherwise report
    a negative percentage."""
    if slope <= 0:
        return None
    return round(max(0, min(100, (rpm - floor) / slope)))


def fit_rpm_cal(samples):
    """Least-squares (floor, slope) over measured ``(percent, rpm)`` pairs.

    Returns None when the data cannot describe a fan: fewer than two usable
    readings, every reading taken at the same percentage (no gradient to
    fit), or a slope that is not positive -- which means the fan never
    responded, and saving that calibration would make every rpm figure in
    the app wrong in a way the user cannot see. Keeping the previous
    calibration is always better than adopting a bad one."""
    pts = [(float(pct), float(rpm)) for pct, rpm in samples if rpm is not None]
    if len(pts) < 2:
        return None
    n = len(pts)
    sx = sum(p for p, _ in pts)
    sy = sum(r for _, r in pts)
    sxy = sum(p * r for p, r in pts)
    sxx = sum(p * p for p, _ in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    floor = (sy - slope * sx) / n
    if slope <= 0:
        return None
    return (round(floor, 1), round(slope, 2))
