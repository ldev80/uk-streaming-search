# New This Week — Highlights

After each weekly scrape, you can curate a "New This Week" section on the site showing selected new titles.

## Workflow

1. **Check your email.** After the Sunday 3am scrape, you'll receive an email at info@liamdevereux.co.uk from tvsearchuk@gmail.com listing all new titles with numbers.

2. **Pick your highlights.** Go to:
   https://github.com/ldev80/uk-streaming-search/actions/workflows/pick-highlights.yml

   Click **"Run workflow"** and enter the numbers from the email (comma-separated, ranges supported):
   ```
   3, 7, 12-15
   ```

   The workflow generates `new_this_week.json`, commits it, and deploys to Firebase automatically.

## Customising thumbnails

Each entry in `new_this_week.json` uses the `image_url` field for its thumbnail. To change one, edit the file directly and push:

```json
{
  "title": "Peaky Blinders",
  "service": "bbc_iplayer",
  "url": "https://www.bbc.co.uk/iplayer/episodes/b045fz8r/peaky-blinders",
  "image_url": "https://example.com/better-image.jpg"
}
```

Landscape 16:9 images work best (displayed at 160x90px).

## Clearing highlights

To remove the section from the site, delete `public/new_this_week.json` and redeploy. The section hides automatically if the file is missing or empty.

## How it works

- GitHub Actions runs `scrape_catalogue.py` weekly, which compares the new catalogue against the previous one
- New titles are saved to `new_titles.json` and deployed to the site
- The curation email is sent automatically after the scrape
- The Pick Highlights workflow reads `new_titles.json`, picks your selections, writes `new_this_week.json`, and deploys
- `index.html` fetches `new_this_week.json` on load and renders a horizontal card strip
- The strip hides when the user searches and reappears when they clear
