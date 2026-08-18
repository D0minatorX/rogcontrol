# Security Policy

## Scope

The part of this app worth a security report is the privileged path: `rogcontrol-helper` (installed to `/usr/local/bin`, runs as root via the sudoers rule `install.sh` adds) and the sudoers rule itself. Everything else runs as your own user and can't do more than you already could.

If you find a way for the helper to be made to do something other than what its caller asked — argument injection, a path that isn't validated, a way to reach a shell through it, a sudoers rule broader than "run this one binary" — that's a real report.

Bugs in the GTK4 UI, a control that doesn't work on your hardware, or the fan curve not behaving the way you expect are **not** security issues — file those as a normal [bug report](https://github.com/D0minatorX/rogcontrol/issues/new/choose) instead.

## Supported versions

Only the latest commit on `gtk4-ui` (the active branch) gets fixes. There's no older version being maintained in parallel.

## Reporting a vulnerability

Open a [GitHub Security Advisory](https://github.com/D0minatorX/rogcontrol/security/advisories/new) (private) rather than a public issue, so it isn't visible before there's a fix. Include what you found, how to reproduce it, and what it lets an attacker do.

This is a one-person hobby project — expect an initial response within a few days, not an SLA. A confirmed vulnerability gets a fix as soon as reasonably possible, and credit in the fix commit unless you'd rather stay anonymous.
