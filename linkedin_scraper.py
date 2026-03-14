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
    load_progress, setup_logging, export_to_excel
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

    # ─────────────────────────────────────────────
    #  BROWSER SETUP & ANTI-BLOCK
    # ─────────────────────────────────────────────

    async def _launch_browser(self):
        """Launch a stealth browser instance."""
        logger.info("🚀 Launching stealth browser...")

        proxy_dict = None
        if self.config.PROXY_LIST:
            proxy_url = random.choice(self.config.PROXY_LIST)
            proxy_dict = {"server": proxy_url}
            logger.info("🛡️  Using proxy server")

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
            }
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
        if self.browser:
            await self.browser.close()
        
        await self._launch_browser()
        # Have to log in again after rotating identity
        await self.login()

    async def _handle_potential_block(self):
        """Increase delay multipliers and evaluate if identity rotation is needed."""
        self.consecutive_errors += 1
        
        if self.consecutive_errors >= self.config.CONSECUTIVE_ERROR_LIMIT and self.config.ADAPTIVE_THROTTLE:
            logger.warning(f"⚠️  {self.consecutive_errors} consecutive errors. Increasing delays and rotating!")
            # Take a long adaptive break
            await human_delay(120, 240)
            
            if self.config.ROTATE_USER_AGENT:
                await self._rotate_identity()
            
            self.consecutive_errors = 0
            return True
            
        return False

    async def _check_page_block_status(self) -> bool:
        """Check if the current page is an auth-wall, captcha, or rate-limit page."""
        current_url = self.page.url
        if "checkpoint" in current_url or "challenge" in current_url:
            logger.error("🛑 CAPTCHA / Security Checkpoint detected!")
            logger.warning("   Please complete the challenge manually in the browser window.")
            await asyncio.sleep(60)
            return True
            
        # Check for authwall
        content = await self.page.content()
        if "authwall" in current_url or "Sign in to LinkedIn" in content:
            logger.error("🛑 LinkedIn triggered an auth-wall (forced login screen). Session compromised.")
            return True
            
        return False

    # ─────────────────────────────────────────────
    #  AUTHENTICATION
    # ─────────────────────────────────────────────

    async def login(self) -> bool:
        """Log in to LinkedIn with human-like behavior."""
        logger.info("🔐 Attempting LinkedIn login...")

        try:
            await self.page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await human_delay(3, 5)

            # Already logged in?
            if any(x in self.page.url for x in ["feed", "mynetwork", "jobs"]):
                logger.info("✅ Already logged in.")
                return True

            # Type email
            email_input = await self.page.wait_for_selector("#username", timeout=20000)
            await email_input.click()
            await human_delay(0.5, 1.5)
            await self._type_like_human(email_input, self.config.LINKEDIN_EMAIL)
            await human_delay(0.8, 1.5)

            # Type password
            password_input = await self.page.wait_for_selector("#password")
            await password_input.click()
            await human_delay(0.3, 0.8)
            await self._type_like_human(password_input, self.config.LINKEDIN_PASSWORD)
            await human_delay(0.5, 1.5)

            # Submit
            await self.page.click('button[type="submit"]')

            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass

            await human_delay(4, 7)
            current_url = self.page.url
            logger.info(f"   Post-login URL: {current_url}")

            # Handle CAPTCHA / checkpoint
            if "checkpoint" in current_url or "challenge" in current_url:
                logger.warning("⚠️  Verification required! Complete it in the browser window.")
                logger.warning("    You have 60 seconds...")
                await asyncio.sleep(60)
                current_url = self.page.url

            # Handle 2FA
            if "verification" in current_url:
                logger.warning("⚠️  2FA required. Complete in browser. Waiting 60s...")
                await asyncio.sleep(60)
                current_url = self.page.url

            # Success check
            if any(x in current_url for x in ["feed", "mynetwork", "jobs", "messaging"]):
                logger.info("✅ Login successful!")
                return True

            if current_url in ["https://www.linkedin.com/", "https://linkedin.com/"]:
                await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
                await human_delay(2, 4)
                if "feed" in self.page.url:
                    logger.info("✅ Login successful!")
                    return True

            logger.error(f"❌ Login failed. URL: {self.page.url}")
            logger.error("   Double-check LINKEDIN_EMAIL and LINKEDIN_PASSWORD in config.py")
            return False

        except Exception as e:
            logger.error(f"❌ Login error: {e}")
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
                self.daily_searches += 1
                
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
        import re
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
            
        encoded_query = search_query.replace(" ", "%20").replace('"', '%22')
        geo_encoded = f'%5B%22{self.config.GEO_URN}%22%5D'

        search_url = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={encoded_query}"
            f"&origin=GLOBAL_SEARCH_HEADER"
            f"&geoUrn={geo_encoded}"
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
                self.daily_searches += 1
                
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
        encoded_query = search_query.replace(" ", "%20").replace('"', '%22')
        geo_encoded = f'%5B%22{self.config.GEO_URN}%22%5D'

        search_url = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={encoded_query}"
            f"&origin=GLOBAL_SEARCH_HEADER"
            f"&geoUrn={geo_encoded}"
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
                self.daily_searches += 1
                
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
            self.failed_urls.append(profile_url)
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
        text = soup.get_text()
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
        filtered = [e for e in emails if "linkedin.com" not in e and "sentry.io" not in e]
        return filtered[0] if filtered else ""

    def _extract_phone(self, soup) -> str:
        text = soup.get_text()
        phones = re.findall(r'(\+?1?\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})', text)
        return phones[0][1] if phones else ""

    def _extract_connections(self, soup) -> str:
        el = soup.select_one(".pv-top-card--list.pv-top-card--list-bullet .t-black--light")
        return el.get_text(strip=True) if el else ""

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

    async def run(self, input_list: list):
        """
        Main loop:
          If mode="people": search for titles at given companies
          If mode="jobs": search for job listings from input keywords
          If mode="candidates": search for candidates from input skills
        """
        setup_logging(self.config.LOG_FILE)
        
        mode = self.config.SEARCH_MODE.lower()

        logger.info("=" * 65)
        logger.info(f"  LinkedIn Scraper v3.0 — Mode: {mode.upper()}")
        logger.info(f"  Location   : {self.config.SEARCH_LOCATION_NAME}")
        logger.info("=" * 65)

        saved = load_progress(self.config.PROGRESS_FILE)
        self.results = saved.get("results", [])
        completed_keys = set(saved.get("completed_keys", []))

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
                            logger.info(f"      Found {len(profile_urls)} profiles to scrape...")
                            for j, url in enumerate(profile_urls, 1):
                                logger.info(f"      [{j}/{url}] Scraping...")
                                profile = await self.scrape_profile(url, search_company=company, job_title=job_title)
                                if profile:
                                    self.results.append(profile)

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
                        logger.info(f"      Found {len(profile_urls)} candidates to scrape...")
                        for j, url in enumerate(profile_urls, 1):
                            logger.info(f"      [{j}/{len(profile_urls)}] Scraping...")
                            # we pass search_company="N/A" because this is pure skill search
                            profile = await self.scrape_profile(url, search_company="N/A", job_title=skill)
                            if profile:
                                profile['search_skill'] = skill
                                self.results.append(profile)

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