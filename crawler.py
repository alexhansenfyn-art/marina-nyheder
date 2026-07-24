#!/usr/bin/env python3
"""Crawler: samler nyheder om danske marinaer og lystbådehavne.

Kilderne står i sources.json og kan udvides via en issue på GitHub
(brug "Tilføj kilde"-knappen på websiden). Skriver news.json.
Køres automatisk af GitHub Actions – se .github/workflows/crawl.yml.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
NEWS_FILE = ROOT / "news.json"
SOURCES_FILE = ROOT / "sources.json"
MAX_ITEMS = 600
MAX_PER_GENERIC_SOURCE = 40
HEADERS = {"User-Agent": "MarinaNyhederBot/1.0 (+https://github.com)"}

# Gamle kildekoder fra tidligere news.json-format
LEGACY_LABELS = {"m": "Minbåd.dk", "o": "Motorbådsnyt", "f": "FLID Havne"}

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


def host_of(url):
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def find_date(text):
    """Find en dato i en tekststump. Returnerer 'YYYY-MM-DD' eller None."""
    m = re.search(
        r"(\d{1,2})\.?\s*(januar|februar|marts|april|maj|juni|juli|august|"
        r"september|oktober|november|december)\s*(\d{4})", text, re.I)
    if m:
        return f"{int(m.group(3)):04d}-{DA_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def parse_typo3(html, base_url, label):
    """minbaad.dk / motorbaadsnyt.dk (TYPO3). Dato ligger i artikel-URL'en."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select('a[href*="/nyhed/archive/"]'):
        href = urljoin(base_url, a.get("href", ""))
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
        h = host_of(href)
        src_label = "Motorbådsnyt" if "motorbaadsnyt" in h else ("Minbåd.dk" if "minbaad" in h else label)
        items.append({
            "key": slug,
            "source": src_label,
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "title": title,
            "url": href,
        })
    return items


def parse_flid(html, base_url, label):
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
            date = find_date(container.get_text(" ", strip=True)[:800])
            if date:
                break
            container = container.parent
        items.append({"key": slug, "source": label, "date": date, "title": title, "url": url})
    return items


def parse_generic(html, base_url, label):
    """Generisk parser til vilkårlige nyhedssider: links i overskrifter
    samt links med lang overskrifts-lignende tekst, på samme domæne."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = host_of(base_url)
    base_path = urlparse(base_url).path.rstrip("/")
    candidates = []
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        a = h.find("a", href=True)
        if a:
            candidates.append(a)
    for a in soup.find_all("a", href=True):
        if len(clean(a.get_text())) >= 30:
            candidates.append(a)

    out = {}
    for a in candidates:
        href = urljoin(base_url, a["href"])
        if not href.startswith("http") or "#" in href.split("/")[-1]:
            continue
        if host_of(href) != base_host:
            continue
        path = urlparse(href).path.rstrip("/")
        if len(path.strip("/")) < 8 or path == base_path:
            continue
        title = clean(a.get("title") or "") or clean(a.get_text())
        if len(title) < 15:
            continue
        if len(title) > 140:  # link-tekst der er et helt afsnit: klip ved sætningsgrænse
            cut = max(title.find(". ", 40, 140), title.rfind(" ", 40, 137))
            title = title[:cut if cut > 0 else 137].rstrip(".,;: ") + "…"
        key = host_of(href) + path
        if key in out:
            continue
        date = None
        node = a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            date = find_date(node.get_text(" ", strip=True)[:800])
            if date:
                break
        out[key] = {"key": key, "source": label, "date": date, "title": title, "url": href}
        if len(out) >= MAX_PER_GENERIC_SOURCE:
            break
    return list(out.values())


def pick_parser(url):
    h = host_of(url)
    if "minbaad.dk" in h or "motorbaadsnyt.dk" in h:
        return parse_typo3
    if "flidhavne.dk" in h:
        return parse_flid
    return parse_generic


def load_sources():
    return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))


def normalize_old(item):
    """Konverter poster fra det gamle format (src-koder) til det nye."""
    if "source" not in item and item.get("src") in LEGACY_LABELS:
        item["source"] = LEGACY_LABELS[item["src"]]
    item.pop("src", None)
    return item


def main():
    collected = []
    errors = []
    for s in load_sources():
        try:
            parser = pick_parser(s["url"])
            collected += parser(fetch(s["url"]), s["url"], s.get("label") or host_of(s["url"]))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{s['url']}: {e}")

    old_items = []
    if NEWS_FILE.exists():
        try:
            old_items = json.loads(NEWS_FILE.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            pass
    old_items = [normalize_old(i) for i in old_items]

    # Nye først; dedupe på key; smid poster med ugyldige URL'er ud.
    # Mangler den nye post en dato, genbruges datoen fra arkivet.
    merged = {}
    for it in collected + old_items:
        if not str(it.get("url", "")).startswith("http") or not it.get("source"):
            continue
        key = it.get("key") or it.get("url")
        if key not in merged:
            merged[key] = it
        elif not merged[key].get("date") and it.get("date"):
            merged[key]["date"] = it["date"]

    # Nyheder uden dato får den dato, de først blev set (ellers drukner de i bunden)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for it in merged.values():
        if not it.get("date"):
            it["date"] = today

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
