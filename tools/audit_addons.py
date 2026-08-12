#!/usr/bin/env python3
"""Audit Kodi-Wulf addon packages.

The audit is deliberately non-destructive. It verifies package structure,
assets and dependencies and, when addon metadata exposes a GitHub source,
checks the upstream repository's activity and releases.

Output: audit/addons-audit.json and audit/addons-audit.md
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "audit"
GITHUB_API = "https://api.github.com"

@dataclass
class Record:
    package: str
    addon_id: str = ""
    version: str = ""
    name: str = ""
    icon: str = ""
    icon_ok: bool = False
    fanart: str = ""
    fanart_ok: bool = False
    dependencies: list[str] | None = None
    github_source: str = ""
    upstream_archived: bool | None = None
    upstream_last_push: str = ""
    upstream_latest_release: str = ""
    upstream_repo_url: str = ""
    upstream_error: str = ""
    status: str = "OK"
    notes: list[str] | None = None


def zip_members(zf: zipfile.ZipFile) -> set[str]:
    return {n.replace("\\", "/").lstrip("./") for n in zf.namelist() if not n.endswith("/")}


def find_addon_xml(members: set[str]) -> str | None:
    candidates = [m for m in members if m == "addon.xml" or m.endswith("/addon.xml")]
    candidates.sort(key=lambda x: (x.count("/"), x))
    return candidates[0] if candidates else None


def asset_path(members: set[str], addon_xml_name: str, value: str) -> str | None:
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in Path(value).parts:
        return None
    base = addon_xml_name.rsplit("/", 1)[0] if "/" in addon_xml_name else ""
    candidates = []
    if base:
        candidates.append(f"{base}/{value}")
    candidates.append(value)
    for candidate in candidates:
        if candidate in members:
            return candidate
    return None


def github_repo_from_urls(urls: list[str]) -> str:
    for raw in urls:
        if not raw:
            continue
        m = re.search(r"github\\.com/([^/\\s]+)/([^/#?\\s]+)", raw, re.I)
        if m:
            owner, repo = m.group(1), re.sub(r"\\.git$", "", m.group(2))
            return f"{owner}/{repo}"
    return ""


def github_get(path: str) -> tuple[dict | list | None, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(GITHUB_API + path, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "kodi-wulf-addon-audit",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response), ""
    except Exception as exc:
        return None, str(exc)


def audit_zip(path: Path) -> Record:
    rec = Record(package=str(path.relative_to(ROOT)), dependencies=[], notes=[])
    try:
        with zipfile.ZipFile(path) as zf:
            members = zip_members(zf)
            addon_xml_name = find_addon_xml(members)
            if not addon_xml_name:
                rec.status = "INVALID"
                rec.notes.append("missing addon.xml")
                return rec
            root = ET.fromstring(zf.read(addon_xml_name))
    except Exception as exc:
        rec.status = "INVALID"
        rec.notes.append(f"ZIP/XML error: {exc}")
        return rec

    rec.addon_id = root.attrib.get("id", "").strip()
    rec.version = root.attrib.get("version", "").strip()
    rec.name = root.attrib.get("name", rec.addon_id).strip()

    metadata = None
    for ext in root.findall("extension"):
        if ext.attrib.get("point") == "xbmc.addon.metadata":
            metadata = ext
            break
    if metadata is not None:
        assets = metadata.find("assets")
        if assets is not None:
            icon = assets.findtext("icon", default="").strip()
            fanart = assets.findtext("fanart", default="").strip()
            rec.icon = icon
            rec.fanart = fanart
            if icon:
                rec.icon_ok = asset_path(members, addon_xml_name, icon) is not None
            if fanart:
                rec.fanart_ok = asset_path(members, addon_xml_name, fanart) is not None
        else:
            rec.notes.append("missing <assets>")
    else:
        rec.notes.append("missing xbmc.addon.metadata extension")

    req = root.find("requires")
    if req is not None:
        rec.dependencies = [x.attrib.get("addon", "") for x in req.findall("import") if x.attrib.get("addon")]

    urls = []
    for tag in ("source", "website", "forum"):
        value = root.findtext(f".//{tag}", default="").strip()
        if value:
            urls.append(value)
    rec.github_source = github_repo_from_urls(urls)

    if not rec.icon_ok:
        rec.status = "FAIL"
        rec.notes.append("icon missing or not resolvable inside package")
    if rec.fanart and not rec.fanart_ok:
        rec.status = "FAIL"
        rec.notes.append("fanart missing or not resolvable inside package")

    if rec.github_source:
        data, err = github_get("/repos/" + rec.github_source)
        if isinstance(data, dict):
            rec.upstream_archived = bool(data.get("archived", False))
            rec.upstream_last_push = data.get("pushed_at", "") or ""
            rec.upstream_repo_url = data.get("html_url", "") or ""
            release, rerr = github_get("/repos/" + rec.github_source + "/releases/latest")
            if isinstance(release, dict):
                rec.upstream_latest_release = release.get("tag_name", "") or release.get("name", "")
            elif rerr and "HTTP Error 404" not in rerr:
                rec.upstream_error = rerr
            if rec.upstream_archived:
                rec.status = "DEAD" if rec.status == "OK" else rec.status
            try:
                pushed = datetime.fromisoformat(rec.upstream_last_push.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - pushed).days
                if age_days > 1461:
                    rec.status = "DEAD" if rec.status == "OK" else rec.status
                    rec.notes.append(f"upstream inactive for {age_days} days")
            except Exception:
                pass
        else:
            rec.upstream_error = err

    return rec


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted((ROOT / "zips").rglob("*.zip")) if (ROOT / "zips").is_dir() else []
    records = [audit_zip(p) for p in paths]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "package_count": len(records),
        "counts": {s: sum(r.status == s for r in records) for s in ("OK", "FAIL", "DEAD", "INVALID")},
        "records": [asdict(r) for r in records],
    }
    (OUT / "addons-audit.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = ["# Kodi-Wulf Add-on Audit", "", f"Generated: {payload['generated_at']}", "", f"Packages: **{len(records)}**", "", "| Status | Count |", "|---|---:|"]
    for status, count in payload["counts"].items():
        lines.append(f"| {status} | {count} |")
    lines += ["", "## Packages", "", "| Add-on | Version | Icon | Upstream | Status | Notes |", "|---|---|---|---|---|---|"]
    for r in records:
        icon = "OK" if r.icon_ok else "MISSING"
        upstream = f"[{r.github_source}]({r.upstream_repo_url})" if r.github_source and r.upstream_repo_url else r.github_source or "—"
        notes = "; ".join(r.notes or [])
        lines.append(f"| `{r.addon_id}` | `{r.version}` | {icon} | {upstream} | **{r.status}** | {notes} |")
    (OUT / "addons-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], indent=2))
    return 0 if payload["counts"]["FAIL"] == 0 and payload["counts"]["INVALID"] == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
