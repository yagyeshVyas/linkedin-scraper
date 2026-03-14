"""
Main Runner — with Proxy Support
Run this instead of main.py to use free rotating proxies.
"""
import asyncio
import sys
from config import Config
from linkedin_scraper_proxy import LinkedInScraperWithProxy

# Same company lists as main.py
TEST_COMPANIES = ["Google", "Microsoft", "Amazon"]
FORTUNE_500_COMPANIES = [
    "Walmart", "Amazon", "Apple", "CVS Health", "UnitedHealth Group",
    "Microsoft", "Alphabet", "AT&T", "Chevron", "Home Depot",
    "JPMorgan Chase", "Johnson & Johnson", "Meta", "Wells Fargo",
    "Goldman Sachs", "Pfizer", "Nike", "IBM", "Comcast", "FedEx",
    # Add more as needed...
]

async def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║     LinkedIn People Scraper — FREE PROXY EDITION         ║
║                                                          ║
║  Proxies refresh automatically every hour                ║
║  Rotates to new proxy on every session break             ║
╚══════════════════════════════════════════════════════════╝
    """)

    config = Config()

    if "your_email" in config.LINKEDIN_EMAIL:
        print("❌ Set your credentials in config.py first!")
        sys.exit(1)

    companies = TEST_COMPANIES  # ← change to FORTUNE_500_COMPANIES for full run
    companies = list(dict.fromkeys(companies))

    print(f"🎯 Job Titles : {', '.join(config.JOB_TITLES)}")
    print(f"🌍 Location   : {config.SEARCH_LOCATION_NAME}")
    print(f"🏢 Companies  : {len(companies)}")
    print(f"🔄 Proxy Mode : AUTO-ROTATING (refreshes every hour)")
    print("\nPress ENTER to start...")
    input()

    scraper = LinkedInScraperWithProxy(config)
    await scraper.run(companies)

if __name__ == "__main__":
    asyncio.run(main())