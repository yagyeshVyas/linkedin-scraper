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


def _scraper_with(page, **overrides):
    from config import Config
    from linkedin_scraper import LinkedInScraper

    config = Config()
    for key, value in overrides.items():
        setattr(config, key, value)
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
        test_cli_has_browser_and_fetch_commands,
        test_cli_doctor_reports_stealth,
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
