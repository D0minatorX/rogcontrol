"""Local Decky plugin detection, verified install, and loader restart."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from . import decky_release
from .updater import UPDATE_TERMINALS


PLUGIN_DIRECTORY = "ROG-Control"
PLUGIN_PACKAGE = "rog-control-decky"


def plugin_root(home=None):
    base = Path(home or Path.home())
    return base / "homebrew" / "plugins" / PLUGIN_DIRECTORY


def detect_installation(plugin_root_path=None):
    root = Path(plugin_root_path) if plugin_root_path else plugin_root()
    manifest_path = root / "package.json"
    if not root.is_dir():
        return {"installed": False, "version": None, "path": str(root),
                "error": None}
    try:
        package = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"installed": False, "version": None, "path": str(root),
                "error": f"invalid plugin installation: {error}"}
    if package.get("name") != PLUGIN_PACKAGE or not package.get("version"):
        return {"installed": False, "version": None, "path": str(root),
                "error": "plugin directory does not contain ROG Control Decky"}
    return {"installed": True, "version": str(package["version"]),
            "path": str(root), "error": None}


def install_archive(archive_path, expected_version=None, plugin_root_path=None):
    """Install a previously verified ZIP by replacing only the plugin folder."""
    manifest = decky_release.validate_archive(archive_path, expected_version)
    root = Path(plugin_root_path) if plugin_root_path else plugin_root()
    parent = root.parent
    if root.is_symlink():
        raise ValueError("plugin destination must not be a symbolic link")
    if root.exists():
        existing = detect_installation(root)
        if not existing.get("installed"):
            raise ValueError("plugin destination is not an existing ROG Control installation")
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".rogcontrol-stage-", dir=parent))
    staged_plugin = stage / PLUGIN_DIRECTORY
    backup = stage / f"{PLUGIN_DIRECTORY}.old"
    try:
        shutil.unpack_archive(str(archive_path), str(stage), "zip")
        if not staged_plugin.is_dir():
            raise ValueError("plugin archive did not contain its named root folder")
        decky_release.validate_archive(archive_path, manifest.get("version"))
        if root.exists():
            os.replace(root, backup)
        try:
            os.replace(staged_plugin, root)
        except Exception:
            if backup.exists() and not root.exists():
                os.replace(backup, root)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return {"ok": True, "version": manifest["version"], "path": str(root)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def launch_loader_restart(status_path, *, app_pid=None, cleanup_archive=None,
                          install_archive_path=None, expected_version=None):
    """Open a terminal to install the verified plugin and restart Decky Loader."""
    cleanup = ''
    if cleanup_archive is not None:
        archive = Path(cleanup_archive).resolve()
        temporary_root = Path(tempfile.gettempdir()).resolve()
        status_file = Path(status_path).resolve()
        if (archive.parent.parent != temporary_root
                or not archive.parent.name.startswith('rogcontrol-decky-')
                or not archive.name.startswith(decky_release.ASSET_PREFIX)
                or archive.suffix != '.zip'
                or status_file.parent != temporary_root
                or not status_file.name.startswith('rogcontrol-decky-')
                or app_pid is None):
            return {'ok': False, 'message': 'Refusing cleanup outside private Decky staging.'}
        # A detached completion watcher owns cleanup until the GUI consumes
        # status or exits, including exit after restart but before its poll.
        watcher = (
            f'while kill -0 {int(app_pid)} 2>/dev/null '
            f'&& [ -e {shlex.quote(str(status_file))} ]; do sleep 1; done; '
            f'if ! kill -0 {int(app_pid)} 2>/dev/null; then '
            f'rm -rf -- {shlex.quote(str(archive.parent))}; '
            f'rm -f -- {shlex.quote(str(status_file))}; fi')
        cleanup = f'nohup bash -c {shlex.quote(watcher)} </dev/null >/dev/null 2>&1 & '
    install_command = "sudo systemctl restart plugin_loader.service"
    if install_archive_path is not None:
        archive = Path(install_archive_path).resolve()
        if cleanup_archive is None or archive != Path(cleanup_archive).resolve():
            return {"ok": False, "message": "Refusing installation outside verified staging."}
        try:
            decky_release.validate_archive(archive, expected_version)
            extraction = archive.parent / "installer"
            if extraction.exists():
                shutil.rmtree(extraction)
            extraction.mkdir(mode=0o700)
            shutil.unpack_archive(str(archive), str(extraction), "zip")
            installer = extraction / PLUGIN_DIRECTORY / "install.sh"
            if not installer.is_file():
                raise ValueError("Plugin package has no standalone installer")
            install_command = "bash " + shlex.quote(str(installer))
        except (OSError, ValueError, shutil.ReadError) as error:
            return {"ok": False, "message": str(error)}
    status = shlex.quote(str(status_path))
    command = (
        f"printf 'running:%s\\n' \"$$\" > {status}; "
        f"{install_command}; result=$?; "
        f"printf '%s\\n' \"$result\" > {status}; "
        + cleanup + "echo; if [ \"$result\" -eq 0 ]; then "
        "echo 'Decky plugin operation completed.'; else "
        "echo 'Decky plugin operation failed.'; fi; "
        "read -r -p 'Press Enter to close... '")
    for name, prefix in UPDATE_TERMINALS:
        if shutil.which(name) is None:
            continue
        try:
            process = subprocess.Popen(
                [*prefix, "bash", "-c", command], start_new_session=True)
            try:
                if process.wait(timeout=0.3) != 0:
                    continue
            except subprocess.TimeoutExpired:
                pass
            return {"ok": True, "message": None}
        except OSError:
            continue
    return {"ok": False,
            "message": "No terminal emulator found for the Decky Loader restart."}
