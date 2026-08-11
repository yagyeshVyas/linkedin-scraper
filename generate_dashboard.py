"""
Generate dashboard.html — a self-contained preview page for the LinkedIn scraper project.
=========================================================================================
Usage:
    python generate_dashboard.py

Fills in `dashboard_template.html` (the UI source of truth) with live data:
    - output/progress.json   → scraped profile records (stats + searchable table)
    - output/scraper.log     → last 40 lines (colorized activity feed)
    - output/session_health.json → latest run's telemetry (Session Health panel)
    - *.py modules           → in-process ast syntax check (Project Health table)

Static content (labels, known issues, styling) lives in the template; this script
only injects dynamic data. Stdlib only — no installs, no disk writes beyond
`dashboard.html` (ast.parse instead of py_compile so no .pyc files are created).
"""

import ast
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent

MAIN_MODULES = [
    "config.py", "main.py", "main_proxy.py", "linkedin_scraper.py",
    "linkedin_scraper_proxy.py", "proxy_manager.py", "utils.py", "resume_parser.py",
]


# ─────────────────────────────────────────────
#  DATA LOADING
# ─────────────────────────────────────────────

def load_records() -> list:
    """Load scraped results from output/progress.json ([] if missing or malformed)."""
    path = ROOT / "output" / "progress.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        results = data.get("results") if isinstance(data, dict) else None
    except Exception:
        return []
    return results if isinstance(results, list) else []


def load_session_health() -> dict:
    """Load the latest run's session-health telemetry ({} if none yet)."""
    path = ROOT / "output" / "session_health.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        last = data.get("last")
        return last if isinstance(last, dict) else {}
    except Exception:
        return {}


def load_log_tail() -> str:
    """Return the tail of output/scraper.log."""
    path = ROOT / "output" / "scraper.log"
    if not path.exists():
        return "(no scraper.log yet - run the scraper first)"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"(could not read scraper.log: {exc})"
    return "\n".join(lines[-40:])


def check_syntax() -> list:
    """Parse MAIN_MODULES in-process: [{"file", "ok", "detail"}...].
    ast.parse never writes .pyc files, so running the generator is side-effect free.
    """
    results = []
    for name in MAIN_MODULES:
        path = ROOT / name
        if not path.exists():
            results.append({"file": name, "ok": False, "detail": "missing"})
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=name)
            results.append({"file": name, "ok": True, "detail": ""})
        except (SyntaxError, ValueError) as exc:
            results.append({"file": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:220]})
    return results


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    records = load_records()
    checks = check_syntax()
    template = (ROOT / "dashboard_template.html").read_text(encoding="utf-8")

    fill = {
        "__RECORDS_JSON__": json.dumps(records, ensure_ascii=False),
        "__LOG_JSON__": json.dumps(load_log_tail(), ensure_ascii=False),
        "__HEALTH_JSON__": json.dumps(checks, ensure_ascii=False),
        "__SESSION_HEALTH_JSON__": json.dumps(load_session_health(), ensure_ascii=False),
        "__GEN_AT__": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for key, value in fill.items():
        if key not in template:
            raise RuntimeError(f"Placeholder {key} not found in dashboard_template.html")
        template = template.replace(key, value)

    out = ROOT / "dashboard.html"
    out.write_text(template, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size / 1024:.1f} KB) with {len(records)} records, "
          f"{sum(1 for c in checks if not c['ok'])} broken module(s)")


if __name__ == "__main__":
    main()
