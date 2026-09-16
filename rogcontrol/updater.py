"""Release checks, download staging, and installer launching (no GTK)."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from . import APP_VERSION

# --- checking for and applying an update -------------------------------------
#
# install.sh needs sudo per step (each call can prompt) and expects to run
# from beside the rest of a release checkout -- the helper binary, the
# .service files, the icons -- not just the python package this app imports
# from. So "update" means: ask GitHub what the latest tagged release is,
# download the zip a human attached to that release, and open a terminal
# running the install.sh inside it. A subprocess with no TTY cannot supply
# the sudo password install.sh's own steps ask for, which is why this never
# tries to run install.sh directly.

GITHUB_REPO = "D0minatorX/rogcontrol"
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
# The naming convention every release asset has followed so far (see the
# Rog-Control-V*.zip files this repo ships). Matched by prefix rather than
# taking whichever .zip happens to be first, so a release with more than one
# attachment (a source archive GitHub adds automatically, say) cannot grab
# the wrong one.
UPDATE_ASSET_PREFIX = "Rog-Control-V"
UPDATE_USER_AGENT = "rogcontrol-update-check"


def _version_tuple(version):
    """"v1.0.0.9" (or "1.0.0.9") -> (1, 0, 0, 9), for a tuple comparison.

    Ignore build suffixes such as "-test.1" and compare the numeric release
    components. Stop at an unexpected component instead of raising."""
    parts = []
    for part in version.lstrip("vV").split("-", 1)[0].split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


def check_for_update(timeout=10):
    """Ask GitHub for the latest release and compare it to APP_VERSION.

    Returns a dict -- ``{"available", "version", "download_url", "error"}``
    -- rather than raising: this runs on a worker thread with exactly one
    caller, which always wants something to show the user, not an exception
    to catch. ``error`` is set only for a request that failed outright; a
    release with nothing newer, or newer but with no matching asset, is not
    an error."""
    try:
        request = urllib.request.Request(
            GITHUB_LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": UPDATE_USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return {"available": False, "version": None, "download_url": None,
                "error": str(e)}
    tag = data.get("tag_name") or ""
    latest = _version_tuple(tag)
    if not latest or latest <= _version_tuple(APP_VERSION):
        return {"available": False, "version": None, "download_url": None,
                "error": None}
    download_url = None
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if name.startswith(UPDATE_ASSET_PREFIX) and name.endswith(".zip"):
            download_url = asset.get("browser_download_url")
            break
    return {"available": True, "version": tag.lstrip("vV"),
            "download_url": download_url, "error": None}


def download_and_stage_update(download_url, timeout=120, progress=None,
                              cancelled=None):
    """Download the release zip and extract it. Returns the path to the
    extracted copy's install.sh.

    Raises on any failure -- network, a corrupt zip, an archive with no
    install.sh in it -- since the one caller turns every one of those
    straight into a toast; there is no partial-success case worth a tuple
    for.

    Extracted into a fresh temp directory every time, never reused, so a
    previous run's leftovers (or a partial extraction from one that failed
    halfway) can never mix into this one."""
    stage_dir = tempfile.mkdtemp(prefix="rogcontrol-update-")
    deadline = time.monotonic() + timeout

    def check_deadline():
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError("Update cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("The update download timed out; please retry")

    try:
        zip_path = os.path.join(stage_dir, "update.zip")
        request = urllib.request.Request(
            download_url, headers={"User-Agent": UPDATE_USER_AGENT})
        with urllib.request.urlopen(request, timeout=min(timeout, 10)) as response, \
                open(zip_path, "wb") as f:
            total = int(response.headers.get("Content-Length", 0))
            received = 0
            while True:
                check_deadline()
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > 128 * 1024 * 1024:
                    raise ValueError("Update archive exceeds 128 MiB")
                f.write(chunk)
                if progress is not None:
                    progress(received, total)
            if total and received != total:
                raise ValueError("Incomplete update download; please retry")
        check_deadline()
        with zipfile.ZipFile(zip_path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 512 * 1024 * 1024:
                raise ValueError("Extracted update exceeds 512 MiB")
            for info in archive.infolist():
                check_deadline()
                archive.extract(info, stage_dir)
        check_deadline()
        for root, _dirs, files in os.walk(stage_dir):
            if "install.sh" in files:
                return os.path.join(root, "install.sh")
        raise RuntimeError("the downloaded update has no install.sh in it")
    except BaseException:
        shutil.rmtree(stage_dir)
        raise


# Tried in this order -- GNOME first since it is the desktop install.sh's own
# detection treats as the common case, then KDE's terminal, then the other
# terminals people actually have installed instead of either DE's default
# (a GNOME session with only alacritty -- the case that surfaced this list
# was too short -- is not rare), then xterm as the one every X11/Wayland
# session with the base package set tends to carry.
# Each entry is (binary name, argv prefix before the command to run).
UPDATE_TERMINALS = (
    ("gnome-terminal", ["gnome-terminal", "--"]),
    ("konsole", ["konsole", "-e"]),
    ("alacritty", ["alacritty", "-e"]),
    ("kitty", ["kitty"]),
    ("foot", ["foot"]),
    ("wezterm", ["wezterm", "start", "--"]),
    ("tilix", ["tilix", "-e"]),
    ("terminator", ["terminator", "-x"]),
    ("xfce4-terminal", ["xfce4-terminal", "-x"]),
    ("ghostty", ["ghostty", "-e"]),
    ("xterm", ["xterm", "-e"]),
)


def launch_update_terminal(install_sh_path, status_path=None):
    """Open a terminal running the staged installer. Returns ``(ok, message)``.

    install.sh cannot simply be run as a subprocess: it calls sudo per step
    and each of those calls can stop to ask for a password, which needs a
    real TTY attached. Opening a terminal is what gives it one, the same way
    a user running it by hand would."""
    script_dir = os.path.dirname(install_sh_path)
    # ZipFile.extract does not preserve executable bits. Bash can run the
    # extracted installer without requiring chmod or trusting archive modes.
    inner_command = f"cd {shlex.quote(script_dir)} && bash ./install.sh; result=$?; "
    if status_path is not None:
        status = shlex.quote(status_path)
        inner_command = (
            f"printf 'running:%s\\n' \"$$\" > {status}; " + inner_command
            + f"printf '%s\\n' \"$result\" > {status}; ")
    inner_command += (
        "echo; if [ \"$result\" -eq 0 ]; then "
        "echo 'Update installed. Reopen ROG Control to use the new version.'; "
        "else echo \"Update failed (exit $result). See the output above.\"; fi; "
        "read -r -p 'Press Enter to close... '")
    for name, prefix in UPDATE_TERMINALS:
        if shutil.which(name) is None:
            continue
        try:
            process = subprocess.Popen(
                [*prefix, "bash", "-c", inner_command],
                start_new_session=True)
            try:
                if process.wait(timeout=0.3) != 0:
                    continue
            except subprocess.TimeoutExpired:
                pass
            return True, None
        except OSError:
            continue
    tried = ", ".join(name for name, _prefix in UPDATE_TERMINALS)
    return False, (f"No terminal emulator found (tried {tried}). Run it "
                   f"yourself: cd {shlex.quote(script_dir)} && bash ./install.sh")
