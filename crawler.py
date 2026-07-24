#!/usr/bin/env python3
"""Crawler: samler nyheder om danske marinaer og lystbådehavne.

Kilder: minbaad.dk, motorbaadsnyt.dk, flidhavne.dk.
Skriver news.json (merges med eksisterende, så arkivet vokser).
Køres automatisk af GitHub Actions – se .github/workflows/crawl.yml.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

NEWS_FILE = Path(__file__).parent / "news.json"
MAX_ITEMS = 600
HEADERS = {"User-Agent": "MarinaNyhederBot/1.0 (+https://github.com)"}

TYPO3_SOURCES = [
    ("m", "https://minbaad.dk/forside"),
    ("m", "https://minbaad.dk/havne"),
    ("m", "https://minbaad.dk/kort-nyt"),
    ("o", "https://motorbaadsnyt.dk/"),
    ("o", "https://motorbaadsnyt.dk/havne"),
]
FLID_SOURCES = [("f", "https://flidhavne.dk/nyheder/")]

EN_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
DA_MONTHS = {m: i + 1 for i, m in enumerate(
    "januar februar marts april maj juni juli august september oktober november december".split())}

ARTICLE_RE = re.compile(
    r"/nyhed/archive/(\d{4})/(\d{1,2})/([a-z]+)/article/([^/?#]+)")


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_typo3(html, default_src, base_url):
    """minbaad.dk / motorbaadsnyt.dk (TYPO3). Dato ligger i artikel-URL'en."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select('a[href*="/nyhed/archive/"]'):
        href = urljoin(base_url, a.get("href", ""))  # gør relative links absolutte
        m = ARTICLE_RE.search(href)
        if not m:
            continue
        year, day, month_name, slug = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        month = EN_MONTHS.get(month_name)
        if not month or not (1 <= day <= 31):
            continue
        title = clean(a.get("title")) or clean(a.get_text())
        if not title or len(title) < 8:
            continue
        src = "o" if "motorbaadsnyt.dk" in href else ("m" if "minbaad.dk" in href else default_src)
        items.append({
            "key": slug,
            "src": src,
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "title": title,
            "url": href,
        })
    return items


def parse_flid(html):
    """flidhavne.dk/nyheder (WordPress). Titler i h2>a, dato i entry-teksten."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for h in soup.find_all(["h2", "h3"]):
        a = h.find("a", href=re.compile(r"^https://flidhavne\.dk/[^/]+/?$"))
        if not a:
            continue
        title = clean(a.get_text())
        if not title or len(title) < 8:
            continue
        url = a["href"]
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if slug in ("nyheder", "koebsalg", "vidensbank", "medlemsblad",
                    "kontakt", "login-register", "medlemstilbud", "medlemshavne"):
            continue
        date = None
        container = h.parent
        for _ in range(4):
            if container is None:
                break
            text = container.get_text(" ", strip=True)
            dm = re.search(
                r"(\d{1,2})\.\s*(januar|februar|marts|april|maj|juni|juli|august|"
                r"september|oktober|november|december)\s*(\d{4})", text, re.I)
            if dm:
                date = f"{int(dm.group(3)):04d}-{DA_MONTHS[dm.group(2).lower()]:02d}-{int(dm.group(1)):02d}"
                break
            im = re.search(r"(\d{4})-(\d{2})-(\d{2})T", text)
            if im:
                date = f"{im.group(1)}-{im.group(2)}-{im.group(3)}"
                break
            container = container.parent
        items.append({"key": slug, "src": "f", "date": date, "title": title, "url": url})
    return items


def main():
    collected = []
    errors = []
    for src, url in TYPO3_SOURCES:
        try:
            collected += parse_typo3(fetch(url), src, url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    for _, url in FLID_SOURCES:
        try:
            collected += parse_flid(fetch(url))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")

    # Eksisterende arkiv
    old_items = []
    if NEWS_FILE.exists():
        try:
            old_items = json.loads(NEWS_FILE.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            pass

    # Nye først, dedupe på key (gamle bevares, titler/datoer fra nye vinder).
    # Smid gamle poster med relative/ugyldige URL'er ud.
    merged = {}
    for it in collected + old_items:
        if not str(it.get("url", "")).startswith("http"):
            continue
        key = it.get("key") or it.get("url")
        if key not in merged:
            merged[key] = it

    items = list(merged.values())
    items.sort(key=lambda i: i.get("date") or "0000-00-00", reverse=True)
    items = items[:MAX_ITEMS]

    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "errors": errors,
        "items": items,
    }
    NEWS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {len(collected)} hentet, {len(items)} i arkivet, {len(errors)} fejl")
    for e in errors:
        print("FEJL:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
