"""Candidate scoring: rank a scraped pool against a resume or an ICP.

Two deterministic scorers, no ML model, no network:

  resume match  - weighted keyword coverage of a resume across the profile's
                  skills, title, headline, About and work history, with the
                  matched/missing terms kept for explainability.
  ICP fit       - how well a profile matches an ideal-candidate profile
                  (titles, seniority bands, locations, companies, skills,
                  email presence, open-to-work) with hard exclusions.

Both return plain numbers plus human-readable reasons, because a sourcing list
that says "87" without saying *why* is impossible to trust or tune.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the",
    "their", "this", "to", "was", "were", "will", "with", "you", "your", "we",
    "our", "i", "my", "me", "they", "them", "he", "she", "his", "her", "who",
    "which", "what", "when", "where", "how", "all", "any", "can", "may", "not",
    "but", "if", "so", "than", "then", "there", "these", "those", "using",
    "used", "use", "work", "worked", "working", "experience", "experienced",
    "years", "year", "team", "teams", "role", "roles", "job", "jobs", "company",
    "including", "etc", "across", "within", "over", "more", "most", "other",
    "such", "also", "well", "new", "about", "per", "via", "both", "each",
}

# Fields searched by the resume scorer and how much each one counts. A hit in
# "skills" is worth more than the same word buried in a paragraph of About.
FIELD_WEIGHTS = {
    "skills": 1.8,
    "title": 1.5,
    "headline": 1.3,
    "experience": 1.0,
    "certifications": 0.7,
    "about": 0.6,
    "education": 0.5,
    "projects": 0.4,
    "languages": 0.3,
}

TIER_THRESHOLDS = ((75, "excellent"), (55, "strong"), (35, "moderate"), (1, "weak"))

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#._/-]*")
_WORD_RE_CACHE: Dict[str, re.Pattern] = {}


def tokenize(text: str) -> List[str]:
    """Lowercase tokens, keeping meaningful punctuation (c++, c#, node.js)."""
    return [t for t in _TOKEN_RE.findall(str(text or "").lower())
            if len(t) > 1 and t not in STOPWORDS]


def _has_term(text: str, term: str) -> bool:
    """Whole-token match for single words; substring for phrases.

    Prevents "java" matching "javascript" and "sales" matching "salesforce"
    while still letting "machine learning" match as a phrase.
    """
    if not term:
        return False
    if " " in term:
        return term in text
    pattern = _WORD_RE_CACHE.get(term)
    if pattern is None:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
        _WORD_RE_CACHE[term] = pattern
    return bool(pattern.search(text))


def read_resume_text(path) -> str:
    """Extract text from a .pdf/.docx/.txt/.md resume. Returns "" on failure.

    Imported lazily so the scraper still runs when PyPDF2/python-docx are not
    installed - the resume feature degrades instead of crashing the run.
    """
    file_path = Path(str(path or ""))
    if not file_path.exists() or not file_path.is_file():
        return ""
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".pdf":
            from PyPDF2 import PdfReader  # type: ignore
            reader = PdfReader(str(file_path))
            return " ".join((page.extract_text() or "") for page in reader.pages)
        if suffix in (".docx", ".doc"):
            from docx import Document  # type: ignore
            document = Document(str(file_path))
            return " ".join(paragraph.text for paragraph in document.paragraphs)
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _known_phrases() -> Tuple[str, ...]:
    """Multi-word skill names from the enrichment taxonomy (lazy import)."""
    try:
        from enrichment import SKILL_ALIASES
    except Exception:
        return ()
    return tuple(sorted({alias for alias in SKILL_ALIASES if " " in alias}))


def keyword_weights(text: str, top_n: int = 60) -> Dict[str, float]:
    """Turn free text into ``{keyword: weight}``.

    Weight grows with frequency (log-scaled, capped at 3) and known multi-word
    skills get a floor of 2.5 even when they appear once - a resume that says
    "machine learning" once still means it.
    """
    lowered = str(text or "").lower()
    tokens = tokenize(lowered)
    if not tokens:
        return {}

    counts: Dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1

    weights: Dict[str, float] = {}
    for token, count in counts.items():
        if len(token) < 2:
            continue
        weights[token] = round(min(1.0 + math.log1p(count), 3.0), 2)

    for phrase in _known_phrases():
        if phrase in lowered and phrase not in weights:
            weights[phrase] = 2.5

    ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(ranked[:top_n])


def resume_keywords(path, top_n: int = 60) -> Dict[str, float]:
    """Read a resume file and return keyword weights ({} when unreadable)."""
    return keyword_weights(read_resume_text(path), top_n=top_n)


def _record_field_text(record: dict, field: str) -> str:
    value = record.get(field)
    if field == "skills":
        value = record.get("skills_normalized") or value
    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value)
    return str(value or "").lower()


def match_tier(score: float) -> str:
    for threshold, label in TIER_THRESHOLDS:
        if score >= threshold:
            return label
    return "none"


def score_record(record: dict, weights: Dict[str, float],
                 *, max_reasons: int = 6) -> dict:
    """Score one profile against keyword weights. Returns the scored record copy.

    Adds ``match_score`` (0-100), ``match_tier``, ``match_reasons``,
    ``matched_keywords`` and ``missing_keywords`` to a copy of the record.
    """
    out = dict(record or {})
    weights = {str(k).lower(): float(v) for k, v in (weights or {}).items() if v}
    if not weights:
        out.update({"match_score": 0.0, "match_tier": "none",
                    "match_reasons": "", "matched_keywords": "",
                    "missing_keywords": ""})
        return out

    fields = {name: _record_field_text(out, name) for name in FIELD_WEIGHTS}
    total_weight = sum(weights.values()) or 1.0
    matched_weight = 0.0
    matched: List[str] = []
    missing: List[str] = []
    field_hits: Dict[str, List[str]] = {}

    for term, weight in weights.items():
        best_multiplier = 0.0
        for name, text in fields.items():
            if text and _has_term(text, term):
                best_multiplier = max(best_multiplier, FIELD_WEIGHTS[name])
        if best_multiplier:
            matched_weight += weight * best_multiplier
            matched.append(term)
            # Attribute the hit to the strongest field it appeared in, for reasons.
            for name in sorted(FIELD_WEIGHTS, key=FIELD_WEIGHTS.get, reverse=True):
                if fields[name] and _has_term(fields[name], term):
                    field_hits.setdefault(name, []).append(term)
                    break
        else:
            missing.append(term)

    # Divided by the maximum multiplier so a perfect match is 100, not 180.
    score = 100.0 * matched_weight / (total_weight * max(FIELD_WEIGHTS.values()))
    score = round(max(0.0, min(100.0, score)), 1)

    if score and any(_has_term(fields["title"], term) for term in weights):
        score = round(min(100.0, score + 4.0), 1)  # title relevance bonus

    reasons = []
    for name in ("title", "skills", "experience", "headline", "about"):
        hits = field_hits.get(name)
        if hits:
            reasons.append(f"{name}: {', '.join(hits[:4])}")
        if len(reasons) >= max_reasons:
            break

    out.update({
        "match_score": score,
        "match_tier": match_tier(score),
        "match_reasons": " | ".join(reasons),
        "matched_keywords": ", ".join(sorted(matched)[:25]),
        "missing_keywords": ", ".join(sorted(missing)[:25]),
    })
    return out


# ─────────────────────────────────────────────────────────────
#  Ideal-candidate-profile (ICP) fit
# ─────────────────────────────────────────────────────────────

@dataclass
class ICP:
    """A hiring brief the scorer can measure profiles against."""

    titles: List[str] = field(default_factory=list)          # substrings, any-of
    seniorities: List[str] = field(default_factory=list)     # labels or ranks
    locations: List[str] = field(default_factory=list)       # any-of substrings
    companies: List[str] = field(default_factory=list)       # any-of substrings
    exclude_companies: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)          # any-of
    must_have_email: bool = False
    open_to_work_only: bool = False
    min_completeness: int = 0
    weights: Dict[str, float] = field(default_factory=lambda: {
        "title": 30.0, "seniority": 15.0, "location": 15.0, "company": 15.0,
        "skills": 15.0, "email": 5.0, "open_to_work": 5.0,
    })

    def validate(self) -> "ICP":
        self.titles = [t.strip() for t in self.titles if str(t).strip()]
        self.seniorities = [str(s).strip().lower() for s in self.seniorities if str(s).strip()]
        self.locations = [loc.strip() for loc in self.locations if str(loc).strip()]
        self.companies = [c.strip() for c in self.companies if str(c).strip()]
        self.exclude_companies = [c.strip() for c in self.exclude_companies if str(c).strip()]
        self.skills = [s.strip() for s in self.skills if str(s).strip()]
        return self


_SENIORITY_WORDS = {
    "1": "intern / entry", "2": "individual contributor", "3": "senior ic",
    "4": "lead / manager", "5": "director", "6": "vp / head", "7": "c-suite / founder",
}


def _seniority_matches(record: dict, wanted: Iterable[str]) -> bool:
    wanted = [w.lower() for w in wanted]
    if not wanted:
        return False
    rank = record.get("seniority_rank")
    label = str(record.get("seniority_label") or record.get("seniority") or "").lower()
    for value in wanted:
        if value == str(rank):
            return True
        if value in label:
            return True
        if _SENIORITY_WORDS.get(value, "") and _SENIORITY_WORDS[value] in label:
            return True
    return False


def score_fit(record: dict, icp: ICP, *, max_reasons: int = 6) -> dict:
    """Score one profile against an ICP. Adds ``fit_score``/``fit_reasons``.

    Hard rules first: an excluded company or a missing required email makes the
    profile a zero regardless of the other components, because a great fit you
    cannot contact is not a lead.
    """
    icp.validate()
    out = dict(record or {})
    company = str(out.get("company") or out.get("company_clean") or "")
    title = str(out.get("title") or out.get("headline") or "")
    location = str(out.get("location") or "")
    skills = str(out.get("skills_normalized") or out.get("skills") or "")
    half_text = f"{title} {out.get('headline') or ''}".lower()

    for excluded in icp.exclude_companies:
        if excluded.lower() in company.lower():
            out.update({"fit_score": 0.0, "fit_tier": "excluded",
                        "fit_reasons": f"excluded company: {company}"})
            return out
    if icp.must_have_email and not out.get("email"):
        out.update({"fit_score": 0.0, "fit_tier": "no_contact",
                    "fit_reasons": "requires an email, none found"})
        return out
    if icp.min_completeness and int(out.get("completeness_score") or 0) < icp.min_completeness:
        out.update({"fit_score": 0.0, "fit_tier": "incomplete",
                    "fit_reasons": f"completeness below {icp.min_completeness}"})
        return out
    if icp.open_to_work_only and not out.get("open_to_work"):
        out.update({"fit_score": 0.0, "fit_tier": "not_open",
                    "fit_reasons": "not open to work"})
        return out

    earned = 0.0
    possible = 0.0
    reasons: List[str] = []

    def award(component: str, ok: bool, detail: str) -> None:
        nonlocal earned, possible
        weight = float(icp.weights.get(component, 0.0))
        possible += weight
        if ok:
            earned += weight
            reasons.append(detail)

    if icp.titles:
        hit = next((t for t in icp.titles if t.lower() in half_text), "")
        award("title", bool(hit), f"title matches '{hit}'")
    if icp.seniorities:
        award("seniority", _seniority_matches(out, icp.seniorities),
              f"seniority {out.get('seniority_label') or out.get('seniority')}")
    if icp.locations:
        hit = next((loc for loc in icp.locations if loc.lower() in location.lower()), "")
        award("location", bool(hit), f"location matches '{hit}'")
    if icp.companies:
        hit = next((c for c in icp.companies if c.lower() in company.lower()), "")
        award("company", bool(hit), f"company matches '{hit}'")
    if icp.skills:
        hit = next((s for s in icp.skills if s.lower() in skills.lower()), "")
        award("skills", bool(hit), f"skill matches '{hit}'")
    if icp.must_have_email or icp.weights.get("email"):
        award("email", bool(out.get("email")), "has a contact email")
    if icp.open_to_work_only or icp.weights.get("open_to_work"):
        award("open_to_work", bool(out.get("open_to_work")), "open to work")

    score = round(100.0 * earned / possible, 1) if possible else 0.0
    out.update({
        "fit_score": score,
        "fit_tier": match_tier(score),
        "fit_reasons": " | ".join(reasons[:max_reasons]),
    })
    return out


def rank_records(records: Iterable[dict], by: str = "match_score",
                 descending: bool = True, limit: Optional[int] = None) -> List[dict]:
    """Sort records by a numeric field, missing/invalid values last."""
    def sort_key(record: dict):
        value = record.get(by)
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float("-inf") if descending else float("inf")
        return -number if descending else number

    ranked = sorted(list(records or []), key=sort_key)
    return ranked[:limit] if limit else ranked


def score_pool(records: Iterable[dict], *, weights: Optional[Dict[str, float]] = None,
               icp: Optional[ICP] = None, min_score: float = 0.0) -> List[dict]:
    """Score a whole pool against resume weights and/or an ICP, then filter+sort.

    ``match_score`` drives the ordering when weights are supplied; otherwise
    ``fit_score`` does. Records below ``min_score`` on the active score are
    dropped.
    """
    scored: List[dict] = []
    for record in records or []:
        item = dict(record)
        if weights:
            item = score_record(item, weights)
        if icp is not None:
            item = score_fit(item, icp)
        active = item.get("match_score") if weights else item.get("fit_score")
        try:
            active_value = float(active or 0.0)
        except (TypeError, ValueError):
            active_value = 0.0
        if min_score and active_value < float(min_score):
            continue
        scored.append(item)

    key = "match_score" if weights else "fit_score"
    return rank_records(scored, by=key)
