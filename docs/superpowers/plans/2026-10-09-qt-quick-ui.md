# ROG Control Qt Quick UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GTK desktop window and tray with a locally installed Python + Qt Quick/QML interface while preserving ROG Control's current hardware, profile, helper, and service behavior.

**Architecture:** A PySide6 Qt Quick shell loads packaged QML and exposes Python QObject controllers for application and feature state. Controllers call the existing config, hardware, profile, and helper modules; Qt worker signals marshal slow operation results back to the GUI thread. A Qt system tray implementation replaces the standalone GTK 3 tray. Packaging is updated only after all Qt UI surfaces work.

**Tech Stack:** Python 3, PySide6, Qt Quick/QML, JavaScript expressions/functions within QML, existing Python hardware/configuration modules, Qt `QSystemTrayIcon` for the tray.

**Spec:** `docs/superpowers/specs/2026-10-09-qt-quick-ui-design.md`

## Global Constraints

- The application remains a locally installed Linux desktop app, not a hosted website.
- Python remains the application and hardware-control language.
- The desktop interface will use Qt Quick/QML. QML, rather than CSS, defines layout and visual styling; JavaScript expressions and functions may be used within QML for UI behavior.
- HTML, a browser, and an embedded web view are out of scope.
- QML calls Python controllers; controllers call the existing configuration, profile, hardware, and helper APIs. QML does not directly access files, spawn privileged commands, or duplicate hardware policy.
- Qt object creation and UI state updates stay on the Qt main thread. Slow hardware probes, fan operations, network update checks, and other blocking work run in workers and report results back through Qt signals.
- Preserve the user's existing `~/.config/rogcontrol.json` data and profile semantics; no configuration migration is expected for the toolkit change.
- The desktop app and tray must not require GTK 3, GTK 4, or libadwaita. Retain PyGObject only where remaining Gio/GLib integrations need it.
- Keep Wayland and X11 support, including the current narrow-window behavior.
- Do not rewrite the hardware helper, background enforcer, profile file format, or Decky plugin.

---

## File Structure

### New Qt application layer

- `rogcontrol/qt/__init__.py` — Qt application package marker.
- `rogcontrol/qt/application.py` — QApplication/QQmlApplicationEngine startup, application ID, command flags, lifecycle, and QML loading.
- `rogcontrol/qt/workers.py` — QRunnable worker and QObject signals used to run slow calls off the GUI thread.
- `rogcontrol/qt/controllers/app.py` — application-level properties and actions: page selection, profile selection, shared busy state, pending changes, apply/revert, close, and tray coordination.
- `rogcontrol/qt/controllers/overview.py`, `quick_access.py`, `cpu.py`, `gpu.py`, `fans.py`, `battery.py`, `keyboard.py`, `system.py` — focused QObject controllers for each feature page. They expose state and invokable operations to QML and delegate policy to existing Python modules.
- `rogcontrol/qml/Main.qml` — responsive application shell and page navigation.
- `rogcontrol/qml/theme/Palette.qml` — QML palette and appearance tokens derived from the current ROG Control light/dark colors in `rogcontrol/ui.py`.
- `rogcontrol/qml/components/` — reusable navigation item, card, status row, button, slider, pending-change bar, dialog, color picker, and fan curve components.
- `rogcontrol/qml/pages/` — Overview, Quick Access, CPU, GPU, Fans, Battery, Keyboard, and System views.

### Existing integration points

- `rogcontrol/__main__.py` — keep headless CLI dispatch first; route graphical startup to `rogcontrol.qt.application`.
- `rogcontrol/rogcontrol-tray` — replace GTK/AppIndicator loop and menu with PySide6 `QApplication`, `QSystemTrayIcon`, and Qt menu actions while preserving CLI/service exit behavior.
- `rogcontrol/rogcontrol-tray.service` — keep its startup and exit-code contract; revise comments only if the Qt implementation changes a described behavior.
- `rogcontrol/install.sh` — replace hard GTK/libadwaita checks, GTK3/AppIndicator optional dependencies, Cairo graph dependency if no longer used, and platform package names with Qt Quick/QML runtime checks.
- `packaging/PKGBUILD` — replace GTK package dependencies and include the `rogcontrol/qt` Python modules and `rogcontrol/qml` assets.
- `rogcontrol/install.sh`, `rogcontrol/uninstall.sh`, `rogcontrol/org.rogcontrol.RogControl.desktop`, service files, and user documentation — update only the launcher/runtime/package references affected by the UI switch.
- `rogcontrol/profile_actions.py`, `rogcontrol/update_ui.py`, `rogcontrol/decky_update_ui.py`, `rogcontrol/ui.py`, `rogcontrol/pages/*.py`, and `rogcontrol/widgets/*.py` — sources to migrate, then remove when their GTK behavior has a Qt equivalent. Keep backend modules and CLI files that do not import GTK.
- `README.md`, `rogcontrol/1-HOW-TO-INSTALL.txt`, `rogcontrol/2-FEATURES.txt`, and `packaging/PKGBUILD` — remove stale GTK UI requirements and explain the Qt runtime.

The existing `decky/` React/TypeScript frontend is intentionally untouched.

## Task 1: Add Qt Quick startup without changing the installed UI yet

**Files:**
- Create: `rogcontrol/qt/__init__.py`
- Create: `rogcontrol/qt/application.py`
- Create: `rogcontrol/qml/Main.qml`
- Modify: `rogcontrol/__main__.py`

**Interfaces:**
- `rogcontrol.qt.application.main(argv: list[str] | None = None) -> int` starts one QApplication and one QQmlApplicationEngine.
- `__main__.py` continues dispatching `--hardware-report`, `profile`, `keyboard`, `report`, and `decky` before importing the GUI package.
- `Main.qml` initially receives no backend controller and displays a simple ROG Control window with a title and placeholder page container.

- [ ] Add `main(argv=None)` in `rogcontrol/qt/application.py`, construct `QApplication`, set the stable application name/organization, create `QQmlApplicationEngine`, load `qml/Main.qml` from a path derived from `Path(__file__).resolve()`, return a nonzero status if no root object loads, and otherwise enter the Qt event loop.
- [ ] Change only the final graphical dispatch in `rogcontrol/__main__.py` to import `rogcontrol.qt.application.main`; leave every headless branch above it intact.
- [ ] Add `Main.qml` with an `ApplicationWindow`, stable minimum dimensions, ROG Control title, and centered placeholder content. Do not import GTK modules in the new path.
- [ ] Inspect import flow and launcher paths so QML resolution works from the installed Python package layout as well as the repository tree.
- [ ] Commit the standalone Qt startup slice as `feat(ui): add Qt Quick application shell`.

## Task 2: Establish the Python/QML controller and worker contract

**Files:**
- Create: `rogcontrol/qt/workers.py`
- Create: `rogcontrol/qt/controllers/__init__.py`
- Create: `rogcontrol/qt/controllers/app.py`
- Modify: `rogcontrol/qt/application.py`
- Modify: `rogcontrol/qml/Main.qml`

**Interfaces:**
- `TaskSignals(QObject)` exposes `finished(str job_id, object result, object error)`.
- `TaskRunner(QObject)` exposes `submit(job_id: str, function: Callable[[], object]) -> None` and emits one completion for each submitted job.
- `AppController(QObject)` exposes `currentPage`, `pageTitle`, `activeProfile`, `profileNames`, `busy`, `pendingChanges`, `statusText`; invokables `navigate(page_id)`, `selectProfile(name)`, `applyChanges()`, and `revertChanges()`; signals `stateChanged()` and `errorOccurred(str)`.
- `application.py` creates one AppController and one TaskRunner, registers the controller as QML context property `appController`, and keeps both alive until app shutdown.

- [ ] Implement a QRunnable wrapper that calls a supplied Python function, catches exceptions as values, and emits `finished(job_id, result, error)` through a QObject signal object owned for the duration of the job.
- [ ] Implement `TaskRunner.submit()` with a `QThreadPool`; prohibit duplicate active job IDs and release IDs on completion so controller busy state cannot stick after failure.
- [ ] Add an AppController with initial navigation/profile state and method signatures listed above; keep profile reads through `config.load_config()` and `profiles` helpers, not file reads in QML.
- [ ] Register the controller before `QQmlApplicationEngine.load()` and connect QML `Connections` to the named state/error signals.
- [ ] Document in code that controller property changes and all QML-facing signals must be emitted on the Qt main thread.
- [ ] Commit the controller boundary as `feat(ui): add Qt controller and worker bridge`.

## Task 3: Rebuild the window shell, theme, Overview, and Quick Access

**Files:**
- Create: `rogcontrol/qt/controllers/overview.py`
- Create: `rogcontrol/qt/controllers/quick_access.py`
- Create: `rogcontrol/qml/theme/Palette.qml`
- Create: `rogcontrol/qml/components/NavigationItem.qml`
- Create: `rogcontrol/qml/components/MetricCard.qml`
- Create: `rogcontrol/qml/components/StatusRow.qml`
- Create: `rogcontrol/qml/pages/OverviewPage.qml`
- Create: `rogcontrol/qml/pages/QuickAccessPage.qml`
- Modify: `rogcontrol/qt/controllers/app.py`
- Modify: `rogcontrol/qt/application.py`
- Modify: `rogcontrol/qml/Main.qml`
- Source behavior: `rogcontrol/app.py`, `rogcontrol/pages/overview.py`, `rogcontrol/pages/quick_access.py`, `rogcontrol/ui.py`, `rogcontrol/appearance.css`

**Interfaces:**
- AppController owns eight page IDs in the existing order: `quick_access`, `cpu`, `gpu`, `fans`, `battery`, `keyboard`, `system`, `overview`.
- `OverviewController.refresh()` collects the values currently shown by the GTK Overview from existing hardware/capability APIs and publishes serializable rows/cards.
- `QuickAccessController.refresh()` exposes saved start-on-boot, AC/battery/USB-C/Gamescope profile choices, and current profile actions without calling widgets.
- Palette.qml provides named light/dark roles (`window`, `panel`, `sidebar`, `text`, `mutedText`, `border`, `accent`, `hover`, `selected`) and responds to the existing `appearance` config value.

- [ ] Replace the placeholder with a responsive two-pane shell: left navigation, page title/header, profile selector, page content, and compact navigation for widths below the current 620sp collapse breakpoint.
- [ ] Port current app palette values and `appearance` semantics from `ui.py` into QML color roles; keep the user's stored setting names `system`, `light`, and `dark`.
- [ ] Move Overview data collection into OverviewController and Quick Access config/profile actions into QuickAccessController; have blocking probes use TaskRunner.
- [ ] Add MetricCard, StatusRow, and NavigationItem QML components and use them to implement the Overview and Quick Access pages.
- [ ] Wire profile selection, selected navigation, refresh, and error messages through AppController; ensure QML never accesses the config path or hardware helper directly.
- [ ] Commit the first complete navigation and page slice as `feat(ui): add Qt overview and quick access pages`.

## Task 4: Port profile editing and shared input components

**Files:**
- Create: `rogcontrol/qml/components/RogButton.qml`
- Create: `rogcontrol/qml/components/SliderRow.qml`
- Create: `rogcontrol/qml/components/PendingChangesBar.qml`
- Create: `rogcontrol/qml/components/ColorPicker.qml`
- Create: `rogcontrol/qml/components/CurveEditor.qml`
- Create: `rogcontrol/qt/controllers/profile.py`
- Modify: `rogcontrol/qt/controllers/app.py`
- Source behavior: `rogcontrol/profile_actions.py`, `rogcontrol/widgets/*.py`, `rogcontrol/fancurve.py`, `rogcontrol/config.py`

**Interfaces:**
- `ProfileController` exposes profile names/current profile, staged profile values, validation errors, and dirty state; invokables `stageValue(path, value)`, `apply()`, `revert()`, `create(name)`, `duplicate(name)`, `delete(name)`, `importFile(path)`, and `exportFile(path)`.
- Profile persistence and validation stay in `profiles.py`/`config.py`; privileged/hardware apply stays behind the current `hardware` and helper APIs.
- Shared components receive values and callbacks through QML properties; no component owns persistence or hardware policy.

- [ ] Port the existing staged-edit/apply/revert semantics to ProfileController, including the pending-change warning and single owner for in-flight hardware writes.
- [ ] Port slider keyboard entry, bounds, units, enable/disable state, and accessibility labels from `widgets/slider_row.py` into SliderRow.qml and controller validation.
- [ ] Port the fan curve editing interaction to CurveEditor.qml while retaining interpolation/conversion helpers from `fancurve.py`.
- [ ] Port profile create/duplicate/delete/import/export workflows into ProfileController and Qt file dialogs; preserve confirmation behavior and existing JSON format.
- [ ] Port color-picker and button components, keeping keyboard RGB validation in `kbdcolor.py` and color preferences in the same config keys.
- [ ] Commit reusable controls/profile behavior as `feat(ui): add Qt profile editing controls`.

## Task 5: Port CPU, GPU, and Fans pages

**Files:**
- Create: `rogcontrol/qt/controllers/cpu.py`
- Create: `rogcontrol/qt/controllers/gpu.py`
- Create: `rogcontrol/qt/controllers/fans.py`
- Create: `rogcontrol/qml/pages/CpuPage.qml`
- Create: `rogcontrol/qml/pages/GpuPage.qml`
- Create: `rogcontrol/qml/pages/FansPage.qml`
- Source behavior: `rogcontrol/pages/cpu.py`, `rogcontrol/pages/gpu.py`, `rogcontrol/pages/gpu_base.py`, `rogcontrol/pages/gpu_supergfx.py`, `rogcontrol/pages/fans.py`, `rogcontrol/graphics_backend.py`, `rogcontrol/fancurve.py`

**Interfaces:**
- Each controller publishes capability-filtered state and typed apply operations. GPU controller uses the selected existing backend and existing capability probes; CPU and Fans controllers call existing hardware APIs and helper entry points.
- Polling operations return immutable Python values from TaskRunner and update controller properties on the GUI thread.
- QML pages do not issue helper commands or duplicate hardware range/capability rules.

- [ ] Add CpuController state for detected CPU vendor/capabilities and existing power, boost, energy preference, clock, and undervolt controls; port the current apply ordering and per-step failure messages.
- [ ] Add GpuController state for backend, limits, offsets, PowerMizer, Dynamic Boost, and conditional voltage controls; preserve GPU guard/probe visibility rules and confirmation for voltage boost.
- [ ] Add FansController state for each channel, live RPM, fan curve points, calibration, and temporary boost; preserve serialized fan channel writes and current timeout/error reporting.
- [ ] Build CpuPage.qml, GpuPage.qml, and FansPage.qml using shared controls, keeping unavailable controls hidden or explained as in the GTK app.
- [ ] Connect navigation to controller refresh/close lifecycle so hidden pages do not start duplicate polling and active worker ownership remains bounded.
- [ ] Commit the three hardware-page group as `feat(ui): port cpu gpu and fan controls to Qt`.

## Task 6: Port Battery, Keyboard, and System pages

**Files:**
- Create: `rogcontrol/qt/controllers/battery.py`
- Create: `rogcontrol/qt/controllers/keyboard.py`
- Create: `rogcontrol/qt/controllers/system.py`
- Create: `rogcontrol/qml/pages/BatteryPage.qml`
- Create: `rogcontrol/qml/pages/KeyboardPage.qml`
- Create: `rogcontrol/qml/pages/SystemPage.qml`
- Modify: `rogcontrol/qt/controllers/app.py`
- Source behavior: `rogcontrol/pages/battery.py`, `rogcontrol/pages/keyboard.py`, `rogcontrol/pages/keyboard_bindings.py`, `rogcontrol/pages/keyboard_power.py`, `rogcontrol/pages/display_settings.py`, `rogcontrol/pages/system.py`, `rogcontrol/pages/system_base.py`, `rogcontrol/pages/system_supergfx.py`, `rogcontrol/decky_update_ui.py`, `rogcontrol/update_ui.py`

**Interfaces:**
- BatteryController exposes charge limit, charging status, one-shot charging, and display refresh options using existing battery/display helpers.
- KeyboardController exposes lighting, per-key bindings, Fn Lock, power states, idle timeouts, ambient mode, and charger flash through existing `kbdcolor`, `fnlock`, and keyboard helper modules.
- SystemController exposes capability/backend status, logs/report actions, update status, graphics mode, and service controls; update and Decky package workflows remain in `updater`, `decky_release`, and `decky_install` modules.

- [ ] Implement BatteryController operations and BatteryPage.qml, keeping config save and hardware transaction behavior unchanged.
- [ ] Implement KeyboardController operations and KeyboardPage.qml for lighting, bindings, Fn Lock, power states, idle timeout, ambient, and charger flash; preserve existing feature gating.
- [ ] Implement SystemController and SystemPage.qml for system status, current graphics backend, diagnostics/report, update flows, Cardwire/Supergfx controls, and Decky plugin install/update status.
- [ ] Replace Adw dialogs and GTK update/decky row construction with QML dialogs/components while retaining existing confirmation, status, timeout, and cleanup behavior from the backend workflow modules.
- [ ] Ensure every periodic monitor is stopped when the Qt app closes and every worker callback ignores results after its controller is disposed.
- [ ] Commit the remaining feature pages as `feat(ui): port battery keyboard and system pages to Qt`.

## Task 7: Replace the GTK 3 tray process

**Files:**
- Modify: `rogcontrol/rogcontrol-tray`
- Modify: `rogcontrol/rogcontrol-tray.service` only for comments or Qt environment needed by the replacement
- Source behavior: existing `Tray` config refresh, menu and process launch behavior in `rogcontrol/rogcontrol-tray`

**Interfaces:**
- Tray process remains the executable `rogcontrol-tray` and retains the existing service exit contract: exit code 42 means deliberate user quit and is not restarted by systemd.
- Tray menu retains Show window, built-in profile selection, and Quit actions. Profile switch requests continue through existing profile request/CLI mechanisms.
- The tray uses PySide6 `QSystemTrayIcon`; it does not import GTK, AppIndicator, or PyGObject for its visual/menu loop.

- [ ] Replace GTK/AppIndicator imports, icon/menu construction, GLib timers, and main loop with QApplication, QSystemTrayIcon, QMenu, and QTimer.
- [ ] Preserve profile polling, subprocess session handling, quit code 42, SIGTERM behavior, and service restart semantics.
- [ ] Keep graceful startup if `QSystemTrayIcon.isSystemTrayAvailable()` is false and report the actionable condition without crashing or entering a restart loop.
- [ ] Commit the Qt tray as `feat(ui): replace GTK tray with Qt system tray`.

## Task 8: Remove GTK UI and update installation/package metadata

**Files:**
- Modify: `rogcontrol/install.sh`
- Modify: `rogcontrol/uninstall.sh`
- Modify: `packaging/PKGBUILD`
- Modify: `rogcontrol/__main__.py`
- Modify: `rogcontrol/rogcontrol-tray.service`
- Modify: `README.md`, `rogcontrol/1-HOW-TO-INSTALL.txt`, `rogcontrol/2-FEATURES.txt`
- Remove after imports are migrated: `rogcontrol/app.py`, `rogcontrol/profile_actions.py`, `rogcontrol/ui.py`, `rogcontrol/update_ui.py`, `rogcontrol/decky_update_ui.py`, `rogcontrol/pages/*.py`, `rogcontrol/widgets/*.py`, `rogcontrol/appearance.css`, `rogcontrol/widgets.css`

**Interfaces:**
- Installer's hard GUI check confirms the Qt Quick and required QML modules import through the same `python3` that launches ROG Control.
- Optional dependency table no longer requires GTK3/AppIndicator or Python Cairo solely for UI drawing; retain Python GObject only for still-used Gio/GLib integrations.
- Arch package installs all `rogcontrol/qt` Python modules and `rogcontrol/qml` assets and declares Qt runtime dependencies instead of GTK/libadwaita.

- [ ] Replace the early GTK 4/libadwaita install check and atomic-system package list with a PySide6/Qt Quick check and per-distro Qt package installation paths.
- [ ] Remove GTK3/AppIndicator and Cairo UI-graph dependencies from the installer, optional dependency summary, package metadata, and user-facing install instructions when their last UI use is removed.
- [ ] Update PKGBUILD package dependencies and copy QML/controller resources into the installed Python package; ensure QML is found from the packaged module path.
- [ ] Update launcher/uninstaller/service comments and docs to describe Qt Quick and any remaining Gio/GLib-only PyGObject use.
- [ ] Remove GTK desktop modules and CSS assets after confirming the new Qt app/update/tray path has no imports to them.
- [ ] Commit dependency and legacy UI removal as `chore(packaging): remove GTK runtime requirements`.

## Task 9: Final migration review and delivery

**Files:**
- Review all files named in Tasks 1-8, especially `rogcontrol/__main__.py`, `rogcontrol/qt/`, `rogcontrol/qml/`, `rogcontrol/rogcontrol-tray`, `rogcontrol/install.sh`, and `packaging/PKGBUILD`.
- Modify: `docs/superpowers/specs/2026-10-09-qt-quick-ui-design.md` only if an implementation discovery changes an approved design decision; otherwise leave the approved spec intact.

- [ ] Review every acceptance criterion in the approved spec against the final source and package layout.
- [ ] Review the installer and Arch package file lists together so source, installer, and package-manager installs all include the same Qt/QML resources.
- [ ] Review the GTK import/dependency references and remove remaining GTK hard requirements; distinguish Gio/GLib PyGObject usage from GTK.
- [ ] Review command-line dispatch and service launchers to confirm CLI/helper/background behavior does not import Qt or UI modules.
- [ ] Summarize known platform/hardware limitations accurately and report the implementation commits and changed dependency requirements.

## Execution Notes

The GTK page modules are behavior references, not reusable Qt views. Port each screen by moving widget-independent policy to a focused controller and translating presentation to QML. Preserve existing hardware API calls, serialized writes, capability gating, save/apply semantics, UI busy feedback, and cleanup behavior. Do not make a page visually complete by exposing controls before its controller enforces the existing capability and validation rules.
