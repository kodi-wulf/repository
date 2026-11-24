#!/usr/bin/env python3
"""
kodiwulf_build_repo.py

Builds a Kodi repository layout from ZIPs/VIDEO, ZIPs/PROGRAMM and ZIPs/REPOSITORY.

Important index behavior:
  - root/index.html is NOT blindly regenerated when it already exists.
  - Existing root/index.html is edited in-place by replacing only the
    Dr.Debug managed block.
  - On first run without markers, the script replaces the old simple
    "Repository Dateien" + "ZIP Downloads" section when found.
  - If no known section exists, the managed block is inserted before </body>.

Writes, with --apply:
  addons.xml
  addons.xml.md5
  Repository/
  Videos/
  Other/                 only if needed
  <addon.id>/<addon.id>-<version>.zip
  repository.kodi-wulf-v<version>.zip
  index.html             updated, not blindly recreated
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import xml.etree.ElementTree as ET
from xml.dom import minidom


MANAGED_BEGIN = "<!-- DRDEBUG-KODIWULF-INDEX:BEGIN -->"
MANAGED_END = "<!-- DRDEBUG-KODIWULF-INDEX:END -->"


@dataclass(frozen=True)
class AddonInfo:
    addon_id: str
    version: str
    name: str
    provider_name: str
    zip_path: Path
    addon_xml: bytes
    is_repository: bool
    provides: tuple[str, ...]


@dataclass(frozen=True)
class BuiltAddon:
    info: AddonInfo
    canonical_rel: str
    category_rel: str
    category: str


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def normalize_package_root(zip_path: Path, addon_id: str, *, preserve_original: Callable[[Path], None] | None = None) -> bool:
    """Ensure Kodi sees exactly ``<addon-id>/addon.xml`` inside a package."""
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        addon_xml_members = [
            member
            for member in members
            if member.filename.replace("\\", "/").rstrip("/").endswith("/addon.xml")
            or member.filename.replace("\\", "/") == "addon.xml"
        ]
        addon_xml_members.sort(
            key=lambda member: (
                member.filename.count("/") + member.filename.count("\\"),
                member.filename,
            )
        )
        if not addon_xml_members:
            raise RuntimeError(f"no addon.xml found in {zip_path}")

        addon_xml_name = addon_xml_members[0].filename.replace("\\", "/")
        source_root = addon_xml_name.rsplit("/", 1)[0] if "/" in addon_xml_name else ""
        source_prefix = source_root.rstrip("/") + "/" if source_root else ""
        normalized_names = [member.filename.replace("\\", "/") for member in members]
        already_normalized = source_root == addon_id and all(
            name == f"{addon_id}/" or name.startswith(f"{addon_id}/")
            for name in normalized_names
        )
        if already_normalized:
            return False

        payloads: list[tuple[zipfile.ZipInfo, str, bytes]] = []
        targets: set[str] = set()
        for member, normalized_name in zip(members, normalized_names):
            if member.is_dir():
                continue
            if source_prefix:
                if not normalized_name.startswith(source_prefix):
                    continue
                relative_name = normalized_name[len(source_prefix):]
            else:
                relative_name = normalized_name
            relative_name = relative_name.lstrip("/")
            parts = Path(relative_name).parts
            if not relative_name or ".." in parts:
                raise RuntimeError(f"unsafe ZIP member in {zip_path}: {member.filename}")
            target_name = f"{addon_id}/{relative_name}"
            if target_name in targets:
                raise RuntimeError(f"duplicate normalized ZIP member in {zip_path}: {target_name}")
            targets.add(target_name)
            payloads.append((member, target_name, archive.read(member)))

    expected_addon_xml = f"{addon_id}/addon.xml"
    if expected_addon_xml not in targets:
        raise RuntimeError(f"normalization lost {expected_addon_xml} in {zip_path}")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{zip_path.stem}-", suffix=".zip", dir=zip_path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as destination:
            for original, target_name, data in payloads:
                target = zipfile.ZipInfo(target_name, date_time=original.date_time)
                target.compress_type = zipfile.ZIP_DEFLATED
                target.comment = original.comment
                target.create_system = original.create_system
                target.external_attr = original.external_attr
                target.internal_attr = original.internal_attr
                destination.writestr(target, data)
        if preserve_original is not None:
            preserve_original(zip_path)
        temporary.replace(zip_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        die("--base-url must not be empty")
    return base_url.rstrip("/") + "/"


def addon_xml_from_zip(zip_path: Path) -> bytes:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            candidates = [
                name for name in zf.namelist()
                if name.endswith("addon.xml")
                and "__MACOSX" not in name
                and not Path(name).name.startswith(".")
            ]
            candidates.sort(key=lambda n: (n.count("/"), n))
            if not candidates:
                die(f"No addon.xml found in {zip_path}")
            return zf.read(candidates[0])
    except zipfile.BadZipFile:
        die(f"Not a readable zip file: {zip_path}")


def parse_addon_zip(zip_path: Path) -> AddonInfo:
    data = addon_xml_from_zip(zip_path)
    declaration = re.match(rb'\A(?:\xef\xbb\xbf)?<\?xml(?=[ \t\r\n])', data)
    # Enforce the XML VersionNum grammar across versions of the Expat parser.
    if declaration and not re.match(
        rb'''[ \t\r\n]+version[ \t\r\n]*=[ \t\r\n]*(['"])1\.[0-9]+\1(?=[ \t\r\n]|\?>)''',
        data[declaration.end():],
    ):
        die(f"Invalid XML version declaration in {zip_path}")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        die(f"Invalid XML in {zip_path}: {exc}")

    if root.tag != "addon":
        die(f"Root element in {zip_path} is not <addon>")

    addon_id = root.attrib.get("id", "").strip()
    version = root.attrib.get("version", "").strip()
    name = root.attrib.get("name", addon_id).strip() or addon_id
    provider_name = root.attrib.get("provider-name", "").strip()

    if not re.fullmatch(r"[A-Za-z0-9._-]+", addon_id):
        die(f"Invalid Kodi addon id in {zip_path}: {addon_id!r}")
    if not version:
        die(f"Missing version in {zip_path}")

    provides: list[str] = []
    is_repository = addon_id.startswith("repository.")
    for ext in root.findall("extension"):
        point = ext.attrib.get("point", "")
        if point == "xbmc.addon.repository":
            is_repository = True
        if point == "xbmc.python.pluginsource":
            p = ext.findtext("provides", default="")
            provides.extend(x.strip().lower() for x in p.split() if x.strip())

    return AddonInfo(
        addon_id=addon_id,
        version=version,
        name=name,
        provider_name=provider_name,
        zip_path=zip_path,
        addon_xml=data,
        is_repository=is_repository,
        provides=tuple(sorted(set(provides))),
    )


def version_key(version: str) -> tuple:
    parts: list[object] = []
    for piece in re.split(r"([0-9]+)", version):
        if piece == "":
            continue
        parts.append(int(piece) if piece.isdigit() else piece)
    return tuple(parts)


def pretty_xml(root: ET.Element) -> bytes:
    raw = ET.tostring(root, encoding="utf-8")
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent="  ", encoding="UTF-8")
    return b"\n".join(line.rstrip() for line in pretty.splitlines()) + b"\n"


def make_repository_addon_xml(repo_id: str, repo_name: str, repo_version: str, provider: str, base_url: str) -> bytes:
    info_url = base_url + "addons.xml"
    checksum_url = base_url + "addons.xml.md5"
    datadir_url = base_url + "zips/"

    xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="{html.escape(repo_id)}" name="{html.escape(repo_name)}" version="{html.escape(repo_version)}" provider-name="{html.escape(provider)}">
  <extension point="xbmc.addon.repository" name="{html.escape(repo_name)}">
    <dir>
      <info compressed="false">{html.escape(info_url)}</info>
      <checksum>{html.escape(checksum_url)}</checksum>
      <datadir zip="true">{html.escape(datadir_url)}</datadir>
      <hashes>false</hashes>
    </dir>
  </extension>
  <extension point="xbmc.addon.metadata">
    <summary lang="en_GB">{html.escape(repo_name)} add-on repository</summary>
    <description lang="en_GB">Repository generated by Kodi-Wulf.</description>
    <platform>all</platform>
    <assets><icon>icon.png</icon><fanart>fanart.png</fanart></assets>
  </extension>
</addon>
"""
    return xml.encode("utf-8")


def create_repository_zip(root: Path, repo_id: str, repo_version: str, addon_xml: bytes) -> Path:
    zip_path = root / f"{repo_id}-v{repo_version}.zip"

    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / repo_id
        stage.mkdir(parents=True)
        (stage / "addon.xml").write_bytes(addon_xml)
        if (root / "icon.png").is_file():
            shutil.copy2(root / "icon.png", stage / "icon.png")
        if (root / "bg.png").is_file():
            shutil.copy2(root / "bg.png", stage / "fanart.png")

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(stage.iterdir()):
                zf.write(file_path, arcname=f"{repo_id}/{file_path.name}")

    return zip_path


def write_md5_sidecar(path: Path) -> None:
    path.with_name(path.name + ".md5").write_text(md5_file(path), encoding="utf-8")


def category_for(info: AddonInfo) -> str:
    if info.is_repository:
        return "Repository"
    if "video" in info.provides or info.addon_id.startswith("plugin.video."):
        return "Videos"

    # Kodi-Wulf: Programm-Add-ons sauber in eigene Kategorie einsortieren.
    if (
        "executable" in info.provides
        or info.addon_id.startswith("plugin.program.")
        or info.addon_id.startswith("script.")
    ):
        return "Program"

    return "Other"


def safe_copy2(src: Path, dst: Path) -> None:
    src = src.resolve()
    dst = dst.resolve()
    if src == dst:
        return
    shutil.copy2(src, dst)


def copy_addon_zip(root: Path, info: AddonInfo) -> BuiltAddon:
    if info.addon_id == "repository.kodi-wulf":
        canonical = root / f"{info.addon_id}-v{info.version}.zip"
        safe_copy2(info.zip_path, canonical)
        normalize_package_root(canonical, info.addon_id)
        return BuiltAddon(info=info, canonical_rel=canonical.name, category_rel=canonical.name, category="Repository")

    id_dir = root / "zips" / info.addon_id
    id_dir.mkdir(parents=True, exist_ok=True)
    canonical = id_dir / f"{info.addon_id}-{info.version}.zip"
    safe_copy2(info.zip_path, canonical)
    normalize_package_root(canonical, info.addon_id)

    category = category_for(info)

    return BuiltAddon(
        info=info,
        canonical_rel=canonical.relative_to(root).as_posix(),
        category_rel=canonical.relative_to(root).as_posix(),
        category=category,
    )


def write_simple_index(path: Path, title: str, entries: Iterable[tuple[str, str]]) -> None:
    rows = []
    for label, href in entries:
        rows.append(f'<li><a href="{html.escape(href)}">{html.escape(label)}</a></li>')
    body = "\n    ".join(rows) if rows else "<li>No files yet.</li>"
    doc = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <ul>
    {body}
  </ul>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def render_download_row(label: str, href: str, note: str = "") -> str:
    label_h = html.escape(label)
    href_h = html.escape(href)
    note_h = html.escape(note)
    return f'      <tr><td><a href="{href_h}">{label_h}</a></td><td>{note_h}</td></tr>'


def render_addon_row(built: BuiltAddon, use_category: bool) -> str:
    info = built.info
    href = built.category_rel if use_category else built.canonical_rel
    fields = [
        info.addon_id,
        info.name,
        info.version,
        info.provider_name or "-",
        built.category,
        f'<a href="{html.escape(href)}">{html.escape(Path(href).name)}</a>',
    ]
    return (
        "      <tr>"
        + "".join(f"<td>{field if field.startswith('<a ') else html.escape(field)}</td>" for field in fields)
        + "</tr>"
    )


def render_managed_index_block(built_addons: list[BuiltAddon], include_other: bool) -> str:
    repo_addons = [x for x in built_addons if x.category == "Repository"]
    video_addons = [x for x in built_addons if x.category == "Videos"]
    other_addons = [x for x in built_addons if x.category == "Other"]

    root_rows = [
        render_download_row("addons.xml", "addons.xml", "Kodi Repository-Metadaten"),
        render_download_row("addons.xml.md5", "addons.xml.md5", "Checksumme für Kodi"),
    ]

    repo_rows = "\n".join(render_addon_row(x, use_category=True) for x in repo_addons)
    video_rows = "\n".join(render_addon_row(x, use_category=True) for x in video_addons)
    all_rows = "\n".join(render_addon_row(x, use_category=False) for x in built_addons)

    if not repo_rows:
        repo_rows = '      <tr><td colspan="6">Noch keine Repository-ZIPs vorhanden.</td></tr>'
    if not video_rows:
        video_rows = '      <tr><td colspan="6">Noch keine Video-ZIPs vorhanden.</td></tr>'
    if not all_rows:
        all_rows = '      <tr><td colspan="6">Noch keine Add-ons vorhanden.</td></tr>'

    return f"""
{MANAGED_BEGIN}
  <section id="kodiwulf-repository-browser" data-drdebug-managed="kodiwulf-repo-index">
    <h2>Kodi Installationsstruktur</h2>
    <p>
      Diese Links sind so aufgebaut, dass Kodi über <strong>Install from ZIP</strong>
      alle installierbaren ZIP-Dateien direkt im Repository-Root sieht.
      Die installierte Repository-ZIP verwendet zusätzlich <code>addons.xml</code>,
      <code>addons.xml.md5</code> und die kanonischen Add-on-Ordner.
    </p>

    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Pfad</th>
          <th>Zweck</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(root_rows)}
      </tbody>
    </table>

    <h2>Repository ZIPs</h2>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Add-on-ID</th>
          <th>Name</th>
          <th>Version</th>
          <th>Provider</th>
          <th>Kategorie</th>
          <th>Download</th>
        </tr>
      </thead>
      <tbody>
{repo_rows}
      </tbody>
    </table>

    <h2>Video Add-ons</h2>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Add-on-ID</th>
          <th>Name</th>
          <th>Version</th>
          <th>Provider</th>
          <th>Kategorie</th>
          <th>Download</th>
        </tr>
      </thead>
      <tbody>
{video_rows}
      </tbody>
    </table>

    <h2>Kodi Repository-Pfade</h2>
    <p>
      Diese kanonischen Pfade werden von Kodi über das installierte Repository
      und <code>datadir zip="true"</code> verwendet.
    </p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Add-on-ID</th>
          <th>Name</th>
          <th>Version</th>
          <th>Provider</th>
          <th>Kategorie</th>
          <th>Download</th>
        </tr>
      </thead>
      <tbody>
{all_rows}
      </tbody>
    </table>
  </section>
{MANAGED_END}
""".strip()


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir = path.parent / ".drdebug-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.bak"
    counter = 1
    while backup.exists():
        backup = backup_dir / f"{path.name}.bak.{counter}"
        counter += 1
    shutil.copy2(path, backup)
    return backup


def update_existing_index_html(index_path: Path, managed_block: str) -> str:
    if not index_path.exists():
        doc = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Kodi-Wulf Repository</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <h1>Kodi-Wulf Repository</h1>

  <p>
    Für Kodi: <strong>Install from ZIP</strong> soll die Repository-ZIP installieren.
    Danach verwendet Kodi <code>addons.xml</code> und <code>addons.xml.md5</code>.
  </p>

{managed_block}
</body>
</html>
"""
        index_path.write_text(doc, encoding="utf-8")
        return "created-minimal-index"

    text = index_path.read_text(encoding="utf-8")

    marker_pattern = re.compile(
        re.escape(MANAGED_BEGIN) + r".*?" + re.escape(MANAGED_END),
        flags=re.DOTALL,
    )
    if marker_pattern.search(text):
        new_text = marker_pattern.sub(managed_block, text)
        index_path.write_text(new_text, encoding="utf-8")
        return "updated-managed-block"

    # First-run migration for the old generated root page. This keeps the
    # existing doctype, html/head/body, h1 and introductory text.
    legacy_pattern = re.compile(
        r"\n\s*<h2>\s*Repository Dateien\s*</h2>\s*"
        r"<ul>.*?</ul>\s*"
        r"<h2>\s*ZIP Downloads\s*</h2>\s*"
        r"<table\b.*?</table>\s*",
        flags=re.IGNORECASE | re.DOTALL,
    )
    if legacy_pattern.search(text):
        new_text = legacy_pattern.sub("\n\n" + managed_block + "\n", text, count=1)
        index_path.write_text(new_text, encoding="utf-8")
        return "migrated-legacy-sections"

    body_close = re.search(r"</body\s*>", text, flags=re.IGNORECASE)
    if body_close:
        pos = body_close.start()
        new_text = text[:pos].rstrip() + "\n\n" + managed_block + "\n" + text[pos:]
        index_path.write_text(new_text, encoding="utf-8")
        return "inserted-before-body-close"

    # Last resort: append without discarding existing content.
    new_text = text.rstrip() + "\n\n" + managed_block + "\n"
    index_path.write_text(new_text, encoding="utf-8")
    return "appended-managed-block"


def discover_addons(root: Path, source_dir: Path, repo_info: AddonInfo) -> dict[str, AddonInfo]:
    discovered: dict[str, AddonInfo] = {repo_info.addon_id: repo_info}

    if not source_dir.exists():
        return discovered

    zip_paths = sorted(source_dir.rglob("*.zip"))

    for zip_path in sorted(zip_paths):
        info = parse_addon_zip(zip_path)

        # The generated repository add-on is authoritative for this repo id.
        if info.addon_id == repo_info.addon_id:
            print(f"Skipping source ZIP with generated repo id: {zip_path.name}", file=sys.stderr)
            continue

        old = discovered.get(info.addon_id)
        if old is None or version_key(info.version) >= version_key(old.version):
            discovered[info.addon_id] = info

    return discovered


def build(args: argparse.Namespace) -> None:
    root = Path(args.root).expanduser().resolve()
    source_dir = root / "zips"
    base_url = normalize_base_url(args.base_url)

    if not root.exists():
        die(f"Repository root does not exist: {root}")

    repo_xml = make_repository_addon_xml(
        repo_id=args.repo_id,
        repo_name=args.repo_name,
        repo_version=args.repo_version,
        provider=args.provider_name,
        base_url=base_url,
    )

    if args.apply:
        source_dir.mkdir(parents=True, exist_ok=True)
        repo_zip = create_repository_zip(root, args.repo_id, args.repo_version, repo_xml)
    else:
        # Dry-run parses ZIPs but does not create the generated repository ZIP.
        repo_zip = root / f"{args.repo_id}-v{args.repo_version}.zip"

    repo_info = AddonInfo(
        addon_id=args.repo_id,
        version=args.repo_version,
        name=args.repo_name,
        provider_name=args.provider_name,
        zip_path=repo_zip,
        addon_xml=repo_xml,
        is_repository=True,
        provides=(),
    )

    discovered = discover_addons(root, source_dir, repo_info)
    ordered_infos = sorted(discovered.values(), key=lambda x: x.addon_id)

    if not args.apply:
        print("DRY-RUN: would build Kodi repository")
        print(f"root:      {root}")
        print(f"source:    {source_dir}")
        print(f"base-url:  {base_url}")
        print(f"repo-id:   {args.repo_id}")
        print()
        print("Would write/rebuild:")
        print("  addons.xml")
        print("  addons.xml.md5")
        print(f"  {repo_zip.name}")
        print("  repository.kodi-wulf-v<version>.zip in repository root")
        print("  zips/<addon.id>/<addon.id>-<version>.zip")
        print("  root index.html managed block only")
        print()
        print("Discovered add-ons:")
        for info in ordered_infos:
            print(f"  - {info.addon_id} {info.version} [{category_for(info)}]")
        print()
        print("Add --apply to write files.")
        return

    copied: list[BuiltAddon] = []
    for info in ordered_infos:
        copied.append(copy_addon_zip(root, info))

    addons_root = ET.Element("addons")
    for info in ordered_infos:
        try:
            addons_root.append(ET.fromstring(info.addon_xml))
        except ET.ParseError as exc:
            die(f"Could not add XML for {info.addon_id}: {exc}")

    addons_xml = pretty_xml(addons_root)
    (root / "addons.xml").write_bytes(addons_xml)
    (root / "addons.xml.md5").write_text(md5_bytes(addons_xml), encoding="utf-8")

    index_path = root / "index.html"
    backup = backup_file(index_path)
    managed_block = render_managed_index_block(
        built_addons=sorted(copied, key=lambda x: x.info.addon_id),
        include_other=any(x.category == "Other" for x in copied),
    )
    index_action = update_existing_index_html(index_path, managed_block)
    try:
        from kodiwulf_addons_xml import write_addons_xml_from_zips
        _, _, addon_count = write_addons_xml_from_zips(root)
        print(f"OK: addons.xml rebuilt from ZIPs: {addon_count} add-ons")
    except Exception as exc:
        print(f"WARN: addons.xml rebuild from ZIPs failed: {exc}")
    try:
        from kodiwulf_dark_index import write_dark_index
        write_dark_index(root)
        index_action = f"{index_action}+dark-index"
    except Exception as exc:
        print(f"WARN: dark index update failed: {exc}")

    if args.write_directory_indexes:
        for built in sorted(copied, key=lambda x: x.info.addon_id):
            id_dir = root / built.info.addon_id
            entries = [
                (p.name, p.name)
                for p in sorted(id_dir.glob("*"))
                if p.is_file() and p.name != "index.html"
            ]
            write_simple_index(id_dir / "index.html", built.info.addon_id, entries)

    print("Kodi-Wulf repository built.")
    print(f"Root:                {root}")
    print(f"Base URL:            {base_url}")
    print(f"Index action:        {index_action}")
    if backup:
        print(f"Index backup:        {backup}")
    print(f"Root install ZIP:    {root / repo_zip.name}")
    print(f"Canonical repo ZIP:  {repo_zip}")
    print()
    print("Discovered add-ons:")
    for built in copied:
        print(f"  - {built.info.addon_id} {built.info.version} -> {built.canonical_rel} + {built.category_rel}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1]),
        help="repository root (default: directory containing tools/)",
    )
    parser.add_argument("--base-url", default="https://kodi-wulf.github.io/repository/")
    parser.add_argument("--repo-id", default="repository.kodi-wulf")
    parser.add_argument("--repo-name", default="Kodi-Wulf")
    parser.add_argument("--repo-version", default="1.33.7a")
    parser.add_argument("--provider-name", default="Kodi-Wulf")
    parser.add_argument("--write-directory-indexes", action="store_true", help="also write index.html files inside generated addon/category folders")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
