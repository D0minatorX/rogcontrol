# ROG Control Decky plugin

This plugin exposes saved ROG Control profiles, the active profile's CPU
Turbo Boost setting, CPU and GPU clock ceilings, and live fan speeds and
temperatures in Decky Quick Access while using Gamescope.

ROG Control must be installed for the same Linux user as Decky, with the
Decky v0.1.2 CLI support included in the matching ROG Control source version.
Install the updated ROG Control app first; older app versions do not report
profile completion and cannot provide every v0.1.2 feature. The plugin
uses ROG Control's command line and saved profile configuration; CPU controls
are shown only when the kernel exposes them. The profile chooser submits a
profile request through ROG Control's background worker. CPU edits are saved
to the active profile and applied immediately.

## Install from the ZIP

Install Decky Loader and ROG Control for your Linux user first. Extract the
plugin ZIP, open a terminal in the extracted `ROG-Control` folder, and run:

```sh
./install.sh
```

Run the installer as your normal user. It installs the plugin and asks for
sudo to install into Decky’s plugin folder and restart Decky Loader. You can also install or update the plugin from
ROG Control under System → Updates.

The **Check for plugin updates** section reads the latest release from
[ROG Control Releases](https://github.com/D0minatorX/rogcontrol/releases).
An update is installed only when that release includes the versioned
`ROG-Control-Decky-vX.Y.Z.zip` package and its `.sha256` sidecar. Reload Decky
after updating to activate the new backend and frontend. Decky must run its
backend with permission to write the plugin folder; if it cannot, use the
desktop app’s System → Updates page or the ZIP installer instead.

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
