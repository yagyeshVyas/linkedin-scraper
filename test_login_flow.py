"""
Behavioral tests for the login power-up, driving the REAL login() decision
tree against a fake Playwright page/context (no browser, no network, no real
sleeps). Run with:  .venv/Scripts/python.exe test_login_flow.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
import linkedin_scraper as L


class FakeElement:
    async def click(self):
        pass

    async def type(self, char, delay=0):
        self.page.typed += 1


class FakePage:
    def __init__(self, stages=None, nav_after_typing=True, error=False, fail_first_selector=False):
        self.stages = list(stages or [])
        self.url = "about:blank"
        self.typed = 0
        self.nav_after_typing = nav_after_typing
        self.error = error
        self.fail_first_selector = fail_first_selector
        self.selector_failures = 0
        self.gotos = 0
        self.nav_checks = 0

    async def goto(self, url, **kwargs):
        self.gotos += 1
        self.url = self.stages.pop(0) if self.stages else url

    async def query_selector(self, selector):
        if "global-nav" in selector:
            self.nav_checks += 1
            blocked = any(x in self.url for x in ["checkpoint", "challenge", "verification", "authwall"])
            # Nav appears only on a real authenticated page (never on the
            # login form, an error page, or a checkpoint interstitial).
            if self.typed and self.nav_after_typing and not self.error and not blocked:
                return object()
            if self.nav_checks >= 3:  # challenge poll eventually resolves
                return object()
            return None
        if "#error-for-password" in selector:
            return object() if self.error else None
        return None

    async def evaluate(self, expr):
        return "That email or password isn't quite right" if self.error else ""

    async def content(self):
        return ""

    async def wait_for_selector(self, selector, timeout=None):
        if selector == "#username" and self.fail_first_selector and self.selector_failures == 0:
            self.selector_failures += 1
            raise TimeoutError("selector timed out")
        el = FakeElement()
        el.page = self
        return el

    async def click(self, selector):
        if "submit" in selector and self.stages:
            # LinkedIn navigates on submit (e.g. to a checkpoint page)
            self.url = self.stages.pop(0)

    async def wait_for_load_state(self, *args, **kwargs):
        pass


class FakeContext:
    def __init__(self):
        self.cleared = False
        self.saved_path = None

    async def clear_cookies(self):
        self.cleared = True

    async def storage_state(self, path=None):
        self.saved_path = path


def make_scraper(cfg, page, ctx=None):
    s = L.LinkedInScraper(cfg)
    s.page = page
    s.context = ctx or FakeContext()
    return s


def test_session_restore_success():
    cfg = Config()
    s = make_scraper(cfg, FakePage(stages=["https://www.linkedin.com/feed/"]))
    session = s._session_file()
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("{}", encoding="utf-8")
    try:
        ok = asyncio.run(s.login())
        assert ok, "restored session should log in without typing"
        assert not s.context.cleared, "valid session must not clear cookies"
        assert s.page.typed == 0, "valid session must not type credentials"
        assert s.telemetry["login"] == {"ok": True, "reason": "restored"}, s.telemetry["login"]
        assert [e["kind"] for e in s.telemetry["events"]] == ["login"], s.telemetry["events"]
        assert s.telemetry["ban_risk"] == 0, s.telemetry["ban_risk"]  # clean session, no risk
    finally:
        session.unlink(missing_ok=True)


def test_stale_session_falls_back_to_fresh_login():
    cfg = Config()
    # restore goto → redirected to login (stale); fresh login → login page; typing
    # makes the nav appear so _is_logged_in() succeeds after submit
    page = FakePage(stages=["https://www.linkedin.com/login",
                            "https://www.linkedin.com/login"])
    s = make_scraper(cfg, page)
    session = s._session_file()
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("{}", encoding="utf-8")
    try:
        ok = asyncio.run(s.login())
        assert ok, "stale session should recover via fresh login"
        assert s.context.cleared, "stale session must be cleared before fresh login"
        assert s.page.typed > 0, "fresh login must type credentials"
        assert s.context.saved_path == str(session), "new session must be persisted"
    finally:
        session.unlink(missing_ok=True)


def test_bad_credentials_give_up_without_retry():
    cfg = Config()
    cfg.USE_PERSISTENT_SESSION = False
    page = FakePage(stages=["https://www.linkedin.com/login"], error=True)
    s = make_scraper(cfg, page)
    ok = asyncio.run(s.login())
    assert not ok, "bad credentials must fail"
    assert s._last_login_error == "credentials"
    assert s.telemetry["login"]["ok"] is False
    assert s.telemetry["login"]["reason"] == "credentials"
    assert s.telemetry["login"].get("detail"), "LinkedIn's own error message must be captured"
    # exactly one login-page load: no retry when credentials are rejected
    assert page.gotos == 1, f"expected 1 goto, got {page.gotos}"
    # events + ban risk reflect the failure immediately
    kinds = [e["kind"] for e in s.telemetry["events"]]
    assert "login" in kinds, kinds
    assert s.telemetry["ban_risk"] >= 40, s.telemetry["ban_risk"]


def test_transient_error_retries_then_succeeds():
    cfg = Config()
    cfg.USE_PERSISTENT_SESSION = False
    page = FakePage(fail_first_selector=True)
    s = make_scraper(cfg, page)
    ok = asyncio.run(s.login())
    assert ok, "transient selector failure should retry and succeed"
    assert page.gotos >= 2, f"expected >=2 gotos, got {page.gotos}"


def test_challenge_polls_until_resolved():
    cfg = Config()
    cfg.USE_PERSISTENT_SESSION = False
    page = FakePage(stages=["https://www.linkedin.com/login",
                            "https://www.linkedin.com/checkpoint/challenge/verify"])
    s = make_scraper(cfg, page)
    ok = asyncio.run(s.login())
    assert ok, "challenge should resolve via polling once the nav appears"
    assert page.nav_checks >= 3, "helper must poll more than once"
    assert s.telemetry["login"] == {"ok": True, "reason": "fresh_after_verification"}, s.telemetry["login"]
    assert s.telemetry["events"][-1]["kind"] == "login", s.telemetry["events"]


if __name__ == "__main__":
    # Patch sleeps/typing delays out of the module namespace (no real waiting).
    class _FastAsync:
        @staticmethod
        async def sleep(*a, **kw):
            pass

        @staticmethod
        def get_running_loop():
            return asyncio.get_running_loop()

    L.asyncio = _FastAsync
    L.human_delay = _FastAsync.sleep
    L.save_session_health_live = _FastAsync.sleep  # keep the real session_health.json clean
    L.save_session_health = _FastAsync.sleep

    tests = [test_session_restore_success, test_stale_session_falls_back_to_fresh_login,
             test_bad_credentials_give_up_without_retry, test_transient_error_retries_then_succeeds,
             test_challenge_polls_until_resolved]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll login flow tests passed.")
