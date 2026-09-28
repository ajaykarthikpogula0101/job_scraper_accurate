import logging
import re
import threading
import time

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import netcache
from .config import (
    ALLOW_INSECURE_SSL,
    CONNECT_TIMEOUT,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    REQUEST_HEADERS,
    RETRY_ON_STATUS,
)
from .urlutils import hostname

log = logging.getLogger("job_scraper")

if ALLOW_INSECURE_SSL:
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

# Install the DNS cache as soon as any session module is imported so that
# worker threads created later all share it.
netcache.install()

# Hosts that have hard-failed DNS/connection often enough that further
# attempts only waste time.  Shared process-wide, reset per run.
_DEAD_HOSTS = {}
_DEAD_HOSTS_LOCK = threading.Lock()
# Hosts whose TLS certificate failed verification.  A cert error is permanent,
# but urllib3 retried it five times with backoff (~25s per host) before the
# unverified retry was even attempted.  Remembering them makes the second and
# later requests go straight to verify=False.
_INSECURE_HOSTS = set()
_DEAD_HOST_THRESHOLD = 3
_DEAD_HOST_TTL = 900


def _host_is_dead(host):
    if not host:
        return False
    with _DEAD_HOSTS_LOCK:
        entry = _DEAD_HOSTS.get(host)
        if not entry:
            return False
        count, last = entry
        if time.time() - last > _DEAD_HOST_TTL:
            _DEAD_HOSTS.pop(host, None)
            return False
        return count >= _DEAD_HOST_THRESHOLD


_HOST_MEMO_LIMIT = 50000


def _mark_host_failure(host):
    if not host:
        return
    with _DEAD_HOSTS_LOCK:
        # Bounded: across 405k companies these dicts would otherwise grow for
        # the entire run and never shrink.
        if len(_DEAD_HOSTS) > _HOST_MEMO_LIMIT:
            cutoff = time.time() - _DEAD_HOST_TTL
            for key in [k for k, v in _DEAD_HOSTS.items() if v[1] < cutoff]:
                _DEAD_HOSTS.pop(key, None)
            if len(_DEAD_HOSTS) > _HOST_MEMO_LIMIT:
                _DEAD_HOSTS.clear()
        count, _ = _DEAD_HOSTS.get(host, (0, 0))
        _DEAD_HOSTS[host] = (count + 1, time.time())


def _mark_host_success(host):
    if not host:
        return
    with _DEAD_HOSTS_LOCK:
        _DEAD_HOSTS.pop(host, None)


def reset_host_failures():
    with _DEAD_HOSTS_LOCK:
        _DEAD_HOSTS.clear()
        _INSECURE_HOSTS.clear()


def _looks_like_javascript_shell(html_text):
    if not html_text:
        return True
    soup = BeautifulSoup(html_text, "html.parser")
    visible = soup.get_text(" ", strip=True)
    anchors = soup.find_all("a", href=True, limit=2)
    scripts = soup.find_all("script", limit=10)
    app_root = soup.find(id=re.compile(r"^(app|root|__next)$", re.I))
    return bool((len(visible) < 200 and scripts) or
                (app_root is not None and len(visible) < 500 and not anchors))


class ScrapeSession:
    """Thread-safe HTTP session factory with DNS caching and layered retries.

    Two retry layers on purpose:

    * urllib3 ``Retry`` handles the cheap cases inside a single call
      (idempotent GET/HEAD, retryable status codes, connect resets).
    * an explicit loop in :meth:`fetch` handles the cases urllib3 gives up on
      -- DNS failures and connect timeouts -- with exponential backoff, since
      those are the two errors that dominated the smoke-test log.
    """

    def __init__(self, timeout=None, max_retries=None):
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else MAX_RETRIES
        self._local = threading.local()

    def _get_session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            retry_kwargs = dict(
                # fetch() runs its own retry loop with backoff and an
                # unverified-TLS fallback, so urllib3's budget stays small.
                # A TLS handshake error is classified as "other" and was
                # burning the full total (5 attempts) before we saw it.
                total=2,
                other=0,
                # Connect failures (a bad certificate among them) are retried
                # by the loop in fetch(), which can also fall back to an
                # unverified request.  Retrying here as well multiplied the
                # cost of every permanently-broken host.
                connect=1,
                # Read timeouts are retried by fetch() too; five retries at a
                # 40s timeout meant 200s+ on a single unresponsive host.
                read=2,
                status=self.max_retries,
                backoff_factor=0.8,
                status_forcelist=RETRY_ON_STATUS,
                allowed_methods=frozenset(["GET", "HEAD"]),
                respect_retry_after_header=True,
                raise_on_status=False,
            )
            try:
                # urllib3 >= 2.0
                retry = Retry(backoff_max=20, **retry_kwargs)
            except TypeError:
                # urllib3 1.x exposes the cap as a class attribute instead.
                Retry.DEFAULT_BACKOFF_MAX = 20
                retry = Retry(**retry_kwargs)
            adapter = HTTPAdapter(
                max_retries=retry, pool_connections=16, pool_maxsize=32
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers.update(REQUEST_HEADERS)
            self._local.session = s
        return s

    def _timeout_pair(self, timeout):
        """requests accepts (connect, read).  Callers pass a single number."""
        value = self.timeout if timeout is None else timeout
        if isinstance(value, (tuple, list)):
            return tuple(value)
        try:
            read = float(value)
        except (TypeError, ValueError):
            read = float(DEFAULT_TIMEOUT)
        return (min(CONNECT_TIMEOUT, read), read)

    def fetch(self, url, timeout=None, method="GET", retries=None, **kwargs):
        """GET (or POST) a URL and return the response, or None on failure.

        Retries DNS and connection failures with exponential backoff.
        """
        host = hostname(url)
        if _host_is_dead(host):
            return None
        attempts = self.max_retries if retries is None else retries
        pair = self._timeout_pair(timeout)
        if ALLOW_INSECURE_SSL and host and "verify" not in kwargs:
            with _DEAD_HOSTS_LOCK:
                known_bad_cert = host in _INSECURE_HOSTS
            if known_bad_cert:
                kwargs["verify"] = False
        last_exc = None
        for attempt in range(max(1, attempts + 1)):
            try:
                s = self._get_session()
                if method.upper() == "POST":
                    response = s.post(url, timeout=pair, **kwargs)
                else:
                    response = s.get(url, timeout=pair, **kwargs)
                _mark_host_success(host)
                return response
            except requests.exceptions.SSLError as exc:
                last_exc = exc
                # Expired / self-signed / hostname-mismatch certificate.  Retry
                # once unverified rather than losing the page entirely.
                if ALLOW_INSECURE_SSL and kwargs.get("verify", True) is not False:
                    with _DEAD_HOSTS_LOCK:
                        if len(_INSECURE_HOSTS) > _HOST_MEMO_LIMIT:
                            _INSECURE_HOSTS.clear()
                        _INSECURE_HOSTS.add(host)
                    log.debug("TLS verification failed for %s (%s); retrying unverified",
                              url, str(exc)[:100])
                    insecure = dict(kwargs)
                    insecure["verify"] = False
                    try:
                        s = self._get_session()
                        if method.upper() == "POST":
                            response = s.post(url, timeout=pair, **insecure)
                        else:
                            response = s.get(url, timeout=pair, **insecure)
                        _mark_host_success(host)
                        return response
                    except requests.exceptions.RequestException as exc2:
                        last_exc = exc2
                if attempt < attempts and netcache.is_retryable_network_error(last_exc):
                    netcache.backoff_sleep(attempt)
                    continue
                _mark_host_failure(host)
                return None
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if not netcache.is_retryable_network_error(exc):
                    return None
                if netcache.is_dns_error(exc):
                    # netcache already retried and negative-cached the name;
                    # another round trip here cannot succeed.
                    _mark_host_failure(host)
                    log.debug("DNS failure for %s: %s", url, str(exc)[:120])
                    return None
                if attempt < attempts:
                    netcache.backoff_sleep(attempt)
                    continue
        if last_exc is not None:
            _mark_host_failure(host)
            log.debug("Giving up on %s after %d attempts: %s",
                      url, attempts + 1, str(last_exc)[:150])
        return None

    def fetch_text(self, url, timeout=None, **kwargs):
        r = self.fetch(url, timeout=timeout, **kwargs)
        if r is None:
            return None
        if r.status_code not in (200, 201, 203):
            if r.status_code in (403, 405, 406, 423, 429, 451, 455):
                from .browser_fetch import fetch_rendered_html
                return fetch_rendered_html(url)
            return None
        ctype = r.headers.get("Content-Type", "").lower()
        if "json" in ctype:
            return r.text
        if "html" in ctype and _looks_like_javascript_shell(r.text):
            from .browser_fetch import fetch_rendered_html
            rendered = fetch_rendered_html(r.url or url)
            if rendered:
                return rendered
        return r.text

    def fetch_json(self, url, timeout=None, **kwargs):
        r = self.fetch(url, timeout=timeout, **kwargs)
        if r is None:
            return None
        try:
            return r.json()
        except Exception:
            return None

    def post_json(self, url, payload, timeout=None):
        r = self.fetch(url, timeout=timeout, method="POST", json=payload)
        if r is None:
            return None
        try:
            return r.json()
        except Exception:
            return None

    def head_ok(self, url, timeout=12):
        r = self.fetch(url, timeout=timeout, method="HEAD", allow_redirects=True)
        if r is None:
            return False
        return r.status_code < 400
