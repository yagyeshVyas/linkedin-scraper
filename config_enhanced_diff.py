--- config_enhanced.py (原始)


+++ config_enhanced.py (修改后)
"""
╔══════════════════════════════════════════════════════════════╗
║       LinkedIn Scraper v4.0 — Enhanced Configuration         ║
║                                                              ║
║  ✏️  ONLY EDIT THIS FILE to customize your scrape            ║
║  Everything else runs automatically                          ║
║                                                              ║
║  🆕 NEW in v4.0:                                             ║
║  • Encrypted credential storage                              ║
║  • Advanced anti-detection settings                          ║
║  • AI-powered resume parsing                                 ║
║  • Real-time monitoring dashboard                            ║
║  • Email finder integration                                  ║
║  • Smart rate limiting                                       ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from pathlib import Path


@dataclass
class Config:
    # ══════════════════════════════════════════════
    #  🔐 STEP 1 — CREDENTIALS & SECURITY
    # ══════════════════════════════════════════════

    # Option A: Environment variables (RECOMMENDED for security)
    LINKEDIN_EMAIL: str    = os.getenv("LINKEDIN_EMAIL", "")
    LINKEDIN_PASSWORD: str = os.getenv("LINKEDIN_PASSWORD", "")

    # Option B: Encrypted vault (for production)
    # Set USE_CREDENTIAL_VAULT=True and provide vault path
    USE_CREDENTIAL_VAULT: bool = False
    CREDENTIAL_VAULT_PATH: str = "secure/credentials.vault"

    # Security settings
    ENCRYPT_LOCAL_STORAGE: bool = True  # Encrypt saved session cookies
    AUTO_CLEAR_CREDENTIALS: bool = True  # Clear credentials from memory after login

    # Account safety
    MAX_DAILY_SEARCHES: int     = 80      # Hard limit to prevent bans
    MAX_HOURLY_SEARCHES: int    = 20      # Soft limit for rate limiting
    SAFETY_COOLDOWN_MINUTES: int = 30     # Break after hitting hourly limit

    # ══════════════════════════════════════════════
    #  🎯 STEP 2 — SEARCH MODE
    # ══════════════════════════════════════════════

    SEARCH_MODE: str = "people"  # "people", "jobs", "candidates", "companies"

    # Multi-threading (advanced)
    ENABLE_CONCURRENT_SEARCHES: bool = False  # Experimental: search multiple companies simultaneously
    MAX_CONCURRENT_WORKERS: int = 3  # Number of parallel browser instances

    # ══════════════════════════════════════════════
    #  👤 PEOPLE SEARCH — Job Titles
    # ══════════════════════════════════════════════

    JOB_TITLES: List[str] = field(default_factory=lambda: [
        # ── Recruiting / HR ──────────────────────
        "Recruiter",
        "Technical Recruiter",
        "Talent Acquisition Partner",
        "Talent Acquisition Manager",
        "HR Manager",
        "People Operations Manager",
        "Head of Talent",

        # ── Engineering ──────────────────────────
        # "Software Engineer",
        # "Senior Software Engineer",
        # "Staff Engineer",
        # "Principal Engineer",
        # "Frontend Developer",
        # "Backend Developer",
        # "Full Stack Developer",
        # "DevOps Engineer",
        # "Site Reliability Engineer",
        # "Data Engineer",
        # "ML Engineer",

        # ── Data / AI ────────────────────────────
        # "Data Scientist",
        # "Senior Data Scientist",
        # "Machine Learning Engineer",
        # "AI Research Scientist",
        # "Data Analyst",
        # "Business Intelligence Analyst",
        # "Analytics Manager",

        # ── Marketing ────────────────────────────
        # "Marketing Manager",
        # "Growth Marketing Manager",
        # "Digital Marketing Specialist",
        # "SEO Manager",
        # "Content Marketing Manager",
        # "Brand Manager",
        # "Product Marketing Manager",

        # ── Sales ────────────────────────────────
        # "Account Executive",
        # "Sales Development Representative",
        # "Business Development Manager",
        # "Sales Director",
        # "VP of Sales",
        # "Chief Revenue Officer",

        # ── Product ──────────────────────────────
        # "Product Manager",
        # "Senior Product Manager",
        # "Director of Product",
        # "VP of Product",
        # "Chief Product Officer",

        # ── Design ───────────────────────────────
        # "UX Designer",
        # "Senior UX Designer",
        # "Product Designer",
        # "UI Designer",
        # "Design Director",

        # ── Leadership / C-Suite ─────────────────
        # "CEO",
        # "CTO",
        # "COO",
        # "CFO",
        # "VP of Engineering",
        # "Director of Engineering",

        # ── Healthcare ───────────────────────────
        # "Registered Nurse",
        # "Physician",
        # "Healthcare Administrator",
        # "Medical Director",

        # ── Finance ──────────────────────────────
        # "Financial Analyst",
        # "Investment Banker",
        # "Portfolio Manager",
        # "Risk Manager",
        # "CFO",

        # ── Legal ────────────────────────────────
        # "General Counsel",
        # "Corporate Lawyer",
        # "Compliance Officer",
        # "Legal Counsel",
    ])

    # Seniority filter (only save profiles with these keywords)
    FILTER_KEYWORDS: List[str] = field(default_factory=lambda: [
        # Examples: "senior", "lead", "principal", "director", "vp", "head of"
    ])

    # Exclude keywords (skip profiles containing these)
    EXCLUDE_KEYWORDS: List[str] = field(default_factory=lambda: [
        # Examples: "intern", "junior", "freelance", "contractor"
    ])

    # ══════════════════════════════════════════════
    #  💼 JOB SEARCH — Keywords & Filters
    # ══════════════════════════════════════════════

    JOB_SEARCH_KEYWORDS: List[str] = field(default_factory=lambda: [
        "Python Developer",
        "Data Engineer",
        "Machine Learning Engineer",
        "Product Manager",
        "UX Designer"
    ])

    # LinkedIn Native Filters
    JOB_EXPERIENCE_LEVEL: str = ""  # ""=All, "1"=Internship, "2"=Entry, "3"=Associate, "4"=Mid-Senior, "5"=Director, "6"=Executive
    JOB_DATE_POSTED: str      = ""  # ""=Anytime, "r86400"=Past 24h, "r604800"=Past Week, "r2592000"=Past Month
    JOB_REMOTE_FILTER: str    = ""  # ""=All, "1"=On-site, "2"=Remote, "3"=Hybrid

    # Smart time filtering for resume matching
    JOB_STRICT_HOURS_FILTER: Optional[int] = None  # e.g., 24 = only jobs posted within 24 hours

    # Resume file for smart job matching
    RESUME_FILE_PATH: str = "my_resume.pdf"

    # Enable AI-powered skill extraction from resume
    ENABLE_AI_RESUME_PARSING: bool = True
    AI_MODEL_TYPE: str = "keyword"  # "keyword" or "llm" (requires API key)

    # LLM API settings (if using AI parsing)
    LLM_API_PROVIDER: str = "openai"  # "openai", "anthropic", "local"
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = "gpt-4o-mini"

    MAX_JOB_PAGES: int = 5

    # Job alerts (experimental)
    ENABLE_JOB_ALERTS: bool = False
    ALERT_EMAIL: str = ""
    ALERT_WEBHOOK_URL: str = ""

    # ══════════════════════════════════════════════
    #  👤 CANDIDATE SEARCH — Skills & Filters
    # ══════════════════════════════════════════════

    CANDIDATE_SKILLS: List[str] = field(default_factory=lambda: [
        "Python",
        "Machine Learning",
        "React",
        "AWS",
        "Kubernetes",
        "Data Science",
        "Product Management",
    ])

    CANDIDATE_TITLE_FILTER: str = ""  # Optional: filter by current title
    CANDIDATE_MIN_CONNECTIONS: int = 0  # Minimum connections required
    MAX_CANDIDATE_PAGES: int = 5

    # Candidate enrichment
    ENABLE_EMAIL_ENRICHMENT: bool = False  # Use email finder APIs
    EMAIL_FINDER_PROVIDER: str = "hunter"  # "hunter", "snovio", "rocketreach", "clearbit"
    EMAIL_FINDER_API_KEY: str = os.getenv("EMAIL_FINDER_API_KEY", "")

    # ══════════════════════════════════════════════
    #  🏢 COMPANY SEARCH (NEW in v4.0)
    # ══════════════════════════════════════════════

    COMPANY_INDUSTRY_FILTER: str = ""  # e.g., "Technology", "Finance", "Healthcare"
    COMPANY_SIZE_FILTER: str = ""  # e.g., "1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001+"
    COMPANY_LOCATION_FILTER: str = ""  # Same as SEARCH_LOCATION_NAME

    # Company data enrichment
    ENABLE_COMPANY_ENRICHMENT: bool = False  # Fetch company details from external APIs
    COMPANY_DATA_PROVIDER: str = "clearbit"  # "clearbit", "crunchbase", "linkedin"
    COMPANY_DATA_API_KEY: str = os.getenv("COMPANY_DATA_API_KEY", "")

    # ══════════════════════════════════════════════
    #  🌍 LOCATION SETTINGS
    # ══════════════════════════════════════════════

    SEARCH_LOCATION_NAME: str = "United States"

    # Common GeoURNs:
    # United States   → 103644278
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
    # Berlin          → 106967730
    # Paris           → 105015875
    # Tokyo           → 101337137
    # Sydney          → 101401490
    GEO_URN: str = "103644278"

    # Multiple locations (search all in one run)
    ENABLE_MULTI_LOCATION: bool = False
    LOCATIONS_TO_SEARCH: List[Dict[str, str]] = field(default_factory=lambda: [
        # {"name": "United States", "geo_urn": "103644278"},
        # {"name": "United Kingdom", "geo_urn": "101165590"},
        # {"name": "Canada", "geo_urn": "101174742"},
    ])

    # ══════════════════════════════════════════════
    #  📁 OUTPUT & STORAGE
    # ══════════════════════════════════════════════

    OUTPUT_DIR: str = "output/"
    OUTPUT_FILE: str        = "output/linkedin_results.xlsx"
    JOBS_OUTPUT_FILE: str   = "output/linkedin_jobs.xlsx"
    CANDIDATES_OUTPUT_FILE: str = "output/linkedin_candidates.xlsx"
    COMPANIES_OUTPUT_FILE: str = "output/linkedin_companies.xlsx"
    PROGRESS_FILE: str      = "output/progress.json"
    SESSION_DIR: str        = "session/"
    LOG_FILE: str           = "output/scraper.log"

    # Export formats
    EXPORT_FORMATS: List[str] = field(default_factory=lambda: ["xlsx", "csv", "json"])
    EXPORT_RAW_HTML: bool = False  # Save raw HTML for debugging

    # Database export (experimental)
    ENABLE_DATABASE_EXPORT: bool = False
    DATABASE_TYPE: str = "sqlite"  # "sqlite", "postgresql", "mongodb"
    DATABASE_CONNECTION_STRING: str = ""

    # Cloud storage (experimental)
    ENABLE_CLOUD_BACKUP: bool = False
    CLOUD_PROVIDER: str = "aws_s3"  # "aws_s3", "gcp_gcs", "azure_blob"
    CLOUD_BUCKET_NAME: str = ""
    CLOUD_ACCESS_KEY: str = os.getenv("CLOUD_ACCESS_KEY", "")
    CLOUD_SECRET_KEY: str = os.getenv("CLOUD_SECRET_KEY", "")

    # ══════════════════════════════════════════════
    #  ⏱️  TIMING & PERFORMANCE
    # ══════════════════════════════════════════════

    # Base delays (DO NOT reduce below these values!)
    MIN_DELAY_BETWEEN_PROFILES: float = 8.0
    MAX_DELAY_BETWEEN_PROFILES: float = 18.0

    # Session management
    MIN_SESSION_BREAK_SECONDS: int    = 60
    MAX_SESSION_BREAK_SECONDS: int    = 180
    BREAK_EVERY_N_REQUESTS: int       = 15

    # Pagination
    MAX_PAGES_PER_COMPANY: int        = 5
    MAX_RESULTS_PER_SEARCH: int       = 100  # Stop after this many results per search

    # Adaptive throttling
    ADAPTIVE_THROTTLE: bool     = True
    CONSECUTIVE_ERROR_LIMIT: int = 3
    ERROR_COOLDOWN_MULTIPLIER: float = 2.0  # Increase delay by 2x after each error

    # Connection settings
    REQUEST_TIMEOUT_MS: int = 30000
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_FACTOR: float = 1.5  # Exponential backoff multiplier

    # ══════════════════════════════════════════════
    #  🛡️  ADVANCED ANTI-DETECTION
    # ══════════════════════════════════════════════

    # User agent rotation
    ROTATE_USER_AGENT: bool     = True
    UA_ROTATE_EVERY_N: int      = 10

    # Browser fingerprinting
    RANDOMIZE_VIEWPORT: bool = True
    VIEWPORT_WIDTH_RANGE: tuple = (1920, 2560)
    VIEWPORT_HEIGHT_RANGE: tuple = (1080, 1440)

    RANDOMIZE_TIMEZONE: bool = True
    TIMEZONE_LIST: List[str] = field(default_factory=lambda: [
        "America/New_York",
        "America/Chicago",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
    ])

    # Mouse movement simulation
    SIMULATE_MOUSE_MOVEMENT: bool = True
    MOUSE_MOVEMENT_CURVE: str = "bezier"  # "bezier", "linear", "random"

    # Keyboard simulation
    SIMULATE_KEYBOARD_TIMING: bool = True
    KEYSTROKE_DELAY_RANGE: tuple = (50, 200)  # milliseconds

    # Canvas fingerprint spoofing
    SPOOF_CANVAS_FINGERPRINT: bool = True

    # WebGL vendor spoofing
    SPOOF_WEBGL_VENDOR: bool = True

    # Audio context fingerprint spoofing
    SPOOF_AUDIO_FINGERPRINT: bool = True

    # Font enumeration spoofing
    SPOOF_FONTS: bool = True

    # Screen resolution spoofing
    SPOOF_SCREEN_RESOLUTION: bool = True

    # Hardware concurrency spoofing (CPU cores)
    SPOOF_HARDWARE_CONCURRENCY: bool = True

    # Device memory spoofing (RAM)
    SPOOF_DEVICE_MEMORY: bool = True

    # Touch support spoofing
    SPOOF_TOUCH_SUPPORT: bool = False  # Keep false for desktop

    # Cookie persistence
    PERSIST_COOKIES_ACROSS_SESSIONS: bool = True
    COOKIE_MAX_AGE_DAYS: int = 7

    # Referrer spoofing
    SPOOF_REFERRER: bool = True
    REFERRER_SOURCES: List[str] = field(default_factory=lambda: [
        "https://www.google.com/",
        "https://www.bing.com/",
        "https://duckduckgo.com/",
        "https://www.linkedin.com/feed/",
    ])

    # Language spoofing
    ACCEPT_LANGUAGE: str = "en-US,en;q=0.9"

    # DNT (Do Not Track) header
    SEND_DNT_HEADER: bool = False  # Setting true can be a fingerprint

    # Connection header spoofing
    SPOOF_CONNECTION_HEADERS: bool = True

    # TLS fingerprint randomization (advanced)
    RANDOMIZE_TLS_FINGERPRINT: bool = False  # Requires special library

    # Request ordering randomization
    RANDOMIZE_RESOURCE_LOADING: bool = True

    # Idle detection bypass
    BYPASS_IDLE_DETECTION: bool = True

    # Automation detector bypass
    BYPASS_AUTOMATION_DETECTORS: bool = True

    # CAPTCHA handling
    CAPTCHA_SOLVER_ENABLED: bool = False
    CAPTCHA_SOLVER_PROVIDER: str = "2captcha"  # "2captcha", "anticaptcha", "capmonster"
    CAPTCHA_SOLVER_API_KEY: str = os.getenv("CAPTCHA_SOLVER_API_KEY", "")
    CAPTCHA_MAX_RETRY: int = 3

    # Honeypot detection (detect if LinkedIn sets traps)
    DETECT_HONEYPOTS: bool = True

    # Behavioral biometrics simulation
    SIMULATE_READING_TIME: bool = True
    MIN_READING_TIME_PER_PROFILE: float = 3.0  # seconds

    SIMULATE_SCROLL_PATTERNS: bool = True
    SCROLL_PATTERN_TYPES: List[str] = field(default_factory=lambda: [
        "gradual", "burst", "pause_and_scroll", "back_and_forth"
    ])

    # Random action injection (click random elements, etc.)
    INJECT_RANDOM_ACTIONS: bool = False
    RANDOM_ACTION_FREQUENCY: int = 20  # Every N requests

    # ══════════════════════════════════════════════
    #  🖥️  BROWSER SETTINGS
    # ══════════════════════════════════════════════

    HEADLESS: bool = False  # Always False for better stealth

    # Browser type
    BROWSER_TYPE: str = "chromium"  # "chromium", "firefox", "webkit"

    # Chromium-specific settings
    CHROMIUM_ARGS: List[str] = field(default_factory=lambda: [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--start-maximized",
        "--disable-gpu",  # Can help with detection
        "--disable-software-rasterizer",
    ])

    # Firefox-specific settings (if using firefox)
    FIREFOX_PREFS: Dict[str, str] = field(default_factory=lambda: {
        "general.useragent.override": "",
        "privacy.resistFingerprinting": "true",
    })

    # Browser context settings
    LOCALE: str = "en-US"
    DEFAULT_TIMEZONE: str = "America/New_York"
    DEFAULT_GEOLOCATION: Dict[str, float] = field(default_factory=lambda: {
        "longitude": -73.9857,
        "latitude": 40.7484,
    })

    # Block unnecessary resources for speed
    BLOCK_RESOURCE_TYPES: List[str] = field(default_factory=lambda: [
        "image", "stylesheet", "font", "media"
    ])

    # Block tracking domains
    BLOCK_TRACKING_DOMAINS: bool = True
    TRACKING_DOMAINS: List[str] = field(default_factory=lambda: [
        "google-analytics.com",
        "doubleclick.net",
        "facebook.net",
        "twitter.com",
        "linkedin.com/li/track",
    ])

    # Pre-defined user agents (will be expanded dynamically)
    USER_AGENTS: List[str] = field(default_factory=lambda: [
        # Chrome - Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",

        # Chrome - macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",

        # Chrome - Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",

        # Safari - macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",

        # Firefox - Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",

        # Firefox - macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",

        # Edge - Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    ])

    # Mobile user agents (for mobile emulation mode)
    MOBILE_USER_AGENTS: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ])

    # Enable mobile emulation (experimental)
    ENABLE_MOBILE_EMULATION: bool = False
    MOBILE_DEVICE_SCALE_FACTOR: float = 2.0
    MOBILE_IS_MOBILE: bool = True
    MOBILE_HAS_TOUCH: bool = True

    # ══════════════════════════════════════════════
    #  🔄 PROXY SETTINGS
    # ══════════════════════════════════════════════

    # Manual proxy list (optional - auto proxies enabled by default in proxy mode)
    PROXY_LIST: List[str] = field(default_factory=lambda: [])

    # Proxy rotation settings
    PROXY_ROTATION_ENABLED: bool = True
    PROXY_ROTATE_EVERY_N_REQUESTS: int = 15
    PROXY_ROTATE_EVERY_N_MINUTES: int = 10

    # Proxy testing
    TEST_PROXIES_ON_STARTUP: bool = True
    PROXY_TEST_TIMEOUT_SECONDS: int = 5
    PROXY_TEST_URL: str = "http://httpbin.org/ip"
    MIN_PROXY_SPEED_MS: int = 3000  # Discard proxies slower than this

    # Proxy failover
    PROXY_FAILOVER_ENABLED: bool = True
    MAX_PROXY_FAILURES_BEFORE_REMOVE: int = 3
    PROXY_BLACKLIST_DURATION_MINUTES: int = 60

    # Proxy providers (for automatic fetching)
    PROXY_PROVIDERS: List[str] = field(default_factory=lambda: [
        "proxyscrape",
        "geonode",
        "proxy-list.download",
        "spys.one",
        "hidemy.name",
    ])

    # Premium proxy services (optional)
    USE_PREMIUM_PROXY_SERVICE: bool = False
    PREMIUM_PROXY_PROVIDER: str = "brightdata"  # "brightdata", "oxylabs", "smartproxy", "netnut"
    PREMIUM_PROXY_API_KEY: str = os.getenv("PREMIUM_PROXY_API_KEY", "")
    PREMIUM_PROXY_ZONE: str = ""

    # Residential vs Datacenter
    PREFER_RESIDENTIAL_PROXIES: bool = True
    RESIDENTIAL_PROXY_PREMIUM: float = 0.7  # 70% residential, 30% datacenter

    # Geographic proxy targeting
    PROXY_COUNTRY_FILTER: List[str] = field(default_factory=lambda: [
        # "US", "GB", "CA", "AU", "DE", "FR"
    ])

    # Proxy authentication
    PROXY_USERNAME: str = ""
    PROXY_PASSWORD: str = ""

    # ══════════════════════════════════════════════
    #  📊 MONITORING & ANALYTICS
    # ══════════════════════════════════════════════

    # Real-time dashboard
    ENABLE_DASHBOARD: bool = True
    DASHBOARD_PORT: int = 8080
    DASHBOARD_REFRESH_INTERVAL_SECONDS: int = 5

    # Metrics collection
    ENABLE_METRICS: bool = True
    METRICS_EXPORT_FORMAT: str = "prometheus"  # "prometheus", "statsd", "json"
    METRICS_EXPORT_INTERVAL_SECONDS: int = 60

    # Alerts
    ENABLE_ALERTS: bool = False
    ALERT_CHANNELS: List[str] = field(default_factory=lambda: [
        # "email", "slack", "discord", "telegram", "webhook"
    ])

    ALERT_WEBHOOK_URL: str = ""
    ALERT_EMAIL_RECIPIENTS: List[str] = field(default_factory=lambda: [])

    # Alert triggers
    ALERT_ON_ERROR_RATE_THRESHOLD: float = 0.3  # Alert if error rate > 30%
    ALERT_ON_SUCCESS_RATE_THRESHOLD: float = 0.5  # Alert if success rate < 50%
    ALERT_ON_PROXY_FAILURE_RATE: float = 0.5  # Alert if proxy failure rate > 50%
    ALERT_ON_RATE_LIMIT_DETECTED: bool = True
    ALERT_ON_CAPTCHA_DETECTED: bool = True

    # Logging verbosity
    LOG_LEVEL: str = "INFO"  # "DEBUG", "INFO", "WARNING", "ERROR"
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True
    LOG_ROTATION_ENABLED: bool = True
    LOG_MAX_FILE_SIZE_MB: int = 10
    LOG_MAX_BACKUPS: int = 5

    # Performance profiling
    ENABLE_PROFILING: bool = False
    PROFILING_OUTPUT_FILE: str = "output/profile_stats.json"

    # ══════════════════════════════════════════════
    #  🧪 EXPERIMENTAL FEATURES
    # ══════════════════════════════════════════════

    # AI-powered features
    ENABLE_AI_FEATURES: bool = False
    AI_AUTO_OPTIMIZE_DELAYS: bool = False  # Let AI learn optimal delays
    AI_DETECT_PATTERN_CHANGES: bool = False  # Detect LinkedIn HTML changes

    # Distributed scraping (multi-machine)
    ENABLE_DISTRIBUTED_MODE: bool = False
    DISTRIBUTED_COORDINATOR_URL: str = ""
    DISTRIBUTED_WORKER_ID: str = ""

    # Queue-based processing
    ENABLE_QUEUE_PROCESSING: bool = False
    QUEUE_PROVIDER: str = "redis"  # "redis", "rabbitmq", "sqs"
    QUEUE_CONNECTION_STRING: str = ""

    # Headless browser farm
    USE_BROWSER_FARM: bool = False
    BROWSER_FARM_URL: str = ""  # Selenium Grid, BrowserStack, etc.

    # Screenshot capture on errors
    CAPTURE_ERROR_SCREENSHOTS: bool = True
    SCREENSHOT_DIR: str = "output/screenshots/"

    # Video recording of sessions (debugging)
    RECORD_SESSION_VIDEO: bool = False
    VIDEO_DIR: str = "output/videos/"

    # HAR file export (network debugging)
    EXPORT_HAR_FILES: bool = False
    HAR_DIR: str = "output/har/"

    # ══════════════════════════════════════════════
    #  🔧 MISC SETTINGS
    # ══════════════════════════════════════════════

    # Dry run mode (test without scraping)
    DRY_RUN: bool = False

    # Debug mode
    DEBUG_MODE: bool = False

    # Skip SSL verification (not recommended)
    SKIP_SSL_VERIFICATION: bool = False

    # Max memory usage (MB) before garbage collection
    MAX_MEMORY_USAGE_MB: int = 2048

    # Garbage collection frequency
    GC_EVERY_N_REQUESTS: int = 50

    # Process priority
    PROCESS_PRIORITY: str = "normal"  # "low", "normal", "high"

    # Auto-update checker
    CHECK_FOR_UPDATES: bool = True
    UPDATE_CHECK_INTERVAL_DAYS: int = 7

    # Telemetry (anonymous usage stats)
    ENABLE_TELEMETRY: bool = False
    TELEMETRY_ENDPOINT: str = ""

    # Feature flags
    FEATURE_FLAGS: Dict[str, bool] = field(default_factory=lambda: {
        "new_search_algorithm": False,
        "enhanced_stealth_mode": False,
        "beta_email_finder": False,
        "experimental_concurrent_mode": False,
    })


# ══════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════

def get_config_from_env() -> Config:
    """Create config entirely from environment variables."""
    return Config()


def validate_config(config: Config) -> List[str]:
    """Validate configuration and return list of warnings/errors."""
    warnings = []
    errors = []

    # Check credentials
    if not config.LINKEDIN_EMAIL or not config.LINKEDIN_PASSWORD:
        if not config.USE_CREDENTIAL_VAULT:
            errors.append("LinkedIn credentials not set. Use environment variables or config file.")

    # Check unsafe settings
    if config.MIN_DELAY_BETWEEN_PROFILES < 5.0:
        warnings.append("⚠️  Very low delay detected. High risk of account ban!")

    if config.MAX_DAILY_SEARCHES > 100:
        warnings.append("⚠️  High daily search limit. Consider reducing to avoid detection.")

    if config.HEADLESS:
        warnings.append("⚠️  Headless mode detected. More likely to be detected by LinkedIn.")

    # Check API keys
    if config.ENABLE_EMAIL_ENRICHMENT and not config.EMAIL_FINDER_API_KEY:
        warnings.append("⚠️  Email enrichment enabled but no API key provided.")

    if config.CAPTCHA_SOLVER_ENABLED and not config.CAPTCHA_SOLVER_API_KEY:
        warnings.append("⚠️  CAPTCHA solver enabled but no API key provided.")

    return errors + warnings


def print_config_summary(config: Config):
    """Print a summary of the current configuration."""
    print("\n" + "="*60)
    print("📋 CONFIGURATION SUMMARY")
    print("="*60)
    print(f"🎯 Search Mode      : {config.SEARCH_MODE.upper()}")
    print(f"🌍 Location         : {config.SEARCH_LOCATION_NAME}")
    print(f"🔐 Credential Vault : {'Enabled' if config.USE_CREDENTIAL_VAULT else 'Disabled'}")
    print(f"🛡️  Encryption       : {'Enabled' if config.ENCRYPT_LOCAL_STORAGE else 'Disabled'}")
    print(f"📊 Dashboard        : {'Enabled' if config.ENABLE_DASHBOARD else 'Disabled'}")
    print(f"🔄 Proxy Rotation   : {'Enabled' if config.PROXY_ROTATION_ENABLED else 'Disabled'}")
    print(f"🤖 AI Features      : {'Enabled' if config.ENABLE_AI_FEATURES else 'Disabled'}")
    print(f"⏱️  Daily Limit      : {config.MAX_DAILY_SEARCHES} searches")
    print(f"⏱️  Hourly Limit     : {config.MAX_HOURLY_SEARCHES} searches")
    print(f"🛑 Cooldown         : {config.SAFETY_COOLDOWN_MINUTES} minutes")
    print("="*60 + "\n")


if __name__ == "__main__":
    # Test configuration
    config = Config()
    warnings = validate_config(config)

    if warnings:
        print("\n⚠️  Configuration Warnings:")
        for w in warnings:
            print(f"   {w}")
    else:
        print("\n✅ Configuration looks good!")

    print_config_summary(config)