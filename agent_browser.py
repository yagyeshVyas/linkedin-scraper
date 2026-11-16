"""An agent-native browser: accessibility snapshots with stable refs.

This is the browser an LLM can actually drive.  Screenshots are expensive and
ambiguous; a DOM dump is noisy and unstable.  The surface here is the one the
good agent browsers converged on (Playwright MCP, Browser Use, Stagehand):

  * **Snapshot** -> a compact list of roles, names, values and ``[ref=eN]``.
    The model reads it like text and acts on refs, never on coordinates.
  * **Refs** -> each snapshot tags live elements with ``data-felix-ref``; a
    click/type resolves the ref through a selector, so the model's target is
    deterministic and verified (a stale ref errors instead of clicking the
    wrong thing).
  * **Actions** -> navigate, click, type, press, scroll, select, hover, wait,
    extract readable text, screenshot, cookies, evaluate.
  * **Walls** -> every navigation is classified; a Cloudflare/Turnstile/bot wall
    is solved through ``cf_bypass`` before the page is reported ready.

Two front doors:

    # 1. an MCP server on stdio (for Claude Desktop, Cursor, any MCP client)
    python agent_browser.py --serve --headless

    # 2. a plain async API for your own agent loop
    async with BrowserSession(headless=True) as session:
        await session.goto("https://example.com")
        snap = await session.snapshot()
        await session.click(snap["refs"][0])
        print(await session.extract())

``TOOL_SPECS`` exposes the same actions as OpenAI-style function schemas and
``dispatch(name, args)`` runs one, so a loop written against that format needs
no adapter.

Import-safe: no browser module is imported until a session starts.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cf_bypass import (Challenge, classify_page, harvest_cookies,
                       launch_stealth_browser, solve_challenge)

logger = logging.getLogger("agent_browser")

DEFAULT_STATE_DIR = "session/agent_browser"
DEFAULT_VIEWPORT = (1440, 900)
PROTOCOL_VERSION = "2024-11-05"

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
if (!window.chrome) {
  window.chrome = { runtime: {}, loadTimes: function () {}, csi: function () {},
                    app: { isInstalled: false } };
}
"""

_SNAPSHOT_JS = r"""
() => {
  const ACTIONABLE = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary', 'details',
    '[role="button"]', '[role="link"]', '[role="textbox"]', '[role="checkbox"]',
    '[role="radio"]', '[role="combobox"]', '[role="tab"]', '[role="menuitem"]',
    '[role="option"]', '[role="switch"]', '[role="slider"]', '[role="searchbox"]',
    '[contenteditable="true"]', '[onclick]', '[tabindex]:not([tabindex="-1"])'
  ].join(',');
  const SEMANTIC = 'h1,h2,h3,h4,h5,h6,img[alt],label,th,caption,blockquote,pre,dt,dd,figcaption';

  document.querySelectorAll('[data-felix-ref]').forEach(el => el.removeAttribute('data-felix-ref'));

  const selected = new Set();
  document.querySelectorAll(ACTIONABLE).forEach(el => selected.add(el));
  document.querySelectorAll(SEMANTIC).forEach(el => selected.add(el));

  // Leaf text nodes only: the innermost element with a short text run.
  document.querySelectorAll('p,li,td,th,dd,dt,blockquote,pre,figcaption,span,div,strong,em,b,i,small,time')
    .forEach(el => {
      if (selected.has(el) || el.children.length > 0) return;
      if (el.closest(ACTIONABLE)) return;
      const text = (el.textContent || '').trim();
      if (text.length < 2) return;
      selected.add(el);
    });

  const isVisible = (el) => {
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    return el.getClientRects().length > 0;
  };

  const all = Array.from(document.querySelectorAll('*'));
  const order = new Map(all.map((el, i) => [el, i]));
  const nodes = Array.from(selected).filter(isVisible)
    .sort((a, b) => order.get(a) - order.get(b));

  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'summary') return 'button';
    if (tag === 'img') return 'image';
    if (tag === 'label') return 'label';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'input') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button' || type === 'reset') return 'button';
      if (type === 'search') return 'searchbox';
      if (type === 'file') return 'file';
      if (type === 'range') return 'slider';
      return 'textbox';
    }
    return 'text';
  };

  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const text = labelledby.split(/\s+/).map(id => {
        const node = document.getElementById(id);
        return node ? node.textContent : '';
      }).join(' ').replace(/\s+/g, ' ').trim();
      if (text) return text;
    }
    const tag = el.tagName.toLowerCase();
    if (tag === 'img') return (el.getAttribute('alt') || '').trim().slice(0, 160);
    if (tag === 'input') {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'submit' || type === 'button' || type === 'reset') {
        return (el.value || '').trim().slice(0, 160);
      }
      if (el.labels && el.labels.length) {
        return (el.labels[0].innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160);
      }
      return (el.getAttribute('placeholder') || el.value || '').trim().slice(0, 160);
    }
    if (tag === 'select' || tag === 'textarea') {
      if (el.labels && el.labels.length) {
        return (el.labels[0].innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160);
      }
      return (el.getAttribute('placeholder') || '').trim().slice(0, 160);
    }
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  };

  const out = [];
  for (const el of nodes) {
    if (out.length >= 400) break;
    const tag = el.tagName.toLowerCase();
    const role = roleOf(el);
    const name = nameOf(el);
    if (!name && role === 'text') continue;
    const node = {
      ref: 'e' + (out.length + 1),
      role: role,
      name: name,
      tag: tag,
      depth: 0
    };
    const type = (el.type || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      node.value = String(el.value || '').slice(0, 80);
    }
    if (type === 'checkbox' || type === 'radio') node.checked = !!el.checked;
    if (tag === 'a') node.href = String(el.getAttribute('href') || '').slice(0, 160);
    if (role === 'heading' && /^h[1-6]$/.test(tag)) node.level = Number(tag[1]);
    let depth = 0;
    let parent = el.parentElement;
    while (parent) {
      if (selected.has(parent)) depth += 1;
      parent = parent.parentElement;
    }
    node.depth = depth;
    el.setAttribute('data-felix-ref', node.ref);
    out.push(node);
  }
  return { url: location.href, title: document.title, nodes: out };
}
"""

_EXTRACT_JS = r"""
(maxChars) => {
  const root = document.querySelector('main, article, [role="main"]') || document.body;
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script,style,noscript,svg,iframe,nav,footer,header,aside')
    .forEach(node => node.remove());
  let text = (clone.innerText || '').replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n').trim();
  if (text.length > maxChars) text = text.slice(0, maxChars) + '\n...[truncated]';
  const links = Array.from(root.querySelectorAll('a[href]')).slice(0, 300).map(a => ({
    text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 100),
    href: a.href
  })).filter(link => link.text && link.href.startsWith('http'));
  return {
    title: document.title,
    url: location.href,
    text: text,
    links: links
  };
}
"""


class ToolError(Exception):
    """Expected, agent-readable failure (bad ref, missing page, timeout)."""


# ─────────────────────────────────────────────────────────────
#  Snapshot formatting
# ─────────────────────────────────────────────────────────────

def format_snapshot(nodes: List[dict], *, max_nodes: int = 250,
                    max_name: int = 160) -> str:
    """Render snapshot nodes as the YAML-ish text an LLM reads."""
    lines: List[str] = []
    for node in (nodes or [])[:max(0, int(max_nodes))]:
        indent = "  " * min(int(node.get("depth") or 0), 12)
        role = str(node.get("role") or "text")
        name = str(node.get("name") or "")[:max_name]
        parts = [f"{indent}- {role}"]
        if name:
            parts.append(json.dumps(name, ensure_ascii=False))
        if node.get("level"):
            parts.append(f"[level={node['level']}]")
        if node.get("checked") is not None:
            parts.append("[checked]" if node["checked"] else "[unchecked]")
        if node.get("value"):
            parts.append(f"value={json.dumps(str(node['value']), ensure_ascii=False)}")
        if node.get("href"):
            parts.append(f"href={node['href']}")
        parts.append(f"[ref={node.get('ref')}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _ref_selector(ref: str) -> str:
    return f'[data-felix-ref="{ref}"]'


# ─────────────────────────────────────────────────────────────
#  Session
# ─────────────────────────────────────────────────────────────

class BrowserSession:
    """One stealth browser, one page, agent-facing actions.

    Async by nature; ``AgentBrowserServer`` runs it on a private event loop so
    the MCP transport can stay synchronous.
    """

    def __init__(self, *, engine: str = "auto", headless: bool = True,
                 proxy: Optional[str] = None, user_agent: str = "",
                 locale: str = "en-US", timezone_id: str = "America/New_York",
                 viewport: Tuple[int, int] = DEFAULT_VIEWPORT,
                 storage_state: str = "", state_dir: str = DEFAULT_STATE_DIR,
                 channel: str = "", timeout_ms: int = 20000,
                 solve_walls: bool = True):
        self.engine = engine
        self.headless = bool(headless)
        self.proxy = proxy or None
        self.user_agent = user_agent or ""
        self.locale = locale
        self.timezone_id = timezone_id
        self.viewport = viewport
        self.storage_state = storage_state or ""
        self.state_dir = Path(state_dir)
        self.channel = channel
        self.timeout_ms = int(timeout_ms)
        self.solve_walls = bool(solve_walls)

        self.handle = None
        self.browser = None
        self.context = None
        self.page = None
        self._nodes: List[dict] = []
        self._refs: Dict[str, dict] = {}
        self._snapshot_url = ""
        self._started = False

    # ── lifecycle ──

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def running(self) -> bool:
        return self._started and self.page is not None

    async def start(self) -> dict:
        if self.running:
            return self.info()
        proxy = {"server": self.proxy} if self.proxy else None
        self.handle = await launch_stealth_browser(
            engine=self.engine, headless=self.headless, proxy=proxy, channel=self.channel)
        self.browser = self.handle.browser
        context_args: Dict[str, Any] = {
            "viewport": {"width": int(self.viewport[0]), "height": int(self.viewport[1])},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "permissions": ["geolocation"],
        }
        if self.user_agent:
            context_args["user_agent"] = self.user_agent
        state = self._state_file()
        if self.storage_state:
            context_args["storage_state"] = self.storage_state
        elif state.exists():
            context_args["storage_state"] = str(state)
        self.context = await self.browser.new_context(**context_args)
        await self.context.add_init_script(_STEALTH_JS)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self._started = True
        logger.info("agent browser up (%s, headless=%s)", self.handle.engine, self.headless)
        return self.info()

    def info(self) -> dict:
        return {
            "engine": self.handle.engine if self.handle else "",
            "headless": self.headless,
            "url": self.page.url if self.page else "",
            "running": self.running,
        }

    def _state_file(self) -> Path:
        return self.state_dir / "state.json"

    async def close(self) -> dict:
        saved = ""
        if self.context is not None:
            try:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                await self.context.storage_state(path=str(self._state_file()))
                saved = str(self._state_file())
            except Exception:
                pass
        if self.handle is not None:
            try:
                await self.handle.close()
            except Exception:
                pass
        self.handle = None
        self.browser = None
        self.context = None
        self.page = None
        self._started = False
        self._nodes = []
        self._refs = {}
        self._snapshot_url = ""
        return {"closed": True, "state_saved": saved}

    def _require_page(self):
        if self.page is None:
            raise ToolError("no browser session - call browser_session first")
        return self.page

    # ── navigation ──

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded",
                   timeout_ms: Optional[int] = None, solve: Optional[bool] = None) -> dict:
        page = self._require_page()
        target = str(url or "").strip()
        if not target:
            raise ToolError("url is required")
        # Accept bare hosts ("example.com") but never mangle a real scheme:
        # data:, file:, about: and chrome: URLs must survive untouched.
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", target):
            target = "https://" + target
        started = time.time()
        status = 0
        try:
            response = await page.goto(target, wait_until=wait_until,
                                       timeout=int(timeout_ms or self.timeout_ms))
            status = int(getattr(response, "status", 0) or 0)
        except Exception as exc:
            raise ToolError(f"navigation failed: {type(exc).__name__}: {exc}") from exc

        challenge = await self._classify_current()
        solved = False
        if challenge.hard and (self.solve_walls if solve is None else solve):
            solved = await solve_challenge(page, timeout=max(20.0, self.timeout_ms / 1000.0))
            challenge = await self._classify_current()
        try:
            title = await page.title()
        except Exception:
            title = ""
        return {
            "url": page.url,
            "status": status,
            "title": title,
            "blocked": challenge.hard,
            "challenge": challenge.as_dict(),
            "solved": solved,
            "seconds": round(time.time() - started, 2),
        }

    async def _classify_current(self) -> Challenge:
        page = self._require_page()
        try:
            html = await page.content()
        except Exception:
            html = ""
        try:
            status = 0
            return classify_page(page.url, html, {}, status)
        except Exception:
            return Challenge()

    async def solve(self, timeout: Optional[float] = None) -> dict:
        page = self._require_page()
        before = await self._classify_current()
        if not before.hard:
            return {"solved": True, "challenge_before": before.as_dict(),
                    "challenge_after": before.as_dict(), "url": page.url}
        solved = await solve_challenge(page, timeout=float(timeout or self.timeout_ms / 1000.0))
        after = await self._classify_current()
        return {"solved": solved and not after.hard, "challenge_before": before.as_dict(),
                "challenge_after": after.as_dict(), "url": page.url}

    # ── snapshot ──

    async def snapshot(self, *, max_nodes: int = 250, include_text: bool = True,
                       text_limit: int = 4000) -> dict:
        page = self._require_page()
        try:
            data = await page.evaluate(_SNAPSHOT_JS)
        except Exception as exc:
            raise ToolError(f"snapshot failed: {type(exc).__name__}: {exc}") from exc
        nodes = list((data or {}).get("nodes") or [])
        self._nodes = nodes
        self._refs = {node["ref"]: node for node in nodes if node.get("ref")}
        self._snapshot_url = str((data or {}).get("url") or page.url)
        text = format_snapshot(nodes, max_nodes=max_nodes)
        payload = {
            "url": self._snapshot_url,
            "title": str((data or {}).get("title") or ""),
            "total": len(nodes),
            "shown": min(len(nodes), max(0, int(max_nodes))),
            "snapshot": text,
            "refs": list(self._refs),
        }
        if include_text:
            try:
                extracted = await page.evaluate(_EXTRACT_JS, max(500, int(text_limit)))
                payload["text"] = str((extracted or {}).get("text") or "")
            except Exception:
                payload["text"] = ""
        return payload

    async def find(self, text: str, *, limit: int = 20) -> dict:
        needle = str(text or "").strip().lower()
        if not needle:
            raise ToolError("text is required")
        if not self._nodes or self.page is None or self._snapshot_url != self.page.url:
            await self.snapshot(include_text=False)
        matches = [node for node in self._nodes
                   if needle in str(node.get("name") or "").lower()
                   or needle in str(node.get("role") or "").lower()]
        return {"query": text, "count": len(matches), "matches": matches[:max(1, int(limit))]}

    # ── actions ──

    async def _resolve(self, ref: Optional[str], selector: Optional[str]):
        page = self._require_page()
        if selector:
            locator = page.locator(selector)
            if await locator.count() == 0:
                raise ToolError(f"selector matched nothing: {selector!r}")
            return locator.first
        if not ref:
            raise ToolError("provide ref (from browser_snapshot) or selector")
        if not self._refs:
            raise ToolError("no snapshot yet - call browser_snapshot, then use its refs")
        if ref not in self._refs:
            raise ToolError(f"stale ref {ref!r} - the page changed; take a new snapshot")
        locator = page.locator(_ref_selector(ref))
        if await locator.count() == 0:
            raise ToolError(f"ref {ref!r} is no longer in the DOM - take a new snapshot")
        return locator.first

    async def click(self, *, ref: Optional[str] = None, selector: Optional[str] = None,
                    button: str = "left", click_count: int = 1,
                    timeout_ms: Optional[int] = None) -> dict:
        locator = await self._resolve(ref, selector)
        try:
            await locator.scroll_into_view_if_needed(timeout=int(timeout_ms or self.timeout_ms))
            await locator.click(button=button, click_count=max(1, int(click_count)),
                                timeout=int(timeout_ms or self.timeout_ms))
        except Exception as exc:
            raise ToolError(f"click failed: {type(exc).__name__}: {exc}") from exc
        return {"clicked": ref or selector, "url": self.page.url,
                "title": await self.page.title()}

    async def type_text(self, *, ref: Optional[str] = None, selector: Optional[str] = None,
                        text: str = "", submit: bool = False, clear: bool = True,
                        timeout_ms: Optional[int] = None) -> dict:
        locator = await self._resolve(ref, selector)
        try:
            await locator.scroll_into_view_if_needed(timeout=int(timeout_ms or self.timeout_ms))
            if clear:
                await locator.fill("", timeout=int(timeout_ms or self.timeout_ms))
            await locator.press_sequentially(str(text), delay=35,
                                             timeout=int(timeout_ms or self.timeout_ms))
            if submit:
                await locator.press("Enter", timeout=int(timeout_ms or self.timeout_ms))
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
        except Exception as exc:
            raise ToolError(f"type failed: {type(exc).__name__}: {exc}") from exc
        return {"typed": len(str(text)), "submitted": bool(submit), "url": self.page.url}

    async def press(self, key: str) -> dict:
        page = self._require_page()
        if not key:
            raise ToolError("key is required (e.g. Enter, Tab, Escape, Control+A)")
        await page.keyboard.press(key)
        return {"pressed": key, "url": page.url}

    async def hover(self, *, ref: Optional[str] = None, selector: Optional[str] = None) -> dict:
        locator = await self._resolve(ref, selector)
        await locator.hover(timeout=self.timeout_ms)
        return {"hovered": ref or selector}

    async def select_option(self, *, ref: Optional[str] = None, selector: Optional[str] = None,
                            value: str = "") -> dict:
        locator = await self._resolve(ref, selector)
        try:
            await locator.select_option(value=str(value), timeout=self.timeout_ms)
        except Exception as exc:
            raise ToolError(f"select failed: {type(exc).__name__}: {exc}") from exc
        return {"selected": value, "ref": ref or selector}

    async def scroll(self, *, amount: int = 800, ref: Optional[str] = None) -> dict:
        page = self._require_page()
        if ref:
            locator = await self._resolve(ref, None)
            await locator.scroll_into_view_if_needed(timeout=self.timeout_ms)
            return {"scrolled_to": ref}
        await page.mouse.wheel(0, int(amount))
        await asyncio.sleep(0.3)
        position = await page.evaluate("() => window.scrollY")
        return {"scrolled": int(amount), "scroll_y": position}

    async def wait(self, *, selector: str = "", text: str = "",
                   timeout_ms: Optional[int] = None) -> dict:
        page = self._require_page()
        budget = int(timeout_ms or self.timeout_ms)
        try:
            if selector:
                await page.wait_for_selector(selector, timeout=budget)
                return {"waited_for": selector, "url": page.url}
            if text:
                await page.get_by_text(text, exact=False).first.wait_for(timeout=budget)
                return {"waited_for_text": text, "url": page.url}
            await page.wait_for_timeout(budget)
            return {"waited_ms": budget, "url": page.url}
        except Exception as exc:
            raise ToolError(f"wait timed out: {type(exc).__name__}: {exc}") from exc

    # ── reading ──

    async def extract(self, *, max_chars: int = 20000, include_links: bool = True) -> dict:
        page = self._require_page()
        try:
            data = await page.evaluate(_EXTRACT_JS, max(1000, int(max_chars)))
        except Exception as exc:
            raise ToolError(f"extract failed: {type(exc).__name__}: {exc}") from exc
        payload = {
            "url": str((data or {}).get("url") or page.url),
            "title": str((data or {}).get("title") or ""),
            "text": str((data or {}).get("text") or "")[:max_chars],
            "length": len(str((data or {}).get("text") or "")),
        }
        if include_links:
            payload["links"] = (data or {}).get("links") or []
        return payload

    async def screenshot(self, *, path: str = "", full_page: bool = False,
                         as_base64: bool = False) -> dict:
        page = self._require_page()
        target = Path(path) if path else (
            Path("output") / "agent_screenshots" / f"shot_{int(time.time())}.png")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = await page.screenshot(path=str(target), full_page=bool(full_page))
        except Exception as exc:
            raise ToolError(f"screenshot failed: {type(exc).__name__}: {exc}") from exc
        payload = {"path": str(target), "bytes": len(raw or b""),
                   "url": page.url}
        if as_base64:
            payload["base64"] = base64.b64encode(raw or b"").decode("ascii")
        return payload

    async def evaluate(self, expression: str) -> dict:
        page = self._require_page()
        expr = str(expression or "").strip()
        if not expr:
            raise ToolError("expression is required")
        try:
            value = await page.evaluate(expr)
        except Exception as exc:
            raise ToolError(f"evaluate failed: {type(exc).__name__}: {exc}") from exc
        return {"result": value}

    async def cookies(self, *, domain: str = "") -> dict:
        if self.context is None:
            raise ToolError("no browser session")
        jar = await self.context.cookies(domain or None)
        return {"count": len(jar), "cookies": jar}

    async def save_state(self, path: str = "") -> dict:
        if self.context is None:
            raise ToolError("no browser session")
        target = Path(path) if path else self._state_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.context.storage_state(path=str(target))
        return {"path": str(target)}


# ─────────────────────────────────────────────────────────────
#  Async loop bridge (sync MCP -> async session)
# ─────────────────────────────────────────────────────────────

class AsyncRunner:
    """A private event loop thread, so one session survives many sync calls."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._loop is not None:
                return
            ready = threading.Event()

            def runner():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()

            self._thread = threading.Thread(target=runner, name="agent-browser-loop",
                                            daemon=True)
            self._thread.start()
            ready.wait(5.0)

    def run(self, coro, timeout: float = 180.0):
        self.start()
        loop = self._loop
        if loop is None:
            raise ToolError("agent browser loop failed to start")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = None
            self._thread = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=3.0)


# ─────────────────────────────────────────────────────────────
#  Tool surface
# ─────────────────────────────────────────────────────────────

def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


TOOLS: List[dict] = [
    {"name": "browser_session",
     "description": "Start (or inspect) the stealth browser. Call this before any other "
                    "browser tool. Returns the engine in use.",
     "inputSchema": _schema({
         "engine": {"type": "string", "enum": ["auto", "patchright", "camoufox", "playwright"],
                    "default": "auto"},
         "headless": {"type": "boolean", "default": True},
         "proxy": {"type": "string", "description": "http://host:port (optional)"},
     })},
    {"name": "browser_navigate",
     "description": "Navigate to a URL, classifying and solving Cloudflare/Turnstile/bot "
                    "walls on arrival. Returns status, title and wall state.",
     "inputSchema": _schema({
         "url": {"type": "string"},
         "wait_until": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle", "commit"],
                        "default": "domcontentloaded"},
         "solve": {"type": "boolean", "default": True},
     }, ["url"])},
    {"name": "browser_snapshot",
     "description": "Accessibility snapshot of the page: roles, names, values and [ref=eN] "
                    "handles. Act on refs with click/type - never guess coordinates.",
     "inputSchema": _schema({
         "max_nodes": {"type": "integer", "minimum": 10, "maximum": 400, "default": 250},
         "include_text": {"type": "boolean", "default": True},
         "text_limit": {"type": "integer", "minimum": 500, "maximum": 20000, "default": 4000},
     })},
    {"name": "browser_find",
     "description": "Search the current snapshot by name/role substring and return matching refs.",
     "inputSchema": _schema({
         "text": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
     }, ["text"])},
    {"name": "browser_click",
     "description": "Click an element by snapshot ref (preferred) or CSS selector.",
     "inputSchema": _schema({
         "ref": {"type": "string", "description": "e.g. e12 from browser_snapshot"},
         "selector": {"type": "string"},
         "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
         "click_count": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
     })},
    {"name": "browser_type",
     "description": "Type into an input/textarea/contenteditable by ref or selector. "
                    "Optionally press Enter to submit.",
     "inputSchema": _schema({
         "ref": {"type": "string"},
         "selector": {"type": "string"},
         "text": {"type": "string"},
         "submit": {"type": "boolean", "default": False},
         "clear": {"type": "boolean", "default": True},
     }, ["text"])},
    {"name": "browser_press",
     "description": "Press one keyboard key on the focused element (Enter, Tab, Escape, ...).",
     "inputSchema": _schema({"key": {"type": "string"}}, ["key"])},
    {"name": "browser_hover",
     "description": "Hover an element (reveals menus and tooltips).",
     "inputSchema": _schema({"ref": {"type": "string"}, "selector": {"type": "string"}})},
    {"name": "browser_select",
     "description": "Choose an option in a <select> element by value.",
     "inputSchema": _schema({
         "ref": {"type": "string"}, "selector": {"type": "string"},
         "value": {"type": "string"},
     }, ["value"])},
    {"name": "browser_scroll",
     "description": "Scroll the page, or bring a ref into view.",
     "inputSchema": _schema({
         "amount": {"type": "integer", "default": 800},
         "ref": {"type": "string"},
     })},
    {"name": "browser_extract",
     "description": "Readable text of the main content plus harvested links. Cheap way to "
                    "read an article or profile after navigation.",
     "inputSchema": _schema({
         "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "default": 20000},
         "include_links": {"type": "boolean", "default": True},
     })},
    {"name": "browser_screenshot",
     "description": "PNG screenshot of the viewport (or full page). Returns the saved path.",
     "inputSchema": _schema({
         "path": {"type": "string"},
         "full_page": {"type": "boolean", "default": False},
         "as_base64": {"type": "boolean", "default": False},
     })},
    {"name": "browser_wait",
     "description": "Wait for a selector, a text string, or a fixed number of milliseconds.",
     "inputSchema": _schema({
         "selector": {"type": "string"},
         "text": {"type": "string"},
         "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 120000},
     })},
    {"name": "browser_evaluate",
     "description": "Evaluate one JavaScript expression in the page and return its JSON value.",
     "inputSchema": _schema({"expression": {"type": "string"}}, ["expression"])},
    {"name": "browser_cookies",
     "description": "Dump the browser cookie jar (optionally for one domain).",
     "inputSchema": _schema({"domain": {"type": "string"}})},
    {"name": "browser_solve",
     "description": "Re-check the current page for a bot wall and wait for the challenge to "
                    "clear. Returns the verdict before and after.",
     "inputSchema": _schema({"timeout_seconds": {"type": "number", "minimum": 5, "maximum": 180}})},
    {"name": "browser_close",
     "description": "Persist the login state and close the browser.",
     "inputSchema": _schema({})},
]


TOOL_SPECS: List[dict] = [
    {"type": "function",
     "function": {"name": spec["name"], "description": spec["description"],
                  "parameters": spec["inputSchema"]}}
    for spec in TOOLS
]


class AgentBrowserServer:
    """MCP stdio server exposing the browser as agent tools."""

    def __init__(self, *, engine: str = "auto", headless: bool = True, proxy: str = "",
                 state_dir: str = DEFAULT_STATE_DIR, channel: str = "",
                 name: str = "agent-browser"):
        self.engine = engine
        self.headless = headless
        self.proxy = proxy
        self.state_dir = state_dir
        self.channel = channel
        self.name = name
        self.runner = AsyncRunner()
        self.session: Optional[BrowserSession] = None
        self.initialized = False

    # ── session plumbing ──

    def _ensure_session(self, engine: Optional[str] = None,
                        headless: Optional[bool] = None,
                        proxy: Optional[str] = None) -> BrowserSession:
        if self.session is not None and self.session.running:
            return self.session
        self.session = BrowserSession(
            engine=engine or self.engine,
            headless=self.headless if headless is None else bool(headless),
            proxy=(self.proxy if proxy is None else proxy) or None,
            state_dir=self.state_dir,
            channel=self.channel,
        )
        self.runner.run(self.session.start(), timeout=120)
        return self.session

    def dispatch(self, name: str, arguments: Optional[dict] = None) -> dict:
        """Run one tool by name. Returns a plain dict (raises ToolError)."""
        args = dict(arguments or {})
        session = self.session
        timeout = 240.0

        if name == "browser_session":
            if session is not None and session.running:
                return session.info()
            started = self._ensure_session(
                engine=args.get("engine") or self.engine,
                headless=args.get("headless", self.headless),
                proxy=args.get("proxy", self.proxy),
            )
            return started.info()

        if name == "browser_close":
            if session is None:
                return {"closed": True, "was_running": False}
            result = self.runner.run(session.close(), timeout=60)
            self.session = None
            return result

        if session is None or not session.running:
            session = self._ensure_session()

        if name == "browser_navigate":
            return self.runner.run(session.goto(
                args.get("url") or "",
                wait_until=str(args.get("wait_until") or "domcontentloaded"),
                solve=args.get("solve")),
                timeout=timeout)
        if name == "browser_snapshot":
            return self.runner.run(session.snapshot(
                max_nodes=int(args.get("max_nodes") or 250),
                include_text=bool(args.get("include_text", True)),
                text_limit=int(args.get("text_limit") or 4000)), timeout=timeout)
        if name == "browser_find":
            return self.runner.run(session.find(
                str(args.get("text") or ""), limit=int(args.get("limit") or 20)), timeout=60)
        if name == "browser_click":
            return self.runner.run(session.click(
                ref=args.get("ref"), selector=args.get("selector"),
                button=str(args.get("button") or "left"),
                click_count=int(args.get("click_count") or 1)), timeout=timeout)
        if name == "browser_type":
            return self.runner.run(session.type_text(
                ref=args.get("ref"), selector=args.get("selector"),
                text=str(args.get("text") or ""),
                submit=bool(args.get("submit", False)),
                clear=bool(args.get("clear", True))), timeout=timeout)
        if name == "browser_press":
            return self.runner.run(session.press(str(args.get("key") or "")), timeout=60)
        if name == "browser_hover":
            return self.runner.run(session.hover(
                ref=args.get("ref"), selector=args.get("selector")), timeout=60)
        if name == "browser_select":
            return self.runner.run(session.select_option(
                ref=args.get("ref"), selector=args.get("selector"),
                value=str(args.get("value") or "")), timeout=60)
        if name == "browser_scroll":
            return self.runner.run(session.scroll(
                amount=int(args.get("amount") or 800), ref=args.get("ref")), timeout=60)
        if name == "browser_extract":
            return self.runner.run(session.extract(
                max_chars=int(args.get("max_chars") or 20000),
                include_links=bool(args.get("include_links", True))), timeout=timeout)
        if name == "browser_screenshot":
            return self.runner.run(session.screenshot(
                path=str(args.get("path") or ""),
                full_page=bool(args.get("full_page", False)),
                as_base64=bool(args.get("as_base64", False))), timeout=120)
        if name == "browser_wait":
            return self.runner.run(session.wait(
                selector=str(args.get("selector") or ""),
                text=str(args.get("text") or ""),
                timeout_ms=args.get("timeout_ms")), timeout=timeout)
        if name == "browser_evaluate":
            return self.runner.run(session.evaluate(
                str(args.get("expression") or "")), timeout=60)
        if name == "browser_cookies":
            return self.runner.run(session.cookies(
                domain=str(args.get("domain") or "")), timeout=60)
        if name == "browser_solve":
            return self.runner.run(session.solve(
                timeout=args.get("timeout_seconds")), timeout=timeout)
        raise ToolError(f"unknown tool: {name}")

    # ── MCP protocol ──

    def handle(self, message: dict) -> Optional[dict]:
        if not isinstance(message, dict):
            return _error(None, -32600, "Invalid Request")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message

        if method == "initialize":
            requested = str((message.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION)
            result = {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": "6.0.0"},
                "instructions": (
                    "A stealth browser you drive by accessibility refs. Start with "
                    "browser_session, navigate, take a snapshot, then click/type the refs it "
                    "returns. Cloudflare walls are classified and solved automatically."
                ),
            }
            return None if is_notification else _result(msg_id, result)
        if method in ("notifications/initialized", "initialized"):
            self.initialized = True
            return None
        if method == "ping":
            return None if is_notification else _result(msg_id, {})
        if method == "tools/list":
            return None if is_notification else _result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = message.get("params") or {}
            try:
                payload = self.dispatch(str(params.get("name") or ""),
                                        params.get("arguments") or {})
                response = _tool_json(payload)
            except ToolError as exc:
                response = _tool_error(str(exc))
            except Exception as exc:
                logger.exception("tool failed")
                response = _tool_error(f"{type(exc).__name__}: {exc}")
            return None if is_notification else _result(msg_id, response)
        if method == "resources/list":
            return None if is_notification else _result(msg_id, {"resources": []})
        if method == "prompts/list":
            return None if is_notification else _result(msg_id, {"prompts": []})
        if method == "shutdown":
            return None if is_notification else _result(msg_id, {})
        return None if is_notification else _error(msg_id, -32601, f"Method not found: {method}")

    def close(self) -> None:
        if self.session is not None:
            try:
                self.runner.run(self.session.close(), timeout=30)
            except Exception:
                pass
            self.session = None
        self.runner.close()

    def serve(self, stdin=None, stdout=None) -> int:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _error(None, -32700, f"Parse error: {exc}")
            else:
                response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
        self.close()
        return 0


# ── JSON-RPC helpers ──

def _result(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_json(payload: dict) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str)}],
            "isError": False}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agent-native stealth browser (a11y refs, MCP server, CF solving).")
    parser.add_argument("--serve", action="store_true", help="run the MCP server on stdio")
    parser.add_argument("--engine", default="auto",
                        choices=["auto", "patchright", "camoufox", "playwright"])
    parser.add_argument("--headless", action="store_true", help="headless (default: headed)")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--channel", default="", help='browser channel, e.g. "chrome"')
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--url", default="", help="navigate here, then run the requested action")
    parser.add_argument("--snapshot", action="store_true", help="print the accessibility snapshot")
    parser.add_argument("--extract", action="store_true", help="print readable page text")
    parser.add_argument("--screenshot", default="", help="save a PNG to this path")
    parser.add_argument("--solve", action="store_true", help="solve a bot wall on --url")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    # Page text is arbitrary UTF-8; the Windows console is often cp1252 and a
    # single curly quote in a snapshot would otherwise kill the command.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)

    if args.serve:
        server = AgentBrowserServer(engine=args.engine, headless=bool(args.headless),
                                    proxy=args.proxy, state_dir=args.state_dir,
                                    channel=args.channel)
        logger.info("agent browser MCP ready on stdio (engine=%s)", args.engine)
        return server.serve()

    if not (args.url or args.snapshot or args.extract or args.screenshot or args.solve):
        build_parser().print_help()
        return 2

    async def run() -> dict:
        session = BrowserSession(engine=args.engine, headless=bool(args.headless),
                                 proxy=args.proxy or None, state_dir=args.state_dir,
                                 channel=args.channel)
        out: Dict[str, Any] = {}
        try:
            await session.start()
            if args.url:
                out["navigation"] = await session.goto(args.url)
            if args.solve:
                out["solve"] = await session.solve()
            if args.snapshot:
                out["snapshot"] = await session.snapshot()
            if args.extract:
                out["extract"] = await session.extract()
            if args.screenshot:
                out["screenshot"] = await session.screenshot(path=args.screenshot,
                                                             full_page=True)
        finally:
            await session.close()
        return out

    payload = asyncio.run(run())
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        snapshot = payload.get("snapshot") or {}
        if snapshot:
            print(snapshot.get("snapshot") or "")
            print()
        extract = payload.get("extract") or {}
        if extract:
            print(extract.get("text") or "")
        for key in ("navigation", "solve", "screenshot"):
            if payload.get(key):
                print(f"[{key}] {json.dumps(payload[key], default=str)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
