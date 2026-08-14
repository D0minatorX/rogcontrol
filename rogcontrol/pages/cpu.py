"""CPU page: power limits and tuning, applied as you leave the control alone.

There is no Apply button here. A control that has not moved for 400 ms is
applied on a worker thread and confirmed by a toast naming what changed; a
failure toasts the error and puts the control back where it was, so the
widget never claims a setting the hardware refused.

Two hardware facts shape the code:

* ryzenadj takes all five power values in a single call, so any one of them
  moving re-sends the set. That also means a failure invalidates all five,
  which is why the revert restores the whole group rather than one row.
* The kHz clock cap has to be written *after* the boost switch: writing
  cpufreq's boost refreshes every policy and takes the ceiling back up to
  hardware maximum with it.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.slider_row import SliderRow, align_value_widths  # noqa: E402

# How long a control has to sit still before it is applied. Long enough to
# swallow a drag across a slider's whole range, short enough that the toast
# still feels like a response to what you just did. The sliders do this
# waiting themselves -- they only emit once the handle has stopped -- so this
# is also what the switch row and the busy-retry below wait.
DEBOUNCE_MS = 400

# A slider that reports in has already settled, so the group timer exists
# only to merge controls that settle together (moving STAPM and then Fast
# within the same instant) into a single ryzenadj call. Waiting another full
# DEBOUNCE_MS there would double the delay the user feels.
COALESCE_MS = 20

# How often the live fan reading is refreshed, matching the Overview and GPU
# pages so a fan does not appear to be doing two different speeds depending
# on which page you are looking at.
REFRESH_SECONDS = 2
DASH = "—"

# The asus hwmon's fan1. Its label comes from hardware, so this page, the GPU
# page and the Overview all name the same fan the same way.
FAN_CHANNEL = "1"

# (key, title, subtitle, min, max, unit). Watts and degrees as the user sees
# them; the config and the helper both work in milliwatts for the first three.
#
# The unit belongs to the value, not the title: the slider's readout shows
# "35 W", so the title does not have to carry a "(W)" to disambiguate it from
# the 80 next to it.
LIMIT_ROWS = (
    ("stapm", "STAPM limit",
     "Sustained package power. The ceiling the chip settles at.", 15, 150, "W"),
    ("fast", "Fast limit",
     "Short-burst ceiling, a few seconds at a time.", 15, 165, "W"),
    ("slow", "Slow limit",
     "Medium-term ceiling, between the fast and sustained windows.",
     15, 150, "W"),
    ("temp", "Temperature target",
     "Tctl the chip throttles itself to hold.", 60, 100, "°C"),
)

COALL_SUBTITLE = (
    "All-core undervolt. Negative runs cooler and often slightly faster, "
    "because the chip has more thermal headroom to boost.\n"
    "Too negative freezes the machine under load — this laptop locked solid "
    "at −20. Move two or three counts at a time and test under load before "
    "going further. 0 is stock."
)

BOOST_SUBTITLE = (
    "Off pins every core at its base clock. Worth trying if the fans surge at "
    "idle: the EC reads the raw hottest core, and a boost spike hits 85–90 °C "
    "for a few milliseconds even while the reported temperature sits near "
    "57 °C — enough to send the fans to the top of the curve."
)


class CpuPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.caps = window.caps
        # True while values are being written into the widgets from the
        # profile, so loading a profile cannot look like the user turning a
        # dial and fire an apply for every row on the page.
        self._loading = True
        self._timers = {}
        self._pending = {}
        self._busy = {}
        # Last values known to have reached the hardware, for putting a
        # control back after a rejected apply.
        self._applied = {}
        self._sampling = False
        self._timer_id = None

        self.rows = {}
        self._build()
        self.reload()
        self._loading = False
        # One read straight away, so the fan is a number the moment the page
        # is opened rather than a dash until the first interval elapses.
        self._start_sample()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        status = Adw.PreferencesGroup(title="Processor")
        self.add(status)
        self.fan_row, self.fan_value = self._live_row(
            status, hardware.FAN_LABELS[FAN_CHANNEL])
        if not self.caps.get("fan_rpm"):
            self.fan_row.set_subtitle("No asus hwmon fan reading on this "
                                      "machine")

        limits = Adw.PreferencesGroup(
            title="Power limits",
            description="Sent to ryzenadj as one set — moving any of them "
                        "re-sends all four.")
        self.add(limits)
        for key, title, subtitle, low, high, unit in LIMIT_ROWS:
            row = SliderRow(title=title, subtitle=subtitle, minimum=low,
                            maximum=high, step=1, unit=unit,
                            settle_ms=DEBOUNCE_MS)
            row.connect("changed", self._on_limit_changed, key, title)
            limits.add(row)
            self.rows[key] = row
        # One readout width across the group, so the four scales end in a
        # column instead of stopping wherever "150 W" and "100 °C" happen to.
        align_value_widths([self.rows[key] for key, *_ in LIMIT_ROWS])

        tuning = Adw.PreferencesGroup(title="Tuning")
        self.add(tuning)

        coall = SliderRow(title="Curve Optimizer", subtitle=COALL_SUBTITLE,
                          minimum=hardware.COALL_MIN,
                          maximum=hardware.COALL_MAX, step=1,
                          settle_ms=DEBOUNCE_MS)
        coall.connect("changed", self._on_limit_changed,
                      "coall", "Curve Optimizer")
        tuning.add(coall)
        self.rows["coall"] = coall

        boost = Adw.SwitchRow()
        boost.set_title("Turbo boost")
        boost.set_subtitle(BOOST_SUBTITLE)
        boost.connect("notify::active", self._on_boost_changed)
        tuning.add(boost)
        self.rows["boost"] = boost

        clock_range = self.caps.get("cpu_clock") or (400000, 5000000)
        self.min_ghz = clock_range[0] / 1e6
        self.max_ghz = clock_range[1] / 1e6
        # One decimal, and a step to match: the top of this machine's range is
        # 3.2 GHz, and a whole-number slider could not express it.
        clock = SliderRow(
            title="Maximum core clock", minimum=self.min_ghz,
            maximum=self.max_ghz, step=0.1, digits=1, unit="GHz",
            settle_ms=DEBOUNCE_MS,
            subtitle=f"Hard ceiling; cores still idle down freely. "
                     f"{self.max_ghz:.1f} means no limit.")
        clock.connect("changed", self._on_clock_changed)
        tuning.add(clock)
        self.rows["clock"] = clock

        self._apply_capability_gating(limits, tuning)

    def _live_row(self, group, title):
        """An ActionRow whose suffix label carries the live reading."""
        row = Adw.ActionRow(title=title)
        # "numeric" is tabular figures, so a value changing width does not
        # shuffle the column sideways twice a second.
        label = Gtk.Label(label=DASH)
        label.add_css_class("numeric")
        label.add_css_class("dim-label")
        row.add_suffix(label)
        group.add(row)
        return row, label

    def _apply_capability_gating(self, limits_group, _tuning_group):
        """Grey out what this machine cannot do, and say why.

        A control left live that silently does nothing is worse than one
        that is visibly unavailable, which is the whole reason capabilities
        are probed at all."""
        if not self.caps.get("ryzenadj"):
            for key in ("stapm", "fast", "slow", "temp", "coall"):
                self.rows[key].set_sensitive(False)
            limits_group.set_description(
                "ryzenadj is not installed — power limits and Curve "
                "Optimizer are unavailable. Boost and the clock ceiling go "
                "through cpufreq and still work.")
        if not self.caps.get("cpu_boost"):
            self.rows["boost"].set_sensitive(False)
            self.rows["boost"].set_subtitle(
                "No cpufreq boost switch on this machine.")
        if not self.caps.get("cpu_clock"):
            self.rows["clock"].set_sensitive(False)
            self.rows["clock"].set_subtitle(
                "No cpufreq clock limit on this machine.")

    # -- loading -------------------------------------------------------------

    @staticmethod
    def _clamp(row, value):
        adj = row.get_adjustment()
        return max(adj.get_lower(), min(adj.get_upper(), value))

    def reload(self):
        """Put the active profile's values on screen without applying them."""
        was_loading = self._loading
        self._loading = True
        try:
            cpu = (self.window.current_profile() or {}).get("cpu") or {}
            values = {
                # The config keeps the first three in milliwatts, which is
                # what ryzenadj wants; the page shows watts.
                "stapm": cpu.get("stapm", 55000) / 1000,
                "fast": cpu.get("fast", 65000) / 1000,
                "slow": cpu.get("slow", 55000) / 1000,
                "temp": cpu.get("temp", 90),
                "coall": cpu.get("coall", 0),
            }
            for key, value in values.items():
                row = self.rows[key]
                row.set_value(self._clamp(row, value))
                self._applied[key] = row.get_value()

            boost = bool(cpu.get("boost", True))
            self.rows["boost"].set_active(boost)
            self._applied["boost"] = boost

            # max_freq of 0 (or absent) is this config's way of saying "no
            # ceiling", which on screen is the top of the range.
            max_freq = cpu.get("max_freq") or 0
            ghz = self.max_ghz if not max_freq else max_freq / 1e6
            row = self.rows["clock"]
            row.set_value(self._clamp(row, ghz))
            self._applied["clock"] = row.get_value()
        finally:
            self._loading = was_loading

    # -- live fan reading ----------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self):
        # The stack unmaps the pages nobody is looking at, and a window
        # started with --minimized is unmapped entirely; neither needs a
        # reading taken for it.
        if self.get_mapped():
            self._start_sample()
        return GLib.SOURCE_CONTINUE

    def _start_sample(self):
        if self._sampling:
            return
        self._sampling = True
        self.window.apply_async(self._sample, self._on_sample)

    def _sample(self):
        """Worker thread: one sysfs read, no widgets."""
        return hardware.read_fan_rpms().get(FAN_CHANNEL)

    def _on_sample(self, rpm, error):
        self._sampling = False
        if error is not None:
            # One failed read is not worth a toast every two seconds; the
            # traceback is already on stderr from apply_async.
            return
        self._render(rpm)

    def _render(self, rpm):
        # A dash, not a zero: a fan that cannot be read is not a fan that has
        # stopped, and "0 rpm" is the reading that would send someone hunting
        # a hardware fault that is not there.
        self.fan_value.set_text(DASH if rpm is None else f"{rpm} rpm")

    # -- change handling -----------------------------------------------------

    def _on_limit_changed(self, row, _value, key, title):
        if self._loading:
            return
        self._schedule("limits", f"{title} set to {row.get_display_value()}")

    def _on_boost_changed(self, row, _param):
        if self._loading:
            return
        state = "on" if row.get_active() else "off"
        # A switch has no drag to wait out, but two quick toggles should
        # still be one apply, so it keeps the full debounce.
        self._schedule("boost", f"Turbo boost {state}", DEBOUNCE_MS)

    def _on_clock_changed(self, row, _value):
        if self._loading:
            return
        ghz = row.get_value()
        if ghz >= self.max_ghz - 0.05:
            label = "Clock ceiling removed"
        else:
            label = f"Clock ceiling set to {ghz:.1f} GHz"
        self._schedule("clock", label)

    def _schedule(self, group, label, delay=COALESCE_MS):
        """Apply ``group`` once its controls have stopped arriving.

        The slider has already waited DEBOUNCE_MS for the handle to stop, so
        the default here is only long enough to merge two controls that
        settled together."""
        self._pending[group] = label
        source = self._timers.pop(group, None)
        if source is not None:
            GLib.source_remove(source)
        self._timers[group] = GLib.timeout_add(delay, self._fire, group)

    def _fire(self, group):
        self._timers.pop(group, None)
        if self._busy.get(group):
            # The previous apply for this group is still in flight. Wait it
            # out rather than firing two ryzenadj calls at once, which would
            # race over which one the hardware ends up holding.
            self._timers[group] = GLib.timeout_add(
                DEBOUNCE_MS, self._fire, group)
            return GLib.SOURCE_REMOVE
        label = self._pending.pop(group, "Setting")
        self._busy[group] = True
        {"limits": self._apply_limits,
         "boost": self._apply_boost,
         "clock": self._apply_clock}[group](label)
        return GLib.SOURCE_REMOVE

    # -- applying ------------------------------------------------------------

    def _finish(self, group, label, result, error, on_success, restore):
        """Common tail of every apply: toast, then either save or roll back."""
        self._busy[group] = False
        if error is not None:
            ok, message = False, str(error)
        else:
            ok, message = result
        if ok:
            on_success()
            config_mod.save_config(self.window.config)
            self.window.toast(f"{label}.")
        else:
            self.window.toast(f"{label} failed: {message}")
            restore()

    def _restore(self, keys):
        """Put controls back to the last values the hardware accepted."""
        self._loading = True
        try:
            for key in keys:
                value = self._applied.get(key)
                if value is None:
                    continue
                row = self.rows[key]
                if key == "boost":
                    row.set_active(bool(value))
                else:
                    row.set_value(value)
        finally:
            self._loading = False

    def _apply_limits(self, label):
        stapm = int(self.rows["stapm"].get_value()) * 1000
        fast = int(self.rows["fast"].get_value()) * 1000
        slow = int(self.rows["slow"].get_value()) * 1000
        temp = int(self.rows["temp"].get_value())
        coall = int(self.rows["coall"].get_value())

        if not self.caps.get("ryzenadj"):
            self._busy["limits"] = False
            self.window.toast("ryzenadj is not installed")
            return

        def work():
            return hardware.run_helper("cpu", stapm, fast, slow, temp, coall)

        def success():
            data = (self.window.current_profile() or {}).setdefault("cpu", {})
            data.update({"stapm": stapm, "fast": fast, "slow": slow,
                         "temp": temp, "coall": coall})
            for key in ("stapm", "fast", "slow", "temp", "coall"):
                self._applied[key] = self.rows[key].get_value()

        self.window.apply_async(work, lambda result, error: self._finish(
            "limits", label, result, error, success,
            lambda: self._restore(("stapm", "fast", "slow", "temp", "coall"))))

    def _apply_boost(self, label):
        state = self.rows["boost"].get_active()

        def work():
            return hardware.run_helper("cpuboost", 1 if state else 0)

        def success():
            data = (self.window.current_profile() or {}).setdefault("cpu", {})
            data["boost"] = state
            self._applied["boost"] = state
            # cpufreq's boost switch refreshes every policy and takes the
            # ceiling back to hardware maximum with it, so any clock cap has
            # to be written again behind it.
            if self.caps.get("cpu_clock"):
                self._schedule("clock", "Clock ceiling re-applied")

        self.window.apply_async(work, lambda result, error: self._finish(
            "boost", label, result, error, success,
            lambda: self._restore(("boost",))))

    def _apply_clock(self, label):
        ghz = self.rows["clock"].get_value()
        # The top of the range means "no limit": the hardware maximum is
        # written back rather than a cap that happens to equal it, so the
        # profile stores 0 and reads as unlimited everywhere.
        unlimited = ghz >= self.max_ghz - 0.05
        cap_khz = 0 if unlimited else int(round(ghz * 1e6))

        def work():
            return hardware.run_helper(
                "cpuclock", "max" if unlimited else cap_khz)

        def success():
            data = (self.window.current_profile() or {}).setdefault("cpu", {})
            # Stored even when 0: that means "this profile wants no ceiling"
            # and still has to be applied, or switching away from a limited
            # profile would leave its cap behind.
            data["max_freq"] = cap_khz
            self._applied["clock"] = self.rows["clock"].get_value()

        self.window.apply_async(work, lambda result, error: self._finish(
            "clock", label, result, error, success,
            lambda: self._restore(("clock",))))

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """Load the active profile into every control and render one fan
        read, no hardware writes."""
        self.reload()
        self._render(self._sample())
