"""Overview: the page worth leaving open.

Everything here is read-only. The one idea it exists to serve is that a fan
reading is meaningless on its own -- what matters is the gap between the rpm
the fan is doing and the rpm the active curve asks for at the temperature the
EC is actually seeing. That gap is how this machine's fan problem was found,
and it is why every fan row carries both numbers.

The whole sample -- sysfs reads plus a pair of nvidia-smi calls that cost a
couple of hundred milliseconds each -- runs on a worker thread through
``window.apply_async``. Only the rendering touches widgets, and neither
nvidia-smi runs at all on a machine with no card.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import hardware  # noqa: E402
from ..fancurve import get_rpm_cal, interpolate_curve, pct_to_rpm  # noqa: E402

REFRESH_SECONDS = 2
DASH = "—"

# One MiB in GiB, since both memory readers answer in MiB.
MIB_PER_GIB = 1024


def format_used_total(used_mib, total_mib):
    """``"7.1 / 30.5 GiB"``, or a dash if either half is missing.

    GiB for both rows, including VRAM, which nvidia-smi reports in MiB.
    "1.9 / 11.9 GiB" beside "7.1 / 30.5 GiB" can be compared at a glance;
    "1920 / 12227 MiB" beside it cannot, and the two rows sit together.

    Both halves or neither: a used figure with no total to read it against
    is not the thing this row exists to say."""
    if used_mib is None or total_mib is None:
        return DASH
    return (f"{used_mib / MIB_PER_GIB:.1f} / "
            f"{total_mib / MIB_PER_GIB:.1f} GiB")


def curve_percent_at(points, temp_c, n=8):
    """Fan percentage the firmware runs at ``temp_c``, or None.

    The EC *steps* between curve points rather than interpolating between
    them -- it holds a point's percentage until the next point's temperature
    is reached -- so this looks up the last point at or below the current
    temperature instead of drawing a line through them. Reading it as a
    linear ramp would show a target the fans never actually take.

    The points are expanded to the firmware's eight first, so the number
    shown is what the hardware holds rather than what the editor drew."""
    try:
        pts = interpolate_curve(points, n)
    except (TypeError, ValueError):
        return None
    if not pts:
        return None
    pct = pts[0][1]
    for temp, point_pct in pts:
        if temp_c >= temp:
            pct = point_pct
        else:
            break
    return pct


class OverviewPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._sampling = False
        self._timer_id = None
        self._build()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _value_row(self, group, title, subtitle=""):
        """An ActionRow whose suffix label is the live value."""
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        label = Gtk.Label(label=DASH)
        # "numeric" gives tabular figures, so a number changing width does
        # not make the whole column jitter twice a second.
        label.add_css_class("numeric")
        label.add_css_class("dim-label")
        row.add_suffix(label)
        group.add(row)
        return row, label

    def _build(self):
        cpu = Adw.PreferencesGroup(title="CPU")
        self.add(cpu)
        self.cpu_temp_row, self.cpu_temp_val = self._value_row(
            cpu, "Temperature", "Tctl, the sensor the fan controller reads")
        self.cpu_clock_row, self.cpu_clock_val = self._value_row(
            cpu, "Peak core clock", "Highest core, averaged by the hardware")
        self.cpu_power_row, self.cpu_power_val = self._value_row(
            cpu, "Package power", "Whole-package draw, what the limits cap")

        gpu = Adw.PreferencesGroup(title="GPU")
        self.add(gpu)
        self.gpu_temp_row, self.gpu_temp_val = self._value_row(
            gpu, "Temperature")
        self.gpu_power_row, self.gpu_power_val = self._value_row(
            gpu, "Power draw")
        self.vram_row, self.vram_val = self._value_row(
            gpu, "VRAM", "The NVIDIA card's own memory")
        if not self.window.caps.get("nvidia"):
            for row in (self.gpu_temp_row, self.gpu_power_row, self.vram_row):
                row.set_subtitle("nvidia-smi not available on this machine")

        memory = Adw.PreferencesGroup(title="Memory")
        self.add(memory)
        self.ram_row, self.ram_val = self._value_row(
            memory, "RAM",
            "In use against installed — what is left is what a program can "
            "still have")

        fans = Adw.PreferencesGroup(
            title="Fans",
            description="Measured speed, against what the active curve asks "
                        "for at the current CPU temperature.")
        self.add(fans)
        self.fan_rows = {}
        self.fan_vals = {}
        for channel in hardware.FAN_CHANNELS:
            row, label = self._value_row(
                fans, hardware.FAN_LABELS[channel], "curve target " + DASH)
            self.fan_rows[channel] = row
            self.fan_vals[channel] = label

        battery = Adw.PreferencesGroup(title="Battery")
        self.add(battery)
        self.battery_row, self.battery_val = self._value_row(battery, "Charge")
        self.limit_row, self.limit_val = self._value_row(
            battery, "Charge limit", "Threshold the firmware is holding")

        status = Adw.PreferencesGroup(
            title="Status",
            description="The failure this app exists to catch: the EC drops "
                        "the custom curve on its own, and nothing else says "
                        "so.")
        self.add(status)
        self.curve_row, self.curve_val = self._value_row(
            status, "Custom fan curve")

    # -- refresh -------------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self):
        # Nothing to update while this page is off screen -- the stack unmaps
        # the pages you are not looking at, and a window started with
        # --minimized is unmapped entirely. Without this check a hidden
        # window would still fork an nvidia-smi every two seconds forever.
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE
        # A sample that has not come back yet must not start another: on a
        # machine where nvidia-smi is slow, a 2 second timer would otherwise
        # pile threads up faster than they finish.
        if not self._sampling:
            self._sampling = True
            self.window.apply_async(self._sample, self._on_sample)
        return GLib.SOURCE_CONTINUE

    def _sample(self):
        """Read everything. Runs on a worker thread -- no widgets in here."""
        have_nvidia = self.window.caps.get("nvidia")
        gpu_temp, gpu_power = (hardware.read_nvidia_stats()
                               if have_nvidia else (None, None))
        # Skipped without a card for the same reason the two above are: the
        # exec fails immediately and would still cost a fork every two
        # seconds to arrive back at the dash it starts on.
        vram = hardware.read_vram() if have_nvidia else (None, None)
        percent, charging = hardware.read_battery()
        return {
            "cpu_temp": hardware.read_cpu_temp(),
            "cpu_clock": hardware.read_peak_core_clock_mhz(),
            "pkg_power": hardware.read_package_power_w(),
            "gpu_temp": gpu_temp,
            "gpu_power": gpu_power,
            "ram": hardware.read_memory(),
            "vram": vram,
            "fan_rpm": hardware.read_fan_rpms(),
            "curve_enabled": hardware.read_fan_curve_enabled(),
            "battery": (percent, charging),
            "charge_limit": hardware.read_charge_limit(),
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is not None:
            # One failed sample is not worth a toast every two seconds; the
            # traceback is already on stderr from apply_async.
            return
        self._render(data)

    def _render(self, data):
        cpu_temp = data.get("cpu_temp")
        self.cpu_temp_val.set_text(
            DASH if cpu_temp is None else f"{cpu_temp:.1f} °C")
        clock = data.get("cpu_clock")
        self.cpu_clock_val.set_text(
            DASH if clock is None else f"{clock} MHz")
        power = data.get("pkg_power")
        self.cpu_power_val.set_text(
            DASH if power is None else f"{power:.1f} W")

        gpu_temp = data.get("gpu_temp")
        self.gpu_temp_val.set_text(
            DASH if gpu_temp is None else f"{gpu_temp:.0f} °C")
        gpu_power = data.get("gpu_power")
        self.gpu_power_val.set_text(
            DASH if gpu_power is None else f"{gpu_power:.1f} W")
        self.vram_val.set_text(
            format_used_total(*(data.get("vram") or (None, None))))

        self.ram_val.set_text(
            format_used_total(*(data.get("ram") or (None, None))))

        self._render_fans(data.get("fan_rpm") or {}, cpu_temp)
        self._render_battery(data)
        self._render_curve_state(data.get("curve_enabled") or {})

    def _render_fans(self, rpms, cpu_temp):
        curves = (self.window.current_profile() or {}).get("fans") or {}
        for channel in hardware.FAN_CHANNELS:
            rpm = rpms.get(channel)
            self.fan_vals[channel].set_text(
                DASH if rpm is None else f"{rpm} rpm")

            points = curves.get(channel)
            pct = (None if (points is None or cpu_temp is None)
                   else curve_percent_at(points, cpu_temp))
            cal = get_rpm_cal(self.window.config, channel)
            if pct is None or cal is None:
                self.fan_rows[channel].set_subtitle(f"curve target {DASH}")
                continue
            target = pct_to_rpm(pct, cal[0], cal[1])
            # One decimal, matching the temperature row: the curve steps, so
            # rounding 67.5 to "68" here would name a temperature whose step
            # is a different percentage from the one shown.
            self.fan_rows[channel].set_subtitle(
                f"curve asks {target} rpm ({pct}%) at {cpu_temp:.1f} °C")

    def _render_battery(self, data):
        percent, charging = data.get("battery") or (None, None)
        if percent is None:
            self.battery_val.set_text(DASH)
            self.battery_row.set_subtitle("")
        else:
            self.battery_val.set_text(f"{percent}%")
            self.battery_row.set_subtitle(
                "charging" if charging else "on battery or held at limit")
        limit = data.get("charge_limit")
        self.limit_val.set_text(DASH if limit is None else f"{limit}%")

    def _render_curve_state(self, state):
        known = {ch: val for ch, val in state.items() if val is not None}
        for css in ("success", "warning", "error"):
            self.curve_val.remove_css_class(css)
        if not known:
            self.curve_val.set_text("not available")
            self.curve_row.set_subtitle(
                "No asus_custom_fan_curve interface on this machine")
            return
        dropped = sorted(ch for ch, val in known.items() if not val)
        if not dropped:
            self.curve_val.set_text("held by EC")
            self.curve_val.add_css_class("success")
            self.curve_row.set_subtitle(
                f"All {len(known)} fans are running the curve you set")
            return
        names = ", ".join(hardware.FAN_LABELS[ch] for ch in dropped)
        self.curve_val.set_text("dropped")
        self.curve_val.add_css_class("warning")
        self.curve_row.set_subtitle(
            f"The EC has taken {names} back onto its own curve — "
            "re-apply the fan curves")

    # -- shell hooks ---------------------------------------------------------

    def reload(self):
        """The active profile changed: the curve targets are now different."""
        self._tick()

    def self_test_tick(self):
        """One synchronous read-and-render, no thread, for ``--self-test``."""
        self._render(self._sample())
