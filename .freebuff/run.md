# Preview run doc — LinkedIn Scraper dashboard

This project is a Python CLI tool (no web app, no package.json, no dev server).
The Preview tab shows a **static, self-contained HTML dashboard** generated from
the scraper's own output files. It is served directly by the Freebuff client
(register_preview `htmlPath` mode) — no process, port, or dependencies involved.

## Reproduce the artifact

The artifact is `dashboard.html` at the repo root. Regenerate it any time the
scraper output changes (new `output/progress.json` records, log growth, or code
syntax status):

```
python generate_dashboard.py
```

The generator is stdlib-only (no installs, no network). It fills in
`dashboard_template.html` — the UI source of truth (styling, layout, static
content like the Known Issues list) — with only the dynamic data:

- `output/progress.json` — scraped profile records (stats + searchable table)
- `output/scraper.log` — last 40 lines (colorized activity feed)
- `*.py` modules — in-process `ast.parse` syntax check (Project Health table;
  never writes `.pyc` files)
- `config.py` — declared job titles / location / GEO_URN (credentials are
  deliberately never read)

Edit the template for any layout or styling change, then re-run the generator.
All three files (`dashboard_template.html`, `generate_dashboard.py`,
`dashboard.html`) live at the repo root; template and generator are the source
of truth.

## Run the server

There is no server. Open the Preview tab for this thread — the client serves
`dashboard.html` locally and safely from `register_preview(htmlPath=...)`. To
view it outside the app, just open the file in any browser.
