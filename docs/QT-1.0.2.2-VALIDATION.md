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

The runner explicitly lists 76 obsolete GTK test methods excluded from the
maintained gate. They are not counted as passing or migrated. Qt smoke and
controller tests cover selected replacement paths, not every legacy assertion.

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
