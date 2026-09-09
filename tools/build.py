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
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

from kodiwulf_build_repo_core import AddonInfo, normalize_package_root, parse_addon_zip, pretty_xml
from repair_addon_assets import repair_tree


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://kodi-wulf.github.io/repository/"
REPO_ID = "repository.kodi-wulf"
REPO_NAME = "Kodi-Wulf"
REPO_PROVIDER = "Kodi-Wulf"
DEFAULT_REPO_VERSION = "1.33.7a"
NAVIGATION_ROOTS = {"plugins", "repository", "script"}
SKIP_PARTS = {".git", ".drdebug-backups", "__pycache__"}
TECHNICAL_ROOTS = {
    ".github", "Repository", "_data", "_layouts", "_site", "assets", "node_modules", "tools"
}
LEGACY_MIRROR_BRANCHES = {("repository", "plugins"), ("repository", "repository"), ("repository", "script")}
LEGACY_MIRROR_NAMES = {"plugins", "repository", "script"}


def installer_name(version: str = DEFAULT_REPO_VERSION) -> str:
    return f"{REPO_ID}-v{version}.zip"


def installer_paths(root: Path) -> list[Path]:
    patterns = (f"{REPO_ID}-v*.zip", "repository.kodiwulf-*.zip")
    return sorted({path for pattern in patterns for path in root.glob(pattern) if path.is_file()})


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_import(path: Path, root: Path, backup: Path) -> Path:
    rel = path.relative_to(root)
    archived = backup / rel
    archived.parent.mkdir(parents=True, exist_ok=True)
    if archived.exists():
        archived = archived.with_name(archived.stem + "-" + digest(path)[:10] + archived.suffix)
    shutil.move(str(path), str(archived))
    return archived


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
        removed_files += sum(1 for path in target.rglob("*") if path.is_file())
        shutil.rmtree(target)
    for pattern in (f"{REPO_ID}-v*.zip", "repository.kodiwulf-*.zip"):
        for obsolete_installer in (root / "repository").glob(pattern):
            if obsolete_installer.parent.resolve() != mirror_parent:
                raise RuntimeError(f"unsafe obsolete installer target: {obsolete_installer}")
            obsolete_installer.unlink()
            removed_files += 1
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
    for path in root.rglob("*.zip"):
        rel = path.relative_to(root)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if rel.parts and rel.parts[0] == "Repository":
            continue
        if len(rel.parts) > 1 and (rel.parts[0], rel.parts[1]) in LEGACY_MIRROR_BRANCHES:
            continue
        if len(rel.parts) == 1 and path in installer_paths(root):
            continue
        result.append(path)
    return sorted(result)


def choose_source(paths: list[Path], root: Path) -> Path:
    def rank(path: Path) -> tuple[int, int, str]:
        rel = path.relative_to(root)
        in_inbox = bool(rel.parts and rel.parts[0].lower() == "zips")
        return (0 if in_inbox else 1, len(rel.parts), rel.as_posix().lower())
    return sorted(paths, key=rank)[0]


def index_document(title: str, entries: list[tuple[str, str, str]]) -> str:
    rows = "\n".join(
        f'<tr><td class="type">{"DIR" if href.endswith("/") else "ZIP"}</td>'
        f'<td><a href="{html.escape(quote(href, safe="/._-~"))}">{html.escape(name)}</a></td>'
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
    for path in directory.rglob("*.zip"):
        relative = path.relative_to(root)
        if len(relative.parts) == 2 and relative.parts[0] == "repository" and (
            path.name.startswith(f"{REPO_ID}-v") or path.name.startswith("repository.kodiwulf-")
        ):
            continue
        if is_visible_directory(path.parent, root):
            result.append(path)
    return result


def root_kind(name: str) -> str:
    lowered = name.lower()
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
    if depth < 1:
        for child in sorted((item for item in directory.iterdir() if item.is_dir() and not item.name.startswith(".") and is_visible_directory(item, root)), key=lambda item: item.name.lower()):
            nodes.extend(menu_nodes(child, root, depth + 1))
    return nodes


def ensure_generic_index(directory: Path, root: Path) -> None:
    entries = [(f"{child.name}/", "Ordner", f"{child.name}/") for child in sorted((item for item in directory.iterdir() if item.is_dir() and not item.name.startswith(".")), key=lambda item: item.name.lower())]
    entries.extend(
        (zip_path.name, "ZIP", zip_path.relative_to(directory).as_posix())
        for zip_path in sorted(directory.rglob("*.zip"), key=lambda item: item.name.lower())
    )
    (directory / "index.html").write_text(index_document(f"Kodi-Wulf / {directory.relative_to(root).as_posix()}", entries), encoding="utf-8")


def write_browse_indexes(root: Path) -> None:
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
    roots = [directory for directory in public_roots(root) if directory.name in NAVIGATION_ROOTS]
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
        for directory in roots
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
    {% for node in site.data.repository_tree.roots %}<a href="{{ node.href | relative_url }}">{{ node.name }}/</a>{% endfor %}
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
    with tempfile.TemporaryDirectory() as temp:
        addon_dir = Path(temp) / REPO_ID
        addon_dir.mkdir()
        (addon_dir / "addon.xml").write_bytes(xml)
        if (root / "icon.png").is_file():
            shutil.copy2(root / "icon.png", addon_dir / "icon.png")
        if (root / "bg.png").is_file():
            shutil.copy2(root / "bg.png", addon_dir / "fanart.png")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(addon_dir.iterdir()):
                archive.write(file_path, f"{REPO_ID}/{file_path.name}")
    for obsolete in installer_paths(root):
        if obsolete.resolve() != target.resolve():
            obsolete.unlink()
    return target


def installer_categories(root: Path) -> list[str]:
    candidates = [root / "repository"]
    candidates.extend(path for parent in (root / "plugins", root / "script") if parent.is_dir() for path in parent.iterdir() if path.is_dir())
    return sorted(
        path.relative_to(root).as_posix()
        for path in candidates
        if (path / "addons.xml").is_file() and (path / "addons.xml.md5").is_file()
    )


def write_existing_metadata(root: Path) -> None:
    category_dirs = [root / "repository"]
    category_dirs.extend(
        path
        for parent in (root / "plugins", root / "script")
        if parent.is_dir()
        for path in parent.iterdir()
        if path.is_dir()
    )
    for directory in category_dirs:
        infos = []
        for zip_path in sorted(visible_zips(directory, root)):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    infos.append(parse_addon_zip(zip_path))
            except SystemExit:
                print(f"WARN: metadata skipped for invalid ZIP: {zip_path.relative_to(root)}")
        if infos:
            write_metadata(directory, infos)


def build(root: Path, base_url: str, version: str, apply: bool, backup: Path, site_only: bool = False, installer_only: bool = False) -> None:
    if installer_only:
        write_existing_metadata(root)
        categories = installer_categories(root)
        if not categories:
            raise RuntimeError("no repository metadata categories found")
        target = write_repo_zip(root, make_repo_xml(categories, base_url, version), version)
        print(f"OK: installer generated: {target.name}; categories: {', '.join(categories)}")
        return
    if site_only:
        write_browse_indexes(root)
        write_site_root(root)
        print("OK: Kodi browse indexes, Jekyll navigation and frontend data generated")
        return
    if apply:
        repair_tree(root)
    by_identity: dict[tuple[str, str], list[tuple[Path, AddonInfo]]] = defaultdict(list)
    invalid: list[tuple[Path, str, str]] = []
    for path in candidates(root):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                info = parse_addon_zip(path)
        except SystemExit:
            stem = path.stem
            match = re.match(r"((?:plugin|repository|script)\.[A-Za-z0-9._-]+)", stem, re.IGNORECASE)
            addon_id = match.group(1) if match else stem
            if addon_id.lower().startswith("plugin."):
                parts = addon_id.split(".")
                group = f"plugins/{parts[1] if len(parts) > 2 else 'other'}"
            elif addon_id.lower().startswith("repository."):
                group = "repository"
            else:
                group = "script/module"
            invalid.append((path, group, addon_id))
            print(f"WARN: invalid addon.xml; filename classification used: {path.relative_to(root)} -> {group}")
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

    backup.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[AddonInfo]] = defaultdict(list)
    final_paths: set[Path] = set()
    for source, info, items in selected:
        group = category(info)
        destination = root / group / info.addon_id / f"{info.addon_id}-{info.version}.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            if destination.exists() and digest(destination) != digest(source):
                archived = archive_import(destination, root, backup)
                print(f"WARN: replaced conflicting package; previous file archived: {archived}")
            if not destination.exists():
                shutil.move(str(source), str(destination))
        if normalize_package_root(destination, info.addon_id):
            print(f"OK: normalized Kodi ZIP root: {destination.relative_to(root)}")
        final_paths.add(destination.resolve())
        grouped[group].append(info)
        for duplicate, _ in items:
            if not duplicate.exists() or duplicate.resolve() in final_paths:
                continue
            archive_import(duplicate, root, backup)

    for source, group, addon_id in invalid:
        destination = root / group / addon_id / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            if destination.exists() and digest(destination) != digest(source):
                archived = archive_import(destination, root, backup)
                print(f"WARN: replaced conflicting unindexed package; previous file archived: {archived}")
            if destination.exists():
                archive_import(source, root, backup)
            else:
                shutil.move(str(source), str(destination))
        grouped.setdefault(group, [])

    remove_empty_import_tree(root)

    for group, infos in grouped.items():
        directory = root / group
        write_metadata(directory, infos)
        depth = len(Path(group).parts)
        prefix = "../" * depth
        entries = []
        for zip_path in sorted(directory.rglob("*.zip"), key=lambda item: item.name.lower()):
            rel = zip_path.relative_to(directory).as_posix()
            entries.append((zip_path.name, "ZIP", rel))
        (directory / "index.html").write_text(index_document(f"Kodi-Wulf / {group}", entries), encoding="utf-8")

    for parent_name in ("plugins", "script"):
        parent = root / parent_name
        parent.mkdir(exist_ok=True)
        children = sorted(path.name for path in parent.iterdir() if path.is_dir() and not path.name.startswith("."))
        entries = [(f"{child}/", "Ordner", f"{child}/") for child in children]
        (parent / "index.html").write_text(index_document(f"Kodi-Wulf / {parent_name}", entries), encoding="utf-8")
        for child in children:
            child_dir = parent / child
            if not (child_dir / "index.html").is_file():
                ensure_generic_index(child_dir, root)

    repo_xml = make_repo_xml(sorted(grouped), base_url, version)
    repo_zip = write_repo_zip(root, repo_xml, version)
    all_infos = [info for infos in grouped.values() for info in infos]
    write_metadata(root, all_infos)

    for directory in public_roots(root):
        if directory.name not in {"repository", "plugins", "script"}:
            ensure_generic_index(directory, root)
            for child in (item for item in directory.iterdir() if item.is_dir() and not item.name.startswith(".")):
                ensure_generic_index(child, root)
    write_browse_indexes(root)
    write_site_root(root)
    print(f"OK: {len(all_infos)} ZIPs classified; installer: {repo_zip.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--repo-version", default=DEFAULT_REPO_VERSION)
    parser.add_argument("--backup", default=str(ROOT.parent / "repository-zip-backup"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--site-only", action="store_true", help="Only regenerate Jekyll/frontend navigation without moving ZIPs")
    parser.add_argument("--installer-only", action="store_true", help="Only rebuild the root repository installer ZIP")
    parser.add_argument("--cleanup-legacy", action="store_true", help="Remove obsolete repository/{plugins,repository,script} mirror trees")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.cleanup_legacy:
        cleanup_legacy_mirrors(root)
        if not (args.apply or args.site_only or args.installer_only):
            return
    build(root, args.base_url, args.repo_version, args.apply, Path(args.backup).resolve(), args.site_only, args.installer_only)


if __name__ == "__main__":
    main()
