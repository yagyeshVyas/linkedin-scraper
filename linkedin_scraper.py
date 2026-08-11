import asyncio
import json
import random
import re
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import async_playwright, Page, BrowserContext
from bs4 import BeautifulSoup

from config import Config
from utils import (
    human_delay, random_scroll, save_progress,
    load_progress, setup_logging, export_to_excel,
    save_session_health, save_session_health_live
)

logger = logging.getLogger(__name__)


class LinkedInScraper:
    """
    Flexible LinkedIn Scraper v3.0.
    Supports People Search, Job Search, and Candidate/Resume Search
    with robust anti-block measures.
    """

    def __init__(self, config: Config):
        self.config = config
        self.results = []
        self.failed_urls = []
        self.session_count = 0
        self.daily_searches = 0
        self.consecutive_errors = 0
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        # Single account from config; supports the login/session code path.
        self.current_account = {
            "email": config.LINKEDIN_EMAIL,
            "password": config.LINKEDIN_PASSWORD,
        }
        # URLs already scraped this run (resumed progress included) — avoids
        # re-scraping the same profile under different titles/companies.
        self.scraped_urls = set()
        # Set to "credentials" when LinkedIn rejects the login form, so the
        # retry loop gives up instead of risking an account lock.
        self._last_login_error = ""
        # Proxy rotation state (see _next_proxy).
        self.current_proxy = None
        self._proxy_cursor = random.randrange(max(1, len(config.PROXY_LIST)))
        # Lazy ProxyManager for USE_FREE_PROXIES mode (auto-fetched free proxies).
        self.proxy_manager = None
        # Session-health telemetry. Persisted LIVE on every meaningful event
        # (login, throttle, rotation) so the dashboard can watch the run as it
        # happens; finalized + moved into history at run end.
        self.telemetry = {
            "status": "running",
            "started_at": None,
            "finished_at": None,
            "mode": None,
            "login": None,
            "proxy": {"source": "none", "url": None},
            "max_consecutive_errors": 0,
            "throttle_events": 0,
            "daily_searches": 0,
            "daily_search_limit": config.MAX_DAILY_SEARCHES,
            "records_scraped": 0,
            "recovered": 0,
            "still_failed": 0,
            "ban_risk": 0,
            "events": [],
        }

    # ─────────────────────────────────────────────
    #  BROWSER SETUP & ANTI-BLOCK
    # ─────────────────────────────────────────────

    async def _launch_browser(self):
        """Launch a stealth browser instance."""
        logger.info("🚀 Launching stealth browser...")

        proxy_dict = None
        if self.config.PROXY_LIST:
            proxy_url = self._next_proxy()
            proxy_dict = {"server": proxy_url}
            logger.info(f"🛡️  Using proxy server: {proxy_url}")
            self.telemetry["proxy"] = {"source": "config_list", "url": proxy_url}
        elif self.config.USE_FREE_PROXIES:
            await self._ensure_free_proxies()
            proxy_url = self._next_proxy()
            if proxy_url:
                proxy_dict = {"server": proxy_url}
                logger.info(f"🌐 Using free proxy: {proxy_url}")
                self.telemetry["proxy"] = {"source": "free", "url": proxy_url}
            else:
                logger.warning("🌐 No free proxy available — using direct connection")

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.config.HEADLESS,
            proxy=proxy_dict,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--start-maximized",
            ]
        )

        user_agent = random.choice(self.config.USER_AGENTS)
        logger.info(f"🕵️  Spoofing UA: {user_agent[:40]}...")

        # Reuse a persisted login session if one exists — the fastest and least
        # detectable path (no typing, no 2FA on subsequent runs).
        storage_state = None
        if self.config.USE_PERSISTENT_SESSION:
            session_file = self._session_file()
            if session_file.exists():
                storage_state = str(session_file)

        self.context = await self.browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation"],
            geolocation={"longitude": -73.9857, "latitude": 40.7484},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
            storage_state=storage_state,
        )

        # Stealth scripts
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        self.page = await self.context.new_page()
        await self.page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}",
            lambda route: route.abort()
        )

        logger.info("✅ Browser launched successfully.")

    async def _rotate_identity(self):
        """Re-launch browser with a new IP/proxy and User-Agent to shed fingerprint."""
        logger.info("🔄 Rotating User-Agent & clearing state to shed fingerprint...")
        self._telemetry_event("rotate", f"proxy: {self.current_proxy or 'direct'}")
        if self.browser:
            await self.browser.close()
        
        await self._launch_browser()
        # Have to log in again after rotating identity
        await self.login()

    def _next_proxy(self):
        """
        Pick the next proxy for a launch/rotation. Source priority:
          1. config.PROXY_LIST — round-robin, never the same proxy twice in a row
             (so identity rotation actually changes IPs).
          2. ProxyManager free-proxy pool (USE_FREE_PROXIES, PROXY_LIST empty).
        Returns None to use a direct connection.
        """
        proxies = self.config.PROXY_LIST
        if proxies:
            if len(proxies) > 1:
                for _ in range(len(proxies)):
                    candidate = proxies[self._proxy_cursor % len(proxies)]
                    self._proxy_cursor = (self._proxy_cursor + 1) % len(proxies)
                    if candidate != self.current_proxy:
                        self.current_proxy = candidate
                        return candidate
            self.current_proxy = proxies[self._proxy_cursor % len(proxies)]
            return self.current_proxy
        if self.config.USE_FREE_PROXIES and self.proxy_manager is not None:
            proxy = self.proxy_manager.get_proxy()
            if proxy:
                self.current_proxy = proxy
                return proxy
        self.current_proxy = None
        return None

    async def _ensure_free_proxies(self):
        """Fetch and test a pool of free proxies when PROXY_LIST is empty and
        USE_FREE_PROXIES is on. Network work runs off the event loop; results
        are cached to output/proxies.json for an hour."""
        if self.proxy_manager is None:
            from proxy_manager import ProxyManager
            self.proxy_manager = ProxyManager()
        if not self.proxy_manager.working_proxies:
            await asyncio.to_thread(self.proxy_manager.refresh)
            if not self.proxy_manager.working_proxies:
                logger.warning("⚠️  No free proxies could be fetched/tested — continuing without a proxy.")

    @staticmethod
    def _backoff_delay(consecutive_errors: int) -> float:
        """Exponential backoff with jitter: ~60s * 2^(errors-3), capped at 600s."""
        capped = min(60 * (2 ** max(consecutive_errors - 3, 0)), 600)
        return capped * random.uniform(0.8, 1.2)

    async def _handle_potential_block(self):
        """Increase delay multipliers and evaluate if identity rotation is needed."""
        self.consecutive_errors += 1
        
        if self.consecutive_errors >= self.config.CONSECUTIVE_ERROR_LIMIT and self.config.ADAPTIVE_THROTTLE:
            logger.warning(f"⚠️  {self.consecutive_errors} consecutive errors. Increasing delays and rotating!")
            # Exponential backoff that grows with the error streak, then rotate.
            wait = self._backoff_delay(self.consecutive_errors)
            logger.info(f"⏳ Adaptive break: {wait:.0f}s (consecutive errors: {self.consecutive_errors})")
            await human_delay(wait * 0.9, wait * 1.1)
            
            if self.config.ROTATE_USER_AGENT:
                # Shed the current proxy before rotating — dead proxies are
                # removed from the free-proxy pool so the list refills.
                if self.proxy_manager is not None and self.current_proxy:
                    self.proxy_manager.mark_failed(self.current_proxy)
                    self.current_proxy = None
                await self._rotate_identity()
            
            self.telemetry["max_consecutive_errors"] = max(
                self.telemetry["max_consecutive_errors"], self.consecutive_errors
            )
            self.telemetry["throttle_events"] += 1
            self._telemetry_event("throttle", f"{self.consecutive_errors} consecutive errors")
            self.consecutive_errors = 0
            return True
            
        return False

    async def _check_page_block_status(self) -> bool:
        """Check if the current page is an auth-wall, captcha, or rate-limit page."""
        current_url = self.page.url
        if "checkpoint" in current_url or "challenge" in current_url:
            logger.error("🛑 CAPTCHA / Security Checkpoint detected!")
            logger.warning("   Please complete the challenge manually in the browser window.")
            resolved = await self._await_challenge_resolution(self.config.LOGIN_CHALLENGE_TIMEOUT_SECONDS)
            return not resolved  # still blocked only if the challenge wasn't resolved
            
        # Check for authwall
        content = await self.page.content()
        if "authwall" in current_url or "Sign in to LinkedIn" in content:
            logger.error("🛑 LinkedIn triggered an auth-wall (forced login screen). Session compromised.")
            return True
            
        return False

    # ─────────────────────────────────────────────
    #  AUTHENTICATION
    # ─────────────────────────────────────────────

    def _session_file(self) -> Path:
        """Path to the persisted session file for the current account."""
        return Path(self.config.SESSION_DIR) / f"session_{self.current_account['email'].replace('@', '_')}.json"

    async def _is_logged_in(self) -> bool:
        """True if the current page looks like an authenticated LinkedIn page."""
        url = self.page.url
        if any(x in url for x in ["feed", "mynetwork", "jobs", "messaging"]):
            return True
        try:
            # The global nav only renders for authenticated sessions.
            return await self.page.query_selector("nav.global-nav, .global-nav") is not None
        except Exception:
            return False

    async def _await_challenge_resolution(self, timeout: int) -> bool:
        """Poll until a checkpoint / 2FA / verification page resolves (or the
        timeout elapses). Returns True once the session is authenticated."""
        logger.warning(f"⚠️  Verification/2FA required — complete it in the browser window (up to {timeout}s)...")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(5)
            if await self._is_logged_in():
                return True
            url = self.page.url
            still_blocked = any(x in url for x in ["checkpoint", "challenge", "verification", "authwall"])
            if not still_blocked:
                # Left the challenge page — give the feed one last check.
                if await self._is_logged_in():
                    return True
        logger.error("❌ Verification/2FA was not completed in time.")
        return False

    async def login(self) -> bool:
        """
        Log in to LinkedIn. Attack order:
          1. Restore the persisted session (fast, invisible, no typing).
          2. If stale, clear cookies and do a human-like fresh login with retries.
          3. Poll through checkpoint/2FA instead of blind waits, then confirm
             with a URL + global-nav check before declaring success.
        """
        logger.info(f"🔐 Attempting login for: {self.current_account['email']}")
        session_file = self._session_file()

        # 1) Persisted session restore first.
        if self.config.USE_PERSISTENT_SESSION and session_file.exists():
            logger.info("🔑 Restoring persisted session...")
            try:
                await self.page.goto(
                    "https://www.linkedin.com/feed/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await human_delay(2, 4)
                if await self._is_logged_in():
                    logger.info("✅ Session restored — already logged in.")
                    self._record_login(True, "restored")
                    return True
                # Stale session: drop cookies so the fresh login isn't fought by
                # a half-validated old session.
                logger.warning("⚠️  Persisted session expired — falling back to fresh login.")
                await self.context.clear_cookies()
            except Exception as e:
                logger.warning(f"⚠️  Session restore failed ({e}); continuing with fresh login.")

        # 2) Fresh login with retries (MAX_RETRIES attempts, growing backoff).
        max_attempts = max(1, self.config.MAX_RETRIES)
        for attempt in range(1, max_attempts + 1):
            if await self._fresh_login():
                if self.config.USE_PERSISTENT_SESSION:
                    session_file.parent.mkdir(parents=True, exist_ok=True)
                    await self.context.storage_state(path=str(session_file))
                    logger.info(f"💾 Session state saved to {session_file}")
                return True
            # Bad credentials won't fix themselves — retrying only risks an account lock.
            # (The detailed outcome was already recorded inside _fresh_login.)
            if self._last_login_error == "credentials":
                return False
            if attempt < max_attempts:
                backoff = random.uniform(15, 30) * attempt
                logger.warning(f"⏳ Login retry {attempt}/{max_attempts} in {backoff:.0f}s...")
                await asyncio.sleep(backoff)
        self._record_login(False, "retries_exhausted")
        return False

    async def _fresh_login(self) -> bool:
        """Single human-like login attempt. Returns True on success and records
        the failure kind in self._last_login_error ("credentials" vs generic)."""
        self._last_login_error = ""
        try:
            await self.page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await human_delay(3, 5)

            email_input = await self.page.wait_for_selector("#username", timeout=20000)
            await email_input.click()
            await human_delay(0.5, 1.5)
            await self._type_like_human(email_input, self.current_account['email'])
            await human_delay(0.8, 1.5)

            password_input = await self.page.wait_for_selector("#password")
            await password_input.click()
            await human_delay(0.3, 0.8)
            await self._type_like_human(password_input, self.current_account['password'])
            await human_delay(0.5, 1.5)

            await self.page.click('button[type="submit"]')

            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await human_delay(4, 7)

            # Fast path: straight to an authenticated page.
            if await self._is_logged_in():
                logger.info("✅ Login successful!")
                self._record_login(True, "fresh")
                return True

            url = self.page.url
            logger.info(f"   Post-login URL: {url}")

            # Credentials rejected — surface LinkedIn's own message instead of guessing.
            if "#username" in url or ("login" in url and await self.page.query_selector("#error-for-password, .alert, .form__error")):
                error_text = await self.page.evaluate(
                    """() => {
                        const el = document.querySelector('#error-for-password, .alert, .form__error');
                        return el ? el.textContent.trim() : '';
                    }"""
                )
                logger.error(f"❌ Login rejected by LinkedIn: {error_text or 'check LINKEDIN_EMAIL / LINKEDIN_PASSWORD'}")
                self._last_login_error = "credentials"
                self._record_login(False, "credentials", detail=error_text or "")
                return False

            # Checkpoint / 2FA / verification — poll until resolved.
            if any(x in url for x in ["checkpoint", "challenge", "verification"]):
                if await self._await_challenge_resolution(self.config.LOGIN_CHALLENGE_TIMEOUT_SECONDS):
                    logger.info("✅ Login successful (after verification)!")
                    self._record_login(True, "fresh_after_verification")
                    return True
                self._record_login(False, "challenge_timeout")
                return False

            # Landed on the homepage without a challenge — poke at the feed once.
            if url.rstrip("/") in ["https://www.linkedin.com", "https://linkedin.com", "https://www.linkedin.com/feed"]:
                await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
                await human_delay(2, 4)
                if await self._is_logged_in():
                    logger.info("✅ Login successful!")
                    self._record_login(True, "fresh")
                    return True

            logger.error(f"❌ Login failed. URL: {self.page.url}")
            logger.error("   Double-check LINKEDIN_EMAIL and LINKEDIN_PASSWORD in config.py")
            self._record_login(False, "unknown")
            return False

        except Exception as e:
            logger.error(f"❌ Login error: {e}")
            self._record_login(False, "error", detail=str(e)[:200])
            return False

    async def _type_like_human(self, element, text: str):
        """Type character by character to mimic human typing."""
        for char in text:
            await element.type(char, delay=random.randint(50, 200))

    # ─────────────────────────────────────────────
    #  SEARCH — JOBS (NEW)
    # ─────────────────────────────────────────────

    async def search_jobs(self, keyword: str) -> list:
        """
        Search LinkedIn for job listings by keyword.
        Returns a list of job dicts extracted from the search results page.
        """
        jobs = []
        page_num = 1
        
        # Build search query
        query_params = {
            "keywords": keyword,
            "origin": "GLOBAL_SEARCH_HEADER",
            "refresh": "true"
        }
        
        # Add location
        if self.config.GEO_URN:
            query_params["f_GC"] = self.config.GEO_URN
            
        # Add filters
        if self.config.JOB_EXPERIENCE_LEVEL:
            query_params["f_E"] = self.config.JOB_EXPERIENCE_LEVEL
            
        # Smart override for strict time filter
        if getattr(self.config, 'JOB_STRICT_HOURS_FILTER', None) is not None:
            if self.config.JOB_STRICT_HOURS_FILTER <= 24:
                query_params["f_TPR"] = "r86400"
            elif self.config.JOB_STRICT_HOURS_FILTER <= 168:
                query_params["f_TPR"] = "r604800"
            else:
                query_params["f_TPR"] = "r2592000"
        elif self.config.JOB_DATE_POSTED:
            query_params["f_TPR"] = self.config.JOB_DATE_POSTED
            
        if self.config.JOB_REMOTE_FILTER:
            query_params["f_WT"] = self.config.JOB_REMOTE_FILTER
            
        search_url = f"https://www.linkedin.com/jobs/search/?{urlencode(query_params)}"
        
        logger.info(f"   💼 Searching Jobs: '{keyword}'")

        while page_num <= self.config.MAX_JOB_PAGES:
            if self.daily_searches >= self.config.MAX_DAILY_SEARCHES:
                logger.warning("🛑 Daily search limit reached. Stopping.")
                break

            try:
                # Add pagination offset (25 jobs per page)
                url = search_url if page_num == 1 else f"{search_url}&start={(page_num-1)*25}"
                
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.REQUEST_TIMEOUT_MS)
                await human_delay(3, 6)
                
                if await self._check_page_block_status():
                    break
                    
                self.consecutive_errors = 0  # Reset errors on success
                self._register_search()
                
                await random_scroll(self.page, scrolls=5) # Scroll more for jobs to load

                content = await self.page.content()
                if "No matching jobs found" in content or "We couldn't find any jobs" in content:
                    logger.info("   No more jobs found.")
                    break

                new_jobs = await self._extract_job_cards()
                if not new_jobs:
                    logger.info(f"      No extractable jobs on page {page_num}.")
                    break
                    
                # Apply Strict Hours Filter
                if getattr(self.config, 'JOB_STRICT_HOURS_FILTER', None) is not None:
                    filtered_jobs = []
                    for job in new_jobs:
                        hours_old = self._parse_hours_ago(job.get('full_text', ''))
                        if hours_old <= self.config.JOB_STRICT_HOURS_FILTER:
                            filtered_jobs.append(job)
                            
                    dropped = len(new_jobs) - len(filtered_jobs)
                    if dropped > 0:
                        logger.info(f"      Dropped {dropped} jobs older than {self.config.JOB_STRICT_HOURS_FILTER} hours.")
                    
                    new_jobs = filtered_jobs
                    
                if not new_jobs:
                    logger.info(f"      No jobs matched time filter on page {page_num}.")
                    # If all jobs on page are too old, we probably hit the chronological end.
                    if dropped > 0:
                        break

                jobs.extend(new_jobs)
                logger.info(f"      Page {page_num}: +{len(new_jobs)} jobs extracted")

                page_num += 1
                await human_delay(4, 9)

                self.session_count += 1
                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                    await self._take_session_break()
                    
                if self.config.ROTATE_USER_AGENT and self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                    await self._rotate_identity()

            except Exception as e:
                logger.error(f"      Search error page {page_num}: {e}")
                if await self._handle_potential_block():
                    pass # Handled by block detector
                else:
                    break

        return jobs

    async def _extract_job_cards(self) -> list:
        """Extract job details directly from the search results list."""
        jobs = []
        try:
            # Wait for job list to render
            await self.page.wait_for_selector(".jobs-search-results-list", timeout=5000)
            
            # Extract basic data using playwright eval
            job_cards = await self.page.eval_on_selector_all(
                ".job-card-container",
                """elements => elements.map(el => {
                    const titleEl = el.querySelector('.job-card-list__title');
                    const companyEl = el.querySelector('.job-card-container__company-name');
                    const locationEl = el.querySelector('.job-card-container__metadata-item');
                    const linkEl = el.querySelector('a.job-card-container__link');
                    
                    return {
                        title: titleEl ? titleEl.innerText.trim() : 'Unknown',
                        company: companyEl ? companyEl.innerText.trim() : 'Unknown',
                        location: locationEl ? locationEl.innerText.trim() : 'Unknown',
                        url: linkEl ? linkEl.href.split('?')[0] : '',
                        full_text: el.innerText
                    }
                })"""
            )
            
            for card in job_cards:
                if card['url']:
                    card['scraped_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    jobs.append(card)
                    
        except Exception as e:
            logger.warning(f"Job extraction warning: {e}")
            
        return jobs

    def _parse_hours_ago(self, text: str) -> int:
        """Parse text like '5 minutes ago' or '2 days ago' into total hours."""
        text = text.lower()
        if "just now" in text or "minutes ago" in text or "minute ago" in text:
            return 0
            
        match = re.search(r'(\d+)\s+(hour|day|week|month)s?\s*ago', text)
        if match:
            val = int(match.group(1))
            unit = match.group(2)
            if unit == 'hour': return val
            if unit == 'day': return val * 24
            if unit == 'week': return val * 24 * 7
            if unit == 'month': return val * 24 * 30
            
        return 999999 # Unknown or extremely old

    # ─────────────────────────────────────────────
    #  SEARCH — CANDIDATES (NEW)
    # ─────────────────────────────────────────────

    async def search_candidates(self, skill: str) -> list:
        """
        Search LinkedIn for people matching specific skills.
        Returns a list of profile URLs.
        """
        profile_urls = []
        page_num = 1

        # Use title filter if provided, otherwise just skill
        if self.config.CANDIDATE_TITLE_FILTER:
            search_query = f'{skill} AND "{self.config.CANDIDATE_TITLE_FILTER}"'
        else:
            search_query = f'{skill}'

        search_url = (
            "https://www.linkedin.com/search/results/people/"
            "?" + urlencode({
                "keywords": search_query,
                "origin": "GLOBAL_SEARCH_HEADER",
                "geoUrn": f'["{self.config.GEO_URN}"]',
            })
        )

        logger.info(f"   👤 Searching Candidates: '{search_query}'")

        while page_num <= self.config.MAX_CANDIDATE_PAGES:
            if self.daily_searches >= self.config.MAX_DAILY_SEARCHES:
                logger.warning("🛑 Daily search limit reached. Stopping.")
                break
                
            try:
                url = search_url if page_num == 1 else f"{search_url}&page={page_num}"
                
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.REQUEST_TIMEOUT_MS)
                await human_delay(3, 6)
                
                if await self._check_page_block_status():
                    break
                    
                self.consecutive_errors = 0
                self._register_search()
                
                await random_scroll(self.page)

                content = await self.page.content()
                if "No results found" in content or "no results" in content.lower():
                    break

                new_urls = await self._extract_profile_urls()
                if not new_urls:
                    break

                profile_urls.extend(new_urls)
                logger.info(f"      Page {page_num}: +{len(new_urls)} candidate profiles")

                page_num += 1
                await human_delay(4, 9)

                self.session_count += 1
                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                    await self._take_session_break()
                    
                if self.config.ROTATE_USER_AGENT and self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                    await self._rotate_identity()

            except Exception as e:
                logger.error(f"      Search error page {page_num}: {e}")
                if await self._handle_potential_block():
                    pass
                else:
                    break

        return list(set(profile_urls))

    # ─────────────────────────────────────────────
    #  SEARCH — PEOPLE (EXISTING, UPDATED with anti-block)
    # ─────────────────────────────────────────────

    async def search_people(self, company: str, job_title: str) -> list:
        """
        Search LinkedIn for people with a specific job title at a company.
        Returns a list of profile URLs.
        """
        profile_urls = []
        page_num = 1

        # Build search query
        search_query = f'"{job_title}" "{company}"'

        search_url = (
            "https://www.linkedin.com/search/results/people/"
            "?" + urlencode({
                "keywords": search_query,
                "origin": "GLOBAL_SEARCH_HEADER",
                "geoUrn": f'["{self.config.GEO_URN}"]',
            })
        )

        logger.info(f"   🔍 '{job_title}' at '{company}'")

        while page_num <= self.config.MAX_PAGES_PER_COMPANY:
            if self.daily_searches >= self.config.MAX_DAILY_SEARCHES:
                logger.warning("🛑 Daily search limit reached. Stopping.")
                break

            try:
                url = search_url if page_num == 1 else f"{search_url}&page={page_num}"
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.REQUEST_TIMEOUT_MS)
                await human_delay(3, 6)
                
                if await self._check_page_block_status():
                    break
                    
                self.consecutive_errors = 0
                self._register_search()
                
                await random_scroll(self.page)

                content = await self.page.content()
                if "No results found" in content or "no results" in content.lower():
                    break

                new_urls = await self._extract_profile_urls()
                if not new_urls:
                    break

                profile_urls.extend(new_urls)
                logger.info(f"      Page {page_num}: +{len(new_urls)} profiles")

                page_num += 1
                await human_delay(4, 9)

                self.session_count += 1
                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                    await self._take_session_break()
                    
                if self.config.ROTATE_USER_AGENT and self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                    await self._rotate_identity()

            except Exception as e:
                logger.error(f"      Search error page {page_num}: {e}")
                if await self._handle_potential_block():
                    pass
                else:
                    break

        return list(set(profile_urls))

    async def _extract_profile_urls(self) -> list:
        """Extract all LinkedIn /in/ profile URLs from the current page."""
        urls = []
        try:
            links = await self.page.eval_on_selector_all(
                "a[href*='/in/']",
                "elements => elements.map(el => el.href)"
            )
            for link in links:
                clean = re.sub(r'\?.*', '', link).rstrip('/')
                if "/in/" in clean and clean not in urls:
                    urls.append(clean)
        except Exception as e:
            logger.warning(f"URL extraction warning: {e}")
        return urls

    # ─────────────────────────────────────────────
    #  PROFILE SCRAPING
    # ─────────────────────────────────────────────

    async def scrape_profile(self, profile_url: str, search_company: str, job_title: str) -> Optional[dict]:
        """Scrape a single LinkedIn profile. Returns dict or None."""
        try:
            await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            await human_delay(3, 7)
            await random_scroll(self.page, scrolls=3)

            soup = BeautifulSoup(await self.page.content(), "html.parser")

            profile = {
                "name":           self._extract_name(soup),
                "title":          self._extract_current_title(soup),
                "company":        self._extract_current_company(soup),
                "headline":       self._extract_headline(soup),
                "location":       self._extract_location(soup),
                "about":          self._extract_about(soup) if self.config.SCRAPE_WORK_EXPERIENCE else "",
                "skills":         self._extract_skills(soup) if self.config.SCRAPE_WORK_EXPERIENCE else "",
                "experience":     self._extract_experience(soup) if self.config.SCRAPE_WORK_EXPERIENCE else "",
                "education":      self._extract_education(soup) if self.config.SCRAPE_EDUCATION else "",
                "email":          self._extract_email(soup),
                "phone":          self._extract_phone(soup),
                "connections":    self._extract_connections(soup),
                "linkedin_url":   profile_url,
                "search_company": search_company,
                "searched_title": job_title,
                "scraped_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            # Apply keyword filter if configured
            if self._passes_filter(profile):
                logger.info(f"   ✅ {profile['name']} | {profile['title']} | {profile['company']}")
                return profile
            else:
                logger.debug(f"   ⏭️  Skipped (filtered out): {profile['name']}")
                return None

        except Exception as e:
            logger.error(f"   ❌ Failed: {profile_url} — {e}")
            # Keep enough context to retry this profile once at the end of the run.
            self.failed_urls.append({
                "url": profile_url,
                "company": search_company,
                "title": job_title,
            })
            return None

    def _passes_filter(self, profile: dict) -> bool:
        """
        Returns True if the profile passes the FILTER_KEYWORDS check.
        If FILTER_KEYWORDS is empty, all profiles pass.
        """
        if not self.config.FILTER_KEYWORDS:
            return True  # No filter = accept everything

        text = (
            (profile.get("title") or "") + " " +
            (profile.get("headline") or "")
        ).lower()

        return any(kw.lower() in text for kw in self.config.FILTER_KEYWORDS)

    # ─────────────────────────────────────────────
    #  DATA EXTRACTION HELPERS
    # ─────────────────────────────────────────────

    def _extract_name(self, soup) -> str:
        for sel in ["h1.text-heading-xlarge", "h1.inline.t-24", "h1"]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return "Unknown"

    def _extract_headline(self, soup) -> str:
        for sel in [".text-body-medium.break-words", ".pv-top-card--experience-list-item"]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return ""

    def _extract_current_title(self, soup) -> str:
        headline = self._extract_headline(soup)
        if headline:
            parts = re.split(r"\s+at\s+|\s*\|\s*", headline)
            return parts[0].strip()
        return ""

    def _extract_current_company(self, soup) -> str:
        exp_section = soup.find("section", {"id": "experience"})
        if exp_section:
            el = exp_section.select_one(".pv-entity__secondary-title, .t-14.t-normal")
            if el:
                return el.get_text(strip=True)
        el = soup.select_one(".pv-top-card--experience-list .pv-top-card--experience-list-item")
        return el.get_text(strip=True) if el else ""

    def _extract_location(self, soup) -> str:
        for sel in [".text-body-small.inline.t-black--light.break-words"]:
            els = soup.select(sel)
            for el in els:
                text = el.get_text(strip=True)
                if "," in text and len(text) < 60:
                    return text
        return ""

    def _extract_email(self, soup) -> str:
        """Find the profile's contact email. Prefers explicit mailto: links,
        then falls back to scanning page text for a plausible address while
        filtering out framework/image/site-internal false positives."""
        # 1) Explicit mailto: links are the most reliable signal.
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto:
            addr = mailto.get("href", "").replace("mailto:", "").split("?")[0].strip()
            if addr and "@" in addr:
                return addr

        # 2) Text scan with aggressive false-positive filtering.
        text = soup.get_text()
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
        junk_domains = (
            "linkedin.com", "sentry.io", "example.com", "gstatic.com",
            "w3.org", "schema.org", "play.google.com", "microsoft.com",
            "google.com", "github.com", "2x.png", "png", "jpg", "jpeg",
        )
        for email in emails:
            local, _, domain = email.partition("@")
            if any(j in domain.lower() for j in junk_domains):
                continue
            if local.isdigit():  # image hash like 123456789@2x.png
                continue
            return email
        return ""

    def _extract_phone(self, soup) -> str:
        text = soup.get_text()
        phones = re.findall(r'(\+?1?\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})', text)
        return phones[0][1] if phones else ""

    def _extract_connections(self, soup) -> str:
        el = soup.select_one(".pv-top-card--list.pv-top-card--list-bullet .t-black--light")
        return el.get_text(strip=True) if el else ""

    def _extract_about(self, soup) -> str:
        section = soup.find("section", {"id": "about"}) or soup.find("section", class_=re.compile(r"pv-about"))
        if not section:
            return ""
        el = section.select_one(".pv-shared-text-with-see-more") or section.select_one(".pv-about__summary-text")
        return el.get_text(" ", strip=True)[:1000] if el else ""

    def _extract_skills(self, soup) -> str:
        section = soup.find("section", {"id": "skills"}) or soup.find("section", class_=re.compile(r"pv-skill"))
        if not section:
            return ""
        names = [
            el.get_text(strip=True)
            for el in section.select(".pv-skill-category-entity__name-text, .skill-category-entity__name")
        ]
        return ", ".join(dict.fromkeys(n for n in names if n))

    def _extract_experience(self, soup) -> str:
        section = soup.find("section", {"id": "experience"}) or soup.find("section", class_=re.compile(r"pv-experience"))
        if not section:
            return ""
        entries = []
        for pos in section.select(".pv-position-entity"):
            title = pos.select_one(".pv-entity__summary-info h3") or pos.select_one(".t-bold")
            company = pos.select_one(".pv-entity__secondary-title")
            date_text = ""
            date_range = pos.select_one(".pv-entity__date-range")
            if date_range:
                spans = [s.get_text(strip=True) for s in date_range.find_all("span")]
                spans = [s for s in spans if s]
                if len(spans) == 2:
                    date_text = f"{spans[0]} – {spans[1]}"
                elif spans:
                    date_text = spans[0]
            if title:
                parts = [title.get_text(strip=True)]
                if company:
                    parts.append("at " + company.get_text(strip=True))
                if date_text:
                    parts.append(f"({date_text})")
                entries.append(" ".join(parts))
        return " | ".join(entries[:10])

    def _extract_education(self, soup) -> str:
        section = soup.find("section", {"id": "education"}) or soup.find("section", class_=re.compile(r"pv-education"))
        if not section:
            return ""
        entries = []
        for edu in section.select(".pv-education-entity"):
            school = edu.select_one(".pv-entity__school-name")
            degree = edu.select_one(".pv-entity__degree-name") or edu.select_one(".pv-entity__summary-info h3")
            if school:
                entries.append((school.get_text(strip=True) + (f" — {degree.get_text(strip=True)}" if degree and degree.get_text(strip=True) else "")))
        return " | ".join(entries[:6])

    async def _retry_failed_profiles(self) -> int:
        """
        One final pass over profiles that failed transiently (timeouts, bad
        page loads). Each URL not already scraped gets a single fresh attempt.
        Returns the number of profiles recovered.
        """
        pending = [f for f in self.failed_urls if f.get("url") not in self.scraped_urls]
        if not pending:
            return 0
        logger.info(f"🔁 Retrying {len(pending)} failed profile(s) once...")
        recovered = 0
        for f in pending:
            profile = await self.scrape_profile(
                f["url"], search_company=f.get("company", ""), job_title=f.get("title", "")
            )
            if profile:
                self.results.append(profile)
                self.scraped_urls.add(f["url"])
                self.telemetry["records_scraped"] = len(self.results)
                recovered += 1
            await human_delay(
                self.config.MIN_DELAY_BETWEEN_PROFILES,
                self.config.MAX_DELAY_BETWEEN_PROFILES,
            )
        # Keep only the ones that are still genuinely failing.
        self.failed_urls = [f for f in self.failed_urls if f.get("url") not in self.scraped_urls]
        logger.info(f"   ↪ Recovered {recovered}, {len(self.failed_urls)} still failing.")
        return recovered

    async def dry_run(self, input_list: list) -> bool:
        """
        Safe end-to-end smoke test: launch the browser, log in, and run ONE
        search for the current mode — then stop. No profiles are scraped, no
        progress.json is written, nothing is exported.
        Returns True only if browser + login + search all worked.
        """
        setup_logging(self.config.LOG_FILE)
        mode = self.config.SEARCH_MODE.lower()
        logger.info("=" * 65)
        logger.info(f"  🧪 DRY RUN — mode: {mode.upper()} (no profile scraping)")
        logger.info("=" * 65)

        self.telemetry["mode"] = mode
        self.telemetry["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._telemetry_event("run_start", f"dry-run · {mode} mode")

        await self._launch_browser()
        try:
            if not await self.login():
                logger.error("❌ Login failed — dry run aborted.")
                return False

            if mode == "jobs":
                keyword = input_list[0] if input_list else "python"
                jobs = await self.search_jobs(keyword)
                logger.info(f"✅ DRY RUN OK — {len(jobs)} job card(s) found for '{keyword}'. No profiles scraped.")
            elif mode == "candidates":
                skill = input_list[0] if input_list else "Python"
                urls = await self.search_candidates(skill)
                logger.info(f"✅ DRY RUN OK — {len(urls)} candidate profile(s) found for '{skill}'. No profiles scraped.")
            else:  # people
                company = input_list[0] if input_list else "Google"
                title = self.config.JOB_TITLES[0] if self.config.JOB_TITLES else "Recruiter"
                urls = await self.search_people(company, title)
                logger.info(f"✅ DRY RUN OK — {len(urls)} profile(s) found for '{title}' at '{company}'. No profiles scraped.")
            return True
        finally:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            self._finalize_telemetry(mode)

    # ─────────────────────────────────────────────
    #  SESSION MANAGEMENT
    # ─────────────────────────────────────────────

    async def _take_session_break(self):
        """Random break to avoid detection — visits feed like a real user."""
        t = random.randint(self.config.MIN_SESSION_BREAK_SECONDS, self.config.MAX_SESSION_BREAK_SECONDS)
        logger.info(f"☕ Session break: {t}s...")
        await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await asyncio.sleep(t / 2)
        await random_scroll(self.page, scrolls=random.randint(2, 4))
        await asyncio.sleep(t / 2)

    # ─────────────────────────────────────────────
    #  MAIN ORCHESTRATION
    # ─────────────────────────────────────────────

    def _compute_ban_risk(self) -> int:
        """0-100 heuristic: a failed login is the heaviest signal, then
        throttles and error streaks, with small bumps for free proxies and
        hitting the daily search cap."""
        t = self.telemetry
        score = 0
        if t.get("login") and not t["login"].get("ok"):
            score += 40
        score += min((t.get("throttle_events") or 0) * 10, 30)
        score += min((t.get("max_consecutive_errors") or 0) * 5, 20)
        if t.get("proxy", {}).get("source") == "free":
            score += 5
        limit = t.get("daily_search_limit") or 0
        if limit and (t.get("daily_searches") or 0) >= limit:
            score += 5
        return min(score, 100)

    def _telemetry_event(self, kind: str, detail: str = ""):
        """Append a timestamped event and persist the live session file so a
        crash mid-run leaves the full story behind."""
        events = self.telemetry.get("events", [])[-49:]
        events.append({
            "t": datetime.now().strftime("%H:%M:%S"),
            "kind": kind,
            "detail": detail,
        })
        self.telemetry["events"] = events
        self.telemetry["ban_risk"] = self._compute_ban_risk()
        try:
            save_session_health_live(self.config.SESSION_HEALTH_FILE, self.telemetry)
        except Exception as e:
            logger.warning(f"⚠️  Could not persist session telemetry: {e}")

    def _record_login(self, ok: bool, reason: str, detail: str = ""):
        """Record a login outcome in telemetry and emit a login event."""
        entry = {"ok": ok, "reason": reason}
        if detail:
            entry["detail"] = detail
        self.telemetry["login"] = entry
        self._telemetry_event("login", f"{'ok' if ok else 'fail'}: {reason}")

    def _register_search(self):
        """Count one search-page load (daily cap + live telemetry counter)."""
        self._register_search()
        self.telemetry["daily_searches"] = self.daily_searches

    def _finalize_telemetry(self, mode: str, recovered: int = 0):
        """Mark the run finished and move it into session_health history."""
        self.telemetry["mode"] = mode
        self.telemetry["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.telemetry["daily_searches"] = self.daily_searches
        self.telemetry["records_scraped"] = len(self.results)
        self.telemetry["recovered"] = recovered
        self.telemetry["still_failed"] = len(self.failed_urls)
        self.telemetry["status"] = "finished"
        self.telemetry["ban_risk"] = self._compute_ban_risk()
        self._telemetry_event("run_end", f"{mode} mode · {len(self.results)} records")
        try:
            save_session_health(self.config.SESSION_HEALTH_FILE, self.telemetry)
        except Exception as e:
            logger.warning(f"⚠️  Could not write session-health telemetry: {e}")

    async def run(self, input_list: list):
        """
        Main loop:
          If mode="people": search for titles at given companies
          If mode="jobs": search for job listings from input keywords
          If mode="candidates": search for candidates from input skills
        """
        setup_logging(self.config.LOG_FILE)
        
        mode = self.config.SEARCH_MODE.lower()
        self.telemetry["mode"] = mode
        self.telemetry["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._telemetry_event("run_start", f"{mode} mode")

        logger.info("=" * 65)
        logger.info(f"  LinkedIn Scraper v3.0 — Mode: {mode.upper()}")
        logger.info(f"  Location   : {self.config.SEARCH_LOCATION_NAME}")
        logger.info("=" * 65)

        saved = load_progress(self.config.PROGRESS_FILE)
        self.results = saved.get("results", [])
        completed_keys = set(saved.get("completed_keys", []))
        # Don't re-scrape profiles already captured in a previous run.
        self.scraped_urls = {r.get("linkedin_url") for r in self.results if r.get("linkedin_url")}

        await self._launch_browser()

        try:
            if not await self.login():
                logger.error("❌ Login failed. Exiting.")
                return

            total = len(input_list)
            
            # --- PEOPLE SEARCH MODE ---
            if mode == "people":
                for i, company in enumerate(input_list, 1):
                    logger.info(f"\n[{i}/{total}] 🏢 {company}")

                    for job_title in self.config.JOB_TITLES:
                        key = f"people::{company}::{job_title}"
                        if key in completed_keys:
                            logger.info(f"   ⏭️  Already done: '{job_title}' — skipping")
                            continue

                        profile_urls = await self.search_people(company, job_title)

                        if not profile_urls:
                            logger.info(f"      No results found.")
                        else:
                            fresh = [u for u in profile_urls if u not in self.scraped_urls]
                            skipped = len(profile_urls) - len(fresh)
                            if skipped:
                                logger.info(f"      Skipped {skipped} already-scraped profile(s)")
                            logger.info(f"      Found {len(fresh)} new profiles to scrape...")
                            for j, url in enumerate(fresh, 1):
                                logger.info(f"      [{j}/{len(fresh)}] Scraping...")
                                profile = await self.scrape_profile(url, search_company=company, job_title=job_title)
                                if profile:
                                    self.results.append(profile)
                                    self.scraped_urls.add(url)
                                    self.telemetry["records_scraped"] = len(self.results)

                                await human_delay(
                                    self.config.MIN_DELAY_BETWEEN_PROFILES,
                                    self.config.MAX_DELAY_BETWEEN_PROFILES
                                )
                                self.session_count += 1
                                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                                    await self._take_session_break()
                                if self.config.ROTATE_USER_AGENT and self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                                    await self._rotate_identity()

                        completed_keys.add(key)
                        save_progress(self.config.PROGRESS_FILE, {
                            "results": self.results,
                            "completed_keys": list(completed_keys)
                        })
                        await human_delay(4, 10)

            # --- JOB SEARCH MODE ---
            elif mode == "jobs":
                for i, keyword in enumerate(input_list, 1):
                    logger.info(f"\n[{i}/{total}] 💼 Keyword: {keyword}")
                    
                    key = f"jobs::{keyword}"
                    if key in completed_keys:
                        logger.info(f"   ⏭️  Already done: '{keyword}' — skipping")
                        continue
                        
                    jobs = await self.search_jobs(keyword)
                    
                    if not jobs:
                        logger.info(f"      No jobs found.")
                    else:
                        # Append search keyword context
                        for j in jobs:
                            j['search_keyword'] = keyword
                        self.results.extend(jobs)
                        
                    completed_keys.add(key)
                    save_progress(self.config.PROGRESS_FILE, {
                        "results": self.results,
                        "completed_keys": list(completed_keys)
                    })
                    await human_delay(4, 10)

            # --- CANDIDATES SEARCH MODE ---
            elif mode == "candidates":
                for i, skill in enumerate(input_list, 1):
                    logger.info(f"\n[{i}/{total}] 👤 Skill: {skill}")
                    
                    key = f"candidates::{skill}"
                    if key in completed_keys:
                        logger.info(f"   ⏭️  Already done: '{skill}' — skipping")
                        continue
                        
                    profile_urls = await self.search_candidates(skill)
                    
                    if not profile_urls:
                        logger.info(f"      No results found.")
                    else:
                        fresh = [u for u in profile_urls if u not in self.scraped_urls]
                        skipped = len(profile_urls) - len(fresh)
                        if skipped:
                            logger.info(f"      Skipped {skipped} already-scraped candidate(s)")
                        logger.info(f"      Found {len(fresh)} candidates to scrape...")
                        for j, url in enumerate(fresh, 1):
                            logger.info(f"      [{j}/{len(fresh)}] Scraping...")
                            # we pass search_company="N/A" because this is pure skill search
                            profile = await self.scrape_profile(url, search_company="N/A", job_title=skill)
                            if profile:
                                profile['search_skill'] = skill
                                self.results.append(profile)
                                self.scraped_urls.add(url)
                                self.telemetry["records_scraped"] = len(self.results)

                            await human_delay(
                                self.config.MIN_DELAY_BETWEEN_PROFILES,
                                self.config.MAX_DELAY_BETWEEN_PROFILES
                            )
                            self.session_count += 1
                            if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                                await self._take_session_break()
                            if self.config.ROTATE_USER_AGENT and self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                                await self._rotate_identity()

                    completed_keys.add(key)
                    save_progress(self.config.PROGRESS_FILE, {
                        "results": self.results,
                        "completed_keys": list(completed_keys)
                    })
                    await human_delay(4, 10)

        except KeyboardInterrupt:
            logger.info("\n⚠️  Interrupted. Saving progress...")

        finally:
            # One last chance for profiles that failed transiently.
            recovered = 0
            if mode in ("people", "candidates") and self.config.RETRY_FAILED_PROFILES:
                try:
                    recovered = await self._retry_failed_profiles()
                except Exception as e:
                    logger.warning(f"⚠️  Failed-URL retry pass errored: {e}")

            if self.results:
                # Decide which export function to use based on mode
                if mode == "jobs":
                    from utils import export_jobs_to_excel
                    path = export_jobs_to_excel(self.results, self.config.JOBS_OUTPUT_FILE)
                elif mode == "candidates":
                    from utils import export_candidates_to_excel
                    path = export_candidates_to_excel(self.results, self.config.CANDIDATES_OUTPUT_FILE)
                else:
                    path = export_to_excel(self.results, self.config.OUTPUT_FILE)
                    
                logger.info(f"\n🎉 Exported {len(self.results)} records → {path}")
                
                # Send Webhook Notification
                if self.config.DISCORD_WEBHOOK_URL:
                    from utils import send_webhook_notification
                    summary = {
                        "Mode": mode.upper(),
                        "Total Records": len(self.results),
                        "Searches Done": self.daily_searches,
                        "Output File": path
                    }
                    send_webhook_notification(self.config.DISCORD_WEBHOOK_URL, "Scrape Complete!", summary)
            else:
                logger.info("\n⚠️  No results to export.")

            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()

            logger.info(f"\n📊 Final Summary:")
            logger.info(f"   Records saved   : {len(self.results)}")
            if mode != "jobs":
                logger.info(f"   Failed URLs     : {len(self.failed_urls)}")
            logger.info(f"   Searches done   : {self.daily_searches} page loads")

            self._finalize_telemetry(mode, recovered)