"""Fans: three curve editors, one Apply button, and an honest status line.

This page is the reason the app exists, and it is built around one hardware
fact that shapes everything on it: **the embedded controller can silently
drop fan-curve writes fired too close together on different channels.**
The first measurement on this machine found a 0.5 s gap left two of three
fans stuck on their old curve, so 8 s was adopted as the safe value. A later,
more careful re-test -- 0.5 s through 8 s, several rounds each, reading the
curve back from the driver after every round -- found 0.5 s through 8 s all
held; the original 0.5 s failure was not reproduced. 5 s was kept as the
working value, for margin over the retested floor rather than because
anything shorter was shown to fail. So writing all three curves takes about
ten seconds.

Everything else here follows from that:

* **Nothing is applied on drag.** A hardware write per dragged point would
  be ten seconds long here, with the next drag interrupting the last one
  mid-gap -- which is exactly how the old version managed to look like it
  was ignoring the curve while it was in fact re-pushing it constantly.
  There is an Apply button instead, and the CPU and GPU pages now follow
  this page rather than the other way round.
* **The apply runs on a worker thread with a progress bar**, because a
  ten second freeze is indistinguishable from a hang.
* **The page says when the embedded controller has thrown the curve away.**
  It knows because it reads the curve back out of the driver every two
  seconds, rather than tracking a dirty flag: a flag only knows what this
  window did, and the EC drops curves behind its back on every power-mode
  change. That is the one thing left on the banner -- an edit that has not
  been applied yet needs no banner, because Apply is in the header bar.

The Y axis of every graph is real rpm, from this machine's own measured
calibration, and the calibration is per fan -- the mid fan reaches 7814 rpm
where the CPU fan stops at 6585, so the three graphs are not interchangeable
even when the curves on them look identical.
"""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import fancurve  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.action_buttons import make_action_buttons  # noqa: E402
from ..widgets.curve_editor import CurveEditor  # noqa: E402

REFRESH_SECONDS = 2
DASH = "—"

# Seconds between one channel's curve write and the next. See the module
# docstring: retested down to 0.5s with no failures, kept at 5s for margin.
CHANNEL_GAP_S = 5

# Percentages the calibration drives the fans to. The three-point version
# of this (20/45/70, no 100%) shipped first and undersold itself: a
# straight line fit to three mid-range points is only as good as the
# assumption that the fan's real response IS a straight line all the way
# to the top, which is exactly the thing this app cannot know without
# measuring it -- and on at least one real machine the fitted ceiling
# missed the fan's actual measured rpm at a flat 100% curve. FAN_RPM_CAL's
# own original measurement (see fancurve.py) always included a real 100%
# sample for this reason; the app-driven calibration just never matched
# it until now.
CAL_PERCENTS = (20, 45, 70, 100)

# How settling is judged done, rather than assumed done. A fixed 22s wait
# -- measured against low/mid percentages, where the jump from idle is
# small -- was the other half of the same bug: driven straight to a flat
# 100% on a real machine, one fan was still climbing a full minute in and
# did not stop until close to 80s, more than 3000 rpm past where the 22s
# mark had caught it. Polling for "stopped climbing" adapts to however big
# a jump each step actually is, rather than assuming every step needs the
# same wait -- most settle well before CAL_SETTLE_MAX_S, which is the
# giving-up point, not the expected time.
CAL_SETTLE_POLL_S = 3
CAL_SETTLE_STABLE_SAMPLES = 3
# A little above the ~100 rpm granularity the driver reports, so ordinary
# read jitter cannot restart the stability count.
CAL_SETTLE_STABLE_BAND = 150
CAL_SETTLE_MAX_S = 100

APPLY_TOOLTIP = (
    "Writes all three curves to the fan controller. Takes about 10 seconds: "
    "the controller can drop curve writes sent too close together, so "
    "each fan is written on its own and waited out."
)

CALIBRATE_TOOLTIP = (
    "Measures how these fans actually respond, so the rpm figures on the "
    "graphs are this machine's rather than the developer's. Takes a few "
    "minutes -- longer if a fan needs more time to settle at full speed "
    "-- and the fans will audibly speed up and slow down."
)

CALIBRATE_BODY = (
    "This measures how your fans actually respond, so the rpm figures shown "
    "are yours rather than estimates.\n\n"
    "• Takes a few minutes -- each step waits for the fans to actually stop "
    "changing speed, not a fixed guess\n"
    "• The fans will audibly speed up and slow down, briefly at full speed\n"
    "• The background enforcer is paused, then restarted\n"
    "• Your saved curves are not modified, and are written back at the end\n\n"
    "Best run while the machine is idle."
)

UNCALIBRATED_NOTE = (
    "The rpm figures on these graphs are estimates measured on the "
    "developer's laptop. Run Calibrate fan RPM once to measure your own."
)


class FansPage(Gtk.Box):
    """A banner, three curve editors, and the apply/calibrate actions.

    A plain Box rather than an Adw.PreferencesPage because the banner has to
    stay put: a status line that scrolls away is one the user reads once and
    never sees again, and this one is how they learn the fans are not
    running what is on screen.
    """

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.editors = {}
        self.fan_groups = {}
        self.rpm_labels = {}
        self._sampling = False
        self._working = False
        self._progress = None
        self._progress_source = None
        self._timer_id = None
        # What the last sample said the hardware holds, so the banner can be
        # recomputed after a drag without waiting two seconds for the next
        # one.
        self._hw_points = {}
        self._hw_enabled = {}

        self._build()
        self.reload()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", self._on_apply_clicked)
        self.append(self.banner)

        page = Adw.PreferencesPage()
        page.set_vexpand(True)
        self.append(page)

        self._build_action_buttons()
        for channel in hardware.FAN_CHANNELS:
            group = self._build_fan_group(channel)
            self.fan_groups[channel] = group
            page.add(group)
        page.add(self._build_calibration_group())

        if not self.window.caps.get("fan_curve"):
            self._disable_everything()
        elif not self.window.config.get("fan_rpm_cal"):
            # On the button, which is the only place the calibration is
            # mentioned now. The rpm figures being someone else's is worth
            # saying, but not worth a permanent row above three graphs.
            self.calibrate_button.set_tooltip_text(
                UNCALIBRATED_NOTE + "\n\n" + CALIBRATE_TOOLTIP)

    def _build_fan_group(self, channel):
        name = hardware.FAN_LABELS[channel]
        group = Adw.PreferencesGroup(title=name)
        if channel == hardware.FAN_CHANNELS[0]:
            # Once, on the first graph. Repeating it under all three would
            # cost three lines of vertical space to say the same thing.
            # The keyboard controls used to be spelled out here, three lines
            # of it above the first graph. They are on the graph's own
            # tooltip -- and in its accessible description, which is what a
            # screen reader reads out -- so this only has to point at them.
            group.set_description(
                "Drag a point to move it; hover a graph for the keyboard "
                "controls.")

        # The live speed belongs in the group header, beside the fan's name:
        # it is what the curve below is being judged against.
        label = Gtk.Label(label=DASH)
        label.add_css_class("numeric")
        label.add_css_class("dim-label")
        group.set_header_suffix(label)
        self.rpm_labels[channel] = label

        editor = CurveEditor(
            rpm_cal=fancurve.get_rpm_cal(self.window.config, channel),
            label=f"{name} curve")
        editor.connect("changed", self._on_curve_changed)
        self.editors[channel] = editor

        row = Adw.PreferencesRow()
        row.set_activatable(False)
        # The row must not take focus itself, or Tab would stop on the row
        # and again on the graph inside it.
        row.set_focusable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for setter in (box.set_margin_top, box.set_margin_bottom,
                       box.set_margin_start, box.set_margin_end):
            setter(8)
        box.append(editor)
        row.set_child(box)
        group.add(row)
        return group

    def _build_action_buttons(self):
        """This page's header-bar buttons: Calibrate, then Apply.

        No Revert: the curve editors show the profile itself rather than a
        staged copy of it, so there is no pending edit to discard. Calibrate
        takes Revert's place as the non-suggested action -- see
        widgets/action_buttons.py.

        The progress bar the calibration drives stays on the page below,
        because a header bar is no place for something that has to be
        watched for two and a half minutes."""
        self.action_box, (self.calibrate_button, self.apply_button) = (
            make_action_buttons((
                ("Calibrate", self._on_calibrate_clicked, CALIBRATE_TOOLTIP,
                 False),
                ("Apply", self._on_apply_clicked, APPLY_TOOLTIP, True),
            )))

    def _build_calibration_group(self):
        """Just the progress bar the long jobs drive.

        There is no "Calibrate fan RPM" row any more. The button is in the
        header bar and carries the same explanation on hover, so the row was
        a title and a subtitle restating a control that was no longer next
        to it. The whole group hides itself when the bar is hidden -- see
        _start_progress -- rather than leaving a titled group with nothing
        under it."""
        self.progress_group = group = Adw.PreferencesGroup(
            title="Fan controller")
        group.set_visible(False)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_hexpand(True)
        progress_row = Adw.PreferencesRow()
        progress_row.set_activatable(False)
        progress_row.set_focusable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for setter in (box.set_margin_top, box.set_margin_bottom,
                       box.set_margin_start, box.set_margin_end):
            setter(10)
        box.append(self.progress)
        progress_row.set_child(box)
        self.progress_row = progress_row
        progress_row.set_visible(False)
        group.add(progress_row)
        return group

    def _disable_everything(self):
        """Hide every control this machine cannot act on -- the whole page,
        in practice, since asus_custom_fan_curve is what all of it needs.
        The banner is what is left to say why."""
        for group in self.fan_groups.values():
            group.set_visible(False)
        for widget in (self.apply_button, self.calibrate_button):
            widget.set_visible(False)
        self.banner.set_title(
            "This kernel or model does not expose asus_custom_fan_curve, so "
            "fan curves cannot be set.")
        self.banner.set_revealed(True)

    # -- loading -------------------------------------------------------------

    def reload(self):
        """Put the active profile's curves on the graphs."""
        curves = (self.window.current_profile() or {}).get("fans") or {}
        for channel, editor in self.editors.items():
            editor.set_rpm_cal(fancurve.get_rpm_cal(self.window.config, channel))
            editor.set_points(curves.get(channel) or [])
        self._update_banner()

    # -- live readout --------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._stop_progress()

    def _tick(self):
        # Nothing to read while the page is off screen -- the stack unmaps
        # the pages nobody is looking at.
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE
        if not self._sampling:
            self._sampling = True
            self.window.apply_async(self._sample, self._on_sample)
        return GLib.SOURCE_CONTINUE

    def _sample(self):
        """Read the fans. Worker thread -- no widgets in here."""
        return {
            "rpm": hardware.read_fan_rpms(),
            "enabled": hardware.read_fan_curve_enabled(),
            "points": {ch: hardware.read_fan_curve_points(ch)
                       for ch in hardware.FAN_CHANNELS},
        }

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
        rpms = data.get("rpm") or {}
        for channel, label in self.rpm_labels.items():
            rpm = rpms.get(channel)
            label.set_text(DASH if rpm is None else f"{rpm} rpm")
        # The speeds keep updating through an apply -- watching the fan
        # answer as each channel is written is the whole feedback -- but what
        # the driver holds is halfway through changing, so comparing against
        # it would flap the banner between "matches" and "differs" every two
        # seconds for the sixteen the write takes.
        if self._working:
            return
        self._hw_points = data.get("points") or {}
        self._hw_enabled = data.get("enabled") or {}
        self._update_banner()

    # -- unsaved state -------------------------------------------------------

    def _on_curve_changed(self, _editor):
        # Emitted on every frame of a drag, so this has to stay cheap: it
        # compares eight pairs per fan against the last sample and sets a
        # string. No hardware access, no config write.
        self._update_banner()

    def _mismatched_channels(self):
        """Fans whose hardware curve is not the one on screen.

        A channel the driver would not report is left out rather than
        counted as different: "cannot tell" and "differs" look the same on a
        banner, and only one of them is worth telling the user to fix."""
        out = []
        for channel, editor in self.editors.items():
            held = self._hw_points.get(channel)
            if held is None:
                continue
            if not fancurve.curve_matches_hardware(editor.get_points(), held):
                out.append(channel)
        return out

    def _dropped_channels(self):
        """Fans the EC has taken back onto its own curve."""
        return [ch for ch, enabled in (self._hw_enabled or {}).items()
                if enabled is False]

    def _update_banner(self):
        if self._working or not self.window.caps.get("fan_curve"):
            # The banner is the progress line while a job is running; the
            # job's own code owns it until it finishes.
            return
        names = hardware.FAN_LABELS
        # No "not applied yet" arm: Apply is in the header bar, always
        # visible, so a banner saying a curve had been dragged but not
        # written was repeating the button. What is kept is the case the
        # button cannot express -- the embedded controller throwing the
        # custom curve away on its own, behind the user's back, which is
        # this page's reason for reading the hardware back at all.
        dropped = self._dropped_channels()
        if dropped:
            which = ", ".join(names[ch] for ch in dropped)
            self._show_banner(
                f"The fan controller has dropped the custom curve on "
                f"{which}. Apply to put it back.", button="Apply")
        else:
            self.banner.set_revealed(False)

    def _show_banner(self, text, button=None):
        self.banner.set_title(text)
        # An empty label is how AdwBanner hides its button; there is no
        # separate visibility for it.
        self.banner.set_button_label(button or "")
        self.banner.set_revealed(True)

    # -- progress ------------------------------------------------------------

    def _start_progress(self, total_seconds, text):
        """Show the bar and start it filling. ``total_seconds`` is an
        estimate -- the sleeps are known, the helper calls are not -- so the
        fraction is capped just short of full until the work really ends,
        rather than sitting at 100% while something is still running."""
        self._progress = {"start": time.monotonic(),
                          "total": max(1.0, float(total_seconds)),
                          "text": text}
        self.progress.set_fraction(0.0)
        self.progress.set_text(text)
        self.progress_row.set_visible(True)
        self.progress_group.set_visible(True)
        if self._progress_source is None:
            self._progress_source = GLib.timeout_add(200, self._progress_tick)

    def _set_progress_text(self, text):
        """Called from the worker thread via GLib.idle_add."""
        if self._progress is not None:
            self._progress["text"] = text
            self.progress.set_text(text)
        return GLib.SOURCE_REMOVE

    def _progress_tick(self):
        if self._progress is None:
            self._progress_source = None
            return GLib.SOURCE_REMOVE
        elapsed = time.monotonic() - self._progress["start"]
        self.progress.set_fraction(min(0.98, elapsed / self._progress["total"]))
        return GLib.SOURCE_CONTINUE

    def _stop_progress(self):
        self._progress = None
        if self._progress_source is not None:
            GLib.source_remove(self._progress_source)
            self._progress_source = None
        self.progress_row.set_visible(False)
        self.progress_group.set_visible(False)

    def _set_busy(self, busy):
        self._working = busy
        self.apply_button.set_sensitive(not busy)
        self.calibrate_button.set_sensitive(not busy)

    # -- applying ------------------------------------------------------------

    def _on_apply_clicked(self, _widget):
        if self._working or not self.window.caps.get("fan_curve"):
            return
        points = {ch: editor.get_points()
                  for ch, editor in self.editors.items()}
        channels = list(hardware.FAN_CHANNELS)
        total = CHANNEL_GAP_S * max(0, len(channels) - 1) + 2

        # The profile these curves belong to, captured now. Sixteen seconds
        # from now the answer may be a different profile -- the enforcer
        # switches on AC/battery all by itself -- and the curves would be
        # saved over a profile the user never opened. See
        # config.deferred_save_target.
        target = self.window.current_profile_name()

        self._set_busy(True)
        self._show_banner("Writing the curves to the fan controller…")
        self._start_progress(total, "Starting…")
        self.window.apply_async(
            lambda: self._apply_worker(points, channels),
            lambda data, error: self._on_applied(target, data, error))

    def _apply_worker(self, points, channels):
        """Write every channel, waiting CHANNEL_GAP_S between them.

        Worker thread. The order and the gaps are the whole point: channel 1,
        sleep 8, channel 2, sleep 8, channel 3. All three are written every
        time rather than only the ones that look changed -- the driver's
        cached points can match while the EC has thrown the curve away, and
        "Apply" that quietly skipped the fan the user came here to fix would
        be the worst possible behaviour on this page."""
        results = []
        for i, channel in enumerate(channels):
            if i > 0:
                time.sleep(CHANNEL_GAP_S)
            name = hardware.FAN_LABELS[channel]
            GLib.idle_add(self._set_progress_text,
                          f"Writing {name} ({i + 1} of {len(channels)})…")
            flat = fancurve.curve_to_flat(points[channel], 8)
            ok, message = hardware.run_helper("fan", channel, *flat)
            results.append((channel, ok, message))
            if i < len(channels) - 1:
                GLib.idle_add(
                    self._set_progress_text,
                    f"Waiting {CHANNEL_GAP_S}s — the controller ignores "
                    f"curves written closer together than that…")
        return {"results": results, "points": points}

    def _on_applied(self, target, data, error):
        self._stop_progress()
        self._set_busy(False)
        if error is not None:
            self._show_banner(f"Applying the fan curves failed: {error}",
                              button="Apply")
            self.window.toast(f"Fan curves failed: {error}")
            return

        applied = {}
        failures = []
        for channel, ok, message in data["results"]:
            if ok:
                applied[channel] = data["points"][channel]
            else:
                failures.append(f"{hardware.FAN_LABELS[channel]}: {message}")

        refused = self._save(target, applied) if applied else None
        if refused is not None:
            # Said before the per-channel failures, and instead of the
            # success line: which profile the curves did or did not land in
            # is the more important of the two things that just happened.
            self._show_banner(refused, button="Apply")
            self.window.toast(refused)
        elif failures:
            self._show_banner("Some fans were not set — " + "; ".join(failures),
                              button="Apply")
            self.window.toast("Fan curves: " + "; ".join(failures))
        else:
            self.banner.set_revealed(False)
            self.window.toast(f"Fan curves applied and saved to {target}.")
        # The driver's cached points have just changed underneath the last
        # sample, so re-read rather than leaving the banner deciding from
        # stale data.
        self._tick()

    def _save(self, target, applied):
        """Write the curves that reached the hardware into profile ``target``.

        ``target`` is the profile that was active when Apply was pressed,
        not whichever one is active now -- resolving it now is what wrote
        one profile's curves into another. Returns None when the save
        happened, or the sentence to show the user when it was refused.

        Only the channels that actually took are written: a profile that
        stores a curve the fan refused is a profile that silently disagrees
        with the machine, and the next window to open would show it as
        fact."""
        curves = {channel: [[int(t), int(p)] for t, p in points]
                  for channel, points in applied.items()}
        return config_mod.save_deferred(
            self.window.config, target, "fans", curves, "curves",
            where="the fan controller")

    # -- calibration ---------------------------------------------------------

    def _on_calibrate_clicked(self, _button):
        if self._working or not self.window.caps.get("fan_curve"):
            return
        dialog = Adw.AlertDialog(heading="Calibrate fan RPM?",
                                 body=CALIBRATE_BODY)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("run", "Calibrate")
        dialog.set_response_appearance("run", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_calibrate_response)
        dialog.present(self)

    def _on_calibrate_response(self, _dialog, response):
        if response != "run":
            return
        points = {ch: editor.get_points()
                  for ch, editor in self.editors.items()}
        channels = list(hardware.FAN_CHANNELS)
        # Progress-bar pacing only -- the real wait is adaptive (_settle)
        # and ends whenever each step actually stabilises, not on this
        # schedule. _start_progress caps the fraction short of 100% until
        # the work really finishes, so a step that runs long just leaves
        # the bar parked rather than lying about being done.
        per_step_estimate = CHANNEL_GAP_S * (len(channels) - 1) + 60
        total = len(CAL_PERCENTS) * per_step_estimate + CHANNEL_GAP_S * (len(channels) - 1)

        self._set_busy(True)
        self._show_banner("Calibrating the fans — this takes a couple of "
                          "minutes and they will be audible.")
        self._start_progress(total, "Starting…")
        self.window.apply_async(
            lambda: self._calibration_worker(points, channels),
            self._on_calibrated)

    def _calibration_worker(self, points, channels):
        """Drive the fans to four known percentages and fit floor + slope.

        Worker thread. All three fans are driven to the *same* flat
        percentage at once so that one settle wait covers every channel
        rather than one wait per channel.

        The enforcer is stopped first because it re-asserts the profile's
        curve on its own schedule, and a curve pushed back mid-measurement
        makes the fan settle somewhere nobody asked for. The user's curves
        are written back at the end whether the fit worked or not, so a
        failed calibration cannot leave the fans pinned at a flat curve."""
        samples = {ch: [] for ch in channels}
        enforcer_paused = hardware.set_enforcer_running(False)
        try:
            for step, pct in enumerate(CAL_PERCENTS, start=1):
                self._write_flat(channels, pct, step)
                rpms = self._settle(channels, pct, step)
                for channel in channels:
                    samples[channel].append((pct, rpms.get(channel)))

            GLib.idle_add(self._set_progress_text,
                          "Writing your own curves back…")
            for i, channel in enumerate(channels):
                if i > 0:
                    time.sleep(CHANNEL_GAP_S)
                hardware.run_helper(
                    "fan", channel, *fancurve.curve_to_flat(points[channel], 8))

            cal, failed = {}, []
            for channel in channels:
                fit = fancurve.fit_rpm_cal(samples[channel])
                if fit:
                    cal[channel] = [fit[0], fit[1]]
                else:
                    failed.append(hardware.FAN_LABELS[channel])
            return {"cal": cal, "failed": failed}
        finally:
            if enforcer_paused:
                hardware.set_enforcer_running(True)

    def _settle(self, channels, pct, step):
        """Poll every channel's rpm until none of them are still moving, or
        CAL_SETTLE_MAX_S has passed. Returns the last reading for each.

        A channel counts as settled once its own last
        CAL_SETTLE_STABLE_SAMPLES readings, CAL_SETTLE_POLL_S apart, sit
        within CAL_SETTLE_STABLE_BAND of each other -- but this keeps
        sampling every channel until ALL of them have, since one fan
        settling first (usually the smaller jump of the two lower steps)
        must not cut the wait short for a slower one still climbing."""
        history = {ch: [] for ch in channels}
        start = time.monotonic()
        while True:
            rpms = hardware.read_fan_rpms()
            for ch in channels:
                history[ch].append(rpms.get(ch))
            elapsed = time.monotonic() - start
            GLib.idle_add(
                self._set_progress_text,
                f"Step {step} of {len(CAL_PERCENTS)} at {pct}% — letting "
                f"the fans settle, {int(elapsed)}s")
            if all(self._is_settled(history[ch]) for ch in channels):
                break
            if elapsed >= CAL_SETTLE_MAX_S:
                break
            time.sleep(CAL_SETTLE_POLL_S)
        return {ch: history[ch][-1] for ch in channels}

    @staticmethod
    def _is_settled(readings):
        tail = [r for r in readings[-CAL_SETTLE_STABLE_SAMPLES:]
               if r is not None]
        if len(tail) < CAL_SETTLE_STABLE_SAMPLES:
            return False
        return max(tail) - min(tail) <= CAL_SETTLE_STABLE_BAND

    def _write_flat(self, channels, pct, step):
        """Hold every fan at one flat percentage, respecting CHANNEL_GAP_S."""
        flat = []
        for temp in (30, 40, 50, 55, 60, 65, 70, 90):
            flat += [temp, fancurve.pct_to_pwm255(pct)]
        for i, channel in enumerate(channels):
            if i > 0:
                time.sleep(CHANNEL_GAP_S)
            GLib.idle_add(
                self._set_progress_text,
                f"Step {step} of {len(CAL_PERCENTS)} at {pct}% — setting "
                f"{hardware.FAN_LABELS[channel]}…")
            hardware.run_helper("fan", channel, *flat)

    def _on_calibrated(self, data, error):
        self._stop_progress()
        self._set_busy(False)
        if error is not None:
            self._show_banner(f"Calibration failed: {error}. The previous "
                              "figures are unchanged.")
            self.window.toast(f"Calibration failed: {error}")
            self._tick()
            return

        cal, failed = data["cal"], data["failed"]
        if not cal:
            self._show_banner("Calibration found no fan that responded "
                              "measurably. The previous figures are kept.")
            self.window.toast("Calibration failed — no fan responded")
            self._tick()
            return

        # MERGED into whatever is already there, not written over it. A
        # channel that produced no usable fit is not in ``cal``, and
        # replacing the whole dict dropped its previous calibration with it
        # -- so a fan that failed to respond this time silently fell back to
        # the built-in G614PR figures, while the banner below told the user
        # its previous figures were kept. Only the channels that actually
        # measured are updated; every other one keeps what it had.
        saved = self.window.config.get("fan_rpm_cal")
        if not isinstance(saved, dict):
            saved = {}
        saved.update(cal)
        self.window.config["fan_rpm_cal"] = saved
        config_mod.save_config(self.window.config)
        parts = []
        for channel, editor in self.editors.items():
            editor.set_rpm_cal(fancurve.get_rpm_cal(self.window.config, channel))
            if channel in cal:
                floor, slope = cal[channel]
                parts.append(f"{hardware.FAN_LABELS[channel]} "
                             f"{round(floor)}–{round(floor + slope * 100)} rpm")
        text = "Calibrated: " + ", ".join(parts) + "."
        if failed:
            text += (f" No usable reading from {', '.join(failed)} — their "
                     "previous figures are kept.")
        self._show_banner(text)
        self.window.toast("Fan calibration saved.")
        self._tick()

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """One synchronous read-and-render, no thread and no hardware write."""
        self.reload()
        self._render(self._sample())
