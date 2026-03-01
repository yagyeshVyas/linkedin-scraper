# 🔵 LinkedIn People Scraper v2.0

A fully flexible LinkedIn scraper. Search for **any job title** at **any company** in **any location** — just edit `config.py`.

---

## 📁 Project Structure

```
linkedin_scraper/
│
├── main.py               ← 🚀 RUN THIS FILE
├── linkedin_scraper.py   ← Scraping engine (don't edit)
├── config.py             ← ✏️  YOUR SETTINGS (edit this!)
├── utils.py              ← Helpers & Excel export (don't edit)
├── requirements.txt      ← Dependencies
│
├── output/               ← Auto-created
│   ├── linkedin_results.xlsx  ← ✅ Your data here
│   ├── progress.json          ← Auto-save / resume
│   └── scraper.log            ← Activity log
│
└── session/              ← Browser session cache
```

---

## ⚡ Quick Start

```bash
# 1. Install
pip install -r requirements.txt
playwright install chromium

# 2. Edit config.py with your credentials + job titles

# 3. Run
python main.py
```

---

## ✏️ How to Customize (Only edit config.py)

### Change Job Title

Open `config.py` and edit `JOB_TITLES`:

```python
# Search for Engineers instead of Recruiters:
JOB_TITLES = [
    "Software Engineer",
    "Frontend Developer",
    "Backend Developer",
]

# Search for Sales people:
JOB_TITLES = [
    "Account Executive",
    "Sales Manager",
    "VP of Sales",
]

# Search for Data roles:
JOB_TITLES = [
    "Data Scientist",
    "Data Analyst",
    "Machine Learning Engineer",
]
```

### Change Location

```python
SEARCH_LOCATION_NAME = "United Kingdom"
GEO_URN = "101165590"

# Other options:
# Canada     → GEO_URN = "101174742"
# Australia  → GEO_URN = "101452733"
# India      → GEO_URN = "102713980"
# Germany    → GEO_URN = "101282230"
```

### Add Keyword Filter (Optional)

```python
# Only save "Senior" or "Lead" level profiles:
FILTER_KEYWORDS = ["senior", "lead", "principal", "staff"]

# Or leave empty to get everyone:
FILTER_KEYWORDS = []
```

---

## 📊 Excel Output Columns

| Column | Description |
|--------|-------------|
| Full Name | Person's name |
| Job Title | Their current title |
| Company (from Profile) | Company on their profile |
| Searched Company | Company you searched |
| Searched Title | Job title you searched |
| LinkedIn Headline | Their headline |
| Location | City, State |
| Email Address | If publicly visible |
| Phone | If publicly visible |
| LinkedIn URL | 🔗 Clickable link |
| Scraped At | Timestamp |

---

## ♻️ Resume Capability

If stopped or crashed — **just run again**. Progress is auto-saved after every company/title combination.

---

## ⚠️ Tips

- Use `TEST_COMPANIES` in `main.py` first to test with 2-3 companies
- Keep `HEADLESS = False` so you can see what's happening
- If LinkedIn asks for verification, you have 60 seconds to complete it in the browser
- Don't lower the delay settings — they protect your account from being blocked

---

## 🔄 Common Use Cases

| Goal | JOB_TITLES setting |
|------|-------------------|
| Find recruiters to connect with | `["Recruiter", "Talent Acquisition"]` |
| Find hiring managers | `["Engineering Manager", "Director of Engineering"]` |
| Find potential clients | `["VP of Marketing", "CMO", "Marketing Director"]` |
| Find co-founders | `["CEO", "CTO", "Co-Founder"]` |
| Find engineers to hire | `["Software Engineer", "Backend Developer"]` |
