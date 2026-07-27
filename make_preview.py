#!/usr/bin/env python3
"""Byg en lokal forhaandsvisning af siden - uden webserver.

index.html henter news.json med fetch(), og det blokerer browseren naar en
side aabnes direkte som fil (file://). Derfor bager dette script nyhederne
ind i en kopi, saa den kan aabnes med et dobbeltklik og ser ud praecis som
den vil goere online.

    python make_preview.py

Resultatet er preview-local.html, som ligger i .gitignore og aldrig
kommer med i repoet.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "index.html"
DATA = ROOT / "news.json"
OUT = ROOT / "preview-local.html"

# Erstat netvaerkskaldet med de faktiske data
FETCH_RE = re.compile(
    r'fetch\("news\.json",\s*\{[^}]*\}\)\s*\n\s*\.then\(function\(r\)\{[^\n]*\n',
    re.M)


def main():
    if not SRC.exists() or not DATA.exists():
        sys.exit("index.html eller news.json mangler")

    html = SRC.read_text(encoding="utf-8")
    data = json.loads(DATA.read_text(encoding="utf-8"))

    inlined = "Promise.resolve(NEWS_DATA)\n"
    new_html, n = FETCH_RE.subn(inlined, html)
    if n != 1:
        sys.exit(f"kunne ikke finde fetch-kaldet i index.html (fandt {n})")

    # Laeg data ind foerst i scriptet, og marker siden som forhaandsvisning
    banner = (
        '<div style="position:sticky;top:0;z-index:99;background:#b26a00;color:#fff;'
        'font:600 12px/1.6 system-ui;text-align:center;padding:5px">'
        'FORHÅNDSVISNING &ndash; lokal kopi, ikke offentliggjort</div>')
    new_html = new_html.replace("<body>", "<body>\n" + banner, 1)
    new_html = new_html.replace(
        "<script>\n(function(){",
        "<script>\nvar NEWS_DATA = " + json.dumps(data, ensure_ascii=False) +
        ";\n(function(){", 1)

    # Service workeren skal ikke blande sig i en lokal kopi
    new_html = re.sub(r'if \("serviceWorker" in navigator[\s\S]*?\n  \}\n',
                      "", new_html, count=1)

    OUT.write_text(new_html, encoding="utf-8")
    print(f"OK: {OUT.name} bygget - {data.get('count', '?')} nyheder bagt ind")
    print(f"Aabn: {OUT}")


if __name__ == "__main__":
    main()
