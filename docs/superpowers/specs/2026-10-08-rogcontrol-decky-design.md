# ROG Control Decky Plugin Design

## Goal

Expose ROG Control's existing profile and CPU controls in Decky while running
in Gamescope. Give the desktop System tab clear plugin installation and update
controls, and let the plugin update itself from the project's GitHub Releases.

## Scope

The first version provides:

- A Decky profile picker that lists saved ROG Control profiles and selects the
  active profile.
- CPU boost toggle and CPU maximum clock control when ROG Control detects the
  corresponding hardware controls.
- A System-tab status row that detects whether the Decky plugin is installed.
- An Install action when missing and an Update action when a newer compatible
  Decky plugin package is available.
- An update check and Update action inside the Decky plugin.

The plugin and desktop app must run on the same Linux installation. The plugin
is not a replacement for ROG Control's existing profile and hardware logic.

## Architecture

### Shared ROG Control interface

Add a small, headless command interface to ROG Control for the plugin. It will
report the plugin-relevant state (profiles, active profile, CPU control values,
and detected capabilities) and accept validated requests to select a profile
or change the current profile's CPU boost and maximum clock settings. Changes
must use ROG Control's existing config update and hardware apply paths, so
saved desktop settings and the running machine remain in sync. Commands return
structured results and meaningful errors without importing GTK.

The interface must serialize config changes through existing config helpers,
validate profile names against the current profile list, and use detected CPU
capabilities rather than assuming a control exists. Unsupported controls are
omitted or reported unavailable; failed hardware writes must be visible to the
Decky UI.

### Decky plugin

Add a separately packaged plugin directory in this repository, with a Decky
frontend for the controls and a backend that calls the headless ROG Control
interface. The frontend refreshes state after a change and shows errors from
the backend. Its update check uses only the repository's GitHub Releases and
installs a versioned plugin package through the supported Decky installation
path.

### Desktop System tab and release flow

The System tab detects the local Decky plugin installation and displays its
installed version. Install and Update download a versioned plugin release
asset from `D0minatorX/rogcontrol`, verify it before installation, then report
success or a useful failure. If no compatible release asset is available, the
UI reports that instead of opening a browser or attempting an incomplete
install.

Release asset naming, compatibility metadata, and checksum format will be
defined with the package build/release implementation. Install and update must
not execute an unverified downloaded script. The UI must not claim success
until the plugin version is detected after installation.

## Data flow

1. Decky requests plugin-relevant state from the local ROG Control command.
2. The command reads the existing config and detected CPU capabilities and
   returns a structured response.
3. A user action is validated by the command, saved to the current profile,
   and applied through the existing ROG Control hardware path.
4. The plugin reloads state and presents the effective saved values or an
   actionable error.
5. The System tab and the Decky plugin independently check the same GitHub
   Releases metadata and install/update the plugin package appropriate to
   their environment.

## Compatibility and errors

- The controls require Decky and ROG Control to be installed on the same Linux
  system.
- CPU controls are capability-gated. An unsupported boost switch or clock
  ceiling must not be presented as usable.
- A missing ROG Control command, malformed response, stale profile name,
  hardware apply failure, network error, invalid release asset, or Decky
  service restart failure must produce a visible error and leave the UI in a
  recoverable state.
- Concurrent changes from the desktop app and Decky must preserve unrelated
  config fields by using the existing fresh-load/update helpers.
- Release metadata and downloaded assets are untrusted input. Restrict
  downloads to the expected repository and asset format, verify integrity,
  and reject path traversal or unexpected package contents.

## Validation

Implementation validation should cover command response and mutation
semantics, capability gating, config preservation, plugin packaging and
manifest metadata, install-state detection, release selection and integrity
checks, and clear handling of unavailable releases and failed operations.
Hardware integration should be checked on a supported host with Decky in
Gamescope before claiming device-level operation.

## Out of scope

- Changing the underlying hardware controls or adding new CPU tuning features
  to ROG Control.
- Supporting systems where Decky and ROG Control run on different machines.
- Publishing a GitHub release as part of the initial implementation.
