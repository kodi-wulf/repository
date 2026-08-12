#!/usr/bin/env python3
"""Repair legacy Kodi add-on ZIP manifests before repository generation."""
from __future__ import annotations
import argparse
import os
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def find_addon_xml(names: list[str]) -> str | None:
    candidates = [n for n in names if n == "addon.xml" or n.endswith("/addon.xml")]
    return sorted(candidates, key=lambda n: (n.count("/"), len(n)))[0] if candidates else None

def choose_asset(names: set[str], root: str, candidates: tuple[str, ...]) -> str | None:
    prefix = root.rstrip("/") + "/"
    normalized = {n.replace("\\", "/"): n for n in names}
    for candidate in candidates:
        target = prefix + candidate if prefix else candidate
        if target in normalized:
            return candidate
    basename = {Path(n).name.lower(): n for n in names if n.startswith(prefix)}
    for candidate in candidates:
        hit = basename.get(Path(candidate).name.lower())
        if hit:
            return hit[len(prefix):] if prefix and hit.startswith(prefix) else hit
    return None

def repair_zip(path: Path) -> bool:
    with zipfile.ZipFile(path, "r") as src:
        names = src.namelist()
        addon_xml_name = find_addon_xml(names)
        if not addon_xml_name:
            return False
        root_prefix = addon_xml_name.rsplit("/", 1)[0] if "/" in addon_xml_name else ""
        root = ET.fromstring(src.read(addon_xml_name))
        metadata = next((e for e in root if local(e.tag) == "extension" and e.attrib.get("point") == "xbmc.addon.metadata"), None)
        if metadata is None:
            return False
        assets = next((e for e in metadata if local(e.tag) == "assets"), None)
        if assets is None:
            assets = ET.Element("assets")
            metadata.append(assets)
        existing = {local(e.tag): (e.text or "").strip() for e in assets}
        icon = choose_asset(set(names), root_prefix, (
            "resources/icon.png", "resources/icon.jpg", "resources/icon.jpeg",
            "icon.png", "icon.jpg", "icon.jpeg"))
        fanart = choose_asset(set(names), root_prefix, (
            "resources/fanart.jpg", "resources/fanart.png", "resources/fanart.jpeg",
            "fanart.jpg", "fanart.png", "fanart.jpeg"))
        changed = False
        if not existing.get("icon") and icon:
            ET.SubElement(assets, "icon").text = icon
            changed = True
        if not existing.get("fanart") and fanart:
            ET.SubElement(assets, "fanart").text = fanart
            changed = True
        if not changed:
            return False
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        fd, tmp_name = tempfile.mkstemp(suffix=".zip", dir=str(path.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w") as dst:
                for info in src.infolist():
                    data = xml_bytes if info.filename == addon_xml_name else src.read(info.filename)
                    dst.writestr(info, data)
            shutil.copystat(path, tmp)
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return True

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    root = Path(args.root)
    repaired = scanned = 0
    for path in sorted(root.rglob("*.zip")):
        if path.name.startswith("repository.kodi-wulf-v"):
            continue
        scanned += 1
        try:
            if repair_zip(path):
                repaired += 1
                print(f"REPAIRED: {path}")
        except (zipfile.BadZipFile, ET.ParseError, OSError) as exc:
            print(f"SKIP: {path}: {exc}")
    print(f"Scanned ZIPs: {scanned}; repaired: {repaired}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
