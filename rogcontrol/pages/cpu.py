"""CPU page: power limits and tuning, written when Apply is pressed.

Nothing on this page reaches the hardware until Apply. Moving a slider
changes a pending value and nothing else: Apply and Revert live in the
header bar, visible at every scroll position, so all three tuning pages
behave alike and none of them push the page down with a banner to say a
change is waiting.

That is a deliberate reversal. This page used to apply a control 400 ms after
it stopped moving, which meant dragging STAPM from 25 to 75 W could push a
handful of intermediate power limits at the chip on the way past, and there
was no moment at which the user had decided anything. An Apply button is one
decision, one write, one toast.

Two hardware facts shape the code:

* ryzenadj takes all five power values in a single call, so the five rows are
  one step of the apply. A failure invalidates all five, which is why the
  revert restores the whole group rather than one row.
* The order of the steps is not a style choice. It is limits, boost, EPP,
  the kHz clock ceiling, then the clock floor **last**: writing cpufreq's
  boost refreshes every policy and takes both ``scaling_max_freq`` and
  ``scaling_min_freq`` back to the hardware's own values with it, so a cap
  written before it is silently undone -- and the ceiling write itself has to
  pull the floor down whenever the two would cross, so the floor goes after
  the ceiling as well. The order lives in ``hardware.cpu_apply_plan`` where
  it can be tested without a display.

Leaving the page, or switching profile, with unapplied changes discards them
and puts the profile's own values back. Silently applying settings the user
walked away from is the behaviour this page exists to remove.

The clock ceiling is greyed while turbo boost is off, for the same reason:
``hardware.cpu_apply_plan`` drops the ceiling write with boost off -- the
boost write has already pinned every core at its base clock -- so a
live-looking slider there would be claiming a setting no apply sends.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.action_buttons import apply_revert_buttons  # noqa: E402
from ..widgets.slider_row import SliderRow, align_value_widths  # noqa: E402
from ..widgets.stat_row import StatCell, build_stat_row  # noqa: E402

# The sliders report as soon as they move rather than after a settle:
# nothing is applied here any more, so a change only updates the pending
# value the Apply button will write.
SETTLE_MS = 0

# How often the live readings are refreshed, matching the Overview and GPU
# pages so a fan does not appear to be doing two different speeds depending
# on which page you are looking at.
REFRESH_SECONDS = 2
DASH = "—"

# The asus hwmon's fan1. Its label comes from hardware, so this page, the GPU
# page and the Overview all name the same fan the same way.
FAN_CHANNEL = "1"

# (key, title, subtitle, tooltip, min, max, unit). Watts and degrees as the
# user sees them; the config and the helper both work in milliwatts for the
# first three.
#
# The unit belongs to the value, not the title: the slider's readout shows
# "35 W", so the title does not have to carry a "(W)" to disambiguate it from
# the 80 next to it.
#
# The subtitle is a few words, and only where the title alone is ambiguous:
# "STAPM limit" says nothing without "Sustained package power", but
# "Temperature target" on a page of °C sliders needs no help. What each one
# actually means is the tooltip -- seven controls whose explanations are all
# printed under them is a page nobody reads and everybody scrolls.
LIMIT_ROWS = (
    ("stapm", "STAPM limit", "Sustained package power",
     "The ceiling the chip settles at once the short-term windows have "
     "expired — the limit that decides how hard it runs indefinitely.",
     15, 150, "W"),
    ("fast", "Fast limit", "Short-burst ceiling",
     "The ceiling for bursts of a few seconds at a time, before the slow and "
     "sustained windows take over.", 15, 165, "W"),
    ("slow", "Slow limit", "Medium-term ceiling",
     "The ceiling between the fast burst window and the sustained STAPM "
     "limit.", 15, 150, "W"),
    ("temp", "Temperature target", "",
     "The Tctl temperature the chip throttles itself to hold. Lower backs "
     "off sooner and runs quieter.", 60, 100, "°C"),
)

# The one warning on this page that stays on screen. See COALL_TOOLTIP.
COALL_SUBTITLE = "All-core undervolt — too negative freezes the machine"

COALL_TOOLTIP = (
    "All-core undervolt. Negative runs cooler and often slightly faster, "
    "because the chip has more thermal headroom to boost.\n\n"
    "Too negative freezes the machine under load — this laptop locked solid "
    "at −20. Move two or three counts at a time and test under load before "
    "going further. 0 is stock."
)

BOOST_TOOLTIP = (
    "Off pins every core at its base clock. Worth trying if the fans surge at "
    "idle: the EC reads the raw hottest core, and a boost spike hits 85–90 °C "
    "for a few milliseconds even while the reported temperature sits near "
    "57 °C — enough to send the fans to the top of the curve."
)

CLOCK_TOOLTIP = (
    "A hard ceiling on the core clock. The cores still idle right down below "
    "it; this only stops them going above it. At the top of the range no "
    "limit is applied at all.\n\n"
    "Greyed out while turbo boost is off: boost off already pins every core "
    "at its base clock, so nothing is written here until it is back on."
)

MIN_CLOCK_TOOLTIP = (
    "A floor under the core clock: how far down the cores are allowed to "
    "drop while they have work to do. Raise it for snappier response on light "
    "load, at the cost of idle power. At the bottom of the range no floor is "
    "applied and the driver's own resting minimum stands.\n\n"
    "It is a floor on what the kernel asks for, not a guarantee. Under a load "
    "heavy enough to pin the package at its STAPM limit the chip runs below "
    "it anyway — it cannot spend watts it has not got, and no clock setting "
    "changes that. Raise STAPM if that is what you are hitting."
)

APPLY_TOOLTIP = (
    "Writes everything on this page to the chip, in the one order that works: "
    "the power limits, then turbo boost, then the energy preference, then the "
    "clock ceiling and the clock floor — the boost switch resets both of "
    "those, and the ceiling write can pull the floor down with it, so they "
    "go last and in that order."
)

REVERT_TOOLTIP = "Puts every control back to what the profile holds."

# The rows the turbo boost switch governs. Boost off pins every core at its
# base clock, so the ceiling above it is not a limit this profile is
# applying -- hardware.cpu_apply_plan drops the "clock" step for the same
# reason, and this greys the row so the page does not show a live-looking
# control for a write that is not happening. The floor is not in here: it is
# still written and still held with boost off.
BOOST_GATED_ROWS = ("clock",)

# Which controls each step of the apply owns, for saving what succeeded and
# putting back what did not. "epp" owns no control: it comes from the profile
# and there is no widget for it.
STEP_ROWS = {
    "limits": ("stapm", "fast", "slow", "temp", "coall"),
    "boost": ("boost",),
    "epp": (),
    "clock": ("clock",),
    "minclock": ("minclock",),
}

# Which config keys each step writes into the profile once the hardware has
# taken it. A table rather than an if/elif chain in _save: the chain grew a
# branch per step, and a step added to the apply plan without one reached the
# hardware on every Apply and was never saved -- the profile came back
# without it every time the page reloaded. Keyed by every step in
# hardware.CPU_APPLY_STEPS, checked by a test.
STEP_SAVES = {
    "limits": ("stapm", "fast", "slow", "temp", "coall"),
    "boost": ("boost",),
    # The profile already owns the energy preference; the apply only
    # re-asserts it, so there is nothing to write back.
    "epp": (),
    # Both stored even when 0: 0 means "this profile wants no ceiling/floor"
    # and still has to be applied, or switching away from a limited profile
    # would leave its limit behind.
    "clock": ("max_freq",),
    "minclock": ("min_freq",),
}

STEP_LABELS = {
    "limits": "Power limits",
    "boost": "Turbo boost",
    "epp": "Energy preference",
    "clock": "Clock ceiling",
    "minclock": "Clock floor",
}


class CpuPage(Gtk.Box):
    """A banner, the controls, and one Apply button.

    A plain Box rather than an Adw.PreferencesPage because the banner has to
    stay put: a "not applied yet" line that scrolls away is one the user
    reads once and never sees again.
    """

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.caps = window.caps
        # True while values are being written into the widgets from the
        # profile, so loading a profile cannot look like the user turning a
        # dial and raise the banner for every row on the page.
        self._loading = True
        self._applying = False
        # Last values known to have reached the hardware, for deciding what is
        # unapplied and for putting a control back after a rejected apply.
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
        # Walking away from unapplied changes discards them. See the module
        # docstring: the one thing this page must never do is apply something
        # the user left behind.
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

        # Named, as the GPU page names the card. "Processor" alone is the one
        # thing on this page the user already knows.
        status = Adw.PreferencesGroup(
            title="Processor",
            description=hardware.read_cpu_name() or "Unknown processor")
        page.add(status)
        # Temperature first, then the fan answering it, side by side on one
        # row: the fan speed only means anything next to the temperature
        # that caused it. The GPU page shows the same two the same way.
        self.temp_cell = StatCell(
            "Temperature",
            "k10temp Tctl — the reading the embedded controller drives the "
            "fans from.")
        self.fan_cell = StatCell(hardware.FAN_LABELS[FAN_CHANNEL])
        build_stat_row(status, (self.temp_cell, self.fan_cell))
        self.temp_value = self.temp_cell.value
        self.fan_value = self.fan_cell.value
        if not self.caps.get("cpu_temp"):
            self.temp_cell.set_note("No CPU temperature sensor found on this "
                                    "machine.")
        if not self.caps.get("fan_rpm"):
            self.fan_cell.set_note("No asus hwmon fan reading on this "
                                   "machine.")

        limits = Adw.PreferencesGroup(
            title="Power limits",
            description="Sent to ryzenadj as one set — Apply re-sends all "
                        "four together.")
        page.add(limits)
        for key, title, subtitle, tooltip, low, high, unit in LIMIT_ROWS:
            row = SliderRow(title=title, subtitle=subtitle, tooltip=tooltip,
                            minimum=low, maximum=high, step=1, unit=unit,
                            settle_ms=SETTLE_MS)
            row.connect("changed", self._on_control_changed)
            limits.add(row)
            self.rows[key] = row
        # One readout width across the group, so the four scales end in a
        # column instead of stopping wherever "150 W" and "100 °C" happen to.
        align_value_widths([self.rows[key] for key, *_ in LIMIT_ROWS])

        tuning = Adw.PreferencesGroup(title="Tuning")
        page.add(tuning)

        # The only row on the page that keeps a warning in visible text. The
        # rest of this one is on hover like everything else, but "too negative
        # freezes the machine" is not something to find out by hovering: this
        # laptop has actually locked solid at −20, and a tooltip is invisible
        # to anyone who does not happen to rest the pointer here -- and
        # unreachable from a touchscreen altogether.
        coall = SliderRow(title="Curve Optimizer", subtitle=COALL_SUBTITLE,
                          tooltip=COALL_TOOLTIP,
                          minimum=hardware.COALL_MIN,
                          maximum=hardware.COALL_MAX, step=1,
                          settle_ms=SETTLE_MS)
        coall.connect("changed", self._on_control_changed)
        tuning.add(coall)
        self.rows["coall"] = coall

        boost = Adw.SwitchRow()
        boost.set_title("Turbo boost")
        boost.set_tooltip_text(BOOST_TOOLTIP)
        boost.connect("notify::active", self._on_switch_changed)
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
            settle_ms=SETTLE_MS, tooltip=CLOCK_TOOLTIP,
            subtitle=f"{self.max_ghz:.1f} GHz means no limit")
        clock.connect("changed", self._on_control_changed)
        tuning.add(clock)
        self.rows["clock"] = clock

        # The bottom of this one is NOT the hardware minimum the ceiling
        # starts at. It is the floor cpufreq already rests at with nothing
        # set -- 1.5 GHz here, against a 0.4 GHz hardware minimum -- because
        # below that there is no floor to raise, only permission to idle
        # lower than stock, which is a different setting and not this one.
        # See hardware.read_cpu_clock_floor_default.
        self.floor_min_ghz = (
            self.caps.get("cpu_clock_floor") or clock_range[0]) / 1e6
        min_clock = SliderRow(
            title="Minimum core clock", minimum=self.floor_min_ghz,
            maximum=self.max_ghz, step=0.1, digits=1, unit="GHz",
            settle_ms=SETTLE_MS, tooltip=MIN_CLOCK_TOOLTIP,
            subtitle=f"{self.floor_min_ghz:.1f} GHz means no floor")
        min_clock.connect("changed", self._on_control_changed)
        tuning.add(min_clock)
        self.rows["minclock"] = min_clock

        self._build_actions_group()
        self._apply_capability_gating(limits, tuning)

    def _build_actions_group(self):
        """The page's header-bar buttons. Not added to the page itself --
        the window packs this beside the title. See
        widgets/action_buttons.py."""
        self.action_box, self.apply_button, self.revert_button = (
            apply_revert_buttons(
                self._on_apply_clicked, self._on_revert_clicked,
                apply_tooltip=APPLY_TOOLTIP, revert_tooltip=REVERT_TOOLTIP))

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

    def _apply_capability_gating(self, limits_group, tuning_group):
        """Hide what this machine cannot do.

        A control for a setting this machine cannot act on is not a choice
        the user can make here, so it does not belong on the page at all --
        unlike a disabled control, which still claims a row's worth of
        space and a place in the layout for something that will never work
        on this hardware."""
        if not self.caps.get("ryzenadj"):
            # Every row in "Power limits" is one field of the single
            # ryzenadj write hardware.cpu_apply_plan makes -- with it
            # unavailable, nothing is left in the group at all.
            limits_group.set_visible(False)
            self.rows["coall"].set_visible(False)
        if not self.caps.get("cpu_boost"):
            self.rows["boost"].set_visible(False)
        if not self.caps.get("cpu_clock"):
            # One capability, both rows: the ceiling and the floor are two
            # files in the same cpufreq policy directory, and a machine that
            # has one has the other.
            self.rows["clock"].set_visible(False)
            self.rows["minclock"].set_visible(False)
        # "Tuning" holds nothing else -- if all of its rows just went, an
        # empty titled group would be left standing for no reason.
        if not any(self.rows[key].get_visible()
                  for key in ("coall", "boost", "clock", "minclock")):
            tuning_group.set_visible(False)

    # -- loading -------------------------------------------------------------

    @staticmethod
    def _clamp(row, value):
        adj = row.get_adjustment()
        return max(adj.get_lower(), min(adj.get_upper(), value))

    def reload(self):
        """Put the active profile's values on screen without applying them.

        Also what discards unapplied edits: the profile is the truth, and
        ``_applied`` is reset from it, so the banner goes away with them."""
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
            # set_active only emits when the value changes, so the common
            # case -- the same value as the last profile -- would leave the
            # ceiling's sensitivity alone. Set it here rather than rely on
            # the signal.
            self._update_boost_sensitive()

            # max_freq of 0 (or absent) is this config's way of saying "no
            # ceiling", which on screen is the top of the range.
            max_freq = cpu.get("max_freq") or 0
            ghz = self.max_ghz if not max_freq else max_freq / 1e6
            row = self.rows["clock"]
            row.set_value(self._clamp(row, ghz))
            self._applied["clock"] = row.get_value()

            # And min_freq of 0 (or absent) is "no floor", the bottom of that
            # row's range. Deliberately not coupled to the ceiling here: a
            # profile is shown as it is stored, and _pending_values is what
            # refuses to send a crossed pair to the hardware.
            min_freq = cpu.get("min_freq") or 0
            floor_ghz = self.floor_min_ghz if not min_freq else min_freq / 1e6
            row = self.rows["minclock"]
            row.set_value(self._clamp(row, floor_ghz))
            self._applied["minclock"] = row.get_value()

        finally:
            self._loading = was_loading
        self._update_banner()

    # -- live readings -------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_unmap(self, _widget):
        """The page went off screen. Unapplied edits go with it.

        Not applied, and not kept: a slider left half-dragged on a page
        nobody is looking at must not be able to reach the chip later, and a
        page that comes back still claiming a value the hardware never took
        is lying about the machine."""
        if self._applying or not self._dirty_keys():
            return
        self.reload()

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
        """Worker thread: two sysfs reads, no widgets.

        Both in one pass, on the same two second tick, because they are read
        together: a fan speed without the temperature that asked for it says
        nothing about whether the curve is doing the right thing."""
        return {
            "temp_c": hardware.read_cpu_temp(),
            "fan_rpm": hardware.read_fan_rpms().get(FAN_CHANNEL),
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is not None:
            # One failed read is not worth a toast every two seconds; the
            # traceback is already on stderr from apply_async.
            return
        self._render(data)

    def _render(self, data):
        temp = data.get("temp_c")
        # Whole degrees, like the GPU page: k10temp reports in millidegrees,
        # and the third decimal of a number that moves 30 C in a second is
        # noise on screen.
        self.temp_value.set_text(DASH if temp is None else f"{temp:.0f} °C")
        rpm = data.get("fan_rpm")
        # A dash, not a zero: a fan that cannot be read is not a fan that has
        # stopped, and "0 rpm" is the reading that would send someone hunting
        # a hardware fault that is not there.
        self.fan_value.set_text(DASH if rpm is None else f"{rpm} rpm")

    # -- unapplied changes ---------------------------------------------------

    def _current(self, key):
        row = self.rows[key]
        return row.get_active() if key == "boost" else row.get_value()

    def _dirty_keys(self):
        """Controls whose value is not the one the hardware was last given."""
        out = []
        for key in self.rows:
            was = self._applied.get(key)
            if was is None:
                continue
            now = self._current(key)
            if key == "boost":
                if bool(was) != bool(now):
                    out.append(key)
            elif abs(float(was) - float(now)) > 1e-9:
                out.append(key)
        return out

    def _clock_ceiling_active(self):
        """True when the ceiling is a setting this apply will write.

        The one condition, in one place: the page's greying and the floor's
        coupling and clamp all have to agree with the step
        hardware.cpu_apply_plan will or will not emit."""
        if not self.caps.get("cpu_boost"):
            # No boost control on this machine: nothing pins the cores at
            # base clock, so the ceiling is written as it always was.
            return True
        row = self.rows.get("boost")
        return True if row is None else row.get_active()

    def _update_boost_sensitive(self):
        """Grey the clock ceiling while turbo boost is off.

        The ceiling is not written with boost off -- see
        hardware.cpu_apply_plan -- so a row that still looked live would be
        claiming a setting no apply is sending. Insensitive rather than
        hidden: it is one switch away from mattering again, and the group
        must not change height every time that switch is flipped."""
        on = self._clock_ceiling_active()
        for key in BOOST_GATED_ROWS:
            if key in self.rows:
                self.rows[key].set_sensitive(on)

    @staticmethod
    def _push(row, value, upward):
        """Move ``row`` to ``value``, landing past it rather than short of it.

        SliderRow snaps to whole steps measured from its own lower bound, and
        the two clock rows do not start at the same place -- so setting one
        to the other's value can land up to half a step on the wrong side of
        it, which is exactly the crossing this is called to prevent. One more
        step in the direction asked for settles it; the adjustment clamps at
        the end of the range, and both rows end at the same maximum."""
        row.set_value(value)
        adj = row.get_adjustment()
        step = adj.get_step_increment()
        if upward and row.get_value() < value - 1e-9:
            row.set_value(min(adj.get_upper(), row.get_value() + step))
        elif not upward and row.get_value() > value + 1e-9:
            row.set_value(max(adj.get_lower(), row.get_value() - step))

    def _couple_clock_rows(self, row):
        """Keep the floor at or below the ceiling as the user drags either.

        The kernel does not refuse a floor above the ceiling: it accepts the
        write and silently clamps the floor down to the ceiling, so a crossed
        pair on screen would be a setting the machine is not running and
        could not tell you it was not running. Pushing the other row rather
        than refusing the drag leaves every position on both scales
        reachable.

        Done here rather than in reload() on purpose -- see reload()."""
        clock, floor = self.rows.get("clock"), self.rows.get("minclock")
        if clock is None or floor is None:
            return
        # Nothing to keep in order while the ceiling is not being written:
        # with turbo boost off the ceiling is greyed and skipped, so pushing
        # it up under a raised floor would move a control the apply ignores
        # -- and leave it permanently unequal to the last applied value,
        # which is this page's definition of an unapplied change.
        if not self._clock_ceiling_active():
            return
        was, self._loading = self._loading, True
        try:
            if row is clock and floor.get_value() > clock.get_value():
                self._push(floor, clock.get_value(), upward=False)
            elif row is floor and clock.get_value() < floor.get_value():
                self._push(clock, floor.get_value(), upward=True)
        finally:
            self._loading = was

    def _on_control_changed(self, row, _value):
        if self._loading:
            return
        self._couple_clock_rows(row)
        self._update_banner()

    def _on_switch_changed(self, row, _param):
        # Before the loading guard: the clock ceiling has to match the boost
        # switch whether it was the user or a profile load that moved it.
        if row is self.rows.get("boost"):
            self._update_boost_sensitive()
        if self._loading:
            return
        self._update_banner()

    def _update_banner(self):
        if self._applying:
            # The banner is the progress line while an apply is running; the
            # apply owns it until it finishes.
            return
        # No "not applied yet" banner. Apply and Revert are in the header
        # bar now, visible on every page and at every scroll position, so a
        # full-width bar appearing the instant a slider moves said nothing
        # the buttons were not already saying -- and it said it by pushing
        # the whole page down a line. The banner is kept for the things the
        # buttons cannot say: an apply that failed, and a machine that
        # cannot do this at all.
        self.banner.set_revealed(False)

    def _show_banner(self, text, button=None):
        self.banner.set_title(text)
        # An empty label is how AdwBanner hides its button; there is no
        # separate visibility for it.
        self.banner.set_button_label(button or "")
        self.banner.set_revealed(True)

    def _on_revert_clicked(self, _button):
        if self._applying:
            return
        if not self._dirty_keys():
            self.window.toast("Nothing to discard — this is what is running.")
            return
        self.reload()
        self.window.toast("Unapplied CPU changes discarded.")

    # -- applying ------------------------------------------------------------

    def _pending_values(self):
        """What the controls hold, in the units the config and helper use."""
        cpu = (self.window.current_profile() or {}).get("cpu") or {}
        ghz = self.rows["clock"].get_value()
        # The top of the range means "no limit": the profile stores 0 and
        # reads as unlimited everywhere.
        unlimited = ghz >= self.max_ghz - 0.05
        # And the bottom of the floor's own range means "no floor". Half a
        # step of tolerance at each end, so a slider parked on the last stop
        # is not read as a limit a hair inside it.
        floor_ghz = self.rows["minclock"].get_value()
        no_floor = floor_ghz <= self.floor_min_ghz + 0.05
        # Last defence against a crossed pair, and the one that is not
        # cosmetic: _couple_clock_rows keeps the two sliders in order while
        # they are being dragged, but a profile hand-edited to a floor above
        # its ceiling is loaded exactly as written and never goes through it.
        # And not while the ceiling is not being written either: with turbo
        # boost off the ceiling is not a limit the chip is being given, so
        # clamping the floor to it would cut the floor down to a number
        # nothing is enforcing.
        if not no_floor and not unlimited and self._clock_ceiling_active():
            floor_ghz = min(floor_ghz, ghz)
        values = {
            "stapm": int(self.rows["stapm"].get_value()) * 1000,
            "fast": int(self.rows["fast"].get_value()) * 1000,
            "slow": int(self.rows["slow"].get_value()) * 1000,
            "temp": int(self.rows["temp"].get_value()),
            "coall": int(self.rows["coall"].get_value()),
            "boost": bool(self.rows["boost"].get_active()),
            "max_freq": 0 if unlimited else int(round(ghz * 1e6)),
            "min_freq": 0 if no_floor else int(round(floor_ghz * 1e6)),
        }
        # There is no EPP control on this page; the profile owns it, and the
        # apply re-asserts it because a profile's EPP is part of what "these
        # CPU settings" means.
        if cpu.get("epp"):
            values["epp"] = cpu["epp"]
        return values

    def _set_busy(self, busy):
        self._applying = busy
        self.apply_button.set_sensitive(not busy)
        self.revert_button.set_sensitive(not busy)

    def _on_apply_clicked(self, _widget):
        if self._applying:
            return
        values = self._pending_values()
        plan = hardware.cpu_apply_plan(values, self.caps)
        if not plan:
            self.window.toast("Nothing on this page can be set on this "
                              "machine.")
            return
        # What every control held at the moment Apply was pressed. The
        # controls stay live while the write runs, so recording their current
        # position afterwards would mark a change made mid-apply as already
        # on the hardware.
        snapshot = {key: self._current(key) for key in self.rows}
        # And which profile they belong to, for the same reason: the write
        # runs off the main loop, and the enforcer switches profile on
        # AC/battery without asking. Resolving the profile when the write
        # finishes would save these limits into whichever one is current
        # then. See config.deferred_save_target.
        target = self.window.current_profile_name()
        self._set_busy(True)
        self._show_banner("Writing the CPU settings…")
        self.window.apply_async(lambda: self._apply_worker(plan),
                                lambda result, error: self._on_applied(
                                    target, values, snapshot, result, error))

    @staticmethod
    def _apply_worker(plan):
        """Run every step of the plan in order. Worker thread.

        Every step runs even if an earlier one failed: they go to different
        places -- ryzenadj, cpufreq's boost, EPP, the per-policy ceiling --
        and a refused power limit says nothing about whether the clock cap
        can be written."""
        results = []
        for step, args in plan:
            ok, message = hardware.run_helper(*args)
            results.append((step, ok, message))
        return results

    def _on_applied(self, target, values, snapshot, results, error):
        self._set_busy(False)
        if error is not None:
            self._show_banner(f"Applying the CPU settings failed: {error}",
                              button="Apply")
            self.window.toast(f"CPU settings failed: {error}")
            return

        failures, applied_steps = [], []
        for step, ok, message in results:
            if ok:
                applied_steps.append(step)
            else:
                failures.append(f"{STEP_LABELS[step]}: {message}")

        # Only when something reached the chip: an apply the chip refused
        # outright has nothing to save, and saying "written to the hardware
        # but not saved" about it would be untrue in both halves.
        refused = (self._save(target, values, snapshot, applied_steps)
                   if applied_steps else None)
        # Everything a failed step owns goes back to the last value the
        # hardware accepted, so no control is left claiming a setting the
        # chip refused.
        failed_rows = [key for step, ok, _ in results if not ok
                       for key in STEP_ROWS[step]]
        if failed_rows:
            self._restore(failed_rows)

        if refused is not None:
            self._show_banner(refused, button="Apply")
            self.window.toast(refused)
        elif failures:
            self._show_banner("Some CPU settings were not applied — "
                              + "; ".join(failures), button="Apply")
            self.window.toast("CPU: " + "; ".join(failures))
        else:
            # Not an unconditional hide: a control moved while the write was
            # running is genuinely unapplied, and the banner has to say so.
            self._update_banner()
            self.window.toast(
                f"CPU settings applied and saved to {target}.")

    def _save(self, target, values, snapshot, steps):
        """Write what reached the hardware into profile ``target``.

        ``target`` is the profile that was active when Apply was pressed,
        not whichever one is active now. Returns None when the save
        happened, or the sentence to show when it was refused.

        Only the steps that took are written: a profile holding a limit the
        chip refused is a profile that silently disagrees with the machine,
        and the next window to open would show it as fact."""
        data = {}
        for step in steps:
            for key in STEP_SAVES.get(step, ()):
                data[key] = values[key]
        refused = config_mod.save_deferred(
            self.window.config, target, "cpu", data, "CPU settings")
        if refused is not None:
            # ``_applied`` is deliberately left alone too. reload() has
            # already reset it from the profile that is current now, and
            # marking these values as applied on top of that would have the
            # banner claim the new profile is running settings that belong
            # to the old one.
            return refused
        for step in steps:
            for key in STEP_ROWS[step]:
                self._applied[key] = snapshot[key]
        return None

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

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """Load the active profile into every control and render one fan
        read, no hardware writes."""
        self.reload()
        self._render(self._sample())
        # The apply path without the apply: the plan is what the button
        # would run, and building it here catches a values/caps mismatch
        # that would otherwise only show up on a click.
        hardware.cpu_apply_plan(self._pending_values(), self.caps)
