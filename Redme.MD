# 🔵 LinkedIn People Scraper

> A learning project built to practice real-world Python skills — async programming, browser automation, HTML parsing, and file I/O.
> 
> ⚠️ **Disclaimer:** This tool is for educational purposes only. Scraping LinkedIn may violate their [Terms of Service](https://www.linkedin.com/legal/user-agreement). Use responsibly. Not intended for commercial or large-scale production use.

---

## ✨ What It Does

- 🔍 Search LinkedIn for **any job title** (Recruiter, Engineer, Designer, CEO — anything)
- 🏢 Filter by **any company** from a custom or Fortune 500 list
- 🌍 Target **any country** using LinkedIn's GeoURN system
- 📊 Export clean **Excel file** with clickable LinkedIn URLs
- ♻️ **Auto-resumes** if stopped — never loses progress
- 🛡️ Built-in **anti-detection** (stealth JS, human delays, session breaks)

---

## 📁 Project Structure

```
linkedin-scraper/
│
├── main.py               ← 🚀 RUN THIS FILE
├── linkedin_scraper.py   ← Core scraping engine
├── config.py             ← ✏️  All your settings (edit this!)
├── utils.py              ← Helpers, delays, Excel export
├── resume_parser.py      ← Parses and structures profile data
├── Requirements.txt      ← Python dependencies
│
├── output/               ← Auto-created on first run
│   ├── linkedin_results.xlsx  ← ✅ Your exported data
│   ├── progress.json          ← Resume checkpoint
│   └── scraper.log            ← Full activity log
│
└── session/              ← Browser session/cookie cache
```

---

## ⚡ Quick Start

### 1. Install dependencies
```bash
pip install -r Requirements.txt
playwright install chromium
```

### 2. Set your credentials
Open `config.py` and edit:
```python
LINKEDIN_EMAIL    = "your_email@gmail.com"
LINKEDIN_PASSWORD = "your_password"
```
Or use environment variables:
```bash
export LINKEDIN_EMAIL="your_email@gmail.com"
export LINKEDIN_PASSWORD="your_password"
```

### 3. Run
```bash
python main.py
```

---

## ✏️ How to Customize

**Everything is controlled from `config.py` — no other file needs editing.**

### 🎯 Change Job Title
```python
# Recruiters (default)
JOB_TITLES = ["Recruiter", "Talent Acquisition", "HR Manager"]

# Engineers
JOB_TITLES = ["Software Engineer", "Backend Developer", "DevOps Engineer"]

# Sales
JOB_TITLES = ["Account Executive", "Sales Manager", "VP of Sales"]

# Data / AI
JOB_TITLES = ["Data Scientist", "Machine Learning Engineer", "Data Analyst"]

# Leadership
JOB_TITLES = ["CEO", "CTO", "Co-Founder", "Director of Engineering"]
```

### 🌍 Change Location
```python
SEARCH_LOCATION_NAME = "United States"
GEO_URN = "103644278"
```

| Country | GeoURN |
|---------|--------|
| United States | `103644278` |
| United Kingdom | `101165590` |
| Canada | `101174742` |
| Australia | `101452733` |
| India | `102713980` |
| Germany | `101282230` |
| France | `105015875` |
| Singapore | `102454443` |

### 🔍 Add a Seniority Filter (Optional)
```python
# Only save senior-level profiles
FILTER_KEYWORDS = ["senior", "lead", "principal", "director"]

# Or leave empty to get everyone
FILTER_KEYWORDS = []
```

### 🏢 Choose Company List (in main.py)
```python
companies = TEST_COMPANIES        # 3 companies — good for testing
companies = MY_COMPANIES          # your custom list
companies = FORTUNE_500_COMPANIES # full Fortune 500
```

---

## 📊 Excel Output

| Column | Description |
|--------|-------------|
| Full Name | Person's full name |
| Job Title | Current job title |
| Company (from Profile) | Company listed on their profile |
| Searched Company | Company you searched for |
| Searched Title | Job title you searched for |
| LinkedIn Headline | Their LinkedIn headline |
| Location | City, State |
| Email Address | If publicly visible |
| Phone | If publicly visible |
| LinkedIn URL | 🔗 Clickable profile link |
| Scraped At | Timestamp |

---

## 🛡️ Anti-Detection Features

| Feature | Description |
|---------|-------------|
| Human-like delays | 8–18 second random waits between profiles |
| Session breaks | 1–3 min breaks every 15 requests |
| Stealth JS injection | Hides browser automation signals |
| User agent rotation | Rotates between real Chrome/Safari agents |
| Natural scrolling | Mimics real reading behavior |
| Human typing | Types credentials character by character |

---

## ♻️ Resume Capability

If the scraper crashes or you stop it — **just run it again.**
Progress is auto-saved to `output/progress.json` after every company/title combination.

---

## 🔄 Common Use Cases

| Goal | JOB_TITLES to use |
|------|------------------|
| Connect with recruiters | `["Recruiter", "Talent Acquisition"]` |
| Find hiring managers | `["Engineering Manager", "Director of Engineering"]` |
| Find potential clients | `["VP of Marketing", "CMO", "Marketing Director"]` |
| Find co-founders | `["CEO", "CTO", "Co-Founder"]` |
| Research a job market | `["Software Engineer", "Backend Developer"]` |

---

## ⚠️ Important Notes

- Use a **secondary LinkedIn account** for scraping — not your main account
- Keep `HEADLESS = False` while learning so you can see what's happening
- If LinkedIn shows a verification screen, you have **60 seconds** to complete it manually
- **Don't lower the delay settings** — they protect your account from being blocked
- First run? Use `TEST_COMPANIES` (3 companies) to make sure everything works

---

## 🧰 Tech Stack

| Library | Purpose |
|---------|---------|
| [Playwright](https://playwright.dev/python/) | Browser automation |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | HTML parsing |
| [Pandas](https://pandas.pydata.org/) | Data handling |
| [OpenPyXL](https://openpyxl.readthedocs.io/) | Excel export |
| asyncio | Async/concurrent execution |

---

## 📄 License

This project is for **educational purposes only**.
Not affiliated with or endorsed by LinkedIn.

---

*Built as a learning project to practice Python, browser automation, and async programming.*