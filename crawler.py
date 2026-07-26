#!/usr/bin/env python3
"""Crawler: samler nyheder om danske marinaer og lystbådehavne.

Kilderne står i sources.json og kan udvides via en issue på GitHub
(brug "Tilføj kilde"-knappen på websiden). Skriver news.json.

Sættes miljøvariablen DEEPSEEK_API_KEY (GitHub Actions secret), beriges
nyhederne med AI: kategori og kort resumé pr. artikel.
Uden nøglen kører crawleren fint - bare uden AI.

Køres automatisk af GitHub Actions – se .github/workflows/crawl.yml.
"""
import json
import os
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
MAX_AI_PER_RUN = 40          # antal nye artikler der AI-beriges pr. kørsel
ACCEPT = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da,en;q=0.8",
}
# Nogle sider afviser bot-agenter, andre afviser browser-agenter - vi prøver begge
HEADER_SETS = [
    {"User-Agent": "MarinaNyhederBot/1.0 (+https://github.com)", **ACCEPT},
    {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"), **ACCEPT},
]
HEADERS = HEADER_SETS[0]
# DeepSeek-model: flash er den billigste og hurtige - bruges bevidst frem for pro
DEEPSEEK_MODELS = ["deepseek-v4-flash"]

CATEGORIES = ["Havnepriser", "Havneliv", "Sikkerhed", "Kapsejlads",
              "Tursejlads", "Udstyr & både", "Jubilæum", "Andet"]

# Gamle kildekoder fra tidligere news.json-format
LEGACY_LABELS = {"m": "Minbåd.dk", "o": "Motorbådsnyt", "f": "FLID Havne"}

EN_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
DA_MONTHS = {m: i + 1 for i, m in enumerate(
    "januar februar marts april maj juni juli august september oktober november december".split())}

ARTICLE_RE = re.compile(
    r"/nyhed/archive/(\d{4})/(\d{1,2})/([a-z]+)/article/([^/?#]+)")

# baadmagasinet.dk (Joomla): artikel-URL'er slutter på "<id>-<slug>"
BAADMAG_RE = re.compile(r"/(\d{3,7})-[a-z0-9-]{6,}/?$", re.I)

# marinaguide.dk (TYPO3): artikel-URL'er er "/nyhed/<slug>"
MARINAGUIDE_RE = re.compile(r"^/nyhed/([a-z0-9-]{8,})/?$", re.I)


def fetch(url):
    last = None
    for headers in HEADER_SETS:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code < 400:
            return r.text
        last = r
    last.raise_for_status()


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


def img_near(a, base_url, levels=3):
    """Find et artikel-billede i eller omkring et link."""
    node = a
    for _ in range(levels + 1):
        if node is None:
            break
        img = node.find("img") if hasattr(node, "find") else None
        if img:
            src = img.get("src") or img.get("data-src") or ""
            if src and not src.startswith("data:"):
                return urljoin(base_url, src)
        node = node.parent
    return None


def parse_typo3(html, base_url, label):
    """minbaad.dk / motorbaadsnyt.dk (TYPO3). Dato ligger i artikel-URL'en."""
    soup = BeautifulSoup(html, "html.parser")
    items, imgs = [], {}
    for a in soup.select('a[href*="/nyhed/archive/"]'):
        href = urljoin(base_url, a.get("href", ""))
        m = ARTICLE_RE.search(href)
        if not m:
            continue
        year, day, month_name, slug = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        month = EN_MONTHS.get(month_name)
        if not month or not (1 <= day <= 31):
            continue
        img = img_near(a, base_url, levels=0)
        if img and slug not in imgs:
            imgs[slug] = img
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
    for it in items:
        if it["key"] in imgs:
            it["img"] = imgs[it["key"]]
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
        it = {"key": slug, "source": label, "date": date, "title": title, "url": url}
        img = img_near(h, base_url)
        if img:
            it["img"] = img
        items.append(it)
    return items


def parse_marinaguide(html, base_url, label):
    """marinaguide.dk/marinanyheder (TYPO3). Hver artikel ligger i en
    'articletype-'-boks med <time datetime="YYYY-MM-DD"> og titlen i en
    overskrift. Samme artikel optræder både som billed- og titel-link,
    så vi holder én post pr. slug."""
    soup = BeautifulSoup(html, "html.parser")
    items = {}
    for a in soup.find_all("a", href=True):
        m = MARINAGUIDE_RE.match(urlparse(urljoin(base_url, a["href"])).path)
        if not m:
            continue
        slug = m.group(1)
        title = clean(a.get("title")) or clean(a.get_text())
        if len(title) < 12 or slug in items:
            continue

        date, node = None, a
        for _ in range(4):
            if node is None:
                break
            t = node.find("time") if hasattr(node, "find") else None
            if t:
                date = find_date((t.get("datetime") or "")[:10]) or find_date(clean(t.get_text()))
                if date:
                    break
            node = node.parent

        it = {"key": "marinaguide-" + slug, "source": label, "date": date,
              "title": title, "url": urljoin(base_url, a["href"])}
        img = img_near(a, base_url)
        if img:
            it["img"] = img
        items[slug] = it
    return list(items.values())


def _headings_with_articles(node):
    """Antal overskrifter i node der linker til en baadmagasinet-artikel."""
    n = 0
    for h in node.find_all(["h1", "h2", "h3", "h4"]):
        a = h.find("a", href=True)
        if a and BAADMAG_RE.search(urlparse(a["href"]).path):
            n += 1
    return n


def parse_baadmagasinet(html, base_url, label):
    """baadmagasinet.dk (Joomla). Titler i h2>a og h3.qx-media-heading>a.
    Artikel-URL'er ender på '<id>-<slug>'; datoen står i et <time datetime>
    inde i artiklens container."""
    soup = BeautifulSoup(html, "html.parser")
    items = {}
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        a = h.find("a", href=True)
        if not a:
            continue
        href = urljoin(base_url, a["href"])
        if host_of(href) != "baadmagasinet.dk":
            continue
        m = BAADMAG_RE.search(urlparse(href).path)
        if not m:
            continue
        title = clean(a.get("title")) or clean(a.get_text())
        if len(title) < 10:
            continue
        art_id = m.group(1)
        if art_id in items:
            continue

        # Dato fra <time datetime> i artiklens egen container. Vi går kun opad
        # så længe containeren indeholder præcis én artikel-overskrift - ellers
        # ville vi hente naboartiklens dato.
        date, node = None, h
        for _ in range(3):
            t = node.find("time")
            if t:
                date = find_date((t.get("datetime") or "")[:10]) or find_date(clean(t.get_text()))
                if date:
                    break
            node = node.parent
            if node is None or _headings_with_articles(node) > 1:
                break

        # Uden dato er linket næsten altid en kategori- eller oversigtsside
        # (fx /nyheder/kapsejlads-2/671-sailgp-2020) og ikke en artikel.
        if not date:
            continue

        it = {"key": "baadmagasinet-" + art_id, "source": label,
              "date": date, "title": title, "url": href}
        img = img_near(h, base_url)
        if img:
            it["img"] = img
        items[art_id] = it
    return list(items.values())


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
        it = {"key": key, "source": label, "date": date, "title": title, "url": href}
        img = img_near(a, base_url)
        if img:
            it["img"] = img
        out[key] = it
        if len(out) >= MAX_PER_GENERIC_SOURCE:
            break
    return list(out.values())


def pick_parser(url):
    h = host_of(url)
    if "minbaad.dk" in h or "motorbaadsnyt.dk" in h:
        return parse_typo3
    if "flidhavne.dk" in h:
        return parse_flid
    if "baadmagasinet.dk" in h:
        return parse_baadmagasinet
    if "marinaguide.dk" in h:
        return parse_marinaguide
    return parse_generic


def load_sources():
    return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))


def normalize_old(item):
    """Konverter poster fra det gamle format (src-koder) til det nye."""
    if "source" not in item and item.get("src") in LEGACY_LABELS:
        item["source"] = LEGACY_LABELS[item["src"]]
    item.pop("src", None)
    return item


# ---------- DeepSeek AI-berigelse ----------

def parse_json(raw):
    """Tolk AI-svar som JSON. Tåler markdown-hegn og afkortede svar."""
    if raw is None:
        raise ValueError("tomt AI-svar")
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\s*|\s*```$", "", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    # Afkortet svar: hent felterne enkeltvis
    out = {}
    m = re.search(r'"kategori"\s*:\s*"([^"]*)"', txt)
    if m:
        out["kategori"] = m.group(1)
    m = re.search(r'"resume"\s*:\s*"(.*?)(?:"|$)', txt, re.S)
    if m and m.group(1).strip():
        out["resume"] = m.group(1).strip()
    if not out:
        raise ValueError(f"kunne ikke tolke AI-svar: {txt[:120]}")
    return out


_MODEL = {"name": None}  # huskes efter første vellykkede kald


def deepseek(messages, json_mode=False, max_tokens=500):
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    models = [_MODEL["name"]] if _MODEL["name"] else DEEPSEEK_MODELS
    last_err = None
    for model in models:
        payload = {"model": model, "messages": messages,
                   "temperature": 0.3, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post("https://api.deepseek.com/chat/completions",
                          json=payload,
                          headers={"Authorization": f"Bearer {key}"},
                          timeout=90)
        if r.status_code == 400 and "model" in r.text.lower():
            last_err = r  # ukendt modelnavn - prøv næste
            continue
        r.raise_for_status()
        _MODEL["name"] = model
        return r.json()["choices"][0]["message"]["content"]
    if last_err is not None:
        last_err.raise_for_status()
    return None


def article_text(url):
    """Hent artiklens brødtekst (bedste bud) til brug for AI-resumé."""
    try:
        soup = BeautifulSoup(fetch(url), "html.parser")
    except Exception:
        return ""
    for t in soup(["script", "style", "nav", "header", "footer", "aside"]):
        t.decompose()
    ps = [clean(p.get_text()) for p in soup.find_all("p")]
    return " ".join(p for p in ps if len(p) > 40)[:3000]


AI_ERRORS = []


def note_ai_error(msg):
    """Gem AI-fejl så de kan ses i news.json (uden at afsløre nøglen)."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        msg = msg.replace(key, "***")
    msg = msg[:300]
    if msg not in AI_ERRORS and len(AI_ERRORS) < 5:
        AI_ERRORS.append(msg)
    print("AI-fejl:", msg, file=sys.stderr)


def enrich_items(items):
    """Giv nye artikler kategori + resumé via DeepSeek. Returnerer antal beriget."""
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        note_ai_error("DEEPSEEK_API_KEY mangler - AI slået fra")
        return 0
    todo = [i for i in items if not i.get("cat")][:MAX_AI_PER_RUN]
    done = 0
    failed = 0
    for it in todo:
        try:
            text = article_text(it["url"])
            prompt = (
                "Kategoriser denne danske nyhed om sejlads/havne og skriv et resumé.\n"
                f"Kategorier (vælg præcis én): {', '.join(CATEGORIES)}\n\n"
                f"Overskrift: {it['title']}\n"
                f"Artikeltekst: {text or '(ingen tekst fundet - brug overskriften)'}\n\n"
                'Svar KUN med JSON: {"kategori": "...", "resume": "1-2 sætninger på dansk, max 200 tegn, nøgternt"}')
            raw = deepseek([
                {"role": "system", "content": "Du er redaktør på et dansk nyhedssite om marinaer og lystbådehavne. Svar kun med gyldig JSON."},
                {"role": "user", "content": prompt},
            ], json_mode=True, max_tokens=800)
            data = parse_json(raw)
            cat = data.get("kategori", "")
            it["cat"] = cat if cat in CATEGORIES else "Andet"
            summary = clean(data.get("resume", ""))[:260]
            if summary:
                it["sum"] = summary
            done += 1
        except Exception as e:  # noqa: BLE001
            detail = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                detail = f" | HTTP {resp.status_code}: {resp.text[:200]}"
            note_ai_error(f"{type(e).__name__}: {e}{detail}")
            failed += 1
            if failed >= 3 and done == 0:
                break  # alle kald fejler - spar tid og penge
    return done


# ---------- Hovedprogram ----------

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
            old = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
            old_items = old.get("items", [])
        except Exception:
            pass
    old_items = [normalize_old(i) for i in old_items]

    # Nye først; dedupe på key; smid poster med ugyldige URL'er ud.
    # Dato, kategori, resumé og billede genbruges fra arkivet hvis de mangler.
    merged = {}
    for it in collected + old_items:
        if not str(it.get("url", "")).startswith("http") or not it.get("source"):
            continue
        key = it.get("key") or it.get("url")
        if key not in merged:
            merged[key] = it
        else:
            for f in ("date", "cat", "sum", "img"):
                if not merged[key].get(f) and it.get(f):
                    merged[key][f] = it[f]

    # Nyheder uden dato får den dato, de først blev set (ellers drukner de i bunden)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for it in merged.values():
        if not it.get("date"):
            it["date"] = today

    items = list(merged.values())
    items.sort(key=lambda i: i.get("date") or "0000-00-00", reverse=True)
    items = items[:MAX_ITEMS]

    enriched = enrich_items(items)

    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "errors": errors + [f"AI: {e}" for e in AI_ERRORS],
        "items": items,
    }
    NEWS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {len(collected)} hentet, {len(items)} i arkivet, "
          f"{enriched} AI-beriget, {len(errors)} fejl")
    for e in errors:
        print("FEJL:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
