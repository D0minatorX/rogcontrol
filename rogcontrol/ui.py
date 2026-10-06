"""Application-owned icons and appearance preferences."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

ICON_DIR = Path(__file__).resolve().parent / "icons"
APPEARANCES = ("system", "light", "dark")
APPEARANCE_LABELS = ("System", "Light", "Dark")

_appearance_provider = None
_appearance_display = None
_widget_provider = None


def _appearance_css(dark):
    # Explicit choices must also win over ~/.config/gtk-4.0/gtk.css,
    # which can hard-code dark colors above libadwaita's own stylesheet.
    bg, fg, view, sidebar, card, border = (
        ("#000000", "#f4f4f5", "#09090b", "#08080a", "#151517", "rgba(255,255,255,0.09)")
        if dark else
        ("#f6f7f9", "#202126", "#ffffff", "#eef0f4", "#ffffff", "rgba(25,30,40,0.09)"))
    colors = {
        "window_bg_color": bg, "window_fg_color": fg,
        "view_bg_color": view, "view_fg_color": fg,
        "headerbar_bg_color": bg, "headerbar_fg_color": fg,
        "headerbar_backdrop_color": bg, "headerbar_border_color": border,
        "sidebar_bg_color": sidebar, "sidebar_fg_color": fg,
        "sidebar_backdrop_color": sidebar, "sidebar_border_color": border,
        "secondary_sidebar_bg_color": sidebar, "secondary_sidebar_fg_color": fg,
        "secondary_sidebar_backdrop_color": sidebar,
        "card_bg_color": card, "card_fg_color": fg,
        "popover_bg_color": card, "popover_fg_color": fg,
        "dialog_bg_color": bg, "dialog_fg_color": fg,
        "shade_color": "rgba(0,0,0,0.12)",
        "accent_bg_color": "#b32638", "accent_fg_color": "#ffffff",
        "accent_color": "#ff8a97" if dark else "#a51d2d",
        "rog_glass_color": "rgba(0,0,0,0.92)" if dark else "rgba(246,247,249,0.92)",
        "rog_panel_color": "rgba(255,255,255,0.045)" if dark else "rgba(255,255,255,0.68)",
        "rog_hover_color": "rgba(255,255,255,0.075)" if dark else "rgba(30,35,45,0.045)",
        "rog_border_color": border,
        "rog_muted_color": "#a3a3ad" if dark else "#646773",
        "rog_sidebar_color": "rgba(8,8,10,0.35)" if dark else "rgba(255,255,255,0.78)",
        "rog_nav_color": "#b5b5bd" if dark else "#454750",
        "rog_selected_color": "rgba(179,38,56,0.16)" if dark else "#f8e8eb",
        "rog_selected_fg": "#ff9aa6" if dark else "#922133",
        "rog_button_color": "#171719" if dark else "#ffffff",
        "rog_button_hover": "#242427" if dark else "#f3f4f6",
        "rog_button_pressed": "#303034" if dark else "#e9ebef",
    }
    css = "\n".join(f"@define-color {name} {value};" for name, value in colors.items())
    # libadwaita 1.6+ uses CSS variables; older supported GTK versions use
    # named colors only and cannot parse custom properties.
    if Gtk.get_minor_version() >= 16:
        css += "\n:root {" + "".join(
            f"--{name.replace('_', '-')}: {value};" for name, value in colors.items()) + "}"
    css += "\n" + (Path(__file__).resolve().parent / "appearance.css").read_text()
    return css.encode("utf-8")


def bundled_icon(name):
    """Use the exact bundled SVG, retaining GTK's symbolic recoloring."""
    return Gio.FileIcon.new(Gio.File.new_for_path(str(ICON_DIR / f"{name}.svg")))


def icon_image(name):
    image = Gtk.Image.new_from_gicon(bundled_icon(name))
    image.set_pixel_size(16)
    return image


def appearance_index(config):
    value = config.get("appearance", "system")
    return APPEARANCES.index(value) if value in APPEARANCES else 0


def apply_appearance(config):
    global _appearance_provider, _appearance_display, _widget_provider
    index = appearance_index(config)
    schemes = (Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT,
               Adw.ColorScheme.FORCE_DARK)
    Adw.StyleManager.get_default().set_color_scheme(
        schemes[index])
    if _appearance_provider is not None:
        Gtk.StyleContext.remove_provider_for_display(
            _appearance_display, _appearance_provider)
        _appearance_provider = None
        _appearance_display = None
    display = Gdk.Display.get_default()
    # Structural widget styles belong to every theme, including System.
    if display is not None and _widget_provider is None:
        _widget_provider = Gtk.CssProvider()
        _widget_provider.load_from_data(
            (Path(__file__).resolve().parent / "widgets.css").read_bytes())
        Gtk.StyleContext.add_provider_for_display(
            display, _widget_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    if index and display is not None:
        provider = Gtk.CssProvider()
        provider.load_from_data(_appearance_css(index == 2))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        _appearance_provider = provider
        _appearance_display = display
