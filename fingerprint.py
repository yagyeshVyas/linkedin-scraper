"""Coherent browser fingerprints — generated, validated, reproducible.

The old ``_FINGERPRINTS`` list in ``linkedin_scraper.py`` is four hand-written
identities. This module generates as many as you want, with one rule: every
value must agree with every other value.

    UA says Windows 11 / Chrome 131
      -> navigator.platform Win32, platformVersion 15.0.0
      -> Chrome UA-CH brands at 131, mobile false
      -> GPU from the Windows pool (never Apple/Metal)
      -> viewport that fits the claimed screen
      -> timezone, locale and geolocation from the same country

A mismatch between any two of those is a detection signal that costs more than
a missing patch. Cloudflare, DataDome and LinkedIn all cross-check them.

Usage:

    from fingerprint import generate_profile, validate_profile, init_script

    fp = generate_profile()                       # random, coherent
    fp = generate_profile(seed=42)                # reproducible
    fp = generate_profile(country="GB")           # London identity
    fp = profile_for_proxy("http://u:p@h:8080#DE")
    issues = validate_profile(fp)                 # [] when coherent

The output dict uses the exact schema ``linkedin_scraper._launch_browser``
already renders (user_agent, platform, platform_label, platform_ver,
ua_version, viewport, timezone_id, locale, geo, webgl_vendor, webgl_renderer,
hardware) plus screen/dpr/accept_language for the agent browser and audit.

Import-safe: standard library only.
"""

from __future__ import annotations

import os
import random
import re
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# ─────────────────────────────────────────────────────────────
#  Geography — one country, one story
# ─────────────────────────────────────────────────────────────

#: country -> list of coherent (timezone, locale, city, lat, lon) bundles.
_GEO_BUNDLES: Dict[str, List[Tuple[str, str, str, float, float]]] = {
    "US": [
        ("America/New_York", "en-US", "New York", 40.7128, -74.0060),
        ("America/Chicago", "en-US", "Chicago", 41.8781, -87.6298),
        ("America/Denver", "en-US", "Denver", 39.7392, -104.9903),
        ("America/Los_Angeles", "en-US", "Los Angeles", 34.0522, -118.2437),
        ("America/Phoenix", "en-US", "Phoenix", 33.4484, -112.0740),
        ("America/New_York", "en-US", "Boston", 42.3601, -71.0589),
    ],
    "CA": [
        ("America/Toronto", "en-CA", "Toronto", 43.6532, -79.3832),
        ("America/Vancouver", "en-CA", "Vancouver", 49.2827, -123.1207),
        ("America/Edmonton", "en-CA", "Edmonton", 53.5461, -113.4938),
    ],
    "GB": [
        ("Europe/London", "en-GB", "London", 51.5074, -0.1278),
        ("Europe/London", "en-GB", "Manchester", 53.4808, -2.2426),
    ],
    "IE": [("Europe/Dublin", "en-IE", "Dublin", 53.3498, -6.2603)],
    "AU": [
        ("Australia/Sydney", "en-AU", "Sydney", -33.8688, 151.2093),
        ("Australia/Melbourne", "en-AU", "Melbourne", -37.8136, 144.9631),
        ("Australia/Brisbane", "en-AU", "Brisbane", -27.4698, 153.0251),
    ],
    "NZ": [("Pacific/Auckland", "en-NZ", "Auckland", -36.8509, 174.7645)],
    "SG": [("Asia/Singapore", "en-SG", "Singapore", 1.3521, 103.8198)],
    "IN": [
        ("Asia/Kolkata", "en-IN", "Mumbai", 19.0760, 72.8777),
        ("Asia/Kolkata", "en-IN", "Bengaluru", 12.9716, 77.5946),
        ("Asia/Kolkata", "en-IN", "Delhi", 28.6139, 77.2090),
    ],
    "DE": [
        ("Europe/Berlin", "de-DE", "Berlin", 52.5200, 13.4050),
        ("Europe/Berlin", "de-DE", "Munich", 48.1351, 11.5820),
    ],
    "FR": [("Europe/Paris", "fr-FR", "Paris", 48.8566, 2.3522)],
    "NL": [("Europe/Amsterdam", "nl-NL", "Amsterdam", 52.3676, 4.9041)],
    "ES": [("Europe/Madrid", "es-ES", "Madrid", 40.4168, -3.7038)],
    "IT": [("Europe/Rome", "it-IT", "Milan", 45.4642, 9.1900)],
    "SE": [("Europe/Stockholm", "sv-SE", "Stockholm", 59.3293, 18.0686)],
    "PL": [("Europe/Warsaw", "pl-PL", "Warsaw", 52.2297, 21.0122)],
    "BR": [
        ("America/Sao_Paulo", "pt-BR", "Sao Paulo", -23.5505, -46.6333),
        ("America/Sao_Paulo", "pt-BR", "Rio de Janeiro", -22.9068, -43.1729),
    ],
    "MX": [("America/Mexico_City", "es-MX", "Mexico City", 19.4326, -99.1332)],
    "JP": [("Asia/Tokyo", "ja-JP", "Tokyo", 35.6762, 139.6503)],
    "KR": [("Asia/Seoul", "ko-KR", "Seoul", 37.5665, 126.9780)],
    "AE": [("Asia/Dubai", "en-AE", "Dubai", 25.2048, 55.2708)],
    "ZA": [("Africa/Johannesburg", "en-ZA", "Johannesburg", -26.2041, 28.0473)],
}

DEFAULT_COUNTRY = "US"

#: Coherent language tags for the accept-language header / navigator.languages.
_LOCALE_TAGS = {
    "en-US": ["en-US", "en"], "en-CA": ["en-CA", "en"], "en-GB": ["en-GB", "en"],
    "en-IE": ["en-IE", "en"], "en-AU": ["en-AU", "en"], "en-NZ": ["en-NZ", "en"],
    "en-SG": ["en-SG", "en"], "en-IN": ["en-IN", "en"], "en-AE": ["en-AE", "en"],
    "en-ZA": ["en-ZA", "en"], "de-DE": ["de-DE", "de", "en"], "fr-FR": ["fr-FR", "fr", "en"],
    "nl-NL": ["nl-NL", "nl", "en"], "es-ES": ["es-ES", "es", "en"],
    "it-IT": ["it-IT", "it", "en"], "sv-SE": ["sv-SE", "sv", "en"],
    "pl-PL": ["pl-PL", "pl", "en"], "pt-BR": ["pt-BR", "pt", "en"],
    "es-MX": ["es-MX", "es", "en"], "ja-JP": ["ja-JP", "ja", "en"],
    "ko-KR": ["ko-KR", "ko", "en"],
}

# ─────────────────────────────────────────────────────────────
#  Hardware pools — matched to the OS
# ─────────────────────────────────────────────────────────────

_GPUS: Dict[str, List[Tuple[str, str]]] = {
    "Windows": [
        ("Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)",
         "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)",
         "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)",
         "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)",
         "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ],
    "macOS": [
        ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"),
        ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)"),
        ("Apple Inc.", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)"),
    ],
    "Linux": [
        ("Google Inc. (AMD)",
         "ANGLE (AMD, AMD Radeon RX 6700 XT (radeonsi, navi22, LLVM 15.0.7, DRM 3.49, 6.5.0), OpenGL 4.6)"),
        ("Google Inc. (Intel)",
         "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)"),
        ("Google Inc. (NVIDIA)",
         "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER/PCIe/SSE2, OpenGL 4.5)"),
    ],
}

_VIEWPORTS: Dict[str, List[Tuple[int, int, int, int, float]]] = {
    # (width, height, screen_w, screen_h, dpr)
    "Windows": [
        (1920, 1080, 1920, 1080, 1.0), (1920, 1080, 1920, 1200, 1.0),
        (1536, 864, 1920, 1080, 1.25), (1366, 768, 1366, 768, 1.0),
        (1600, 900, 1920, 1080, 1.0), (2560, 1440, 2560, 1440, 1.0),
        (1440, 900, 1920, 1200, 1.0),
    ],
    "macOS": [
        (1728, 1117, 1728, 1117, 2.0), (1512, 982, 1512, 982, 2.0),
        (1440, 900, 1440, 900, 2.0), (1680, 1050, 1680, 1050, 2.0),
        (2560, 1440, 2560, 1440, 1.0),
    ],
    "Linux": [
        (1920, 1080, 1920, 1080, 1.0), (1600, 900, 1920, 1080, 1.0),
        (1366, 768, 1366, 768, 1.0), (2560, 1440, 2560, 1440, 1.0),
    ],
}

_OS_SPECS = {
    "Windows": {
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36"),
        "platform": "Win32", "platform_ver": "15.0.0",
    },
    "macOS": {
        "ua": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36"),
        "platform": "MacIntel", "platform_ver": "14.6.1",
    },
    "Linux": {
        "ua": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36"),
        "platform": "Linux x86_64", "platform_ver": "6.8.0",
    },
}

#: Chrome majors worth claiming. Old versions are rare enough to stand out.
_CHROME_MAJORS = (130, 131, 132, 133, 134, 135, 136)


@dataclass
class Profile:
    """A generated identity plus the metadata used to keep it coherent."""

    data: Dict
    country: str
    city: str

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def as_dict(self) -> Dict:
        return dict(self.data)


def _rng(seed: Optional[int]) -> random.Random:
    return random.Random(seed) if seed is not None else random.Random()


def country_from_proxy(proxy_url: str) -> str:
    """Read a ``#CC`` country tag from a proxy URL (or ``?cc=CC``).

    Fragments are never sent to the proxy, so tagging a proxy with
    ``http://user:pass@host:8080#DE`` is free: the tag stays local and the
    fingerprint can match the exit country.
    """
    if not proxy_url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(proxy_url)
    except ValueError:
        return ""
    fragment = (parsed.fragment or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", fragment):
        return fragment
    query = urllib.parse.parse_qs(parsed.query or "")
    for key in ("cc", "country"):
        value = (query.get(key) or [""])[0].strip().upper()
        if re.fullmatch(r"[A-Z]{2}", value):
            return value
    return ""


def strip_proxy_tag(proxy_url: str) -> str:
    """The proxy URL without its local ``#CC`` tag (what Playwright accepts)."""
    if not proxy_url:
        return proxy_url
    try:
        parsed = urllib.parse.urlsplit(proxy_url)
    except ValueError:
        return proxy_url
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def supported_countries() -> List[str]:
    return sorted(_GEO_BUNDLES)


def host_os() -> str:
    """The real operating system, so a non-scriptable engine stays coherent."""
    import platform as _platform

    system = (_platform.system() or "").lower()
    if system.startswith("win"):
        return "Windows"
    if system == "darwin":
        return "macOS"
    return "Linux"


_LOCALE_COUNTRY_OVERRIDES = {
    "EN": "US", "C": "US", "POSIX": "US",
}

#: Windows reports localized names, not IANA zones. These are the common ones;
#: unknown zones fall through to the locale country.
_WINDOWS_TZ_COUNTRY = {
    "Eastern Standard Time": "US", "Central Standard Time": "US",
    "Mountain Standard Time": "US", "Pacific Standard Time": "US",
    "GMT Standard Time": "GB", "W. Europe Standard Time": "DE",
    "Romance Standard Time": "FR", "Central Europe Standard Time": "PL",
    "India Standard Time": "IN", "Singapore Standard Time": "SG",
    "AUS Eastern Standard Time": "AU", "Tokyo Standard Time": "JP",
    "Korea Standard Time": "KR", "Arabian Standard Time": "AE",
    "South Africa Standard Time": "ZA", "E. South America Standard Time": "BR",
    "Central Standard Time (Mexico)": "MX",
}


def _locale_country() -> str:
    """Best-effort country from the system locale, no network involved."""
    try:
        import locale as _locale

        tag = ""
        try:
            tag = _locale.getdefaultlocale()[0] or ""
        except Exception:
            tag = ""
        if not tag:
            tag = (os.environ.get("LANG") or os.environ.get("LC_ALL") or "")
        while tag and tag[-1].isdigit() and not tag[-3:-1].isdigit():
            tag = tag[:-1]
        tag = tag.replace("-", "_")
        parts = [part for part in tag.split("_") if part]
        if parts:
            country = parts[-1].upper()
            if len(country) == 2:
                return _LOCALE_COUNTRY_OVERRIDES.get(country, country)
            return _LOCALE_COUNTRY_OVERRIDES.get(parts[0].upper(), "US")
    except Exception:
        pass
    return ""


def _timezone_country() -> str:
    try:
        import datetime as _datetime

        name = _datetime.datetime.now().astimezone().tzinfo
        key = str(getattr(name, "key", "") or getattr(name, "tzname", lambda: "")())
        if key in _WINDOWS_TZ_COUNTRY:
            return _WINDOWS_TZ_COUNTRY[key]
        # IANA zones ("Europe/Berlin") carry no country code; map the big ones.
        iana = {
            "Europe/London": "GB", "Europe/Berlin": "DE", "Europe/Paris": "FR",
            "Europe/Amsterdam": "NL", "Europe/Madrid": "ES", "Europe/Rome": "IT",
            "Europe/Stockholm": "SE", "Europe/Warsaw": "PL", "Europe/Dublin": "IE",
            "Asia/Kolkata": "IN", "Asia/Singapore": "SG", "Asia/Tokyo": "JP",
            "Asia/Seoul": "KR", "Asia/Dubai": "AE", "Africa/Johannesburg": "ZA",
            "America/Sao_Paulo": "BR", "America/Mexico_City": "MX",
            "America/Toronto": "CA", "America/Vancouver": "CA",
            "Australia/Sydney": "AU", "Australia/Melbourne": "AU",
        }
        if key in iana:
            return iana[key]
        if key.startswith("America/"):
            return "US"
        if key.startswith("Pacific/Auckland"):
            return "NZ"
    except Exception:
        pass
    return ""


def detect_country(proxy_country: str = "", override: str = "") -> str:
    """Country for a fingerprint when the proxy carries no tag.

    Priority: explicit override -> proxy tag -> env FINGERPRINT_COUNTRY ->
    system timezone -> system locale -> US. A random country with a direct
    connection is a contradiction detectors love.
    """
    candidates = [
        (override or "").upper(),
        (proxy_country or "").upper(),
        (os.environ.get("FINGERPRINT_COUNTRY") or "").upper(),
        _timezone_country().upper(),
        _locale_country().upper(),
    ]
    for candidate in candidates:
        if candidate in _GEO_BUNDLES:
            return candidate
    return DEFAULT_COUNTRY


def generate_profile(seed: Optional[int] = None, *,
                     os_name: Optional[str] = None,
                     country: Optional[str] = None,
                     chrome_major: Optional[int] = None,
                     safe_viewport: bool = False,
                     screen_limit: Optional[Tuple[int, int]] = None) -> Dict:
    """Generate one coherent fingerprint using the scraper's profile schema.

    ``chrome_major`` should be the *real* browser build's major version; a UA
    claiming 131 while the binary is 141 is a mismatch no script can hide.
    ``safe_viewport`` pins 1366x768 for engines that cannot patch `screen`.
    """
    rng = _rng(seed)
    os_name = os_name if os_name in _OS_SPECS else rng.choice(tuple(_OS_SPECS))
    country = (country or "").upper() or detect_country()
    if country not in _GEO_BUNDLES:
        country = DEFAULT_COUNTRY
    timezone_id, locale, city, lat, lon = rng.choice(_GEO_BUNDLES[country])
    major = int(chrome_major or rng.choice(_CHROME_MAJORS))
    spec = _OS_SPECS[os_name]
    pool = _VIEWPORTS[os_name]
    if screen_limit:
        limit_w, limit_h = int(screen_limit[0]), int(screen_limit[1])
        fitting = [entry for entry in pool
                   if entry[2] <= limit_w and entry[3] <= limit_h]
        viewport = rng.choice(fitting or [(min(1366, limit_w), min(768, limit_h),
                                           min(1366, limit_w), min(768, limit_h), 1.0)])
    elif safe_viewport:
        viewport = (1366, 768, 1366, 768, 1.0)
    else:
        viewport = rng.choice(pool)
    width, height, screen_w, screen_h, dpr = viewport
    webgl_vendor, webgl_renderer = rng.choice(_GPUS[os_name])
    device_memory = rng.choice((4, 8, 8, 16, 16, 32))
    concurrency = rng.choice((4, 8, 8, 12, 12, 16))
    languages = list(_LOCALE_TAGS.get(locale, [locale, "en"]))

    return {
        "user_agent": spec["ua"].format(v=major),
        "platform": spec["platform"],
        "platform_label": os_name,
        "platform_ver": spec["platform_ver"],
        "ua_version": str(major),
        "viewport": (width, height),
        "screen": (screen_w, screen_h),
        "dpr": dpr,
        "color_depth": 24,
        "timezone_id": timezone_id,
        "locale": locale,
        "languages": languages,
        "accept_language": ", ".join(
            [languages[0]] + [f"{tag};q=0.9" for tag in languages[1:]]),
        "geo": {"longitude": lon, "latitude": lat},
        "city": city,
        "country": country,
        "webgl_vendor": webgl_vendor,
        "webgl_renderer": webgl_renderer,
        "hardware": {
            "deviceMemory": device_memory,
            "hardwareConcurrency": concurrency,
            "maxTouchPoints": 0,
        },
    }


def profile_for_proxy(proxy_url: str, seed: Optional[int] = None) -> Dict:
    """Generate a fingerprint whose geography matches a tagged proxy."""
    return generate_profile(seed=seed, country=country_from_proxy(proxy_url) or None)


def validate_profile(profile: Dict) -> List[str]:
    """Return every incoherence found in ``profile`` (empty list == coherent)."""
    issues: List[str] = []
    required = ("user_agent", "platform", "platform_label", "platform_ver",
                "ua_version", "viewport", "timezone_id", "locale", "geo",
                "webgl_vendor", "webgl_renderer", "hardware")
    missing = [key for key in required if key not in profile]
    if missing:
        return [f"missing keys: {missing}"]

    ua = str(profile["user_agent"])
    major = str(profile["ua_version"])
    if f"Chrome/{major}." not in ua:
        issues.append(f"UA version and ua_version disagree ({major})")
    if not ua.endswith("Safari/537.36"):
        issues.append("UA does not end with Safari/537.36")

    expected_platform = {"Windows NT": "Win32", "Macintosh": "MacIntel", "X11": "Linux x86_64"}
    matched = False
    for needle, platform in expected_platform.items():
        if needle in ua:
            matched = True
            if profile["platform"] != platform:
                issues.append(f"platform {profile['platform']!r} contradicts UA OS")
    if not matched:
        issues.append("UA names no known desktop OS")

    label_gpu = {"Windows": ("NVIDIA", "Intel", "AMD"),
                 "macOS": ("Apple",), "Linux": ("Mesa", "AMD", "NVIDIA", "Intel")}
    label = str(profile.get("platform_label") or "")
    renderer = str(profile["webgl_renderer"])
    if label in label_gpu and not any(token in renderer for token in label_gpu[label]):
        issues.append(f"GPU renderer does not belong to {label}")
    if "SwiftShader" in renderer or "llvmpipe" in renderer.lower():
        issues.append("software renderer in a desktop profile")

    viewport = profile["viewport"]
    if (not isinstance(viewport, (tuple, list)) or len(viewport) != 2
            or not all(isinstance(v, int) and v > 0 for v in viewport)):
        issues.append("viewport must be a positive (width, height) pair")
    else:
        screen = profile.get("screen") or viewport
        if viewport[0] > screen[0] or viewport[1] > screen[1]:
            issues.append("viewport larger than the claimed screen")

    dpr = profile.get("dpr")
    if dpr is not None and dpr not in (1.0, 1.25, 1.5, 2.0):
        issues.append(f"unusual devicePixelRatio {dpr}")

    hardware = profile["hardware"]
    if set(hardware) != {"deviceMemory", "hardwareConcurrency", "maxTouchPoints"}:
        issues.append("hardware block malformed")
    elif not all(isinstance(value, int) for value in hardware.values()):
        issues.append("hardware values must be integers")

    geo = profile["geo"]
    if set(geo) != {"longitude", "latitude"}:
        issues.append("geo must contain longitude/latitude")
    elif not (-180 <= geo["longitude"] <= 180 and -90 <= geo["latitude"] <= 90):
        issues.append("geo coordinates out of range")

    locale = str(profile["locale"])
    if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", locale):
        issues.append(f"locale {locale!r} is not xx-YY")
    if not re.fullmatch(r"[A-Za-z_]+/[A-Za-z_]+", str(profile["timezone_id"])):
        issues.append(f"timezone {profile['timezone_id']!r} is not Area/City")

    return issues


def score_profile(profile: Dict) -> int:
    """0-100 coherence score (100 == nothing to flag)."""
    issues = validate_profile(profile)
    return max(0, 100 - 15 * len(issues))


def summary(profile: Dict) -> str:
    """One-line description for logs."""
    width, height = profile.get("viewport", (0, 0))
    hardware = profile.get("hardware") or {}
    return (
        f"{profile.get('platform_label', '?')} · Chrome {profile.get('ua_version', '?')} · "
        f"{width}x{height} @{profile.get('dpr', 1)} · {profile.get('locale', '?')} "
        f"{profile.get('timezone_id', '?')} · {hardware.get('hardwareConcurrency', '?')} cores "
        f"· {profile.get('city', profile.get('country', '?'))}"
    )


def init_script(profile: Dict) -> str:
    """JS that makes the browser tell the generated profile's story.

    The scraper keeps its own richer inline script (a tested f-string), so this
    one is for the agent browser and for ad-hoc sessions. It covers the values
    that are cheap to spoof and expensive to get wrong: webdriver, chrome
    runtime, plugins, languages, WebGL, hardware, UA-CH and screen.
    """
    import json as _json

    languages = profile.get("languages") or [profile.get("locale", "en-US"), "en"]
    screen = profile.get("screen") or profile.get("viewport") or (1920, 1080)
    hardware = profile.get("hardware") or {}
    brands = [
        {"brand": "Not_A Brand", "version": "24"},
        {"brand": "Chromium", "version": str(profile.get("ua_version", "131"))},
        {"brand": "Google Chrome", "version": str(profile.get("ua_version", "131"))},
    ]
    payload = {
        "webglVendor": profile.get("webgl_vendor", "Google Inc. (NVIDIA)"),
        "webglRenderer": profile.get("webgl_renderer", ""),
        "languages": languages,
        "locale": profile.get("locale", "en-US"),
        "platform": profile.get("platform", "Win32"),
        "platformLabel": profile.get("platform_label", "Windows"),
        "platformVersion": profile.get("platform_ver", "15.0.0"),
        "uaVersion": str(profile.get("ua_version", "131")),
        "deviceMemory": hardware.get("deviceMemory", 8),
        "hardwareConcurrency": hardware.get("hardwareConcurrency", 8),
        "maxTouchPoints": hardware.get("maxTouchPoints", 0),
        "screenWidth": screen[0],
        "screenHeight": screen[1],
        "dpr": profile.get("dpr", 1),
        "brands": brands,
    }
    data = _json.dumps(payload)
    return f"""
(() => {{
  const P = {data};
  try {{ Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }}); }} catch (e) {{}}
  try {{ Object.defineProperty(navigator, 'platform', {{ get: () => P.platform }}); }} catch (e) {{}}
  window.chrome = window.chrome || {{}};
  try {{
    if (!window.chrome.runtime) window.chrome.runtime = {{}};
    if (!window.chrome.loadTimes) window.chrome.loadTimes = function () {{}};
    if (!window.chrome.csi) window.chrome.csi = function () {{}};
    if (!window.chrome.app) window.chrome.app = {{ isInstalled: false }};
  }} catch (e) {{}}
  try {{
    const makePlugin = (name, filename) => {{
      const plugin = Object.create(Plugin.prototype);
      Object.defineProperties(plugin, {{
        name: {{ value: name, enumerable: true }},
        filename: {{ value: filename, enumerable: true }},
        description: {{ value: '', enumerable: true }},
        length: {{ value: 0, enumerable: true }}
      }});
      return plugin;
    }};
    const plugins = [makePlugin('Chrome PDF Plugin', 'internal-pdf-viewer'),
                     makePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoikgieeekl'),
                     makePlugin('Native Client', 'internal-nacl-plugin')];
    const fake = Object.create(PluginArray.prototype);
    plugins.forEach((plugin, index) => Object.defineProperty(fake, index, {{ value: plugin, enumerable: true }}));
    Object.defineProperty(fake, 'length', {{ value: plugins.length, enumerable: true }});
    Object.defineProperty(navigator, 'plugins', {{ get: () => fake }});
  }} catch (e) {{}}
  try {{ Object.defineProperty(navigator, 'languages', {{ get: () => P.languages }}); }} catch (e) {{}}
  try {{
    const patch = (proto) => {{
      const original = proto.getParameter;
      proto.getParameter = function (param) {{
        if (param === 37445) return P.webglVendor;
        if (param === 37446) return P.webglRenderer;
        return original.call(this, param);
      }};
    }};
    patch(WebGLRenderingContext.prototype);
    patch(WebGL2RenderingContext.prototype);
  }} catch (e) {{}}
  try {{
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => P.deviceMemory }});
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => P.hardwareConcurrency }});
    Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => P.maxTouchPoints }});
  }} catch (e) {{}}
  try {{
    Object.defineProperty(navigator, 'userAgentData', {{
      value: {{
        brands: P.brands,
        mobile: false,
        platform: P.platformLabel,
        getHighEntropyValues: async () => ({{
          architecture: 'x86', bitness: '64', model: '',
          platform: P.platformLabel, platformVersion: P.platformVersion,
          uaFullVersion: P.uaVersion + '.0.0.0',
          fullVersionList: P.brands.map((b) => ({{ brand: b.brand, version: b.version + '.0.0.0' }}))
        }}),
        toJSON: function () {{ return {{ brands: this.brands, mobile: this.mobile, platform: this.platform }}; }}
      }}
    }});
  }} catch (e) {{}}
  try {{
    Object.defineProperty(screen, 'width', {{ get: () => P.screenWidth }});
    Object.defineProperty(screen, 'height', {{ get: () => P.screenHeight }});
    Object.defineProperty(screen, 'availWidth', {{ get: () => P.screenWidth }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => P.screenHeight }});
    Object.defineProperty(window, 'devicePixelRatio', {{ get: () => P.dpr }});
  }} catch (e) {{}}
  try {{
    const query = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
      parameters.name === 'notifications'
        ? Promise.resolve({{ state: Notification.permission }})
        : query(parameters)
    );
  }} catch (e) {{}}
}})();
"""


def parse_proxy_list(entries: Sequence[str]) -> List[Dict]:
    """Split tagged proxies into ``{"url", "country"}`` records."""
    parsed = []
    for entry in entries or []:
        url = str(entry or "").strip()
        if not url:
            continue
        parsed.append({"url": strip_proxy_tag(url), "country": country_from_proxy(url)})
    return parsed
