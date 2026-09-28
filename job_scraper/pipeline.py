import collections
import csv
import itertools
import logging
import re
import sys
import os
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler

import openpyxl

from . import netcache
from .config import (
    INPUT_FILE,
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    INPUT_COUNTRY_HEADERS,
    INPUT_NAME_HEADERS,
    INPUT_WEBSITE_HEADERS,
    OUTPUT_FILE,
    OUTPUT_COLUMNS,
    DEFAULT_WORKERS,
    LOG_FILE,
)
from .company import process_company_details
from .session import ScrapeSession, reset_host_failures
from .fields import now_iso, extract_labeled_fields, experience_year_range, clean_text
from .clean_html import html_to_plain_text
from .detect_language import detect_language
from .urlutils import normalize_website, hostname

log = logging.getLogger("job_scraper")

# job_description is capped at 30,000 characters, comfortably under the csv
# default, but a hand-edited or merged file can exceed it and csv raises
# rather than truncating.  Raise the ceiling instead of failing a resume.
try:
    csv.field_size_limit(min(sys.maxsize, 10 * 1024 * 1024))
except (OverflowError, ValueError):
    pass

_RETRYABLE_STATUSES = {"Unreachable", "Career Page Not Found",
                       "Career Page Found - Extraction Unsupported", "Error"}


def _normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _resolve_columns(header_row):
    """Map (name, website, country) onto whatever columns the sheet actually has.

    Reading by fixed position silently mismatched smoke_test_input.xlsx
    (KEYID, COMPANY_NAME, COUNTRY, ENTITY_TYPE, WEBSITE): company names landed
    in the website column and the KEYID hash became the company name.  Every
    lookup then failed with NameResolutionError and the row was written out as
    Unreachable.  Headers are resolved by name now, with positional fallback
    only when no header matches.
    """
    normalized = [_normalize_header(cell) for cell in (header_row or [])]
    lookup = {}
    for index, cell in enumerate(normalized):
        if cell and cell not in lookup:
            lookup[cell] = index

    def pick(candidates):
        for candidate in candidates:
            key = _normalize_header(candidate)
            if key in lookup:
                return lookup[key]
        # substring match, e.g. "company_website_url"
        for candidate in candidates:
            key = _normalize_header(candidate)
            for cell, index in lookup.items():
                if key and key in cell:
                    return index
        return None

    name_index = pick(INPUT_NAME_HEADERS)
    website_index = pick(INPUT_WEBSITE_HEADERS)
    country_index = pick(INPUT_COUNTRY_HEADERS)

    if name_index is None and website_index is None and country_index is None:
        # Headerless sheet: fall back to the historical layout.
        return 0, 1, 2, False
    if name_index is None:
        name_index = 0
    return name_index, website_index, country_index, True


def _read_delimited(input_file):
    """Stream a CSV/TSV company list, resolving columns by header name.

    data_7.7m_405k.csv (405,210 rows) is the real input; only the smoke-test
    extract is an .xlsx, so openpyxl alone was not enough.
    """
    delimiter = "\t" if input_file.lower().endswith((".tsv", ".tab")) else ","
    rows = []
    with open(input_file, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            header_row = next(reader)
        except StopIteration:
            return []
        name_index, website_index, country_index, has_header = _resolve_columns(header_row)
        log.info("Input columns resolved: name=%s website=%s country=%s (header=%s) from %s",
                 name_index, website_index, country_index, has_header, header_row)

        def cell(row, index):
            if index is None or index >= len(row):
                return ""
            value = row[index]
            return value.strip() if isinstance(value, str) else (
                "" if value is None else str(value).strip())

        if not has_header:
            reader = itertools.chain([header_row], reader)
        for row in reader:
            if not row:
                continue
            name = cell(row, name_index)
            if not name:
                continue
            rows.append((name, cell(row, website_index), cell(row, country_index)))
    return rows


def read_companies(input_file):
    if str(input_file).lower().endswith((".csv", ".tsv", ".tab", ".txt")):
        return _read_delimited(input_file)

    wb = openpyxl.load_workbook(input_file, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        wb.close()
        return []

    name_index, website_index, country_index, has_header = _resolve_columns(header_row)
    log.info("Input columns resolved: name=%s website=%s country=%s (header=%s) from %s",
             name_index, website_index, country_index, has_header,
             [str(c) for c in (header_row or [])])

    def cell(row, index):
        if index is None or index >= len(row):
            return ""
        value = row[index]
        return str(value).strip() if value is not None else ""

    data_rows = rows_iter if has_header else itertools.chain([header_row], rows_iter)

    rows = []
    for row in data_rows:
        if not row:
            continue
        name = cell(row, name_index)
        if not name:
            continue
        rows.append((name, cell(row, website_index), cell(row, country_index)))
    wb.close()
    return rows


def load_completed(output_file):
    done = set()
    if not os.path.exists(output_file):
        return done
    with open(output_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = "%s|%s" % (row.get("company_name", ""), row.get("website", ""))
            done.add(key)
    return done


def remove_retryable_rows(output_file):
    """Drop transient-failure rows so --retry-failures can redo them.

    Streams row by row.  The previous version did `rows = list(reader)`, which
    on a shard output measured in gigabytes would exhaust memory on the VM.
    """
    if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
        return 0

    directory = os.path.dirname(os.path.abspath(output_file))
    fd, temporary = tempfile.mkstemp(prefix=".retry_", suffix=".csv", dir=directory)
    removed = 0
    kept = 0
    try:
        with open(output_file, "r", encoding="utf-8-sig", newline="") as source, \
                os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as destination:
            reader = csv.DictReader(source)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                return 0
            writer = csv.DictWriter(destination, fieldnames=fieldnames,
                                    extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                if row.get("job_status") in _RETRYABLE_STATUSES:
                    removed += 1
                    continue
                writer.writerow(row)
                kept += 1
            destination.flush()
            os.fsync(destination.fileno())
        if removed:
            os.replace(temporary, output_file)
            log.info("Removed %d retry-eligible rows, kept %d", removed, kept)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return removed


class CsvWriter:
    def __init__(self, path, columns):
        self.path = path
        self.columns = columns
        self.lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self):
        """Create the header, or migrate an existing file to a new schema.

        The previous version accepted any file whose header merely started with
        "company_name", so adding `num_positions` to OUTPUT_COLUMNS left older
        CSVs (ajay.csv included) permanently without the new column.  A header
        that no longer matches is now rewritten in place, with existing rows
        preserved and new columns backfilled.
        """
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            with open(self.path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                try:
                    existing = next(reader)
                except StopIteration:
                    existing = []
            existing = [c.lstrip("\ufeff") for c in existing]
            if existing == list(self.columns):
                return
            if existing:
                self._migrate_header(existing)
                return
        with open(self.path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.columns)

    def _migrate_header(self, existing):
        added = [c for c in self.columns if c not in existing]
        dropped = [c for c in existing if c not in self.columns]
        log.info("Migrating CSV schema for %s (added=%s dropped=%s)",
                 os.path.basename(self.path), added, dropped)
        with open(self.path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, temporary = tempfile.mkstemp(prefix=".schema_", suffix=".csv", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.columns,
                                        extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    for column in added:
                        row.setdefault(column, "")
                    writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def write_rows(self, rows):
        if not rows:
            return
        with self.lock:
            with open(self.path, "a", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.columns, extrasaction="ignore")
                for r in rows:
                    w.writerow(r)


def _company_key(row):
    return "%s|%s" % (row[0], row[1])


def run(
    input_file=INPUT_FILE,
    output_file=OUTPUT_FILE,
    limit=0,
    offset=0,
    workers=DEFAULT_WORKERS,
    resume=True,
    countries=None,
    quiet=True,
    enable_search=True,
    retry_failures=False,
    shard_index=0,
    shard_count=1,
    shard_mode="interleave",
):
    # Rotating file handler, installed once: basicConfig is a no-op if the
    # root logger already has handlers, so calling run() twice in a process
    # previously left the second call logging through the first call's
    # unrotated handler.
    root = logging.getLogger()
    if not any(getattr(h, "_job_scraper_handler", False) for h in root.handlers):
        for handler in list(root.handlers):
            root.removeHandler(handler)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)
        except OSError:
            pass
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8")
        stream_handler = logging.StreamHandler()
        for handler in (file_handler, stream_handler):
            handler.setFormatter(formatter)
            handler._job_scraper_handler = True
            root.addHandler(handler)
        root.setLevel(logging.INFO)
    if quiet:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)

    netcache.install()
    reset_host_failures()

    companies = read_companies(input_file)
    log.info("Total companies in input: %d", len(companies))
    with_website = sum(1 for c in companies if c[1])
    log.info("Input rows with a website value: %d/%d", with_website, len(companies))

    if countries:
        companies = [c for c in companies if c[2] in countries]
        log.info("After country filter: %d", len(companies))

    # Shard selection, for splitting one input across several machines.
    # "interleave" takes every Nth row; "block" takes a contiguous slice.
    # Interleave is the default because the input is grouped by source, so
    # contiguous blocks differ by up to 30% in how many rows actually carry a
    # website -- i.e. in how much work they are -- and the whole run then waits
    # on the slowest machine.  Every row belongs to exactly one shard under
    # either mode.
    if shard_count and shard_count > 1:
        if not (0 <= shard_index < shard_count):
            raise ValueError("shard_index must be in 0..shard_count-1")
        if shard_mode == "block":
            chunk = (len(companies) + shard_count - 1) // shard_count
            companies = companies[shard_index * chunk:(shard_index + 1) * chunk]
        else:
            companies = companies[shard_index::shard_count]
        log.info("Shard %d/%d (%s): %d companies",
                 shard_index, shard_count, shard_mode, len(companies))

    if offset:
        companies = companies[offset:]
    if limit:
        companies = companies[:limit]
    log.info("Companies to process this run: %d", len(companies))

    if retry_failures:
        removed = remove_retryable_rows(output_file)
        log.info("Removed %d retry-eligible rows before reprocessing", removed)
    writer = CsvWriter(output_file, OUTPUT_COLUMNS)
    completed = load_completed(output_file) if resume else set()
    todo = [c for c in companies if _company_key(c) not in completed]
    log.info("Skipping %d already processed; %d to do", len(companies) - len(todo), len(todo))

    counters = {"processed": 0, "ok": 0, "no_jobs": 0, "unsupported": 0,
                "career_not_found": 0, "unreachable": 0, "error": 0, "jobs": 0}
    counters_lock = threading.Lock()
    session_factory = lambda: ScrapeSession()
    # Bounded: an unbounded dict of {domain: full job list} across 405k
    # companies is measured in gigabytes.  Insertion-ordered eviction keeps the
    # dedupe benefit for recently-seen domains without unbounded growth.
    domain_results = collections.OrderedDict()
    domain_cache_limit = max(1000, int(os.environ.get("JOB_SCRAPER_DOMAIN_CACHE", "20000")))
    domain_inflight = {}
    domain_cache_lock = threading.Lock()

    def process_with_domain_cache(row, session):
        cache_key = hostname(normalize_website(row[1])) if row[1] else ""
        if not cache_key:
            return process_company_details(row, session, enable_search=enable_search)
        owner = False
        with domain_cache_lock:
            if cache_key in domain_results:
                return domain_results[cache_key]
            event = domain_inflight.get(cache_key)
            if event is None:
                event = threading.Event()
                domain_inflight[cache_key] = event
                owner = True
        if not owner:
            event.wait()
            with domain_cache_lock:
                if cache_key in domain_results:
                    return domain_results[cache_key]
            return process_company_details(row, session, enable_search=enable_search)
        try:
            details = process_company_details(row, session, enable_search=enable_search)
            with domain_cache_lock:
                domain_results[cache_key] = details
                while len(domain_results) > domain_cache_limit:
                    domain_results.popitem(last=False)
            return details
        finally:
            with domain_cache_lock:
                domain_inflight.pop(cache_key, None)
                event.set()

    def process(row):
        key = _company_key(row)
        name, web, country = row
        try:
            session = session_factory()
            details = process_with_domain_cache(row, session)
            status, jobs, source = details["status"], details["jobs"], details["source"]
        except Exception as exc:
            status, jobs, source = "error", [], str(exc)[:200]
            details = {"career_page_url": "", "career_page_status": "Error",
                       "career_page_discovery_method": ""}
        scraped_at = now_iso()
        # Total positions scraped for this company.  process_company_details
        # reports it; len(jobs) is the fallback for older call paths.
        total_positions = int(details.get("num_positions", len(jobs)) or 0)

        out_rows = []
        for j in jobs:
            raw_description = j.get("job_description", "")
            clean_description = html_to_plain_text(raw_description)
            csv_description = clean_text(clean_description, max_len=30000)
            labeled = extract_labeled_fields(clean_description)
            experience_text = j.get("years_of_experience", "") or labeled.get("years_of_experience", "")
            derived_experience_min, derived_experience_max = experience_year_range(experience_text)
            job_source = j.get("source", "") or source
            structured_source = job_source in ("jsonld", "microdata", "hrmanager-detail")
            out_rows.append({
                "company_name": name,
                "country": country,
                "website": web,
                "career_page_url": details.get("career_page_url", ""),
                "career_page_status": details.get("career_page_status", ""),
                "career_page_discovery_method": details.get("career_page_discovery_method", ""),
                "resolved_website": details.get("resolved_website", ""),
                "website_discovery": details.get("website_discovery", ""),
                "job_title": j.get("job_title", ""),
                "job_category": j.get("job_category", ""),
                "job_location": j.get("job_location", ""),
                "posted_date": j.get("posted_date", ""),
                "application_deadline": j.get("application_deadline", ""),
                "closed_date": j.get("closed_date", ""),
                "job_status": j.get("job_status", "Active"),
                "extraction_status": "Job Detail Extracted",
                "extraction_confidence": j.get("extraction_confidence") or
                                         ("High" if structured_source else "Medium"),
                "extraction_evidence": j.get("extraction_evidence") or job_source,
                "last_checked_at": j.get("last_checked_at", ""),
                "education_stream": j.get("education_stream", ""),
                "education_type": j.get("education_type", ""),
                "education_qualification": j.get("education_qualification", ""),
                "years_of_experience_min": j.get("years_of_experience_min", "") or derived_experience_min,
                "years_of_experience_max": j.get("years_of_experience_max", "") or derived_experience_max,
                "seniority_level": j.get("seniority_level", ""),
                "employment_type": j.get("employment_type", ""),
                "skills": j.get("skills", ""),
                "description_language": detect_language(clean_description),
                # Keep each CSV record on one physical line. Raw multiline HTML
                # made correct quoted CSV look column-shifted in text viewers.
                "job_description": csv_description,
                "job_description_clean": csv_description,
                "job_url": j.get("job_url", ""),
                "salary_disclosed": bool(j.get("salary") or j.get("min_salary") or j.get("max_salary")),
                "salary": j.get("salary", ""),
                "min_salary": j.get("min_salary", ""),
                "max_salary": j.get("max_salary", ""),
                "currency": j.get("currency", ""),
                "source": job_source,
                "scraped_at": scraped_at,
                "num_positions": total_positions,
            })
        if not out_rows:
            out_rows.append({
                "company_name": name,
                "country": country,
                "website": web,
                "career_page_url": details.get("career_page_url", ""),
                "career_page_status": details.get("career_page_status", ""),
                "career_page_discovery_method": details.get("career_page_discovery_method", ""),
                "resolved_website": details.get("resolved_website", ""),
                "website_discovery": details.get("website_discovery", ""),
                "job_status": {
                    "no_jobs": "No Jobs Found",
                    "unsupported": "Career Page Found - Extraction Unsupported",
                    "career_not_found": "Career Page Not Found",
                }.get(status, status.replace("_", " ").title()),
                "extraction_status": {
                    "no_jobs": "Explicit No-Openings Evidence",
                    "unsupported": "Extraction Unsupported",
                    "career_not_found": "Career Page Not Found",
                    "unreachable": "Website Unreachable",
                }.get(status, "Processing Error"),
                "extraction_confidence": "High" if status == "no_jobs" else "Low",
                "extraction_evidence": source,
                "source": source,
                "scraped_at": scraped_at,
                # Explicit 0 rather than blank, so the column is always numeric.
                "num_positions": total_positions,
            })
        writer.write_rows(out_rows)
        with counters_lock:
            counters["processed"] += 1
            counters[status] = counters.get(status, 0) + 1
            counters["jobs"] += len(jobs)
            p = counters["processed"]
            if p % 50 == 0 or p == len(todo):
                log.info(
                    "[%d/%d] ok=%d no_jobs=%d unreach=%d err=%d jobs=%d last=%s status=%s src=%s",
                    p, len(todo), counters["ok"], counters["no_jobs"],
                    counters["unreachable"], counters["error"], counters["jobs"],
                    name, status, source,
                )
        return key

    if not todo:
        log.info("Nothing to do.")
        return counters

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process, c): c for c in todo}
        try:
            for fut in as_completed(futs):
                fut.result()
        except KeyboardInterrupt:
            log.warning("Interrupted; results so far saved. Re-run with --resume to continue.")
            ex.shutdown(wait=False, cancel_futures=True)
            raise

    from .websearch import engine_status
    log.info("Done. %s", counters)
    log.info("DNS cache stats: %s", netcache.stats())
    log.info("Search engine state: %s", engine_status())
    return counters
