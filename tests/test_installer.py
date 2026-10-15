"""Regression tests for installer package routing and saved startup preferences."""

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).parents[1] / "rogcontrol" / "install.sh"


class InstallerTests(unittest.TestCase):
    def run_service_setup(self, enabled, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / 'commands'
            commands.mkdir()
            for name in ('systemctl', 'pkill'):
                executable = commands / name
                executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\n')
                executable.chmod(0o755)
            package = root / '.local/lib/rogcontrol'
            package.mkdir(parents=True)
            (package / '__init__.py').touch()
            (package / 'startup.py').write_text('''
import json, os
def is_enabled():
    return json.loads(os.environ['STARTUP_ENABLED'])
def configure_services(enabled, *, start_now=False):
    with open(os.environ['STARTUP_LOG'], 'w') as out:
        json.dump([enabled, start_now], out)
    if json.loads(os.environ['STARTUP_FAIL']):
        raise OSError('startup setup failed')
''')
            source = INSTALLER.read_text()
            section = source.split('# -------------------------------------------------------------- services ----')[1]
            section = section.split('# Leftovers from an older build')[0]
            shell = 'set -eu\nstep() { :; }\nsay() { echo "$*"; }\nwarn() { echo "WARNING: $*"; }\n'
            shell += 'ENFORCER_WAS_RUNNING=1\n' + section
            env = dict(os.environ, HOME=str(root), PATH=str(commands) + ':' + os.environ['PATH'],
                       SCRIPT_DIR=str(INSTALLER.parent), STARTUP_ENABLED=json.dumps(enabled),
                       STARTUP_FAIL=json.dumps(fail), STARTUP_LOG=str(root / 'startup.json'),
                       COMMAND_LOG=str(root / 'commands.log'))
            result = subprocess.run(['bash', '-c', shell], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            log_path = root / 'startup.json'
            calls = json.loads(log_path.read_text()) if log_path.exists() else None
            return calls, result.stdout, (root / 'commands.log').read_text()

    def test_service_setup_preserves_enabled_and_disabled_preference(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                calls, output, commands = self.run_service_setup(enabled)
                self.assertEqual(calls, [enabled, True])
                self.assertNotIn('WARNING:', output)
                self.assertNotIn(' enable ', commands)
                self.assertNotIn(' restart ', commands)
                if not enabled:
                    self.assertNotIn('is-active', commands)

    def test_service_configuration_failure_is_reported(self):
        calls, output, _ = self.run_service_setup(False, fail=True)
        self.assertEqual(calls, [False, True])
        self.assertIn('WARNING:', output)

    def test_missing_qt_downloads_packages_and_rechecks_runtime(self):
        source = INSTALLER.read_text()
        section = source.split('if [ "$QT_OK" = 0 ] && [ "$ATOMIC" = 1 ]; then', 1)[1]
        section = 'if [ "$QT_OK" = 0 ] && [ "$ATOMIC" = 1 ]; then' + section.split('# ------------------------------------------------------------ deps: repo', 1)[0]
        for manager in ('pacman', 'apt', 'dnf'):
            shell = 'set -eu\nstep() { :; }\ndie() { echo "$*"; exit 1; }\n'
            shell += 'sudo() { echo "INSTALL $*"; }\ncheck_qt_runtime() { echo RECHECK; }\n'
            shell += f'QT_OK=0; ATOMIC=0; PM={manager}; QT_PACKAGES=(qt-test-package)\n'
            result = subprocess.run(['bash', '-c', shell + section], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('qt-test-package', result.stdout)
            self.assertIn('RECHECK', result.stdout)

    def test_optional_packages_continue_after_unavailable_package(self):
        source = INSTALLER.read_text()
        self.assertIn('install_optional_packages() {', source)
        function = source.split('install_optional_packages() {', 1)[1].split('\n}\n', 1)[0]
        for manager in ('pacman', 'apt', 'dnf'):
            shell = "set -eu\nwarn() { echo WARN \"$*\"; }\nsay() { echo OK \"$*\"; }\n"
            shell += 'sudo() { echo "CALL $*"; case "$*" in *missing-package*) return 1;; esac; }\n'
            shell += 'install_optional_packages() {' + function + '\n}\n'
            shell += f'PM={manager}\ninstall_optional_packages missing-package good-package\n'
            result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('good-package', result.stdout)
            self.assertIn('WARN', result.stdout)
            self.assertNotIn('OK Installed missing-package', result.stdout)

    def test_atomic_hosts_never_route_packages_through_dnf(self):
        source = INSTALLER.read_text()
        self.assertIn('if [ "$ATOMIC" = 1 ]; then PM_HOST=none; fi', source)

    def test_atomic_dependency_prompt_identifies_rpm_ostree_and_reboot(self):
        source = INSTALLER.read_text()
        self.assertIn('This is an atomic/ostree system:', source)
        self.assertIn("rpm-ostree", source)
        self.assertIn("reboot", source.lower())


if __name__ == "__main__":
    unittest.main()
