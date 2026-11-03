"""
Behavioral tests for the v5.0 "power stack".

Run with the project venv:  .venv/Scripts/python.exe test_power_features.py

Pure unit tests - no browser, no network. Each test exercises a real code path
(URL grammar, enrichment, scoring, SQLite, exporters, MCP protocol, scraper
integration) rather than re-asserting implementation details.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from linkedin_scraper import LinkedInScraper
import linkedin_scraper as L

from enrichment import (
    clean_text, dedupe_key, department_of, enrich_record, fix_mojibake,
    infer_company_domain, infer_emails, merge_record, normalize_company,
    normalize_phone, normalize_skills, parse_name, profile_completeness,
    quality_flags, seniority_label, seniority_rank, split_location,
)
from exporters import (
    export_all, export_csv, export_html_report, export_json, export_jsonl,
    export_markdown, infer_mode, ordered_columns, summarize,
)
from mcp_server import MCPServer
from scoring import (
    ICP, keyword_weights, match_tier, rank_records, score_fit, score_pool,
    score_record, tokenize,
)
from search_filters import (
    PeopleFilters, JobFilters, build_company_search_url, build_jobs_search_url,
    build_keywords, build_people_search_url, canonical_profile_url,
    classify_url, geo_id_for, profile_public_id, resolve_geo,
)
from storage import EventLog, Store


async def _noop(*args, **kwargs):
    pass


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="li_power_"))


# ─────────────────────────────────────────────────────────────
#  SEARCH FILTERS
# ─────────────────────────────────────────────────────────────

def test_people_url_encodes_json_facets():
    url = build_people_search_url(PeopleFilters(
        keywords='"Recruiter" "Walmart"',
        geo_ids=["103644278"],
        network=["second"],
        current_company_ids=["1441"],
        past_company_ids=["1035"],
        school_ids=["12345"],
        industry_ids=["96"],
        seniority=["mid_senior", "executive"],
    ))
    # JSON list facets must be percent-encoded exactly like LinkedIn writes them.
    assert "geoUrn=%5B%22103644278%22%5D" in url, url
    assert "network=%5B%22S%22%5D" in url, url
    assert "currentCompany=%5B%221441%22%5D" in url, url
    assert "pastCompany=%5B%221035%22%5D" in url, url
    assert "schoolFilter=%5B%2212345%22%5D" in url, url
    assert "industry=%5B%2296%22%5D" in url, url
    assert "seniority=%5B%224%22%2C%226%22%5D" in url, url
    assert "origin=FACETED_SEARCH" in url, url


def test_people_url_omits_empty_facets():
    url = build_people_search_url(PeopleFilters(keywords="Recruiter"))
    assert "geoUrn=" not in url and "network=" not in url
    assert "origin=GLOBAL_SEARCH_HEADER" in url, url


def test_people_keywords_boolean_or_and_exclude():
    keywords = build_keywords(["Recruiter", "Talent Acquisition"],
                              boolean_or=True, exclude=["intern"])
    assert keywords == '"Recruiter" OR "Talent Acquisition" NOT "intern"'
    # Default is implicit AND, and a quoted term is not double-quoted.
    assert build_keywords(["Recruiter", "AT&T"]) == '"Recruiter" "AT&T"'
    assert build_keywords(['"Python"']) == '"Python"'


def test_people_invalid_values_warn_and_drop():
    filters = PeopleFilters(keywords="x", network=["nope"], seniority=["99"],
                            current_company_ids=["Google"])
    build_people_search_url(filters)
    assert any("network" in warning for warning in filters.warnings)
    assert any("seniority" in warning for warning in filters.warnings)
    assert any("current_company" in warning for warning in filters.warnings)
    # The bad company name is not emitted as a facet.
    assert "currentCompany" not in build_people_search_url(PeopleFilters(
        keywords="x", current_company_ids=["Google"]))


def test_geo_resolution():
    assert resolve_geo("United States") == (["103644278"], None)
    assert resolve_geo("uk") == (["101165590"], None)
    assert resolve_geo("geo:123456") == (["123456"], None)
    assert resolve_geo("103644278") == (["103644278"], None)
    ids, warning = resolve_geo("Atlantis")
    assert ids == [] and "unknown location" in (warning or "")
    assert geo_id_for("Canada") == "101174742"


def test_page_param_and_origin_switch():
    first = build_people_search_url(PeopleFilters(keywords="x", page=1))
    third = build_people_search_url(PeopleFilters(keywords="x", page=3))
    assert "page=" not in first
    assert "page=3" in third


def test_jobs_url_full_facets():
    url = build_jobs_search_url(JobFilters(
        keywords="data engineer",
        location="United States",
        geo_id="103644278",
        date_posted="past_24_hours",
        experience_levels=["mid_senior"],
        job_types=["full_time", "contract"],
        workplace=["remote"],
        easy_apply=True,
        company_ids=["1441"],
        sort_by="date",
        start=25,
    ))
    assert "keywords=data%20engineer" in url
    assert "geoId=103644278" in url
    assert "f_TPR=r86400" in url
    assert "f_E=4" in url
    assert "f_JT=F%2CC" in url
    assert "f_WT=2" in url
    assert "f_EA=true" in url
    assert "f_C=1441" in url
    assert "sortBy=DD" in url
    assert "start=25" in url


def test_jobs_date_and_sort_validation():
    filters = JobFilters(keywords="x", date_posted="yesterday", sort_by="whatever")
    build_jobs_search_url(filters)
    assert filters.date_posted == ""
    assert filters.sort_by == ""
    assert len(filters.warnings) >= 2


def test_jobs_page_start():
    filters = JobFilters(keywords="x")
    assert filters.page_start(1) == 0
    assert filters.page_start(3) == 50


def test_company_search_url():
    url = build_company_search_url("Walmart")
    assert url.startswith("https://www.linkedin.com/search/results/companies/?")
    assert "keywords=Walmart" in url


def test_canonical_profile_url():
    cases = {
        "https://www.linkedin.com/in/jane-doe": "https://www.linkedin.com/in/jane-doe",
        "https://uk.linkedin.com/in/Jane-Doe/?trk=hi": "https://www.linkedin.com/in/jane-doe",
        "https://www.linkedin.com/in/jane-doe/details/experience/": "https://www.linkedin.com/in/jane-doe",
        "https://www.linkedin.com/jobs/view/123": "",
    }
    for raw, expected in cases.items():
        assert canonical_profile_url(raw) == expected, (raw, canonical_profile_url(raw))
    assert profile_public_id("https://www.linkedin.com/in/jane-doe/") == "jane-doe"
    assert profile_public_id("https://www.linkedin.com/feed/") == ""


def test_classify_url():
    assert classify_url("https://www.linkedin.com/in/jane")["kind"] == "profile"
    assert classify_url("https://www.linkedin.com/company/google/")["kind"] == "company"
    assert classify_url("https://www.linkedin.com/jobs/view/123456")["kind"] == "job"
    assert classify_url("https://www.linkedin.com/school/nyu/")["kind"] == "school"
    assert classify_url("https://example.com")["kind"] == "unknown"


def test_ampersand_company_name_is_encoded():
    url = build_people_search_url(PeopleFilters(
        keywords=build_keywords(["Recruiter", "AT&T"]), geo_ids=["103644278"]))
    assert "%22AT%26T%22" in url
    assert "AT&T" not in url


# ─────────────────────────────────────────────────────────────
#  ENRICHMENT
# ─────────────────────────────────────────────────────────────

def test_parse_name_variants():
    assert parse_name("Jane Doe")["first"] == "Jane"
    assert parse_name("Jane Doe")["last"] == "Doe"
    assert parse_name("Dr. Jane Q. Doe, PhD")["middle"] == "Q."
    assert parse_name("Dr. Jane Q. Doe, PhD")["suffix"] == "PhD"
    assert parse_name("Doe, Jane")["first"] == "Jane"
    assert parse_name("Ludwig van Beethoven")["last"] == "van Beethoven"
    assert parse_name("Cher")["first"] == "Cher"
    # Empty / junk must never raise.
    assert parse_name("")["first"] == ""
    assert parse_name(None)["display"] == ""


def test_clean_text_and_mojibake():
    assert clean_text("  Jane\u00a0 Doe  ") == "Jane Doe"
    assert clean_text("line1\n\n\nline2", keep_newlines=True) == "line1\n\nline2"
    assert fix_mojibake("Caf\u00c3\u00a9") == "Café"
    assert fix_mojibake("â€¢ bullet") == "• bullet"
    # Genuine non-mojibake text is untouched.
    assert fix_mojibake("Hello world") == "Hello world"


def test_split_location_variants():
    bentonville = split_location("Bentonville, Arkansas, United States")
    assert bentonville["city"] == "Bentonville"
    assert bentonville["region_code"] == "AR"
    assert bentonville["country_code"] == "US"

    ny = split_location("New York, NY")
    assert ny["city"] == "New York" and ny["region_code"] == "NY"
    assert ny["country_code"] == "US"

    london = split_location("London, England, United Kingdom")
    assert london["city"] == "London"
    assert london["country_code"] == "GB"

    metro = split_location("Greater New York City Area")
    assert metro["city"] == "New York City" and metro["is_metro"] is True

    remote = split_location("Remote")
    assert remote["is_remote"] is True and remote["city"] == ""

    india = split_location("India")
    assert india["country_code"] == "IN" and india["city"] == ""

    assert split_location("")["city"] == ""


def test_normalize_phone():
    assert normalize_phone("(479) 555-0123") == "+14795550123"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert normalize_phone("555-0123") == ""          # too short / implausible
    assert normalize_phone("") == ""
    assert normalize_phone("12345") == ""


def test_company_normalization_and_domains():
    assert normalize_company("Google Inc.") == "Google"
    assert normalize_company("Acme, LLC") == "Acme"
    assert normalize_company("Foo Limited") == "Foo"
    assert infer_company_domain({"company": "Walmart"})[0] == "walmart.com"
    domain, source, confidence = infer_company_domain(
        {"company": "Unknown Co", "websites": "https://careers.unknownco.com/jobs"})
    assert domain == "careers.unknownco.com" and source == "website" and confidence > 0.5
    # Free-mail addresses never become a company domain.
    assert infer_company_domain({"company": "X", "email": "jane@gmail.com"})[0] == ""


def test_infer_emails_are_labelled():
    candidates = infer_emails("Jane", "Doe", "walmart.com")
    assert candidates[0]["email"] == "jane.doe@walmart.com"
    assert all(candidate["verified"] is False for candidate in candidates)
    assert all(0 < candidate["confidence"] <= 1 for candidate in candidates)
    assert infer_emails("", "", "walmart.com") == []
    assert infer_emails("Jane", "Doe", "") == []


def test_skills_normalization():
    skills, categories = normalize_skills("js, ML, ATS, Talent Sourcing, js")
    assert "JavaScript" in skills and "Machine Learning" in skills
    assert skills.count("JavaScript") == 1, "duplicates must collapse"
    assert "People / HR" in categories
    assert normalize_skills("") == ("", "")


def test_seniority_and_department():
    assert seniority_rank("Chief Executive Officer") == 7
    assert seniority_rank("VP of Engineering") == 6
    assert seniority_rank("Director of Product") == 5
    assert seniority_rank("Engineering Manager") == 4
    assert seniority_rank("Senior Recruiter") == 3
    assert seniority_rank("Software Engineer") == 2
    assert seniority_rank("Marketing Intern") == 1
    assert seniority_rank("") == 0
    assert seniority_label(7) == "C-Suite / Founder"

    assert department_of("Senior Talent Acquisition Partner") == "People / HR"
    assert department_of("Software Engineer") == "Engineering"
    assert department_of("Data Scientist") == "Data / AI"
    assert department_of("Product Manager") == "Product"
    # "ui" inside "acquisition" must not reroute to Design (word-boundary fix).
    assert department_of("Talent Acquisition Partner") == "People / HR"


def test_completeness_flags_and_dedupe():
    empty = enrich_record({})
    assert empty["completeness_score"] == 0
    assert "no_email" in json.loads(empty["quality_flags"])

    rich = enrich_record({
        "name": "Jane Doe", "title": "Recruiter", "company": "Walmart",
        "location": "Bentonville, Arkansas, United States", "about": "Hiring",
        "skills": "ATS, Sourcing", "experience": "Recruiter", "education": "BS",
        "email": "jane@walmart.com", "photo_url": "https://x/y.jpg",
        "connections": "500+", "followers": "1,000",
    })
    assert rich["completeness_score"] > 80
    assert rich["email_status"] == "scraped"
    assert rich["email_confidence"] == 1.0
    assert rich["country_code"] == "US"

    assert dedupe_key({"public_id": "Jane-Doe"}) == "jane-doe"
    assert dedupe_key({"linkedin_url": "https://www.linkedin.com/in/bob"}) == "bob"
    assert dedupe_key({"name": "Jane Doe", "company": "Walmart Inc."}) == "janedoe@walmart"
    assert dedupe_key({}).startswith("hash:")


def test_merge_record_keeps_best_values():
    existing = {"name": "Jane Doe", "title": "Recruiter", "skills": "ATS, Sourcing",
                "email": "jane@old.com", "seen_count": 2, "first_seen": "2026-01-01"}
    incoming = {"name": "Jane Doe", "title": "", "skills": "Sourcing, Boolean",
                "email": "jane@new.com", "seen_count": 9, "first_seen": "2026-02-02"}
    merged = merge_record(existing, incoming)
    assert merged["title"] == "Recruiter", "blank incoming must not erase data"
    assert merged["email"] == "jane@new.com", "newer non-empty wins"
    assert merged["skills"] == "ATS, Sourcing, Boolean"
    assert merged["seen_count"] == 2 and merged["first_seen"] == "2026-01-01"


def test_enrich_record_is_additive():
    raw = {"name": "  JANE DOE ", "title": "Senior Recruiter", "company": "Walmart Inc.",
           "location": "Bentonville, Arkansas, United States", "skills": "js, ML",
           "phone": "(479) 555-0123 ext. 7", "linkedin_url": "https://www.linkedin.com/in/jane-doe?trk=x"}
    enriched = enrich_record(raw)
    assert enriched is not raw, "must return a copy"
    assert "JANE DOE" not in enriched["name"] and enriched["name"] == "Jane Doe"
    assert enriched["public_id"] == "jane-doe"    # derived from the profile URL
    assert enriched["dedupe_key"] == "jane-doe"
    assert enriched["phone_e164"] == "+14795550123"
    assert enriched["phone_ext"] == "7"
    assert enriched["company_clean"] == "Walmart"
    assert enriched["seniority_rank"] == 3
    assert enriched["department"] == "People / HR"
    # No scraped address, but Walmart has a known domain -> clearly-labelled
    # inferred candidates, never a bare "email".
    assert enriched["email_status"] == "inferred"
    assert enriched["email"] == ""
    assert json.loads(enriched["email_candidates"])[0]["email"].endswith("@walmart.com")


def test_quality_flags_inferred_email():
    record = enrich_record({"name": "Jane Doe", "title": "Recruiter",
                            "company": "Walmart", "location": "Bentonville, AR"})
    flags = json.loads(record["quality_flags"])
    assert "email_inferred" in flags and "no_email" in flags


# ─────────────────────────────────────────────────────────────
#  SCORING
# ─────────────────────────────────────────────────────────────

def test_tokenize_and_weights():
    tokens = tokenize("Python, SQL and C++ at scale")
    assert "python" in tokens and "sql" in tokens and "c++" in tokens
    assert "and" not in tokens and "at" not in tokens
    weights = keyword_weights("recruiter recruiter python machine learning")
    assert weights["recruiter"] > weights["python"], weights
    assert any(" " in term for term in weights), "multi-word skill phrases should survive"
    assert keyword_weights("") == {}


def test_score_record_bounds_and_explanation():
    weights = keyword_weights("recruiter sourcing python talent acquisition")
    record = {"name": "Jane", "title": "Senior Recruiter", "company": "Walmart",
              "skills_normalized": "Talent Sourcing, Python",
              "headline": "Recruiter at Walmart", "about": "Full-cycle recruiting",
              "experience": "Recruiter"}
    scored = score_record(record, weights)
    assert 0 <= scored["match_score"] <= 100
    assert scored["match_score"] > 40, scored
    assert scored["match_tier"] in ("moderate", "strong", "excellent")
    assert "title" in scored["match_reasons"] or "skills" in scored["match_reasons"]
    assert scored["matched_keywords"]

    empty = score_record({"name": "Nobody"}, weights)
    assert empty["match_score"] == 0 and empty["match_tier"] == "none"
    assert score_record({"name": "x"}, {})["match_tier"] == "none"


def test_java_does_not_match_javascript():
    weights = {"java": 1.0}
    java_engineer = score_record({"title": "Java Developer"}, weights)
    js_engineer = score_record({"title": "JavaScript Developer"}, weights)
    assert java_engineer["match_score"] > js_engineer["match_score"] == 0.0


def test_match_tier_thresholds():
    assert match_tier(90) == "excellent"
    assert match_tier(60) == "strong"
    assert match_tier(40) == "moderate"
    assert match_tier(5) == "weak"
    assert match_tier(0) == "none"


def test_icp_hard_filters():
    icp = ICP(exclude_companies=["Amazon"], must_have_email=False)
    excluded = score_fit({"company": "Amazon", "title": "Recruiter"}, icp)
    assert excluded["fit_score"] == 0 and excluded["fit_tier"] == "excluded"

    contact_icp = ICP(must_have_email=True)
    no_email = score_fit({"company": "Walmart"}, contact_icp)
    assert no_email["fit_tier"] == "no_contact"

    open_icp = ICP(open_to_work_only=True)
    closed = score_fit({"open_to_work": False}, open_icp)
    assert closed["fit_tier"] == "not_open"

    incomplete = score_fit({"completeness_score": 10}, ICP(min_completeness=50))
    assert incomplete["fit_tier"] == "incomplete"


def test_icp_scoring_and_ranking():
    icp = ICP(titles=["Recruiting"], seniorities=["director"],
              locations=["United States"], skills=["sourcing"])
    good = score_fit({"title": "Director of Recruiting", "seniority_label": "Director",
                      "location": "Bentonville, Arkansas, United States",
                      "skills": "Talent Sourcing", "email": "a@b.com",
                      "open_to_work": True}, icp)
    bad = score_fit({"title": "Accountant", "seniority_label": "Individual Contributor",
                     "location": "Berlin, Germany", "skills": "Tax"}, icp)
    assert good["fit_score"] > bad["fit_score"]
    assert "title matches" in good["fit_reasons"]

    ranked = rank_records([{"match_score": 10}, {"match_score": 90}, {}], by="match_score")
    assert [item.get("match_score") for item in ranked] == [90, 10, None]

    filtered = score_pool([{"match_score": 10}, {"match_score": 90}],
                          weights={"x": 1}, min_score=50)
    assert all(item["match_score"] >= 50 for item in filtered)


# ─────────────────────────────────────────────────────────────
#  STORAGE
# ─────────────────────────────────────────────────────────────

def _record(name="Jane Doe", url="https://www.linkedin.com/in/jane-doe", **extra):
    record = {
        "name": name, "title": "Senior Recruiter", "company": "Walmart",
        "location": "Bentonville, Arkansas, United States",
        "public_id": url.rsplit("/", 1)[-1], "linkedin_url": url,
        "skills": "Talent Sourcing, ATS, Recruiting",
        "email": "jane@walmart.com", "open_to_work": True, "match_score": 70,
    }
    record.update(extra)
    return enrich_record(record)


def test_store_insert_update_unchanged():
    store = Store(str(_tmpdir() / "t.db"))
    try:
        run = store.start_run("people")
        first = store.upsert_record(_record(), run)
        assert first["status"] == "inserted"
        assert store.upsert_record(_record(), run)["status"] == "unchanged"

        changed = store.upsert_record(_record(title="Lead Recruiter"), run)
        assert changed["status"] == "updated"
        stored = store.get_record("jane-doe")
        assert stored["title"] == "Lead Recruiter"
        assert stored["seen_count"] == 3
        assert store.count() == 1
    finally:
        store.close()


def test_store_cross_run_dedupe_and_lookup():
    store = Store(str(_tmpdir() / "t.db"))
    try:
        run1 = store.start_run("people")
        store.upsert_record(_record(), run1)
        store.finish_run(run1, {"records": 1})

        run2 = store.start_run("people")
        assert store.record_exists("https://www.linkedin.com/in/jane-doe?trk=x")
        assert store.record_exists("jane-doe")
        assert not store.record_exists("https://www.linkedin.com/in/unknown")

        result = store.upsert_record(_record(email="jane.doe@walmart.com"), run2)
        assert result["status"] == "updated"
        stored = store.get_record("https://www.linkedin.com/in/jane-doe")
        assert stored["email"] == "jane.doe@walmart.com"
        assert stored["first_run"] == run1
        assert stored["last_run"] == run2
    finally:
        store.close()


def test_store_filters_and_stats():
    store = Store(str(_tmpdir() / "t.db"))
    try:
        run = store.start_run("people")
        store.upsert_record(_record(), run)
        store.upsert_record(_record(
            name="John Smith", url="https://www.linkedin.com/in/john-smith",
            title="Software Engineer", company="Google",
            location="London, England, United Kingdom", email="",
            open_to_work=False, match_score=20), run)

        assert len(store.load_records(company="walmart")) == 1
        assert len(store.load_records(query="recruiter")) == 1
        assert len(store.load_records(has_email=True)) == 1
        assert len(store.load_records(min_score=50)) == 1
        assert len(store.load_records(country="United Kingdom")) == 1

        stats = store.stats()
        assert stats["total_records"] == 2
        assert stats["with_email"] == 1
        assert stats["top_companies"][0]["company"] in ("Walmart", "Google")
        assert stats["runs"] == 1
    finally:
        store.close()


def test_store_search_checkpoints_and_events():
    store = Store(str(_tmpdir() / "t.db"))
    try:
        run = store.start_run("people")
        assert store.is_search_done("people::Walmart::Recruiter") is False
        store.mark_search_done("people::Walmart::Recruiter", run)
        assert store.is_search_done("people::Walmart::Recruiter") is True
        assert store.completed_keys() == ["people::Walmart::Recruiter"]
        assert store.clear_search_keys() == 1

        store.log_event("test", "hello", run_id=run)
        events = store.events(run_id=run)
        assert events and events[0]["kind"] == "test"
    finally:
        store.close()


def test_store_import_progress_and_prune():
    directory = _tmpdir()
    progress = directory / "progress.json"
    progress.write_text(json.dumps({
        "results": [_record()],
        "completed_keys": ["people::Walmart::Recruiter"],
    }), encoding="utf-8")

    store = Store(str(directory / "t.db"))
    try:
        imported = store.import_progress(str(progress))
        assert imported == 1
        assert store.count() == 1
        assert store.is_search_done("people::Walmart::Recruiter")
        assert store.prune(days=0) == 1
        assert store.count() == 0
    finally:
        store.close()


def test_event_log_writes_jsonl():
    path = _tmpdir() / "runs" / "run.jsonl"
    log = EventLog(str(path))
    log.emit("run_start", mode="people")
    log.emit("profile", name="Jane")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["kind"] for line in lines] == ["run_start", "profile"]
    assert lines[0]["mode"] == "people"


# ─────────────────────────────────────────────────────────────
#  EXPORTERS
# ─────────────────────────────────────────────────────────────

def _sample_records():
    return [
        _record(),
        _record(name="John Smith", url="https://www.linkedin.com/in/john-smith",
                title="Software Engineer", company="Google",
                location="London, England, United Kingdom", email=""),
    ]


def test_export_csv_has_bom_and_people_columns():
    path = _tmpdir() / "out.csv"
    written = export_csv(_sample_records(), str(path))
    raw = Path(written).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "Excel needs the UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert "Full Name" in header and "LinkedIn URL" in header
    assert "Seniority" in header and "Resume Match" in header
    assert "Jane Doe" in text and "John Smith" in text


def test_export_json_and_jsonl():
    directory = _tmpdir()
    records = _sample_records()
    json_path = export_json(records, str(directory / "out.json"), meta={"mode": "people"})
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert payload["count"] == 2 and payload["mode"] == "people"
    assert payload["records"][0]["name"] == "Jane Doe"

    jsonl_path = export_jsonl(records, str(directory / "out.jsonl"))
    lines = Path(jsonl_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["name"] == "John Smith"


def test_export_markdown():
    path = export_markdown(_sample_records(), str(_tmpdir() / "out.md"),
                           meta={"title": "Pool"})
    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("# Pool")
    assert "## Snapshot" in text
    assert "Jane Doe" in text
    assert "| Company | Profiles |" in text


def test_html_report_escapes_and_renders_charts():
    record = _record(name="<script>alert(1)</script>", title="Recruiter")
    path = export_html_report([record], str(_tmpdir() / "report.html"),
                              meta={"title": "Report"})
    text = Path(path).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert 'class="bar"' in text            # chart bars rendered
    assert "Top companies" in text and "Seniority" in text
    assert 'id="q"' in text                 # live filter box
    assert "inferred" in text               # disclaimer for guessed emails


def test_summarize_and_infer_mode():
    stats = summarize(_sample_records())
    assert stats["total"] == 2
    assert stats["with_email"] == 1
    assert stats["email_rate"] == 50.0
    assert stats["top_skills"], "skills should aggregate"
    assert infer_mode(_sample_records()) == "people"
    assert infer_mode([{"title": "x", "search_keyword": "python"}]) == "jobs"
    assert infer_mode([]) == "people"


def test_export_all_formats():
    directory = _tmpdir()
    written = export_all(_sample_records(), str(directory / "results.xlsx"),
                         formats=["csv", "json", "jsonl", "md", "html"],
                         mode="people", meta={"title": "All"})
    for fmt in ("csv", "json", "jsonl", "md", "html"):
        assert fmt in written and Path(written[fmt]).exists(), fmt
    assert all(Path(path).stat().st_size > 0 for path in written.values())


def test_ordered_columns_includes_extras():
    columns = ordered_columns([{"name": "x", "custom_field": "y"}], mode="people")
    keys = [key for key, _ in columns]
    assert "name" in keys and "custom_field" in keys


# ─────────────────────────────────────────────────────────────
#  MCP SERVER
# ─────────────────────────────────────────────────────────────

def _mcp_with_store():
    store = Store(":memory:")
    run = store.start_run("people")
    store.upsert_record(_record(), run)
    store.upsert_record(_record(name="John Smith", url="https://www.linkedin.com/in/john-smith",
                                title="Engineer", company="Google", email=""), run)
    return MCPServer(":memory:", store=store), store


def test_mcp_initialize_and_lists():
    server, store = _mcp_with_store()
    try:
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2024-11-05"}})
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert init["result"]["capabilities"]["tools"] is not None
        assert init["result"]["serverInfo"]["version"] == "5.0.0"

        assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in tools["result"]["tools"]]
        assert {"list_profiles", "get_profile", "get_stats", "export_records",
                "build_search_url"} <= set(names)
        assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}
    finally:
        store.close()


def test_mcp_tools_return_data():
    server, store = _mcp_with_store()
    try:
        listing = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "list_profiles",
                                            "arguments": {"limit": 1}}})
        payload = json.loads(listing["result"]["content"][0]["text"])
        assert payload["count"] == 1 and payload["total_in_store"] == 2

        profile = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "get_profile",
                                            "arguments": {"id_or_url": "jane-doe"}}})
        record = json.loads(profile["result"]["content"][0]["text"])
        assert record["name"] == "Jane Doe"

        stats = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "get_stats", "arguments": {}}})
        assert json.loads(stats["result"]["content"][0]["text"])["total_records"] == 2

        search = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "search_profiles",
                                           "arguments": {"keywords": ["recruiter"], "limit": 5}}})
        payload = json.loads(search["result"]["content"][0]["text"])
        assert payload["profiles"][0]["name"] == "Jane Doe"
    finally:
        store.close()


def test_mcp_export_and_errors():
    server, store = _mcp_with_store()
    try:
        path = _tmpdir() / "mcp.csv"
        exported = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "export_records",
                                             "arguments": {"format": "csv", "path": str(path)}}})
        result = json.loads(exported["result"]["content"][0]["text"])
        assert Path(result["path"]).exists() and result["count"] == 2

        unknown = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "does_not_exist", "arguments": {}}})
        assert unknown["result"]["isError"] is True

        missing = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "get_profile", "arguments": {}}})
        assert missing["result"]["isError"] is True

        bad_method = server.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})
        assert bad_method["error"]["code"] == -32601
    finally:
        store.close()


def test_mcp_build_search_url_tool():
    server, store = _mcp_with_store()
    try:
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "build_search_url",
                                             "arguments": {"kind": "people",
                                                           "keywords": "Recruiter",
                                                           "location": "United States",
                                                           "network": ["second"]}}})
        payload = json.loads(response["result"]["content"][0]["text"])
        assert "geoUrn=%5B%22103644278%22%5D" in payload["url"]
        assert "network=%5B%22S%22%5D" in payload["url"]

        jobs = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "build_search_url",
                                         "arguments": {"kind": "jobs",
                                                       "keywords": "python",
                                                       "workplace": ["remote"]}}})
        assert "f_WT=2" in json.loads(jobs["result"]["content"][0]["text"])["url"]
    finally:
        store.close()


# ─────────────────────────────────────────────────────────────
#  SCRAPER INTEGRATION
# ─────────────────────────────────────────────────────────────

def test_register_search_increments_without_recursion():
    """Regression: _register_search used to call itself forever, so the daily
    cap never advanced and every search looked like an error."""
    scraper = LinkedInScraper(Config())
    assert scraper.daily_searches == 0
    scraper._register_search()
    scraper._register_search()
    assert scraper.daily_searches == 2
    assert scraper.telemetry["daily_searches"] == 2


def test_post_filters():
    cfg = Config()
    scorer = LinkedInScraper(cfg)
    profile = {"title": "Recruiter", "headline": "TA", "open_to_work": True,
               "is_hiring": True, "completeness_score": 80, "match_score": 60}
    assert scorer._passes_filter(profile) is True

    cfg.OPEN_TO_WORK_ONLY = True
    assert scorer._passes_filter({**profile, "open_to_work": False}) is False

    cfg.OPEN_TO_WORK_ONLY = False
    cfg.HIRING_ONLY = True
    assert scorer._passes_filter({**profile, "is_hiring": False}) is False

    cfg.HIRING_ONLY = False
    cfg.MIN_COMPLETENESS = 90
    assert scorer._passes_filter({**profile, "completeness_score": 40}) is False

    cfg.MIN_COMPLETENESS = 0
    cfg.MATCH_MIN_SCORE = 75
    assert scorer._passes_filter({**profile, "match_score": 10}) is False

    cfg.MATCH_MIN_SCORE = 0
    cfg.FILTER_KEYWORDS = ["engineer"]
    assert scorer._passes_filter(profile) is False
    assert scorer._passes_filter({**profile, "title": "Engineer"}) is True


def test_enrichment_hook_in_scrape_profile():
    L.human_delay = _noop
    L.random_scroll = _noop
    scraper = LinkedInScraper(Config())
    scraper.page = _StaticPage(_PROFILE_FIXTURE)
    profile = asyncio.run(scraper.scrape_profile(
        "https://www.linkedin.com/in/jane-doe", search_company="Walmart", job_title="Recruiter"))
    assert profile is not None
    for key in ("city", "country_code", "seniority_rank", "seniority_label",
                "department", "email_status", "completeness_score", "dedupe_key",
                "skills_normalized", "quality_flags"):
        assert key in profile, f"missing enriched field {key}"
    assert profile["city"] == "Bentonville"
    assert profile["country_code"] == "US"
    assert profile["dedupe_key"] == "jane-doe"
    assert profile["email_status"] in ("scraped", "inferred", "none")


def test_search_people_uses_faceted_url():
    cfg = Config()
    cfg.NETWORK_FILTER = ["F"]
    cfg.SENIORITY_FILTER = ["director"]
    cfg.GEO_URN = "103644278"
    scraper = LinkedInScraper(cfg)
    scraper.page = _CapturePage()
    L.human_delay = _noop
    L.random_scroll = _noop
    urls = asyncio.run(scraper.search_people("Walmart", "Recruiter"))
    assert urls == []
    assert scraper.page.visited, "no search URL was visited"
    url = scraper.page.visited[0]
    assert "geoUrn=%5B%22103644278%22%5D" in url
    assert "network=%5B%22F%22%5D" in url
    assert "seniority=%5B%225%22%5D" in url
    assert "keywords=%22Recruiter%22%20%22Walmart%22" in url


def test_job_filters_from_config():
    cfg = Config()
    cfg.JOB_DATE_POSTED = "r604800"
    cfg.JOB_EXPERIENCE_LEVEL = "4"
    cfg.JOB_REMOTE_FILTER = "2"
    scraper = LinkedInScraper(cfg)
    filters = scraper._build_job_filters("python developer", start=25)
    url = build_jobs_search_url(filters)
    assert "f_TPR=r604800" in url and "f_E=4" in url and "f_WT=2" in url
    assert "start=25" in url


def test_cap_reached():
    cfg = Config()
    cfg.MAX_PROFILES_PER_RUN = 2
    scraper = LinkedInScraper(cfg)
    assert scraper._cap_reached() is False
    scraper.profiles_this_run = 2
    assert scraper._cap_reached() is True
    cfg.MAX_PROFILES_PER_RUN = 0
    assert scraper._cap_reached() is False


def test_finish_power_stack_persists_summary():
    scraper = LinkedInScraper(Config())
    scraper.store = Store(str(_tmpdir() / "t.db"))
    try:
        scraper.run_id = scraper.store.start_run("people")
        scraper.results = [_record()]
        scraper.new_records = 1
        summary = scraper._finish_power_stack("people", recovered=0, written={})
        assert summary["records"] == 1 and summary["new_in_db"] == 1
        runs = scraper.store.load_runs()
        assert runs[0]["status"] == "finished"
        assert runs[0]["stats"]["records"] == 1
    finally:
        scraper.store.close()


class _StaticPage:
    def __init__(self, html):
        self.html = html
        self.url = "https://www.linkedin.com/in/jane-doe"

    async def goto(self, *args, **kwargs):
        pass

    async def content(self):
        return self.html

    async def evaluate(self, *args, **kwargs):
        pass


class _CapturePage:
    def __init__(self):
        self.visited = []
        self.url = "https://www.linkedin.com/search/results/people/"

    async def goto(self, url, *args, **kwargs):
        self.visited.append(url)

    async def content(self):
        return "<html><body>No results found</body></html>"

    async def evaluate(self, *args, **kwargs):
        pass

    async def eval_on_selector_all(self, *args, **kwargs):
        return []

    async def wait_for_selector(self, *args, **kwargs):
        return None


_PROFILE_FIXTURE = """<html><body>
<h1 class="text-heading-xlarge">Jane Doe</h1>
<div class="text-body-medium break-words">Senior Talent Acquisition Partner at Walmart</div>
<span class="text-body-small inline t-black--light break-words">Bentonville, Arkansas, United States</span>
<section id="experience">
  <div class="pv-position-entity">
    <h3 class="t-bold">Senior Talent Acquisition Partner</h3>
    <div class="pv-entity__secondary-title">Walmart</div>
    <div class="pv-entity__date-range"><span>Jan 2021</span><span>Present</span></div>
  </div>
</section>
<section id="skills">
  <div class="pv-skill-category-entity__name-text">Talent Sourcing</div>
  <div class="pv-skill-category-entity__name-text">ATS</div>
</section>
</body></html>
"""


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────

def test_cli_parser_commands():
    import cli

    parser = cli.build_parser()
    args = parser.parse_args(["run", "--mode", "people", "--companies", "Google,Microsoft",
                              "--titles", "Recruiter", "--formats", "csv,json"])
    assert args.command == "run" and args.mode == "people"
    assert cli._split_csv(args.companies) == ["Google", "Microsoft"]

    export_args = parser.parse_args(["export", "--format", "all", "--out", "x.xlsx"])
    assert export_args.command == "export" and export_args.format == "all"

    db_args = parser.parse_args(["db", "prune", "--days", "30"])
    assert db_args.db_command == "prune" and db_args.days == 30

    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["mcp"]).command == "mcp"


def test_cli_doctor_runs():
    import cli

    checks = cli.run_doctor("output/does-not-exist.db")
    names = {check["name"] for check in checks}
    assert {"python", "dep:playwright", "config", "database"} <= names
    assert all("detail" in check for check in checks)


def test_cli_export_from_progress(tmpdir=None):
    import cli
    from argparse import Namespace

    directory = _tmpdir()
    progress = directory / "progress.json"
    progress.write_text(json.dumps({"results": _sample_records(), "completed_keys": []}),
                        encoding="utf-8")
    out = directory / "export.xlsx"
    args = Namespace(format="csv,json", out=str(out), db=str(directory / "none.db"),
                     source="progress", company="", query="", min_score=0.0,
                     limit=0, deep=False)
    assert cli.cmd_export(args) == 0
    assert (directory / "export.csv").exists()
    assert (directory / "export.json").exists()


# ─────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_people_url_encodes_json_facets,
        test_people_url_omits_empty_facets,
        test_people_keywords_boolean_or_and_exclude,
        test_people_invalid_values_warn_and_drop,
        test_geo_resolution,
        test_page_param_and_origin_switch,
        test_jobs_url_full_facets,
        test_jobs_date_and_sort_validation,
        test_jobs_page_start,
        test_company_search_url,
        test_canonical_profile_url,
        test_classify_url,
        test_ampersand_company_name_is_encoded,
        test_parse_name_variants,
        test_clean_text_and_mojibake,
        test_split_location_variants,
        test_normalize_phone,
        test_company_normalization_and_domains,
        test_infer_emails_are_labelled,
        test_skills_normalization,
        test_seniority_and_department,
        test_completeness_flags_and_dedupe,
        test_merge_record_keeps_best_values,
        test_enrich_record_is_additive,
        test_quality_flags_inferred_email,
        test_tokenize_and_weights,
        test_score_record_bounds_and_explanation,
        test_java_does_not_match_javascript,
        test_match_tier_thresholds,
        test_icp_hard_filters,
        test_icp_scoring_and_ranking,
        test_store_insert_update_unchanged,
        test_store_cross_run_dedupe_and_lookup,
        test_store_filters_and_stats,
        test_store_search_checkpoints_and_events,
        test_store_import_progress_and_prune,
        test_event_log_writes_jsonl,
        test_export_csv_has_bom_and_people_columns,
        test_export_json_and_jsonl,
        test_export_markdown,
        test_html_report_escapes_and_renders_charts,
        test_summarize_and_infer_mode,
        test_export_all_formats,
        test_ordered_columns_includes_extras,
        test_mcp_initialize_and_lists,
        test_mcp_tools_return_data,
        test_mcp_export_and_errors,
        test_mcp_build_search_url_tool,
        test_register_search_increments_without_recursion,
        test_post_filters,
        test_enrichment_hook_in_scrape_profile,
        test_search_people_uses_faceted_url,
        test_job_filters_from_config,
        test_cap_reached,
        test_finish_power_stack_persists_summary,
        test_cli_parser_commands,
        test_cli_doctor_runs,
        test_cli_export_from_progress,
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
    print(f"\n{len(tests) - failed}/{len(tests)} power-feature tests passed.")
    sys.exit(1 if failed else 0)
