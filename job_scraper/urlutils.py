import re
from urllib.parse import urlparse, urljoin, unquote

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_TRAILING_RE = re.compile(r"[/?#]+$")


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:xn--[a-z0-9\-]{1,59}|[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:xn--[a-z0-9\-]{1,59}|[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?))+$"
)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def looks_like_hostname(host):
    """True only for values that could actually resolve as a DNS name.

    Company names ("ROADS AGENCY LIMPOPO (PTY) LTD"), free text and bare
    words are rejected.  Feeding those into the fetcher is what produced the
    NameResolutionError storm in the smoke test: they were treated as
    websites, failed to resolve, and the company was marked Unreachable
    instead of falling through to web search.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host or len(host) > 253:
        return False
    if _IPV4_RE.match(host):
        return all(0 <= int(part) <= 255 for part in host.split("."))
    if any(ch.isspace() for ch in host):
        return False
    if not host.isascii():
        # Internationalised domain: test the punycode form instead.
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            return False
    if not _HOSTNAME_RE.match(host):
        return False
    tld = host.rsplit(".", 1)[-1]
    if len(tld) < 2 or tld.isdigit():
        return False
    return True


def ensure_https(url):
    url = (url or "").strip().strip("\u200b\u200c\u200d\ufeff")
    if not url:
        return ""
    if not _SCHEME_RE.match(url):
        url = "https://" + url
    return url


def normalize_website(raw):
    """Normalize a website value from the input file to a base https URL (no path)."""
    url = ensure_https(raw)
    if not url:
        return ""
    try:
        p = urlparse(url)
    except Exception:
        return url
    host = (p.hostname or "").lower()
    if not host:
        return ""
    # Reject values that are plainly not hostnames (company names, free text).
    if not looks_like_hostname(host):
        return ""
    if p.port and p.port not in (80, 443):
        host = "%s:%s" % (host, p.port)
    return "https://" + host


def hostname(url):
    try:
        p = urlparse(ensure_https(url))
        return (p.hostname or "").lower()
    except Exception:
        return ""


def domain_slug(url):
    """Derive an alphanumeric slug from the registrable host name."""
    host = hostname(url)
    if not host:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(".")[0]
    host = re.sub(r"[^a-z0-9]", "-", host)
    host = re.sub(r"-+", "-", host).strip("-")
    return host


def slugify(text):
    text = unquote(text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def url_join(base, href):
    if not href:
        return ""
    href = href.strip()
    if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
        return ""
    if href.startswith("//"):
        href = "https:" + href
    return urljoin(ensure_https(base), href)


def clean_url(url):
    url = (url or "").strip()
    url = _TRAILING_RE.sub("", url)
    return url
