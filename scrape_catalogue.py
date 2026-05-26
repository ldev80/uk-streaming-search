#!/usr/bin/env python3
"""
UK Free Streaming Catalogue Scraper
====================================
Scrapes full catalogues from 8 UK free streaming services and stores them in SQLite.
Designed to run daily via cron or manually.

Services: BBC iPlayer, ITVX, Channel 4, Channel 5, PBS America, Pluto TV UK, Tubi, TPTV Encore

Usage:
    python scrape_catalogue.py              # Scrape all services
    python scrape_catalogue.py --service bbc  # Scrape one service
    python scrape_catalogue.py --list        # List available services

Dependencies: requests, beautifulsoup4, playwright (with chromium installed)
"""

import argparse
import json
import logging
import re
import sqlite3
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "catalogue.db"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
REQUEST_TIMEOUT = 30
PLAYWRIGHT_TIMEOUT = 45_000  # ms

HEADERS_DEFAULT = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

logger = logging.getLogger("scraper")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create / connect to the SQLite database and ensure schema exists."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS programmes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            service     TEXT NOT NULL,
            title       TEXT NOT NULL,
            title_lower TEXT NOT NULL,
            url         TEXT,
            description TEXT,
            image_url   TEXT,
            category    TEXT,
            programme_id TEXT,
            extra_json  TEXT,
            scraped_at  TEXT NOT NULL,
            UNIQUE(service, programme_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_title_lower ON programmes(title_lower)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_service ON programmes(service)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            service    TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status     TEXT,
            count      INTEGER DEFAULT 0,
            error_msg  TEXT
        )
    """)
    conn.commit()
    return conn


def upsert_programme(conn: sqlite3.Connection, service: str, title: str,
                     url: str = None, description: str = None,
                     image_url: str = None, category: str = None,
                     programme_id: str = None, extra: dict = None):
    """Insert or update a single programme record."""
    now = datetime.now(timezone.utc).isoformat()
    pid = programme_id or url or title  # fallback unique key
    conn.execute("""
        INSERT INTO programmes
            (service, title, title_lower, url, description, image_url, category, programme_id, extra_json, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(service, programme_id) DO UPDATE SET
            title = excluded.title,
            title_lower = excluded.title_lower,
            url = excluded.url,
            description = excluded.description,
            image_url = excluded.image_url,
            category = excluded.category,
            extra_json = excluded.extra_json,
            scraped_at = excluded.scraped_at
    """, (
        service,
        title.strip(),
        title.strip().lower(),
        url,
        description.strip() if description else None,
        image_url,
        category,
        pid,
        json.dumps(extra) if extra else None,
        now,
    ))


def clear_service(conn: sqlite3.Connection, service: str):
    """Remove all records for a service before a fresh scrape."""
    conn.execute("DELETE FROM programmes WHERE service = ?", (service,))
    conn.commit()


# ---------------------------------------------------------------------------
# Playwright helpers (lazy-loaded)
# ---------------------------------------------------------------------------

_playwright_ctx = None
_browser = None


def get_browser():
    """Lazily launch a Playwright Chromium browser."""
    global _playwright_ctx, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright_ctx = sync_playwright().start()
        _browser = _playwright_ctx.chromium.launch(headless=True)
        logger.info("Playwright browser launched")
    return _browser


def close_browser():
    """Shut down Playwright if it was started."""
    global _playwright_ctx, _browser
    if _browser:
        _browser.close()
        _browser = None
    if _playwright_ctx:
        _playwright_ctx.stop()
        _playwright_ctx = None


def pw_new_page(url: str, wait_selector: str = None, wait_ms: int = 5000):
    """Open a Playwright page, navigate, wait, and return the page object."""
    browser = get_browser()
    context = browser.new_context(
        locale="en-GB",
        timezone_id="Europe/London",
        user_agent=HEADERS_DEFAULT["User-Agent"],
    )
    page = context.new_page()
    page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=PLAYWRIGHT_TIMEOUT)
        except Exception:
            logger.warning(f"Selector '{wait_selector}' not found on {url}, continuing anyway")
    else:
        page.wait_for_timeout(wait_ms)
    return page, context


# ---------------------------------------------------------------------------
# Service scrapers — each returns a count of programmes found
# ---------------------------------------------------------------------------

# ── BBC iPlayer ────────────────────────────────────────────────────────────

def scrape_bbc(conn: sqlite3.Connection) -> int:
    """
    Scrape BBC iPlayer catalogue via the A-Z pages.
    Each letter page lists programmes starting with that letter.
    URL pattern: https://www.bbc.co.uk/iplayer/a-z/{letter}?page={n}

    The page embeds JSON in window.__IPLAYER_REDUX_STATE__ with structure:
        state.programmes[letter].entities  — list of {props, meta} objects
        state.programmes[letter].count     — total programmes for that letter
        state.pagination.totalPages        — number of pages (200 per page)

    Programme hrefs use /iplayer/episodes/{pid} (multi-episode series) or
    /iplayer/episode/{pid} (single programmes).

    Falls back to HTML link parsing, then Playwright if needed.
    """
    count = 0
    base = "https://www.bbc.co.uk"
    letters = list(string.ascii_lowercase) + ["0-9"]

    for letter in letters:
        page_num = 1
        total_pages = 1  # updated after first fetch

        while page_num <= total_pages:
            url = f"{base}/iplayer/a-z/{letter}"
            if page_num > 1:
                url += f"?page={page_num}"
            logger.info(f"BBC iPlayer A-Z: {url}")

            try:
                resp = requests.get(url, headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                html = resp.text

                # Try extracting from Redux state first
                redux_match = re.search(
                    r'window\.__IPLAYER_REDUX_STATE__\s*=\s*({.+?});\s*</script>',
                    html, re.DOTALL
                )
                redux_ok = False
                if redux_match:
                    try:
                        state = json.loads(redux_match.group(1))
                        # Programme data lives at state.programmes[letter].entities
                        letter_data = state.get("programmes", {}).get(letter, {})
                        entities = letter_data.get("entities", [])

                        # Update pagination from first page
                        if page_num == 1:
                            pagination = state.get("pagination", {})
                            total_pages = pagination.get("totalPages", 1)
                            letter_count = letter_data.get("count", len(entities))
                            if total_pages > 1:
                                logger.info(f"  Letter '{letter}': {letter_count} programmes across {total_pages} pages")

                        for entity in entities:
                            props = entity.get("props", {})
                            title = props.get("title", "").strip()
                            if not title:
                                continue
                            href = props.get("href", "")
                            synopsis = props.get("synopsis", "")
                            img_template = props.get("imageTemplate", "")
                            img_url = img_template.replace("{recipe}", "320x180") if img_template else ""
                            episodes_available = entity.get("meta", {}).get("episodesAvailable")

                            # Extract PID from href: /iplayer/episode(s)/{pid}/slug
                            pid_match = re.match(r"/iplayer/(?:episodes?|group)/([a-z0-9]+)", href)
                            pid = pid_match.group(1) if pid_match else ""

                            prog_url = f"{base}{href}" if href else ""
                            upsert_programme(conn, "bbc_iplayer", title,
                                             url=prog_url, description=synopsis,
                                             image_url=img_url, programme_id=pid or title,
                                             extra={"episodesAvailable": episodes_available} if episodes_available else None)
                            count += 1

                        if entities:
                            redux_ok = True

                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse Redux JSON for {letter} page {page_num}")

                # Fallback: parse HTML links (if Redux yielded nothing)
                if not redux_ok:
                    soup = BeautifulSoup(html, "html.parser")
                    for a_tag in soup.find_all("a", href=True):
                        href = a_tag["href"]
                        # Match /iplayer/episode/{pid}, /iplayer/episodes/{pid}, or /iplayer/group/{pid}
                        m = re.match(r"/iplayer/(?:episodes?|group)/([a-z0-9]+)", href)
                        if m:
                            pid = m.group(1)
                            title = a_tag.get_text(strip=True)
                            if not title or len(title) < 2:
                                parent = a_tag.find_parent(class_=re.compile(r"content-item|programme"))
                                if parent:
                                    title_el = parent.find(class_=re.compile(r"title|heading"))
                                    if title_el:
                                        title = title_el.get_text(strip=True)
                            if title and len(title) >= 2:
                                img_tag = a_tag.find("img")
                                img_url = img_tag.get("src", "") if img_tag else ""
                                upsert_programme(conn, "bbc_iplayer", title,
                                                 url=f"{base}{href}",
                                                 image_url=img_url,
                                                 programme_id=pid)
                                count += 1

            except requests.RequestException as e:
                logger.error(f"BBC iPlayer error for letter {letter} page {page_num}: {e}")

            page_num += 1
            time.sleep(1)  # polite delay

    # If we got very few results from requests, try Playwright
    if count < 20:
        logger.info("BBC iPlayer: few results from requests, trying Playwright...")
        count += _scrape_bbc_playwright(conn)

    conn.commit()
    return count


def _scrape_bbc_playwright(conn: sqlite3.Connection) -> int:
    """Playwright fallback for BBC iPlayer A-Z pages."""
    count = 0
    base = "https://www.bbc.co.uk"
    letters = list(string.ascii_lowercase) + ["0-9"]

    for letter in letters:
        url = f"{base}/iplayer/a-z/{letter}"
        try:
            page, ctx = pw_new_page(url, wait_selector="a[href*='/iplayer/']", wait_ms=6000)
            links = page.query_selector_all(
                "a[href*='/iplayer/episode/'], "
                "a[href*='/iplayer/episodes/'], "
                "a[href*='/iplayer/group/']"
            )
            for link in links:
                href = link.get_attribute("href") or ""
                m = re.match(r"/iplayer/(?:episodes?|group)/([a-z0-9]+)", href)
                if not m:
                    continue
                pid = m.group(1)
                title = link.inner_text().strip()
                if not title or len(title) < 2:
                    # Try parent
                    parent = link.evaluate_handle("el => el.closest('[class*=content], [class*=programme]')")
                    if parent:
                        try:
                            title = parent.evaluate("el => el.querySelector('[class*=title], h2, h3')?.textContent?.trim() || ''")
                        except Exception:
                            pass
                if title and len(title) >= 2:
                    upsert_programme(conn, "bbc_iplayer", title,
                                     url=f"{base}{href}",
                                     programme_id=pid)
                    count += 1
            ctx.close()
        except Exception as e:
            logger.error(f"BBC Playwright error for {letter}: {e}")
        time.sleep(1)

    return count


# ── ITVX ───────────────────────────────────────────────────────────────────

def scrape_itvx(conn: sqlite3.Connection) -> int:
    """
    Scrape ITVX catalogue using their search API.
    Search A-Z with large page size to cover the full catalogue.
    """
    count = 0
    seen_ids = set()
    api_base = (
        "https://textsearch.prd.oasvc.itv.com/search?"
        "broadcaster=itv&featureSet=clearkey,outband-webvtt,hls,aes,"
        "playready,widevine,fairplay,bbts,progressive,hd,rtmpe"
        "&onlyFree=true&platform=dotcom&size=5000"
    )
    itvx_headers = {
        **HEADERS_DEFAULT,
        "Accept": "application/json",
        "Origin": "https://www.itv.com",
        "Referer": "https://www.itv.com/",
    }

    queries = list(string.ascii_lowercase) + [
        "the", "love", "murder", "detective", "house", "doctor", "family",
        "news", "world", "night", "day", "life", "death", "war", "crime",
        "police", "real", "great", "wild", "britain", "english", "island",
        "drama", "comedy", "thriller", "documentary", "sport", "film",
        "christmas", "celebrity", "coronation", "emmerdale", "chase",
    ]

    for q in queries:
        url = f"{api_base}&query={quote(q)}"
        logger.info(f"ITVX search: {q}")
        try:
            resp = requests.get(url, headers=itvx_headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.warning(f"ITVX returned {resp.status_code} for '{q}'")
                continue
            results = resp.json().get("results", [])
            for item in results:
                data = item.get("data", {})
                entity_type = item.get("entityType", "")
                pid = data.get("programmeCCId") or data.get("filmCCId") or item.get("id", "")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                title = (data.get("programmeTitle") or data.get("filmTitle", "")).strip()
                if not title:
                    continue
                synopsis = data.get("synopsis", "")
                image = data.get("imageUrl", "") or data.get("latestAvailableEpisode", {}).get("imageHref", "")
                genres = data.get("genre", [])
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                watch_url = f"https://www.itv.com/watch/{slug}"

                upsert_programme(conn, "itvx", title,
                                 url=watch_url,
                                 description=synopsis,
                                 image_url=image,
                                 programme_id=pid,
                                 extra={"genre": genres, "entityType": entity_type})
                count += 1

        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"ITVX error for '{q}': {e}")
        time.sleep(0.5)

    conn.commit()
    return count


# ── Channel 4 ──────────────────────────────────────────────────────────────

def scrape_channel4(conn: sqlite3.Connection) -> int:
    """
    Scrape Channel 4 catalogue via sitemap + search API enrichment.
    The sitemap has the definitive list of all programmes; the search API
    provides proper titles and descriptions for as many as possible.
    """
    count = 0
    seen_slugs = set()
    skip_slugs = {"a-z", "categories", "highlights"}

    # Step 1: Parse the sitemap for the full list of programme slugs
    logger.info("Channel 4: fetching sitemap")
    slugs_to_enrich = []
    try:
        resp = requests.get("https://www.channel4.com/sitemap/1.xml",
                            headers=HEADERS_DEFAULT, timeout=60, stream=True)
        resp.raise_for_status()
        from xml.etree import ElementTree as ET
        for event, elem in ET.iterparse(resp.raw, events=("end",)):
            if elem.tag.endswith("}loc") or elem.tag == "loc":
                url_text = (elem.text or "").strip()
                m = re.match(r"https://www\.channel4\.com/programmes/([a-z0-9-]+)$", url_text)
                if m:
                    slug = m.group(1)
                    if slug not in seen_slugs and slug not in skip_slugs:
                        seen_slugs.add(slug)
                        slugs_to_enrich.append(slug)
                elem.clear()
        logger.info(f"Channel 4: found {len(slugs_to_enrich)} programme slugs in sitemap")
    except Exception as e:
        logger.error(f"Channel 4 sitemap error: {e}")

    # Step 2: Batch-enrich via search API (get proper titles/descriptions)
    enriched = {}
    search_headers = {
        **HEADERS_DEFAULT,
        "Referer": "https://www.channel4.com/",
        "Origin": "https://www.channel4.com",
    }
    search_queries = list(string.ascii_lowercase) + [
        "the", "great", "british", "bake", "taskmaster", "hollyoaks",
        "gogglebox", "countdown", "film", "documentary", "drama",
    ]
    logger.info("Channel 4: enriching via search API")
    for q in search_queries:
        url = f"https://all4nav.channel4.com/v1/api/search?expand=default&q={quote(q)}&limit=100&offset=0"
        try:
            resp = requests.get(url, headers=search_headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            content_type = resp.headers.get("Content-Type", "")
            if "json" not in content_type:
                continue
            data = resp.json()
            items = data if isinstance(data, list) else data.get("results", data.get("items", []))
            for item in items:
                brand = item.get("brand", item)
                slug = brand.get("websafeTitle") or brand.get("slug") or brand.get("id", "")
                if slug and slug not in enriched:
                    enriched[slug] = {
                        "title": brand.get("title", ""),
                        "description": brand.get("description", brand.get("synopsis", "")),
                        "image_url": brand.get("thumbnailUrl", brand.get("image", "")),
                    }
        except Exception as e:
            logger.error(f"Channel 4 search error for '{q}': {e}")
        time.sleep(0.5)

    logger.info(f"Channel 4: enriched {len(enriched)} programmes via search API")

    # Step 3: Upsert all programmes
    for slug in slugs_to_enrich:
        meta = enriched.get(slug, {})
        title = meta.get("title", "").strip()
        if not title:
            title = _slug_to_title(slug)
        upsert_programme(conn, "channel4", title,
                         url=f"https://www.channel4.com/programmes/{slug}",
                         description=meta.get("description", ""),
                         image_url=meta.get("image_url", ""),
                         programme_id=slug)
        count += 1

    conn.commit()
    return count


def _slug_to_title(slug: str) -> str:
    """Convert a URL slug to a readable title."""
    words = slug.replace("-", " ").split()
    small_words = {"a", "an", "the", "and", "but", "or", "for", "nor",
                   "at", "by", "in", "of", "on", "to", "up", "is", "it"}
    return " ".join(
        w if i > 0 and w in small_words else w.capitalize()
        for i, w in enumerate(words)
    )


# ── Channel 5 ──────────────────────────────────────────────────────────────

def scrape_channel5(conn: sqlite3.Connection) -> int:
    """
    Scrape Channel 5 (My5) catalogue using their JSON search API.
    An empty query returns the entire catalogue in a single request.
    """
    count = 0
    seen_ids = set()
    headers = {
        **HEADERS_DEFAULT,
        "Origin": "https://www.channel5.com",
        "Referer": "https://www.channel5.com/",
    }

    logger.info("Channel 5: fetching full catalogue via empty search query")
    try:
        resp = requests.get(
            "https://corona.channel5.com/shows/search.json?query=&platform=my5desktop&friendly=1",
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        shows = data.get("shows", [])
        logger.info(f"Channel 5: API returned {len(shows)} shows")
        for show in shows:
            sid = str(show.get("id", show.get("f_name", "")))
            if not sid or sid in seen_ids:
                continue
            seen_ids.add(sid)
            title = show.get("title", "")
            if not title:
                continue
            friendly = show.get("f_name", "")
            upsert_programme(conn, "channel5", title,
                             url=f"https://www.channel5.com/show/{friendly}" if friendly else None,
                             description=show.get("s_desc", ""),
                             image_url=show.get("image", ""),
                             programme_id=sid,
                             extra={"genre": show.get("genre")})
            count += 1
    except Exception as e:
        logger.error(f"Channel 5 error: {e}")

    conn.commit()
    return count


# ── PBS America ────────────────────────────────────────────────────────────

def scrape_pbs_america(conn: sqlite3.Connection) -> int:
    """
    Scrape PBS America catalogue via WordPress sitemap.
    The sitemap lists all series URLs without needing Playwright.
    """
    count = 0
    seen_slugs = set()

    logger.info("PBS America: fetching WordPress sitemap")
    try:
        resp = requests.get(
            "https://www.pbsamerica.co.uk/wp-sitemap-posts-series-1.xml",
            headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        from xml.etree import ElementTree as ET
        root = ET.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for url_el in root.findall(".//sm:url/sm:loc", ns):
            url_text = (url_el.text or "").strip()
            m = re.search(r"/series/([a-z0-9-]+?)/?$", url_text)
            if not m:
                continue
            slug = m.group(1)
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            title = _slug_to_title(slug)
            upsert_programme(conn, "pbs_america", title,
                             url=f"https://www.pbsamerica.co.uk/series/{slug}/",
                             programme_id=slug)
            count += 1

        logger.info(f"PBS America: got {count} series from sitemap")
    except Exception as e:
        logger.error(f"PBS America sitemap error: {e}")

    conn.commit()
    return count


# ── Pluto TV UK ────────────────────────────────────────────────────────────

def scrape_pluto_tv(conn: sqlite3.Connection) -> int:
    """
    Scrape Pluto TV UK catalogue via the VOD categories API.
    Fetches all categories with items, then individually fetches any
    truncated categories to get the full list.
    """
    count = 0
    seen_ids = set()

    api_base = "https://api.pluto.tv/v3/vod/categories"
    params = "includeItems=true&deviceType=web&countryCode=GB&offset=1000"

    logger.info("Pluto TV UK: fetching VOD categories API")
    try:
        resp = requests.get(f"{api_base}?{params}",
                            headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        categories = resp.json()
        if not isinstance(categories, list):
            categories = categories.get("categories", [])

        truncated_cats = []
        for cat in categories:
            cat_name = cat.get("name", "")
            cat_id = cat.get("_id", "")
            items = cat.get("items", [])
            total = cat.get("totalItemsCount", len(items))

            count += _process_pluto_items(conn, items, seen_ids, cat_name)

            if total > len(items):
                truncated_cats.append((cat_id, cat_name, total))

        logger.info(f"Pluto TV: got {count} from main categories, "
                     f"{len(truncated_cats)} categories need full fetch")

        for cat_id, cat_name, total in truncated_cats:
            try:
                resp = requests.get(
                    f"{api_base}/{cat_id}/items?deviceType=web&countryCode=GB&offset={total + 100}",
                    headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    items = resp.json()
                    if not isinstance(items, list):
                        items = items.get("items", [])
                    count += _process_pluto_items(conn, items, seen_ids, cat_name)
            except Exception as e:
                logger.warning(f"Pluto TV category {cat_name} fetch error: {e}")
            time.sleep(0.5)

    except Exception as e:
        logger.error(f"Pluto TV VOD API error: {e}")

    logger.info(f"Pluto TV: {count} total programmes")
    conn.commit()
    return count


def _process_pluto_items(conn: sqlite3.Connection, items: list,
                         seen_ids: set, category: str) -> int:
    """Process a list of Pluto TV VOD items, returning count of new items."""
    count = 0
    for item in items:
        iid = item.get("_id", item.get("id", ""))
        if not iid or iid in seen_ids:
            continue
        seen_ids.add(iid)
        title = item.get("name", item.get("title", ""))
        if not title:
            continue
        item_type = item.get("type", "movie")
        slug = item.get("slug", "")
        path = "movies" if item_type == "movie" else "series"
        watch_url = f"https://pluto.tv/uk/on-demand/{path}/{slug or iid}"

        img_url = ""
        covers = item.get("covers", item.get("images", []))
        if isinstance(covers, list) and covers:
            img_url = covers[0].get("url", "")
        elif isinstance(covers, dict):
            img_url = covers.get("default", covers.get("poster", ""))
        if not img_url:
            featured = item.get("featuredImage", {})
            if isinstance(featured, dict):
                img_url = featured.get("path", "")

        upsert_programme(conn, "pluto_tv_uk", title,
                         url=watch_url,
                         description=item.get("description", item.get("synopsis", "")),
                         image_url=img_url,
                         category=category,
                         programme_id=iid,
                         extra={"genre": item.get("genre"), "rating": item.get("rating")})
        count += 1
    return count


# ── Tubi ───────────────────────────────────────────────────────────────────

def scrape_tubi(conn: sqlite3.Connection) -> int:
    """
    Scrape Tubi catalogue using Playwright.
    Browse category pages and search for common terms.
    """
    count = 0
    seen_ids = set()

    # Browse category pages
    category_urls = [
        "https://tubitv.com/category/action",
        "https://tubitv.com/category/comedy",
        "https://tubitv.com/category/drama",
        "https://tubitv.com/category/horror",
        "https://tubitv.com/category/thriller",
        "https://tubitv.com/category/documentary",
        "https://tubitv.com/category/family",
        "https://tubitv.com/category/reality",
        "https://tubitv.com/category/romance",
        "https://tubitv.com/category/sci-fi",
        "https://tubitv.com/category/anime",
        "https://tubitv.com/category/crime",
    ]

    for cat_url in category_urls:
        logger.info(f"Tubi: browsing {cat_url}")
        try:
            page, ctx = pw_new_page(cat_url, wait_ms=6000)
            for _ in range(15):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(1500)

            links = page.query_selector_all("a[href*='/movies/'], a[href*='/series/']")
            for link in links:
                href = link.get_attribute("href") or ""
                m = re.search(r"/(movies|series)/(\d+)", href)
                if not m:
                    continue
                content_type, cid = m.group(1), m.group(2)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                title = link.inner_text().strip()
                if not title or len(title) < 2:
                    img_el = link.query_selector("img")
                    if img_el:
                        title = img_el.get_attribute("alt") or ""
                if not title:
                    continue
                # Get thumbnail
                img_el = link.query_selector("img")
                img_url = img_el.get_attribute("src") if img_el else ""
                upsert_programme(conn, "tubi", title,
                                 url=f"https://tubitv.com{href}" if not href.startswith("http") else href,
                                 image_url=img_url or "",
                                 programme_id=cid,
                                 category=cat_url.split("/")[-1])
                count += 1
            ctx.close()
        except Exception as e:
            logger.error(f"Tubi category error for {cat_url}: {e}")
        time.sleep(2)

    # Search for additional terms
    search_terms = [
        "british", "classic", "thriller", "mystery", "western",
        "war", "music", "nature", "sports", "kids",
    ]
    for term in search_terms:
        logger.info(f"Tubi: searching '{term}'")
        try:
            page, ctx = pw_new_page(
                f"https://tubitv.com/search/{quote(term)}",
                wait_selector="a[href*='/movies/'], a[href*='/series/']",
                wait_ms=6000,
            )
            links = page.query_selector_all("a[href*='/movies/'], a[href*='/series/']")
            for link in links:
                href = link.get_attribute("href") or ""
                m = re.search(r"/(movies|series)/(\d+)", href)
                if not m:
                    continue
                cid = m.group(2)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                title = link.inner_text().strip()
                if not title:
                    continue
                upsert_programme(conn, "tubi", title,
                                 url=f"https://tubitv.com{href}",
                                 programme_id=cid)
                count += 1
            ctx.close()
        except Exception as e:
            logger.error(f"Tubi search error for '{term}': {e}")
        time.sleep(2)

    conn.commit()
    return count


# ── TPTV Encore ────────────────────────────────────────────────────────────

def scrape_tptv_encore(conn: sqlite3.Connection) -> int:
    """
    Scrape TPTV Encore catalogue using Playwright.
    Browse categories and search. Titles extracted from URL slugs since
    innerText is unreliable.
    """
    count = 0
    seen_slugs = set()

    # Try browsing the main catalogue page
    browse_urls = [
        "https://www.tptvencore.co.uk/",
        "https://www.tptvencore.co.uk/category/films",
        "https://www.tptvencore.co.uk/category/series",
    ]

    for browse_url in browse_urls:
        logger.info(f"TPTV Encore: browsing {browse_url}")
        try:
            page, ctx = pw_new_page(browse_url, wait_ms=8000)
            for _ in range(15):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(2000)

            links = page.query_selector_all("a[href*='/product/']")
            for link in links:
                href = link.get_attribute("href") or ""
                m = re.search(r"/product/([a-z0-9-]+)", href)
                if not m:
                    continue
                raw_slug = m.group(1)
                if raw_slug in seen_slugs:
                    continue
                seen_slugs.add(raw_slug)

                # Extract title from slug: strip trailing numeric ID
                title = _tptv_slug_to_title(raw_slug)
                if not title:
                    continue

                full_url = f"https://www.tptvencore.co.uk/product/{raw_slug}"
                upsert_programme(conn, "tptv_encore", title,
                                 url=full_url,
                                 programme_id=raw_slug)
                count += 1
            ctx.close()
        except Exception as e:
            logger.error(f"TPTV Encore browse error: {e}")
        time.sleep(2)

    # Supplement with search
    search_terms = list(string.ascii_lowercase[:10]) + [
        "war", "western", "mystery", "comedy", "drama", "thriller",
        "horror", "adventure", "classic", "british",
    ]
    for term in search_terms:
        logger.info(f"TPTV Encore: searching '{term}'")
        try:
            page, ctx = pw_new_page(
                f"https://www.tptvencore.co.uk/search/{quote(term)}",
                wait_selector="a[href*='/product/']",
                wait_ms=6000,
            )
            links = page.query_selector_all("a[href*='/product/']")
            for link in links:
                href = link.get_attribute("href") or ""
                m = re.search(r"/product/([a-z0-9-]+)", href)
                if not m:
                    continue
                raw_slug = m.group(1)
                if raw_slug in seen_slugs:
                    continue
                seen_slugs.add(raw_slug)
                title = _tptv_slug_to_title(raw_slug)
                if not title:
                    continue
                upsert_programme(conn, "tptv_encore", title,
                                 url=f"https://www.tptvencore.co.uk/product/{raw_slug}",
                                 programme_id=raw_slug)
                count += 1
            ctx.close()
        except Exception as e:
            logger.error(f"TPTV Encore search error for '{term}': {e}")
        time.sleep(2)

    conn.commit()
    return count


def _tptv_slug_to_title(slug: str) -> str:
    """
    Convert a TPTV Encore product slug to a readable title.
    e.g. 'sea-of-sand-6301823137001' → 'Sea of Sand'
    Strip trailing numeric IDs, convert hyphens to spaces, title-case.
    """
    # Remove trailing numeric segment(s) — TPTV uses long numeric IDs
    cleaned = re.sub(r"-\d{5,}$", "", slug)
    if not cleaned:
        return ""
    title = cleaned.replace("-", " ").strip()
    # Title case but keep small words lowercase
    words = title.split()
    small_words = {"a", "an", "the", "and", "but", "or", "for", "nor",
                   "at", "by", "in", "of", "on", "to", "up", "is", "it"}
    result = []
    for i, word in enumerate(words):
        if i == 0 or word not in small_words:
            result.append(word.capitalize())
        else:
            result.append(word)
    return " ".join(result)


# ── STV Player ────────────────────────────────────────────────────────────

def scrape_stv(conn: sqlite3.Connection) -> int:
    """
    Scrape STV Player catalogue via their public JSON API.
    Paginate with limit=300 until no more results.
    """
    count = 0
    seen_ids = set()
    offset = 0
    limit = 300

    while True:
        url = f"https://player.api.stv.tv/v1/programmes?limit={limit}&offset={offset}"
        logger.info(f"STV Player: fetching offset={offset}")
        try:
            resp = requests.get(url, headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.warning(f"STV Player returned {resp.status_code} at offset {offset}")
                break
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                pid = str(item.get("guid") or item.get("id", ""))
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                title = (item.get("name") or item.get("shortName", "")).strip()
                if not title:
                    continue
                watch_url = item.get("_permalink", "")
                img_url = ""
                images = item.get("images", [])
                if isinstance(images, list):
                    for img in images:
                        if img.get("imageType") == "mainImage":
                            img_url = img.get("_filepath", "")
                            break
                    if not img_url and images:
                        img_url = images[0].get("_filepath", "")
                genre = item.get("genre", {})
                genre_name = genre.get("name", "") if isinstance(genre, dict) else ""
                upsert_programme(conn, "stv_player", title,
                                 url=watch_url,
                                 description=item.get("shortDescription", item.get("longDescription", "")),
                                 image_url=img_url,
                                 programme_id=pid,
                                 extra={"genre": genre_name} if genre_name else None)
                count += 1
            if len(results) < limit:
                break
            offset += limit
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"STV Player error at offset {offset}: {e}")
            break
        time.sleep(0.5)

    conn.commit()
    logger.info(f"STV Player: {count} programmes")
    return count


# ── UKTV Play ─────────────────────────────────────────────────────────────

def scrape_uktv(conn: sqlite3.Connection) -> int:
    """
    Scrape UKTV Play (U) catalogue via their brand list API.
    Single JSON endpoint returns all available shows.
    """
    count = 0
    seen_ids = set()

    logger.info("UKTV Play: fetching brand list")
    try:
        resp = requests.get(
            "https://vschedules.uktv.co.uk/vod/brand_list/?format=json",
            headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        brands = data if isinstance(data, list) else data.get("brands", data.get("results", []))
        logger.info(f"UKTV Play: API returned {len(brands)} brands")

        for brand in brands:
            bid = str(brand.get("id") or brand.get("slug", ""))
            if not bid or bid in seen_ids:
                continue
            seen_ids.add(bid)
            title = (brand.get("name") or brand.get("title", "")).strip()
            if not title:
                continue
            slug = brand.get("slug", "")
            watch_url = f"https://u.co.uk/shows/{slug}/watch-online" if slug else ""
            img_url = brand.get("image", brand.get("image_url", ""))
            upsert_programme(conn, "uktv_play", title,
                             url=watch_url,
                             description=brand.get("synopsis", brand.get("description", "")),
                             image_url=img_url,
                             programme_id=bid,
                             extra={"channel": brand.get("channel"), "genre": brand.get("genre")})
            count += 1

    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error(f"UKTV Play error: {e}")

    conn.commit()
    logger.info(f"UKTV Play: {count} programmes")
    return count


# ── Filmzie ───────────────────────────────────────────────────────────────

def scrape_filmzie(conn: sqlite3.Connection) -> int:
    """
    Scrape Filmzie catalogue via their paginated content API.
    Response structure: { data: { data: [...items], paging: { total, limit, offset } } }
    Filmzie is app-based so URLs point to the main site.
    """
    count = 0
    seen_ids = set()
    offset = 0
    limit = 100

    while True:
        url = f"https://filmzie.com/api/v1/content?limit={limit}&offset={offset}"
        logger.info(f"Filmzie: fetching offset={offset}")
        try:
            resp = requests.get(url, headers=HEADERS_DEFAULT, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.warning(f"Filmzie returned {resp.status_code} at offset {offset}")
                break
            outer = resp.json().get("data", {})
            items = outer.get("data", [])
            paging = outer.get("paging", {})
            total = paging.get("total", 0)
            if not items:
                break
            for item in items:
                fid = str(item.get("id", ""))
                if not fid or fid in seen_ids:
                    continue
                seen_ids.add(fid)
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                sef = item.get("sefName", "")
                categories = item.get("category", [])
                released = (item.get("released") or "")[:4]
                upsert_programme(conn, "filmzie", title,
                                 url=f"https://filmzie.com/{sef}" if sef else "https://filmzie.com",
                                 description=(item.get("description") or "")[:500],
                                 programme_id=fid,
                                 extra={"genre": categories,
                                        "year": released,
                                        "type": item.get("type")})
                count += 1
            if offset + limit >= total:
                break
            offset += limit
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"Filmzie error at offset {offset}: {e}")
            break
        time.sleep(0.5)

    conn.commit()
    logger.info(f"Filmzie: {count} programmes")
    return count


# ── Rakuten TV ────────────────────────────────────────────────────────────

def scrape_rakuten(conn: sqlite3.Connection) -> int:
    """
    Scrape Rakuten TV free UK catalogue via their Gizmo API.
    Two endpoints: free movies and free TV shows.
    Response: { data: [...items], meta: { pagination: { total_pages, page } } }
    """
    count = 0
    seen_ids = set()
    headers = {
        **HEADERS_DEFAULT,
        "Accept": "application/json",
    }

    lists = [
        ("free-all-movies", "movies"),
        ("free-all-tv-shows", "tv-series"),
    ]

    for list_slug, url_path in lists:
        page = 1
        while True:
            url = (f"https://gizmo.rakuten.tv/v3/lists/{list_slug}/contents"
                   f"?classification_id=18&device_identifier=web"
                   f"&locale=en&market_code=uk&per_page=50&page={page}")
            logger.info(f"Rakuten TV: fetching {list_slug} page {page}")
            try:
                resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    logger.warning(f"Rakuten TV returned {resp.status_code}")
                    break
                body = resp.json()
                items = body.get("data", [])
                if not items:
                    break
                for item in items:
                    rid = str(item.get("id", ""))
                    if not rid or rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    title = (item.get("title") or "").strip()
                    if not title:
                        continue
                    watch_url = f"https://www.rakuten.tv/uk/{url_path}/{rid}"
                    imgs = item.get("images", {})
                    img_url = ""
                    if isinstance(imgs, dict):
                        img_url = imgs.get("artwork", imgs.get("snapshot", "")) or ""
                    upsert_programme(conn, "rakuten_tv", title,
                                     url=watch_url,
                                     description=item.get("short_plot", ""),
                                     image_url=img_url,
                                     programme_id=rid,
                                     extra={"year": item.get("year"),
                                            "type": url_path,
                                            "genre": item.get("genres")})
                    count += 1
                pagination = body.get("meta", {}).get("pagination", {})
                total_pages = pagination.get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1
            except (requests.RequestException, json.JSONDecodeError) as e:
                logger.error(f"Rakuten TV error for {list_slug} page {page}: {e}")
                break
            time.sleep(0.5)

    conn.commit()
    logger.info(f"Rakuten TV: {count} programmes")
    return count


# ── Wedotv ────────────────────────────────────────────────────────────────

def scrape_wedotv(conn: sqlite3.Connection) -> int:
    """
    Scrape Wedotv catalogue via their movies.xml and series.xml sitemaps.
    Filter for en-gb locale URLs only.
    """
    count = 0
    seen_slugs = set()
    from xml.etree import ElementTree as ET

    for sitemap in ["movies.xml", "series.xml"]:
        logger.info(f"Wedotv: fetching {sitemap}")
        try:
            resp = requests.get(
                f"https://wedotv.com/sitemap/{sitemap}",
                headers=HEADERS_DEFAULT, timeout=60,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for loc_el in root.findall(".//sm:url/sm:loc", ns):
                url_text = (loc_el.text or "").strip()
                m = re.match(r"https://www\.wedotv\.com/en-gb/([a-z0-9-]+)", url_text)
                if not m:
                    continue
                slug = m.group(1)
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                title = _slug_to_title(slug)
                upsert_programme(conn, "wedotv", title,
                                 url=url_text,
                                 programme_id=slug)
                count += 1
        except Exception as e:
            logger.error(f"Wedotv {sitemap} error: {e}")

    logger.info(f"Wedotv: {count} programmes from sitemaps")
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------

SERVICES = {
    "bbc":       ("BBC iPlayer",    scrape_bbc),
    "itvx":      ("ITVX",           scrape_itvx),
    "channel4":  ("Channel 4",      scrape_channel4),
    "channel5":  ("Channel 5",      scrape_channel5),
    "pbs":       ("PBS America",    scrape_pbs_america),
    "pluto":     ("Pluto TV UK",    scrape_pluto_tv),
    "tubi":      ("Tubi",           scrape_tubi),
    "tptv":      ("TPTV Encore",    scrape_tptv_encore),
    "uktv":      ("UKTV Play",      scrape_uktv),
    "filmzie":   ("Filmzie",        scrape_filmzie),
    "rakuten":   ("Rakuten TV",     scrape_rakuten),
    "wedotv":    ("Wedotv",         scrape_wedotv),
}


def run_scrape(conn: sqlite3.Connection, service_key: str):
    """Run a single service scrape with logging."""
    name, func = SERVICES[service_key]
    logger.info(f"{'='*60}")
    logger.info(f"Starting scrape: {name}")
    logger.info(f"{'='*60}")

    started = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO scrape_log (service, started_at, status) VALUES (?, ?, 'running')",
                 (service_key, started))
    conn.commit()
    log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        # Clear old data for this service before fresh scrape
        clear_service(conn, name.lower().replace(" ", "_"))
        # Also clear by service key pattern
        for svc_name in [name, service_key, name.lower().replace(" ", "_")]:
            conn.execute("DELETE FROM programmes WHERE service = ?", (svc_name,))
        conn.commit()

        count = func(conn)

        conn.execute(
            "UPDATE scrape_log SET finished_at=?, status='success', count=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), count, log_id)
        )
        conn.commit()
        logger.info(f"✓ {name}: {count} programmes scraped")
        return count

    except Exception as e:
        conn.execute(
            "UPDATE scrape_log SET finished_at=?, status='error', error_msg=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), str(e), log_id)
        )
        conn.commit()
        logger.error(f"✗ {name}: {e}")
        return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="UK Free Streaming Catalogue Scraper")
    parser.add_argument("--service", "-s", choices=list(SERVICES.keys()),
                        help="Scrape a single service (default: all)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available services")
    parser.add_argument("--db", default=str(DB_PATH),
                        help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=LOG_FORMAT,
    )

    if args.list:
        print("\nAvailable services:")
        for key, (name, _) in SERVICES.items():
            print(f"  {key:12s}  {name}")
        print()
        return

    db_path = Path(args.db)
    conn = init_db(db_path)
    logger.info(f"Database: {db_path}")

    total = 0
    services_to_run = [args.service] if args.service else list(SERVICES.keys())

    for svc in services_to_run:
        total += run_scrape(conn, svc)

    logger.info(f"\n{'='*60}")
    logger.info(f"Scraping complete. Total programmes: {total}")
    logger.info(f"Database: {db_path}")

    # Print summary
    cursor = conn.execute("""
        SELECT service, COUNT(*) as cnt
        FROM programmes
        GROUP BY service
        ORDER BY cnt DESC
    """)
    print("\nCatalogue summary:")
    print(f"{'Service':<20} {'Count':>8}")
    print("-" * 30)
    for row in cursor:
        print(f"{row[0]:<20} {row[1]:>8}")
    print("-" * 30)
    print(f"{'TOTAL':<20} {total:>8}")

    # Snapshot old catalogue titles before overwriting
    catalogue_path = db_path.parent / "catalogue.json"
    old_titles = set()
    if catalogue_path.exists():
        try:
            for item in json.loads(catalogue_path.read_text()):
                old_titles.add((item.get("service", ""), item.get("title", "").strip().lower()))
        except (json.JSONDecodeError, IOError):
            pass

    # Export to JSON for the static site
    export_json(conn, catalogue_path)

    # Find new titles added since last scrape
    if old_titles:
        new_data = json.loads(catalogue_path.read_text())
        new_titles = [
            item for item in new_data
            if (item.get("service", ""), item.get("title", "").strip().lower()) not in old_titles
        ]
        logger.info(f"Found {len(new_titles)} new titles since last scrape")
        if new_titles:
            new_all_path = catalogue_path.parent / "new_titles.json"
            new_all_path.write_text(json.dumps(new_titles, ensure_ascii=False, indent=2))
            logger.info(f"Saved new titles to {new_all_path}")
            print(f"\n{len(new_titles)} new titles — run 'python pick_highlights.py' to choose highlights")

    close_browser()
    conn.close()


def export_json(conn: sqlite3.Connection, out_path: Path):
    """Export the full catalogue to a JSON file for the static search UI."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT service, title, url, description, image_url, category
        FROM programmes
        ORDER BY service, title COLLATE NOCASE
    """).fetchall()
    data = [dict(r) for r in rows]
    out_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    logger.info(f"Exported {len(data)} programmes to {out_path}")


if __name__ == "__main__":
    main()
