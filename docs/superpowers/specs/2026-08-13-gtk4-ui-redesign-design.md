# ROG Control — GTK4 / libadwaita UI redesign

Date: 2026-08-13
Status: approved design, not yet implemented

## Why

The app works and is about to be published, but its interface is a GTK3
notebook with six tabs, twelve raw sliders, three separate Apply buttons and a
hand-written dark stylesheet. It looks like a settings dump rather than a
control panel, and the tab strip hides where things are. The goal is an
interface that reads as a modern GNOME application and tells you what the
machine is doing, without changing a single thing about how the hardware is
driven.

Verified available on the target machine: GTK 4.22.4, libadwaita 1.9.3,
python-gobject 3.56.3. Every widget this design names was checked to exist
before being specified.

## Goals

- A window that looks and behaves like a current GNOME application.
- An Overview screen that answers "what is my laptop doing right now".
- Settings that take effect without hunting for an Apply button, except where
  the hardware genuinely cannot do that.
- Stop the enforcer, the boot-apply script and the hotkey scripts from each
  carrying their own copy of shared logic.

## Non-goals

- No new hardware features. Nothing about fan curves, power limits, keyboard
  effects or the ambient sampler changes behaviour.
- No change to the config file format.
- No theme switcher. libadwaita follows the system light/dark preference.
- No change to the privileged helper or the sudoers rule.

## Architecture

### Layout

```
~/.local/lib/rogcontrol/
    __main__.py       entry point, argument parsing
    app.py            Adw.Application, window shell, navigation
    config.py         load / migrate / save            [shared]
    hardware.py       helper invocation, sysfs reads,
                      capability detection             [shared]
    fancurve.py       interpolation, pwm/percent, rpm
                      calibration                      [shared]
    profiles.py       stock profiles, PPD mode map     [shared]
    pages/
        overview.py  cpu.py  gpu.py  fans.py
        battery.py   keyboard.py  system.py
    widgets/
        curve_editor.py   the fan curve drawing area
        ambient.py        screen sampler (moved as-is)

~/.local/bin/
    rogcontrol              launcher: python3 -m rogcontrol
    rogcontrol-tray         GTK3 sidecar, tray icon only
    rogcontrol-enforcer.py  imports config/hardware/fancurve/profiles
    rogcontrol-apply.py     imports the same
    rogcontrol-cycle-*.py   imports the same
```

The four shared modules are the point of the split. Today the enforcer
re-implements curve interpolation, the helper call wrapper and the profile-to-
power-mode map, and the hotkey scripts keep their own copies of the keyboard
mode list and the zone-colour maths. That duplication has already produced real
bugs: the apply ordering fix had to be made in four places, and the keyboard
mode list drifted between the app and the cycler twice.

The scripts gain `sys.path.insert(0, "~/.local/lib")` and import what they
need. They stay separate executables so the systemd units and keyboard
shortcuts are unchanged.

### Processes

Two, because they cannot be one. `AyatanaAppIndicator3` and `AppIndicator3`
both link `Gtk-3.0`, and GTK3 and GTK4 cannot be loaded into the same process.

- `rogcontrol` — GTK4 + libadwaita, the window.
- `rogcontrol-tray` — GTK3 + AppIndicator, roughly 150 lines, carrying over
  today's working tray code.

Contract between them, deliberately minimal and file-based, matching what the
app already does:

- The tray reads `~/.config/rogcontrol.json` to build its profile list and show
  which is active, polling on the same 5-second cycle the app already uses.
- Selecting a profile writes `current_profile` atomically, then runs
  `rogcontrol-apply.py`, which is the same code path used at boot. This applies
  the switch immediately rather than waiting up to 60 seconds for the enforcer.
- "Show window" runs `rogcontrol --toggle`, which the existing single-instance
  handling already understands.
- "Quit" terminates both.

## Navigation

`Adw.ApplicationWindow` containing `Adw.NavigationSplitView`:

- Sidebar: Overview, CPU, GPU, Fans, Battery, Keyboard, System. Collapses to a
  hamburger when the window is narrow, which is what finally makes the window
  freely resizable — the current version had to fight its own minimum width
  with wrapping flow boxes and ellipsized combos.
- Content: `Adw.ToolbarView` with an `Adw.HeaderBar`. The header carries the
  profile switcher and a menu button (New profile, Delete, Import, Export,
  About).
- An `Adw.ToastOverlay` wraps the content for confirmations and errors.

## Pages

Every page is an `Adw.PreferencesPage` of `Adw.PreferencesGroup` cards. All
controls stay visible — no "advanced" hiding — with dangerous ones carrying an
explanatory subtitle rather than being tucked away.

### Overview (new)

The screen worth leaving open. Live values, refreshed on the existing
2-second timer:

- CPU: temperature, peak core clock, package power.
- GPU: temperature, power draw.
- Fans: all three, each shown next to what the active curve asks for at the
  current temperature. This makes the gap between request and reality visible,
  which is the single most useful thing learned while debugging this machine.
- Battery: charge and the configured limit.
- Two status rows that catch the failure modes seen in practice: whether the
  EC currently holds the custom curve (`pwmN_enable`), and whether the OS power
  mode matches the active profile.

### CPU

- *Power limits*: STAPM, Fast, Slow, temperature target.
- *Tuning*: Curve Optimizer (subtitle stating plainly that too negative a value
  causes freezes under load — this happened during development), turbo boost as
  a switch row, maximum clock with the value that means "no limit" named in the
  subtitle.

### GPU

Power limit, core and memory clock offsets, clock ceiling, NVIDIA Dynamic
Boost, temperature target, force-maximum switch.

### Fans

Three curve editors, an Apply button, and the RPM calibration action.

### Battery

Charge limit, plus the AC and battery profile pickers. These currently sit in
the window header, where they are unrelated to everything around them.

### Keyboard

Brightness, mode, colours, speed. The nine RGB spin buttons become two
`Gtk.ColorDialogButton`s. Ambient keeps its current behaviour and its note
about screen-sharing permission.

### System

GPU mode (supergfxctl), power-mode sync state, log view, about.

## Apply model

Hybrid, because the hardware forces it:

- Cheap settings apply themselves 400 ms after the control stops moving, on a
  worker thread, confirmed by a toast naming what changed. A failure toasts the
  error and returns the widget to its previous value.
- Fan curves keep an explicit **Apply fan curves** button with a progress
  indicator. A full push takes about 16 seconds because the embedded controller
  silently drops curve writes fired less than 8 seconds apart across channels;
  applying on every drag would mean the fans were perpetually mid-apply.

## Fan curve editor

Ported from `Gtk.DrawingArea` + `draw` signal to `set_draw_func`, with
`Gtk.GestureDrag` for moving points and `Gtk.GestureClick` for selection.
Keeps six points, the RPM axis, and per-machine calibration. Gains keyboard
nudging with the arrow keys, which the current editor does not have.

## Compatibility

- Config format unchanged. Existing profiles, curves, calibration and the
  ambient permission token load as they are.
- `install.sh` gains a dependency check that fails with the package names for
  Arch, Fedora and Debian when GTK4 or libadwaita is missing, installs the
  package directory to `~/.local/lib/rogcontrol`, installs the launcher and the
  tray sidecar, and removes the old GTK3 `rogcontrol.py`.
- `uninstall.sh` removes the new locations.
- Capability gating is unchanged, including the deliberate decision to leave
  keyboard controls enabled even when detection fails, because detection runs
  once at startup and the USB controller may not have enumerated yet.

## Testing

The bugs that actually occurred in this codebase were in pure logic, not in
widgets, so that is where the tests go:

- `config.py`: migration leaves user values untouched; a fresh config produces
  the stock profiles; running twice changes nothing.
- `fancurve.py`: interpolation preserves the user's own points, fills by
  bisecting the widest gap, and always returns exactly eight; percent-to-pwm
  rounding; rpm calibration.
- `profiles.py`: the profile-to-power-mode map resolves a shared mode to a
  single profile deterministically.
- `keyboard`: zone colour maths for gradients, temperature and battery colour
  mapping, ambient brightness boost preserving hue.

Plus a `--self-test` flag that constructs every page and exits non-zero on
exception, and a short manual checklist for the paths that touch hardware:
profile switch, fan apply, keyboard mode, ambient permission, tray switch.

## Risks

- This is a rewrite of roughly 2,000 lines of interface code. It will not be
  finished in one sitting.
- The curve editor is the fiddliest part; gesture handling differs
  substantially from GTK3's event boxes.
- libadwaita is standard on GNOME systems but an extra install on KDE or XFCE.
  The dependency check must therefore be explicit and helpful rather than a
  traceback.

The current GTK3 application stays installed and working until the replacement
passes its checklist.
