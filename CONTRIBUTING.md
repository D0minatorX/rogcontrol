# Contributing

## Before filing a bug

Read the "If something is wrong" section of [1-HOW-TO-INSTALL.txt](rogcontrol/1-HOW-TO-INSTALL.txt) first — most control-greyed-out and "does nothing" reports trace back to a missing optional dependency, which the tooltip on the control already names. Then use the [bug report form](https://github.com/D0minatorX/rogcontrol/issues/new/choose); it asks for the device model, `asusd` status, OS, and logs up front because those are the first things needed to reproduce anything.

## This has one test bench

Everything here has been verified on exactly one machine: an ASUS ROG Strix G16 (G614PR), Ryzen 9 8940HX, RTX 5070 Ti. If you're proposing a change to CPU/GPU limit ranges, fan curve shapes, or anything else that reads real hardware, say in the PR what you tested it on and what you observed — a change that looks correct in the diff can still be wrong on hardware neither of us has measured.

If you're adding support for a different ASUS model, the interesting part is usually what's *different*: different power limit ranges, a different embedded-controller quirk, extra keyboard lighting modes. Say what you found and how you found it.

## Making a change

1. Fork and branch off `gtk4-ui` — that's the active branch, not `main`.
2. Keep the change scoped to the problem. This codebase tends to explain *why* a piece of logic exists in a comment right above it (see [hardware.py](rogcontrol/hardware.py) for plenty of examples) — if you're adding a workaround for a hardware quirk, explain what you measured, the same way.
3. If your change touches CPU, GPU, or fan behaviour, describe what you tested and on what hardware, in the PR description.
4. Open the PR against `gtk4-ui`.

## What's useful even without touching code

Confirmation reports are genuinely useful: "worked on a G614JI, here's what differed" is a real contribution, not noise. Open an issue for it.
