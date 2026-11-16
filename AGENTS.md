# AGENTS.md

Non-obvious knowledge about this repo that isn't recoverable from the code or docs.

## Project state

- As of the 2026-08 scraper power-up, the main path runs: `config.py`, `linkedin_scraper.py`
  and `utils.py` were fixed (duplicate `USER_AGENTS` block, merged `logger.info` line, and
  stripped imports restored). `python main.py` requires the project venv
  (`.venv/Scripts/python.exe`) with `Requirements.txt` installed — the venv had been left
  empty. `playwright install chromium` is still needed before a real run.
- `linkedin_scraper.py` referenced `self.current_account` (login) and `config.DISCORD_WEBHOOK_URL`
  which didn't exist; both are now defined. `scrape_profile` calls `_extract_about/skills/
  experience/education` — implemented in the power-up; keep them if you touch the scraper.
- Login (2026-08): `login()` now restores the persisted session from `session/` before typing
  (it previously saved but never loaded it), retries `MAX_RETRIES` times with backoff but gives
  up immediately on bad credentials, and polls checkpoint/2FA pages until they resolve
  (`LOGIN_CHALLENGE_TIMEOUT_SECONDS`, default 90) instead of blind 60s sleeps. Success is
  confirmed by URL + presence of `nav.global-nav`. `test_login_flow.py` drives the real
  `login()` decision tree against fakes — run both test files with the venv python.
- Login DOM (2026-11): LinkedIn now renders generated ids (`_R_e3p...`), no `#username` /
  `#password`, and two responsive forms; the visible pair is `input[type=email]` +
  `input[type=password]`, and the submit control is a plain `button` whose text is exactly
  "Sign in" (Microsoft/Apple buttons also contain "Sign in"). `_fresh_login` resolves through
  `_wait_first()` and the `_LOGIN_*_SELECTORS` tuples (exact XPath first), never a single id.
  Never reintroduce `wait_for_selector("#username")` — it silently hangs 20s per attempt.
  Login relaunches the browser when the window was closed mid-retry; `run --challenge-timeout`
  controls the 2FA/checkpoint wait (0 = config). A fresh browser always meets a checkpoint on
  first login; complete it in the headed window and the run continues and persists the session.
- The `*_diff.py` files (`linkedin_scraper_enhanced_diff.py`, `linkedin_scraper_pro_diff.py`,
  `config_enhanced_diff.py`) and `_diff.gitignore` are saved unified-diff patches for a
  never-merged "v4.0 Enhanced" line — not runnable modules; they reference modules
  (`linkedin_scraper_enhanced.py`, `config_enhanced.py`) that don't exist in the repo.
- Scraper accuracy features added: `self.scraped_urls` dedup (seeded from `progress.json`
  results) so the same profile isn't re-scraped under different titles/companies; search URLs
  built with `urlencode` (manual `%20`/`"` replacing broke company names containing `&` like
  "AT&T"); `_extract_email` prefers `mailto:` links and filters image/site-internal junk.
- Anti-block / resilience (2026-08): `_next_proxy()` round-robins `PROXY_LIST` and never
  repeats the current proxy (identity rotation actually changes IPs); `_handle_potential_block`
  uses exponential backoff `_backoff_delay(errors)` (~60s·2^(n-3), cap 600s, jittered); failed
  profile URLs are retried once before export (`RETRY_FAILED_PROFILES`, `failed_urls` now stores
  `{url, company, title}` dicts, not bare strings); `USE_TEST_COMPANIES` makes main.py run a
  2-company smoke test instead of the full 25-company Fortune 500 list.
- `USE_FREE_PROXIES=True` (PROXY_LIST empty) makes the main scraper auto-fetch/test free proxies
  via `proxy_manager.ProxyManager` — refresh runs in a thread (`asyncio.to_thread`); results cache
  to `output/proxies.json`. Note: `ProxyManager.refresh()` was fixed so a fresh instance honors a
  fresh cache (`last_refresh` was 0 → it used to refetch from the network every launch). Dead
  proxies are shed via `mark_failed` on consecutive errors.
- Anti-detection (2026-09): `_FINGERPRINTS` (top of `linkedin_scraper.py`) holds 4 *coherent*
  identity profiles. `_launch_browser` picks one (`RANDOMIZE_FINGERPRINT`, else `[0]`) and feeds
  UA, ``navigator.platform``, viewport, locale, timezone, geolocation **and** the stealth script
  from that same profile, so nothing contradicts anything else. `config.USER_AGENTS` is now
  Chromium-desktop-only on purpose (Firefox/Safari UAs in a Chromium build are a signal).
- Block handling (2026-09): `_check_page_block_status()` classifies auth-wall / captcha /
  checkpoint / rate-limit pages and records the reason in `self._block_reason`;
  `_recover_from_block(context)` cools down, calls `_rotate_identity()` (new proxy + fingerprint
  + re-login) and returns True so the calling search loop `continue`s. Bounded by
  `config.MAX_BLOCK_RECOVERIES`. `_rotate_identity()` now returns bool (False on relaunch/login
  failure) and is teardown-safe: context/browser closes are individually guarded, the Playwright
  driver is always stopped + restarted, and `_handle_potential_block` returns False if rotation
  fails so the search stops instead of spraying errors at a dead session.
- `utils.save_progress` writes atomically (temp + `os.replace`) and never raises;
  `utils.load_progress` tolerates missing/corrupt/non-dict files and normalises the legacy
  `completed_companies` key into `completed_keys` (the key `run()` actually reads).
- Deep extraction (2026-09): `scrape_profile` now also records structured work history and
  education plus certifications / languages / honors / followers / open-to-work / premium /
  photo, all parsed from the ONE page load (no extra requests). Invariant to preserve:
  `_extract_experience`/`_extract_education` are thin wrappers over `_format_experience(
  _extract_roles(soup))` / `_format_education(_extract_education_entries(soup))`, so the flat
  string and the structured `experience_roles`/`education_entries` JSON can never drift. Edit
  the structured extractor, not the flatteners. `_parse_count` turns "500+"/"1,234"/"10K" into
  ints. Extend `DEEP_FIXTURE` in the tests, not `FIXTURE`, to cover new fields.
- Second wave of profile depth (2026-09): added volunteer / projects / publications / courses /
  patents / services / interests / recommendations / websites, badge flags (`is_hiring`,
  `is_creator`, `verified`), `pronouns`, `public_id`, `banner_url`, plus derived screening
  metrics (`seniority`, `total_experience_years`, `career_start_year`, `companies_count`,
  `avg_tenure_months`, `current_tenure_months`, `current_role_duration`, `skills_count`).
  Almost all new sections reuse `_section_list(soup, ids, patterns, item_selectors)` — add a
  new section by calling it with the right id rather than hand-rolling a selector chain.
  `_career_stats` is frozen-time testable because `_now_month_index` is a classmethod the
  tests monkeypatch; keep it that way. Badge detection MUST stay attribute-only
  (`_extract_top_card_flag` scans alt/aria/class/src, never visible text) — scanning text makes
  every profile look like it's hiring, because 'hiring' appears in ordinary About copy.
- `scrape_profile` tallies empty fields into `self.section_misses` and `_log_section_coverage`
  prints them at the end of a run (naming fields empty for EVERY profile). That is the
  intended early-warning signal for LinkedIn selector changes — do not remove it as noise.
- The dashboard template renders and CSV-exports `r.url`, but records store `linkedin_url`;
  `generate_dashboard.load_records()` mirrors it. Profile links silently broke before that
  normalisation — keep it if you touch either side.
- The dashboard table's sort coerces with `String(a[key] ?? "")`; without that, sorting any
  boolean/number column (open_to_work, follower_count, connection_count) throws.
- Behavioral tests: `test_scraper_fixes.py` (33 tests), `test_login_flow.py` (5 tests),
  `test_power_features.py` (58 tests), `test_cf_agent.py` (46 tests), `test_brutal.py`
  (53 tests) and `test_webapp.py` (18 tests) — run each with `.venv/Scripts/python.exe <file>`
  (no browser or network needed; the login suite drives the real `login()` against fakes, the
  dashboard suite binds an ephemeral loopback port). No pytest in the venv — each file is a
  plain script with a `__main__` runner list; add new tests to that list.

## Power stack (v5.0, 2026-11)

- New modules, all stdlib-only and import-safe (no browser/network at import):
  `search_filters.py` (URL grammar + facet validation), `enrichment.py` (derived fields),
  `scoring.py` (resume + ICP), `storage.py` (SQLite + EventLog), `exporters.py`
  (csv/json/jsonl/md/html + `export_all`), `behavior.py` (human pointer/scroll/warm-up),
  `mcp_server.py` (JSON-RPC over stdio), `cli.py` (run/export/stats/report/mcp/doctor/db).
  `main.py` keeps the old menu and `build_parser()`; `test_cli_parser` depends on it.
- `linkedin_scraper.run()` now returns a summary dict and owns a "power stack" block of
  helpers (`_start_power_stack`, `_scrape_profile_urls`, `_complete_search`, `_export_results`,
  `_finish_power_stack`). All of them are fail-soft: a missing database, export or event-log
  error must never lose a scraped profile. `run()` returns from `finally`-adjacent code, not
  inside `finally`, so KeyboardInterrupt is not swallowed.
- Critical fixed bug: `_register_search()` used to call itself recursively, so every search
  page raised `RecursionError` into the caller's except-block (counted as an error and
  triggered a pointless identity rotation) and `daily_searches` never advanced. One search
  page now increments exactly once — do not reintroduce a call to any "count" helper inside it.
- `_scrape_profile_urls()` is the single profile loop for people and candidates mode. It
  dedupes through `_already_seen()` (in-memory + SQLite `record_exists`), persists each
  profile with `_remember_profile()`, and enforces `MAX_PROFILES_PER_RUN` via `_cap_reached()`.
  Add per-profile behaviour there, not in the three mode loops.
- Faceted search invariant: LinkedIn silently ignores plain-text `location`/`currentCompany`
  on people search. `PeopleFilters.validate()` therefore rejects non-numeric company/school/
  industry ids and unknown place names and records warnings; `_log_search_warnings` prints
  them once. Do not "helpfully" emit a name facet — that is the bug where a filtered search
  returns the whole world. Verified job params are `geoId`/`f_TPR`/`f_E`/`f_JT`/`f_WT`/`f_EA`
  (easy apply is `f_EA`, not `f_AL`) and paging is `start`, not `page`.
- Enrichment is additive and must stay that way: `enrich_record()` returns a copy, never
  overwrites a scraped value with a worse one, and labels guessed emails `email_status:
  "inferred"` + `verified: false`. `email` stays empty unless it was scraped. Department
  matching uses `_has_keyword()` with `*` suffix meaning word-prefix — plain substring matching
  routed "Talent Acquisition" to Design because "ui" is inside "acquisition".
- SQLite (`output/scraper.db`) upserts merge via `enrichment.merge_record`: blank incoming
  fields never erase stored ones, `seen_count`/`first_seen`/`first_run` are metadata and are
  skipped by the merge, and `_decode()` always overrides those from the row. `record_exists()`
  normalises any URL variant to the public id, so `?trk=` and `uk.linkedin.com` are the same
  person. Jobs are deliberately NOT stored in SQLite (profile-centric schema).
- `test_power_features.py` (58 tests) is the third suite. It covers URL facets/encoding,
  enrichment edge cases (mojibake, metro/remote locations, E.164, name particles), scoring
  bounds and word boundaries (`java` ≠ `javascript`), SQLite merge/filters/prune, every
  exporter (including HTML escaping of a `<script>` name), MCP dispatcher failures, and the
  scraper integration (`_register_search`, faceted search URL, post-scrape enrichment,
  `_finish_power_stack`). Run all three suites before claiming a change works.
- MCP is read-only over the database by design. It must never launch a browser or touch
  LinkedIn directly; scraping stays in `cli.py run`. stdout is protocol-only — log to stderr.


## Stealth + agent browser (v6.0, 2026-11)

- Two new modules, both import-safe (no browser/network at import): `cf_bypass.py`
  (wall classifier, clearance cache, tiered fetch, engine launch) and `agent_browser.py`
  (a11y-ref browser, MCP server, function-calling specs). `cli.py` grew `browser`,
  `mcp-browser` and `fetch`; `doctor` reports engines / curl_cffi / agent-browser.
- Engine ladder: `cf_bypass.launch_stealth_browser(engine="auto")` tries patchright →
  camoufox → playwright and returns a `BrowserHandle` that owns the browser AND the driver
  that must stop. `linkedin_scraper._launch_browser` uses it; `_shutdown_browser()` is the
  ONLY teardown (context, browser, handle, driver, each guarded, safe twice). Do not
  reintroduce a raw `async_playwright()` in the scraper — it orphans the handle and skips
  camoufox.
- `_check_page_block_status` runs `classify_page` BEFORE the rate-limit/authwall phrase
  checks: a CF interstitial contains none of LinkedIn's phrases. The verdict reaches
  `_block_reason` through `block_reason_for()`; both `cloudflare` and `turnstile` map to
  `"cloudflare"` so `_recover_from_block` rotates normally. `CF_AUTO_SOLVE` waits the wall
  out first and returns "not blocked" when it clears; a headed run then falls back to
  `_await_wall_resolution()` so a human click finishes an interactive Turnstile
  (`CF_MANUAL_FALLBACK` / `CF_MANUAL_TIMEOUT`). `solve_challenge` also accepts a solved
  `cf-turnstile-response` token as success, so a widget that solved without navigating still
  counts.
- Classifier invariant: Turnstile is a WALL only when the surrounding document is an
  interstitial (CF challenge markers, "verify you are human", or body under ~6 KB). A widget
  on a content page (login form, 2captcha demo) is an in-page captcha — treating it as a wall
  made the agent browser burn its whole solver timeout on ordinary pages.
  `challenges.cloudflare.com` alone proves the widget exists, not that the page is walled.
- Clearance cache is keyed `domain | proxy | UA` on purpose: `cf_clearance` is bound to the
  solving IP, so replaying a cookie across a rotated proxy is a detection signal, not a
  shortcut. TTL + atomic write; cache failures are debug-level, never fatal.
- TLS target selection must pick a profile that EXISTS in the installed curl_cffi
  (`_curl_targets()`); its enum is `chrome131` / `safari180` / `firefox144`-style, not
  `safari17_0`. An invalid target silently degrades to a generic handshake.
- Agent browser contract: refs (`eN`) are re-assigned on every snapshot and re-verified on
  every action; a stale ref raises `ToolError` ("take a new snapshot") instead of clicking a
  moved element. `BrowserSession._resolve()` is the single ref/selector resolution point.
- `AgentBrowserServer` bridges the synchronous MCP stdio loop to the async session through
  `AsyncRunner` (private loop thread); `serve()` closes the session on EOF. stdout is
  protocol-only — log to stderr.
- `test_cf_agent.py` (46 tests) is the fourth suite: classifier cases, cache scoping/TTL,
  TLS targets, FlareSolverr, fetch-ladder order, snapshot format/staleness, MCP dispatch,
  scraper block classification. Run every suite before claiming a change works.
- Windows console: `agent_browser.main` and `cli.main` reconfigure stdout/stderr to UTF-8
  with `errors="replace"` — page text is arbitrary Unicode and cp1252 would crash a snapshot
  print. Keep that at CLI entry points only; library code must not touch global streams.


## Armor (v7.0, 2026-11)

- New modules, stdlib-only and import-safe: `fingerprint.py` (coherent profiles,
  `validate_profile`, `init_script`), `stealth_audit.py` (12-check live probe, 0-100 score),
  `proxy_intel.py` (health/cooldown/country scoreboard). `behavior.py` gained `Humanizer`.
  `cli.py` gained `stealth-check` and `proxy-scan`; `doctor` reports armor checks.
- **Measured engine behaviour, do not "fix" by assumption:** patchright silently ignores
  `context.add_init_script` and `page.add_init_script` (both bundled Chromium and
  `channel="chrome"`); stock Playwright honours them; `Page.addScriptToEvaluateOnNewDocument`
  via CDP did not inject in either engine in this environment. `BrowserHandle.scriptable`
  records the capability. Anything that depends on injected JS must check it.
- Consequence: with a non-scriptable engine the fingerprint is constrained to
  `fingerprint.host_os()` (geography preserved, OS truthful), a 1366x768 safe viewport, and
  the audit drops un-fakeable expectations. `agent_browser.BrowserSession` passes
  `prefer_scriptable=self.armor`. `linkedin_scraper` does the same by default through
  `PREFER_SCRIPTABLE_ENGINE=True` (so the production path audits **100 A**); set it False to
  put patchright first for Cloudflare-heavy targets.
- Coherence fixes measured in production: the scraper script had **no**
  `navigator.platform` patch and **no singular** `navigator.userAgentData.platform` (only
  `platforms`), which made every generated non-Windows identity fail audit. Both are now
  patched; `test_brutal` guards the source. The UA major is pinned to the real
  `browser.version` after launch, `detect_country()` picks local country when no proxy tag
  exists, and `_audit_stealth()` runs once after login and writes `stealth_score`/
  `stealth_grade` into telemetry.
- Audit ground truth on this machine: Playwright + armor (scraper and agent) = 100 A;
  patchright headed = 86 B (strips `navigator.userAgentData`, iframe `webdriver=false`);
  patchright headless = 61 C (SwiftShader, no plugins/chrome runtime). Keep `HEADLESS=False`
  for real runs.
- `stealth_audit` weights live in one table and must sum to 128. Timezone compares through
  `_ZONE_ALIASES` because Windows tzdata reports Asia/Calcutta for Asia/Kolkata.
  `browser_audit` filters `expected` down to platform/timezone when the engine is not
  scriptable, so un-fakeable values are not graded as lies.
- `proxy_intel.ProxyIntel.pick` must mirror round-robin when the scoreboard is empty (old
  tests and old semantics depend on coverage + no-immediate-repeat), then skip cooling/dead
  proxies and prefer `prefer_country`. If every proxy is cooling it returns the one that
  recovers first — never silently a direct connection. Proxy tags are URL fragments
  (`#DE`); `fingerprint.strip_proxy_tag` removes them before Playwright sees the URL.
- The scraper marks block/fail/ok in `_recover_from_block`, `_handle_potential_block` and
  `_rotate_identity`; do not scatter proxy bookkeeping into the search loops.
- `Humanizer` takes injectable `rng`/`sleep` — tests run instantly. `level=0` multiplies all
  delays by zero; keep that property (the whole test suite relies on it).
- `test_brutal.py` (53 tests) is the fifth suite: 300 generated profiles validated, planted
  incoherence caught, proxy pick/cooldown/persistence, audit scoring, Humanizer typing/typos,
  eight new MCP tools, tagged-proxy scraper wiring. Run all FIVE suites.
- Runtime artifacts now ignored: `output/proxy_intel.json`, `output/_intel_brutal.json`,
  `output/agent_downloads/`.


## Dashboard (v7.2, 2026-11)

- `webapp.py` (stdlib `ThreadingHTTPServer`) + `dashboard_ui.html` (one file, no build, no
  framework) are the local control room: `python cli.py dashboard --open`. Default port
  **8765** — 8787 is taken/excluded on this machine (WinError 10013/10048). Bound to
  127.0.0.1 only.
- `RunManager` owns exactly ONE `cli.py run` subprocess; a second start raises and the API
  returns 409. stdout streams to `output/dashboard_run.log` plus a 5,000-line ring. Stop on
  Windows is `taskkill /PID <pid> /T /F` scoped to that run's own tree — never an image-name
  kill (see the machine-wide Chrome rule).
- `stealth-check` and `proxy-scan` actions run the real CLI as subprocesses and parse
  `--json`, so the dashboard cannot drift from the command line.
- `/api/status` caches the database section for 4s but always refreshes the live run
  snapshot. `/api/download` resolves the path and rejects anything outside the repo, plus
  non-report suffixes — keep that guard.
- UI contract used by the tests: tab buttons are `data-view="..."`, panels are
  `#view-<name>`, and these ids exist: `recordsTable`, `runsTable`, `recentTable`,
  `btnAudit`, `btnProxy`, `gauge`, `console`, `fullLog`. JS polls status every 3.2s and the
  log every 1.6s while a run is active.
- `test_webapp.py` (18 tests) boots the real handler on an ephemeral loopback port, so it
  covers HTTP without a browser. Run all SIX suites before claiming a change works.


## Security

- `config.py` hardcodes real-looking LinkedIn credentials as field defaults and they are in
  git history. Never propagate or display them; use `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`
  env vars instead.

## Gotchas

- `python -m py_compile` writes `.pyc` files into `__pycache__`, which is TRACKED in git here
  (`.gitignore` only excludes `.aider*` plus the v6.0 runtime state). For syntax checks prefer
  in-process `ast.parse`, or run with `PYTHONDONTWRITEBYTECODE=1`.
- The stealth init script in `_launch_browser` is one big f-string, so **every** literal JS brace
  must be doubled (`{{`/`}}`). A single stray `{` makes the module fail to import
  (`f-string: single '}' is not allowed`) or silently emits broken JS. `test_stealth_script_
  renders_with_real_fingerprint` renders the f-string with a sample profile and asserts there are
  no unconverted braces — extend that test if you edit the script.
- The Windows console (cp1252) can't print emoji from Python — a bare `print("✅ ...")`
  raises UnicodeEncodeError. Keep script prints ASCII or set `PYTHONIOENCODING=utf-8`.

## Dashboard preview

- `dashboard.html` is a generated artifact. Edit `dashboard_template.html` (UI source of
  truth), then regenerate with `python generate_dashboard.py`; never edit `dashboard.html`
  directly or the next run wipes your change.
- `.freebuff/` contains Freebuff client DB files (`desktop-v2.db*`) that must never be
  committed; `.freebuff/run.md` documents how the dashboard preview is reproduced and served
  (static htmlPath mode — no server).
