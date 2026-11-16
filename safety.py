"""Account-safety governor: budgets, wall circuit-breaker, cooldowns, risk.

The stack can already rotate identity and recover from walls. This module is
the opposite instinct: know when to **stop**. It keeps a small daily ledger and
refuses to keep hammering an account that is producing hard walls or too many
requests.

Rules (defaults, all configurable):

* hard-stop after 3 wall events in a day
* hard-stop after 120 profiles in a day
* hard-stop after 120 minutes in one run
* after a wall, cool down 90 s before the recovery path is allowed to rotate
* a run refuses to start when the day's wall/profile budget is already spent

State lives in ``output/safety.json`` and is written at run end (atomic). It is
advisory protection for the account, not a guarantee from LinkedIn.

Import-safe: standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PATH = "output/safety.json"
DEFAULT_MAX_WALLS = 3
DEFAULT_MAX_PROFILES = 120
DEFAULT_MAX_RUNTIME_MIN = 120
DEFAULT_COOLDOWN = 90


@dataclass
class SafetyState:
    day: str = ""
    runs: int = 0
    searches: int = 0
    profiles: int = 0
    walls: int = 0
    errors: int = 0
    last_wall_at: float = 0.0
    last_stop_reason: str = ""
    updated_at: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SafetyGuard:
    """Persisted daily budget + per-run circuit breaker."""

    def __init__(self, path: str = DEFAULT_PATH, *, enabled: bool = True,
                 max_walls: int = DEFAULT_MAX_WALLS,
                 max_profiles: int = DEFAULT_MAX_PROFILES,
                 max_searches: int = 0,
                 max_runtime_minutes: int = DEFAULT_MAX_RUNTIME_MIN,
                 cooldown_seconds: int = DEFAULT_COOLDOWN,
                 clock=time.time):
        self.path = Path(path)
        self.enabled = bool(enabled)
        self.max_walls = max(1, int(max_walls))
        self.max_profiles = max(1, int(max_profiles))
        self.max_searches = max(0, int(max_searches))
        self.max_runtime = max(1.0, float(max_runtime_minutes)) * 60.0
        self.cooldown = max(0.0, float(cooldown_seconds))
        self._clock = clock
        self.state = SafetyState(day=self._today())
        self._run_started: Optional[float] = None
        self._loaded = False

    # ── construction ──

    @classmethod
    def from_config(cls, config) -> "SafetyGuard":
        return cls(
            path=getattr(config, "SAFETY_LEDGER_FILE", DEFAULT_PATH),
            enabled=bool(getattr(config, "SAFE_MODE", True)),
            max_walls=int(getattr(config, "SAFETY_MAX_WALLS_PER_DAY", DEFAULT_MAX_WALLS)),
            max_profiles=int(getattr(config, "SAFETY_MAX_PROFILES_PER_DAY",
                                     DEFAULT_MAX_PROFILES)),
            max_searches=int(getattr(config, "MAX_DAILY_SEARCHES", 0) or 0),
            max_runtime_minutes=int(getattr(config, "SAFETY_MAX_RUNTIME_MINUTES",
                                            DEFAULT_MAX_RUNTIME_MIN)),
            cooldown_seconds=int(getattr(config, "SAFETY_COOLDOWN_SECONDS",
                                          DEFAULT_COOLDOWN)),
        )

    # ── time / day ──

    def _today(self) -> str:
        return date.fromtimestamp(self._clock()).isoformat()

    def _rollover(self) -> None:
        today = self._today()
        if self.state.day != today:
            self.state = SafetyState(day=today)
            self._loaded = True

    # ── persistence ──

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        allowed = set(SafetyState.__dataclass_fields__)
        values = {key: data[key] for key in allowed if key in data}
        try:
            self.state = SafetyState(**values)
        except TypeError:
            return
        self._rollover()

    def save(self) -> None:
        """Write the ledger atomically; never raise into a scrape."""
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.state.updated_at = self._clock()
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.state.as_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.debug("safety ledger write failed: %s", exc)

    # ── lifecycle ──

    def start_run(self) -> None:
        if not self.enabled:
            return
        self._load()
        self._rollover()
        self.state.runs += 1
        self._run_started = self._clock()
        self.save()

    def finish(self, reason: str = "") -> None:
        if not self.enabled:
            return
        if reason:
            self.state.last_stop_reason = str(reason)[:200]
        self._run_started = None
        self.save()

    # ── counters ──

    def tick_search(self, count: int = 1) -> None:
        if not self.enabled:
            return
        self._load()
        self._rollover()
        self.state.searches += max(0, int(count))

    def tick_profile(self, count: int = 1) -> None:
        if not self.enabled:
            return
        self._load()
        self._rollover()
        self.state.profiles += max(0, int(count))

    def note_wall(self, reason: str = "") -> None:
        if not self.enabled:
            return
        self._load()
        self._rollover()
        self.state.walls += 1
        self.state.last_wall_at = self._clock()
        logger.warning("🛡️  Safety: wall #%s today (%s)", self.state.walls, reason or "unknown")

    def note_error(self) -> None:
        if not self.enabled:
            return
        self._load()
        self._rollover()
        self.state.errors += 1

    # ── decisions ──

    def cooldown_remaining(self) -> float:
        if not self.enabled or not self.state.last_wall_at:
            return 0.0
        remaining = (self.state.last_wall_at + self.cooldown) - self._clock()
        return max(0.0, remaining)

    @property
    def active(self) -> bool:
        """True only inside a guarded run (start_run called)."""
        return self.enabled and self._run_started is not None

    def should_stop(self) -> Tuple[bool, str]:
        """True when continuing would be reckless. Returns (stop, reason)."""
        if not self.active:
            return False, ""
        self._load()
        self._rollover()
        if self.state.walls >= self.max_walls:
            return True, f"wall budget spent ({self.state.walls}/{self.max_walls} today)"
        if self.state.profiles >= self.max_profiles:
            return True, f"profile budget spent ({self.state.profiles}/{self.max_profiles} today)"
        if self.max_searches and self.state.searches >= self.max_searches:
            return True, f"search budget spent ({self.state.searches}/{self.max_searches} today)"
        if self._run_started is not None:
            elapsed = self._clock() - self._run_started
            if elapsed >= self.max_runtime:
                return True, f"runtime budget spent ({elapsed / 60:.0f} min)"
        return False, ""

    def can_start(self) -> Tuple[bool, str]:
        if not self.enabled:
            return True, ""
        self._load()
        self._rollover()
        if self.state.walls >= self.max_walls:
            return False, (f"daily wall budget already spent "
                           f"({self.state.walls}/{self.max_walls}); try again tomorrow")
        if self.state.profiles >= self.max_profiles:
            return False, (f"daily profile budget already spent "
                           f"({self.state.profiles}/{self.max_profiles}); try again tomorrow")
        if self.max_searches and self.state.searches >= self.max_searches:
            return False, (f"daily search budget already spent "
                           f"({self.state.searches}/{self.max_searches}); try again tomorrow")
        return True, ""

    def risk(self) -> int:
        """0-100 heuristic: walls dominate, then volume, then errors."""
        if not self.enabled:
            return 0
        self._load()
        self._rollover()
        walls = min(1.0, self.state.walls / self.max_walls)
        profiles = min(1.0, self.state.profiles / self.max_profiles)
        errors = min(1.0, self.state.errors / 8.0)
        score = 100.0 * (0.62 * walls + 0.26 * profiles + 0.12 * errors)
        return int(max(0, min(100, round(score))))

    def grade(self) -> str:
        risk = self.risk()
        return "low" if risk < 25 else "medium" if risk < 55 else "high" if risk < 80 else "critical"

    def summary(self) -> Dict[str, Any]:
        self._load()
        self._rollover()
        return {
            "enabled": self.enabled,
            "risk": self.risk(),
            "grade": self.grade(),
            "day": self.state.day,
            "runs": self.state.runs,
            "searches": self.state.searches,
            "profiles": self.state.profiles,
            "walls": self.state.walls,
            "errors": self.state.errors,
            "max_walls": self.max_walls,
            "max_profiles": self.max_profiles,
            "max_searches": self.max_searches,
            "max_runtime_minutes": int(self.max_runtime / 60),
            "cooldown_remaining": round(self.cooldown_remaining(), 1),
            "last_stop_reason": self.state.last_stop_reason,
            "last_wall_at": self.state.last_wall_at,
        }


def read_summary(path: str = DEFAULT_PATH) -> Dict[str, Any]:
    """Read-only summary for the dashboard (no clock side effects)."""
    guard = SafetyGuard(path)
    return guard.summary()
