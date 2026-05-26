#!/usr/bin/env python3
"""Show new titles added since the last scrape and pick highlights."""

import json
from pathlib import Path

PROJECT = Path(__file__).parent
NEW_TITLES = PROJECT / "new_titles.json"
OUTPUT = PROJECT / "new_this_week.json"


def main():
    if not NEW_TITLES.exists():
        print("No new_titles.json found — run a scrape first.")
        return

    titles = json.loads(NEW_TITLES.read_text())
    if not titles:
        print("No new titles found since last scrape.")
        return

    by_service = {}
    for item in titles:
        by_service.setdefault(item.get("service", "unknown"), []).append(item)

    numbered = []
    print(f"\n{len(titles)} new titles since last scrape:\n")
    for service, items in sorted(by_service.items()):
        print(f"  {service} ({len(items)} new)")
        for item in sorted(items, key=lambda x: x["title"].lower()):
            idx = len(numbered) + 1
            numbered.append(item)
            print(f"    {idx:>4}. {item['title']}")
        print()

    print("Enter numbers to highlight (e.g. 3, 7, 12) or 'q' to quit:")
    choice = input("> ").strip()
    if choice.lower() == "q":
        return

    selected = []
    for part in choice.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            for i in range(int(start), int(end) + 1):
                if 1 <= i <= len(numbered):
                    selected.append(numbered[i - 1])
        elif part.isdigit():
            i = int(part)
            if 1 <= i <= len(numbered):
                selected.append(numbered[i - 1])

    if not selected:
        print("No valid selections.")
        return

    OUTPUT.write_text(json.dumps(selected, ensure_ascii=False, indent=2))
    print(f"\nSaved {len(selected)} highlights to {OUTPUT}")
    print("Run 'cp new_this_week.json public/ && firebase deploy --only hosting --project streamfind-2abe7' to go live.")


if __name__ == "__main__":
    main()
