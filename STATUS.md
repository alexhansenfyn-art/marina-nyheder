# Status — Marina- og HavneNyheder

Sidst opdateret: 28. august 2026

Denne fil er projektets hukommelse. Læs den først, hvis du er en AI der lige er
åbnet i denne mappe, eller et menneske der ikke har rørt projektet i en måned.
Ret den, når noget ændrer sig.

## Hvad det er

Automatisk nyhedsoversigt for danske marinaer og lystbådehavne. En crawler
samler overskrifter fra danske bådmedier, beriger dem med AI (kategori og kort
resumé), og viser dem på `marinanyheder.dk`.

**Siden er offentlig** på eget domæne. Det er en vigtig forskel fra søsterprojektet
Rumnyt, som er privat — flere af valgene nedenfor følger direkte af det.

## Sådan hænger det sammen

1. GitHub Actions kører `crawler.py` hver halve time (`crawl.yml`).
2. Crawleren læser kildelisten i `sources.json` og henter hver side.
   Hver kilde har sin egen parser: TYPO3 (minbaad, motorbaadsnyt), Joomla
   (baadmagasinet), MarinaGuide, RSS, og en generisk fallback.
3. Nye artikler beriges med DeepSeek: kategori og et kort dansk resumé.
4. Resultatet skrives til `news.json`, og de nyeste bages ind i `index.html`
   som rigtig HTML (prerender) — ellers ser Google en tom side, fordi
   nyhederne først hentes med JavaScript.
5. `sitemap.xml` og `feed.xml` skrives også. Alt committes af github-actions.

## Beslutninger der ikke er til forhandling uden videre

**Billeder gemmes ikke.** Parserne samler et billede op undervejs, men feltet
fjernes lige inden `news.json` skrives. Retten til et pressefoto følger ikke med
et link til artiklen, og det her er et offentligt site. Begrundelsen står også i
toppen af `crawler.py`, netop fordi tomme billedpladser ser ud som en fejl.

**Skæve minuttal i cron (17 og 47).** GitHubs planlagte kørsler ligger i en
lavprioritetskø, og de runde minuttal er de mest belastede. Skæve tidspunkter
rammer oftere igennem.

**`concurrency: group: crawl` med `cancel-in-progress: false`.** To kørsler må
aldrig overlappe — de skriver til samme `news.json` og pusher til main.

**Push forsøges tre gange med rebase imellem.** En anden kørsel kan nå at pushe
imens, og så skal kørslen ikke bare dø.

## Vagthund

Går en kilde i stå i en måned, skriver crawleren til `vagthund.txt`, og
workflowet opretter en GitHub-issue. Kun én åben sag ad gangen — ellers kommer
der en mail i timen. Er `vagthund.txt` tom, er alle kilder friske.

## Tilføj en ny kilde

Opret en issue med titlen "Ny kilde" (der er en knap på websiden).
`add-source.yml` reagerer kun på issues fra repo-ejeren, kører `add_source.py`,
som validerer at siden faktisk kan parses, føjer den til `sources.json`, kører
crawleren og lukker sagen med et svar.

## Nøgler

`DEEPSEEK_API_KEY` ligger i GitHub Actions Secrets. Uden nøglen kører crawleren
fint — bare uden AI-kategorier og resuméer.

## Rettelser 28. august 2026

- Begrundelsen for at billeder ikke gemmes er flyttet op i toppen af
  `crawler.py`, hvor den er svær at overse.
- 42 linjer død billedkode fjernet: `backfill_images` blev aldrig kaldt af
  noget, og `OG_IMAGE_RES` blev kun brugt af den.
- `er_web_url()` erstatter `url.startswith("http")` tre steder. Alle links
  ender i et `href` på en offentlig side, og `esc()` gør intet ved
  `javascript:`. Det gamle tjek udelukkede det ved et tilfælde; nu er det et
  valg. Testet mod alle 342 poster i `news.json` — ingen falder fra.
- `add-source.yml` opdateret til `actions/checkout@v5` og
  `actions/setup-python@v6`. `crawl.yml` var allerede på v6. Node 20 fjernes
  fra GitHubs runnere i efteråret 2026.

## Åbent / ikke gjort

- **Resuméerne er ikke faktatjekket.** Der er ingen systematisk kontrol af, om
  tal, navne og datoer i de AI-skrevne resuméer matcher kildeartiklerne. Det er
  et offentligt site, så det vejer tungere her end i et privat projekt.
- Der er ikke taget udtrykkelig stilling til, om AI-skrevne resuméer af andres
  artikler er i orden at publicere. Billedspørgsmålet er afklaret; det her er
  den samme diskussion, en grad mildere.
- Advarslen om Node 20 på "pages build and deployment" kan ikke rettes — det er
  GitHubs eget indbyggede workflow, ikke en fil i dette repo.
