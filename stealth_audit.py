"""Live stealth audit: measure the browser instead of assuming it is fine.

Every anti-detection claim in this repo is a hypothesis until something checks
it inside a real page. This module evaluates a probe in the running browser and
scores the result, so a broken patch, a fingerprint/UA mismatch or a headless
tell shows up as a number instead of a ban three days later.

    from stealth_audit import audit_page
    result = await audit_page(page, expected=profile)
    print(result["score"], result["grade"])
    for finding in result["findings"]:
        if not finding["ok"]:
            print(finding["id"], finding["detail"])

Checks: navigator.webdriver, chrome runtime, plugins/mimeTypes, languages,
platform vs UA, UA-CH (userAgentData), WebGL vendor/renderer (and SwiftShader),
hardware, timezone, screen vs viewport, CDP artifacts, iframe webdriver.

The scoring is deliberately blunt. A missing plugin array is not fatal; a
`webdriver = true` is. Weights live in one table so the audit is auditable.

Import-safe: no browser import at module load.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

PROBE_JS = r"""
() => {
  const glInfo = (() => {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      if (!gl) return { vendor: '', renderer: '' };
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(37445);
      const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(37446);
      return { vendor: String(vendor || ''), renderer: String(renderer || '') };
    } catch (e) { return { vendor: '', renderer: '' }; }
  })();
  const cdcProps = Object.keys(window).filter((key) =>
    /^(cdc_|_CDP|__driver|domAutomation|__playwright|__puppeteer)/.test(key));
  const iframeWebdriver = (() => {
    try {
      const frame = document.createElement('iframe');
      frame.style.display = 'none';
      document.body.appendChild(frame);
      const value = frame.contentWindow.navigator.webdriver;
      frame.remove();
      return value === undefined ? 'undefined' : String(value);
    } catch (e) { return 'error'; }
  })();
  return {
    url: location.href,
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    webdriver: navigator.webdriver === undefined ? 'undefined' : String(navigator.webdriver),
    languages: Array.from(navigator.languages || []),
    language: navigator.language,
    plugins: navigator.plugins ? navigator.plugins.length : 0,
    mimeTypes: navigator.mimeTypes ? navigator.mimeTypes.length : 0,
    hardwareConcurrency: navigator.hardwareConcurrency || 0,
    deviceMemory: navigator.deviceMemory === undefined ? null : navigator.deviceMemory,
    maxTouchPoints: navigator.maxTouchPoints || 0,
    webglVendor: glInfo.vendor,
    webglRenderer: glInfo.renderer,
    timezone: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return ''; } })(),
    screen: {
      width: screen.width, height: screen.height,
      availWidth: screen.availWidth, availHeight: screen.availHeight,
      colorDepth: screen.colorDepth
    },
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    hasChrome: !!window.chrome,
    chromeKeys: Object.keys(window.chrome || {}),
    chromeRuntime: !!(window.chrome && window.chrome.runtime),
    chromeLoadTimes: !!(window.chrome && window.chrome.loadTimes),
    chromeCsi: !!(window.chrome && window.chrome.csi),
    pdfViewer: navigator.pdfViewerEnabled === true,
    uaData: navigator.userAgentData ? {
      brands: Array.from(navigator.userAgentData.brands || []),
      platform: navigator.userAgentData.platform,
      mobile: navigator.userAgentData.mobile
    } : null,
    cdcProps: cdcProps,
    iframeWebdriver: iframeWebdriver
  };
}
"""

_ZONE_ALIASES = {
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Rangoon": "Asia/Yangon",
    "Europe/Kiev": "Europe/Kyiv",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
}


def _same_zone(actual: str, expected: str) -> bool:
    """Compare IANA zones while tolerating platform aliases (Windows tzdata)."""
    if not actual or not expected:
        return False
    actual = _ZONE_ALIASES.get(actual, actual)
    expected = _ZONE_ALIASES.get(expected, expected)
    return actual == expected


#: id -> (weight, description). Weights sum to 128.
CHECKS: Dict[str, Tuple[int, str]] = {
    "webdriver": (30, "navigator.webdriver must be undefined"),
    "chrome_runtime": (10, "window.chrome must exist in a Chromium browser"),
    "plugins": (10, "plugin/mimeType arrays must look populated"),
    "languages": (8, "navigator.languages must be non-empty and coherent"),
    "platform": (12, "navigator.platform must agree with the UA"),
    "ua_data": (10, "userAgentData brands/platform must agree with the UA"),
    "webgl": (12, "WebGL must report a real GPU, not SwiftShader"),
    "hardware": (6, "hardwareConcurrency/deviceMemory must look like a desktop"),
    "timezone": (6, "timezone must be a real IANA zone"),
    "screen": (4, "viewport must fit inside the screen"),
    "cdp": (12, "no CDP / automation globals may leak into window"),
    "iframe_webdriver": (8, "an iframe must see the same clean navigator"),
}

_TOTAL_WEIGHT = sum(weight for weight, _ in CHECKS.values())


def _check(check_id: str, ok: bool, detail: str) -> Dict:
    weight, description = CHECKS[check_id]
    return {"id": check_id, "ok": bool(ok), "weight": weight,
            "detail": detail, "expects": description}


def evaluate(signals: Dict, expected: Optional[Dict] = None) -> Dict:
    """Grade one probe result. Pure function — used by tests and by audit_page."""
    findings: List[Dict] = []
    ua = str(signals.get("userAgent") or "")
    platform = str(signals.get("platform") or "")
    expected = expected or {}

    findings.append(_check(
        "webdriver", str(signals.get("webdriver")) in ("undefined", "None", "false"),
        f"navigator.webdriver = {signals.get('webdriver')}"))
    chrome_keys = signals.get("chromeKeys") or []
    chrome_alive = (bool(signals.get("hasChrome"))
                    and (bool(signals.get("chromeRuntime"))
                         or bool(signals.get("chromeLoadTimes"))
                         or bool(signals.get("chromeCsi"))
                         or "runtime" in chrome_keys))
    findings.append(_check(
        "chrome_runtime", chrome_alive,
        f"window.chrome keys: {chrome_keys or 'none'}"))

    plugins = int(signals.get("plugins") or 0)
    mime_types = int(signals.get("mimeTypes") or 0)
    pdf_viewer = bool(signals.get("pdfViewer"))
    findings.append(_check(
        "plugins", plugins >= 2 or mime_types >= 2 or pdf_viewer,
        f"plugins={plugins}, mimeTypes={mime_types}, pdfViewer={pdf_viewer}"))

    languages = signals.get("languages") or []
    language = str(signals.get("language") or "")
    coherent_language = bool(languages) and (
        not language or str(languages[0]).lower().split("-")[0]
        == language.lower().split("-")[0])
    findings.append(_check(
        "languages", coherent_language and len(languages) >= 1,
        f"languages={languages}, language={language or '?'}"))

    platform_ok = True
    platform_detail = f"platform={platform or '?'}"
    if "Windows" in ua:
        platform_ok = platform in ("Win32", "Win64", "Windows")
    elif "Macintosh" in ua or "Mac OS X" in ua:
        platform_ok = platform in ("MacIntel", "Macintosh")
    elif "Linux" in ua and "Android" not in ua:
        platform_ok = platform.startswith("Linux")
    if expected.get("platform"):
        platform_ok = platform_ok and platform == expected["platform"]
        platform_detail += f" (expected {expected['platform']})"
    findings.append(_check("platform", platform_ok, platform_detail))

    ua_data = signals.get("uaData") or {}
    brands = ua_data.get("brands") or []
    brand_ok = bool(brands) and any(
        str(brand.get("brand", "")).lower() in ("chromium", "google chrome")
        for brand in brands if isinstance(brand, dict))
    mobile = ua_data.get("mobile")
    ua_mobile = "Mobile" in ua
    if ua_data:
        brand_ok = brand_ok and (bool(mobile) == ua_mobile)
        ua_platform = str(ua_data.get("platform") or "")
        if "Windows" in ua:
            brand_ok = brand_ok and ua_platform in ("Windows", "Win32")
        elif "Mac" in ua:
            brand_ok = brand_ok and ua_platform in ("macOS", "MacIntel")
        elif "Linux" in ua:
            brand_ok = brand_ok and ua_platform.startswith("Linux")
    findings.append(_check(
        "ua_data", brand_ok,
        f"brands={[b.get('brand') for b in brands if isinstance(b, dict)]}, "
        f"platform={ua_data.get('platform')!r}"))

    renderer = str(signals.get("webglRenderer") or "")
    vendor = str(signals.get("webglVendor") or "")
    software = ("swiftshader" in renderer.lower() or "llvmpipe" in renderer.lower()
                or "software" in renderer.lower())
    webgl_ok = bool(renderer) and bool(vendor) and not software
    if expected.get("webgl_renderer"):
        webgl_ok = webgl_ok and expected["webgl_renderer"] == renderer
    findings.append(_check(
        "webgl", webgl_ok,
        f"{vendor or '?'} / {renderer or '?'}" + (" [software]" if software else "")))

    cores = int(signals.get("hardwareConcurrency") or 0)
    memory = signals.get("deviceMemory")
    hardware_ok = 2 <= cores <= 128 and (memory is None or 2 <= int(memory) <= 128)
    if expected.get("hardware"):
        hardware_ok = (hardware_ok
                       and cores == int(expected["hardware"].get("hardwareConcurrency", cores))
                       and (memory is None
                            or int(memory) == int(expected["hardware"].get("deviceMemory", memory))))
    findings.append(_check(
        "hardware", hardware_ok,
        f"cores={cores}, deviceMemory={memory}"))

    timezone = str(signals.get("timezone") or "")
    timezone_ok = bool(re.fullmatch(r"[A-Za-z_]+/[A-Za-z_]+", timezone))
    if expected.get("timezone_id"):
        timezone_ok = timezone_ok and _same_zone(timezone, expected["timezone_id"])
    findings.append(_check(
        "timezone", timezone_ok,
        f"timezone={timezone or '?'}"
        + (f" (expected {expected['timezone_id']})" if expected.get("timezone_id") else "")))

    screen = signals.get("screen") or {}
    viewport = signals.get("viewport") or {}
    try:
        screen_ok = (int(viewport.get("width") or 0) <= int(screen.get("width") or 0)
                     and int(viewport.get("height") or 0) <= int(screen.get("height") or 0))
    except (TypeError, ValueError):
        screen_ok = False
    if expected.get("viewport"):
        expected_w, expected_h = expected["viewport"]
        screen_ok = screen_ok and (int(viewport.get("width") or 0) == int(expected_w)
                                   and int(viewport.get("height") or 0) == int(expected_h))
    findings.append(_check(
        "screen", screen_ok,
        f"viewport={viewport.get('width')}x{viewport.get('height')} "
        f"screen={screen.get('width')}x{screen.get('height')} dpr={viewport.get('dpr')}"))

    leaks = signals.get("cdcProps") or []
    findings.append(_check("cdp", not leaks, f"automation globals: {leaks or 'none'}"))

    iframe_value = str(signals.get("iframeWebdriver", "undefined"))
    findings.append(_check(
        "iframe_webdriver", iframe_value == "undefined",
        f"iframe navigator.webdriver = {iframe_value}"))

    earned = sum(item["weight"] for item in findings if item["ok"])
    score = int(round(100.0 * earned / _TOTAL_WEIGHT))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return {
        "score": score,
        "grade": grade,
        "passed": sum(1 for item in findings if item["ok"]),
        "total": len(findings),
        "findings": findings,
        "signals": signals,
    }


async def audit_page(page, expected: Optional[Dict] = None) -> Dict:
    """Run the probe in a live Playwright/Patchright page and grade it."""
    try:
        signals = await page.evaluate(PROBE_JS)
    except Exception as exc:
        return {"score": 0, "grade": "F", "passed": 0, "total": len(CHECKS),
                "findings": [_check("webdriver", False,
                                    f"probe failed: {type(exc).__name__}: {exc}")],
                "signals": {}}
    return evaluate(dict(signals or {}), expected)


async def audit_session(session, expected: Optional[Dict] = None) -> Dict:
    """Audit a ``BrowserSession`` (navigates to a blank harness if needed)."""
    page = session.page
    if page is None:
        raise RuntimeError("session has no page")
    if str(page.url or "").startswith("about:"):
        await session.goto(
            "data:text/html,<html><body><h1>audit</h1></body></html>",
            solve=False)
    result = await audit_page(page, expected)
    result["url"] = page.url
    return result


def report_text(result: Dict, *, only_failures: bool = False) -> str:
    lines = [f"Stealth audit: {result.get('score')}/100  grade {result.get('grade')} "
             f"({result.get('passed')}/{result.get('total')} checks)"]
    for finding in result.get("findings") or []:
        if only_failures and finding["ok"]:
            continue
        mark = "PASS" if finding["ok"] else "FAIL"
        lines.append(f"[{mark}] {finding['id']:<16} {finding['detail']}")
    return "\n".join(lines)
