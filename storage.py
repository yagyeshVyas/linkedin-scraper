"""SQLite storage: cross-run dedupe, resume-safe checkpoints, analytics.

Before this module every run lived in ``progress.json``: one giant JSON blob,
rewritten atomically after each search, with no history and no way to ask
"which companies did I already exhaust".  This store keeps one row per unique
profile (keyed by the public LinkedIn id), tracks every run, records finished
search combinations, and answers the queries the CLI / MCP / report layers need.

Design choices:
  * stdlib ``sqlite3`` only - no new dependency, works offline.
  * WAL mode so the live dashboard / MCP server can read while a scrape writes.
  * the full record is stored as JSON in ``data``; the handful of columns
    beside it exist purely for indexing and cheap filtering, so adding a new
    scraped field never needs a migration.
  * upserts merge (see ``enrichment.merge_record``) instead of overwriting, so
    a later scrape that lost a section cannot erase data the first one had.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from enrichment import dedupe_key, merge_record

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    dedupe_key   TEXT PRIMARY KEY,
    public_id    TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '',
    name         TEXT DEFAULT '',
    title        TEXT DEFAULT '',
    company      TEXT DEFAULT '',
    location     TEXT DEFAULT '',
    email        TEXT DEFAULT '',
    seniority    TEXT DEFAULT '',
    match_score  REAL DEFAULT 0,
    data         TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    first_run    TEXT DEFAULT '',
    last_run     TEXT DEFAULT '',
    seen_count   INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_records_company ON records(company);
CREATE INDEX IF NOT EXISTS idx_records_title   ON records(title);
CREATE INDEX IF NOT EXISTS idx_records_score   ON records(match_score);
CREATE INDEX IF NOT EXISTS idx_records_seen    ON records(last_seen);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT,
    finished_at TEXT,
    mode        TEXT,
    status      TEXT DEFAULT 'running',
    stats       TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS search_keys (
    key          TEXT PRIMARY KEY,
    run_id       TEXT DEFAULT '',
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT DEFAULT '',
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    detail  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm(value) -> str:
    return " ".join(str(value or "").split())


def _to_float(value, default: float = 0.0) -> float:
    """Coerce a scraped/legacy score to float without ever raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Store:
    """Thin, thread-safe wrapper around one SQLite file."""

    def __init__(self, path: str = "output/scraper.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(_SCHEMA)
            self.conn.commit()

    # ── lifecycle ──

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass

    # ── runs ──

    def start_run(self, mode: str, meta: Optional[dict] = None) -> str:
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        with self._lock:
            self.conn.execute(
                "INSERT INTO runs (run_id, started_at, mode, status, stats) "
                "VALUES (?, ?, ?, 'running', ?)",
                (run_id, _now(), _norm(mode), json.dumps(meta or {}, ensure_ascii=False)),
            )
            self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, stats: Optional[dict] = None,
                   status: str = "finished") -> None:
        if not run_id:
            return
        with self._lock:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, status=?, stats=? WHERE run_id=?",
                (_now(), status, json.dumps(stats or {}, ensure_ascii=False), run_id),
            )
            self.conn.commit()

    def load_runs(self, limit: int = 20) -> List[dict]:
        rows = self._query(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (int(limit),))
        return [self._run_row(row) for row in rows]

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["stats"] = json.loads(data.get("stats") or "{}")
        except Exception:
            data["stats"] = {}
        return data

    # ── records ──

    def upsert_record(self, record: dict, run_id: str = "") -> dict:
        """Insert or merge one profile. Returns {status, dedupe_key, record}.

        ``status`` is "inserted" for a new profile, "updated" when the same
        person was seen before (even in an earlier run), "unchanged" when the
        stored JSON is byte-identical.
        """
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        key = str(record.get("dedupe_key") or dedupe_key(record))
        if not key:
            key = "hash:" + uuid.uuid4().hex[:16]
        now = _now()

        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM records WHERE dedupe_key=?", (key,)).fetchone()
            if row is None:
                payload = dict(record, dedupe_key=key)
                self.conn.execute(
                    "INSERT INTO records (dedupe_key, public_id, linkedin_url, name,"
                    " title, company, location, email, seniority, match_score, data,"
                    " first_seen, last_seen, first_run, last_run, seen_count)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        key,
                        _norm(payload.get("public_id")),
                        _norm(payload.get("linkedin_url")),
                        _norm(payload.get("name")),
                        _norm(payload.get("title")),
                        _norm(payload.get("company")),
                        _norm(payload.get("location")),
                        _norm(payload.get("email")),
                        _norm(payload.get("seniority")),
                        _to_float(payload.get("match_score")),
                        json.dumps(payload, ensure_ascii=False, default=str),
                        now, now, run_id or "", run_id or "",
                    ),
                )
                self.conn.commit()
                return {"status": "inserted", "dedupe_key": key, "record": payload}

            existing = self._decode(row["data"], row)
            merged = merge_record(existing, dict(record, dedupe_key=key))
            merged["first_seen"] = existing.get("first_seen") or row["first_seen"]
            merged["dedupe_key"] = key
            if merged.get("match_score") is None:
                merged["match_score"] = existing.get("match_score")

            new_json = json.dumps(merged, ensure_ascii=False, default=str, sort_keys=True)
            old_json = json.dumps(existing, ensure_ascii=False, default=str, sort_keys=True)
            status = "unchanged" if new_json == old_json else "updated"

            self.conn.execute(
                "UPDATE records SET public_id=?, linkedin_url=?, name=?, title=?,"
                " company=?, location=?, email=?, seniority=?, match_score=?, data=?,"
                " last_seen=?, last_run=?, seen_count=seen_count+1 WHERE dedupe_key=?",
                (
                    _norm(merged.get("public_id")),
                    _norm(merged.get("linkedin_url")),
                    _norm(merged.get("name")),
                    _norm(merged.get("title")),
                    _norm(merged.get("company")),
                    _norm(merged.get("location")),
                    _norm(merged.get("email")),
                    _norm(merged.get("seniority")),
                    _to_float(merged.get("match_score")),
                    new_json, now, run_id or "", key,
                ),
            )
            self.conn.commit()
            return {"status": status, "dedupe_key": key, "record": merged}

    def upsert_many(self, records: Iterable[dict], run_id: str = "") -> Dict[str, int]:
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        for record in records or []:
            result = self.upsert_record(record, run_id=run_id)
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        return counts

    def get_record(self, key: str) -> Optional[dict]:
        key = str(key or "")
        if not key:
            return None
        lookup = dedupe_key({"linkedin_url": key,
                             "public_id": key if "/" not in key else ""})
        row = self.conn.execute(
            "SELECT * FROM records WHERE dedupe_key=? OR public_id=?",
            (lookup, lookup)).fetchone()
        return self._decode(row["data"], row) if row else None

    def record_exists(self, key_or_url: str) -> bool:
        text = str(key_or_url or "").strip()
        if not text:
            return False
        key = dedupe_key({"linkedin_url": text, "public_id": text
                          if "/" not in text else ""})
        row = self.conn.execute(
            "SELECT 1 FROM records WHERE dedupe_key=? OR public_id=? LIMIT 1",
            (key, key)).fetchone()
        return row is not None

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()
        return int(row["n"]) if row else 0

    def load_records(self, limit: Optional[int] = None, offset: int = 0,
                     *, company: str = "", title: str = "", location: str = "",
                     seniority: str = "", country: str = "", query: str = "",
                     min_score: float = 0.0, has_email: bool = False,
                     open_to_work: bool = False, since: str = "",
                     order_by: str = "last_seen", descending: bool = True) -> List[dict]:
        """Filtered read used by the CLI, MCP tools and the HTML report."""
        where: List[str] = []
        params: List[object] = []
        if company:
            where.append("company LIKE ? ESCAPE '\\'")
            params.append(f"%{_like(company)}%")
        if title:
            where.append("title LIKE ? ESCAPE '\\'")
            params.append(f"%{_like(title)}%")
        if location:
            where.append("location LIKE ? ESCAPE '\\'")
            params.append(f"%{_like(location)}%")
        if seniority:
            where.append("seniority LIKE ? ESCAPE '\\'")
            params.append(f"%{_like(seniority)}%")
        if country:
            where.append("(data LIKE ? ESCAPE '\\')")
            params.append(f'%"country": "{_like(country)}%')
        if query:
            where.append("(name LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\'"
                         " OR company LIKE ? ESCAPE '\\')")
            pattern = f"%{_like(query)}%"
            params.extend([pattern, pattern, pattern])
        if min_score:
            where.append("match_score >= ?")
            params.append(_to_float(min_score))
        if has_email:
            where.append("email != ''")
        if open_to_work:
            where.append("(data LIKE '%\"open_to_work\": true%')")
        if since:
            where.append("last_seen >= ?")
            params.append(str(since))

        allowed_order = {"last_seen", "first_seen", "match_score", "name",
                         "company", "title", "seen_count"}
        order = order_by if order_by in allowed_order else "last_seen"
        direction = "DESC" if descending else "ASC"
        sql = "SELECT * FROM records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} {direction}"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        rows = self._query(sql, tuple(params))
        return [self._decode(row["data"], row) for row in rows]

    def all_records(self) -> List[dict]:
        return self.load_records(limit=None)

    def update_score(self, key: str, score: float, extra: Optional[dict] = None) -> None:
        """Persist a recomputed match score without a full upsert."""
        with self._lock:
            row = self.conn.execute(
                "SELECT data FROM records WHERE dedupe_key=?", (key,)).fetchone()
            if row is None:
                return
            payload = self._decode(row["data"], row)
            payload["match_score"] = _to_float(score)
            if extra:
                payload.update(extra)
            self.conn.execute(
                "UPDATE records SET match_score=?, data=? WHERE dedupe_key=?",
                (_to_float(score), json.dumps(payload, ensure_ascii=False, default=str), key),
            )
            self.conn.commit()

    # ── search checkpoints ──

    def mark_search_done(self, key: str, run_id: str = "") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO search_keys (key, run_id, completed_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET run_id=excluded.run_id,"
                " completed_at=excluded.completed_at",
                (str(key), run_id or "", _now()),
            )
            self.conn.commit()

    def is_search_done(self, key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM search_keys WHERE key=? LIMIT 1", (str(key),)).fetchone()
        return row is not None

    def completed_keys(self) -> List[str]:
        return [row["key"] for row in
                self._query("SELECT key FROM search_keys ORDER BY completed_at")]

    def clear_search_keys(self) -> int:
        with self._lock:
            cursor = self.conn.execute("DELETE FROM search_keys")
            self.conn.commit()
            return cursor.rowcount

    # ── events ──

    def log_event(self, kind: str, detail: str = "", run_id: str = "") -> None:
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO events (run_id, ts, kind, detail) VALUES (?,?,?,?)",
                    (run_id or "", _now(), str(kind)[:80], str(detail)[:2000]),
                )
                self.conn.commit()
        except Exception:
            pass  # telemetry must never break a scrape

    def events(self, run_id: str = "", limit: int = 100) -> List[dict]:
        if run_id:
            rows = self._query(
                "SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, int(limit)))
        else:
            rows = self._query(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    # ── analytics ──

    def stats(self) -> dict:
        total = self.count()
        with_email = self._scalar("SELECT COUNT(*) FROM records WHERE email != ''")
        with_phone = self._scalar(
            "SELECT COUNT(*) FROM records WHERE data LIKE '%\"phone_e164\": \"+%'")
        open_to_work = self._scalar(
            "SELECT COUNT(*) FROM records WHERE data LIKE '%\"open_to_work\": true%'")
        avg_score = self._scalar("SELECT AVG(match_score) FROM records") or 0
        avg_completeness = self._scalar(
            "SELECT AVG(json_extract(data, '$.completeness_score')) FROM records") \
            if self._has_json1() else 0
        companies = [
            {"company": row["company"], "count": row["n"]}
            for row in self._query(
                "SELECT company, COUNT(*) AS n FROM records WHERE company != ''"
                " GROUP BY company ORDER BY n DESC LIMIT 10")
        ]
        seniority = [
            {"seniority": row["seniority"], "count": row["n"]}
            for row in self._query(
                "SELECT seniority, COUNT(*) AS n FROM records WHERE seniority != ''"
                " GROUP BY seniority ORDER BY n DESC LIMIT 10")
        ]
        countries: Dict[str, int] = {}
        for record in self.load_records():
            country = str(record.get("country") or "").strip()
            if country:
                countries[country] = countries.get(country, 0) + 1
        top_countries = sorted(countries.items(), key=lambda kv: -kv[1])[:10]

        return {
            "total_records": total,
            "with_email": int(with_email or 0),
            "with_phone": int(with_phone or 0),
            "open_to_work": int(open_to_work or 0),
            "avg_match_score": round(float(avg_score or 0), 1),
            "avg_completeness": round(float(avg_completeness or 0), 1),
            "top_companies": companies,
            "seniority": seniority,
            "top_countries": [{"country": c, "count": n} for c, n in top_countries],
            "runs": self._scalar("SELECT COUNT(*) FROM runs"),
        }

    def _has_json1(self) -> bool:
        try:
            self.conn.execute("SELECT json_extract('{}', '$.a')")
            return True
        except sqlite3.OperationalError:
            return False

    # ── maintenance ──

    def import_progress(self, progress_path: str, run_id: str = "",
                        enrich: bool = True) -> int:
        """Seed the store from a legacy progress.json (idempotent).

        Legacy records predate the enrichment fields, so they are enriched on
        the way in: city/country, normalized skills, dedupe key and completeness
        become available for old data without re-scraping anything.
        """
        path = Path(str(progress_path or ""))
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return 0
        imported = 0
        for record in results:
            if not isinstance(record, dict):
                continue
            if enrich:
                try:
                    from enrichment import enrich_record
                    record = enrich_record(record)
                except Exception:
                    pass
            self.upsert_record(record, run_id=run_id)
            imported += 1
        for key in (data.get("completed_keys") or []):
            self.mark_search_done(str(key), run_id=run_id)
        for key in (data.get("completed_companies") or []):
            self.mark_search_done(f"people::{key}", run_id=run_id)
        return imported

    def prune(self, days: int = 90) -> int:
        """Delete records not seen in the last ``days`` days."""
        cutoff = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            cursor = self.conn.execute("DELETE FROM records WHERE last_seen <= ?", (cutoff,))
            self.conn.commit()
        return cursor.rowcount

    def vacuum(self) -> None:
        with self._lock:
            self.conn.execute("VACUUM")
            self.conn.commit()

    def backup(self, destination: str) -> str:
        dest_path = Path(destination)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest_path))
            try:
                self.conn.backup(target)
            finally:
                target.close()
        return str(dest_path)

    # ── internals ──

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, params).fetchall())

    def _scalar(self, sql: str, params: tuple = ()):
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    @staticmethod
    def _decode(payload: str, row: Optional[sqlite3.Row] = None) -> dict:
        try:
            data = json.loads(payload) if payload else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if row is not None:
            data["dedupe_key"] = row["dedupe_key"]
            for column in ("first_seen", "last_seen", "first_run", "last_run",
                           "seen_count"):
                data[column] = row[column]
        return data


def _like(value: str) -> str:
    """Escape LIKE wildcards so a query containing % or _ is literal."""
    return (str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


class EventLog:
    """Append-only JSONL run log (one file per run) for post-mortems."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, kind: str, **data) -> None:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "kind": str(kind), **data}
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning(f"event log write failed: {exc}")


def open_store(path: str = "output/scraper.db") -> Optional[Store]:
    """Open a store, returning None (with a warning) instead of raising."""
    try:
        return Store(path)
    except Exception as exc:
        logger.warning(f"SQLite store unavailable ({exc}); continuing without it.")
        return None


def default_store_path(config) -> str:
    return str(getattr(config, "DATABASE_FILE", "output/scraper.db") or "output/scraper.db")


def ensure_dir(path: str) -> None:
    if path and os.path.dirname(path):
        Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
