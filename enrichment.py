"""Profile enrichment: turn raw scraped strings into screening-ready fields.

The scraper hands back what LinkedIn renders - one ``name`` blob, a free-text
``location``, a comma-joined skills string, sometimes an email.  That is enough
to eyeball a spreadsheet, not enough to filter, dedupe or rank a candidate
pool.  This module derives the missing structure deterministically (no network,
no ML models, no extra page loads):

  * names      -> first / middle / last, suffixes and credentials stripped
  * locations  -> city / region / country + ISO code, metro and remote flags
  * phones     -> E.164 (or "" when the number is implausible)
  * companies  -> legal-suffix-stripped name, website/email-derived domain
  * emails     -> explicit address, or clearly-labelled *inferred* patterns
  * skills     -> canonical names + a coarse category, so "js" == "JavaScript"
  * titles     -> seniority rank + department/function
  * records    -> completeness score, quality flags, a stable dedupe key

Everything is additive: existing keys are never overwritten with worse data,
and ``merge_record`` prefers non-empty values while unioning list-like fields.

Inferred emails are never presented as verified.  ``email_status`` says
``scraped`` only for an address that was literally on the profile; anything
else is ``inferred`` with a confidence number, because guessing an address is
useful for prioritisation and harmful if mistaken for a fact.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from search_filters import canonical_profile_url, profile_public_id

# ─────────────────────────────────────────────────────────────
#  Text cleanup
# ─────────────────────────────────────────────────────────────

_MOJIBAKE_HINTS = ("\u00c3", "\u00c2", "\u00e2\u20ac", "\u00f0\u0178", "\u00ef\u00b8")


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was once decoded as Latin-1 ("Ã©" -> "é").

    Only rewrites when the round-trip is valid *and* strictly reduces the
    number of mojibake markers, so genuine accented text is left alone.
    """
    text = str(text or "")
    if not any(hint in text for hint in _MOJIBAKE_HINTS):
        return text
    # cp1252 first: that is the encoding a Windows console actually used when
    # it turned UTF-8 bytes into "â€¢" (U+20AC only exists in cp1252, not
    # latin-1). Fall back to latin-1 for the subset that survives it.
    for codec in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _mojibake_score(repaired) < _mojibake_score(text):
            return repaired
    return text


def _mojibake_score(text: str) -> int:
    return sum(text.count(hint) for hint in _MOJIBAKE_HINTS)


def clean_text(value, *, keep_newlines: bool = False) -> str:
    """Normalise whitespace / NBSP / zero-width junk and trim."""
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").replace("\u200b", "")
    text = text.replace("\u200e", "").replace("\u200f", "").replace("\ufeff", "")
    text = fix_mojibake(text)
    if keep_newlines:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────
#  Names
# ─────────────────────────────────────────────────────────────

_NAME_PREFIXES = {"dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "miss",
                  "prof", "prof.", "sir", "rev", "rev.", "hon", "hon."}
_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v", "phd", "ph.d.",
                  "mba", "md", "cpa", "pmp", "cfa", "esq", "esq.", "dds", "rn",
                  "msc", "bsc", "ma", "ba", "pe", "p.e.", "sphr", "shrm"}
_NAME_PARTICLES = {"van", "von", "de", "da", "del", "der", "la", "le", "di", "du",
                   "bin", "al", "el", "st", "st."}


def parse_name(full_name: str) -> dict:
    """Split a display name into structured parts.

    Handles "Dr. Jane Q. Doe, PhD", "JANE DOE", "Doe, Jane", single names and
    names with particles ("Ludwig van Beethoven"). Never raises; unknown parts
    come back as "".
    """
    raw = clean_text(full_name)
    result = {"first": "", "middle": "", "last": "", "suffix": "", "display": raw}
    if not raw:
        return result

    # "Doe, Jane" (comma separates last name) vs "Jane Doe, PhD" (suffix).
    if "," in raw:
        left, right = (part.strip() for part in raw.split(",", 1))
        right_words = right.split()
        if right_words and all(w.lower().rstrip(".") in
                               {s.rstrip(".") for s in _NAME_SUFFIXES}
                               for w in right_words):
            raw = left
            result["suffix"] = right
        elif left and right and len(left.split()) == 1 and 1 <= len(right_words) <= 2:
            # LinkedIn renders "First Last"; a "Last, First" string is rare but
            # unambiguous enough to reverse when the left side is one token.
            raw = f"{right} {left}"
        elif left and not right:
            raw = left

    tokens = [t for t in raw.split() if t]
    if len(tokens) > 1 and tokens[-1].lower().rstrip(".") in _NAME_SUFFIXES:
        result["suffix"] = (result["suffix"] + " " + tokens.pop()).strip()
    while tokens and tokens[0].lower().rstrip(".") in _NAME_PREFIXES:
        tokens.pop(0)
    if not tokens:
        return result

    if len(tokens) == 1:
        result["first"] = tokens[0]
        return result

    result["first"] = tokens[0]
    result["last"] = tokens[-1]
    if len(tokens) > 2:
        middle = tokens[1:-1]
        # "van der Berg": particle belongs to the surname.
        if middle and middle[0].lower() in _NAME_PARTICLES:
            result["last"] = " ".join(middle + [result["last"]])
        else:
            result["middle"] = " ".join(middle)
    return result


def normalize_name_case(name: str) -> str:
    """Title-case an ALL-CAPS name; leave already mixed-case names alone."""
    text = clean_text(name)
    if not text:
        return ""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    if sum(1 for c in letters if c.isupper()) / len(letters) > 0.9:
        return " ".join(
            "-".join(part.capitalize() for part in word.split("-"))
            for word in text.split()
        )
    return text


# ─────────────────────────────────────────────────────────────
#  Locations
# ─────────────────────────────────────────────────────────────

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington, d.c.": "DC",
}

COUNTRY_ALIASES = {
    "united states": ("US", "United States"), "united states of america": ("US", "United States"),
    "usa": ("US", "United States"), "u.s.a.": ("US", "United States"),
    "us": ("US", "United States"), "america": ("US", "United States"),
    "united kingdom": ("GB", "United Kingdom"), "uk": ("GB", "United Kingdom"),
    "great britain": ("GB", "United Kingdom"), "england": ("GB", "United Kingdom"),
    "scotland": ("GB", "United Kingdom"), "wales": ("GB", "United Kingdom"),
    "northern ireland": ("GB", "United Kingdom"),
    "canada": ("CA", "Canada"), "australia": ("AU", "Australia"),
    "india": ("IN", "India"), "germany": ("DE", "Germany"),
    "france": ("FR", "France"), "netherlands": ("NL", "Netherlands"),
    "singapore": ("SG", "Singapore"), "united arab emirates": ("AE", "United Arab Emirates"),
    "uae": ("AE", "United Arab Emirates"), "brazil": ("BR", "Brazil"),
    "spain": ("ES", "Spain"), "italy": ("IT", "Italy"), "ireland": ("IE", "Ireland"),
    "switzerland": ("CH", "Switzerland"), "sweden": ("SE", "Sweden"),
    "denmark": ("DK", "Denmark"), "norway": ("NO", "Norway"),
    "finland": ("FI", "Finland"), "poland": ("PL", "Poland"),
    "israel": ("IL", "Israel"), "mexico": ("MX", "Mexico"), "japan": ("JP", "Japan"),
    "china": ("CN", "China"), "hong kong": ("HK", "Hong Kong"),
    "south korea": ("KR", "South Korea"), "korea": ("KR", "South Korea"),
    "new zealand": ("NZ", "New Zealand"), "south africa": ("ZA", "South Africa"),
    "portugal": ("PT", "Portugal"), "belgium": ("BE", "Belgium"),
    "austria": ("AT", "Austria"), "turkey": ("TR", "Turkey"),
    "argentina": ("AR", "Argentina"), "chile": ("CL", "Chile"),
    "colombia": ("CO", "Colombia"), "philippines": ("PH", "Philippines"),
    "malaysia": ("MY", "Malaysia"), "indonesia": ("ID", "Indonesia"),
    "thailand": ("TH", "Thailand"), "vietnam": ("VN", "Vietnam"),
    "pakistan": ("PK", "Pakistan"), "czech republic": ("CZ", "Czechia"),
    "czechia": ("CZ", "Czechia"), "greece": ("GR", "Greece"),
    "russia": ("RU", "Russia"), "nigeria": ("NG", "Nigeria"), "kenya": ("KE", "Kenya"),
    "egypt": ("EG", "Egypt"), "saudi arabia": ("SA", "Saudi Arabia"),
}

_REMOTE_WORDS = ("remote", "anywhere", "work from home", "wfh", "virtual", "distributed")
_METRO_RE = re.compile(r"^(greater\s+)?(.+?)\s+(area|metropolitan area|metro area|bay area)$", re.I)


def split_location(location: str) -> dict:
    """Parse a LinkedIn location string into city/region/country parts.

    Examples handled: "Bentonville, Arkansas, United States",
    "New York, NY", "Greater New York City Area", "San Francisco Bay Area",
    "London, England, United Kingdom", "Remote", "India".
    """
    raw = clean_text(location)
    out = {
        "city": "", "region": "", "region_code": "", "country": "",
        "country_code": "", "is_remote": False, "is_metro": False,
    }
    if not raw:
        return out

    lowered = raw.lower()
    if any(word == lowered or lowered.startswith(word + " ") for word in _REMOTE_WORDS):
        out["is_remote"] = True
        return out

    metro = _METRO_RE.match(raw)
    if metro:
        out["is_metro"] = True
        raw = metro.group(2).strip()
        if raw.lower().startswith("greater "):
            raw = raw[8:]
        lowered = raw.lower()

    parts = [p.strip() for p in re.split(r"\s*[,\u00b7|]\s*", raw) if p.strip()]
    if not parts:
        return out

    # Country: last part (alias, or two-letter code).
    country_code, country_name = "", ""
    if len(parts) >= 2:
        candidate = parts[-1].lower()
        alias = COUNTRY_ALIASES.get(candidate)
        if alias:
            country_code, country_name = alias
            parts = parts[:-1]
        elif len(parts[-1]) == 2 and parts[-1].isalpha():
            uppered = parts[-1].upper()
            if uppered in {code for code, _ in COUNTRY_ALIASES.values()}:
                country_code = uppered
                country_name = next(
                    (name for code, name in COUNTRY_ALIASES.values() if code == uppered), "")
                parts = parts[:-1]

    # Region: US state name/code, else a 2-3 letter code, else a leftover name.
    if parts:
        candidate = parts[-1].lower()
        if candidate in US_STATES:
            out["region"] = parts[-1]
            out["region_code"] = US_STATES[candidate]
            country_code, country_name = country_code or "US", country_name or "United States"
            parts = parts[:-1]
        elif len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isupper():
            out["region"] = parts[-1]
            out["region_code"] = parts[-1]
            if not country_code:
                country_code, country_name = "US", "United States"
            parts = parts[:-1]

    if parts:
        out["city"] = parts[0]
    if parts and not out["region"] and len(parts) >= 2:
        out["region"] = parts[1]

    # A lone "United States" / "India" parses with no country left above.
    if not country_code and len(parts) == 1 and parts[0].lower() in COUNTRY_ALIASES:
        country_code, country_name = COUNTRY_ALIASES[parts[0].lower()]
        out["city"] = ""

    out["country"] = country_name
    out["country_code"] = country_code
    return out


# ─────────────────────────────────────────────────────────────
#  Phones
# ─────────────────────────────────────────────────────────────

_PHONE_RE = re.compile(r"(\+?\d[\d\s().\-]{6,}\d)")
_EXT_RE = re.compile(r"(?:ext|x|extension)\.?\s*(\d{1,6})", re.I)


def phone_extension(raw: str) -> str:
    """Extension digits from a phone string ("... ext. 12" -> "12"), else ""."""
    match = _EXT_RE.search(str(raw or ""))
    return match.group(1) if match else ""


def normalize_phone(raw: str, default_region: str = "US") -> str:
    """Best-effort E.164 ("+14155552671"). Returns "" for implausible numbers."""
    text = str(raw or "")
    if not text.strip():
        return ""
    ext_match = _EXT_RE.search(text)
    if ext_match:
        text = text[:ext_match.start()]
    match = _PHONE_RE.search(text)
    if not match:
        return ""
    source = match.group(1)
    digits = re.sub(r"\D", "", source)
    if not digits or len(digits) < 7:
        return ""
    # A bare run of digits is an id (LinkedIn member/analytics numbers), not a
    # phone. Require a country prefix or human formatting (space, dash, dot,
    # parentheses) before treating the value as contactable.
    if not source.strip().startswith("+") and not re.search(r"[\s().\-]", source):
        return ""

    if text.strip().startswith("+"):
        number = digits
    elif digits.startswith("00"):
        number = digits[2:]
    elif len(digits) == 10 and default_region == "US":
        number = "1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        number = digits
    else:
        number = digits

    if not 8 <= len(number) <= 15:
        return ""
    if number.startswith("0"):
        return ""
    return "+" + number


# ─────────────────────────────────────────────────────────────
#  Companies & domains
# ─────────────────────────────────────────────────────────────

_COMPANY_SUFFIXES = (
    "incorporated", "inc", "inc.", "llc", "l.l.c.", "ltd", "ltd.", "limited",
    "plc", "plc.", "corp", "corp.", "corporation", "company", "co", "co.",
    "gmbh", "ag", "sa", "s.a.", "sas", "bv", "b.v.", "nv", "n.v.", "pty",
    "pvt", "pvt.", "private limited", "srl", "s.p.a.", "oy", "ab", "as", "a/s",
)

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "protonmail.com", "proton.me", "gmx.com", "gmx.de", "mail.com",
    "yandex.com", "yandex.ru", "zoho.com", "fastmail.com", "hey.com",
}

# Well-known corporate domains. Deliberately small and obvious; anything else
# falls back to the profile's own website/email domain, then to a slug guess
# flagged as low confidence.
COMPANY_DOMAINS = {
    "google": "google.com", "alphabet": "abc.xyz", "microsoft": "microsoft.com",
    "amazon": "amazon.com", "apple": "apple.com", "meta": "meta.com",
    "facebook": "meta.com", "netflix": "netflix.com", "tesla": "tesla.com",
    "nvidia": "nvidia.com", "intel": "intel.com", "ibm": "ibm.com",
    "oracle": "oracle.com", "salesforce": "salesforce.com", "adobe": "adobe.com",
    "cisco": "cisco.com", "dell": "dell.com", "hp": "hp.com", "uber": "uber.com",
    "airbnb": "airbnb.com", "stripe": "stripe.com", "paypal": "paypal.com",
    "jpmorgan chase": "jpmorganchase.com", "jpmorgan": "jpmorganchase.com",
    "goldman sachs": "goldmansachs.com", "morgan stanley": "morganstanley.com",
    "bank of america": "bankofamerica.com", "wells fargo": "wellsfargo.com",
    "citi": "citi.com", "citigroup": "citi.com", "walmart": "walmart.com",
    "target": "target.com", "costco": "costco.com", "kroger": "kroger.com",
    "cvs health": "cvshealth.com", "unitedhealth group": "unitedhealthgroup.com",
    "cigna": "cigna.com", "pfizer": "pfizer.com", "merck": "merck.com",
    "johnson & johnson": "jnj.com", "procter & gamble": "pg.com",
    "general motors": "gm.com", "ford": "ford.com", "boeing": "boeing.com",
    "lockheed martin": "lockheedmartin.com", "mckinsey": "mckinsey.com",
    "boston consulting group": "bcg.com", "bain": "bain.com",
    "deloitte": "deloitte.com", "pwc": "pwc.com", "ey": "ey.com",
    "kpmg": "kpmg.com", "accenture": "accenture.com", "infosys": "infosys.com",
    "tata consultancy services": "tcs.com", "tcs": "tcs.com", "wipro": "wipro.com",
    "cognizant": "cognizant.com", "hcl": "hcl.com", "atlassian": "atlassian.com",
    "shopify": "shopify.com", "spotify": "spotify.com", "linkedin": "linkedin.com",
    "github": "github.com", "gitlab": "gitlab.com", "samsung": "samsung.com",
    "sony": "sony.com", "toyota": "toyota.com", "siemens": "siemens.com",
    "sap": "sap.com", "zoom": "zoom.us", "slack": "slack.com",
    "servicenow": "servicenow.com", "workday": "workday.com", "datadog": "datadoghq.com",
    "snowflake": "snowflake.com", "palantir": "palantir.com", "openai": "openai.com",
}


def normalize_company(name: str) -> str:
    """Strip legal suffixes and normalise punctuation ("Google Inc." -> "Google")."""
    text = clean_text(name)
    if not text:
        return ""
    text = text.replace("&amp;", "&").replace("&#39;", "'")
    if text.endswith("."):
        text = text[:-1]
    changed = True
    while changed:
        changed = False
        lowered = text.lower()
        for suffix in _COMPANY_SUFFIXES:
            if lowered.endswith(" " + suffix) and len(text) > len(suffix) + 1:
                text = text[: -(len(suffix) + 1)].rstrip(" ,.-")
                changed = True
                break
    return text.strip()


def _company_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_company(name).lower())


def domain_from_url(url: str) -> str:
    """Extract a bare registrable-ish host from a URL (www./careers. stripped)."""
    text = str(url or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[a-z]+://", "", text, flags=re.I)
    host = text.split("/")[0].split("?")[0].split("#")[0].lower()
    host = re.sub(r":\d+$", "", host)
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or " " in host:
        return ""
    return host


def infer_company_domain(record: dict, domains: Optional[Dict[str, str]] = None) -> Tuple[str, str, float]:
    """Return (domain, source, confidence) using the strongest available signal."""
    lookup = dict(COMPANY_DOMAINS)
    if domains:
        lookup.update({str(k).lower(): str(v) for k, v in domains.items()})

    email = str(record.get("email") or "").strip().lower()
    if "@" in email:
        domain = email.rsplit("@", 1)[1]
        if domain and domain not in FREE_EMAIL_DOMAINS:
            return domain, "email", 0.99

    for site in str(record.get("websites") or "").replace(";", ",").split(","):
        domain = domain_from_url(site)
        if not domain:
            continue
        if any(domain.endswith(provider) for provider in (
            "linkedin.com", "github.com", "medium.com", "twitter.com", "x.com",
            "facebook.com", "instagram.com", "youtube.com", "behance.net",
            "dribbble.com", "substack.com", "wordpress.com", "blogspot.com",
        )):
            continue
        if domain not in FREE_EMAIL_DOMAINS:
            return domain, "website", 0.9

    company = normalize_company(record.get("company") or "")
    if company:
        known = lookup.get(company.lower())
        if known:
            return known, "known_company", 0.95
        slug = _company_slug(company)
        if slug and len(slug) >= 3:
            return f"{slug}.com", "slug_guess", 0.25
    return "", "none", 0.0


# ─────────────────────────────────────────────────────────────
#  Emails
# ─────────────────────────────────────────────────────────────

# Ordered most -> least likely for a corporate address. Confidence is a
# heuristic prior, not a measurement.
EMAIL_PATTERNS = [
    ("{first}.{last}", 0.55),
    ("{first}{last}", 0.45),
    ("{f}{last}", 0.40),
    ("{first}", 0.30),
    ("{last}.{first}", 0.22),
    ("{first}_{last}", 0.20),
    ("{f}.{last}", 0.16),
    ("{first}{l}", 0.12),
]


def _ascii_slug(value: str) -> str:
    text = clean_text(value).lower()
    text = (text.replace("ä", "a").replace("ö", "o").replace("ü", "u")
            .replace("é", "e").replace("è", "e").replace("ç", "c")
            .replace("ñ", "n").replace("ø", "o").replace("å", "a"))
    return re.sub(r"[^a-z]", "", text)


def infer_emails(first: str, last: str, domain: str,
                 patterns: Optional[Iterable[Tuple[str, float]]] = None) -> List[dict]:
    """Generate plausible corporate addresses, labelled with a confidence prior."""
    first_slug, last_slug = _ascii_slug(first), _ascii_slug(last)
    domain = str(domain or "").strip().lower()
    if not domain or "." not in domain:
        return []
    if not first_slug and not last_slug:
        return []

    candidates: List[dict] = []
    seen = set()
    for template, confidence in (patterns or EMAIL_PATTERNS):
        if not first_slug and ("{first}" in template or "{f}" in template):
            continue
        if not last_slug and ("{last}" in template or "{l}" in template):
            continue
        local = (template
                 .replace("{first}", first_slug)
                 .replace("{last}", last_slug)
                 .replace("{f}", first_slug[:1])
                 .replace("{l}", last_slug[:1]))
        local = local.strip("._-")
        if len(local) < 2 or local in seen:
            continue
        seen.add(local)
        candidates.append({
            "email": f"{local}@{domain}",
            "pattern": template,
            "confidence": round(confidence * (1.0 if len(first_slug) > 1 else 0.8), 2),
            "verified": False,
        })
    return candidates


# ─────────────────────────────────────────────────────────────
#  Skills
# ─────────────────────────────────────────────────────────────

SKILL_ALIASES = {
    "js": "JavaScript", "javascript": "JavaScript", "ts": "TypeScript",
    "typescript": "TypeScript", "node": "Node.js", "nodejs": "Node.js",
    "node.js": "Node.js", "react": "React", "reactjs": "React", "react.js": "React",
    "vue": "Vue.js", "angular": "Angular", "next.js": "Next.js", "nextjs": "Next.js",
    "py": "Python", "python": "Python", "python3": "Python",
    "ml": "Machine Learning", "machine learning": "Machine Learning",
    "ai": "Artificial Intelligence", "artificial intelligence": "Artificial Intelligence",
    "dl": "Deep Learning", "deep learning": "Deep Learning",
    "nlp": "Natural Language Processing",
    "llm": "Large Language Models", "llms": "Large Language Models",
    "sql": "SQL", "mysql": "MySQL", "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "mongodb": "MongoDB", "mongo": "MongoDB", "redis": "Redis",
    "aws": "Amazon Web Services", "gcp": "Google Cloud Platform", "azure": "Microsoft Azure",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes", "docker": "Docker",
    "terraform": "Terraform", "ansible": "Ansible", "jenkins": "Jenkins",
    "ci/cd": "CI/CD", "cicd": "CI/CD", "devops": "DevOps",
    "git": "Git", "github": "GitHub", "gitlab": "GitLab",
    "java": "Java", "c++": "C++", "c#": "C#", "csharp": ".NET", "dotnet": ".NET",
    ".net": ".NET", "go": "Go", "golang": "Go", "rust": "Rust", "php": "PHP",
    "ruby": "Ruby", "rails": "Ruby on Rails", "swift": "Swift", "kotlin": "Kotlin",
    "html": "HTML", "css": "CSS", "sass": "Sass", "tailwind": "Tailwind CSS",
    "figma": "Figma", "sketch": "Sketch", "photoshop": "Adobe Photoshop",
    "illustrator": "Adobe Illustrator", "indesign": "Adobe InDesign",
    "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
    "word": "Microsoft Word", "office": "Microsoft Office", "sheets": "Google Sheets",
    "tableau": "Tableau", "power bi": "Power BI", "powerbi": "Power BI",
    "looker": "Looker", "snowflake": "Snowflake", "bigquery": "BigQuery",
    "spark": "Apache Spark", "hadoop": "Hadoop", "kafka": "Apache Kafka",
    "airflow": "Apache Airflow", "dbt": "dbt", "etl": "ETL", "elt": "ELT",
    "pandas": "pandas", "numpy": "NumPy", "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn", "tensorflow": "TensorFlow", "pytorch": "PyTorch",
    "seo": "SEO", "sem": "SEM", "ppc": "PPC", "crm": "CRM",
    "salesforce crm": "Salesforce", "hubspot": "HubSpot", "marketo": "Marketo",
    "ga4": "Google Analytics", "google analytics": "Google Analytics",
    "ats": "Applicant Tracking Systems", "hr": "Human Resources",
    "hrbp": "HR Business Partner", "recruiting": "Recruiting",
    "talent acquisition": "Talent Acquisition", "sourcing": "Talent Sourcing",
    "accounting": "Accounting", "finance": "Finance", "fp&a": "FP&A",
    "project management": "Project Management", "pmp": "Project Management",
    "agile": "Agile", "scrum": "Scrum", "kanban": "Kanban", "jira": "Jira",
    "confluence": "Confluence", "sales": "Sales", "negotiation": "Negotiation",
    "leadership": "Leadership", "communication": "Communication",
    "customer service": "Customer Service", "support": "Customer Support",
    "copywriting": "Copywriting", "content marketing": "Content Marketing",
    "email marketing": "Email Marketing", "paid media": "Paid Media",
    "ux": "UX Design", "ui": "UI Design", "user research": "User Research",
    "product management": "Product Management", "roadmap": "Product Roadmapping",
}

SKILL_CATEGORY_RULES = [
    ("Data / AI", {"python", "r", "sql", "machine learning", "artificial intelligence",
                   "deep learning", "natural language processing", "large language models",
                   "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch",
                   "tableau", "power bi", "looker", "snowflake", "bigquery",
                   "apache spark", "hadoop", "apache kafka", "apache airflow", "dbt",
                   "etl", "elt", "data analysis", "data science", "statistics"}),
    ("Engineering", {"javascript", "typescript", "node.js", "react", "vue.js", "angular",
                     "next.js", "java", "c++", "c#", ".net", "go", "rust", "php", "ruby",
                     "ruby on rails", "swift", "kotlin", "html", "css", "sass",
                     "tailwind css", "amazon web services", "google cloud platform",
                     "microsoft azure", "kubernetes", "docker", "terraform", "ansible",
                     "jenkins", "ci/cd", "devops", "git", "github", "gitlab", "mysql",
                     "postgresql", "mongodb", "redis", "rest apis", "graphql", "linux"}),
    ("Design", {"figma", "sketch", "adobe photoshop", "adobe illustrator",
                "adobe indesign", "ux design", "ui design", "user research",
                "wireframing", "prototyping", "design systems"}),
    ("Product", {"product management", "product roadmapping", "agile", "scrum",
                 "kanban", "jira", "confluence", "a/b testing", "analytics"}),
    ("Marketing", {"seo", "sem", "ppc", "content marketing", "copywriting",
                   "email marketing", "paid media", "google analytics", "crm",
                   "hubspot", "marketo", "brand marketing", "social media"}),
    ("Sales", {"sales", "negotiation", "account management", "business development",
               "salesforce", "lead generation", "cold calling", "quota"}),
    ("People / HR", {"recruiting", "talent acquisition", "talent sourcing",
                     "applicant tracking systems", "human resources",
                     "hr business partner", "onboarding", "compensation",
                     "people operations", "leadership", "communication"}),
    ("Finance", {"accounting", "finance", "fp&a", "financial modeling", "valuation",
                 "microsoft excel", "audit", "tax"}),
    ("Operations", {"project management", "operations", "supply chain", "logistics",
                    "customer service", "customer support", "process improvement"}),
]
_MAX_SKILLS = 60


def canonical_skill(skill: str) -> str:
    text = clean_text(skill).strip(" ,;|")
    if not text:
        return ""
    return SKILL_ALIASES.get(text.lower(), text)


def normalize_skills(skills) -> Tuple[str, str]:
    """Return (canonical comma string, category comma string) from any input."""
    if isinstance(skills, (list, tuple, set)):
        raw_items = [str(item) for item in skills]
    else:
        raw_items = re.split(r"[,;|]", str(skills or ""))

    canonical: List[str] = []
    seen = set()
    for item in raw_items:
        value = canonical_skill(item)
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        canonical.append(value)
        if len(canonical) >= _MAX_SKILLS:
            break

    categories: List[str] = []
    for category, members in SKILL_CATEGORY_RULES:
        if any(skill.lower() in members for skill in canonical):
            categories.append(category)
    return ", ".join(canonical), ", ".join(categories)


# ─────────────────────────────────────────────────────────────
#  Titles: seniority + department
# ─────────────────────────────────────────────────────────────

_SENIORITY_RULES = [
    (7, ("chief", "ceo", "cto", "cfo", "coo", "cmo", "cio", "cro", "chro",
         "president", "founder", "co-founder", "owner", "managing director",
         "board member", "managing partner")),
    (6, ("vp", "vice president", "svp", "evp", "avp", "head of", "global head")),
    (5, ("director", "senior director", "director of")),
    (4, ("manager", "senior manager", "program manager", "project manager",
         "team lead", "lead", "principal", "staff", "architect")),
    (3, ("senior", "sr.", "specialist", "consultant", "supervisor")),
    (2, ("associate", "analyst", "engineer", "developer", "designer", "coordinator",
         "executive", "account executive", "recruiter", "scientist", "generalist")),
    (1, ("intern", "internship", "trainee", "assistant", "apprentice", "student",
         "entry level", "junior", "jr.")),
]

SENIORITY_LABELS = {
    1: "Intern / Entry", 2: "Individual Contributor", 3: "Senior IC",
    4: "Lead / Manager", 5: "Director", 6: "VP / Head", 7: "C-Suite / Founder",
}

_DEPARTMENT_RULES = [
    ("Engineering", ("engineer*", "develop*", "software", "sre", "devops",
                     "architect", "programmer", "full stack", "frontend", "backend")),
    ("Data / AI", ("data", "machine learning", "ml", "ai", "scientist",
                   "analytics", "analyst")),
    ("Product", ("product manager", "product owner", "product lead", "product")),
    ("Design", ("design*", "ux", "ui", "creative", "art director")),
    ("Marketing", ("marketing", "growth", "seo", "content", "brand", "communications",
                   "social media")),
    ("Sales", ("sales", "account executive", "business development", "revenue",
               "partnerships", "customer success")),
    ("People / HR", ("recruit*", "talent", "human resources", "hr", "people",
                     "sourcer", "compensation")),
    ("Finance", ("finance", "financial", "accounting", "controller", "treasurer",
                 "audit", "tax", "investment")),
    ("Legal", ("legal", "counsel", "attorney", "paralegal", "compliance")),
    ("Operations", ("operations", "supply chain", "logistics", "procurement",
                    "program")),
    ("Support", ("support", "service desk", "help desk", "technical support")),
]


_WORD_RE_CACHE: Dict[str, re.Pattern] = {}


def _has_keyword(text: str, keyword: str) -> bool:
    """Whole-word match for single tokens, substring for phrases.

    Substring matching alone is a trap here: "ui" is inside "acquisition" and
    "cro" is inside "microbiologist", which silently sent recruiters to the
    Design department.  Single words get a word-boundary regex; multi-word
    keywords are specific enough to match as substrings.  A trailing ``*``
    means "word prefix", so ``recruit*`` matches recruiter/recruiting.
    """
    keyword = keyword.strip()
    if not keyword:
        return False
    if " " in keyword and not keyword.endswith("*"):
        return keyword in text
    prefix = keyword.endswith("*")
    stem = keyword.rstrip("*").rstrip()
    if not stem:
        return False
    cache_key = ("prefix:" if prefix else "word:") + stem
    pattern = _WORD_RE_CACHE.get(cache_key)
    if pattern is None:
        tail = "" if prefix else r"(?![a-z0-9])"
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(stem) + tail)
        _WORD_RE_CACHE[cache_key] = pattern
    return bool(pattern.search(text))


def _matches(text: str, keywords: Iterable[str]) -> bool:
    return any(_has_keyword(text, keyword) for keyword in keywords)


def seniority_rank(title: str) -> int:
    """1 (intern) .. 7 (C-suite). 0 when the title is empty."""
    text = f" {clean_text(title).lower()} "
    if not text.strip():
        return 0
    for rank, keywords in _SENIORITY_RULES:
        if _matches(text, keywords):
            return rank
    return 2


def seniority_label(rank: int) -> str:
    return SENIORITY_LABELS.get(int(rank or 0), "Unknown")


def department_of(title: str) -> str:
    text = f" {clean_text(title).lower()} "
    for department, keywords in _DEPARTMENT_RULES:
        if _matches(text, keywords):
            return department
    return "Other"


# ─────────────────────────────────────────────────────────────
#  Record-level enrichment
# ─────────────────────────────────────────────────────────────

_COMPLETENESS_WEIGHTS = [
    ("name", 8), ("title", 10), ("company", 10), ("location", 8),
    ("about", 8), ("skills", 8), ("experience", 10), ("education", 6),
    ("email", 14), ("photo_url", 4), ("connections", 4), ("followers", 4),
    ("certifications", 3), ("languages", 3),
]


def profile_completeness(record: dict) -> int:
    """0-100 weighted score of how much usable information a record holds."""
    earned = 0.0
    total = 0
    for key, weight in _COMPLETENESS_WEIGHTS:
        total += weight
        value = record.get(key)
        if isinstance(value, bool):
            earned += weight if value else 0
        elif isinstance(value, (int, float)):
            earned += weight if value else 0
        elif str(value or "").strip():
            earned += weight
    return int(round(100 * earned / total)) if total else 0


def dedupe_key(record: dict) -> str:
    """Stable identity for cross-run dedupe.

    Priority: public profile id -> canonical URL slug -> name+company slug ->
    hash of the remaining identity fields.  The key never depends on mutable
    fields (title, followers), so an updated profile maps to the same row.
    """
    public_id = str(record.get("public_id") or "").strip().lower()
    if public_id:
        return public_id
    url_id = profile_public_id(record.get("linkedin_url") or "")
    if url_id:
        return url_id
    name = _ascii_slug(record.get("name") or "")
    company = _ascii_slug(normalize_company(record.get("company") or ""))
    if name or company:
        return f"{name}@{company}" if company else name
    blob = json.dumps(
        {k: record.get(k) for k in ("name", "title", "company", "location")},
        sort_keys=True, ensure_ascii=False,
    )
    return "hash:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def quality_flags(record: dict) -> List[str]:
    """Short machine-readable list of things a human should double-check."""
    flags: List[str] = []
    if not record.get("email"):
        flags.append("no_email")
    if not record.get("location"):
        flags.append("no_location")
    if not record.get("title"):
        flags.append("no_title")
    if not record.get("company"):
        flags.append("no_company")
    if profile_completeness(record) < 50:
        flags.append("low_completeness")
    if record.get("email_status") == "inferred":
        flags.append("email_inferred")
    if record.get("is_hiring") and not record.get("title"):
        flags.append("hiring_without_title")
    return flags


_MERGE_LIST_FIELDS = ("skills", "certifications", "languages", "websites")


def merge_record(existing: dict, incoming: dict) -> dict:
    """Merge a re-scraped record into a stored one without losing data.

    Incoming non-empty values win (the profile is fresher), existing values
    survive when the new scrape came back blank, list-like fields are unioned,
    and first-seen metadata is preserved by the caller.
    """
    merged = dict(existing)
    for key, value in (incoming or {}).items():
        if key in ("first_seen", "last_seen", "first_run", "last_run", "seen_count"):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if value is None:
            continue
        if key in _MERGE_LIST_FIELDS:
            parts = []
            for source in (existing.get(key), value):
                for item in str(source or "").split(","):
                    item = item.strip()
                    if item and item not in parts:
                        parts.append(item)
            merged[key] = ", ".join(parts)
            continue
        merged[key] = value
    return merged


def enrich_record(record: dict, *, infer_email: bool = True,
                  domains: Optional[Dict[str, str]] = None,
                  now: Optional[str] = None) -> dict:
    """Add derived screening fields to a raw scraped record.

    Returns a new dict; input is not mutated.  Existing keys keep their value
    unless the enrichment produces a strictly better one (e.g. ``phone_e164``
    is new, ``name`` is only repaired for mojibake/whitespace).
    """
    # Long-form fields keep their line breaks (About / Work History are prose);
    # serialized JSON columns are passed through untouched.
    keep_newlines = {
        "about", "experience", "education", "recommendations", "projects",
        "publications", "honors", "services", "volunteer", "interests",
    }
    passthrough = {"experience_roles", "education_entries"}
    out = {}
    for key, value in dict(record or {}).items():
        if isinstance(value, str) and key not in passthrough:
            out[key] = clean_text(value, keep_newlines=key in keep_newlines)
        else:
            out[key] = value

    name = normalize_name_case(out.get("name") or "")
    parsed = parse_name(name)
    out["name"] = name or out.get("name", "")
    out["name_first"] = parsed["first"]
    out["name_last"] = parsed["last"]
    out["name_middle"] = parsed["middle"]
    out["name_suffix"] = parsed["suffix"]

    location = split_location(out.get("location") or "")
    out["city"] = location["city"]
    out["region"] = location["region"]
    out["region_code"] = location["region_code"]
    out["country"] = location["country"]
    out["country_code"] = location["country_code"]
    out["is_remote_location"] = location["is_remote"]
    out["is_metro_location"] = location["is_metro"]

    out["company_clean"] = normalize_company(out.get("company") or out.get("search_company") or "")
    domain, domain_source, domain_confidence = infer_company_domain(out, domains)
    out["company_domain"] = domain
    out["domain_source"] = domain_source
    out["domain_confidence"] = domain_confidence

    title = out.get("title") or out.get("headline") or ""
    rank = seniority_rank(title)
    out["seniority_rank"] = rank
    out["seniority_label"] = seniority_label(rank)
    out["department"] = department_of(title)

    raw_phone = clean_text(out.get("phone") or "")
    e164 = normalize_phone(raw_phone)
    # Legacy scrapes stored a bare digit run (an internal id) as "phone".
    # Keep human-formatted raw values for reference, drop unparseable digit runs.
    if not e164 and raw_phone and re.fullmatch(r"\+?\d{7,15}", raw_phone):
        raw_phone = ""
    out["phone"] = raw_phone
    out["phone_e164"] = e164
    out["phone_ext"] = phone_extension(raw_phone)

    skills, categories = normalize_skills(out.get("skills") or "")
    out["skills_normalized"] = skills
    out["skill_categories"] = categories

    scraped_email = clean_text(out.get("email") or "").lower()
    candidates: List[dict] = []
    if scraped_email and "@" in scraped_email:
        out["email"] = scraped_email
        out["email_status"] = "scraped"
        out["email_confidence"] = 1.0
        candidates.append({
            "email": scraped_email, "pattern": "scraped",
            "confidence": 1.0, "verified": True,
        })
    else:
        out["email"] = ""
        out["email_status"] = "none"
        out["email_confidence"] = 0.0
        if infer_email and domain:
            candidates = infer_emails(parsed["first"], parsed["last"], domain)
            if candidates:
                out["email_status"] = "inferred"
                out["email_confidence"] = candidates[0]["confidence"]
    out["email_candidates"] = json.dumps(candidates[:8], ensure_ascii=False) if candidates else ""

    out["completeness_score"] = profile_completeness(out)
    # Derive the public id from the profile URL when the scraper did not pass
    # one; it is a fact from the URL, not an invented identifier.
    out["public_id"] = str(out.get("public_id") or "").strip().lower() or \
        profile_public_id(out.get("linkedin_url") or "")
    out["dedupe_key"] = dedupe_key(out)
    out["quality_flags"] = json.dumps(quality_flags(out), ensure_ascii=False)
    out["enriched_at"] = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return out


def summarize_record(record: dict) -> str:
    """One-line human summary used by the CLI/MCP tools."""
    name = record.get("name") or "Unknown"
    title = record.get("title") or "?"
    company = record.get("company") or record.get("search_company") or "?"
    location = record.get("location") or "?"
    score = record.get("match_score")
    suffix = f" | match {score:.0f}" if isinstance(score, (int, float)) else ""
    return f"{name} - {title} @ {company} ({location}){suffix}"
