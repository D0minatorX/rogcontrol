# Qt 1.0.2.2 installation-test build

The default launcher uses Qt Quick and the original Python hardware, config,
profile, service and helper modules. `--demo` remains isolated and simulated;
launch without that flag after installation to control the real device.

## Verified on 2026-10-12

- 266 maintained backend, tray and mocked Qt controller tests passed.
- QML lint and Python syntax checks passed.
- All-page fixture smoke passed at 1040px and 360px.
- Six rendered control visibility assertions passed for supported and unsupported
  capability fixtures at both widths (24 assertions total).
- Installer library-copy staging and Arch package staging preserved resources.
- The release ZIP contains 113 application files, including Qt/QML/JavaScript;
  all extracted files matched source, and an extracted-only smoke test passed.
- Original 48-hour/weekly/monthly update choices restored; unsupported charge,
  boost, PSR, PowerMizer and Gamescope controls have their original availability
  behaviour. Keyboard capability gates and external brightness handling restored.

## Repeat checks from the source checkout

```sh
python3 scripts/test_qt_release.py
python3 scripts/smoke_qt_capabilities.py
python3 scripts/smoke_qt_capabilities.py --width 360
python3 scripts/smoke_qt_capabilities.py --unsupported
python3 scripts/smoke_qt_capabilities.py --unsupported --width 360
```

The original runner excluded 76 obsolete GTK test methods from the maintained
gate. As of the 2026-10-30 cleanup, those exclusions are removed: the retired
test files were already absent, and the remaining two GTK cases were removed
from the local GPU capability tests. The runner now loads that entire backend
module. Qt smoke and controller tests cover selected replacement paths, not
every legacy assertion. The `tests/` directory remains local and is required
to run the release gate.

## Install for device testing

Extract `Rog-Control-V1.0.2.2.zip`, then:

```sh
cd Rog-Control-V1.0.2.2/rogcontrol
bash install.sh
```

Use the normal desktop launcher (no `--demo`) after installation. The installer
backs up and preserves existing settings. See `1-HOW-TO-INSTALL.txt` for distro
requirements, including atomic-host dependency reboot requirements.

## Device checks still required

Installation on the real host was not performed. No real settings were changed
for validation. Check existing profiles and their Apply/Revert readback, supported
CPU/GPU controls, fan curves/calibration, lighting and Fn Lock, charge limit and
one-shot charging, display refresh on unplug/replug, tray/startup and updates.
Calibration, GPU transitions, privileged helpers, suspend/resume, reboot-dependent
PSR and package installation need an actual device session. A passing fixture or
mocked backend test does not prove those physical effects.

## Follow-up fixes — 2026-10-12

- Maintained suite: 273 tests passed after the screenshot fixes.
- EPP apply routes through `cpuepp`, saves only successful changes to the selected profile, and reloads the value. Tests use a temporary configuration and mocked helper, not physical writes.
- Preserve saved EPP when the performance governor temporarily advertises only `performance`; refresh choices after governor changes.
- GPU identity reads the NVIDIA driver model without waking the GPU. This machine reports NVIDIA GeForce RTX 5070 Ti Laptop GPU.
- This machine's firmware reports cycle_count=0 (unavailable). Hide that unsupported metric; positive firmware counts remain displayed.
- Force English numeric locale. Arabic-locale Qt self-test and light-theme render passed.
- Successful global status messages expire after 3.5 seconds; geometry saves are silent.
- Light theme uses muted gray surfaces instead of white.

### CPU maximum clock follow-up

AMD pstate reduces cpuinfo_max_freq to 2.401 GHz while boost is disabled. Range detection now uses amd_pstate_max_freq when higher, preserving the 5.386028 GHz boost range and saved profile ceilings. The disabled control explains the Turbo boost dependency. 275 maintained tests and the offscreen Qt self-test passed. No boost or clock settings were changed on hardware.

### Recheck and temporary fan boost follow-up

Saved status now expires after 1.5 seconds. Fan boost speed and duration persist on edit in fan_boost_preferences; Stop remains visible and is enabled during a boost. Added tests for preference persistence, start/helper routing, stop/restoration routing, and keyboard Recheck in Static/Ambient modes. 280 maintained tests, Qt self-test, and affected QML lint passed. Hardware writes and screen capture were mocked.

### Installer dependency audit

Required Qt/PySide6 installation and runtime recheck verified using stubbed pacman/apt/dnf commands. Optional packages now install independently so an unavailable package does not abort the remaining dependency installs. Added GStreamer introspection, video elements, PipeWire capture, and desktop portal packages for Ambient lighting; Arch optdepends includes these and evdev. No GTK imports in application Python; Gio/GLib and GStreamer remain in use. 282 maintained tests, bash syntax, and the real offscreen Qt runtime diagnostic passed. No actual package installation or distro-wide fresh-install test was performed.

## Cleanup and refactor validation — 2026-10-30

- Removed unused GTK sidebar SVGs and the unused `PendingChangesBar.qml`;
  both installer paths were updated to stop copying the SVG bundle.
- Extracted CPU command planning into `cpu_plan.py`, preserving the
  `hardware.cpu_apply_plan` and `hardware.CPU_APPLY_STEPS` interfaces.
- Shared fan-curve conversion/write decisions, service-state queries, and CPU
  boost detection while preserving caller-specific retry and readback behavior.
- Removed ten uncalled helpers and four unused constants from application source.
- The release runner passed 396 backend/Qt tests and six isolated UI tests.
  The same maintained tests remained selected after the test cleanup.
- Qt capability smoke checks passed with supported and unsupported controls.
  The offscreen application `--self-test` also passed.
- Python parsing, installer/uninstaller and PKGBUILD shell syntax, Arch package
  assembly from the current source, and isolated staged-package imports passed.

This validation used local tests and staged files. It did not replace the
installed application, perform a full `makepkg` build or system installation,
or exercise live hardware writes. Previously built release archives were not
rebuilt as part of this cleanup.
