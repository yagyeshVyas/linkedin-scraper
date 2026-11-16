"""Cloudflare / bot-wall survival layer for the scraper.

Four independent tiers, cheapest first, each one able to hand its result to the
next:

  1. **Clearance cache** — ``cf_clearance`` / ``__cf_bm`` / Datadome cookies
     harvested from an earlier solve, scoped per (domain, proxy) so a cookie
     minted for one IP is never replayed from another.  Persisted atomically to
     ``output/clearance.json`` with a TTL.
  2. **curl_cffi** — real Chrome/Firefox TLS + HTTP/2 fingerprints.  Cloudflare
     scores the TLS handshake (JA3/JA4, cipher order, ALPN) before it ever looks
     at the User-Agent; stock ``requests`` loses that fight, curl_cffi wins it.
  3. **FlareSolverr** — optional sidecar (``CF_SOLVERR_URL``), a headless Chrome
     that does nothing but clear challenges and return cookies.
  4. **Stealth browser** — patchright (deep-patched Chrome) > camoufox
     (fingerprint-spoofed Firefox) > stock playwright.  Used both to solve a
     wall on demand and as the fallback of last resort.

Nothing here imports a browser or a network library at module import time: the
heavy engines are resolved lazily, inside the function that needs them.  That
keeps ``python test_*.py`` and the CLI's ``--help`` import-safe on a machine
with no Playwright browsers installed.

Public surface used elsewhere in the repo:

    classify_page(url, html, headers, status) -> Challenge
    classify_response(status, headers, body)  -> Challenge
    block_reason_for(verdict)                 -> str   ("cloudflare", ...)
    ClearanceCache(path, ttl)                 -> cache of harvested cookies
    TieredFetcher(...).fetch(url)             -> FetchResult
    launch_stealth_browser(...)               -> BrowserHandle
    solve_challenge(page, timeout)            -> bool
    capability_report()                       -> dict  (doctor / diagnostics)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  Verdicts
# ─────────────────────────────────────────────────────────────

NONE = "none"
CLOUDFLARE = "cloudflare"
TURNSTILE = "turnstile"
CAPTCHA = "captcha"
RATE_LIMIT = "rate_limit"
AUTHWALL = "authwall"
BOT_WALL = "bot_wall"          # DataDome / PerimeterX / Akamai / Incapsula
JS_REQUIRED = "js_required"

#: Verdicts that mean "an IP-level wall stands between us and the page".
HARD_CHALLENGES = frozenset({CLOUDFLARE, TURNSTILE, CAPTCHA, BOT_WALL})

#: Map challenge verdicts onto the scraper's ``_block_reason`` vocabulary.
_BLOCK_REASONS = {
    CLOUDFLARE: "cloudflare",
    TURNSTILE: "cloudflare",
    BOT_WALL: "bot_wall",
    CAPTCHA: "captcha",
    RATE_LIMIT: "rate_limit",
    AUTHWALL: "authwall",
    JS_REQUIRED: "js_required",
    NONE: "",
}


def block_reason_for(verdict: str) -> str:
    """Translate a classifier verdict into a scraper block reason."""
    return _BLOCK_REASONS.get(str(verdict or "").lower(), "")


# ─────────────────────────────────────────────────────────────
#  Signatures
# ─────────────────────────────────────────────────────────────

_CF_BODY = (
    "window._cf_chl_opt",
    "cf-chl-opt",
    "cf_chl_",
    "cf-browser-verification",
    "challenges.cloudflare.com",
    "__cf_chl_tk",
    "just a moment...",
    "just a moment…",
    "attention required! | cloudflare",
    "attention required!",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
    "cf-challenge",
    "ray id:",
)

_CF_HEADERS = ("cf-mitigated", "cf-chl-bypass", "cf-ray", "cf-cache-status")
_TURNSTILE = ("cf-turnstile", "data-sitekey", "turnstile/v0", "turnstile.render")
_DATADOME = ("datadome", "dd_cookie_test", "geo.captcha-delivery.com", "captcha-delivery.com")
_PX = ("_pxcaptcha", "px-captcha", "captcha.px-cdn.net", "perimeterx", "px-cookie")
_AKAMAI = ("ak_bmsc", "_abck", "bm-verify", "reference #18.", "akam/13")
_INCAPSULA = ("incap_ses", "visid_incap", "incapsula incident", "incapsula resource")
_JS_WALL = ("<noscript>", "please enable javascript", "javascript is required")

_RATE_LIMIT_BODY = (
    "too many requests",
    "error 429",
    "http 429",
    "you've reached the limit",
    "unusual activity",
    "we've restricted",
    "rate limit exceeded",
    "slow down",
    "try again later",
)
_AUTHWALL_BODY = (
    "authwall",
    "sign in to linkedin",
    "join linkedin to see",
    "please log in to continue",
    "session expired",
)
_CAPTCHA_BODY = (
    "prove you're not a robot",
    "are you a human",
    "let's do a quick security check",
    "verify you are human",
    "complete the security check",
)

#: Cookies worth persisting after a solve.  Scoped-prefix matches allowed.
_CLEARANCE_COOKIES = (
    "cf_clearance", "__cf_bm", "cf_ob_info", "cf_use_ob", "__cf_waf_tk",
    "datadome", "dd_cookie_test", "_px", "pxcts", "ak_bmsc", "_abck",
    "incap_ses", "visid_incap", "bm_sv", "bm_sz", "nlbi_",
)


@dataclass
class Challenge:
    """Result of inspecting one page / HTTP response."""

    verdict: str = NONE
    confidence: float = 0.0
    markers: List[str] = field(default_factory=list)
    status: int = 0
    retry_after: Optional[float] = None
    hint: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict not in (NONE, "")

    @property
    def hard(self) -> bool:
        return self.verdict in HARD_CHALLENGES

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": round(self.confidence, 2),
            "markers": self.markers[:8],
            "status": self.status,
            "retry_after": self.retry_after,
            "hint": self.hint,
        }


def _hits(text: str, needles: Iterable[str]) -> List[str]:
    return [needle for needle in needles if needle in text]


def _header_blob(headers: Optional[dict]) -> str:
    if not headers:
        return ""
    try:
        items = headers.items()
    except AttributeError:
        return str(headers).lower()
    return " ".join(f"{k}: {v}" for k, v in items).lower()


def _retry_after(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_page(url: str = "", html: str = "", headers: Optional[dict] = None,
                  status: int = 200) -> Challenge:
    """Classify an HTML page (rendered or raw) as walled or clean."""
    body = (html or "")[:400_000].lower()
    head = _header_blob(headers)
    target = f"{url or ''}".lower()
    markers: List[str] = []

    # Turnstile widget is the most specific Cloudflare signal. But a widget on a
    # normal content page (a login form, a demo page) is an in-page captcha, not
    # an edge wall: only treat it as one when the surrounding document is itself
    # an interstitial (CF challenge scripts, or a tiny "verify" page).
    turnstile = _hits(body, _TURNSTILE)
    if turnstile:
        # "challenges.cloudflare.com" is the widget itself, so it does not by
        # itself prove the *document* is an interstitial.
        cf_around = [marker for marker in _hits(body, _CF_BODY)
                     if marker != "challenges.cloudflare.com"]
        verify_copy = _hits(body, ("verify you are human", "checking your browser",
                                   "just a moment", "are you a robot"))
        if cf_around or verify_copy or len(body) < 6_000:
            return Challenge(TURNSTILE, 0.95, turnstile + cf_around, status,
                             _retry_after(_value_of(headers, "retry-after")),
                             "Cloudflare Turnstile interstitial")
        return Challenge(NONE, 0.2, turnstile, status, None,
                         "Turnstile widget on a content page (not an edge wall)")

    cf = _hits(body, _CF_BODY)
    cf_headers = [h for h in _CF_HEADERS if h in head]
    if "cf-mitigated: challenge" in head or "cf-chl-bypass" in head:
        cf_headers.append("cf-mitigated")
    if cf or (cf_headers and status in (403, 429, 503)):
        markers = cf + cf_headers
        confidence = 0.9 if cf else 0.6
        return Challenge(CLOUDFLARE, confidence, markers, status,
                         _retry_after(_value_of(headers, "retry-after")),
                         "Cloudflare interstitial / managed challenge")

    for family, needles, hint in (
        (_DATADOME, _DATADOME, "DataDome bot wall"),
        (_PX, _PX, "PerimeterX bot wall"),
        (_AKAMAI, _AKAMAI, "Akamai bot manager"),
        (_INCAPSULA, _INCAPSULA, "Imperva/Incapsula wall"),
    ):
        found = _hits(body, needles) + _hits(head, needles)
        if found:
            return Challenge(BOT_WALL, 0.8, found, status,
                             _retry_after(_value_of(headers, "retry-after")), hint)

    rate = _hits(body, _RATE_LIMIT_BODY)
    if status == 429 or rate:
        return Challenge(RATE_LIMIT, 0.9 if status == 429 else 0.7, rate, status,
                         _retry_after(_value_of(headers, "retry-after")),
                         "Rate limited")

    captcha = _hits(body, _CAPTCHA_BODY)
    if captcha:
        return Challenge(CAPTCHA, 0.85, captcha, status, None, "Human-verification page")

    auth = _hits(body, _AUTHWALL_BODY) + _hits(target, ("authwall", "/uas/login", "/signup"))
    if auth:
        return Challenge(AUTHWALL, 0.8, auth, status, None, "Auth wall")

    if status in (401, 403) and not body.strip():
        return Challenge(CLOUDFLARE, 0.5, [f"http {status} empty body"], status, None,
                         "Bare 403 — almost always an edge block")

    js = _hits(body, _JS_WALL)
    if js and len(body) < 20_000:
        return Challenge(JS_REQUIRED, 0.4, js, status, None, "Page needs JS to render")

    return Challenge(NONE, 0.0, [], status, None, "")


def classify_response(status: int = 200, headers: Optional[dict] = None,
                      body: str = "") -> Challenge:
    """Classify an HTTP response (no URL context)."""
    return classify_page("", body, headers, status)


def _value_of(headers: Optional[dict], key: str) -> Any:
    if not headers:
        return None
    try:
        for name, value in headers.items():
            if name.lower() == key.lower():
                return value
    except AttributeError:
        return None
    return None


def is_clearance_cookie(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(lowered == want or lowered.startswith(want) for want in _CLEARANCE_COOKIES)


# ─────────────────────────────────────────────────────────────
#  Clearance cache
# ─────────────────────────────────────────────────────────────

class ClearanceCache:
    """Persist solved bot-wall cookies, scoped per domain + proxy + UA.

    Keying on the proxy matters: Cloudflare binds ``cf_clearance`` to the client
    IP that solved it.  Replaying a cookie from another egress IP is worse than
    having no cookie at all — it flags the session.  The UA is part of the key
    for the same reason.
    """

    def __init__(self, path: str = "output/clearance.json", ttl: float = 1800.0):
        self.path = Path(path)
        self.ttl = float(ttl)

    @staticmethod
    def key_for(url: str, proxy: Optional[str] = None, user_agent: str = "") -> str:
        host = urllib.parse.urlparse(url).netloc.lower() or url.lower()
        proxy_key = (proxy or "direct").strip().lower()
        ua_key = (user_agent or "")[:40]
        return f"{host}|{proxy_key}|{ua_key}"

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:  # cache is an optimisation, never a hard failure
            logger.debug("clearance cache write failed: %s", exc)

    def get(self, url: str, proxy: Optional[str] = None,
            user_agent: str = "", now: Optional[float] = None) -> Dict[str, str]:
        entry = self._load().get(self.key_for(url, proxy, user_agent)) or {}
        saved = float(entry.get("saved_at") or 0)
        if not saved or ((now if now is not None else time.time()) - saved) > self.ttl:
            return {}
        cookies = entry.get("cookies") or {}
        return {str(k): str(v) for k, v in cookies.items() if is_clearance_cookie(k)}

    def put(self, url: str, cookies: Dict[str, str], proxy: Optional[str] = None,
            user_agent: str = "", engine: str = "", now: Optional[float] = None) -> bool:
        kept = {str(k): str(v) for k, v in (cookies or {}).items() if is_clearance_cookie(k)}
        if not kept:
            return False
        data = self._load()
        data[self.key_for(url, proxy, user_agent)] = {
            "cookies": kept,
            "engine": engine,
            "saved_at": float(now if now is not None else time.time()),
        }
        self._save(data)
        return True

    def clear(self) -> None:
        self._save({})

    def prune(self, now: Optional[float] = None) -> int:
        moment = float(now if now is not None else time.time())
        data = self._load()
        keep = {
            key: entry for key, entry in data.items()
            if moment - float((entry or {}).get("saved_at") or 0) <= self.ttl
        }
        removed = len(data) - len(keep)
        if removed:
            self._save(keep)
        return removed


def cookie_header(cookies: Optional[Dict[str, str]]) -> str:
    if not cookies:
        return ""
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


# ─────────────────────────────────────────────────────────────
#  Identity / TLS impersonation
# ─────────────────────────────────────────────────────────────

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def platform_for_user_agent(user_agent: str) -> Tuple[str, str, str]:
    """(navigator.platform, platform_label, platform_version) for a UA string."""
    ua = user_agent or ""
    if "Windows" in ua:
        return "Win32", "Windows", "15.0.0"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "MacIntel", "macOS", "10.15.7"
    if "Android" in ua:
        return "Linux armv8l", "Android", "13.0.0"
    return "Linux x86_64", "Linux", "6.5.0"


def impersonate_for_user_agent(user_agent: str) -> str:
    """Pick the closest curl_cffi TLS target for a browser UA string.

    The TLS target must not contradict the UA: a Chromium UA with a Firefox
    handshake is a mismatch Cloudflare scores.  Unknown/unsupported targets fall
    back to the generic ``chrome`` profile.
    """
    ua = user_agent or ""
    targets = _curl_targets()

    def pick(candidates: List[str]) -> str:
        for candidate in candidates:
            if candidate in targets:
                return candidate
        return "chrome"

    match = re.search(r"Chrome/(\d{2,3})", ua)
    if "Edg/" in ua:
        return pick(["edge101", "chrome131", "chrome"])
    if match:
        major = int(match.group(1))
        candidates = [f"chrome{major}"] + [f"chrome{version}" for version in
                                           (136, 133, 131, 124, 120)]
        return pick(candidates + ["chrome"])
    if "Firefox/" in ua:
        return pick(["firefox144", "firefox135", "firefox133", "firefox"])
    if "Safari/" in ua and "Chrome" not in ua:
        return pick(["safari180", "safari170", "safari155", "safari153"])
    return pick(["chrome131", "chrome"])


_CURL_TARGETS: Optional[set] = None


def _curl_targets() -> set:
    """The impersonation profiles this curl_cffi build actually ships."""
    global _CURL_TARGETS
    if _CURL_TARGETS is None:
        try:
            from curl_cffi.requests import BrowserType
            _CURL_TARGETS = {
                str(item.value) if hasattr(item, "value") else str(item)
                for item in BrowserType
            }
        except Exception:
            _CURL_TARGETS = set()
    return _CURL_TARGETS


def _curl_target_supported(target: str) -> bool:
    return str(target) in _curl_targets()


# ─────────────────────────────────────────────────────────────
#  FlareSolverr client
# ─────────────────────────────────────────────────────────────

class FlareSolverrClient:
    """Minimal client for the FlareSolverr sidecar (``/v1`` JSON API)."""

    def __init__(self, base_url: str = "", timeout: float = 90.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = float(timeout)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _post_json(self, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def solve(self, url: str, *, method: str = "GET", cookies: Optional[Dict[str, str]] = None,
              proxy: Optional[str] = None) -> "FetchResult":
        if not self.configured:
            return FetchResult(False, 0, url, "", {}, "flaresolverr", Challenge(NONE),
                               error="FlareSolverr is not configured")
        payload: Dict[str, Any] = {
            "cmd": "request.get" if method.upper() == "GET" else "request.post",
            "url": url,
            "maxTimeout": int(self.timeout * 1000),
        }
        if cookies:
            payload["cookies"] = [{"name": k, "value": v} for k, v in cookies.items()]
        if proxy:
            payload["proxy"] = {"url": proxy}
        started = time.time()
        try:
            body = self._post_json(payload)
        except Exception as exc:
            return FetchResult(False, 0, url, "", {}, "flaresolverr", Challenge(NONE),
                               error=f"{type(exc).__name__}: {exc}", seconds=time.time() - started)

        solution = body.get("solution") or {}
        html = solution.get("response") or ""
        status = int(solution.get("status") or 0)
        jar = {item.get("name"): item.get("value") for item in solution.get("cookies") or []
               if item.get("name")}
        challenge = classify_page(url, html, {}, status)
        ok = str(body.get("status") or "").lower() == "ok" and not challenge.hard
        return FetchResult(ok, status, solution.get("url") or url, html, jar, "flaresolverr",
                           challenge, error="" if ok else str(body.get("message") or ""),
                           seconds=time.time() - started)


# ─────────────────────────────────────────────────────────────
#  Fetch result + tiered fetcher
# ─────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    ok: bool
    status: int
    url: str
    html: str
    cookies: Dict[str, str] = field(default_factory=dict)
    tier: str = ""
    challenge: Challenge = field(default_factory=Challenge)
    error: str = ""
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "url": self.url,
            "tier": self.tier,
            "challenge": self.challenge.as_dict(),
            "cookies": sorted(self.cookies),
            "bytes": len(self.html or ""),
            "seconds": round(self.seconds, 2),
            "error": self.error,
        }


def _urllib_fetch(url: str, headers: Dict[str, str], proxy: Optional[str],
                  timeout: float, method: str = "GET", data: Optional[bytes] = None) -> Tuple[int, str, dict]:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers or {})


class TieredFetcher:
    """Fetch a URL through the cheapest tier that clears the current wall."""

    def __init__(self, *, proxy: Optional[str] = None, user_agent: str = _DEFAULT_UA,
                 timeout: float = 30.0, clearance: Optional[ClearanceCache] = None,
                 solverr_url: str = "", engine: str = "auto", headless: bool = True,
                 allow_browser: bool = True, clearance_ttl: float = 1800.0):
        self.proxy = proxy
        self.user_agent = user_agent or _DEFAULT_UA
        self.timeout = float(timeout)
        self.clearance = clearance or ClearanceCache(ttl=clearance_ttl)
        self.solverr = FlareSolverrClient(solverr_url, timeout=max(60.0, self.timeout * 2))
        self.engine = engine
        self.headless = headless
        self.allow_browser = allow_browser

    # ── headers ──

    def headers_for(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        platform, label, version = platform_for_user_agent(self.user_agent)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{label}"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        headers.update(extra or {})
        return headers

    # ── tiers ──

    def _tier_curl(self, url: str, headers: Dict[str, str], data: Optional[bytes],
                   method: str) -> Optional[FetchResult]:
        try:
            from curl_cffi import requests as crequests
        except Exception:
            return None
        started = time.time()
        target = impersonate_for_user_agent(self.user_agent)
        jar = self.clearance.get(url, self.proxy, self.user_agent)
        try:
            session = crequests.Session(impersonate=target, timeout=self.timeout)
        except Exception:
            try:
                session = crequests.Session(timeout=self.timeout)
            except Exception as exc:
                return FetchResult(False, 0, url, "", {}, "curl_cffi", Challenge(NONE),
                                   error=f"{type(exc).__name__}: {exc}")
        if self.proxy:
            try:
                session.proxies = {"http": self.proxy, "https": self.proxy}
            except Exception:
                pass
        merged = dict(headers)
        if jar:
            merged["Cookie"] = cookie_header(jar)
        try:
            response = session.request(method.upper(), url, headers=merged, data=data,
                                       allow_redirects=True, timeout=self.timeout)
            html = response.text or ""
            status = int(response.status_code)
            response_headers = dict(response.headers or {})
            cookies = jar
        except Exception as exc:
            return FetchResult(False, 0, url, "", {}, "curl_cffi", Challenge(NONE),
                               error=f"{type(exc).__name__}: {exc}",
                               seconds=time.time() - started)
        finally:
            try:
                session.close()
            except Exception:
                pass

        challenge = classify_page(str(getattr(response, "url", url)), html, response_headers, status)
        ok = not challenge.hard and status < 400
        if ok:
            harvested = _harvest_clearance(response.cookies if hasattr(response, "cookies") else None)
            if harvested:
                self.clearance.put(url, harvested, self.proxy, self.user_agent, engine="curl_cffi")
        return FetchResult(ok, status, str(getattr(response, "url", url)), html, cookies,
                           "curl_cffi", challenge,
                           error="" if ok else challenge.hint or f"HTTP {status}",
                           seconds=time.time() - started)

    def _tier_solverr(self, url: str) -> Optional[FetchResult]:
        if not self.solverr.configured:
            return None
        cookies = self.clearance.get(url, self.proxy, self.user_agent)
        result = self.solverr.solve(url, cookies=cookies, proxy=self.proxy)
        if result.ok and result.cookies:
            self.clearance.put(url, result.cookies, self.proxy, self.user_agent,
                               engine="flaresolverr")
        return result

    def _tier_browser(self, url: str) -> Optional[FetchResult]:
        if not self.allow_browser:
            return None
        started = time.time()
        cookies = self.clearance.get(url, self.proxy, self.user_agent)
        try:
            result = _run_async(self._browser_fetch(url, cookies))
        except Exception as exc:
            return FetchResult(False, 0, url, "", {}, "browser", Challenge(NONE),
                               error=f"{type(exc).__name__}: {exc}",
                               seconds=time.time() - started)
        result.seconds = time.time() - started
        return result

    async def _browser_fetch(self, url: str, cookies: Dict[str, str]) -> FetchResult:
        handle = await launch_stealth_browser(engine=self.engine, headless=self.headless,
                                              proxy=self.proxy and {"server": self.proxy})
        try:
            context = await handle.browser.new_context(user_agent=self.user_agent)
            if cookies:
                await context.add_cookies([
                    {"name": name, "value": value,
                     "domain": urllib.parse.urlparse(url).netloc, "path": "/"}
                    for name, value in cookies.items()
                ])
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded",
                                       timeout=int(self.timeout * 1000))
            await asyncio.sleep(random.uniform(0.6, 1.4))
            solved = await solve_challenge(page, timeout=max(20.0, self.timeout))
            html = await page.content()
            jar = await harvest_cookies(context)
            status = int(getattr(response, "status", 0) or 0)
            challenge = classify_page(page.url, html, {}, status)
            if solved and jar:
                self.clearance.put(url, jar, self.proxy, self.user_agent, engine=handle.engine)
            return FetchResult(solved and not challenge.hard, status, page.url, html, jar,
                               f"browser:{handle.engine}", challenge,
                               error="" if solved else challenge.hint)
        finally:
            await handle.close()

    # ── entry point ──

    def fetch(self, url: str, *, method: str = "GET", headers: Optional[Dict[str, str]] = None,
              data: Optional[bytes] = None) -> FetchResult:
        """Run the tier ladder; never raises for a challenge, only for bad input."""
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f"not an http(s) URL: {url!r}")
        merged = self.headers_for(headers)
        started = time.time()
        errors: List[str] = []

        result = self._tier_curl(url, merged, data, method)
        if result is not None:
            if result.ok:
                return result
            errors.append(f"curl_cffi: {result.error}")
        else:
            errors.append("curl_cffi: not installed")

        result = self._tier_solverr(url)
        if result is not None:
            if result.ok:
                return result
            errors.append(f"flaresolverr: {result.error}")

        result = self._tier_browser(url)
        if result is not None:
            if result.ok:
                return result
            errors.append(f"browser: {result.error}")

        # Last resort: plain urllib, still with a coherent header set.
        try:
            status, html, response_headers = _urllib_fetch(
                url, merged, self.proxy, self.timeout, method, data)
            challenge = classify_page(url, html, response_headers, status)
            return FetchResult(not challenge.hard and status < 400, status, url, html, {},
                               "urllib", challenge,
                               error="" if not challenge.hard else challenge.hint,
                               seconds=time.time() - started)
        except Exception as exc:
            errors.append(f"urllib: {type(exc).__name__}: {exc}")

        logger.warning("all fetch tiers failed for %s (%s)", url, "; ".join(errors))
        return FetchResult(False, 0, url, "", {}, "none", Challenge(NONE),
                           error="; ".join(errors), seconds=time.time() - started)


def _harvest_clearance(cookie_jar) -> Dict[str, str]:
    """Pull clearance cookies out of a curl_cffi / requests cookie mapping."""
    if cookie_jar is None:
        return {}
    found: Dict[str, str] = {}
    try:
        items = cookie_jar.items()
    except AttributeError:
        try:
            items = [(cookie.name, cookie.value) for cookie in cookie_jar]
        except Exception:
            return {}
    for name, value in items:
        if is_clearance_cookie(str(name)):
            found[str(name)] = str(value)
    return found


async def harvest_cookies(context) -> Dict[str, str]:
    """All cookies from a Playwright browser context (sync dict, name -> value)."""
    jar: Dict[str, str] = {}
    try:
        cookies = await context.cookies()
    except Exception:
        return jar
    for item in cookies or []:
        name = str(item.get("name") or "")
        if name:
            jar[name] = str(item.get("value") or "")
    return jar


# ─────────────────────────────────────────────────────────────
#  Browser solving
# ─────────────────────────────────────────────────────────────

async def solve_challenge(page, timeout: float = 45.0, *, click_turnstile: bool = True,
                          poll: float = 1.5) -> bool:
    """Wait for a Cloudflare/bot wall on ``page`` to clear.

    Fast path: most patchright/camoufox sessions clear a managed challenge on
    their own within a few seconds — the poll loop returns the moment the page
    stops classifying as a wall.  Turnstile needs a click; a best-effort click
    inside the iframe is attempted twice, and a solved Turnstile token counts as
    success even while the document still looks like an interstitial.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(5.0, float(timeout))
    clicks = 0
    while loop.time() < deadline:
        try:
            html = await page.content()
        except Exception:
            await asyncio.sleep(poll)
            continue
        challenge = classify_page(getattr(page, "url", "") or "", html)
        if not challenge.hard:
            return True
        if await _turnstile_token(page):
            return True
        if click_turnstile and challenge.verdict == TURNSTILE and clicks < 2:
            clicks += 1
            if await _click_turnstile(page):
                await asyncio.sleep(random.uniform(2.0, 3.5))
        await asyncio.sleep(poll)
    return False


async def _turnstile_token(page) -> str:
    """The response token Turnstile writes when the widget is solved."""
    try:
        return str(await page.evaluate(
            "() => {"
            "  const nodes = document.querySelectorAll("
            "    'input[name=\"cf-turnstile-response\"], textarea[name=\"cf-turnstile-response\"],"
            "    input[name=\"cf_chl_response\"]');"
            "  for (const node of nodes) { if (node.value) return node.value; }"
            "  return '';"
            "}"
        ) or "")
    except Exception:
        return ""


async def _click_turnstile(page) -> bool:
    """Click the Turnstile checkbox inside its iframe, at the widget's real box."""
    box = None
    try:
        frames = [frame for frame in page.frames
                  if "challenges.cloudflare.com" in (frame.url or "")]
    except Exception:
        frames = []
    for frame in frames:
        try:
            element = await frame.frame_element()
            box = await element.bounding_box()
        except Exception:
            box = None
        if box and box.get("width"):
            break
    if not box or not box.get("width"):
        try:
            iframe = await page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            box = await iframe.bounding_box() if iframe is not None else None
        except Exception:
            box = None
    if not box or not box.get("width"):
        return False
    # The checkbox sits in the left third, vertically centred. Approach it like
    # a hand: a short move in, a small pause, then the click.
    x = box["x"] + max(12.0, min(40.0, box["width"] * 0.18))
    y = box["y"] + box["height"] / 2
    try:
        await page.mouse.move(x - 45, y - 25, steps=12)
        await asyncio.sleep(random.uniform(0.15, 0.4))
        await page.mouse.click(x, y)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
#  Engine launch
# ─────────────────────────────────────────────────────────────

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--start-maximized",
]

_ENGINE_ORDER = ("patchright", "camoufox", "playwright")


class BrowserHandle:
    """One launched engine: browser + whatever object must be torn down with it."""

    def __init__(self, engine: str, browser, driver=None, cm=None):
        self.engine = engine
        self.browser = browser
        self.driver = driver
        self._cm = cm
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        if self.driver is not None:
            try:
                stopper = getattr(self.driver, "stop", None)
                if stopper is not None:
                    result = stopper()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception:
                pass


def _module_available(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def available_engines() -> List[str]:
    return [engine for engine in _ENGINE_ORDER if _module_available(engine)]


async def launch_stealth_browser(engine: str = "auto", *, headless: bool = False,
                                 proxy: Optional[dict] = None, channel: str = "",
                                 args: Optional[List[str]] = None,
                                 user_data_dir: str = "") -> BrowserHandle:
    """Launch the best available stealth engine, in order, with fallbacks.

    ``engine`` may be an explicit name or ``"auto"``.  Every attempt is isolated:
    a missing browser build, a broken proxy or an import error moves to the next
    engine instead of killing the run.  Raises only when no engine launches.
    """
    order = list(_ENGINE_ORDER) if str(engine).lower() in ("", "auto") else [str(engine).lower()]
    if str(engine).lower() not in ("", "auto") and order[0] not in _ENGINE_ORDER:
        raise ValueError(f"unknown browser engine: {engine!r}")
    if str(engine).lower() in ("", "auto"):
        order = [item for item in order if _module_available(item)]
    if not order:
        raise RuntimeError("no browser engine available (install playwright/patchright/camoufox)")

    launch_args = list(args or _LAUNCH_ARGS)
    errors: List[str] = []
    for name in order:
        try:
            if name == "playwright":
                handle = await _launch_playwright_like("playwright", headless, proxy, channel,
                                                       launch_args, user_data_dir)
            elif name == "patchright":
                handle = await _launch_playwright_like("patchright", headless, proxy, channel,
                                                       launch_args, user_data_dir)
            elif name == "camoufox":
                handle = await _launch_camoufox(headless, proxy, launch_args)
            else:
                raise ValueError(f"unknown browser engine: {name!r}")
            logger.info("browser engine: %s", handle.engine)
            return handle
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            logger.debug("engine %s failed: %s", name, exc)
    raise RuntimeError("no browser engine launched (" + "; ".join(errors) + ")")


async def _launch_playwright_like(module_name: str, headless: bool, proxy: Optional[dict],
                                  channel: str, launch_args: List[str],
                                  user_data_dir: str) -> BrowserHandle:
    module = __import__(f"{module_name}.async_api", fromlist=["async_playwright"])
    driver = await module.async_playwright().start()
    try:
        if user_data_dir:
            context = await driver.chromium.launch_persistent_context(
                user_data_dir, headless=headless, proxy=proxy, args=launch_args)
            browser = context.browser
            handle = BrowserHandle(module_name, browser, driver)
            handle.context = context  # type: ignore[attr-defined]
            return handle
        attempts = [{"channel": channel}] if channel else []
        attempts.append({})
        last_error: Optional[Exception] = None
        for extra in attempts:
            try:
                browser = await driver.chromium.launch(
                    headless=headless, proxy=proxy, args=launch_args, **extra)
                return BrowserHandle(module_name, browser, driver)
            except Exception as exc:
                last_error = exc
        raise last_error or RuntimeError("launch failed")
    except Exception:
        try:
            await driver.stop()
        except Exception:
            pass
        raise


async def _launch_camoufox(headless: bool, proxy: Optional[dict], launch_args: List[str]) -> BrowserHandle:
    from camoufox.async_api import AsyncCamoufox

    kwargs: Dict[str, Any] = {"headless": headless}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        cm = AsyncCamoufox(**kwargs)
        browser = await cm.__aenter__()
        return BrowserHandle("camoufox", browser, None, cm)
    except TypeError:
        # Older builds take the proxy as a plain URL string.
        if proxy and proxy.get("server"):
            cm = AsyncCamoufox(headless=headless, proxy=proxy["server"])
            browser = await cm.__aenter__()
            return BrowserHandle("camoufox", browser, None, cm)
        raise


# ─────────────────────────────────────────────────────────────
#  Diagnostics
# ─────────────────────────────────────────────────────────────

def capability_report() -> dict:
    """What this machine can actually do, for ``cli.py doctor``."""
    engines = available_engines()
    return {
        "engines": engines,
        "best_engine": engines[0] if engines else "",
        "curl_cffi": _module_available("curl_cffi"),
        "camoufox": _module_available("camoufox"),
        "patchright": _module_available("patchright"),
        "flaresolverr_configured": bool(os.environ.get("CF_SOLVERR_URL")),
    }


# ─────────────────────────────────────────────────────────────
#  Async plumbing (sync callers, running loops)
# ─────────────────────────────────────────────────────────────

def _run_async(coro):
    """Run a coroutine from sync code, even when a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: Dict[str, Any] = {}

    def runner():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # propagate to the caller thread
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")
