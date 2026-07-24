#!/usr/bin/env python3
"""Køres af GitHub Actions når en 'Ny kilde'-issue oprettes.

Læser URL og navn fra issue-teksten, validerer at siden kan crawles,
og føjer den til sources.json. Skriver et svar til comment.txt.
Exit-kode 0 = tilføjet, 1 = afvist.
"""
import json
import os
import re
import sys
from pathlib import Path

import crawler

ROOT = Path(__file__).parent
COMMENT = ROOT / "comment.txt"


def reply(msg, ok):
    COMMENT.write_text(msg, encoding="utf-8")
    print(msg)
    sys.exit(0 if ok else 1)


def field(body, name):
    m = re.search(rf"###\s*{name}\s*\n+\s*([^\n]+)", body, re.I)
    val = m.group(1).strip() if m else ""
    return "" if val in ("_No response_", "None") else val


def main():
    body = os.environ.get("ISSUE_BODY", "")
    url = field(body, "URL")
    label = field(body, "Navn") or crawler.host_of(url or "")

    if not url.startswith(("http://", "https://")):
        reply("Afvist: URL'en mangler eller er ugyldig. Den skal starte med https://", False)

    sources = json.loads(crawler.SOURCES_FILE.read_text(encoding="utf-8"))
    if any(s["url"].rstrip("/") == url.rstrip("/") for s in sources):
        reply(f"Kilden findes allerede i listen: {url}", False)

    try:
        html = crawler.fetch(url)
    except Exception as e:  # noqa: BLE001
        reply(f"Afvist: Kunne ikke hente siden ({e}).", False)

    parser = crawler.pick_parser(url)
    items = parser(html, url, label)
    if not items:
        reply("Afvist: Kunne ikke finde nyhedsoverskrifter på siden. "
              "Prøv evt. en anden side på samme websted (fx selve nyhedsoversigten).", False)

    sources.append({"url": url, "label": label})
    crawler.SOURCES_FILE.write_text(
        json.dumps(sources, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    eksempler = "\n".join(f"- {i['title']}" for i in items[:5])
    reply(f"Kilden **{label}** er tilføjet ({url}).\n\n"
          f"Fandt {len(items)} overskrifter, fx:\n{eksempler}\n\n"
          f"Nyhederne er med på siden om et øjeblik.", True)


if __name__ == "__main__":
    main()
