"""
╔══════════════════════════════════════════════════════════════╗
║       LinkedIn Scraper v3.0 — Configuration                  ║
║                                                              ║
║  ✏️  ONLY EDIT THIS FILE to customize your scrape            ║
║  Everything else runs automatically                          ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # ══════════════════════════════════════════════
    #  🔐 STEP 1 — YOUR LINKEDIN CREDENTIALS
    # ══════════════════════════════════════════════
    LINKEDIN_EMAIL: str    = os.getenv("LINKEDIN_EMAIL",    "ddaaveepranav@gmail.com")
    LINKEDIN_PASSWORD: str = os.getenv("LINKEDIN_PASSWORD", "Badboy@612")


    # ══════════════════════════════════════════════
    #  🎯 STEP 2 — SEARCH MODE
    #
    #  Choose what you want to search for:
    #    "people"      → Find people by job title + company
    #    "jobs"        → Find job listings by keyword
    #    "candidates"  → Find candidates by skills/resume keywords
    # ══════════════════════════════════════════════

    SEARCH_MODE: str = "people"   # "people", "jobs", or "candidates"


    # ══════════════════════════════════════════════
    #  👤 PEOPLE SEARCH — Job Titles to Search
    #
    #  Used when SEARCH_MODE = "people"
    #  Uncomment the titles you want!
    # ══════════════════════════════════════════════

    JOB_TITLES: List[str] = field(default_factory=lambda: [

        # ── Recruiting / HR ──────────────────────
        "Recruiter",
        "Technical Recruiter",
        "Talent Acquisition",
        "HR Manager",
        "People Operations",

        # ── Engineering ──────────────────────────
        # "Software Engineer",
        # "Frontend Developer",
        # "Backend Developer",
        # "DevOps Engineer",
        # "Data Engineer",

        # ── Data / AI ────────────────────────────
        # "Data Scientist",
        # "Machine Learning Engineer",
        # "AI Engineer",
        # "Data Analyst",
        # "Business Intelligence Analyst",

        # ── Marketing ────────────────────────────
        # "Marketing Manager",
        # "Growth Hacker",
        # "SEO Specialist",
        # "Content Strategist",
        # "Brand Manager",

        # ── Sales ────────────────────────────────
        # "Sales Manager",
        # "Account Executive",
        # "Business Development Manager",
        # "Sales Director",
        # "VP of Sales",

        # ── Finance ──────────────────────────────
        # "CFO",
        # "Financial Analyst",
        # "Investment Banker",
        # "Portfolio Manager",
        # "Risk Analyst",

        # ── Design ───────────────────────────────
        # "UX Designer",
        # "Product Designer",
        # "Graphic Designer",
        # "UI Developer",

        # ── Leadership / C-Suite ─────────────────
        # "CEO",
        # "CTO",
        # "COO",
        # "VP of Engineering",
        # "Director of Product",

        # ── Healthcare ───────────────────────────
        # "Nurse",
        # "Doctor",
        # "Physician",
        # "Healthcare Administrator",

        # ── Legal ────────────────────────────────
        # "Lawyer",
        # "Legal Counsel",
        # "Paralegal",
        # "Compliance Officer",

    ])


    # ══════════════════════════════════════════════
    #  💼 JOB SEARCH — Keywords & Filters
    #
    #  Used when SEARCH_MODE = "jobs"
    # ══════════════════════════════════════════════

    JOB_SEARCH_KEYWORDS: List[str] = field(default_factory=lambda: [
        "Python Developer",
        "Data Engineer",
        "Machine Learning"
    ])

    # LinkedIn Native Filters (Empty string "" means no filter)
    JOB_EXPERIENCE_LEVEL: str = "2" # 2=Entry, 3=Associate, 4=Mid-Senior, 5=Director
    JOB_DATE_POSTED: str      = "r86400" # "r86400"=Past 24 Hours, "r604800"=Past Week, "r2592000"=Past Month
    JOB_REMOTE_FILTER: str    = "2" # 1=On-site, 2=Remote, 3=Hybrid

    # ─────────────────────────────────────────────
    #  SMART RESUME MATCH & STRICT TIME FILTER (Option 4)
    # ─────────────────────────────────────────────
    # Exact hour filter (e.g. 5 means ONLY keep jobs posted <= 5 hours ago). Set to None to disable.
    JOB_STRICT_HOURS_FILTER: int = None

    # Path to your resume (PDF or DOCX) for extracting keywords
    RESUME_FILE_PATH: str = "my_resume.pdf"

    MAX_JOB_PAGES: int = 5  # Max search result pages per keyword


    # ══════════════════════════════════════════════
    #  👤 CANDIDATE SEARCH — Skills & Filters
    #
    #  Used when SEARCH_MODE = "candidates"
    #  Searches for people whose profile matches
    #  these skill/keyword combos.
    # ══════════════════════════════════════════════

    CANDIDATE_SKILLS: List[str] = field(default_factory=lambda: [
        "Python",
        "Machine Learning",
        # Add skills to search for...
    ])

    CANDIDATE_TITLE_FILTER: str = ""  # Optional: filter by current title (e.g. "Engineer")

    MAX_CANDIDATE_PAGES: int = 5  # Max pages per skill search


    # ══════════════════════════════════════════════
    #  🌍 LOCATION
    #
    #  Change GEO_URN to target a different country.
    # ══════════════════════════════════════════════

    SEARCH_LOCATION_NAME: str = "United States"

    # Common GeoURNs:
    # United States   → 103644278   ✅ (default)
    # United Kingdom  → 101165590
    # Canada          → 101174742
    # Australia       → 101452733
    # India           → 102713980
    # Germany         → 101282230
    # France          → 105015875
    # Singapore       → 102454443
    # New York City   → 105080838
    # San Francisco   → 102277331
    # London          → 102257491
    GEO_URN: str = "103644278"


    # ══════════════════════════════════════════════
    #  🔍 EXTRA KEYWORD FILTER (Optional)
    #
    #  Only save profiles whose title contains
    #  one of these words. Leave empty [] for all.
    # ══════════════════════════════════════════════

    FILTER_KEYWORDS: List[str] = field(default_factory=lambda: [
        # Examples: "senior", "lead", "manager", "director"
    ])


    # ══════════════════════════════════════════════
    #  📁 OUTPUT FILES
    # ══════════════════════════════════════════════

    OUTPUT_FILE: str        = "output/linkedin_results.xlsx"
    JOBS_OUTPUT_FILE: str   = "output/linkedin_jobs.xlsx"
    CANDIDATES_OUTPUT_FILE: str = "output/linkedin_candidates.xlsx"
    PROGRESS_FILE: str      = "output/progress.json"
    SESSION_DIR: str        = "session/"
    LOG_FILE: str           = "output/scraper.log"


    # ══════════════════════════════════════════════
    #  ⏱️  TIMING (Don't lower these — ban risk!)
    # ══════════════════════════════════════════════

    MIN_DELAY_BETWEEN_PROFILES: float = 8.0
    MAX_DELAY_BETWEEN_PROFILES: float = 18.0
    MIN_SESSION_BREAK_SECONDS: int    = 60
    MAX_SESSION_BREAK_SECONDS: int    = 180
    BREAK_EVERY_N_REQUESTS: int       = 15
    MAX_PAGES_PER_COMPANY: int        = 5


    # ══════════════════════════════════════════════
    #  🛡️  ANTI-BLOCK SETTINGS
    # ══════════════════════════════════════════════

    MAX_DAILY_SEARCHES: int     = 80      # Stop after this many searches per day
    ROTATE_USER_AGENT: bool     = True    # Rotate UA every few requests
    UA_ROTATE_EVERY_N: int      = 10      # Rotate user agent every N requests
    ADAPTIVE_THROTTLE: bool     = True    # Auto-increase delays on errors
    CONSECUTIVE_ERROR_LIMIT: int = 3      # Errors before throttle kicks in

    # Optional: Proxy list for IP rotation
    # Example: ["http://user:pass@proxy1:8080", "http://user:pass@proxy2:8080"]
    PROXY_LIST: List[str] = field(default_factory=lambda: [])


    # ══════════════════════════════════════════════
    #  🖥️  BROWSER SETTINGS
    # ══════════════════════════════════════════════

    HEADLESS: bool         = False
    MAX_RETRIES: int       = 3
    REQUEST_TIMEOUT_MS: int = 30000

    USER_AGENTS: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ])
