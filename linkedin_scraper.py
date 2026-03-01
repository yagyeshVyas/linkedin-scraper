"""
LinkedIn Recruiter Scraper
==========================
A production-grade LinkedIn scraper for finding recruiters at top companies.
Uses Playwright for browser automation with stealth techniques.

DISCLAIMER: This tool is for educational purposes. Scraping LinkedIn may violate
their Terms of Service. Use responsibly and consider using LinkedIn's official API
or authorized data providers for commercial use.
"""

import asyncio
import json
import random
import time
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext
from bs4 import BeautifulSoup
import pandas as pd

from config import Config
from utils import (
    human_delay, random_scroll, save_progress,
    load_progress, setup_logging, export_to_excel
)


logger = logging.getLogger(__name__)


class LinkedInScraper:
    """
    A stealth LinkedIn scraper that mimics human behavior to extract
    recruiter profiles from top companies.
    """

    def __init__(self, config: Config):
        self.config = config
        self.results = []
        self.failed_urls = []
        self.session_count = 0
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

    # ─────────────────────────────────────────────
    #  BROWSER SETUP
    # ─────────────────────────────────────────────

    async def _launch_browser(self):
        """Launch a stealth browser instance."""
        logger.info("🚀 Launching stealth browser...")

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.config.HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-plugins-discovery",
                "--start-maximized",
            ]
        )

        # Use a persistent context so cookies/session survive restarts
        user_data_dir = Path(self.config.SESSION_DIR)
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self.context = await self.browser.new_context(
            user_agent=random.choice(self.config.USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation"],
            geolocation={"longitude": -73.9857, "latitude": 40.7484},  # NYC
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            }
        )

        # Inject stealth scripts to avoid detection
        await self.context.add_init_script("""
            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });

            // Override chrome
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // Override plugins length
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
        """)

        self.page = await self.context.new_page()

        # Block unnecessary resources to speed things up
        await self.page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf}", lambda route: route.abort())

        logger.info("✅ Browser launched successfully.")

    # ─────────────────────────────────────────────
    #  AUTHENTICATION
    # ─────────────────────────────────────────────

    async def login(self) -> bool:
        """
        Log in to LinkedIn with human-like behavior.
        Returns True if login was successful.
        """
        logger.info("🔐 Attempting LinkedIn login...")

        try:
            await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            await human_delay(2, 4)

            # Check if already logged in
            if "feed" in self.page.url or "mynetwork" in self.page.url:
                logger.info("✅ Already logged in (session restored).")
                return True

            # Fill email
            email_input = await self.page.wait_for_selector("#username", timeout=15000)
            await email_input.click()
            await human_delay(0.5, 1.5)
            await self._type_like_human(email_input, self.config.LINKEDIN_EMAIL)

            await human_delay(0.8, 1.5)

            # Fill password
            password_input = await self.page.wait_for_selector("#password", timeout=10000)
            await password_input.click()
            await human_delay(0.3, 0.8)
            await self._type_like_human(password_input, self.config.LINKEDIN_PASSWORD)

            await human_delay(0.5, 1.5)

            # Click sign in - then wait for navigation (NOT networkidle; LinkedIn never reaches it)
            await self.page.click('button[type="submit"]')
            logger.info("   Waiting for LinkedIn to respond after login...")

            # Poll the URL for up to 30 seconds to detect a successful redirect
            for _ in range(30):
                await asyncio.sleep(1)
                url = self.page.url
                if "feed" in url or "mynetwork" in url:
                    break
                if "checkpoint" in url or "challenge" in url:
                    logger.warning("⚠️  LinkedIn requires verification (CAPTCHA/2FA).")
                    logger.warning("   Please complete the verification in the browser window.")
                    logger.warning("   You have 60 seconds...")
                    # Wait up to 60 seconds for user to complete verification
                    for _ in range(60):
                        await asyncio.sleep(1)
                        if "feed" in self.page.url or "mynetwork" in self.page.url:
                            break
                    break

            await human_delay(1, 2)

            # Final check
            final_url = self.page.url
            if "feed" in final_url or "mynetwork" in final_url:
                logger.info("✅ Login successful!")
                return True
            elif "login" in final_url or "authwall" in final_url:
                logger.error("❌ Login failed — still on login page. Check your credentials in config.py")
                return False
            else:
                logger.warning(f"⚠️  Unexpected URL after login: {final_url}")
                logger.warning("   Attempting to continue anyway...")
                return True  # May still work (e.g. home page redirect)

        except Exception as e:
            logger.error(f"❌ Login error: {e}")
            return False

    async def _type_like_human(self, element, text: str):
        """Type text character by character with random delays to mimic human input."""
        for char in text:
            await element.type(char, delay=random.randint(50, 200))

    # ─────────────────────────────────────────────
    #  SEARCH
    # ─────────────────────────────────────────────

    async def search_recruiters(self, company: str, company_url: str = None) -> list:
        """
        Search for recruiters at a specific company.
        Returns list of profile URLs found.
        """
        profile_urls = []
        page_num = 1

        # Build search URL - People search filtered by company and title
        search_query = f'recruiter "{company}"'
        encoded_query = search_query.replace(" ", "%20").replace('"', '%22')

        search_url = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={encoded_query}"
            f"&origin=GLOBAL_SEARCH_HEADER"
            f"&geoUrn=%5B%22103644278%22%5D"  # United States geo URN
        )

        logger.info(f"🔍 Searching recruiters at: {company}")

        while page_num <= self.config.MAX_PAGES_PER_COMPANY:
            try:
                url = search_url if page_num == 1 else f"{search_url}&page={page_num}"
                await self.page.goto(url, wait_until="domcontentloaded")
                await human_delay(3, 6)
                await random_scroll(self.page)

                # Check for "No results" message
                content = await self.page.content()
                if "No results found" in content or "no results" in content.lower():
                    logger.info(f"   No more results at page {page_num} for {company}")
                    break

                # Extract profile links from search results
                new_urls = await self._extract_profile_urls_from_search()

                if not new_urls:
                    logger.info(f"   No profiles found on page {page_num}. Stopping.")
                    break

                profile_urls.extend(new_urls)
                logger.info(f"   Page {page_num}: Found {len(new_urls)} profiles (Total: {len(profile_urls)})")

                page_num += 1

                # Random delay between pages
                await human_delay(4, 9)

                # Session break every N pages to avoid detection
                self.session_count += 1
                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                    await self._take_session_break()

            except Exception as e:
                logger.error(f"   Error on page {page_num} for {company}: {e}")
                break

        return list(set(profile_urls))  # Deduplicate

    async def _extract_profile_urls_from_search(self) -> list:
        """Extract LinkedIn profile URLs from search results page."""
        urls = []
        try:
            soup = BeautifulSoup(await self.page.content(), "html.parser")

            # LinkedIn search result profile links
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if "/in/" in href and "linkedin.com" in href:
                    # Clean URL - remove query params
                    clean_url = re.sub(r'\?.*', '', href)
                    if clean_url not in urls:
                        urls.append(clean_url)

            # Also try extracting from JS data attributes
            links = await self.page.eval_on_selector_all(
                "a[href*='/in/']",
                "elements => elements.map(el => el.href)"
            )
            for link in links:
                clean = re.sub(r'\?.*', '', link)
                if clean not in urls and "/in/" in clean:
                    urls.append(clean)

        except Exception as e:
            logger.warning(f"   URL extraction warning: {e}")

        return urls

    # ─────────────────────────────────────────────
    #  PROFILE SCRAPING
    # ─────────────────────────────────────────────

    async def scrape_profile(self, profile_url: str) -> Optional[dict]:
        """
        Scrape a single LinkedIn profile and extract recruiter data.
        Returns a dict with profile information or None if failed.
        """
        try:
            await self.page.goto(profile_url, wait_until="domcontentloaded")
            await human_delay(3, 7)
            await random_scroll(self.page, scrolls=3)

            soup = BeautifulSoup(await self.page.content(), "html.parser")

            profile = {
                "name": self._extract_name(soup),
                "headline": self._extract_headline(soup),
                "location": self._extract_location(soup),
                "company": self._extract_current_company(soup),
                "title": self._extract_current_title(soup),
                "linkedin_url": profile_url,
                "email": self._extract_email(soup),
                "phone": self._extract_phone(soup),
                "connections": self._extract_connections(soup),
                "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            # Only include if it looks like a recruiter
            if self._is_recruiter(profile):
                logger.info(f"   ✅ Scraped: {profile['name']} | {profile['title']} | {profile['company']}")
                return profile
            else:
                logger.debug(f"   ⏭️  Skipped (not a recruiter): {profile['name']}")
                return None

        except Exception as e:
            logger.error(f"   ❌ Failed to scrape {profile_url}: {e}")
            self.failed_urls.append(profile_url)
            return None

    def _extract_name(self, soup: BeautifulSoup) -> str:
        selectors = [
            "h1.text-heading-xlarge",
            "h1.inline.t-24",
            ".pv-top-card--list li:first-child",
            "h1"
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return "Unknown"

    def _extract_headline(self, soup: BeautifulSoup) -> str:
        selectors = [
            ".text-body-medium.break-words",
            ".pv-top-card--experience-list-item",
            "[data-generated-suggestion-target]"
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return ""

    def _extract_location(self, soup: BeautifulSoup) -> str:
        selectors = [
            ".text-body-small.inline.t-black--light.break-words",
            ".pv-top-card--list.pv-top-card--list-bullet li"
        ]
        for sel in selectors:
            els = soup.select(sel)
            for el in els:
                text = el.get_text(strip=True)
                # Location typically has comma (City, State)
                if "," in text and len(text) < 60:
                    return text
        return ""

    def _extract_current_company(self, soup: BeautifulSoup) -> str:
        # Try experience section
        exp_section = soup.find("section", {"id": "experience"})
        if exp_section:
            company_el = exp_section.select_one(".pv-entity__secondary-title, .t-14.t-normal")
            if company_el:
                return company_el.get_text(strip=True)

        # Try top card
        selectors = [".pv-top-card--experience-list .pv-top-card--experience-list-item"]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return ""

    def _extract_current_title(self, soup: BeautifulSoup) -> str:
        headline = self._extract_headline(soup)
        if headline:
            # Title is usually the first part before | or at
            parts = re.split(r"\s+at\s+|\s*\|\s*", headline)
            return parts[0].strip()
        return ""

    def _extract_email(self, soup: BeautifulSoup) -> str:
        """Try to find email from contact info section."""
        # Email regex search in page source
        text = soup.get_text()
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, text)
        # Filter out LinkedIn's own emails
        filtered = [e for e in emails if "linkedin.com" not in e and "sentry.io" not in e]
        return filtered[0] if filtered else ""

    def _extract_phone(self, soup: BeautifulSoup) -> str:
        text = soup.get_text()
        phone_pattern = r'(\+?1?\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})'
        phones = re.findall(phone_pattern, text)
        return phones[0][1] if phones else ""

    def _extract_connections(self, soup: BeautifulSoup) -> str:
        el = soup.select_one(".pv-top-card--list.pv-top-card--list-bullet .t-black--light")
        if el:
            return el.get_text(strip=True)
        return ""

    def _is_recruiter(self, profile: dict) -> bool:
        """Check if the profile belongs to a recruiter based on keywords."""
        recruiter_keywords = [
            "recruiter", "recruiting", "talent acquisition",
            "talent sourcer", "sourcer", "hr", "human resources",
            "staffing", "headhunter", "talent partner",
            "people operations", "technical recruiter",
            "campus recruiter", "executive recruiter"
        ]
        text = (
            (profile.get("title") or "") + " " +
            (profile.get("headline") or "")
        ).lower()

        return any(kw in text for kw in recruiter_keywords)

    # ─────────────────────────────────────────────
    #  SESSION MANAGEMENT
    # ─────────────────────────────────────────────

    async def _take_session_break(self):
        """Take a randomized break to avoid detection."""
        break_time = random.randint(
            self.config.MIN_SESSION_BREAK_SECONDS,
            self.config.MAX_SESSION_BREAK_SECONDS
        )
        logger.info(f"☕ Taking a {break_time}s session break to avoid detection...")

        # Visit LinkedIn homepage during break (natural behavior)
        await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        await asyncio.sleep(break_time / 2)
        await random_scroll(self.page, scrolls=random.randint(2, 5))
        await asyncio.sleep(break_time / 2)

    # ─────────────────────────────────────────────
    #  MAIN ORCHESTRATION
    # ─────────────────────────────────────────────

    async def run(self, companies: list):
        """
        Main scraping orchestration.
        Iterates over all companies, finds recruiters, and exports to Excel.
        """
        setup_logging(self.config.LOG_FILE)
        logger.info("=" * 60)
        logger.info("  LinkedIn Recruiter Scraper - Starting")
        logger.info(f"  Companies to scrape: {len(companies)}")
        logger.info("=" * 60)

        # Load any previously saved progress
        saved = load_progress(self.config.PROGRESS_FILE)
        self.results = saved.get("results", [])
        completed_companies = set(saved.get("completed_companies", []))

        await self._launch_browser()

        try:
            logged_in = await self.login()
            if not logged_in:
                logger.error("❌ Could not log in. Exiting.")
                return

            for i, company in enumerate(companies, 1):
                if company in completed_companies:
                    logger.info(f"[{i}/{len(companies)}] ⏭️  Skipping (already done): {company}")
                    continue

                logger.info(f"\n[{i}/{len(companies)}] 🏢 Processing: {company}")

                # Search for recruiter profile URLs at this company
                profile_urls = await self.search_recruiters(company)

                if not profile_urls:
                    logger.info(f"   No recruiter profiles found for {company}")
                else:
                    logger.info(f"   Found {len(profile_urls)} profiles to scrape...")

                    # Scrape each profile
                    for j, url in enumerate(profile_urls, 1):
                        logger.info(f"   [{j}/{len(profile_urls)}] Scraping profile...")
                        profile = await self.scrape_profile(url)
                        if profile:
                            profile["search_company"] = company
                            self.results.append(profile)

                        await human_delay(
                            self.config.MIN_DELAY_BETWEEN_PROFILES,
                            self.config.MAX_DELAY_BETWEEN_PROFILES
                        )

                        self.session_count += 1
                        if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                            await self._take_session_break()

                completed_companies.add(company)

                # Save progress after each company
                save_progress(self.config.PROGRESS_FILE, {
                    "results": self.results,
                    "completed_companies": list(completed_companies)
                })

                # Delay between companies
                await human_delay(5, 12)

        except KeyboardInterrupt:
            logger.info("\n⚠️  Interrupted by user. Saving progress...")

        finally:
            # Export results to Excel
            if self.results:
                output_path = export_to_excel(self.results, self.config.OUTPUT_FILE)
                logger.info(f"\n🎉 Done! Exported {len(self.results)} recruiters to: {output_path}")
            else:
                logger.info("\n⚠️  No results to export.")

            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()

            logger.info(f"📊 Summary:")
            logger.info(f"   Total recruiters found: {len(self.results)}")
            logger.info(f"   Failed URLs: {len(self.failed_urls)}")
            logger.info(f"   Companies completed: {len(completed_companies)}/{len(companies)}")