import re
from datetime import datetime, timedelta, timezone

_WS_RE = re.compile(r"\s+")


def clean_text(value, max_len=20000):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\x00", "")
    value = _WS_RE.sub(" ", value).strip()
    if max_len and len(value) > max_len:
        value = value[:max_len].rstrip()
    return value


def to_list(value):
    """Normalize a value (string, list, comma separated) to a clean list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    elif isinstance(value, (int, float)):
        items = [str(value)]
    else:
        parts = str(value)
        parts = re.split(r"[,;\n|]", parts)
        items = parts
    out = []
    for it in items:
        it = clean_text(it, max_len=500)
        if it:
            out.append(it)
    return out


def join_list(value, sep="; "):
    return sep.join(to_list(value))


def parse_date(value):
    """Return an ISO date string (YYYY-MM-DD) from various formats, else ''."""
    if value is None:
        return ""
    if isinstance(value, (datetime,)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)):
        # epoch milliseconds or seconds
        try:
            if value > 10**11:
                value = value / 1000.0
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return ""
    text = clean_text(value, max_len=200)
    if not text:
        return ""
    # Relative phrasing.  Workday's listing API returns "Posted Today",
    # "Posted Yesterday", "Posted 30+ Days Ago" -- previously all discarded.
    low = text.lower()
    if re.search(r"\b(today|just posted|posted today)\b", low):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "yesterday" in low:
        return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*\+?\s*(minute|hour|day|week|month|year)s?\s*ago", low)
    if not m:
        m = re.search(r"posted\s+(\d+)\s*\+?\s*(minute|hour|day|week|month|year)s?", low)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        days = {"minute": 0, "hour": 0, "day": 1, "week": 7,
                "month": 30, "year": 365}[unit] * amount
        try:
            return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        except Exception:
            return ""
    # ISO / with time
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
        except Exception:
            return ""
    # dd/mm/yyyy or dd.mm.yyyy
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", text)
    if m:
        try:
            return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
        except Exception:
            return ""
    # mm/dd/yyyy
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", text)
    if m:
        try:
            return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
        except Exception:
            return ""
    # Mon DD, YYYY
    m = re.search(
        r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", text
    )
    if m:
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
            "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
            "november": 11, "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
            "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        mon = months.get(m.group(1).lower()[:3])
        if mon:
            try:
                return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(2)))
            except Exception:
                return ""
    # Mon DD HH:MM:SS TZ YYYY   (SuccessFactors datePosted)
    m = re.search(
        r"([A-Za-z]{3})\s+(\d{1,2})\s+\d{1,2}:\d{2}:\d{2}\s+[A-Z]{2,4}\s+(\d{4})", text
    )
    if m:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        mon = months.get(m.group(1).lower()[:3])
        if mon:
            try:
                return "%s-%02d-%02d" % (m.group(3), mon, int(m.group(2)))
            except Exception:
                return ""
    return ""


def parse_decimal(value):
    text = clean_text(value, max_len=50)
    if not text:
        return ""
    m = re.search(r"-?\d[\d,]*\.?\d*", text.replace("\u00a0", " "))
    if not m:
        return ""
    num = m.group(0)
    sign = "-" if num.startswith("-") else ""
    unsigned = num.lstrip("-")
    if "," in unsigned and "." in unsigned:
        if unsigned.rfind(",") > unsigned.rfind("."):
            unsigned = unsigned.replace(".", "").replace(",", ".")
        else:
            unsigned = unsigned.replace(",", "")
    elif "," in unsigned:
        parts = unsigned.split(",")
        unsigned = "".join(parts) if all(len(part) == 3 for part in parts[1:]) else unsigned.replace(",", ".")
    elif "." in unsigned:
        parts = unsigned.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            unsigned = "".join(parts)
    num = sign + unsigned
    try:
        f = float(num)
        if f == int(f):
            return str(int(f))
        return str(f)
    except Exception:
        return ""


def currency_from_salary(value):
    text = clean_text(value, max_len=500)
    for cur in ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "CHF", "JPY", "CNY",
                "SEK", "NOK", "DKK", "PLN", "BRL", "HKD", "SGD", "NZD", "ZAR",
                "CZK", "HUF", "MXN"]:
        if cur in text.upper():
            return cur
    m = re.search(r"[€$£¥₹]\s?", text)
    if m:
        return {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "₹": "INR"}[m.group(0)[0]]
    return ""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LABELED_RE = {
    "employment_type": re.compile(
        r"(?:employment\s*type|job\s*type|type\s*of\s*employment|work\s*type|"
        r"employment\s*tag|schedule)\s*[:\-]\s*([^|\n\r]+?)(?=\s*(?:career\s*status"
        r"|requisition|expected\s*travel|additional\s*locations|job\s*id|location"
        r"|salary|compensation|education|qualification|experience|remote|#|$))",
        re.IGNORECASE,
    ),
    "seniority_level": re.compile(
        r"(?:career\s*status|seniority(?:\s*level)?|level|experience\s*level|"
        r"career\s*level)\s*[:\-]\s*([^|\n\r]+?)(?=\s*(?:requisition|expected\s*"
        r"travel|additional\s*locations|employment\s*type|job\s*id|location|#|$))",
        re.IGNORECASE,
    ),
    "education_qualification": re.compile(
        r"(?:education(?:al)?\s*(?:qualification|requirement|level|type)?|qualification|"
        r"degree\s*(?:requirement|level)?|minimum\s*qualification)\s*[:\-]\s*([^|\n\r]+?)"
        r"(?=\s*(?:requisition|experience|skills|job\s*id|location|#|$))",
        re.IGNORECASE,
    ),
    "years_of_experience": re.compile(
        r"(?:years?\s*of\s*experience|experience\s*(?:required)?|minimum\s*experience)"
        r"\s*[:\-]\s*([^|\n\r]+?)(?=\s*(?:requisition|education|skills|job\s*id|#|$))",
        re.IGNORECASE,
    ),
    "salary": re.compile(
        r"\b(?:salary|compensation|pay\s*range|annual\s*salary|pay|gehalt|vergütung)\b"
        r"\s*[:\-]\s*([^|\n\r]+?)"
        r"(?=\s*(?:requisition|experience|education|job\s*id|#|$))",
        re.IGNORECASE,
    ),
}

_SALARY_CURRENCIES = ["usd", "eur", "gbp", "inr", "aud", "cad", "chf", "jpy", "cn", "sek",
                      "nok", "dkk", "pln", "brl", "$", "€", "£", "₹", "¥"]


def extract_labeled_fields(text):
    """Extract explicitly-labeled fields from a job description text.

    Only captures fields the posting itself states (e.g. 'Employment Type: Full Time').
    Returns a dict of column -> clean value.
    """
    out = {}
    if not text:
        return out
    for key, pat in _LABELED_RE.items():
        m = pat.search(text)
        if not m:
            continue
        val = m.group(1)
        val = re.split(r"(?:\r?\n|•|\|\||:)", val)[0]
        val = clean_text(val, max_len=300)
        val = val.rstrip(",;")
        if len(val) < 2:
            continue
        if key == "years_of_experience" and not re.search(r"\d", val):
            continue
        if key == "salary" and not re.search(r"\d|€|\$|£|₹|¥", val):
            continue
        out[key] = val
    return out


def salary_evidence_from_text(text):
    """Return an explicit salary sentence/line containing numeric evidence."""
    if not text:
        return ""
    keyword = re.compile(
        r"\b(?:salary|compensation|pay range|annual pay|gehalt|vergütung|"
        r"brutto(?:jahres|monats)?gehalt|jahresbruttogehalt|monatsbruttogehalt|"
        r"jahresbrutto|monatsbrutto|entlohnung)\b",
        re.I,
    )
    numeric = re.compile(
        r"(?:[$€£₹¥]\s*\d|\d[\d.,]*\s*(?:[$€£₹¥]|USD|EUR|GBP|INR|AUD|CAD|CHF))",
        re.I,
    )
    source = str(text)
    for match in keyword.finditer(source):
        # Start at the salary keyword so flattened bullet lists cannot pollute
        # the salary column with hundreds of characters preceding the amount.
        value = clean_text(source[match.start():match.start() + 350], max_len=350)
        amount = numeric.search(value)
        if amount:
            end = len(value)
            sentence_end = re.search(r"[.!?](?:\s|$)", value[amount.end():])
            if sentence_end:
                end = amount.end() + sentence_end.end()
            return value[:end].strip()
    return ""


_SALARY_RANGE_RE = re.compile(
    # $70,000 - $90,000   |   £70,000-90,000   |   €50.000 – €60.000   |  ₹8,00,000 to ₹12,00,000
    r"([$€£₹¥]|USD|EUR|GBP|INR|AUD|CAD|CHF|SEK|NOK|DKK|PLN|BRL)\s*"
    r"(\d[\d.,]{2,})\s*(?:k\b)?\s*(?:-|–|—|to|bis|até|a)\s*"
    r"(?:[$€£₹¥]|USD|EUR|GBP|INR|AUD|CAD|CHF|SEK|NOK|DKK|PLN|BRL)?\s*"
    r"(\d[\d.,]{2,})\s*(?:k\b)?",
    re.IGNORECASE,
)
_SALARY_PERIOD_RE = re.compile(
    # $32.50 per hour   |   €55,000 per annum   |   45000 EUR / year
    r"(?:([$€£₹¥])\s*(\d[\d.,]{2,})|(\d[\d.,]{2,})\s*(USD|EUR|GBP|INR|AUD|CAD|CHF))"
    r"\s*(?:per|/|p\.?a\.?|pro)\s*(hour|hr|year|annum|month|week|jahr|stunde|monat)",
    re.IGNORECASE,
)


def salary_range_from_text(text):
    """An explicit pay range or per-period rate, with no keyword required.

    `salary_evidence_from_text` only fires when a word like "salary" or
    "compensation" sits near the number.  Many postings just state
    "$70,000 - $90,000", so this second pass accepts a currency-marked range
    or an explicit per-hour/per-year rate -- both strong enough signals that
    they will not pick up unrelated prices.
    """
    if not text:
        return ""
    source = str(text)[:20000]
    match = _SALARY_RANGE_RE.search(source)
    if match:
        return clean_text(match.group(0), max_len=120)
    match = _SALARY_PERIOD_RE.search(source)
    if match:
        return clean_text(match.group(0), max_len=120)
    return ""


def salary_breakdown(salary_text):
    """Split a salary text into display, min, max."""
    if not salary_text:
        return "", "", ""
    text = clean_text(salary_text, max_len=200)
    # Extract each endpoint separately; parsing the whole "60.000-112.000"
    # range as one token silently discarded the maximum.
    numbers = [parse_decimal(x) for x in re.findall(r"\d[\d,.]*", text)]
    nums = [n for n in numbers if n]
    if nums and all(float(n) <= 0 for n in nums):
        return "", "", ""
    mn = nums[0] if len(nums) >= 1 else ""
    mx = nums[1] if len(nums) >= 2 else ""
    if mn and mx and mn != mx:
        return "%s - %s" % (mn, mx), mn, mx
    if mn:
        return mn, mn, ""
    return text, "", ""


def experience_year_range(text):
    """Extract an explicitly stated year range without inferring experience."""
    value = clean_text(text, max_len=500)
    if not value:
        return "", ""
    match = re.search(r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:\+\s*)?years?\b",
                      value, re.I)
    if match:
        minimum, maximum = int(match.group(1)), int(match.group(2))
        if 0 <= minimum <= maximum <= 80:
            return str(minimum), str(maximum)
    match = re.search(
        r"(?:minimum(?: of)?|at least|more than|over)?\s*(\d{1,2})\s*\+?\s*years?"
        r"(?:\s+of)?\s+experience|\b(\d{1,2})\s*\+\s*years?\b",
        value, re.I)
    if match:
        years = int(match.group(1) or match.group(2))
        if 0 <= years <= 80:
            return str(years), ""
    return "", ""


def empty_job():
    return {
        "job_title": "",
        "job_category": "",
        "job_location": "",
        "posted_date": "",
        "application_deadline": "",
        "closed_date": "",
        "job_status": "",
        "extraction_status": "",
        "extraction_confidence": "",
        "extraction_evidence": "",
        "last_checked_at": "",
        "education_stream": "",
        "education_type": "",
        "education_qualification": "",
        "years_of_experience": "",
        "years_of_experience_min": "",
        "years_of_experience_max": "",
        "seniority_level": "",
        "employment_type": "",
        "skills": "",
        "description_language": "",
        "job_description": "",
        "job_description_clean": "",
        "job_url": "",
        "salary_disclosed": "",
        "salary": "",
        "min_salary": "",
        "max_salary": "",
        "currency": "",
        "source": "",
    }

# ---------------------------------------------------------------------------
# Derived fields.
#
# The ATS API parsers return whatever the vendor's JSON contains -- usually a
# title, a location and a URL.  Everything else in OUTPUT_COLUMNS
# (employment_type, seniority, education, skills, experience, category) has to
# be derived from the title and description, and that derivation used to run
# only inside parse_generic, so every ATS-sourced row came out blank.
# ---------------------------------------------------------------------------

_EMPLOYMENT_PATTERNS = (
    ("Full Time", r"\bfull[\s\-]?time\b|\bvollzeit\b|\btemps\s+plein\b|\bjornada\s+completa\b|\bFTE\b"),
    ("Part Time", r"\bpart[\s\-]?time\b|\bteilzeit\b|\btemps\s+partiel\b|\bmedia\s+jornada\b"),
    ("PRN", r"\bPRN\b|\bas\s+needed\b|\bcasual\s+pool\b"),
    ("Contract", r"\bcontract(?:or|ual)?\b|\bfreelance\b|\bb2b\b|\bwerkvertrag\b"),
    ("Temporary", r"\btemporary\b|\btemp\b|\bfixed[\s\-]term\b|\bbefristet\b|\binterim\b|\bseasonal\b"),
    ("Internship", r"\bintern(?:ship)?\b|\bpraktikum\b|\bworking\s+student\b|\bwerkstudent\b|\bstage\b|\btrainee\b"),
    ("Apprenticeship", r"\bapprentice(?:ship)?\b|\bausbildung\b|\blehrstelle\b"),
    ("Volunteer", r"\bvolunteer\b|\behrenamt\b"),
    ("Per Diem", r"\bper\s+diem\b"),
)

_SENIORITY_PATTERNS = (
    ("Executive", r"\b(?:chief|c-level|cto|ceo|cfo|coo|ciso|president|vice\s+president|\bvp\b|managing\s+director|geschäftsführ)"),
    ("Director", r"\bdirector\b|\bhead\s+of\b|\bleiter(?:in)?\b"),
    ("Manager", r"\bmanager\b|\bsupervisor\b|\bteam\s+lead(?:er)?\b|\bforeman\b"),
    ("Principal", r"\bprincipal\b|\bstaff\s+engineer\b|\bdistinguished\b|\bfellow\b"),
    ("Senior", r"\bsenior\b|\bsr\.?\b|\bexperienced\b|\blead\b|\biii\b|\bexpert\b"),
    ("Mid", r"\bmid[\s\-]?level\b|\bii\b|\bintermediate\b"),
    ("Junior", r"\bjunior\b|\bjr\.?\b|\bentry[\s\-]?level\b|\bgraduate\b|\bassociate\b|\bi\b(?!\w)"),
    ("Intern", r"\bintern(?:ship)?\b|\bpraktikant\b|\btrainee\b|\bapprentice\b|\bstudent\b"),
)

_EDUCATION_TYPES = (
    ("Doctorate", r"\bph\.?d\b|\bdoctoral\b|\bdoctorate\b|\bpromotion\b|\bmd\b|\bdnp\b|\bedd\b"),
    ("Master", r"\bmaster'?s?\b|\bm\.?sc\b|\bm\.?a\.?\b|\bmba\b|\bm\.?tech\b|\bmsn\b|\bmph\b|\bdiplom\b"),
    ("Bachelor", r"\bbachelor'?s?\b|\bb\.?sc\b|\bb\.?a\.?\b|\bb\.?tech\b|\bb\.?e\.?\b|\bbsn\b|\bundergraduate\s+degree\b"),
    ("Associate Degree", r"\bassociate'?s?\s+degree\b|\ba\.?a\.?s?\b(?=\s+degree)|\bfoundation\s+degree\b"),
    ("Diploma", r"\bdiploma\b|\bpolytechnic\b|\bhnd\b|\bhnc\b"),
    ("Certification", r"\bcertifi(?:ed|cation)\b|\blicens(?:e|ed|ure)\b|\bregistered\s+nurse\b|\brn\b|\blpn\b|\bcna\b"),
    ("High School", r"\bhigh\s+school\b|\bged\b|\bsecondary\s+school\b|\babitur\b|\bhaupt-?schulabschluss\b|\bmatric\b|\b10\+2\b"),
    ("Vocational", r"\bvocational\b|\bberufsausbildung\b|\bapprenticeship\b|\biti\b"),
)

_EDUCATION_STREAMS = (
    ("Nursing", r"\bnursing\b|\bnurse\b|\bbsn\b|\bmsn\b|\brn\b"),
    ("Medicine", r"\bmedicine\b|\bmedical\b|\bphysician\b|\bsurgery\b|\bclinical\b|\bpharmac"),
    ("Engineering", r"\bengineering\b|\bmechanical\b|\belectrical\b|\bcivil\b|\bb\.?tech\b|\bingenieur"),
    ("Computer Science", r"\bcomputer\s+science\b|\binformatics\b|\binformation\s+technology\b|\bsoftware\s+engineering\b|\binformatik\b"),
    ("Business", r"\bbusiness\s+admin|\bmba\b|\bcommerce\b|\bmanagement\s+studies\b|\bbetriebswirt"),
    ("Finance", r"\bfinance\b|\baccount(?:ing|ancy)\b|\bcpa\b|\bca\b(?=\s|$)|\beconomics\b"),
    ("Law", r"\blaw\b|\blegal\s+studies\b|\bjuris\b|\bllb\b|\bllm\b|\bjura\b"),
    ("Education", r"\beducation\b|\bteaching\b|\bpedagog|\bb\.?ed\b|\blehramt\b"),
    ("Sciences", r"\bbiology\b|\bchemistry\b|\bphysics\b|\bbiochem|\blife\s+sciences\b|\bnaturwissenschaft"),
    ("Social Sciences", r"\bsocial\s+work\b|\bpsychology\b|\bsociology\b|\blmsw\b|\blcsw\b|\bsozial"),
    ("Allied Health", r"\bphysical\s+therapy\b|\bphysiotherap|\boccupational\s+therapy\b|"
                      r"\bspeech\s+(?:therapy|language)\b|\bradiograph|\bsonograph|\bdpt\b|"
                      r"\brespiratory\s+(?:therapy|care)\b|\bdietetic|\bnutrition\b"),
    ("Pharmacy", r"\bpharmacy\b|\bpharm\.?d\b|\bpharmaceutical\s+sciences\b"),
    ("Hospitality", r"\bhospitality\s+management\b|\bculinary\b|\btourism\b"),
)

_JOB_CATEGORIES = (
    ("Healthcare", r"\bnurse\b|\bnursing\b|\bphysician\b|\bclinical\b|\bpatient\b|\bmedical\b|"
                   r"\btherapist\b|\bpharmac|\bradiolog|\bsurgical\b|\bcare\s+partner\b|\bcna\b|"
                   r"\bphlebotom|\bsonograph|\bdental\b|\bveterinar"),
    ("Information Technology", r"\bsoftware\b|\bdeveloper\b|\bprogrammer\b|\bdata\s+(?:engineer|scientist|analyst)\b|\bbackend\b|\bfrontend\b|\bfull[\s\-]?stack\b|"
                               r"\bdevops\b|\bsysadmin\b|\bit\s+support\b|\bnetwork\b|\bcloud\b|"
                               r"\bqa\b|\bfull[\s\-]?stack\b|\bfrontend\b|\bbackend\b|\banalyst\b.*\bsystem"),
    ("Engineering", r"\bengineer\b|\bengineering\b|\bmechanic\b|\belectrician\b|\btechnician\b|"
                    r"\bmaintenance\b|\bwelder\b|\bmachinist\b|\bfitter\b"),
    ("Sales", r"\bsales\b|\baccount\s+(?:executive|manager)\b|\bbusiness\s+development\b|\bvertrieb\b"),
    ("Marketing", r"\bmarketing\b|\bbrand\b|\bseo\b|\bcontent\s+(?:writer|manager)\b|\bsocial\s+media\b|\bcommunications\b"),
    ("Finance", r"\baccount(?:ant|ing)\b|\bfinance\b|\bcontroller\b|\bauditor\b|\bpayroll\b|\btreasur|\bbookkeep"),
    ("Human Resources", r"\bhuman\s+resources\b|\bhr\b|\brecruit(?:er|ment)\b|\btalent\s+acquisition\b|\bpersonal(?:referent|wesen)\b"),
    ("Legal", r"\blegal\b|\battorney\b|\bcounsel\b|\bparalegal\b|\bcompliance\b|\bjurist"),
    ("Operations", r"\boperations\b|\blogistics\b|\bsupply\s+chain\b|\bwarehouse\b|\bdriver\b|"
                   r"\bproduction\b|\bmanufacturing\b|\bquality\s+(?:control|assurance)\b|\bplanner\b"),
    ("Administration", r"\badministrat|\bassistant\b|\bsecretar|\breception|\bclerk\b|\bscheduling\b|"
                       r"\boffice\s+manager\b|\bdata\s+entry\b|\bspecialist\b.*\bservice"),
    ("Education", r"\bteacher\b|\blecturer\b|\bprofessor\b|\btutor\b|\bfaculty\b|\binstructor\b|\berzieher"),
    ("Customer Service", r"\bcustomer\s+(?:service|support|success)\b|\bcall\s+cent|\bhelp\s?desk\b|\bguest\s+services\b"),
    ("Research", r"\bresearch\b|\bscientist\b|\blaborator|\bpostdoc\b|\bclinical\s+trial\b"),
    ("Construction", r"\bconstruction\b|\bcarpenter\b|\bplumber\b|\bsite\s+manager\b|\bsurveyor\b|\barchitect\b"),
    ("Hospitality", r"\bchef\b|\bcook\b|\bwaiter\b|\bhousekeep|\bhotel\b|\brestaurant\b|\bbarista\b|\bcatering\b"),
    ("Retail", r"\bretail\b|\bcashier\b|\bstore\s+(?:manager|associate)\b|\bmerchandis|\bverkäufer"),
    ("Security", r"\bsecurity\s+(?:officer|guard)\b|\bsurveillance\b|\bcorrections?\b|\bprobation\b"),
)

_SKILL_VOCABULARY = (
    # technical
    "python", "java", "javascript", "typescript", "c\\+\\+", "c#", "\\.net", "php", "ruby",
    "go(?:lang)?", "rust", "kotlin", "swift", "scala", "perl", "r\\b", "matlab",
    "sql", "mysql", "postgresql", "oracle", "mongodb", "redis", "elasticsearch",
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "terraform",
    "jenkins", "git", "linux", "windows server", "react", "angular", "vue",
    "node\\.?js", "django", "flask", "spring", "laravel", "\\.net core",
    "power bi", "tableau", "excel", "sap", "salesforce", "servicenow",
    "machine learning", "deep learning", "nlp", "tensorflow", "pytorch",
    "etl", "data warehouse", "snowflake", "databricks", "spark", "hadoop", "airflow",
    "rest api", "graphql", "microservices", "ci/cd", "agile", "scrum", "jira",
    "autocad", "solidworks", "revit", "plc", "scada", "six sigma", "lean",
    # healthcare
    "bls", "acls", "pals", "epic", "cerner", "meditech", "hipaa", "icd-10",
    "phlebotomy", "iv insertion", "wound care", "telemetry", "ventilator",
    # business
    "quickbooks", "sap fico", "ifrs", "gaap", "payroll", "accounts payable",
    "accounts receivable", "financial modelling", "financial modeling",
    "procurement", "contract negotiation", "stakeholder management",
    "project management", "pmp", "prince2", "budgeting", "forecasting",
    "seo", "sem", "google analytics", "hubspot", "crm", "email marketing",
    "customer service", "conflict resolution", "team leadership",
)
_SKILL_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(_SKILL_VOCABULARY) + r")(?![a-z0-9])",
                       re.IGNORECASE)


def _first_match(patterns, haystack):
    for label, pattern in patterns:
        if re.search(pattern, haystack, re.IGNORECASE):
            return label
    return ""


def _all_matches(patterns, haystack, limit=4):
    found = []
    for label, pattern in patterns:
        if re.search(pattern, haystack, re.IGNORECASE):
            found.append(label)
        if len(found) >= limit:
            break
    return found


def infer_employment_type(title, text):
    return _first_match(_EMPLOYMENT_PATTERNS, (title or "") + " " + (text or "")[:4000])


def infer_seniority(title, text):
    # Title first: "Senior Engineer" is decisive, a stray "senior" in the body is not.
    found = _first_match(_SENIORITY_PATTERNS, title or "")
    return found or _first_match(_SENIORITY_PATTERNS, (text or "")[:1500])


def infer_education(text):
    """Return (education_type, education_stream, education_qualification)."""
    text = (text or "")[:12000]
    if not text:
        return "", "", ""
    etype = _first_match(_EDUCATION_TYPES, text)
    stream = _first_match(_EDUCATION_STREAMS, text)
    qualification = ""
    match = re.search(
        r"((?:bachelor'?s?|master'?s?|associate'?s?|doctoral|ph\.?d|diploma|"
        r"b\.?sc|m\.?sc|b\.?tech|m\.?tech|mba|bsn|msn|\brn\b|\blpn\b|"
        r"high\s+school)"
        r"[^.;\n]{0,90})", text, re.IGNORECASE)
    if match:
        qualification = clean_text(match.group(1), max_len=160)
    return etype, stream, qualification


def infer_job_category(title, text):
    # Title carries the signal; fall back to the opening of the description.
    return (_first_match(_JOB_CATEGORIES, title or "")
            or _first_match(_JOB_CATEGORIES, (text or "")[:2000]))


def extract_skills(text, limit=25):
    if not text:
        return ""
    seen = []
    lowered = set()
    for match in _SKILL_RE.finditer(text[:20000]):
        skill = match.group(1).strip()
        key = skill.lower()
        if key in lowered:
            continue
        lowered.add(key)
        seen.append(skill if skill.isupper() else skill.title())
        if len(seen) >= limit:
            break
    return "; ".join(seen)


def enrich_job(job, plain_description=""):
    """Fill in every derivable field that a parser left blank.

    Called once for every job from every source, so ATS API rows get the same
    treatment as generically-parsed ones.  Never overwrites a value the
    posting itself supplied.
    """
    if not isinstance(job, dict):
        return job

    title = job.get("job_title") or ""
    raw = job.get("job_description") or ""
    text = plain_description
    if not text and raw:
        if "<" in raw and ">" in raw:
            from .clean_html import html_to_plain_text
            text = html_to_plain_text(raw)
        else:
            text = raw
    text = clean_text(text, max_len=30000)

    labeled = extract_labeled_fields(text) if text else {}
    for key in ("employment_type", "seniority_level", "education_qualification",
                "years_of_experience", "salary"):
        if not job.get(key) and labeled.get(key):
            job[key] = labeled[key]

    if not job.get("employment_type"):
        job["employment_type"] = infer_employment_type(title, text)
    if not job.get("seniority_level"):
        job["seniority_level"] = infer_seniority(title, text)
    if not job.get("job_category"):
        job["job_category"] = infer_job_category(title, text)

    if text:
        etype, stream, qualification = infer_education(text)
        if not job.get("education_type"):
            job["education_type"] = etype
        if not job.get("education_stream"):
            job["education_stream"] = stream
        if not job.get("education_qualification"):
            job["education_qualification"] = qualification
        if not job.get("skills"):
            job["skills"] = extract_skills(text)

    if not job.get("years_of_experience_min") and not job.get("years_of_experience_max"):
        source_text = job.get("years_of_experience") or text
        minimum, maximum = experience_year_range(source_text)
        if minimum or maximum:
            job["years_of_experience_min"] = minimum
            job["years_of_experience_max"] = maximum

    if not job.get("salary"):
        evidence = salary_evidence_from_text(text) or salary_range_from_text(text)
        if evidence:
            job["salary"] = evidence
    if job.get("salary") and not (job.get("min_salary") or job.get("max_salary")):
        original_salary = job["salary"]
        display, minimum, maximum = salary_breakdown(original_salary)
        if display:
            job["salary"] = display
            job["min_salary"] = minimum
            job["max_salary"] = maximum
        if not job.get("currency"):
            # From the original string: salary_breakdown strips the symbol, so
            # reading the currency off the normalised value found nothing.
            job["currency"] = (currency_from_salary(original_salary)
                               or currency_from_salary(text[:4000] if text else ""))

    if not job.get("job_status"):
        job["job_status"] = "Active"
    return job
