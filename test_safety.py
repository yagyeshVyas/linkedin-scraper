"""Behavioral tests for the account-safety governor.

No browser, no network, no real waiting. Run with:
    .venv/Scripts/python.exe test_safety.py
"""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from safety import SafetyGuard
from webapp import build_run_args


def _guard(tmp, now, **kwargs):
    return SafetyGuard(str(Path(tmp) / "safety.json"),
                       clock=lambda: now[0], **kwargs)


def test_fresh_guard_is_clean():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now)
        allowed, reason = guard.can_start()
        assert allowed and reason == ""
        assert guard.risk() == 0
        assert guard.grade() == "low"


def test_profile_budget_stops_an_active_run():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, max_profiles=2)
        guard.start_run()
        guard.tick_profile()
        guard.tick_profile()
        stop, reason = guard.should_stop()
        assert stop and "profile budget" in reason


def test_wall_budget_stops_run():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, max_walls=2)
        guard.start_run()
        guard.note_wall("authwall")
        assert not guard.should_stop()[0]
        guard.note_wall("rate_limit")
        stop, reason = guard.should_stop()
        assert stop and "wall budget" in reason


def test_wall_cooldown_counts_down():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, cooldown_seconds=90)
        guard.start_run()
        guard.note_wall("captcha")
        assert 85 <= guard.cooldown_remaining() <= 90
        now[0] += 100
        assert guard.cooldown_remaining() == 0


def test_runtime_budget_stops():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, max_runtime_minutes=1)
        guard.start_run()
        now[0] += 61
        stop, reason = guard.should_stop()
        assert stop and "runtime" in reason


def test_disabled_guard_never_stops_or_writes():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, enabled=False, max_walls=1)
        guard.start_run()
        guard.note_wall("authwall")
        guard.tick_profile(999)
        assert guard.should_stop() == (False, "")
        assert guard.can_start() == (True, "")
        guard.finish("x")
        assert not (Path(tmp) / "safety.json").exists()


def test_ledger_persists_and_blocks_next_run():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, max_walls=2)
        guard.start_run()
        guard.note_wall("authwall")
        guard.note_wall("authwall")
        guard.tick_profile(3)
        guard.finish("safety: wall budget spent")

        path = Path(tmp) / "safety.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["walls"] == 2 and data["profiles"] == 3

        again = _guard(tmp, now, max_walls=2)
        allowed, reason = again.can_start()
        assert not allowed and "wall budget" in reason
        assert again.summary()["last_stop_reason"].startswith("safety")


def test_day_rollover_resets_counters():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now, max_walls=1)
        guard.start_run()
        guard.note_wall("authwall")
        guard.finish("wall")
        assert not guard.can_start()[0]

        now[0] += 86_400
        tomorrow = _guard(tmp, now)
        assert tomorrow.can_start()[0]
        assert tomorrow.summary()["walls"] == 0


def test_risk_and_grade_scale_with_walls():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        clean = _guard(tmp, now)
        assert clean.risk() == 0

        guard = _guard(tmp, now, max_walls=2)
        guard.start_run()
        guard.note_wall("authwall")
        guard.note_wall("authwall")
        assert guard.risk() >= 50
        assert guard.grade() in ("medium", "high", "critical")


def test_summary_shape():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1_700_000_000.0]
        guard = _guard(tmp, now)
        guard.start_run()
        summary = guard.summary()
        for key in ("enabled", "risk", "grade", "walls", "max_walls",
                    "profiles", "max_profiles", "cooldown_remaining"):
            assert key in summary, key


def test_config_has_safety_defaults():
    from config import Config

    config = Config()
    assert config.SAFE_MODE is True
    assert config.SAFETY_MAX_WALLS_PER_DAY >= 1
    assert config.SAFETY_MAX_PROFILES_PER_DAY >= 1
    assert config.SAFETY_MAX_RUNTIME_MINUTES >= 1
    assert config.SAFETY_COOLDOWN_SECONDS >= 0
    assert config.SAFETY_LEDGER_FILE.endswith("safety.json")


def test_cli_run_has_unsafe_flag():
    import cli

    args = cli.build_parser().parse_args(["run", "--unsafe", "--mode", "people"])
    assert args.unsafe is True


def test_dashboard_run_args_pass_unsafe_only_when_disabled():
    safe = build_run_args({"mode": "people", "companies": "Google"})
    assert "--unsafe" not in safe
    unsafe = build_run_args({"mode": "people", "companies": "Google",
                             "safe_mode": False})
    assert "--unsafe" in unsafe


def test_scraper_recover_respects_wall_budget():
    from config import Config
    from linkedin_scraper import LinkedInScraper

    with tempfile.TemporaryDirectory() as tmp:
        config = Config()
        config.SAFETY_MAX_WALLS_PER_DAY = 2
        config.SAFETY_LEDGER_FILE = str(Path(tmp) / "safety.json")
        scraper = LinkedInScraper(config)
        scraper.safety.start_run()
        scraper.safety.note_wall("authwall")
        scraper.safety.note_wall("authwall")
        scraper._block_reason = "authwall"
        assert asyncio.run(scraper._recover_from_block("test")) is False
        assert scraper._stop_reason.startswith("safety")


def main():
    tests = [
        test_fresh_guard_is_clean,
        test_profile_budget_stops_an_active_run,
        test_wall_budget_stops_run,
        test_wall_cooldown_counts_down,
        test_runtime_budget_stops,
        test_disabled_guard_never_stops_or_writes,
        test_ledger_persists_and_blocks_next_run,
        test_day_rollover_resets_counters,
        test_risk_and_grade_scale_with_walls,
        test_summary_shape,
        test_config_has_safety_defaults,
        test_cli_run_has_unsafe_flag,
        test_dashboard_run_args_pass_unsafe_only_when_disabled,
        test_scraper_recover_respects_wall_budget,
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
    print(f"\n{len(tests) - failed}/{len(tests)} safety tests passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
