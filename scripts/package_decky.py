#!/usr/bin/env python3
"""Build a versioned, install-ready ROG Control Decky plugin ZIP."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "decky"
PREFIX = "ROG-Control-Decky-v"


def build_package(output_dir, build=True):
    manifest = json.loads((PLUGIN / "package.json").read_text(encoding="utf-8"))
    version = manifest.get("version")
    if (manifest.get("name") != "rog-control-decky"
            or not isinstance(version, str)
            or not all(part.isdecimal() for part in version.split("."))):
        raise ValueError("Decky package.json must have its expected name and numeric version")
    if build:
        subprocess.run(["pnpm", "run", "build"], cwd=PLUGIN, check=True)
    dist = PLUGIN / "dist" / "index.js"
    if not dist.is_file():
        raise FileNotFoundError("Build the Decky frontend before packaging")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{PREFIX}{version}.zip"
    digest_path = output_dir / f"{archive_path.name}.sha256"
    with tempfile.TemporaryDirectory(prefix="rogcontrol-decky-package-") as temp:
        stage = Path(temp) / "ROG-Control"
        stage.mkdir()
        for relative in ("main.py", "plugin.json", "package.json", "README.md"):
            shutil.copy2(PLUGIN / relative, stage / relative)
        shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
        shutil.copytree(PLUGIN / "dist", stage / "dist")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            for source in sorted(path for path in stage.rglob("*") if path.is_file()):
                relative = source.relative_to(stage.parent).as_posix()
                info = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                                 compresslevel=9)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    digest_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    return archive_path, digest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=PLUGIN / "out")
    parser.add_argument("--no-build", action="store_true",
                        help="package an already-built frontend")
    args = parser.parse_args()
    archive, digest = build_package(args.output, build=not args.no_build)
    print(archive)
    print(digest)


if __name__ == "__main__":
    main()
