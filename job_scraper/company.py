import re
import socket
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .config import (
    CAREER_PAGE_TIMEOUT,
    HOMEPAGE_TIMEOUT,
    JOB_DETAIL_TIMEOUT,
    COMPANY_TIME_BUDGET,
    MAX_CAREER_LINKS,
    PROBE_TIMEOUT,
    MAX_CAREER_SUBPAGES,
    MAX_COMMON_PATH_PROBES,
    MAX_HOMEPAGE_CAREER_LINKS,
    MAX_JOBS_PER_COMPANY,
    MAX_PAGINATION_PAGES,
    MAX_SEARCH_JOB_CANDIDATES,
    SEARCH_BUDGET_NO_WEBSITE,
    ATS_BOARD_QUERY_GROUPS,
    ATS_BOARD_QUERY_GROUPS_NO_WEBSITE,
    DOMAIN_GUESS_LIMIT,
    DOMAIN_GUESS_MIN_EVIDENCE,
)
from .ats import (
    detect_ats_in_url,
    is_career_link,
    is_probable_board_url,
    find_career_links,
    common_career_urls,
    validate_career_page,
)
from .urlutils import normalize_website, hostname
from .session import ScrapeSession
from .websearch import (search_company_website, search_company_career_pages,
                        search_company_job_pages, search_ats_boards,
                        set_query_budget, raise_query_budget)
from . import parsers_ats
from .parsers_generic import parse_generic
from .fields import clean_text, enrich_job
from .ownership import (page_evidence, domain_matches_company,
                        board_belongs_to_company, slug_matches_company,
                        distinctive_tokens)
from .ats import registrable_domain

# Minimum share of the company's distinctive name words that a page found by
# WEB SEARCH must show before it is accepted as the company's website or
# career page.  Input websites are client data and are trusted with a lower
# bar; searched pages have no such backing.
SEARCH_MIN_EVIDENCE = 0.5


def _same_registrable_domain(url_a, url_b):
    a = registrable_domain(hostname(url_a or "")) if url_a else ""
    b = registrable_domain(hostname(url_b or "")) if url_b else ""
    return bool(a and b and a == b)


# ---------------------------------------------------------------------------
# Board-level result cache.  Forty Accenture subsidiaries all resolve to the
# same Workday board; parsing it forty times costs forty time budgets for one
# answer.  Keyed on the board identity, bounded, deep-copied on read so that
# per-company enrichment never mutates a shared object.
# ---------------------------------------------------------------------------
import copy
import threading
import collections
_BOARD_CACHE = collections.OrderedDict()
_BOARD_INFLIGHT = {}
_BOARD_CACHE_LOCK = threading.Lock()
_BOARD_CACHE_LIMIT = 48

# Vendors without a dedicated API parser: rendered with Camoufox and parsed
# generically (JSON-LD, microdata, listing links, inline postings).  Being
# listed here is far better than being unrecognised -- an unknown board gets
# no rendering hint and no ATS attribution.
_KNOWN_GENERIC = {
    "teamtailor", "softgarden", "join", "bamboo", "icims", "taleo",
    "jobvite", "oracle", "pinpoint", "zoho", "freshteam",
    "jobadder", "bullhorn", "indeed", "adzuna",
    "avature", "talentsoft", "hrmanager",
    # newly recognised
    "rippling", "dayforce", "phenom", "paylocity", "paycom", "ukg", "adp",
    "isolved", "hibob", "homerun", "occupop", "tribepad", "lumesse",
    "cezanne", "hireserve", "eploy", "jobylon", "reachmee", "emply",
    "talentlyft", "factorial", "otys", "keka", "darwinbox", "zwayam",
    "manatal", "jobsoid",
    # Brazil / LatAm
    "gupy", "kenoby", "solides", "inhire", "abler", "quickin", "pandape",
    "99jobs", "compleo", "selecty",
    # DACH
    "dvinci", "rexx", "umantis", "prescreen", "onlyfy", "concludis", "bite",
    "talention", "workwise", "guidecom", "interamt",
    # India
    "hrone", "greythr", "turbohire", "peoplestrong", "springrecruit",
    "ceipal", "oorwin",
    # Australia / NZ
    "livehire", "pageup", "elmo", "employmenthero", "expr3ss", "foundu",
    "scouterecruit",
    # Nordics / Benelux / France / CEE
    "webcruiter", "jobbnorge", "varbi", "talentech", "flatchr", "taleez",
    "digitalrecruiters", "softy", "beetween", "welcometothejungle",
    "carerix", "recruitnow", "erecruiter", "traffit", "hrappka",
    # North America / global mid-market
    "applicantpro", "applicantstack", "clearcompany", "trakstar", "newton",
    "paycor", "brassring", "silkroad", "catsone", "crelate", "vincere",
    "jobdiva", "recruitcrm", "smartsearch", "workstream", "fountain",
    "hireology", "paradox", "avionte", "polymer", "infor",
    # Education / public sector
    "interfolio", "peopleadmin", "schooljobs", "governmentjobs",
    # US education / public sector
    "applitrack", "schoolspring", "frontline", "powerschool",
    "interviewexchange", "symplr",
    # additional vendors with known board hosts
    "100hires", "peoplehr", "altamira", "apploi", "beapplied", "arcoro",
    "auzmor", "bernieportal", "bizneo", "brightmove", "briohr", "careerplug",
    "chameleoni", "ciphr", "clayhr", "cleverstaff", "comeet", "connexys",
    "coveto", "cvminder", "datacruit", "dover", "dualoo", "eddy", "employwise",
    "exelare", "firefish", "fitzii", "folkshr", "gestmax", "gohire",
    "gr8people", "harri", "higherme", "hirebridge", "hireful", "hireplanner",
    "hiringthing", "hrworks", "ismartrecruit", "irecruit", "inrecruiting",
    "jobboardio", "jobconvo", "jobscore", "jobtrain", "keldair", "kula",
    "lanteria", "loxo", "njoyn", "oleeo", "onehcm", "pcrecruiter",
    "peoplefluent", "peopleforce", "pereless", "pitchnhire", "porters",
    "pyjamahr", "qjumpers", "recooty", "recruitbpm", "recruiterflow",
    "recruiteze", "sagehr", "skeeled", "sloneek", "smartrecruitonline",
    "smartjobboard", "snaphire", "snaphunt", "sparkhire", "staffcv",
    "talentera", "talentnest", "talentrecruit", "talexio", "talos",
    "targetrecruit", "teamengine", "teamworkonline", "teamdash", "tempworks",
    "applicantmanager", "tool2match", "trackerrms", "trisys", "truckright",
    "viterbit", "vultus", "winsearch", "wizehire", "workforcecom",
    "workforcehub", "workllama", "vagas", "jobteaser",
}

_API_PARSERS = {
    "greenhouse": parsers_ats.parse_greenhouse,
    "lever": parsers_ats.parse_lever,
    "smartrecruiters": parsers_ats.parse_smartrecruiters,
    "workable": parsers_ats.parse_workable,
    "recruitee": parsers_ats.parse_recruitee,
    "breezy": parsers_ats.parse_breezy,
    "jazzhr": parsers_ats.parse_jazzhr,
    "personio": parsers_ats.parse_personio,
    "workday": parsers_ats.parse_workday,
    "successfactors": parsers_ats.parse_successfactors,
    "ashby": parsers_ats.parse_ashby,
    "eightfold": parsers_ats.parse_eightfold,
    "cornerstone": parsers_ats.parse_cornerstone,
}

_SOCIAL_OR_AGGREGATOR_RE = re.compile(
    r"(?:linkedin|facebook|twitter|x\.com|instagram|youtube|tiktok|pinterest|"
    r"wikipedia|indeed|glassdoor|monster|ziprecruiter|careerbuilder|xing|"
    r"stepstone|naukri|wellfound|angel\.co|google\.|bing\.|yahoo\.|"
    r"crunchbase|zoominfo|trustpilot|yelp|yellowpages|dun\.com)",
    re.IGNORECASE,
)

_EXPLICIT_NO_JOBS_RE = re.compile(
    r"\b(?:no|not currently any|currently no)\s+(?:open\s+)?(?:positions|jobs|vacancies|openings)\b|"
    r"\bwe (?:do not|don't) have any (?:openings|vacancies)\b|"
    r"\bingen ledige stillinger\b|\bkeine (?:offenen )?(?:stellen|stellenangebote)\b|"
    r"\baucune offre d['’]emploi\b|\bno hay (?:vacantes|puestos disponibles)\b",
    re.IGNORECASE,
)


def _ats_info(ats, url, captured):
    info = {"board": captured or "", "base_url": url}
    if ats == "personio":
        m = re.search(r"jobs\.personio\.([a-z]{2}(?:\.[a-z]{2})?)", url)
        info["tld"] = m.group(1) if m else "de"
    elif ats == "workday":
        info = parsers_ats.workday_info_from_url(url)
    elif ats in ("eightfold", "cornerstone", "dayforce", "ukg", "adp"):
        parsed = urlparse(url)
        info["base_url"] = "%s://%s" % (parsed.scheme or "https", parsed.netloc)
    elif ats == "ashby":
        if not captured:
            segments = [seg for seg in urlparse(url).path.split("/") if seg]
            info["board"] = segments[0] if segments else ""
    elif ats == "smartrecruiters":
        if not captured:
            p = urlparse(url)
            segs = [s for s in p.path.split("/") if s]
            info["board"] = segs[0] if segs else ""
    return info


def _dispatch(session, ats, url, captured):
    info = _ats_info(ats, url, captured)
    if ats == "smartrecruiters" and not info.get("board"):
        return []
    parser = _API_PARSERS.get(ats)
    if not parser:
        return []
    key = (ats, tuple(sorted((k, str(v)) for k, v in info.items())))
    # Single-flight: when twenty Accenture subsidiaries reach the same
    # Workday board at once, one worker parses it and the rest wait for that
    # result instead of each paging through 10,000 postings themselves.
    owner = False
    with _BOARD_CACHE_LOCK:
        cached = _BOARD_CACHE.get(key)
        if cached is not None:
            _BOARD_CACHE.move_to_end(key)
            return copy.deepcopy(cached)
        event = _BOARD_INFLIGHT.get(key)
        if event is None:
            event = threading.Event()
            _BOARD_INFLIGHT[key] = event
            owner = True
    if not owner:
        import logging
        logging.getLogger("job_scraper").info(
            "Waiting for in-flight parse of %s board %s", ats, info.get("base_url") or info.get("board"))
        event.wait(timeout=1800)
        with _BOARD_CACHE_LOCK:
            cached = _BOARD_CACHE.get(key)
            if cached is not None:
                return copy.deepcopy(cached)
        # the owner produced nothing (or failed): do not retry a dead board
        return []
    try:
        import logging
        logging.getLogger("job_scraper").info(
            "Parsing %s board %s", ats, info.get("base_url") or info.get("board"))
        try:
            jobs = parser(session, info) or []
        except Exception:
            jobs = []
        logging.getLogger("job_scraper").info(
            "Parsed %s board %s: %d postings", ats, info.get("base_url") or info.get("board"), len(jobs))
        with _BOARD_CACHE_LOCK:
            # cache empty results too, so waiters and later companies do not
            # re-page a board that yielded nothing
            _BOARD_CACHE[key] = copy.deepcopy(jobs)
            while len(_BOARD_CACHE) > _BOARD_CACHE_LIMIT:
                _BOARD_CACHE.popitem(last=False)
        return jobs
    finally:
        with _BOARD_CACHE_LOCK:
            _BOARD_INFLIGHT.pop(key, None)
            event.set()


def _candidate_ats(session, candidate_url, html):
    """Detect the ATS *and* the URL its board actually lives at.

    This used to return only (ats, captured) and throw the discovered URL
    away, so a careers hub that links out to an external board -- e.g.
    vumc.org/careers -> vumc.wd1.myworkdayjobs.com/vumccareers -- was
    dispatched against the hub's own URL.  For Workday that produced
    "https://www.vumc.org/wday/cxs/careers/careers/jobs", a 404, no jobs, and
    a "Career Page Found - Extraction Unsupported" verdict for a company with
    thousands of open positions.

    Returns (ats_name, captured_board, ats_url).
    """
    ats, captured = detect_ats_in_url(candidate_url)
    if ats:
        return ats, captured, candidate_url
    if not html:
        return None, None, ""

    from .urlutils import url_join
    from .ats import ats_urls_in_html
    soup = BeautifulSoup(html, "html.parser")

    # Prefer links whose text or href looks like the real job board, so a
    # footer "powered by" link does not beat the main "Search Jobs" button.
    scored = []
    anchor_urls = set()
    for anchor in soup.find_all("a", href=True):
        full = url_join(candidate_url, anchor["href"])
        if not full:
            continue
        name, cap = detect_ats_in_url(full)
        if not name or not is_probable_board_url(full):
            continue
        anchor_urls.add(full)
        text = " ".join(anchor.get_text(" ", strip=True).split()).lower()
        score = 0
        if re.search(r"search|view|all|current|open|browse|apply|job|caree|vacan", text):
            score += 2
        if re.search(r"job|caree|vacan|search", full, re.I):
            score += 1
        scored.append((-score, len(full), name, cap, full))
    if scored:
        scored.sort()
        _, _, name, cap, full = scored[0]
        return name, cap, full

    # Boards embedded rather than linked: <iframe src>, widget <script src>,
    # <form action>, data-* attributes, inline JS.
    for name, cap, full in ats_urls_in_html(html, candidate_url):
        if full not in anchor_urls:
            return name, cap, full

    # Embedded ATS URLs in scripts are useful; bare vendor words are not.
    embedded = re.search(
        r"https?://[^\"'\s<>]+(?:greenhouse\.io|lever\.co|smartrecruiters\.com|"
        r"workable\.com|teamtailor\.com|recruitee\.com|breezy\.hr|applytojob\.com|"
        r"bamboohr\.com|personio\.[a-z.]+|myworkdayjobs\.com|taleo\.net|jobvite\.com|"
        r"icims\.com|jobvite\.com|successfactors\.[a-z.]+|sapsf\.[a-z.]+|"
        r"pinpointhq\.com|zohorecruit\.[a-z.]+|softgarden\.io|jazzhr\.com)[^\"'\s<>]*",
        html, re.I)
    if embedded:
        url = embedded.group(0).rstrip('\\"\'),;')
        name, cap = detect_ats_in_url(url)
        if name and is_probable_board_url(url):
            return name, cap, url
    return None, None, ""


def _career_subpages(html, page_url, homepage_url, limit):
    """Same-domain career sub-pages linked from a careers hub.

    Large employers publish a hub -- "Nursing Careers", "Physician Careers",
    "Allied Health Careers" -- with no postings on the hub itself.  Career
    links were only ever harvested from the homepage, so those branches were
    never visited.
    """
    if not html:
        return []
    from .urlutils import url_join
    from .ats import registrable_domain

    page_domain = registrable_domain(hostname(page_url))
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        full = url_join(page_url, anchor["href"])
        if not full:
            continue
        full = full.split("#")[0].rstrip("/")
        if not full or full in seen:
            continue
        if full.rstrip("/") == page_url.rstrip("/"):
            continue
        if not is_career_link(text, full):
            continue
        if registrable_domain(hostname(full)) != page_domain:
            continue
        if re.search(r"\.(?:pdf|docx?|xlsx?|jpe?g|png|gif|zip)$", full, re.I):
            continue
        # Skip pure informational branches: they never carry postings.
        if re.search(r"/(?:how-to-apply|contact|faq|benefits|why-|about|"
                     r"privacy|terms|login|sign-?in)\b", full, re.I):
            continue
        seen.add(full)
        out.append(full)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Website resolution.
#
# Input websites are frequently missing, stale, or simply belong to a
# different company.  Rather than trusting the value and giving up, every
# plausible address is probed and verified against the company name, and the
# search engines are used as a first-class source rather than a last resort.
# ---------------------------------------------------------------------------

_PARKED_RE = re.compile(
    r"(?:domain\s+(?:is\s+)?for\s+sale|buy\s+this\s+domain|this\s+domain\s+is\s+parked|"
    r"parked\s+(?:free\s+)?(?:at|by)|domain\s+(?:has\s+)?expired|renew\s+your\s+domain|"
    r"under\s+construction|coming\s+soon|site\s+not\s+published|"
    r"future\s+home\s+of\s+something|default\s+web\s?site\s+page|"
    r"apache2?\s+(?:ubuntu\s+|debian\s+)?default\s+page|welcome\s+to\s+nginx|"
    r"iis\s+windows\s+server|account\s+suspended|bandwidth\s+limit\s+exceeded|"
    r"sedo\.com|hugedomains|godaddy\.com/domainsearch|afternic|dan\.com/buy-domain)",
    re.I,
)

_NAME_NOISE = {
    "the", "and", "of", "for", "ltd", "llc", "gmbh", "inc", "corp", "co",
    "sa", "oy", "ab", "bv", "nv", "plc", "limited", "company", "group",
    "holding", "srl", "ag", "kg", "sas", "spa", "pty", "pvt", "private",
    "sdn", "bhd", "ooo", "zao", "doo", "sro", "kft", "corporation", "ltda",
    "eirl", "cia", "sarl", "sl", "aps", "as", "oyj", "kk", "kft", "dba",
}


def _name_tokens(name):
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return [w for w in words if len(w) > 2 and w not in _NAME_NOISE]


def _company_evidence(html, name):
    """Fraction of the company's *distinctive* name tokens present on the page.

    Generic words (state, national, city, hospital, ...) no longer count: they
    are what let state.gov pass as the website of a Sri Lankan pharmaceutical
    corporation.  See ownership.page_evidence.
    """
    return page_evidence(html, name)


def _probe_site(session, url, name, timeout=None):
    """Fetch a candidate homepage and judge whether it is usable and correct."""
    outcome = {"url": url, "final_url": url, "html": "", "status": 0,
               "alive": False, "parked": False, "evidence": 0.0}
    if not url:
        return outcome
    response = session.fetch(url, timeout=timeout or PROBE_TIMEOUT, retries=1)
    if response is None:
        return outcome
    outcome["status"] = response.status_code
    outcome["final_url"] = response.url or url
    if response.status_code >= 400:
        return outcome
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "json" in content_type:
        outcome["alive"] = True
        return outcome
    html = response.text or ""
    outcome["html"] = html
    outcome["alive"] = bool(html.strip())
    text = clean_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True),
                      max_len=4000)
    # A parked or placeholder page is "reachable" but is not the company.
    if len(text) < 2000 and _PARKED_RE.search(text):
        outcome["parked"] = True
    outcome["evidence"] = _company_evidence(html, name)
    return outcome


# Country -> the TLD a company there is most likely to use.  ".com" is always
# tried as well.  Only the countries that actually appear in the input in
# quantity are listed; anything else falls back to ".com" alone.
_COUNTRY_TLDS = {
    "usa": ("com",), "united states": ("com",), "canada": ("ca", "com"),
    "united kingdom": ("co.uk", "com"), "uk": ("co.uk", "com"),
    "ireland": ("ie",), "germany": ("de",), "austria": ("at",),
    "switzerland": ("ch",), "france": ("fr",), "italy": ("it",),
    "spain": ("es",), "portugal": ("pt",), "netherlands": ("nl",),
    "belgium": ("be",), "denmark": ("dk",), "sweden": ("se",),
    "norway": ("no",), "finland": ("fi",), "poland": ("pl",),
    "czech republic": ("cz",), "czechia": ("cz",), "romania": ("ro",),
    "hungary": ("hu",), "greece": ("gr",), "turkey": ("com.tr",),
    "russia": ("ru",), "ukraine": ("ua",),
    "brazil": ("com.br",), "argentina": ("com.ar",), "chile": ("cl",),
    "mexico": ("com.mx",), "colombia": ("com.co",), "peru": ("com.pe",),
    "india": ("in", "co.in"), "china": ("cn", "com.cn"), "japan": ("co.jp", "jp"),
    "south korea": ("co.kr",), "korea": ("co.kr",), "taiwan": ("com.tw",),
    "singapore": ("com.sg",), "malaysia": ("com.my",), "indonesia": ("co.id",),
    "thailand": ("co.th",), "vietnam": ("vn",), "philippines": ("com.ph",),
    "australia": ("com.au",), "new zealand": ("co.nz",),
    "south africa": ("co.za",), "nigeria": ("com.ng",), "kenya": ("co.ke",),
    "egypt": ("com.eg",), "israel": ("co.il",),
    "united arab emirates": ("ae",), "saudi arabia": ("com.sa",),
}


def _guess_hosts(name, country):
    """Hostnames a company of this name in this country plausibly uses.

    "Acme Foods Ltd" in Germany -> acmefoods.de, acme-foods.de, acmefoods.com,
    acme-foods.com, acme.de, acme.com.  Nothing is fetched here; the caller
    checks which of them exist in DNS first.
    """
    tokens = _name_tokens(name)
    if not tokens:
        return []
    # A one-word name that is short is too generic to guess from: "star.com"
    # belongs to someone else.
    if len(tokens) == 1 and len(tokens[0]) < 5:
        return []

    stems = []

    def add_stem(value):
        if value and 3 < len(value) <= 30 and value not in stems:
            stems.append(value)

    add_stem("".join(tokens))
    if len(tokens) >= 2:
        add_stem("-".join(tokens))
        add_stem("".join(tokens[:2]))
    if len(tokens[0]) >= 5:
        add_stem(tokens[0])

    tlds = list(_COUNTRY_TLDS.get((country or "").strip().lower(), ()))
    if "com" not in tlds:
        tlds.append("com")

    hosts = []
    for stem in stems:
        for tld in tlds:
            host = "%s.%s" % (stem, tld)
            if host not in hosts:
                hosts.append(host)
            if len(hosts) >= DOMAIN_GUESS_LIMIT:
                return hosts
    return hosts


def _resolves(host):
    """True if the hostname exists in DNS.

    netcache installs a cached resolver, so a name that does not exist is
    answered from the negative cache after the first miss -- which makes
    checking a handful of guesses far cheaper than a single search query.
    """
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False


def _guess_candidates(name, country):
    """Guessed addresses that actually resolve, ready to probe."""
    candidates = []
    for host in _guess_hosts(name, country):
        if _resolves(host):
            candidates.append(("https://" + host, "name_domain_guess"))
        elif _resolves("www." + host):
            candidates.append(("https://www." + host, "name_domain_guess"))
    return candidates


def _input_candidates(base):
    """Addresses derived from the input value alone -- no search required."""
    candidates = []

    def add(url, method):
        if not url:
            return
        normalized = url.rstrip("/")
        if all(normalized != existing[0].rstrip("/") for existing in candidates):
            candidates.append((url, method))

    if base:
        add(base, "input_website")
        host = urlparse(base).netloc
        if host:
            add("http://" + host, "input_website_http")
            if host.lower().startswith("www."):
                add("https://" + host[4:], "input_website_bare")
            else:
                add("https://www." + host, "input_website_www")
    return candidates


def _resolve_homepage(name, website, country, session, enable_search):
    """Find an address that is both reachable and plausibly this company.

    Search is used lazily: the input website and its http/www variants are
    probed first, and a web search only happens if none of them work.  Running
    the search eagerly cost one query for every company, including the 424 of
    999 whose own website was fine.

    Returns (base, homepage_url, homepage_html, method, note).
    """
    import logging
    log = logging.getLogger("job_scraper")

    strong_threshold = 0.34          # at least a third of the name tokens
    best_weak = None
    attempts = []

    def probe_all(candidates):
        nonlocal best_weak
        for url, method in candidates:
            probe = _probe_site(session, url, name)
            attempts.append("%s=%s%s" % (
                method, probe["status"] or "conn-fail",
                "/parked" if probe["parked"] else ""))
            if not probe["alive"] or probe["parked"]:
                continue
            if probe["evidence"] >= strong_threshold or not _name_tokens(name):
                return probe, method
            if best_weak is None or probe["evidence"] > best_weak[0]["evidence"]:
                best_weak = (probe, method)
        return None, None

    base = normalize_website(website)
    probe, method = probe_all(_input_candidates(base)[:4])

    # The input WEBSITE column is missing for 234,685 of the 405,210 rows and
    # wrong or dead for more, so the company's own name is treated as the
    # primary key, not the URL.  Guessed domains are checked before any search
    # because a DNS miss is free and a search query is not.  A guess has no
    # external corroboration, so it must clear a higher evidence bar.
    # A name with no distinctive word ("City of Boston", "Ministry of
    # Health") cannot be verified by name.  Guessing or searching a website
    # for it produced somebody else's jobs, so those paths are closed and only
    # the client-supplied website is used.
    verifiable = bool(distinctive_tokens(name))

    if probe is None and verifiable:
        for url, guess_method in _guess_candidates(name, country):
            guess_probe = _probe_site(session, url, name)
            attempts.append("guess:%s=%s%s" % (
                hostname(url), guess_probe["status"] or "conn-fail",
                "/parked" if guess_probe["parked"] else ""))
            if not guess_probe["alive"] or guess_probe["parked"]:
                continue
            final_domain = registrable_domain(hostname(guess_probe["final_url"]))
            if (guess_probe["evidence"] >= DOMAIN_GUESS_MIN_EVIDENCE
                    and domain_matches_company(final_domain, name)):
                probe, method = guess_probe, guess_method
                break

    if probe is None and enable_search and (verifiable or len(_name_tokens(name)) >= 2):
        from .ownership import title_evidence
        searched = [(found, "web_search")
                    for found in (search_company_website(name, session, country=country) or [])]
        for url, search_method in searched[:5]:
            search_probe = _probe_site(session, url, name)
            attempts.append("%s=%s%s" % (
                search_method, search_probe["status"] or "conn-fail",
                "/parked" if search_probe["parked"] else ""))
            if not search_probe["alive"] or search_probe["parked"]:
                continue
            final_domain = registrable_domain(hostname(search_probe["final_url"]))
            # Accept a searched site only when (a) its domain carries a
            # distinctive word of the name AND the page shows the name, or
            # (b) the site's own title/site-name spells the name out almost
            # completely ("Technical University of Crete" on tuc.gr).  A
            # search engine returning state.gov for "State ... Corporation"
            # passes neither.
            domain_ok = verifiable and domain_matches_company(final_domain, name)
            title_share, title_count = title_evidence(search_probe["html"], name)
            title_ok = title_count >= 2 and title_share >= 0.8
            if (domain_ok and search_probe["evidence"] >= SEARCH_MIN_EVIDENCE) or title_ok:
                probe, method = search_probe, search_method
                break

    if probe is not None:
        parsed = urlparse(probe["final_url"])
        resolved = "%s://%s" % (parsed.scheme or "https", parsed.netloc)
        return (resolved, probe["final_url"], probe["html"], method,
                "evidence=%.2f" % probe["evidence"])

    # Only a client-supplied website may be used "unverified" (its page did
    # not show the company name -- common for non-Latin names and brand
    # sites).  A searched site never is.
    if best_weak is not None and best_weak[1].startswith("input_website"):
        probe, method = best_weak
        parsed = urlparse(probe["final_url"])
        resolved = "%s://%s" % (parsed.scheme or "https", parsed.netloc)
        log.debug("Weak website match for %s: %s (evidence=%.2f)",
                  name, resolved, probe["evidence"])
        return (resolved, probe["final_url"], probe["html"],
                method + "_unverified", "evidence=%.2f" % probe["evidence"])

    log.info("No usable website for %s [%s]", name, " ".join(attempts))
    return "", "", "", "none", "tried:" + ",".join(attempts)


def _unknown_board_handoff(session, page_url, html, company_name, limit=2):
    """Follow off-domain board-looking links from a career page.

    No vendor list can cover every ATS, so when nothing on the page is
    recognised this looks for outbound links that *look* like a job board,
    fetches the best one or two, and keeps them only if the fetched page
    actually behaves like a board (`looks_like_job_board`).  That makes an
    unlisted vendor work without a pattern for it.
    """
    if not html:
        return []
    from .urlutils import url_join
    from .ats import looks_like_job_board, registrable_domain
    from . import parsers_ats

    page_domain = registrable_domain(hostname(page_url))
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        full = url_join(page_url, anchor["href"])
        if not full:
            continue
        full = full.split("#")[0]
        if full in seen:
            continue
        target_domain = registrable_domain(hostname(full))
        if not target_domain or target_domain == page_domain:
            continue
        if _SOCIAL_OR_AGGREGATOR_RE.search(target_domain):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split()).lower()
        boardish = bool(re.search(
            r"/(?:jobs?|careers?|vacanc\w*|openings?|positions?|recruit\w*|"
            r"stellen\w*|karriere|vagas?|empleos?|emplois?|apply)(?:/|$|\?)",
            full, re.I))
        texty = bool(re.search(
            r"job|caree?r|vacanc|position|opening|apply|stelle|vaga|emploi|empleo",
            text))
        if not (boardish or texty):
            continue
        score = (2 if boardish else 0) + (1 if texty else 0)
        seen.add(full)
        candidates.append((-score, len(full), full))

    if not candidates:
        return []
    candidates.sort()

    jobs = []
    for _, _, board_url in candidates[:limit]:
        board_html = session.fetch_text(board_url, timeout=CAREER_PAGE_TIMEOUT)
        if not board_html or not looks_like_job_board(board_html, board_url):
            continue
        import logging
        logging.getLogger("job_scraper").info(
            "Unrecognised board detected structurally: %s -> %s",
            page_url, board_url)
        found = parsers_ats.probe_json_board(session, board_url, "unknown-ats")
        if not found:
            found = parse_generic(session, board_url)
        for job in found or []:
            # Overwrite, not default: the row must be auditable as having come
            # from behaviour-based detection rather than a known vendor.
            job["source"] = "structural-board-detection"
            job["extraction_evidence"] = (job.get("extraction_evidence")
                                          or "board:" + board_url)
        jobs.extend(found or [])
        if jobs:
            break
    return jobs


def process_company_details(company_row, session=None, enable_search=True):
    """
    company_row: (company_name, website, country)
    Return status/jobs plus auditable career-page discovery metadata.
    """
    name, website, country = company_row
    session = session or ScrapeSession()
    # One search budget per company (config.MAX_SEARCH_QUERIES_PER_COMPANY).
    set_query_budget()
    # Wall-clock budget: whatever is collected when it expires is returned.
    deadline = time.time() + COMPANY_TIME_BUDGET if COMPANY_TIME_BUDGET > 0 else None

    def out_of_time(stage=""):
        if deadline is None or time.time() <= deadline:
            return False
        import logging
        logging.getLogger("job_scraper").info(
            "Time budget (%ds) reached for %s%s; returning what was collected",
            COMPANY_TIME_BUDGET, name, (" at " + stage) if stage else "")
        return True
    base = normalize_website(website)
    sources = []
    discovery = {
        "career_page_url": "",
        "career_page_status": "Not Found",
        "career_page_discovery_method": "",
        # What address was actually used, and how it was found.  The input
        # WEBSITE column is unreliable, so the output records the truth.
        "resolved_website": "",
        "website_discovery": "",
    }

    def result(status, jobs, source):
        jobs = jobs or []
        # Single enrichment point for every source -- ATS APIs included.  The
        # vendor JSON usually carries only title/location/url; employment
        # type, seniority, education, skills, experience, category and salary
        # are derived here, so no row leaves with avoidable blanks.
        for job in jobs:
            try:
                enrich_job(job)
            except Exception:
                pass
        # num_positions is authoritative here: it is the count of jobs actually
        # returned for this company, computed before the result leaves this
        # function so every consumer (CSV, callers, tests) agrees.
        return {"status": status, "jobs": jobs, "source": source,
                "num_positions": len(jobs), **discovery}

    def internet_job_fallback(official_url):
        """Search by company name, then verify every result before extraction."""
        if not enable_search:
            return []
        # Nothing found this way can be verified for a name made only of
        # generic words unless the company has a verified site of its own,
        # so do not spend the queries.
        if not distinctive_tokens(name) and not official_url:
            return []
        found_jobs = []
        rejected = {"unfetchable": 0, "no_evidence": 0, "unparseable": 0}
        search_candidates = search_company_job_pages(
            name, official_url or website, session, country=country,
            limit=MAX_SEARCH_JOB_CANDIDATES)
        for candidate in search_candidates:
            if out_of_time("internet job search"):
                break
            candidate_html = session.fetch_text(candidate, timeout=CAREER_PAGE_TIMEOUT)
            if not candidate_html:
                rejected["unfetchable"] += 1
                continue
            page_text = clean_text(BeautifulSoup(candidate_html, "html.parser").get_text(" ", strip=True),
                                   max_len=50000)
            # Ownership: the page must be on the company's own (verified)
            # domain, or be a board whose slug carries the company name, or
            # show at least half of the company's distinctive name words.
            # "any single word anywhere on the page" is what attached a
            # U.S. State Department posting to a Sri Lankan corporation.
            same_domain = bool(official_url) and _same_registrable_domain(candidate, official_url)
            ats, captured = detect_ats_in_url(candidate)
            owned, why = board_belongs_to_company(ats, captured, candidate, name, candidate_html)
            job_evidence = bool(re.search(
                r"\b(?:apply|job description|responsibilities|qualifications|vacancy|"
                r"stellenangebot|aufgaben|bewerb(?:en|ung))\b", page_text, re.I))
            if not (job_evidence and (same_domain or owned)):
                rejected["no_evidence"] += 1
                continue
            parsed = parse_generic(session, candidate)
            if not parsed:
                rejected["unparseable"] += 1
                continue
            for job in parsed:
                job["source"] = job.get("source") or "internet-company-name-search"
            found_jobs.extend(parsed)
            # No cap here: every verified candidate contributes all its jobs.
        if found_jobs:
            first_url = found_jobs[0].get("job_url", "")
            discovery.update({
                "career_page_url": first_url,
                "career_page_status": "Validated",
                "career_page_discovery_method": "internet_company_name_search",
            })
        elif not discovery.get("career_page_url"):
            discovery.update({
                "career_page_status": "Search Completed - No Verified Jobs",
                "career_page_discovery_method": "internet_company_name_search_no_verified_results",
            })
        import logging
        logging.getLogger("job_scraper").info(
            "Internet job search company=%s candidates=%d verified_jobs=%d "
            "rejected(unfetchable=%d no_evidence=%d unparseable=%d)",
            name, len(search_candidates), len(found_jobs),
            rejected["unfetchable"], rejected["no_evidence"], rejected["unparseable"])
        return found_jobs

    board_search_done = []

    def ats_board_fallback():
        """Look for an ATS board published under this company's name.

        Memoized: it was being invoked from two paths, issuing the same
        queries twice for the same company.

        Search engines index Greenhouse/Lever/Workday/Ashby boards well, so a
        company with no working website of its own is often still findable
        this way.
        """
        if not enable_search or board_search_done:
            return []
        board_search_done.append(True)
        found_jobs = []
        # No website of its own: the board search is this company's best (often
        # only) chance, so ask about more vendors than usual.
        groups = (ATS_BOARD_QUERY_GROUPS_NO_WEBSITE
                  if not discovery.get("resolved_website")
                  else ATS_BOARD_QUERY_GROUPS)
        boards = search_ats_boards(name, session, country=country, groups=groups)
        for board_url in boards:
            ats, captured = detect_ats_in_url(board_url)
            if not ats:
                continue
            # A board found by NAME SEARCH must prove it is this company's:
            # slug/tenant carries the name, or the board page names the
            # company.  Otherwise the search engine's fuzzy match becomes
            # another employer's postings under this company.
            owned, why = board_belongs_to_company(ats, captured, board_url, name)
            if not owned:
                board_html = session.fetch_text(board_url, timeout=CAREER_PAGE_TIMEOUT)
                owned, why = board_belongs_to_company(ats, captured, board_url, name, board_html)
            if not owned:
                import logging
                logging.getLogger("job_scraper").info(
                    "Rejected searched board for %s: %s (%s)", name, board_url, why)
                continue
            if ats not in sources:
                sources.append(ats)
            if ats in _KNOWN_GENERIC:
                found_jobs.extend(parse_generic(session, board_url))
            else:
                board_jobs = _dispatch(session, ats, board_url, captured)
                found_jobs.extend(board_jobs or parse_generic(session, board_url))
            if found_jobs:
                discovery.update({
                    "career_page_url": board_url,
                    "career_page_status": "Validated",
                    "career_page_discovery_method": "ats_board_name_search:%s" % ats,
                })
                break
        import logging
        logging.getLogger("job_scraper").info(
            "ATS board search company=%s boards=%d jobs=%d",
            name, len(boards), len(found_jobs))
        return found_jobs

    def terminal_or_internet_search(status, source, official_url):
        fallback_jobs = internet_job_fallback(official_url)
        if fallback_jobs:
            return result("ok", fallback_jobs, "internet-company-name-search")
        board_jobs = ats_board_fallback()
        if board_jobs:
            return result("ok", board_jobs, "ats-board-name-search")
        evidence = source or "internet-company-name-search:no-verified-results"
        return result(status, [], evidence)

    jobs = []
    tried = set()
    # Boards already handled for this company.  Every /careers, /jobs,
    # /join-us, /karriere ... probe on one site resolves to the SAME external
    # board, and each one was dispatched, JSON-probed and parsed again --
    # seventeen times for a single company in one observed run.
    processed_boards = set()
    explicit_no_jobs_pages = set()

    # 1. Resolve an address that is reachable AND plausibly this company.
    #    Handles: missing website, wrong website, dead website, HTTP 4xx/5xx
    #    (previously ignored entirely), parked/placeholder pages, and
    #    http/https + www/non-www variants.
    base, homepage_url, homepage_html, website_method, website_note = _resolve_homepage(
        name, website, country, session, enable_search)
    discovery["resolved_website"] = base
    discovery["website_discovery"] = website_method
    if website_method not in ("input_website", "none"):
        sources.append("website:" + website_method)

    if not base:
        # No usable site at all.  Everything from here on is name-based, so lift
        # the query budget: the 5-query default exists to stop a company with a
        # working website from wasting searches, which is the opposite of this
        # company's problem.
        raise_query_budget(SEARCH_BUDGET_NO_WEBSITE)
        # Go straight to name-based discovery, including ATS boards published
        # under the company's name.
        board_jobs = ats_board_fallback()
        if board_jobs:
            return result("ok", board_jobs, "ats-board-name-search")
        return terminal_or_internet_search(
            "unreachable", website_note or "no_website", website)

    # 2. build candidate career urls
    candidates = []
    soup = BeautifulSoup(homepage_html or "", "html.parser")
    # Gather a wider pool before ranking. Otherwise several regional job links
    # can crowd the official careers root out of the fixed-size candidate list.
    found_links = find_career_links(soup, homepage_url,
                                    limit=MAX_HOMEPAGE_CAREER_LINKS)
    candidates.extend(found_links)
    candidate_methods = {url: "homepage_link" for url in found_links}
    # Merge discovery channels instead of allowing one weak homepage match to
    # suppress every fallback.
    for u, _ in common_career_urls(homepage_url)[:MAX_COMMON_PATH_PROBES]:
        candidates.append(u)
        candidate_methods.setdefault(u, "common_path_probe")
    strong_homepage_link = any(re.search(
        r"(?:career|jobs?|vacanc|join[-_/ ]?us|work[-_/ ]?with)", u, re.I)
        for u in found_links)
    if enable_search and not strong_homepage_link:
        for u in search_company_career_pages(name, homepage_url, session, country=country):
            candidates.append(u)
            candidate_methods.setdefault(u, "web_search")

    # dedupe candidates
    seen = set()
    cands = []
    for u in candidates:
        method = candidate_methods.get(u, "homepage_link")
        normalized = u.split("#")[0].rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            cands.append(normalized)
            candidate_methods[normalized] = method
    def candidate_priority(value):
        method = candidate_methods.get(value, "")
        strong = bool(re.search(r"career|jobs?|vacanc|join[-_/ ]?us|work[-_/ ]?with", value, re.I))
        ats_name, _ = detect_ats_in_url(value)
        path = urlparse(value).path.rstrip("/").lower()
        career_root = bool(re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?careers?$", path))
        regional_jobs = bool(re.search(r"/careers?/jobs?/.+", path))
        base_score = {"homepage_link": 30 if strong else 15,
                      "web_search": 20, "common_path_probe": 10}.get(method, 0)
        if career_root:
            base_score += 50
        if ats_name:
            base_score += 35
        if regional_jobs:
            base_score -= 20
        return (-base_score, -strong, len(value))

    # Order by likelihood, but process every discovered candidate.  The old
    # [:MAX_CAREER_LINKS] slice threw away real career pages whenever a
    # homepage exposed more than five career-ish links.
    candidates = sorted(cands, key=candidate_priority)[:MAX_CAREER_LINKS]

    # 3. Process explicit career candidates. A vendor marker on a homepage is
    # not enough to call the homepage a career page.
    for cand in candidates:
        # No `if len(jobs) >= 50: break` here: every candidate is processed and
        # contributes all of its postings.
        if out_of_time("career-candidate loop"):
            break
        if cand in tried:
            continue
        tried.add(cand)
        ats, cap = detect_ats_in_url(cand)
        cand_html = session.fetch_text(cand, timeout=CAREER_PAGE_TIMEOUT)
        if not cand_html and not ats:
            continue
        # Ownership gate.  Links harvested from the company's own (verified)
        # homepage and probes of its own paths are the company's; a page that
        # came from a WEB SEARCH is not, unless it is on the company's domain,
        # is a board whose slug carries the company name, or names the
        # company itself.  This is the check whose absence attached one
        # Eurofins posting to 124 unrelated companies.
        cand_method = candidate_methods.get(cand, "homepage_link")
        own_site = _same_registrable_domain(cand, homepage_url)
        if cand_method == "web_search" and not own_site:
            owned, why = board_belongs_to_company(ats, cap, cand, name, cand_html)
            if not owned:
                import logging
                logging.getLogger("job_scraper").info(
                    "Rejected searched career page for %s: %s (%s)", name, cand, why)
                continue
        if cand_html and _EXPLICIT_NO_JOBS_RE.search(
                BeautifulSoup(cand_html, "html.parser").get_text(" ", strip=True)):
            explicit_no_jobs_pages.add(cand)
        if not discovery["career_page_url"] and (
                (cand_html and validate_career_page(cand, cand_html, homepage_url)) or ats):
            discovery.update({
                "career_page_url": cand,
                "career_page_status": "Validated",
                "career_page_discovery_method": candidate_methods.get(cand, "homepage_link"),
            })
        ats_url = cand
        if not ats:
            ats, cap, discovered = _candidate_ats(session, cand, cand_html)
            if ats and discovered:
                ats_url = discovered
                # A board linked from a searched, off-domain page inherits no
                # trust from it: the board itself must carry the name.
                if cand_method == "web_search" and not own_site:
                    owned, why = board_belongs_to_company(ats, cap, ats_url, name)
                    if not owned:
                        import logging
                        logging.getLogger("job_scraper").info(
                            "Rejected board %s linked from searched page for %s (%s)",
                            ats_url, name, why)
                        ats, cap, ats_url = None, None, cand
        board_key = (ats or "", (ats_url or "").split("#")[0].rstrip("/"))
        if ats and board_key in processed_boards:
            import logging
            logging.getLogger("job_scraper").debug(
                "Board %s already handled for %s, skipping duplicate", ats_url, name)
            ats = None
        if ats:
            processed_boards.add(board_key)
            src = ats
            if src not in sources:
                sources.append(src)
            if ats_url != cand:
                import logging
                logging.getLogger("job_scraper").info(
                    "ATS handoff %s -> %s (%s)", cand, ats_url, ats)
            if ats in _KNOWN_GENERIC:
                probed = parsers_ats.probe_json_board(session, ats_url, ats)
                jobs.extend(probed or parse_generic(session, ats_url))
            else:
                ats_jobs = _dispatch(session, ats, ats_url, cap)
                jobs.extend(ats_jobs)
                # A recognised board the API parser cannot read: try the
                # vendor-agnostic JSON endpoints, then fall back to HTML.
                if not ats_jobs and ats_url not in tried:
                    tried.add(ats_url)
                    probed = parsers_ats.probe_json_board(session, ats_url, ats)
                    jobs.extend(probed or parse_generic(session, ats_url))
            # Point the audit trail at the board where the jobs actually are.
            if ats_url != cand and jobs:
                discovery.update({
                    "career_page_url": ats_url,
                    "career_page_status": "Validated",
                    "career_page_discovery_method": "%s_via_%s" % (
                        ats, candidate_methods.get(cand, "career_page_link")),
                })
        elif cand_html:
            # generic: parse the candidate page directly
            jobs.extend(parse_generic(session, cand))

        # Whether or not an ATS was recognised, mine the page itself for job
        # links, follow its pagination to the end, and pick up inline
        # postings.  Previously this only ran as a last-ditch fallback when
        # nothing at all had been found, so paginated career pages were
        # truncated at page one.
        if cand_html:
            jobs.extend(_extract_and_process_job_links(
                session, cand, cand_html, homepage_url, deadline=deadline))
            jobs.extend(_extract_and_process_paginated_content(
                session, cand, cand_html, homepage_url, deadline=deadline))
            jobs.extend(_enhance_inline_job_detection(cand_html, cand))

            # Treat the career page as a hub and walk its same-domain career
            # branches one level deep.
            for sub in _career_subpages(cand_html, cand, homepage_url,
                                        MAX_CAREER_SUBPAGES):
                if out_of_time("career-hub traversal"):
                    break
                if sub in tried:
                    continue
                tried.add(sub)
                sub_html = session.fetch_text(sub, timeout=CAREER_PAGE_TIMEOUT)
                if not sub_html:
                    continue
                sub_ats, sub_cap, sub_url = _candidate_ats(session, sub, sub_html)
                if sub_ats and sub_url:
                    if sub_ats not in sources:
                        sources.append(sub_ats)
                    if sub_ats in _KNOWN_GENERIC:
                        jobs.extend(parse_generic(session, sub_url))
                    else:
                        found = _dispatch(session, sub_ats, sub_url, sub_cap)
                        jobs.extend(found or parse_generic(session, sub_url))
                else:
                    jobs.extend(parse_generic(session, sub))
                jobs.extend(_extract_and_process_paginated_content(
                    session, sub, sub_html, homepage_url, deadline=deadline))

    # 4. dedupe jobs by url/title
    uniq = {}
    for j in jobs:
        key = (j.get("job_url") or "").strip() or (j.get("job_title") or "").strip()
        if not key:
            continue
        if key in uniq:
            continue
        uniq[key] = j
    # Ceiling is a safety valve (config.MAX_JOBS_PER_COMPANY, 10000), not a
    # sample size.  The old hard-coded [:200] silently dropped postings.
    jobs = list(uniq.values())[:MAX_JOBS_PER_COMPANY]

    if not jobs:
        # final fallback: JSON-LD on homepage
        if homepage_html:
            from .parsers_jsonld import parse_jsonld_jobs
            jobs = parse_jsonld_jobs(homepage_html, homepage_url)
        if not jobs:
            # Enhanced fallback: try to extract jobs from career page links
            if homepage_html and discovery.get("career_page_url") and \
                    discovery["career_page_url"] not in tried:
                career_page_html = session.fetch_text(
                    discovery["career_page_url"], timeout=CAREER_PAGE_TIMEOUT)
                if career_page_html:
                    target = discovery["career_page_url"]
                    jobs.extend(_extract_and_process_job_links(
                        session, target, career_page_html, homepage_url,
                        deadline=deadline))
                    jobs.extend(_extract_and_process_paginated_content(
                        session, target, career_page_html, homepage_url,
                        deadline=deadline))
                    jobs.extend(_enhance_inline_job_detection(career_page_html, target))
                    if jobs:
                        uniq = {}
                        for j in jobs:
                            key = (j.get("job_url") or "").strip() or (j.get("job_title") or "").strip()
                            if key and key not in uniq:
                                uniq[key] = j
                        jobs = list(uniq.values())[:MAX_JOBS_PER_COMPANY]
        if not jobs and discovery.get("career_page_url"):
            # Unrecognised vendor: detect the board by behaviour, not by name.
            career_html = session.fetch_text(discovery["career_page_url"],
                                             timeout=CAREER_PAGE_TIMEOUT)
            unknown = _unknown_board_handoff(
                session, discovery["career_page_url"], career_html, name)
            if unknown:
                jobs.extend(unknown)
                if "structural-board-detection" not in sources:
                    sources.append("structural-board-detection")
        if not jobs:
            jobs = internet_job_fallback(homepage_url)
        if not jobs:
            jobs = ats_board_fallback()
        if not jobs:
            # A regional page saying "no jobs" is not evidence that the whole
            # company has no openings. Only apply it to the selected official
            # page, and never override a recognized but unsupported ATS board.
            if (discovery["career_page_url"] in explicit_no_jobs_pages and not sources):
                return result("no_jobs", [], ";".join(sources))
            if discovery["career_page_url"]:
                return result("unsupported", [], ";".join(sources))
            return result("career_not_found", [], ";".join(sources))
        if discovery.get("career_page_discovery_method") == "internet_company_name_search":
            return result("ok", jobs, "internet-company-name-search")
        if "structural-board-detection" in sources:
            return result("ok", jobs, ";".join(sources))
        if not discovery["career_page_url"]:
            discovery.update({
                "career_page_url": homepage_url,
                "career_page_status": "Validated",
                "career_page_discovery_method": "homepage_jobposting",
            })
        return result("ok", jobs, "jsonld-homepage")

    return result("ok", jobs, ";".join(sources) or "generic")


def _extract_and_process_job_links(session, career_page_url, html, base_url,
                                   limit=MAX_JOBS_PER_COMPANY, seen_urls=None,
                                   deadline=None):
    """Follow every job-detail link on a career page.

    The old version accepted a `limit` of 5 from its single call site.  It now
    processes all discovered links, bounded only by the config ceiling.
    """
    if not html:
        return []

    from .parsers_generic import _extract_job_links, parse_generic

    job_links = _extract_job_links(html, career_page_url)
    if not job_links:
        return []

    visited = seen_urls if seen_urls is not None else set()
    jobs = []
    for job_url in job_links:
        if len(jobs) >= limit:
            break
        if deadline is not None and time.time() > deadline:
            break
        if not job_url or job_url.rstrip("/") == career_page_url.rstrip("/"):
            continue
        normalized = job_url.split("#")[0].rstrip("/")
        if normalized in visited:
            continue
        visited.add(normalized)

        job_html = session.fetch_text(job_url, timeout=JOB_DETAIL_TIMEOUT)
        if not job_html:
            continue

        detail_jobs = parse_generic(session, job_url)
        for job in detail_jobs or []:
            if not job.get("source"):
                job["source"] = "career_page_job_link"
            jobs.append(job)

    return jobs


def _extract_and_process_paginated_content(session, career_page_url, html, base_url,
                                          limit_pages=MAX_PAGINATION_PAGES,
                                          seen_urls=None, deadline=None):
    """Walk a career page's pagination to the end.

    Previously this followed only the "next" links present on the first page
    (and its call site capped that at three).  It now traverses the chain
    breadth-first until no unvisited next-page link remains, so page 2 -> 3 ->
    4 ... are all harvested.
    """
    if not html:
        return []

    from .parsers_generic import parse_generic, _extract_pagination_links

    visited = seen_urls if seen_urls is not None else set()
    visited.add(career_page_url.split("#")[0].rstrip("/"))

    queue = []
    for link in _extract_pagination_links(html, career_page_url):
        normalized = (link or "").split("#")[0].rstrip("/")
        if normalized and normalized not in visited:
            queue.append(link)

    jobs = []
    pages_done = 0
    while queue and pages_done < limit_pages:
        if deadline is not None and time.time() > deadline:
            break
        page_url = queue.pop(0)
        normalized = page_url.split("#")[0].rstrip("/")
        if normalized in visited:
            continue
        visited.add(normalized)

        page_html = session.fetch_text(page_url, timeout=CAREER_PAGE_TIMEOUT)
        if not page_html:
            continue
        pages_done += 1

        page_jobs = parse_generic(session, page_url)
        for job in page_jobs or []:
            if not job.get("source"):
                job["source"] = "career_page_pagination"
            jobs.append(job)

        # Keep following the chain from this page.
        for link in _extract_pagination_links(page_html, page_url):
            candidate = (link or "").split("#")[0].rstrip("/")
            if candidate and candidate not in visited:
                queue.append(link)

    if pages_done:
        import logging
        logging.getLogger("job_scraper").debug(
            "Pagination traversal %s pages=%d jobs=%d", career_page_url, pages_done, len(jobs))
    return jobs


_GENERIC_HEADING_RE = re.compile(
    r"^(?:recently\s+posted\s+jobs?|open\s+(?:positions?|roles?|jobs?)|job\s+openings?|"
    r"current\s+(?:vacancies|openings?|opportunities)|latest\s+jobs?|all\s+jobs?|"
    r"search\s+results?|vacancies|opportunities|careers?|join\s+us|work\s+with\s+us|"
    r"our\s+jobs?|available\s+positions?|browse\s+jobs?|jobs?|positions?|"
    r"stellenangebote|offene\s+stellen|karriere)$",
    re.IGNORECASE,
)
_ROLE_WORD_RE = re.compile(
    r"\b(?:manager|director|engineer|developer|analyst|specialist|coordinator|"
    r"assistant|administrator|technician|nurse|physician|therapist|officer|"
    r"consultant|advisor|associate|supervisor|lead|head|clerk|accountant|"
    r"designer|architect|scientist|researcher|teacher|lecturer|professor|"
    r"driver|operator|representative|agent|executive|intern|apprentice|"
    r"receptionist|secretary|cashier|chef|cook|cleaner|guard|paramedic|"
    r"pharmacist|dentist|surveyor|electrician|plumber|carpenter|welder)\b"
    # German/compound role words appear inside longer words
    # ("Pflegefachkraft", "Sachbearbeiter"), so these are not \b-anchored.
    r"|(?:mitarbeiter|fachkraft|fachangestellt|ingenieur|pflege|erzieher|"
    r"sachbearbeit|assistenz|leitung|berater|verkäufer|techniker|"
    r"kaufmann|kauffrau|helfer|arzt|ärztin)",
    re.IGNORECASE,
)
# "(m/w/d)", "(m/f/x)" -- a German/European job-title convention, and on its
# own strong evidence that a string is a posting title.
_GENDER_MARKER_RE = re.compile(r"\(\s*(?:m|w|d|f|x)(?:\s*[/|,\-]\s*(?:m|w|d|f|x)){1,}\s*\)",
                               re.IGNORECASE)
_MAX_INLINE_HEURISTIC_JOBS = 25


def _looks_like_job_title(title):
    """Guard for the loosest extraction path.

    "Recently Posted Jobs" was accepted as a job title on jobs.virginia.gov,
    producing one row whose description was the whole page -- 4,000 characters
    of unrelated state-government listings.  A heading is not a posting.
    """
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 120:
        return False
    if _GENERIC_HEADING_RE.match(title):
        return False
    if title.endswith("?") or title.count(".") > 2:
        return False
    words = title.split()
    if len(words) > 14:
        return False
    if _GENDER_MARKER_RE.search(title) or _ROLE_WORD_RE.search(title):
        return True
    # Otherwise require a title-cased multi-word phrase, as job titles are.
    # Count only word-like tokens, so "(m/w/d)" or "-" cannot drag the ratio down.
    wordish = [w for w in words if any(ch.isalpha() for ch in w)]
    capitalised = sum(1 for w in wordish if w[:1].isupper())
    return len(wordish) >= 2 and capitalised >= max(2, len(wordish) - 1)


def _enhance_inline_job_detection(html, url):
    """Last-resort extraction from job-ish containers on a page.

    Deliberately conservative: this runs when nothing structured was found, so
    a false positive here becomes a bogus row with no way to tell.
    """
    if not html:
        return []

    from .parsers_generic import _parse_inline_jobs, BeautifulSoup, clean_text, empty_job
    import re as _re

    jobs = _parse_inline_jobs(html, url)
    if jobs:
        return jobs

    soup = BeautifulSoup(html or "", "html.parser")
    enhanced_patterns = [
        r"\b(?:vacancy|position|role|job)\b.*?(?:apply|deadline|location|department)",
        r"\b(?:we are hiring|join our team|career opportunity)\b",
    ]
    containers = soup.find_all(['div', 'li', 'tr', 'section', 'article'],
                               class_=_re.compile(r'(job|position|vacancy|career|opening)',
                                                  _re.I))
    for container in containers:
        if len(jobs) >= _MAX_INLINE_HEURISTIC_JOBS:
            break
        container_text = clean_text(container.get_text(" ", strip=True), max_len=5000)
        # A container holding the entire page is a layout wrapper, not a posting.
        if not (50 < len(container_text) < 3000):
            continue
        if not any(_re.search(pattern, container_text, _re.I)
                   for pattern in enhanced_patterns):
            continue
        if not _re.search(r"\b(?:apply|application|cv|resume|deadline)\b",
                          container_text, _re.I):
            continue

        title_elem = container.find(['h1', 'h2', 'h3', 'h4', 'h5', 'strong', 'b'])
        title = clean_text(title_elem.get_text(" ", strip=True) if title_elem else "",
                           max_len=200)
        if not _looks_like_job_title(title):
            continue

        job = empty_job()
        job["job_title"] = title
        job["job_description"] = str(container)
        job["job_url"] = url
        job["job_status"] = "Active"
        job["source"] = "enhanced_inline_detection"
        job["extraction_confidence"] = "Low"
        jobs.append(job)

    return jobs


def process_company(company_row, session=None, enable_search=True):
    """Backward-compatible three-value company processing API."""
    details = process_company_details(company_row, session=session, enable_search=enable_search)
    return details["status"], details["jobs"], details["source"]


def count_positions(details):
    """Total positions scraped for a company (see OUTPUT_COLUMNS.num_positions)."""
    if not isinstance(details, dict):
        return 0
    if "num_positions" in details:
        return int(details["num_positions"] or 0)
    return len(details.get("jobs") or [])
