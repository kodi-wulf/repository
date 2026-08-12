#!/usr/bin/env python3
"""Audit Kodi-Wulf addon packages (non-destructive)."""
from __future__ import annotations
import json
import os
import re
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
    for candidate in ([f"{base}/{value}"] if base else []) + [value]:
        if candidate in members:
            return candidate
    return None


def github_repo_from_urls(urls: list[str]) -> str:
    for raw in urls:
        m = re.search(r"github\.com/([^/\s]+)/([^/#?\s]+)", raw, re.I)
        if m:
            return f"{m.group(1)}/{re.sub(r'\.git$', '', m.group(2))}"
    return ""


def github_get(path: str):
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "kodi-wulf-addon-audit"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(GITHUB_API + path, headers=headers)
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
                rec.status = "INVALID"; rec.notes.append("missing addon.xml"); return rec
            root = ET.fromstring(zf.read(addon_xml_name))
    except Exception as exc:
        rec.status = "INVALID"; rec.notes.append(f"ZIP/XML error: {exc}"); return rec

    rec.addon_id = root.attrib.get("id", "").strip()
    rec.version = root.attrib.get("version", "").strip()
    rec.name = root.attrib.get("name", rec.addon_id).strip()

    metadata = next((e for e in root.findall("extension") if e.attrib.get("point") == "xbmc.addon.metadata"), None)
    if metadata is None:
        rec.notes.append("missing xbmc.addon.metadata extension")
    else:
        assets = metadata.find("assets")
        if assets is None:
            rec.notes.append("missing <assets>")
        else:
            rec.icon = assets.findtext("icon", default="").strip()
            rec.fanart = assets.findtext("fanart", default="").strip()
            rec.icon_ok = bool(rec.icon and asset_path(members, addon_xml_name, rec.icon))
            rec.fanart_ok = bool(rec.fanart and asset_path(members, addon_xml_name, rec.fanart))

    req = root.find("requires")
    if req is not None:
        rec.dependencies = [x.attrib.get("addon", "") for x in req.findall("import") if x.attrib.get("addon")]

    urls = [root.findtext(f".//{tag}", default="").strip() for tag in ("source", "website", "forum")]
    rec.github_source = github_repo_from_urls([u for u in urls if u])

    if not rec.icon_ok:
        rec.status = "FAIL"; rec.notes.append("icon missing or not resolvable inside package")
    if rec.fanart and not rec.fanart_ok:
        rec.status = "FAIL"; rec.notes.append("fanart missing or not resolvable inside package")

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
            try:
                pushed = datetime.fromisoformat(rec.upstream_last_push.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - pushed).days > 1461:
                    rec.status = "DEAD" if rec.status == "OK" else rec.status
                    rec.notes.append("upstream inactive for more than 4 years")
            except Exception:
                pass
            if rec.upstream_archived:
                rec.status = "DEAD" if rec.status == "OK" else rec.status
        else:
            rec.upstream_error = err
    return rec


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted((ROOT / "zips").rglob("*.zip")) if (ROOT / "zips").is_dir() else []
    records = [audit_zip(p) for p in paths]
    counts = {s: sum(r.status == s for r in records) for s in ("OK", "FAIL", "DEAD", "INVALID")}
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "package_count": len(records), "counts": counts, "records": [asdict(r) for r in records]}
    (OUT / "addons-audit.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Kodi-Wulf Add-on Audit", "", f"Generated: {payload['generated_at']}", "", f"Packages: **{len(records)}**", "", "| Status | Count |", "|---|---:|"]
    lines += [f"| {s} | {c} |" for s, c in counts.items()]
    lines += ["", "## Packages", "", "| Add-on | Version | Icon | Upstream | Status | Notes |", "|---|---|---|---|---|---|"]
    for r in records:
        upstream = f"[{r.github_source}]({r.upstream_repo_url})" if r.github_source and r.upstream_repo_url else r.github_source or "—"
        lines.append(f"| `{r.addon_id}` | `{r.version}` | {'OK' if r.icon_ok else 'MISSING'} | {upstream} | **{r.status}** | {'; '.join(r.notes or [])} |")
    (OUT / "addons-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(counts, indent=2))
    return 0 if counts["FAIL"] == 0 and counts["INVALID"] == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
