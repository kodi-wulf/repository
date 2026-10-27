#!/usr/bin/env python3
"""Read-only Kodi Omega discovery: candidate IDs, never runtime/redistribution claims."""
from __future__ import annotations

import argparse
import gzip
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_METADATA = "https://mirrors.kodi.tv/addons/omega/addons.xml.gz"
MAX_DOWNLOAD = 40 * 1024 * 1024


def source_bytes(source: str) -> bytes:
    parsed = urlsplit(source)
    if parsed.scheme in ("http", "https"):
        if parsed.scheme != "https":
            raise ValueError("Only HTTPS sources are allowed")
        with urllib.request.urlopen(
            urllib.request.Request(source, headers={"User-Agent": "Kodi-Wulf-discovery/1.0"}),
            timeout=45,
        ) as response:
            content = response.read(MAX_DOWNLOAD + 1)
    else:
        content = Path(source).read_bytes()
    if len(content) > MAX_DOWNLOAD:
        raise ValueError("metadata is unreasonably large")
    if content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)
    if len(content) > MAX_DOWNLOAD:
        raise ValueError("uncompressed metadata is unreasonably large")
    return content


def version_key(version: str) -> tuple[tuple[int, object], ...]:
    return tuple((0, int(x)) if x.isdigit() else (1, x.lower())
                 for x in re.findall(r"\\d+|[^\\d]+", version))


def plugin_inventory(xml_bytes: bytes) -> dict[str, dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    if root.tag != "addons":
        raise ValueError("expected an <addons> Kodi catalog")
    inventory = {}
    for addon in root.findall("addon"):
        addon_id = addon.get("id", "")
        version = addon.get("version", "")
        if not addon_id or not version:
            continue
        if not (addon_id.startswith("plugin.") or any(
            node.get("point") == "xbmc.python.pluginsource" for node in addon.findall("extension")
        )):
            continue
        previous = inventory.get(addon_id)
        record = {"addon_id": addon_id, "name": addon.get("name", addon_id), "version": version}
        if previous is None or version_key(previous["version"]) < version_key(version):
            inventory[addon_id] = record
    return inventory


def compare(official: bytes, local: bytes, source_url: str) -> dict:
    upstream = plugin_inventory(official)
    owned_root = ET.fromstring(local)
    if owned_root.tag != "addons":
        raise ValueError("expected local Kodi-Wulf <addons> catalog")
    local_versions = {addon.get("id"): addon.get("version") for addon in owned_root.findall("addon")}
    missing = [row for name, row in sorted(upstream.items()) if name not in local_versions]
    overlap = [row for name, row in sorted(upstream.items()) if name in local_versions]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source_url,
        "scope": "Kodi 21 Omega official repository plugin inventory only",
        "verification": "discovery-only; installability, functionality, license and distributable ZIP unverified",
        "official_plugin_count": len(upstream),
        "present_in_kodiwulf": len(overlap),
        "missing_from_kodiwulf": len(missing),
        "candidates": missing,
        "overlap": overlap,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official", default=OFFICIAL_METADATA, help="HTTPS URL or local fixture")
    parser.add_argument("--local", default=str(ROOT / "addons.xml"), help="Kodi-Wulf catalog")
    parser.add_argument("--output", default=str(ROOT / "audit" / "omega-discovery.json"))
    args = parser.parse_args()
    report = compare(source_bytes(args.official), Path(args.local).read_bytes(), args.official)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"candidates", "overlap"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
