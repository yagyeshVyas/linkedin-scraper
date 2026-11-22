"""One local dashboard for everything: scrape, inspect, audit, export.

Run it:

    python cli.py dashboard --open
    python webapp.py --port 8787 --open

Backend: standard library only (``http.server`` + ``ThreadingHTTPServer``).
Frontend: ``dashboard_ui.html`` served at ``/`` — no build step, no CDN.

Endpoints (JSON unless noted):

    GET  /                      the dashboard page
    GET  /api/status            pool, runs, telemetry, engines, proxies, live run
    GET  /api/records           filtered record summaries
    GET  /api/record?id=...     one full record
    GET  /api/runs              recent runs
    GET  /api/proxies           proxy scoreboard
    GET  /api/run/status        live run state + log tail
    POST /api/run               start a scrape (cli.py run ...)
    POST /api/run/stop          stop the run tree
    POST /api/stealth           run cli.py stealth-check --json
    POST /api/proxy-scan        run cli.py proxy-scan --json
    POST /api/export            export stored records
    GET  /api/download?path=    download a file under the repo

Bound to 127.0.0.1 by default; nothing is exposed to the network.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
UI_FILE = ROOT / "dashboard_ui.html"
DEFAULT_DB = "output/scraper.db"
DEFAULT_LOG = "output/dashboard_run.log"
RUN_MODES = ("people", "jobs", "candidates")
EXPORT_FORMATS = ("xlsx", "csv", "json", "jsonl", "md", "html")

logger = logging.getLogger("dashboard")


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _infer_mode(records: List[dict]) -> str:
    if records and any(record.get("search_mode") == "jobs" for record in records):
        return "jobs"
    return "people"


def _store(db_path: str = ""):
    from storage import Store

    path = db_path or os.environ.get("DASHBOARD_DB") or DEFAULT_DB
    return Store(path)


def _clean_int(value: Any, default: int = 0, minimum: int = 0, maximum: int = 100000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


# ─────────────────────────────────────────────────────────────
#  Run manager (one scrape at a time)
# ─────────────────────────────────────────────────────────────

class RunManager:
    """Owns the single scrape subprocess and its log ring."""

    def __init__(self, root: Path = ROOT, python: str = ""):
        self.root = Path(root)
        self.python = python or sys.executable
        self.lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None
        self.status = "idle"            # idle | running | done | failed | stopped
        self.exit_code: Optional[int] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.command: List[str] = []
        self.log_path = self.root / DEFAULT_LOG
        self._lines: deque = deque(maxlen=5000)

    # ── process control ──

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: List[str]) -> Dict[str, Any]:
        with self.lock:
            if self.running():
                raise RuntimeError("a run is already in progress")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            command = [self.python, str(self.root / "cli.py")] + list(args)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                self.proc = subprocess.Popen(
                    command, cwd=str(self.root), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, creationflags=flags,
                )
            except OSError as exc:
                self.status = "failed"
                self.exit_code = -1
                self._lines.append(f"[dashboard] failed to start: {exc}")
                raise RuntimeError(f"could not start run: {exc}") from exc
            self.status = "running"
            self.exit_code = None
            self.started_at = _now()
            self.finished_at = None
            self.command = command
            self._lines.clear()
            self._lines.append(f"[dashboard] {self.started_at} starting: cli.py {' '.join(args)}")
            threading.Thread(target=self._pump, args=(self.proc,),
                             name="dashboard-run", daemon=True).start()
            return self.snapshot(tail=40)

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
            with self.log_path.open("w", encoding="utf-8") as handle:
                assert proc.stdout is not None
                for line in proc.stdout:
                    text = line.rstrip("\r\n")
                    self._lines.append(text)
                    handle.write(line)
                    handle.flush()
            code = proc.wait()
        except Exception as exc:  # never leave the manager stuck "running"
            self._lines.append(f"[dashboard] stream error: {exc}")
            code = proc.wait() if proc.poll() is None else (proc.returncode or -1)
        with self.lock:
            self.exit_code = int(code)
            self.finished_at = _now()
            if self.status == "stopped":
                pass
            elif code == 0:
                self.status = "done"
                self._lines.append(f"[dashboard] finished successfully at {self.finished_at}")
            else:
                self.status = "failed"
                self._lines.append(f"[dashboard] exited {code} at {self.finished_at}")

    def stop(self) -> Dict[str, Any]:
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                self.status = "stopped" if self.status == "running" else self.status
                return self.snapshot(tail=20)
            self.status = "stopped"
            pid = proc.pid
        try:
            if os.name == "nt":
                # Scoped to our own process tree — never image-name kills.
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=20)
            else:
                proc.terminate()
        except Exception as exc:
            self._lines.append(f"[dashboard] stop failed: {exc}")
        try:
            proc.wait(timeout=15)
        except Exception:
            pass
        with self.lock:
            self.exit_code = proc.returncode
            self.finished_at = _now()
            self._lines.append(f"[dashboard] stopped at {self.finished_at}")
        return self.snapshot(tail=40)

    # ── read ──

    def snapshot(self, tail: int = 120) -> Dict[str, Any]:
        lines = list(self._lines)
        return {
            "status": self.status,
            "running": self.running(),
            "pid": self.proc.pid if self.proc is not None and self.running() else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "command": " ".join(self.command),
            "log_path": str(self.log_path.relative_to(self.root))
            if self.log_path.is_relative_to(self.root) else str(self.log_path),
            "lines": lines[-max(1, int(tail)):] if tail else [],
            "line_count": len(lines),
        }


# ─────────────────────────────────────────────────────────────
#  Read-only data
# ─────────────────────────────────────────────────────────────

_STATUS_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}


def build_status(root: Path = ROOT, manager: Optional[RunManager] = None,
                 db_path: str = "") -> Dict[str, Any]:
    now = time.time()
    if _STATUS_CACHE["payload"] is not None and now - _STATUS_CACHE["at"] < 4.0:
        payload = dict(_STATUS_CACHE["payload"])
        payload["run"] = manager.snapshot(tail=0) if manager else payload.get("run")
        return payload

    payload: Dict[str, Any] = {
        "ok": True,
        "time": _now(),
        "records": 0,
        "stats": {},
        "runs": [],
        "telemetry": {},
        "engines": {},
        "proxies": [],
        "db": {},
    }
    store = None
    try:
        from mcp_server import summarize_profile
        store = _store(db_path)
        payload["records"] = store.count()
        payload["stats"] = store.stats()
        payload["runs"] = store.load_runs(limit=5)
        recent = store.load_records(limit=6, order_by="last_seen")
        payload["recent"] = [summarize_profile(record) for record in recent]
    except Exception as exc:
        payload["db"] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass

    telemetry = _read_json(root / "output" / "session_health.json", {})
    payload["telemetry"] = telemetry if isinstance(telemetry, dict) else {}

    try:
        from cf_bypass import capability_report
        payload["engines"] = capability_report()
    except Exception:
        payload["engines"] = {}

    try:
        from proxy_intel import ProxyIntel
        payload["proxies"] = ProxyIntel(root / "output" / "proxy_intel.json").summary()
    except Exception:
        payload["proxies"] = []

    try:
        from safety import read_summary
        payload["safety"] = read_summary(str(root / "output" / "safety.json"))
    except Exception:
        payload["safety"] = {}

    payload["run"] = manager.snapshot(tail=0) if manager else {}
    payload["files"] = {
        "db": str(root / DEFAULT_DB),
        "log": str(root / DEFAULT_LOG),
    }
    _STATUS_CACHE.update({"at": now, "payload": payload})
    return payload


def list_records(query: str = "", limit: int = 50, db_path: str = "") -> Dict[str, Any]:
    from mcp_server import summarize_profile

    store = _store(db_path)
    try:
        records = store.load_records(limit=_clean_int(limit, 50, 1, 500),
                                     query=query, order_by="last_seen")
        return {"count": len(records), "total": store.count(),
                "records": [summarize_profile(record) for record in records]}
    finally:
        store.close()


def get_record(key: str, db_path: str = "") -> Optional[dict]:
    store = _store(db_path)
    try:
        return store.get_record(key)
    finally:
        store.close()


# ─────────────────────────────────────────────────────────────
#  Actions
# ─────────────────────────────────────────────────────────────

def build_run_args(payload: Dict[str, Any]) -> List[str]:
    """Translate the dashboard form into safe ``cli.py run`` arguments."""
    args: List[str] = ["run"]
    mode = str(payload.get("mode") or "people").lower()
    if mode not in RUN_MODES:
        raise ValueError(f"unknown mode {mode!r}")
    args += ["--mode", mode]

    for flag, key in (("--companies", "companies"), ("--titles", "titles"),
                      ("--keywords", "keywords"), ("--skills", "skills"),
                      ("--location", "location")):
        value = str(payload.get(key) or "").strip()
        if value:
            args += [flag, value[:500]]

    limit = _clean_int(payload.get("limit"), 0, 0, 500)
    if limit:
        args += ["--limit", str(limit)]
    max_profiles = _clean_int(payload.get("max_profiles"), 0, 0, 10000)
    if max_profiles:
        args += ["--max-profiles", str(max_profiles)]
    for key, flag, top in (("profiles_per_day", "--profiles-per-day", 10000),
                           ("walls_per_day", "--walls-per-day", 100),
                           ("runtime_minutes", "--runtime-minutes", 1440)):
        value = _clean_int(payload.get(key), 0, 0, top)
        if value:
            args += [flag, str(value)]

    # Mode-specific facets. Values are validated/clamped downstream by the CLI
    # and the URL builders; here we only keep them bounded.
    for flag, key, max_length in (
        ("--network", "network", 20),
        ("--seniority", "seniority", 60),
        ("--current-company-ids", "current_company_ids", 200),
        ("--past-company-ids", "past_company_ids", 200),
        ("--school-ids", "school_ids", 200),
        ("--industry-ids", "industry_ids", 200),
        ("--title-filter", "title_filter", 120),
        ("--date-posted", "date_posted", 20),
        ("--experience-levels", "experience_levels", 40),
        ("--workplace", "workplace", 4),
        ("--job-types", "job_types", 40),
    ):
        value = str(payload.get(key) or "").strip()[:max_length]
        if value:
            args += [flag, value]
    if payload.get("easy_apply"):
        args.append("--easy-apply")
    for flag, key in (("--max-pages", "max_pages"),
                      ("--max-candidate-pages", "max_candidate_pages")):
        value = _clean_int(payload.get(key), 0, 0, 100)
        if value:
            args += [flag, str(value)]

    formats = ",".join(fmt for fmt in str(payload.get("formats") or "").split(",")
                       if fmt.strip() in EXPORT_FORMATS)
    if formats:
        args += ["--formats", formats]
    if payload.get("headless"):
        args.append("--headless")
    if payload.get("safe_mode") is False:
        args.append("--unsafe")
    if payload.get("dry_run"):
        args.append("--dry-run")
    if payload.get("fresh"):
        args.append("--fresh")
    if payload.get("no_db"):
        args.append("--no-db")
    if payload.get("challenge_timeout"):
        args += ["--challenge-timeout", str(_clean_int(payload.get("challenge_timeout"),
                                                      0, 0, 3600))]
    return args


def run_cli_json(args: List[str], root: Path = ROOT, timeout: float = 300.0) -> Dict[str, Any]:
    """Run a ``--json`` CLI command and parse its stdout."""
    command = [sys.executable, str(root / "cli.py")] + list(args)
    try:
        completed = subprocess.run(command, cwd=str(root), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": f"timeout after {timeout:.0f}s", "output": str(exc)}
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    try:
        data = json.loads(output) if output else {}
    except json.JSONDecodeError:
        data = {"raw": output[-4000:]}
    if not isinstance(data, dict):
        data = {"data": data}
    data.setdefault("ok", completed.returncode == 0)
    data["exit_code"] = completed.returncode
    if completed.returncode != 0 and error:
        data["stderr"] = error[-3000:]
    return data


def export_records(payload: Dict[str, Any], root: Path = ROOT) -> Dict[str, Any]:
    from exporters import export_all

    store = _store(str(payload.get("db") or ""))
    try:
        records = store.load_records(limit=None)
    finally:
        store.close()
    if not records:
        raise ValueError("the store has no records to export")
    formats = [fmt for fmt in (payload.get("formats") or ["xlsx", "csv", "json", "md", "html"])
               if str(fmt).lower() in EXPORT_FORMATS]
    base = str(payload.get("out") or (root / "output" / "linkedin_results.xlsx"))
    paths = export_all(records, base, formats=formats, mode=_infer_mode(records),
                       meta={"source": "dashboard", "generated_at": _now()})
    return {"ok": True, "count": len(records), "files": paths}


# ─────────────────────────────────────────────────────────────
#  HTTP server
# ─────────────────────────────────────────────────────────────

class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, root: Path = ROOT):
        super().__init__(address, handler)
        self.root = Path(root)
        self.manager = RunManager(self.root)


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

    # ── plumbing ──

    def log_message(self, fmt, *args):
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> Dict[str, Any]:
        length = _clean_int(self.headers.get("Content-Length"), 0, 0, 2_000_000)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    @property
    def root(self) -> Path:
        return self.server.root

    @property
    def manager(self) -> RunManager:
        return self.server.manager

    # ── routing ──

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                html = UI_FILE.read_bytes() if UI_FILE.exists() else b"<h1>dashboard_ui.html missing</h1>"
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                self._json(build_status(self.root, self.manager))
                return
            if path == "/api/records":
                payload = list_records(query=(query.get("query") or [""])[0],
                                       limit=_clean_int((query.get("limit") or ["50"])[0],
                                                        50, 1, 500))
                self._json({"ok": True, **payload})
                return
            if path == "/api/record":
                record = get_record((query.get("id") or [""])[0])
                if record is None:
                    self._error("record not found", 404)
                else:
                    self._json({"ok": True, "record": record})
                return
            if path == "/api/runs":
                store = _store()
                try:
                    runs = store.load_runs(limit=_clean_int((query.get("limit") or ["20"])[0],
                                                            20, 1, 100))
                finally:
                    store.close()
                self._json({"ok": True, "runs": runs})
                return
            if path == "/api/proxies":
                from proxy_intel import ProxyIntel
                self._json({"ok": True, "proxies": ProxyIntel(self.root / "output" / "proxy_intel.json").summary()})
                return
            if path == "/api/run/status":
                tail = _clean_int((query.get("tail") or ["150"])[0], 150, 0, 5000)
                self._json({"ok": True, "run": self.manager.snapshot(tail=tail)})
                return
            if path == "/api/download":
                self._download((query.get("path") or [""])[0])
                return
            if path == "/api/health":
                self._json({"ok": True, "time": _now()})
                return
            self._error("not found", 404)
        except Exception as exc:
            logger.exception("GET %s failed", path)
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._body()
            if path == "/api/run":
                args = build_run_args(payload)
                self._json({"ok": True, "run": self.manager.start(args)})
                return
            if path == "/api/run/stop":
                self._json({"ok": True, "run": self.manager.stop()})
                return
            if path == "/api/stealth":
                args = ["stealth-check", "--json"]
                if payload.get("headed"):
                    args.append("--headed")
                engine = str(payload.get("engine") or "").strip()
                if engine in ("auto", "patchright", "camoufox", "playwright"):
                    args += ["--engine", engine]
                country = str(payload.get("country") or "").strip().upper()
                if len(country) == 2:
                    args += ["--country", country]
                result = run_cli_json(args, self.root, timeout=240)
                self._json({"ok": bool(result.get("ok", False)), "result": result})
                return
            if path == "/api/proxy-scan":
                args = ["proxy-scan", "--json"]
                proxies = str(payload.get("proxies") or "").strip()
                if proxies:
                    args += ["--proxies", proxies[:1000]]
                result = run_cli_json(args, self.root, timeout=300)
                # A scan that ran but found zero healthy proxies is still a
                # completed scan, not an API failure — the table shows the truth.
                ran = isinstance(result.get("results"), list)
                self._json({"ok": bool(ran or result.get("ok", False)), "result": result})
                return
            if path == "/api/export":
                self._json(export_records(payload, self.root))
                return
            self._error("not found", 404)
        except ValueError as exc:
            self._error(str(exc), 400)
        except RuntimeError as exc:
            self._error(str(exc), 409)
        except Exception as exc:
            logger.exception("POST %s failed", path)
            self._error(f"{type(exc).__name__}: {exc}", 500)

    # ── files ──

    def _download(self, raw_path: str) -> None:
        relative = unquote(raw_path or "").strip()
        if not relative:
            self._error("path is required", 400)
            return
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.is_file():
            self._error("file not found", 404)
            return
        if target.suffix.lower() not in (".xlsx", ".csv", ".json", ".jsonl", ".md", ".html", ".png", ".log", ".txt"):
            self._error("file type not served", 403)
            return
        data = target.read_bytes()
        self._send(200, data, "application/octet-stream", {
            "Content-Disposition": f'attachment; filename="{target.name}"'})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local dashboard for the LinkedIn scraper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the browser")
    parser.add_argument("--db", default="", help="SQLite path override")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    if args.db:
        os.environ["DASHBOARD_DB"] = args.db

    try:
        server = DashboardServer((args.host, int(args.port)), Handler, root=ROOT)
    except OSError as exc:
        print(f"[dashboard] cannot bind {args.host}:{args.port} — {exc}", file=sys.stderr)
        return 1
    url = f"http://{args.host}:{args.port}"
    print(f"[dashboard] {url}")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] stopping")
    finally:
        if server.manager.running():
            server.manager.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
