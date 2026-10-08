# ROG Control Decky plugin

This plugin exposes saved ROG Control profiles, the active profile's CPU
Turbo Boost setting, and its CPU clock ceiling in Decky Quick Access while
using Gamescope.

ROG Control must be installed for the same Linux user as Decky. The plugin
uses ROG Control's command line and saved profile configuration; CPU controls
are shown only when the kernel exposes them. The profile chooser submits a
profile request through ROG Control's background worker. CPU edits are saved
to the active profile and applied immediately.

The **Check for plugin updates** section reads the latest release from
[ROG Control Releases](https://github.com/D0minatorX/rogcontrol/releases).
An update is installed only when that release includes the versioned
`ROG-Control-Decky-vX.Y.Z.zip` package and its `.sha256` sidecar. Reload Decky
after updating to activate the new backend and frontend.

For maintainers, build an installable package with:

```sh
python3 scripts/package_decky.py
```

Attach both generated files to a ROG Control GitHub release:

- `ROG-Control-Decky-vX.Y.Z.zip`
- `ROG-Control-Decky-vX.Y.Z.zip.sha256`

The desktop app's System → Updates page checks the same release assets and
offers Install or Update. No plugin release is included until those assets are
published.
