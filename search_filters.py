"""LinkedIn search URL grammar + full faceted filters.

The original scraper built ``"<title>" "<company>"`` with a bare ``geoUrn``.
LinkedIn's search URLs accept a much richer set of facets (network degree,
current / past company, school, industry, seniority, job type, work type,
experience level, recency, sort).  Getting them right is the difference between
"search recruiters" and "search 2nd-degree mid-senior recruiters at Google who
went to NYU, in the US".

The grammar here follows what LinkedIn actually writes into its own URLs
(verified against a maintained MCP implementation and observed search URLs):

  people : keywords, geoUrn, network, currentCompany, pastCompany,
           schoolFilter, industry, seniority, page, origin
  jobs   : keywords, location, geoId, f_TPR, f_E, f_JT, f_WT, f_EA, f_C,
           sortBy, start
  company: keywords, page

Honest limits - documented instead of guessed:
  * ``geoUrn`` / ``currentCompany`` / ``pastCompany`` / ``schoolFilter`` /
    ``industry`` take LinkedIn *numeric IDs*, not names. ``GEO_IDS`` covers the
    country-level IDs most people need; anything else can be passed as
    ``geo:<id>``. Unknown place names are reported as warnings and pushed into
    the keyword string by the caller, because LinkedIn silently ignores a
    plain-text ``location=`` on people search and would return the whole world.
  * ``function`` is not part of the classic people-search URL, and "open to
    work"/"hiring" are applied client-side after scraping instead. Shipping a
    made-up facet would silently return unfiltered results - the worst
    possible failure for a sourcing tool.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple
from urllib.parse import quote, urlencode

PEOPLE_SEARCH_BASE = "https://www.linkedin.com/search/results/people/"
COMPANY_SEARCH_BASE = "https://www.linkedin.com/search/results/companies/"
JOBS_SEARCH_BASE = "https://www.linkedin.com/jobs/search/"

# Country-level geo IDs (LinkedIn "geoUrn"). These are the values LinkedIn
# itself puts in the URL when a country is picked in the search UI. City-level
# IDs are intentionally omitted - pass "geo:<id>" if you need one.
GEO_IDS = {
    "united states": "103644278",
    "usa": "103644278",
    "us": "103644278",
    "united kingdom": "101165590",
    "uk": "101165590",
    "england": "101165590",
    "canada": "101174742",
    "australia": "101452733",
    "india": "102713980",
    "germany": "101282230",
    "france": "105015875",
    "netherlands": "102890719",
    "singapore": "102454443",
    "united arab emirates": "104305776",
    "uae": "104305776",
    "brazil": "106057199",
    "spain": "105646813",
    "italy": "103350119",
    "ireland": "104738515",
    "switzerland": "106693272",
    "sweden": "105565249",
    "denmark": "104514075",
    "norway": "103819153",
    "finland": "100456013",
    "poland": "105072130",
    "israel": "101620260",
    "mexico": "103323778",
    "japan": "101355337",
    "hong kong": "103291313",
    "south africa": "104035573",
    "portugal": "100364837",
    "belgium": "100565514",
    "austria": "103883259",
    "turkey": "102105699",
    "argentina": "100446943",
    "chile": "104621616",
    "colombia": "100876405",
    "philippines": "103121230",
    "malaysia": "102470437",
    "indonesia": "102478259",
    "thailand": "102526465",
    "vietnam": "104195383",
    "pakistan": "101022442",
    "new zealand": "105490917",
    "czechia": "104369085",
    "czech republic": "104369085",
    "greece": "104677530",
}

# Human name -> LinkedIn code maps (both spellings accepted, raw codes pass).
NETWORK_TOKENS = ("F", "S", "O")  # 1st, 2nd, 3rd+
NETWORK_MAP = {
    "first": "F", "1st": "F", "f": "F",
    "second": "S", "2nd": "S", "s": "S",
    "third": "O", "3rd": "O", "o": "O", "3rd+": "O",
}

EXPERIENCE_LEVEL_MAP = {
    "internship": "1", "intern": "1",
    "entry": "2", "entry level": "2", "junior": "2",
    "associate": "3",
    "mid_senior": "4", "mid-senior": "4", "mid senior": "4", "senior": "4",
    "director": "5",
    "executive": "6", "c-level": "6", "cxo": "6",
}
JOB_TYPE_MAP = {
    "full_time": "F", "full-time": "F", "full time": "F",
    "part_time": "P", "part-time": "P", "part time": "P",
    "contract": "C", "temporary": "T", "volunteer": "V",
    "internship": "I", "intern": "I", "other": "O",
}
WORK_TYPE_MAP = {
    "on_site": "1", "on-site": "1", "onsite": "1",
    "remote": "2", "hybrid": "3",
}
DATE_POSTED_MAP = {
    "past_hour": "r3600", "past-hour": "r3600", "hour": "r3600",
    "past_24_hours": "r86400", "past-24h": "r86400", "day": "r86400",
    "past_week": "r604800", "past-week": "r604800", "week": "r604800",
    "past_month": "r2592000", "past-month": "r2592000", "month": "r2592000",
}
SORT_BY_MAP = {"date": "DD", "recent": "DD", "relevance": "R"}
# LinkedIn's "Seniority level" people facet uses the same codes as the job
# filter (1 internship .. 6 executive), so one map serves both.
SENIORITY_MAP = {
    "internship": "1", "intern": "1",
    "entry": "2", "entry level": "2", "junior": "2",
    "associate": "3",
    "mid_senior": "4", "mid-senior": "4", "mid senior": "4", "senior": "4",
    "director": "5",
    "executive": "6", "c-level": "6", "cxo": "6",
}

_NUMERIC = re.compile(r"^\d+$")


def _warn(warnings: List[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _map_tokens(values: Iterable[str], mapping: dict, allowed: Optional[Tuple[str, ...]],
                label: str, warnings: List[str]) -> List[str]:
    """Normalise tokens through ``mapping``; validate; drop silently-bad values."""
    out: List[str] = []
    for value in values or []:
        text = str(value).strip().lower()
        if not text:
            continue
        token = mapping.get(text, str(value).strip())
        if allowed is not None and token not in allowed:
            _warn(warnings, f"{label} value {value!r} ignored (allowed: {', '.join(allowed)})")
            continue
        if token not in out:
            out.append(token)
    return out


def _numeric_ids(values: Iterable[str], label: str, warnings: List[str]) -> List[str]:
    out: List[str] = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        if not _NUMERIC.match(text):
            _warn(warnings, f"{label} expects numeric LinkedIn IDs, got {value!r}")
            continue
        if text not in out:
            out.append(text)
    return out


def _json_facet(values: Iterable[str]) -> str:
    """JSON array the way LinkedIn encodes list facets: '["1441","1035"]'."""
    return json.dumps(list(values), separators=(",", ":"))


def _quote_term(term: str) -> str:
    term = " ".join(str(term).split())
    if not term:
        return ""
    if term.startswith('"') and term.endswith('"'):
        return term
    return f'"{term}"'


def build_keywords(terms: Iterable[str], *, boolean_or: bool = False,
                   exclude: Iterable[str] = ()) -> str:
    """Build a LinkedIn keyword string.

    Terms are phrase-quoted so multi-word titles stay intact; ``OR`` widens a
    search and ``NOT "term"`` excludes one (both are LinkedIn boolean syntax).
    """
    quoted = [q for q in (_quote_term(t) for t in terms or []) if q]
    keywords = (" OR " if boolean_or else " ").join(quoted)
    for term in exclude or []:
        cleaned = " ".join(str(term).split())
        if cleaned:
            keywords = f"{keywords} NOT {_quote_term(cleaned)}".strip()
    return keywords.strip()


def resolve_geo(value: Optional[str]) -> Tuple[List[str], Optional[str]]:
    """Turn a country name or ``geo:<id>`` into ``(["<id>"], warning)``."""
    if value is None:
        return [], None
    text = str(value).strip()
    if not text:
        return [], None
    lowered = text.lower()
    if lowered.startswith("geo:"):
        raw = text.split(":", 1)[1].strip()
        if _NUMERIC.match(raw):
            return [raw], None
        return [], f"invalid geo id {text!r}"
    if _NUMERIC.match(text):
        return [text], None
    geo_id = GEO_IDS.get(lowered)
    if geo_id:
        return [geo_id], None
    return [], f"unknown location {text!r} (use geo:<id> or add it to GEO_IDS)"


@dataclass
class PeopleFilters:
    """Everything the classic LinkedIn people-search URL understands."""

    keywords: str = ""
    geo_ids: List[str] = field(default_factory=list)
    network: List[str] = field(default_factory=list)
    current_company_ids: List[str] = field(default_factory=list)
    past_company_ids: List[str] = field(default_factory=list)
    school_ids: List[str] = field(default_factory=list)
    industry_ids: List[str] = field(default_factory=list)
    seniority: List[str] = field(default_factory=list)
    page: int = 1
    origin: str = ""            # "" -> auto (FACETED_SEARCH when facets exist)
    warnings: List[str] = field(default_factory=list)

    def validate(self) -> "PeopleFilters":
        """Normalise + validate in place, collecting warnings. Returns self."""
        self.keywords = " ".join(str(self.keywords or "").split())
        self.network = _map_tokens(self.network, NETWORK_MAP, NETWORK_TOKENS,
                                   "network", self.warnings)
        self.seniority = _map_tokens(self.seniority, SENIORITY_MAP,
                                     tuple(str(i) for i in range(1, 11)),
                                     "seniority", self.warnings)
        self.current_company_ids = _numeric_ids(
            self.current_company_ids, "current_company", self.warnings)
        self.past_company_ids = _numeric_ids(
            self.past_company_ids, "past_company", self.warnings)
        self.school_ids = _numeric_ids(self.school_ids, "school", self.warnings)
        self.industry_ids = _numeric_ids(self.industry_ids, "industry", self.warnings)
        try:
            self.page = max(1, int(self.page))
        except (TypeError, ValueError):
            self.page = 1
        return self

    @property
    def has_facets(self) -> bool:
        return bool(self.geo_ids or self.network or self.current_company_ids
                    or self.past_company_ids or self.school_ids
                    or self.industry_ids or self.seniority)


def build_people_search_url(filters: PeopleFilters) -> str:
    """Render validated PeopleFilters into a LinkedIn people-search URL."""
    filters.validate()
    params: List[Tuple[str, str]] = []

    if filters.keywords:
        params.append(("keywords", filters.keywords))
    if filters.geo_ids:
        params.append(("geoUrn", _json_facet(filters.geo_ids)))
    if filters.network:
        params.append(("network", _json_facet(filters.network)))
    if filters.current_company_ids:
        params.append(("currentCompany", _json_facet(filters.current_company_ids)))
    if filters.past_company_ids:
        params.append(("pastCompany", _json_facet(filters.past_company_ids)))
    if filters.school_ids:
        params.append(("schoolFilter", _json_facet(filters.school_ids)))
    if filters.industry_ids:
        params.append(("industry", _json_facet(filters.industry_ids)))
    if filters.seniority:
        params.append(("seniority", _json_facet(filters.seniority)))
    if filters.page and filters.page > 1:
        params.append(("page", str(filters.page)))

    origin = filters.origin or ("FACETED_SEARCH" if filters.has_facets
                                else "GLOBAL_SEARCH_HEADER")
    params.append(("origin", origin))

    # quote() with safe="" percent-encodes brackets/quotes/spaces the way
    # LinkedIn's own parser expects (%5B%22F%22%5D) while leaving a literal
    # "&" inside a company name harmless because it is already encoded.
    return PEOPLE_SEARCH_BASE + "?" + urlencode(params, quote_via=quote, safe="")


def build_company_search_url(keywords: str, page: int = 1) -> str:
    """Company search - the documented way to look up a company's numeric URN."""
    params = [("keywords", " ".join(str(keywords).split()))]
    if page and int(page) > 1:
        params.append(("page", str(int(page))))
    params.append(("origin", "GLOBAL_SEARCH_HEADER"))
    return COMPANY_SEARCH_BASE + "?" + urlencode(params, quote_via=quote, safe="")


@dataclass
class JobFilters:
    """Facets for LinkedIn's public job search URL."""

    keywords: str = ""
    location: str = ""
    geo_id: Optional[str] = None
    date_posted: str = ""
    experience_levels: List[str] = field(default_factory=list)
    job_types: List[str] = field(default_factory=list)
    workplace: List[str] = field(default_factory=list)
    easy_apply: bool = False
    company_ids: List[str] = field(default_factory=list)
    sort_by: str = ""
    start: int = 0
    warnings: List[str] = field(default_factory=list)

    def validate(self) -> "JobFilters":
        self.keywords = " ".join(str(self.keywords or "").split())
        self.location = " ".join(str(self.location or "").split())
        self.experience_levels = _map_tokens(
            self.experience_levels, EXPERIENCE_LEVEL_MAP,
            tuple(str(i) for i in range(1, 7)), "experience_level", self.warnings)
        self.job_types = _map_tokens(
            self.job_types, JOB_TYPE_MAP, tuple("FPCTIVO"), "job_type", self.warnings)
        self.workplace = _map_tokens(
            self.workplace, WORK_TYPE_MAP, ("1", "2", "3"), "workplace", self.warnings)
        self.company_ids = _numeric_ids(self.company_ids, "company", self.warnings)
        if self.date_posted:
            token = DATE_POSTED_MAP.get(
                str(self.date_posted).strip().lower(), str(self.date_posted).strip())
            if not re.fullmatch(r"r\d+", token):
                _warn(self.warnings, f"date_posted {self.date_posted!r} ignored")
                token = ""
            self.date_posted = token
        if self.geo_id:
            ids, warning = resolve_geo(self.geo_id)
            if warning:
                _warn(self.warnings, warning)
            self.geo_id = ids[0] if ids else None
        if self.sort_by:
            self.sort_by = SORT_BY_MAP.get(str(self.sort_by).strip().lower(),
                                           str(self.sort_by).strip())
            if self.sort_by not in ("DD", "R"):
                _warn(self.warnings, f"sort_by {self.sort_by!r} ignored")
                self.sort_by = ""
        try:
            self.start = max(0, int(self.start))
        except (TypeError, ValueError):
            self.start = 0
        return self

    def page_start(self, page: int, per_page: int = 25) -> int:
        """LinkedIn pages jobs by ``start`` offset, not by page number."""
        return max(0, (max(1, int(page)) - 1) * per_page)


def build_jobs_search_url(filters: JobFilters) -> str:
    filters.validate()
    params: List[Tuple[str, str]] = []
    if filters.keywords:
        params.append(("keywords", filters.keywords))
    if filters.location:
        params.append(("location", filters.location))
    if filters.geo_id:
        params.append(("geoId", str(filters.geo_id)))
    if filters.date_posted:
        params.append(("f_TPR", filters.date_posted))
    if filters.experience_levels:
        params.append(("f_E", ",".join(filters.experience_levels)))
    if filters.job_types:
        params.append(("f_JT", ",".join(filters.job_types)))
    if filters.workplace:
        params.append(("f_WT", ",".join(filters.workplace)))
    if filters.easy_apply:
        params.append(("f_EA", "true"))
    if filters.company_ids:
        params.append(("f_C", ",".join(filters.company_ids)))
    if filters.sort_by:
        params.append(("sortBy", filters.sort_by))
    if filters.start:
        params.append(("start", str(filters.start)))
    params.append(("origin", "JOB_SEARCH_PAGE_SEARCH_BUTTON"))
    return JOBS_SEARCH_BASE + "?" + urlencode(params, quote_via=quote, safe="")


# ─────────────────────────────────────────────────────────────
#  URL parsing / canonicalisation (dedupe, CLI and MCP use this)
# ─────────────────────────────────────────────────────────────

_PROFILE_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
_COMPANY_RE = re.compile(r"linkedin\.com/company/([^/?#]+)", re.I)
_JOB_RE = re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d+)", re.I)
_SCHOOL_RE = re.compile(r"linkedin\.com/school/([^/?#]+)", re.I)


def canonical_profile_url(url: str) -> str:
    """Reduce any profile URL variant to https://www.linkedin.com/in/<slug>.

    Strips tracking query strings, ``/details/...`` sub-pages, locale
    sub-domains and trailing slashes. Returns "" for non-profile URLs.
    """
    match = _PROFILE_RE.search(str(url or "").strip())
    if not match:
        return ""
    slug = match.group(1).strip().strip("/").lower()
    return f"https://www.linkedin.com/in/{slug}" if slug else ""


def profile_public_id(url: str) -> str:
    canonical = canonical_profile_url(url)
    return canonical.rsplit("/", 1)[-1] if canonical else ""


def classify_url(url: str) -> dict:
    """Return {'kind': 'profile'|'company'|'school'|'job'|'unknown', 'id': str}."""
    text = str(url or "")
    for regex, kind in ((_PROFILE_RE, "profile"), (_COMPANY_RE, "company"),
                        (_SCHOOL_RE, "school")):
        match = regex.search(text)
        if match:
            return {"kind": kind, "id": match.group(1).strip("/").lower()}
    match = _JOB_RE.search(text)
    if match:
        return {"kind": "job", "id": match.group(1)}
    return {"kind": "unknown", "id": ""}


def geo_id_for(name: str) -> Optional[str]:
    """Convenience: country name -> geo id (None when unknown)."""
    ids, _ = resolve_geo(name)
    return ids[0] if ids else None
