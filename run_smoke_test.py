"""Smoke test: 100 companies from smoke_test_input.xlsx -> ajay.csv.

Run from D:\\data_companies:

    python run_smoke_test.py                     # 100 companies, fresh ajay.csv
    python run_smoke_test.py --limit 25          # quicker probe
    python run_smoke_test.py --resume            # continue an interrupted run
    python run_smoke_test.py --no-search         # skip web search entirely

The old runtest.py did `from pipeline import run` after chdir'ing into
job_scraper/, which cannot work: pipeline.py uses package-relative imports.
This runs the package properly from the project root.
"""

import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from job_scraper.pipeline import run  # noqa: E402
from job_scraper import netcache  # noqa: E402
from job_scraper.websearch import engine_status  # noqa: E402

DEFAULT_INPUT = os.path.join(ROOT, "smoke_test_input.xlsx")
DEFAULT_OUTPUT = os.path.join(ROOT, "ajay.csv")


def summarize(path):
    if not os.path.exists(path):
        print("!! output file was not created: %s" % path)
        return
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        print("!! output file is empty")
        return

    columns = list(rows[0].keys())
    print("\n" + "=" * 72)
    print("OUTPUT: %s" % path)
    print("rows: %d   columns: %d" % (len(rows), len(columns)))
    print("num_positions column present: %s" % ("num_positions" in columns))

    companies = {}
    for row in rows:
        key = (row.get("company_name", ""), row.get("website", ""))
        companies.setdefault(key, row)

    statuses = collections.Counter(r.get("job_status", "") for r in companies.values())
    print("\ncompanies: %d" % len(companies))
    print("status breakdown (one vote per company):")
    for status, count in statuses.most_common():
        print("  %-45s %4d  (%5.1f%%)" % (status or "(blank)", count,
                                          100.0 * count / len(companies)))

    counts = []
    for row in companies.values():
        try:
            counts.append(int(row.get("num_positions") or 0))
        except ValueError:
            counts.append(0)
    with_jobs = [c for c in counts if c > 0]
    print("\npositions scraped: total=%d  companies_with_positions=%d" %
          (sum(counts), len(with_jobs)))
    if with_jobs:
        print("  per company with positions: min=%d median=%d max=%d" % (
            min(with_jobs), sorted(with_jobs)[len(with_jobs) // 2], max(with_jobs)))
    blank = sum(1 for r in rows if r.get("num_positions", "") == "")
    print("  rows with a blank num_positions: %d (should be 0)" % blank)

    top = sorted(companies.values(),
                 key=lambda r: -(int(r.get("num_positions") or 0)))[:10]
    print("\ntop companies by num_positions:")
    for row in top:
        print("  %-6s %s" % (row.get("num_positions"), row.get("company_name", "")[:60]))
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--resume", action="store_true",
                        help="keep existing ajay.csv rows and skip those companies")
    parser.add_argument("--no-search", dest="search", action="store_false")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not args.resume and os.path.exists(args.output):
        backup = args.output + ".prev"
        os.replace(args.output, backup)
        print("moved previous output to %s" % backup)

    print("input : %s" % args.input)
    print("output: %s" % args.output)
    print("limit=%d offset=%d workers=%d search=%s resume=%s\n"
          % (args.limit, args.offset, args.workers, args.search, args.resume))

    counters = run(
        input_file=args.input,
        output_file=args.output,
        limit=args.limit,
        offset=args.offset,
        workers=args.workers,
        resume=args.resume,
        quiet=args.quiet,
        enable_search=args.search,
    )

    print("\ncounters: %s" % counters)
    print("dns cache: %s" % netcache.stats())
    print("search engines: %s" % engine_status())
    summarize(args.output)


if __name__ == "__main__":
    main()
