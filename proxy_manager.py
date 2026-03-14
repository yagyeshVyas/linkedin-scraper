"""
Free Proxy Manager
==================
Fetches fresh free proxies every hour automatically.
Sources: ProxyScrape, Free-Proxy-List, GeoNode, Proxy-List
"""

import asyncio
import random
import logging
import time
import json
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  FREE PROXY SOURCES (all 100% free)
# ─────────────────────────────────────────────

PROXY_SOURCES = [
    # ProxyScrape — refreshes every few minutes
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=US&ssl=all&anonymity=all",

    # Free-Proxy-List via ProxyScrape API
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=elite",

    # GeoNode free proxy API
    "https://proxylist.geonode.com/api/proxy-list?limit=100&page=1&sort_by=lastChecked&sort_type=desc&protocols=http,https&speed=fast",

    # Proxy-list.download
    "https://www.proxy-list.download/api/v1/get?type=http&anon=elite",

    # ProxyNova (scraped)
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=US&ssl=yes&anonymity=elite",
]


class ProxyManager:
    """
    Fetches, tests, and rotates free proxies automatically.
    Refreshes proxy list every hour.
    """

    def __init__(self, cache_file: str = "output/proxies.json"):
        self.proxies = []
        self.working_proxies = []
        self.current_index = 0
        self.cache_file = cache_file
        self.last_refresh = 0
        self.refresh_interval = 3600  # 1 hour in seconds
        self.failed_proxies = set()

    # ─────────────────────────────────────────────
    #  FETCH PROXIES FROM FREE SOURCES
    # ─────────────────────────────────────────────

    def fetch_proxies(self) -> list:
        """Fetch proxies from all free sources."""
        all_proxies = []

        for url in PROXY_SOURCES:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    content = response.read().decode("utf-8")

                    # Handle JSON response (GeoNode)
                    if "geonode" in url:
                        data = json.loads(content)
                        for proxy in data.get("data", []):
                            ip = proxy.get("ip")
                            port = proxy.get("port")
                            if ip and port:
                                all_proxies.append(f"{ip}:{port}")

                    # Handle plain text response (ip:port per line)
                    else:
                        lines = content.strip().split("\n")
                        for line in lines:
                            line = line.strip()
                            if ":" in line and len(line) < 25:
                                all_proxies.append(line)

                logger.info(f"   ✅ Fetched from {url[:50]}... ({len(all_proxies)} total so far)")

            except Exception as e:
                logger.warning(f"   ⚠️  Failed to fetch from source: {e}")

        # Deduplicate
        all_proxies = list(set(all_proxies))
        logger.info(f"📋 Total proxies fetched: {len(all_proxies)}")
        return all_proxies

    # ─────────────────────────────────────────────
    #  TEST PROXIES
    # ─────────────────────────────────────────────

    def test_proxy(self, proxy: str, timeout: int = 5) -> bool:
        """Test if a proxy works by making a quick request."""
        try:
            proxy_handler = urllib.request.ProxyHandler({
                "http": f"http://{proxy}",
                "https": f"http://{proxy}"
            })
            opener = urllib.request.build_opener(proxy_handler)
            opener.addheaders = [("User-Agent", "Mozilla/5.0")]

            with opener.open("http://httpbin.org/ip", timeout=timeout) as response:
                return response.status == 200

        except Exception:
            return False

    def test_proxies_batch(self, proxies: list, sample_size: int = 50) -> list:
        """Test a sample of proxies and return working ones."""
        logger.info(f"🧪 Testing {min(sample_size, len(proxies))} proxies...")

        # Test a random sample (testing all would take too long)
        sample = random.sample(proxies, min(sample_size, len(proxies)))
        working = []

        for i, proxy in enumerate(sample, 1):
            if proxy in self.failed_proxies:
                continue
            if self.test_proxy(proxy):
                working.append(proxy)
                logger.info(f"   ✅ [{i}/{len(sample)}] Working: {proxy}")
            else:
                logger.debug(f"   ❌ [{i}/{len(sample)}] Failed: {proxy}")

            # Stop early if we have enough working proxies
            if len(working) >= 20:
                break

        logger.info(f"✅ Found {len(working)} working proxies")
        return working

    # ─────────────────────────────────────────────
    #  REFRESH (fetch + test)
    # ─────────────────────────────────────────────

    def refresh(self, force: bool = False):
        """
        Refresh the proxy list.
        Auto-refreshes every hour. Pass force=True to refresh immediately.
        """
        now = time.time()
        elapsed = now - self.last_refresh

        if not force and elapsed < self.refresh_interval and self.working_proxies:
            remaining = int(self.refresh_interval - elapsed)
            logger.info(f"⏱️  Proxies still fresh. Next refresh in {remaining//60}m {remaining%60}s")
            return

        logger.info("🔄 Refreshing proxy list...")

        # Try to load from cache first (if less than 30 min old)
        cached = self._load_cache()
        if cached and not force and elapsed < 1800:
            self.working_proxies = cached
            logger.info(f"📂 Loaded {len(self.working_proxies)} proxies from cache")
            return

        # Fetch fresh proxies
        raw_proxies = self.fetch_proxies()

        if not raw_proxies:
            logger.warning("⚠️  No proxies fetched. Continuing without proxy.")
            return

        # Test them
        self.working_proxies = self.test_proxies_batch(raw_proxies)
        self.last_refresh = now
        self.current_index = 0

        # Save to cache
        self._save_cache(self.working_proxies)

        logger.info(f"✅ Proxy list ready: {len(self.working_proxies)} working proxies")

    # ─────────────────────────────────────────────
    #  GET NEXT PROXY (rotation)
    # ─────────────────────────────────────────────

    def get_proxy(self) -> Optional[str]:
        """
        Get the next proxy in rotation.
        Auto-refreshes if the list is empty or stale.
        """
        # Auto-refresh every hour
        if time.time() - self.last_refresh > self.refresh_interval:
            self.refresh()

        if not self.working_proxies:
            logger.warning("⚠️  No working proxies available.")
            return None

        # Rotate through proxies
        proxy = self.working_proxies[self.current_index % len(self.working_proxies)]
        self.current_index += 1
        return proxy

    def mark_failed(self, proxy: str):
        """Mark a proxy as failed and remove it from the working list."""
        self.failed_proxies.add(proxy)
        if proxy in self.working_proxies:
            self.working_proxies.remove(proxy)
            logger.info(f"🗑️  Removed failed proxy: {proxy} ({len(self.working_proxies)} remaining)")

        # If running low, refresh
        if len(self.working_proxies) < 3:
            logger.info("⚠️  Running low on proxies — refreshing...")
            self.refresh(force=True)

    # ─────────────────────────────────────────────
    #  CACHE (save/load to avoid re-testing every run)
    # ─────────────────────────────────────────────

    def _save_cache(self, proxies: list):
        Path(self.cache_file).parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump({"proxies": proxies, "timestamp": time.time()}, f)

    def _load_cache(self) -> list:
        try:
            if Path(self.cache_file).exists():
                with open(self.cache_file) as f:
                    data = json.load(f)
                age = time.time() - data.get("timestamp", 0)
                if age < self.refresh_interval:
                    return data.get("proxies", [])
        except Exception:
            pass
        return []

    def status(self):
        """Print current proxy status."""
        age = int(time.time() - self.last_refresh)
        next_refresh = max(0, self.refresh_interval - age)
        print(f"""
📡 Proxy Status:
   Working proxies : {len(self.working_proxies)}
   Failed proxies  : {len(self.failed_proxies)}
   List age        : {age//60}m {age%60}s
   Next refresh    : {next_refresh//60}m {next_refresh%60}s
        """)