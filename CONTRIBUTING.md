# Contributing

## Before filing a bug

Read the "If something is wrong" section of [1-HOW-TO-INSTALL.txt](rogcontrol/1-HOW-TO-INSTALL.txt) first — most control-greyed-out and "does nothing" reports trace back to a missing optional dependency, which the tooltip on the control already names. Then use the [bug report form](https://github.com/D0minatorX/rogcontrol/issues/new/choose); it asks for the device model, `asusd` status, OS, and logs up front because those are the first things needed to reproduce anything.

## This has one test bench

Everything here has been verified on exactly one machine: an ASUS ROG Strix G16 (G614PR), Ryzen 9 8940HX, RTX 5070 Ti. If you're proposing a change to CPU/GPU limit ranges, fan curve shapes, or anything else that reads real hardware, say in the PR what you tested it on and what you observed — a change that looks correct in the diff can still be wrong on hardware neither of us has measured.

If you're adding support for a different ASUS model, the interesting part is usually what's *different*: different power limit ranges, a different embedded-controller quirk, extra keyboard lighting modes. Say what you found and how you found it.

## Hardware support reports

If your laptop supports a setting that ROG Control hides, does not apply, or
labels incorrectly, please report it. This includes CPU controls, GPU power
and clock controls, Dynamic Boost, temperature targets, fan curves, keyboard
features, graphics-mode switching, and Cardwire or Supergfx behavior.

Please include:

- exact laptop model and board name, if available;
- CPU, GPU, kernel, distribution, desktop, and display-server details;
- NVIDIA driver or other relevant driver versions;
- the ROG Control version and selected graphics backend;
- the control that is missing or incorrect, plus the expected behavior;
- relevant logs and the exact commands or steps that reproduce it;
- whether another tool, such as G-Helper or LACT, can perform the setting.

Do not include passwords, private files, or complete system dumps containing
personal information. A confirmation that a feature works on another model is
also valuable, even when you cannot change the code.

## Making a change

1. Fork and branch off `gtk4-ui` — that's the active branch, not `main`.
2. Keep the change scoped to the problem. This codebase tends to explain *why* a piece of logic exists in a comment right above it (see [hardware.py](rogcontrol/hardware.py) for plenty of examples) — if you're adding a workaround for a hardware quirk, explain what you measured, the same way.
3. If your change touches CPU, GPU, or fan behaviour, describe what you tested and on what hardware, in the PR description.
4. Open the PR against `gtk4-ui`.

For hardware support pull requests, explain the detection logic, show the
write/readback or other verification you used, and identify the hardware on
which you tested it. Add or update regression tests where practical. Do not
make a control visible solely because a range is reported: unsupported
devices must continue to hide it safely.

## Source layout and local checks

The application source is in `rogcontrol/`. Qt controllers live in
`rogcontrol/qt/controllers/`, and the window and components are in
`rogcontrol/qml/`. Hardware reads and helper calls live in `hardware.py`;
`cpu_plan.py` builds the ordered CPU commands without accessing hardware.
`fancurve.py` owns shared fan-curve conversion and write decisions. The
`rogcontrol-apply.py` and `rogcontrol-enforcer.py` scripts handle login and
background enforcement. The separate Decky plugin is in `decky/`.

With the local test suite available, run from the repository root:

```sh
python3 scripts/test_qt_release.py
QT_QPA_PLATFORM=offscreen python3 -m rogcontrol --self-test
QT_QPA_PLATFORM=offscreen python3 scripts/smoke_qt_capabilities.py
QT_QPA_PLATFORM=offscreen python3 scripts/smoke_qt_capabilities.py --unsupported
```

The `tests/` directory is kept local and is not included in this repository.
The release runner needs those local tests, plus the GPU recovery tests in
`scripts/`. It rejects unclassified test modules rather than silently omitting
them. Qt UI tests run separately with the offscreen platform.

These checks use fixtures and mocked hardware operations; they do not prove
physical device behavior. For installer or resource changes, also verify the
staged installation/package contents. The dated
[Qt validation record](docs/QT-1.0.2.2-VALIDATION.md) records the checks performed
and their limits.

## What's useful even without touching code

Confirmation reports are genuinely useful: "worked on a G614JI, here's what differed" is a real contribution, not noise. Open an issue for it.
