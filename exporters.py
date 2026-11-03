"""Multi-format exports: CSV, JSON, JSONL, Markdown and a standalone HTML report.

The Excel writer in ``utils.py`` stays the default for spreadsheet users.  This
module adds the formats that make the data usable elsewhere - a CSV for
imports, JSON/JSONL for pipelines and LLM context, Markdown for a PR or Slack
post, and a single-file HTML report with charts you can email to a hiring
manager (no external assets, no server, opens offline).

Design rules:
  * every exporter sorts columns the way a recruiter reads a profile;
  * values are flattened to text so a JSON-encoded list never lands in a cell
    as ``[...]``;
  * CSV is UTF-8 **with BOM** so Excel on Windows opens non-ASCII names right;
  * the HTML report escapes every value (profile text is untrusted input).
"""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ─────────────────────────────────────────────────────────────
#  Columns
# ─────────────────────────────────────────────────────────────

PEOPLE_COLUMNS: Sequence[Tuple[str, str]] = (
    ("name", "Full Name"),
    ("title", "Job Title"),
    ("company", "Company"),
    ("search_company", "Searched Company"),
    ("location", "Location"),
    ("city", "City"),
    ("country", "Country"),
    ("seniority_label", "Seniority"),
    ("department", "Department"),
    ("match_score", "Resume Match"),
    ("fit_score", "ICP Fit"),
    ("total_experience_years", "Years Experience"),
    ("email", "Email"),
    ("email_status", "Email Status"),
    ("email_confidence", "Email Confidence"),
    ("phone_e164", "Phone"),
    ("open_to_work", "Open To Work"),
    ("is_hiring", "Hiring"),
    ("skills_normalized", "Skills (normalized)"),
    ("skills_count", "Skills Count"),
    ("companies_count", "Employers"),
    ("avg_tenure_months", "Avg Tenure (mo)"),
    ("completeness_score", "Completeness"),
    ("quality_flags", "Quality Flags"),
    ("public_id", "Profile ID"),
    ("linkedin_url", "LinkedIn URL"),
    ("scraped_at", "Scraped At"),
    ("first_seen", "First Seen"),
    ("last_seen", "Last Seen"),
    ("seen_count", "Times Seen"),
)

PEOPLE_DEEP_COLUMNS: Sequence[Tuple[str, str]] = (
    ("headline", "Headline"),
    ("about", "About"),
    ("experience", "Work History"),
    ("education", "Education"),
    ("certifications", "Certifications"),
    ("languages", "Languages"),
    ("honors", "Honors"),
    ("volunteer", "Volunteer"),
    ("projects", "Projects"),
    ("publications", "Publications"),
    ("courses", "Courses"),
    ("patents", "Patents"),
    ("services", "Services"),
    ("interests", "Interests"),
    ("recommendations", "Recommendations"),
    ("websites", "Websites"),
    ("connections", "Connections"),
    ("followers", "Followers"),
    ("follower_count", "Followers (num)"),
    ("is_premium", "Premium"),
    ("is_creator", "Creator"),
    ("verified", "Verified"),
    ("pronouns", "Pronouns"),
    ("photo_url", "Photo URL"),
    ("company_domain", "Company Domain"),
    ("email_candidates", "Inferred Emails"),
    ("skills", "Skills (raw)"),
    ("skill_categories", "Skill Categories"),
    ("matched_keywords", "Matched Keywords"),
    ("missing_keywords", "Missing Keywords"),
    ("experience_roles", "Roles (JSON)"),
    ("education_entries", "Education (JSON)"),
)

JOBS_COLUMNS: Sequence[Tuple[str, str]] = (
    ("title", "Job Title"),
    ("company", "Company"),
    ("location", "Location"),
    ("search_keyword", "Searched Keyword"),
    ("hours_old", "Hours Old"),
    ("url", "Job Link"),
    ("scraped_at", "Scraped At"),
)

_BOOL_KEYS = {"open_to_work", "is_hiring", "is_premium", "is_creator", "verified",
              "is_remote_location", "is_metro_location"}


def infer_mode(records: Iterable[dict]) -> str:
    """Guess whether a record set is jobs or people (jobs carry a job URL)."""
    for record in records or []:
        if isinstance(record, dict) and record.get("search_keyword") is not None:
            return "jobs"
        if isinstance(record, dict) and (record.get("linkedin_url") or record.get("public_id")):
            return "people"
    return "people"


def ordered_columns(records: Iterable[dict], mode: str = "people",
                    deep: bool = False) -> List[Tuple[str, str]]:
    base = list(JOBS_COLUMNS if mode == "jobs" else PEOPLE_COLUMNS)
    if mode != "jobs" and deep:
        base += [column for column in PEOPLE_DEEP_COLUMNS if column not in base]
    present = set()
    for record in records or []:
        if isinstance(record, dict):
            present.update(record.keys())
    if not present:
        return list(JOBS_COLUMNS if mode == "jobs" else PEOPLE_COLUMNS)
    columns = [(key, label) for key, label in base if key in present]
    if mode == "jobs":
        return columns
    # Any new scraped field appears after the curated columns, never dropped.
    hidden = {"data", "full_text", "_raw", "html", "soup"}
    covered = {key for key, _ in base}
    extras = sorted(key for key in present if key not in covered
                    and not key.startswith("_") and key not in hidden)
    return columns + [(key, key.replace("_", " ").title()) for key in extras]


def _cell(value) -> str:
    """Flatten any scraped value into something a spreadsheet can hold."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except Exception:
            return text
        if isinstance(parsed, list):
            return ", ".join(_cell(item) for item in parsed)
    return text


def record_rows(records: Iterable[dict], columns: Sequence[Tuple[str, str]]) -> List[List[str]]:
    return [[_cell((record or {}).get(key, "")) for key, _ in columns]
            for record in records or []]


# ─────────────────────────────────────────────────────────────
#  Plain formats
# ─────────────────────────────────────────────────────────────

def _ensure_parent(path: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def export_csv(records: Iterable[dict], output_path: str, mode: str = "people",
               deep: bool = False) -> str:
    records = list(records or [])
    columns = ordered_columns(records, mode=mode, deep=deep)
    target = _ensure_parent(output_path)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([label for _, label in columns])
        for row in record_rows(records, columns):
            writer.writerow(row)
    return str(target)


def export_json(records: Iterable[dict], output_path: str,
                meta: Optional[dict] = None) -> str:
    records = list(records or [])
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(records),
        **(meta or {}),
        "records": records,
    }
    target = _ensure_parent(output_path)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    return str(target)


def export_jsonl(records: Iterable[dict], output_path: str) -> str:
    target = _ensure_parent(output_path)
    with target.open("w", encoding="utf-8") as handle:
        for record in records or []:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return str(target)


def summarize(records: Iterable[dict]) -> dict:
    """Aggregates shared by the Markdown and HTML exporters."""
    records = list(records or [])
    total = len(records)
    emails = sum(1 for r in records if r.get("email"))
    open_to_work = sum(1 for r in records if r.get("open_to_work"))
    hiring = sum(1 for r in records if r.get("is_hiring"))
    completeness = [float(r.get("completeness_score") or 0) for r in records]
    scores = [float(r.get("match_score") or 0) for r in records if r.get("match_score")]
    companies = Counter(str(r.get("company") or r.get("search_company") or "Unknown")
                        for r in records)
    seniorities = Counter(str(r.get("seniority_label") or r.get("seniority") or "Unknown")
                          for r in records)
    countries = Counter(str(r.get("country") or "") for r in records if r.get("country"))
    skills: Counter = Counter()
    for record in records:
        for skill in str(record.get("skills_normalized") or record.get("skills") or "").split(","):
            skill = skill.strip()
            if skill:
                skills[skill] += 1
    tiers = Counter(str(r.get("match_tier") or "") for r in records if r.get("match_tier"))
    return {
        "total": total,
        "with_email": emails,
        "email_rate": round(100 * emails / total, 1) if total else 0.0,
        "open_to_work": open_to_work,
        "hiring": hiring,
        "avg_completeness": round(sum(completeness) / total, 1) if total else 0.0,
        "avg_match": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "top_companies": companies.most_common(10),
        "seniority": seniorities.most_common(8),
        "top_countries": countries.most_common(8),
        "top_skills": skills.most_common(15),
        "tiers": tiers.most_common(),
    }


def export_markdown(records: Iterable[dict], output_path: str,
                    meta: Optional[dict] = None, limit: int = 25,
                    mode: str = "people") -> str:
    records = list(records or [])
    stats = summarize(records) if mode != "jobs" else {"total": len(records)}
    title = (meta or {}).get("title") or "LinkedIn Scrape Summary"
    lines = [f"# {title}", ""]
    generated = (meta or {}).get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"_Generated {generated} - {stats['total']} record(s)_")
    lines.append("")
    if mode != "jobs":
        lines += [
            "## Snapshot",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Records | {stats['total']} |",
            f"| With email | {stats['with_email']} ({stats['email_rate']}%) |",
            f"| Open to work | {stats['open_to_work']} |",
            f"| Avg completeness | {stats['avg_completeness']} / 100 |",
            f"| Avg resume match | {stats['avg_match']} / 100 |",
            "",
        ]
        if stats["top_companies"]:
            lines += ["## Top companies", "",
                      "| Company | Profiles |", "| --- | --- |"]
            lines += [f"| {name} | {count} |" for name, count in stats["top_companies"]]
            lines.append("")
        if stats["top_skills"]:
            lines += ["## Most common skills", ""]
            lines.append(", ".join(f"{skill} ({count})" for skill, count in stats["top_skills"]))
            lines.append("")

    lines += ["## Records", "",
              "| Name | Title | Company | Location | Email | Match |",
              "| --- | --- | --- | --- | --- | --- |"]
    for record in records[:limit]:
        lines.append("| " + " | ".join(_md(record.get(key, "")) for key in
                                        ("name", "title", "company", "location")) +
                     f" | {_md(record.get('email', ''))} | {_md(record.get('match_score', ''))} |")
    if len(records) > limit:
        lines.append(f"\n_...and {len(records) - limit} more in the CSV/JSON export._")
    target = _ensure_parent(output_path)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


def _md(value) -> str:
    return str(_cell(value)).replace("|", "\\|").replace("\n", " ")


# ─────────────────────────────────────────────────────────────
#  HTML report
# ─────────────────────────────────────────────────────────────

_CSS = """
:root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--blue:#0a66c2;--bg:#f8fafc;
--card:#fff;--good:#0f9d58;--warn:#d97706}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
color:var(--ink);background:var(--bg)}
header{background:linear-gradient(120deg,#0a66c2,#004182);color:#fff;padding:28px 32px}
header h1{margin:0;font-size:24px;letter-spacing:-.02em}
header p{margin:6px 0 0;opacity:.85}
main{max-width:1200px;margin:0 auto;padding:24px 20px 64px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.card .v{font-size:26px;font-weight:700;color:var(--blue);margin-top:2px}
.card .s{font-size:12px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}
.panel h2{margin:0 0 12px;font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.bar{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.bar .label{flex:0 0 42%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .track{flex:1;background:#eef2f7;border-radius:6px;height:9px;overflow:hidden}
.bar .fill{height:100%;background:var(--blue);border-radius:6px}
.bar .n{flex:0 0 40px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;background:#f1f5f9;font-size:11px;text-transform:uppercase;
letter-spacing:.05em;color:var(--muted);cursor:pointer;white-space:nowrap}
tr:hover td{background:#f8fbff}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600}
.pill.hi{background:#e7f6ed;color:var(--good)}.pill.mid{background:#fff4e5;color:var(--warn)}
.pill.lo{background:#f1f5f9;color:var(--muted)}
.muted{color:var(--muted)}
input[type=search]{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:9px;
font-size:13px;margin-bottom:10px}
footer{max-width:1200px;margin:0 auto;padding:0 20px 40px;color:var(--muted);font-size:12px}
"""

_JS = """
(function(){
  var input=document.getElementById('q');
  var rows=Array.prototype.slice.call(document.querySelectorAll('#tbl tbody tr'));
  if(input){input.addEventListener('input',function(){
    var q=input.value.toLowerCase();
    rows.forEach(function(r){r.style.display=r.textContent.toLowerCase().indexOf(q)>-1?'':'none';});
  });}
  var table=document.getElementById('tbl');
  if(!table) return;
  table.querySelectorAll('th').forEach(function(th,i){
    th.addEventListener('click',function(){
      var body=table.tBodies[0];
      var rows=Array.prototype.slice.call(body.rows);
      var asc=th.dataset.asc!=='1';
      rows.sort(function(a,b){
        var x=a.cells[i]?a.cells[i].innerText:''; var y=b.cells[i]?b.cells[i].innerText:'';
        var nx=parseFloat(x.replace(/[^0-9.\\-]/g,'')), ny=parseFloat(y.replace(/[^0-9.\\-]/g,''));
        if(!isNaN(nx)&&!isNaN(ny)&&x.match(/[0-9]/)&&y.match(/[0-9]/)) return asc?nx-ny:ny-nx;
        return asc?x.localeCompare(y):y.localeCompare(x);
      });
      th.dataset.asc=asc?'1':'0';
      rows.forEach(function(r){body.appendChild(r);});
    });
  });
})();
"""


def _esc(value) -> str:
    return html.escape(_cell(value), quote=True)


def _bars(items: Sequence[Tuple[str, int]], empty: str = "No data") -> str:
    if not items:
        return f'<p class="muted">{_esc(empty)}</p>'
    top = max(int(count) for _, count in items) or 1
    out = []
    for label, count in items:
        width = max(2, round(100 * int(count) / top))
        out.append(
            f'<div class="bar"><span class="label" title="{_esc(label)}">{_esc(label)}</span>'
            f'<span class="track"><span class="fill" style="width:{width}%"></span></span>'
            f'<span class="n">{int(count)}</span></div>'
        )
    return "".join(out)


def _score_pill(value) -> str:
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        score = 0.0
    if not score:
        return '<span class="pill lo">-</span>'
    css = "hi" if score >= 55 else "mid" if score >= 35 else "lo"
    return f'<span class="pill {css}">{score:.0f}</span>'


def export_html_report(records: Iterable[dict], output_path: str,
                       meta: Optional[dict] = None, limit: int = 500,
                       mode: str = "people") -> str:
    """Write a self-contained, offline HTML report with charts and a table."""
    records = list(records or [])
    meta = dict(meta or {})
    stats = summarize(records) if mode != "jobs" else {"total": len(records), "with_email": 0}
    title = meta.get("title") or "LinkedIn Scrape Report"
    generated = meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subtitle_bits = [f"{stats.get('total', len(records))} profiles", generated]
    if meta.get("mode"):
        subtitle_bits.insert(0, str(meta["mode"]).upper())
    if meta.get("run_id"):
        subtitle_bits.append(f"run {meta['run_id']}")

    cards = "".join([
        _card("Records", stats.get("total", len(records)), "unique profiles"),
        _card("With email", stats.get("with_email", 0),
              f"{stats.get('email_rate', 0)}% coverage"),
        _card("Open to work", stats.get("open_to_work", 0), "ready to move"),
        _card("Avg completeness", stats.get("avg_completeness", 0), "out of 100"),
        _card("Avg resume match", stats.get("avg_match", 0), "out of 100"),
    ])

    charts = "".join([
        f'<div class="panel"><h2>Top companies</h2>{_bars(stats.get("top_companies", []))}</div>',
        f'<div class="panel"><h2>Seniority</h2>{_bars(stats.get("seniority", []))}</div>',
        f'<div class="panel"><h2>Top skills</h2>{_bars(stats.get("top_skills", [])[:10])}</div>',
        f'<div class="panel"><h2>Countries</h2>{_bars(stats.get("top_countries", []))}</div>',
    ])

    head = ("<tr><th>Name</th><th>Title</th><th>Company</th><th>Location</th>"
            "<th>Seniority</th><th>Match</th><th>Email</th><th>Completeness</th></tr>")
    body_rows = []
    for record in records[:limit]:
        url = record.get("linkedin_url") or ""
        name = _esc(record.get("name") or "Unknown")
        name_html = f'<a href="{_esc(url)}" target="_blank" rel="noopener">{name}</a>' if url else name
        email = record.get("email") or ""
        email_html = _esc(email) if email else (
            '<span class="muted">inferred</span>' if record.get("email_status") == "inferred"
            else '<span class="muted">-</span>')
        body_rows.append(
            "<tr>"
            f"<td>{name_html}</td>"
            f"<td>{_esc(record.get('title'))}</td>"
            f"<td>{_esc(record.get('company'))}</td>"
            f"<td>{_esc(record.get('location'))}</td>"
            f"<td>{_esc(record.get('seniority_label') or record.get('seniority'))}</td>"
            f"<td>{_score_pill(record.get('match_score') or record.get('fit_score'))}</td>"
            f"<td>{email_html}</td>"
            f"<td>{_esc(record.get('completeness_score'))}</td>"
            "</tr>"
        )
    if not body_rows:
        body_rows.append('<tr><td colspan="8" class="muted">No records yet.</td></tr>')

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{_CSS}</style></head>
<body>
<header><h1>{_esc(title)}</h1><p>{_esc(" - ".join(subtitle_bits))}</p></header>
<main>
  <section class="cards">{cards}</section>
  <section class="grid">{charts}</section>
  <section class="panel">
    <h2>Profiles ({min(len(records), limit)} of {len(records)})</h2>
    <input id="q" type="search" placeholder="Filter by name, title, company, location, email...">
    <div style="overflow:auto;max-height:70vh">
      <table id="tbl"><thead>{head}</thead><tbody>{''.join(body_rows)}</tbody></table>
    </div>
  </section>
</main>
<footer>Generated by the LinkedIn Scraper power stack. Values marked "inferred"
are pattern guesses, not verified addresses.</footer>
<script>{_JS}</script>
</body></html>"""
    target = _ensure_parent(output_path)
    target.write_text(document, encoding="utf-8")
    return str(target)


def _card(label: str, value, sub: str) -> str:
    return (f'<div class="card"><div class="k">{_esc(label)}</div>'
            f'<div class="v">{_esc(value)}</div><div class="s">{_esc(sub)}</div></div>')


# ─────────────────────────────────────────────────────────────
#  Orchestration
# ─────────────────────────────────────────────────────────────

def export_all(records: Iterable[dict], base_path: str,
               formats: Iterable[str] = ("xlsx", "csv", "json", "jsonl", "md", "html"),
               mode: str = "people", meta: Optional[dict] = None) -> Dict[str, str]:
    """Write every requested format next to ``base_path``.

    The xlsx leg delegates to ``utils`` so the existing formatting (hyperlinks,
    summary sheet, frozen header) is preserved. Returns {format: path}.
    """
    records = list(records or [])
    base = Path(base_path)
    stem = base.with_suffix("")
    formats = [str(fmt).lower().lstrip(".") for fmt in formats or []]
    written: Dict[str, str] = {}

    if "xlsx" in formats:
        try:
            from utils import (export_candidates_to_excel, export_jobs_to_excel,
                               export_to_excel)
            if mode == "jobs":
                written["xlsx"] = export_jobs_to_excel(records, str(base))
            elif mode == "candidates":
                written["candidates_xlsx"] = export_candidates_to_excel(records, str(base))
            else:
                written["xlsx"] = export_to_excel(records, str(base))
        except Exception as exc:  # export must not kill a finished run
            written["xlsx_error"] = str(exc)

    if "csv" in formats:
        written["csv"] = export_csv(records, f"{stem}.csv", mode=mode)
    if "json" in formats:
        written["json"] = export_json(records, f"{stem}.json", meta=meta)
    if "jsonl" in formats:
        written["jsonl"] = export_jsonl(records, f"{stem}.jsonl")
    if "md" in formats or "markdown" in formats:
        written["md"] = export_markdown(records, f"{stem}.md", meta=meta, mode=mode)
    if "html" in formats:
        written["html"] = export_html_report(records, f"{stem}.html", meta=meta, mode=mode)
    return written
