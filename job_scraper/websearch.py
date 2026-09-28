import logging
import re
import threading
import time
from urllib.parse import quote_plus, parse_qs, urlparse, unquote

from bs4 import BeautifulSoup

from . import netcache
from .ats import (registrable_domain, detect_ats_in_url, is_career_link,
                   is_probable_board_url)
from .config import (
    ATS_BOARD_QUERY_GROUPS,
    MAX_SEARCH_CAREER_CANDIDATES,
    MAX_SEARCH_QUERIES_PER_COMPANY,
    MAX_SEARCH_JOB_CANDIDATES,
    SEARCH_CACHE_TTL,
    SEARCH_ENGINE_COOLDOWN,
    SEARCH_ENGINE_MAX_FAILURES,
    SEARCH_MAX_ATTEMPTS,
    SEARCH_MAX_RESULTS,
    WEB_SEARCH_TIMEOUT as SEARCH_TIMEOUT,
)
from .urlutils import ensure_https, hostname

log = logging.getLogger("job_scraper")

_SOCIAL_HOSTS = re.compile(
    r"(linkedin\.com|facebook\.com|twitter\.com|instagram\.com|youtube\.com|"
    r"wikipedia\.org|glassdoor\.com|indeed\.com|pinterest\.com|xing\.com|"
    r"crunchbase\.com|zoominfo\.com|trustpilot\.com|yelp\.com|linkedin\.co|"
    r"yellowpages\.com|find-and-update\.company|dun\.com|glassdoor\.co|"
    r"companyhouse|facebook\.co|reuters\.com|britannica\.com)", re.IGNORECASE,
)

_SKIP_SUBDOMAIN = (
    "blog", "news", "m", "shop", "store", "forum", "mail", "wiki",
    "support", "help", "docs", "login", "app", "secure", "status",
)

_STOPWORDS = {
    "the", "and", "ltd", "llc", "gmbh", "inc", "corp", "co", "sa", "oy",
    "ab", "bv", "nv", "plc", "limited", "company", "group", "holding",
    "srl", "ag", "kg", "sas", "spa", "pty", "pvt", "private", "sdn", "bhd",
    "ooo", "zao", "doo", "sro", "kft", "sa", "corp", "corporation", "gmbh",
}

_MIN_SCORE = 20


def _tokens(name):
    name = re.sub(r"[^a-z0-9]+", " ", (name or "").lower())
    return [t for t in name.split() if t and t not in _STOPWORDS and len(t) > 1]


def _root_url(dom):
    return "https://www." + dom


def _score(dom, tokens):
    dom = (dom or "").lower()
    if not dom or dom.count(".") < 1:
        return -1000
    if _SOCIAL_HOSTS.search(dom):
        return -1000
    s = 0
    if not tokens:
        return 0
    for t in tokens:
        if t in dom:
            s += 20
        if len(t) > 3 and (dom.startswith(t) or dom.endswith(t)):
            s += 10
    return s


# ---------------------------------------------------------------------------
# Search engine chain.
#
# The smoke test failed on repeated ConnectTimeoutError to
# html.duckduckgo.com: one flaky engine took the whole discovery path down
# with it.  Engines are now tried in order, each with its own retry budget and
# its own circuit breaker, and every query result is cached so the same query
# is never paid for twice.
# ---------------------------------------------------------------------------

_QUERY_CACHE = {}
_ENGINE_STATE = {}
_SEARCH_LOCK = threading.Lock()

# Per-company query budget, thread-local because one worker handles one
# company at a time.  A cache hit is free and does not consume budget.
_BUDGET = threading.local()


def set_query_budget(count=None):
    """Start a fresh query budget for the company about to be processed."""
    _BUDGET.remaining = (MAX_SEARCH_QUERIES_PER_COMPANY if count is None else count)
    _BUDGET.spent = 0


def raise_query_budget(minimum):
    """Lift this company's remaining budget to at least `minimum`.

    Used for companies with no usable website: finding them at all is a
    search-only job, so they are allowed more queries than a company whose own
    site answered.
    """
    remaining = getattr(_BUDGET, "remaining", None)
    if remaining is None or remaining < minimum:
        _BUDGET.remaining = minimum


def query_budget_left():
    return getattr(_BUDGET, "remaining", MAX_SEARCH_QUERIES_PER_COMPANY)


def _consume_budget():
    remaining = getattr(_BUDGET, "remaining", None)
    if remaining is None:
        return True
    if remaining <= 0:
        return False
    _BUDGET.remaining = remaining - 1
    _BUDGET.spent = getattr(_BUDGET, "spent", 0) + 1
    return True


def _engine_available(name):
    with _SEARCH_LOCK:
        state = _ENGINE_STATE.get(name)
        if not state:
            return True
        failures, benched_until = state
        if benched_until and time.time() < benched_until:
            return False
        if benched_until and time.time() >= benched_until:
            _ENGINE_STATE[name] = (0, 0)
        return True


def _engine_failed(name):
    with _SEARCH_LOCK:
        failures, _ = _ENGINE_STATE.get(name, (0, 0))
        failures += 1
        if failures >= SEARCH_ENGINE_MAX_FAILURES:
            _ENGINE_STATE[name] = (failures, time.time() + SEARCH_ENGINE_COOLDOWN)
            log.warning("Search engine %s benched for %ds after %d consecutive failures",
                        name, SEARCH_ENGINE_COOLDOWN, failures)
        else:
            _ENGINE_STATE[name] = (failures, 0)


def _engine_ok(name):
    with _SEARCH_LOCK:
        _ENGINE_STATE[name] = (0, 0)


def engine_status():
    with _SEARCH_LOCK:
        return {name: {"consecutive_failures": f, "benched_until": b}
                for name, (f, b) in _ENGINE_STATE.items()}


def _unwrap_redirect(href):
    """Unwrap DuckDuckGo / Startpage redirector links."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        query = parse_qs(urlparse(href).query)
    except Exception:
        return href
    for key in ("uddg", "u", "url", "q"):
        if key in query and query[key]:
            candidate = unquote(query[key][0])
            if candidate.startswith("http"):
                return candidate
    return href


def _anchor_links(html, selectors):
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for selector in selectors:
        for a in soup.select(selector):
            href = _unwrap_redirect(a.get("href", ""))
            if href.startswith("http"):
                out.append(href)
        if out:
            break
    return out


def _search_bing_rss(session, query):
    url = "https://www.bing.com/search?q=" + quote_plus(query) + "&format=rss"
    r = session.fetch(url, timeout=SEARCH_TIMEOUT, retries=1)
    if r is None or r.status_code >= 400:
        return None
    txt = r.text
    if "<item>" not in txt:
        return []
    out = []
    for m in re.finditer(r"<item>.*?<link>(.*?)</link>.*?</item>", txt, re.S | re.I):
        link = m.group(1).strip()
        if link.startswith("http"):
            out.append(link)
    return out


def _search_duckduckgo(session, query):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    r = session.fetch(url, timeout=SEARCH_TIMEOUT, retries=1)
    if r is None or r.status_code >= 400:
        return None
    html = r.text
    if not html or "anomaly" in html.lower():
        return None
    return _anchor_links(html, ["a.result__a", "a.result__url", "h2 a"])


def _search_duckduckgo_lite(session, query):
    url = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
    r = session.fetch(url, timeout=SEARCH_TIMEOUT, retries=1)
    if r is None or r.status_code >= 400:
        return None
    html = r.text
    if not html or "anomaly" in html.lower():
        return None
    return _anchor_links(html, ["a.result-link", "td a[href^='http']", "a[href^='http']"])


def _search_mojeek(session, query):
    url = "https://www.mojeek.com/search?q=" + quote_plus(query)
    r = session.fetch(url, timeout=SEARCH_TIMEOUT, retries=1)
    if r is None or r.status_code >= 400:
        return None
    return _anchor_links(r.text, ["a.ob", "ul.results-standard li h2 a", "h2 a"])


def _search_startpage(session, query):
    url = "https://www.startpage.com/sp/search?query=" + quote_plus(query)
    r = session.fetch(url, timeout=SEARCH_TIMEOUT, retries=1)
    if r is None or r.status_code >= 400:
        return None
    return _anchor_links(r.text, ["a.w-gl__result-url", "a.result-link", "h3 a"])


def _search_search_brave(session, query):
    url = "https://search.brave.com/search?q=" + quote_plus(query)
    r = session.fetch(url, timeout=SEARCH_TIMEOUT, retries=1)
    if r is None or r.status_code >= 400:
        return None
    return _anchor_links(r.text, ["a.result-header", "div.snippet a[href^='http']", "h2 a"])


def _search_googlesearch(session, query):
    """Optional last resort via the `googlesearch-python` package."""
    try:
        from googlesearch import search as _google_search  # type: ignore
    except Exception:
        return None
    try:
        return [u for u in _google_search(query, num_results=SEARCH_MAX_RESULTS)
                if isinstance(u, str) and u.startswith("http")]
    except Exception:
        return None


# Order matters: cheapest and most reliable first.
_ENGINES = (
    ("bing_rss", _search_bing_rss),
    ("duckduckgo_html", _search_duckduckgo),
    ("duckduckgo_lite", _search_duckduckgo_lite),
    ("mojeek", _search_mojeek),
    ("startpage", _search_startpage),
    ("brave", _search_search_brave),
    ("googlesearch", _search_googlesearch),
)


def web_search(session, query, min_results=1):
    """Run `query` against the engine chain until results appear.

    Returns a list of URLs (possibly empty).  ``None`` from an engine means
    "engine failed"; ``[]`` means "engine worked, found nothing" -- only the
    former counts against the circuit breaker.
    """
    if not query:
        return []
    key = query.strip().lower()
    now = time.time()
    with _SEARCH_LOCK:
        cached = _QUERY_CACHE.get(key)
        if cached and cached[0] > now:
            return list(cached[1])

    if not _consume_budget():
        log.debug("query budget exhausted, skipping %r", query[:80])
        return []

    collected = []
    seen = set()
    attempted = []
    for name, engine in _ENGINES:
        if len(collected) >= min_results:
            break
        if not _engine_available(name):
            continue
        results = None
        for attempt in range(max(1, SEARCH_MAX_ATTEMPTS)):
            try:
                results = engine(session, query)
            except Exception as exc:
                log.debug("Search engine %s raised for %r: %s", name, query, str(exc)[:120])
                results = None
            if results is not None:
                break
            if attempt < SEARCH_MAX_ATTEMPTS - 1:
                netcache.backoff_sleep(attempt, base=0.8, cap=8.0)
        if results is None:
            _engine_failed(name)
            attempted.append("%s=ERR" % name)
            continue
        _engine_ok(name)
        attempted.append("%s=%d" % (name, len(results)))
        for url in results:
            normalized = url.split("#")[0]
            if normalized not in seen:
                seen.add(normalized)
                collected.append(normalized)

    collected = collected[:SEARCH_MAX_RESULTS]
    log.info("web_search %r -> %d urls [%s]", query[:90], len(collected),
             " ".join(attempted) or "no engine available")
    with _SEARCH_LOCK:
        if len(_QUERY_CACHE) > 20000:
            _QUERY_CACHE.clear()
        _QUERY_CACHE[key] = (time.time() + SEARCH_CACHE_TTL, list(collected))
    return collected


# Generic place / institution words that must never carry a domain match on
# their own.  "VIRGINIA COLLEGE-RICHMOND" matching jobs.virginia.gov is the
# failure this prevents: one shared geographic token is not evidence of
# ownership.
_WEAK_TOKENS = {
    "virginia", "carolina", "georgia", "california", "texas", "florida",
    "washington", "london", "paris", "berlin", "madrid", "dublin", "sydney",
    "toronto", "america", "american", "national", "international", "federal",
    "state", "states", "united", "central", "north", "south", "east", "west",
    "northern", "southern", "eastern", "western", "city", "county", "district",
    "regional", "region", "province", "municipal", "college", "university",
    "school", "schools", "institute", "hospital", "medical", "clinic",
    "center", "centre", "services", "service", "solutions", "systems",
    "technologies", "technology", "industries", "industrial", "global",
    "digital", "consulting", "partners", "associates", "enterprises",
    "trading", "commerce", "comercio", "industria", "bank", "banco", "energy",
    "health", "care", "foundation", "trust", "council", "agency", "authority",
    "department", "ministry", "government", "public", "general",
    # Well-known place names.  A company sharing a city name with a domain is
    # not evidence of ownership.
    "vancouver", "montreal", "calgary", "ottawa", "quebec", "chicago",
    "boston", "austin", "denver", "seattle", "portland", "phoenix", "atlanta",
    "dallas", "houston", "miami", "detroit", "richmond", "phoenix", "orlando",
    "mumbai", "delhi", "bangalore", "bengaluru", "chennai", "kolkata", "pune",
    "hyderabad", "ahmedabad", "singapore", "dubai", "tokyo", "osaka",
    "shanghai", "beijing", "seoul", "jakarta", "manila", "bangkok",
    "munich", "muenchen", "hamburg", "frankfurt", "cologne", "koeln",
    "stuttgart", "vienna", "wien", "zurich", "geneva", "milan", "milano",
    "rome", "roma", "naples", "barcelona", "lisbon", "lisboa", "amsterdam",
    "rotterdam", "brussels", "antwerp", "copenhagen", "stockholm", "oslo",
    "helsinki", "warsaw", "krakow", "prague", "praha", "budapest",
    "bucharest", "bucuresti", "sofia", "athens", "istanbul", "ankara",
    "cairo", "lagos", "nairobi", "johannesburg", "pretoria", "durban",
    "limpopo", "gauteng", "sydney", "melbourne", "brisbane", "perth",
    "adelaide", "auckland", "wellington", "toronto", "brandenburg",
    "bavaria", "bayern", "saxony", "ontario", "alberta", "yorkshire",
}


def _domain_matches_company(dom, tokens):
    """Does this registrable domain plausibly belong to this company?

    Needed because 575 of the 999 smoke-test rows have no WEBSITE value at
    all.  With no official domain to compare against, the old filter could
    only accept recognised ATS boards or job-board detail pages, so nearly
    every genuine result was discarded (candidates=0..3, verified_jobs=0).

    Precision rules, so the fallback does not invent matches:

    * social networks and job aggregators never match;
    * a single weak token (a place, "college", "services", ...) is not enough;
    * otherwise require either two distinct tokens present in the domain, or
      one strong token of >= 5 characters that the domain starts with.
    """
    dom = (dom or "").lower()
    if not dom or _SOCIAL_HOSTS.search(dom):
        return False
    if not tokens:
        return False

    label = dom.split(".")[0]
    if label.startswith("www"):
        label = label[3:].lstrip("-.")

    present = [t for t in tokens if t in dom]
    if not present:
        return False
    strong = [t for t in present if t not in _WEAK_TOKENS]

    # How much of the domain's own label the matched tokens actually account
    # for.  "virginiacollege" is fully explained by (virginia, college);
    # "virginia" alone explains all of "virginia.gov" too, which is why
    # coverage on its own is not sufficient -- see the single-token rule.
    coverage = 0.0
    if label:
        coverage = min(1.0, sum(len(t) for t in present) / float(len(label)))

    # Two distinct tokens both present in the domain is strong evidence even
    # when each token is individually generic.
    if len(present) >= 2 and coverage >= 0.6:
        return True

    # A lone token must be distinctive (not a place or a generic industry
    # word), long enough to be meaningful, and essentially *be* the domain.
    for token in strong:
        if len(token) >= 5 and coverage >= 0.6 and (label == token or label.startswith(token)):
            return True
    return False


def search_company_website(name, session, country=None, limit=5):
    """Search for the official website of a company by name.

    Returns a list of candidate website roots (best first), or [].
    """
    tokens = _tokens(name)
    queries = ['"%s" official website' % name]
    if country:
        queries.append('"%s" official website %s' % (name, country))
    if not tokens:
        return []

    results = []
    for q in queries:
        results.extend(web_search(session, q))
        if results:
            break
    if not results:
        # Last resort: a plain name query, which some engines answer when the
        # quoted "official website" form returns nothing.
        results.extend(web_search(session, "%s %s" % (name, country or "")))
    if not results:
        return []

    scored = {}
    for r in results:
        try:
            dom = registrable_domain(hostname(ensure_https(r)))
        except Exception:
            continue
        if not dom:
            continue
        score = _score(dom, tokens)
        if score >= _MIN_SCORE and dom not in scored:
            scored[dom] = score

    ranked = sorted(scored.items(), key=lambda x: -x[1])
    return [_root_url(dom) for dom, _ in ranked[:limit]]


def search_company_career_pages(name, official_url, session, country=None,
                                limit=MAX_SEARCH_CAREER_CANDIDATES):
    """Find likely official career pages while rejecting unrelated job sites."""
    official_domain = registrable_domain(hostname(ensure_https(official_url)))
    query_name = '"%s" careers jobs' % name
    queries = [query_name]
    if official_domain:
        queries.insert(0, "site:%s careers jobs" % official_domain)
    if country:
        queries.append(query_name + " " + country)

    from .ownership import slug_matches_company, domain_matches_company
    accepted = []
    seen = set()
    # Lazily: stop as soon as enough candidates are accepted, so a company
    # whose own domain answers the first query costs exactly one query.
    for query in queries:
        for url in web_search(session, query):
            url = ensure_https(url).split("#")[0]
            if not url or url in seen:
                continue
            result_domain = registrable_domain(hostname(url))
            ats, captured = detect_ats_in_url(url)
            same_domain = bool(official_domain) and result_domain == official_domain
            # The board slug must carry a DISTINCTIVE word of the name; "state"
            # or "national" inside a slug is not a match.
            ats_matches_company = bool(ats and captured and slug_matches_company(captured, name))
            name_matched_domain = (not official_domain
                                   and domain_matches_company(result_domain, name)
                                   and is_career_link("", url))
            if not ((same_domain and is_career_link("", url)) or ats_matches_company
                    or name_matched_domain):
                continue
            seen.add(url)
            accepted.append(url)
            if len(accepted) >= limit:
                return accepted
        if accepted:
            break
    return accepted


def search_company_job_pages(name, official_url, session, country=None,
                             limit=MAX_SEARCH_JOB_CANDIDATES):
    """Search by company name for likely career boards or individual jobs.

    Results are candidates only. The caller must fetch the page and verify the
    company identity before exporting a job.
    """
    official_domain = registrable_domain(hostname(ensure_https(official_url)))
    queries = ['"%s" jobs careers' % name, '%s jobs careers' % name]
    if country:
        queries[0] += " " + country
    if official_domain:
        queries.insert(0, "site:%s (jobs OR careers OR vacancies OR stellenangebote)"
                          % official_domain)

    tokens = _tokens(name)
    accepted = []
    seen = set()
    for query in queries:
        for raw_url in web_search(session, query):
            url = ensure_https(raw_url).split("#")[0]
            if not url or url in seen:
                continue
            result_domain = registrable_domain(hostname(url))
            ats, captured = detect_ats_in_url(url)
            same_domain_job = bool(official_domain and result_domain == official_domain
                                   and is_career_link("", url))
            from .ownership import slug_matches_company, domain_matches_company
            ats_company_match = bool(ats and captured and slug_matches_company(captured, name))
            name_matched_domain = bool(
                not official_domain and domain_matches_company(result_domain, name)
                and is_career_link("", url))
            aggregator = bool(re.search(
                r"(?:linkedin|indeed|glassdoor|stepstone|monster|ziprecruiter|xing)\.",
                hostname(url), re.I))
            job_detail_path = bool(re.search(
                r"/jobs?/view/\d+|/viewjob\b|/job-listing/|/jobs/\d+|"
                r"/stellenangebote?/[^/?#]+-\d+|[?&](?:jk|jobId|currentJobId)=",
                url, re.I))
            search_listing = bool(re.search(
                r"SRCH|/srch|[?&](?:q|keyword|kw|query)=|/jobs-in-|-jobs-|/job-search|"
                r"/browse|/companies?/|/salaries|/reviews", url, re.I))
            job_board_detail = bool(aggregator and job_detail_path and not search_listing)
            if not (same_domain_job or ats_company_match or job_board_detail
                    or name_matched_domain):
                continue
            seen.add(url)
            accepted.append(url)
            if len(accepted) >= limit:
                return accepted
        # Only escalate to the next, broader query when nothing was accepted.
        if accepted:
            break
    return accepted


# ---------------------------------------------------------------------------
# ATS boards by company name.
#
# For a company whose website is missing, dead or wrong, its hosted job board
# is often still indexed under its name.  Two grouped site: queries cover the
# vendors that host the overwhelming majority of boards without spending a
# query per vendor.
# ---------------------------------------------------------------------------

_ATS_SITE_GROUPS = (
    # 0 -- the vendors search engines index best, worldwide
    ("myworkdayjobs.com", "boards.greenhouse.io", "job-boards.greenhouse.io",
     "jobs.lever.co", "jobs.ashbyhq.com", "jobs.smartrecruiters.com"),
    # 1 -- mid-market, mostly EU/US
    ("apply.workable.com", "teamtailor.com", "recruitee.com", "breezy.hr",
     "personio.de", "icims.com", "taleo.net", "csod.com"),
    # 2 -- US enterprise suites
    ("myworkdaysite.com", "successfactors.com", "oraclecloud.com",
     "jobvite.com", "bamboohr.com", "applytojob.com", "paylocity.com",
     "phenompeople.com"),
    # 3 -- Brazil / LatAm (Brazil is the second-largest country in the input)
    ("gupy.io", "solides.com", "kenoby.com", "quickin.io", "abler.com.br",
     "inhire.io"),
    # 4 -- DACH and India
    ("softgarden.io", "dvinci-hr.com", "rexx-systems.com", "interamt.de",
     "keka.com", "darwinbox.in", "zohorecruit.com", "greythr.com"),
)

# A company's own country says which vendors are worth asking about first.  With
# a limited query budget, asking Gupy about a Brazilian company before asking
# Workday is the difference between finding a board and not.
_COUNTRY_PRIORITY_GROUPS = {
    "brazil": (3,), "portugal": (3,), "argentina": (3,), "chile": (3,),
    "mexico": (3,), "colombia": (3,),
    "germany": (4,), "austria": (4,), "switzerland": (4,),
    "india": (4,),
}


def _group_order(country):
    """Group indices to try, most promising for this country first."""
    priority = _COUNTRY_PRIORITY_GROUPS.get((country or "").strip().lower(), ())
    order = list(priority)
    order.extend(i for i in range(len(_ATS_SITE_GROUPS)) if i not in priority)
    return order


def search_ats_boards(name, session, country=None, limit=8, groups=None):
    """Candidate ATS board URLs that plausibly belong to `name`.

    Acceptance is deliberately strict -- the captured board slug, or the URL
    itself, must carry a distinctive token from the company name -- because a
    wrong board would attach another company's postings.
    """
    from .ownership import slug_matches_company, distinctive_tokens
    tokens = _tokens(name)
    # A name made only of generic words cannot be matched to a board slug
    # with any confidence ("national" -> nationalgeographic): do not search.
    if not tokens or not distinctive_tokens(name):
        return []

    accepted = []
    seen = set()
    group_count = ATS_BOARD_QUERY_GROUPS if groups is None else groups
    order = _group_order(country)[:max(1, group_count)]
    for group in (_ATS_SITE_GROUPS[i] for i in order):
        query = '"%s" (%s)' % (name, " OR ".join("site:" + host for host in group))
        for url in web_search(session, query):
            url = ensure_https(url).split("#")[0]
            if not url or url in seen:
                continue
            ats, captured = detect_ats_in_url(url)
            if not ats or not is_probable_board_url(url):
                continue
            # Only the board slug / tenant may carry the match, and only a
            # distinctive word of the name counts.
            slug = captured or ""
            if ats == "workday":
                m = re.match(r"^https?://([a-z0-9\-_]+)\.wd\d+\.myworkday(?:jobs|site)\.com", url.lower())
                slug = m.group(1) if m else slug
            if not slug_matches_company(slug, name):
                continue
            seen.add(url)
            accepted.append(url)
            if len(accepted) >= limit:
                return accepted
        if accepted:
            break
    return accepted
