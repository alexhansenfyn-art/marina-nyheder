"""Engangs-efterhentning af billeder til gamle arkivposter.

Mange artikler i news.json og news-arkiv.json blev crawlet, FØR vi den
17. september 2026 begyndte at gemme et linket billede (se crawler.py's
dokumentation). Den almindelige crawler genbesøger ikke en artikel, den
allerede kender til - den henter jo allerede dato, titel og kategori, og
skal ikke bruge tid og AI-kald på noget, den har - så de gamle poster får
aldrig et billede af sig selv, uanset hvor god koden bliver.

Dette script kører derfor ÉN GANG: det går artikel for artikel gennem
alle poster uden "img", henter artiklens EGEN side (linket har vi jo
allerede) og læser dens og:image - den samme metode, som fx Facebook
selv bruger, når nogen deler linket. Findes et billede, gemmes kun
linket, ligesom den almindelige crawler gør - intet hentes eller
kopieres til dette repo.

Køres via GitHub Actions ("Efterhent billeder" i Actions-fanen, manuel
"Run workflow") - ikke lokalt og ikke automatisk. Det er et engangsjob,
ikke en del af den almindelige halvtimes-crawl.
"""
from __future__ import annotations

import json
import time

from crawler import (
    ARKIV_FILE,
    IMG_JUNK_RE,
    NEWS_FILE,
    er_web_url,
    fetch,
    og_meta,
    prerender_index,
    write_feed,
    write_sitemap,
)

PAUSE = 0.4  # høflig pause mellem hvert kald, sekunder


def find_image(url):
    """Prøv at finde et billede på artiklens egen side. Returnerer
    (billede-url eller None, fejltekst eller None)."""
    try:
        html = fetch(url)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    img = og_meta(html, "og:image")
    if not img or not er_web_url(img) or IMG_JUNK_RE.search(img):
        return None, None
    return img, None


def backfill(items, label):
    fundet, fejl, sprunget_over = 0, 0, 0
    for i, it in enumerate(items, 1):
        if it.get("img"):
            continue
        url = it.get("url")
        if not url or not er_web_url(url):
            sprunget_over += 1
            continue
        img, err = find_image(url)
        if img:
            it["img"] = img
            fundet += 1
            print(f"[{label} {i}/{len(items)}] billede fundet: {it['title'][:60]}")
        elif err:
            fejl += 1
            print(f"[{label} {i}/{len(items)}] fejl ({err}): {url}")
        time.sleep(PAUSE)
    print(f"{label}: {fundet} billeder fundet, {fejl} fejl, "
          f"{sprunget_over} sprunget over (intet gyldigt link).")
    return fundet


def main():
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    arkiv = json.loads(ARKIV_FILE.read_text(encoding="utf-8"))

    fundet_forside = backfill(news["items"], "Forside")
    fundet_arkiv = backfill(arkiv["items"], "Arkiv")

    NEWS_FILE.write_text(json.dumps(news, ensure_ascii=False, indent=1), encoding="utf-8")
    ARKIV_FILE.write_text(json.dumps(arkiv, ensure_ascii=False, indent=1), encoding="utf-8")

    if fundet_forside:
        # Kun forsidens 300 nyeste er bagt ind i index.html som statisk HTML.
        prerender_index(news["items"])
        write_feed(news["items"])
        write_sitemap()

    print(f"I alt: {fundet_forside + fundet_arkiv} billeder tilføjet.")


if __name__ == "__main__":
    main()
