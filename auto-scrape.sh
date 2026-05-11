#!/bin/bash
# Weekly auto-scrape: runs the catalogue scraper then pushes updated DB to GitHub.
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

python3 scrape_catalogue.py

# Only commit and push if the database actually changed
if git diff --quiet catalogue.db 2>/dev/null; then
    echo "No changes to catalogue.db — skipping push."
else
    git add catalogue.db
    git commit -m "Update catalogue — $(date '+%Y-%m-%d')"
    git push origin main
    echo "Pushed updated catalogue.db to GitHub."
fi

echo "=== Auto-scrape finished at $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
