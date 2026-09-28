"""Leadership-title criteria shared by the scraper and the delivery export.

This is the same rule set as export.py's DuckDB filter, expressed once in
Python so that (a) ATS detail enrichment can fetch descriptions for the
titles the client actually receives before spending its budget on the rest,
and (b) the delivery file is filtered with exactly the same definition.
"""
import re

_SENIOR = r"(?:chief|director|vice\s+president|vp|svp|evp|avp|head\s+of|general\s+manager|senior\s+manager|principal|partner)"

_PATTERNS = [
    # C-suite / owner
    re.compile(r"\b(ceo|cfo|coo|cto|cio|cmo|chro|ciso|cro|cpo|cdo)\b", re.I),
    re.compile(r"\bchief\s+[a-z]+(\s+[a-z]+)?\s+officer\b", re.I),
    re.compile(r"\b(president|founder|co-founder|owner|proprietor|managing\s+director|managing\s+partner)\b", re.I),
    # Director / VP / Head-of
    re.compile(r"\b(director|vice\s+president|vp|svp|evp|avp|head\s+of|general\s+manager)\b", re.I),
    # chief-level IT
    re.compile(r"\bchief\s+(technolog[a-z]*|information|digital|data|technical|security|innovation|product|ai|analytics)\s+(and\s+[a-z]+\s+)?officer\b", re.I),
]
_PAIRED = [
    (re.compile(r"\b(financ[a-z]*|account[a-z]*|controller|treasur[a-z]*|audit[a-z]*|tax|bookkeep[a-z]*|payroll|fp&a)\b", re.I),
     re.compile(r"\b" + _SENIOR + r"\b", re.I)),
    (re.compile(r"\b(operations?|operational|ops)\b", re.I),
     re.compile(r"\b" + _SENIOR + r"\b", re.I)),
    (re.compile(r"\b(procurement|purchas[a-z]*|sourcing|supply\s+chain|buyer|vendor\s+manage[a-z]*|category\s+manager)\b", re.I),
     re.compile(r"\b" + _SENIOR + r"\b", re.I)),
]


# "Business Process Owner", "Product Owner", "Data Owner" are individual
# contributor roles that the bare word "owner" would otherwise pull in.
_NOT_OWNER = re.compile(
    r"\b(?:process|product|service|data|risk|system|application|platform|feature|"
    r"epic|story|control|capability|workflow|business\s+process|technical|solution)\s+owner\b", re.I)


def is_leadership_title(title):
    title = (title or "").strip()
    if not title:
        return False
    if _NOT_OWNER.search(title):
        title = _NOT_OWNER.sub(" ", title)
    for pattern in _PATTERNS:
        if pattern.search(title):
            return True
    for subject, level in _PAIRED:
        if subject.search(title) and level.search(title):
            return True
    return False


def prioritize(items, title_of):
    """Stable reorder: leadership titles first, everything else after.

    ``title_of`` extracts the title from one item, so the same helper serves
    a list of job dicts and a list of (job, path) pairs.
    """
    first, rest = [], []
    for item in items:
        (first if is_leadership_title(title_of(item)) else rest).append(item)
    return first + rest
