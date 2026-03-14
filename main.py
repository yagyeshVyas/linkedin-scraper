"""
╔══════════════════════════════════════════════════════════════╗
║          LinkedIn Scraper v3.0 — Main Runner                 ║
║                                                              ║
║  HOW TO USE:                                                 ║
║  1. Edit config.py — set credentials & target lists          ║
║  2. Run: python main.py                                      ║
║                                                              ║
║  ♻️  RESUME: If stopped, just run again. It picks up         ║
║     exactly where it left off.                               ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import sys
from config import Config
from linkedin_scraper import LinkedInScraper
from resume_parser import parse_resume_for_keywords

# ══════════════════════════════════════════════════
#  🏢 COMPANY LIST (For People Search Mode)
# ══════════════════════════════════════════════════
TEST_COMPANIES = ["Google", "Microsoft"]
FORTUNE_500_COMPANIES = [
    "Walmart", "Amazon", "Apple", "CVS Health", "UnitedHealth Group",
    "Exxon Mobil", "Berkshire Hathaway", "Alphabet", "McKesson", "AmerisourceBergen",
    "Costco Wholesale", "Cigna", "AT&T", "Microsoft", "Cardinal Health",
    "Chevron", "Home Depot", "Walgreens", "JPMorgan Chase", "Marathon Petroleum",
    "Elevance Health", "Kroger", "Ford Motor", "Comcast", "Phillips 66",
    # (truncated for brevity, user can add more in their custom lists)
]

def print_menu():
    print("""
╔══════════════════════════════════════════════════════════╗
║          LinkedIn Scraper v3.0 — Multi-Mode              ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  [1] 🔍 Search People (By Company + Job Title)          ║
║      • Best for: Finding specific roles at target orgs   ║
║                                                          ║
║  [2] 💼 Search Jobs (By Keywords + Filters)             ║
║      • Best for: Scraping open job listings safely       ║
║                                                          ║
║  [3] 👤 Find Candidates (By Skills / Keywords)          ║
║      • Best for: Sourcing talent regardless of company   ║
║                                                          ║
║  [4] 📄 Smart Resume Job Match (Time Filtered)          ║
║      • Best for: Finding perfectly matched fresh jobs    ║
║                                                          ║
║  [5] ❌ Exit                                             ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
    """)

async def main():
    config = Config()

    # Validate credentials
    if config.LINKEDIN_EMAIL == "your_email@gmail.com":
        print("❌ ERROR: Please set your LinkedIn credentials in config.py")
        sys.exit(1)

    while True:
        print_menu()
        choice = input("Select an option (1-5): ").strip()
        
        if choice == '1':
            config.SEARCH_MODE = "people"
            target_list = list(dict.fromkeys(FORTUNE_500_COMPANIES)) # Or TEST_COMPANIES
            if not config.JOB_TITLES:
                print("❌ ERROR: No JOB_TITLES set in config.py")
                sys.exit(1)
            break
            
        elif choice == '2':
            config.SEARCH_MODE = "jobs"
            target_list = config.JOB_SEARCH_KEYWORDS
            if not target_list:
                print("❌ ERROR: No JOB_SEARCH_KEYWORDS set in config.py")
                sys.exit(1)
            break
            
        elif choice == '3':
            config.SEARCH_MODE = "candidates"
            target_list = config.CANDIDATE_SKILLS
            if not target_list:
                print("❌ ERROR: No CANDIDATE_SKILLS set in config.py")
                sys.exit(1)
            break
            
        elif choice == '4':
            config.SEARCH_MODE = "jobs"
            print(f"\n📄 Reading Resume: {config.RESUME_FILE_PATH}")
            extracted_skills = parse_resume_for_keywords(config.RESUME_FILE_PATH, top_n=5)
            
            if not extracted_skills:
                print("❌ ERROR: Could not extract useful skills from resume. Make sure it exists and contains text.")
                sys.exit(1)
                
            print(f"✨ Extracted Skills: {', '.join(extracted_skills)}")
            target_list = extracted_skills
            
            # Ask for strict time
            print("\n⏳ Strict Time Limit Filter")
            print("   Example: 5 = Only jobs posted within 5 hours")
            print("   Example: 24 = Only jobs posted within 1 day")
            print("   Example: 48 = Only jobs posted within 2 days")
            hours_input = input("Enter max hours old (or leave blank for default): ").strip()
            if hours_input.isdigit():
                config.JOB_STRICT_HOURS_FILTER = int(hours_input)
                print(f"✅ Strict Filter Applied: {config.JOB_STRICT_HOURS_FILTER} Hours")
            else:
                print("ℹ️ No strict time limit applied.")
                config.JOB_STRICT_HOURS_FILTER = None
                
            break
            
        elif choice == '5':
            print("Goodbye! 👋")
            sys.exit(0)
            
        else:
            print("❌ Invalid choice. Please try again.")

    print("\n" + "="*50)
    print(f"🎯 Mode          : {config.SEARCH_MODE.upper()}")
    print(f"🌍 Location      : {config.SEARCH_LOCATION_NAME}")
    print(f"📋 Target Items  : {len(target_list)}")
    print(f"🛡️  Daily Cap     : {config.MAX_DAILY_SEARCHES} searches")
    print(f"🔄 UA Rotation   : {'ON' if config.ROTATE_USER_AGENT else 'OFF'}")
    
    if config.SEARCH_MODE == "people":
        total_searches = len(target_list) * len(config.JOB_TITLES)
        print(f"🔍 Total Searches: {total_searches} (Companies × Titles)")
        print(f"📁 Output file   : {config.OUTPUT_FILE}")
    elif config.SEARCH_MODE == "jobs":
        print(f"📁 Output file   : {config.JOBS_OUTPUT_FILE}")
    elif config.SEARCH_MODE == "candidates":
        print(f"📁 Output file   : {config.CANDIDATES_OUTPUT_FILE}")
        
    print("="*50)
    print("\nPress ENTER to start, or Ctrl+C to cancel...")
    input()

    scraper = LinkedInScraper(config)
    await scraper.run(target_list)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted. Program stopped.")
        sys.exit(0)