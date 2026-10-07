"""Opt-in internal-panel refresh switching in the logged-in desktop session.

KDE uses kscreen-doctor's JSON and changes just the internal output mode.
GNOME reads Mutter's typed GetCurrentState API and supplies the whole current
layout to gdctl. It never parses the human-readable gdctl tree or guesses a
mode. Mirroring and leased monitors are deliberately left alone.

Protocols: KDE libkscreen src/configserializer.cpp and src/doctor/doctor.cpp;
GNOME mutter tools/gdctl and data/dbus-interfaces/org.gnome.Mutter.DisplayConfig.xml.
No root helper, xrandr fallback, custom timings, or persistent display writes.
"""

from contextlib import contextmanager
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CONFIG_KEY = 'auto_display_refresh'
BATTERY_RATE_KEY = 'battery_display_refresh_hz'
STATE_PATH = os.path.expanduser('~/.local/state/rogcontrol/display-refresh.json')
TRANSFORMS = ('normal', '90', '180', '270', 'flipped', 'flipped-90',
              'flipped-180', 'flipped-270')
COLOR_MODES = {0: 'default', 1: 'bt2100', 2: 'sdr-native'}
RGB_RANGES = {1: 'auto', 2: 'full', 3: 'limited'}


def backend_name():
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower().split(':')
    if any('gnome' in name for name in desktop):
        return 'gnome'
    if 'kde' in desktop:
        return 'kde'
    return None


def availability():
    """Cheap dependency check, not a promise that a panel has multiple modes."""
    backend = backend_name()
    command = {'gnome': 'gdctl', 'kde': 'kscreen-doctor'}.get(backend)
    if command is None:
        return False, 'Requires GNOME with gdctl or KDE Plasma with kscreen-doctor.'
    if not shutil.which(command):
        return False, f'{command} is not installed for this desktop session.'
    if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        return False, 'No graphical session bus is available.'
    return True, ('GNOME' if backend == 'gnome' else 'KDE Plasma') + (
        ': supported modes at the current resolution; mirrored panels are skipped.')


def _token(value):
    value = str(value)
    # KDE uses dots as command delimiters; disallow both delimiters and options.
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_:-]*', value):
        raise ValueError('Unsupported display identifier')
    return value


def _rate(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid display refresh rate')
    return value


def _internal(name):
    return bool(re.fullmatch(r'(?:eDP|LVDS|DSI)(?:-?\d+)+', name, re.I))


def _kde_plan(state, power_source, requested_rate=None):
    """Plan a mode-only update. None means no safe change is needed."""
    if requested_rate is None and power_source not in ('ac', 'usbc', 'battery'):
        return None, []
    outputs = state['outputs']
    commands = []
    panels = []
    for output in outputs:
        if not (_internal(output['name']) and output.get('connected') is True
                and output.get('enabled') is True):
            continue
        # A replicated output (in either direction) needs coordinated modes.
        if output.get('replicationSource', 0) or any(
                other.get('replicationSource', 0) == output['id'] for other in outputs):
            continue
        current = next((mode for mode in output['modes']
                        if mode['id'] == output.get('currentModeId')), None)
        if current is None:
            continue
        modes = [mode for mode in output['modes'] if mode['size'] == current['size']]
        for mode in modes:
            _rate(mode['refreshRate'])
            _token(mode['id'])
        panels.append(({_rate(mode['refreshRate']) for mode in modes},
                       _rate(current['refreshRate']), 'kde:' + output['name']))
        target = _choose_mode(modes, lambda mode: mode['refreshRate'],
                              power_source, requested_rate)
        if _rate(target['refreshRate']) == _rate(current['refreshRate']):
            continue
        commands.append(f"output.{_token(output['id'])}.mode.{_token(target['id'])}")
    return (['kscreen-doctor', *commands] if commands else None), panels


def _gnome_plan(state, power_source, requested_rate=None):
    """Reproduce the live layout, changing only eligible built-in mode IDs."""
    if requested_rate is None and power_source not in ('ac', 'usbc', 'battery'):
        return None, []
    _, monitors, logical, properties = state
    layout = {1: 'logical', 2: 'physical'}.get(properties.get('layout-mode', 1))
    if layout is None:
        raise ValueError('Unsupported GNOME layout mode')
    by_spec = {tuple(monitor[0]): monitor for monitor in monitors}
    if any(monitor[2].get('is-for-lease') for monitor in monitors):
        raise ValueError('Leased display configurations are not supported')
    command = ['gdctl', 'set', '--layout-mode', layout]
    changed = False
    panels = []
    for x, y, scale, transform, primary, specs, _ in logical:
        if not 0 <= transform < len(TRANSFORMS) or not math.isfinite(scale) or scale <= 0:
            raise ValueError('Invalid logical display configuration')
        # gdctl releases disagree with Mutter's numeric mapping for these
        # two flipped rotations. Reconstructing either could rotate an external
        # display while only the internal panel's refresh was meant to change.
        if transform in (6, 7):
            raise ValueError('Cannot preserve this GNOME flipped transform safely with gdctl')
        command += ['--logical-monitor', '--x', str(x), '--y', str(y),
                    '--scale', str(scale), '--transform', TRANSFORMS[transform]]
        if primary:
            command += ['--primary']
        for spec in specs:
            connector = spec[0]
            _, modes, props = by_spec[tuple(spec)]
            # gdctl has no underscan option; Mutter treats an omitted setting
            # as false when applying the full configuration.
            if props.get('is-underscanning') is True:
                raise ValueError('Cannot preserve active monitor underscanning with gdctl')
            current = [mode for mode in modes if mode[6].get('is-current')]
            if len(current) != 1:
                raise ValueError('Cannot preserve an active monitor without its current mode')
            current = current[0]
            target = current
            if len(specs) == 1 and (props.get('is-builtin') is True or _internal(connector)):
                candidates = [mode for mode in modes if mode[1:3] == current[1:3]
                              and any(abs(s - scale) < 0.00001 for s in mode[5])
                              and mode[6].get('is-interlaced', False)
                              == current[6].get('is-interlaced', False)
                              and mode[6].get('refresh-rate-mode', 'fixed')
                              == current[6].get('refresh-rate-mode', 'fixed')]
                for mode in candidates:
                    _rate(mode[3])
                panels.append(({_rate(mode[3]) for mode in candidates}, _rate(current[3]),
                               'gnome:' + json.dumps(list(spec))))
                if candidates:
                    target = _choose_mode(candidates, lambda mode: mode[3],
                                          power_source, requested_rate)
                elif requested_rate is not None:
                    raise ValueError('No safe refresh rates for an internal display')
                if candidates:
                    if _rate(target[3]) == _rate(current[3]):
                        target = current
                changed |= target[0] != current[0]
            # Separate argv values; no shell. Reject option-like strings anyway.
            if not connector or connector.startswith('-') or not target[0] or target[0].startswith('-'):
                raise ValueError('Unsupported GNOME display identifier')
            command += ['--monitor', connector, '--mode', target[0]]
            for prop, choices in [('color-mode', COLOR_MODES), ('rgb-range', RGB_RANGES)]:
                if prop in props:
                    if props[prop] not in choices:
                        raise ValueError(f'Unsupported GNOME {prop}')
                    command += ['--' + prop, choices[props[prop]]]
    return (command if changed else None), panels


def _choose_mode(modes, rate_of, power_source, requested_rate):
    if requested_rate is not None:
        requested_rate = _rate(requested_rate)
        target = next((mode for mode in modes if _rate(rate_of(mode)) == requested_rate), None)
        if target is None:
            raise ValueError(f'{requested_rate:g} Hz is not supported by every eligible internal display')
        return target
    return (min if power_source == 'battery' else max)(modes, key=lambda mode: _rate(rate_of(mode)))


def _common_rates(panels):
    if not panels:
        raise ValueError('No eligible internal display; disabled or mirrored panels are skipped.')
    rates = set.intersection(*(panel[0] for panel in panels))
    if not rates:
        raise ValueError('No common safe refresh rates for the internal displays.')
    current = {panel[1] for panel in panels}
    return sorted(rates), next(iter(current)) if len(current) == 1 else None


def kde_command(state, power_source, requested_rate=None):
    """Plan a mode-only update; optionally require an exact supported rate."""
    command, panels = _kde_plan(state, power_source, requested_rate)
    if requested_rate is not None:
        _common_rates(panels)
    return command


def gnome_command(state, power_source, requested_rate=None):
    """Preserve the live layout; optionally require an exact supported rate."""
    command, panels = _gnome_plan(state, power_source, requested_rate)
    if requested_rate is not None:
        _common_rates(panels)
    return command


def _current_plan(requested_rate=None):
    supported, reason = availability()
    if not supported:
        raise ValueError(reason)
    if backend_name() == 'kde':
        return _kde_plan(json.loads(_run(['kscreen-doctor', '--json'])), 'ac', requested_rate)
    return _gnome_plan(_gnome_state(), 'ac', requested_rate)


def get_refresh_rates():
    """Return (sorted supported Hz values, current Hz or None) from live state.

    Only rates shared by eligible internal panels at their current resolution
    and layout are offered. Raises ValueError for unavailable or unsafe layouts.
    This query performs no display changes.
    """
    try:
        _, panels = _current_plan()
        return _common_rates(panels)
    except Exception as error:
        raise ValueError(f'Cannot read display refresh rates: {error}') from error


def set_refresh_rate(rate):
    """Apply an exact supported Hz value using fresh state; return (ok, message)."""
    try:
        rate = _rate(rate)
        command, panels = _current_plan(rate)
        _common_rates(panels)
        if command is not None:
            _run(command)
        return True, f'Internal display refresh set to {rate:g} Hz.'
    except Exception as error:
        return False, f'Display refresh unchanged: {error}'


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=True,
                          timeout=10).stdout


def _gnome_state():
    from gi.repository import Gio
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return connection.call_sync(
        'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', None, None,
        Gio.DBusCallFlags.NO_AUTO_START, 5000, None).unpack()


@contextmanager
def _locked_state(state_path):
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    with open(state_path + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read_restore(state_path):
    try:
        with open(state_path) as source:
            state = json.load(source)
    except FileNotFoundError:
        return None
    if (not isinstance(state, dict) or not isinstance(state.get('panels'), list)
            or not state['panels'] or not all(isinstance(item, str) for item in state['panels'])):
        raise ValueError('Invalid saved display refresh restore state')
    _rate(state.get('restore_hz'))
    return state


def _save_restore(state, state_path):
    fd, temporary = tempfile.mkstemp(dir=str(Path(state_path).parent))
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump(state, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, state_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def tick(cfg, power_source, state_path=None):
    """Use the selected battery rate, then restore the pre-battery rate.

    The original rate is saved before a display write, survives restarts, and
    remains pending until restoration succeeds. AC without a pending restore
    does nothing. Disabled mode restores a pending change; unknown power waits.
    """
    if power_source not in ('ac', 'usbc', 'battery'):
        return 'Power source unknown; display unchanged.'
    enabled = cfg.get(CONFIG_KEY) is True
    state_path = str(state_path or STATE_PATH)
    if not enabled and not Path(state_path).exists():
        return 'Automatic display refresh is off.'
    try:
        with _locked_state(state_path):
            pending = _read_restore(state_path)
            restoring = not enabled or power_source != 'battery'
            if restoring and pending is None:
                return 'Automatic display refresh is off.' if not enabled else 'Current display refresh preserved.'
            if restoring:
                requested = _rate(pending['restore_hz'])
            else:
                requested = cfg.get(BATTERY_RATE_KEY)
                if requested is None:
                    requested = get_refresh_rates()[0][0]
                requested = _rate(requested)
            # The command and snapshot come from the same fresh desktop state.
            command, panels = _current_plan(requested)
            rates, current = _common_rates(panels)
            identities = sorted(panel[2] for panel in panels)
            if pending is not None and identities != pending['panels']:
                raise ValueError('Internal displays changed; waiting to restore the original panels')
            if current is None and pending is None:
                raise ValueError('Internal displays have different refresh rates; cannot restore them safely')
            if not restoring and pending is None and command is not None:
                if current not in rates:
                    raise ValueError('Current internal refresh rate cannot be safely restored')
                _save_restore({'restore_hz': current, 'panels': identities}, state_path)
            if command is not None:
                _run(command)
            if restoring:
                Path(state_path).unlink(missing_ok=True)
                return f'Previous display refresh restored to {requested:g} Hz.'
            return f'Battery display refresh applied at {requested:g} Hz.'
    except Exception as error:
        message = f'Display refresh unchanged: {error}'
        _log_failure(message)
        return message


def _log_failure(message):
    from .hardware import log
    log(message, 'WARN', source='automation', dedupe_key='display-refresh')
