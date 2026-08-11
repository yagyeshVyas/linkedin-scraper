"""
Behavioral tests for the scraper power-up fixes.
Run with the project venv:  .venv/Scripts/python.exe test_scraper_fixes.py
Pure unit tests — no browser, no network.
"""
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup

from config import Config
from linkedin_scraper import LinkedInScraper
import linkedin_scraper as L
from urllib.parse import urlencode


async def _noop(*a, **kw):
    pass


class FlakyPage:
    """Goto raises until flip() is called — models a transient navigation timeout."""
    def __init__(self):
        self.fail_goto = True
        self.html = "<html><body><h1 class='text-heading-xlarge'>Jane Doe</h1></body></html>"

    async def goto(self, *a, **kw):
        if self.fail_goto:
            raise TimeoutError("navigation timeout")

    async def content(self):
        return self.html

    async def evaluate(self, *a, **kw):
        pass

FIXTURE = """<html><body>
<h1 class="text-heading-xlarge">Jane Doe</h1>
<div class="text-body-medium break-words">Senior Talent Acquisition Partner at Walmart</div>
<span class="text-body-small inline t-black--light break-words">Bentonville, Arkansas, United States</span>
<a href="mailto:jane.doe@example.org">Email</a>
<section id="about"><div class="pv-shared-text-with-see-more">Hiring for Walmart Global Tech. 15 years in TA.</div></section>
<section id="skills">
  <div class="pv-skill-category-entity__name-text">Talent Sourcing</div>
  <div class="pv-skill-category-entity__name-text">ATS</div>
  <div class="pv-skill-category-entity__name-text">Talent Sourcing</div>
</section>
<section id="experience">
  <div class="pv-position-entity">
    <h3 class="t-bold">Senior Talent Acquisition Partner</h3>
    <div class="pv-entity__secondary-title">Walmart</div>
    <div class="pv-entity__date-range"><span>2021</span><span>Present</span></div>
  </div>
</section>
<section id="education">
  <div class="pv-education-entity">
    <h3 class="pv-entity__school-name">University of Arkansas</h3>
    <div class="pv-entity__degree-name">BS Business</div>
  </div>
</section>
<div class="pv-top-card--list pv-top-card--list-bullet"><span class="t-black--light">500+</span></div>
</body></html>
"""


def test_extractors():
    s = LinkedInScraper(Config())
    soup = BeautifulSoup(FIXTURE, "html.parser")
    assert s._extract_name(soup) == "Jane Doe"
    assert s._extract_current_title(soup) == "Senior Talent Acquisition Partner"
    assert s._extract_location(soup) == "Bentonville, Arkansas, United States"
    assert s._extract_email(soup) == "jane.doe@example.org"
    assert "Hiring for Walmart" in s._extract_about(soup)
    assert s._extract_skills(soup) == "Talent Sourcing, ATS"
    assert "Walmart" in s._extract_experience(soup) and "2021" in s._extract_experience(soup)
    assert "University of Arkansas" in s._extract_education(soup)
    assert s._extract_connections(soup) == "500+"
    # Empty page → graceful empty strings, no crash
    empty = BeautifulSoup("<html><body></body></html>", "html.parser")
    assert s._extract_about(empty) == ""
    assert s._extract_skills(empty) == ""
    assert s._extract_experience(empty) == ""
    assert s._extract_education(empty) == ""
    assert s._extract_email(empty) == ""


def test_email_filtering():
    s = LinkedInScraper(Config())
    html = ('<html><body><p>contact: 123456789@2x.png join us admin@sentry.io '
            "visit 5f3a9c2b@linkedin.com my real address is kate.smith@walmart.com today</p></body></html>")
    got = s._extract_email(BeautifulSoup(html, "html.parser"))
    assert got == "kate.smith@walmart.com", got


def test_hours_ago():
    s = LinkedInScraper(Config())
    assert s._parse_hours_ago("Just now") == 0
    assert s._parse_hours_ago("5 minutes ago") == 0
    assert s._parse_hours_ago("3 hours ago") == 3
    assert s._parse_hours_ago("2 days ago") == 48
    assert s._parse_hours_ago("1 week ago") == 168
    assert s._parse_hours_ago("garbage") == 999999


def test_url_encoding():
    url = "https://www.linkedin.com/search/results/people/?" + urlencode({
        "keywords": '"Recruiter" "AT&T"',
        "origin": "GLOBAL_SEARCH_HEADER",
        "geoUrn": '["103644278"]',
    })
    # The critical fix: '&' inside the company name must be escaped (AT&T bug),
    # not treated as a query-parameter separator. Spaces may be '+' or %20.
    assert '%22AT%26T%22' in url, url
    assert url.count('AT&T') == 0, url  # raw '&' would break the query


def test_init_and_dedup_seeding():
    cfg = Config()
    s = LinkedInScraper(cfg)
    assert s.current_account["email"] == cfg.LINKEDIN_EMAIL
    assert s.current_account["password"] == cfg.LINKEDIN_PASSWORD
    assert s.scraped_urls == set()
    # Simulate resumed progress seeding the dedup set
    s.results = [{"linkedin_url": "https://www.linkedin.com/in/alice"}, {"linkedin_url": ""}]
    s.scraped_urls = {r.get("linkedin_url") for r in s.results if r.get("linkedin_url")}
    assert s.scraped_urls == {"https://www.linkedin.com/in/alice"}


def test_proxy_rotation_no_immediate_repeat():
    cfg = Config()
    cfg.PROXY_LIST = ["http://p1:8080", "http://p2:8080", "http://p3:8080"]
    s = L.LinkedInScraper(cfg)
    picked = [s._next_proxy() for _ in range(6)]
    for a, b in zip(picked, picked[1:]):
        assert a != b, f"rotation repeated {a} consecutively"
    assert set(picked) == set(cfg.PROXY_LIST), "rotation must cover every proxy"
    # Empty list → no proxy, no crash
    cfg.PROXY_LIST = []
    assert L.LinkedInScraper(cfg)._next_proxy() is None


def test_free_proxy_fallback_and_priority():
    from types import SimpleNamespace

    # 1) PROXY_LIST set → rotation ignores the free-proxy manager entirely.
    cfg = Config()
    cfg.PROXY_LIST = ["http://paid1:8080", "http://paid2:8080"]
    cfg.USE_FREE_PROXIES = True
    s = L.LinkedInScraper(cfg)
    s.proxy_manager = SimpleNamespace(get_proxy=lambda: "http://free:8080")
    picked = [s._next_proxy() for _ in range(3)]
    assert "http://free:8080" not in picked, "configured list must win over free proxies"
    assert all(p.startswith("http://paid") for p in picked)

    # 2) PROXY_LIST empty + USE_FREE_PROXIES → falls back to the manager.
    cfg.PROXY_LIST = []
    s2 = L.LinkedInScraper(cfg)
    s2.proxy_manager = SimpleNamespace(get_proxy=lambda: "http://free:8080")
    assert s2._next_proxy() == "http://free:8080"
    assert s2.current_proxy == "http://free:8080"

    # 3) Manager returns nothing → direct connection, no crash.
    s3 = L.LinkedInScraper(cfg)
    s3.proxy_manager = SimpleNamespace(get_proxy=lambda: None)
    assert s3._next_proxy() is None
    assert s3.current_proxy is None

    # 4) Feature off entirely → direct connection.
    cfg.USE_FREE_PROXIES = False
    assert L.LinkedInScraper(cfg)._next_proxy() is None


def test_ensure_free_proxies_gates_refresh():
    from types import SimpleNamespace

    cfg = Config()
    cfg.PROXY_LIST = []
    cfg.USE_FREE_PROXIES = True
    s = L.LinkedInScraper(cfg)

    calls = []

    class FakeManager:
        def __init__(self):
            self.working_proxies = []

        def refresh(self):
            calls.append("refresh")
            self.working_proxies = ["http://free1:8080", "http://free2:8080"]

    s.proxy_manager = FakeManager()
    asyncio.run(s._ensure_free_proxies())
    assert calls == ["refresh"], "first call must fetch"
    assert len(s.proxy_manager.working_proxies) == 2

    asyncio.run(s._ensure_free_proxies())
    assert calls == ["refresh"], "second call must reuse the pool, not refetch"

    # No working proxies after refresh → warns but does not crash.
    s2 = L.LinkedInScraper(cfg)
    s2.proxy_manager = SimpleNamespace(working_proxies=[], refresh=lambda: None)
    asyncio.run(s2._ensure_free_proxies())


def test_proxy_manager_cache_used_without_network():
    """A fresh ProxyManager with a fresh cache file must load it instead of
    hitting the proxy sources (the pre-fix behavior refetched every launch)."""
    import json
    import tempfile
    import time

    from proxy_manager import ProxyManager

    with tempfile.TemporaryDirectory() as tmp:
        cache = str(Path(tmp) / "proxies.json")
        Path(cache).write_text(json.dumps({
            "proxies": ["http://cached1:8080", "http://cached2:8080"],
            "timestamp": time.time(),
        }), encoding="utf-8")

        pm = ProxyManager(cache_file=cache)
        pm.refresh()
        assert pm.working_proxies == ["http://cached1:8080", "http://cached2:8080"], pm.working_proxies
        assert pm.last_refresh > 0, "last_refresh must be set from the cache"


def test_backoff_growth_and_cap():
    random.seed(7)
    d3 = L.LinkedInScraper._backoff_delay(3)
    d4 = L.LinkedInScraper._backoff_delay(4)
    d5 = L.LinkedInScraper._backoff_delay(5)
    d10 = L.LinkedInScraper._backoff_delay(10)
    assert 30 < d3 < 90, d3          # ~60s baseline with jitter
    assert d4 > d3, "backoff must grow"
    assert d5 > d4
    assert d10 <= 720, d10           # capped at 600s + jitter headroom


def test_failed_url_retry_recovers():
    L.human_delay = _noop
    L.random_scroll = _noop
    cfg = Config()
    cfg.USE_PERSISTENT_SESSION = False
    s = L.LinkedInScraper(cfg)
    s.page = FlakyPage()
    url = "https://www.linkedin.com/in/jane-doe"

    prof = asyncio.run(s.scrape_profile(url, search_company="Walmart", job_title="Recruiter"))
    assert prof is None, "first attempt must fail"
    assert s.failed_urls == [{"url": url, "company": "Walmart", "title": "Recruiter"}], s.failed_urls

    s.page.fail_goto = False
    recovered = asyncio.run(s._retry_failed_profiles())
    assert recovered == 1
    assert len(s.results) == 1
    assert s.scraped_urls == {url}
    assert s.failed_urls == [], "recovered URL must leave the failed list"

    # Already-scraped URLs are never retried.
    assert asyncio.run(s._retry_failed_profiles()) == 0


def test_login_helpers():
    cfg = Config()
    s = LinkedInScraper(cfg)
    # Session file path: @ → _ (matches the save/restore convention)
    assert s._session_file().name == f"session_{cfg.LINKEDIN_EMAIL.replace('@', '_')}.json"
    assert str(s._session_file().parent) == cfg.SESSION_DIR.rstrip("/\\")
    # Fresh instance starts with no recorded login error
    assert s._last_login_error == ""
    # Challenge polling is bounded by config, not a blind fixed sleep
    assert cfg.LOGIN_CHALLENGE_TIMEOUT_SECONDS == 90
    assert cfg.MAX_RETRIES >= 1


def test_config_defaults():
    cfg = Config()
    assert cfg.DISCORD_WEBHOOK_URL == ""  # export path no longer AttributeErrors
    assert cfg.LOGIN_CHALLENGE_TIMEOUT_SECONDS > 0
    assert cfg.USE_FREE_PROXIES is False  # opt-in: no network at launch by default
    assert cfg.RETRY_FAILED_PROFILES is True


if __name__ == "__main__":
    for fn in [test_extractors, test_email_filtering, test_hours_ago,
               test_url_encoding, test_init_and_dedup_seeding,
               test_proxy_rotation_no_immediate_repeat,
               test_free_proxy_fallback_and_priority,
               test_ensure_free_proxies_gates_refresh,
               test_proxy_manager_cache_used_without_network,
               test_backoff_growth_and_cap,
               test_failed_url_retry_recovers, test_login_helpers,
               test_config_defaults]:
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll scraper fix tests passed.")
