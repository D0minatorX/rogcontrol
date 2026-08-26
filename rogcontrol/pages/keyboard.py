"""Keyboard page: backlight level, effect, colours and speed.

Two things make this page unlike the others.

The first is that its controls are **never disabled by capability
detection**. Everywhere else a missing sysfs node greys the control out; here
it must not, and the comment on that decision is kept verbatim from the GTK3
app because the reasoning is easy to lose. Detection runs once at startup and
the keyboard interfaces are the ones most likely to be absent at that instant
and fine a moment later -- asus-nb-wmi still settling, the USB controller not
yet enumerated. A control greyed out on a bad guess stays greyed out for the
whole session with no way to retry, which is worse than a control that tries
and reports what went wrong.

The second is that two modes, Ambient and Profile Color, are not a single
write the firmware then animates by itself.

Ambient is a screen-capture session and a sampling thread, which means it has
to be started when chosen, stopped when another mode is chosen or the window
goes away, and started again at launch when it is what was saved -- see
``start_ambient`` / ``stop_ambient`` and the application's shutdown hook.

Profile Color is the opposite: it needs nothing running here at all. The
keyboard is repainted by whichever process makes a profile current -- this
window, the tray's apply, the hotkey cycler, the enforcer -- through
``hardware.set_profile_kbd_color``, so the colour follows the profile with
this app closed, which is when most profile switches happen. What this page
owns is the *choice* of mode and the per-profile colours behind it.

The two are exclusive because there is one saved ``mode`` and it holds one of
them: choosing Profile Color stops the sampler on its way past, and every
non-GUI painter asks ``kbdcolor.profile_color_enabled`` before it writes, so
a user on Ambient never has a profile switch painted over their screen
colours.

All the colour arithmetic lives in ``kbdcolor``, which has no GTK in it and
is unit-tested. This file decides *when* a colour is sent, never what it is.
The two colour buttons convert through ``kbdcolor.byte_to_float`` and
``float_to_byte`` in both directions, so a colour loaded from the config and
saved back unchanged is byte-identical rather than drifting a point per
round trip.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .. import config as config_mod  # noqa: E402
from .. import hardware  # noqa: E402
from .. import kbdcolor  # noqa: E402
from ..widgets.ambient import AmbientSampler  # noqa: E402
from ..widgets.color_picker import ColorButton  # noqa: E402
from ..widgets.slider_row import SliderRow  # noqa: E402

# Long enough to swallow a drag across a colour wheel, short enough that the
# keys still feel like they are answering the pointer.
DEBOUNCE_MS = 400
# The tick that re-colours the temperature and battery modes. A keyboard
# write costs a ~270 ms USB round trip, so the tick only writes when the
# mapped colour has actually moved -- see _resample_live.
REFRESH_SECONDS = 2

BRIGHTNESS_LEVELS = ("Off", "Low", "Medium", "Full")
SPEED_LEVELS = {1: "Slow", 2: "Medium", 3: "Fast"}

BRIGHTNESS_TOOLTIP = (
    "The backlight the keys are lit at, which is separate from their colour. "
    "At Off the keyboard is dark whatever effect is chosen below."
)

# Short on the page, the rest on hover. What each effect *does* stays visible
# (see MODE_HINTS): that one changes with the picker and is the answer to
# "why are the keys not the colour I chose".
EFFECT_DESCRIPTION = "Applied through rogauracore."

EFFECT_TOOLTIP = (
    "Applied through rogauracore, which talks to the ASUS Aura controller "
    "directly. Effects the firmware animates itself keep running after this "
    "app exits; Ambient does not, because it needs the screen."
)

COLOUR_TOOLTIP = "The colour the effect is drawn in."

COLOUR2_TOOLTIP = (
    "Breathing fades to this one; Gradient Static blends towards it across "
    "the keyboard's four zones. The other effects ignore it."
)

SPEED_TOOLTIP = "How fast the chosen animation runs."

PROFILE_COLOUR_TITLE = "Profile colours"

PROFILE_COLOUR_DESCRIPTION = (
    "One colour per power profile, used by the Profile Color effect. The "
    "keys are repainted whenever the profile changes — from this window, the "
    "tray, the shortcut key, or automatically on the charger."
)

PROFILE_COLOUR_TOOLTIP = (
    "The colour the keyboard is painted while this profile is active."
)

CHARGER_FLASH_TITLE = "Charger flash"

CHARGER_FLASH_DESCRIPTION = (
    "Blink the keys once when the charger is plugged in or unplugged, then "
    "go straight back to the effect above. Handled by the background "
    "service, so it works with this window closed."
)

CHARGER_FLASH_TOOLTIP = (
    "Both directions: a blink on plug in and a blink on unplug. Unplugging "
    "is the half worth noticing — a cable knocked out of the socket is "
    "otherwise invisible until the battery is low."
)

CHARGER_FLASH_COLOUR_TOOLTIP = (
    "The colour of the blink. It is only ever on the keys for about half a "
    "second, so a bright, saturated colour reads better than a subtle one."
)

# Shown under the switch while the chosen effect is one the flash cannot be
# undone from. Said out loud rather than hiding the switch: a control that
# vanishes when the picker above it moves is harder to understand than one
# that explains itself, and the setting is still worth keeping for when the
# effect changes back.
CHARGER_FLASH_UNRESTORABLE_HINT = (
    "Not used with this effect — its colour is not something the service "
    "can put back afterwards, so the keys are left alone."
)

# What each effect actually does, shown under the picker. Modes whose colour
# is not the one in the button below need saying out loud -- otherwise a
# picker showing pink while the keys glow green reads as a bug.
MODE_HINTS = {
    "Static": "One colour, held.",
    "Breathing": "Fades between the two colours below.",
    "Pulse": "A sharp flash in the chosen colour, not a slow fade.",
    "Color Cycle": "Cycles through every hue by itself — the colour below is "
                   "not used.",
    "Rainbow": "A rainbow sweeping across the keyboard — the colour below is "
               "not used.",
    "Gradient Static": "Blends the two colours below across the keyboard's "
                       "four zones.",
    "GPU Temp Color": "Blue to red as the GPU heats up. Re-coloured every "
                      "few seconds.",
    "CPU Temp Color": "Blue to red as the CPU heats up. Re-coloured every "
                      "few seconds.",
    "Battery Level": "Green to red as the battery empties; blue to green "
                     "while it charges.",
    "Ambient": "Follows what is on the screen. Needs screen-sharing "
               "permission, and stops when this app closes.",
    "Profile Color": "One colour per power profile, repainted on every "
                     "switch — including with this app closed.",
}

NO_BACKLIGHT_HINT = (
    "No asus::kbd_backlight LED was found at startup — brightness may not "
    "work, but the control is left enabled in case it appeared later."
)
NO_ROGAURACORE_HINT = (
    "rogauracore was not found at startup — colours and effects need it. The "
    "controls are left enabled; install rogauracore if they report an error."
)


def _clamp_level(level):
    """A backlight level from anywhere -- the LED class, the config -- as an
    int inside the range the hardware accepts."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return kbdcolor.KBD_MIN
    return max(kbdcolor.KBD_MIN, min(kbdcolor.KBD_MAX, level))


class _LevelRow(SliderRow):
    """A SliderRow whose readout is a word rather than a number.

    "2" means nothing next to a keyboard backlight; "Medium" does. The
    override is on ``format_value`` alone, so the row still sizes its readout
    from the two ends of the range like every other one.
    """

    def __init__(self, labels, **kwargs):
        # Set before the base class builds the row: its constructor sizes the
        # readout by asking format_value for both ends of the range.
        self._labels = labels
        super().__init__(**kwargs)
        # ...and re-sized from the longest word here, because the base class
        # sizes from the two ends and the longest word is in the middle:
        # "Medium" is wider than either "Slow" or "Fast".
        self.set_value_width_chars(max(len(str(v)) for v in labels.values()))

    def format_value(self, value):
        try:
            index = int(round(float(value)))
        except (TypeError, ValueError):
            index = 0
        return self._labels.get(index, str(index))


class KeyboardPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.caps = window.caps
        self._loading = True
        self._timer_id = None
        self._color_timer = None
        self._busy = False
        self._live_busy = False
        # What the LED class last said, filled in by _refresh_brightness off
        # the main loop. None until the first read comes back, when the
        # config's own value stands in. See _current_brightness.
        self._brightness = None
        self._brightness_busy = False
        # The colour a live mode last put on the keys, so the refresh tick can
        # skip the USB write when the reading maps to the same colour again.
        self._last_live_color = None
        self._ambient = None
        # One colour row per profile, keyed by name so reload can tell an
        # added/renamed/deleted profile from an unchanged one.
        self._profile_rows = {}
        # None on a machine with no controllable keyboard colour, where the
        # whole group is withheld rather than built. See _build.
        self.flash_group = None

        self.modes = kbdcolor.supported_modes(self.caps)
        self._build()
        self.reload()
        self._loading = False
        self._timer_id = GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self.connect("destroy", self._on_destroy)

    # -- construction --------------------------------------------------------

    def _build(self):
        backlight = Adw.PreferencesGroup(title="Backlight")
        self.add(backlight)
        self.brightness_row = _LevelRow(
            dict(enumerate(BRIGHTNESS_LEVELS)),
            title="Brightness", tooltip=BRIGHTNESS_TOOLTIP,
            minimum=kbdcolor.KBD_MIN, maximum=kbdcolor.KBD_MAX, step=1,
            settle_ms=DEBOUNCE_MS)
        self.brightness_row.connect("changed", self._on_brightness_changed)
        backlight.add(self.brightness_row)

        lighting = Adw.PreferencesGroup(title="Lighting",
                                        description=EFFECT_DESCRIPTION)
        lighting.set_tooltip_text(EFFECT_TOOLTIP)
        self.add(lighting)

        self.mode_row = Adw.ComboRow(title="Effect")
        self.mode_row.set_tooltip_text(EFFECT_TOOLTIP)
        self.mode_row.set_model(Gtk.StringList.new(
            self.modes or list(kbdcolor.KBD_RGB_MODES)))
        # Connected in reload(), after the saved mode is selected: setting the
        # selection emits the same signal, and handling it here would re-push
        # the keyboard every time the window opened.
        lighting.add(self.mode_row)

        self.color_row, self.color_button = self._color_row(
            lighting, "Colour", COLOUR_TOOLTIP)
        self.color2_row, self.color2_button = self._color_row(
            lighting, "Second colour", COLOUR2_TOOLTIP)

        self.speed_row = _LevelRow(
            SPEED_LEVELS, title="Speed", tooltip=SPEED_TOOLTIP,
            minimum=kbdcolor.SPEED_MIN, maximum=kbdcolor.SPEED_MAX, step=1,
            settle_ms=DEBOUNCE_MS)
        self.speed_row.connect("changed", lambda _row, _v: self._on_edited())
        lighting.add(self.speed_row)

        # Its own group rather than more rows in Lighting: there is one row
        # per profile, so this is the only part of the page whose length is
        # the user's to decide, and it is hidden outright for every other
        # effect.
        self.profile_group = Adw.PreferencesGroup(
            title=PROFILE_COLOUR_TITLE,
            description=PROFILE_COLOUR_DESCRIPTION)
        self.add(self.profile_group)

        self._build_charger_flash()

        # Keyboard controls are deliberately NEVER disabled by capability
        # detection, unlike the other pages. Detection runs once at startup,
        # and the keyboard interfaces are the ones most likely to be absent
        # at that instant but fine moments later -- asus-nb-wmi may still be
        # settling, or the USB controller may not have enumerated yet. A
        # control greyed out on a bad guess stays greyed out for the whole
        # session with no way to retry, which is worse than a control that
        # tries and reports what went wrong.
        #
        # So they stay live and any problem is reported when used. Missing
        # pieces are only hinted at here.
        if not self.caps.get("kbd_backlight"):
            backlight.set_description(NO_BACKLIGHT_HINT)
        if not self.caps.get("rogauracore"):
            lighting.set_description(
                f"{EFFECT_DESCRIPTION}\n\n{NO_ROGAURACORE_HINT}")

    def _color_row(self, group, title, tooltip):
        """An action row whose control is a colour button.

        The title carries the row; which effects read which button is on
        hover, because the two of them together were four lines of text
        above the picker that decides whether either is used at all."""
        row = Adw.ActionRow(title=title)
        row.set_tooltip_text(tooltip)
        # Not Gtk.ColorDialogButton: the dialog it opens is sized for its
        # palette view and clips the saturation/value plane behind a
        # scrollbar when you choose Custom. See widgets/color_picker.py.
        button = ColorButton(title=title)
        button.set_valign(Gtk.Align.CENTER)
        button.connect("color-set", self._on_color_changed)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        group.add(row)
        return row, button

    # -- loading -------------------------------------------------------------

    def reload(self):
        """Put the config's values on screen without applying anything."""
        was_loading = self._loading
        self._loading = True
        try:
            saved = self.window.config.get("kbd_rgb") or {}
            self.brightness_row.set_value(self._current_brightness())
            self._select_mode(saved.get("mode") or "Static")
            self._set_button(self.color_button,
                             kbdcolor.saved_color(saved))
            self._set_button(self.color2_button,
                             kbdcolor.saved_color(
                                 saved, "2", kbdcolor.DEFAULT_COLOR2))
            self.speed_row.set_value(kbdcolor.clamp_speed(saved.get("speed")))
            self._rebuild_profile_rows()
            self._reload_charger_flash()
            self._sync_visibility()
        finally:
            self._loading = was_loading
        if self.mode_row.get_selected() >= 0:
            # Safe to connect now: nothing below re-selects during a reload.
            self._connect_mode_once()

    def _connect_mode_once(self):
        if getattr(self, "_mode_connected", False):
            return
        self._mode_connected = True
        self.mode_row.connect("notify::selected", self._on_mode_changed)

    def _current_brightness(self):
        """The level the LED class last reported, or the config's.

        No hardware read of its own. Every caller is on the GTK main loop --
        reload, a failed write's toast, the periodic tick -- and this used to
        run read_kbd_brightness (and a config save) inline on all three,
        which is the one thing app.py's apply_async exists to stop. The read
        happens in _refresh_brightness now and leaves its answer here."""
        level = self._brightness
        if level is None:
            level = self.window.config.get("kbd_brightness", kbdcolor.KBD_MIN)
        return _clamp_level(level)

    def _refresh_brightness(self):
        """Ask the LED class what it is holding, off the main loop.

        Read back rather than trusted, because the level can be changed from
        outside this app -- the keyboard's own Fn keys, the desktop's quick
        settings, this project's own shortcut script -- and a slider set once
        at startup would show a stale value for the rest of the session."""
        if self._brightness_busy:
            return
        self._brightness_busy = True
        self.window.apply_async(hardware.read_kbd_brightness,
                                self._brightness_refreshed)

    def _brightness_refreshed(self, level, error):
        self._brightness_busy = False
        if error is not None or level is None:
            return
        self._brightness = _clamp_level(level)
        # Keep the config honest with whatever the LED is actually holding.
        # Without this, a Fn-key change is only ever shown on screen -- the
        # saved kbd_brightness stays at its old value, and the login-time
        # apply service reasserts that stale value and undoes the Fn-key
        # change the next time the user logs in.
        if self._brightness != self.window.config.get("kbd_brightness"):
            self.window.config["kbd_brightness"] = self._brightness
            config_mod.save_config(self.window.config)
        self._loading = True
        try:
            self.brightness_row.set_value(self._brightness)
        finally:
            self._loading = False

    def _select_mode(self, name):
        """Select a mode by name, falling back to the first entry.

        A mode this build does not know about -- an older config, or one from
        a newer build -- would otherwise leave the picker showing nothing."""
        model = self.mode_row.get_model()
        for index in range(model.get_n_items()):
            if model.get_string(index) == name:
                self.mode_row.set_selected(index)
                return
        self.mode_row.set_selected(0)

    def current_mode(self):
        item = self.mode_row.get_selected_item()
        return item.get_string() if item is not None else "Static"

    @staticmethod
    def _set_button(button, color):
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue = (kbdcolor.byte_to_float(c)
                                           for c in color)
        rgba.alpha = 1.0
        button.set_rgba(rgba)

    @staticmethod
    def _button_color(button):
        rgba = button.get_rgba()
        return tuple(kbdcolor.float_to_byte(c)
                     for c in (rgba.red, rgba.green, rgba.blue))

    def _sync_visibility(self):
        """Show only the controls the chosen effect actually reads.

        A picker whose colour does nothing is worse than no picker: it invites
        the user to set a colour and then blames the hardware when the keys
        ignore it."""
        mode = self.current_mode()
        self.color_row.set_visible(mode in kbdcolor.COLOUR_MODES)
        self.color2_row.set_visible(mode in kbdcolor.SECOND_COLOUR_MODES)
        self.speed_row.set_visible(mode in kbdcolor.SPEED_MODES)
        self.profile_group.set_visible(mode == kbdcolor.PROFILE_COLOR_MODE)
        self.mode_row.set_subtitle(MODE_HINTS.get(mode, ""))
        if self.flash_group is not None:
            # Shown, not hidden: see the note above _build_charger_flash.
            self.flash_row.set_subtitle(
                CHARGER_FLASH_UNRESTORABLE_HINT
                if mode in kbdcolor.FLASH_UNRESTORABLE_MODES else "")

    # -- brightness ----------------------------------------------------------

    def _on_brightness_changed(self, row, _value):
        if self._loading:
            return
        level = int(round(row.get_value()))
        label = row.get_display_value()
        self.window.apply_async(
            lambda: hardware.run_helper("kbd", level),
            lambda result, error: self._brightness_done(
                level, label, result, error))

    def _brightness_done(self, level, label, result, error):
        ok, message = (False, str(error)) if error is not None else result
        if ok:
            self.window.config["kbd_brightness"] = level
            config_mod.save_config(self.window.config)
            self.window.toast(f"Backlight: {label}")
        else:
            self.window.toast(f"Backlight failed: {message}")
            self._loading = True
            try:
                self.brightness_row.set_value(self._current_brightness())
            finally:
                self._loading = False

    # -- effect and colour ---------------------------------------------------

    def _on_mode_changed(self, _row, _param):
        if self._loading:
            return
        self._sync_visibility()
        # A mode change is a deliberate act, so it goes straight through
        # rather than waiting out the colour debounce -- and it absorbs any
        # edit still waiting in that debounce, which would otherwise land a
        # second identical write on the keyboard a moment later.
        if self._color_timer is not None:
            GLib.source_remove(self._color_timer)
            self._color_timer = None
        self._apply()

    def _on_color_changed(self, _button):
        self._on_edited()

    def _on_edited(self):
        """A colour or the speed moved: apply once the user has stopped."""
        if self._loading:
            return
        if self._color_timer is not None:
            GLib.source_remove(self._color_timer)
        self._color_timer = GLib.timeout_add(DEBOUNCE_MS, self._fire)

    def _fire(self):
        self._color_timer = None
        self._apply()
        return GLib.SOURCE_REMOVE

    def _apply(self):
        mode = self.current_mode()
        color1 = self._button_color(self.color_button)
        color2 = self._button_color(self.color2_button)
        speed = int(round(self.speed_row.get_value()))

        if mode == "Ambient":
            self.start_ambient()
            # Saved immediately: the first frame can be a second away, and the
            # mode should survive a restart even if the portal is declined.
            self._save(mode, color1, color2, speed)
            return

        # Ambient is the one mode that keeps running: every other mode is a
        # single write the firmware then animates by itself, so leaving the
        # screen capture alive after switching away would hold a capture
        # session open for nothing.
        self.stop_ambient()

        if self._busy:
            # rogauracore serialises on the USB device anyway; queueing a
            # second write behind the first only makes the keys lag the
            # pointer. The debounce means the last edit always gets through.
            self._on_edited()
            return
        self._busy = True
        self.window.apply_async(
            lambda: self._push(mode, color1, color2, speed),
            lambda result, error: self._applied(
                mode, color1, color2, speed, result, error))

    def _push(self, mode, color1, color2, speed):
        """Worker thread: work out the arguments and make the one call."""
        args = kbdcolor.helper_args(mode, color1, color2, speed)
        color = None
        if args is None and mode == kbdcolor.PROFILE_COLOR_MODE:
            # Not a live reading: the colour is whatever the active profile
            # is wearing, and it only moves when the profile does. Resolved
            # through the same kbdcolor call every non-GUI painter uses, so
            # the window and the enforcer cannot disagree about the colour.
            args = kbdcolor.static_args(
                kbdcolor.profile_color(self.window.config))
        elif args is None:
            # A mode whose colour comes from a live reading rather than from
            # a picker. Ambient never reaches here -- it returned above.
            color, reason = self._live_color(mode)
            if color is None:
                return {"ok": False, "message": reason, "color": None}
            args = kbdcolor.static_args(color)
        ok, message = hardware.run_helper(*args)
        return {"ok": ok, "message": message, "color": color}

    @staticmethod
    def _live_color(mode):
        """``(colour, reason)`` for the modes driven by a reading."""
        if mode == "Battery Level":
            percent, charging = hardware.read_battery()
            if percent is None:
                return None, "no battery found on this machine"
            return kbdcolor.battery_to_rgb(percent, charging), None
        if mode == "CPU Temp Color":
            temp = hardware.read_cpu_temp()
        elif mode == "GPU Temp Color":
            temp = hardware.read_nvidia_stats()[0]
        else:
            return None, f"{mode} has no reading to colour from"
        if temp is None:
            return None, "no temperature reading yet"
        return kbdcolor.temp_to_rgb(temp), None

    def _applied(self, mode, color1, color2, speed, result, error):
        self._busy = False
        if error is not None:
            self.window.toast(f"{mode} failed: {error}")
            return
        if not result["ok"]:
            self.window.toast(f"{mode} failed: {result['message']}")
            return
        self._last_live_color = result["color"]
        self._save(mode, color1, color2, speed)
        # The colour is real but invisible while the backlight is off, which
        # otherwise looks exactly like the write having failed.
        dark = self._current_brightness() == kbdcolor.KBD_MIN
        self.window.toast(f"Keyboard: {mode}"
                          + (" (backlight is off)" if dark else ""))

    def _save(self, mode, color1, color2, speed):
        self.window.config["kbd_rgb"] = kbdcolor.merge_kbd_rgb(
            self.window.config.get("kbd_rgb"), mode, color1, color2, speed)
        config_mod.save_config(self.window.config)

    # -- charger flash -------------------------------------------------------
    #
    # The one control on this page that is withheld by capability detection,
    # and the exception is the same one supported_modes already makes for
    # Profile Color: what this switch turns on is read and acted on by the
    # enforcer, which has no window to report a failure into. Everything else
    # here stays enabled on the doctrine at the top of the file -- try it and
    # say what went wrong -- because there is somebody watching when it does.
    # A switch that silently arms a blink on a machine with no Aura
    # controller has nobody watching, and no way to find out.
    #
    # Nothing here writes to the keyboard. The colour button sets what a
    # future plug event will blink, not what the keys are doing now, and
    # previewing it would mean two USB round trips every time the picker was
    # nudged -- on top of fighting whatever effect is currently running.

    def _build_charger_flash(self):
        if not self.caps.get("kbd_rgb", True):
            return
        group = Adw.PreferencesGroup(title=CHARGER_FLASH_TITLE,
                                     description=CHARGER_FLASH_DESCRIPTION)
        group.set_tooltip_text(CHARGER_FLASH_TOOLTIP)
        self.add(group)
        self.flash_group = group

        self.flash_row = Adw.SwitchRow(title="Flash on charger change")
        self.flash_row.set_tooltip_text(CHARGER_FLASH_TOOLTIP)
        self.flash_row.connect("notify::active", self._on_charger_flash_toggled)
        group.add(self.flash_row)

        row = Adw.ActionRow(title="Flash colour")
        row.set_tooltip_text(CHARGER_FLASH_COLOUR_TOOLTIP)
        self.flash_color_button = ColorButton(title="Flash colour")
        self.flash_color_button.set_valign(Gtk.Align.CENTER)
        # Its own handler rather than _on_color_changed: that one debounces
        # into _apply, which would push the *effect* to the keyboard every
        # time this swatch moved.
        self.flash_color_button.connect("color-set",
                                        self._on_flash_color_changed)
        row.add_suffix(self.flash_color_button)
        row.set_activatable_widget(self.flash_color_button)
        group.add(row)

    def _reload_charger_flash(self):
        if self.flash_group is None:
            return
        self.flash_row.set_active(
            kbdcolor.charger_flash_enabled(self.window.config))
        self._set_button(self.flash_color_button,
                         kbdcolor.charger_flash_color(self.window.config))

    def _on_charger_flash_toggled(self, row, _param):
        if self._loading:
            return
        self.window.config[kbdcolor.CHARGER_FLASH_KEY] = row.get_active()
        config_mod.save_config(self.window.config)

    def _on_flash_color_changed(self, button):
        if self._loading:
            return
        self.window.config[kbdcolor.CHARGER_FLASH_COLOR_KEY] = list(
            self._button_color(button))
        config_mod.save_config(self.window.config)

    # -- Profile Color mode --------------------------------------------------
    #
    # The page owns the colours and the choice of mode, and nothing else.
    # The repaint on a profile switch is deliberately NOT here: see
    # hardware.set_profile_kbd_color for why it lives on the switch path
    # instead, where the tray, the hotkey cycler and the enforcer can reach
    # it with this window closed.

    def _rebuild_profile_rows(self):
        """One colour row per profile, rebuilt from the config.

        Rebuilt rather than updated in place because profiles are created,
        renamed, deleted and imported while this page exists, and the window
        also follows the config file being written by the enforcer. Matching
        rows to names by hand is the kind of bookkeeping that ends up showing
        a swatch for a profile that was deleted ten minutes ago."""
        for row in self._profile_rows.values():
            self.profile_group.remove(row)
        self._profile_rows = {}
        for name in (self.window.config.get("profiles") or {}):
            row = Adw.ActionRow(title=name)
            row.set_tooltip_text(PROFILE_COLOUR_TOOLTIP)
            button = ColorButton(title=name)
            button.set_valign(Gtk.Align.CENTER)
            # Through kbdcolor rather than straight out of the profile, so a
            # profile that has never been given a colour -- imported, or
            # written before the key existed -- shows the one it would
            # actually be painted in rather than black.
            self._set_button(
                button, kbdcolor.profile_color(self.window.config, name))
            # set_rgba above does not emit, so nothing here looks like the
            # user picking a colour. See widgets/color_picker.py.
            button.connect("color-set", self._on_profile_color_changed, name)
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self.profile_group.add(row)
            self._profile_rows[name] = row

    def _on_profile_color_changed(self, button, name):
        """Store one profile's colour, and repaint only if it is on show.

        Editing the colour of a profile that is not active changes nothing on
        the keyboard, which is correct and is why the row is titled with the
        profile's name: the swatch is what that profile will look like, not
        what the keys look like now."""
        if self._loading:
            return
        profile = (self.window.config.get("profiles") or {}).get(name)
        if not isinstance(profile, dict):
            return
        profile[kbdcolor.PROFILE_COLOR_KEY] = list(self._button_color(button))
        config_mod.save_config(self.window.config)
        if (name == self.window.config.get("current_profile")
                and self.current_mode() == kbdcolor.PROFILE_COLOR_MODE):
            # Through the same debounce as the other colour buttons: a
            # keyboard write is a ~270 ms USB round trip, and the picker can
            # be reopened twice in a second.
            self._on_edited()

    # -- Ambient mode --------------------------------------------------------

    def start_ambient(self):
        if self._ambient is not None:
            return
        if not self.caps.get("kbd_ambient"):
            self.window.toast(
                "Ambient needs the screen-sharing portal and GStreamer's "
                "PipeWire plugin, which this system does not have")
            return
        self._ambient = AmbientSampler(
            self._ambient_colors, self.window.toast,
            restore_token=(self.window.config.get("kbd_rgb") or {}).get(
                "ambient_restore_token"),
            on_token=self._ambient_token)
        self._ambient.start()

    def _ambient_colors(self, zones):
        """Called from the sampler thread: the helper call is safe there, no
        widget would be."""
        if self.caps.get("kbd_rgb_zones"):
            hardware.run_helper("kbdrgb", "multi_static",
                                *[c for zone in zones for c in zone])
        else:
            hardware.run_helper(
                *kbdcolor.static_args(kbdcolor.average_color(zones)))

    def _ambient_token(self, token):
        """Store the portal's restore token, so the next launch does not have
        to ask for screen permission again."""
        saved = self.window.config.setdefault("kbd_rgb", {})
        if saved.get("ambient_restore_token") != token:
            saved["ambient_restore_token"] = token
            GLib.idle_add(self._save_config_idle)

    def _save_config_idle(self):
        config_mod.save_config(self.window.config)
        return GLib.SOURCE_REMOVE

    def stop_ambient(self):
        # The reference is dropped before the teardown starts, which is what
        # makes running it off the main loop safe -- nothing here will look
        # at that sampler again. See AmbientSampler.stop_async.
        sampler, self._ambient = self._ambient, None
        if sampler is not None:
            sampler.stop_async()

    def start_saved_ambient(self):
        """Restart Ambient at launch if it is the saved mode.

        Ambient is the only mode that needs a process behind it; every other
        one is already live in the firmware from when it was applied."""
        saved = self.window.config.get("kbd_rgb") or {}
        if saved.get("mode") == "Ambient" and self.caps.get("kbd_ambient"):
            self.start_ambient()

    # -- live readout --------------------------------------------------------

    def _on_destroy(self, _widget):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if self._color_timer is not None:
            GLib.source_remove(self._color_timer)
            self._color_timer = None
        self.stop_ambient()

    def _tick(self):
        # The brightness readout is only worth refreshing while it is being
        # looked at; the stack unmaps the pages nobody is on.
        if self.get_mapped():
            self._refresh_brightness()
        # The recolour is not gated on that: the keys stay lit whichever page
        # is open, and a temperature mode that only tracked while its own page
        # was visible would be wrong everywhere else.
        self._recolour_live()
        return GLib.SOURCE_CONTINUE

    def _recolour_live(self):
        mode = (self.window.config.get("kbd_rgb") or {}).get("mode")
        if mode not in kbdcolor.LIVE_COLOUR_MODES:
            return
        if self._live_busy or self._busy:
            return
        self._live_busy = True
        self.window.apply_async(lambda: self._resample_live(mode),
                                self._live_done)

    def _resample_live(self, mode):
        """Worker thread: re-read, and write only if the colour has moved.

        Every write is a ~270 ms USB round trip, so an unchanged colour is
        skipped entirely rather than being re-sent on every tick."""
        color, _reason = self._live_color(mode)
        if color is None or color == self._last_live_color:
            return None
        ok, _message = hardware.run_helper(*kbdcolor.static_args(color))
        return color if ok else None

    def _live_done(self, color, error):
        self._live_busy = False
        if error is None and color is not None:
            self._last_live_color = color

    # -- shell hooks ---------------------------------------------------------

    def self_test_tick(self):
        """Reload and re-render. Deliberately writes nothing.

        Every other page's tick is a read; this page's would be a keyboard
        write, and a self test is not allowed to change what the machine is
        doing -- least of all to start a screen capture."""
        self.reload()
        self._sync_visibility()
        for mode in self.modes:
            kbdcolor.helper_args(mode, (1, 2, 3), (4, 5, 6), 2)
