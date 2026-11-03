"""Human-like browser behaviour: pointer paths, wheel scrolling, warm-up.

LinkedIn's anti-automation signals are behavioural as much as syntactic.  A
session that jumps straight from a search URL to a profile URL, never moves the
mouse and scrolls in perfect 800px steps looks nothing like a person.  These
helpers add the cheap, low-risk half of that camouflage:

  * pointer movement along a slightly curved path with pauses,
  * wheel scrolling in uneven pulses instead of one teleport,
  * a "warm-up" visit to the feed (scroll, hover, read) after login,
  * small idle jitter between actions.

They are deliberately conservative: no clicking, no liking, no navigation away
from pages the scraper already visits.  Every helper degrades to a no-op when
the page object does not expose the API (unit tests use stubs).
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import Optional


def _viewport(page) -> tuple:
    size = getattr(page, "viewport_size", None)
    if isinstance(size, dict):
        width = int(size.get("width") or 1920)
        height = int(size.get("height") or 1080)
    elif isinstance(size, (tuple, list)) and len(size) == 2:
        width, height = int(size[0]), int(size[1])
    else:
        width, height = 1920, 1080
    return max(width, 320), max(height, 240)


async def human_mouse_move(page, moves: int = 2, *, spread: float = 0.6) -> None:
    """Move the cursor between random points with a curved, stepped path."""
    mouse = getattr(page, "mouse", None)
    if mouse is None or not hasattr(mouse, "move"):
        return
    width, height = _viewport(page)
    try:
        for _ in range(max(1, int(moves))):
            start_x = random.uniform(0.2, 0.8) * width
            start_y = random.uniform(0.2, 0.8) * height
            end_x = min(width - 8, max(8, start_x + random.uniform(-spread, spread) * width))
            end_y = min(height - 8, max(8, start_y + random.uniform(-spread, spread) * height))
            steps = random.randint(8, 22)
            for step in range(1, steps + 1):
                progress = step / steps
                # Quadratic ease with a small perpendicular bow, so the path is
                # not a straight line (browsers see pointermove events).
                bow = math.sin(progress * math.pi) * random.uniform(-22, 22)
                x = start_x + (end_x - start_x) * progress + bow * 0.4
                y = start_y + (end_y - start_y) * progress + bow
                await mouse.move(x, y)
                if random.random() < 0.12:
                    await asyncio.sleep(random.uniform(0.01, 0.04))
            await asyncio.sleep(random.uniform(0.15, 0.5))
    except Exception:
        return


async def human_scroll(page, pulses: int = 3, *, max_pixels: int = 900) -> None:
    """Scroll in uneven wheel pulses; falls back to window.scrollBy."""
    mouse = getattr(page, "mouse", None)
    for _ in range(max(1, int(pulses))):
        distance = random.randint(180, max(200, int(max_pixels)))
        try:
            if mouse is not None and hasattr(mouse, "wheel"):
                await mouse.wheel(0, distance)
            else:
                await page.evaluate("(dy) => window.scrollBy(0, dy)", distance)
        except Exception:
            return
        await asyncio.sleep(random.uniform(0.25, 0.9))
    # Small correction upwards now and then, like someone re-reading a line.
    if random.random() < 0.35:
        try:
            if mouse is not None and hasattr(mouse, "wheel"):
                await mouse.wheel(0, -random.randint(60, 180))
            else:
                await page.evaluate("() => window.scrollBy(0, -120)")
        except Exception:
            pass


async def hover_nav(page) -> None:
    """Hover a couple of navigation items (no clicks)."""
    selectors = ("nav.global-nav a", ".global-nav__primary-link", "header a")
    for selector in selectors:
        try:
            element = await page.query_selector(selector)
        except Exception:
            element = None
        if element is None:
            continue
        try:
            await element.hover(timeout=2000)
            await asyncio.sleep(random.uniform(0.2, 0.6))
        except Exception:
            continue


async def idle_jitter(page, chance: float = 0.3) -> None:
    """Occasionally move the mouse / scroll a little between real actions."""
    if random.random() > chance:
        return
    await human_mouse_move(page, moves=1, spread=0.25)


async def warm_up(page, *, home: str = "https://www.linkedin.com/feed/",
                  seconds: Optional[float] = None) -> bool:
    """Visit the feed, scroll and hover like a returning user. Returns success.

    Safe by construction: it only reads the feed, never opens a notification,
    never clicks a post and never navigates to a third-party site.
    """
    budget = float(seconds if seconds is not None else random.uniform(8, 18))
    try:
        await page.goto(home, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return False
    try:
        await asyncio.sleep(random.uniform(1.5, 3.5))
        await human_mouse_move(page, moves=2)
        await human_scroll(page, pulses=random.randint(1, 3), max_pixels=1200)
        await asyncio.sleep(max(0.5, budget * random.uniform(0.25, 0.5)))
        await hover_nav(page)
        await human_scroll(page, pulses=random.randint(1, 2), max_pixels=800)
        await asyncio.sleep(max(0.5, budget * random.uniform(0.2, 0.4)))
        return True
    except Exception:
        return False
