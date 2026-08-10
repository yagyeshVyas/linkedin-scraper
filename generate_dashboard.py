"""
Generate dashboard.html — a self-contained preview page for the LinkedIn scraper project.
=========================================================================================
Usage:
    python generate_dashboard.py

Fills in `dashboard_template.html` (the UI source of truth) with live data:
    - output/progress.json   → scraped profile records (stats + searchable table)
    - output/scraper.log     → last 40 lines (colorized activity feed)
    - *.py modules           → in-process ast syntax check (Project Health table)
    - config.py              → declared job titles / location (credentials never read)

Static content (labels, known issues, styling) lives in the template; this script
only injects dynamic data. Stdlib only — no installs, no disk writes beyond
`dashboard.html` (ast.parse instead of py_compile so no .pyc files are created).
"""

import ast
import json
import re
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
    except Exception:
        return []
    results = data.get("results")
    return results if isinstance(results, list) else []


def load_log_tail(max_lines: int = 40) -> str:
    """Return the tail of output/scraper.log."""
    path = ROOT / "output" / "scraper.log"
    if not path.exists():
        return "(no scraper.log yet - run the scraper first)"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"(could not read scraper.log: {exc})"
    return "\n".join(lines[-max_lines:])


def check_syntax(files) -> list:
    """Parse the given .py files in-process: [{"file", "ok", "detail"}...].
    ast.parse never writes .pyc files, so running the generator is side-effect free.
    """
    results = []
    for name in files:
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


def extract_config_value(text: str, key: str) -> str:
    """Extract the declared value of a config.py field, as a comma-joined string.

    Handles typed fields (`KEY: List[str] = ...`), plain lists, and
    `field(default_factory=lambda: [...])` wrappers; skips commented-out entries.
    Returns "" when the key is absent. Credentials are never read (keys are
    whitelisted by the caller).
    """
    m = re.search(rf"^\s*{re.escape(key)}\s*(?::[^=\n]*)?=(.*)$", text, re.MULTILINE)
    if not m:
        return ""

    value = m.group(1).strip()

    # String value: `KEY: str = "..."` (a trailing comment is ignored).
    if value.startswith('"'):
        m2 = re.match(r'"([^"]*)"', value)
        return m2.group(1) if m2 else ""

    # List value: `KEY = [...]` or `KEY = field(default_factory=lambda: [...])`.
    open_idx = text.find("[", m.start(1))
    if open_idx != -1:
        end = text.find("]", open_idx)
        if end != -1:
            block = text[open_idx:end + 1]
            items = [
                item for line in block.splitlines()
                if not line.strip().startswith("#")
                for item in re.findall(r'"([^"]*)"', line)
            ]
            return ", ".join(items)
    return ""


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    records = load_records()
    log_tail = load_log_tail()
    checks = check_syntax(MAIN_MODULES)

    # Config snapshot (best-effort; only whitelisted keys, never credentials).
    try:
        cfg_text = (ROOT / "config.py").read_text(encoding="utf-8", errors="replace")
        cfg_json = {
            "job_titles": extract_config_value(cfg_text, "JOB_TITLES"),
            "location": extract_config_value(cfg_text, "SEARCH_LOCATION_NAME"),
            "geo": extract_config_value(cfg_text, "GEO_URN"),
        }
    except Exception:
        cfg_json = {"job_titles": "", "location": "", "geo": ""}

    template = (ROOT / "dashboard_template.html").read_text(encoding="utf-8")

    fill = {
        "__RECORDS_JSON__": json.dumps(records, ensure_ascii=False),
        "__LOG_JSON__": json.dumps(log_tail, ensure_ascii=False),
        "__HEALTH_JSON__": json.dumps(checks, ensure_ascii=False),
        "__CFG_JSON__": json.dumps(cfg_json, ensure_ascii=False),
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
