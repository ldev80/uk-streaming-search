#!/usr/bin/env python3
"""
UK Free Streaming Catalogue — Web Search UI
=============================================
A lightweight local web server providing instant search across all services.

Usage:
    python search_web.py                 # Start on port 8899
    python search_web.py --port 9000     # Custom port

Open http://localhost:8899 in your browser.
No external web framework required — uses Python's built-in http.server.
"""

import argparse
import html
import json
import sqlite3
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

DB_PATH = Path(__file__).parent / "catalogue.db"

SERVICE_META = {
    "bbc_iplayer":  {"label": "BBC iPlayer",  "colour": "#e4134f", "icon": "📺"},
    "itvx":         {"label": "ITVX",          "colour": "#00b2a9", "icon": "📡"},
    "channel4":     {"label": "Channel 4",     "colour": "#0d47a1", "icon": "4️⃣"},
    "channel5":     {"label": "Channel 5",     "colour": "#6a1b9a", "icon": "5️⃣"},
    "pbs_america":  {"label": "PBS America",   "colour": "#1565c0", "icon": "🇺🇸"},
    "pluto_tv_uk":  {"label": "Pluto TV UK",   "colour": "#1a237e", "icon": "🪐"},
    "tubi":         {"label": "Tubi",           "colour": "#ff6100", "icon": "🎬"},
    "tptv_encore":  {"label": "TPTV Encore",   "colour": "#795548", "icon": "🎞️"},
}

SERVICE_KEY_MAP = {
    "bbc": "bbc_iplayer", "iplayer": "bbc_iplayer",
    "itvx": "itvx", "itv": "itvx",
    "channel4": "channel4", "c4": "channel4",
    "channel5": "channel5", "c5": "channel5",
    "pbs": "pbs_america",
    "pluto": "pluto_tv_uk",
    "tubi": "tubi",
    "tptv": "tptv_encore",
}


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def do_search(query: str, service: str = "", limit: int = 200) -> list[dict]:
    conn = get_db()
    q = query.lower().strip()
    params = [f"%{q}%"]
    sql = "SELECT * FROM programmes WHERE title_lower LIKE ?"
    if service:
        svc = SERVICE_KEY_MAP.get(service.lower(), service.lower())
        sql += " AND service = ?"
        params.append(svc)
    sql += " ORDER BY service, title_lower LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    conn = get_db()
    rows = conn.execute("""
        SELECT service, COUNT(*) as cnt
        FROM programmes GROUP BY service ORDER BY cnt DESC
    """).fetchall()
    total = sum(r["cnt"] for r in rows)
    conn.close()
    return {"services": [dict(r) for r in rows], "total": total}


def build_html_page(query: str = "", service: str = "", results: list = None,
                    stats: dict = None) -> str:
    """Build the complete HTML page."""

    # Build stats badges
    stats = stats or get_stats()
    badges_html = ""
    for s in stats["services"]:
        meta = SERVICE_META.get(s["service"], {"label": s["service"], "colour": "#666", "icon": "📺"})
        badges_html += f'''
            <span class="badge" style="--svc-colour: {meta['colour']}"
                  onclick="filterService('{s['service']}')"
                  title="Filter to {meta['label']}">
                {meta['icon']} {meta['label']} <em>{s['cnt']:,}</em>
            </span>
        '''

    # Build results
    results_html = ""
    if query and results is not None:
        if not results:
            results_html = '<div class="no-results">No matches found. The query must appear in the programme title.</div>'
        else:
            current_svc = None
            for r in results:
                svc = r["service"]
                meta = SERVICE_META.get(svc, {"label": svc, "colour": "#666", "icon": "📺"})

                if svc != current_svc:
                    if current_svc is not None:
                        results_html += '</div>'  # close previous group
                    current_svc = svc
                    results_html += f'''
                        <div class="service-group">
                            <div class="service-header" style="--svc-colour: {meta['colour']}">
                                {meta['icon']} {meta['label']}
                            </div>
                    '''

                title_escaped = html.escape(r["title"])
                # Highlight match in title
                q_lower = query.lower()
                t_lower = title_escaped.lower()
                idx = t_lower.find(q_lower)
                if idx >= 0:
                    before = title_escaped[:idx]
                    match = title_escaped[idx:idx+len(query)]
                    after = title_escaped[idx+len(query):]
                    title_display = f'{before}<mark>{match}</mark>{after}'
                else:
                    title_display = title_escaped

                desc = html.escape(r.get("description") or "")[:200]
                url = r.get("url") or "#"
                img = r.get("image_url") or ""

                img_html = ""
                if img:
                    img_html = f'<img src="{html.escape(img)}" alt="" loading="lazy" onerror="this.style.display=\'none\'">'

                results_html += f'''
                    <a class="result-card" href="{html.escape(url)}" target="_blank" rel="noopener">
                        <div class="card-image">{img_html}</div>
                        <div class="card-body">
                            <div class="card-title">{title_display}</div>
                            <div class="card-desc">{desc}</div>
                        </div>
                        <div class="card-arrow">→</div>
                    </a>
                '''

            if current_svc is not None:
                results_html += '</div>'  # close last group

            results_html = f'<div class="results-count">{len(results)} result{"s" if len(results) != 1 else ""}</div>' + results_html

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UK Free Streaming Search</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&family=Space+Mono:wght@400;700&display=swap');

:root {{
    --bg: #0c0e12;
    --surface: #161920;
    --surface2: #1e222c;
    --border: #2a2f3a;
    --text: #e8eaed;
    --text-dim: #8a8f9c;
    --accent: #60a5fa;
    --accent-glow: rgba(96, 165, 250, 0.15);
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: 'DM Sans', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
}}

.container {{
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
}}

header {{
    text-align: center;
    margin-bottom: 2.5rem;
}}

header h1 {{
    font-family: 'Space Mono', monospace;
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin-bottom: 0.4rem;
    background: linear-gradient(135deg, #60a5fa 0%, #a78bfa 50%, #f472b6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}

header p {{
    color: var(--text-dim);
    font-size: 0.9rem;
}}

.search-box {{
    position: relative;
    margin-bottom: 1.5rem;
}}

.search-box input {{
    width: 100%;
    padding: 1rem 1.2rem 1rem 3rem;
    font-family: 'DM Sans', sans-serif;
    font-size: 1.1rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: var(--text);
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
}}

.search-box input:focus {{
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
}}

.search-box input::placeholder {{ color: var(--text-dim); }}

.search-box .icon {{
    position: absolute;
    left: 1rem;
    top: 50%;
    transform: translateY(-50%);
    font-size: 1.2rem;
    color: var(--text-dim);
    pointer-events: none;
}}

.search-box .clear-btn {{
    position: absolute;
    right: 0.8rem;
    top: 50%;
    transform: translateY(-50%);
    background: var(--surface2);
    border: none;
    color: var(--text-dim);
    font-size: 0.85rem;
    padding: 0.3rem 0.6rem;
    border-radius: 6px;
    cursor: pointer;
    display: none;
    font-family: 'Space Mono', monospace;
}}

.search-box input:not(:placeholder-shown) ~ .clear-btn {{ display: block; }}

.badges {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 2rem;
    justify-content: center;
}}

.badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.4rem 0.75rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: all 0.2s;
    border-left: 3px solid var(--svc-colour);
}}

.badge:hover {{
    background: var(--surface2);
    border-color: var(--svc-colour);
}}

.badge.active {{
    background: var(--surface2);
    border-color: var(--svc-colour);
    box-shadow: 0 0 8px rgba(96, 165, 250, 0.1);
}}

.badge em {{
    font-style: normal;
    color: var(--text-dim);
    font-family: 'Space Mono', monospace;
    font-size: 0.75rem;
}}

.results-count {{
    font-family: 'Space Mono', monospace;
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-bottom: 1rem;
    padding-left: 0.2rem;
}}

.no-results {{
    text-align: center;
    padding: 3rem;
    color: var(--text-dim);
    font-size: 1rem;
}}

.service-group {{
    margin-bottom: 1.5rem;
}}

.service-header {{
    font-family: 'Space Mono', monospace;
    font-size: 0.85rem;
    font-weight: 700;
    color: var(--svc-colour);
    padding: 0.5rem 0;
    margin-bottom: 0.5rem;
    border-bottom: 1px solid var(--border);
}}

.result-card {{
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.8rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 0.5rem;
    text-decoration: none;
    color: inherit;
    transition: all 0.15s;
}}

.result-card:hover {{
    background: var(--surface2);
    border-color: var(--accent);
    transform: translateX(2px);
}}

.card-image {{
    width: 64px;
    height: 48px;
    border-radius: 6px;
    overflow: hidden;
    background: var(--surface2);
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}}

.card-image img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
}}

.card-body {{
    flex: 1;
    min-width: 0;
}}

.card-title {{
    font-weight: 600;
    font-size: 0.95rem;
    margin-bottom: 0.2rem;
}}

.card-title mark {{
    background: var(--accent-glow);
    color: var(--accent);
    border-radius: 2px;
    padding: 0 2px;
}}

.card-desc {{
    font-size: 0.8rem;
    color: var(--text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}

.card-arrow {{
    color: var(--text-dim);
    font-size: 1.2rem;
    flex-shrink: 0;
    transition: transform 0.15s;
}}

.result-card:hover .card-arrow {{
    transform: translateX(3px);
    color: var(--accent);
}}

.footer {{
    text-align: center;
    margin-top: 3rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 0.8rem;
}}

.footer kbd {{
    font-family: 'Space Mono', monospace;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.15rem 0.4rem;
    font-size: 0.75rem;
}}

/* Loading spinner */
.spinner {{
    display: none;
    width: 20px;
    height: 20px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    position: absolute;
    right: 0.8rem;
    top: 50%;
    transform: translateY(-50%);
}}
@keyframes spin {{ to {{ transform: translateY(-50%) rotate(360deg); }} }}

@media (max-width: 600px) {{
    .container {{ padding: 1rem; }}
    header h1 {{ font-size: 1.3rem; }}
    .card-image {{ display: none; }}
}}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>UK FREE STREAMING SEARCH</h1>
        <p>{stats['total']:,} programmes across {len(stats['services'])} services</p>
    </header>

    <form method="GET" action="/" autocomplete="off" id="searchForm">
        <div class="search-box">
            <span class="icon">⌕</span>
            <input type="text" name="q" id="searchInput"
                   placeholder="Search titles…"
                   value="{html.escape(query)}"
                   autofocus>
            <button type="button" class="clear-btn" onclick="clearSearch()">ESC</button>
            <input type="hidden" name="service" id="serviceInput" value="{html.escape(service)}">
            <div class="spinner" id="spinner"></div>
        </div>
    </form>

    <div class="badges" id="badges">
        <span class="badge {'active' if not service else ''}"
              onclick="filterService('')"
              style="--svc-colour: var(--accent)">
            All <em>{stats['total']:,}</em>
        </span>
        {badges_html}
    </div>

    <div id="results">
        {results_html}
    </div>

    <div class="footer">
        <kbd>/</kbd> to focus &nbsp; <kbd>ESC</kbd> to clear &nbsp; Results open in a new tab
    </div>
</div>

<script>
const input = document.getElementById('searchInput');
const serviceInput = document.getElementById('serviceInput');
let debounceTimer;

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {{
    if (e.key === '/' && document.activeElement !== input) {{
        e.preventDefault();
        input.focus();
    }}
    if (e.key === 'Escape') {{
        clearSearch();
    }}
}});

// Live search with debounce
input.addEventListener('input', () => {{
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(submitSearch, 300);
}});

function filterService(svc) {{
    serviceInput.value = svc;
    // Update active badge
    document.querySelectorAll('.badge').forEach(b => b.classList.remove('active'));
    event.target.closest('.badge').classList.add('active');
    if (input.value.trim()) submitSearch();
}}

function submitSearch() {{
    const q = input.value.trim();
    if (q.length < 2) return;
    const svc = serviceInput.value;
    const url = '/?q=' + encodeURIComponent(q) + (svc ? '&service=' + encodeURIComponent(svc) : '');
    // Use fetch for snappy updates
    document.getElementById('spinner').style.display = 'block';
    fetch(url, {{ headers: {{ 'X-Requested-With': 'fetch' }} }})
        .then(r => r.text())
        .then(html => {{
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            document.getElementById('results').innerHTML =
                doc.getElementById('results').innerHTML;
            document.getElementById('spinner').style.display = 'none';
            // Update URL without reload
            history.replaceState(null, '', url);
        }})
        .catch(() => {{
            document.getElementById('spinner').style.display = 'none';
        }});
}}

function clearSearch() {{
    input.value = '';
    serviceInput.value = '';
    document.getElementById('results').innerHTML = '';
    document.querySelectorAll('.badge').forEach(b => b.classList.remove('active'));
    document.querySelector('.badge').classList.add('active');
    history.replaceState(null, '', '/');
    input.focus();
}}
</script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/search":
            # JSON API endpoint
            params = urllib.parse.parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            service = params.get("service", [""])[0]
            if not query:
                self._json_response({"error": "Missing 'q' parameter"}, 400)
                return
            results = do_search(query, service)
            # Clean up results
            for r in results:
                r.pop("id", None)
                r.pop("title_lower", None)
            self._json_response({"query": query, "count": len(results), "results": results})
            return

        if parsed.path == "/api/stats":
            self._json_response(get_stats())
            return

        if parsed.path == "/":
            params = urllib.parse.parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            service = params.get("service", [""])[0]
            results = do_search(query, service) if query else None
            page_html = build_html_page(query, service, results)
            self._html_response(page_html)
            return

        self._html_response("<h1>404</h1>", 404)

    def _html_response(self, body: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _json_response(self, data: dict, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        # Quieter logging
        pass


def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="UK Free Streaming Catalogue — Web UI")
    parser.add_argument("--port", "-p", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    DB_PATH = Path(args.db)

    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Run scrape_catalogue.py first to build the catalogue.")
        return

    server = HTTPServer((args.host, args.port), Handler)
    print(f"\n  UK Free Streaming Search")
    print(f"  ────────────────────────")
    print(f"  http://{args.host}:{args.port}")
    print(f"  Database: {DB_PATH}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
