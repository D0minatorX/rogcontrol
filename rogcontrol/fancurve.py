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

    The user's own points are preserved verbatim whenever they fit (the
    hardware takes 8, the editor allows at most 6). Extra slots are filled
    by bisecting the widest temperature gap, so the added points sit on the
    straight line the user already drew between their own points.

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


def pct_to_rpm(pct, floor, slope):
    """Fan percentage to rpm using this machine's measured calibration."""
    return round(floor + slope * pct)
