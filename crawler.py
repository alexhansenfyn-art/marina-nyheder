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
from datetime import datetime, timedelta, timezone
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
BACKFILL_IMAGES_PER_RUN = 30 # antal manglende billeder der efterhentes pr. kørsel
BACKFILL_IMAGES_SINCE = "2026-05-01"   # ældre artikler lades i fred
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


def plausible_date(date):
    """Kasser datoer i fremtiden og urimeligt gamle datoer. En nyhed kan ikke
    være udgivet i morgen - sker det, har vi fanget en arrangementsdato eller
    en tilmeldingsfrist i teksten omkring artiklen. Ét døgns slæk for tidszoner."""
    if not date:
        return None
    today = datetime.now(timezone.utc).date()
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return None
    if d > today + timedelta(days=1):
        return None
    if d.year < 2000:
        return None
    return date


def find_date(text):
    """Find en dato i en tekststump. Returnerer 'YYYY-MM-DD' eller None.
    Datoer i fremtiden kasseres - se plausible_date."""
    m = re.search(
        r"(\d{1,2})\.?\s*(januar|februar|marts|april|maj|juni|juli|august|"
        r"september|oktober|november|december)\s*(\d{4})", text, re.I)
    if m:
        return plausible_date(
            f"{int(m.group(3)):04d}-{DA_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return plausible_date(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(1)) <= 31:
        return plausible_date(
            f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}")
    return None


# Billeder der aldrig er artikelfotos: forfatter-avatarer, delings-ikoner,
# logoer og pladsholdere. Baadmagasinet lægger fx en 20px Gravatar og fire
# sociale ikoner i samme boks som artiklen - dem må vi ikke forveksle med fotoet.
IMG_JUNK_RE = re.compile(
    r"gravatar\.com|/_template/|/templates?/|/icons?/|avatar|sprite|"
    r"placeholder|spacer|blank\.(gif|png)|logo\.(png|svg|jpg)", re.I)


def img_near(a, base_url, levels=3):
    """Find et artikel-billede i eller omkring et link. Springer avatarer,
    ikoner og logoer over - se IMG_JUNK_RE."""
    node = a
    for _ in range(levels + 1):
        if node is None:
            break
        if hasattr(node, "find_all"):
            for img in node.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if not src or src.startswith("data:"):
                    continue
                if IMG_JUNK_RE.search(src):
                    continue
                css = " ".join(img.get("class") or []).lower()
                if "author" in css or "avatar" in css:
                    continue
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


PUBLISHED_META_RE = re.compile(
    r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
    re.I)


def published_date(url):
    """Hent artiklens udgivelsesdato fra dens egen side (OpenGraph-metadata).
    Bruges når oversigtssiden ikke viser datoer. Fejler stille."""
    try:
        m = PUBLISHED_META_RE.search(fetch(url))
        if m:
            return plausible_date(m.group(1)[:10])
        note_ai_error(f"ingen udgivelsesdato i {url}")
    except Exception as e:  # noqa: BLE001
        note_ai_error(f"datoopslag fejlede for {url}: {type(e).__name__}: {e}")
    return None


def og_meta(html, prop):
    """Hent en OpenGraph-værdi fra en artikelside. Metafelterne er
    maskinskrevne og langt mere pålidelige end tekst plukket fra siden."""
    m = re.search(
        r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) +
        r'["\'][^>]+content=["\']([^"\']*)', html, re.I)
    return m.group(1).strip() if m else ""


# ---- RSS ---------------------------------------------------------------
# Et feed er langt at foretrække frem for at skrabe HTML: titel, link og
# udgivelsesdato staar praecist angivet i stedet for at skulle gaettes ud
# af sidens opbygning - og feedet gaar ikke i stykker, naar de skifter tema.

def rss_dato(tekst):
    """RSS bruger RFC822 ("Fri, 31 Jul 2026 08:00:00 +0000"), Atom bruger
    ISO ("2026-07-31T08:00:00Z"). Begge ender som YYYY-MM-DD."""
    tekst = (tekst or "").strip()
    if not tekst:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return plausible_date(parsedate_to_datetime(tekst).strftime("%Y-%m-%d"))
    except Exception:  # noqa: BLE001
        pass
    return plausible_date(tekst[:10])


def parse_rss(xml_text, base_url, label):
    import xml.etree.ElementTree as ET
    NS = "{http://www.w3.org/2005/Atom}"
    try:
        rod = ET.fromstring(xml_text.lstrip("\ufeff \r\n\t"))
    except ET.ParseError as e:
        raise ValueError(f"ugyldigt feed: {e}") from e

    poster = rod.findall(".//item") or rod.findall(f".//{NS}entry")
    ud = []
    for post in poster[:MAX_PER_GENERIC_SOURCE]:
        def txt(*navne):
            for n in navne:
                el = post.find(n)
                if el is not None and (el.text or "").strip():
                    return el.text.strip()
            return ""

        title = clean(txt("title", f"{NS}title"))
        url = txt("link", "guid")
        if not url:                       # Atom: <link href="...">
            el = post.find(f"{NS}link")
            url = (el.get("href") or "").strip() if el is not None else ""
        if not title or not url.startswith("http") or len(title) < 12:
            continue

        dato = rss_dato(txt("pubDate", "{http://purl.org/dc/elements/1.1/}date",
                            f"{NS}published", f"{NS}updated"))
        path = urlparse(url).path.rstrip("/")
        ud.append({"key": host_of(url) + path, "source": label,
                   "date": dato, "title": title, "url": url})
    return ud


# ---- Dansk Sejlunion ---------------------------------------------------
DSU_ARTIKEL_RE = re.compile(r"^/nyheder/(19|20)\d{2}/[a-z0-9-]{8,}/?$", re.I)
DSU_AJOUR_RE = re.compile(r"Senest ajourf\u00f8rt d\.\s*([^<\n]{6,30})", re.I)


def parse_dansksejlunion(html, base_url, label):
    """dansksejlunion.dk (Umbraco). Oversigten viser hverken rene overskrifter
    eller datoer - linkteksten er overskrift og manchet slaaet sammen. Derfor
    hentes titel og dato fra artiklens egen side. Artikler kendes paa stien
    /nyheder/<aarstal>/<slug>."""
    soup = BeautifulSoup(html, "html.parser")
    fundet, ud = set(), []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        if host_of(url) != host_of(base_url):
            continue
        path = urlparse(url).path
        if not DSU_ARTIKEL_RE.match(path) or path in fundet:
            continue
        fundet.add(path)

        title, dato = clean(a.get_text())[:140], None
        try:
            side = fetch(url)
            title = clean(og_meta(side, "og:title")) or title
            m = DSU_AJOUR_RE.search(side)
            if m:
                dato = plausible_date(find_date(m.group(1)) or "")
            if not dato:
                # og:updated_time staar som 15.06.2026 06.39.02
                u = og_meta(side, "og:updated_time")
                d = re.match(r"(\d{2})\.(\d{2})\.((?:19|20)\d{2})", u)
                if d:
                    dato = plausible_date(f"{d.group(3)}-{d.group(2)}-{d.group(1)}")
        except Exception as e:  # noqa: BLE001
            note_ai_error(f"dansksejlunion: {url}: {type(e).__name__}: {e}")

        if len(title) < 12:
            continue
        ud.append({"key": host_of(url) + path.rstrip("/"), "source": label,
                   "date": dato, "title": title, "url": url})
        if len(ud) >= MAX_PER_GENERIC_SOURCE:
            break
    return ud


DT_NOT_ARTICLES = {
    "nyhedsbrev-fra-marinaguide", "nyhedsbrev", "tursejleren", "arrangementer",
    "tips-og-tricks", "bliv-medlem", "medlemsfordele", "kontakt", "om-os",
    "turboejer", "turbojer", "forsikring",
}


def parse_dansketursejlere(html, base_url, label):
    """dansketursejlere.dk (WordPress). Nyhederne står i et grid af
    <article class="uagb-post__inner-wrap"> UDEN datoer - forsiden viser ingen.
    Vi forsøger at hente den rigtige dato fra artiklens egen side, men lader
    ikke posten falde hvis opslaget fejler: så får den i stedet 'først set'-
    datoen. Grid-afgrænsningen holder oversatte spam-sider (/it/, /en/) ude."""
    soup = BeautifulSoup(html, "html.parser")
    items = {}
    for art in soup.find_all("article", class_="uagb-post__inner-wrap"):
        h = art.find(["h1", "h2", "h3", "h4", "h5"])
        a = h.find("a", href=True) if h else None
        if not a:
            continue
        url = urljoin(base_url, a["href"])
        if host_of(url) != "dansketursejlere.dk":
            continue
        path = urlparse(url).path.strip("/")
        # Nyheder ligger i roden: "/slug/". Alt med undermapper er sprogsider
        # eller sektioner, ikke artikler.
        if not path or "/" in path or len(path) < 10:
            continue
        if path in DT_NOT_ARTICLES:
            continue
        title = clean(a.get("title")) or clean(a.get_text())
        if len(title) < 12 or path in items:
            continue

        it = {"key": "dansketursejlere-" + path, "source": label,
              "date": published_date(url), "title": title, "url": url}
        img = img_near(art, base_url, levels=0)
        if img:
            it["img"] = img
        items[path] = it
    return list(items.values())


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

        # Find artiklens egen boks: gå opad så længe boksen kun indeholder
        # ÉN artikel-overskrift. Ellers henter vi naboartiklens dato og foto.
        box = h
        for _ in range(3):
            parent = box.parent
            if parent is None or _headings_with_articles(parent) > 1:
                break
            box = parent

        date = None
        t = box.find("time")
        if t:
            date = find_date((t.get("datetime") or "")[:10]) or find_date(clean(t.get_text()))

        # Uden dato er linket næsten altid en kategori- eller oversigtsside
        # (fx /nyheder/kapsejlads-2/671-sailgp-2020) og ikke en artikel.
        if not date:
            continue

        it = {"key": "baadmagasinet-" + art_id, "source": label,
              "date": date, "title": title, "url": href}
        img = img_near(box, base_url, levels=0)
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
    if "dansketursejlere.dk" in h:
        return parse_dansketursejlere
    if "dansksejlunion.dk" in h:
        return parse_dansksejlunion
    # Feed-adresser kendes paa stien, ikke paa domaenet
    sti = urlparse(url).path.lower()
    if sti.endswith(("/feed", "/feed/", ".xml", ".rss")) or "feed=" in (urlparse(url).query or ""):
        return parse_rss
    return parse_generic


def load_sources():
    return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))


def normalize_old(item):
    """Konverter poster fra det gamle format (src-koder) til det nye."""
    if "source" not in item and item.get("src") in LEGACY_LABELS:
        item["source"] = LEGACY_LABELS[item["src"]]
    item.pop("src", None)
    return item


# Kilder der har fået en dedikeret parser med nye nøgler. Arkiverede poster fra
# den gamle generiske parser har andre nøgler og ville blive dubletter - med
# forkerte datoer. De kasseres, så de bliver hentet ind igen med rigtig dato.
REKEYED_SOURCES = {"Danske Tursejlere": "dansketursejlere-"}


def is_stale_rekeyed(item):
    prefix = REKEYED_SOURCES.get(item.get("source"))
    return bool(prefix) and not str(item.get("key", "")).startswith(prefix)


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


OG_IMAGE_RES = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', re.I),
]


def backfill_images(items, since, limit=BACKFILL_IMAGES_PER_RUN):
    """Hent manglende billeder fra artiklernes egne sider (og:image).

    Oversigtssiderne viser kun de nyeste artikler, så ældre poster i arkivet
    står tilbage uden billede. Vi henter et pænt antal pr. kørsel, nyeste
    først, så vi ikke hamrer kildernes servere.
    """
    todo = [i for i in items
            if not i.get("img") and (i.get("date") or "") >= since][:limit]
    done = 0
    for it in todo:
        try:
            html = fetch(it["url"])
        except Exception as e:  # noqa: BLE001
            note_ai_error(f"billedhentning fejlede for {it['url']}: {type(e).__name__}")
            continue
        url = None
        for rx in OG_IMAGE_RES:
            m = rx.search(html)
            if m:
                url = m.group(1).strip()
                break
        if not url:
            # Fald tilbage til det første rigtige billede i artiklens brødtekst
            soup = BeautifulSoup(html, "html.parser")
            body = soup.find("article") or soup.find("main") or soup
            url = img_near(body, it["url"], levels=0)
        else:
            url = urljoin(it["url"], url)
        if url and not IMG_JUNK_RE.search(url):
            it["img"] = url
            done += 1
    return done


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


# ---------- Statisk HTML til søgemaskiner ----------

INDEX_FILE = ROOT / "index.html"
SITEMAP_FILE = ROOT / "sitemap.xml"
SITE_URL = "https://marinanyheder.dk/"
PRERENDER_COUNT = 40      # antal nyheder der skrives fast ind i index.html

NEWS_BLOCK_RE = re.compile(r"(<!--NEWS_START-->)(.*?)(<!--NEWS_END-->)", re.S)

DA_MONTH_SHORT = ["jan", "feb", "mar", "apr", "maj", "jun",
                  "jul", "aug", "sep", "okt", "nov", "dec"]


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def da_date(iso):
    try:
        y, m, d = iso.split("-")
        return f"{int(d)}. {DA_MONTH_SHORT[int(m) - 1]} {y}"
    except Exception:  # noqa: BLE001
        return iso or ""


def prerender_index(items):
    """Skriv de nyeste nyheder som rigtig HTML ind i index.html.

    Uden det ser Google (og alle andre robotter) en tom side, fordi
    nyhederne først hentes med JavaScript. JavaScript'et overskriver
    blokken med det samme ved indlæsning, så brugerne mærker intet.
    """
    if not INDEX_FILE.exists():
        return 0
    html = INDEX_FILE.read_text(encoding="utf-8")
    if not NEWS_BLOCK_RE.search(html):
        note_ai_error("index.html mangler NEWS_START/NEWS_END-markører")
        return 0

    parts = []
    for it in items[:PRERENDER_COUNT]:
        summary = (f'<div class="summary">{esc(it["sum"])}</div>'
                   if it.get("sum") else "")
        parts.append(
            f'<a class="card" href="{esc(it["url"])}" target="_blank" rel="noopener">'
            f'<span class="title">{esc(it["title"])}</span>{summary}'
            f'<div class="meta">{da_date(it.get("date"))}'
            f'<span class="dot">&middot;</span>{esc(it.get("source", ""))}</div></a>')

    block = "\n" + "\n".join(parts) + "\n"
    new_html = NEWS_BLOCK_RE.sub(
        lambda m: m.group(1) + block + m.group(3), html, count=1)
    # Samlet antal nyheder i overskriften, så tallet også står der uden JavaScript
    new_html = re.sub(r"(<!--ANTAL_START-->)\d*(<!--ANTAL_END-->)",
                      lambda m: m.group(1) + str(len(items)) + m.group(2),
                      new_html, count=1)
    dk = dansk_tid(datetime.now(timezone.utc))
    stempel = (f"{dk.day}. {DA_MONTH_SHORT[dk.month - 1]} {dk.year} "
               f"kl. {dk.hour:02d}.{dk.minute:02d}")
    new_html = re.sub(r"(<!--OPDATERET_START-->).*?(<!--OPDATERET_END-->)",
                      lambda m: m.group(1) + stempel + m.group(2),
                      new_html, count=1)
    if new_html != html:
        INDEX_FILE.write_text(new_html, encoding="utf-8")
    return len(parts)


FEED_FILE = ROOT / "feed.xml"
FEED_COUNT = 50           # antal nyheder i RSS-feedet
STALE_DAYS = 30           # kilde uden nyheder så længe = noget er galt
WATCHDOG_FILE = ROOT / "vagthund.txt"

RFC822_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RFC822_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def rfc822(iso):
    """Dato til RSS-format. Feedlæsere kræver engelske forkortelser."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        d = datetime.now(timezone.utc)
    return (f"{RFC822_DAYS[d.weekday()]}, {d.day:02d} "
            f"{RFC822_MONTHS[d.month - 1]} {d.year} 08:00:00 +0000")


def dansk_tid(dt):
    """UTC til dansk tid. Danmark er UTC+1, og UTC+2 fra sidste søndag i
    marts til sidste søndag i oktober. Regnet ud i hånden, så crawleren
    ikke afhænger af tidszonedata på serveren."""
    def sidste_soendag(aar, maaned):
        d = datetime(aar, maaned, 31, tzinfo=timezone.utc)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    start = sidste_soendag(dt.year, 3).replace(hour=1)
    slut = sidste_soendag(dt.year, 10).replace(hour=1)
    return dt + timedelta(hours=2 if start <= dt < slut else 1)


def write_feed(items):
    """RSS-feed, så folk kan følge siden i deres egen nyhedslæser."""
    nu = datetime.now(timezone.utc)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "<title>Marina- og HavneNyheder</title>",
        f"<link>{SITE_URL}</link>",
        "<description>Nyheder om danske marinaer, lystbådehavne og "
        "gæstehavne. Opdateres automatisk flere gange om dagen.</description>",
        "<language>da-dk</language>",
        f"<lastBuildDate>{rfc822(nu.strftime('%Y-%m-%d'))}</lastBuildDate>",
        f'<atom:link href="{SITE_URL}feed.xml" rel="self" '
        'type="application/rss+xml"/>',
    ]
    for it in items[:FEED_COUNT]:
        kilde = esc(it.get("source", ""))
        tekst = esc(it.get("sum") or "")
        beskrivelse = f"{tekst} (Kilde: {kilde})" if tekst else f"Kilde: {kilde}"
        parts += [
            "<item>",
            f'<title>{esc(it["title"])}</title>',
            f'<link>{esc(it["url"])}</link>',
            f'<guid isPermaLink="true">{esc(it["url"])}</guid>',
            f"<pubDate>{rfc822(it.get('date'))}</pubDate>",
            f"<description>{beskrivelse}</description>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>", ""]
    FEED_FILE.write_text("\n".join(parts), encoding="utf-8")


def check_sources(items):
    """Vagthund: en kilde, der ikke har leveret i en måned, er sandsynligvis
    lagt om, så parseren er gået i stykker. Skriv en besked, som workflowet
    kan sende videre."""
    nyeste = {}
    for it in items:
        k, d = it.get("source"), it.get("date") or ""
        if k and d > nyeste.get(k, ""):
            nyeste[k] = d
    graense = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")

    doede = []
    for s in load_sources():
        navn = s.get("label") or host_of(s["url"])
        sidst = nyeste.get(navn)
        if not sidst or sidst < graense:
            doede.append((navn, s["url"], sidst))

    if not doede:
        # Tøm en gammel besked, så workflowet ikke sender den igen
        if WATCHDOG_FILE.exists():
            WATCHDOG_FILE.write_text("", encoding="utf-8")
        return []

    linjer = [f"Disse kilder har ikke leveret nyheder i {STALE_DAYS} dage.",
              "Som regel betyder det, at hjemmesiden er lagt om, og at "
              "parseren i crawler.py skal rettes.", ""]
    for navn, url, sidst in sorted(set(doede)):
        linjer.append(f"- {navn} ({url}) - sidste nyhed: {sidst or 'ingen'}")
    linjer += ["", f"Tjekket {datetime.now(timezone.utc):%d-%m-%Y %H:%M} UTC.",
               "Se selv efter på marinanyheder.dk"]
    WATCHDOG_FILE.write_text("\n".join(linjer) + "\n", encoding="utf-8")
    return doede


def write_sitemap():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           f"  <url>\n    <loc>{SITE_URL}</loc>\n"
           f"    <lastmod>{today}</lastmod>\n"
           "    <changefreq>hourly</changefreq>\n"
           "    <priority>1.0</priority>\n  </url>\n"
           "</urlset>\n")
    SITEMAP_FILE.write_text(xml, encoding="utf-8")


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
    old_items = [i for i in old_items if not is_stale_rekeyed(i)]

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

    # Billeder gemmes ikke: retten til pressefotos følger ikke med et link til artiklen.
    for it in items:
        it.pop("img", None)

    enriched = enrich_items(items)

    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "errors": errors + [f"AI: {e}" for e in AI_ERRORS],
        "items": items,
    }
    NEWS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    prerendered = prerender_index(items)
    write_sitemap()
    write_feed(items)
    doede = check_sources(items)
    print(f"OK: {len(collected)} hentet, {len(items)} i arkivet, "
          f"{enriched} AI-beriget, "
          f"{prerendered} skrevet i HTML, {len(errors)} fejl")
    if doede:
        print("VAGTHUND: kilder uden nyheder i "
              f"{STALE_DAYS} dage: " + ", ".join(sorted({d[0] for d in doede})))
    for e in errors:
        print("FEJL:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
