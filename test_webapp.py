"""Behavioral tests for the local dashboard (no browser, no network beyond loopback).

Run with:  .venv/Scripts/python.exe test_webapp.py
"""

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import webapp
from webapp import (RunManager, build_run_args, build_status, export_records,
                    get_record, list_records, run_cli_json, _clean_int)


def test_build_run_args_defaults():
    args = build_run_args({"mode": "people"})
    assert args[0] == "run" and "--mode" in args and args[args.index("--mode") + 1] == "people"


def test_build_run_args_full_form():
    args = build_run_args({
        "mode": "jobs", "companies": "Google", "keywords": "data engineer",
        "titles": "", "limit": "3", "max_profiles": 7, "formats": "csv,json,exe",
        "headless": True, "dry_run": True, "fresh": True, "no_db": True,
        "challenge_timeout": 600,
    })
    joined = " ".join(args)
    assert "--companies Google" in joined
    assert "--keywords data engineer" in joined
    assert "--limit 3" in joined and "--max-profiles 7" in joined
    assert "--formats csv,json" in joined, "unknown formats must be dropped"
    for flag in ("--headless", "--dry-run", "--fresh", "--no-db"):
        assert flag in args
    assert "--challenge-timeout 600" in joined


def test_build_run_args_rejects_unknown_mode():
    try:
        build_run_args({"mode": "scrape-everything"})
    except ValueError as exc:
        assert "unknown mode" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_run_args_mode_specific_facets():
    people = " ".join(build_run_args({
        "mode": "people", "companies": "Google", "titles": "Recruiter",
        "network": "S", "seniority": "director", "current_company_ids": "1035",
        "past_company_ids": "1441", "school_ids": "17901", "industry_ids": "4",
    }))
    assert "--network S" in people and "--seniority director" in people
    assert "--current-company-ids 1035" in people
    assert "--past-company-ids 1441" in people
    assert "--school-ids 17901" in people and "--industry-ids 4" in people

    jobs = " ".join(build_run_args({
        "mode": "jobs", "keywords": "data engineer", "date_posted": "r604800",
        "experience_levels": "4", "workplace": "2", "job_types": "F,C",
        "easy_apply": True, "max_pages": 3,
    }))
    assert "--date-posted r604800" in jobs
    assert "--experience-levels 4" in jobs
    assert "--workplace 2" in jobs and "--job-types F,C" in jobs
    assert "--easy-apply" in jobs and "--max-pages 3" in jobs

    candidates = " ".join(build_run_args({
        "mode": "candidates", "skills": "Python", "title_filter": "Engineer",
        "max_candidate_pages": 2,
    }))
    assert "--skills Python" in candidates
    assert "--title-filter Engineer" in candidates
    assert "--max-candidate-pages 2" in candidates


def test_clean_int_clamps():
    assert _clean_int("12") == 12
    assert _clean_int("999999") == 100000
    assert _clean_int("nope", default=5) == 5
    assert _clean_int("-4") == 0


def test_clean_int_custom_range():
    assert _clean_int(999, 0, 0, 3600) == 999
    assert _clean_int(99999, 0, 0, 3600) == 3600


def test_status_shape_with_empty_store():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "empty.db")
        status = build_status(Path.cwd(), manager=None, db_path=db)
        assert status["ok"] is True
        assert status["records"] == 0
        assert isinstance(status["engines"], dict)
        assert "best_engine" in status["engines"]
        assert isinstance(status["proxies"], list)
        # engine preference matches what the scraper launches
        assert status["engines"]["best_engine"] == "playwright"


def test_list_records_empty_store():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "empty.db")
        payload = list_records(limit=10, db_path=db)
        assert payload["count"] == 0 and payload["total"] == 0 and payload["records"] == []


def test_get_record_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "empty.db")
        assert get_record("nobody", db_path=db) is None


def test_run_manager_idle_snapshot():
    manager = RunManager(Path.cwd())
    snapshot = manager.snapshot(tail=5)
    assert snapshot["status"] == "idle"
    assert snapshot["running"] is False
    assert snapshot["log_path"].endswith("dashboard_run.log")


def test_run_manager_stop_when_idle_is_safe():
    manager = RunManager(Path.cwd())
    snapshot = manager.stop()
    assert snapshot["running"] is False


def test_ui_file_has_the_doors():
    html = (Path.cwd() / "dashboard_ui.html").read_text(encoding="utf-8")
    for marker in ('data-view="overview"', 'data-view="run"', 'data-view="records"',
                   'data-view="armor"', 'data-view="logs"',
                   'id="recordsTable"', 'id="btnAudit"', 'id="btnProxy"',
                   'id="console"', 'id="gauge"'):
        assert marker in html, marker
    # Three distinct run panels, not one generic form.
    for mode in ("people", "jobs", "candidates"):
        assert f'data-mode="{mode}"' in html
        assert f'data-panel="{mode}"' in html
    for field in ('id="pCompanies"', 'id="jKeywords"', 'id="cSkills"',
                  'id="pNetwork"', 'id="jDate"', 'id="cTitle"'):
        assert field in html, field


def test_run_cli_json_tolerates_non_json_output():
    result = run_cli_json(["--version"], timeout=30)
    assert isinstance(result, dict)
    assert result.get("exit_code") == 0


def test_cli_exposes_dashboard_command():
    import cli
    parser = cli.build_parser()
    args = parser.parse_args(["dashboard", "--port", "9123", "--open"])
    assert args.command == "dashboard" and args.port == 9123 and args.open


def test_main_defaults_to_dashboard_menu_is_optin():
    """Running the project with no arguments must open the dashboard."""
    import main
    parser = main.build_parser()
    default = parser.parse_args([])
    assert default.menu is False, "dashboard is the default entry now"
    args = parser.parse_args(["--menu", "--port", "9123", "--no-open"])
    assert args.menu is True and args.port == 9123 and args.no_open is True


def _with_server(fn):
    server = webapp.DashboardServer(("127.0.0.1", 0), webapp.Handler, root=Path.cwd())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fn(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_health_and_status():
    def check(port):
        base = f"http://127.0.0.1:{port}"
        health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=5).read())
        assert health["ok"] is True
        status = json.loads(urllib.request.urlopen(base + "/api/status", timeout=10).read())
        assert status["ok"] is True
        assert isinstance(status["records"], int)
        assert "engines" in status

    _with_server(check)


def test_http_index_serves_the_ui():
    def check(port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            body = response.read().decode("utf-8")
            assert response.status == 200
            assert "Rack" in body and "recordsTable" in body

    _with_server(check)


def test_http_download_rejects_traversal():
    def check(port):
        url = f"http://127.0.0.1:{port}/api/download?path=" + urllib.parse.quote("../../Windows/win.ini")
        try:
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("path traversal must be rejected")

    _with_server(check)


def test_http_records_endpoint():
    def check(port):
        body = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/records?limit=2", timeout=10).read())
        assert body["ok"] is True and "records" in body
        assert len(body["records"]) <= 2

    _with_server(check)


def test_http_proxy_scan_counts_as_a_scan_even_with_no_healthy_proxy():
    def check(port):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/proxy-scan",
            data=json.dumps({"proxies": "http://127.0.0.1:9"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        body = json.loads(urllib.request.urlopen(request, timeout=90).read())
        assert body["ok"] is True, "a completed scan must not read as an API failure"
        assert isinstance(body["result"].get("results"), list)

    _with_server(check)


def test_export_empty_store_errors():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "empty.db")
        try:
            export_records({"db": db, "out": str(Path(tmp) / "out.xlsx")})
        except ValueError as exc:
            assert "no records" in str(exc)
        else:
            raise AssertionError("expected ValueError for an empty store")


def main():
    tests = [
        test_build_run_args_defaults,
        test_build_run_args_full_form,
        test_build_run_args_rejects_unknown_mode,
        test_build_run_args_mode_specific_facets,
        test_clean_int_clamps,
        test_clean_int_custom_range,
        test_status_shape_with_empty_store,
        test_list_records_empty_store,
        test_get_record_missing_returns_none,
        test_run_manager_idle_snapshot,
        test_run_manager_stop_when_idle_is_safe,
        test_ui_file_has_the_doors,
        test_run_cli_json_tolerates_non_json_output,
        test_cli_exposes_dashboard_command,
        test_main_defaults_to_dashboard_menu_is_optin,
        test_http_health_and_status,
        test_http_index_serves_the_ui,
        test_http_download_rejects_traversal,
        test_http_records_endpoint,
        test_http_proxy_scan_counts_as_a_scan_even_with_no_healthy_proxy,
        test_export_empty_store_errors,
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
    print(f"\n{len(tests) - failed}/{len(tests)} dashboard tests passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
