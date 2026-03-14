# 🔵 LinkedIn People Scraper

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.40+-green.svg)](https://playwright.dev/python/)
[![Proxies](https://img.shields.io/badge/Proxies-Auto--Rotating-purple.svg)](#-proxy-support-free)
[![License](https://img.shields.io/badge/License-Educational-orange.svg)](#-disclaimer)

> **A learning project** built to practice real-world Python skills —
> async programming, browser automation, HTML parsing, proxy rotation, and Excel file I/O.

Search LinkedIn for **any job title**, at **any company**, in **any country** — export everything to a clean Excel file with one command. Now with **free auto-rotating proxy support.**

---

## ⚠️ Disclaimer

This tool is for **educational purposes only.**
Scraping LinkedIn may violate their [Terms of Service](https://www.linkedin.com/legal/user-agreement).
- Do **not** use this for commercial purposes
- Do **not** run this at large scale
- Use a **secondary LinkedIn account** — not your main one
- The author is not responsible for any account restrictions or legal issues

---

## 📋 Table of Contents

- [What It Does](#-what-it-does)
- [Project Structure](#-project-structure)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [How to Run](#-how-to-run)
- [Proxy Support](#-proxy-support-free)
- [Output](#-output)
- [How It Works](#-how-it-works)
- [Troubleshooting](#-troubleshooting)
- [Tech Stack](#-tech-stack)

---

## ✨ What It Does

- 🔍 Search LinkedIn for **any job title** — Recruiter, Engineer, Designer, CEO, anything
- 🏢 Search across **any company list** — your own list or the full Fortune 500
- 🌍 Filter by **country or city** using LinkedIn's location system
- 🔄 **Auto-rotating free proxies** — fetches and tests fresh proxies every hour
- 📊 Export results to a **formatted Excel file** with clickable LinkedIn profile links
- ♻️ **Auto-resumes** if stopped — progress is saved after every search
- 🛡️ Built-in **anti-detection** — stealth JS, human delays, session breaks

---

## 📁 Project Structure

```
linkedin-scraper/
│
├── main.py                    ← Run WITHOUT proxy (direct connection)
├── main_proxy.py              ← Run WITH free rotating proxies ✨
│
├── linkedin_scraper.py        ← Core scraping engine
├── linkedin_scraper_proxy.py  ← Scraping engine with proxy support
├── proxy_manager.py           ← Free proxy fetcher, tester & rotator
│
├── config.py                  ← ALL your settings (edit this!)
├── utils.py                   ← Helpers, delays, Excel export
├── resume_parser.py           ← Parses and structures profile data
├── Requirements.txt           ← Python dependencies
│
├── output/                    ← Auto-created on first run
│   ├── linkedin_results.xlsx  ← Your exported Excel data
│   ├── progress.json          ← Auto-save checkpoint (resume feature)
│   ├── proxies.json           ← Cached working proxy list
│   └── scraper.log            ← Full activity log
│
└── session/                   ← Browser session/cookie cache
```

---

## 📦 Requirements

- Python **3.8 or higher**
- A LinkedIn account (use a secondary one!)
- Windows / macOS / Linux

Check your Python version:
```bash
python --version
```

---

## 🚀 Installation

### Step 1 — Clone the repository
```bash
git clone https://github.com/yagyeshVyas/linkedin-scraper.git
cd linkedin-scraper
```

### Step 2 — Install Python dependencies
```bash
pip install -r Requirements.txt
```

### Step 3 — Install the Playwright browser
```bash
playwright install chromium
```

---

## ⚙️ Configuration

**You only ever need to edit one file: `config.py`**

---

### 🔐 Step 1 — Set Your LinkedIn Credentials

```python
LINKEDIN_EMAIL    = "your_email@gmail.com"
LINKEDIN_PASSWORD = "your_password"
```

> 💡 Use environment variables to keep credentials safe:
> ```bash
> # Windows
> set LINKEDIN_EMAIL=your_email@gmail.com
> set LINKEDIN_PASSWORD=your_password
>
> # Mac / Linux
> export LINKEDIN_EMAIL=your_email@gmail.com
> export LINKEDIN_PASSWORD=your_password
> ```

---

### 🎯 Step 2 — Choose What Job Title to Search

Edit `JOB_TITLES` in `config.py`:

```python
# Recruiters (default)
JOB_TITLES = ["Recruiter", "Technical Recruiter", "Talent Acquisition"]

# Software Engineers
JOB_TITLES = ["Software Engineer", "Backend Developer", "Frontend Developer"]

# Sales
JOB_TITLES = ["Account Executive", "Sales Manager", "VP of Sales"]

# Data / AI
JOB_TITLES = ["Data Scientist", "Machine Learning Engineer", "Data Analyst"]

# Leadership / C-Suite
JOB_TITLES = ["CEO", "CTO", "Co-Founder", "Director of Engineering"]

# Marketing
JOB_TITLES = ["Marketing Manager", "CMO", "Growth Manager", "SEO Specialist"]
```

---

### 🌍 Step 3 — Choose Your Location

```python
SEARCH_LOCATION_NAME = "United States"
GEO_URN = "103644278"
```

| Country / City   | GEO_URN     |
|------------------|-------------|
| United States    | `103644278` |
| United Kingdom   | `101165590` |
| Canada           | `101174742` |
| Australia        | `101452733` |
| India            | `102713980` |
| Germany          | `101282230` |
| France           | `105015875` |
| Singapore        | `102454443` |
| New York City    | `105080838` |
| San Francisco    | `102277331` |
| London           | `102257491` |

---

### 🔎 Step 4 — Optional: Filter by Seniority

```python
# Only save senior-level profiles:
FILTER_KEYWORDS = ["senior", "lead", "principal", "director", "vp"]

# Save everyone — no filter:
FILTER_KEYWORDS = []
```

---

### 🏢 Step 5 — Choose Company List (in main.py or main_proxy.py)

```python
companies = TEST_COMPANIES         # 3 companies — good for first test
companies = MY_COMPANIES           # your own custom list
companies = FORTUNE_500_COMPANIES  # full Fortune 500
```

---

## ▶️ How to Run

### Option A — Without Proxy (direct connection)
```bash
python main.py
```

### Option B — With Free Auto-Rotating Proxies ✨ (recommended)
```bash
python main_proxy.py
```

The scraper shows a summary before starting:

```
🎯 Job Titles  : Recruiter, Technical Recruiter
🌍 Location    : United States
🏢 Companies   : 3
🔄 Proxy Mode  : AUTO-ROTATING (refreshes every hour)

Press ENTER to start...
```

> **First time?** Always start with `TEST_COMPANIES` (3 companies) to make sure everything works before running the full list.

---

### ♻️ Resume After a Stop

If the scraper stops for any reason — just run the same command again:

```bash
python main_proxy.py
```

It reads `output/progress.json` and continues exactly where it left off. No data is lost.

---

## 🔄 Proxy Support (Free)

`proxy_manager.py` handles everything automatically:

### How It Works

| When | What Happens |
|------|-------------|
| **On startup** | Fetches 100+ free proxies, tests them, keeps working ones |
| **Every hour** | Fetches a completely fresh proxy list automatically |
| **Every 15 requests** | Rotates to the next proxy in the list |
| **Every 45 requests** | Rotates proxy AND relaunches the browser fresh |
| **On network error** | Immediately switches to a new proxy |
| **Proxy list runs out** | Auto-fetches a brand new list instantly |

### Free Proxy Sources Used

| Source | Refresh Rate |
|--------|-------------|
| [ProxyScrape](https://proxyscrape.com) | Every few minutes |
| [GeoNode](https://geonode.com/free-proxy-list) | Hourly |
| [Proxy-List.download](https://www.proxy-list.download) | Daily |
| [ProxyNova](https://proxynova.com) | Hourly |

### Proxy Reality Check

Free proxies are not perfect — expect:
- ~10–30% of fetched proxies will actually work
- Some will be slow (3–8 second response times)
- `proxy_manager.py` automatically tests and removes bad proxies

---

## 📊 Output

Results saved to `output/linkedin_results.xlsx` with two sheets:

**Sheet 1 — All Profiles**

| Column | Description |
|--------|-------------|
| Full Name | Person's full name |
| Job Title | Their current title |
| Company (from Profile) | Company listed on their LinkedIn |
| Searched Company | Company name you searched for |
| Searched Title | Job title you searched for |
| LinkedIn Headline | Their full LinkedIn headline |
| Location | City, State |
| Email Address | If publicly visible |
| Phone | If publicly visible |
| Proxy Used | Which proxy was used (or "direct") |
| LinkedIn URL | 🔗 Clickable link to their profile |
| Scraped At | Date and time scraped |

**Sheet 2 — Summary**

Quick stats: total profiles, companies covered, profiles with email.

---

## 🛡️ How It Works (Anti-Detection)

| Technique | What It Does |
|-----------|-------------|
| **Random delays** | Waits 8–18 seconds between profiles (not fixed) |
| **Session breaks** | 1–3 min break every 15 requests, scrolls feed naturally |
| **Proxy rotation** | Different IP address every 15–45 requests |
| **Stealth JS** | Hides signs that a browser is being automated |
| **User agent rotation** | Rotates between real Chrome and Safari agents |
| **Human-like typing** | Types credentials one character at a time |
| **Random scrolling** | Scrolls pages up and down like a real person |
| **NYC Geolocation** | Sets browser location to New York |

> ⚠️ **Do NOT reduce the delay settings.** Faster = higher chance of getting blocked.

---

## 🔧 Troubleshooting

**❌ Login timeout**
```
Login error: Timeout 15000ms exceeded
```
→ Run again. LinkedIn can be slow to load sometimes.

---

**❌ No results for every company**

→ LinkedIn may have rate-limited your IP.
→ Switch to proxy mode: `python main_proxy.py`
→ Or wait 1–2 hours and try again.

---

**⚠️ Verification / CAPTCHA screen**

→ Normal! The scraper pauses for 60 seconds.
→ Complete the verification manually in the browser window.
→ The scraper continues automatically after you finish.

---

**❌ All proxies failing**

→ Free proxies sometimes go down all at once.
→ The scraper will fall back to direct connection automatically.
→ Try again after 30 minutes — new proxies will be available.

---

**❌ ModuleNotFoundError**
```bash
pip install -r Requirements.txt
playwright install chromium
```

---

**❌ 0 results every time**

→ LinkedIn may have updated their HTML structure.
→ Open an [Issue](https://github.com/yagyeshVyas/linkedin-scraper/issues) and I'll investigate.

---

## ⏱️ Time Estimates

| Companies | Job Titles | Mode | Est. Time |
|-----------|-----------|------|-----------|
| 3 (test) | 2 | Any | ~10–20 min |
| 50 | 2 | Direct | ~2–5 hours |
| 50 | 2 | Proxy | ~3–7 hours |
| 500 | 5 | Proxy | ~25–70 hours |

> Run overnight for large lists. The resume feature means you can split it across multiple sessions.

---

## 🧰 Tech Stack

| Library | Version | Purpose |
|---------|---------|---------|
| [Playwright](https://playwright.dev/python/) | 1.40+ | Browser automation |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | 4.12+ | HTML parsing |
| [Pandas](https://pandas.pydata.org/) | 2.0+ | Data handling |
| [OpenPyXL](https://openpyxl.readthedocs.io/) | 3.1+ | Excel export |
| asyncio | built-in | Async execution |
| urllib | built-in | Proxy fetching |

---

## 🤝 Contributing

Found a bug or want to improve something?

1. Fork the repo
2. Create a branch: `git checkout -b fix/your-fix`
3. Commit: `git commit -m "Fix: describe what you fixed"`
4. Push: `git push origin fix/your-fix`
5. Open a Pull Request

---

## ⭐ Support

If this helped you, consider giving it a star — it helps others find it!

---

*Built as a learning project. Practicing Python, Playwright, asyncio, proxy rotation, and real-world problem solving.*