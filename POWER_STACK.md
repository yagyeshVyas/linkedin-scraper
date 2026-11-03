# Power Stack (v5.0)

Everything the scraper gained on top of the classic "search → Excel" flow.
Nothing here is required: with the config defaults the old behaviour is intact.
Each feature is a separate module so you can read, test or remove it alone.

```
             ┌──────────────────────────────────────────────────────┐
             │                     config.py                        │
             └───────────────┬──────────────────────┬───────────────┘
                             │                      │
                    ┌────────▼────────┐    ┌────────▼────────┐
                    │ search_filters  │    │    scoring      │
                    │ URL grammar +   │    │ resume match +  │
                    │ facet validation│    │ ICP fit         │
                    └────────┬────────┘    └────────┬────────┘
                             │                      │
   linkedin_scraper.py ──────┴──────────┬───────────┘
   (browser, login, blocks)             │
                    ┌───────────────────▼───────────────────┐
                    │             enrichment.py             │
                    │ names · locations · phones · skills   │
                    │ departments · email candidates · keys │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │              storage.py               │
                    │ SQLite: records · runs · searches     │
                    │ events · cross-run dedupe             │
                    └───────┬───────────────────┬───────────┘
                            │                   │
                 ┌──────────▼──────┐   ┌────────▼─────────┐
                 │   exporters.py  │   │   mcp_server.py  │
                 │ csv json jsonl  │   │ agent tools over │
                 │ md html xlsx    │   │ stdio (JSON-RPC) │
                 └──────────┬──────┘   └──────────────────┘
                            │
                     cli.py / main.py
```

---

## 1. Quick start

```bash
# classic interactive menu (unchanged)
python main.py

# one safe end-to-end check: browser + login + one search, saves nothing
python main.py --dry-run --mode people

# scriptable: faceted people search, then every export format
python cli.py run --mode people --companies Google,Microsoft \
    --titles Recruiter --network S --seniority director \
    --formats xlsx,csv,json,jsonl,md,html

# jobs with real filters
python cli.py run --mode jobs --keywords "data engineer" \
    --location "United States" --formats csv,html

# analyse / re-export what is already stored
python cli.py stats
python cli.py export --format all
python cli.py report --out output/report.html

# health check (deps, Chromium, credentials, session, database, writability)
python cli.py doctor
```

`cli.py` never prompts, exits `0` on success and `1` on failure, and prints
`[command] ...` lines, so it composes with cron/Task Scheduler/CI.

---

## 2. Faceted search (`search_filters.py`)

The original code searched `"<title>" "<company>"` with a bare `geoUrn`. The
new builder emits LinkedIn's own facet grammar:

| Surface | Parameter | Accepted values |
| --- | --- | --- |
| people | `keywords` | quoted phrases, `OR`, `NOT "term"` |
| people | `geoUrn` | JSON list of numeric geo ids (`["103644278"]`) |
| people | `network` | `F`, `S`, `O` (1st/2nd/3rd+) |
| people | `currentCompany` / `pastCompany` | numeric company ids |
| people | `schoolFilter` / `industry` | numeric ids |
| people | `seniority` | `1`-`10` (or `mid_senior`, `director`, ...) |
| people | `page` | 1-based page number |
| jobs | `keywords`, `location`, `geoId` | text / numeric geo id |
| jobs | `f_TPR` | `r3600`, `r86400`, `r604800`, `r2592000` (or `past_24_hours`, ...) |
| jobs | `f_E`, `f_JT`, `f_WT`, `f_EA` | experience, job type, workplace, easy apply |
| jobs | `f_C`, `sortBy`, `start` | company ids, `DD`/`R`, 25-per-page offset |

Config knobs (all empty/off by default):

```python
NETWORK_FILTER = ["S"]                 # 2nd degree only
CURRENT_COMPANY_FILTER = ["1441"]      # numeric ids, not names
PAST_COMPANY_FILTER = []
SCHOOL_FILTER = []
INDUSTRY_FILTER = []
SENIORITY_FILTER = ["director"]
KEYWORD_BOOLEAN_OR = False             # True -> "Recruiter" OR "Sourcer"
EXCLUDE_KEYWORDS = ["intern"]          # -> NOT "intern"
```

**The failure mode this design refuses.** LinkedIn accepts a plain-text
`location=` or `currentCompany=` on people search and silently ignores it,
answering with the unfiltered result set. A search that *reads* as scoped then
returns the whole world. `PeopleFilters.validate()` therefore rejects
non-numeric company/school/industry ids and unknown place names, records a
warning, and the scraper logs it once instead of pretending the filter applied.

Country names are resolved through `GEO_IDS` (`united states` → `103644278`);
for cities pass `geo:<id>`. Company ids can be found with
`build_company_search_url()` / the company page URL (`linkedin.com/company/<slug>`
→ numeric id in the page source).

`build_people_search_url`, `build_jobs_search_url` and `build_company_search_url`
are pure functions: no browser, no network, fully unit-tested.

---

## 3. Enrichment (`enrichment.py`)

Each scraped profile gains these fields (all additive; raw keys are preserved):

| Field | Example | Notes |
| --- | --- | --- |
| `name_first` / `name_middle` / `name_last` / `name_suffix` | `Jane` / `Q.` / `Doe` / `PhD` | handles `Doe, Jane`, particles, credentials |
| `city` / `region` / `region_code` / `country` / `country_code` | `Bentonville` / `Arkansas` / `AR` / `United States` / `US` | US states, country aliases, metro and remote flags |
| `is_remote_location` / `is_metro_location` | `false` / `true` | "Remote", "Greater NYC Area" |
| `company_clean` | `Walmart` | legal suffixes stripped |
| `company_domain` / `domain_source` / `domain_confidence` | `walmart.com` / `known_company` / `0.95` | email > website > known map > slug guess |
| `seniority_rank` / `seniority_label` | `3` / `Senior IC` | 1 intern … 7 C-suite |
| `department` | `People / HR` | word-boundary rules (no "ui" in "acquisition") |
| `phone_e164` / `phone_ext` | `+14795550123` / `12` | implausible numbers become `""` |
| `skills_normalized` / `skill_categories` | `JavaScript, Machine Learning` / `Data / AI, Engineering` | ~150 aliases, deduped |
| `email_status` | `scraped` / `inferred` / `none` | never claims an inferred address is verified |
| `email_confidence` / `email_candidates` | `0.55` / JSON list | pattern + prior, always `"verified": false` |
| `completeness_score` | `0-100` | weighted field coverage |
| `quality_flags` | `["no_email","email_inferred"]` | machine-readable review hints |
| `dedupe_key` | `jane-doe` | public id → URL slug → name@company → hash |

Inferred emails are pattern guesses from the company domain. They are useful
for prioritisation and dangerous if treated as facts, so they are kept in
`email_candidates` and the CSV/HTML report marks them as *inferred*.

---

## 4. Scoring (`scoring.py`)

### Resume match

```bash
python cli.py run --mode people --resume my_resume.pdf --min-score 40
```

* Resume text (`.pdf`, `.docx`, `.txt`, `.md`) → `keyword_weights()`:
  log-scaled frequency, known multi-word skills floored at 2.5.
* `score_record()` searches skills, title, headline, experience, certifications,
  about, education, projects and languages with field multipliers
  (skills 1.8 … languages 0.3), whole-word matching (`java` ≠ `javascript`).
* Result: `match_score` 0-100, `match_tier`
  (`excellent` ≥75, `strong` ≥55, `moderate` ≥35, `weak` >0),
  `matched_keywords`, `missing_keywords`, `match_reasons`.
* `MATCH_MIN_SCORE` filters the pool; `--min-score` overrides per run.

### ICP fit

```python
from scoring import ICP, score_pool
icp = ICP(titles=["Recruiter"], seniorities=["director"],
          locations=["United States"], skills=["sourcing"],
          exclude_companies=["Amazon"], must_have_email=True)
ranked = score_pool(records, icp=icp)
```

Hard rules return `fit_tier` `excluded` / `no_contact` / `incomplete` /
`not_open` with score 0 — a great fit you cannot contact is not a lead.

---

## 5. Storage (`storage.py`)

SQLite at `output/scraper.db` (WAL mode, stdlib only).

```sql
records   (dedupe_key PK, public_id, linkedin_url, name, title, company,
           location, email, seniority, match_score, data JSON,
           first_seen, last_seen, first_run, last_run, seen_count)
runs      (run_id PK, started_at, finished_at, mode, status, stats JSON)
search_keys (key PK, run_id, completed_at)      -- resume at search granularity
events    (id, run_id, ts, kind, detail)
```

* Upserts **merge** (`enrichment.merge_record`): a later scrape that lost the
  About section cannot erase it.
* `seen_count`, `first_seen`, `first_run` track history across runs.
* `record_exists()` normalises any URL variant (`?trk=`, `uk.linkedin.com`,
  `/details/...`) to the same key, so dedupe survives tracking parameters.
* `load_records()` supports company/title/location/country/seniority/query/
  min_score/has_email/open_to_work/since filters used by the CLI, MCP and report.
* `import_progress()` seeds the database from a legacy `progress.json`
  (idempotent), so upgrading mid-project loses nothing.
* `stats()` returns totals, email coverage, top companies, seniority mix,
  countries and run count.

Maintenance:

```bash
python cli.py db import --progress output/progress.json
python cli.py db prune --days 90
python cli.py db backup --to output/backup/scraper.db
python cli.py db query "SELECT company, COUNT(*) n FROM records GROUP BY company ORDER BY n DESC"
```

Jobs are **not** stored in SQLite (the schema is profile-centric); they keep
using `progress.json` + the job exports.

---

## 6. Exports (`exporters.py`)

```python
EXPORT_FORMATS = ["xlsx"]              # default: unchanged behaviour
EXPORT_FORMATS = ["xlsx", "csv", "json", "jsonl", "md", "html"]
```

| Format | Notes |
| --- | --- |
| `xlsx` | delegates to `utils.py` — same formatting, hyperlinks, summary sheet |
| `csv` | curated recruiter-order columns + any extra scraped field; UTF-8 **BOM** so Excel opens non-ASCII names correctly |
| `json` | envelope with `generated_at`, `count`, `meta`, `records` |
| `jsonl` | one record per line, for pipelines/LLM context |
| `md` | snapshot table + top companies + top skills + record table |
| `html` | single-file report: KPI cards, CSS bar charts (companies, seniority, skills, countries), searchable/sortable table, escaped values |

`export_all(records, base_path, formats=...)` writes everything next to a base
path and returns `{format: path}`. The HTML report has no external assets and
opens offline — good for emailing a hiring manager.

---

## 7. MCP server (`mcp_server.py`)

Speak MCP over stdio with no SDK, no new dependency:

```bash
python cli.py mcp --db output/scraper.db
# or
python mcp_server.py --db output/scraper.db
```

Client config (Claude Desktop / any MCP client):

```json
{
  "mcpServers": {
    "linkedin-pool": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server.py", "--db",
               "/absolute/path/to/output/scraper.db"]
    }
  }
}
```

Tools: `list_profiles`, `get_profile`, `get_stats`, `search_profiles`
(resume/keyword ranking), `export_records`, `build_search_url`, `list_runs`.
Protocol: `initialize`, `tools/list`, `tools/call`, `ping`,
`notifications/initialized`; unknown methods return JSON-RPC `-32601`, tool
failures return `isError: true` instead of killing the server.

The server is **read-only over the database** — it never browses LinkedIn, so
an agent cannot click around your account. Scraping stays in `cli.py`.

---

## 8. Human behaviour (`behavior.py`)

Warm-up (after login), curved pointer paths, uneven wheel pulses, nav hover and
idle jitter. All of it is bounded and no-op-safe, and it never clicks a link,
likes a post or navigates to a third-party site. Disable with:

```python
WARM_UP_BROWSER = False
HUMAN_MOUSE = False
```

---

## 9. New config keys

| Key | Default | Meaning |
| --- | --- | --- |
| `NETWORK_FILTER` | `[]` | `F`/`S`/`O` |
| `CURRENT_COMPANY_FILTER`, `PAST_COMPANY_FILTER`, `SCHOOL_FILTER`, `INDUSTRY_FILTER` | `[]` | numeric ids |
| `SENIORITY_FILTER` | `[]` | names or `1`-`10` |
| `KEYWORD_BOOLEAN_OR` | `False` | OR the quoted terms |
| `EXCLUDE_KEYWORDS` | `[]` | `NOT "term"` |
| `OPEN_TO_WORK_ONLY`, `HIRING_ONLY` | `False` | client-side screening |
| `MIN_COMPLETENESS` | `0` | drop profiles below this score |
| `ENABLE_DATABASE` | `True` | SQLite store |
| `DATABASE_FILE` | `output/scraper.db` | store path |
| `IMPORT_PROGRESS_ON_START` | `True` | seed DB from progress.json |
| `RESUME_COMPLETED_SEARCHES` | `True` | `False` re-runs finished searches |
| `MAX_PROFILES_PER_RUN` | `0` | per-run cap (0 = none) |
| `ENRICH_PROFILES`, `INFER_EMAILS` | `True` | enrichment switches |
| `COMPANY_DOMAINS` | `{}` | extra `{name: domain}` overrides |
| `SCORE_WITH_RESUME`, `MATCH_MIN_SCORE` | `True`, `0` | resume scoring |
| `EXPORT_FORMATS` | `["xlsx"]` | add csv/json/jsonl/md/html |
| `WARM_UP_BROWSER`, `HUMAN_MOUSE` | `True` | behaviour |
| `ENABLE_EVENT_LOG`, `EVENT_LOG_DIR` | `True`, `output/runs` | JSONL run log |

---

## 10. Bugs fixed on the way

* `_register_search()` called itself recursively. Every search page raised
  `RecursionError` into the caller's `except`, which counted as an error and
  triggered identity rotation — and the daily search cap never advanced.
  Now one page load = one increment.
* `search_jobs()` referenced `dropped` before assignment whenever the strict
  hours filter was off (`UnboundLocalError` at the end of a page).
* Jobs search used `f_GC` (wrong parameter) for the location; it now uses
  `geoId` + `location`, and pages by `start` instead of string-appending.
* Excel export crashed on a missing `job_url`-style column; `export_all` now
  fails soft per format and never loses a finished run.
* Department classification matched `ui` inside "acquisition" and routed
  recruiters to Design; matching is word-boundary based now.

---

## 11. Research: what informed this

Public GitHub projects were reviewed for feature ideas and known failure modes.
No code was copied; the table records what was adopted and what was rejected.

| Project | Idea adopted | Deliberately not adopted |
| --- | --- | --- |
| [joeyism/linkedin_scraper](https://github.com/joeyism/linkedin_scraper) | Playwright over Selenium, persisted session, typed profile records, "one page load → all sections" | Pydantic models (the codebase is dict-based end to end; adding a model layer would break tests and exports) |
| [stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) | MCP tool surface; its `search_urls.py` documents which facets LinkedIn honours and why plain-text `location`/`currentCompany` must be refused → shaped `search_filters.py` | Automating messages/connections/Easy Apply — out of scope and high account risk |
| [AtuboDad/playwright_stealth](https://github.com/AtuboDad/playwright_stealth) | Patch checklist (navigator, permissions, plugins, WebGL, iframe contentWindow, outer dimensions) | Dropping the dependency in blindly: the init script must stay coherent with `_FINGERPRINTS`, so patches were folded into the existing f-string |
| [SyedBahjat/linkedin-profile-scraper](https://github.com/SyedBahjat/linkedin-profile-scraper) | CSV/JSON export surface, concurrency ambition | Parallel browser tabs — LinkedIn rate-limits by account, not by tab; sequential + backoff is safer |
| [dchrastil/ScrapedIn](https://github.com/dchrastil/ScrapedIn), [l4rm4nd/LinkedInDumper](https://github.com/l4rm4nd/LinkedInDumper) | Company-employee enumeration, dedupe between searches | Authenticated Voyager API endpoints — they break often and look nothing like a human session |
| [spinlud/py-linkedin-jobs-scraper](https://github.com/spinlud/py-linkedin-jobs-scraper) | Job filters and `f_TPR` recency codes | Selenium/requests job scraping — the browser session already exists |
| [linkedtales/scrapedin](https://github.com/linkedtales/scrapedin) | Pagination + filter-driven search loops | Fixed CSS selectors without fallbacks (this repo already tracks section misses) |

Two findings changed the design directly:

1. **Silent filter failure** (from `search_urls.py`): a plain-text `location` or
   `currentCompany` on people search is accepted and ignored, returning the
   unfiltered result set. This build refuses those values and warns.
2. **Wrong parameter names**: job search uses `geoId` (not `f_GC`), `f_EA=true`
   for Easy Apply (not `f_AL`), and `start` offsets (not `page`). The old code
   had all three wrong, which made "remote, past 24h, US" searches lie.

---

## 12. Dumping a whole company

Set the title list to a single empty string — the keyword builder then searches
only the company name, which is how a company-wide employee dump is done:

```python
JOB_TITLES = [""]          # config.py — "everyone at these companies"
```

Or from the CLI, run people mode with `--companies-file companies.txt` and a
config that has `JOB_TITLES = [""]`. Everything else (dedupe, enrichment,
exports, database) applies unchanged.

---

## 13. Limits & ethics

* Scraping LinkedIn may violate its Terms of Service. Use a secondary account,
  keep the delays, and do not run this at scale. The same disclaimer as the
  README applies.
* Facet *names* and *codes* change. The builder validates what it can and warns
  about what it refuses; verify a filter by looking at the URL LinkedIn itself
  produces when you click the filter in the UI.
* Inferred emails are guesses. They are labelled, never verified, and must not
  be used for bulk unsolicited mail.
* MCP exposes stored data only; it does not automate your account.

---

## 14. Tests

```bash
.venv/Scripts/python.exe test_scraper_fixes.py    # 33 classic tests
.venv/Scripts/python.exe test_login_flow.py       # 5 login decision-tree tests
.venv/Scripts/python.exe test_power_features.py   # 58 power-stack tests
```

All three run without a browser or network. The power suite covers URL
encoding/facet validation, enrichment edge cases, scoring bounds and
explanations, SQLite dedupe/merging/filters, every export format (including
HTML escaping), the MCP JSON-RPC dispatcher, and scraper integration
(`_register_search`, faceted search URL, post-scrape enrichment, run summary).
