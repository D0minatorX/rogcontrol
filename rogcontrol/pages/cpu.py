"""CPU page: power limits and tuning, written when Apply is pressed.

Nothing on this page reaches the hardware until Apply. Moving a slider
changes a pending value and raises the banner at the top of the page; the
banner names what has not been written yet and carries its own Apply button,
exactly as the Fans page does, so all three tuning pages behave alike.

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
  then the kHz clock cap **last**: writing cpufreq's boost refreshes every
  policy and takes ``scaling_max_freq`` back up to hardware maximum with it,
  so a cap written before it is silently undone. The order lives in
  ``hardware.cpu_apply_plan`` where it can be tested without a display.

Leaving the page, or switching profile, with unapplied changes discards them
and puts the profile's own values back. Silently applying settings the user
walked away from is the behaviour this page exists to remove.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.slider_row import SliderRow, align_value_widths  # noqa: E402

# The sliders report as soon as they move rather than after a settle: nothing
# is applied here any more, so the only thing a change drives is the banner,
# and a banner that appears half a second after the handle does looks like a
# lag rather than a consequence.
SETTLE_MS = 0

# How often the live readings are refreshed, matching the Overview and GPU
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

APPLY_SUBTITLE = (
    "Writes everything on this page to the chip, in the one order that works: "
    "the power limits, then turbo boost, then the energy preference, then the "
    "clock ceiling — the boost switch resets every policy's ceiling, so the "
    "cap has to go last. Takes a second or two."
)

# Which controls each step of the apply owns, for saving what succeeded and
# putting back what did not. "epp" owns no control: it comes from the profile
# and there is no widget for it.
STEP_ROWS = {
    "limits": ("stapm", "fast", "slow", "temp", "coall"),
    "boost": ("boost",),
    "epp": (),
    "clock": ("clock",),
}

STEP_LABELS = {
    "limits": "Power limits",
    "boost": "Turbo boost",
    "epp": "Energy preference",
    "clock": "Clock ceiling",
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

        status = Adw.PreferencesGroup(title="Processor")
        page.add(status)
        # Temperature first, then the fan answering it: this is the pair the
        # fan curve is tuned against, and the GPU page shows the same two in
        # the same order.
        self.temp_row, self.temp_value = self._live_row(status, "Temperature")
        self.temp_row.set_subtitle(
            "k10temp Tctl — the reading the embedded controller drives the "
            "fans from.")
        self.fan_row, self.fan_value = self._live_row(
            status, hardware.FAN_LABELS[FAN_CHANNEL])
        if not self.caps.get("cpu_temp"):
            self.temp_row.set_subtitle("No CPU temperature sensor found on "
                                       "this machine")
        if not self.caps.get("fan_rpm"):
            self.fan_row.set_subtitle("No asus hwmon fan reading on this "
                                      "machine")

        limits = Adw.PreferencesGroup(
            title="Power limits",
            description="Sent to ryzenadj as one set — Apply re-sends all "
                        "four together.")
        page.add(limits)
        for key, title, subtitle, low, high, unit in LIMIT_ROWS:
            row = SliderRow(title=title, subtitle=subtitle, minimum=low,
                            maximum=high, step=1, unit=unit,
                            settle_ms=SETTLE_MS)
            row.connect("changed", self._on_control_changed)
            limits.add(row)
            self.rows[key] = row
        # One readout width across the group, so the four scales end in a
        # column instead of stopping wherever "150 W" and "100 °C" happen to.
        align_value_widths([self.rows[key] for key, *_ in LIMIT_ROWS])

        tuning = Adw.PreferencesGroup(title="Tuning")
        page.add(tuning)

        coall = SliderRow(title="Curve Optimizer", subtitle=COALL_SUBTITLE,
                          minimum=hardware.COALL_MIN,
                          maximum=hardware.COALL_MAX, step=1,
                          settle_ms=SETTLE_MS)
        coall.connect("changed", self._on_control_changed)
        tuning.add(coall)
        self.rows["coall"] = coall

        boost = Adw.SwitchRow()
        boost.set_title("Turbo boost")
        boost.set_subtitle(BOOST_SUBTITLE)
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
            settle_ms=SETTLE_MS,
            subtitle=f"Hard ceiling; cores still idle down freely. "
                     f"{self.max_ghz:.1f} means no limit.")
        clock.connect("changed", self._on_control_changed)
        tuning.add(clock)
        self.rows["clock"] = clock

        page.add(self._build_actions_group())
        self._apply_capability_gating(limits, tuning)

    def _build_actions_group(self):
        group = Adw.PreferencesGroup(title="Apply")
        self.apply_row = Adw.ActionRow(title="Apply CPU settings",
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

            # max_freq of 0 (or absent) is this config's way of saying "no
            # ceiling", which on screen is the top of the range.
            max_freq = cpu.get("max_freq") or 0
            ghz = self.max_ghz if not max_freq else max_freq / 1e6
            row = self.rows["clock"]
            row.set_value(self._clamp(row, ghz))
            self._applied["clock"] = row.get_value()
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

    def _on_control_changed(self, _row, _value):
        if self._loading:
            return
        self._update_banner()

    def _on_switch_changed(self, _row, _param):
        if self._loading:
            return
        self._update_banner()

    def _update_banner(self):
        if self._applying:
            # The banner is the progress line while an apply is running; the
            # apply owns it until it finishes.
            return
        dirty = self._dirty_keys()
        if not dirty:
            self.banner.set_revealed(False)
            return
        titles = {"stapm": "power limits", "fast": "power limits",
                  "slow": "power limits", "temp": "power limits",
                  "coall": "Curve Optimizer", "boost": "turbo boost",
                  "clock": "the clock ceiling"}
        named = []
        for key in dirty:
            name = titles[key]
            if name not in named:
                named.append(name)
        self._show_banner("Not applied yet — " + ", ".join(named)
                          + " changed.", button="Apply")

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
        values = {
            "stapm": int(self.rows["stapm"].get_value()) * 1000,
            "fast": int(self.rows["fast"].get_value()) * 1000,
            "slow": int(self.rows["slow"].get_value()) * 1000,
            "temp": int(self.rows["temp"].get_value()),
            "coall": int(self.rows["coall"].get_value()),
            "boost": bool(self.rows["boost"].get_active()),
            "max_freq": 0 if unlimited else int(round(ghz * 1e6)),
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
        self._set_busy(True)
        self._show_banner("Writing the CPU settings…")
        self.window.apply_async(lambda: self._apply_worker(plan),
                                lambda result, error: self._on_applied(
                                    values, snapshot, result, error))

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

    def _on_applied(self, values, snapshot, results, error):
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

        self._save(values, snapshot, applied_steps)
        # Everything a failed step owns goes back to the last value the
        # hardware accepted, so no control is left claiming a setting the
        # chip refused.
        failed_rows = [key for step, ok, _ in results if not ok
                       for key in STEP_ROWS[step]]
        if failed_rows:
            self._restore(failed_rows)

        if failures:
            self._show_banner("Some CPU settings were not applied — "
                              + "; ".join(failures), button="Apply")
            self.window.toast("CPU: " + "; ".join(failures))
        else:
            # Not an unconditional hide: a control moved while the write was
            # running is genuinely unapplied, and the banner has to say so.
            self._update_banner()
            self.window.toast("CPU settings applied and saved to "
                              f"{self.window.current_profile_name()}.")

    def _save(self, values, snapshot, steps):
        """Write what reached the hardware into the profile.

        Only the steps that took: a profile holding a limit the chip refused
        is a profile that silently disagrees with the machine, and the next
        window to open would show it as fact."""
        profile = self.window.current_profile()
        if not profile:
            return
        data = profile.setdefault("cpu", {})
        wrote = False
        for step in steps:
            if step == "limits":
                for key in ("stapm", "fast", "slow", "temp", "coall"):
                    data[key] = values[key]
                wrote = True
            elif step == "boost":
                data["boost"] = values["boost"]
                wrote = True
            elif step == "clock":
                # Stored even when 0: that means "this profile wants no
                # ceiling" and still has to be applied, or switching away
                # from a limited profile would leave its cap behind.
                data["max_freq"] = values["max_freq"]
                wrote = True
            for key in STEP_ROWS[step]:
                self._applied[key] = snapshot[key]
        if wrote:
            config_mod.save_config(self.window.config)

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
