#!/usr/bin/env python3
from __future__ import annotations

import argparse
from html import escape
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def render_page(path: Path) -> bytes:
    document = path.read_text(encoding="utf-8")
    document = re.sub(r"\A---\s*\nlayout: null\s*\n---\s*\n", "", document)
    document = document.replace('href="/repository/', 'href="/')
    document = re.sub(
        r"\{\{\s*'(/[^']+)'\s*\|\s*relative_url\s*\}\}",
        lambda match: match.group(1),
        document,
    )
    if path != ROOT / "index.html":
        return document.encode("utf-8")
    tree = json.loads((ROOT / "_data" / "repository_tree.json").read_text(encoding="utf-8"))
    links = []
    for item in tree["roots"]:
        href = escape(item["href"], quote=True)
        if item.get("download_only"):
            links.append(f'<form action="/{href}" method="get"><button type="submit">{escape(item["kind"])}</button></form>')
        else:
            links.append(f'<a href="/{href}">{escape(item["name"])}/</a>')
    document = re.sub(
        r"\{% for node in site\.data\.repository_tree\.roots %\}.*?\{% endfor %\}",
        lambda _: "".join(links),
        document,
        flags=re.DOTALL,
    )
    return document.encode("utf-8")


class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            path /= "index.html"
        if path.suffix == ".html" and path.is_file() and path.resolve().is_relative_to(ROOT):
            payload = render_page(path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the Jekyll entry page without a Ruby installation")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    handler = lambda *items, **kwargs: PreviewHandler(*items, directory=str(ROOT), **kwargs)
    print(f"Kodi-Wulf preview: http://127.0.0.1:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
