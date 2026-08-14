"""GPU page: power, clocks and the two firmware knobs, written on Apply.

Same rule as the CPU page, and for the same reason -- nothing here reaches
the card until Apply is pressed. Moving a slider changes a pending value and
raises the banner; the banner names what is unapplied and carries its own
Apply button, as the Fans page does.

What is different here is that there is no single call that sets everything.
The six settings go to four different places:

* power limit and clock ceiling -> nvidia-smi, through the privileged helper
* Dynamic Boost and temperature target -> asus-wmi sysfs, through the helper
* the two clock offsets -> nvidia-settings, *not* through the helper, because
  it needs the user's own display connection and root has none

So one Apply is up to six independent writes. They all run, in order, even if
one of them fails: a refused power limit says nothing about whether a clock
offset can be set. Only the settings that took are saved to the profile, and
a control whose write was refused goes back to the value the card accepted
last, so nothing on screen claims a setting the hardware never held.

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

# The sliders report as soon as they move: nothing is applied on this page
# any more, so the only thing a change drives is the banner.
SETTLE_MS = 0
REFRESH_SECONDS = 2
DASH = "—"

# The asus hwmon's fan2, which is the one blowing over the card. The label
# comes from hardware too, so this page, the CPU page and the Overview all
# call the same fan the same thing.
FAN_CHANNEL = "2"

# Apply order. Nothing here is as load-bearing as the CPU page's, but the
# power budget is set before the clocks that spend it, and it matches the
# order a whole-profile apply uses.
APPLY_ORDER = ("watts", "clock_limit", "dyn_boost", "temp_target",
               "clock_offset", "mem_clock_offset")

# Which capability each setting needs. Four independent questions, because
# the four back ends fail independently: a machine can have nvidia-smi
# without nvidia-settings, and the asus-wmi knobs are absent on every
# non-ASUS machine regardless of the card.
CAPABILITY = {"watts": "nvidia",
              "clock_limit": "nvidia",
              "clock_offset": "nvidia_settings",
              "mem_clock_offset": "nvidia_settings",
              "dyn_boost": "nv_dynamic_boost",
              "temp_target": "nv_temp_target"}

TITLES = {"watts": "Power limit",
          "clock_limit": "Clock ceiling",
          "dyn_boost": "Dynamic Boost",
          "temp_target": "GPU temperature target",
          "clock_offset": "Core clock offset",
          "mem_clock_offset": "Memory clock offset"}

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

APPLY_SUBTITLE = (
    "Writes everything on this page to the card: the power limit and clock "
    "ceiling through nvidia-smi, Dynamic Boost and the temperature target "
    "through asus-wmi, and the two offsets through nvidia-settings. Takes a "
    "second or two."
)


class GpuPage(Gtk.Box):
    """A banner, the controls, and one Apply button.

    A plain Box rather than an Adw.PreferencesPage because the banner has to
    stay put; see the CPU page, which is built the same way for the same
    reason.
    """

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
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
        self._applying = False
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
        # Walking away from unapplied changes discards them rather than
        # applying them behind the user's back.
        self.connect("unmap", self._on_unmap)

    # -- construction --------------------------------------------------------

    def _build(self):
        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", self._on_apply_clicked)
        self.append(self.banner)

        page = Adw.PreferencesPage()
        page.set_vexpand(True)
        self.append(page)

        status = Adw.PreferencesGroup(
            title="Graphics card",
            description=self.gpu_name or "No NVIDIA card detected")
        page.add(status)
        self.temp_row, self.temp_value = self._live_row(status, "Temperature")
        self.fan_row, self.fan_value = self._live_row(
            status, hardware.FAN_LABELS[FAN_CHANNEL])

        power = Adw.PreferencesGroup(title="Power")
        page.add(power)

        watts = SliderRow(
            title="Power limit",
            subtitle=f"The board power the card is allowed to draw. This "
                     f"card reports {self.min_w}–{self.max_w} W.",
            minimum=self.min_w, maximum=self.max_w, step=1, unit="W",
            settle_ms=SETTLE_MS)
        watts.connect("changed", self._on_changed)
        power.add(watts)
        self.rows["watts"] = watts

        boost = SliderRow(
            title="NVIDIA Dynamic Boost", subtitle=BOOST_SUBTITLE,
            minimum=hardware.DYN_BOOST_MIN, maximum=hardware.DYN_BOOST_MAX,
            step=1, unit="W", settle_ms=SETTLE_MS)
        boost.connect("changed", self._on_changed)
        power.add(boost)
        self.rows["dyn_boost"] = boost

        temp_target = SliderRow(
            title="Temperature target", subtitle=TEMP_TARGET_SUBTITLE,
            minimum=hardware.TEMP_TARGET_MIN,
            maximum=hardware.TEMP_TARGET_MAX,
            step=1, unit="°C", settle_ms=SETTLE_MS)
        temp_target.connect("changed", self._on_changed)
        power.add(temp_target)
        self.rows["temp_target"] = temp_target
        align_value_widths([watts, boost, temp_target])

        clocks = Adw.PreferencesGroup(title="Clocks")
        page.add(clocks)

        ceiling = SliderRow(
            title="Clock ceiling", subtitle=CLOCK_LIMIT_SUBTITLE,
            minimum=hardware.CLOCK_LIMIT_MIN, maximum=self.clock_limit_max,
            step=15, unit="MHz", settle_ms=SETTLE_MS)
        ceiling.connect("changed", self._on_changed)
        clocks.add(ceiling)
        self.rows["clock_limit"] = ceiling

        core = SliderRow(
            title="Core clock offset", subtitle=OFFSET_SUBTITLE,
            minimum=hardware.CLOCK_OFFSET_MIN,
            maximum=hardware.CLOCK_OFFSET_MAX,
            step=25, unit="MHz", settle_ms=SETTLE_MS)
        core.connect("changed", self._on_changed)
        clocks.add(core)
        self.rows["clock_offset"] = core

        memory = SliderRow(
            title="Memory clock offset", subtitle=OFFSET_SUBTITLE,
            minimum=hardware.MEM_CLOCK_OFFSET_MIN,
            maximum=hardware.MEM_CLOCK_OFFSET_MAX,
            step=25, unit="MHz", settle_ms=SETTLE_MS)
        memory.connect("changed", self._on_changed)
        clocks.add(memory)
        self.rows["mem_clock_offset"] = memory
        align_value_widths([ceiling, core, memory])

        page.add(self._build_actions_group())
        self._apply_capability_gating()

    def _build_actions_group(self):
        group = Adw.PreferencesGroup(title="Apply")
        self.apply_row = Adw.ActionRow(title="Apply GPU settings",
                                       subtitle=APPLY_SUBTITLE)
        self.apply_row.set_subtitle_lines(0)
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.set_valign(Gtk.Align.CENTER)
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", self._on_apply_clicked)
        self.apply_row.add_suffix(self.apply_button)
        self.apply_row.set_activatable_widget(self.apply_button)
        group.add(self.apply_row)

        self.revert_button = Gtk.Button(label="Revert")
        self.revert_button.set_valign(Gtk.Align.CENTER)
        self.revert_button.connect("clicked", self._on_revert_clicked)
        revert_row = Adw.ActionRow(
            title="Discard unapplied changes",
            subtitle="Puts every control back to what the profile holds.")
        revert_row.add_suffix(self.revert_button)
        revert_row.set_activatable_widget(self.revert_button)
        group.add(revert_row)
        return group

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
        """Grey out what this machine cannot do, and say why on hover."""
        if not self.caps.get("nvidia"):
            for key in ("watts", "clock_limit"):
                self._disable(key, "nvidia-smi is not installed")
            self.temp_row.set_subtitle("nvidia-smi is not installed")
        if not self.caps.get("fan_rpm"):
            # The tachometer is on the asus hwmon, not the card, so it can be
            # missing on a machine whose GPU controls all work.
            self.fan_row.set_subtitle("No asus hwmon fan reading on this "
                                      "machine")
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
        """Put the active profile's values on screen without applying them.

        Also what discards unapplied edits: the profile is the truth, and
        ``_applied`` is reset from it, so the banner goes with them."""
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
        self._update_banner()

    # -- live readings -------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_unmap(self, _widget):
        """The page went off screen; unapplied edits go with it."""
        if self._applying or not self._dirty_keys():
            return
        self.reload()

    def _tick(self):
        # nvidia-smi costs a couple of hundred milliseconds a call, so it is
        # not run for a page nobody is looking at.
        if self.get_mapped():
            self._start_sample()
        return GLib.SOURCE_CONTINUE

    def _start_sample(self):
        if self._sampling:
            return
        self._sampling = True
        self.window.apply_async(self._sample, self._on_sample)

    def _sample(self):
        """Worker thread: the card's own numbers and the fan cooling it.

        Both, always, in one pass -- the fan is a sysfs read that costs
        nothing next to the nvidia-smi call, and a machine with no NVIDIA
        card still has a fan reading worth showing."""
        return {
            "nvidia": (hardware.read_nvidia_stats()
                       if self.caps.get("nvidia") else (None, None)),
            "fan_rpm": hardware.read_fan_rpms().get(FAN_CHANNEL),
        }

    def _on_sample(self, result, error):
        self._sampling = False
        if error is not None:
            return
        self._render(result)

    def _render(self, data):
        temp = (data.get("nvidia") or (None, None))[0]
        self.temp_value.set_text(DASH if temp is None else f"{temp:.0f} °C")
        rpm = data.get("fan_rpm")
        # A dash, not a zero: a fan that cannot be read is not a fan that has
        # stopped, and "0 rpm" is the reading that would send someone
        # hunting a hardware fault that is not there.
        self.fan_value.set_text(DASH if rpm is None else f"{rpm} rpm")

    # -- unapplied changes ---------------------------------------------------

    def _dirty_keys(self):
        """Controls whose value is not the one the card was last given."""
        out = []
        for key, row in self.rows.items():
            was = self._applied.get(key)
            if was is None:
                continue
            if abs(float(was) - float(row.get_value())) > 1e-9:
                out.append(key)
        return out

    def _on_changed(self, _row, _value):
        if self._loading:
            return
        self._update_banner()

    def _update_banner(self):
        if self._applying:
            # The banner is the progress line while an apply is running.
            return
        dirty = self._dirty_keys()
        if not dirty:
            self.banner.set_revealed(False)
            return
        names = [TITLES[key] for key in APPLY_ORDER if key in dirty]
        self._show_banner("Not applied yet — " + ", ".join(names)
                          + (" is" if len(names) == 1 else " are")
                          + " not on the card.", button="Apply")

    def _show_banner(self, text, button=None):
        self.banner.set_title(text)
        # An empty label is how AdwBanner hides its button.
        self.banner.set_button_label(button or "")
        self.banner.set_revealed(True)

    def _on_revert_clicked(self, _button):
        if self._applying:
            return
        if not self._dirty_keys():
            self.window.toast("Nothing to discard — this is what is running.")
            return
        self.reload()
        self.window.toast("Unapplied GPU changes discarded.")

    # -- applying ------------------------------------------------------------

    def _pending_values(self):
        """What the controls hold, for the settings this machine can write."""
        return [(key, int(self.rows[key].get_value())) for key in APPLY_ORDER
                if self.caps.get(CAPABILITY[key])]

    def _set_busy(self, busy):
        self._applying = busy
        self.apply_button.set_sensitive(not busy)
        self.revert_button.set_sensitive(not busy)

    def _on_apply_clicked(self, _widget):
        if self._applying:
            return
        wanted = self._pending_values()
        if not wanted:
            self.window.toast("Nothing on this page can be set on this "
                              "machine.")
            return
        self._set_busy(True)
        self._show_banner("Writing the GPU settings…")
        self.window.apply_async(lambda: self._apply_worker(wanted),
                                self._on_applied)

    def _apply_worker(self, wanted):
        """Write every setting in order. Worker thread.

        Every one runs even if an earlier one failed: they go to three
        different back ends, and a refused power limit says nothing about
        whether a clock offset can be set."""
        results = []
        for key, value in wanted:
            ok, message = self._write(key, value)
            results.append((key, value, ok, message))
        return results

    def _write(self, key, value):
        """One setting, on the worker thread. Returns ``(ok, message)``."""
        if key == "watts":
            return hardware.run_helper("gpu", value)
        if key == "clock_limit":
            return hardware.run_helper(
                "gpuclocklimit",
                hardware.gpu_clock_limit_arg(value, self.clock_limit_max))
        if key == "dyn_boost":
            return hardware.run_helper("nvboost", value)
        if key == "temp_target":
            return hardware.run_helper("nvtemp", value)
        if key == "clock_offset":
            return hardware.set_nvidia_clock_offset("core", value)
        return hardware.set_nvidia_clock_offset("memory", value)

    def _on_applied(self, results, error):
        self._set_busy(False)
        if error is not None:
            self._show_banner(f"Applying the GPU settings failed: {error}",
                              button="Apply")
            self.window.toast(f"GPU settings failed: {error}")
            return

        failures = []
        applied = {}
        for key, value, ok, message in results:
            if ok:
                applied[key] = value
            else:
                failures.append(f"{TITLES[key]}: {message}")
        if applied:
            self._save(applied)
        # Anything the card refused goes back to the value it accepted last.
        failed = [key for key, _value, ok, _message in results if not ok]
        if failed:
            self._restore(failed)

        if failures:
            self._show_banner("Some GPU settings were not applied — "
                              + "; ".join(failures), button="Apply")
            self.window.toast("GPU: " + "; ".join(failures))
        else:
            # Not an unconditional hide: a slider moved while the write was
            # running is genuinely unapplied, and the banner has to say so.
            self._update_banner()
            self.window.toast("GPU settings applied and saved to "
                              f"{self.window.current_profile_name()}.")

    def _save(self, applied):
        """Write what reached the card into the profile.

        Only what took: a profile holding a setting the card refused is a
        profile that silently disagrees with the machine."""
        profile = self.window.current_profile()
        if not profile:
            return
        data = profile.setdefault("gpu", {})
        for key, value in applied.items():
            # The ceiling is stored even at the top of the range, where it
            # means "no limit": a profile that wants no ceiling still has to
            # say so, or switching away from a limited profile would leave
            # the old cap in place.
            data[key] = value
            # The value that was written, not what the row holds now: the
            # sliders stay live during an apply, and recording the current
            # position would mark a change made mid-write as already applied.
            self._applied[key] = float(value)
        config_mod.save_config(self.window.config)

    def _restore(self, keys):
        """Put controls back to the last values the card accepted."""
        self._loading = True
        try:
            for key in keys:
                value = self._applied.get(key)
                if value is not None:
                    self.rows[key].set_value(value)
        finally:
            self._loading = False

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """Load the profile and render one live read. No writes."""
        self.reload()
        self._render(self._sample())
        # What Apply would write, built but not run: a key with no capability
        # entry or no row would fail here rather than on a click.
        self._pending_values()
