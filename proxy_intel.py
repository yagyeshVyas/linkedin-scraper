"""Proxy scoreboard: health, geography, cooldowns, and a smarter picker.

The old rotation was round-robin: every proxy got the same share of traffic
whether it was fast, dead, or freshly banned. That is how a whole pool gets
burned in one run. This module keeps a per-proxy record and lets the scraper:

  * skip proxies still cooling down after a block,
  * retire proxies that keep failing,
  * prefer a healthy proxy in the country the fingerprint claims,
  * keep the no-immediate-repeat property (a rotation must change the IP).

State lives in ``output/proxy_intel.json``, written atomically, and is
advisory: when every proxy is cooling the picker still returns the least-bad
one instead of dropping to a direct connection.

Proxy URLs may carry a local country tag — ``http://user:pass@host:8080#DE``.
The fragment never reaches the proxy; it tells the fingerprint generator which
identity to wear.

Import-safe: standard library + ``fingerprint`` only.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fingerprint import country_from_proxy, strip_proxy_tag

logger = logging.getLogger(__name__)

DEFAULT_PATH = "output/proxy_intel.json"


@dataclass
class ProxyStat:
    url: str
    country: str = ""
    successes: int = 0
    blocks: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    latency_ms: float = 0.0
    last_used: float = 0.0
    cooldown_until: float = 0.0

    @property
    def samples(self) -> int:
        return self.successes + self.blocks + self.failures

    @property
    def success_rate(self) -> float:
        total = self.successes + self.blocks + self.failures
        return (self.successes / total) if total else 0.5

    def score(self) -> int:
        """0-100 health score; 50 means 'unknown'."""
        if self.samples == 0:
            return 50
        score = 100.0 * self.success_rate
        score -= min(30.0, self.blocks * 6.0)
        score -= min(20.0, self.consecutive_failures * 5.0)
        if self.latency_ms:
            score -= min(15.0, max(0.0, (self.latency_ms - 400) / 80.0))
        return int(max(0, min(100, round(score))))

    def cooling(self, now: Optional[float] = None) -> bool:
        return self.cooldown_until > (now if now is not None else time.time())

    def as_dict(self) -> Dict:
        return {
            "url": self.url,
            "country": self.country,
            "successes": self.successes,
            "blocks": self.blocks,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "latency_ms": round(self.latency_ms, 1),
            "last_used": self.last_used,
            "cooldown_until": self.cooldown_until,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ProxyStat":
        return cls(
            url=str(data.get("url") or ""),
            country=str(data.get("country") or ""),
            successes=int(data.get("successes") or 0),
            blocks=int(data.get("blocks") or 0),
            failures=int(data.get("failures") or 0),
            consecutive_failures=int(data.get("consecutive_failures") or 0),
            latency_ms=float(data.get("latency_ms") or 0.0),
            last_used=float(data.get("last_used") or 0.0),
            cooldown_until=float(data.get("cooldown_until") or 0.0),
        )


class ProxyIntel:
    """Per-proxy reputation with a deterministic, health-aware picker."""

    def __init__(self, path: str = DEFAULT_PATH, *, cooldown: float = 300.0,
                 dead_after: int = 6, enabled: bool = True,
                 clock=time.time, rng: Optional[random.Random] = None):
        self.path = Path(path)
        self.cooldown = float(cooldown)
        self.dead_after = max(1, int(dead_after))
        self.enabled = bool(enabled)
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._entries: Dict[str, ProxyStat] = {}
        self._loaded = False

    # ── persistence ──

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = data.get("proxies") if isinstance(data, dict) else None
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            stat = ProxyStat.from_dict(item)
            if stat.url:
                self._entries[stat.url] = stat

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "saved_at": self._clock(),
                       "proxies": [stat.as_dict() for stat in self._entries.values()]}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.debug("proxy intel save failed: %s", exc)

    # ── registration ──

    def register(self, proxies: Iterable, country: str = "") -> List[str]:
        """Register URLs (or ``{"url","country"}`` dicts). Returns clean URLs."""
        clean: List[str] = []
        changed = False
        with self._lock:
            self._load()
            for item in proxies or []:
                if isinstance(item, dict):
                    raw = str(item.get("url") or "")
                    tag = str(item.get("country") or "")
                else:
                    raw = str(item or "")
                    tag = ""
                url = strip_proxy_tag(raw).strip()
                if not url:
                    continue
                clean.append(url)
                stat = self._entries.get(url)
                if stat is None:
                    stat = ProxyStat(url=url, country=(tag or country_from_proxy(raw)).upper())
                    self._entries[url] = stat
                    changed = True
                elif tag and not stat.country:
                    stat.country = tag.upper()
                    changed = True
            if changed:
                self._save()
        return clean

    def get(self, url: str) -> Optional[ProxyStat]:
        with self._lock:
            self._load()
            return self._entries.get(strip_proxy_tag(url))

    # ── outcomes ──

    def mark_ok(self, url: str, latency_ms: Optional[float] = None) -> None:
        if not url:
            return
        with self._lock:
            self._load()
            stat = self._entry(url)
            stat.successes += 1
            stat.consecutive_failures = 0
            stat.cooldown_until = 0.0
            stat.last_used = self._clock()
            if latency_ms is not None:
                stat.latency_ms = (0.7 * stat.latency_ms + 0.3 * float(latency_ms)
                                   if stat.latency_ms else float(latency_ms))
            self._save()

    def mark_block(self, url: str, *, cooldown: Optional[float] = None) -> None:
        if not url:
            return
        with self._lock:
            self._load()
            stat = self._entry(url)
            stat.blocks += 1
            stat.last_used = self._clock()
            # Repeated blocks on the same IP deserve progressively longer rests.
            wait = float(cooldown if cooldown is not None else self.cooldown)
            wait *= 1.0 + min(stat.blocks - 1, 3)
            wait *= self._rng.uniform(0.9, 1.15)
            stat.cooldown_until = self._clock() + wait
            self._save()
            logger.info("proxy cooling %ss after block (%s)", int(wait), stat.url)

    def mark_failed(self, url: str, *, cooldown: Optional[float] = None) -> None:
        if not url:
            return
        with self._lock:
            self._load()
            stat = self._entry(url)
            stat.failures += 1
            stat.consecutive_failures += 1
            stat.last_used = self._clock()
            if stat.consecutive_failures >= self.dead_after:
                stat.cooldown_until = self._clock() + float(self.cooldown) * 4
            else:
                stat.cooldown_until = self._clock() + min(
                    60.0, float(cooldown if cooldown is not None else self.cooldown / 4))
            self._save()

    def mark_dead(self, url: str) -> None:
        with self._lock:
            self._load()
            stat = self._entry(url)
            stat.consecutive_failures = max(stat.consecutive_failures, self.dead_after)
            stat.cooldown_until = self._clock() + float(self.cooldown) * 8
            self._save()

    # ── picking ──

    def pick(self, proxies: Sequence[str], *, exclude: Optional[str] = None,
             prefer_country: str = "", cursor: int = 0) -> Tuple[Optional[str], int]:
        """Pick the next proxy. Returns ``(url | None, next_cursor)``.

        Mirrors round-robin when nothing is known (the old tests and the old
        behaviour depend on that), then applies health: cooling/dead proxies are
        skipped, country-matched ones get first refusal.
        """
        candidates = [strip_proxy_tag(str(url or "")) for url in proxies or [] if str(url or "").strip()]
        if not candidates:
            return None, cursor
        excluded = strip_proxy_tag(exclude) if exclude else ""
        if len(candidates) == 1:
            only = candidates[0]
            return (None if only == excluded else only), cursor

        with self._lock:
            self._load()
            now = self._clock()
            eligible = [url for url in candidates
                        if url != excluded and not self._is_dead(url)
                        and not self._stat(url).cooling(now)]
            preferred = [url for url in eligible
                         if prefer_country and self._stat(url).country == prefer_country.upper()]
            pool = preferred or eligible
            if pool:
                start = cursor % len(candidates)
                next_cursor = cursor
                for offset in range(len(candidates)):
                    url = candidates[(start + offset) % len(candidates)]
                    next_cursor = (start + offset + 1) % len(candidates)
                    if url in pool:
                        self._stat(url).last_used = now
                        return url, next_cursor
                return pool[0], next_cursor

            # Everything is cooling or dead. Prefer the one that recovers first.
            rest = [url for url in candidates if url != excluded]
            if not rest:
                return None, cursor
            rest.sort(key=self._recovery_rank)
            return rest[0], cursor

    def _recovery_rank(self, url: str) -> Tuple[float, float, int]:
        stat = self._stat(url)
        return (stat.cooldown_until, -stat.score(), stat.failures)

    def _is_dead(self, url: str) -> bool:
        stat = self._stat(url)
        return stat.consecutive_failures >= self.dead_after and not stat.successes

    # ── reporting ──

    def summary(self) -> List[Dict]:
        with self._lock:
            self._load()
            stats = list(self._entries.values())
        stats.sort(key=lambda stat: (-stat.score(), stat.latency_ms))
        now = self._clock()
        rows = []
        for stat in stats:
            row = stat.as_dict()
            row["score"] = stat.score()
            row["cooling_for"] = max(0, int(stat.cooldown_until - now))
            rows.append(row)
        return rows

    def best(self) -> Optional[str]:
        rows = self.summary()
        return rows[0]["url"] if rows else None

    # ── internals ──

    def _entry(self, url: str) -> ProxyStat:
        clean = strip_proxy_tag(url)
        stat = self._entries.get(clean)
        if stat is None:
            stat = ProxyStat(url=clean, country=country_from_proxy(url))
            self._entries[clean] = stat
        return stat

    def _stat(self, url: str) -> ProxyStat:
        return self._entry(url)

    def reset_cooling(self) -> int:
        with self._lock:
            self._load()
            cleared = 0
            for stat in self._entries.values():
                if stat.cooldown_until:
                    stat.cooldown_until = 0.0
                    stat.consecutive_failures = 0
                    cleared += 1
            self._save()
            return cleared
