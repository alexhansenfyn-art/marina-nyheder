# Marina Nyheder

Automatisk nyhedsoversigt for danske marinaer og lystbådehavne.

GitHub Actions er sat op til at køre `crawler.py` hver halve time
(minut 17 og 47; kørsler kan blive forsinket). Kilderne står i `sources.json`:
Minbåd.dk, Motorbådsnyt, FLID Havne, Danske Tursejlere, Bådmagasinet,
MarinaGuide, Havneguide, Dansk Sejlunion og Bådbladet.

Crawleren samler nyheder i `news.json` og bruger DeepSeek til kategorier og
korte danske resuméer, når `DEEPSEEK_API_KEY` er sat. Billeder gemmes ikke.
Den opdaterer også nyhederne direkte i `index.html`, RSS-feedet `feed.xml`
og `sitemap.xml`.

`index.html` viser oversigten via GitHub Pages med filtre og søgning.
Hvis nye data ikke kan hentes, bevares de nyheder, som allerede står på siden.
En service worker giver mulighed for at genåbne tidligere cachet indhold offline.

## Opsætning

1. Læg disse filer i et offentligt GitHub-repo. Workflow-filen `crawl.yml`
   skal ligge i mappen `.github/workflows/`.
2. Aktivér Pages: **Settings → Pages → Source: Deploy from a branch →
   Branch: main / (root)**.
3. Kør første crawl manuelt: **Actions → Crawl marina nyheder → Run workflow**.

Projektets domæne er `marinanyheder.dk`, angivet i `CNAME`.
Ved opsætning af en separat kopi skal domænet tilpasses eller `CNAME` fjernes;
uden eget domæne er Pages-adressen `https://<dit-brugernavn>.github.io/<repo-navn>/`.

## Tilføj kilder og overvåg driften

Repo-ejeren kan oprette en "Ny kilde"-issue via skabelonen. Workflowet
`add-source.yml` validerer kilden, opdaterer nyhederne og gemmer alle genererede
filer. Det deler kørselsgruppe med den planlagte crawler og forsøger push op til
tre gange. Ved fejl under crawl eller push forbliver sagen åben.

Crawlerens vagthund melder kilder uden nyheder i en måned via en GitHub-issue.
Se `STATUS.md` for beslutninger og kendte begrænsninger.

## Lokal forhåndsvisning

Kør `python -m http.server 8000 --bind 127.0.0.1` fra projektmappen og åbn
`http://127.0.0.1:8000/`. Lokale ændringer offentliggøres først, hvis de
committes og pushes til det tilknyttede GitHub-repositorium.
