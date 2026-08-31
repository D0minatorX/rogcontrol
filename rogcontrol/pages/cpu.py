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
  revert restores the whole group rather than one row. It is also why the
  "Apply power limits" checkbox governs the Curve Optimizer as well as the
  four limits: there is no sending half of that call.
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

The "Apply power limits" checkbox is stored per profile as ``limits_enabled``
and is read by ``hardware.cpu_apply_plan``, not by this page, so unticking it
stops the limits being written everywhere they are written from -- this page,
the enforcer's 60-second re-assert, the tray apply and the hotkey cycler.
Missing from a profile means ticked, so upgrading does not turn anybody's
limits off.

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
from ..sampling import SampleFailures  # noqa: E402
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

# Intel's table, in the same (key, title, subtitle, tooltip, min, max, unit)
# shape as LIMIT_ROWS above -- the two are never both on screen at once, see
# _apply_capability_gating. Two rows rather than four: Intel has no
# equivalent of the fast/slow windows (PL1/PL2 is the whole of what the
# firmware exposes here) and no equivalent of the temperature target, which
# is an SMU setting with nothing standing in for it on this platform.
PL_ROWS = (
    ("pl1", "PL1 limit", "Sustained package power",
     "The ceiling the chip settles at once the short-term window has "
     "expired — Intel's equivalent of STAPM.",
     hardware.PL_MIN_W, hardware.PL_MAX_W, "W"),
    ("pl2", "PL2 limit", "Short-burst ceiling",
     "The ceiling for short bursts before PL1 takes over — Intel's "
     "equivalent of the fast limit.",
     hardware.PL_MIN_W, hardware.PL_MAX_W, "W"),
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

# The profile key, and the key in ``rows``, for the checkbox at the top of
# the Power limits group. Absent from a profile means on: profiles written
# before the checkbox existed have no such key, and an upgrade must not
# quietly stop applying their limits.
LIMITS_KEY = "limits_enabled"

LIMITS_ENABLED_TOOLTIP = (
    "Whether this profile sets the chip's power limits at all.\n\n"
    "Unticking it and pressing Apply hands the limits back to the firmware: "
    "the firmware is told to reselect its own power table, so the BIOS "
    "numbers are what runs from then on. Nothing here is written afterwards "
    "either — the background service stops re-asserting these limits too, so "
    "they stay the firmware's.\n\n"
    "The governor still follows the profile. Only the power limits are given "
    "up; the energy preference, turbo boost and the clock ceiling and floor "
    "are still this profile's.\n\n"
    "It covers the Curve Optimizer as well. ryzenadj takes the four limits "
    "and the undervolt as a single call, so there is no sending one without "
    "the other.\n\n"
    "The firmware reselecting its power table drops the custom fan curves, "
    "exactly as changing the power mode does. They are re-applied for you."
)

# Said on a machine with no power-limit backend at all -- neither ryzenadj
# nor the Intel ppt/RAPL path below found anything to write to. Not greyed
# out there, gone -- see _apply_capability_gating -- so without this line the
# page would simply be missing the whole "Power limits" group with nothing on
# it saying why. Named per vendor: "Intel is not supported yet" would be a
# false promise about this app once an Intel machine WITH the ppt/RAPL nodes
# shows the sliders instead of this notice, so the title only fires for a
# vendor this app genuinely has nothing for, and the subtitle below explains
# the actual reason rather than assuming it is always "wrong vendor".
UNSUPPORTED_CPU_TITLE = {
    hardware.CPU_VENDOR_INTEL: "No CPU power limit control found",
}
UNSUPPORTED_CPU_TITLE_DEFAULT = "This CPU is not supported yet"

# AMD with no ryzenadj: the vendor is right but the tool is missing.
UNSUPPORTED_CPU_SUBTITLE_AMD = (
    "The power limits and the Curve Optimizer go through ryzenadj, which "
    "is not installed (or cannot reach this chip), so those controls are "
    "not shown here. Turbo boost, the energy preference and the clock "
    "ceiling and floor go through cpufreq and work on this machine as usual."
)

# Intel with neither the asus-wmi ppt_pl1_spl/ppt_pl2_sppt nodes nor a
# writable RAPL constraint -- a real possibility per
# docs/INTEL-SUPPORT-PLAN.txt: the ppt_* nodes may not exist on this model,
# and ASUS firmware frequently locks the RAPL fallback.
UNSUPPORTED_CPU_SUBTITLE_INTEL = (
    "This machine has neither the asus-wmi PL1/PL2 firmware control nor a "
    "writable RAPL power limit, so no CPU power limit control is shown. "
    "There is no Curve Optimizer or temperature target on Intel either way "
    "-- neither has an equivalent on this platform. Turbo boost, the energy "
    "preference and the clock ceiling and floor go through cpufreq and work "
    "on this machine as usual."
)

UNSUPPORTED_CPU_SUBTITLE_DEFAULT = (
    "No CPU power limit control was found on this machine. Turbo boost, the "
    "energy preference and the clock ceiling and floor go through cpufreq "
    "and work as usual."
)

# Controls whose value is a bool, read with get_active() rather than
# get_value(). Everything else in ``rows`` is a SliderRow.
BOOL_ROWS = ("boost", LIMITS_KEY)

# The rows the checkbox governs: the four ryzenadj limits and the Curve
# Optimizer, which travels in the same call. Insensitive while it is off, so
# a slider that would not be written does not look live.
GATED_ROWS = ("stapm", "fast", "slow", "temp", "coall")

# The same checkbox's rows on the Intel backend -- PL1/PL2 and nothing else,
# since there is no Curve Optimizer or temperature target to grey alongside
# them. Chosen in __init__ as self.gated_rows, alongside self.step_rows and
# self.step_saves below, all three keyed on which backend
# caps["cpu_power_limits"] actually is.
PPT_GATED_ROWS = ("pl1", "pl2")

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
#
# This is the AMD shape of "limits" -- the four ryzenadj rows. __init__
# copies this dict into self.step_rows and overwrites the "limits" entry with
# PPT_GATED_ROWS on the ppt/rapl backend, or () with no backend at all, so
# every other step (boost/epp/clock/minclock) is shared unchanged between
# both CPU vendors.
STEP_ROWS = {
    # Owns no control: it is the untick itself reaching the hardware, and
    # the checkbox is not put back when it fails -- see _save.
    "fwreset": (),
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
# hardware.CPU_APPLY_STEPS, checked by a test. The AMD shape of "limits";
# __init__ copies this into self.step_saves with "limits" overwritten the
# same way as self.step_rows above.
STEP_SAVES = {
    # The checkbox that caused this step is saved by _save on its own, on
    # the same "not a step" grounds it is not in STEP_ROWS.
    "fwreset": (),
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
    "fwreset": "Firmware power limits",
    "limits": "Power limits",
    "boost": "Turbo boost",
    "epp": "Energy preference",
    "clock": "Clock ceiling",
    "minclock": "Clock floor",
}


def resolve_power_backend(caps):
    """Which power-limit backend a capability dict means: "ryzenadj", "ppt",
    "rapl" or None.

    A free function, not a method, so both __init__ and
    _apply_capability_gating agree on the answer without either depending on
    the other having run -- the gating tests build a page with
    ``CpuPage.__new__`` and never call __init__ at all.

    caps["cpu_power_limits"] is what a real hardware.detect_capabilities()
    always sets, already resolved in this same priority order. The
    caps.get("ryzenadj") fallback is for a hand-built dict that has never
    heard of the newer key -- every test in this tree, and the other
    scripts' ALL_CPU_CAPS -- which still means "ryzenadj" exactly as it did
    before caps["cpu_power_limits"] existed."""
    backend = caps.get("cpu_power_limits")
    if backend is None and caps.get("ryzenadj"):
        backend = "ryzenadj"
    return backend


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
        # Whether ryzenadj has a chip to talk to at all. Read from the
        # capabilities rather than the hardware, so a test can hand the page
        # an Intel machine without one.
        self._cpu_is_amd = (self.caps.get("cpu_vendor")
                            == hardware.CPU_VENDOR_AMD)
        # Which power-limit backend this machine actually has, if any --
        # see resolve_power_backend just above this class.
        self._power_backend = resolve_power_backend(self.caps)
        # Which rows the checkbox governs, and which config keys the
        # "limits" apply step reads and saves -- all three follow the
        # backend above, so a Curve Optimizer key never gets greyed or saved
        # on an Intel machine and pl1/pl2 never do on an AMD one. Copies of
        # the module-level AMD-shaped tables, not the tables themselves, so
        # multiple pages/instances never share (and one instance's Intel
        # override never leaks into another's dict).
        self.step_rows = dict(STEP_ROWS)
        self.step_saves = dict(STEP_SAVES)
        if self._power_backend in ("ppt", "rapl"):
            self.gated_rows = PPT_GATED_ROWS
            self.step_rows["limits"] = PPT_GATED_ROWS
            self.step_saves["limits"] = PPT_GATED_ROWS
        elif self._power_backend == "ryzenadj":
            self.gated_rows = GATED_ROWS
        else:
            self.gated_rows = ()
            self.step_rows["limits"] = ()
            self.step_saves["limits"] = ()
        # True while values are being written into the widgets from the
        # profile, so loading a profile cannot look like the user turning a
        # dial and raise the banner for every row on the page.
        self._loading = True
        self._applying = False
        # Last values known to have reached the hardware, for deciding what is
        # unapplied and for putting a control back after a rejected apply.
        self._applied = {}
        self._sampling = False
        # Consecutive failures of the sampler below, so a page whose
        # readings have stopped coming back says so once instead of
        # showing dashes forever. See sampling.py.
        self._sample_failures = SampleFailures("CPU")
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
        # k10temp's Tctl is what the embedded controller drives the fans
        # from, so on AMD it is worth naming; on anything else the reading
        # comes from coretemp or the ACPI thermal zone and naming k10temp
        # would be describing a sensor this machine does not have.
        self.temp_cell = StatCell(
            "Temperature",
            "k10temp Tctl — the reading the embedded controller drives the "
            "fans from." if self._cpu_is_amd
            else "The package temperature this machine reports.")
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

        if self._power_backend is None:
            self._add_unsupported_cpu_notice(page)

        # Which table this machine gets, if any: the four ryzenadj rows, the
        # two Intel PL rows, or nothing at all -- see
        # hardware.detect_capabilities' caps["cpu_power_limits"] for how the
        # backend is chosen (ryzenadj first, then ppt, then rapl, then None).
        if self._power_backend == "ryzenadj":
            limit_rows, limits_description = (
                LIMIT_ROWS,
                "Sent to ryzenadj as one set — Apply re-sends all four "
                "together.")
        elif self._power_backend in ("ppt", "rapl"):
            limit_rows, limits_description = (
                PL_ROWS,
                "Sent together, through the firmware's own PL1/PL2 control "
                "on this machine." if self._power_backend == "ppt" else
                "Sent together, through RAPL — verified by reading the "
                "values back, since this firmware sometimes locks them.")
        else:
            limit_rows, limits_description = (), ""

        limits = Adw.PreferencesGroup(
            title="Power limits", description=limits_description)
        page.add(limits)

        # A checkbox, not a switch, and first in the group it governs: it is
        # not a setting of the chip's alongside the four under it, it decides
        # whether those four are written at all. The switches on this page
        # (turbo boost) each turn one piece of hardware on and off; this one
        # turns a section of the page on and off, which is what a checkbox
        # in front of a group says and a switch does not.
        enable_row = Adw.ActionRow(
            title="Apply power limits",
            subtitle="Off hands the limits back to the firmware's own")
        enable_row.set_tooltip_text(LIMITS_ENABLED_TOOLTIP)
        enable_check = Gtk.CheckButton()
        # Centred against a two-line row; without it the box sits against the
        # title and reads as belonging to that line rather than the row.
        enable_check.set_valign(Gtk.Align.CENTER)
        enable_check.connect("toggled", self._on_limits_enabled_toggled)
        enable_row.add_prefix(enable_check)
        # So the whole row is a click target, not the 16 px box alone.
        enable_row.set_activatable_widget(enable_check)
        limits.add(enable_row)
        self.rows[LIMITS_KEY] = enable_check

        for key, title, subtitle, tooltip, low, high, unit in limit_rows:
            row = SliderRow(title=title, subtitle=subtitle, tooltip=tooltip,
                            minimum=low, maximum=high, step=1, unit=unit,
                            settle_ms=SETTLE_MS)
            row.connect("changed", self._on_control_changed)
            limits.add(row)
            self.rows[key] = row
        # One readout width across the group, so the rows end in a column
        # instead of stopping wherever "150 W" and "100 °C" happen to.
        align_value_widths([self.rows[key] for key, *_ in limit_rows])

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

    def _add_unsupported_cpu_notice(self, page):
        """One row saying why half of this page is not here.

        A row rather than the page's banner: the banner is the "not applied
        yet" line, it is revealed and hidden as the user edits, and a second
        permanent message fighting it for the same strip would flicker in
        and out with every slider."""
        vendor = self.caps.get("cpu_vendor")
        if vendor == hardware.CPU_VENDOR_AMD:
            subtitle = UNSUPPORTED_CPU_SUBTITLE_AMD
        elif vendor == hardware.CPU_VENDOR_INTEL:
            subtitle = UNSUPPORTED_CPU_SUBTITLE_INTEL
        else:
            subtitle = UNSUPPORTED_CPU_SUBTITLE_DEFAULT
        # Named only when one was written THIS run -- see MainWindow and
        # app.py's first-launch check. getattr all the way down: the tests
        # in tests/test_cpu_intel_page.py build this page with
        # CpuPage.__new__ and set nothing on it but caps.
        report_path = getattr(getattr(self, "window", None),
                              "hardware_report_path", None)
        if report_path:
            subtitle += (f"\n\nA hardware report was just written to "
                        f"{report_path} — attaching it to an issue is what "
                        f"helps get this machine supported.")
        group = Adw.PreferencesGroup()
        row = Adw.ActionRow(
            title=UNSUPPORTED_CPU_TITLE.get(vendor,
                                            UNSUPPORTED_CPU_TITLE_DEFAULT),
            subtitle=subtitle)
        # Unlimited, or the explanation is ellipsised to one line and says
        # nothing at all.
        row.set_subtitle_lines(0)
        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_valign(Gtk.Align.CENTER)
        row.add_prefix(icon)
        group.add(row)
        page.add(group)

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
        backend = resolve_power_backend(self.caps)
        if backend is None:
            # No backend at all: nothing is left in "Power limits" -- neither
            # table was built (see _build), and the group would otherwise
            # stand there empty.
            limits_group.set_visible(False)
        # The Curve Optimizer is ryzenadj's alone -- there is no equivalent on
        # the ppt/rapl backend, so it is hidden whenever ryzenadj is not the
        # chosen backend, not only when there is no backend at all.
        if backend != "ryzenadj" and "coall" in self.rows:
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
            # coall's row exists on every machine (hidden where it does not
            # apply -- see _apply_capability_gating), so it is always in
            # this dict; stapm/fast/slow/temp only exist as rows on the
            # ryzenadj backend, and pl1/pl2 only on ppt/rapl -- building
            # only the pair that has a row keeps the loop below from a
            # KeyError on whichever backend this machine is not.
            values = {"coall": cpu.get("coall", 0)}
            if self._power_backend == "ryzenadj":
                values.update({
                    # The config keeps these three in milliwatts, which is
                    # what ryzenadj wants; the page shows watts.
                    "stapm": cpu.get("stapm", 55000) / 1000,
                    "fast": cpu.get("fast", 65000) / 1000,
                    "slow": cpu.get("slow", 55000) / 1000,
                    "temp": cpu.get("temp", 90),
                })
            elif self._power_backend in ("ppt", "rapl"):
                # Already in watts in the config -- see profiles.py -- so no
                # unit conversion, unlike the three above.
                values.update({
                    "pl1": cpu.get("pl1", 55),
                    "pl2": cpu.get("pl2", 65),
                })
            for key, value in values.items():
                row = self.rows[key]
                row.set_value(self._clamp(row, value))
                self._applied[key] = row.get_value()

            # Missing means ticked. A profile from before this checkbox
            # existed had its limits applied, and loading it must not turn
            # them off. Setting it fires "toggled", which is what puts the
            # four rows above into the right sensitivity for this profile.
            enabled = bool(cpu.get(LIMITS_KEY, True))
            self.rows[LIMITS_KEY].set_active(enabled)
            self._applied[LIMITS_KEY] = enabled
            # set_active only emits when the value actually changes, so the
            # common case -- the same value as the last profile -- would
            # leave the sensitivity alone. Set it here rather than rely on
            # the signal.
            self._update_limits_sensitive()

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
            # One failed read is not worth a toast every two seconds, and a
            # run of them is worth exactly one. See sampling.py.
            self._sample_failures.report(self.window, error, source="cpu")
            return
        self._sample_failures.succeeded()
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
        return row.get_active() if key in BOOL_ROWS else row.get_value()

    def _dirty_keys(self):
        """Controls whose value is not the one the hardware was last given."""
        out = []
        for key in self.rows:
            was = self._applied.get(key)
            if was is None:
                continue
            now = self._current(key)
            if key in BOOL_ROWS:
                if bool(was) != bool(now):
                    out.append(key)
            elif abs(float(was) - float(now)) > 1e-9:
                out.append(key)
        return out

    def _limits_toggle_moved(self, snapshot):
        """True if the checkbox is not where the last apply left it."""
        return bool(snapshot[LIMITS_KEY]) != bool(
            self._applied.get(LIMITS_KEY, True))

    def _update_limits_sensitive(self):
        """Grey the rows the checkbox governs while it is off.

        Not hidden: unlike a machine with no ryzenadj, these rows are a
        choice the user has, and one they turn back on here. Insensitive
        says "not being written" without the group jumping in height every
        time the box is clicked."""
        on = self.rows[LIMITS_KEY].get_active()
        for key in self.gated_rows:
            self.rows[key].set_sensitive(on)

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

    def _on_limits_enabled_toggled(self, _check):
        # Before the loading guard: the rows below have to match the
        # checkbox whether it was the user or a profile load that moved it.
        self._update_limits_sensitive()
        if self._loading:
            return
        self._update_banner()

    def _update_banner(self):
        if self._applying:
            # The banner is the progress line while an apply is running; the
            # apply owns it until it finishes.
            return
        if self.window.config.get("safety_tripped"):
            # config.record_boot_attempt tripped this after two boots in a
            # row with no proof the undervolt did not freeze the machine.
            # The login apply and the enforcer are both forcing coall to 0
            # right now regardless of what this profile stores, so the
            # slider on screen is not what the chip is actually running --
            # this is the one thing on the page that has to say so.
            # button-clicked below goes to _on_apply_clicked, which clears
            # the trip before sending the plan.
            self._show_banner(
                "Undervolt disabled after repeated boot failures — the CPU "
                "is running stock. Adjust it and Apply to try again.",
                "Apply")
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
            "coall": int(self.rows["coall"].get_value()),
            "boost": bool(self.rows["boost"].get_active()),
            "max_freq": 0 if unlimited else int(round(ghz * 1e6)),
            "min_freq": 0 if no_floor else int(round(floor_ghz * 1e6)),
            # Read by hardware.cpu_apply_plan, which drops the whole
            # limits step when it is False, whichever backend that step
            # turns out to be. The limit values themselves are still sent
            # along either way -- the plan decides, not this function, so
            # what is on screen is what gets saved the moment it is back on.
            LIMITS_KEY: bool(self.rows[LIMITS_KEY].get_active()),
        }
        # Only the rows this backend actually has: the ryzenadj branch of
        # cpu_apply_plan needs all four of stapm/fast/slow/temp present to
        # fire at all, and the ppt/rapl branch needs pl1/pl2 -- sending the
        # other pair's keys with no rows behind them would either KeyError
        # here or, worse, hand the plan values.get() has no widget to match.
        if self._power_backend == "ryzenadj":
            values.update({
                "stapm": int(self.rows["stapm"].get_value()) * 1000,
                "fast": int(self.rows["fast"].get_value()) * 1000,
                "slow": int(self.rows["slow"].get_value()) * 1000,
                "temp": int(self.rows["temp"].get_value()),
            })
        elif self._power_backend in ("ppt", "rapl"):
            values.update({
                "pl1": int(self.rows["pl1"].get_value()),
                "pl2": int(self.rows["pl2"].get_value()),
            })
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

    def set_hardware_busy(self, busy):
        """Something else is writing the machine -- see app.claim_hardware.

        Not folded into _set_busy: that one owns this page's own state, and
        the two can disagree (a profile switch greys these buttons without
        this page applying anything)."""
        if not self._applying:
            self.apply_button.set_sensitive(not busy)
            self.revert_button.set_sensitive(not busy)

    def _on_apply_clicked(self, _widget):
        if self._applying:
            return
        if self.window.config.get("safety_tripped"):
            # The user has seen the crash-loop banner and is choosing to
            # send this coall value for real. Cleared here, before the plan
            # is built below, so a plan this same call produces is not
            # immediately overridden back to stock by the enforcer's next
            # 60-second pass.
            config_mod.clear_safety_trip(self.window.config)
            config_mod.save_config(self.window.config)
        values = self._pending_values()
        # What every control held at the moment Apply was pressed. The
        # controls stay live while the write runs, so recording their current
        # position afterwards would mark a change made mid-apply as already
        # on the hardware.
        snapshot = {key: self._current(key) for key in self.rows}
        # The untick itself is an action, not just the absence of one:
        # ryzenadj has no reset, so the limits this app last wrote go on
        # running until something puts the firmware's own back. This is the
        # one moment that can be known to be that transition -- the box was
        # ticked when the last apply finished and is not now -- so it is the
        # one place the firmware reset is asked for. Re-applying with the box
        # already off does not repeat it: the limits are the firmware's
        # already, and each reset costs the fan curves a re-push.
        if not values[LIMITS_KEY] and self._limits_toggle_moved(snapshot):
            values["reset_to_firmware"] = True
        plan = hardware.cpu_apply_plan(values, self.caps)
        # And which profile they belong to, for the same reason: the write
        # runs off the main loop, and the enforcer switches profile on
        # AC/battery without asking. Resolving the profile when the write
        # finishes would save these limits into whichever one is current
        # then. See config.deferred_save_target.
        target = self.window.current_profile_name()
        if not plan:
            # Unticking the checkbox can empty the plan outright, on a
            # machine whose only CPU control is ryzenadj. There is then
            # nothing to write -- but the checkbox is itself a profile
            # setting the user just changed, and it has to be saved or the
            # limits come straight back on the next apply. No worker thread:
            # this path touches the config and nothing else.
            if self._limits_toggle_moved(snapshot):
                refused = self._save(target, values, snapshot, [])
                self._update_banner()
                self.window.toast(
                    refused or f"CPU settings saved to {target}.")
            else:
                self.window.toast("Nothing on this page can be set on this "
                                  "machine.")
            return
        # After the empty-plan path above, which writes nothing at all: a
        # save that touches only the config has no reason to wait for
        # whatever else is on the hardware.
        if not self.window.claim_hardware("writing the CPU settings"):
            return
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
            self.window.release_hardware()
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

        # Only when something reached the chip -- or when the checkbox
        # moved, which _save decides for itself: an apply the chip refused
        # outright has nothing to save, and saying "written to the hardware
        # but not saved" about it would be untrue in both halves.
        refused = self._save(target, values, snapshot, applied_steps)
        # Everything a failed step owns goes back to the last value the
        # hardware accepted, so no control is left claiming a setting the
        # chip refused.
        failed_rows = [key for step, ok, _ in results if not ok
                       for key in self.step_rows[step]]
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

        # Released only here, after everything above has finished reading the
        # target and snapshot captured when Apply was pressed -- releasing
        # earlier lets a deferred reload_pages repoint the rows underneath
        # this callback. And the firmware-reset re-apply below needs the
        # machine free to claim it.
        self.window.release_hardware()
        if "fwreset" in applied_steps:
            self._reapply_profile_after_firmware_reset()

    def _reapply_profile_after_firmware_reset(self):
        """Put the machine back to the profile after a firmware reset.

        The firmware reselecting its power table is not a quiet write. The
        EC drops the custom fan curves when its thermal policy is written,
        exactly as it does on an ordinary power-mode change, so leaving it
        there would trade "the limits are the firmware's again" for fans
        running the firmware's curve until something noticed -- up to five
        minutes, which is how long the enforcer's re-verify takes.

        So the whole profile is re-applied, rather than the fan curves being
        re-pushed from here. The window's apply is the one piece of code
        that knows the order this needs -- power mode first, fan curves last
        -- and that reads the curves back from the driver to write only the
        channels the EC actually threw away. A second copy of that here is
        how three of the four CPU applies in this tree came to silently drop
        settings.

        The profile applied is whichever is current NOW, not the one Apply
        was pressed on: the enforcer switches profile on AC/battery without
        asking, and the machine has to end up matching what it is running."""
        name = self.window.current_profile_name()
        if not name:
            return
        self.window.apply_profile_async(name)

    def _save(self, target, values, snapshot, steps):
        """Write what reached the hardware into profile ``target``.

        ``target`` is the profile that was active when Apply was pressed,
        not whichever one is active now. Returns None when the save
        happened, or the sentence to show when it was refused.

        Only the steps that took are written: a profile holding a limit the
        chip refused is a profile that silently disagrees with the machine,
        and the next window to open would show it as fact.

        The checkbox is the one exception, because it is not a step. It
        says whether the limits step should run at all, so an apply that
        turned it off has no "limits" step to hang it on and would never
        save it -- and the box would come back ticked on the next reload,
        with the limits applied again. It is written whenever it moved,
        whether or not anything reached the chip; nothing is claimed of the
        hardware by storing a preference about what to write to it.

        Saves nothing, and asks the config nothing, when neither applies:
        an apply with nothing to record must not be able to produce a
        "the profile moved" refusal as the only thing the user hears."""
        data = {}
        for step in steps:
            for key in self.step_saves.get(step, ()):
                data[key] = values[key]
        toggle_moved = self._limits_toggle_moved(snapshot)
        if toggle_moved:
            data[LIMITS_KEY] = bool(snapshot[LIMITS_KEY])
        if not steps and not toggle_moved:
            return None
        refused = config_mod.save_deferred(
            self.window.config, target, "cpu", data, "CPU settings")
        if refused is not None:
            # ``_applied`` is deliberately left alone too. reload() has
            # already reset it from the profile that is current now, and
            # marking these values as applied on top of that would have the
            # banner claim the new profile is running settings that belong
            # to the old one.
            return refused
        if toggle_moved:
            self._applied[LIMITS_KEY] = bool(snapshot[LIMITS_KEY])
        for step in steps:
            for key in self.step_rows[step]:
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
                if key in BOOL_ROWS:
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
