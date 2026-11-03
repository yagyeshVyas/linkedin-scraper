"""A dependency-free MCP server over the scraped LinkedIn pool.

MCP (Model Context Protocol) lets an AI agent call tools over stdio.  This
server exposes the *results* of scraping - not the browser - so an agent can
query, rank and export the pool safely, with no risk of it clicking around
LinkedIn on your account.

Wire format: newline-delimited JSON-RPC 2.0 on stdin/stdout (the MCP stdio
transport).  Nothing is written to stdout except protocol messages; logs go to
stderr.  Implemented with the standard library only, so there is nothing to
install and nothing to keep in sync with an SDK release.

Run it directly:

    python cli.py mcp --db output/scraper.db

or point any MCP client at ``python mcp_server.py --db output/scraper.db``.

Tools
-----
list_profiles      filter the stored pool (query/company/title/seniority/country/score)
get_profile        fetch one profile by public id or URL
get_stats          pool analytics (totals, coverage, top companies/skills)
search_profiles    score the pool against free-text resume keywords
export_records     write csv/json/jsonl/md/html and return the path
build_search_url   render a LinkedIn search URL with validated facets
list_runs          recent scrape runs with their stats
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("linkedin_mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "linkedin-scraper", "version": "5.0.0"}
DEFAULT_DB = "output/scraper.db"


class ToolError(Exception):
    """Raised inside a tool call; turned into an isError result, not a crash."""


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: List[dict] = [
    {
        "name": "list_profiles",
        "description": "List scraped profiles from the local database with optional "
                       "filters. Returns concise profile objects (name, title, company, "
                       "location, email, seniority, match score).",
        "inputSchema": _schema({
            "query": {"type": "string", "description": "Free text across name/title/company"},
            "company": {"type": "string"},
            "title": {"type": "string"},
            "seniority": {"type": "string", "description": "e.g. Director, VP / Head"},
            "country": {"type": "string"},
            "has_email": {"type": "boolean"},
            "open_to_work": {"type": "boolean"},
            "min_score": {"type": "number", "description": "Minimum resume match score"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20},
            "order_by": {"type": "string", "enum": ["last_seen", "match_score", "name", "company"]},
        }),
    },
    {
        "name": "get_profile",
        "description": "Fetch the full stored record for one profile by LinkedIn public id "
                       "or profile URL.",
        "inputSchema": _schema({
            "id_or_url": {"type": "string", "description": "e.g. jane-doe or https://www.linkedin.com/in/jane-doe"},
        }, ["id_or_url"]),
    },
    {
        "name": "get_stats",
        "description": "Aggregate statistics for the stored pool: totals, email coverage, "
                       "open-to-work count, top companies, seniority mix, countries, runs.",
        "inputSchema": _schema({}),
    },
    {
        "name": "search_profiles",
        "description": "Rank the stored pool against resume text or explicit keywords and "
                       "return the best matches with scores and matched keywords.",
        "inputSchema": _schema({
            "resume_text": {"type": "string", "description": "Resume or job description text"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "min_score": {"type": "number", "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        }),
    },
    {
        "name": "export_records",
        "description": "Export the stored pool to csv/json/jsonl/md/html and return the "
                       "written path and record count.",
        "inputSchema": _schema({
            "format": {"type": "string", "enum": ["csv", "json", "jsonl", "md", "html"],
                       "default": "csv"},
            "path": {"type": "string", "description": "Output path (default output/export.<format>)"},
            "limit": {"type": "integer", "minimum": 0},
            "company": {"type": "string"},
            "min_score": {"type": "number"},
        }),
    },
    {
        "name": "build_search_url",
        "description": "Build a validated LinkedIn search URL (people, jobs or company) with "
                       "facets. Returns the URL plus warnings for values LinkedIn would ignore.",
        "inputSchema": _schema({
            "kind": {"type": "string", "enum": ["people", "jobs", "company"], "default": "people"},
            "keywords": {"type": "string"},
            "location": {"type": "string", "description": "Country name or geo:<id>"},
            "network": {"type": "array", "items": {"type": "string"}},
            "current_company_ids": {"type": "array", "items": {"type": "string"}},
            "past_company_ids": {"type": "array", "items": {"type": "string"}},
            "school_ids": {"type": "array", "items": {"type": "string"}},
            "industry_ids": {"type": "array", "items": {"type": "string"}},
            "seniority": {"type": "array", "items": {"type": "string"}},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "date_posted": {"type": "string"},
            "experience_levels": {"type": "array", "items": {"type": "string"}},
            "job_types": {"type": "array", "items": {"type": "string"}},
            "workplace": {"type": "array", "items": {"type": "string"}},
            "easy_apply": {"type": "boolean"},
            "sort_by": {"type": "string"},
        }),
    },
    {
        "name": "list_runs",
        "description": "List recent scrape runs with mode, status, timing and summary stats.",
        "inputSchema": _schema({
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        }),
    },
]

_SUMMARY_KEYS = ("name", "title", "company", "location", "email", "seniority_label",
                 "match_score", "fit_score", "completeness_score", "open_to_work",
                 "linkedin_url", "public_id", "seen_count", "last_seen")


def summarize_profile(record: dict) -> dict:
    return {key: record.get(key) for key in _SUMMARY_KEYS if record.get(key) not in (None, "")}


class MCPServer:
    """JSON-RPC dispatcher + stdio loop; also usable directly in tests."""

    def __init__(self, db_path: str = DEFAULT_DB, store=None, name: str = SERVER_INFO["name"]):
        self.db_path = str(db_path or DEFAULT_DB)
        self._store = store
        self.name = name
        self.initialized = False

    # ── resources ──

    @property
    def store(self):
        if self._store is None:
            from storage import Store
            self._store = Store(self.db_path)
        return self._store

    def close(self) -> None:
        if self._store is not None:
            try:
                self._store.close()
            except Exception:
                pass
            self._store = None

    # ── protocol ──

    def handle(self, message: dict) -> Optional[dict]:
        """Handle one JSON-RPC message. Returns None for notifications."""
        if not isinstance(message, dict):
            return _error(None, -32600, "Invalid Request")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message

        if method == "initialize":
            result = self._initialize(message.get("params") or {})
            return None if is_notification else _result(msg_id, result)
        if method in ("notifications/initialized", "initialized"):
            self.initialized = True
            return None
        if method == "ping":
            return None if is_notification else _result(msg_id, {})
        if method == "tools/list":
            return None if is_notification else _result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = message.get("params") or {}
            return None if is_notification else _result(
                msg_id, self._call_tool(params.get("name"), params.get("arguments") or {}))
        if method == "resources/list":
            return None if is_notification else _result(msg_id, {"resources": []})
        if method == "prompts/list":
            return None if is_notification else _result(msg_id, {"prompts": []})
        if method == "shutdown":
            return None if is_notification else _result(msg_id, {})
        return None if is_notification else _error(msg_id, -32601, f"Method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        requested = str((params or {}).get("protocolVersion") or PROTOCOL_VERSION)
        return {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {**SERVER_INFO, "name": self.name},
            "instructions": (
                "Query the local LinkedIn scrape database. Profiles are scraped by the "
                "CLI (python cli.py run); this server never browses LinkedIn itself."
            ),
        }

    def _call_tool(self, name: str, arguments: dict) -> dict:
        if not name:
            return _tool_error("missing tool name")
        handler = {
            "list_profiles": self._tool_list_profiles,
            "get_profile": self._tool_get_profile,
            "get_stats": self._tool_get_stats,
            "search_profiles": self._tool_search_profiles,
            "export_records": self._tool_export_records,
            "build_search_url": self._tool_build_search_url,
            "list_runs": self._tool_list_runs,
        }.get(name)
        if handler is None:
            return _tool_error(f"unknown tool: {name}")
        try:
            return handler(arguments or {})
        except ToolError as exc:
            return _tool_error(str(exc))
        except Exception as exc:  # never let one bad call kill the server
            logger.exception("tool %s failed", name)
            return _tool_error(f"{type(exc).__name__}: {exc}")

    # ── tools ──

    def _load(self, arguments: dict, default_limit: Optional[int] = None) -> List[dict]:
        limit = arguments.get("limit", default_limit)
        try:
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit = None
        return self.store.load_records(
            limit=limit if limit and limit > 0 else None,
            query=str(arguments.get("query") or ""),
            company=str(arguments.get("company") or ""),
            title=str(arguments.get("title") or ""),
            seniority=str(arguments.get("seniority") or ""),
            country=str(arguments.get("country") or ""),
            min_score=float(arguments.get("min_score") or 0),
            has_email=bool(arguments.get("has_email")),
            open_to_work=bool(arguments.get("open_to_work")),
            order_by=str(arguments.get("order_by") or "last_seen"),
        )

    def _tool_list_profiles(self, arguments: dict) -> dict:
        records = self._load(arguments, default_limit=20)
        payload = {
            "count": len(records),
            "total_in_store": self.store.count(),
            "profiles": [summarize_profile(record) for record in records],
        }
        return _tool_json(payload)

    def _tool_get_profile(self, arguments: dict) -> dict:
        key = str(arguments.get("id_or_url") or "").strip()
        if not key:
            raise ToolError("id_or_url is required")
        record = self.store.get_record(key)
        if record is None:
            raise ToolError(f"no profile stored for {key!r}")
        return _tool_json(record)

    def _tool_get_stats(self, arguments: dict) -> dict:
        return _tool_json(self.store.stats())

    def _tool_search_profiles(self, arguments: dict) -> dict:
        from scoring import keyword_weights, rank_records, score_record

        text = str(arguments.get("resume_text") or "")
        keywords = arguments.get("keywords") or []
        if keywords:
            weights = {str(keyword).lower(): 2.0 for keyword in keywords if str(keyword).strip()}
        else:
            weights = keyword_weights(text)
        if not weights:
            raise ToolError("provide resume_text or a non-empty keywords array")

        limit = int(arguments.get("limit") or 20)
        min_score = float(arguments.get("min_score") or 0)
        scored = []
        for record in self.store.load_records(limit=None):
            item = score_record(record, weights)
            if float(item.get("match_score") or 0) >= min_score:
                scored.append(item)
        ranked = rank_records(scored, by="match_score")[:limit]
        payload = {
            "count": len(ranked),
            "scored_pool": len(scored),
            "keywords": list(weights)[:40],
            "profiles": [{
                **summarize_profile(record),
                "match_score": record.get("match_score"),
                "match_tier": record.get("match_tier"),
                "matched_keywords": record.get("matched_keywords"),
            } for record in ranked],
        }
        return _tool_json(payload)

    def _tool_export_records(self, arguments: dict) -> dict:
        from exporters import (export_csv, export_html_report, export_json,
                               export_jsonl)

        fmt = str(arguments.get("format") or "csv").lower()
        records = self._load(arguments, default_limit=None)
        if not records:
            raise ToolError("the store has no records matching those filters")
        path = str(arguments.get("path") or f"output/export.{fmt}")
        if fmt == "csv":
            written = export_csv(records, path)
        elif fmt == "json":
            written = export_json(records, path, meta={"source": "mcp"})
        elif fmt == "jsonl":
            written = export_jsonl(records, path)
        elif fmt in ("md", "markdown"):
            from exporters import export_markdown
            written = export_markdown(records, path, meta={"title": "LinkedIn Export"})
        elif fmt == "html":
            written = export_html_report(records, path, meta={"title": "LinkedIn Export"})
        else:
            raise ToolError(f"unsupported format {fmt!r}")
        return _tool_json({"path": written, "format": fmt, "count": len(records)})

    def _tool_build_search_url(self, arguments: dict) -> dict:
        from search_filters import (JobFilters, PeopleFilters,
                                    build_company_search_url, build_jobs_search_url,
                                    build_people_search_url, resolve_geo)

        kind = str(arguments.get("kind") or "people").lower()
        keywords = str(arguments.get("keywords") or "")
        warnings: List[str] = []

        if kind == "company":
            return _tool_json({"url": build_company_search_url(keywords), "warnings": []})

        if kind == "jobs":
            job_filters = JobFilters(
                keywords=keywords,
                location=str(arguments.get("location") or ""),
                geo_id=str(arguments.get("location") or "") or None,
                date_posted=str(arguments.get("date_posted") or ""),
                experience_levels=list(arguments.get("experience_levels") or []),
                job_types=list(arguments.get("job_types") or []),
                workplace=list(arguments.get("workplace") or []),
                easy_apply=bool(arguments.get("easy_apply")),
                company_ids=list(arguments.get("current_company_ids") or []),
                sort_by=str(arguments.get("sort_by") or ""),
            )
            url = build_jobs_search_url(job_filters)
            return _tool_json({"url": url, "warnings": job_filters.warnings})

        geo_ids, geo_warning = resolve_geo(str(arguments.get("location") or ""))
        if geo_warning:
            warnings.append(geo_warning)
        people_filters = PeopleFilters(
            keywords=keywords,
            geo_ids=geo_ids,
            network=list(arguments.get("network") or []),
            current_company_ids=list(arguments.get("current_company_ids") or []),
            past_company_ids=list(arguments.get("past_company_ids") or []),
            school_ids=list(arguments.get("school_ids") or []),
            industry_ids=list(arguments.get("industry_ids") or []),
            seniority=list(arguments.get("seniority") or []),
            page=int(arguments.get("page") or 1),
        )
        url = build_people_search_url(people_filters)
        warnings.extend(people_filters.warnings)
        return _tool_json({"url": url, "warnings": warnings})

    def _tool_list_runs(self, arguments: dict) -> dict:
        runs = self.store.load_runs(limit=int(arguments.get("limit") or 10))
        return _tool_json({"count": len(runs), "runs": runs})

    # ── stdio loop ──

    def serve(self, stdin=None, stdout=None) -> int:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _error(None, -32700, f"Parse error: {exc}")
            else:
                response = self.handle(message)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
        return 0


# ── JSON-RPC helpers ──

def _result(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_json(payload: dict) -> dict:
    return _tool_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCP server over the LinkedIn scrape database")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--name", default=SERVER_INFO["name"], help="server name reported to clients")
    return parser


def main(argv=None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args(argv)
    server = MCPServer(args.db, name=args.name)
    logger.info("MCP server ready on stdio (db=%s)", args.db)
    try:
        return server.serve()
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
