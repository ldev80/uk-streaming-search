#!/usr/bin/env python3
"""
UK Free Streaming Catalogue — CLI Search
==========================================
Searches the local SQLite catalogue for exact title matches.

Usage:
    python search.py "doctor who"
    python search.py "taskmaster" --service itvx
    python search.py "bake off" --fuzzy
    python search.py --stats

The query must appear somewhere within the programme title (case-insensitive).
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "catalogue.db"

# ANSI colour codes
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"
C_DIM    = "\033[2m"
C_CYAN   = "\033[36m"
C_GREEN  = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE   = "\033[34m"
C_MAG    = "\033[35m"

SERVICE_COLOURS = {
    "bbc_iplayer":  "\033[91m",   # red
    "itvx":         "\033[93m",   # yellow
    "channel4":     "\033[94m",   # blue
    "channel5":     "\033[95m",   # magenta
    "pbs_america":  "\033[96m",   # cyan
    "pluto_tv_uk":  "\033[92m",   # green
    "tubi":         "\033[33m",   # orange-ish
    "tptv_encore":  "\033[37m",   # white
}

SERVICE_LABELS = {
    "bbc_iplayer":  "BBC iPlayer",
    "itvx":         "ITVX",
    "channel4":     "Channel 4",
    "channel5":     "Channel 5",
    "pbs_america":  "PBS America",
    "pluto_tv_uk":  "Pluto TV UK",
    "tubi":         "Tubi",
    "tptv_encore":  "TPTV Encore",
}


def get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        print("Run scrape_catalogue.py first to build the catalogue.")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def search(query: str, service: str = None, fuzzy: bool = False,
           db_path: Path = DB_PATH, limit: int = 100) -> list[dict]:
    """
    Search the catalogue. By default, the query must appear in the title
    (exact substring match, case-insensitive).
    If fuzzy=True, also search descriptions.
    """
    conn = get_db(db_path)
    q_lower = query.lower().strip()

    if fuzzy:
        sql = """
            SELECT * FROM programmes
            WHERE title_lower LIKE ?
               OR LOWER(description) LIKE ?
        """
        params = [f"%{q_lower}%", f"%{q_lower}%"]
    else:
        sql = "SELECT * FROM programmes WHERE title_lower LIKE ?"
        params = [f"%{q_lower}%"]

    if service:
        # Map short keys to service names
        svc_map = {
            "bbc": "bbc_iplayer", "iplayer": "bbc_iplayer",
            "itvx": "itvx", "itv": "itvx",
            "channel4": "channel4", "c4": "channel4",
            "channel5": "channel5", "c5": "channel5",
            "pbs": "pbs_america",
            "pluto": "pluto_tv_uk",
            "tubi": "tubi",
            "tptv": "tptv_encore",
        }
        svc_name = svc_map.get(service.lower(), service.lower())
        sql += " AND service = ?"
        params.append(svc_name)

    sql += " ORDER BY service, title_lower LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def print_results(results: list[dict], query: str):
    """Pretty-print search results to the terminal."""
    if not results:
        print(f"\n  No results found for '{query}'.\n")
        return

    print(f"\n  {C_BOLD}{len(results)} result(s) for '{query}'{C_RESET}\n")

    current_service = None
    for r in results:
        svc = r["service"]
        if svc != current_service:
            current_service = svc
            colour = SERVICE_COLOURS.get(svc, "")
            label = SERVICE_LABELS.get(svc, svc)
            print(f"  {colour}{C_BOLD}━━ {label} ━━{C_RESET}")

        title = r["title"]
        # Highlight the matching part of the title
        q_lower = query.lower()
        idx = title.lower().find(q_lower)
        if idx >= 0:
            before = title[:idx]
            match = title[idx:idx+len(query)]
            after = title[idx+len(query):]
            title_display = f"{before}{C_BOLD}{C_GREEN}{match}{C_RESET}{after}"
        else:
            title_display = title

        print(f"    {title_display}")
        if r.get("description"):
            desc = r["description"][:120]
            if len(r["description"]) > 120:
                desc += "..."
            print(f"    {C_DIM}{desc}{C_RESET}")
        if r.get("url"):
            print(f"    {C_CYAN}{r['url']}{C_RESET}")
        print()


def print_stats(db_path: Path = DB_PATH):
    """Print catalogue statistics."""
    conn = get_db(db_path)

    print(f"\n  {C_BOLD}Catalogue Statistics{C_RESET}\n")

    rows = conn.execute("""
        SELECT service, COUNT(*) as cnt
        FROM programmes
        GROUP BY service
        ORDER BY cnt DESC
    """).fetchall()

    total = 0
    print(f"  {'Service':<20} {'Programmes':>12}")
    print(f"  {'─'*34}")
    for r in rows:
        svc = r["service"]
        cnt = r["cnt"]
        total += cnt
        colour = SERVICE_COLOURS.get(svc, "")
        label = SERVICE_LABELS.get(svc, svc)
        print(f"  {colour}{label:<20}{C_RESET} {cnt:>12,}")
    print(f"  {'─'*34}")
    print(f"  {C_BOLD}{'TOTAL':<20} {total:>12,}{C_RESET}")

    # Last scrape times
    print(f"\n  {C_BOLD}Last Scrape Times{C_RESET}\n")
    log_rows = conn.execute("""
        SELECT service, status, count, finished_at, error_msg
        FROM scrape_log
        WHERE id IN (SELECT MAX(id) FROM scrape_log GROUP BY service)
        ORDER BY service
    """).fetchall()
    for r in log_rows:
        status_icon = "✓" if r["status"] == "success" else "✗"
        status_colour = C_GREEN if r["status"] == "success" else "\033[91m"
        print(f"  {status_colour}{status_icon}{C_RESET} {r['service']:<12} "
              f"{r['count'] or 0:>6} items  "
              f"{C_DIM}{r['finished_at'] or 'N/A'}{C_RESET}")
        if r["error_msg"]:
            print(f"    {C_DIM}Error: {r['error_msg'][:80]}{C_RESET}")

    print()
    conn.close()


def export_json(query: str, service: str = None, db_path: Path = DB_PATH):
    """Export search results as JSON."""
    results = search(query, service=service, db_path=db_path)
    # Remove internal fields
    for r in results:
        r.pop("id", None)
        r.pop("title_lower", None)
    print(json.dumps(results, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="Search the UK Free Streaming Catalogue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python search.py "doctor who"
  python search.py "bake off" --service channel4
  python search.py "nature" --fuzzy
  python search.py "thriller" --json
  python search.py --stats
        """,
    )
    parser.add_argument("query", nargs="?", help="Search query (must appear in title)")
    parser.add_argument("--service", "-s", help="Filter by service (bbc, itvx, channel4, channel5, pbs, pluto, tubi, tptv)")
    parser.add_argument("--fuzzy", "-f", action="store_true", help="Also search descriptions")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--stats", action="store_true", help="Show catalogue statistics")
    parser.add_argument("--limit", "-n", type=int, default=100, help="Max results (default: 100)")
    parser.add_argument("--db", default=str(DB_PATH), help=f"Database path (default: {DB_PATH})")
    args = parser.parse_args()

    db_path = Path(args.db)

    if args.stats:
        print_stats(db_path)
        return

    if not args.query:
        parser.print_help()
        return

    if args.json:
        export_json(args.query, service=args.service, db_path=db_path)
    else:
        results = search(args.query, service=args.service, fuzzy=args.fuzzy,
                         db_path=db_path, limit=args.limit)
        print_results(results, args.query)


if __name__ == "__main__":
    main()
