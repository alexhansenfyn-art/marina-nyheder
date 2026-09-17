# Status — Marina- og HavneNyheder

Sidst opdateret: 6. september 2026 (lokal kopi Marina-Nyheder-GPT)

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

## Lokale rettelser 6. september 2026

- Ved fejl i hentning af nyhedsdata bevares eksisterende nyhedskort, og en
  besked forklarer, at de nyeste nyheder ikke kunne hentes.
- `add-source.yml` deler nu kørselsgruppen `crawl`, gemmer også `index.html`,
  `sitemap.xml` og `feed.xml` og forsøger push tre gange med rebase imellem.
  Sagen lukkes ikke ved fejl under crawl eller push.
- README beskriver de nuværende kilder, tidsplan, AI, drift og lokal visning.
- Artikeltekst sendt til AI er begrænset til 1.000 tegn inklusive mellemrum
  (tidligere 3.000). Eksisterende resuméer ændres ikke.
- Nyhedskort viser nu "Læs hos [medie] ↗" i både JavaScript-visningen og
  crawlerens indbyggede HTML. Teksten under "Om siden" er gennemlæst.
- Masseomskrivning er fravalgt. Grænsen på 1.000 tegn bruges fremover ved
  normal AI-berigelse; eksisterende resuméer skal ikke gendannes.
- Rettelserne er samlet til publicering fra Marina-Nyheder-GPT-kopien.
- Lokal backup af de oprindelige 503 nyheder findes i den Git-ignorerede mappe
  `backups/news-before-rewrite-20260906.json`.

## Rettelser 6. september 2026 (arkiv-opsplitning)

- Arkivet er delt i to filer: `news.json` (de 300 nyeste, "forsiden") og
  `news-arkiv.json` (resten). Almindelige besøgende henter kun `news.json`.
  `news-arkiv.json` hentes først, når nogen søger, åbner filter-dropdownen
  eller ruller helt ned til "Vis ældre nyheder".
- Begge filer skrives helt forfra ved hver kørsel (ikke en løbende
  tilføj/fjern). Testet med 10.000 syntetiske poster: serialisering,
  sortering og opsplitning tager under 100 ms — ubetydeligt i forhold til
  selve hentningen og AI-berigelsen, som tager minutter.
- `MAX_ITEMS` (loftet for hele arkivet) sat til **10.000** (tidligere 2.000,
  oprindeligt 600). Ved ca. 6-7 nye artikler om dagen giver det næsten 3 års
  arkiv, før det skal kigges på igen. Loftet er et bevidst valg, ikke en
  teknisk grænse — begrundelsen står som kommentar i `crawler.py` ved
  `MAX_ITEMS`.
- `sw.js` (cache `marina-v3`) og `make_preview.py` opdateret til at kende
  begge filer.

## Rettelser 17. september 2026 (resuméer skjult, billeder linket)

- **Resuméerne vises ikke længere nogen steder på sitet** (hverken i
  kortene, i den forudrenderede HTML eller i RSS-feedet). Alex har ikke
  indhentet tilladelse fra kilderne til at gengive uddrag af deres tekst.
- **AI-resumégenerering er sat helt på pause**, ikke bare skjult i visningen.
  `enrich_items` beder nu kun DeepSeek om kategori, ikke resumé - der er
  ingen grund til at lade AI'en digte tekst, der bare ligger ubrugt. Gamle
  resuméer i arkivet (feltet `"sum"`) slettes ikke og virker stadig med
  klik-genvejen nedenfor, men der kommer ikke flere til. Kategorien (bruges
  i Emner-filteret) beriges fortsat som før - det er en visende, brugt
  funktion, ikke en hvilende tekst.
- **Skjult genvej i index.html:** klik 3 gange på "Kilder"-overskriften i
  filterpanelet inden for 5 sekunder for at vise resuméer (kun de gamle, der
  allerede findes); klik 3 gange til (6 i alt) for at skjule dem igen. Tænkt
  som demo-værktøj, fx til at vise en redaktør hvordan det ville se ud - IKKE
  en beskyttelse. Resuméteksten ligger under alle omstændigheder i
  `news.json`, som enhver besøgendes browser henter; genvejen styrer kun,
  om den vises i selve kortet.
- Får en kilde en dag lov til at bruge et resumé, er den oplagte løsning at
  bruge KILDENS EGNE ord (fra en redaktør), ikke at slå AI-generering til
  igen.
- **Billedbeslutningen fra 26. august er vendt om.** `img_near` (som finder
  et billedelink i eller omkring artiklen) beholdes nu i `"img"`-feltet i
  stedet for at blive fjernet før news.json skrives. Billedet vises som
  `<img src="kildens-egen-adresse">` — det hentes/kopieres aldrig til dette
  repo, kun linket bruges, ligesom links til selve artiklen. Går kildens
  billede offline, fjerner `onerror="this.remove()"` det stille fra kortet.
  På skærme over 560px vises billede og tekst side om side (`.card-body`);
  under 560px (mobil) er billedet foroven i fuld bredde som hidtil.
- **Rettet:** et tomt AI-svar fra DeepSeek gav en kryptisk fejlbesked uden
  indhold ("kunne ikke tolke AI-svar:" - uden noget efter kolon). Fejlbeskeden
  er nu tydelig, og `enrich_items` prøver automatisk én gang til ved et tomt
  svar, før artiklen opgives til næste kørsel.

## Kendte begrænsninger

- **Resuméerne er ikke faktatjekket.** Der er ingen systematisk kontrol af, om
  tal, navne og datoer i de AI-skrevne resuméer matcher kildeartiklerne.
  Mindre kritisk nu, da de ikke vises offentligt — men relevant igen, hvis de
  en dag slås til.
- **Hotlinkede billeder er ikke uden egne overvejelser.** Nogle kilder kan
  blokere hotlinking (billedet vises da slet ikke, `onerror` skjuler det
  pænt), og billedet kan når som helst ændres eller forsvinde hos kilden uden
  varsel. Det er stadig en mildere situation end at kopiere/hoste billedet
  selv, men ikke risikofrit.
- Advarslen om Node 20 på "pages build and deployment" kan ikke rettes — det er
  GitHubs eget indbyggede workflow, ikke en fil i dette repo.
