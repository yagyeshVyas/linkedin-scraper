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

To actually run the scraper itself (not the dashboard):

```
.venv/Scripts/python.exe -m pip install -r Requirements.txt
.venv/Scripts/python.exe -m playwright install chromium
.venv/Scripts/python.exe main.py
```

Safe smoke test (launches browser, logs in, runs ONE search, saves nothing):

```
.venv/Scripts/python.exe main.py --dry-run
.venv/Scripts/python.exe main.py --dry-run --mode jobs
```

(The venv existed but had been left empty — deps were never installed. Behavioral
tests for the scraper fixes: `.venv/Scripts/python.exe test_scraper_fixes.py`.)

The generator is stdlib-only (no installs, no network). It fills in
`dashboard_template.html` — the UI source of truth (styling, layout, static
content like the Known Issues list) — with only the dynamic data:

- `output/progress.json` — scraped profile records (stats + searchable table)
- `output/scraper.log` — last 40 lines (colorized activity feed)
- `output/session_health.json` — latest run's telemetry (Session Health panel:
  login reason, proxy, error streaks, daily searches; written by the scraper
  via `save_session_health`, generated only after a real run)
- `*.py` modules — in-process `ast.parse` syntax check (Project Health table;
  never writes `.pyc` files)

Edit the template for any layout or styling change, then re-run the generator.
All three files (`dashboard_template.html`, `generate_dashboard.py`,
`dashboard.html`) live at the repo root; template and generator are the source
of truth.

## Run the server

There is no server. Open the Preview tab for this thread — the client serves
`dashboard.html` locally and safely from `register_preview(htmlPath=...)`. To
view it outside the app, just open the file in any browser.
