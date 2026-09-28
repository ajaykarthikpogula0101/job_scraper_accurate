"""DNS caching, retrying resolution, and shared retry/backoff helpers.

Two problems in the smoke test come from here:

1.  Company names were being fed in as hostnames, so every lookup raised
    NameResolutionError.  Those failures were retried by urllib3 five times
    each, per candidate URL -- minutes of wall clock per company for a name
    that can never resolve.  A negative cache makes the second and later
    lookups free.
2.  Genuine transient DNS failures (resolver rate limiting, UDP loss) were
    treated as "site does not exist".  Resolution is now retried with
    exponential backoff, and optionally against public resolvers via
    dnspython when the system resolver keeps failing.

`install()` monkeypatches socket.getaddrinfo, so requests, urllib3 and any
other library in the process share the cache.  It is idempotent.
"""

import logging
import random
import socket
import threading
import time

from .config import (
    DNS_CACHE_TTL,
    DNS_FALLBACK_SERVERS,
    DNS_MAX_ATTEMPTS,
    DNS_NEGATIVE_CACHE_TTL,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
)

log = logging.getLogger("job_scraper")

_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_CACHE = {}
_NEGATIVE = {}
_LOCK = threading.RLock()
_INSTALLED = False
_STATS = {"hits": 0, "misses": 0, "negative_hits": 0, "retries": 0,
          "fallback_ok": 0, "failures": 0}


def backoff_sleep(attempt, base=None, cap=None):
    """Sleep for base * 2**attempt seconds with jitter, capped."""
    base = RETRY_BACKOFF_BASE if base is None else base
    cap = RETRY_BACKOFF_MAX if cap is None else cap
    delay = min(base * (2 ** attempt), cap)
    time.sleep(delay + random.uniform(0, min(1.0, delay / 2 or 0.25)))


def is_dns_error(exc):
    text = str(exc).lower()
    if isinstance(exc, socket.gaierror):
        return True
    return ("nameresolutionerror" in text or "name or service not known" in text
            or "temporary failure in name resolution" in text
            or "failed to resolve" in text or "getaddrinfo failed" in text
            or "nodename nor servname" in text)


def is_retryable_network_error(exc):
    text = str(exc).lower()
    return bool(is_dns_error(exc) or "connecttimeout" in text or "read timed out" in text
                or "readtimeout" in text or "connection reset" in text
                or "connection aborted" in text or "connectionerror" in text
                or "max retries exceeded" in text or "remote end closed" in text
                or "timed out" in text or "temporarily unavailable" in text
                or "ssl" in text and "handshake" in text)


def stats():
    with _LOCK:
        return dict(_STATS)


def _cache_get(key):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            _STATS["hits"] += 1
            return hit[1], None
        if hit:
            _CACHE.pop(key, None)
        miss = _NEGATIVE.get(key)
        if miss and miss[0] > now:
            _STATS["negative_hits"] += 1
            return None, miss[1]
        if miss:
            _NEGATIVE.pop(key, None)
    return None, None


def _cache_put(key, value=None, error=None):
    expiry_positive = time.time() + DNS_CACHE_TTL
    expiry_negative = time.time() + DNS_NEGATIVE_CACHE_TTL
    with _LOCK:
        if error is None:
            if len(_CACHE) > 20000:
                _CACHE.clear()
            _CACHE[key] = (expiry_positive, value)
            _NEGATIVE.pop(key, None)
        else:
            if len(_NEGATIVE) > 20000:
                _NEGATIVE.clear()
            _NEGATIVE[key] = (expiry_negative, error)


def _looks_resolvable(host):
    """Cheap sanity check: a hostname with spaces or no dot can never resolve."""
    if not host or not isinstance(host, str):
        return False
    if " " in host or "(" in host or ")" in host or "," in host:
        return False
    if len(host) > 253:
        return False
    if host in ("localhost",):
        return True
    return "." in host


def _resolve_with_dnspython(host, port, family, type_, proto, flags):
    """Ask public resolvers directly.  Silently unavailable without dnspython."""
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return None
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = list(DNS_FALLBACK_SERVERS)
    resolver.lifetime = 8.0
    resolver.timeout = 4.0
    addresses = []
    for rdtype, af in (("A", socket.AF_INET), ("AAAA", socket.AF_INET6)):
        if family not in (socket.AF_UNSPEC, af):
            continue
        try:
            answer = resolver.resolve(host, rdtype)
        except Exception:
            continue
        for record in answer:
            addresses.append((af, record.to_text()))
    if not addresses:
        return None
    out = []
    for af, address in addresses:
        sockaddr = (address, port) if af == socket.AF_INET else (address, port, 0, 0)
        out.append((af, type_ or socket.SOCK_STREAM, proto or 6, "", sockaddr))
    return out


def resolve(host, port=0, family=0, type=0, proto=0, flags=0):
    """Cached, retrying getaddrinfo replacement."""
    if not isinstance(host, str) or not host:
        return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)

    key = (host.lower(), port, family, type, proto, flags)
    cached, cached_error = _cache_get(key)
    if cached is not None:
        return cached
    if cached_error is not None:
        raise socket.gaierror(socket.EAI_NONAME, cached_error)

    if not _looks_resolvable(host):
        message = "not a resolvable hostname: %r" % host
        _cache_put(key, error=message)
        raise socket.gaierror(socket.EAI_NONAME, message)

    with _LOCK:
        _STATS["misses"] += 1

    last_error = None
    for attempt in range(max(1, DNS_MAX_ATTEMPTS)):
        try:
            result = _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
            if result:
                _cache_put(key, value=result)
                return result
            last_error = socket.gaierror(socket.EAI_NONAME, "empty getaddrinfo result")
        except socket.gaierror as exc:
            last_error = exc
        except OSError as exc:
            last_error = exc
        if attempt < DNS_MAX_ATTEMPTS - 1:
            with _LOCK:
                _STATS["retries"] += 1
            backoff_sleep(attempt, base=0.5, cap=6.0)

    fallback = None
    try:
        fallback = _resolve_with_dnspython(host, port, family, type, proto, flags)
    except Exception:
        fallback = None
    if fallback:
        with _LOCK:
            _STATS["fallback_ok"] += 1
        _cache_put(key, value=fallback)
        return fallback

    with _LOCK:
        _STATS["failures"] += 1
    message = str(last_error) or "dns resolution failed for %s" % host
    _cache_put(key, error=message)
    raise socket.gaierror(socket.EAI_NONAME, message)


def install():
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return False
        socket.getaddrinfo = resolve
        _INSTALLED = True
    log.info("DNS cache installed (ttl=%ds negative_ttl=%ds attempts=%d)",
             DNS_CACHE_TTL, DNS_NEGATIVE_CACHE_TTL, DNS_MAX_ATTEMPTS)
    return True


def uninstall():
    global _INSTALLED
    with _LOCK:
        socket.getaddrinfo = _ORIGINAL_GETADDRINFO
        _INSTALLED = False
