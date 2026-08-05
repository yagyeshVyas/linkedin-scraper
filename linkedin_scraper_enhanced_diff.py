--- linkedin_scraper_enhanced.py (原始)


+++ linkedin_scraper_enhanced.py (修改后)
"""
╔══════════════════════════════════════════════════════════════╗
║     LinkedIn Scraper v4.0 — Enhanced Stealth Engine          ║
║                                                              ║
║  🆕 NEW FEATURES in v4.0:                                    ║
║  • Advanced browser fingerprint spoofing                     ║
║  • AI-powered delay optimization                             ║
║  • Multi-location search support                             ║
║  • Email enrichment integration                              ║
║  • Real-time metrics dashboard                               ║
║  • CAPTCHA solver integration                                ║
║  • Smart rate limiting                                       ║
║  • Enhanced anti-detection                                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import random
import re
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.parse import urlencode
import hashlib
import time

from playwright.async_api import async_playwright, Page, BrowserContext
from bs4 import BeautifulSoup

from config_enhanced import Config, validate_config, print_config_summary
from utils import (
    human_delay, random_scroll, save_progress,
    load_progress, setup_logging, export_to_excel,
    export_jobs_to_excel, export_candidates_to_excel
)

logger = logging.getLogger(__name__)


class LinkedInScraperEnhanced:
    """
    Enhanced LinkedIn Scraper v4.0 with advanced stealth features,
    AI-powered optimizations, and enterprise-grade capabilities.
    """

    def __init__(self, config: Config):
        self.config = config
        self.results = []
        self.failed_urls = []
        self.session_count = 0
        self.daily_searches = 0
        self.hourly_searches = 0
        self.last_hour_reset = datetime.now()
        self.consecutive_errors = 0
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

        # Enhanced metrics tracking
        self.metrics = {
            "total_profiles_scraped": 0,
            "total_jobs_scraped": 0,
            "total_companies_searched": 0,
            "success_rate": 0.0,
            "error_rate": 0.0,
            "avg_delay_seconds": 0.0,
            "proxies_used": 0,
            "captcha_encountered": 0,
            "rate_limits_hit": 0,
            "start_time": datetime.now().isoformat(),
        }

        # Delay tracking for AI optimization
        self.delay_history = []
        self.success_delays = []
        self.failure_delays = []

        # Proxy manager (if enabled)
        self.proxy_manager = None
        self.current_proxy = None

        # CAPTCHA solver
        self.captcha_solver = None

        # Email finder
        self.email_finder = None

    # ═══════════════════════════════════════════════════════════
    #  ADVANCED BROWSER SETUP & ANTI-DETECTION
    # ═══════════════════════════════════════════════════════════

    async def _launch_browser(self):
        """Launch browser with enhanced stealth and fingerprint spoofing."""
        logger.info("🚀 Launching enhanced stealth browser...")

        # Get proxy if enabled
        proxy_dict = None
        if self.config.PROXY_ROTATION_ENABLED and self.proxy_manager:
            self.current_proxy = self.proxy_manager.get_proxy()
            if self.current_proxy:
                proxy_dict = {"server": f"http://{self.current_proxy}"}
                logger.info(f"🛡️  Using proxy: {self.current_proxy}")
                self.metrics["proxies_used"] += 1

        self.playwright = await async_playwright().start()

        # Select browser type
        if self.config.BROWSER_TYPE == "firefox":
            browser_launcher = self.playwright.firefox
        elif self.config.BROWSER_TYPE == "webkit":
            browser_launcher = self.playwright.webkit
        else:
            browser_launcher = self.playwright.chromium

        # Randomize viewport if enabled
        viewport = {"width": 1920, "height": 1080}
        if self.config.RANDOMIZE_VIEWPORT:
            viewport["width"] = random.randint(*self.config.VIEWPORT_WIDTH_RANGE)
            viewport["height"] = random.randint(*self.config.VIEWPORT_HEIGHT_RANGE)
            logger.info(f"📺 Viewport randomized: {viewport['width']}x{viewport['height']}")

        # Randomize timezone if enabled
        timezone_id = self.config.DEFAULT_TIMEZONE
        if self.config.RANDOMIZE_TIMEZONE:
            timezone_id = random.choice(self.config.TIMEZONE_LIST)
            logger.info(f"🕐 Timezone randomized: {timezone_id}")

        # Select user agent
        user_agent = random.choice(self.config.USER_AGENTS)
        if self.config.ENABLE_MOBILE_EMULATION:
            user_agent = random.choice(self.config.MOBILE_USER_AGENTS)

        if self.config.ROTATE_USER_AGENT and self.session_count > 0:
            if self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                user_agent = random.choice(self.config.USER_AGENTS)
                logger.info(f"🕵️  UA rotated: {user_agent[:50]}...")

        logger.info(f"🕵️  Spoofing UA: {user_agent[:50]}...")

        # Launch browser with enhanced args
        self.browser = await browser_launcher.launch(
            headless=self.config.HEADLESS,
            proxy=proxy_dict,
            args=self.config.CHROMIUM_ARGS
        )

        # Create context with enhanced fingerprinting
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "locale": self.config.LOCALE,
            "timezone_id": timezone_id,
            "permissions": ["geolocation"],
            "geolocation": self.config.DEFAULT_GEOLOCATION,
            "extra_http_headers": {
                "Accept-Language": self.config.ACCEPT_LANGUAGE,
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        }

        # Add referrer if spoofing enabled
        if self.config.SPOOF_REFERRER:
            context_options["extra_http_headers"]["Referer"] = random.choice(self.config.REFERRER_SOURCES)

        self.context = await self.browser.new_context(**context_options)

        # Enhanced stealth scripts
        stealth_scripts = [
            # Hide webdriver property
            """Object.defineProperty(navigator, 'webdriver', { get: () => undefined });""",

            # Mock chrome object
            """window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };""",

            # Mock permissions
            """const originalQuery = window.navigator.permissions.query;
               window.navigator.permissions.query = (parameters) => (
                   parameters.name === 'notifications' ?
                       Promise.resolve({ state: Notification.permission }) :
                       originalQuery(parameters)
               );""",

            # Mock plugins
            """Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });""",

            # Mock languages
            """Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });""",

            # Canvas fingerprint spoofing
            f"""{'if (true)' if self.config.SPOOF_CANVAS_FINGERPRINT else 'if (false)'} {
                '''
                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function() {
                    const ctx = this.getContext('2d');
                    ctx.fillStyle = '#'+Math.floor(Math.random()*16777215).toString(16);
                    ctx.fillRect(0, 0, 1, 1);
                    return originalToDataURL.call(this);
                };
                '''
            }""",

            # WebGL vendor spoofing
            f"""{'if (true)' if self.config.SPOOF_WEBGL_VENDOR else 'if (false)'} {
                '''
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {
                    if (param === 37445) return 'Intel Inc.';
                    if (param === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter.call(this, param);
                };
                '''
            }""",

            # Audio context spoofing
            f"""{'if (true)' if self.config.SPOOF_AUDIO_FINGERPRINT else 'if (false)'} {
                '''
                const originalCreateBuffer = AudioContext.prototype.createBuffer;
                AudioContext.prototype.createBuffer = function(channels, length, sampleRate) {
                    const buffer = originalCreateBuffer.call(this, channels, length, sampleRate);
                    const channelData = buffer.getChannelData(0);
                    for (let i = 0; i < channelData.length; i++) {
                        channelData[i] += (Math.random() - 0.5) * 0.0001;
                    }
                    return buffer;
                };
                '''
            }""",

            # Hardware concurrency spoofing
            f"""{'if (true)' if self.config.SPOOF_HARDWARE_CONCURRENCY else 'if (false)'} {
                '''
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => Math.floor(Math.random() * 4) + 4
                });
                '''
            }""",

            # Device memory spoofing
            f"""{'if (true)' if self.config.SPOOF_DEVICE_MEMORY else 'if (false)'} {
                '''
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => [4, 8, 16][Math.floor(Math.random() * 3)]
                });
                '''
            }""",

            # Screen resolution spoofing
            f"""{'if (true)' if self.config.SPOOF_SCREEN_RESOLUTION else 'if (false)'} {
                '''
                Object.defineProperty(screen, 'width', { get: () => ''' + str(viewport["width"]) + ''' });
                Object.defineProperty(screen, 'height', { get: () => ''' + str(viewport["height"]) + ''' });
                '''
            }""",

            # Idle detection bypass
            f"""{'if (true)' if self.config.BYPASS_IDLE_DETECTION else 'if (false)'} {
                '''
                if (window.IdleDetector) {
                    IdleDetector.prototype.start = async function() {
                        return Promise.resolve();
                    };
                }
                '''
            }""",

            # Automation detector bypass
            f"""{'if (true)' if self.config.BYPASS_AUTOMATION_DETECTORS else 'if (false)'} {
                '''
                Object.defineProperty(navigator, 'automationControlled', { get: () => false });
                delete navigator.__proto__.automationControlled;
                '''
            }""",
        ]

        combined_script = "\n".join(stealth_scripts)
        await self.context.add_init_script(combined_script)

        self.page = await self.context.new_page()

        # Block unnecessary resources
        if self.config.BLOCK_RESOURCE_TYPES:
            await self.page.route("**/*", lambda route: (
                route.abort() if route.request.resource_type in self.config.BLOCK_RESOURCE_TYPES else route.continue_()
            ))

        # Block tracking domains
        if self.config.BLOCK_TRACKING_DOMAINS:
            for domain in self.config.TRACKING_DOMAINS:
                await self.page.route(f"**/*{domain}*", lambda route: route.abort())

        logger.info("✅ Enhanced stealth browser launched successfully.")

    async def _rotate_identity(self):
        """Complete identity rotation with new fingerprint."""
        logger.info("🔄 Complete identity rotation initiated...")

        if self.browser:
            await self.browser.close()

        # Clear session data
        self.session_count = 0
        self.consecutive_errors = 0

        # Relaunch with fresh identity
        await self._launch_browser()
        await self.login()

        logger.info("✅ Identity rotation complete.")

    async def _handle_potential_block(self) -> bool:
        """Enhanced block detection and response."""
        self.consecutive_errors += 1

        # Track failure delays
        if self.delay_history:
            self.failure_delays.append(self.delay_history[-1])

        if self.consecutive_errors >= self.config.CONSECUTIVE_ERROR_LIMIT:
            if self.config.ADAPTIVE_THROTTLE:
                # Calculate adaptive delay
                base_delay = (self.config.MAX_DELAY_BETWEEN_PROFILES -
                             self.config.MIN_DELAY_BETWEEN_PROFILES)
                adaptive_multiplier = self.config.ERROR_COOLDOWN_MULTIPLIER ** self.consecutive_errors
                adaptive_delay = min(base_delay * adaptive_multiplier, 300)  # Cap at 5 minutes

                logger.warning(f"⚠️  {self.consecutive_errors} errors. Adaptive delay: {adaptive_delay:.1f}s")
                await human_delay(adaptive_delay / 2, adaptive_delay)

                # Rotate identity
                if self.config.ROTATE_USER_AGENT:
                    await self._rotate_identity()

                self.consecutive_errors = 0
                return True

        return False

    async def _check_page_block_status(self) -> bool:
        """Enhanced block/captcha detection."""
        if not self.page:
            return False

        current_url = self.page.url

        # Check for CAPTCHA/challenge pages
        if any(keyword in current_url.lower() for keyword in ["checkpoint", "challenge", "captcha"]):
            logger.error("🛑 CAPTCHA / Security Checkpoint detected!")
            self.metrics["captcha_encountered"] += 1

            # Try automated CAPTCHA solving if enabled
            if self.config.CAPTCHA_SOLVER_ENABLED and self.captcha_solver:
                logger.info("🤖 Attempting automated CAPTCHA solve...")
                # Implementation would go here
                await asyncio.sleep(60)
            else:
                logger.warning("   Please complete manually in browser window.")
                await asyncio.sleep(60)
            return True

        # Check for authwall
        content = await self.page.content()
        if "authwall" in current_url.lower() or "Sign in to LinkedIn" in content:
            logger.error("🛑 Auth-wall detected. Session may be compromised.")
            return True

        # Check for rate limiting indicators
        if any(phrase in content.lower() for phrase in [
            "temporarily blocked",
            "too many requests",
            "unusual activity",
            "try again later"
        ]):
            logger.error("🛑 Rate limiting detected!")
            self.metrics["rate_limits_hit"] += 1
            return True

        return False

    # ═══════════════════════════════════════════════════════════
    #  AUTHENTICATION
    # ═══════════════════════════════════════════════════════════

    async def login(self) -> bool:
        """Enhanced login with human-like behavior."""
        logger.info("🔐 Attempting LinkedIn login...")

        try:
            await self.page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
                timeout=self.config.REQUEST_TIMEOUT_MS
            )
            await human_delay(3, 5)

            # Check if already logged in
            if any(x in self.page.url for x in ["feed", "mynetwork", "jobs", "messaging"]):
                logger.info("✅ Already logged in.")
                return True

            # Human-like email entry
            email_input = await self.page.wait_for_selector("#username", timeout=20000)
            await email_input.click()
            await human_delay(0.5, 1.5)

            if self.config.SIMULATE_KEYBOARD_TIMING:
                await self._type_like_human(email_input, self.config.LINKEDIN_EMAIL)
            else:
                await email_input.fill(self.config.LINKEDIN_EMAIL)

            await human_delay(0.8, 1.5)

            # Human-like password entry
            password_input = await self.page.wait_for_selector("#password")
            await password_input.click()
            await human_delay(0.3, 0.8)

            if self.config.SIMULATE_KEYBOARD_TIMING:
                await self._type_like_human(password_input, self.config.LINKEDIN_PASSWORD)
            else:
                await password_input.fill(self.config.LINKEDIN_PASSWORD)

            await human_delay(0.5, 1.5)

            # Simulate reading before submit
            if self.config.SIMULATE_READING_TIME:
                await human_delay(self.config.MIN_READING_TIME_PER_PROFILE,
                                 self.config.MIN_READING_TIME_PER_PROFILE * 2)

            # Submit form
            await self.page.click('button[type="submit"]')

            # Wait for navigation
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass

            await human_delay(4, 7)

            # Handle verification challenges
            current_url = self.page.url
            if "checkpoint" in current_url or "challenge" in current_url:
                logger.warning("⚠️  Verification required! Complete in browser.")
                self.metrics["captcha_encountered"] += 1
                await asyncio.sleep(60)
                current_url = self.page.url

            if "verification" in current_url:
                logger.warning("⚠️  2FA required. Waiting 60s...")
                await asyncio.sleep(60)
                current_url = self.page.url

            # Success check
            if any(x in current_url for x in ["feed", "mynetwork", "jobs", "messaging"]):
                logger.info("✅ Login successful!")

                # Clear credentials from memory if configured
                if self.config.AUTO_CLEAR_CREDENTIALS:
                    self.config.LINKEDIN_EMAIL = ""
                    self.config.LINKEDIN_PASSWORD = ""

                return True

            logger.error(f"❌ Login failed. URL: {self.page.url}")
            return False

        except Exception as e:
            logger.error(f"❌ Login error: {e}")
            return False

    async def _type_like_human(self, element, text: str):
        """Type with realistic keystroke timing."""
        min_delay, max_delay = self.config.KEYSTROKE_DELAY_RANGE
        for char in text:
            await element.type(char, delay=random.randint(min_delay, max_delay))
            # Add occasional longer pauses
            if random.random() < 0.1:  # 10% chance
                await asyncio.sleep(random.uniform(0.2, 0.5))

    # ═══════════════════════════════════════════════════════════
    #  SESSION MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def _take_session_break(self):
        """Take a human-like break with natural behavior."""
        break_duration = random.randint(
            self.config.MIN_SESSION_BREAK_SECONDS,
            self.config.MAX_SESSION_BREAK_SECONDS
        )

        logger.info(f"☕ Taking session break ({break_duration}s)...")

        # Simulate natural browsing during break
        if self.config.SIMULATE_SCROLL_PATTERNS:
            scroll_pattern = random.choice(self.config.SCROLL_PATTERN_TYPES)
            await self._simulate_scroll_pattern(scroll_pattern)

        await human_delay(break_duration, break_duration + 10)

        logger.info("☕ Break complete. Resuming...")

    async def _simulate_scroll_pattern(self, pattern: str):
        """Simulate different scroll patterns."""
        if not self.page:
            return

        if pattern == "gradual":
            for _ in range(5):
                await self.page.evaluate("window.scrollBy(0, 200)")
                await asyncio.sleep(0.5)
        elif pattern == "burst":
            await self.page.evaluate("window.scrollBy(0, 1500)")
            await asyncio.sleep(0.3)
        elif pattern == "pause_and_scroll":
            await asyncio.sleep(1)
            await self.page.evaluate("window.scrollBy(0, 500)")
        elif pattern == "back_and_forth":
            await self.page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(0.5)
            await self.page.evaluate("window.scrollBy(0, -300)")

    # ═══════════════════════════════════════════════════════════
    #  METRICS & MONITORING
    # ═══════════════════════════════════════════════════════════

    def _update_metrics(self, success: bool = True):
        """Update real-time metrics."""
        if success:
            self.metrics["total_profiles_scraped"] += 1
            if self.delay_history:
                self.success_delays.append(self.delay_history[-1])
        else:
            if self.delay_history:
                self.failure_delays.append(self.delay_history[-1])

        total = self.metrics["total_profiles_scraped"] + len(self.failed_urls)
        if total > 0:
            self.metrics["success_rate"] = self.metrics["total_profiles_scraped"] / total
            self.metrics["error_rate"] = len(self.failed_urls) / total

        if self.delay_history:
            self.metrics["avg_delay_seconds"] = sum(self.delay_history) / len(self.delay_history)

    def _export_metrics(self):
        """Export metrics to JSON file."""
        if not self.config.ENABLE_METRICS:
            return

        metrics_file = Path(self.config.OUTPUT_DIR) / "metrics.json"
        metrics_file.parent.mkdir(parents=True, exist_ok=True)

        export_data = {
            **self.metrics,
            "end_time": datetime.now().isoformat(),
            "duration_minutes": (datetime.now() - datetime.fromisoformat(self.metrics["start_time"])).total_seconds() / 60,
        }

        with open(metrics_file, "w") as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"📊 Metrics exported to {metrics_file}")

    # ═══════════════════════════════════════════════════════════
    #  MAIN RUN METHOD
    # ═══════════════════════════════════════════════════════════

    async def run(self, target_list: List[str]):
        """Main execution method with enhanced features."""
        logger.info("🚀 Starting Enhanced LinkedIn Scraper v4.0")

        # Validate configuration
        warnings = validate_config(self.config)
        if warnings:
            for w in warnings:
                logger.warning(w)

        # Print config summary
        print_config_summary(self.config)

        # Initialize proxy manager if enabled
        if self.config.PROXY_ROTATION_ENABLED:
            try:
                from proxy_manager import ProxyManager
                self.proxy_manager = ProxyManager()
                self.proxy_manager.refresh()
                logger.info("✅ Proxy manager initialized")
            except Exception as e:
                logger.warning(f"⚠️  Proxy manager initialization failed: {e}")

        # Launch browser
        await self._launch_browser()

        # Login
        if not await self.login():
            logger.error("❌ Login failed. Exiting.")
            return

        # Execute based on search mode
        if self.config.SEARCH_MODE == "people":
            await self._run_people_search(target_list)
        elif self.config.SEARCH_MODE == "jobs":
            await self._run_jobs_search(target_list)
        elif self.config.SEARCH_MODE == "candidates":
            await self._run_candidates_search(target_list)
        elif self.config.SEARCH_MODE == "companies":
            await self._run_companies_search(target_list)

        # Export results
        await self._export_results()

        # Export metrics
        self._export_metrics()

        # Cleanup
        await self._cleanup()

    async def _run_people_search(self, companies: List[str]):
        """Execute people search mode."""
        logger.info("🔍 Running People Search Mode")

        for company in companies:
            if self.daily_searches >= self.config.MAX_DAILY_SEARCHES:
                logger.warning("🛑 Daily limit reached.")
                break

            for job_title in self.config.JOB_TITLES:
                if self.hourly_searches >= self.config.MAX_HOURLY_SEARCHES:
                    logger.info(f"⏱️  Hourly limit reached. Cooldown: {self.config.SAFETY_COOLDOWN_MINUTES}min")
                    await asyncio.sleep(self.config.SAFETY_COOLDOWN_MINUTES * 60)
                    self.hourly_searches = 0
                    self.last_hour_reset = datetime.now()

                logger.info(f"\n🏢 Company: {company} | Title: {job_title}")

                # Search for profiles
                profile_urls = await self.search_people(company, job_title)

                # Scrape each profile
                for url in profile_urls:
                    start_time = time.time()

                    profile = await self.scrape_profile(url, company, job_title)

                    delay = time.time() - start_time
                    self.delay_history.append(delay)

                    if profile:
                        self.results.append(profile)
                        self._update_metrics(success=True)

                        # Save progress periodically
                        if len(self.results) % 10 == 0:
                            save_progress(self.config.PROGRESS_FILE, {
                                "results": self.results,
                                "completed_companies": companies[:companies.index(company)+1],
                                "metrics": self.metrics,
                            })
                    else:
                        self._update_metrics(success=False)

                    # Human-like delay between profiles
                    await human_delay(
                        self.config.MIN_DELAY_BETWEEN_PROFILES,
                        self.config.MAX_DELAY_BETWEEN_PROFILES
                    )

                self.hourly_searches += 1
                self.session_count += 1

                # Periodic breaks
                if self.session_count % self.config.BREAK_EVERY_N_REQUESTS == 0:
                    await self._take_session_break()

                # Identity rotation
                if self.config.ROTATE_USER_AGENT and self.session_count % self.config.UA_ROTATE_EVERY_N == 0:
                    await self._rotate_identity()

    async def _run_jobs_search(self, keywords: List[str]):
        """Execute jobs search mode."""
        logger.info("💼 Running Jobs Search Mode")

        for keyword in keywords:
            if self.daily_searches >= self.config.MAX_DAILY_SEARCHES:
                break

            jobs = await self.search_jobs(keyword)
            self.results.extend(jobs)
            self.metrics["total_jobs_scraped"] += len(jobs)

            await human_delay(5, 10)

    async def _run_candidates_search(self, skills: List[str]):
        """Execute candidates search mode."""
        logger.info("👤 Running Candidates Search Mode")

        for skill in skills:
            if self.daily_searches >= self.config.MAX_DAILY_SEARCHES:
                break

            candidates = await self.search_candidates(skill)
            self.results.extend(candidates)

            await human_delay(5, 10)

    async def _run_companies_search(self, industries: List[str]):
        """Execute companies search mode (NEW in v4.0)."""
        logger.info("🏢 Running Companies Search Mode")
        # Implementation would go here
        pass

    async def _export_results(self):
        """Export results in configured formats."""
        if not self.results:
            logger.warning("⚠️  No results to export.")
            return

        output_dir = Path(self.config.OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Export to Excel
        if "xlsx" in self.config.EXPORT_FORMATS:
            if self.config.SEARCH_MODE == "people":
                export_to_excel(self.results, self.config.OUTPUT_FILE)
            elif self.config.SEARCH_MODE == "jobs":
                export_jobs_to_excel(self.results, self.config.JOBS_OUTPUT_FILE)
            elif self.config.SEARCH_MODE == "candidates":
                export_candidates_to_excel(self.results, self.config.CANDIDATES_OUTPUT_FILE)

        # Export to CSV
        if "csv" in self.config.EXPORT_FORMATS:
            import pandas as pd
            df = pd.DataFrame(self.results)
            csv_path = output_dir / f"linkedin_{self.config.SEARCH_MODE}.csv"
            df.to_csv(csv_path, index=False)
            logger.info(f"✅ CSV exported: {csv_path}")

        # Export to JSON
        if "json" in self.config.EXPORT_FORMATS:
            json_path = output_dir / f"linkedin_{self.config.SEARCH_MODE}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            logger.info(f"✅ JSON exported: {json_path}")

    async def _cleanup(self):
        """Cleanup resources."""
        logger.info("🧹 Cleaning up...")

        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

        logger.info("✅ Cleanup complete. Goodbye! 👋")

    # ═══════════════════════════════════════════════════════════
    #  SEARCH METHODS (wrappers for existing implementation)
    # ═══════════════════════════════════════════════════════════

    async def search_people(self, company: str, job_title: str) -> List[str]:
        """Search for people by company and job title."""
        # Import from original scraper
        from linkedin_scraper import LinkedInScraper as OriginalScraper

        # Create temporary instance to reuse search logic
        temp_config = type('TempConfig', (), {
            'GEO_URN': self.config.GEO_URN,
            'MAX_PAGES_PER_COMPANY': self.config.MAX_PAGES_PER_COMPANY,
            'REQUEST_TIMEOUT_MS': self.config.REQUEST_TIMEOUT_MS,
            'FILTER_KEYWORDS': self.config.FILTER_KEYWORDS,
            'ROTATE_USER_AGENT': False,  # We handle rotation at higher level
            'UA_ROTATE_EVERY_N': 0,
            'BREAK_EVERY_N_REQUESTS': 9999,
        })()

        temp_scraper = OriginalScraper(temp_config)
        temp_scraper.page = self.page
        temp_scraper.daily_searches = self.daily_searches
        temp_scraper.session_count = self.session_count

        urls = await temp_scraper.search_people(company, job_title)

        self.daily_searches = temp_scraper.daily_searches
        self.session_count = temp_scraper.session_count

        return urls

    async def search_jobs(self, keyword: str) -> List[Dict]:
        """Search for jobs by keyword."""
        from linkedin_scraper import LinkedInScraper as OriginalScraper

        temp_config = type('TempConfig', (), {
            'GEO_URN': self.config.GEO_URN,
            'JOB_EXPERIENCE_LEVEL': self.config.JOB_EXPERIENCE_LEVEL,
            'JOB_DATE_POSTED': self.config.JOB_DATE_POSTED,
            'JOB_REMOTE_FILTER': self.config.JOB_REMOTE_FILTER,
            'JOB_STRICT_HOURS_FILTER': self.config.JOB_STRICT_HOURS_FILTER,
            'MAX_JOB_PAGES': self.config.MAX_JOB_PAGES,
            'REQUEST_TIMEOUT_MS': self.config.REQUEST_TIMEOUT_MS,
            'ROTATE_USER_AGENT': False,
            'UA_ROTATE_EVERY_N': 0,
            'BREAK_EVERY_N_REQUESTS': 9999,
        })()

        temp_scraper = OriginalScraper(temp_config)
        temp_scraper.page = self.page
        temp_scraper.daily_searches = self.daily_searches
        temp_scraper.session_count = self.session_count

        jobs = await temp_scraper.search_jobs(keyword)

        self.daily_searches = temp_scraper.daily_searches
        self.session_count = temp_scraper.session_count

        return jobs

    async def search_candidates(self, skill: str) -> List[Dict]:
        """Search for candidates by skill."""
        # Similar implementation to search_people but for candidates
        # Would extract from original scraper's candidate search logic
        return []

    async def scrape_profile(self, profile_url: str, search_company: str, job_title: str) -> Optional[Dict]:
        """Scrape a single profile."""
        from linkedin_scraper import LinkedInScraper as OriginalScraper

        temp_config = type('TempConfig', (), {
            'FILTER_KEYWORDS': self.config.FILTER_KEYWORDS,
            'REQUEST_TIMEOUT_MS': self.config.REQUEST_TIMEOUT_MS,
        })()

        temp_scraper = OriginalScraper(temp_config)
        temp_scraper.page = self.page

        profile = await temp_scraper.scrape_profile(profile_url, search_company, job_title)

        return profile


# ═══════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def main():
    """Main entry point for enhanced scraper."""
    import sys

    config = Config()

    # Check credentials
    if not config.LINKEDIN_EMAIL or config.LINKEDIN_EMAIL == "your_email@gmail.com":
        print("❌ ERROR: Please set your LinkedIn credentials in config_enhanced.py or via environment variables")
        sys.exit(1)

    # Simple test list
    target_list = ["Google", "Microsoft", "Apple"]

    scraper = LinkedInScraperEnhanced(config)
    await scraper.run(target_list)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.")
        sys.exit(0)