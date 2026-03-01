"""
Configuration for LinkedIn Recruiter Scraper
=============================================
Edit these settings before running the scraper.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # ─────────────────────────────────────────────
    #  🔐 CREDENTIALS (Required)
    # ─────────────────────────────────────────────
    LINKEDIN_EMAIL: str = os.getenv("LINKEDIN_EMAIL", "your-gmail@gmail.com")
    LINKEDIN_PASSWORD: str = os.getenv("LINKEDIN_PASSWORD", "password here")

    # ─────────────────────────────────────────────
    #  🖥️  BROWSER SETTINGS
    # ─────────────────────────────────────────────
    HEADLESS: bool = False          # False = show browser (recommended for debugging)
                                    # True  = run hidden (faster, but riskier)

    USER_AGENTS: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ])

    # ─────────────────────────────────────────────
    #  ⏱️  TIMING (Critical for avoiding detection)
    # ─────────────────────────────────────────────
    MIN_DELAY_BETWEEN_PROFILES: float = 8.0     # seconds (minimum wait between profiles)
    MAX_DELAY_BETWEEN_PROFILES: float = 18.0    # seconds (maximum wait between profiles)

    MIN_SESSION_BREAK_SECONDS: int = 60         # min break every N requests
    MAX_SESSION_BREAK_SECONDS: int = 180        # max break every N requests
    BREAK_EVERY_N_REQUESTS: int = 15            # take a break every X requests

    MAX_PAGES_PER_COMPANY: int = 5              # max search result pages per company
                                                # (each page = ~10 profiles)

    # ─────────────────────────────────────────────
    #  📁 FILE PATHS
    # ─────────────────────────────────────────────
    OUTPUT_FILE: str = "output/linkedin_recruiters.xlsx"
    PROGRESS_FILE: str = "output/progress.json"
    SESSION_DIR: str = "session/"
    LOG_FILE: str = "output/scraper.log"

    # ─────────────────────────────────────────────
    #  🔧 ADVANCED SETTINGS
    # ─────────────────────────────────────────────
    MAX_RETRIES: int = 3                        # retries on network errors
    REQUEST_TIMEOUT_MS: int = 30000            # page load timeout in ms

    # Recruiter title keywords to match
    RECRUITER_KEYWORDS: List[str] = field(default_factory=lambda: [
        "recruiter", "recruiting", "talent acquisition",
        "talent sourcer", "sourcer", "staffing",
        "headhunter", "talent partner", "people operations",
        "technical recruiter", "campus recruiter",
        "executive recruiter", "hr generalist"
    ])
