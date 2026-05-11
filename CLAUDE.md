# StreamFind — UK Free Streaming Catalogue

Scrapes and indexes content from 8 UK free streaming services into SQLite, then serves a static client-side search UI via Firebase Hosting.

## Architecture

- `scrape_catalogue.py` — scrapes all 8 services into `catalogue.db` (SQLite), exports `catalogue.json`
- `index.html` — static search UI that loads `catalogue.json` and searches entirely client-side
- `public/` — directory served by Firebase Hosting (contains `index.html` + `catalogue.json`)
- `auto-scrape.sh` — weekly automation: scrapes, commits to GitHub, deploys to Firebase
- `search_web.py` — legacy local Python web server (queries SQLite directly, not used in production)
- `search.py` — CLI search tool

## Live site

https://streamfind-2abe7.web.app

## Weekly automation

A macOS `launchd` job runs `auto-scrape.sh` every Sunday at 3am:
- Plist: `~/Library/LaunchAgents/uk.co.liamdevereux.streamfind-scrape.plist`
- Scrapes all services from the local Mac (UK IP, avoids geo-blocking)
- Exports `catalogue.json`, commits to GitHub, deploys to Firebase
- Logs to `scrape.log`

To manage: `launchctl list | grep streamfind` / `launchctl unload ~/Library/LaunchAgents/uk.co.liamdevereux.streamfind-scrape.plist`

## Service scrapers — problems and fixes

### BBC iPlayer (1,055 → 3,542)
**Method:** A-Z pages with embedded Redux state JSON (`window.__IPLAYER_REDUX_STATE__`), paginated at 200 per page. Falls back to HTML parsing, then Playwright.

**Problems fixed:**
- Redux data path was wrong — code read `state["entities"]` but data lives at `state["programmes"][letter]["entities"]`
- URL regex only matched `/iplayer/episode/` (singular), missing `/iplayer/episodes/` (plural) used by multi-episode series
- No pagination support — letters a, b, c, m, s have 200+ entries across multiple pages

### ITVX (0 → 1,475)
**Method:** Public search API at `textsearch.prd.oasvc.itv.com/search`, queried with A-Z letters and common terms.

**Problems fixed:**
- API response nests programme data inside `item["data"]` but code read fields from `item` directly — every title was empty, every result skipped
- No `size` parameter meant only 10 results per query (default). Now uses `size=5000`
- Films use `filmTitle`/`filmCCId` instead of `programmeTitle`/`programmeCCId` — now handles both

### Channel 4 (60 → 2,429)
**Method:** Sitemap XML at `channel4.com/sitemap/1.xml` for the definitive slug list, enriched with titles/descriptions from the search API at `all4nav.channel4.com`.

**Problems fixed:**
- Previous approach used Playwright on the A-Z page (only loaded ~60 via scrolling) and a search API capped at 100 results with broken pagination
- Replaced entirely with sitemap parsing — single HTTP request gets all 2,429 programme slugs
- Search API still used for enrichment (proper titles, descriptions) but slugs from sitemap are the source of truth

### Channel 5 (1,561 → 1,577)
**Method:** JSON API at `corona.channel5.com/shows/search.json` with an empty query string, which returns the entire catalogue in one request.

**Problems fixed:**
- Previous approach tried two browse endpoints (both return 404) then fell back to 26+ individual letter queries
- Simplified to single `query=` call — faster and slightly more complete

### PBS America (60 → 1,340)
**Method:** WordPress sitemap at `pbsamerica.co.uk/wp-sitemap-posts-series-1.xml`.

**Problems fixed:**
- A-Z page at `/shows/a-z/` is broken (shows "no results" due to JS rendering failure)
- Playwright fallback only picked up ~60 via WordPress search
- Replaced with sitemap parsing — no Playwright needed, gets all 1,340 series

### Pluto TV UK (28 → 2,838)
**Method:** VOD categories API at `api.pluto.tv/v3/vod/categories` with `includeItems=true&countryCode=GB`.

**Problems fixed:**
- Boot API (`boot.pluto.tv/v4/start`) doesn't contain VOD data — only session config and live channels
- Playwright fallback failed on the JS-heavy site
- Replaced with the VOD categories API. Uses `offset=1000` (acts as a limit, not page offset). Categories with more than 100 items are fetched individually via `/categories/{id}/items`

### Tubi (2,084 — unchanged)
**Method:** Playwright browsing of category pages and search. No changes made — working adequately.

### TPTV Encore (401 — unchanged)
**Method:** Playwright browsing and search. Titles extracted from URL slugs. No changes made.

## Data pipeline

```
scrape_catalogue.py
    → catalogue.db (SQLite — full data with metadata)
    → catalogue.json (compact JSON — title, service, url, description, image_url, category)
        → public/catalogue.json (copy for Firebase)
            → Firebase Hosting (streamfind-2abe7.web.app)
```

## API endpoints and data sources

These are the external endpoints the scraper depends on. If a scraper breaks, check these first:

| Service | Type | Endpoint |
|---------|------|----------|
| BBC iPlayer | HTML + JSON | `bbc.co.uk/iplayer/a-z/{letter}?page={n}` (Redux state) |
| ITVX | JSON API | `textsearch.prd.oasvc.itv.com/search?query={q}&size=5000` |
| Channel 4 | XML sitemap | `channel4.com/sitemap/1.xml` (~28MB) |
| Channel 4 | JSON API | `all4nav.channel4.com/v1/api/search?q={q}&limit=100` (enrichment only) |
| Channel 5 | JSON API | `corona.channel5.com/shows/search.json?query=` |
| PBS America | XML sitemap | `pbsamerica.co.uk/wp-sitemap-posts-series-1.xml` |
| Pluto TV UK | JSON API | `api.pluto.tv/v3/vod/categories?includeItems=true&countryCode=GB` |
| Tubi | Playwright | `tubitv.com/category/{genre}` and `/search/{term}` |
| TPTV Encore | Playwright | `tptvencore.co.uk/` and `/search/{term}` |

## Geo-sensitivity

Most API-based scrapers work from any IP. BBC iPlayer and Pluto TV may be sensitive to non-UK IPs. The weekly scrape runs locally from a UK machine to avoid this.

## Running manually

```bash
source venv/bin/activate
python scrape_catalogue.py              # all services (~30-60 min)
python scrape_catalogue.py -s itvx -v   # single service, verbose
python scrape_catalogue.py --list       # list service keys
```

After scraping, deploy:
```bash
cp catalogue.json public/catalogue.json
firebase deploy --only hosting --project streamfind-2abe7
```

Or use `./auto-scrape.sh` which does scrape + commit + push + deploy.
