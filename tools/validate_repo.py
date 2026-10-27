#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
TECHNICAL_ROOTS = {".github", "Repository", "_data", "_layouts", "_site", "assets", "node_modules", "tools"}
LEGACY_MIRROR_BRANCHES = {("repository", "plugins"), ("repository", "repository"), ("repository", "script")}
NAVIGATION_ROOTS = {"plugins", "repository", "script", "not-working"}
REPO_ID = "repository.kodi-wulf"
REPO_NAME = "Kodi-Wulf"
REPO_VERSION = "1.33.7a"
INSTALLER_NAME = f"{REPO_ID}-v{REPO_VERSION}.zip"
BASE_URL = "https://kodi-wulf.github.io/repository/"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def is_visible(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if path.suffix.lower() == ".zip" and len(relative.parts) == 2 and relative.parts[0] == "repository" and (
        path.name.startswith(f"{REPO_ID}-v") or path.name.startswith("repository.kodiwulf-")
    ):
        return False
    if relative.parts and relative.parts[0] == "Repository":
        return False
    return not (len(relative.parts) > 1 and (relative.parts[0], relative.parts[1]) in LEGACY_MIRROR_BRANCHES)


def main() -> None:
    removed_addon_marker = "x" + "wulf"
    searchable_suffixes = {".html", ".js", ".json", ".md", ".py", ".xml", ".yml", ".yaml"}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts:
            continue
        if removed_addon_marker in path.name.lower():
            fail(f"removed add-on path still exists: {relative}")
        if path.is_file() and path.suffix.lower() in searchable_suffixes:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if removed_addon_marker in text.lower():
                fail(f"removed add-on reference still exists: {relative}")

    if (ROOT / ".nojekyll").exists():
        fail(".nojekyll must not exist because Jekyll rendering is enabled")
    if not (ROOT / "_config.yml").is_file():
        fail("_config.yml is required for Jekyll")
    if (ROOT / "index.md").exists():
        fail("index.md must not compete with the Jekyll index.html output")
    for asset in ("bg.png", "icon.png"):
        if not (ROOT / asset).is_file():
            fail(f"Kodi artwork is missing: {asset}")

    addons_xml = ROOT / "addons.xml"
    checksum = ROOT / "addons.xml.md5"
    expected = hashlib.md5(addons_xml.read_bytes()).hexdigest()
    if checksum.read_text(encoding="utf-8").strip() != expected:
        fail("addons.xml.md5 does not match addons.xml")

    index = (ROOT / "index.html").read_text(encoding="utf-8")
    browser_data = (ROOT / "assets" / "files.js").read_text(encoding="utf-8")
    tree_path = ROOT / "_data" / "repository_tree.json"
    if not tree_path.is_file():
        fail("Jekyll navigation data is missing: _data/repository_tree.json")
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    public_roots = sorted(path for path in ROOT.iterdir() if path.is_dir() and path.name in NAVIGATION_ROOTS)
    root_zips = sorted(ROOT.glob("*.zip"))
    if [path.name for path in root_zips] != [INSTALLER_NAME]:
        fail(f"{INSTALLER_NAME} must be the only ZIP in repository root")

    with zipfile.ZipFile(root_zips[0]) as archive:
        names = set(archive.namelist())
        required_members = {
            f"{REPO_ID}/addon.xml",
            f"{REPO_ID}/icon.png",
            f"{REPO_ID}/fanart.png",
        }
        missing = required_members - names
        if missing:
            fail(f"installer artwork or metadata is missing: {sorted(missing)[0]}")
        if archive.read(f"{REPO_ID}/icon.png") != (ROOT / "icon.png").read_bytes():
            fail("installer icon.png does not match the selected Kodi icon")
        if archive.read(f"{REPO_ID}/fanart.png") != (ROOT / "bg.png").read_bytes():
            fail("installer fanart.png does not match the selected Kodi banner")
        addon_xml = archive.read(f"{REPO_ID}/addon.xml").decode("utf-8")
        for required in (
            f'id="{REPO_ID}"',
            f'name="{REPO_NAME}"',
            f'version="{REPO_VERSION}"',
            "<icon>icon.png</icon>",
            "<fanart>fanart.png</fanart>",
        ):
            if required not in addon_xml:
                fail(f"installer metadata is missing: {required}")
        for category in ("plugins/program", "plugins/video", "repository", "script/module"):
            expected_url = f"https://kodi-wulf.github.io/repository/{category}/addons.xml"
            if expected_url not in addon_xml:
                fail(f"installer does not expose category: {category}")

    for zip_path in root_zips:
        linked_name = quote(zip_path.name, safe="/._-~")
        if linked_name not in browser_data:
            fail(f"root ZIP is not present in React browser data: {zip_path.name}")
    for required in ("bootstrap@5.3.8", "jquery-3.7.1.min.js", "vue@3", "anime.min.js", "d3@7", "assets/app.js", "assets/svelte-status.js"):
        if required not in index:
            fail(f"missing frontend resource: {required}")
    if "how-to-use.html" not in index:
        fail("How-To-Use menu link is missing from the root page")
    how_to = ROOT / "how-to-use.html"
    if not how_to.is_file():
        fail("How-To-Use page is missing")
    how_to_text = how_to.read_text(encoding="utf-8")
    for required in (BASE_URL, INSTALLER_NAME, "Dateimanager", "Aus ZIP-Datei installieren"):
        if required not in how_to_text:
            fail(f"How-To-Use page is missing: {required}")
    for href in (*(quote(path.name, safe="._-~") + "/" for path in public_roots), quote(root_zips[0].name, safe="/._-~")):
        if f'href="{href}"' not in index:
            fail(f"missing static Kodi root link: {href}")
    static_nav = re.search(r'<nav class="kodi-static"[^>]*>(.*?)</nav>', index, re.DOTALL)
    if not static_nav:
        fail("static Kodi root navigation is missing")
    static_hrefs = set(re.findall(r'href="([^"]+)"', static_nav.group(1)))
    expected_hrefs = {"plugins/", "repository/", "script/", "not-working/", INSTALLER_NAME}
    if static_hrefs != expected_hrefs:
        fail(f"unexpected Kodi root entries: {sorted(static_hrefs ^ expected_hrefs)}")
    data_names = {item["name"] for item in tree.get("roots", [])}
    if data_names != {path.name for path in public_roots}:
        fail("Jekyll root navigation does not match public root directories")
    if "site.data.repository_tree.roots" not in index:
        fail("root index does not render the generated Jekyll roots")
    for required in ("data-terminal-shadow", "data-terminal-solid", "brand-shadow", "brand-solid"):
        if required not in index:
            fail(f"two-layer banner markup is missing: {required}")
    for font in ("DampfPlatz.ttf", "DampfPlatzs.ttf", "DampfPlatzsh.ttf"):
        if not (ROOT / "assets" / "fonts" / "dampfplatz" / font).is_file():
            fail(f"Dampfplatz font is missing: {font}")
    for legacy in ("plugins", "repository", "script"):
        if (ROOT / "repository" / legacy).exists():
            fail(f"obsolete Kodi mirror still exists: repository/{legacy}")
    obsolete_installers = sorted({
        *list((ROOT / "repository").glob("repository.kodiwulf-*.zip")),
        *list((ROOT / "repository").glob(f"{REPO_ID}-v*.zip")),
    })
    if obsolete_installers:
        fail(f"obsolete nested Kodi-Wulf installer exists: {obsolete_installers[0].relative_to(ROOT)}")
    for item in json.loads(browser_data.removeprefix("window.KODIWULF_FILES=").split(";", 1)[0]):
        if "size" not in item or "size_label" not in item:
            fail(f"ZIP size metadata is missing: {item.get('path', '?')}")

    category_dirs = [ROOT / "repository"]
    category_dirs.extend(
        path
        for parent in (ROOT / "plugins", ROOT / "script")
        if parent.is_dir()
        for path in parent.iterdir()
        if path.is_dir()
    )
    for directory in category_dirs:
        metadata = ET.parse(directory / "addons.xml").getroot()
        addon_ids = [addon.get("id") for addon in metadata]
        if len(addon_ids) != len(set(addon_ids)):
            fail(f"duplicate add-on ID in metadata: {directory.relative_to(ROOT)}")
        for addon in metadata:
            addon_id = addon.get("id")
            version = addon.get("version")
            package = directory / str(addon_id) / f"{addon_id}-{version}.zip"
            if not package.is_file():
                fail(f"advertised Kodi package is missing: {package.relative_to(ROOT)}")
            try:
                with zipfile.ZipFile(package) as archive:
                    expected_member = f"{addon_id}/addon.xml"
                    if expected_member not in archive.namelist():
                        fail(
                            "advertised Kodi package has the wrong internal root: "
                            f"{package.relative_to(ROOT)} (expected {expected_member})"
                        )
                    unexpected_members = [
                        member
                        for member in archive.namelist()
                        if member.replace("\\", "/").lstrip("/")
                        and not member.replace("\\", "/").lstrip("/").startswith(f"{addon_id}/")
                    ]
                    if unexpected_members:
                        fail(
                            "advertised Kodi package contains files outside its add-on root: "
                            f"{package.relative_to(ROOT)} ({unexpected_members[0]})"
                        )
                    internal_addon = ET.fromstring(archive.read(expected_member))
            except (zipfile.BadZipFile, ET.ParseError) as error:
                fail(f"advertised Kodi package is invalid: {package.relative_to(ROOT)}: {error}")
            if internal_addon.get("id") != addon_id or internal_addon.get("version") != version:
                fail(f"advertised Kodi package metadata differs from its catalog: {package.relative_to(ROOT)}")
        for addon_dir in (path for path in directory.iterdir() if path.is_dir()):
            direct_zips = sorted(addon_dir.glob("*.zip"))
            if not direct_zips:
                continue
            addon_index = addon_dir / "index.html"
            if not addon_index.is_file():
                fail(f"Kodi browse index is missing: {addon_dir.relative_to(ROOT)}")
            addon_page = addon_index.read_text(encoding="utf-8")
            for zip_path in direct_zips:
                href = quote(zip_path.name, safe="/._-~")
                if f'href="{href}"' not in addon_page:
                    fail(f"ZIP is not linked in add-on index: {zip_path.relative_to(ROOT)}")

    allowed_md5 = {checksum}
    allowed_md5.update(path for path in ROOT.rglob("addons.xml.md5") if (path.parent / "index.html").is_file())
    stray_md5 = [path for path in ROOT.rglob("*.md5") if path not in allowed_md5]
    if stray_md5:
        fail(f"unneeded MD5 sidecar exists: {stray_md5[0].relative_to(ROOT)}")

    classified = [zip_path for directory in public_roots for zip_path in directory.rglob("*.zip") if is_visible(zip_path)]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in classified]
    if len(hashes) != len(set(hashes)):
        fail("duplicate ZIP content exists in the public category structure")
    pending_imports = sorted((ROOT / "zips").rglob("*.zip")) if (ROOT / "zips").is_dir() else []
    if pending_imports:
        fail(f"unprocessed ZIP remains in zips/: {pending_imports[0].relative_to(ROOT)}")
    plugins_index = (ROOT / "plugins" / "index.html").read_text(encoding="utf-8")
    for child in sorted(path for path in (ROOT / "plugins").iterdir() if path.is_dir()):
        if f'href="{quote(child.name, safe="._-~")}/"' not in plugins_index:
            fail(f"plugins index does not link category: {child.name}")

    required_indexes = [ROOT / "repository"]
    for parent_name in ("plugin", "plugins", "script"):
        parent = ROOT / parent_name
        if parent.is_dir():
            required_indexes.extend(path for path in parent.iterdir() if path.is_dir())
    required_indexes.extend(path for path in public_roots if path.name not in {"repository", "plugin", "plugins", "script"})
    for directory in required_indexes:
        page = directory / "index.html"
        if not page.is_file():
            fail(f"missing Kodi/Jekyll directory index: {directory.relative_to(ROOT)}")
        page_text = page.read_text(encoding="utf-8")
        for zip_path in (path for path in directory.rglob("*.zip") if is_visible(path)):
            relative_link = quote(zip_path.relative_to(directory).as_posix(), safe="/._-~")
            if f'href="{relative_link}"' not in page_text:
                fail(f"ZIP is not linked in category index: {zip_path.relative_to(ROOT)}")
    print(f"OK: one root installer and {len(classified)} unique classified ZIP files")


if __name__ == "__main__":
    main()
