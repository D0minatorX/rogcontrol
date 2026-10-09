# ROG Control Qt Quick UI Design

## Goal

Replace ROG Control's GTK desktop interface with a locally installed Qt Quick
interface designed and branded by ROG Control. Keep the existing Python
hardware and system-control code wherever it is independent of GTK. The desktop
application must no longer require GTK 3 or GTK 4 to install or run.

## User decisions

- The application remains a locally installed Linux desktop app, not a hosted
  website.
- Python remains the application and hardware-control language.
- The desktop interface will use Qt Quick/QML. QML, rather than CSS, defines
  layout and visual styling; JavaScript expressions and functions may be used
  within QML for UI behavior.
- HTML, a browser, and an embedded web view are out of scope.
- The desired visual direction is a distinctive gaming control panel inspired
  by the information hierarchy and polish of Armoury Crate/G-Helper, without
  copying their assets or exact screen designs.

## Current system

### Approved visual direction (2026-10-09)

The user approved the revised Armoury-inspired mockup with a black background
and near-black panels, red accents, a compact icon rail, a large CPU/GPU
frequency gauge, adjacent hardware readings, individual fan speeds, and
prominent operating-profile buttons. Earlier generic dashboard concepts were
rejected. The implementation should retain this layout and use actual backend
readings; illustrative mockup telemetry belongs only in an explicit demo mode.

The design reference is the conversation artifact `rog-control-armoury.html`.
Its full path at approval is
`/home/dominatorx/.codex/visualizations/2026/10/09/01a12210-e51e-7e90-9113-7c3ce2ff0280/rog-control-armoury.html`.

The desktop shell is `rogcontrol/app.py`, built on GTK 4 and libadwaita. Its
feature pages and custom widgets are also GTK classes, with roughly 529 GTK and
Adwaita references across the desktop UI modules. The pages combine widget
construction, event handling, polling, validation, and calls into hardware and
configuration modules, so a toolkit change requires rebuilding the view layer
and teasing apart some controller behavior; it is not a stylesheet swap.

Hardware access, profile/configuration storage, command-line operations,
privileged helper operations, and background services are substantially
separate from the GTK interface and should remain the source of truth. The
Decky plugin already has an independent React/TypeScript UI and Python backend;
it is not part of this desktop migration.

There are additional GTK-related runtime surfaces: `rogcontrol-tray` is a
separate GTK 3 process; the installer checks and installs GTK 4/libadwaita and
GTK 3/AppIndicator; packaging declares these dependencies; UI update helpers
import GTK. Some non-UI modules use Gio/GLib through PyGObject. Those uses do
not require GTK and may remain as Python GObject dependencies unless a later
explicit scope removes PyGObject as well.

## Architecture

### Desktop shell and views

- Add a Qt Quick application shell using PySide6 and QML. QML owns navigation,
  page composition, responsive layout, styling, dialogs, and input controls.
- Package QML and other UI assets as application resources or install them in
  a stable application data directory; do not rely on the caller's working
  directory.
- Keep the current Python app entry point and CLI dispatch. The graphical path
  will start the Qt application; headless CLI commands must continue to avoid
  importing the graphical toolkit.
- Replace GTK page/widget classes with QML views backed by Python controller
  objects. Controllers expose typed properties, invokable operations, and
  signals for state changes. Keep reusable hardware calculations and
  configuration validation in existing non-UI modules.

### Backend bridge and concurrency

- QML calls Python controllers; controllers call the existing configuration,
  profile, hardware, and helper APIs. QML does not directly access files,
  spawn privileged commands, or duplicate hardware policy.
- Qt object creation and UI state updates stay on the Qt main thread. Slow
  hardware probes, fan operations, network update checks, and other blocking
  work run in workers and report results back through Qt signals.
- Preserve current busy ownership, timeout, cancellation/close behavior,
  error reporting, and apply/revert semantics as each screen is migrated.

### Tray and desktop integration

- Replace the separate GTK 3 tray process with a GTK-free implementation using
  PySide6 `QSystemTrayIcon` and a Qt menu, preserving profile switching,
  show-window, and quit behavior. Confirm StatusNotifierItem availability on
  supported desktop environments during implementation; retain graceful
  operation when no tray host is present.
- Keep existing desktop launchers, systemd user services, hotkeys, notifications,
  and helper security boundaries unless a specific integration must change for
  Qt.
- GTK-only drawing code and GTK CSS assets are replaced by QML/Qt equivalents.

### Packaging and installation

- Replace GTK 3/4 and libadwaita requirements with PySide6/Qt Quick runtime
  requirements in the installer and Arch package metadata. Runtime package
  names vary by distribution, so installation checks must verify the selected
  Qt Quick and QML modules using the system Python used to launch ROG Control.
- Retain PyGObject only where remaining Gio/GLib integrations need it; do not
  treat PyGObject as equivalent to a GTK dependency.
- Update package asset-copy rules to include QML/resource files and remove
  obsolete GTK UI stylesheet assets.
- Update install, update, uninstall, dependency summaries, and documentation so
  they agree about the new runtime requirements and no longer describe GTK as
  the application toolkit.

## Migration sequence

1. Establish a runnable PySide6/Qt Quick shell and a minimal controller bridge;
   verify it launches from the installed-package layout without importing GTK.
2. Migrate shared app state and navigation, then migrate feature pages and
   dialogs in coherent groups, preserving the existing config and hardware
   contracts. Start with overview/quick access and shared profile/apply flows;
   proceed through CPU, GPU, fans, battery, keyboard, and system features.
3. Replace GTK-specific update UI and the GTK 3 tray process.
4. Update packaging, installer/uninstaller, launcher, and user documentation;
   remove GTK dependencies after all GTK entry points and modules are gone.
5. Perform integration review for supported desktop sessions and hardware
   flows, then delete obsolete GTK UI files and assets.

Each migration step must leave CLI, helper, and background service operation
independent from the graphical toolkit. The existing GTK application may be
kept runnable during early development, but the released package must have one
supported desktop UI and must not require GTK.

## Compatibility and error handling

- Preserve the user's existing `~/.config/rogcontrol.json` data and profile
  semantics; no configuration migration is expected for the toolkit change.
- A missing or incompatible Qt Quick runtime must produce an actionable
  installation error rather than a Python import traceback.
- Hardware failures and unsupported device controls continue to be displayed
  as feature-level status/errors; a UI migration must not silently broaden
  hardware support or change the behavior of helpers/services.
- Keep Wayland and X11 support. Verify scaling, resizing, and keyboard input on
  both, including narrow window sizes supported by the current app.

## Out of scope

- Rewriting the hardware helper, background enforcer, profile file format, or
  Decky plugin.
- Adding a web server, hosted UI, HTML, CSS, browser engine, or Electron/Tauri
  runtime.
- Removing all PyGObject/Gio/GLib dependencies if they are still needed for
  non-GTK desktop integrations.
- Changing feature behavior, hardware support claims, or privilege boundaries.

## Acceptance criteria

- The desktop UI launches as a locally installed Qt Quick application and
  provides an ROG Control visual identity and navigation without GTK.
- All currently supported desktop controls and profile workflows are available
  and continue to use existing Python configuration and hardware APIs.
- CLI commands, privileged helper behavior, background services, and existing
  user configuration continue to work without importing the desktop toolkit.
- Tray functions remain available through the GTK-free tray implementation
  where the desktop provides a supported tray host; no-tray environments can
  still launch and use the application.
- Installer and package metadata install the required Qt runtime and do not
  require GTK 3, GTK 4, or libadwaita. Remaining PyGObject dependencies are
  documented as Gio/GLib integrations if retained.
- QML/resources are installed correctly; no runtime path depends on a source
  checkout.
- Documentation and dependency checks consistently describe the Qt Quick UI.

## Verification approach

During implementation, run focused checks for CLI dispatch without the GUI
runtime, QML startup from both a source checkout and an installed package,
controller operations, tray behavior, dependency checks, and package contents.
Exercise representative profile apply/revert, live polling, fan curve, GPU,
keyboard, and settings import/export flows on available supported hardware.
Use static checks to find residual GTK imports and verify GTK packages are no
longer hard requirements. Do not claim hardware integration coverage beyond
machines actually exercised.
