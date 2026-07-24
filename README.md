# Marina Nyheder

Automatisk nyhedsoversigt for danske marinaer og lystbådehavne.

En GitHub Action kører `crawler.py` hver time, som samler nyheder fra
[Minbåd.dk](https://minbaad.dk), [Motorbådsnyt.dk](https://motorbaadsnyt.dk)
og [FLID](https://flidhavne.dk/nyheder/) i `news.json`.
`index.html` viser oversigten via GitHub Pages med filtre og søgning.

## Opsætning

1. Læg disse filer i et offentligt GitHub-repo. Workflow-filen `crawl.yml`
   skal ligge i mappen `.github/workflows/`.
2. Aktivér Pages: **Settings → Pages → Source: Deploy from a branch →
   Branch: main / (root)**.
3. Kør første crawl manuelt: **Actions → Crawl marina nyheder → Run workflow**.

Siden ligger derefter på `https://<dit-brugernavn>.github.io/<repo-navn>/`.
