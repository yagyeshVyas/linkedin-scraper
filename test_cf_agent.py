"""Behavioral tests for the v6.0 stealth + agent-browser layer.

No real browser and no network: every test exercises a pure code path or a
stubbed engine. Run with:

    .venv/Scripts/python.exe test_cf_agent.py
"""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import agent_browser
import cf_bypass
from agent_browser import (AgentBrowserServer, BrowserSession, ToolError,
                           format_snapshot, _ref_selector)
from cf_bypass import (ClearanceCache, FetchResult, FlareSolverrClient,
                       Challenge, TieredFetcher, classify_page,
                       classify_response, block_reason_for, cookie_header,
                       impersonate_for_user_agent, platform_for_user_agent,
                       is_clearance_cookie, solve_challenge, available_engines,
                       capability_report, launch_stealth_browser, _run_async)


# ─────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────

CF_INTERSTITIAL = """<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="cf-wrapper">Checking your browser before accessing example.com.
<script>window._cf_chl_opt={cvId:'3'};</script>
<a href="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b">Ray ID</a>
</div></body></html>"""

TURNSTILE_PAGE = """<html><body><form>
<div class="cf-turnstile" data-sitekey="0x4AAA"></div>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
</form></body></html>"""

CLEAN_PAGE = """<html><head><title>Profile</title></head><body>
<h1>Jane Doe</h1><p>Recruiter at Acme Corp.</p></body></html>"""

DATADOME_PAGE = """<html><body>Please enable JS.
<script src="https://geo.captcha-delivery.com/captcha/"></script>
</body></html>"""

RATE_LIMIT_PAGE = "<html><body><h1>Too many requests</h1><p>Slow down.</p></body></html>"

AUTHWALL_PAGE = "<html><body>Sign in to LinkedIn to see this profile.</body></html>"


def _headers(**kwargs):
    return dict(kwargs)


async def _noop(*args, **kwargs):
    return None


# ─────────────────────────────────────────────────────────────
#  Classification
# ─────────────────────────────────────────────────────────────

def test_import_is_light():
    """Importing the stealth modules must not drag in a browser engine."""
    for banned in ("patchright", "camoufox", "curl_cffi"):
        assert banned not in sys.modules, f"{banned} imported at module import time"


def test_classify_clean_page():
    challenge = classify_page("https://www.linkedin.com/in/jane-doe", CLEAN_PAGE)
    assert challenge.verdict == cf_bypass.NONE
    assert not challenge.blocked and not challenge.hard


def test_classify_cloudflare_interstitial():
    challenge = classify_page("https://example.com", CF_INTERSTITIAL)
    assert challenge.verdict == cf_bypass.CLOUDFLARE
    assert challenge.hard
    assert any("_cf_chl_opt" in marker or "just a moment" in marker
               for marker in challenge.markers)


def test_classify_cloudflare_header_only():
    challenge = classify_response(403, _headers(**{"cf-mitigated": "challenge",
                                                   "cf-ray": "abc"}),
                                 "<html><body>nope</body></html>")
    assert challenge.verdict == cf_bypass.CLOUDFLARE
    assert challenge.hard


def test_classify_turnstile_widget():
    challenge = classify_page("https://example.com", TURNSTILE_PAGE)
    assert challenge.verdict == cf_bypass.TURNSTILE
    assert challenge.hard


def test_turnstile_on_content_page_is_not_a_wall():
    """A Turnstile widget inside a real page (login form, demo) is an in-page
    captcha, not an edge challenge — the page is readable and must not trigger
    a solver wait."""
    page = ("<html><body><div class='cf-turnstile' data-sitekey='0x4AAA'></div>"
            "<p>" + "Recruiter search results. " * 400 + "</p></body></html>")
    challenge = classify_page("https://example.com", page)
    assert challenge.verdict == cf_bypass.NONE
    assert not challenge.hard


def test_classify_other_bot_walls():
    assert classify_page("https://x", DATADOME_PAGE).verdict == cf_bypass.BOT_WALL
    px = classify_page("https://x", '<script>window._pxCaptcha = 1;</script>')
    assert px.verdict == cf_bypass.BOT_WALL


def test_classify_rate_limit_and_authwall():
    assert classify_response(429, {}, RATE_LIMIT_PAGE).verdict == cf_bypass.RATE_LIMIT
    assert classify_page("https://x", AUTHWALL_PAGE).verdict == cf_bypass.AUTHWALL


def test_classify_empty_403_is_edge_block():
    assert classify_response(403, {}, "").verdict == cf_bypass.CLOUDFLARE


def test_retry_after_is_parsed():
    challenge = classify_response(429, _headers(**{"retry-after": "30"}), RATE_LIMIT_PAGE)
    assert challenge.retry_after == 30.0


def test_block_reason_mapping():
    assert block_reason_for("turnstile") == "cloudflare"
    assert block_reason_for("bot_wall") == "bot_wall"
    assert block_reason_for("none") == ""
    assert block_reason_for("garbage") == ""


# ─────────────────────────────────────────────────────────────
#  Clearance cache
# ─────────────────────────────────────────────────────────────

def test_clearance_cache_roundtrip_and_scope():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ClearanceCache(str(Path(tmp) / "clearance.json"), ttl=600)
        cookies = {"cf_clearance": "token-1", "tz": "UTC", "__cf_bm": "bm-1"}
        assert cache.put("https://site.test/a", cookies, proxy="http://p:1", user_agent="UA")
        got = cache.get("https://site.test/b", proxy="http://p:1", user_agent="UA")
        assert got == {"cf_clearance": "token-1", "__cf_bm": "bm-1"}
        # Same domain, different egress IP or UA -> must NOT replay.
        assert cache.get("https://site.test/a", proxy="http://p:2", user_agent="UA") == {}
        assert cache.get("https://site.test/a", proxy="http://p:1", user_agent="UA2") == {}
        assert is_clearance_cookie("cf_clearance") and is_clearance_cookie("__cf_bm")
        assert not is_clearance_cookie("li_at")


def test_clearance_cache_ttl_and_prune():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ClearanceCache(str(Path(tmp) / "clearance.json"), ttl=10)
        cache.put("https://site.test/", {"cf_clearance": "x"}, now=1_000)
        assert cache.get("https://site.test/", now=1_005) == {"cf_clearance": "x"}
        assert cache.get("https://site.test/", now=1_011) == {}
        removed = cache.prune(now=1_100)
        assert removed == 1


def test_cookie_header():
    assert cookie_header({"a": "1", "b": "2"}) == "a=1; b=2"
    assert cookie_header({}) == ""


# ─────────────────────────────────────────────────────────────
#  Identity / TLS
# ─────────────────────────────────────────────────────────────

def test_impersonate_matches_user_agent():
    assert impersonate_for_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0").startswith("chrome")
    assert impersonate_for_user_agent("Mozilla/5.0 Firefox/133.0").startswith("firefox")
    assert impersonate_for_user_agent(
        "Mozilla/5.0 (Macintosh) Safari/605.1.15").startswith("safari")
    assert impersonate_for_user_agent("").startswith("chrome")


def test_impersonate_returns_a_real_curl_target():
    """The chosen TLS profile must exist in this curl_cffi build, or the tier
    silently degrades to a generic handshake."""
    targets = cf_bypass._curl_targets()
    if not targets:
        return  # curl_cffi not installed on this machine
    for ua in ("Chrome/131.0.0.0", "Firefox/133.0", "Safari/605.1.15", ""):
        assert impersonate_for_user_agent(
            "Mozilla/5.0 (Windows NT 10.0) " + ua) in targets


def test_platform_for_user_agent():
    assert platform_for_user_agent("Windows NT 10.0")[1] == "Windows"
    assert platform_for_user_agent("Macintosh; Intel Mac OS X")[1] == "macOS"
    assert platform_for_user_agent("X11; Linux x86_64")[1] == "Linux"
    assert platform_for_user_agent("Android 13")[1] == "Android"


# ─────────────────────────────────────────────────────────────
#  FlareSolverr
# ─────────────────────────────────────────────────────────────

def test_flaresolverr_unconfigured():
    result = FlareSolverrClient("").solve("https://x")
    assert not result.ok and "not configured" in result.error


def test_flaresolverr_parses_solution():
    client = FlareSolverrClient("http://127.0.0.1:8191")
    client._post_json = lambda payload: {
        "status": "ok",
        "solution": {
            "status": 200,
            "url": "https://x/final",
            "response": CLEAN_PAGE,
            "cookies": [{"name": "cf_clearance", "value": "abc"},
                        {"name": "li_at", "value": "secret"}],
        },
    }
    result = client.solve("https://x")
    assert result.ok and result.tier == "flaresolverr"
    assert result.cookies["cf_clearance"] == "abc"


def test_flaresolverr_reports_failure():
    client = FlareSolverrClient("http://127.0.0.1:8191")
    client._post_json = lambda payload: {"status": "error", "message": "captcha did not solve"}
    result = client.solve("https://x")
    assert not result.ok and "captcha did not solve" in result.error


# ─────────────────────────────────────────────────────────────
#  Fetch ladder
# ─────────────────────────────────────────────────────────────

class _LadderFetcher(TieredFetcher):
    """TieredFetcher with the tiers replaced by scripted results."""

    def __init__(self, script):
        super().__init__(allow_browser=True)
        self.script = script
        self.calls = []

    def _tier_curl(self, url, headers, data, method):
        self.calls.append("curl")
        return self.script.get("curl")

    def _tier_solverr(self, url):
        self.calls.append("solverr")
        return self.script.get("solverr")

    def _tier_browser(self, url):
        self.calls.append("browser")
        return self.script.get("browser")


def _ok(tier):
    return FetchResult(True, 200, "https://x", CLEAN_PAGE, {}, tier, Challenge())


def _blocked(tier, verdict=cf_bypass.CLOUDFLARE):
    return FetchResult(False, 403, "https://x", CF_INTERSTITIAL, {}, tier,
                       Challenge(verdict, 0.9))


def test_fetch_ladder_short_circuits_on_clean_curl():
    fetcher = _LadderFetcher({"curl": _ok("curl_cffi")})
    result = fetcher.fetch("https://x")
    assert result.ok and result.tier == "curl_cffi"
    assert fetcher.calls == ["curl"]


def test_fetch_ladder_falls_through_to_solverr():
    fetcher = _LadderFetcher({"curl": _blocked("curl_cffi"),
                              "solverr": _ok("flaresolverr")})
    result = fetcher.fetch("https://x")
    assert result.ok and result.tier == "flaresolverr"
    assert fetcher.calls == ["curl", "solverr"]


def test_fetch_ladder_reaches_browser_last():
    fetcher = _LadderFetcher({"curl": _blocked("curl_cffi"),
                              "solverr": _blocked("flaresolverr"),
                              "browser": _ok("browser:patchright")})
    result = fetcher.fetch("https://x")
    assert result.ok and result.tier == "browser:patchright"
    assert fetcher.calls == ["curl", "solverr", "browser"]


def test_fetch_rejects_non_http():
    try:
        TieredFetcher().fetch("file:///etc/passwd")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a non-http URL")


# ─────────────────────────────────────────────────────────────
#  Snapshot formatting
# ─────────────────────────────────────────────────────────────

SNAPSHOT_NODES = [
    {"ref": "e1", "role": "heading", "name": "Jane Doe", "level": 1, "depth": 0},
    {"ref": "e2", "role": "link", "name": "Contact info", "href": "/in/jane",
     "depth": 1},
    {"ref": "e3", "role": "textbox", "name": "Search", "value": "recruiters",
     "depth": 1},
    {"ref": "e4", "role": "checkbox", "name": "Remote only", "checked": False,
     "depth": 2},
    {"ref": "e5", "role": "text", "name": "Recruiter at Acme", "depth": 1},
]


def test_format_snapshot_renders_refs():
    text = format_snapshot(SNAPSHOT_NODES)
    assert '[ref=e1]' in text and '[ref=e4]' in text
    assert 'heading "Jane Doe" [level=1]' in text
    assert 'href=/in/jane' in text
    assert 'value="recruiters"' in text
    assert '[unchecked]' in text
    # depth becomes indentation
    assert text.splitlines()[2].startswith("  - ")


def test_format_snapshot_truncates_and_escapes():
    text = format_snapshot(SNAPSHOT_NODES, max_nodes=2)
    assert len(text.splitlines()) == 2
    quoted = format_snapshot([{"ref": "e1", "role": "text",
                              "name": 'He said "hi"', "depth": 0}])
    assert '\\"hi\\"' in quoted


def test_ref_selector():
    assert _ref_selector("e42") == '[data-felix-ref="e42"]'


# ─────────────────────────────────────────────────────────────
#  Agent browser: tools, MCP, stale refs
# ─────────────────────────────────────────────────────────────

def test_tool_specs_cover_all_tools():
    names = {spec["function"]["name"] for spec in agent_browser.TOOL_SPECS}
    tool_names = {tool["name"] for tool in agent_browser.TOOLS}
    assert names == tool_names and "browser_snapshot" in names
    for spec in agent_browser.TOOL_SPECS:
        assert spec["function"]["parameters"]["type"] == "object"


def test_mcp_initialize_and_tools_list():
    server = AgentBrowserServer()
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05"}})
    assert init["result"]["serverInfo"]["name"] == "agent-browser"
    listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in listing["result"]["tools"]}
    assert "browser_navigate" in names and "browser_snapshot" in names
    # Notifications get no response.
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


class _FakeSession:
    running = True

    def info(self):
        return {"engine": "patchright", "headless": True, "url": "", "running": True}

    async def snapshot(self, **kwargs):
        return {"url": "https://x", "title": "X", "total": 1, "shown": 1,
                "snapshot": '- button "Go" [ref=e1]', "refs": ["e1"], "text": "Go"}

    async def goto(self, url, **kwargs):
        return {"url": url, "status": 200, "blocked": False,
                "challenge": {"verdict": "none"}, "solved": False}

    async def close(self):
        return {"closed": True}


def test_dispatch_snapshot_with_fake_session():
    server = AgentBrowserServer()
    server.session = _FakeSession()
    try:
        result = server.dispatch("browser_snapshot", {"max_nodes": 5})
        assert result["refs"] == ["e1"] and "[ref=e1]" in result["snapshot"]
        nav = server.dispatch("browser_navigate", {"url": "https://example.com"})
        assert nav["status"] == 200
    finally:
        server.close()


def test_dispatch_unknown_tool_is_agent_error():
    server = AgentBrowserServer()
    try:
        try:
            server.dispatch("browser_teleport", {})
        except ToolError as exc:
            assert "unknown tool" in str(exc)
        else:
            raise AssertionError("expected ToolError")
        response = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                  "params": {"name": "browser_teleport"}})
        assert response["result"]["isError"] is True
    finally:
        server.close()


def test_click_without_snapshot_is_agent_error():
    session = BrowserSession()
    session.page = object()  # pretend the page exists; no refs captured yet
    try:
        asyncio.run(session.click(ref="e1"))
    except ToolError as exc:
        assert "no snapshot" in str(exc)
    else:
        raise AssertionError("expected ToolError")


def test_stale_ref_is_agent_error():
    session = BrowserSession()
    session.page = object()
    session._refs = {"e1": {"ref": "e1"}}       # has a snapshot...
    session._nodes = [{"ref": "e1"}]
    try:
        asyncio.run(session.click(ref="e99"))    # ...but the ref is stale
    except ToolError as exc:
        assert "stale ref" in str(exc)
    else:
        raise AssertionError("expected ToolError")


# ─────────────────────────────────────────────────────────────
#  Engine launch guards
# ─────────────────────────────────────────────────────────────

def test_unknown_launch_engine_raises():
    try:
        asyncio.run(launch_stealth_browser("netscape"))
    except ValueError as exc:
        assert "unknown browser engine" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_capability_report_shape():
    report = capability_report()
    assert isinstance(report["engines"], list)
    assert set(report) >= {"engines", "best_engine", "curl_cffi", "patchright", "camoufox"}


def test_available_engines_is_a_subset_of_known():
    assert set(available_engines()) <= {"patchright", "camoufox", "playwright"}


def test_run_async_inside_running_loop():
    async def wrapper():
        return _run_async(asyncio.sleep(0, result=7))

    assert asyncio.run(wrapper()) == 7


# ─────────────────────────────────────────────────────────────
#  Scraper wiring
# ─────────────────────────────────────────────────────────────

class _FakePage:
    def __init__(self, url, html):
        self.url = url
        self._html = html

    async def content(self):
        return self._html

    async def goto(self, *args, **kwargs):
        return None


def _isolated_config(**overrides):
    """Config whose runtime artifacts land in a throwaway dir.

    A suite must never write the live output/scraper.log, output/session_health.json
    or output/safety.json: the ledger is the real account's daily budget and the
    session-health file is real run history.
    """
    from config import Config

    tmp = Path(tempfile.mkdtemp(prefix="li_test_"))
    config = Config()
    config.LOG_FILE = str(tmp / "scraper.log")
    config.SESSION_HEALTH_FILE = str(tmp / "session_health.json")
    config.SAFETY_LEDGER_FILE = str(tmp / "safety.json")
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _scraper_with(page, **overrides):
    from linkedin_scraper import LinkedInScraper

    config = _isolated_config(**overrides)
    config.USE_PERSISTENT_SESSION = False
    scraper = LinkedInScraper(config)
    scraper.page = page
    return scraper


def test_scraper_classifies_cloudflare_as_block():
    page = _FakePage("https://www.linkedin.com/in/x", CF_INTERSTITIAL)
    scraper = _scraper_with(page, CF_AUTO_SOLVE=False)
    assert asyncio.run(scraper._check_page_block_status()) is True
    assert scraper._block_reason == "cloudflare"


def test_scraper_clean_page_is_not_blocked():
    page = _FakePage("https://www.linkedin.com/in/x", CLEAN_PAGE)
    scraper = _scraper_with(page, CF_AUTO_SOLVE=False)
    assert asyncio.run(scraper._check_page_block_status()) is False
    assert scraper._block_reason == ""


def test_scraper_turnstile_reason_maps_to_cloudflare():
    page = _FakePage("https://www.linkedin.com/in/x", TURNSTILE_PAGE)
    scraper = _scraper_with(page, CF_AUTO_SOLVE=False)
    assert asyncio.run(scraper._check_page_block_status()) is True
    assert scraper._block_reason == "cloudflare"


def test_scraper_captcha_blocks_without_solver_wait():
    """LinkedIn's own captcha must rotate immediately — waiting on it would
    burn the whole solver timeout on every captcha page."""
    page = _FakePage("https://www.linkedin.com/search/results/people/",
                     "<html>Please prove you're not a robot</html>")
    scraper = _scraper_with(page)  # CF_AUTO_SOLVE left at its default (True)
    started = time.time()
    assert asyncio.run(scraper._check_page_block_status()) is True
    assert scraper._block_reason == "captcha"
    assert time.time() - started < 2.0


def test_solve_challenge_clean_page_returns_immediately():
    page = _FakePage("https://x", CLEAN_PAGE)
    assert asyncio.run(solve_challenge(page, timeout=2)) is True


def test_solve_challenge_walled_page_times_out():
    page = _FakePage("https://x", CF_INTERSTITIAL)
    assert asyncio.run(solve_challenge(page, timeout=0.6, poll=0.2)) is False


class _TokenPage(_FakePage):
    """A walled document whose Turnstile widget has already produced a token."""

    async def evaluate(self, expression):
        return "turnstile-solved-token"


def test_solve_challenge_accepts_solved_token():
    page = _TokenPage("https://x", CF_INTERSTITIAL)
    assert asyncio.run(solve_challenge(page, timeout=2, poll=0.2)) is True


# ─────────────────────────────────────────────────────────────
#  v7.4 hardening: clearance replay, header walls, wall gates
# ─────────────────────────────────────────────────────────────

def test_classify_cf_mitigated_header_on_200():
    """``cf-mitigated: challenge`` is a wall even on a 200 with a clean body."""
    challenge = classify_page("https://x", CLEAN_PAGE, {"cf-mitigated": "challenge"}, 200)
    assert challenge.verdict == cf_bypass.CLOUDFLARE and challenge.hard


def test_playwright_cookies_use_registrable_domain():
    payload = cf_bypass.playwright_cookies(
        "https://www.linkedin.com/in/x", {"cf_clearance": "abc", "": "dropped"})
    assert len(payload) == 1
    assert payload[0]["domain"] == ".linkedin.com"
    assert payload[0]["secure"] and payload[0]["httpOnly"]


def test_playwright_cookies_require_host_and_jar():
    assert cf_bypass.playwright_cookies("https://x", {}) == []
    assert cf_bypass.playwright_cookies("", {"cf_clearance": "a"}) == []


def test_cookie_domain_handles_ips_and_multi_part_suffixes():
    assert cf_bypass._cookie_domain("127.0.0.1") == "127.0.0.1"
    assert cf_bypass._cookie_domain("localhost") == "localhost"
    assert cf_bypass._cookie_domain("www.linkedin.com") == ".linkedin.com"
    assert cf_bypass._cookie_domain("www.bbc.co.uk") == ".bbc.co.uk"
    payload = cf_bypass.playwright_cookies("http://127.0.0.1:8765/x", {"cf_clearance": "a"})
    assert payload[0]["domain"] == "127.0.0.1"


def test_retry_pass_recovers_a_dead_browser_once():
    import linkedin_scraper as L

    saved = L.human_delay
    L.human_delay = _noop
    try:
        scraper = _scraper_with(_FakePage("https://www.linkedin.com/in/x", CLEAN_PAGE))
        scraper.failed_urls = [{"url": "https://www.linkedin.com/in/a",
                                "company": "", "title": ""}]
        scraper._browser_closed = True
        recoveries = []

        async def fake_recover():
            recoveries.append(True)
            return True

        async def fake_scrape(url, **kwargs):
            return None

        scraper._recover_closed_browser = fake_recover
        scraper.scrape_profile = fake_scrape
        assert asyncio.run(scraper._retry_failed_profiles()) == 0
        assert recoveries == [True], "the retry pass must relaunch a dead browser once"
    finally:
        L.human_delay = saved


class _CookieContext:
    def __init__(self, jar):
        self.jar = dict(jar)

    async def cookies(self):
        return [{"name": name, "value": value} for name, value in self.jar.items()]


class _ClearanceThenCleanPage(_FakePage):
    """Walled document whose context already holds a clearance cookie."""

    def __init__(self):
        super().__init__("https://x", CF_INTERSTITIAL)
        self.context = _CookieContext({"cf_clearance": "edge-token"})
        self.reloads = 0

    async def reload(self, **kwargs):
        self.reloads += 1
        self._html = CLEAN_PAGE


def test_solve_challenge_reloads_when_clearance_lands():
    page = _ClearanceThenCleanPage()
    assert asyncio.run(solve_challenge(page, timeout=2, poll=0.1)) is True
    assert page.reloads == 1, "a clearance cookie must trigger exactly one reload"


class _SeededContext:
    def __init__(self):
        self.added = []

    async def add_cookies(self, payload):
        self.added.extend(payload)

    async def cookies(self):
        return [{"name": "cf_clearance", "value": "fresh-edge"},
                {"name": "li_at", "value": "session-secret"}]


def test_scraper_seeds_and_persists_clearance():
    with tempfile.TemporaryDirectory() as tmp:
        scraper = _scraper_with(_FakePage("https://www.linkedin.com/in/x", CLEAN_PAGE))
        scraper.clearance = ClearanceCache(str(Path(tmp) / "clearance.json"), ttl=600)
        scraper.clearance.put("https://www.linkedin.com", {"cf_clearance": "seed"},
                              proxy=None, user_agent="UA")
        scraper.context = _SeededContext()
        scraper._active_user_agent = "UA"

        assert asyncio.run(scraper._seed_clearance()) == 1
        assert scraper.context.added[0]["domain"] == ".linkedin.com"
        assert asyncio.run(scraper._persist_clearance()) == 1

        jar = scraper.clearance.get("https://www.linkedin.com", None, "UA")
        assert jar["cf_clearance"] == "fresh-edge"
        assert "li_at" not in jar, "only clearance cookies may be cached"


def test_profile_wall_fails_instead_of_saving_an_empty_record():
    import linkedin_scraper as L

    saved = (L.human_delay, L.random_scroll)
    L.human_delay = _noop
    L.random_scroll = _noop
    try:
        page = _FakePage("https://www.linkedin.com/in/x", CF_INTERSTITIAL)
        scraper = _scraper_with(page, CF_AUTO_SOLVE=False)
        profile = asyncio.run(scraper.scrape_profile(
            "https://www.linkedin.com/in/x", search_company="Acme", job_title="Recruiter"))
        assert profile is None
        assert not scraper.results, "a walled page must never become a record"
        assert scraper.failed_urls[0]["url"].endswith("/in/x")
        assert scraper._block_reason == "cloudflare"
    finally:
        L.human_delay, L.random_scroll = saved


def test_session_break_survives_dead_browser():
    class _DeadPage:
        url = "https://www.linkedin.com/feed/"

        async def goto(self, *args, **kwargs):
            raise RuntimeError("Target page, context or browser has been closed")

    scraper = _scraper_with(_DeadPage())
    scraper.config.MIN_SESSION_BREAK_SECONDS = 1
    scraper.config.MAX_SESSION_BREAK_SECONDS = 1
    calls = []

    async def fake_recover():
        calls.append(True)
        return True

    scraper._recover_closed_browser = fake_recover
    asyncio.run(scraper._take_session_break())  # must not raise
    assert calls == [True]


def test_engine_escalation_walks_the_ladder():
    import linkedin_scraper as L

    original = L.available_engines
    L.available_engines = lambda: ["playwright", "patchright", "camoufox"]
    try:
        scraper = _scraper_with(_FakePage("https://x", CLEAN_PAGE))
        scraper.engine_handle = type("_Handle", (), {"engine": "playwright"})()
        assert scraper._escalate_engine() == "patchright"
        assert scraper._prefer_scriptable_override is False
        assert scraper._escalate_engine() == "camoufox"
        assert scraper._escalate_engine() == ""
    finally:
        L.available_engines = original


def test_cf_hardening_knobs_default_on():
    from config import Config

    config = Config()
    assert config.CF_SEED_CLEARANCE is True
    assert config.CF_ENGINE_ESCALATION is True


# ─────────────────────────────────────────────────────────────
#  CLI surface
# ─────────────────────────────────────────────────────────────

def test_cli_has_browser_and_fetch_commands():
    import cli

    parser = cli.build_parser()
    args = parser.parse_args(["browser", "--url", "https://x", "--snapshot", "--json"])
    assert args.command == "browser" and args.snapshot and args.json
    mcp_args = parser.parse_args(["mcp-browser", "--headless"])
    assert mcp_args.command == "mcp-browser" and mcp_args.headless
    fetch_args = parser.parse_args(["fetch", "https://x", "--json"])
    assert fetch_args.command == "fetch" and fetch_args.url == "https://x"


def test_cli_doctor_reports_stealth():
    import cli

    checks = {check["name"]: check for check in cli.run_doctor("output/test_doctor.db")}
    assert "stealth:engines" in checks and "agent-browser" in checks
    assert checks["agent-browser"]["ok"]


# ─────────────────────────────────────────────────────────────
#  v7.6: external CAPTCHA solving + no-limits mode
# ─────────────────────────────────────────────────────────────

def test_captcha_solver_unconfigured_is_a_noop():
    import captcha_solver as cs

    solver = cs.CaptchaSolver()
    assert solver.configured is False
    assert solver.solve_turnstile("0x4AAA", "https://x") == ""
    assert "not configured" in solver.describe()


def test_captcha_solver_capsolver_task_shape():
    import captcha_solver as cs

    calls = []

    def fake_post(url, payload, timeout, headers=None):
        calls.append((url, payload))
        if url.endswith("createTask"):
            return {"errorId": 0, "taskId": "task-1"}
        return {"errorId": 0, "status": "ready", "solution": {"token": "solved"}}

    saved = cs._post_json
    cs._post_json = fake_post
    try:
        solver = cs.CaptchaSolver("capsolver", "KEY123", timeout=20, poll=1)
        token = solver.solve_turnstile("0x4AAA", "https://target/x", action="login")
    finally:
        cs._post_json = saved

    assert token == "solved"
    create_url, create = calls[0]
    assert create_url.endswith("/createTask")
    assert create["clientKey"] == "KEY123"
    assert create["task"]["type"] == "AntiTurnstileTaskProxyLess"
    assert create["task"]["websiteKey"] == "0x4AAA"
    assert create["task"]["metadata"]["action"] == "login"
    assert calls[1][1]["taskId"] == "task-1"


def test_captcha_solver_2captcha_task_shape():
    import captcha_solver as cs

    seen = []

    def fake_form(url, fields, timeout):
        seen.append(fields)
        return {"status": 1, "request": "777"}

    saved = cs._post_form
    cs._post_form = fake_form
    try:
        solver = cs.CaptchaSolver("2captcha", "KEY", timeout=20)
        task_id = solver._create_2captcha("turnstile", "0x4AAA", "https://target/x",
                                          {"action": "login", "cdata": ""})
    finally:
        cs._post_form = saved

    assert task_id == "777"
    assert seen[0]["method"] == "turnstile"
    assert seen[0]["sitekey"] == "0x4AAA"
    assert seen[0]["action"] == "login"
    assert seen[0]["json"] == "1"


def test_solver_rescue_skips_a_page_without_a_sitekey():
    class _Page(_FakePage):
        async def evaluate(self, expression):
            return {"family": "", "sitekey": "", "url": self.url}

    page = _Page("https://x", CF_INTERSTITIAL)
    solver = cs_stub()
    assert asyncio.run(cf_bypass._solver_rescue(
        page, Challenge(cf_bypass.CLOUDFLARE), solver)) is False
    assert solver.calls == []


class _SolverPage(_FakePage):
    """Walled page that becomes clean the moment a token is injected."""

    def __init__(self, url, html, target):
        super().__init__(url, html)
        self._target = target
        self.injected = 0
        self.reloads = 0

    async def evaluate(self, expression):
        if "data-sitekey" in expression:
            return self._target
        if "const names" in expression:
            self.injected += 1
            self._html = CLEAN_PAGE
            return 1
        return ""

    async def reload(self, **kwargs):
        self.reloads += 1
        return None


def cs_stub():
    class _Stub:
        configured = True
        provider = "capsolver"
        timeout = 5.0

        def __init__(self):
            self.calls = []

        def solve(self, family, sitekey, url, **kwargs):
            self.calls.append((family, sitekey, url))
            return "solved-token"

    return _Stub()


def test_solver_rescue_injects_the_token_and_clears_the_wall():
    target = {"family": "turnstile", "sitekey": "0x4AAA",
              "url": "https://target/x", "action": "", "cdata": ""}
    page = _SolverPage("https://target/x", CF_INTERSTITIAL, target)
    solver = cs_stub()
    assert asyncio.run(cf_bypass._solver_rescue(
        page, Challenge(cf_bypass.CLOUDFLARE), solver)) is True
    assert solver.calls == [("turnstile", "0x4AAA", "https://target/x")]
    assert page.injected == 1


def test_no_limits_disables_every_cap():
    import cli
    from config import Config

    config = Config()
    cli._apply_no_limits(config)
    assert config.SAFE_MODE is False
    for key in ("MAX_PAGES_PER_COMPANY", "MAX_JOB_PAGES", "MAX_CANDIDATE_PAGES",
                "MAX_DAILY_SEARCHES", "MAX_BLOCK_RECOVERIES",
                "SAFETY_MAX_WALLS_PER_DAY", "SAFETY_MAX_PROFILES_PER_DAY"):
        assert getattr(config, key) >= 100_000, key
    assert config.MAX_PROFILES_PER_RUN == 0
    assert config.SAFETY_COOLDOWN_SECONDS == 0


def test_cli_run_parser_exposes_captcha_and_no_limits_flags():
    import cli

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--no-limits", "--captcha-provider", "capsolver",
                              "--captcha-key", "KEY"])
    assert args.no_limits is True
    assert args.captcha_provider == "capsolver"
    assert args.captcha_key == "KEY"


def main():
    tests = [
        test_import_is_light,
        test_classify_clean_page,
        test_classify_cloudflare_interstitial,
        test_classify_cloudflare_header_only,
        test_classify_turnstile_widget,
        test_turnstile_on_content_page_is_not_a_wall,
        test_classify_other_bot_walls,
        test_classify_rate_limit_and_authwall,
        test_classify_empty_403_is_edge_block,
        test_retry_after_is_parsed,
        test_block_reason_mapping,
        test_clearance_cache_roundtrip_and_scope,
        test_clearance_cache_ttl_and_prune,
        test_cookie_header,
        test_impersonate_matches_user_agent,
        test_impersonate_returns_a_real_curl_target,
        test_platform_for_user_agent,
        test_flaresolverr_unconfigured,
        test_flaresolverr_parses_solution,
        test_flaresolverr_reports_failure,
        test_fetch_ladder_short_circuits_on_clean_curl,
        test_fetch_ladder_falls_through_to_solverr,
        test_fetch_ladder_reaches_browser_last,
        test_fetch_rejects_non_http,
        test_format_snapshot_renders_refs,
        test_format_snapshot_truncates_and_escapes,
        test_ref_selector,
        test_tool_specs_cover_all_tools,
        test_mcp_initialize_and_tools_list,
        test_dispatch_snapshot_with_fake_session,
        test_dispatch_unknown_tool_is_agent_error,
        test_click_without_snapshot_is_agent_error,
        test_stale_ref_is_agent_error,
        test_unknown_launch_engine_raises,
        test_capability_report_shape,
        test_available_engines_is_a_subset_of_known,
        test_run_async_inside_running_loop,
        test_scraper_classifies_cloudflare_as_block,
        test_scraper_clean_page_is_not_blocked,
        test_scraper_turnstile_reason_maps_to_cloudflare,
        test_scraper_captcha_blocks_without_solver_wait,
        test_solve_challenge_clean_page_returns_immediately,
        test_solve_challenge_walled_page_times_out,
        test_solve_challenge_accepts_solved_token,
        test_classify_cf_mitigated_header_on_200,
        test_playwright_cookies_use_registrable_domain,
        test_playwright_cookies_require_host_and_jar,
        test_cookie_domain_handles_ips_and_multi_part_suffixes,
        test_retry_pass_recovers_a_dead_browser_once,
        test_solve_challenge_reloads_when_clearance_lands,
        test_scraper_seeds_and_persists_clearance,
        test_profile_wall_fails_instead_of_saving_an_empty_record,
        test_session_break_survives_dead_browser,
        test_engine_escalation_walks_the_ladder,
        test_cf_hardening_knobs_default_on,
        test_cli_has_browser_and_fetch_commands,
        test_cli_doctor_reports_stealth,
        test_captcha_solver_unconfigured_is_a_noop,
        test_captcha_solver_capsolver_task_shape,
        test_captcha_solver_2captcha_task_shape,
        test_solver_rescue_skips_a_page_without_a_sitekey,
        test_solver_rescue_injects_the_token_and_clears_the_wall,
        test_no_limits_disables_every_cap,
        test_cli_run_parser_exposes_captcha_and_no_limits_flags,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            import traceback
            print(f"FAIL {fn.__name__}: {exc.__class__.__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} cf/agent tests passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
