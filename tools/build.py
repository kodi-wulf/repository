#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import io
import json
import re
import shutil
import tempfile
import zipfile
import zlib
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from kodiwulf_build_repo_core import AddonInfo, normalize_package_root, parse_addon_zip, pretty_xml
from repair_addon_assets import repair_zip


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://kodi-wulf.github.io/repository/"
REPO_ID = "repository.kodi-wulf"
REPO_NAME = "Kodi-Wulf"
REPO_PROVIDER = "Kodi-Wulf"
DEFAULT_REPO_VERSION = "1.33.7a"
NAVIGATION_ROOTS = {"plugins", "repository", "script"}
DOWNLOAD_ROOTS = {"archiv", "defekt"}
SKIP_PARTS = {".git", ".agents", ".drdebug-backups", "__pycache__"}
TECHNICAL_ROOTS = {
    ".github", "Repository", "_data", "_layouts", "_site", "assets", "node_modules", "tools"
}
LEGACY_MIRROR_BRANCHES = {("repository", "plugins"), ("repository", "repository"), ("repository", "script")}
LEGACY_MIRROR_NAMES = {"plugins", "repository", "script"}


def installer_name(version: str = DEFAULT_REPO_VERSION) -> str:
    return f"{REPO_ID}-v{version}.zip"


def zip_files(directory: Path, *, recursive: bool = True):
    paths = directory.rglob("*") if recursive else directory.glob("*")
    return (path for path in paths if path.suffix.casefold() == ".zip" and path.is_file())


def require_no_publish_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if SKIP_PARTS.intersection(relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"symbolic link cannot be published: {relative}")


def installer_paths(root: Path) -> list[Path]:
    prefixes = (f"{REPO_ID}-v", "repository.kodiwulf-")
    return sorted(path for path in zip_files(root, recursive=False) if path.name.casefold().startswith(prefixes))


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def preserve_zip(path: Path, relative: Path, archive: Path, *, copy: bool = False) -> Path:
    destination = archive / relative
    if not destination.parent.resolve().is_relative_to(archive.absolute()):
        raise RuntimeError(f"unsafe ZIP preservation path: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = destination
    counter = 0
    while destination.exists() or destination.is_symlink():
        counter += 1
        destination = original.with_name(f"{original.stem}-{counter}{original.suffix}")
    if copy:
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            staged = Path(temporary) / destination.name
            shutil.copy2(path, staged)
            staged.rename(destination)
    else:
        path.rename(destination)
    return destination


def classify_zip(path: Path) -> tuple[AddonInfo | None, str]:
    messages = io.StringIO()
    try:
        with contextlib.redirect_stderr(messages):
            info = parse_addon_zip(path)
        if not info.addon_id[0].isalnum() or not re.fullmatch(r"[A-Za-z0-9._+~-]+", info.version) or info.version in {".", ".."}:
            return None, "Ungültige Add-on-ID oder Version"
        with zipfile.ZipFile(path) as archive:
            filenames = set()
            for member in archive.infolist():
                name = member.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name):
                    return None, "Unsicherer Pfad im ZIP"
                if not member.is_dir():
                    if name in filenames:
                        return None, "Doppelter Dateipfad im ZIP"
                    filenames.add(name)
            if archive.testzip() is not None:
                return None, "Prüfsumme im ZIP stimmt nicht"
    except SystemExit:
        return None, messages.getvalue().replace(str(path), path.name).strip() or "Ungültige Add-on-Metadaten"
    except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError) as error:
        return None, f"ZIP kann nicht gelesen werden ({type(error).__name__})"
    return info, ""


def visible_archive_path(path: Path, root: Path) -> Path:
    return Path(*(f"file-{part}" if part.startswith((".", "_")) else part for part in path.relative_to(root).parts))


def move_out_of_catalog(path: Path, root: Path, bucket: str) -> Path:
    return preserve_zip(path, visible_archive_path(path, root), root / bucket)


def remove_empty_import_tree(root: Path) -> None:
    import_root = root / "zips"
    if not import_root.is_dir():
        return
    directories = sorted((path for path in import_root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True)
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        import_root.rmdir()
    except OSError:
        pass


def cleanup_legacy_mirrors(root: Path) -> None:
    mirror_parent = (root / "repository").resolve()
    removed_files = 0
    for name in sorted(LEGACY_MIRROR_NAMES):
        target = root / "repository" / name
        if not target.exists():
            continue
        resolved = target.resolve()
        if resolved.parent != mirror_parent or resolved.name not in LEGACY_MIRROR_NAMES:
            raise RuntimeError(f"unsafe legacy cleanup target: {resolved}")
        for package in sorted(zip_files(target)):
            info, _ = classify_zip(package)
            move_out_of_catalog(package, root, "archiv" if info else "defekt")
        removed_files += sum(1 for path in target.rglob("*") if path.is_file())
        shutil.rmtree(target)
    for obsolete_installer in installer_paths(root / "repository"):
        if obsolete_installer.parent.resolve() != mirror_parent:
            raise RuntimeError(f"unsafe obsolete installer target: {obsolete_installer}")
        info, _ = classify_zip(obsolete_installer)
        move_out_of_catalog(obsolete_installer, root, "archiv" if info else "defekt")
    print(f"OK: removed {removed_files} files from legacy repository mirrors")


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def version_key(version: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.findall(r"\d+|[^\d]+", version)
    )


def category(info: AddonInfo) -> str:
    addon_id = info.addon_id.lower()
    if info.is_repository:
        return "repository"
    if addon_id.startswith("plugin."):
        parts = addon_id.split(".")
        kind = parts[1] if len(parts) > 2 else "other"
        return f"plugins/{kind}"
    if addon_id.startswith("script.module."):
        return "script/module"
    if addon_id.startswith("script."):
        parts = addon_id.split(".")
        kind = parts[1] if len(parts) > 2 else "other"
        return f"script/{kind}"
    if "video" in info.provides:
        return "plugins/video"
    if "audio" in info.provides:
        return "plugins/audio"
    return "script/module"


def candidates(root: Path) -> list[Path]:
    result = []
    installers = set(installer_paths(root) + installer_paths(root / "repository"))
    for path in zip_files(root):
        rel = path.relative_to(root)
        if path.is_symlink() or any(part in SKIP_PARTS for part in rel.parts):
            continue
        if rel.parts[0] in DOWNLOAD_ROOTS:
            continue
        if path in installers:
            continue
        result.append(path)
    return sorted(result)


def choose_source(paths: list[Path], root: Path) -> Path:
    def rank(path: Path) -> tuple[int, int, str]:
        rel = path.relative_to(root)
        in_inbox = bool(rel.parts and rel.parts[0].lower() == "zips")
        return (0 if in_inbox else 1, len(rel.parts), rel.as_posix().lower())
    return sorted(paths, key=rank)[0]


def index_document(title: str, entries: list[tuple[str, str, str]], *, download_only: bool = False) -> str:
    download = ' download=""' if download_only else ""
    rows = "\n".join(
        f'<tr><td class="type">{"DIR" if href.endswith("/") else "ZIP"}</td>'
        f'<td><a href="{html.escape(quote(href, safe="/._-~"))}"{download}>{html.escape(name)}</a></td>'
        f'<td>{html.escape(version)}</td></tr>'
        for name, version, href in entries
    ) or '<tr><td colspan="3" class="empty">Keine ZIP-Dateien vorhanden.</td></tr>'
    return f"""---
layout: null
---
<!doctype html>
<html lang="de" data-bs-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css"><link rel="stylesheet" href="/repository/assets/theme.css"></head>
<body><main><nav class="site-menu" aria-label="Hauptmenü"><a class="site-brand" href="/repository/">Kodi-Wulf</a><div><a href="/repository/">Repository</a><a href="/repository/how-to-use.html">How-To-Use</a></div></nav><header class="hero compact"><p class="eyebrow">GitHub Pages Kodi Repository</p><h1>Kodi-Wulf Repository</h1><p class="subtitle">{html.escape(title)}</p></header><section class="panel"><div class="panel-head"><h2>Index</h2><a class="pill" href="/repository/">root</a></div><div class="table-responsive"><table><thead><tr><th>Typ</th><th>Name</th><th>Art</th></tr></thead><tbody>{rows}</tbody></table></div></section></main></body></html>
"""


def public_roots(root: Path) -> list[Path]:
    return sorted(
        (
            path for path in root.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and path.name not in TECHNICAL_ROOTS
        ),
        key=lambda path: path.name.lower(),
    )


def is_visible_directory(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if relative.parts and relative.parts[0] == "Repository":
        return False
    return not (len(relative.parts) > 1 and (relative.parts[0], relative.parts[1]) in LEGACY_MIRROR_BRANCHES)


def visible_zips(directory: Path, root: Path) -> list[Path]:
    result = []
    for path in zip_files(directory):
        relative = path.relative_to(root)
        if len(relative.parts) == 2 and relative.parts[0] == "repository" and (
            path.name.casefold().startswith((f"{REPO_ID}-v", "repository.kodiwulf-"))
        ):
            continue
        if is_visible_directory(path.parent, root):
            result.append(path)
    return result


def root_kind(name: str) -> str:
    lowered = name.lower()
    if lowered == "defekt":
        return "Defekt · nur Download"
    if lowered == "archiv":
        return "ZIP-Archiv"
    if lowered == "repository":
        return "Repository"
    if lowered in {"plugin", "plugins"}:
        return "Plugin"
    if lowered == "script":
        return "Script"
    return "Script-Erweiterung"


def menu_nodes(directory: Path, root: Path, depth: int = 0) -> list[dict[str, object]]:
    relative = directory.relative_to(root).as_posix()
    nodes = [{
        "name": directory.name,
        "path": relative,
        "href": quote(relative, safe="/._-~") + "/",
        "kind": root_kind(directory.name) if depth == 0 else "Unterordner",
        "depth": depth,
        "zip_count": len(visible_zips(directory, root)),
    }]
    if depth < 1 and directory.name not in DOWNLOAD_ROOTS:
        for child in sorted((item for item in directory.iterdir() if item.is_dir() and not item.name.startswith(".") and is_visible_directory(item, root)), key=lambda item: item.name.lower()):
            nodes.extend(menu_nodes(child, root, depth + 1))
    return nodes


def ensure_generic_index(directory: Path, root: Path) -> None:
    entries = [(f"{child.name}/", "Ordner", f"{child.name}/") for child in sorted((item for item in directory.iterdir() if item.is_dir() and not item.name.startswith(".")), key=lambda item: item.name.lower())]
    entries.extend(
        (zip_path.name, "ZIP", zip_path.relative_to(directory).as_posix())
        for zip_path in sorted(zip_files(directory), key=lambda item: item.name.lower())
    )
    (directory / "index.html").write_text(index_document(f"Kodi-Wulf / {directory.relative_to(root).as_posix()}", entries), encoding="utf-8")


def write_browse_indexes(root: Path) -> None:
    for name in sorted(DOWNLOAD_ROOTS):
        directory = root / name
        if directory.is_dir():
            entries = [
                (path.name, root_kind(name), path.relative_to(directory).as_posix())
                for path in sorted(visible_zips(directory, root))
            ]
            (directory / "index.html").write_text(index_document(f"Kodi-Wulf / {root_kind(name)}", entries, download_only=True), encoding="utf-8")
    repository = root / "repository"
    if repository.is_dir():
        ensure_generic_index(repository, root)
        for addon_dir in (
            path
            for path in repository.iterdir()
            if path.is_dir() and not path.name.startswith(".") and any(path.glob("*.zip"))
        ):
            ensure_generic_index(addon_dir, root)
    for parent_name in ("plugins", "script"):
        parent = root / parent_name
        if not parent.is_dir():
            continue
        children = sorted(
            (path for path in parent.iterdir() if path.is_dir() and not path.name.startswith(".")),
            key=lambda path: path.name.lower(),
        )
        entries = [(f"{child.name}/", "Ordner", f"{child.name}/") for child in children]
        (parent / "index.html").write_text(index_document(f"Kodi-Wulf / {parent_name}", entries), encoding="utf-8")
        for child in children:
            ensure_generic_index(child, root)
            for addon_dir in (
                path
                for path in child.iterdir()
                if path.is_dir() and not path.name.startswith(".") and any(path.glob("*.zip"))
            ):
                ensure_generic_index(addon_dir, root)


def write_site_root(root: Path) -> None:
    items = []
    for zip_path in installer_paths(root):
        items.append({"path": zip_path.name, "name": zip_path.name, "url": quote(zip_path.name, safe="/._-~"), "category": "installer", "size": zip_path.stat().st_size, "size_label": format_size(zip_path.stat().st_size)})
    roots = [directory for directory in public_roots(root) if directory.name in NAVIGATION_ROOTS | DOWNLOAD_ROOTS]
    tree_roots = []
    for directory in roots:
        top = directory.name
        zip_count = 0
        for zip_path in sorted(visible_zips(directory, root)):
            rel = zip_path.relative_to(root).as_posix()
            items.append({"path": rel, "name": zip_path.name, "url": quote(rel, safe="/._-~"), "category": top, "size": zip_path.stat().st_size, "size_label": format_size(zip_path.stat().st_size)})
            zip_count += 1
        tree_roots.append({
            "name": top,
            "href": quote(top, safe="._-~") + "/",
            "kind": root_kind(top),
            "download_only": top in DOWNLOAD_ROOTS,
            "zip_count": zip_count,
        })
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    menu = [node for directory in roots for node in menu_nodes(directory, root)]
    tree = {"roots": tree_roots, "menu": menu, "total_zips": len(items)}
    data_dir = root / "_data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "repository_tree.json").write_text(
        json.dumps(tree, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (assets / "files.js").write_text(
        "window.KODIWULF_FILES=" + json.dumps(items, ensure_ascii=False, separators=(",", ":")) + ";\n"
        + "window.KODIWULF_TREE=" + json.dumps(tree, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    static_links = "\n".join(
        f'<a href="{quote(directory.name, safe="._-~")}/">{html.escape(directory.name)}/</a>'
        for directory in roots if directory.name in NAVIGATION_ROOTS
    )
    installer_links = "\n".join(
        f'<a href="{quote(zip_path.name, safe="/._-~")}">{html.escape(zip_path.name)}</a>'
        for zip_path in installer_paths(root)
    )
    installers = installer_paths(root)
    current_installer = installers[-1].name if installers else installer_name()
    kodi_links = "\n".join(part for part in (static_links, installer_links) if part)
    root_doc = """---
layout: null
---
<!doctype html>
<html lang="de" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Kodi-Wulf Repository</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css">
  <link rel="stylesheet" href="{{ '/assets/theme.css' | relative_url }}">
</head>
<body>
<nav class="kodi-static" aria-label="Kodi ZIP index">__KODI_LINKS__</nav>
<main>
  <nav class="site-menu" aria-label="Hauptmenü">
    <a class="site-brand" href="{{ '/' | relative_url }}">Kodi-Wulf</a>
    <div><a href="{{ '/' | relative_url }}">Repository</a><a href="{{ '/how-to-use.html' | relative_url }}">How-To-Use</a></div>
  </nav>
  <header class="hero hero-banner animate-target">
    <div class="hero-scrim"></div>
    <div class="hero-content">
      <p class="eyebrow">GitHub Pages · Kodi Repository</p>
      <div class="brand-line">
        <h1 class="brand-stage" aria-label="Kodi-Wulf Repository">
          <span class="brand-layer brand-shadow" aria-hidden="true">Kodi-<span class="brand-x">W</span>ulf <span class="brand-r">R</span>epository</span>
          <span class="brand-layer brand-solid">Kodi-<span class="brand-x">W</span>ulf <span class="brand-r">R</span>epository</span>
        </h1>
        <div class="terminal-stage" aria-live="polite" aria-label="Kodi-Wulf Features">
          <span class="terminal-layer terminal-shadow" aria-hidden="true"><span data-terminal-shadow></span><i>_</i></span>
          <span class="terminal-layer terminal-solid"><span data-terminal-solid></span><i>_</i></span>
        </div>
      </div>
      <p class="subtitle">ZIP-Browser für Kodi-Plugins und Repository-Pakete. Direkte Downloads, klare Ordnerwege, flüssige Navigation.</p>
      <div class="hero-actions">
        <a class="btn btn-danger install-button" href="__INSTALLER__"><span>ZIP</span> Repository __VERSION__ installieren</a>
        <span id="react-summary" class="package-count">Pakete werden geladen …</span>
      </div>
    </div>
  </header>

  <section class="browser-shell animate-target" aria-label="Kodi-Wulf ZIP Browser">
    <div class="browser-heading">
      <div><p class="eyebrow">Install from ZIP</p><h2>Repository Browser</h2></div>
      <div id="vue-runtime" class="runtime-pill">Navigation bereit</div>
    </div>
    <div id="react-browser"></div>
  </section>

  <nav class="jekyll-fallback" aria-label="Jekyll Navigation">
    {% for node in site.data.repository_tree.roots %}
      {% if node.download_only %}
        <form action="{{ node.href | relative_url }}" method="get"><button type="submit">{{ node.kind }}</button></form>
      {% else %}
        <a href="{{ node.href | relative_url }}">{{ node.name }}/</a>
      {% endif %}
    {% endfor %}
  </nav>
  <footer><span id="svelte-status">Kodi-Wulf Repository wird initialisiert …</span></footer>
</main>

<noscript><p class="noscript">JavaScript erweitert die Website. Kodi und die Jekyll-Ordnerlinks bleiben ohne JavaScript verwendbar.</p></noscript>
<script defer src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js"></script>
<script defer src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script defer src="{{ '/assets/files.js' | relative_url }}"></script>
<script type="module" src="{{ '/assets/app.js' | relative_url }}"></script>
<script type="module" src="{{ '/assets/svelte-status.js' | relative_url }}"></script>
</body></html>
"""
    root_doc = root_doc.replace("__KODI_LINKS__", kodi_links)
    root_doc = root_doc.replace("__INSTALLER__", quote(current_installer, safe="/._-~"))
    root_doc = root_doc.replace("__VERSION__", current_installer.removeprefix(f"{REPO_ID}-").removesuffix(".zip"))
    (root / "index.html").write_text(root_doc, encoding="utf-8")
    write_how_to_use(root, current_installer)


def write_how_to_use(root: Path, current_installer: str) -> None:
    document = f"""---
layout: null
---
<!doctype html>
<html lang="de" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>How-To-Use · Kodi-Wulf Repository</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css">
  <link rel="stylesheet" href="{{{{ '/assets/theme.css' | relative_url }}}}">
</head>
<body><main>
  <nav class="site-menu" aria-label="Hauptmenü">
    <a class="site-brand" href="{{{{ '/' | relative_url }}}}">Kodi-Wulf</a>
    <div><a href="{{{{ '/' | relative_url }}}}">Repository</a><a aria-current="page" href="{{{{ '/how-to-use.html' | relative_url }}}}">How-To-Use</a></div>
  </nav>
  <article class="howto-shell">
    <header class="howto-header"><p class="eyebrow">Kodi FileManager</p><h1>Kodi-Wulf installieren</h1><p>Die Quelle einmal im Datei-Manager eintragen, danach die Repository-ZIP installieren.</p></header>
    <section class="howto-panel"><h2>1. Dateiquelle hinzufügen</h2><ol class="step-list">
      <li>In Kodi <strong>Einstellungen</strong> öffnen und <strong>Dateimanager</strong> wählen.</li>
      <li><strong>Quelle hinzufügen</strong> und anschließend <strong>&lt;Keine&gt;</strong> auswählen.</li>
      <li>Diese Adresse exakt eingeben: <code class="code-value">{html.escape(DEFAULT_BASE_URL)}</code></li>
      <li>Als Namen <strong>Kodi-Wulf</strong> eintragen und mit <strong>OK</strong> bestätigen.</li>
    </ol></section>
    <section class="howto-panel"><h2>2. Repository-ZIP installieren</h2><ol class="step-list">
      <li>Zu <strong>Add-ons → Aus ZIP-Datei installieren</strong> wechseln.</li>
      <li>Falls Kodi fragt, die Installation aus unbekannten Quellen in den Systemeinstellungen erlauben.</li>
      <li>Die Quelle <strong>Kodi-Wulf</strong> öffnen.</li>
      <li><a class="inline-download" href="{html.escape(quote(current_installer, safe='/._-~'))}">{html.escape(current_installer)}</a> auswählen.</li>
      <li>Auf die Meldung <strong>„Add-on installiert“</strong> warten.</li>
    </ol></section>
    <section class="howto-panel"><h2>3. Add-ons aus Kodi-Wulf installieren</h2><ol class="step-list">
      <li><strong>Aus Repository installieren</strong> öffnen.</li>
      <li><strong>Kodi-Wulf</strong> auswählen und das gewünschte Add-on installieren.</li>
    </ol><p class="notice">Die Quelle muss mit <strong>https://</strong> beginnen und auf <strong>/repository/</strong> enden.</p></section>
  </article>
</main></body></html>
"""
    (root / "how-to-use.html").write_text(document, encoding="utf-8")


def write_metadata(directory: Path, infos: list[AddonInfo]) -> None:
    latest: dict[str, AddonInfo] = {}
    for info in infos:
        current = latest.get(info.addon_id)
        if current is None or version_key(info.version) > version_key(current.version):
            latest[info.addon_id] = info
    root = ET.Element("addons")
    for info in sorted(latest.values(), key=lambda item: item.addon_id.lower()):
        root.append(ET.fromstring(info.addon_xml))
    data = pretty_xml(root)
    (directory / "addons.xml").write_bytes(data)
    (directory / "addons.xml.md5").write_text(hashlib.md5(data).hexdigest(), encoding="utf-8")


def make_repo_xml(categories: list[str], base_url: str, version: str) -> bytes:
    dirs = []
    for name in categories:
        url = base_url.rstrip("/") + "/" + name.strip("/") + "/"
        dirs.append(f"""    <dir>
      <info compressed="false">{html.escape(url)}addons.xml</info>
      <checksum>{html.escape(url)}addons.xml.md5</checksum>
      <datadir zip="true">{html.escape(url)}</datadir>
      <hashes>false</hashes>
    </dir>""")
    return (f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="{REPO_ID}" name="{REPO_NAME}" version="{html.escape(version)}" provider-name="{REPO_PROVIDER}">
  <extension point="xbmc.addon.repository" name="{REPO_NAME}">
{chr(10).join(dirs)}
  </extension>
  <extension point="xbmc.addon.metadata">
    <summary lang="de_DE">Kodi-Wulf Add-on-Repository</summary>
    <description lang="de_DE">Kodi-Wulf Repository für direkt installierbare Kodi ZIP-Pakete.</description>
    <platform>all</platform>
    <assets><icon>icon.png</icon><fanart>fanart.png</fanart></assets>
  </extension>
</addon>
""").encode("utf-8")


def write_repo_zip(root: Path, xml: bytes, version: str) -> Path:
    target = root / installer_name(version)
    members = {f"{REPO_ID}/addon.xml": xml}
    for source_name, addon_name in (("icon.png", "icon.png"), ("bg.png", "fanart.png")):
        source = root / source_name
        if source.is_file():
            members[f"{REPO_ID}/{addon_name}"] = source.read_bytes()
    reusable = False
    if target.is_file():
        try:
            with zipfile.ZipFile(target) as current:
                reusable = sorted(current.namelist()) == sorted(members) and all(
                    current.read(name) == content for name, content in members.items()
                )
        except (zipfile.BadZipFile, RuntimeError, zlib.error):
            pass
    if not reusable:
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            staged = Path(temporary) / target.name
            with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, content in members.items():
                    archive.writestr(name, content)
            if target.exists():
                info, _ = classify_zip(target)
                preserve_zip(target, visible_archive_path(target, root), root / ("archiv" if info else "defekt"), copy=True)
            staged.replace(target)
    for obsolete in installer_paths(root):
        if obsolete.resolve() != target.resolve():
            info, _ = classify_zip(obsolete)
            move_out_of_catalog(obsolete, root, "archiv" if info else "defekt")
    return target


def catalog_directories(root: Path) -> list[Path]:
    category_dirs = [root / "repository"] if (root / "repository").is_dir() else []
    category_dirs.extend(
        path
        for parent in (root / "plugins", root / "script")
        if parent.is_dir()
        for path in parent.iterdir()
        if path.is_dir()
    )
    return category_dirs


def write_catalog(root: Path, grouped: dict[str, list[AddonInfo]], base_url: str, version: str) -> Path:
    for directory in catalog_directories(root):
        write_metadata(directory, grouped.get(directory.relative_to(root).as_posix(), []))
    write_metadata(root, [info for infos in grouped.values() for info in infos])
    return write_repo_zip(root, make_repo_xml(sorted(grouped), base_url, version), version)


def repair_catalog_zip(path: Path, root: Path, info: AddonInfo) -> AddonInfo:
    def preserve_original(original: Path) -> None:
        preserve_zip(original, visible_archive_path(original, root), root / "archiv", copy=True)

    if normalize_package_root(path, info.addon_id, preserve_original=preserve_original):
        print(f"OK: normalized Kodi ZIP root: {path.relative_to(root)}")
    if repair_zip(path, preserve_original=preserve_original):
        print(f"OK: repaired Kodi ZIP artwork: {path.relative_to(root)}")
        return parse_addon_zip(path)
    return info


def build(root: Path, base_url: str, version: str, apply: bool, backup: Path | None, site_only: bool = False, installer_only: bool = False, cleanup_legacy: bool = False) -> None:
    require_no_publish_symlinks(root)
    if installer_only:
        grouped: dict[str, list[AddonInfo]] = defaultdict(list)
        for directory in catalog_directories(root):
            for zip_path in sorted(visible_zips(directory, root)):
                info, error = classify_zip(zip_path)
                if info is None:
                    print(f"WARN: Defekt: {zip_path.relative_to(root)}: {error}")
                    move_out_of_catalog(zip_path, root, "defekt")
                    continue
                if zip_path != directory / info.addon_id / f"{info.addon_id}-{info.version}.zip":
                    raise RuntimeError(f"noncanonical package, run --apply: {zip_path.relative_to(root)}")
                grouped[directory.relative_to(root).as_posix()].append(repair_catalog_zip(zip_path, root, info))
        if not grouped:
            raise RuntimeError("no repository metadata categories found")
        target = write_catalog(root, grouped, base_url, version)
        print(f"OK: installer generated: {target.name}; categories: {', '.join(sorted(grouped))}")
        return
    if site_only:
        write_browse_indexes(root)
        write_site_root(root)
        print("OK: Kodi browse indexes, Jekyll navigation and frontend data generated")
        return
    by_identity: dict[tuple[str, str], list[tuple[Path, AddonInfo]]] = defaultdict(list)
    invalid: list[Path] = []
    for path in candidates(root):
        info, error = classify_zip(path)
        if info is None:
            invalid.append(path)
            print(f"WARN: Defekt: {path.relative_to(root)}: {error}")
            continue
        by_identity[(info.addon_id, info.version)].append((path, info))

    selected = []
    for items in by_identity.values():
        source = choose_source([path for path, _ in items], root)
        info = next(info for path, info in items if path == source)
        selected.append((source, info, items))

    if not apply:
        for source, info, _ in sorted(selected, key=lambda item: item[1].addon_id.lower()):
            print(f"{source.relative_to(root)} -> {category(info)}/{info.addon_id}/{info.addon_id}-{info.version}.zip")
        print(f"DRY-RUN: {len(selected)} unique add-ons")
        return

    for source in invalid:
        move_out_of_catalog(source, root, "defekt")

    def archive_duplicate(path: Path) -> Path:
        if backup is not None:
            preserve_zip(path, path.relative_to(root), backup, copy=True)
        return move_out_of_catalog(path, root, "archiv")

    grouped = defaultdict(list)
    pending_sources = {source: source for source, _, _ in selected}
    published_paths = set()
    for original, info, items in selected:
        source = pending_sources.pop(original)
        group = category(info)
        destination = root / group / info.addon_id / f"{info.addon_id}-{info.version}.zip"
        if destination.is_symlink() or not destination.parent.resolve().is_relative_to(root):
            raise RuntimeError(f"unsafe Kodi package destination: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            if destination.exists() and digest(destination) != digest(source):
                archived = archive_duplicate(destination)
                if destination in pending_sources:
                    pending_sources[destination] = archived
                print(f"WARN: previous ZIP preserved in archiv/: {destination.relative_to(root)}")
            if not destination.exists():
                source.rename(destination)
            elif source.exists():
                archive_duplicate(source)
        grouped[group].append(repair_catalog_zip(destination, root, info))
        published_paths.add(destination)
        for duplicate, _ in items:
            if duplicate == original or duplicate in published_paths or not duplicate.exists():
                continue
            archive_duplicate(duplicate)

    remove_empty_import_tree(root)
    if cleanup_legacy:
        cleanup_legacy_mirrors(root)
    repo_zip = write_catalog(root, grouped, base_url, version)
    write_browse_indexes(root)
    write_site_root(root)
    print(f"OK: {sum(map(len, grouped.values()))} installable ZIPs; {len(invalid)} defective ZIPs preserved; installer: {repo_zip.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--repo-version", default=DEFAULT_REPO_VERSION)
    parser.add_argument("--backup", type=Path, help="Keep an additional private copy of superseded ZIPs")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--site-only", action="store_true", help="Only regenerate Jekyll/frontend navigation without moving ZIPs")
    parser.add_argument("--installer-only", action="store_true", help="Only rebuild the root repository installer ZIP")
    parser.add_argument("--cleanup-legacy", action="store_true", help="Remove obsolete repository/{plugins,repository,script} mirror trees")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.cleanup_legacy and not args.apply:
        require_no_publish_symlinks(root)
        cleanup_legacy_mirrors(root)
        if not (args.apply or args.site_only or args.installer_only):
            return
    build(root, args.base_url, args.repo_version, args.apply, args.backup.resolve() if args.backup else None,
          args.site_only, args.installer_only, args.cleanup_legacy)


if __name__ == "__main__":
    main()
