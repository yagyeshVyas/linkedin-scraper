# 🔵 LinkedIn Scraper v3.0

The ultimate LinkedIn scraper with **People**, **Job**, and **Candidate** search modes. Features advanced anti-detection including adaptive rate-limiting and user-agent rotation.

---

## 📁 Project Structure

```
linkedin_scraper/
│
├── main.py               ← 🚀 RUN THIS FILE (Interactive Menu)
├── linkedin_scraper.py   ← Scraping engine (handles anti-block logic)
├── config.py             ← ✏️ YOUR SETTINGS (Search modes, filters, credentials)
├── utils.py              ← Helpers & Excel exports (for jobs/people/candidates)
├── requirements.txt      ← Dependencies
│
├── output/               ← Auto-created
│   ├── linkedin_results.xlsx     ← People search results
│   ├── linkedin_jobs.xlsx        ← Job search results
│   ├── linkedin_candidates.xlsx  ← Candidate search results
│   ├── progress.json             ← Auto-save / resume state
│   └── scraper.log               ← Activity log
│
└── session/              ← Browser session cache & cookies
```

---

## ⚡ Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 2. Add your LinkedIn credentials & set your target fields in config.py
# (e.g., set LINKEDIN_EMAIL, LINKEDIN_PASSWORD)

# 3. Run the interactive menu
python main.py
```

---

## 🔍 Search Modes

When you run `main.py`, you'll be prompted to select one of three search modes. You can configure what each mode searches for in `config.py`.

### 1. 🏢 Search People (By Title & Company)
Finds people with specific job titles at target companies.
**Configure in `config.py`:**
```python
# Lists of job titles to search for
JOB_TITLES = [
    "Software Engineer",
    "Data Scientist",
]
```
*(Note: Edit `main.py` directly to change the default `FORTUNE_500_COMPANIES` list to your own custom company list).*

### 2. 💼 Search Jobs (By Keywords)
Extracts live job postings from LinkedIn Job Search without visiting individual profiles (highly resistant to blocking).
**Configure in `config.py`:**
```python
JOB_SEARCH_KEYWORDS = ["Python Developer", "Machine Learning"]
JOB_EXPERIENCE_LEVEL = "2" # Entry level
JOB_REMOTE_FILTER = "2"    # Remote
```

### 3. 👤 Find Candidates (By Skills)
Searches the LinkedIn people directory for specific skills or keywords to find candidate profiles regardless of their current company.
**Configure in `config.py`:**
```python
CANDIDATE_SKILLS = ["Python", "TensorFlow", "AWS"]
CANDIDATE_TITLE_FILTER = "Engineer" # Optional title filter
```

### 4. 📄 Smart Resume Job Match (With Strict Time Filters)
Upload your personal resume and have the scraper dynamically generate highly relevant job searches, discarding any job that falls outside your exact hourly requirements.
**How it Works:**
1. Drop your resume in the project root folder (default name: `my_resume.pdf` or `.docx`).
2. Run `python main.py` and hit Option 4.
3. Enter exactly how fresh you want the jobs (e.g. `5` for 5 hours, `24` for 1 day, `48` for 2 days). The bot will natively read the job cards ("posted 3 hours ago") and instantly reject anything too old!

---

## 🛡️ Anti-Block Features

LinkedIn aggressively monitors for bots. v3.0 introduces a robust anti-detection suite, configured in `config.py`:

- **Adaptive Throttling:** Automatically increases delays and triggers emergency brakes if it encounters consecutive Captchas or rate limits (`ADAPTIVE_THROTTLE = True`).
- **User-Agent Rotation:** Periodically replaces its fingerprint to look like a brand new browser (`ROTATE_USER_AGENT = True`).
- **Proxy Support:** Route traffic through proxies to mask your IP (`PROXY_LIST = ["http://user:pass@12.34.56.78:8080"]`).
- **Randomized Behaviors:** Randomly scrolls pages, injects human-like typing delays, and randomly visits the home feed during breaks.
- **Daily Limits:** Stops scraping when it hits `MAX_DAILY_SEARCHES` to protect your account.

---

## ♻️ Resume Capability

If stopped or crashed — **just run the script again**. Progress is auto-saved after every query.

---

## 📊 Excel Output Sheets

Separate Microsoft Excel files are generated depending on the mode you run:
- **`linkedin_results.xlsx`**: Detailed profiles for People search.
- **`linkedin_jobs.xlsx`**: Job listings with Job Title, Company, Location, Date, and direct URL.
- **`linkedin_candidates.xlsx`**: Candidates matching skills with Headline, Location, and Contact info.

All Excel exports feature beautiful formatting, colored headers, auto-sizing columns, and clickable URLs.