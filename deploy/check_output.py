"""Summarize a scraper output CSV without re-running anything.

    python deploy\\check_output.py                     # defaults to ajay.csv
    python deploy\\check_output.py --file output.csv
    python deploy\\check_output.py --compare ajay.csv.prev
    python deploy\\check_output.py --watch             # refresh every 15s

Safe to run while a scrape is in progress: opens read-only.
"""

import argparse
import collections
import csv
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    if not os.path.exists(path):
        return None, "does not exist"
    if os.path.getsize(path) == 0:
        return None, "is empty"
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle)), None
    except Exception as exc:
        return None, str(exc)


def to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def summarize(path, label=""):
    rows, error = load(path)
    print("\n" + "=" * 74)
    print("%s%s" % (label, path))
    if error:
        print("  !! %s" % error)
        return None
    if not rows:
        print("  header only, no data rows yet")
        return None

    columns = list(rows[0].keys())
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
    age = (time.time() - os.path.getmtime(path)) / 60.0
    print("  last written : %s  (%.1f min ago%s)"
          % (mtime, age, ", still running?" if age < 2 else ""))
    print("  size         : %.1f KB" % (os.path.getsize(path) / 1024.0))
    print("  rows         : %d   columns: %d" % (len(rows), len(columns)))
    print("  num_positions column: %s"
          % ("PRESENT" if "num_positions" in columns else "*** MISSING ***"))

    companies = {}
    for row in rows:
        companies.setdefault((row.get("company_name", ""), row.get("website", "")), row)
    print("  companies    : %d" % len(companies))

    statuses = collections.Counter(r.get("job_status", "") or "(blank)"
                                  for r in companies.values())
    print("\n  status per company:")
    for status, count in statuses.most_common():
        print("    %-48s %4d  %5.1f%%"
              % (status, count, 100.0 * count / len(companies)))

    counts = [to_int(r.get("num_positions")) for r in companies.values()]
    positive = sorted(c for c in counts if c > 0)
    print("\n  positions    : total=%d across %d companies"
          % (sum(counts), len(positive)))
    if positive:
        print("    min=%d  median=%d  max=%d  mean=%.1f"
              % (positive[0], positive[len(positive) // 2], positive[-1],
                 sum(positive) / float(len(positive))))
    blank = sum(1 for r in rows if r.get("num_positions", "") == "")
    print("    rows with blank num_positions: %d %s"
          % (blank, "(should be 0)" if blank else "OK"))

    sources = collections.Counter((r.get("source") or "(none)").split(";")[0]
                                  for r in rows if to_int(r.get("num_positions")))
    if sources:
        print("\n  top extraction sources:")
        for source, count in sources.most_common(8):
            print("    %-34s %5d rows" % (source[:34], count))

    methods = collections.Counter(r.get("career_page_discovery_method") or "(none)"
                                  for r in companies.values())
    print("\n  career-page discovery method:")
    for method, count in methods.most_common(8):
        print("    %-40s %4d" % (method[:40], count))

    top = sorted(companies.values(), key=lambda r: -to_int(r.get("num_positions")))
    hits = [r for r in top if to_int(r.get("num_positions"))]
    if hits:
        print("\n  top companies by positions:")
        for row in hits[:10]:
            print("    %-6s %-42s %s"
                  % (row.get("num_positions"), row.get("company_name", "")[:42],
                     (row.get("career_page_url") or "")[:60]))
    else:
        print("\n  no company produced any positions yet")

    return {"rows": len(rows), "companies": len(companies),
            "positions": sum(counts), "statuses": statuses}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=os.path.join(ROOT, "ajay.csv"))
    parser.add_argument("--compare", default="",
                        help="second CSV to diff against (e.g. ajay.csv.prev)")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()

    while True:
        current = summarize(args.file, "CURRENT  ")
        if args.compare:
            baseline_path = args.compare
            if not os.path.isabs(baseline_path):
                baseline_path = os.path.join(ROOT, baseline_path)
            baseline = summarize(baseline_path, "BASELINE ")
            if current and baseline:
                print("\n" + "=" * 74)
                print("DELTA (current - baseline)")
                print("  rows      : %+d" % (current["rows"] - baseline["rows"]))
                print("  companies : %+d" % (current["companies"] - baseline["companies"]))
                print("  positions : %+d" % (current["positions"] - baseline["positions"]))
                keys = set(current["statuses"]) | set(baseline["statuses"])
                for key in sorted(keys):
                    delta = current["statuses"].get(key, 0) - baseline["statuses"].get(key, 0)
                    if delta:
                        print("  %-46s %+d" % (key, delta))
        if not args.watch:
            break
        print("\n(refreshing in %ds -- Ctrl+C to stop)" % args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
