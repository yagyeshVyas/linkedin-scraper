import asyncio
import sys
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import Config
from linkedin_scraper_proxy import LinkedInScraperWithProxy
from resume_parser import parse_resume_for_keywords

console = Console()

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
]

def print_menu():
    menu_text = """
[1] [bold cyan]🔍 Search People[/] (By Company + Job Title)
    • Best for: Finding specific roles at target orgs
[2] [bold green]💼 Search Jobs[/] (By Keywords + Filters)
    • Best for: Scraping open job listings safely
[3] [bold yellow]👤 Find Candidates[/] (By Skills / Keywords)
    • Best for: Sourcing talent regardless of company
[4] [bold magenta]📄 Smart Resume Job Match[/] (Time Filtered)
    • Best for: Finding perfectly matched fresh jobs
[5] [bold red]❌ Exit[/]
"""
    panel = Panel(menu_text, title="[bold white]LinkedIn Scraper (Proxy) v4.0 — Multi-Mode (Enhanced)[/]", border_style="blue", expand=False)
    console.print(panel)

async def main():
    config = Config()

    if config.LINKEDIN_EMAIL == "your_email@gmail.com":
        console.print("[bold red]❌ ERROR: Please set your LinkedIn credentials in config.py[/]")
        sys.exit(1)

    while True:
        print_menu()
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5"])
        
        if choice == '1':
            config.SEARCH_MODE = "people"
            target_list = list(dict.fromkeys(FORTUNE_500_COMPANIES)) # Or TEST_COMPANIES
            if not config.JOB_TITLES:
                console.print("[bold red]❌ ERROR: No JOB_TITLES set in config.py[/]")
                sys.exit(1)
            break
            
        elif choice == '2':
            config.SEARCH_MODE = "jobs"
            target_list = config.JOB_SEARCH_KEYWORDS
            if not target_list:
                console.print("[bold red]❌ ERROR: No JOB_SEARCH_KEYWORDS set in config.py[/]")
                sys.exit(1)
            break
            
        elif choice == '3':
            config.SEARCH_MODE = "candidates"
            target_list = config.CANDIDATE_SKILLS
            if not target_list:
                console.print("[bold red]❌ ERROR: No CANDIDATE_SKILLS set in config.py[/]")
                sys.exit(1)
            break
            
        elif choice == '4':
            config.SEARCH_MODE = "jobs"
            console.print(f"\n[cyan]📄 Reading Resume:[/] {config.RESUME_FILE_PATH}")
            extracted_skills = parse_resume_for_keywords(config.RESUME_FILE_PATH, top_n=5)
            
            if not extracted_skills:
                console.print("[bold red]❌ ERROR: Could not extract useful skills from resume.[/]")
                sys.exit(1)
                
            console.print(f"[bold yellow]✨ Extracted Skills:[/] {', '.join(extracted_skills)}")
            target_list = extracted_skills
            
            console.print("\n[bold cyan]⏳ Strict Time Limit Filter[/]")
            hours_input = Prompt.ask("Enter max hours old (or leave blank for default)", default="")
            if hours_input.isdigit():
                config.JOB_STRICT_HOURS_FILTER = int(hours_input)
                console.print(f"[bold green]✅ Strict Filter Applied:[/] {config.JOB_STRICT_HOURS_FILTER} Hours")
            else:
                console.print("[dim]ℹ️ No strict time limit applied.[/]")
                config.JOB_STRICT_HOURS_FILTER = None
            break
            
        elif choice == '5':
            console.print("[bold green]Goodbye! 👋[/]")
            sys.exit(0)

    table = Table(title="Run Configuration (With Auto-Rotating Proxy)", show_header=False, box=None)
    table.add_column("Key", style="cyan", justify="right")
    table.add_column("Value", style="white")
    table.add_row("Mode", config.SEARCH_MODE.upper())
    table.add_row("Location", config.SEARCH_LOCATION_NAME)
    table.add_row("Target Items", str(len(target_list)))
    table.add_row("Daily Cap", f"{config.MAX_DAILY_SEARCHES} searches")
    table.add_row("UA Rotation", "ON" if config.ROTATE_USER_AGENT else "OFF")
    table.add_row("Proxy Mode", "[bold green]AUTO-ROTATING (1 hr)[/]")

    if config.SEARCH_MODE == "people":
        table.add_row("Total Searches", f"{len(target_list) * len(config.JOB_TITLES)} (Companies × Titles)")
        table.add_row("Output file", config.OUTPUT_FILE)
    elif config.SEARCH_MODE == "jobs":
        table.add_row("Output file", config.JOBS_OUTPUT_FILE)
    elif config.SEARCH_MODE == "candidates":
        table.add_row("Output file", config.CANDIDATES_OUTPUT_FILE)

    console.print(Panel(table, border_style="green", expand=False))
    
    Prompt.ask("\n[bold]Press ENTER to start[/]")

    console.print("[bold blue]🚀 Initializing Proxy Scraper Engine...[/]")
    scraper = LinkedInScraperWithProxy(config)
    await scraper.run(target_list)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠️ Interrupted. Program stopped.[/]")
        sys.exit(0)
