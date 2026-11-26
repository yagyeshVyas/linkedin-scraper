"""Behavioral tests for the v7.0 armor: fingerprints, proxies, audit, hands.

No browser and no network. Run with:

    .venv/Scripts/python.exe test_brutal.py
"""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import behavior
import cf_bypass
import fingerprint
import proxy_intel
import stealth_audit
import agent_browser
from fingerprint import (country_from_proxy, detect_country, generate_profile,
                         init_script, parse_proxy_list, profile_for_proxy,
                         score_profile, strip_proxy_tag, summary,
                         supported_countries, validate_profile)
from proxy_intel import ProxyIntel
from stealth_audit import CHECKS, evaluate, report_text


GOOD_SIGNALS = {
    "url": "https://example.com",
    "userAgent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "platform": "Win32",
    "webdriver": "undefined",
    "languages": ["en-US", "en"],
    "language": "en-US",
    "plugins": 3,
    "mimeTypes": 2,
    "hardwareConcurrency": 12,
    "deviceMemory": 16,
    "maxTouchPoints": 0,
    "webglVendor": "Google Inc. (NVIDIA)",
    "webglRenderer": ("ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, "
                      "D3D11)"),
    "timezone": "America/New_York",
    "screen": {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1080,
               "colorDepth": 24},
    "viewport": {"width": 1920, "height": 1080, "dpr": 1.0},
    "hasChrome": True,
    "chromeKeys": ["runtime", "loadTimes", "csi", "app"],
    "uaData": {"brands": [{"brand": "Not_A Brand", "version": "24"},
                          {"brand": "Chromium", "version": "131"},
                          {"brand": "Google Chrome", "version": "131"}],
               "platform": "Windows", "mobile": False},
    "cdcProps": [],
    "iframeWebdriver": "undefined",
}


# ─────────────────────────────────────────────────────────────
#  Fingerprints
# ─────────────────────────────────────────────────────────────

def test_generated_profiles_are_coherent():
    for seed in range(300):
        profile = generate_profile(seed=seed)
        issues = validate_profile(profile)
        assert not issues, f"seed {seed}: {issues}"


def test_generation_is_reproducible():
    assert generate_profile(seed=42) == generate_profile(seed=42)
    assert generate_profile(seed=42) != generate_profile(seed=43)


def test_ua_and_platform_agree():
    for seed in range(60):
        profile = generate_profile(seed=seed)
        ua = profile["user_agent"]
        if "Windows NT" in ua:
            assert profile["platform"] == "Win32"
        elif "Macintosh" in ua:
            assert profile["platform"] == "MacIntel"
        else:
            assert profile["platform"] == "Linux x86_64"


def test_viewport_fits_screen_and_dpr_is_plausible():
    for seed in range(60):
        profile = generate_profile(seed=seed)
        width, height = profile["viewport"]
        screen_w, screen_h = profile["screen"]
        assert 0 < width <= screen_w and 0 < height <= screen_h
        assert profile["dpr"] in (1.0, 1.25, 1.5, 2.0)


def test_gpu_matches_operating_system():
    for os_name, tokens in (("Windows", ("NVIDIA", "Intel", "AMD")),
                            ("macOS", ("Apple",)),
                            ("Linux", ("Mesa", "AMD", "NVIDIA", "Intel"))):
        for seed in range(15):
            profile = generate_profile(seed=seed, os_name=os_name)
            assert profile["platform_label"] == os_name
            assert any(token in profile["webgl_renderer"] for token in tokens)


def test_country_identity_is_coherent():
    for country in supported_countries():
        for seed in range(3):
            profile = generate_profile(seed=seed, country=country)
            assert profile["country"] == country
            assert profile["timezone_id"].split("/")[0] in (
                "America", "Europe", "Asia", "Australia", "Pacific", "Africa")
            assert profile["locale"].split("-")[-1].upper() == country or country in (
                "GB", "IE", "AU", "NZ", "SG", "IN", "AE", "ZA", "CA") and \
                profile["locale"].startswith("en")


def test_proxy_country_tags():
    assert country_from_proxy("http://u:p@host:8080#DE") == "DE"
    assert country_from_proxy("http://u:p@host:8080?cc=gb") == "GB"
    assert country_from_proxy("http://u:p@host:8080") == ""
    assert strip_proxy_tag("http://u:p@host:8080#DE") == "http://u:p@host:8080"
    profile = profile_for_proxy("http://u:p@host:8080#JP", seed=7)
    assert profile["country"] == "JP"
    assert profile["timezone_id"] == "Asia/Tokyo"


def test_parse_proxy_list():
    rows = parse_proxy_list(["http://a:1#US", "", "http://b:2"])
    assert rows == [{"url": "http://a:1", "country": "US"},
                    {"url": "http://b:2", "country": ""}]


def test_init_script_renders_without_brace_damage():
    profile = generate_profile(seed=5)
    script = init_script(profile)
    assert "{{" not in script and "}}" not in script
    assert script.count("{") == script.count("}")
    assert profile["webgl_renderer"] in script
    assert profile["locale"] in script
    assert "navigator" in script


def test_validator_catches_planted_mismatches():
    base = generate_profile(seed=11)
    cases = []
    wrong_platform = "MacIntel" if base["platform"] != "MacIntel" else "Win32"
    cases.append(dict(base, platform=wrong_platform))
    cases.append(dict(base, ua_version="999"))
    cases.append(dict(base, webgl_renderer="ANGLE (Google, Vulkan SwiftShader)"))
    cases.append(dict(base, viewport=(4000, 3000)))
    cases.append(dict(base, hardware={"deviceMemory": "lots", "hardwareConcurrency": 8,
                                      "maxTouchPoints": 0}))
    for case in cases:
        assert validate_profile(case), f"validator missed {case}"
        assert score_profile(case) < 100


def test_summary_mentions_identity():
    text = summary(generate_profile(seed=3))
    assert "Chrome" in text and "cores" in text


def test_country_detection_precedence():
    assert detect_country(override="GB") == "GB"
    assert detect_country(proxy_country="DE") == "DE"
    assert detect_country("ZZ", "FR") == "FR"
    assert detect_country() in supported_countries()


def test_safe_viewport_and_screen_limit():
    assert generate_profile(seed=1, safe_viewport=True)["viewport"] == (1366, 768)
    small = generate_profile(seed=1, screen_limit=(1366, 768))
    assert small["viewport"][0] <= 1366 and small["viewport"][1] <= 768
    big = generate_profile(seed=1, screen_limit=(1920, 1080))
    assert big["screen"][0] <= 1920 and big["screen"][1] <= 1080


def test_ua_version_can_be_pinned_to_binary():
    profile = generate_profile(seed=3, chrome_major=151)
    assert profile["ua_version"] == "151"
    assert "Chrome/151." in profile["user_agent"]
    assert validate_profile(profile) == []


# ─────────────────────────────────────────────────────────────
#  Proxy intelligence
# ─────────────────────────────────────────────────────────────

P1, P2, P3 = "http://p1:8080", "http://p2:8080", "http://p3:8080"


def _intel(tmp_path=None, **kwargs):
    path = str(Path(tmp_path) / "intel.json") if tmp_path else "output/_intel_brutal.json"
    return ProxyIntel(path, **kwargs)


def test_round_robin_coverage_and_no_repeat():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        urls = intel.register([P1, P2, P3])
        cursor = 0
        picks = []
        for _ in range(6):
            url, cursor = intel.pick(urls, exclude=picks[-1] if picks else None,
                                     cursor=cursor)
            picks.append(url)
        assert set(picks) == set(urls)
        assert all(a != b for a, b in zip(picks, picks[1:]))


def test_single_proxy_and_exclusion():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        assert intel.pick([P1], exclude=None)[0] == P1
        assert intel.pick([P1], exclude=P1)[0] is None


def test_block_cools_a_proxy_down():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp, cooldown=300)
        intel.register([P1, P2])
        intel.mark_block(P1)
        stat = intel.get(P1)
        assert stat is not None and stat.blocks == 1
        assert stat.cooling()
        url, _ = intel.pick([P1, P2], cursor=0)
        assert url == P2, "a cooling proxy must be skipped when an alternative exists"
        assert intel.get(P1).score() < 100


def test_failures_retire_a_proxy():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp, dead_after=3)
        intel.register([P1, P2])
        for _ in range(3):
            intel.mark_failed(P1)
        stat = intel.get(P1)
        assert stat.consecutive_failures == 3
        assert stat.cooling()


def test_success_clears_cooldown():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        intel.register([P1])
        intel.mark_block(P1)
        assert intel.get(P1).cooling()
        intel.mark_ok(P1, latency_ms=250)
        stat = intel.get(P1)
        assert not stat.cooling() and stat.successes == 1
        assert 200 <= stat.latency_ms <= 300


def test_persistence_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        intel.register([P1, P2])
        intel.mark_ok(P1, latency_ms=120)
        intel.mark_block(P2)
        again = ProxyIntel(str(Path(tmp) / "intel.json"))
        assert again.get(P1).successes == 1
        assert again.get(P2).blocks == 1


def test_summary_shape():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        intel.register([{"url": P1, "country": "DE"}])
        rows = intel.summary()
        assert rows and rows[0]["score"] == 50
        assert rows[0]["country"] == "DE"


def test_prefer_country():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        intel.register([{"url": P1, "country": "US"}, {"url": P2, "country": "DE"}])
        url, _ = intel.pick([P1, P2], prefer_country="DE", cursor=0)
        assert url == P2


def test_all_cooling_returns_least_bad():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp, cooldown=60)
        intel.register([P1, P2])
        intel.mark_block(P1)
        intel.mark_block(P2)
        url, _ = intel.pick([P1, P2], cursor=0)
        assert url in (P1, P2), "must not drop to a direct connection while proxies exist"


def test_reset_cooling():
    with tempfile.TemporaryDirectory() as tmp:
        intel = _intel(tmp)
        intel.register([P1])
        intel.mark_block(P1)
        assert intel.reset_cooling() == 1
        assert not intel.get(P1).cooling()


# ─────────────────────────────────────────────────────────────
#  Stealth audit
# ─────────────────────────────────────────────────────────────

def test_clean_profile_scores_perfect():
    result = evaluate(dict(GOOD_SIGNALS))
    assert result["score"] == 100 and result["grade"] == "A"
    assert all(finding["ok"] for finding in result["findings"])


def test_webdriver_is_fatal_enough():
    signals = dict(GOOD_SIGNALS, webdriver="true")
    result = evaluate(signals)
    assert result["score"] < 80
    assert any(f["id"] == "webdriver" and not f["ok"] for f in result["findings"])


def test_swiftshader_fails_webgl():
    signals = dict(GOOD_SIGNALS,
                   webglRenderer="ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device))")
    result = evaluate(signals)
    finding = next(f for f in result["findings"] if f["id"] == "webgl")
    assert not finding["ok"]


def test_platform_mismatch_is_caught():
    signals = dict(GOOD_SIGNALS, platform="MacIntel")
    assert not next(f for f in evaluate(signals)["findings"]
                    if f["id"] == "platform")["ok"]


def test_expected_identity_mismatch_is_caught():
    profile = generate_profile(seed=9, country="DE")
    result = evaluate(dict(GOOD_SIGNALS), expected=profile)
    assert result["score"] < 100
    assert not next(f for f in result["findings"]
                    if f["id"] == "timezone")["ok"]


def test_missing_chrome_and_cdp_leak():
    signals = dict(GOOD_SIGNALS, hasChrome=False, chromeKeys=[],
                   cdcProps=["cdc_adoQpoasnfa76pfcZLmcfl_Array"])
    result = evaluate(signals)
    failed = {f["id"] for f in result["findings"] if not f["ok"]}
    assert {"chrome_runtime", "cdp"} <= failed


def test_iframe_webdriver_is_caught():
    signals = dict(GOOD_SIGNALS, iframeWebdriver="true")
    assert not next(f for f in evaluate(signals)["findings"]
                    if f["id"] == "iframe_webdriver")["ok"]


def test_report_text_mentions_grade_and_failures():
    text = report_text(evaluate(dict(GOOD_SIGNALS, webdriver="true")))
    assert "grade" in text and "FAIL" in text


class _ProbePage:
    def __init__(self, signals=None, error=None):
        self.signals = signals
        self.error = error

    async def evaluate(self, expression):
        if self.error:
            raise RuntimeError(self.error)
        return self.signals


def test_audit_page_grades_live_probe():
    result = asyncio.run(stealth_audit.audit_page(_ProbePage(dict(GOOD_SIGNALS))))
    assert result["score"] == 100


def test_audit_page_survives_probe_failure():
    result = asyncio.run(stealth_audit.audit_page(_ProbePage(error="detached")))
    assert result["grade"] == "F" and result["score"] == 0


def test_check_table_is_weighted():
    assert sum(weight for weight, _ in CHECKS.values()) == 128
    assert CHECKS["webdriver"][0] >= CHECKS["screen"][0]


# ─────────────────────────────────────────────────────────────
#  Humanizer
# ─────────────────────────────────────────────────────────────

class _Keyboard:
    def __init__(self):
        self.typed = []
        self.presses = []

    async def type(self, text):
        self.typed.append(text)

    async def press(self, key):
        self.presses.append(key)


class _Mouse:
    def __init__(self):
        self.moves = []
        self.clicks = []
        self.wheels = []

    async def move(self, x, y, steps=1):
        self.moves.append((x, y, steps))

    async def click(self, x, y, button="left"):
        self.clicks.append((x, y, button))

    async def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class _Locator:
    def __init__(self, box=None):
        self.box = box
        self.filled = []

    async def focus(self):
        return None

    async def fill(self, value):
        self.filled.append(value)

    async def press(self, key):
        return None

    async def bounding_box(self):
        return self.box

    async def click(self, button="left"):
        self.clicks = getattr(self, "clicks", 0) + 1


class _Page:
    def __init__(self):
        self.keyboard = _Keyboard()
        self.mouse = _Mouse()

    async def evaluate(self, expression, arg=None):
        return None


def _humanizer(level=2, seed=1):
    sleeps = []

    async def sleeper(seconds):
        sleeps.append(seconds)

    humanizer = behavior.Humanizer(level=level, rng=__import__("random").Random(seed),
                                   sleep=sleeper)
    return humanizer, sleeps


def test_humanizer_level_zero_does_not_wait():
    humanizer, sleeps = _humanizer(level=0)
    page = _Page()
    asyncio.run(humanizer.type_text(_Locator(), "hello", page=page, typo_rate=0))
    assert sleeps and all(seconds == 0 for seconds in sleeps)


def test_typing_hits_every_character():
    humanizer, _ = _humanizer(level=0)
    page = _Page()
    result = asyncio.run(humanizer.type_text(_Locator(), "abc def", page=page,
                                             typo_rate=0))
    assert page.keyboard.typed == list("abc def")
    assert result["chars"] == 7 and result["typos"] == 0


def test_typo_produces_a_backspace():
    humanizer, _ = _humanizer(level=0)
    page = _Page()
    result = asyncio.run(humanizer.type_text(_Locator(), "abc", page=page,
                                             typo_rate=1.0))
    assert result["typos"] == 3
    assert page.keyboard.presses.count("Backspace") == 3


def test_submit_presses_enter():
    humanizer, _ = _humanizer(level=0)
    page = _Page()
    asyncio.run(humanizer.type_text(_Locator(), "hi", page=page, typo_rate=0,
                                    submit=True))
    assert page.keyboard.presses[-1] == "Enter"


def test_key_delays_are_bounded():
    humanizer, _ = _humanizer(level=3)
    for char in "aA ,.!":
        delay = humanizer.key_delay(char)
        assert 0.0 <= delay <= 0.8


def test_click_moves_before_clicking():
    humanizer, _ = _humanizer(level=0)
    page = _Page()
    locator = _Locator(box={"x": 100, "y": 50, "width": 200, "height": 40})
    assert asyncio.run(humanizer.click(locator, page))
    assert page.mouse.moves and page.mouse.clicks


def test_session_rhythm_is_bounded():
    for hour in range(24):
        rhythm = behavior.Humanizer.session_rhythm(hour)
        assert 0.8 <= rhythm <= 1.8


# ─────────────────────────────────────────────────────────────
#  Agent browser v7 surface
# ─────────────────────────────────────────────────────────────

def test_new_tools_are_registered():
    names = {tool["name"] for tool in agent_browser.TOOLS}
    expected = {"browser_tabs", "browser_console", "browser_network", "browser_dialog",
                "browser_fill_form", "browser_upload", "browser_download",
                "browser_audit"}
    assert expected <= names
    spec_names = {spec["function"]["name"] for spec in agent_browser.TOOL_SPECS}
    assert spec_names == names


class _FakeSessionV7:
    running = True

    def info(self):
        return {"engine": "patchright", "headless": True, "running": True, "url": ""}

    async def tabs(self, action="list", index=0, url=""):
        return {"action": action, "count": 2, "tabs": [{"index": 0, "active": True}]}

    def console(self, clear=False, limit=100, errors_only=False):
        return {"count": 1, "entries": [{"type": "error", "text": "boom"}]}

    def network(self, clear=False, limit=100, contains="", status_min=0):
        return {"count": 1, "entries": [{"status": 200, "url": "https://x"}]}

    def dialog(self, action="dismiss", text=""):
        return {"policy": action, "prompt_text": text, "recent": []}

    async def fill_form(self, fields, submit=False):
        return {"filled": [field.get("ref") for field in fields],
                "count": len(fields), "submitted": submit}

    async def upload(self, ref=None, selector=None, files=None):
        return {"uploaded": len(files or []), "files": files}

    async def download(self, ref=None, selector=None, path=""):
        return {"path": path or "output/agent_downloads/x.bin", "bytes": 10}

    async def audit(self):
        return {"score": 96, "grade": "A", "findings": []}

    async def close(self):
        return {"closed": True}


def _server_with_fake():
    server = agent_browser.AgentBrowserServer()
    server.session = _FakeSessionV7()
    return server


def test_dispatch_tabs_and_telemetry():
    server = _server_with_fake()
    try:
        assert server.dispatch("browser_tabs", {"action": "list"})["count"] == 2
        assert server.dispatch("browser_console", {})["entries"][0]["type"] == "error"
        assert server.dispatch("browser_network", {})["entries"][0]["status"] == 200
        assert server.dispatch("browser_dialog", {"action": "accept", "text": "ok"})["policy"] == "accept"
    finally:
        server.close()


def test_dispatch_form_upload_download_audit():
    server = _server_with_fake()
    try:
        form = server.dispatch("browser_fill_form",
                               {"fields": [{"ref": "e1", "value": "x"}], "submit": True})
        assert form["count"] == 1 and form["submitted"]
        uploaded = server.dispatch("browser_upload",
                                   {"files": ["a.txt"], "ref": "e2"})
        assert uploaded["uploaded"] == 1
        downloaded = server.dispatch("browser_download", {"path": "out.bin"})
        assert downloaded["path"] == "out.bin"
        audited = server.dispatch("browser_audit", {})
        assert audited["score"] == 96
    finally:
        server.close()


def test_session_armor_defaults_to_generated_profile():
    session = agent_browser.BrowserSession(armor=True)
    assert session.fingerprint
    assert fingerprint.validate_profile(session.fingerprint) == []
    unarmored = agent_browser.BrowserSession(armor=False)
    assert unarmored.fingerprint == {}


# ─────────────────────────────────────────────────────────────
#  Scraper integration
# ─────────────────────────────────────────────────────────────

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


def test_scraper_strips_proxy_tags_and_remembers_country():
    from config import Config
    from linkedin_scraper import LinkedInScraper

    config = _isolated_config(PROXY_LIST=["http://u:p@a:1#DE", "http://u:p@b:1#US"],
                              PROXY_INTEL_ENABLED=False)
    scraper = LinkedInScraper(config)
    picked = scraper._next_proxy()
    assert picked == "http://u:p@a:1" or picked == "http://u:p@b:1"
    assert "#" not in picked
    assert scraper._proxy_country in ("DE", "US")


def test_scraper_intel_picks_healthy_proxy():
    from config import Config
    from linkedin_scraper import LinkedInScraper

    with tempfile.TemporaryDirectory() as tmp:
        config = _isolated_config(PROXY_LIST=["http://a:1", "http://b:1"],
                                  PROXY_INTEL_FILE=str(Path(tmp) / "intel.json"))
        scraper = LinkedInScraper(config)
        scraper.proxy_intel.mark_block("http://a:1")
        picked = scraper._next_proxy()
        assert picked == "http://b:1", "a cooling proxy must not be chosen"


def test_config_exposes_armor_flags():
    from config import Config

    config = Config()
    assert config.FINGERPRINT_ENGINE in ("generator", "static")
    assert config.PROXY_INTEL_ENABLED in (True, False)
    assert 0 <= config.HUMANIZE_LEVEL <= 3
    assert config.PREFER_SCRIPTABLE_ENGINE in (True, False)
    assert config.STEALTH_AUDIT_ON_LAUNCH in (True, False)


def test_scraper_script_patches_platform_and_ua_ch():
    """Regression: navigator.platform and the singular userAgentData.platform
    were missing, so every generated non-Windows identity contradicted the
    real binary (audit platform/ua_data failures)."""
    import inspect
    from linkedin_scraper import LinkedInScraper

    src = inspect.getsource(LinkedInScraper._launch_browser)
    assert "navigator, 'platform'" in src
    assert "platform: UA_PLATFORM" in src


def test_scraper_launch_audit_records_score():
    from config import Config
    from linkedin_scraper import LinkedInScraper

    config = _isolated_config(STEALTH_AUDIT_ON_LAUNCH=True, STEALTH_AUDIT_MIN_SCORE=90)
    scraper = LinkedInScraper(config)
    scraper.page = _ProbePage(dict(GOOD_SIGNALS))
    result = asyncio.run(scraper._audit_stealth())
    assert result["score"] == 100 and result["grade"] == "A"
    assert scraper.telemetry["stealth_score"] == 100
    assert scraper.telemetry["stealth_grade"] == "A"


def test_browser_closed_detection_and_recovery():
    from config import Config
    from linkedin_scraper import LinkedInScraper

    scraper = LinkedInScraper(_isolated_config())
    assert scraper._is_browser_closed(RuntimeError(
        "Target page, context or browser has been closed"))
    assert scraper._is_browser_closed(RuntimeError("TargetClosedError: page crashed"))
    assert not scraper._is_browser_closed(RuntimeError("Timeout 30000ms exceeded"))

    calls = []

    async def shutdown():
        calls.append("shutdown")

    async def launch():
        calls.append("launch")

    async def login():
        calls.append("login")
        return True

    scraper._shutdown_browser = shutdown
    scraper._launch_browser = launch
    scraper.login = login
    assert asyncio.run(scraper._recover_closed_browser()) is True
    assert calls == ["shutdown", "launch", "login"]


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

def test_cli_parsers_for_new_commands():
    import cli

    parser = cli.build_parser()
    stealth = parser.parse_args(["stealth-check", "--country", "DE", "--json"])
    assert stealth.command == "stealth-check" and stealth.country == "DE"
    scan = parser.parse_args(["proxy-scan", "--workers", "4", "--reset"])
    assert scan.command == "proxy-scan" and scan.workers == 4 and scan.reset
    browser = parser.parse_args(["browser", "--audit", "--no-armor"])
    assert browser.audit and browser.no_armor


def test_doctor_reports_armor_checks():
    import cli

    checks = {check["name"]: check for check in cli.run_doctor("output/_doctor_brutal.db")}
    assert checks["armor:fingerprint"]["ok"]
    assert checks["armor:audit"]["ok"]


def main():
    tests = [
        test_generated_profiles_are_coherent,
        test_generation_is_reproducible,
        test_ua_and_platform_agree,
        test_viewport_fits_screen_and_dpr_is_plausible,
        test_gpu_matches_operating_system,
        test_country_identity_is_coherent,
        test_proxy_country_tags,
        test_parse_proxy_list,
        test_init_script_renders_without_brace_damage,
        test_validator_catches_planted_mismatches,
        test_summary_mentions_identity,
        test_country_detection_precedence,
        test_safe_viewport_and_screen_limit,
        test_ua_version_can_be_pinned_to_binary,
        test_round_robin_coverage_and_no_repeat,
        test_single_proxy_and_exclusion,
        test_block_cools_a_proxy_down,
        test_failures_retire_a_proxy,
        test_success_clears_cooldown,
        test_persistence_roundtrip,
        test_summary_shape,
        test_prefer_country,
        test_all_cooling_returns_least_bad,
        test_reset_cooling,
        test_clean_profile_scores_perfect,
        test_webdriver_is_fatal_enough,
        test_swiftshader_fails_webgl,
        test_platform_mismatch_is_caught,
        test_expected_identity_mismatch_is_caught,
        test_missing_chrome_and_cdp_leak,
        test_iframe_webdriver_is_caught,
        test_report_text_mentions_grade_and_failures,
        test_audit_page_grades_live_probe,
        test_audit_page_survives_probe_failure,
        test_check_table_is_weighted,
        test_humanizer_level_zero_does_not_wait,
        test_typing_hits_every_character,
        test_typo_produces_a_backspace,
        test_submit_presses_enter,
        test_key_delays_are_bounded,
        test_click_moves_before_clicking,
        test_session_rhythm_is_bounded,
        test_new_tools_are_registered,
        test_dispatch_tabs_and_telemetry,
        test_dispatch_form_upload_download_audit,
        test_session_armor_defaults_to_generated_profile,
        test_scraper_strips_proxy_tags_and_remembers_country,
        test_scraper_intel_picks_healthy_proxy,
        test_config_exposes_armor_flags,
        test_scraper_script_patches_platform_and_ua_ch,
        test_scraper_launch_audit_records_score,
        test_browser_closed_detection_and_recovery,
        test_cli_parsers_for_new_commands,
        test_doctor_reports_armor_checks,
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
    print(f"\n{len(tests) - failed}/{len(tests)} brutal tests passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
