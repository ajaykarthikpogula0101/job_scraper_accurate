"""Persistent, Windows-safe browser rendering workers with disk caching."""

import atexit
import asyncio
import hashlib
import logging
import os
import queue
import re
import threading
import time
import sys

from . import netcache
from .config import (
    BASE_DIR,
    BROWSER_LOAD_MORE_CLICKS,
    BROWSER_RENDER_TIMEOUT_MS,
    BROWSER_SETTLE_MS,
    BROWSER_RENDER_FAILURE_TTL,
    BROWSER_RENDER_MAX_ATTEMPTS,
    BROWSER_WORKERS,
    MAX_RETRIES,
)

# Share the DNS cache with the HTTP layer.
netcache.install()


log = logging.getLogger("job_scraper")
_CACHE_DIR = os.path.join(BASE_DIR, ".browser_cache")
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_MEMORY_CACHE = {}
_CACHE_LOCK = threading.Lock()
_WORKERS = []
_WORKERS_LOCK = threading.Lock()
_NEXT_WORKER = 0
# Set when Camoufox cannot be launched at all.  Without this every rendered
# fetch waited out the full timeout, six times over, before giving up.
_BROWSER_UNAVAILABLE = threading.Event()
_BROWSER_UNAVAILABLE_REASON = [""]
_CAMOUFOX_NODE_MODULES = os.environ.get(
    "CAMOUFOX_NODE_MODULES",
    os.path.join(BASE_DIR, "camofox-browser-master", "node_modules"),
)
# "true" | "false" | "virtual".  On a headless Linux VM "virtual" runs a real
# (non-headless) Firefox inside Xvfb, which is markedly harder to fingerprint
# than true headless; it needs xvfb installed.
_CAMOUFOX_HEADLESS = os.environ.get("CAMOUFOX_HEADLESS", "true").strip().lower()
_CAMOUFOX_GEOIP = os.environ.get("CAMOUFOX_GEOIP", "").strip().lower() in ("1", "true", "yes")
_CAMOUFOX_HUMANIZE = os.environ.get("CAMOUFOX_HUMANIZE", "true").strip().lower() not in ("0", "false", "no")
_CAMOUFOX_PROXY = os.environ.get("CAMOUFOX_PROXY", "").strip()
_CAMOUFOX_OS = os.environ.get("CAMOUFOX_OS", "").strip()


# URLs that could not be rendered.  Career-page discovery often reaches the
# same board URL from several candidates, and without this each one paid the
# full render cost again.
_FAILED_RENDERS = {}
_FAILED_LOCK = threading.Lock()


def _render_recently_failed(url):
    with _FAILED_LOCK:
        when = _FAILED_RENDERS.get(url)
        if when is None:
            return False
        if time.time() - when > BROWSER_RENDER_FAILURE_TTL:
            _FAILED_RENDERS.pop(url, None)
            return False
        return True


def _mark_render_failed(url):
    with _FAILED_LOCK:
        if len(_FAILED_RENDERS) > 20000:
            _FAILED_RENDERS.clear()
        _FAILED_RENDERS[url] = time.time()


def _is_navigation_timeout(error):
    text = str(error or "")
    return ("Timeout" in text and "exceeded" in text) or "net::ERR" in text


# Pages served per browser context before it is replaced.
_CONTEXT_RECYCLE_AFTER = int(os.environ.get("CAMOUFOX_CONTEXT_RECYCLE", "200") or 200)

_UNAVAILABLE_LOGGED = threading.Event()


def _warn_unavailable_once():
    if not _UNAVAILABLE_LOGGED.is_set():
        _UNAVAILABLE_LOGGED.set()
        log.warning("Browser rendering is DISABLED for this run: %s",
                    _BROWSER_UNAVAILABLE_REASON[0][:300])


def _headless_value():
    if _CAMOUFOX_HEADLESS in ("virtual", "xvfb"):
        return "virtual"
    return _CAMOUFOX_HEADLESS not in ("0", "false", "no")


def _launch_camoufox():
    """Start a Camoufox browser, whichever binding is installed.

    Preferred: the PyPI `camoufox` package (`camoufox.sync_api.Camoufox`).
    Fallback: the `camoufox_js` binding this file used to import exclusively.
    That fallback never actually resolved -- node_modules/camoufox-js is a pure
    JavaScript package with no importable Python module -- so every rendered
    fetch was silently failing before this change.

    Returns (browser, close_callable, binding_name).
    """
    errors = []

    # ---- 1. PyPI camoufox (Python)
    try:
        from camoufox.sync_api import Camoufox as PyCamoufox
    except Exception as exc:            # ImportError, or a broken install
        PyCamoufox = None
        errors.append("camoufox (python): %s" % exc)

    if PyCamoufox is not None:
        options = {"headless": _headless_value(), "humanize": _CAMOUFOX_HUMANIZE}
        if _CAMOUFOX_GEOIP:
            options["geoip"] = True
        if _CAMOUFOX_PROXY:
            options["proxy"] = {"server": _CAMOUFOX_PROXY}
        if _CAMOUFOX_OS:
            options["os"] = [o.strip() for o in _CAMOUFOX_OS.split(",") if o.strip()]
        launcher = PyCamoufox(**options)
        browser = None
        if hasattr(launcher, "start"):
            browser = launcher.start()
        if browser is None and hasattr(launcher, "__enter__"):
            browser = launcher.__enter__()
        if browser is None:
            browser = launcher

        def close_python():
            for closer in ("stop", "__exit__", "close"):
                fn = getattr(launcher, closer, None)
                if fn is None:
                    continue
                try:
                    fn(None, None, None) if closer == "__exit__" else fn()
                    return
                except Exception:
                    continue
            try:
                browser.close()
            except Exception:
                pass

        return browser, close_python, "camoufox(python)"

    # ---- 2. camoufox_js binding (legacy path)
    if _CAMOUFOX_NODE_MODULES and _CAMOUFOX_NODE_MODULES not in sys.path:
        sys.path.insert(0, _CAMOUFOX_NODE_MODULES)
    try:
        from camoufox_js.sync_api import Camoufox as JsCamoufox
    except Exception as exc:
        errors.append("camoufox_js: %s" % exc)
        raise ImportError(
            "no usable Camoufox binding. Install the Python package with "
            "`pip install \"camoufox[geoip]\"` then `python -m camoufox fetch`. "
            "Tried: " + " | ".join(errors))
    browser = JsCamoufox({"headless": _headless_value() is not False,
                          "user_data_dir": False})
    return browser, getattr(browser, "close", lambda: None), "camoufox_js"


def _cache_path(url):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(_CACHE_DIR, digest + ".html")


def _read_cache(url):
    with _CACHE_LOCK:
        if url in _MEMORY_CACHE:
            return _MEMORY_CACHE[url]
    path = _cache_path(url)
    try:
        if time.time() - os.path.getmtime(path) > _CACHE_TTL_SECONDS:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            html = handle.read()
        with _CACHE_LOCK:
            _MEMORY_CACHE[url] = html
        return html
    except (OSError, UnicodeError):
        return None


_CACHE_PRUNE_EVERY = int(os.environ.get("CAMOUFOX_CACHE_PRUNE_EVERY", "500") or 500)
_CACHE_MAX_BYTES = int(os.environ.get("CAMOUFOX_CACHE_MAX_BYTES", str(5 * 1024 ** 3)) or 5 * 1024 ** 3)
_WRITES_SINCE_PRUNE = [0]


def _prune_cache():
    """Delete expired, then oldest, rendered pages.

    The TTL was only checked when a page was *read*, so stale files were never
    removed.  Over a multi-week run across 405k companies this directory grows
    without limit and eventually fills the VM's disk.
    """
    try:
        entries = []
        total = 0
        now = time.time()
        with os.scandir(_CACHE_DIR) as scanner:
            for entry in scanner:
                if not entry.name.endswith(".html"):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                if now - stat.st_mtime > _CACHE_TTL_SECONDS:
                    try:
                        os.unlink(entry.path)
                    except OSError:
                        pass
                    continue
                entries.append((stat.st_mtime, stat.st_size, entry.path))
                total += stat.st_size
        if total <= _CACHE_MAX_BYTES:
            return
        entries.sort()                      # oldest first
        for _mtime, size, path in entries:
            if total <= _CACHE_MAX_BYTES:
                break
            try:
                os.unlink(path)
                total -= size
            except OSError:
                pass
        log.info("Pruned rendered-page cache to %.1f GB", total / 1024.0 ** 3)
    except (OSError, FileNotFoundError):
        pass


def _write_cache(url, html):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(url)
        temporary = path + ".tmp-%s" % threading.get_ident()
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write(html)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        with _CACHE_LOCK:
            _WRITES_SINCE_PRUNE[0] += 1
            due_for_prune = _WRITES_SINCE_PRUNE[0] >= _CACHE_PRUNE_EVERY
            if due_for_prune:
                _WRITES_SINCE_PRUNE[0] = 0
        if due_for_prune:
            _prune_cache()
        with _CACHE_LOCK:
            if len(_MEMORY_CACHE) >= 500:
                _MEMORY_CACHE.pop(next(iter(_MEMORY_CACHE)))
            _MEMORY_CACHE[url] = html
    except OSError as exc:
        log.warning("Could not cache rendered page %s: %s", url, str(exc)[:150])


_LOAD_MORE_PATTERN = re.compile(
    # "indlæs" (Danish) was stored double-encoded as "indlÃ¦s", so Danish
    # "indlæs mere" buttons never matched.
    r"^\s*(load|show|view|see|more|indlæs|vis|mehr|weitere|charger|cargar|"
    r"mostrar|carregar)\s*(more|flere|mere|jobs?|positions?|openings?|"
    r"stellen|ergebnisse)?\s*(jobs?|positions?|openings?|results?)?\s*$",
    re.I,
)
# Total wall-clock budget for expanding one page, regardless of click count.
_LOAD_MORE_BUDGET_SECONDS = 30.0


def _click_load_more(page, request):
    """Click "load more" until the page stops growing.

    Every failure mode here is non-fatal: the caller has already captured the
    unexpanded HTML.  Glassdoor-style pages where the button matches but is
    never clickable used to raise Locator.click timeouts that destroyed the
    whole render.
    """
    deadline = time.time() + _LOAD_MORE_BUDGET_SECONDS
    for _ in range(BROWSER_LOAD_MORE_CLICKS):
        if time.time() > deadline:
            log.debug("load-more budget exhausted for %s", request.url)
            return
        try:
            candidates = page.get_by_role("button", name=_LOAD_MORE_PATTERN)
            if candidates.count() == 0:
                # Some sites use links rather than buttons.
                candidates = page.get_by_role("link", name=_LOAD_MORE_PATTERN)
                if candidates.count() == 0:
                    return
            button = candidates.first
            if not button.is_visible() or not button.is_enabled():
                return
            before = len(page.content())
            button.click(timeout=4000)
            page.wait_for_timeout(800)
            if len(page.content()) <= before:
                return
        except Exception as exc:
            # Not clickable, detached, obscured by a consent overlay, ...
            log.debug("load-more click stopped for %s: %s",
                      request.url, str(exc).splitlines()[0][:120])
            return


class _RenderRequest:
    def __init__(self, url, timeout_ms, settle_ms):
        self.url = url
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self.html = None
        self.error = None
        self.done = threading.Event()


class _BrowserWorker:
    def __init__(self, number):
        self.number = number
        self.requests = queue.Queue()
        self.dead = threading.Event()
        self.thread = threading.Thread(target=self._run, name="browser-render-%d" % number,
                                       daemon=True)
        self.thread.start()

    @property
    def alive(self):
        return not self.dead.is_set() and self.thread.is_alive()

    def submit(self, request):
        if self.dead.is_set():
            request.error = RuntimeError("browser worker %d is dead" % self.number)
            request.done.set()
            return False
        self.requests.put(request)
        return True

    def stop(self):
        self.requests.put(None)

    def _run(self):
        if os.name == "nt" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        browser = None
        close_browser = None
        try:
            browser, close_browser, binding = _launch_camoufox()
            log.info("Browser worker %d started via %s (headless=%s)",
                     self.number, binding, _headless_value())
        except ImportError as exc:
            message = str(exc)
            _BROWSER_UNAVAILABLE_REASON[0] = message
            _BROWSER_UNAVAILABLE.set()
            self.dead.set()
            self._fail_pending(message)
            return
        except Exception as exc:
            message = "Camoufox failed to launch: %s" % exc
            log.warning("Browser worker %d: %s", self.number, message[:250])
            _BROWSER_UNAVAILABLE_REASON[0] = message
            self.dead.set()
            self._fail_pending(message)
            return
        try:
            context = browser.new_context()
            pages_served = 0
            while True:
                request = self.requests.get()
                if request is None:
                    break
                # Recycle the context periodically.  A single long-lived
                # Firefox context accumulates cookies, storage and cache
                # across thousands of pages; over a multi-day run that is a
                # steady memory climb on the VM.
                if pages_served >= _CONTEXT_RECYCLE_AFTER:
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        context = browser.new_context()
                        log.info("Browser worker %d recycled its context after %d pages",
                                 self.number, pages_served)
                    except Exception as exc:
                        log.warning("Browser worker %d could not recycle context: %s",
                                    self.number, str(exc)[:150])
                        self.dead.set()
                        request.error = RuntimeError("context recycle failed")
                        request.done.set()
                        break
                    pages_served = 0
                pages_served += 1
                page = None
                try:
                    page = context.new_page()
                    response = page.goto(request.url, wait_until="domcontentloaded",
                                         timeout=request.timeout_ms)
                    page.wait_for_timeout(request.settle_ms)

                    # Capture the page BEFORE trying to expand it.  A failed
                    # "load more" click used to raise and discard the whole
                    # rendered page, then the caller retried the render six
                    # times -- minutes wasted per URL for a page we already had.
                    if response is not None and response.status < 400:
                        request.html = page.content()

                    _click_load_more(page, request)

                    if response is not None and response.status < 400:
                        try:
                            expanded = page.content()
                            if expanded and len(expanded) >= len(request.html or ""):
                                request.html = expanded
                        except Exception:
                            pass
                except Exception as exc:
                    # Only a genuine navigation failure counts as an error; if
                    # we already captured HTML, keep it.
                    if not request.html:
                        request.error = exc
                    else:
                        log.debug("Render of %s partially failed but HTML kept: %s",
                                  request.url, str(exc)[:150])
                finally:
                    if page:
                        try:
                            page.close()
                        except Exception:
                            pass
                    request.done.set()
        except Exception as exc:
            log.warning("Browser worker %d failed: %s", self.number, str(exc)[:200])
            self.dead.set()
            self._fail_pending(str(exc))
        finally:
            self.dead.set()
            if close_browser:
                try:
                    close_browser()
                except Exception:
                    pass

    def _fail_pending(self, message):
        while True:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                return
            if request is not None:
                request.error = RuntimeError(message)
                request.done.set()


def _ensure_workers():
    """Return live workers, replacing any that have died.

    A worker whose Camoufox process crashed used to leave requests queued
    forever; they are now respawned, and a hard import failure short-circuits
    rendering entirely instead of timing out repeatedly.
    """
    global _WORKERS
    if _BROWSER_UNAVAILABLE.is_set():
        return []
    with _WORKERS_LOCK:
        _WORKERS = [w for w in _WORKERS if w.alive]
        missing = max(1, BROWSER_WORKERS) - len(_WORKERS)
        for _ in range(missing):
            _WORKERS.append(_BrowserWorker(len(_WORKERS) + 1))
        return list(_WORKERS)


def fetch_rendered_html(url, timeout_ms=BROWSER_RENDER_TIMEOUT_MS,
                        settle_ms=BROWSER_SETTLE_MS):
    global _NEXT_WORKER

    # Check cache first
    cached = _read_cache(url)
    if cached is not None:
        return cached

    if _BROWSER_UNAVAILABLE.is_set():
        return None
    if _render_recently_failed(url):
        log.debug("Skipping known-unrenderable URL %s", url)
        return None

    # Retry with exponential backoff (netcache.backoff_sleep).
    max_retries = max(0, BROWSER_RENDER_MAX_ATTEMPTS - 1)
    for attempt in range(max_retries + 1):
        try:
            workers = _ensure_workers()
            if not workers:
                _warn_unavailable_once()
                return None
            with _WORKERS_LOCK:
                worker = workers[_NEXT_WORKER % len(workers)]
                _NEXT_WORKER += 1

            request = _RenderRequest(url, timeout_ms, settle_ms)
            if not worker.submit(request):
                continue

            # Wait for completion with timeout
            if not request.done.wait((timeout_ms + settle_ms + 15000) / 1000.0):
                log.warning("Browser rendering timed out for %s (attempt %d/%d)", url, attempt + 1, max_retries + 1)
                _mark_render_failed(url)
                if attempt < max_retries:
                    netcache.backoff_sleep(attempt)
                    continue
                else:
                    return None

            if request.error:
                if _BROWSER_UNAVAILABLE.is_set():
                    # No binding at all -- retrying cannot help.  Log once at
                    # debug level so the run is not flooded.
                    log.debug("Browser rendering unavailable for %s: %s",
                              url, str(request.error)[:200])
                    return None
                if _is_navigation_timeout(request.error):
                    # The page will not load.  Retrying costs another full
                    # timeout and blocks a worker the whole time.
                    log.warning("Page will not load, giving up on %s: %s",
                                url, str(request.error).splitlines()[0][:160])
                    _mark_render_failed(url)
                    return None
                log.warning("Browser rendering failed for %s: %s (attempt %d/%d)", url, str(request.error)[:200], attempt + 1, max_retries + 1)
                _mark_render_failed(url)
                if attempt < max_retries:
                    netcache.backoff_sleep(attempt)
                    continue
                else:
                    return None

            if request.html:
                _write_cache(url, request.html)
                return request.html

        except Exception as exc:
            log.warning("Unexpected error in browser rendering for %s: %s (attempt %d/%d)", url, str(exc)[:200], attempt + 1, max_retries + 1)
            if attempt < max_retries:
                netcache.backoff_sleep(attempt)
                continue
            else:
                return None

    return None


def close_browser_workers():
    with _WORKERS_LOCK:
        for worker in _WORKERS:
            worker.stop()


atexit.register(close_browser_workers)

