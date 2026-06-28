#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Pulling latest scrape data ==="
git pull

echo ""
echo "=== Deploying to Firebase ==="
firebase deploy --only hosting --project streamfind-2abe7

echo ""
echo "=== New titles this week ==="
if [ -f new_titles.json ]; then
    python3 -c "
import json
titles = json.load(open('new_titles.json'))
if not titles:
    print('No new titles this week.')
else:
    by_svc = {}
    for t in titles:
        by_svc.setdefault(t.get('service', 'unknown'), []).append(t)
    n = 0
    for svc, items in sorted(by_svc.items()):
        print(f'\n{svc} ({len(items)} new):')
        for item in sorted(items, key=lambda x: x['title'].lower()):
            n += 1
            print(f'  {n:>4}. {item[\"title\"]}')
    print(f'\n{n} new titles total.')
"
else
    echo "No new_titles.json found."
fi
