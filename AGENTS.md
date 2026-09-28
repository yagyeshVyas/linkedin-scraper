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
- The dashboard template renders and CSV-exports `r.url`, but records store `linkedin_url`;
  `generate_dashboard.load_records()` mirrors it. Profile links silently broke before that
  normalisation — keep it if you touch either side.
- The dashboard table's sort coerces with `String(a[key] ?? "")`; without that, sorting any
  boolean/number column (open_to_work, follower_count, connection_count) throws.
- Behavioral tests: `test_scraper_fixes.py` (30 tests) and `test_login_flow.py` (5 tests) — run
  both with `.venv/Scripts/python.exe <file>` (no browser or network needed; the login suite
  drives the real `login()` against fakes). No pytest in the venv — each file is a plain script
  with a `__main__` runner list; add new tests to that list.

## Security

- `config.py` hardcodes real-looking LinkedIn credentials as field defaults and they are in
  git history. Never propagate or display them; use `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`
  env vars instead.

## Gotchas

- `python -m py_compile` writes `.pyc` files into `__pycache__`, which is TRACKED in git here
  (`.gitignore` only excludes `.aider*`). For syntax checks prefer in-process `ast.parse`, or
  run with `PYTHONDONTWRITEBYTECODE=1`.
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
