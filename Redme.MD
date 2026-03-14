# 🔵 LinkedIn People Scraper

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.40+-green.svg)](https://playwright.dev/python/)
[![License](https://img.shields.io/badge/License-Educational-orange.svg)](#-disclaimer)

> **A learning project** built to practice real-world Python skills —
> async programming, browser automation, HTML parsing, and Excel file I/O.

Search LinkedIn for **any job title**, at **any company**, in **any country** — and export everything to a clean Excel file with one command.

---

## ⚠️ Disclaimer

This tool is for **educational purposes only.**
Scraping LinkedIn may violate their [Terms of Service](https://www.linkedin.com/legal/user-agreement).
- Do **not** use this for commercial purposes
- Do **not** run this at large scale
- Use a **secondary account** — not your main LinkedIn account
- The author is not responsible for any account restrictions or legal issues

---

## 📋 Table of Contents

- [What It Does](#-what-it-does)
- [Project Structure](#-project-structure)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [How to Run](#-how-to-run)
- [Output](#-output)
- [How It Works](#-how-it-works)
- [Troubleshooting](#-troubleshooting)
- [Tech Stack](#-tech-stack)

---

## ✨ What It Does

- 🔍 Search LinkedIn for **any job title** — Recruiter, Engineer, Designer, CEO, anything
- 🏢 Search across **any company list** — your own list or the full Fortune 500
- 🌍 Filter by **country or city** using LinkedIn's location system
- 📊 Export results to a **formatted Excel file** with clickable LinkedIn profile links
- ♻️ **Auto-resumes** if stopped — progress is saved after every search so you never lose data
- 🛡️ Built-in **anti-detection** so LinkedIn doesn't block you immediately

---

## 📁 Project Structure

```
linkedin-scraper/
│
├── main.py               ← START HERE — run this file
├── linkedin_scraper.py   ← Core browser automation engine
├── config.py             ← ALL your settings live here (edit this!)
├── utils.py              ← Helper functions (delays, scrolling, Excel export)
├── resume_parser.py      ← Parses and structures scraped profile data
├── Requirements.txt      ← Python package dependencies
│
├── output/               ← Created automatically when you run the scraper
│   ├── linkedin_results.xlsx  ← Your final Excel data file
│   ├── progress.json          ← Auto-save checkpoint for resume feature
│   └── scraper.log            ← Full log of everything that happened
│
└── session/              ← Browser session storage (keeps you logged in)
```

---

## 📦 Requirements

- Python **3.8 or higher**
- A LinkedIn account (use a secondary one — not your main!)
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

### Step 3 — Install the browser (Playwright needs Chromium)
```bash
playwright install chromium
```

That's it! Now configure it and run.

---

## ⚙️ Configuration

**You only ever need to edit one file: `config.py`**

Open `config.py` and follow the steps inside:

---

### 🔐 Step 1 — Set Your LinkedIn Credentials

```python
LINKEDIN_EMAIL    = "your_email@gmail.com"   # ← your LinkedIn email
LINKEDIN_PASSWORD = "your_password"           # ← your LinkedIn password
```

> 💡 **Tip:** Use environment variables to keep credentials out of your code:
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

Edit `JOB_TITLES` in `config.py`. Just uncomment the block you want:

```python
# Recruiters (default)
JOB_TITLES = [
    "Recruiter",
    "Technical Recruiter",
    "Talent Acquisition",
]

# Software Engineers
JOB_TITLES = [
    "Software Engineer",
    "Backend Developer",
    "Frontend Developer",
]

# Sales
JOB_TITLES = [
    "Account Executive",
    "Sales Manager",
    "VP of Sales",
]

# Data / AI
JOB_TITLES = [
    "Data Scientist",
    "Machine Learning Engineer",
    "Data Analyst",
]

# Leadership
JOB_TITLES = [
    "CEO",
    "CTO",
    "Co-Founder",
]
```

---

### 🌍 Step 3 — Choose Your Location

```python
SEARCH_LOCATION_NAME = "United States"
GEO_URN = "103644278"
```

Common locations:

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

### 🏢 Step 5 — Choose Your Company List (in main.py)

Open `main.py` and change this one line:

```python
companies = TEST_COMPANIES         # ← 3 companies, good for first test run
companies = MY_COMPANIES           # ← your own custom list
companies = FORTUNE_500_COMPANIES  # ← full Fortune 500 list
```

---

## ▶️ How to Run

```bash
python main.py
```

The scraper will show a summary and ask you to press ENTER before starting:

```
🎯 Job Titles  : Recruiter, Technical Recruiter
🌍 Location    : United States
🏢 Companies   : 3
🔍 Total searches: 6
📁 Output file : output/linkedin_results.xlsx

Press ENTER to start, or Ctrl+C to cancel...
```

> **First time?** Change `companies = TEST_COMPANIES` in `main.py` to test with just 3 companies before running the full list.

---

### ♻️ Resume After a Crash

If the scraper stops for any reason — just run it again:

```bash
python main.py
```

It reads `output/progress.json` and continues exactly where it left off.

---

## 📊 Output

Results are saved to `output/linkedin_results.xlsx` with two sheets:

**Sheet 1 — Recruiters (all data)**

| Column | Description |
|--------|-------------|
| Full Name | Person's full name |
| Job Title | Their current title |
| Company (from Profile) | Company listed on their LinkedIn |
| Searched Company | Company name you searched |
| Searched Title | Job title you searched |
| LinkedIn Headline | Their full LinkedIn headline |
| Location | City, State |
| Email Address | If publicly visible on their profile |
| Phone | If publicly visible on their profile |
| LinkedIn URL | Clickable link directly to their profile |
| Scraped At | Date and time scraped |

**Sheet 2 — Summary**

A quick summary showing total profiles found, companies covered, and how many had emails.

---

## 🛡️ How It Works (Anti-Detection)

LinkedIn actively detects bots. This scraper uses several techniques to behave like a real human:

| Technique | What It Does |
|-----------|-------------|
| **Random delays** | Waits 8–18 seconds between each profile (not a fixed speed) |
| **Session breaks** | Takes a 1–3 minute break every 15 requests, scrolls the feed naturally |
| **Stealth JS** | Hides signs that a browser is being automated |
| **User agent rotation** | Pretends to be different real browsers (Chrome, Safari) |
| **Human-like typing** | Types your login credentials one character at a time |
| **Random scrolling** | Scrolls pages up and down like a real person reading |
| **US Geolocation** | Sets browser location to New York to appear local |

> ⚠️ **Do NOT reduce the delay settings.** Faster = higher chance of getting blocked.

---

## 🔧 Troubleshooting

**❌ Login timeout error**
```
Login error: Timeout 15000ms exceeded
```
→ Your internet may be slow. The scraper uses `domcontentloaded` which should handle this — try running again.

---

**❌ "No results found" for every company**

→ LinkedIn may have temporarily rate-limited your IP.
→ Wait 1–2 hours and try again, or restart your router to get a new IP.

---

**⚠️ Verification / CAPTCHA screen appears**

→ Normal! The scraper pauses for **60 seconds** and shows the browser window.
→ Complete the verification manually, then the scraper continues automatically.

---

**❌ "Please set your LinkedIn credentials"**

→ Open `config.py` and update `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD`.

---

**❌ ModuleNotFoundError**

```bash
pip install -r Requirements.txt
playwright install chromium
```

---

**❌ 0 results every time**

→ LinkedIn may have changed their HTML structure. This is common with scrapers.
→ Open an [Issue](https://github.com/yagyeshVyas/linkedin-scraper/issues) and I'll look into it.

---

## ⏱️ Time Estimates

| Companies | Job Titles | Est. Time |
|-----------|-----------|-----------|
| 3 (test) | 2 | ~10–20 min |
| 50 | 2 | ~2–5 hours |
| 100 | 3 | ~5–15 hours |
| 500 | 5 | ~25–70 hours |

> Run it overnight for large lists. The resume feature means you can split it across multiple sessions.

---

## 🧰 Tech Stack

| Library | Version | Purpose |
|---------|---------|---------|
| [Playwright](https://playwright.dev/python/) | 1.40+ | Browser automation |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | 4.12+ | HTML parsing |
| [Pandas](https://pandas.pydata.org/) | 2.0+ | Data processing |
| [OpenPyXL](https://openpyxl.readthedocs.io/) | 3.1+ | Excel file creation |
| asyncio | built-in | Async execution |

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

If this project helped you, consider giving it a star on GitHub — it helps others find it!

---

*Built as a learning project. Practicing Python, Playwright, asyncio, and real-world problem solving.*