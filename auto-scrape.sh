#!/bin/bash
# Weekly auto-scrape: runs the catalogue scraper, updates the static site,
# pushes to GitHub, and deploys to Firebase.
# Intended to be triggered by launchd on macOS.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$PROJECT_DIR/scrape.log"
VENV="$PROJECT_DIR/venv"

exec >> "$LOG_FILE" 2>&1
echo ""
echo "=== Auto-scrape started at $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

cd "$PROJECT_DIR"
source "$VENV/bin/activate"

# Scrape all services (also exports catalogue.json)
python3 scrape_catalogue.py

# Copy updated files to the public directory for Firebase
cp catalogue.json public/catalogue.json
cp index.html public/index.html

# Only commit and push if data actually changed
if git diff --quiet catalogue.db catalogue.json 2>/dev/null; then
    echo "No changes to catalogue — skipping push and deploy."
else
    git add catalogue.db catalogue.json public/catalogue.json
    git commit -m "Update catalogue — $(date '+%Y-%m-%d')"
    git push origin main
    echo "Pushed to GitHub."

    # Deploy to Firebase
    firebase deploy --only hosting --project streamfind-2abe7
    echo "Deployed to Firebase."
fi

echo "=== Auto-scrape finished at $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
