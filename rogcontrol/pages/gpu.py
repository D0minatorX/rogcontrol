"""GPU page: power, clocks and the two firmware knobs.

Same rule as the CPU page -- no Apply button, a control that has sat still
for 400 ms is applied on a worker thread and confirmed by a toast, and a
refusal puts the control back rather than leaving it claiming a value the
hardware never took.

What is different here is that there is no single call that sets everything.
The six settings go to four different places:

* power limit and clock ceiling -> nvidia-smi, through the privileged helper
* Dynamic Boost and temperature target -> asus-wmi sysfs, through the helper
* the two clock offsets -> nvidia-settings, *not* through the helper, because
  it needs the user's own display connection and root has none

So each control is its own apply group. A failure in one cannot invalidate
the others, which is why nothing here restores in a batch the way the CPU
page's five ryzenadj values do.

The ranges come off the card at startup rather than being hardcoded: this
laptop's 5-140 W is not another laptop's, and a slider that stops at the
wrong number is a slider that either refuses valid settings or offers ones
nvidia-smi will reject.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.slider_row import SliderRow, align_value_widths  # noqa: E402

DEBOUNCE_MS = 400
COALESCE_MS = 20
REFRESH_SECONDS = 2
DASH = "—"

CLOCK_LIMIT_SUBTITLE = (
    "A ceiling, not a target — the GPU still idles and boosts freely below "
    "it, and this raises no power or thermal limit.\n"
    "Lower it to cut heat and noise. The top of the slider means Default: no "
    "limit is applied at all."
)

OFFSET_SUBTITLE = (
    "A genuine overclock when positive: this raises the voltage/frequency "
    "curve, so the card draws more power and runs hotter at the same clock — "
    "unlike the ceiling above, which cannot do that.\n"
    "0 is stock. Increase in small steps and test; too much causes crashes "
    "or graphical corruption."
)

BOOST_SUBTITLE = (
    "Extra power the firmware may shift from the CPU to the GPU under load. "
    "Higher favours the GPU in games; lower leaves more headroom for the "
    "CPU. The range is fixed by the firmware."
)

TEMP_TARGET_SUBTITLE = (
    "The temperature the GPU aims to hold before it starts reducing clocks. "
    "Lower runs cooler and quieter but throttles sooner. The range is fixed "
    "by the firmware."
)


class GpuPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.caps = window.caps
        limits = self.caps.get("gpu_limits") or hardware.default_gpu_limits()
        self.gpu_name = limits.get("name")
        self.min_w = limits.get("min_w", hardware.GPU_MIN_W_FALLBACK)
        self.max_w = limits.get("max_w", hardware.GPU_MAX_W_FALLBACK)
        self.clock_limit_max = limits.get("clock_limit_max",
                                          hardware.CLOCK_LIMIT_FALLBACK_MAX)

        # Starting values for a profile that has never stored one: whatever
        # the firmware is holding right now, so a fresh profile begins where
        # the machine shipped rather than at an arbitrary end of a slider.
        self.firmware_boost = (hardware.read_nv_dynamic_boost()
                               or hardware.DYN_BOOST_MIN)
        self.firmware_temp_target = (hardware.read_nv_temp_target()
                                     or hardware.TEMP_TARGET_MIN)

        self._loading = True
        self._timers = {}
        self._pending = {}
        self._busy = {}
        self._applied = {}
        self._sampling = False
        self._timer_id = None

        self.rows = {}
        self._build()
        self.reload()
        self._loading = False
        # One read straight away, so the temperature is a number when the
        # page is first looked at rather than a dash for two seconds. The
        # timer below only fires after its first interval has elapsed.
        self._start_sample()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        status = Adw.PreferencesGroup(
            title="Graphics card",
            description=self.gpu_name or "No NVIDIA card detected")
        self.add(status)
        self.temp_row, self.temp_value = self._live_row(status, "Temperature")

        power = Adw.PreferencesGroup(title="Power")
        self.add(power)

        watts = SliderRow(
            title="Power limit",
            subtitle=f"The board power the card is allowed to draw. This "
                     f"card reports {self.min_w}–{self.max_w} W.",
            minimum=self.min_w, maximum=self.max_w, step=1, unit="W",
            settle_ms=DEBOUNCE_MS)
        watts.connect("changed", self._on_changed, "watts")
        power.add(watts)
        self.rows["watts"] = watts

        boost = SliderRow(
            title="NVIDIA Dynamic Boost", subtitle=BOOST_SUBTITLE,
            minimum=hardware.DYN_BOOST_MIN, maximum=hardware.DYN_BOOST_MAX,
            step=1, unit="W", settle_ms=DEBOUNCE_MS)
        boost.connect("changed", self._on_changed, "dyn_boost")
        power.add(boost)
        self.rows["dyn_boost"] = boost

        temp_target = SliderRow(
            title="Temperature target", subtitle=TEMP_TARGET_SUBTITLE,
            minimum=hardware.TEMP_TARGET_MIN,
            maximum=hardware.TEMP_TARGET_MAX,
            step=1, unit="°C", settle_ms=DEBOUNCE_MS)
        temp_target.connect("changed", self._on_changed, "temp_target")
        power.add(temp_target)
        self.rows["temp_target"] = temp_target
        align_value_widths([watts, boost, temp_target])

        clocks = Adw.PreferencesGroup(title="Clocks")
        self.add(clocks)

        ceiling = SliderRow(
            title="Clock ceiling", subtitle=CLOCK_LIMIT_SUBTITLE,
            minimum=hardware.CLOCK_LIMIT_MIN, maximum=self.clock_limit_max,
            step=15, unit="MHz", settle_ms=DEBOUNCE_MS)
        ceiling.connect("changed", self._on_changed, "clock_limit")
        clocks.add(ceiling)
        self.rows["clock_limit"] = ceiling

        core = SliderRow(
            title="Core clock offset", subtitle=OFFSET_SUBTITLE,
            minimum=hardware.CLOCK_OFFSET_MIN,
            maximum=hardware.CLOCK_OFFSET_MAX,
            step=25, unit="MHz", settle_ms=DEBOUNCE_MS)
        core.connect("changed", self._on_changed, "clock_offset")
        clocks.add(core)
        self.rows["clock_offset"] = core

        memory = SliderRow(
            title="Memory clock offset", subtitle=OFFSET_SUBTITLE,
            minimum=hardware.MEM_CLOCK_OFFSET_MIN,
            maximum=hardware.MEM_CLOCK_OFFSET_MAX,
            step=25, unit="MHz", settle_ms=DEBOUNCE_MS)
        memory.connect("changed", self._on_changed, "mem_clock_offset")
        clocks.add(memory)
        self.rows["mem_clock_offset"] = memory
        align_value_widths([ceiling, core, memory])

        self._apply_capability_gating()

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

    def _apply_capability_gating(self):
        """Grey out what this machine cannot do, and say why on hover.

        Four independent questions, because the four back ends fail
        independently: a machine can have nvidia-smi without
        nvidia-settings, and the asus-wmi knobs are absent on every
        non-ASUS machine regardless of the card."""
        if not self.caps.get("nvidia"):
            for key in ("watts", "clock_limit"):
                self._disable(key, "nvidia-smi is not installed")
            self.temp_row.set_subtitle("nvidia-smi is not installed")
        if not self.caps.get("nvidia_settings"):
            for key in ("clock_offset", "mem_clock_offset"):
                self._disable(key, "nvidia-settings is not installed")
        if not self.caps.get("nv_dynamic_boost"):
            self._disable("dyn_boost",
                          "asus-wmi does not expose nv_dynamic_boost")
        if not self.caps.get("nv_temp_target"):
            self._disable("temp_target",
                          "asus-wmi does not expose nv_temp_target")

    def _disable(self, key, reason):
        row = self.rows[key]
        row.set_sensitive(False)
        # The tooltip, not the subtitle: the subtitle explains what the
        # setting does and is still worth reading on a machine that cannot
        # use it, so the reason goes somewhere it cannot displace that.
        row.set_tooltip_text(f"Not available on this machine: {reason}")

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
            gpu = (self.window.current_profile() or {}).get("gpu") or {}
            values = {
                "watts": gpu.get("watts", 100),
                "clock_offset": gpu.get("clock_offset", 0),
                "mem_clock_offset": gpu.get("mem_clock_offset", 0),
                # Absent means "no ceiling", which on screen is the top of
                # the slider -- the same convention the apply writes back.
                "clock_limit": gpu.get("clock_limit", self.clock_limit_max),
                "dyn_boost": gpu.get("dyn_boost", self.firmware_boost),
                "temp_target": gpu.get("temp_target",
                                       self.firmware_temp_target),
            }
            for key, value in values.items():
                row = self.rows[key]
                row.set_value(self._clamp(row, value))
                self._applied[key] = row.get_value()
        finally:
            self._loading = was_loading

    # -- live temperature ----------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self):
        # nvidia-smi costs a couple of hundred milliseconds a call, so it is
        # not run for a page nobody is looking at.
        if self.get_mapped():
            self._start_sample()
        return GLib.SOURCE_CONTINUE

    def _start_sample(self):
        if self._sampling or not self.caps.get("nvidia"):
            return
        self._sampling = True
        self.window.apply_async(hardware.read_nvidia_stats, self._on_sample)

    def _on_sample(self, result, error):
        self._sampling = False
        if error is not None:
            return
        self._render(result)

    def _render(self, stats):
        temp = (stats or (None, None))[0]
        self.temp_value.set_text(DASH if temp is None else f"{temp:.0f} °C")

    # -- change handling -----------------------------------------------------

    def _on_changed(self, row, _value, key):
        if self._loading:
            return
        self._schedule(key, self._label_for(key, row))

    def _label_for(self, key, row):
        if key == "clock_limit":
            mhz = int(row.get_value())
            if hardware.gpu_clock_limit_arg(mhz,
                                            self.clock_limit_max) == "reset":
                return "Clock ceiling removed"
            return f"Clock ceiling set to {mhz} MHz"
        titles = {"watts": "Power limit",
                  "dyn_boost": "Dynamic Boost",
                  "temp_target": "GPU temperature target",
                  "clock_offset": "Core clock offset",
                  "mem_clock_offset": "Memory clock offset"}
        return f"{titles[key]} set to {row.get_display_value()}"

    def _schedule(self, key, label, delay=COALESCE_MS):
        self._pending[key] = label
        source = self._timers.pop(key, None)
        if source is not None:
            GLib.source_remove(source)
        self._timers[key] = GLib.timeout_add(delay, self._fire, key)

    def _fire(self, key):
        self._timers.pop(key, None)
        if self._busy.get(key):
            # An apply for this control is still in flight. Two nvidia-smi
            # writes for the same knob at once would race over which value
            # the card ends up holding.
            self._timers[key] = GLib.timeout_add(DEBOUNCE_MS, self._fire, key)
            return GLib.SOURCE_REMOVE
        label = self._pending.pop(key, "Setting")
        self._busy[key] = True
        self._apply(key, label)
        return GLib.SOURCE_REMOVE

    # -- applying ------------------------------------------------------------

    def _apply(self, key, label):
        value = int(self.rows[key].get_value())
        gate = {"watts": "nvidia",
                "clock_limit": "nvidia",
                "clock_offset": "nvidia_settings",
                "mem_clock_offset": "nvidia_settings",
                "dyn_boost": "nv_dynamic_boost",
                "temp_target": "nv_temp_target"}[key]
        if not self.caps.get(gate):
            # Reachable by keyboard on a machine that grew the capability
            # after startup; nothing here may pretend it worked.
            self._busy[key] = False
            self.window.toast(f"{label} failed: not available on this machine")
            return

        if key == "watts":
            def work():
                return hardware.run_helper("gpu", value)
        elif key == "clock_limit":
            arg = hardware.gpu_clock_limit_arg(value, self.clock_limit_max)

            def work():
                return hardware.run_helper("gpuclocklimit", arg)
        elif key == "dyn_boost":
            def work():
                return hardware.run_helper("nvboost", value)
        elif key == "temp_target":
            def work():
                return hardware.run_helper("nvtemp", value)
        elif key == "clock_offset":
            def work():
                return hardware.set_nvidia_clock_offset("core", value)
        else:
            def work():
                return hardware.set_nvidia_clock_offset("memory", value)

        self.window.apply_async(work, lambda result, error: self._finish(
            key, label, value, result, error))

    def _finish(self, key, label, value, result, error):
        self._busy[key] = False
        if error is not None:
            ok, message = False, str(error)
        else:
            ok, message = result
        if ok:
            data = (self.window.current_profile() or {}).setdefault("gpu", {})
            # The ceiling is stored even at the top of the range, where it
            # means "no limit": a profile that wants no ceiling still has to
            # say so, or switching away from a limited profile would leave
            # the old cap in place.
            data[key] = value
            self._applied[key] = self.rows[key].get_value()
            config_mod.save_config(self.window.config)
            self.window.toast(f"{label}.")
        else:
            self.window.toast(f"{label} failed: {message}")
            self._restore(key)

    def _restore(self, key):
        """Put one control back to the last value the hardware accepted."""
        value = self._applied.get(key)
        if value is None:
            return
        self._loading = True
        try:
            self.rows[key].set_value(value)
        finally:
            self._loading = False

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """Load the profile and render one temperature read. No writes."""
        self.reload()
        self._render(hardware.read_nvidia_stats()
                     if self.caps.get("nvidia") else (None, None))
