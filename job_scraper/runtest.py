"""Backward-compatible entry point.

The previous version did `from pipeline import run` after chdir'ing into this
directory, which cannot work: pipeline.py uses package-relative imports.  The
smoke test now lives in run_smoke_test.py at the project root; this shim keeps
`python job_scraper/runtest.py` working.
"""

import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "run_smoke_test.py")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if not os.path.exists(RUNNER):
    raise SystemExit("run_smoke_test.py not found at %s" % RUNNER)

print("Delegating to %s" % RUNNER)
runpy.run_path(RUNNER, run_name="__main__")
