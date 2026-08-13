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

DEBOUNCE_MS = 400
COALESCE_MS = 20
# Slower than the other pages' two seconds: a percentage moves over minutes,
# and this is the only page whose readings cannot change faster than that.
REFRESH_SECONDS = 5
DASH = "—"

CHARGE_SUBTITLE = (
    "Caps charging at this percentage, which preserves the cell over the "
    "years a laptop spends plugged in.\n"
    "Independent of profiles — it applies whichever one is active. 100% is "
    "no cap at all."
)

AUTO_SWITCH_DESCRIPTION = (
    "Which profile to move to when the power source changes. The switch "
    "happens on plug and unplug, not now — choosing one here does not change "
    "the profile you are running."
)


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

        limit = Adw.PreferencesGroup(title="Charging")
        self.add(limit)
        self.limit_row = SliderRow(
            title="Charge limit", subtitle=CHARGE_SUBTITLE,
            minimum=0, maximum=100, step=1, unit="%", settle_ms=DEBOUNCE_MS)
        self.limit_row.connect("changed", self._on_limit_changed)
        limit.add(self.limit_row)
        if not self.caps.get("charge_limit"):
            self.limit_row.set_sensitive(False)
            self.limit_row.set_tooltip_text(
                "Not available on this machine: this battery has no "
                "charge_control_end_threshold")

        switching = Adw.PreferencesGroup(title="Automatic profile switching",
                                         description=AUTO_SWITCH_DESCRIPTION)
        self.add(switching)
        self.combos = {}
        for source, title, subtitle in (
                ("ac", "On AC power", "Profile to switch to when mains power "
                                      "is connected"),
                ("battery", "On battery", "Profile to switch to when the "
                                          "mains is unplugged")):
            row = Adw.ComboRow(title=title, subtitle=subtitle)
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
                "limit": hardware.read_charge_limit()}

    def _on_sample(self, data, error):
        self._sampling = False
        if error is None:
            self._render(data)

    def _render(self, data):
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
        where = "AC power" if source == "ac" else "battery"
        self.window.toast(
            f"No automatic switch on {where}" if value is None
            else f"On {where}: {value}")

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """One synchronous read-and-render plus a reload. No writes."""
        self.reload()
        self._render(self._sample())
