#!/usr/bin/env python3
"""Validate Kodi add-on artwork used by the repository browser.

Kodi displays the artwork declared by each add-on's <assets> metadata.  This
validator makes sure every packaged add-on has a usable icon and that the
path declared in addon.xml actually exists inside the ZIP.
"""
from __future__ import annotations

import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def normalize(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def find_addon_xml(names: list[str]) -> str | None:
    candidates = [
        normalize(name) for name in names
        if normalize(name) == "addon.xml" or normalize(name).endswith("/addon.xml")
    ]
    candidates = [name for name in candidates if "__MACOSX/" not in name]
    return sorted(candidates, key=lambda value: (value.count("/"), value))[0] if candidates else None


def resolve_member(addon_xml_name: str, asset: str) -> str:
    asset = normalize(asset)
    if not asset or asset.startswith("../") or "/../" in asset:
        fail(f"unsafe asset path {asset!r} in {addon_xml_name}")
    root = addon_xml_name.rsplit("/", 1)[0] if "/" in addon_xml_name else ""
    return f"{root}/{asset}" if root else asset


def validate(zip_path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [normalize(name) for name in archive.namelist()]
            addon_xml_name = find_addon_xml(names)
            if not addon_xml_name:
                fail(f"no addon.xml: {zip_path.relative_to(ROOT)}")
            root = ET.fromstring(archive.read(addon_xml_name))
            addon_id = (root.get("id") or "").strip()
            version = (root.get("version") or "").strip()
            if not addon_id or not version:
                fail(f"missing id/version: {zip_path.relative_to(ROOT)}")

            assets = root.find("extension[@point='xbmc.addon.metadata']/assets")
            if assets is None:
                fail(f"<assets> missing: {addon_id} {version} ({zip_path.relative_to(ROOT)})")

            icon = (assets.findtext("icon") or "").strip()
            if not icon:
                fail(f"<assets><icon> missing: {addon_id} {version} ({zip_path.relative_to(ROOT)})")

            icon_member = resolve_member(addon_xml_name, icon)
            if icon_member not in names:
                fail(
                    f"icon target missing from ZIP: {addon_id} {version}: "
                    f"{icon} -> {icon_member} ({zip_path.relative_to(ROOT)})"
                )
            if PurePosixPath(icon_member).name.startswith("."):
                fail(f"invalid hidden icon filename: {addon_id} {version}: {icon}")
            if len(archive.read(icon_member)) == 0:
                fail(f"empty icon file: {addon_id} {version}: {icon}")

            fanart = (assets.findtext("fanart") or "").strip()
            if fanart:
                fanart_member = resolve_member(addon_xml_name, fanart)
                if fanart_member not in names:
                    fail(
                        f"fanart target missing from ZIP: {addon_id} {version}: "
                        f"{fanart} -> {fanart_member} ({zip_path.relative_to(ROOT)})"
                    )

            return addon_id, version
    except zipfile.BadZipFile as exc:
        fail(f"invalid ZIP: {zip_path.relative_to(ROOT)}: {exc}")
    except ET.ParseError as exc:
        fail(f"invalid addon.xml: {zip_path.relative_to(ROOT)}: {exc}")


def main() -> None:
    paths = sorted({
        *ROOT.glob("*.zip"),
        *ROOT.glob("plugins/**/*.zip"),
        *ROOT.glob("repository/**/*.zip"),
        *ROOT.glob("script/**/*.zip"),
        *ROOT.glob("zips/**/*.zip"),
    })
    # The repository installer is itself an add-on and is intentionally included.
    if not paths:
        fail("no Kodi ZIP packages found")

    seen: set[tuple[str, str]] = set()
    for path in paths:
        identity = validate(path)
        if identity in seen:
            continue
        seen.add(identity)

    print(f"OK: validated artwork for {len(seen)} unique Kodi add-on revisions")


if __name__ == "__main__":
    main()
