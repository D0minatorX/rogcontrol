"""Shared GPU page UI, settings and lifecycle.

Backend subclasses own graphics access and switching behavior.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.pending_changes import PendingChanges
from ..sampling import SampleFailures  # noqa: E402
from ..widgets.action_buttons import apply_revert_buttons  # noqa: E402
from ..widgets.stat_row import StatCell, build_stat_row  # noqa: E402
from ..widgets.slider_row import SliderRow, align_value_widths  # noqa: E402


# The sliders report as soon as they move: nothing is applied on this page
# any more, so a change only updates the pending value.
SETTLE_MS = 0

REFRESH_SECONDS = 2

DASH = "—"

# What the temperature row says while the card is runtime-suspended. A dash
# would read as "cannot be read", which is what a missing driver looks like;
# this says the card is fine and asleep, which on a hybrid machine is the
# state it should be in most of the time.
IDLE_TEXT = "Idle"

# The asus hwmon's fan2, which is the one blowing over the card. The label
# comes from hardware too, so this page, the CPU page and the Overview all
# call the same fan the same thing.
FAN_CHANNEL = "2"

# Apply order. Nothing here is as load-bearing as the CPU page's, but the
# power budget is set before the clocks that spend it, and it matches the
# order a whole-profile apply uses.
APPLY_ORDER = ("watts", "clock_limit", "dyn_boost", "temp_target",
               "voltage_boost", "clock_offset", "mem_clock_offset")

# Which capability each setting needs. Four independent questions, because
# the four back ends fail independently: a machine can have nvidia-smi
# without nvidia-settings, and the asus-wmi knobs are absent on every
# non-ASUS machine regardless of the card.
CAPABILITY = {"watts": "gpu_power_limit",
              "clock_limit": "gpu_clock_limit",
              "clock_offset": "nvidia_core_clock_offset",
              "mem_clock_offset": "nvidia_memory_clock_offset",
              "voltage_boost": "nvidia_voltage_boost",
              "dyn_boost": "nv_dynamic_boost",
              "temp_target": "nv_temp_target"}

TITLES = {"watts": "Power limit",
          "clock_limit": "Clock ceiling",
          "dyn_boost": "Dynamic Boost",
          "temp_target": "GPU temperature target",
          "clock_offset": "Core clock offset",
          "mem_clock_offset": "Memory clock offset",
          "voltage_boost": "Voltage Boost"}

# Each control keeps a few words on the row and says the rest on hover: six
# sliders with a paragraph under each is a page that has to be scrolled past
# rather than read. The wording that survives on screen is the part that
# changes what you would do -- "top of the slider means no limit", "0 is
# stock" -- not the explanation of the mechanism behind it.
CLOCK_LIMIT_TOOLTIP = (
    "A ceiling, not a target — the GPU still idles and boosts freely below "
    "it, and this raises no power or thermal limit.\n\n"
    "Lower it to cut heat and noise. The top of the slider means Default: no "
    "limit is applied at all."
)

OFFSET_SUBTITLE = "0 is stock; positive is a real overclock"

OFFSET_TOOLTIP = (
    "A genuine overclock when positive: this raises the voltage/frequency "
    "curve, so the card draws more power and runs hotter at the same clock — "
    "unlike the clock ceiling, which cannot do that.\n\n"
    "Increase in small steps and test; too much causes crashes or graphical "
    "corruption."
)

VOLTAGE_BOOST_TOOLTIP = (
    "Experimental NVIDIA driver control that gives GPU Boost up to 100% "
    "more voltage headroom. 0% is stock.\n\n"
    "This can increase heat and power use and can make the GPU unstable. "
    "Increase it only in small steps and test thoroughly."
)

BOOST_TOOLTIP = (
    "Extra power the firmware may shift from the CPU to the GPU under load. "
    "Higher favours the GPU in games; lower leaves more headroom for the "
    "CPU. The range is fixed by the firmware."
)

TEMP_TARGET_TOOLTIP = (
    "The temperature the GPU aims to hold before it starts reducing clocks. "
    "Lower runs cooler and quieter but throttles sooner. The range is fixed "
    "by the firmware."
)

APPLY_TOOLTIP = (
    "Writes everything on this page to the card: the power limit and clock "
    "ceiling through nvidia-smi, Dynamic Boost and the temperature target "
    "through asus-wmi, Voltage Boost through the NVIDIA driver, and the "
    "two offsets through nvidia-settings."
)

REVERT_TOOLTIP = "Puts every control back to what the profile holds."

MODE_ANSWER_SILENT_OK = "It accepted the change without printing anything."

MODE_ANSWER_SILENT_FAIL = "It refused the change without saying why."


class GpuPageBase(Gtk.Box):
    """Common controls; graphics backend behavior is supplied by subclasses."""

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
        self._hardware_busy = False
        self._applied = {}
        self._sampling = False
        # Consecutive failures of the sampler below, so a page whose
        # readings have stopped coming back says so once instead of
        # showing dashes forever. See sampling.py.
        self._sample_failures = SampleFailures("GPU")
        self._timer_id = None

        # Graphics mode. ``modes`` is what the picker holds and
        # ``supported_modes`` the last non-empty answer the graphics backend gave;
        # the two are deliberately not the same list. ``current_mode`` is
        # the mode currently reported by the backend.
        self.modes = []
        self.supported_modes = []
        self.current_mode = None
        self._init_backend()
        self._switching = False

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
        # The pending bar warns that navigation discards staged changes.
        self.connect("unmap", self._on_unmap)

    # -- construction --------------------------------------------------------

    def _init_backend(self):
        """Initialize optional backend state before constructing controls."""

    def _build(self):
        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", self._on_apply_clicked)
        self.append(self.banner)
        self.pending = PendingChanges(self._on_apply_clicked, self._on_revert_clicked,
                                      discard_on_leave=True)
        self.append(self.pending)

        page = Adw.PreferencesPage()
        page.set_vexpand(True)
        self.append(page)

        status = Adw.PreferencesGroup(
            title="Graphics card",
            description=self.gpu_name or "No NVIDIA card detected")
        self.status_group = status
        page.add(status)
        # Side by side on one row -- see the CPU page, which pairs the same
        # two readings the same way.
        self.temp_cell = StatCell(
            "Temperature",
            "The card's own sensor. Reads Idle while the card is asleep.")
        self.fan_cell = StatCell(hardware.FAN_LABELS[FAN_CHANNEL])
        build_stat_row(status, (self.temp_cell, self.fan_cell))
        self.temp_value = self.temp_cell.value
        self.fan_value = self.fan_cell.value

        self.mode_group = self._build_gpu_mode()
        page.add(self.mode_group)

        self.power_group = power = Adw.PreferencesGroup(title="Power")
        page.add(power)

        watts = SliderRow(
            title="Power limit",
            subtitle="Board power the card may draw",
            tooltip=f"The board power the card is allowed to draw. This card "
                    f"reports {self.min_w}–{self.max_w} W.",
            minimum=self.min_w, maximum=self.max_w, step=1, unit="W",
            settle_ms=SETTLE_MS)
        watts.connect("changed", self._on_changed)
        power.add(watts)
        self.rows["watts"] = watts

        boost = SliderRow(
            title="NVIDIA Dynamic Boost",
            subtitle="Extra GPU power allowance from the shared CPU/GPU budget",
            tooltip=BOOST_TOOLTIP,
            minimum=hardware.DYN_BOOST_MIN, maximum=hardware.DYN_BOOST_MAX,
            step=1, unit="W", settle_ms=SETTLE_MS)
        boost.connect("changed", self._on_changed)
        power.add(boost)
        self.rows["dyn_boost"] = boost

        temp_target = SliderRow(
            title="Temperature target", tooltip=TEMP_TARGET_TOOLTIP,
            minimum=hardware.TEMP_TARGET_MIN,
            maximum=hardware.TEMP_TARGET_MAX,
            step=1, unit="°C", settle_ms=SETTLE_MS)
        temp_target.connect("changed", self._on_changed)
        power.add(temp_target)
        self.rows["temp_target"] = temp_target
        align_value_widths([watts, boost, temp_target])

        self.clocks_group = clocks = Adw.PreferencesGroup(title="Clocks")
        page.add(clocks)

        ceiling = SliderRow(
            title="Clock ceiling", subtitle="Top of the slider means no limit",
            tooltip=CLOCK_LIMIT_TOOLTIP,
            minimum=hardware.CLOCK_LIMIT_MIN, maximum=self.clock_limit_max,
            step=15, unit="MHz", settle_ms=SETTLE_MS)
        ceiling.connect("changed", self._on_changed)
        clocks.add(ceiling)
        self.rows["clock_limit"] = ceiling

        core = SliderRow(
            title="Core clock offset", subtitle=OFFSET_SUBTITLE,
            tooltip=OFFSET_TOOLTIP,
            minimum=hardware.CLOCK_OFFSET_MIN,
            maximum=hardware.CLOCK_OFFSET_MAX,
            step=hardware.CLOCK_OFFSET_STEPS["core"], unit="MHz", settle_ms=SETTLE_MS)
        core.connect("changed", self._on_changed)
        clocks.add(core)
        self.rows["clock_offset"] = core

        memory = SliderRow(
            title="Memory clock offset", subtitle=OFFSET_SUBTITLE,
            tooltip=OFFSET_TOOLTIP,
            minimum=hardware.MEM_CLOCK_OFFSET_MIN,
            maximum=hardware.MEM_CLOCK_OFFSET_MAX,
            step=hardware.CLOCK_OFFSET_STEPS["memory"], unit="MHz", settle_ms=SETTLE_MS)
        memory.connect("changed", self._on_changed)
        clocks.add(memory)
        self.rows["mem_clock_offset"] = memory

        voltage_boost = SliderRow(
            title="Voltage Boost", subtitle="Experimental; 0% is stock",
            tooltip=VOLTAGE_BOOST_TOOLTIP,
            minimum=hardware.NVIDIA_VOLTAGE_BOOST_MIN,
            maximum=hardware.NVIDIA_VOLTAGE_BOOST_MAX,
            step=1, unit="%", settle_ms=SETTLE_MS)
        voltage_boost.connect("changed", self._on_changed)
        clocks.add(voltage_boost)
        self.rows["voltage_boost"] = voltage_boost
        align_value_widths([ceiling, core, memory, voltage_boost])

        self._build_actions_group()
        self._apply_capability_gating()

    def _build_actions_group(self):
        """The page's header-bar buttons -- see widgets/action_buttons.py."""
        self.action_box, self.apply_button, self.revert_button = (
            apply_revert_buttons(
                self._on_apply_clicked, self._on_revert_clicked,
                apply_tooltip=APPLY_TOOLTIP, revert_tooltip=REVERT_TOOLTIP))

    def _block_switching(self, reason):
        """Replace the picker with the reason there is nothing to pick."""
        self.mode_row.set_visible(False)
        self.mode_blocked_row.set_subtitle(reason)
        self.mode_blocked_row.set_visible(True)

    def _allow_switching(self):
        # The subtitle is not reset here: _render_modes has just put the
        # current mode's description on it, and overwriting that with the
        # generic line would undo it on every sample.
        self.mode_blocked_row.set_visible(False)
        self.mode_row.set_sensitive(True)
        self.mode_row.set_visible(True)

    def _value_row(self, group, title, subtitle="", strong=False):
        """A titled row whose suffix label carries the value."""
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.set_subtitle_lines(0)
        label = Gtk.Label(label=DASH)
        label.add_css_class("heading" if strong else "dim-label")
        label.set_wrap(True)
        # WORD, not WORD_CHAR: these values are single words as often as not
        # -- Integrated, Hybrid -- and breaking inside one produced awkward
        # hyphenation in a window with room to spare.
        label.set_wrap_mode(Pango.WrapMode.WORD)
        label.set_xalign(1.0)
        row.add_suffix(label)
        group.add(row)
        return row, label

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
                "voltage_boost": gpu.get("voltage_boost", 0),
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
        """Discard on navigation, as the pending bar explicitly warns."""
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

    def _on_sample(self, result, error):
        self._sampling = False
        if error is not None:
            # A run of these is reported once; see sampling.py.
            self._sample_failures.report(self.window, error, source="gpu")
            return
        self._sample_failures.succeeded()
        self._render(result)

    # -- unapplied changes ---------------------------------------------------

    def _render(self, data):
        temp = (data.get("nvidia") or (None, None))[0]
        if data.get("dgpu_suspended"):
            # Not a dash: a suspended card is a working card doing its job,
            # and a dash here reads as "cannot be read" -- which is what a
            # missing driver looks like. IDLE_TEXT says which it is.
            self.temp_value.set_text(IDLE_TEXT)
        else:
            self.temp_value.set_text(
                DASH if temp is None else f"{temp:.0f} °C")
        rpm = data.get("fan_rpm")
        # A dash, not a zero: a fan that cannot be read is not a fan that has
        # stopped, and "0 rpm" is the reading that would send someone
        # hunting a hardware fault that is not there.
        self.fan_value.set_text(DASH if rpm is None else f"{rpm} rpm")
        # Not while a switch is in flight: the picker is showing the mode
        # being switched to, and a sample landing mid-switch would put it
        # back to the one still running.
        self._render_backend_mode(data)

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

    def _update_pending(self):
        self.pending.update(self.rows, self._dirty_keys(),
                            self._applying or self._hardware_busy)

    def _update_banner(self):
        self._update_pending()
        if self._applying:
            # The banner is the progress line while an apply is running.
            return
        # Pending edits have their own bar; this banner is for operations.
        self.banner.set_revealed(False)

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

    def _set_busy(self, busy):
        self._applying = busy
        self._update_pending()
        self.apply_button.set_sensitive(not busy)
        self.revert_button.set_sensitive(not busy)

    def set_hardware_busy(self, busy):
        """Something else is writing the machine -- see app.claim_hardware."""
        self._hardware_busy = busy
        self._update_pending()
        if not self._applying:
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
        voltage_boost = dict(wanted).get("voltage_boost", 0)
        if voltage_boost > 0:
            self._confirm_voltage_boost(wanted)
            return
        self._start_apply(wanted)

    def _confirm_voltage_boost(self, wanted):
        """Require a fresh acknowledgement before an interactive boost."""
        value = dict(wanted)["voltage_boost"]
        dialog = Adw.AlertDialog(
            heading=f"Apply {value}% Voltage Boost?",
            body=("Voltage Boost is an experimental NVIDIA driver control. "
                  "It may increase GPU heat and power use, and can cause "
                  "instability, graphical corruption, or crashes.\n\n"
                  "0% returns to stock without this warning."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", f"Apply {value}%")
        dialog.set_response_appearance("apply",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_voltage_boost_response, wanted)
        dialog.present(self)

    def _on_voltage_boost_response(self, _dialog, response, wanted):
        if response == "apply":
            self._start_apply(wanted)

    def _start_apply(self, wanted):
        if not self.window.claim_hardware("writing the GPU settings"):
            return
        # Which profile these settings belong to, captured now: the write
        # runs off the main loop, and the enforcer switches profile on
        # AC/battery on its own. Resolving the profile when the write
        # finishes would save them into whichever one is current by then.
        # See config.deferred_save_target.
        target = self.window.current_profile_name()
        self._set_busy(True)
        self._show_banner("Writing the GPU settings…")
        self.window.apply_async(
            lambda: self._apply_worker(wanted),
            lambda results, error: self._on_applied(target, results, error))

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
        if key == "voltage_boost":
            return hardware.set_nvidia_voltage_boost(value)
        if key == "clock_offset":
            return hardware.set_nvidia_clock_offset("core", value)
        return hardware.set_nvidia_clock_offset("memory", value)

    def _on_applied(self, target, results, error):
        self._set_busy(False)
        if error is not None:
            self.window.release_hardware()
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
        refused = self._save(target, applied) if applied else None
        # Anything the card refused goes back to the value it accepted last.
        failed = [key for key, _value, ok, _message in results if not ok]
        if failed:
            self._restore(failed)
        self._update_pending()

        if refused is not None:
            self._show_banner(refused, button="Apply")
            self.window.toast(refused)
        elif failures:
            self._show_banner("Some GPU settings were not applied — "
                              + "; ".join(failures), button="Apply")
            self.window.toast("GPU: " + "; ".join(failures))
        else:
            # Not an unconditional hide: a slider moved while the write was
            # running is genuinely unapplied, and the banner has to say so.
            self._update_banner()
            self.window.toast(
                f"GPU settings applied and saved to {target}.")
        # Last, after everything above has finished with the target captured
        # when Apply was pressed: releasing sooner lets a deferred
        # reload_pages repoint the rows underneath this callback.
        self.window.release_hardware()

    def _save(self, target, applied):
        """Write what reached the card into profile ``target``.

        ``target`` is the profile that was active when Apply was pressed,
        not whichever one is active now. Returns None when the save
        happened, or the sentence to show when it was refused.

        Only what took is written: a profile holding a setting the card
        refused is a profile that silently disagrees with the machine."""
        # The ceiling is stored even at the top of the range, where it means
        # "no limit": a profile that wants no ceiling still has to say so, or
        # switching away from a limited profile would leave the old cap in
        # place.
        refused = config_mod.save_deferred(
            self.window.config, target, "gpu", applied, "GPU settings")
        if refused is not None:
            # ``_applied`` is left alone as well: reload() has already reset
            # it from the profile that is current now, and marking these
            # values applied on top of that would have the banner claim the
            # new profile is running the old one's settings.
            return refused
        for key, value in applied.items():
            # The value that was written, not what the row holds now: the
            # sliders stay live during an apply, and recording the current
            # position would mark a change made mid-write as already applied.
            self._applied[key] = float(value)
        return None

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
