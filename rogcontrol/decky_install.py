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


def launch_loader_restart(status_path):
    """Open a terminal for the sudo prompt needed to restart Decky Loader."""
    status = shlex.quote(str(status_path))
    command = (
        f"printf 'running:%s\\n' \"$$\" > {status}; "
        "sudo systemctl restart plugin_loader.service; result=$?; "
        f"printf '%s\\n' \"$result\" > {status}; "
        "echo; if [ \"$result\" -eq 0 ]; then "
        "echo 'Decky Loader restarted.'; else "
        "echo 'Decky Loader restart failed.'; fi; "
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
