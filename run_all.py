"""Full-dataset runner: resumable, shardable, CSV or XLSX input.

The dataset is 405,210 companies, so this is built for a run measured in days
across several machines, not a single pass.

    # single machine, whole file, resume-safe -- just re-run after any stop
    python run_all.py

    # 6 machines: set --shard-index 0..5 on each, same --shard-count
    python run_all.py --shard-count 6 --shard-index 0

    # a slice, to gauge throughput before committing
    python run_all.py --limit 2000

    # re-attempt only the rows that failed for transient reasons
    python run_all.py --retry-failures

Resume is ON by default: companies already present in the output CSV are
skipped, so killing and restarting never loses or duplicates work.
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from job_scraper import netcache                     # noqa: E402
from job_scraper.config import OUTPUT_COLUMNS        # noqa: E402
from job_scraper.pipeline import run  # noqa: E402
from job_scraper.websearch import engine_status      # noqa: E402

DEFAULT_INPUT = os.path.join(ROOT, "data_7.7m_405k.csv")


def human(seconds):
    seconds = int(max(0, seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %dm" % (hours, minutes)
    return "%dm" % minutes


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default="",
                        help="default: all_companies.csv, or "
                             "shard_<i>_of_<n>.csv when sharding")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-mode", choices=("interleave", "block"),
                        default=os.environ.get("JOB_SCRAPER_SHARD_MODE", "interleave"),
                        help="interleave: every Nth row (balanced work per VM). "
                             "block: one contiguous slice.")
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int,
                        default=int(os.environ.get("JOB_SCRAPER_WORKERS", "12")))
    parser.add_argument("--countries", default="",
                        help="comma-separated filter, e.g. USA,Germany")
    parser.add_argument("--no-search", dest="search", action="store_false")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--retry-failures", action="store_true",
                        help="drop Unreachable/Not Found/Error rows first, then redo them")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        parser.error("input not found: %s" % args.input)
    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        parser.error("--shard-index must be in 0..--shard-count-1")

    output = args.output
    if not output:
        output = ("shard_%d_of_%d.csv" % (args.shard_index, args.shard_count)
                  if args.shard_count > 1 else "all_companies.csv")
    if not os.path.isabs(output):
        output = os.path.join(ROOT, output)

    # Shard selection happens inside run(), after the country filter, so that
    # --countries and sharding compose correctly.  offset/limit then apply
    # within this machine's shard.
    offset, limit = args.offset, args.limit
    if args.shard_count > 1:
        print("shard   : %d of %d, mode=%s"
              % (args.shard_index, args.shard_count, args.shard_mode))

    print("\ninput   : %s" % args.input)
    print("output  : %s" % output)
    print("workers : %d   search=%s   resume=%s   retry_failures=%s"
          % (args.workers, args.search, args.resume, args.retry_failures))
    print("columns : %d (num_positions %s)\n"
          % (len(OUTPUT_COLUMNS),
             "present" if "num_positions" in OUTPUT_COLUMNS else "MISSING"))

    started = time.time()
    try:
        counters = run(
            input_file=args.input,
            output_file=output,
            limit=limit,
            offset=offset,
            workers=args.workers,
            resume=args.resume,
            countries=[c.strip() for c in args.countries.split(",") if c.strip()] or None,
            quiet=not args.verbose,
            enable_search=args.search,
            retry_failures=args.retry_failures,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            shard_mode=args.shard_mode,
        )
    except KeyboardInterrupt:
        elapsed = time.time() - started
        print("\ninterrupted after %s. Progress is saved in %s."
              % (human(elapsed), output))
        print("Re-run the same command to continue (resume is on by default).")
        return 130

    elapsed = time.time() - started
    processed = counters.get("processed", 0)
    print("\n" + "=" * 70)
    print("counters : %s" % counters)
    print("elapsed  : %s   (%.1f companies/min)"
          % (human(elapsed), processed / (elapsed / 60.0) if elapsed else 0))
    if processed and limit:
        remaining = max(0, limit - processed)
        if remaining:
            print("projected: %s for the remaining %d companies"
                  % (human(remaining * elapsed / processed), remaining))
    print("dns      : %s" % netcache.stats())
    print("engines  : %s" % engine_status())
    print("\nsummarize with:  python deploy%scheck_output.py --file %s"
          % (os.sep, output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
