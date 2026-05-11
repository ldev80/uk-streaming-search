# UK Free Streaming Catalogue

Local tool that scrapes and indexes content from 8 UK free streaming services for instant offline search.

## Services

| Service       | Method                  | Notes                                      |
|---------------|-------------------------|--------------------------------------------|
| BBC iPlayer   | HTML + Redux state      | A-Z pages, Playwright fallback             |
| ITVX          | JSON API                | Fast, rich metadata                        |
| Channel 4     | Playwright + search API | A-Z browse + nav search                    |
| Channel 5     | JSON API                | Needs Origin/Referer headers               |
| PBS America   | Playwright              | JS-rendered, A-Z page                      |
| Pluto TV UK   | Boot API + Playwright   | Tries API first, falls back to browsing    |
| Tubi          | Playwright              | Category + search browsing                 |
| TPTV Encore   | Playwright              | Titles extracted from URL slugs            |

## Setup

```bash
cd ~/Downloads/files_Opus/

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install requests beautifulsoup4 playwright
playwright install chromium

# Copy the project files into place
# (The three .py files should be in this directory)
```

## Usage

### Scrape the full catalogue

```bash
# Scrape all 8 services (takes 30-60 minutes)
python scrape_catalogue.py

# Scrape a single service
python scrape_catalogue.py --service bbc
python scrape_catalogue.py --service itvx

# Verbose logging
python scrape_catalogue.py -v

# List available services
python scrape_catalogue.py --list
```

### Search via command line

```bash
# Basic title search
python search.py "doctor who"

# Filter by service
python search.py "bake off" --service channel4

# Also search descriptions
python search.py "nature" --fuzzy

# JSON output (for piping/scripting)
python search.py "thriller" --json

# View catalogue statistics
python search.py --stats
```

### Search via web UI

```bash
# Start the local web server
python search_web.py

# Open http://localhost:8899
# Custom port:
python search_web.py --port 9000
```

The web UI features:
- Live search with debounce (instant results as you type)
- Filter by service (click the badges)
- Highlighted matches in titles
- Thumbnails where available
- Direct links to watch on each service
- JSON API at `/api/search?q=query` and `/api/stats`
- Keyboard shortcuts: `/` to focus, `ESC` to clear

### Daily automation (cron)

```bash
# Edit your crontab
crontab -e

# Add this line to scrape daily at 3 AM:
0 3 * * * cd ~/Downloads/files_Opus && ./venv/bin/python scrape_catalogue.py >> scrape.log 2>&1
```

## Architecture

```
scrape_catalogue.py     Main scraper — 8 service scrapers, SQLite storage
search.py               CLI search — coloured terminal output, JSON export
search_web.py           Web UI — zero-dependency HTTP server, live search
catalogue.db            SQLite database (auto-created on first scrape)
```

### Database schema

**programmes** — one row per programme per service:
- `service` — service identifier (e.g. `bbc_iplayer`, `itvx`)
- `title` / `title_lower` — programme title (original + lowercase for search)
- `url` — direct link to watch
- `description` — synopsis where available
- `image_url` — thumbnail/poster URL
- `category` — genre/category if provided
- `programme_id` — unique ID within the service (used for deduplication)
- `extra_json` — any additional metadata as JSON
- `scraped_at` — UTC timestamp of last scrape

**scrape_log** — tracks each scrape run with timing, status, and counts.

### Search behaviour

The default search mode is **exact title match**: the query string must appear as a substring within the programme title (case-insensitive). This means searching "who" will match "Doctor Who" and "Who Wants to Be a Millionaire" but not a programme that only mentions "who" in its description.

The `--fuzzy` flag (CLI) extends matching to descriptions as well.

## Troubleshooting

**Playwright not installed**: Run `playwright install chromium`

**Empty results for a service**: Some services may change their page structure.
Check the scrape log with `python search.py --stats` and run the individual
service with verbose logging: `python scrape_catalogue.py -s bbc -v`

**Rate limiting**: The scraper includes polite delays between requests (0.5-2s).
If a service blocks you, increase the delays in `scrape_catalogue.py`.

**Channel 5 returning empty**: Ensure the Origin and Referer headers are set.
The scraper handles this automatically.

**Pluto TV UK 404s**: Make sure you're accessing from a UK IP. The `/uk/` paths
only work from the UK.

**TPTV Encore missing titles**: Titles are extracted from URL slugs since the
React app doesn't expose them in innerText. Quality depends on slug formatting.
