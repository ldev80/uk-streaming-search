# New This Week — Highlights

After each weekly scrape, you can curate a "New This Week" section on the site showing selected new titles.

## Workflow

1. **After the Sunday scrape**, the scraper saves all newly added titles to `new_titles.json`.

2. **View the new titles:**
   ```bash
   cd ~/Projects/streamfind
   source venv/bin/activate
   python pick_highlights.py
   ```

3. **Pick your highlights** by entering the numbers (comma-separated, ranges supported):
   ```
   > 3, 7, 12-15
   ```
   This saves your picks to `new_this_week.json`.

4. **Deploy:**
   ```bash
   cp new_this_week.json public/new_this_week.json
   firebase deploy --only hosting --project streamfind-2abe7
   ```

## Customising thumbnails

Each entry in `new_this_week.json` uses the `image_url` field for its thumbnail. To change one, edit the file directly:

```json
{
  "title": "Peaky Blinders",
  "service": "bbc_iplayer",
  "url": "https://www.bbc.co.uk/iplayer/episodes/b045fz8r/peaky-blinders",
  "image_url": "https://example.com/better-image.jpg"
}
```

Options for image sources:
- **Service-provided** (default) — whatever the streaming service API returned. Quality varies.
- **Any image URL** — paste a URL from the web. Landscape 16:9 images work best (displayed at 160x90px).
- **Local images** — save to `public/images/highlights/` and use a relative path like `images/highlights/peaky-blinders.jpg`.

## Clearing highlights

To remove the section from the site, delete `public/new_this_week.json` and redeploy. The section hides automatically if the file is missing or empty.

## How it works

- `scrape_catalogue.py` compares the new catalogue against the previous `catalogue.json` before overwriting it
- New titles (present in new but not old) are saved to `new_titles.json`
- `pick_highlights.py` displays the list and writes your selections to `new_this_week.json`
- `index.html` fetches `new_this_week.json` on load and renders a horizontal card strip
- The strip hides when the user searches and reappears when they clear
