"""Release lookup and verified download helpers for the Decky plugin."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "D0minatorX/rogcontrol"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_PREFIX = "ROG-Control-Decky-v"
USER_AGENT = "rogcontrol-decky-update"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 2048


def _version_tuple(value):
    core = str(value or "").lstrip("vV").split("-", 1)[0]
    parts = core.split(".")
    if not parts or not all(part.isdecimal() for part in parts):
        return ()
    return tuple(int(part) for part in parts)


def _github_release_url(url, filename):
    if not isinstance(url, str):
        return False
    match = re.fullmatch(
        rf"https://github\.com/{re.escape(REPOSITORY)}/releases/download/"
        rf"[^/]+/{re.escape(filename)}", url)
    return match is not None


def _read_url(url, timeout, max_bytes=None):
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if max_bytes is not None:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("release response exceeds the size limit")
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("release response exceeds the size limit")
            return data
        return response.read()


def check_for_update(installed_version=None, timeout=10):
    """Find the latest release only when it carries a verifiable plugin ZIP."""
    try:
        release = json.loads(_read_url(RELEASE_API, timeout, 5 * 1024 * 1024)
                             .decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return {"installed": installed_version, "available": False,
                "version": None, "download_url": None, "sha256_url": None,
                "error": str(error)}
    if not isinstance(release, dict):
        return {"installed": installed_version, "available": False,
                "version": None, "download_url": None, "sha256_url": None,
                "error": "GitHub returned malformed release metadata"}

    candidates = {}
    for asset in release.get("assets", []):
        if not isinstance(asset, dict):
            continue
        asset_name = str(asset.get("name") or "")
        match = re.fullmatch(
            re.escape(ASSET_PREFIX) + r"(\d+(?:\.\d+)+)\.zip", asset_name)
        if match:
            candidates[match.group(1)] = asset
    if not candidates or release.get("draft"):
        return {"installed": installed_version, "available": False,
                "version": None, "download_url": None,
                "sha256_url": None, "error": None}
    version = max(candidates, key=_version_tuple)
    latest_tuple = _version_tuple(version)
    installed_tuple = _version_tuple(installed_version)
    if not latest_tuple or (installed_tuple and latest_tuple <= installed_tuple):
        return {"installed": installed_version, "available": False,
                "version": version, "download_url": None,
                "sha256_url": None, "error": None}

    zip_name = f"{ASSET_PREFIX}{version}.zip"
    digest_name = f"{zip_name}.sha256"
    assets = {asset.get("name"): asset.get("browser_download_url")
              for asset in release.get("assets", [])
              if isinstance(asset, dict)}
    download_url = assets.get(zip_name)
    sha256_url = assets.get(digest_name)
    if (not download_url or not sha256_url
            or not _github_release_url(download_url, zip_name)
            or not _github_release_url(sha256_url, digest_name)):
        return {"installed": installed_version, "available": False,
                "version": version, "download_url": None,
                "sha256_url": None, "error": None}
    return {"installed": installed_version, "available": True,
            "version": version, "download_url": download_url,
            "sha256_url": sha256_url, "error": None}


def validate_archive(archive_path, expected_version=None):
    """Reject unsafe or malformed packages before writing any plugin files."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError("plugin archive contains too many files")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise ValueError("plugin archive contains duplicate paths")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or ".." in path.parts
                        or "\\" in entry.filename or not path.parts
                        or path.parts[0] != "ROG-Control"):
                    raise ValueError("plugin archive has an unsafe path")
                mode = entry.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:
                    raise ValueError("plugin archive contains a symbolic link")
            required = {
                "ROG-Control/plugin.json",
                "ROG-Control/package.json",
                "ROG-Control/main.py",
                "ROG-Control/dist/index.js",
            }
            if not required.issubset(set(names)):
                raise ValueError("plugin archive is missing required files")
            manifest = json.loads(archive.read("ROG-Control/package.json"))
            if not isinstance(manifest, dict):
                raise ValueError("plugin package manifest is malformed")
            if expected_version and manifest.get("version") != expected_version:
                raise ValueError("plugin archive version does not match its release")
            if manifest.get("name") != "rog-control-decky":
                raise ValueError("plugin archive has an unexpected package name")
            plugin_manifest = json.loads(archive.read("ROG-Control/plugin.json"))
            if (not isinstance(plugin_manifest, dict)
                    or plugin_manifest.get("name") != "ROG Control"):
                raise ValueError("plugin archive has an unexpected Decky manifest")
            total_size = sum(entry.file_size for entry in entries)
            if total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("plugin archive expands beyond the allowed size")
            return manifest
    except zipfile.BadZipFile as error:
        raise ValueError("downloaded plugin archive is not a valid ZIP") from error


def download_and_stage(asset_url, sha256_url, version, timeout=120):
    """Download, hash-check and validate a release ZIP into a fresh directory."""
    name = f"{ASSET_PREFIX}{version}.zip"
    digest_name = f"{name}.sha256"
    if not _github_release_url(asset_url, name):
        raise ValueError("plugin download URL is outside the ROG Control releases")
    if not _github_release_url(sha256_url, digest_name):
        raise ValueError("plugin checksum URL is outside the ROG Control releases")

    deadline = time.monotonic() + timeout
    archive_data = _read_url(asset_url, timeout, MAX_ARCHIVE_BYTES)
    digest_text = _read_url(sha256_url,
                            max(1, deadline - time.monotonic()), 4096)
    digest_text = digest_text.decode("ascii").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64})(?:\s+.+)?", digest_text)
    if not match:
        raise ValueError("plugin release checksum is malformed")
    actual_digest = hashlib.sha256(archive_data).hexdigest()
    if actual_digest.lower() != match.group(1).lower():
        raise ValueError("plugin release checksum does not match the download")

    directory = tempfile.mkdtemp(prefix="rogcontrol-decky-")
    path = Path(directory) / name
    try:
        path.write_bytes(archive_data)
        validate_archive(path, expected_version=version)
        return str(path)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def cleanup_staged(archive_path):
    """Remove only the private temporary directory created by this module."""
    archive = Path(archive_path)
    parent = archive.parent
    if (parent.name.startswith("rogcontrol-decky-")
            and archive.name.startswith(ASSET_PREFIX)
            and archive.suffix == ".zip"):
        shutil.rmtree(parent, ignore_errors=True)
