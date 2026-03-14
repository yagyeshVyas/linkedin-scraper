"""
LinkedIn People Scraper — with Proxy Support
=============================================
Drop-in replacement for linkedin_scraper.py
Automatically rotates free proxies every hour.
"""

import asyncio
import random
import re
import logging
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from config import Config
from utils import human_delay, random_scroll, save_progress, load_progress, setup_logging, export_to_excel
from proxy_manager import ProxyManager

logger = logging.getLogger(__name__)


class LinkedInScraperWithProxy:
    """LinkedIn scraper with automatic free proxy rotation."""

    def __init__(self, config: Config):
        self.config = config
        self.results = []
        self.failed_urls = []
        self.session_count = 0
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.proxy_manager = ProxyManager()
        self.current_proxy = None

    # ─────────────────────────────────────────────
    #  BROWSER — launches with proxy
    # ─────────────────────────────────────────────

    async def _launch_browser(self, proxy: Optional[str] = None):
        """Launch browser, optionally with a proxy."""
        if self.browser:
            await self.browser.close()

        if not self.playwright:
            self.playwright = await async_playwright().start()

        launch_args = {
            "headless": self.config.HEADLESS,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--start-maximized",
            ]
        }

        # Add proxy if provided
        if proxy:
            launch_args["proxy"] = {
                "server": f"http://{proxy}"
            }
            logger.info(f"🌐 Using proxy: {proxy}")
        else:
            logger.info("🌐 No proxy — using direct connection")

        self.browser = await self.playwright.chromium.launch(**launch_args)

        self.context = await self.browser.new_context(
            user_agent=random.choice(self.config.USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        # Stealth JS
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        self.page = await self.context.new_page()
        await self.page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}",
            lambda route: route.abort()
        )
        self.current_proxy = proxy

    async def _rotate_proxy(self):
        """Get a new proxy and relaunch browser with it."""
        old_proxy = self.current_proxy
        new_proxy = self.proxy_manager.get_proxy()

        if old_proxy:
            self.proxy_manager.mark_failed(old_proxy)

        logger.info(f"🔄 Rotating proxy: {old_proxy} → {new_proxy}")
        await self._launch_browser(proxy=new_proxy)

        # Re-login after proxy switch
        return await self.login()

    # ─────────────────────────────────────────────
    #  LOGIN
    # ─────────────────────────────────────────────

    async def login(self) -> bool:
        """Log in to LinkedIn."""
        logger.info("🔐 Logging in...")
        try:
            await self.page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await human_delay(3, 5)

            if any(x in self.page.url for x in ["feed", "mynetwork", "jobs"]):
                logger.info("✅ Already logged in.")
                return True

            email_input = await self.page.wait_for_selector("#username", timeout=20000)
            await email_input.click()
            await human_delay(0.5, 1.5)
            for char in self.config.LINKEDIN_EMAIL:
                await email_input.type(char, delay=random.randint(50, 200))

            await human_delay(0.8, 1.5)
            password_input = await self.page.wait_for_selector("#password")
            await password_input.click()
            await human_delay(0.3, 0.8)
            for char in self.config.LINKEDIN_PASSWORD:
                await password_input.type(char, delay=random.randint(50, 200))

            await human_delay(0.5, 1.5)
            await self.page.click('button[type="submit"]')

            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass

            await human_delay(4, 7)
            current_url = self.page.url

            if "checkpoint" in current_url or "challenge" in current_url:
                logger.warning("⚠️  Verification required! Complete in browser. 60s...")
                await asyncio.sleep(60)
                current_url = self.page.url

            if any(x in current_url for x in ["feed", "mynetwork", "jobs", "messaging"]):
                logger.info("✅ Login successful!")
                return True

            logger.error(f"❌ Login failed. URL: {self.page.url}")
            return False

        except Exception as e:
            logger.error(f"❌ Login error: {e}")
            return False

    # ─────────────────────────────────────────────
    #  SEARCH
    # ─────────────────────────────────────────────

    async def search_people(self, company: str, job_title: str) -> list:
        """Search for people with a job title at a company."""
        profile_urls = []
        page_num = 1

        search_query = f'"{job_title}" "{company}"'
        encoded_query = search_query.replace(" ", "%20").replace('"', '%22')
        geo_encoded = f'%5B%22{self.config.GEO_URN}%22%5D'
        search_url = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={encoded_query}&origin=GLOBAL_SEARCH_HEADER&geoUrn={geo_encoded}"
        )

        logger.info(f"   🔍 '{job_title}' at '{company}'")

        while page_num <= self.config.MAX_PAGES_PER_COMPANY:
            try:
                url = search_url if page_num == 1 else f"{search_url}&page={page_num}"
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await human_delay(3, 6)
                await random_scroll(self.page)

                content = await self.page.content()
                if "No results found" in content:
                    break

                links = await self.page.eval_on_selector_all(
                    "a[href*='/in/']",
                    "elements => elements.map(el => el.href)"
                )
                new_urls = list(set([
                    re.sub(r'\?.*', '', l).rstrip('/')
                    for l in links if "/in/" in l
                ]))

                if not new_urls:
                    break

                profile_urls.extend(new_urls)
                logger.info(f"      Page {page_num}: +{len(new_urls)} profiles")
                page_num += 1
                await human_delay(4, 9)

                self.session_count += 1
                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                    await self._session_break()

            except Exception as e:
                logger.error(f"      Search error: {e}")
                # Try rotating proxy on error
                if "net::" in str(e) or "timeout" in str(e).lower():
                    logger.info("🔄 Network error — rotating proxy...")
                    await self._rotate_proxy()
                break

        return list(set(profile_urls))

    # ─────────────────────────────────────────────
    #  PROFILE SCRAPING
    # ─────────────────────────────────────────────

    async def scrape_profile(self, url: str, company: str, title: str) -> Optional[dict]:
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await human_delay(3, 7)
            await random_scroll(self.page, scrolls=3)

            soup = BeautifulSoup(await self.page.content(), "html.parser")

            def get_text(selectors):
                for sel in selectors:
                    el = soup.select_one(sel)
                    if el:
                        return el.get_text(strip=True)
                return ""

            headline = get_text([".text-body-medium.break-words"])
            title_text = re.split(r"\s+at\s+|\s*\|\s*", headline)[0].strip() if headline else ""

            text = soup.get_text()
            emails = [e for e in re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
                      if "linkedin.com" not in e]

            profile = {
                "name":           get_text(["h1.text-heading-xlarge", "h1"]),
                "title":          title_text,
                "headline":       headline,
                "location":       get_text([".text-body-small.inline.t-black--light.break-words"]),
                "email":          emails[0] if emails else "",
                "linkedin_url":   url,
                "search_company": company,
                "searched_title": title,
                "proxy_used":     self.current_proxy or "direct",
                "scraped_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            # Filter check
            if self.config.FILTER_KEYWORDS:
                text_check = (profile["title"] + " " + profile["headline"]).lower()
                if not any(kw.lower() in text_check for kw in self.config.FILTER_KEYWORDS):
                    return None

            logger.info(f"   ✅ {profile['name']} | {profile['title']}")
            return profile

        except Exception as e:
            logger.error(f"   ❌ {url}: {e}")
            self.failed_urls.append(url)
            return None

    # ─────────────────────────────────────────────
    #  SESSION BREAK
    # ─────────────────────────────────────────────

    async def _session_break(self):
        t = random.randint(self.config.MIN_SESSION_BREAK_SECONDS, self.config.MAX_SESSION_BREAK_SECONDS)
        logger.info(f"☕ Break: {t}s...")

        # Every 3 breaks, rotate proxy too
        if self.session_count % (self.config.BREAK_EVERY_N_REQUESTS * 3) == 0:
            logger.info("🔄 Rotating proxy during break...")
            await self._rotate_proxy()
        else:
            await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await asyncio.sleep(t / 2)
            await random_scroll(self.page, scrolls=3)
            await asyncio.sleep(t / 2)

    # ─────────────────────────────────────────────
    #  MAIN RUN
    # ─────────────────────────────────────────────

    async def run(self, companies: list):
        setup_logging(self.config.LOG_FILE)
        logger.info("=" * 65)
        logger.info("  LinkedIn People Scraper — WITH PROXY ROTATION")
        logger.info(f"  Titles   : {', '.join(self.config.JOB_TITLES)}")
        logger.info(f"  Location : {self.config.SEARCH_LOCATION_NAME}")
        logger.info(f"  Companies: {len(companies)}")
        logger.info("=" * 65)

        # Fetch proxies first
        logger.info("\n📡 Setting up proxies...")
        self.proxy_manager.refresh()
        self.proxy_manager.status()

        first_proxy = self.proxy_manager.get_proxy()

        saved = load_progress(self.config.PROGRESS_FILE)
        self.results = saved.get("results", [])
        completed_keys = set(saved.get("completed_keys", []))

        await self._launch_browser(proxy=first_proxy)

        try:
            if not await self.login():
                logger.error("❌ Login failed.")
                return

            total = len(companies)
            for i, company in enumerate(companies, 1):
                logger.info(f"\n[{i}/{total}] 🏢 {company}")

                for job_title in self.config.JOB_TITLES:
                    key = f"{company}::{job_title}"
                    if key in completed_keys:
                        logger.info(f"   ⏭️  Skipping: {job_title}")
                        continue

                    urls = await self.search_people(company, job_title)

                    if not urls:
                        logger.info("      No results.")
                    else:
                        for j, url in enumerate(urls, 1):
                            logger.info(f"      [{j}/{len(urls)}] Scraping...")
                            profile = await self.scrape_profile(url, company, job_title)
                            if profile:
                                self.results.append(profile)

                            await human_delay(
                                self.config.MIN_DELAY_BETWEEN_PROFILES,
                                self.config.MAX_DELAY_BETWEEN_PROFILES
                            )
                            self.session_count += 1
                            if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                                await self._session_break()

                    completed_keys.add(key)
                    save_progress(self.config.PROGRESS_FILE, {
                        "results": self.results,
                        "completed_keys": list(completed_keys)
                    })
                    await human_delay(4, 10)

        except KeyboardInterrupt:
            logger.info("\n⚠️  Interrupted. Saving...")

        finally:
            if self.results:
                path = export_to_excel(self.results, self.config.OUTPUT_FILE)
                logger.info(f"\n🎉 Exported {len(self.results)} profiles → {path}")
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()

            logger.info(f"\n📊 Summary:")
            logger.info(f"   Profiles : {len(self.results)}")
            logger.info(f"   Failed   : {len(self.failed_urls)}")