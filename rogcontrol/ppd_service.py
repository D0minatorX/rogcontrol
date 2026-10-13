"""PPD service status and user-visible, interactive system transactions."""
import os
import shutil
import subprocess

from .hardware import parse_service_state
from .updater import UPDATE_TERMINALS

SERVICE = 'power-profiles-daemon.service'
PACKAGE = 'power-profiles-daemon'


def _query(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None


def package_manager():
    # Do not use dnf on an immutable host even if it is installed.
    if os.path.exists('/run/ostree-booted'):
        return 'rpm-ostree' if shutil.which('rpm-ostree') else None
    for name in ('pacman', 'apt-get', 'dnf', 'zypper'):
        if shutil.which(name):
            return name
    return None


def _package_installed(package):
    queries = (('rpm', ['rpm', '-q', package]),
               ('dpkg-query', ['dpkg-query', '-W', '-f=${Status}', package]),
               ('pacman', ['pacman', '-Q', package]))
    for tool, argv in queries:
        if not shutil.which(tool):
            continue
        result = _query(argv)
        if result and result.returncode == 0:
            if tool != 'dpkg-query' or result.stdout.strip() == 'install ok installed':
                return True
    return False


def tuned_ppd_installed():
    return bool(shutil.which('tuned-ppd')) or _package_installed('tuned-ppd')


def read_state():
    def ask(action):
        result = _query(['systemctl', action, SERVICE])
        return result.stdout if result else ''
    state = parse_service_state(
        unit_files=ask('list-unit-files'), is_active=ask('is-active'),
        is_enabled=ask('is-enabled'),
        binary_found=bool(shutil.which(PACKAGE)), service=SERVICE)
    state['tuned_ppd'] = tuned_ppd_installed()
    state['manager'] = package_manager() or ''
    state['masked'] = state['raw_enabled'] in ('masked', 'masked-runtime')
    # A mask can survive package removal. Do not let it hide the Install button.
    if state['masked']:
        state['installed'] = _package_installed(PACKAGE)
        state['has_unit'] = state['installed']
    return state


def action_command(action):
    """Only fixed package/unit names are allowed; package prompts stay enabled."""
    if action not in ('enable', 'disable', 'install', 'uninstall'):
        raise ValueError('Unsupported PPD action')
    if tuned_ppd_installed():
        raise ValueError('tuned-ppd provides OS power modes on this system. It was left unchanged.')
    if action == 'enable':
        return f'sudo systemctl unmask {SERVICE} && sudo systemctl enable --now {SERVICE}'
    if action == 'disable':
        # Merely stopping/disabling an activatable D-Bus service is not enough.
        return f'sudo systemctl disable --now {SERVICE} && sudo systemctl mask {SERVICE}'
    manager = package_manager()
    commands = {
        'pacman': ('sudo pacman -S', 'sudo pacman -R'),
        'apt-get': ('sudo apt-get update && sudo apt-get install', 'sudo apt-get remove'),
        'dnf': ('sudo dnf install', 'sudo dnf remove'),
        'zypper': ('sudo zypper install', 'sudo zypper remove'),
        'rpm-ostree': ('sudo rpm-ostree install', 'sudo rpm-ostree uninstall'),
    }
    if manager not in commands:
        raise ValueError('No supported system package manager found.')
    command = commands[manager][0 if action == 'install' else 1] + ' ' + PACKAGE
    if manager == 'rpm-ostree':
        command += " && printf '\\nReboot to activate the package change. Base-image packages may only be disabled.\\n'"
    return command


def launch_action(action):
    """Open the transaction terminal, not a claim that the transaction succeeded."""
    try:
        command = action_command(action)
    except ValueError as error:
        return False, str(error)
    script = (
        command + '; result=$?; '
        'if [ "$result" -eq 0 ]; then '
        "printf '\\nPPD operation completed. Return to Services to check its state.\\n'; "
        'else printf "\\nPPD operation failed (exit %s). See output above.\\n" "$result"; fi; '
        "read -r -p 'Press Enter to close... '")
    for name, prefix in UPDATE_TERMINALS:
        if not shutil.which(name):
            continue
        try:
            process = subprocess.Popen([*prefix, 'bash', '-c', script], start_new_session=True)
            try:
                if process.wait(timeout=0.3) != 0:
                    continue
            except subprocess.TimeoutExpired:
                pass
            return True, 'Terminal opened. Complete the prompts there; service status refreshes automatically.'
        except OSError:
            continue
    return False, 'No terminal could be opened. Run: ' + command
