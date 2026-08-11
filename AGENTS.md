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
- Behavioral tests: `test_scraper_fixes.py` and `test_login_flow.py` — run both with
  `.venv/Scripts/python.exe <file>` (no browser or network needed; the login suite drives the
  real `login()` against fakes).

## Security

- `config.py` hardcodes real-looking LinkedIn credentials as field defaults and they are in
  git history. Never propagate or display them; use `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD`
  env vars instead.

## Gotchas

- `python -m py_compile` writes `.pyc` files into `__pycache__`, which is TRACKED in git here
  (`.gitignore` only excludes `.aider*`). For syntax checks prefer in-process `ast.parse`, or
  run with `PYTHONDONTWRITEBYTECODE=1`.
- The Windows console (cp1252) can't print emoji from Python — a bare `print("✅ ...")`
  raises UnicodeEncodeError. Keep script prints ASCII or set `PYTHONIOENCODING=utf-8`.

## Dashboard preview

- `dashboard.html` is a generated artifact. Edit `dashboard_template.html` (UI source of
  truth), then regenerate with `python generate_dashboard.py`; never edit `dashboard.html`
  directly or the next run wipes your change.
- `.freebuff/` contains Freebuff client DB files (`desktop-v2.db*`) that must never be
  committed; `.freebuff/run.md` documents how the dashboard preview is reproduced and served
  (static htmlPath mode — no server).
