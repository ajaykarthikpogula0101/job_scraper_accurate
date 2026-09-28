import re
import json
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .fields import (
    clean_text,
    join_list,
    parse_date,
    parse_decimal,
    currency_from_salary,
    to_list,
    empty_job,
    extract_labeled_fields,
)
from .config import (
    CAREER_PAGE_TIMEOUT,
    MAX_JOBS_PER_COMPANY,
    JOB_DETAIL_TIMEOUT,
    MAX_ATS_API_OFFSET,
    MAX_ATS_DETAIL_ENRICH,
    MAX_JOB_DETAIL_PAGES,
)
from .urlutils import ensure_https, url_join
from .parsers_jsonld import parse_jsonld_jobs, parse_microdata_jobs


def make_job(ats_name, title="", url="", source=""):
    j = empty_job()
    j["job_title"] = title
    j["job_url"] = url
    j["job_status"] = "Active"
    j["source"] = source or ats_name
    return j


def _strip_html(text):
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return clean_text(soup.get_text(" ", strip=True))


def _meta_to_dict(metadata_list):
    d = {}
    if not metadata_list:
        return d
    for m in metadata_list:
        if isinstance(m, dict):
            key = clean_text(m.get("name") or m.get("id"))
            val = m.get("value")
            if isinstance(val, (dict, list)):
                val = clean_text(val.get("name") or val) if isinstance(val, dict) else clean_text(val)
            d[key.lower()] = clean_text(val)
    return d


def _lookup_employment(d, keys=("work type", "employment type", "type", "job type", "emp-type")):
    for k in keys:
        if k in d:
            return d[k]
    return ""


def _location_text(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (_location_text(item) for item in value)))
    if isinstance(value, dict):
        parts = []
        for key in ("name", "fullLocation", "location", "city", "region", "state", "country"):
            text = clean_text(value.get(key))
            if text and text not in parts:
                parts.append(text)
        return ", ".join(parts)
    return clean_text(value)


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------
def parse_greenhouse(session, info):
    board = info.get("board") or ""
    api = "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % board
    data = session.fetch_json(api, timeout=30)
    if not data or not isinstance(data, dict):
        return []
    jobs = []
    for item in data.get("jobs", []):
        if not isinstance(item, dict):
            continue
        j = make_job("greenhouse", item.get("title"), item.get("absolute_url"))
        j["posted_date"] = parse_date(item.get("updated_at") or item.get("first_published"))
        j["job_description"] = _strip_html(item.get("content"))
        loc = item.get("location") or {}
        if isinstance(loc, dict):
            j["job_location"] = clean_text(loc.get("name"))
        meta = _meta_to_dict(item.get("metadata"))
        j["employment_type"] = _lookup_employment(meta)
        for d in item.get("departments") or []:
            pass
        jobs.append(j)
    return jobs


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------
def parse_lever(session, info):
    slug = info.get("board") or info.get("slug") or ""
    api = "https://api.lever.co/v0/postings/%s?mode=json" % slug
    data = session.fetch_json(api, timeout=30)
    if not isinstance(data, list):
        return []
    jobs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        j = make_job("lever", item.get("text"), item.get("hostedUrl"))
        j["posted_date"] = parse_date(item.get("createdAt"))
        cats = item.get("categories") or {}
        j["employment_type"] = clean_text(cats.get("commitment"))
        j["job_location"] = _location_text(cats.get("location") or item.get("workplaceType") or
                                             item.get("allLocations"))
        j["job_description"] = clean_text(item.get("descriptionPlain") or item.get("description"))
        j["salary"] = clean_text(item.get("salaryRange"))
        j["source"] = "lever"
        jobs.append(j)
    return jobs
# ---------------------------------------------------------------------------
# SmartRecruiters
# ---------------------------------------------------------------------------
def _sr_label(value):
    if isinstance(value, dict):
        return clean_text(value.get("label") or value.get("name"))
    return clean_text(value)


def parse_smartrecruiters(session, info):
    slug = info.get("board") or ""
    jobs = []
    offset = 0
    while offset < MAX_ATS_API_OFFSET:
        api = "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100&offset=%d" % (slug, offset)
        data = session.fetch_json(api, timeout=30)
        if not data:
            break
        content = data.get("content") or []
        if not content:
            break
        for item in content:
            if not isinstance(item, dict):
                continue
            status = (item.get("postingStatus") or {})
            if isinstance(status, dict):
                st = clean_text(status.get("status"))
            else:
                st = clean_text(status)
            if st and st.lower() not in ("active", "published", ""):
                continue
            j = make_job("smartrecruiters", item.get("name"))
            j["posted_date"] = parse_date(item.get("releasedDate") or item.get("createdAt"))
            j["employment_type"] = _sr_label(item.get("typeOfEmployment") or item.get("employmentType"))
            j["seniority_level"] = _sr_label(item.get("experienceLevel"))
            loc = item.get("location")
            if isinstance(loc, dict):
                j["job_location"] = clean_text(loc.get("fullLocation") or loc.get("city"))
            j["job_url"] = clean_text(item.get("postingUrl")) or (
                "https://jobs.smartrecruiters.com/%s/%s" % (slug, item.get("id")))
            jobs.append(j)
        total = data.get("totalFound") or 0
        offset += len(content)
        if offset >= total:
            break
    # Enrich descriptions from the detail endpoint for every posting
    # (ceiling only, config.MAX_ATS_DETAIL_ENRICH).  Leadership titles are
    # enriched first so the ceiling never leaves a delivered row without a
    # description while spending the budget on the rest of the board.
    from .leadership import prioritize
    from concurrent.futures import ThreadPoolExecutor

    def fetch_detail(j):
        pid = (j.get("job_url") or "").split("/")[-1]
        if not pid or not str(pid).isdigit():
            return j, None
        return j, session.fetch_json(
            "https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (slug, pid),
            timeout=25,
        )

    chosen = prioritize(jobs, lambda job: job.get("job_title"))[:MAX_ATS_DETAIL_ENRICH]
    with ThreadPoolExecutor(max_workers=8) as pool:
        details = list(pool.map(fetch_detail, chosen))
    for j, detail in details:
        if not detail:
            continue
        ja = detail.get("jobAd")
        if isinstance(ja, dict):
            sections = ja.get("sections") or {}
            if isinstance(sections, dict):
                sections = list(sections.values())
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                name = clean_text(sec.get("sectionName") or sec.get("title"))
                body = clean_text(sec.get("description") or sec.get("text"))
                if name.lower() in (
                    "jobdescription", "job description", "description", "the role",
                    "about the job", "your role", "what we offer", "responsibilities",
                    "requirements", "qualifications", "skills", "your profile", "about you",
                ):
                    if body and not j["job_description"]:
                        j["job_description"] = body
                elif "skills" in name.lower() and body and not j["skills"]:
                    j["skills"] = body
        if not j["job_url"] and detail.get("postingUrl"):
            j["job_url"] = clean_text(detail.get("postingUrl"))
    return jobs


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------
def parse_workable(session, info):
    slug = info.get("board") or ""
    api = "https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % slug
    data = session.fetch_json(api, timeout=30)
    if not data:
        return []
    jobs = []
    for item in data.get("jobs", []):
        if not isinstance(item, dict):
            continue
        j = make_job("workable", item.get("title"), item.get("url") or item.get("shortlink"))
        j["posted_date"] = parse_date(item.get("published_on") or item.get("created_at") or item.get("created"))
        j["employment_type"] = clean_text(item.get("employment_type"))
        j["job_location"] = _location_text(item.get("location") or item.get("locations") or
                                             item.get("office"))
        j["seniority_level"] = clean_text(item.get("seniority"))
        j["job_description"] = _strip_html(item.get("description"))
        j["salary"] = clean_text(item.get("salary"))
        if not j["job_url"]:
            j["job_url"] = "https://apply.workable.com/%s/j/%s" % (slug, item.get("shortcode"))
        jobs.append(j)
    return jobs


# ---------------------------------------------------------------------------
# Recruitee
# ---------------------------------------------------------------------------
def parse_recruitee(session, info):
    slug = info.get("board") or ""
    api = "https://%s.recruitee.com/api/offers/" % slug
    data = session.fetch_json(api, timeout=30)
    if not data:
        return []
    jobs = []
    for item in data.get("offers", []):
        if not isinstance(item, dict):
            continue
        j = make_job("recruitee", item.get("title"))
        j["posted_date"] = parse_date(item.get("created_at") or item.get("published_at"))
        j["employment_type"] = clean_text(item.get("employment_type"))
        j["job_location"] = _location_text(item.get("location") or item.get("locations") or
                                             item.get("city") or item.get("country"))
        j["job_description"] = _strip_html(item.get("description"))
        slug_name = item.get("slug")
        j["job_url"] = "https://%s.recruitee.com/o/%s" % (slug, slug_name or item.get("id"))
        jobs.append(j)
    return jobs


# ---------------------------------------------------------------------------
# Breezy
# ---------------------------------------------------------------------------
def parse_breezy(session, info):
    slug = info.get("board") or ""
    jobs = []
    for cand in ("https://%s.breezy.hr/positions.json" % slug,
                 "https://%s.breezy.hr/json" % slug,
                 "https://%s.breezy.hr/api/positions" % slug):
        data = session.fetch_json(cand, timeout=30)
        if isinstance(data, dict):
            data = data.get("positions") or data.get("data") or []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                j = make_job("breezy", item.get("name") or item.get("title"),
                             item.get("public_url") or item.get("url"))
                j["posted_date"] = parse_date(item.get("created_at") or item.get("published_at"))
                j["job_description"] = _strip_html(item.get("description"))
                j["employment_type"] = clean_text(item.get("type") or item.get("employment_type"))
                j["job_location"] = _location_text(item.get("location") or item.get("locations"))
                j["job_url"] = j["job_url"] or "https://%s.breezy.hr/p/%s" % (slug, item.get("slug"))
                jobs.append(j)
            if jobs:
                break
    return jobs


# ---------------------------------------------------------------------------
# JazzHR
# ---------------------------------------------------------------------------
def parse_jazzhr(session, info):
    slug = info.get("board") or ""
    jobs = []
    # public JSON endpoint
    for cand in ("https://%s.applytojob.com/api/jobs" % slug,
                 "https://%s.jazzhr.com/api/jobs" % slug,
                 "https://%s.applytojob.com/apply/jobs" % slug):
        data = session.fetch_json(cand, timeout=30)
        items = None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("jobs") or data.get("data") or data.get("results")
        if items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                j = make_job("jazzhr", item.get("title"))
                j["posted_date"] = parse_date(item.get("date_created") or item.get("created_at") or item.get("posted_on"))
                j["job_description"] = _strip_html(item.get("description") or item.get("job_description"))
                j["job_url"] = clean_text(item.get("apply_url") or item.get("url"))
                j["salary"] = clean_text(item.get("salary") or item.get("pay_range"))
                j["job_location"] = _location_text(item.get("location") or item.get("locations") or
                                                     item.get("city"))
                jobs.append(j)
            break
    return jobs


# ---------------------------------------------------------------------------
# Personio (public XML feed; search.json fallback)
# ---------------------------------------------------------------------------
import xml.etree.ElementTree as ET


def parse_personio(session, info):
    slug = info.get("board") or ""
    tld = info.get("tld") or "de"
    base = "https://%s.jobs.personio.%s" % (slug, tld)
    jobs = []

    xml_text = session.fetch_text(base + "/xml", timeout=35)
    if xml_text:
        try:
            root = ET.fromstring(xml_text)
            if root.tag == "workzag-jobs":
                for p in root.findall("position"):
                    j = _personio_xml_item(p, slug, tld)
                    if j:
                        jobs.append(j)
                if jobs:
                    return jobs
        except ET.ParseError:
            pass

    data = session.fetch_json(base + "/search.json", timeout=30)
    if isinstance(data, list):
        for item in data:
            j = _personio_item(item, slug, base)
            if j:
                jobs.append(j)
    elif isinstance(data, dict) and data.get("data"):
        for item in data["data"]:
            j = _personio_item(item, slug, base)
            if j:
                jobs.append(j)
    return jobs


def _personio_xml_item(p, slug, tld):
    def tx(tag):
        el = p.find(tag)
        return clean_text(el.text) if el is not None and el.text else ""

    j = make_job("personio", tx("name"))
    pid = tx("id")
    j["job_url"] = "https://%s.jobs.personio.%s/job/%s" % (slug, tld, pid)
    j["posted_date"] = parse_date(tx("createdAt"))
    offices = [tx("office")]
    add = p.find("additionalOffices")
    if add is not None:
        for o in add.findall("office"):
            if o.text:
                offices.append(clean_text(o.text))
    j["job_location"] = ", ".join(x for x in offices if x)
    j["employment_type"] = tx("schedule") or tx("employmentType")
    if tx("employmentType").lower() == "intern":
        j["employment_type"] = "Internship"
    j["seniority_level"] = tx("seniority")
    j["years_of_experience"] = tx("yearsOfExperience")
    j["skills"] = tx("keywords")
    desc_parts = []
    for group_tag in ("jobDescriptions", "jobRequirements"):
        grp = p.find(group_tag)
        if grp is None:
            continue
        for sec in grp.findall("jobDescription"):
            name = clean_text(sec.findtext("name"))
            raw = sec.findtext("value") or "".join(sec.itertext())
            val = _strip_html(raw)
            if name and val:
                desc_parts.append("%s: %s" % (name, val))
            elif val:
                desc_parts.append(val)
    j["job_description"] = "\n\n".join(desc_parts)
    j["salary"] = tx("salaryInformation")
    return j


def _personio_item(item, slug, base):
    if not isinstance(item, dict):
        return None
    j = make_job("personio", item.get("name") or item.get("jobName"))
    pid = item.get("id") or item.get("jobId")
    j["job_url"] = "https://%s.jobs.personio.%s/job/%s" % (slug, base.split("personio.")[1], pid)
    j["posted_date"] = parse_date(item.get("publishedAt") or item.get("publishedAt"))
    j["employment_type"] = clean_text(item.get("employmentType") or item.get("workType") or item.get("schedule"))
    j["job_description"] = clean_text(item.get("description") or item.get("jobDescription"))
    j["job_location"] = _location_text(item.get("location") or item.get("workplace"))
    j["seniority_level"] = clean_text(item.get("seniorityLevel") or item.get("careerLevel"))
    j["salary"] = clean_text(item.get("salary") or item.get("salaryInformation"))
    return j


# ---------------------------------------------------------------------------
# Workday (cxs JSON endpoint)
# ---------------------------------------------------------------------------
def parse_workday(session, info):
    """List every posting on a Workday board, then enrich each from its detail
    endpoint.

    Two fixes over the previous version:

    * the listing loop no longer exits on ``offset >= total``.  Workday's
      reported ``total`` is unreliable (a VUMC board with thousands of
      openings reported 40), so paging continues until a page comes back
      empty.
    * job details are fetched from ``/wday/cxs/{tenant}/{site}{externalPath}``,
      which is where the description, real posted date and time type live.
      Without it every Workday row had a title and nothing else.
    """
    base = (info.get("base_url") or "").rstrip("/")
    tenant = info.get("tenant") or ""
    domain = info.get("domain") or tenant
    if not base or not tenant:
        return []

    api = "%s/wday/cxs/%s/%s/jobs" % (base, tenant, domain)
    jobs = []
    paths = []
    page_size = 20                      # Workday caps the page size at 20
    # Pages are fetched CONCURRENTLY in batches.  Sequentially, a 10,000
    # posting board is 500 round trips at ~2s each -- 17 minutes -- and every
    # worker waiting on that board sat idle.  ScrapeSession keeps one
    # requests.Session per thread, so a small pool is safe.
    from concurrent.futures import ThreadPoolExecutor
    import logging
    log = logging.getLogger("job_scraper")

    def fetch_page(offset):
        payload = {"appliedFacets": {}, "limit": page_size, "offset": offset,
                   "searchText": ""}
        data = session.post_json(api, payload, timeout=CAREER_PAGE_TIMEOUT)
        if not isinstance(data, dict):
            return offset, None
        return offset, (data.get("jobPostings") or [])

    def add_items(items):
        for it in items:
            if not isinstance(it, dict):
                continue
            j = make_job("workday", it.get("title"))
            ext = it.get("externalPath") or ""
            if ext:
                j["job_url"] = base + ext if ext.startswith("/") else base + "/" + ext
                paths.append((j, ext))
            else:
                j["job_url"] = base
            j["posted_date"] = parse_date(it.get("postedOn") or it.get("postedOnDateTime"))
            j["employment_type"] = clean_text(it.get("jobRequisitionType")
                                              or it.get("workerType") or it.get("timeType"))
            j["job_location"] = _location_text(it.get("locationsText")
                                                or it.get("locations") or "")
            j["salary"] = clean_text(it.get("compensation") or it.get("payRate"))
            jobs.append(j)

    _, first = fetch_page(0)
    if not first:
        return []
    add_items(first)
    offset = page_size
    batch = 8
    done = len(first) < page_size
    with ThreadPoolExecutor(max_workers=batch) as pool:
        while not done and offset < MAX_ATS_API_OFFSET:
            offsets = [offset + i * page_size for i in range(batch)
                       if offset + i * page_size < MAX_ATS_API_OFFSET]
            results = sorted(pool.map(fetch_page, offsets), key=lambda r: r[0])
            for page_offset, items in results:
                if items is None or not items:
                    done = True
                    break
                add_items(items)
                if len(items) < page_size:
                    done = True
                    break
            offset += batch * page_size
            if (offset // page_size) % 50 == 0:
                log.info("Workday %s/%s: %d postings listed so far", tenant, domain, len(jobs))

    # Detail pass: description, canonical posted date, time type, req id.
    # Leadership titles first (see leadership.prioritize): a 5,000-posting
    # board only gets MAX_ATS_DETAIL_ENRICH detail fetches.  Also concurrent.
    from .leadership import prioritize
    chosen = prioritize(paths, lambda pair: pair[0].get("job_title"))[:MAX_ATS_DETAIL_ENRICH]

    def fetch_detail(pair):
        j, ext = pair
        detail_url = "%s/wday/cxs/%s/%s%s" % (base, tenant, domain,
                                             ext if ext.startswith("/") else "/" + ext)
        return j, session.fetch_json(detail_url, timeout=JOB_DETAIL_TIMEOUT)

    with ThreadPoolExecutor(max_workers=batch) as pool:
        details = list(pool.map(fetch_detail, chosen))
    for j, detail in details:
        if not isinstance(detail, dict):
            continue
        posting = detail.get("jobPostingInfo") or detail.get("jobPosting") or {}
        if not isinstance(posting, dict):
            continue
        description = posting.get("jobDescription") or posting.get("description")
        if description and not j["job_description"]:
            j["job_description"] = description
        for key in ("startDate", "postedOn", "postedDate"):
            resolved = parse_date(posting.get(key))
            if resolved:
                j["posted_date"] = resolved
                break
        deadline = parse_date(posting.get("endDate") or posting.get("applicationDeadline"))
        if deadline:
            j["application_deadline"] = deadline
        if not j["employment_type"]:
            j["employment_type"] = clean_text(posting.get("timeType")
                                               or posting.get("jobRequisitionType"))
        # The listing shows "2 Locations" / "60 Locations" for multi-site
        # postings; the detail carries the actual places.
        if not j["job_location"] or re.match(r"^\s*\d+\s+locations?\s*$", j["job_location"], re.I):
            primary = _location_text(posting.get("location"))
            extra = posting.get("additionalLocations")
            places = [p for p in [primary] if p]
            if isinstance(extra, list):
                for item in extra[:4]:
                    text = _location_text(item)
                    if text and text not in places:
                        places.append(text)
            elif isinstance(extra, str) and extra.strip() and extra.strip() not in places:
                places.append(extra.strip())
            if places:
                j["job_location"] = "; ".join(places)
        if posting.get("remoteType") and not j["job_location"]:
            j["job_location"] = clean_text(posting.get("remoteType"))
        if posting.get("jobReqId") and not j.get("extraction_evidence"):
            j["extraction_evidence"] = "workday:" + clean_text(posting.get("jobReqId"))
    return jobs


def workday_info_from_url(url):
    """Extract tenant/site/base from any Workday careers URL.

    A Workday board is addressed as
    ``https://{tenant}.wd{N}.myworkdayjobs.com/{site}`` and its JSON lives at
    ``/wday/cxs/{tenant}/{site}/jobs``.  The tenant comes from the *host*
    subdomain and the site from the path -- the old version took both from the
    path, so vumc.wd1.myworkdayjobs.com/vumccareers produced
    ``/wday/cxs/vumccareers/vumccareers/jobs`` and returned nothing.
    """
    parsed = urlparse(ensure_https(url or ""))
    base = "%s://%s" % (parsed.scheme or "https", parsed.netloc)
    segments = [s for s in (parsed.path or "").split("/") if s]

    host_tenant = ""
    match = re.match(r"^([a-z0-9\-_]+)\.wd\d+\.myworkdayjobs\.com$",
                     (parsed.netloc or "").lower())
    if match:
        host_tenant = match.group(1)

    tenant = ""
    site = ""

    # Explicit API form: /wday/cxs/{tenant}/{site}/...
    if "cxs" in segments:
        index = segments.index("cxs")
        if index + 1 < len(segments):
            tenant = segments[index + 1]
        if index + 2 < len(segments):
            site = segments[index + 2]
    else:
        # Drop locale segments (en-US, de-DE) and job-detail tails.
        meaningful = []
        for segment in segments:
            if re.match(r"^[a-z]{2}([-_][A-Za-z]{2})?$", segment):
                continue
            if segment.lower() in ("job", "jobs", "details", "d", "apply",
                                   "login", "search"):
                break
            meaningful.append(segment)
        if meaningful:
            site = meaningful[0]
        tenant = host_tenant

    if not tenant:
        tenant = host_tenant or (parsed.netloc.split(".")[0] if parsed.netloc else "")
    if not site:
        site = tenant
    return {"base_url": base, "tenant": tenant, "domain": site}


# ---------------------------------------------------------------------------
# SuccessFactors (classic jobsearch endpoint + microdata detail pages)
# ---------------------------------------------------------------------------
def parse_successfactors(session, info):
    base = info.get("base_url") or ""
    if not base:
        return []
    # resolve redirects to find the real SuccessFactors host
    r = session.fetch(base, timeout=30)
    final = r.url if (r is not None and r.url) else base
    host = urlparse(final).netloc or urlparse(base).netloc
    job_links = []
    seen = set()
    for start in range(0, MAX_ATS_API_OFFSET, 50):
        search = "https://%s/jobsearch/search?q=&location=&num=50&start=%d" % (host, start)
        html = session.fetch_text(search, timeout=CAREER_PAGE_TIMEOUT)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        found = False
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"/job/", href):
                full = url_join("https://%s/" % host, href)
                if full and full not in seen:
                    seen.add(full)
                    job_links.append(full)
                    found = True
        if not found:
            break
    jobs = []
    for u in job_links[:MAX_JOB_DETAIL_PAGES]:
        detail = session.fetch_text(u, timeout=JOB_DETAIL_TIMEOUT)
        if not detail:
            continue
        djobs = parse_jsonld_jobs(detail, u)
        if not djobs:
            djobs = parse_microdata_jobs(detail, u)
        if djobs:
            for j in djobs:
                if not j["job_url"]:
                    j["job_url"] = u
            jobs.extend(djobs)
            continue
        # fallback micro-parse
        dsoup = BeautifulSoup(detail, "html.parser")
        j = make_job("successfactors", "")
        og = dsoup.find("meta", attrs={"property": "og:title"})
        j["job_title"] = clean_text(og.get("content")) if og else ""
        date_meta = dsoup.find("meta", attrs={"itemprop": "datePosted"})
        if date_meta:
            j["posted_date"] = parse_date(date_meta.get("content"))
        disp = dsoup.find("div", class_=re.compile("jobDisplay"))
        if disp:
            j["job_description"] = clean_text(disp.get_text(" ", strip=True))
        j["job_url"] = u
        if j["job_title"] or j["job_description"]:
            labeled = extract_labeled_fields(j["job_description"])
            for key in ("employment_type", "seniority_level", "education_qualification",
                        "years_of_experience", "salary"):
                if not j.get(key) and labeled.get(key):
                    j[key] = labeled[key]
            jobs.append(j)
    return jobs

# ---------------------------------------------------------------------------
# Ashby (documented public posting API)
# ---------------------------------------------------------------------------
def parse_ashby(session, info):
    slug = info.get("board") or ""
    if not slug:
        return []
    data = session.fetch_json(
        "https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % slug,
        timeout=CAREER_PAGE_TIMEOUT)
    if not isinstance(data, dict):
        return []
    jobs = []
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        j = make_job("ashby", item.get("title"))
        j["job_url"] = clean_text(item.get("jobUrl") or item.get("applyUrl")) or (
            "https://jobs.ashbyhq.com/%s/%s" % (slug, item.get("id") or ""))
        j["posted_date"] = parse_date(item.get("publishedAt") or item.get("updatedAt"))
        j["job_location"] = _location_text(item.get("location") or item.get("address"))
        j["employment_type"] = clean_text(item.get("employmentType"))
        j["job_category"] = clean_text(item.get("department") or item.get("team"))
        j["job_description"] = _strip_html(item.get("descriptionHtml")
                                           or item.get("descriptionPlain"))
        comp = item.get("compensation") or {}
        if isinstance(comp, dict):
            summary = comp.get("compensationTierSummary") or comp.get("summary")
            if summary:
                j["salary"] = clean_text(summary)
        if item.get("isListed") is False:
            continue
        jobs.append(j)
    return jobs


# ---------------------------------------------------------------------------
# Eightfold
# ---------------------------------------------------------------------------
def parse_eightfold(session, info):
    base = (info.get("base_url") or "").rstrip("/")
    if not base:
        return []
    host = urlparse(base).netloc
    jobs = []
    start = 0
    while start < MAX_ATS_API_OFFSET:
        api = "%s/api/apply/v2/jobs?start=%d&num=100&domain=%s" % (base, start, host)
        data = session.fetch_json(api, timeout=CAREER_PAGE_TIMEOUT)
        if not isinstance(data, dict):
            break
        positions = data.get("positions") or data.get("jobs") or []
        if not positions:
            break
        for item in positions:
            if not isinstance(item, dict):
                continue
            j = make_job("eightfold", item.get("name") or item.get("title"))
            j["job_url"] = clean_text(item.get("canonicalPositionUrl")
                                      or item.get("positionUrl")) or base
            j["job_location"] = _location_text(item.get("location")
                                               or item.get("locations"))
            j["job_description"] = _strip_html(item.get("job_description")
                                                or item.get("description"))
            j["employment_type"] = clean_text(item.get("type") or item.get("t_employment"))
            j["job_category"] = clean_text(item.get("department") or item.get("business_unit"))
            j["posted_date"] = parse_date(item.get("t_create") or item.get("created_at"))
            jobs.append(j)
        total = data.get("count") or data.get("total") or 0
        start += len(positions)
        if total and start >= total:
            break
    return jobs


# ---------------------------------------------------------------------------
# Cornerstone OnDemand (career-site search endpoint; best effort)
# ---------------------------------------------------------------------------
def parse_cornerstone(session, info):
    base = (info.get("base_url") or "").rstrip("/")
    slug = info.get("board") or ""
    if not base and slug:
        base = "https://%s.csod.com" % slug
    if not base:
        return []
    jobs = []
    page = 1
    while page <= 50:
        payload = {"careerSiteId": 1, "cultureId": 1, "pageNumber": page,
                   "pageSize": 100, "searchText": "", "cultureName": "en-US"}
        data = session.post_json(
            "%s/services/x/career-site/v1/search" % base, payload,
            timeout=CAREER_PAGE_TIMEOUT)
        if not isinstance(data, dict):
            break
        payload_data = data.get("data") or data
        requisitions = (payload_data.get("requisitions")
                        or payload_data.get("Requisitions") or [])
        if not requisitions:
            break
        for item in requisitions:
            if not isinstance(item, dict):
                continue
            j = make_job("cornerstone",
                         item.get("displayJobTitle") or item.get("title"))
            req_id = item.get("requisitionId") or item.get("id") or ""
            j["job_url"] = "%s/ux/ats/careersite/1/requisition/%s" % (base, req_id)
            j["job_location"] = _location_text(item.get("locationName")
                                               or item.get("locations"))
            j["job_category"] = clean_text(item.get("departmentName"))
            j["posted_date"] = parse_date(item.get("postedDate"))
            j["job_description"] = _strip_html(item.get("externalJobDescription")
                                                or item.get("jobDescription"))
            jobs.append(j)
        total = payload_data.get("totalRecords") or payload_data.get("total") or 0
        if total and len(jobs) >= total:
            break
        page += 1
    return jobs


# ---------------------------------------------------------------------------
# BambooHR
# ---------------------------------------------------------------------------
def parse_bamboo(session, info):
    slug = info.get("board") or ""
    base = "https://%s.bamboohr.com/careers" % slug
    html = session.fetch_text(base, timeout=30)
    if not html:
        return []
    jobs = []
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/careers/\d+", href):
            u = url_join(base, href)
            if u and u not in seen:
                seen.add(u)
    jsonld = parse_jsonld_jobs(html, base)
    if jsonld:
        jobs.extend(jsonld)
    for u in list(seen)[:MAX_JOB_DETAIL_PAGES]:
        detail = session.fetch_text(u, timeout=JOB_DETAIL_TIMEOUT)
        if detail:
            jobs.extend(parse_jsonld_jobs(detail, u))
    return jobs

# ---------------------------------------------------------------------------
# Vendor-agnostic JSON board prober.
#
# There are far more ATS products than anyone can write parsers for, but a
# large share of them expose their postings as JSON at a predictable path off
# the board URL.  This tries a small, bounded set of those paths and maps
# whatever list of postings it finds through common key names -- so a board
# from a vendor with no dedicated parser still yields structured rows instead
# of falling back to HTML scraping.
# ---------------------------------------------------------------------------

_JSON_LIST_KEYS = ("jobs", "data", "results", "content", "items", "positions",
                   "offers", "vacancies", "postings", "jobPostings", "openings",
                   "jobPosts", "vagas", "records", "list", "elements")

_TITLE_KEYS = ("title", "name", "jobTitle", "job_title", "position",
               "positionName", "position_name", "titulo", "vaga", "bezeichnung",
               "jobName", "displayJobTitle", "headline", "role")
_URL_KEYS = ("url", "absolute_url", "absoluteUrl", "jobUrl", "job_url",
             "applyUrl", "apply_url", "applicationUrl", "link", "permalink",
             "canonicalPositionUrl", "href", "shortlink", "publicUrl", "webUrl")
_LOCATION_KEYS = ("location", "locationName", "location_name", "job_location",
                  "city", "cidade", "ort", "locations", "locationsText",
                  "workplace", "office", "region", "place")
_DESCRIPTION_KEYS = ("description", "descriptionHtml", "description_html",
                     "content", "job_description", "jobDescription",
                     "descricao", "descrição", "beschreibung", "body",
                     "requirements", "details", "text")
_DATE_KEYS = ("publishedAt", "published_at", "publishedDate", "createdAt",
              "created_at", "datePosted", "posted_at", "postedOn", "postedDate",
              "updated_at", "updatedAt", "dataPublicacao", "publicationDate",
              "firstPublished", "openDate")
_EMPLOYMENT_KEYS = ("employmentType", "employment_type", "contractType",
                    "contract_type", "type", "tipo", "workType", "schedule",
                    "timeType", "jobType", "job_type")
_CATEGORY_KEYS = ("department", "departmentName", "team", "category", "function",
                  "area", "abteilung", "businessUnit", "business_unit", "sector")


def _first_value(item, keys):
    for key in keys:
        if key in item and item[key] not in (None, "", [], {}):
            return item[key]
    return None


def _find_posting_list(payload, depth=0):
    """Locate the list of postings inside an arbitrary JSON payload."""
    if depth > 4:
        return []
    if isinstance(payload, list):
        dicts = [x for x in payload if isinstance(x, dict)]
        if len(dicts) >= 2 and any(_first_value(x, _TITLE_KEYS) for x in dicts):
            return dicts
        return []
    if not isinstance(payload, dict):
        return []
    for key in _JSON_LIST_KEYS:
        if key in payload:
            found = _find_posting_list(payload[key], depth + 1)
            if found:
                return found
    # Otherwise walk the values once.
    for value in payload.values():
        if isinstance(value, (list, dict)):
            found = _find_posting_list(value, depth + 1)
            if found:
                return found
    return []


def _json_candidate_urls(board_url):
    parsed = urlparse(ensure_https(board_url))
    origin = "%s://%s" % (parsed.scheme or "https", parsed.netloc)
    path = (parsed.path or "").rstrip("/")
    base = origin + path
    candidates = [
        base + "/jobs.json",
        base + "/api/jobs",
    ]
    # "<base>.json" is only valid when there is a path.  With a bare origin it
    # produced "https://deliverymuch.gupy.io.json", an invalid hostname that
    # cost a DNS failure and its retries on every probe.
    if path:
        candidates.append(base + ".json")
    candidates.append(origin + "/api/jobs")
    if path:
        candidates.append(origin + "/api/v1" + path + "/jobs")
    seen = []
    for url in candidates:
        if url not in seen:
            seen.append(url)
    return seen[:4]


def probe_json_board(session, board_url, ats_name="", limit=None):
    """Try a few predictable JSON endpoints for a recognised but unparsed board."""
    if not board_url:
        return []
    limit = limit or MAX_JOBS_PER_COMPANY
    for api in _json_candidate_urls(board_url):
        data = session.fetch_json(api, timeout=JOB_DETAIL_TIMEOUT)
        if data is None:
            continue
        items = _find_posting_list(data)
        if len(items) < 2:
            continue

        jobs = []
        for item in items[:limit]:
            title = clean_text(_first_value(item, _TITLE_KEYS))
            if not title or len(title) > 200:
                continue
            j = make_job(ats_name or "json-board", title)
            url_value = _first_value(item, _URL_KEYS)
            if isinstance(url_value, str) and url_value.startswith("http"):
                j["job_url"] = url_value
            elif url_value:
                j["job_url"] = url_join(board_url, str(url_value))
            else:
                identifier = item.get("id") or item.get("slug") or ""
                j["job_url"] = ("%s/%s" % (board_url.rstrip("/"), identifier)
                                if identifier else board_url)
            j["job_location"] = _location_text(_first_value(item, _LOCATION_KEYS))
            description = _first_value(item, _DESCRIPTION_KEYS)
            if isinstance(description, (dict, list)):
                description = json.dumps(description)[:20000]
            j["job_description"] = _strip_html(description)
            j["posted_date"] = parse_date(_first_value(item, _DATE_KEYS))
            employment = _first_value(item, _EMPLOYMENT_KEYS)
            if isinstance(employment, dict):
                employment = employment.get("name") or employment.get("label")
            j["employment_type"] = clean_text(employment)
            category = _first_value(item, _CATEGORY_KEYS)
            if isinstance(category, dict):
                category = category.get("name") or category.get("label")
            j["job_category"] = clean_text(category)
            jobs.append(j)

        if len(jobs) >= 2:
            import logging
            logging.getLogger("job_scraper").info(
                "JSON board probe hit %s -> %d postings (%s)", api, len(jobs),
                ats_name or "unknown vendor")
            return jobs
    return []
