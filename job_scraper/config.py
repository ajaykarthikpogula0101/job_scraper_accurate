import os


def _env(name, default):
    value = os.environ.get(name, "")
    return value.strip() if value.strip() else default


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


# Every path is overridable by environment variable so the same checkout runs
# unchanged on a Windows workstation (D:\data_companies) and on a Linux VM
# (/opt/job_scraper).  Nothing here assumes a drive letter.
BASE_DIR = os.path.abspath(_env(
    "JOB_SCRAPER_BASE_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
))

INPUT_FILE = _env("JOB_SCRAPER_INPUT", os.path.join(BASE_DIR, "COMPANYWEB29th_July.xlsx"))
OUTPUT_FILE = _env("JOB_SCRAPER_OUTPUT", os.path.join(BASE_DIR, "company_jobs_output.csv"))
STATE_FILE = _env("JOB_SCRAPER_STATE", os.path.join(BASE_DIR, "scraper_state.json"))
LOG_FILE = _env("JOB_SCRAPER_LOG", os.path.join(BASE_DIR, "scraper.log"))

# ---------------------------------------------------------------------------
# Timeouts.  Raised across the board: Camoufox renders and ATS APIs are slow,
# and a premature timeout is indistinguishable from an unreachable site.
# ---------------------------------------------------------------------------
CONNECT_TIMEOUT = 15
DEFAULT_TIMEOUT = 35
HOMEPAGE_TIMEOUT = 40
CAREER_TIMEOUT = 45
CAREER_PAGE_TIMEOUT = 45
JOB_DETAIL_TIMEOUT = 35
WEB_SEARCH_TIMEOUT = 30
SEARCH_TIMEOUT = WEB_SEARCH_TIMEOUT

DEFAULT_WORKERS = _env_int("JOB_SCRAPER_WORKERS", 10)

# ---------------------------------------------------------------------------
# Retry / backoff.
# ---------------------------------------------------------------------------
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 1.0          # seconds; attempt N waits BASE * 2**N + jitter
RETRY_BACKOFF_MAX = 20.0          # per-sleep ceiling
RETRY_ON_STATUS = (408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524)

# Small-company sites very often have expired or self-signed certificates.
# Dropping them loses real career pages, and we only ever read public HTML, so
# a single unverified retry is worth it.  Set JOB_SCRAPER_ALLOW_INSECURE_SSL=0
# to disable.
ALLOW_INSECURE_SSL = os.environ.get("JOB_SCRAPER_ALLOW_INSECURE_SSL", "1").strip() not in ("0", "false", "no")

# DNS resolution
DNS_MAX_ATTEMPTS = 3              # resolution attempts before giving up
DNS_CACHE_TTL = 3600              # successful lookups cached this long
DNS_NEGATIVE_CACHE_TTL = 900      # failures cached this long (stops hammering)
DNS_FALLBACK_SERVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")  # used via dnspython if available

# Probing a candidate homepage only needs to know whether it answers; a long
# timeout here multiplied by five urllib3 read retries cost 200s+ per dead host.
PROBE_TIMEOUT = 15

# Browser rendering (Camoufox)
BROWSER_RENDER_TIMEOUT_MS = _env_int("JOB_SCRAPER_RENDER_TIMEOUT_MS", 45000)
# A navigation timeout means the page will not load.  Retrying it six times at
# the full timeout blocked both render workers for minutes at a stretch.
BROWSER_RENDER_MAX_ATTEMPTS = _env_int("JOB_SCRAPER_RENDER_ATTEMPTS", 2)
BROWSER_RENDER_FAILURE_TTL = 1800     # remember unrenderable URLs this long
BROWSER_SETTLE_MS = 3000
BROWSER_LOAD_MORE_CLICKS = 40     # "load more" clicks per rendered page
BROWSER_WORKERS = _env_int("JOB_SCRAPER_BROWSER_WORKERS", 2)

# Search-engine reliability
SEARCH_MAX_ATTEMPTS = 3           # attempts per engine per query
SEARCH_ENGINE_MAX_FAILURES = 5    # consecutive failures before cooldown
SEARCH_ENGINE_COOLDOWN = 300      # seconds an engine stays benched
SEARCH_CACHE_TTL = 3600
SEARCH_MAX_RESULTS = 25
# Hard cap on search-engine queries per company.  The first run spent ~9 per
# company, which is both slow (6 companies/min) and a fast route to being
# rate-limited across 405k rows.
MAX_SEARCH_QUERIES_PER_COMPANY = _env_int("JOB_SCRAPER_SEARCH_BUDGET", 5)
# Grouped site: queries for ATS-board discovery.  1 keeps the cost at a single
# query; 2 broadens coverage at double the cost.
ATS_BOARD_QUERY_GROUPS = _env_int("JOB_SCRAPER_ATS_BOARD_GROUPS", 1)

# --- companies with no usable website of their own -------------------------
# 234,685 of the 405,210 input rows have no WEBSITE value at all, and more have
# one that is dead or wrong.  Those companies are found by NAME instead, which
# is search-driven, so that path gets its own larger budget and searches more
# ATS host groups.  Nothing here is spent on a company whose own site works.
SEARCH_BUDGET_NO_WEBSITE = _env_int("JOB_SCRAPER_SEARCH_BUDGET_NO_SITE", 10)
ATS_BOARD_QUERY_GROUPS_NO_WEBSITE = _env_int(
    "JOB_SCRAPER_ATS_BOARD_GROUPS_NO_SITE", 3)

# Before spending any search query, guess likely domains from the company name
# ("Acme Foods Ltd" -> acmefoods.com, acme-foods.com, acmefoods.de ...) and
# probe them over plain HTTP.  Failed guesses are NXDOMAIN, which the DNS
# negative cache answers instantly, so this is far cheaper than a search.
DOMAIN_GUESS_LIMIT = _env_int("JOB_SCRAPER_DOMAIN_GUESSES", 6)
# A guess has no external corroboration, so it must clear a higher bar than a
# searched or input address (0.34) before it is accepted as the company's site.
DOMAIN_GUESS_MIN_EVIDENCE = float(
    os.environ.get("JOB_SCRAPER_DOMAIN_GUESS_EVIDENCE", "0.60"))

# ---------------------------------------------------------------------------
# Extraction ceilings.
#
# These are safety valves only -- they are set far above anything a real
# company career site produces, so no genuine posting is ever truncated, but a
# pathological site (infinite pagination, link farm) cannot stall a worker
# forever.  Raise them freely.
# ---------------------------------------------------------------------------
MAX_CAREER_LINKS = 500            # career-page candidates processed per company
MAX_HOMEPAGE_CAREER_LINKS = 300   # career links harvested from a homepage
MAX_COMMON_PATH_PROBES = 24       # /careers, /jobs, ... probes per company
MAX_CAREER_SUBPAGES = 40          # career-hub branches walked per career page
MAX_SEARCH_CAREER_CANDIDATES = 15 # career pages taken from web search
MAX_SEARCH_JOB_CANDIDATES = 25    # job pages taken from web search
MAX_JOB_DETAIL_PAGES = 5000       # job detail pages fetched per source
MAX_JOBS_PER_COMPANY = 10000      # positions kept per company
MAX_LISTING_PAGES = 200           # listing/pagination pages walked per source
MAX_PAGINATION_PAGES = 200        # "next page" hops per listing
# Detail enrichment is SEQUENTIAL: a board with 2,000 postings meant 2,000
# round trips inside one worker, hours for a single company.  Positions all
# come from the listing, so capping this loses descriptions for the tail, not
# postings.  Raise it for a run that prioritises full descriptions.
MAX_ATS_DETAIL_ENRICH = _env_int("JOB_SCRAPER_DETAIL_ENRICH", 300)
MAX_ATS_API_OFFSET = 10000        # paging ceiling for ATS APIs

# Hard wall-clock budget per company.  Without it a single pathological site
# (infinite pagination, a 5,000-posting board, a stalling host) can hold a
# worker for hours; over a 405k-row run that is the difference between days
# and weeks.  Whatever has been collected when the budget expires is kept.
COMPANY_TIME_BUDGET = _env_int("JOB_SCRAPER_COMPANY_BUDGET", 180)

# Log rotation.  A single FileHandler reached 6.8 MB in one 100-company run;
# unrotated over a multi-week run it fills the disk.
LOG_MAX_BYTES = _env_int("JOB_SCRAPER_LOG_MAX_BYTES", 50 * 1024 * 1024)
LOG_BACKUP_COUNT = _env_int("JOB_SCRAPER_LOG_BACKUPS", 5)

ENABLE_WEB_SEARCH = True

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# ---------------------------------------------------------------------------
# Input column resolution.  The input workbooks are not consistent: some have
# (COMPANY_NAME, WEBSITE, COUNTRY), smoke_test_input.xlsx has
# (KEYID, COMPANY_NAME, COUNTRY, ENTITY_TYPE, WEBSITE).  Reading by position
# fed company names into the website column, which is what produced the
# NameResolutionError storm.  Columns are now resolved by header name.
# ---------------------------------------------------------------------------
INPUT_NAME_HEADERS = ("company_name", "company", "name", "companyname",
                      "company name", "organisation", "organization", "employer")
INPUT_WEBSITE_HEADERS = ("website", "web", "url", "domain", "website_url",
                         "company_website", "web site", "homepage")
INPUT_COUNTRY_HEADERS = ("country", "country_name", "location", "nation")

OUTPUT_COLUMNS = [
    "company_name",
    "country",
    "website",
    "career_page_url",
    "career_page_status",
    "career_page_discovery_method",
    "resolved_website",
    "website_discovery",
    "job_title",
    "job_category",
    "job_location",
    "posted_date",
    "application_deadline",
    "closed_date",
    "job_status",
    "extraction_status",
    "extraction_confidence",
    "extraction_evidence",
    "last_checked_at",
    "education_stream",
    "education_type",
    "education_qualification",
    "years_of_experience_min",
    "years_of_experience_max",
    "seniority_level",
    "employment_type",
    "skills",
    "description_language",
    "job_description",
    "job_description_clean",
    "job_url",
    "salary_disclosed",
    "salary",
    "min_salary",
    "max_salary",
    "currency",
    "source",
    "scraped_at",
    "num_positions",
]
