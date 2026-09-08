#!/usr/bin/env python3
"""Validate published catalogs, checksums and derived package URLs."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://kodi-wulf.github.io/repository/"
USER_AGENT = "Kodi-Wulf repository validator/1.0"


@dataclass(frozen=True)
class Package:
    addon_id: str
    version: str
    relative_path: str


def catalog_directories(root: Path) -> list[Path]:
    directories: list[Path] = []
    for parent in (root / "plugins", root / "repository", root / "script"):
        if (parent / "addons.xml").is_file():
            directories.append(parent)
        if parent.is_dir():
            directories.extend(
                child
                for child in sorted(parent.iterdir(), key=lambda item: item.name.casefold())
                if child.is_dir() and (child / "addons.xml").is_file()
            )
    return directories


def url_for(base_url: str, relative_path: str) -> str:
    encoded = urllib.parse.quote(relative_path.replace("\\", "/"), safe="/._-~+")
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", encoded)


def fetch(url: str, *, method: str = "GET", timeout: float = 30.0) -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get("Content-Type", "")


def load_packages(root: Path, directories: list[Path]) -> tuple[list[Package], list[str]]:
    packages: list[Package] = []
    errors: list[str] = []

    for directory in directories:
        relative_directory = directory.relative_to(root).as_posix()
        try:
            document = ET.parse(directory / "addons.xml")
        except (ET.ParseError, OSError) as error:
            errors.append(f"{relative_directory}/addons.xml: {error}")
            continue

        for addon in document.getroot().findall("addon"):
            addon_id = addon.get("id", "").strip()
            version = addon.get("version", "").strip()
            if not addon_id or not version:
                errors.append(f"{relative_directory}/addons.xml: add-on without id/version")
                continue
            relative_path = f"{relative_directory}/{addon_id}/{addon_id}-{version}.zip"
            if not (root / Path(relative_path)).is_file():
                errors.append(f"Local package missing: {relative_path}")
            packages.append(Package(addon_id, version, relative_path))

    return packages, errors


def xml_semantically_equal(left: bytes, right: bytes) -> bool:
    try:
        left_root = ET.fromstring(left)
        right_root = ET.fromstring(right)
    except ET.ParseError:
        return False
    return ET.tostring(left_root, encoding="utf-8") == ET.tostring(right_root, encoding="utf-8")


def prepare_expected_root(root: Path, base_url: str) -> tempfile.TemporaryDirectory[str]:
    workspace = tempfile.TemporaryDirectory(prefix="kodiwulf-online-", dir=str(root.parent))
    expected_root = Path(workspace.name) / "repository"
    shutil.copytree(
        root,
        expected_root,
        ignore=shutil.ignore_patterns(".git", "audit", "__pycache__", ".pytest_cache"),
    )
    installer_names = sorted(path.name for path in expected_root.glob("repository.kodi-wulf-v*.zip"))
    if len(installer_names) != 1:
        workspace.cleanup()
        raise RuntimeError(f"expected one Kodi-Wulf installer, found {len(installer_names)}")
    prefix = "repository.kodi-wulf-v"
    suffix = ".zip"
    installer = installer_names[0]
    repo_version = installer[len(prefix):-len(suffix)]
    python = sys.executable
    commands = [
        [python, "tools/repair_addon_assets.py", str(expected_root)],
        [python, "tools/build.py", "--root", str(expected_root), "--cleanup-legacy"],
        [python, "tools/build.py", "--root", str(expected_root), "--base-url", base_url, "--repo-version", repo_version, "--installer-only"],
        [python, "tools/build.py", "--root", str(expected_root), "--base-url", base_url, "--repo-version", repo_version, "--site-only"],
    ]
    try:
        for command in commands:
            subprocess.run(command, cwd=expected_root, check=True, stdout=subprocess.DEVNULL)
    except Exception:
        workspace.cleanup()
        raise
    return workspace


def validate_catalog(base_url: str, root: Path, directory: Path) -> list[str]:
    errors: list[str] = []
    relative_directory = directory.relative_to(root).as_posix()
    xml_relative = f"{relative_directory}/addons.xml"
    md5_relative = f"{xml_relative}.md5"

    xml_status, remote_xml, xml_type = fetch(url_for(base_url, xml_relative))
    if xml_status != 200:
        return [f"HTTP {xml_status}: {xml_relative}"]
    if "xml" not in xml_type.casefold():
        errors.append(f"Unexpected content type {xml_type!r}: {xml_relative}")

    md5_status, remote_md5, _ = fetch(url_for(base_url, md5_relative))
    if md5_status != 200:
        errors.append(f"HTTP {md5_status}: {md5_relative}")
    else:
        expected = remote_md5.decode("ascii", errors="replace").strip().casefold()
        actual = hashlib.md5(remote_xml).hexdigest()
        if expected != actual:
            errors.append(f"Published checksum mismatch: {md5_relative}")

    try:
        local_xml = (directory / "addons.xml").read_bytes()
    except OSError as error:
        errors.append(f"Cannot read local {xml_relative}: {error}")
    else:
        if not xml_semantically_equal(remote_xml, local_xml):
            errors.append(f"Published catalog differs semantically from expected build: {xml_relative}")

    try:
        ET.fromstring(remote_xml)
    except ET.ParseError as error:
        errors.append(f"Published XML is invalid: {xml_relative}: {error}")

    return errors


def validate_package(base_url: str, package: Package) -> str | None:
    status, _, content_type = fetch(url_for(base_url, package.relative_path), method="HEAD")
    if status != 200:
        return f"HTTP {status}: {package.relative_path}"
    lowered_type = content_type.casefold()
    if "zip" not in lowered_type and "octet-stream" not in lowered_type:
        return f"Unexpected content type {content_type!r}: {package.relative_path}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--workers", type=int, default=24)
    arguments = parser.parse_args()

    try:
        workspace = prepare_expected_root(ROOT, arguments.base_url)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"ERROR: cannot prepare expected build state: {error}", file=sys.stderr)
        return 1

    with workspace:
        expected_root = Path(workspace.name) / "repository"
        directories = catalog_directories(expected_root)
        packages, errors = load_packages(expected_root, directories)

        for directory in directories:
            try:
                errors.extend(validate_catalog(arguments.base_url, expected_root, directory))
            except (OSError, urllib.error.URLError) as error:
                errors.append(f"Network error for {directory.relative_to(expected_root)}: {error}")

        installers = sorted(expected_root.glob("repository.kodi-wulf-v*.zip"))
        if len(installers) != 1:
            errors.append(f"Expected one local Kodi-Wulf installer, found {len(installers)}")
        else:
            installer = installers[0].name
            try:
                status, _, content_type = fetch(url_for(arguments.base_url, installer), method="HEAD")
                if status != 200:
                    errors.append(f"HTTP {status}: {installer}")
                elif "zip" not in content_type.casefold() and "octet-stream" not in content_type.casefold():
                    errors.append(f"Unexpected content type {content_type!r}: {installer}")
            except (OSError, urllib.error.URLError) as error:
                errors.append(f"Network error for {installer}: {error}")

        with ThreadPoolExecutor(max_workers=max(1, arguments.workers)) as executor:
            futures = {
                executor.submit(validate_package, arguments.base_url, package): package
                for package in packages
            }
            for future in as_completed(futures):
                package = futures[future]
                try:
                    error = future.result()
                except (OSError, urllib.error.URLError) as exception:
                    error = f"Network error for {package.relative_path}: {exception}"
                if error:
                    errors.append(error)

    if errors:
        for error in sorted(errors, key=str.casefold):
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"FAILED: {len(directories)} catalogs, {len(packages)} advertised packages, "
            f"{len(errors)} errors",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(directories)} published catalogs, {len(packages)} advertised packages, "
        "checksums and download URLs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
