# job_scraper_accurate

Career-page / ATS job scraper with an **ownership gate**: a job is only
attributed to a company when the career page or job board provably belongs to
that company (its own verified domain, a board slug that carries a distinctive
word of its name, or a page that identifies the company). Fallbacks that used
to attach other employers' postings (web search, domain guessing, ATS-board
name search) are now verified or skipped.

The `rescrape/` folder holds the inputs and export script for the
`Job_Hiring.xlsx` rework (4,344 unique companies, keyed by KEYID / ID_Company /
DUNS_NUMBER / PARTY_NAME).

## 1. Setup (Windows, Python 3.11)

```powershell
git clone https://github.com/ajaykarthikpogula0101/job_scraper_accurate.git
cd job_scraper_accurate
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m camoufox fetch          # downloads the stealth browser used for JS-only pages (one time)
```

Optional check that nothing is broken (no network needed):

```powershell
python -m unittest test_job_scraper_offline
```

## 2. Scrape every company in Job_Hiring.xlsx

```powershell
$env:JOB_SCRAPER_LOG = "$PWD\rescrape\full_run.log"
python run_all.py --input rescrape\job_hiring_companies.csv --output rescrape\full_run.csv --workers 20
```

* Resume is on by default: re-running the same command skips companies already
  in `rescrape\full_run.csv`. Add `--no-resume` to start over.
* Progress is logged every 50 companies as `[N/4344]`:
  `Select-String -Path rescrape\full_run.log -Pattern '\[\d+/4344\]' | Select-Object -Last 1`
* Live summary while running: `python deploy\check_output.py --file rescrape\full_run.csv`
* Ctrl+C stops cleanly; progress is saved.

Quick 30-company smoke test first (about 7 minutes):

```powershell
python run_all.py --input rescrape\smoke_sample.csv --output rescrape\smoke_run.csv --workers 8 --no-resume
```

## 3. Build the client workbook

```powershell
python rescrape\export_job_hiring.py --scrape rescrape\full_run.csv --out D:\Job_Hiring_Corrected.xlsx --all-out D:\Job_Hiring_AllPostings.csv
python rescrape\validate_output.py D:\Job_Hiring_Corrected.xlsx
```

Workbook sheets:

| Sheet | Content |
|---|---|
| `Job_Hiring` | Leadership postings located in the entity's own country, in the original 21 columns (KEYID ... Max_Salary_USD) plus Job Location, Job URL, Career Page URL and source columns for verification |
| `Other_Country_Postings` | Leadership postings from the same board whose location is in a different country than the entity |
| `Company_Coverage` | Every input company: resolved website, career page, how it was found, scrape status, posting counts |
| `Summary` | Counts |

`--all-out` writes every scraped posting (all titles, not only leadership) as CSV.

**Country** is derived from the posting's location (`rescrape/location_country.py`);
when the location is blank, remote or unrecognisable it falls back to the
company's country and `Location Country Source` says so.

## What changed versus the previous scraper

* `job_scraper/ownership.py` – distinctive-token matching for domains, board slugs and page identity.
* `job_scraper/company.py` – ownership gate on searched websites, searched career pages, internet job search and ATS-board name search; boards parsed once and shared across subsidiaries (single-flight cache).
* `job_scraper/websearch.py` – search acceptance uses the same distinctive-token rules; names made only of generic words are not board-searched.
* `job_scraper/parsers_ats.py` – Workday / SmartRecruiters listing and detail fetches run concurrently; leadership titles are enriched first; Workday "N Locations" resolved from the detail record.
* `job_scraper/leadership.py` – the leadership-title criteria (same rules as the DuckDB export) in one place.
