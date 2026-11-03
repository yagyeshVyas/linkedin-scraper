"""Automation CLI for the LinkedIn scraper power stack.

``main.py`` keeps the friendly interactive menu.  This module is the
scriptable surface: one command per job, explicit flags, non-zero exit codes,
and no prompt that can block a cron job.

    python cli.py run --mode people --companies Google,Microsoft --titles Recruiter
    python cli.py run --mode jobs --keywords "data engineer" --formats csv,json,html
    python cli.py export --format all --out output/linkedin_results.xlsx
    python cli.py stats --json
    python cli.py report --out output/report.html
    python cli.py mcp --db output/scraper.db
    python cli.py doctor
    python cli.py db prune --days 90

Every command exits 0 on success and 1 on a real failure, so it composes with
CI and schedulers.  Output is plain text (plus --json where useful) and stays
ASCII-safe for the Windows console.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

DEFAULT_EXPORT_BASE = "output/linkedin_results.xlsx"
VERSION = "5.0.0"


# ─────────────────────────────────────────────────────────────
#  Parser
# ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-scraper",
        description="LinkedIn scraper power stack - scraping, enrichment, storage, exports, MCP.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="scrape LinkedIn (people / jobs / candidates)")
    run.add_argument("--mode", choices=["people", "jobs", "candidates"], default=None)
    run.add_argument("--companies", default="", help="comma-separated company list")
    run.add_argument("--companies-file", default="", help="file with one company per line")
    run.add_argument("--titles", default="", help="comma-separated job titles (people mode)")
    run.add_argument("--skills", default="", help="comma-separated skills (candidates mode)")
    run.add_argument("--keywords", default="", help="comma-separated keywords (jobs mode)")
    run.add_argument("--limit", type=int, default=0,
                     help="process at most N companies/keywords/skills (0 = all)")
    run.add_argument("--max-profiles", type=int, default=0,
                     help="stop after N profiles this run (0 = no cap)")
    run.add_argument("--dry-run", action="store_true",
                     help="launch, log in, verify one search, save nothing")
    run.add_argument("--fresh", action="store_true",
                     help="ignore completed searches and re-run them")
    run.add_argument("--no-db", action="store_true", help="disable the SQLite store")
    run.add_argument("--db", default="", help="SQLite path (default: config.DATABASE_FILE)")
    run.add_argument("--headless", action="store_true", help="run Chromium headless")
    run.add_argument("--formats", default="",
                     help="export formats: xlsx,csv,json,jsonl,md,html (default: config)")
    run.add_argument("--no-export", action="store_true", help="skip exporting at the end")
    run.add_argument("--resume", default="", help="resume file for match scoring")
    run.add_argument("--min-score", type=float, default=None,
                     help="drop profiles below this resume match score")
    run.add_argument("--network", default="", help="people search: F,S,O (1st/2nd/3rd+)")
    run.add_argument("--location", default="", help="country name or geo:<id>")
    run.add_argument("--seniority", default="", help="people search seniority: mid_senior,director,...")
    run.add_argument("--current-company-ids", default="", help="numeric LinkedIn company IDs")
    run.add_argument("--past-company-ids", default="", help="numeric LinkedIn company IDs")
    run.add_argument("--school-ids", default="", help="numeric LinkedIn school IDs")
    run.add_argument("--industry-ids", default="", help="numeric LinkedIn industry IDs")

    export = sub.add_parser("export", help="export stored records to one or more formats")
    export.add_argument("--format", default="all",
                        help="all, or a comma list of xlsx,csv,json,jsonl,md,html")
    export.add_argument("--out", default=DEFAULT_EXPORT_BASE, help="output base path")
    export.add_argument("--db", default="", help="SQLite path")
    export.add_argument("--source", choices=["auto", "db", "progress"], default="auto")
    export.add_argument("--company", default="")
    export.add_argument("--query", default="")
    export.add_argument("--min-score", type=float, default=0.0)
    export.add_argument("--limit", type=int, default=0)
    export.add_argument("--deep", action="store_true", help="include every scraped field in CSV")

    stats = sub.add_parser("stats", help="print pool statistics")
    stats.add_argument("--db", default="")
    stats.add_argument("--json", action="store_true")

    report = sub.add_parser("report", help="write the self-contained HTML report")
    report.add_argument("--out", default="output/report.html")
    report.add_argument("--db", default="")
    report.add_argument("--limit", type=int, default=500)
    report.add_argument("--title", default="LinkedIn Scrape Report")

    mcp = sub.add_parser("mcp", help="run the MCP server on stdio")
    mcp.add_argument("--db", default="")
    mcp.add_argument("--name", default="linkedin-scraper")

    doctor = sub.add_parser("doctor", help="check environment, credentials and data")
    doctor.add_argument("--json", action="store_true")

    db = sub.add_parser("db", help="database maintenance")
    db.add_argument("--db", default="")
    db_sub = db.add_subparsers(dest="db_command")
    imp = db_sub.add_parser("import", help="import output/progress.json into the database")
    imp.add_argument("--progress", default="output/progress.json")
    prune = db_sub.add_parser("prune", help="delete records not seen in N days")
    prune.add_argument("--days", type=int, default=90)
    backup = db_sub.add_parser("backup", help="copy the database")
    backup.add_argument("--to", default="output/backup/scraper.db")
    query = db_sub.add_parser("query", help="run a read-only SQL query")
    query.add_argument("sql", nargs="?", default="SELECT COUNT(*) AS records FROM records")

    return parser


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

def _split_csv(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _cmd_list(value: str, fallback: List[str]) -> List[str]:
    items = _split_csv(value)
    return items or list(fallback or [])


def _db_path(args, config=None) -> str:
    if getattr(args, "db", ""):
        return args.db
    if config is not None and getattr(config, "DATABASE_FILE", ""):
        return config.DATABASE_FILE
    return "output/scraper.db"


def _open_store(path: str, quiet: bool = False):
    from storage import open_store
    store = open_store(path)
    if store is None and not quiet:
        print(f"[warn] could not open database at {path}")
    return store


def _records_from_source(store, source: str = "auto",
                         progress: str = "output/progress.json") -> List[dict]:
    """Load records from the DB, falling back to progress.json."""
    if store is not None and source in ("auto", "db"):
        records = store.load_records(limit=None)
        if records or source == "db":
            return records
    path = Path(progress)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results = data.get("results") if isinstance(data, dict) else None
            if isinstance(results, list):
                return [record for record in results if isinstance(record, dict)]
        except Exception as exc:
            print(f"[warn] could not read {path}: {exc}")
    return []


# ─────────────────────────────────────────────────────────────
#  Commands
# ─────────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    from config import Config
    from linkedin_scraper import LinkedInScraper

    config = Config()
    if args.mode:
        config.SEARCH_MODE = args.mode
    if args.headless:
        config.HEADLESS = True
    if args.db:
        config.DATABASE_FILE = args.db
    if args.no_db:
        config.ENABLE_DATABASE = False
    if args.fresh:
        config.RESUME_COMPLETED_SEARCHES = False
    if args.formats:
        config.EXPORT_FORMATS = _split_csv(args.formats)
    if args.no_export:
        config.EXPORT_FORMATS = []
    if args.resume:
        config.RESUME_FILE_PATH = args.resume
    if args.min_score is not None:
        config.MATCH_MIN_SCORE = float(args.min_score)

    # Search filters (people mode) - empty flags keep the config values.
    for attr, value in (
        ("NETWORK_FILTER", args.network),
        ("SENIORITY_FILTER", args.seniority),
        ("CURRENT_COMPANY_FILTER", args.current_company_ids),
        ("PAST_COMPANY_FILTER", args.past_company_ids),
        ("SCHOOL_FILTER", args.school_ids),
        ("INDUSTRY_FILTER", args.industry_ids),
    ):
        items = _split_csv(value)
        if items:
            setattr(config, attr, items)
    if args.location:
        from search_filters import resolve_geo
        geo_ids, warning = resolve_geo(args.location)
        if geo_ids:
            config.GEO_URN = geo_ids[0]
            config.SEARCH_LOCATION_NAME = args.location.strip()
        elif warning:
            print(f"[warn] {warning}")

    mode = config.SEARCH_MODE.lower()
    titles = _cmd_list(args.titles, config.JOB_TITLES)
    if args.titles:
        config.JOB_TITLES = titles

    if mode == "jobs":
        targets = _cmd_list(args.keywords, config.JOB_SEARCH_KEYWORDS)
    elif mode == "candidates":
        targets = _cmd_list(args.skills, config.CANDIDATE_SKILLS)
    else:
        if args.companies_file:
            companies = [line.strip() for line in Path(args.companies_file).read_text(
                encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
        else:
            companies = _split_csv(args.companies)
        if not companies:
            from main import FORTUNE_500_COMPANIES
            companies = list(FORTUNE_500_COMPANIES)
        targets = list(dict.fromkeys(companies))

    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    if not targets:
        print("[error] nothing to search - provide --companies/--keywords/--skills")
        return 2

    if args.max_profiles:
        config.MAX_PROFILES_PER_RUN = int(args.max_profiles)

    print(f"[run] mode={mode} targets={len(targets)} titles={len(titles)} "
          f"db={'off' if not config.ENABLE_DATABASE else config.DATABASE_FILE}")
    print(f"[run] location={config.SEARCH_LOCATION_NAME} formats={config.EXPORT_FORMATS or 'none'}")

    scraper = LinkedInScraper(config)
    try:
        if args.dry_run:
            ok = asyncio.run(scraper.dry_run(targets))
            print("[dry-run] OK" if ok else "[dry-run] FAILED")
            return 0 if ok else 1
        summary = asyncio.run(scraper.run(targets))
        print(f"[run] finished: {json.dumps(summary, default=str) if summary else 'no summary'}")
        return 0
    except KeyboardInterrupt:
        print("\n[run] interrupted - progress is saved")
        return 130


def cmd_export(args) -> int:
    from exporters import export_all

    store = _open_store(_db_path(args), quiet=True)
    try:
        records = _records_from_source(store, args.source)
        if args.company:
            needle = args.company.lower()
            records = [r for r in records
                       if needle in str(r.get("company") or r.get("search_company") or "").lower()]
        if args.query:
            needle = args.query.lower()
            records = [r for r in records
                       if needle in json.dumps(r, ensure_ascii=False).lower()]
        if args.min_score:
            records = [r for r in records
                       if float(r.get("match_score") or 0) >= float(args.min_score)]
        if args.limit:
            records = records[: args.limit]
        if not records:
            print("[export] no records found (run a scrape first)")
            return 1

        formats = (["xlsx", "csv", "json", "jsonl", "md", "html"]
                   if args.format.strip().lower() == "all" else _split_csv(args.format))
        meta = {"title": "LinkedIn Export", "count": len(records)}
        written = export_all(records, args.out, formats=formats, meta=meta,
                             mode=_infer_mode(records))
        for fmt, path in written.items():
            print(f"[export] {fmt:>14} -> {path}")
        return 0
    finally:
        if store is not None:
            store.close()


def _infer_mode(records: List[dict]) -> str:
    from exporters import infer_mode
    return infer_mode(records)


def cmd_stats(args) -> int:
    path = _db_path(args)
    if not Path(path).exists() or path == ":memory:":
        print(f"[stats] no database at {path} yet - run a scrape or "
              f"`python cli.py db import --progress output/progress.json`")
        return 1
    store = _open_store(path, quiet=True)
    if store is None:
        print(f"[stats] could not open {path}")
        return 1
    try:
        stats = store.stats()
    finally:
        store.close()

    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0

    print(f"Records            : {stats['total_records']}")
    print(f"With email         : {stats['with_email']}")
    print(f"With phone         : {stats['with_phone']}")
    print(f"Open to work       : {stats['open_to_work']}")
    print(f"Avg match score    : {stats['avg_match_score']}")
    print(f"Avg completeness   : {stats['avg_completeness']}")
    print(f"Runs recorded      : {stats['runs']}")
    if stats["top_companies"]:
        print("\nTop companies:")
        for row in stats["top_companies"]:
            print(f"  {row['count']:>4}  {row['company']}")
    if stats["seniority"]:
        print("\nSeniority:")
        for row in stats["seniority"]:
            print(f"  {row['count']:>4}  {row['seniority']}")
    if stats["top_countries"]:
        print("\nCountries:")
        for row in stats["top_countries"]:
            print(f"  {row['count']:>4}  {row['country']}")
    return 0


def cmd_report(args) -> int:
    from exporters import export_html_report

    store = _open_store(_db_path(args), quiet=True)
    try:
        records = _records_from_source(store)
    finally:
        if store is not None:
            store.close()
    if not records:
        print("[report] no records found")
        return 1
    records = records[: args.limit]
    path = export_html_report(records, args.out, meta={
        "title": args.title,
        "mode": _infer_mode(records),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    print(f"[report] {len(records)} records -> {path}")
    return 0


def cmd_mcp(args) -> int:
    from mcp_server import main as mcp_main
    return mcp_main(["--db", _db_path(args), "--name", args.name])


def cmd_db(args) -> int:
    from storage import Store

    path = _db_path(args)
    if not getattr(args, "db_command", None):
        print("usage: cli.py db [--db PATH] {import,prune,backup,query} ...")
        return 2

    store = Store(path)
    try:
        if args.db_command == "import":
            run_id = store.start_run("import", {"source": args.progress})
            count = store.import_progress(args.progress, run_id=run_id)
            store.finish_run(run_id, {"imported": count})
            print(f"[db] imported {count} record(s) from {args.progress} -> {path}")
            return 0
        if args.db_command == "prune":
            removed = store.prune(days=args.days)
            print(f"[db] pruned {removed} record(s) older than {args.days} days")
            return 0
        if args.db_command == "backup":
            destination = store.backup(args.to)
            print(f"[db] backup written -> {destination}")
            return 0
        if args.db_command == "query":
            sql = args.sql.strip()
            if not sql.lower().startswith(("select", "pragma", "with")):
                print("[db] only read-only queries (SELECT/PRAGMA/WITH) are allowed")
                return 2
            rows = store.conn.execute(sql).fetchmany(200)
            for row in rows:
                print(dict(row))
            print(f"[db] {len(rows)} row(s)")
            return 0
    finally:
        store.close()
    return 2


# ─────────────────────────────────────────────────────────────
#  Doctor
# ─────────────────────────────────────────────────────────────

def _check(name: str, ok: bool, detail: str, level: str = "fail") -> dict:
    return {"name": name, "ok": ok, "detail": detail,
            "level": "ok" if ok else level}


def _expected_chromium_revision() -> str:
    """The chromium build the installed Playwright driver will actually look up."""
    try:
        import playwright
        manifest = (Path(playwright.__file__).parent / "driver" / "package"
                    / "browsers.json")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in data.get("browsers", []):
            if entry.get("name") == "chromium":
                return str(entry.get("revision") or "")
    except Exception:
        return ""
    return ""


def run_doctor(db_path: str = "output/scraper.db") -> List[dict]:
    checks: List[dict] = []

    checks.append(_check("python", sys.version_info >= (3, 9),
                         f"{sys.version.split()[0]} (need 3.9+)"))

    for module in ("playwright", "bs4", "pandas", "openpyxl", "rich"):
        try:
            __import__(module)
            checks.append(_check(f"dep:{module}", True, "installed"))
        except Exception as exc:
            checks.append(_check(f"dep:{module}", False, f"missing ({exc.__class__.__name__})"))

    browser_roots = []
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        browser_roots.append(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    if os.environ.get("LOCALAPPDATA"):
        browser_roots.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    browser_roots.append(Path.home() / ".cache" / "ms-playwright")

    # Check the *exact* build this Playwright release expects. An older/newer
    # chromium-* directory in the cache is not enough: the driver looks up its
    # own revision and fails at launch with "Executable doesn't exist".
    expected = _expected_chromium_revision()
    installed: List[str] = []
    for root in browser_roots:
        try:
            installed.extend(item.name for item in root.iterdir()
                             if item.name.startswith(("chromium-", "chromium_headless_shell-")))
        except OSError:
            continue
    if expected:
        has_expected = (f"chromium-{expected}" in installed
                        or f"chromium_headless_shell-{expected}" in installed)
        if has_expected:
            checks.append(_check("playwright:chromium", True,
                                 f"build {expected} present"))
        else:
            checks.append(_check(
                "playwright:chromium", False,
                f"playwright expects build {expected}; installed: "
                f"{', '.join(sorted(set(installed))) or 'none'} - run: "
                f"playwright install chromium"))
    else:
        checks.append(_check("playwright:chromium", bool(installed),
                             f"{len(installed)} browser build(s) found" if installed
                             else "missing - run: playwright install chromium"))

    creds = bool(os.environ.get("LINKEDIN_EMAIL") and os.environ.get("LINKEDIN_PASSWORD"))
    checks.append(_check("credentials:env", creds,
                         "LINKEDIN_EMAIL/LINKEDIN_PASSWORD set" if creds
                         else "not set - config.py defaults will be used (avoid committing them)",
                         level="warn"))

    session_dir = Path("session")
    sessions = list(session_dir.glob("session_*.json")) if session_dir.exists() else []
    checks.append(_check("session", bool(sessions),
                         f"{len(sessions)} saved session(s)" if sessions
                         else "no saved session - first login will type credentials",
                         level="warn"))

    try:
        from config import Config
        config = Config()
        checks.append(_check("config", True, f"mode={config.SEARCH_MODE}, "
                                             f"titles={len(config.JOB_TITLES)}"))
    except Exception as exc:
        checks.append(_check("config", False, f"{exc.__class__.__name__}: {exc}"))

    if not Path(db_path).exists():
        checks.append(_check("database", False,
                             f"{db_path} not created yet (first run will create it)",
                             level="warn"))
    else:
        try:
            from storage import Store
            store = Store(db_path)
            try:
                total = store.count()
                runs = len(store.load_runs(limit=1))
            finally:
                store.close()
            checks.append(_check("database", True, f"{db_path} ({total} records, {runs} run(s))"))
        except Exception as exc:
            checks.append(_check("database", False, f"{db_path} unreadable ({exc})", level="warn"))

    for target in ("output", "session"):
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
            probe = Path(target) / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append(_check(f"writable:{target}", True, "ok"))
        except Exception as exc:
            checks.append(_check(f"writable:{target}", False, str(exc)))

    return checks


def cmd_doctor(args) -> int:
    checks = run_doctor(_db_path(args))
    failures = [check for check in checks if not check["ok"] and check["level"] == "fail"]
    if args.json:
        print(json.dumps({"checks": checks, "failures": len(failures)}, indent=2))
    else:
        print(f"Environment check ({len(checks)} checks)")
        print("-" * 64)
        for check in checks:
            status = "PASS" if check["ok"] else ("FAIL" if check["level"] == "fail" else "WARN")
            print(f"[{status:4}] {check['name']:<22} {check['detail']}")
        print("-" * 64)
        print(f"{len(failures)} failure(s), "
              f"{sum(1 for c in checks if not c['ok'] and c['level'] == 'warn')} warning(s)")
    return 0 if not failures else 1


# ─────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    dispatch = {
        "run": cmd_run,
        "export": cmd_export,
        "stats": cmd_stats,
        "report": cmd_report,
        "mcp": cmd_mcp,
        "doctor": cmd_doctor,
        "db": cmd_db,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
