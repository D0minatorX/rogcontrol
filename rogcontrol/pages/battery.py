"""Battery: the charge limit, and which profile each power source gets.

The charge limit is the one setting on any page that is deliberately *not*
part of a profile. It is a property of the battery, not of how hard the
machine is being driven, and a user who caps charging at 80% to preserve the
cell does not want that undone by switching to Performance. So it is stored
at the top level of the config and applied whichever profile is active --
exactly as the GTK3 app had it.

The two profile pickers moved here from the window header. They belong with
the battery rather than beside the profile switcher: what they configure is
what happens when the power source changes, which is battery behaviour, and
in the header they read as two more ways to change the profile right now,
which is not what they do.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from ..widgets.slider_row import SliderRow  # noqa: E402
from ..widgets.stat_row import StatCell, build_stat_row  # noqa: E402

DEBOUNCE_MS = 400
COALESCE_MS = 20
# Slower than the other pages' two seconds: a percentage moves over minutes,
# and this is the only page whose readings cannot change faster than that.
REFRESH_SECONDS = 5
DASH = "—"

CHARGE_TOOLTIP = (
    "Caps charging at this percentage, which preserves the cell over the "
    "years a laptop spends plugged in.\n\n"
    "Independent of profiles — it applies whichever one is active. 100% is "
    "no cap at all."
)

AUTO_SWITCH_DESCRIPTION = (
    "Which profile to move to when the power source changes.")

AUTO_SWITCH_TOOLTIP = (
    "The switch happens on plug and unplug, not now — choosing one here does "
    "not change the profile you are running."
)

HEALTH_TOOLTIP = (
    "How much of the battery's original capacity it can still hold: the "
    "charge it fills to now, against what it was built to hold.\n\n"
    "Above 100% is not a bug. A pack that has not been through a full "
    "cycle yet reports a learned full charge slightly over its design "
    "figure, and this shows the firmware's number rather than rounding it "
    "down to a tidier one.\n\n"
    "It moves over months, not minutes — a drop of a percent between two "
    "glances at this page is the firmware re-learning, not wear."
)

CAPACITY_TOOLTIP = (
    "What the pack charges to now, against its design capacity. The pair "
    "the health percentage is computed from, in the unit the battery is "
    "sold in."
)


def format_capacity(full, design, unit):
    """``"83.0 / 90.0 Wh"`` — what it holds now, against what it was built to.

    Both halves or neither, and one decimal for both, for the same reason
    the Overview's memory row does it: the number only means anything read
    against the one beside it, and two figures at the same precision can be
    compared by eye where "83047 / 90001" cannot."""
    if full is None or design is None:
        return DASH
    return f"{full:.1f} / {design:.1f} {unit}"


class BatteryPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.caps = window.caps
        self._loading = True
        self._timer = None
        self._busy = False
        self._sampling = False
        self._timer_id = None
        self._applied = None
        self._pending_label = None

        self._build()
        self.reload()
        self._loading = False
        self._refresh_now()
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        status = Adw.PreferencesGroup(title="Battery")
        self.add(status)
        self.charge_row = Adw.ActionRow(title="Charge")
        self.charge_value = Gtk.Label(label=DASH)
        # Tabular figures, so a percentage crossing 100 -> 99 does not shift
        # the column.
        self.charge_value.add_css_class("numeric")
        self.charge_value.add_css_class("dim-label")
        self.charge_row.add_suffix(self.charge_value)
        status.add(self.charge_row)

        # Health sits with Charge rather than on the Overview: Overview is a
        # 2-second live readout and these three figures move over months, so
        # a tile there would poll three sysfs files 30 times a minute to
        # redraw the same number. It is a StatCell row rather than three more
        # ActionRows because that is what this widget is for -- short
        # readings that belong side by side -- and stacking them would spend
        # three full rows on a page whose point is the charge limit.
        self.health_cell = StatCell("Health", HEALTH_TOOLTIP)
        self.capacity_cell = StatCell("Full charge", CAPACITY_TOOLTIP)
        self.health_row = build_stat_row(
            status, (self.health_cell, self.capacity_cell))
        # Hidden until a sample proves this machine reports wear at all. A
        # battery whose driver exposes neither the charge_* nor the energy_*
        # design pair -- and a desktop with no battery -- must show nothing
        # here, not a row of dashes that reads as a failed read.
        self.health_row.set_visible(False)

        self.limit_group = limit = Adw.PreferencesGroup(title="Charging")
        self.add(limit)
        self.limit_row = SliderRow(
            title="Charge limit", subtitle="100% is no cap at all",
            tooltip=CHARGE_TOOLTIP,
            minimum=0, maximum=100, step=1, unit="%", settle_ms=DEBOUNCE_MS)
        self.limit_row.connect("changed", self._on_limit_changed)
        limit.add(self.limit_row)
        if not self.caps.get("charge_limit"):
            # The only row "Charging" has -- nothing left in the group.
            self.limit_group.set_visible(False)

        switching = Adw.PreferencesGroup(title="Automatic profile switching",
                                         description=AUTO_SWITCH_DESCRIPTION)
        switching.set_tooltip_text(
            AUTO_SWITCH_DESCRIPTION + " " + AUTO_SWITCH_TOOLTIP)
        self.add(switching)
        self.combos = {}
        # No subtitles: "On AC power" under "Automatic profile switching" is
        # already the whole sentence, and the caveat that matters -- this does
        # not switch anything now -- is on hover.
        #
        # "On Type-C charger" is a refinement of "On AC power", not a third
        # power source: read_power_source (hardware.py) reports "usb" as one
        # of two AC *kinds*, not a state alongside AC/battery. Left at
        # "Don't auto-switch" -- its default -- a USB-C connect still falls
        # through to whatever "On AC power" names, so this row only matters
        # to someone who wants the barrel jack and a USB-C PD charger to land
        # on different profiles.
        for source, title in (("ac", "On AC power"),
                              ("battery", "On battery"),
                              ("usbc", "On Type-C charger")):
            row = Adw.ComboRow(title=title)
            row.set_tooltip_text(AUTO_SWITCH_TOOLTIP)
            row.set_model(Gtk.StringList.new(
                config_mod.auto_switch_choices(self.window.config)))
            row.connect("notify::selected", self._on_auto_switch_changed,
                        source)
            switching.add(row)
            self.combos[source] = row


    # -- loading -------------------------------------------------------------

    def reload(self):
        """Put the config's values on screen without applying anything.

        The profile lists are rebuilt here rather than only at construction:
        a profile can be added or renamed elsewhere, and a picker offering a
        name that no longer exists would store a target nothing can switch
        to."""
        was_loading = self._loading
        self._loading = True
        try:
            config = self.window.config
            limit = config.get("charge_limit", 100)
            self.limit_row.set_value(limit)
            self._applied = self.limit_row.get_value()

            choices = config_mod.auto_switch_choices(config)
            for source, row in self.combos.items():
                key = config_mod.AUTO_SWITCH_KEYS[source]
                row.set_model(Gtk.StringList.new(choices))
                row.set_selected(config_mod.auto_switch_selected(config, key))
        finally:
            self._loading = was_loading

    # -- live readout --------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self):
        # The stack unmaps the pages nobody is looking at, and a window
        # started with --minimized is unmapped entirely.
        if self.get_mapped():
            self._refresh_now()
        return GLib.SOURCE_CONTINUE

    def _refresh_now(self):
        """Sample regardless of whether the page is on screen.

        Used at construction, so the first paint has real numbers on it
        rather than dashes for up to five seconds, and straight after an
        apply, where waiting for the next tick would leave a stale reading
        under a toast that has just said it changed."""
        if not self._sampling:
            self._sampling = True
            self.window.apply_async(self._sample, self._on_sample)

    @staticmethod
    def _sample():
        """Worker thread -- no widgets in here."""
        percent, charging = hardware.read_battery()
        return {"percent": percent, "charging": charging,
                "ac": hardware.is_ac_connected(),
                "limit": hardware.read_charge_limit(),
                # Re-read every tick rather than once at construction: the
                # firmware re-learns full-charge capacity after a deep
                # cycle, and three small sysfs reads every five seconds is
                # nothing next to the reads already in this sample.
                "health": hardware.read_battery_health()}

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
        self._render_health(data.get("health"))
        percent = data.get("percent")
        self.charge_value.set_text(
            DASH if percent is None else f"{percent}%")
        if percent is None:
            self.charge_row.set_subtitle("No battery found")
            return
        # Three states, not two. "Not charging" is what a charge-limited ASUS
        # reports while sitting on mains at its threshold, and calling that
        # "on battery" would have the row contradict the plug.
        if data.get("charging"):
            state = "charging"
        elif data.get("ac"):
            state = "on mains, not charging"
        else:
            state = "on battery"
        limit = data.get("limit")
        if limit is not None and limit < 100:
            state += f" — firmware is holding a {limit}% limit"
        self.charge_row.set_subtitle(state)

    def _render_health(self, health):
        """Draw the wear row, or hide it on hardware that cannot say.

        One decimal on the percentage, not none. Wear is the one reading on
        this page that moves slowly enough for the fraction to be the whole
        story: rounded to a whole number this row would sit on "92%" for a
        month and tell a user nothing about which way it was going.

        Whatever the firmware says, including above 100 -- see
        HEALTH_TOOLTIP, which is where that is explained, and
        hardware.battery_health, which is where it is deliberately not
        clamped."""
        if not health:
            self.health_row.set_visible(False)
            return
        self.health_row.set_visible(True)
        self.health_cell.value.set_text(f"{health['percent']:.1f}%")
        self.capacity_cell.value.set_text(format_capacity(
            health.get("full"), health.get("design"), health.get("unit", "")))

    # -- charge limit --------------------------------------------------------

    def _on_limit_changed(self, row, _value):
        if self._loading:
            return
        self._pending_label = f"Charge limit set to {row.get_display_value()}"
        if self._timer is not None:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(COALESCE_MS, self._fire)

    def _fire(self):
        self._timer = None
        if self._busy:
            self._timer = GLib.timeout_add(DEBOUNCE_MS, self._fire)
            return GLib.SOURCE_REMOVE
        if not self.caps.get("charge_limit"):
            self.window.toast("This battery has no charge limit threshold")
            return GLib.SOURCE_REMOVE
        label = self._pending_label or "Charge limit"
        percent = int(self.limit_row.get_value())
        self._busy = True
        self.window.apply_async(
            lambda: hardware.run_helper("charge", percent),
            lambda result, error: self._finish(label, percent, result, error))
        return GLib.SOURCE_REMOVE

    def _finish(self, label, percent, result, error):
        self._busy = False
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            # Top level, not inside the profile: see the module docstring.
            self.window.config["charge_limit"] = percent
            config_mod.save_config(self.window.config)
            self._applied = self.limit_row.get_value()
            self.window.toast(f"{label}.")
            # The firmware clamps and reports back; show what it took rather
            # than waiting five seconds to contradict the toast.
            self._refresh_now()
        else:
            self.window.toast(f"{label} failed: {message}")
            if self._applied is not None:
                self._loading = True
                try:
                    self.limit_row.set_value(self._applied)
                finally:
                    self._loading = False

    # -- automatic switching -------------------------------------------------

    def _on_auto_switch_changed(self, row, _param, source):
        if self._loading:
            return
        item = row.get_selected_item()
        if item is None:
            return
        key = config_mod.AUTO_SWITCH_KEYS[source]
        value = config_mod.auto_switch_value(item.get_string())
        if self.window.config.get(key) == value:
            return
        # Stored as null for the no-op choice, which is what every reader
        # treats as "leave the profile alone on this power source".
        self.window.config[key] = value
        config_mod.save_config(self.window.config)
        where = {"ac": "AC power", "battery": "battery",
                 "usbc": "Type-C charger"}[source]
        self.window.toast(
            f"No automatic switch on {where}" if value is None
            else f"On {where}: {value}")

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """One synchronous read-and-render plus a reload. No writes."""
        self.reload()
        self._render(self._sample())
