"""Persistent NVIDIA display-power option and boot-image transaction.

The helper runs a root-owned copy with isolated Python. Only this one registry
token is editable; existing options are preserved, and failed rebuilds restore
the original configuration and attempt to rebuild it again.
"""
import fcntl
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile

TOKEN = 'RmDisableDisplayGlitchPerfLimit'
DEFAULT_CONFIG = '/etc/modprobe.d/rogcontrol-nvidia-power.conf'
MIN_VERSION = (615, 71, 9)
OPTION = re.compile(r'''NVreg_RegistryDwords=(?:"([^"\n]*)"|'([^'\n]*)'|([^\s#]+))''')
VALUE = re.compile(r'(?<![\w])' + TOKEN + r'\s*=\s*(0[xX][0-9a-fA-F]+|[0-9]+)(?=\s*(?:;|$))')


def path(root, name):
    return Path(root) / name.lstrip('/')


def _read(filename):
    try:
        return filename.read_text()
    except FileNotFoundError:
        return ''


def boot_commands(root='/', which=shutil.which):
    if path(root, '/run/ostree-booted').exists():
        raise ValueError('Boot-image changes on atomic systems are not supported by this toggle.')
    if path(root, '/etc/default/limine').exists():
        names = ('limine-update', 'limine-enroll-config')
        if not all(which(name) for name in names):
            raise ValueError('Limine update and enrollment tools are required.')
        return [[which(name)] for name in names]
    if list(path(root, '/etc/mkinitcpio.d').glob('*.preset')) and which('mkinitcpio'):
        return [[which('mkinitcpio'), '-P']]
    if path(root, '/etc/debian_version').exists() and which('update-initramfs'):
        return [[which('update-initramfs'), '-u', '-k', 'all']]
    if which('dracut'):
        return [[which('dracut'), '--regenerate-all', '--force']]
    raise ValueError('No supported boot-image builder found; configuration was not changed.')


def configuration(root='/'):
    """Resolve modprobe precedence and one editable RegistryDwords assignment."""
    if 'nvidia.NVreg_RegistryDwords=' in _read(path(root, '/proc/cmdline')):
        raise ValueError('The kernel command line sets RegistryDwords; remove that override first.')
    files = {}
    for directory in ('/etc/modprobe.d', '/run/modprobe.d', '/usr/local/lib/modprobe.d',
                      '/usr/lib/modprobe.d', '/lib/modprobe.d'):
        for filename in sorted(path(root, directory).glob('*.conf')):
            files.setdefault(filename.name, filename)
    declarations = []
    for filename in files.values():
        text = _read(filename)
        offset = 0
        for line in text.splitlines(keepends=True):
            if re.match(r'^\s*options\s+nvidia\s', line):
                # Multi-line declarations need a dedicated parser, not guesses.
                if line.rstrip().endswith('\\'):
                    raise ValueError(f'Multi-line NVIDIA options in {filename}; consolidate them before using this toggle.')
                args = shlex.split(line, comments=True)
                if any(a.startswith('NVreg_RegistryDwordsPerDevice=') and TOKEN in a for a in args):
                    raise ValueError(f'A per-device override for {TOKEN} exists in {filename}.')
                values = [a.split('=', 1)[1] for a in args if a.startswith('NVreg_RegistryDwords=')]
                if values:
                    match = OPTION.search(line)
                    if len(values) != 1 or match is None:
                        raise ValueError(f'Ambiguous RegistryDwords assignment in {filename}.')
                    captured = next(v for v in match.groups() if v is not None)
                    if captured != values[0] or any(c in values[0] for c in ('"', '\\', '\n')):
                        raise ValueError(f'Unsupported RegistryDwords quoting in {filename}.')
                    declarations.append((filename, text, offset + match.start(), offset + match.end(), values[0]))
            offset += len(line)
    if len(declarations) > 1:
        raise ValueError('Multiple RegistryDwords assignments found; consolidate them before using this toggle.')
    if declarations:
        result = declarations[0]
        if result[0].parent != path(root, '/etc/modprobe.d') or result[0].is_symlink():
            raise ValueError('RegistryDwords is managed outside a regular /etc/modprobe.d file; change it there first.')
        return result
    filename = path(root, DEFAULT_CONFIG)
    if filename.is_symlink():
        raise ValueError('The NVIDIA power configuration is a symlink; refusing to replace it.')
    text = _read(filename)
    return filename, text, len(text), len(text), ''


def registry_enabled(value):
    matches = list(VALUE.finditer(value))
    if TOKEN in value and len(matches) != 1:
        raise ValueError(f'Unsupported or duplicate {TOKEN} value.')
    if not matches:
        return False
    number = matches[0].group(1)
    number = int(number, 16 if number.lower().startswith('0x') else 10)
    if number not in (0, 1):
        raise ValueError(f'{TOKEN} must be 0 or 1.')
    return bool(number)


def driver_version(root='/'):
    value = _read(path(root, '/sys/module/nvidia/version')).strip()
    if not value:
        try:
            result = subprocess.run(['modinfo', '-F', 'version', 'nvidia'],
                                    capture_output=True, text=True, timeout=5)
            value = result.stdout.strip() if result.returncode == 0 else ''
        except (OSError, subprocess.TimeoutExpired):
            pass
    return value


def read_state(root='/', which=shutil.which):
    state = dict(available=False, supported=False, enabled=False, live=None,
                 pending=False, message='', config='')
    try:
        version = driver_version(root)
        state['available'] = bool(version)
        parts = tuple(int(p) for p in version.split('.') if p.isdigit())
        if parts < MIN_VERSION:
            raise ValueError('Requires NVIDIA driver 615.71.09 or newer.')
        filename, _, _, _, registry = configuration(root)
        state['config'] = str(filename)
        state['enabled'] = registry_enabled(registry)
        params = _read(path(root, '/proc/driver/nvidia/params'))
        live = re.search(r'^RegistryDwords:\s*"([^"]*)"', params, re.M)
        if live:
            state['live'] = registry_enabled(live.group(1))
            state['pending'] = state['live'] != state['enabled']
        commands = boot_commands(root, which)
        state['builder'] = Path(commands[0][0]).name
        state['supported'] = True
        state['message'] = ('Reboot required to apply the saved setting.' if state['pending'] else
                            'Running driver state unavailable; changes take effect after reboot.' if state['live'] is None else
                            'Saved setting matches the running driver.')
    except (ValueError, OSError) as error:
        state['message'] = str(error)
    return state


def _atomic_write(filename, text, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix='.rogcontrol-nvidia-', dir=filename.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _rebuild(commands):
    for command in commands:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            # Builders spawn children. Stop the whole group before rollback;
            # a surviving compressor must not race the recovery rebuild.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            raise
        if process.returncode:
            raise RuntimeError(f'{Path(command[0]).name} failed: {stderr or stdout}')


def apply(enabled, root='/', which=shutil.which):
    if type(enabled) is not bool:
        raise ValueError('The NVIDIA display-power option takes only on or off.')
    state = read_state(root, which)
    if not state['supported']:
        raise ValueError(state['message'])
    commands = boot_commands(root, which)
    filename, original, start, end, registry = configuration(root)
    registry_enabled(registry)  # Revalidate the freshly read assignment.
    if filename.exists() and filename.stat().st_uid != os.geteuid():
        raise ValueError('NVIDIA configuration must be owned by the helper user (root).')
    wanted = str(int(enabled))
    if TOKEN in registry:
        registry = VALUE.sub(lambda match: match.group(0)[:match.start(1) - match.start()] + wanted, registry)
    else:
        registry = registry.rstrip(';') + (';' if registry else '') + TOKEN + '=' + wanted
    replacement = 'NVreg_RegistryDwords="' + registry + '"'
    if start == end:
        replacement = ('\n' if original and not original.endswith('\n') else '') + 'options nvidia ' + replacement + '\n'
    updated = original[:start] + replacement + original[end:]
    filename.parent.mkdir(parents=True, exist_ok=True)
    existed = filename.exists()
    mode = filename.stat().st_mode & 0o777 if existed else 0o644
    backup = None
    if existed:
        fd, backup = tempfile.mkstemp(prefix=filename.name + '.rogcontrol-backup-', dir=filename.parent)
        with os.fdopen(fd, 'w') as stream:
            stream.write(original)
    _atomic_write(filename, updated, mode)
    try:
        _rebuild(commands)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        try:
            if existed:
                _atomic_write(filename, original, mode)
            else:
                filename.unlink()
        except OSError as restore_error:
            raise RuntimeError(f'{error}\nCould not restore configuration: {restore_error}. Repair boot configuration before rebooting. Backup: {backup or "original configuration had no file"}') from error
        try:
            _rebuild(commands)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as rollback:
            raise RuntimeError(f'{error}\nConfiguration restored, but recovery rebuild failed: {rollback}. Repair boot images before rebooting. Backup: {backup or "original configuration had no file"}') from error
        raise RuntimeError(f'{error}\nOriginal configuration restored and boot images rebuilt.') from error
    return 'NVIDIA display-power setting saved and boot images rebuilt. Reboot to apply.'


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('0', '1'):
        print('nvidia-display-power takes exactly 0 or 1', file=sys.stderr)
        return 1
    if os.geteuid() != 0:
        print('Run through the installed privileged helper.', file=sys.stderr)
        return 1
    try:
        fd = os.open('/run/lock/rogcontrol-nvidia-boot.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(apply(sys.argv[1] == '1'))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
