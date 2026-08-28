"""GPU page: power, clocks and the two firmware knobs, written on Apply.

Same rule as the CPU page, and for the same reason -- nothing here reaches
the card until Apply is pressed. Moving a slider changes a pending value;
Apply and Revert sit in the header bar, visible whatever the page is
scrolled to, so nothing has to appear on the page to say a change is
waiting.

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

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
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
    "through asus-wmi, and the two offsets through nvidia-settings."
)

REVERT_TOOLTIP = "Puts every control back to what the profile holds."

# -- Graphics mode -----------------------------------------------------------
#
# This section used to live on the System page. It belongs here: which GPU
# the screen is plugged into is a fact about the graphics card, and the
# controls that depend on it -- power limit, temperature target, Dynamic
# Boost -- are all on this page.

GPU_MODE_SUBTITLE = "Ends the session"

GPU_MODE_TOOLTIP = (
    "Integrated turns the NVIDIA card off entirely for battery life; hybrid "
    "leaves it available for the applications that ask for it; AsusMuxDgpu "
    "wires the display straight to it.\n\n"
    "Switching between Integrated and Hybrid restarts the display stack. "
    "Switching into or out of AsusMuxDgpu moves the hardware MUX, which only "
    "the firmware can do, so that one needs a reboot."
)

# One short line each. The full explanation is on the row's tooltip: six
# lines of prose under the picker pushed the drop-down itself so narrow that
# the mode it was showing read "Asu...".
GPU_MODE_DESCRIPTIONS = {
    "Integrated": "NVIDIA card off — best battery",
    "Hybrid": "NVIDIA card wakes on demand",
    "NvidiaNoModeset": "NVIDIA loaded without modesetting",
    "Vfio": "NVIDIA card bound to vfio for a VM",
    "AsusEgpu": "An external GPU drives the display",
    "AsusMuxDgpu": "Display wired straight to NVIDIA — fastest",
}

# The row that repeats supergfxd's reply to a switch, word for word. A toast
# is gone in five seconds and a refusal is the thing you most want to still
# be able to read.
MODE_ANSWER_TITLE = "supergfxd's answer"
MODE_ANSWER_SILENT_OK = "It accepted the change without printing anything."
MODE_ANSWER_SILENT_FAIL = "It refused the change without saying why."

NO_DAEMON_SUBTITLE = (
    "supergfxctl is installed but supergfxd is not answering, so the current "
    "mode cannot be read and nothing can be switched. Check the service with "
    "systemctl status supergfxd."
)

# How long the reboot dialog leaves between OK and the reboot itself. Long
# enough to notice it is happening and pull the plug on it with Ctrl+Alt+F2
# if it was pressed by accident; short enough not to look stuck.
REBOOT_DELAY_SECONDS = 5


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
        # Consecutive failures of the sampler below, so a page whose
        # readings have stopped coming back says so once instead of
        # showing dashes forever. See sampling.py.
        self._sample_failures = SampleFailures("GPU")
        self._timer_id = None

        # Graphics mode. ``modes`` is what the picker holds and
        # ``supported_modes`` the last non-empty answer supergfxctl -s gave;
        # the two are deliberately not the same list. ``current_mode`` is
        # what is running, and is what decides whether a switch needs a
        # reboot.
        self.modes = []
        self.supported_modes = []
        self.current_mode = None
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
        # Side by side on one row -- see the CPU page, which pairs the same
        # two readings the same way.
        self.temp_cell = StatCell(
            "Temperature",
            "The card's own sensor. Reads Idle while the card is asleep.")
        self.fan_cell = StatCell(hardware.FAN_LABELS[FAN_CHANNEL])
        build_stat_row(status, (self.temp_cell, self.fan_cell))
        self.temp_value = self.temp_cell.value
        self.fan_value = self.fan_cell.value

        page.add(self._build_gpu_mode())

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
            subtitle="Watts the firmware may move from the CPU",
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
            step=25, unit="MHz", settle_ms=SETTLE_MS)
        core.connect("changed", self._on_changed)
        clocks.add(core)
        self.rows["clock_offset"] = core

        memory = SliderRow(
            title="Memory clock offset", subtitle=OFFSET_SUBTITLE,
            tooltip=OFFSET_TOOLTIP,
            minimum=hardware.MEM_CLOCK_OFFSET_MIN,
            maximum=hardware.MEM_CLOCK_OFFSET_MAX,
            step=25, unit="MHz", settle_ms=SETTLE_MS)
        memory.connect("changed", self._on_changed)
        clocks.add(memory)
        self.rows["mem_clock_offset"] = memory
        align_value_widths([ceiling, core, memory])

        self._build_actions_group()
        self._apply_capability_gating()

    def _build_actions_group(self):
        """The page's header-bar buttons -- see widgets/action_buttons.py."""
        self.action_box, self.apply_button, self.revert_button = (
            apply_revert_buttons(
                self._on_apply_clicked, self._on_revert_clicked,
                apply_tooltip=APPLY_TOOLTIP, revert_tooltip=REVERT_TOOLTIP))

    def _build_gpu_mode(self):
        group = Adw.PreferencesGroup(title="Graphics mode")

        # No "Current mode" row: the picker below is a drop-down showing the
        # mode in force, so a row above it stating the same name twice was
        # only taking height. What the mode *means* moved onto the picker's
        # own subtitle, which is the row that was already there.

        # Why there is no picker, when there is no picker. A separate row and
        # not the ComboRow's own subtitle, because an insensitive row draws
        # its text dimmed -- and the one thing this text must be is readable.
        self.mode_blocked_row = Adw.ActionRow(title="Switch mode")
        self.mode_blocked_row.set_subtitle_lines(0)
        self.mode_blocked_row.set_visible(False)
        group.add(self.mode_blocked_row)

        self.mode_row = Adw.ComboRow(title="Switch mode",
                                     subtitle=GPU_MODE_SUBTITLE)
        self.mode_row.set_tooltip_text(GPU_MODE_TOOLTIP)
        # All three from the start, not a list built from supergfxctl -s.
        # The daemon's list says what it will take in the state it is in, not
        # what the machine can do, and filtering by it is what left this
        # picker with a single entry and no way to switch anything.
        self.modes = hardware.gpu_mode_choices()
        self.mode_row.set_model(Gtk.StringList.new(self.modes))
        # One line, not unlimited: the subtitle is a few words now, and an
        # unbounded one is what let a paragraph grow under the picker and
        # squeeze the drop-down until it showed "Asu..." instead of the mode.
        self.mode_row.set_subtitle_lines(1)
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        group.add(self.mode_row)

        # Empty until something has actually been switched, then supergfxd's
        # reply verbatim -- an acceptance or, more usefully, its refusal.
        self.mode_answer_row, self.mode_answer_value = self._value_row(
            group, MODE_ANSWER_TITLE)
        self.mode_answer_row.set_visible(False)

        if not self.caps.get("supergfxctl"):
            self._block_switching(
                "supergfxctl is not installed, so the graphics mode cannot "
                "be read or changed from here. Install supergfxctl and its "
                "supergfxd service to switch between integrated and hybrid "
                "graphics.")
        return group

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
        # -- AsusMuxDgpu, Hybrid -- and breaking inside one produced
        # "Asus-Mux-Dgpu" in a window with room to spare.
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

    def _apply_capability_gating(self):
        """Hide what this machine cannot do.

        A control for a setting this machine cannot act on does not belong
        on the page at all -- see the CPU page's version of this method for
        the fuller reasoning."""
        if not self.caps.get("nvidia"):
            for key in ("watts", "clock_limit"):
                self.rows[key].set_visible(False)
            self.temp_cell.set_note("nvidia-smi is not installed.")
        if not self.caps.get("fan_rpm"):
            # The tachometer is on the asus hwmon, not the card, so it can be
            # missing on a machine whose GPU controls all work.
            self.fan_cell.set_note("No asus hwmon fan reading on this "
                                   "machine.")
        if not self.caps.get("nvidia_settings"):
            for key in ("clock_offset", "mem_clock_offset"):
                self.rows[key].set_visible(False)
        if not self.caps.get("nv_dynamic_boost"):
            self.rows["dyn_boost"].set_visible(False)
        if not self.caps.get("nv_temp_target"):
            self.rows["temp_target"].set_visible(False)
        # Both groups can end up with nothing left in them -- a machine
        # with no NVIDIA card and no ASUS power knobs, say -- and an empty
        # titled group left standing says nothing a missing one would not.
        if not any(self.rows[key].get_visible()
                  for key in ("watts", "dyn_boost", "temp_target")):
            self.power_group.set_visible(False)
        if not any(self.rows[key].get_visible()
                  for key in ("clock_limit", "clock_offset", "mem_clock_offset")):
            self.clocks_group.set_visible(False)

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
        card still has a fan reading worth showing. VRAM lives on the
        Overview page's GPU section, not here -- see overview.py."""
        # Asked first, and it decides whether nvidia-smi runs at all: that
        # call wakes the card to answer it, so polling it every two seconds
        # would hold the dGPU awake for as long as this page is open. On a
        # hybrid machine that is both the wrong reading -- the card is never
        # seen idle -- and a real cost in battery.
        suspended = hardware.dgpu_is_suspended()
        return {
            "dgpu_suspended": suspended,
            "nvidia": (hardware.read_nvidia_stats()
                       if self.caps.get("nvidia") and not suspended
                       else (None, None)),
            "fan_rpm": hardware.read_fan_rpms().get(FAN_CHANNEL),
            "mode": (hardware.read_gpu_mode()
                     if self.caps.get("supergfxctl") else None),
            "modes": (hardware.read_supported_gpu_modes()
                      if self.caps.get("supergfxctl") else []),
        }

    def _on_sample(self, result, error):
        self._sampling = False
        if error is not None:
            # A run of these is reported once; see sampling.py.
            self._sample_failures.report(self.window, error, source="gpu")
            return
        self._sample_failures.succeeded()
        self._render(result)

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
        if not self._switching:
            self.current_mode = data.get("mode")
            self._render_modes(data.get("modes") or [], self.current_mode)

    # -- graphics mode -------------------------------------------------------

    def _render_modes(self, supported, active):
        """Fill the picker without letting -s decide what is in it.

        See hardware.gpu_mode_choices: what the daemon lists is what it will
        take in the state it is in, which on a machine sitting in AsusMuxDgpu
        is that one mode. Filtering by it is what left this picker unable to
        switch anything."""
        if supported:
            self.supported_modes = list(supported)
        modes = hardware.gpu_mode_choices(active, self.supported_modes)
        was_loading = self._loading
        self._loading = True
        try:
            if modes != self.modes:
                self.modes = modes
                self.mode_row.set_model(Gtk.StringList.new(modes))
            if active in modes:
                index = modes.index(active)
                if self.mode_row.get_selected() != index:
                    self.mode_row.set_selected(index)
        finally:
            self._loading = was_loading
        # The picker names the mode; its subtitle says what that mode means,
        # so the description is not a second row.
        if active:
            self.mode_row.set_subtitle(GPU_MODE_DESCRIPTIONS.get(
                active, GPU_MODE_SUBTITLE))
        if not self.caps.get("supergfxctl"):
            return
        # Set both ways round, not just off: supergfxd can be restarted under
        # a running window, and a row latched insensitive on one sample would
        # never come back.
        if active is None:
            self._block_switching(NO_DAEMON_SUBTITLE)
            return
        self._allow_switching()

    def _on_mode_changed(self, row, _param):
        if self._loading or self._switching:
            return
        item = row.get_selected_item()
        if item is None:
            return
        mode = item.get_string()
        if hardware.mode_needs_hybrid_first(self.current_mode, mode):
            # Refused, not attempted. supergfxd would take this happily,
            # store Integrated, and power down the card the panel is wired
            # to -- then re-apply it at every login. That is the freeze this
            # machine spent three boots in.
            self._offer_hybrid_first(mode)
            return
        needs_reboot = hardware.mode_change_needs_reboot(self.current_mode,
                                                         mode)
        # Asked, never assumed: either answer ends the session. The picker is
        # put back first, so declining leaves the row showing what is
        # actually running rather than the mode that was not switched to.
        body = ("This moves the hardware MUX, which only the firmware can "
                "do, so the machine has to reboot to finish it."
                if needs_reboot else
                "This restarts the display stack. You will be logged out and "
                "anything unsaved in any application will be lost.")
        dialog = Adw.AlertDialog(
            heading=f"Switch graphics mode to {mode}?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("switch", f"Switch to {mode}")
        dialog.set_response_appearance("switch",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_mode_response, mode)
        dialog.present(self)

    def _offer_hybrid_first(self, mode):
        """Integrated cannot be reached directly from the MUX mode."""
        dialog = Adw.AlertDialog(
            heading="Switch to Hybrid first",
            body=f"{mode} powers the NVIDIA card down, but the hardware MUX "
                 f"still has your display wired to that card — so it cannot "
                 f"be done in one step, and doing it anyway freezes the "
                 f"session at every login.\n\n"
                 f"Switch to Hybrid first, which moves the MUX and needs a "
                 f"reboot. {mode} is available once the machine comes back.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("hybrid", "Switch to Hybrid")
        dialog.set_response_appearance("hybrid",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("hybrid")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_hybrid_first_response)
        dialog.present(self)

    def _on_hybrid_first_response(self, _dialog, response):
        # Either way the picker goes back to what is running: it is showing
        # the mode that was asked for and refused.
        self._render_modes(self.supported_modes, self.current_mode)
        if response == "hybrid":
            self._on_mode_response(None, "switch", "Hybrid")

    def _on_mode_response(self, _dialog, response, mode):
        if response != "switch":
            self._render_modes(self.supported_modes, self.current_mode)
            return
        self._switching = True
        self.mode_row.set_sensitive(False)
        self.mode_answer_row.set_visible(False)
        self.window.toast(f"Switching graphics mode to {mode}…")
        self.window.apply_async(
            lambda: hardware.set_gpu_mode(mode),
            lambda result, error: self._on_mode_applied(mode, result, error))

    def _on_mode_applied(self, mode, result, error):
        self._switching = False
        self.mode_row.set_sensitive(True)
        ok, message = (False, str(error)) if error is not None else result
        self._show_mode_answer(mode, ok, message)
        if not ok:
            self.window.toast(f"Graphics mode change failed: {message}")
            self._start_sample()
            return
        if hardware.mode_change_needs_reboot(self.current_mode, mode):
            # The MUX flip is queued in firmware and applied at POST.
            # Nothing a running system does finishes it, so offering "log
            # out" here would send the user round a loop that cannot work.
            self._ask_to_reboot(mode)
            return
        self.window.toast(f"Graphics mode set to {mode}. "
                          f"Log out to finish switching.")
        self._start_sample()

    def _ask_to_reboot(self, mode):
        dialog = Adw.AlertDialog(
            heading="Reboot to finish switching?",
            body=f"{mode} is set, and the hardware MUX changes at the next "
                 f"boot. The machine will restart in "
                 f"{REBOOT_DELAY_SECONDS} seconds.")
        dialog.add_response("later", "Later")
        dialog.add_response("reboot", "Reboot now")
        dialog.set_response_appearance("reboot",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("later")
        dialog.set_close_response("later")
        dialog.connect("response", self._on_reboot_response, mode)
        dialog.present(self)

    def _on_reboot_response(self, _dialog, response, mode):
        if response != "reboot":
            self.window.toast(f"{mode} is set — it takes effect at the next "
                              f"reboot.")
            self._start_sample()
            return
        self.window.toast(f"Rebooting in {REBOOT_DELAY_SECONDS} seconds…")
        GLib.timeout_add_seconds(REBOOT_DELAY_SECONDS, self._do_reboot)

    def _do_reboot(self):
        ok, message = hardware.reboot_system()
        if not ok:
            self.window.toast(f"Could not reboot: {message}")
        return GLib.SOURCE_REMOVE

    def _show_mode_answer(self, mode, ok, message):
        """Put supergfxd's reply on the page, word for word.

        Verbatim and not summarised: when the daemon refuses, its own
        wording is the only thing that says which of several reasons
        applied. A toast is gone in five seconds; this stays until the next
        attempt."""
        self.mode_answer_row.set_title(f"supergfxd's answer to {mode}")
        self.mode_answer_value.set_text("accepted" if ok else "refused")
        for css in ("success", "warning"):
            self.mode_answer_value.remove_css_class(css)
        self.mode_answer_value.add_css_class("success" if ok else "warning")
        self.mode_answer_row.set_subtitle(
            (message or "").strip()
            or (MODE_ANSWER_SILENT_OK if ok else MODE_ANSWER_SILENT_FAIL))
        self.mode_answer_row.set_visible(True)

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
        # See the CPU page: the header buttons replaced this banner.
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

    # -- applying ------------------------------------------------------------

    def _pending_values(self):
        """What the controls hold, for the settings this machine can write."""
        return [(key, int(self.rows[key].get_value())) for key in APPLY_ORDER
                if self.caps.get(CAPABILITY[key])]

    def _set_busy(self, busy):
        self._applying = busy
        self.apply_button.set_sensitive(not busy)
        self.revert_button.set_sensitive(not busy)

    def set_hardware_busy(self, busy):
        """Something else is writing the machine -- see app.claim_hardware."""
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
