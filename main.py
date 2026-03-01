"""
Main Runner - LinkedIn Recruiter Scraper
=========================================
Run this file to start scraping.

SETUP:
  1. pip install -r requirements.txt
  2. playwright install chromium
  3. Edit config.py with your LinkedIn credentials
  4. python main.py

RESUME: If the scraper stops, just run it again.
        It will pick up from where it left off.
"""

import asyncio
import sys
from config import Config
from linkedin_scraper import LinkedInScraper


# ─────────────────────────────────────────────
#  TOP 500 US COMPANIES (Fortune 500)
# ─────────────────────────────────────────────

FORTUNE_500_COMPANIES = [
    # TOP 50
    "Walmart", "Amazon", "Apple", "CVS Health", "UnitedHealth Group",
    "Exxon Mobil", "Berkshire Hathaway", "Alphabet", "McKesson", "AmerisourceBergen",
    "Costco Wholesale", "Cigna", "AT&T", "Microsoft", "Cardinal Health",
    "Chevron", "Home Depot", "Walgreens Boots Alliance", "JPMorgan Chase", "Marathon Petroleum",
    "Elevance Health", "Kroger", "Ford Motor", "Comcast", "Phillips 66",
    "Valero Energy", "Walt Disney", "Centene", "General Motors", "Target",
    "Fannie Mae", "Lowes Companies", "Johnson & Johnson", "FedEx", "Humana",
    "Wells Fargo", "Goldman Sachs", "Meta Platforms", "Freddie Mac", "United Parcel Service",
    "Eaton Vance", "IBM", "Procter & Gamble", "Allstate", "Pfizer",
    "Lockheed Martin", "Best Buy", "TIAA", "Bank of America", "Charter Communications",

    # 51–100
    "Raytheon Technologies", "Archer Daniels Midland", "Boeing", "PepsiCo", "Sysco",
    "Northrop Grumman", "New York Life Insurance", "ConocoPhillips", "Nationwide", "USAA",
    "State Farm", "Deere & Company", "Delta Air Lines", "American International Group", "Prudential Financial",
    "Johnson Controls", "HCA Healthcare", "Caterpillar", "Merck", "Enterprise Products Partners",
    "Aetna", "Tesoro", "General Electric", "MetLife", "Waste Management",
    "Abbott Laboratories", "American Airlines", "Plains All American Pipeline", "SYSCO", "Cisco Systems",
    "StoneX Group", "Nucor", "Southern Company", "United Airlines", "Publix Super Markets",
    "Applied Materials", "Baker Hughes", "US Foods", "Avnet", "Centene Corporation",
    "CDW", "Illinois Tool Works", "Hartford Financial Services", "Nike", "3M",
    "VF Corporation", "Ameriprise Financial", "AECOM", "Dollar General", "Qualcomm",

    # 101–200
    "Molina Healthcare", "Fluor", "Honeywell International", "Southwest Airlines", "Leidos Holdings",
    "Emerson Electric", "WBA", "Bristol-Myers Squibb", "General Dynamics", "Becton Dickinson",
    "Duke Energy", "Archer-Daniels-Midland", "Texas Instruments", "L3Harris Technologies", "Marriott International",
    "Altria Group", "Schlumberger", "Freeport-McMoRan", "Synchrony Financial", "Cognizant Technology Solutions",
    "Mastercard", "Visa", "Morgan Stanley", "American Express", "Capital One Financial",
    "Automatic Data Processing", "TIAA", "Norfolk Southern", "WestRock", "Ball Corporation",
    "Huntington Bancshares", "Xerox Holdings", "MassMutual", "CBRE Group", "Principal Financial Group",
    "Zimmer Biomet Holdings", "Fidelity National Financial", "Textron", "Tenet Healthcare", "AbbVie",
    "Aon", "Marsh & McLennan", "Fiserv", "Western Digital", "KKR",
    "Stifel Financial", "Tapestry", "Baxter International", "Packaging Corp of America", "Parker-Hannifin",

    # 201–300
    "Danaher", "Biogen", "Amgen", "Zoetis", "Zimmer Biomet",
    "CoStar Group", "Cintas", "Estee Lauder Companies", "Mohawk Industries", "VeriSign",
    "Seagate Technology", "Western Union", "Alliance Data Systems", "Teradata", "Gartner",
    "Booz Allen Hamilton", "Cognex", "IDEX", "Hubbell", "Graco",
    "Mettler-Toledo", "Watts Water Technologies", "RBC Bearings", "Entegris", "Watts Water",
    "Hilton Worldwide", "Hyatt Hotels", "Extended Stay America", "Wyndham Hotels", "Choice Hotels",
    "Intercontinental Hotels", "Marriott Vacations Worldwide", "Airbnb", "Booking Holdings", "Expedia Group",
    "Netflix", "Warner Bros Discovery", "Paramount Global", "Fox Corporation", "News Corp",
    "Spotify", "Snap", "Twitter", "LinkedIn", "Pinterest",
    "Lyft", "Uber Technologies", "DoorDash", "Instacart", "Grubhub",

    # 301–400
    "Salesforce", "ServiceNow", "Workday", "Adobe", "Intuit",
    "Splunk", "Palo Alto Networks", "CrowdStrike", "Okta", "Zscaler",
    "Twilio", "DocuSign", "Zoom Video", "RingCentral", "Slack Technologies",
    "Dropbox", "Box", "Atlassian", "Zendesk", "HubSpot",
    "Shopify", "BigCommerce", "WooCommerce", "Magento", "Squarespace",
    "Wix", "GoDaddy", "Rackspace", "Digital Ocean", "Linode",
    "Cloudflare", "Fastly", "Akamai Technologies", "Lumen Technologies", "CenturyLink",
    "T-Mobile", "Verizon Communications", "Sprint", "US Cellular", "Dish Network",
    "Comcast NBCUniversal", "Discovery", "AMC Networks", "Starz", "HBO",
    "Fidelity Investments", "Charles Schwab", "TD Ameritrade", "Interactive Brokers", "Robinhood",

    # 401–500
    "Visa Inc", "Square", "PayPal Holdings", "Stripe", "Braintree",
    "Adyen", "Klarna", "Affirm Holdings", "Blend Labs", "Chime",
    "Sofi Technologies", "Rocket Mortgage", "United Wholesale Mortgage", "Pennymac", "Quicken Loans",
    "loanDepot", "Caliber Home Loans", "Home Point Capital", "Guaranteed Rate", "Better.com",
    "Opendoor Technologies", "Offerpad", "Zillow Group", "Redfin", "Compass",
    "Realogy Holdings", "Re/Max", "Keller Williams", "Coldwell Banker", "Century 21",
    "Iron Mountain", "Public Storage", "Extra Space Storage", "Life Storage", "CubeSmart",
    "Prologis", "American Tower", "Crown Castle", "SBA Communications", "Uniti Group",
    "Equinix", "CoreSite Realty", "CyrusOne", "Switch", "QTS Realty",
    "AutoNation", "Group 1 Automotive", "Penske Automotive", "Lithia Motors", "Sonic Automotive",
]


async def main():
    print("""
╔══════════════════════════════════════════════════════════╗
║        LinkedIn Recruiter Scraper - Starting Up          ║
║                                                          ║
║  ⚠️  DISCLAIMER: Educational use only. This scraper      ║
║  may violate LinkedIn's Terms of Service. Use at         ║
║  your own risk. Consider LinkedIn's official API          ║
║  for commercial/professional use.                         ║
╚══════════════════════════════════════════════════════════╝
    """)

    config = Config()
"""
╔══════════════════════════════════════════════════════════════╗
║          LinkedIn People Scraper — Main Runner               ║
║                                                              ║
║  HOW TO USE:                                                 ║
║  1. Edit config.py — set credentials + job titles            ║
║  2. Run: python main.py                                      ║
║  3. Find results in: output/linkedin_results.xlsx            ║
║                                                              ║
║  ♻️  RESUME: If stopped, just run again. It picks up         ║
║     exactly where it left off.                               ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import sys
from config import Config
from linkedin_scraper import LinkedInScraper


# ══════════════════════════════════════════════════
#  🏢 COMPANY LIST
#
#  Edit this list to target specific companies,
#  or use FORTUNE_500_COMPANIES for the full list.
# ══════════════════════════════════════════════════

# ── Quick test (2-3 companies) ──────────────────
TEST_COMPANIES = [
    "Google",
    "Microsoft",
    "Amazon",
]

# ── Custom list — add your own ──────────────────
MY_COMPANIES = [
    # "Apple",
    # "Meta",
    # "Netflix",
    # "Tesla",
    # Add as many as you want...
]

# ── Full Fortune 500 ────────────────────────────
FORTUNE_500_COMPANIES = [
    # TOP 50
    "Walmart", "Amazon", "Apple", "CVS Health", "UnitedHealth Group",
    "Exxon Mobil", "Berkshire Hathaway", "Alphabet", "McKesson", "AmerisourceBergen",
    "Costco Wholesale", "Cigna", "AT&T", "Microsoft", "Cardinal Health",
    "Chevron", "Home Depot", "Walgreens", "JPMorgan Chase", "Marathon Petroleum",
    "Elevance Health", "Kroger", "Ford Motor", "Comcast", "Phillips 66",
    "Valero Energy", "Walt Disney", "Centene", "General Motors", "Target",
    "Fannie Mae", "Lowes", "Johnson & Johnson", "FedEx", "Humana",
    "Wells Fargo", "Goldman Sachs", "Meta", "Freddie Mac", "UPS",
    "IBM", "Procter & Gamble", "Allstate", "Pfizer",
    "Lockheed Martin", "Best Buy", "Bank of America", "Charter Communications",

    # 51-100
    "Raytheon Technologies", "Boeing", "PepsiCo", "Sysco",
    "Northrop Grumman", "ConocoPhillips", "Nationwide", "USAA",
    "State Farm", "Deere & Company", "Delta Air Lines", "AIG", "Prudential Financial",
    "Johnson Controls", "HCA Healthcare", "Caterpillar", "Merck",
    "General Electric", "MetLife", "Waste Management",
    "Abbott Laboratories", "American Airlines", "Cisco Systems",
    "Nike", "3M", "Dollar General", "Qualcomm",

    # 101-200
    "Honeywell", "Southwest Airlines", "Emerson Electric",
    "Bristol-Myers Squibb", "General Dynamics", "Becton Dickinson",
    "Duke Energy", "Texas Instruments", "L3Harris Technologies", "Marriott International",
    "Altria Group", "Schlumberger", "Mastercard", "Visa", "Morgan Stanley",
    "American Express", "Capital One", "ADP", "Norfolk Southern",
    "Fidelity National Financial", "Textron", "Tenet Healthcare", "AbbVie",
    "Aon", "Marsh & McLennan", "Fiserv", "KKR",

    # 201-300
    "Danaher", "Biogen", "Amgen", "Zoetis",
    "Cintas", "Estee Lauder", "Gartner",
    "Booz Allen Hamilton", "Hilton Worldwide", "Hyatt Hotels",
    "Wyndham Hotels", "Choice Hotels", "Booking Holdings", "Expedia Group",
    "Netflix", "Warner Bros Discovery", "Paramount Global", "Fox Corporation",
    "Uber Technologies", "DoorDash", "Lyft",

    # 301-400
    "Salesforce", "ServiceNow", "Workday", "Adobe", "Intuit",
    "Palo Alto Networks", "CrowdStrike", "Okta", "Zscaler",
    "Twilio", "DocuSign", "Zoom Video", "RingCentral",
    "Dropbox", "Atlassian", "Zendesk", "HubSpot", "Shopify",
    "Cloudflare", "Akamai Technologies", "Lumen Technologies",
    "T-Mobile", "Verizon Communications",

    # 401-500
    "PayPal Holdings", "Square", "Stripe", "Affirm Holdings",
    "SoFi Technologies", "Rocket Mortgage", "Opendoor Technologies",
    "Zillow Group", "Redfin", "Compass",
    "Iron Mountain", "Public Storage", "Prologis", "American Tower",
    "Equinix", "AutoNation", "Lithia Motors", "Sonic Automotive",
    "Walmart", "Costco", "Target", "Kroger", "Albertsons",
]


async def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║           LinkedIn People Scraper v2.0                       ║
║                                                              ║
║  ⚠️  For educational purposes only.                          ║
║  Use responsibly. Scraping may violate LinkedIn ToS.         ║
╚══════════════════════════════════════════════════════════════╝
    """)

    config = Config()

    # Validate credentials
    if "your_email" in config.LINKEDIN_EMAIL:
        print("❌ ERROR: Please set your LinkedIn credentials in config.py")
        print("   Edit LINKEDIN_EMAIL and LINKEDIN_PASSWORD")
        sys.exit(1)

    if not config.JOB_TITLES:
        print("❌ ERROR: No job titles set! Edit JOB_TITLES in config.py")
        sys.exit(1)

    # ── Choose which company list to use ──
    companies = FORTUNE_500_COMPANIES   # ← Change to TEST_COMPANIES or MY_COMPANIES
    companies = list(dict.fromkeys(companies))  # Remove duplicates

    # Print run summary
    print(f"🎯 Job Titles    : {', '.join(config.JOB_TITLES)}")
    print(f"🌍 Location      : {config.SEARCH_LOCATION_NAME}")
    print(f"🏢 Companies     : {len(companies)}")
    print(f"🔍 Total searches: {len(companies) * len(config.JOB_TITLES)}")
    print(f"📁 Output file   : {config.OUTPUT_FILE}")
    estimate_min = len(companies) * len(config.JOB_TITLES) * 3
    estimate_max = len(companies) * len(config.JOB_TITLES) * 8
    print(f"⏱️  Est. time     : {estimate_min}–{estimate_max} minutes")
    print()
    print("Press ENTER to start, or Ctrl+C to cancel...")
    input()

    scraper = LinkedInScraper(config)
    await scraper.run(companies)


if __name__ == "__main__":
    asyncio.run(main())
    # Validate credentials
    if config.LINKEDIN_EMAIL == "your_email@gmail.com":
        print("❌ ERROR: Please set your LinkedIn credentials in config.py")
        print("   Edit LINKEDIN_EMAIL and LINKEDIN_PASSWORD in config.py")
        sys.exit(1)

    print(f"📋 Companies to process: {len(FORTUNE_500_COMPANIES)}")
    print(f"🎯 Target: Recruiter profiles in United States")
    print(f"📁 Output: {config.OUTPUT_FILE}")
    print(f"⏱️  Estimated time: {len(FORTUNE_500_COMPANIES) * 3} - {len(FORTUNE_500_COMPANIES) * 8} minutes")
    print("\nPress ENTER to start, or Ctrl+C to cancel...")
    input()

    scraper = LinkedInScraper(config)
    await scraper.run(FORTUNE_500_COMPANIES)


if __name__ == "__main__":
    asyncio.run(main())