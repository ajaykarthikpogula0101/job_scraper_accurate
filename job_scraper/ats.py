import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .urlutils import ensure_https, url_join

_TLD_EXTRACTOR = []


def _get_tld_extractor():
    """Return a cached tldextract callable, or None if unavailable."""
    if _TLD_EXTRACTOR:
        return _TLD_EXTRACTOR[0]
    extractor = None
    try:
        import tldextract
        # suffix_list_urls=() keeps it entirely offline: it uses the snapshot
        # shipped with the package instead of fetching the live PSL.
        extractor = tldextract.TLDExtract(suffix_list_urls=())
    except Exception:
        extractor = None
    _TLD_EXTRACTOR.append(extractor)
    return extractor

# ---------------------------------------------------------------------------
# ATS fingerprints found inside URLs.
# ---------------------------------------------------------------------------
ATS_URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|careers)(?:\.[a-zA-Z0-9\-_]+)*\.greenhouse\.io/(?:[^/?#]+/)*(?:[^/#]*[?&]for=)?([a-zA-Z0-9\-_.]+)")),
    ("lever", re.compile(r"(?:jobs|careers)\.lever\.co/([a-zA-Z0-9\-_.]+)")),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9\-_]+)")),
    ("workable", re.compile(r"apply\.workable\.com/([a-zA-Z0-9\-_]+)")),
    ("teamtailor", re.compile(r"([a-zA-Z0-9\-_]+)\.teamtailor\.com")),
    ("recruitee", re.compile(r"([a-zA-Z0-9\-_]+)\.recruitee\.com")),
    ("breezy", re.compile(r"([a-zA-Z0-9\-_]+)\.breezy\.hr")),
    ("jazzhr", re.compile(r"([a-zA-Z0-9\-_]+)\.(?:applytojob\.com|jazzhr\.com|jazz\.co)")),
    ("bamboo", re.compile(r"([a-zA-Z0-9\-_]+)\.bamboohr\.com")),
    ("personio", re.compile(r"([a-zA-Z0-9\-_]+)\.jobs\.personio\.(?:de|com|eu|at|es|fr|it|nl|uk|co\.uk|be|ch|dk|se|no|pl|cz)")),
    ("softgarden", re.compile(r"([a-zA-Z0-9\-_]+)\.softgarden\.io")),
    ("workday", re.compile(r"([a-zA-Z0-9\-_]+)\.wd\d+\.myworkdayjobs\.com")),
    ("icims", re.compile(r"(?:jobs\.icims\.com|(?!www\.)([a-zA-Z0-9\-_]+)\.icims\.com)")),
    ("taleo", re.compile(r"([a-zA-Z0-9\-_]+)\.taleo\.net")),
    ("successfactors", re.compile(r"(?:(?:[a-zA-Z0-9\-_]+)\.)?(?:jobs\.sap\.com|successfactors\.(?:eu|com|net)|sapsf\.eu|sap\.jobs)")),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([a-zA-Z0-9\-_]+)")),
    ("join", re.compile(r"join\.com/(?:companies/)?([a-zA-Z0-9\-_]+)")),
    ("oracle", re.compile(r"([a-zA-Z0-9\-_]+)\.(?:oraclecloud\.com|ce\.hcm\.od\.taleo\.net)")),
    ("pinpoint", re.compile(r"([a-zA-Z0-9\-_]+)\.pinpointhq\.com")),
    ("zoho", re.compile(r"([a-zA-Z0-9\-_]+)\.zohorecruit\.(?:com|eu|in|jp|com\.au|com\.mx)")),
    ("freshteam", re.compile(r"([a-zA-Z0-9\-_]+)\.freshteam\.com")),
    ("jobadder", re.compile(r"([a-zA-Z0-9\-_]+)\.jobadder\.com")),
    ("bullhorn", re.compile(r"(?:bullhorn|bulhorn|vault)\.com")),
    ("hrmanager", re.compile(r"candidate\.hr-manager\.net/(?:ApplicationInit\.aspx)?", re.I)),
    ("workable", re.compile(r"([a-zA-Z0-9\-_]+)\.workable\.com")),
    # ---------------------------------------------------------------
    # Modern / additional vendors.  Anything matched here is at minimum
    # rendered and generically parsed instead of falling through as unknown.
    # ---------------------------------------------------------------
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9\-_.]+)")),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9\-_.]+)")),
    ("rippling", re.compile(r"ats\.rippling\.com/([a-zA-Z0-9\-_.]+)")),
    ("rippling", re.compile(r"([a-zA-Z0-9\-_]+)\.rippling-ats\.com")),
    ("cornerstone", re.compile(r"([a-zA-Z0-9\-_]+)\.csod\.com")),
    ("dayforce", re.compile(r"([a-zA-Z0-9\-_]+)\.dayforcehcm\.com")),
    ("dayforce", re.compile(r"dayforcehcm\.com/CandidatePortal/[^/]*/([a-zA-Z0-9\-_]+)")),
    ("eightfold", re.compile(r"([a-zA-Z0-9\-_]+)\.eightfold\.ai")),
    ("phenom", re.compile(r"(?:phenompeople\.com|\.phenom\.people|jobs\.phenom)")),
    ("paylocity", re.compile(r"recruiting\.paylocity\.com/recruiting/jobs/[^/]+/([a-zA-Z0-9\-]+)")),
    ("paylocity", re.compile(r"recruiting\.paylocity\.com")),
    ("paycom", re.compile(r"([a-zA-Z0-9\-_]+)\.paycomonline\.net")),
    ("ukg", re.compile(r"recruiting\d*\.ultipro\.com/([a-zA-Z0-9\-_]+)")),
    ("ukg", re.compile(r"([a-zA-Z0-9\-_]+)\.ultipro\.com")),
    ("ukg", re.compile(r"([a-zA-Z0-9\-_]+)\.ukg\.net")),
    ("adp", re.compile(r"workforcenow\.adp\.com")),
    ("adp", re.compile(r"myjobs\.adp\.com/([a-zA-Z0-9\-_]+)")),
    ("isolved", re.compile(r"([a-zA-Z0-9\-_]+)\.myisolved\.com")),
    ("hibob", re.compile(r"(?:apply\.hibob\.com|([a-zA-Z0-9\-_]+)\.hibob\.com)")),
    ("homerun", re.compile(r"([a-zA-Z0-9\-_]+)\.homerun\.co")),
    ("occupop", re.compile(r"([a-zA-Z0-9\-_]+)\.occupop\.com")),
    ("tribepad", re.compile(r"([a-zA-Z0-9\-_]+)\.tribepad\.com")),
    ("lumesse", re.compile(r"(?:[a-zA-Z0-9\-_]+\.)?(?:lumesse\.com|recruitmentplatform\.com)")),
    ("cezanne", re.compile(r"([a-zA-Z0-9\-_]+)\.cezanneondemand\.com")),
    ("hireserve", re.compile(r"([a-zA-Z0-9\-_]+)\.hireserve(?:live)?\.com")),
    ("eploy", re.compile(r"([a-zA-Z0-9\-_]+)\.eploy\.net")),
    ("jobylon", re.compile(r"([a-zA-Z0-9\-_]+)\.jobylon\.com")),
    ("reachmee", re.compile(r"([a-zA-Z0-9\-_]+)\.reachmee\.com")),
    ("emply", re.compile(r"([a-zA-Z0-9\-_]+)\.emply\.com")),
    ("talentlyft", re.compile(r"([a-zA-Z0-9\-_]+)\.talentlyft\.com")),
    ("factorial", re.compile(r"([a-zA-Z0-9\-_]+)\.factorialhr\.com")),
    ("otys", re.compile(r"([a-zA-Z0-9\-_]+)\.otys\.com")),
    ("keka", re.compile(r"([a-zA-Z0-9\-_]+)\.keka\.com")),
    ("darwinbox", re.compile(r"([a-zA-Z0-9\-_]+)\.darwinbox\.(?:in|com|io)")),
    ("zwayam", re.compile(r"([a-zA-Z0-9\-_]+)\.zwayam\.com")),
    ("manatal", re.compile(r"([a-zA-Z0-9\-_]+)\.manatal\.com")),
    ("jobsoid", re.compile(r"([a-zA-Z0-9\-_]+)\.jobsoid\.com")),
    ("avature", re.compile(r"([a-zA-Z0-9\-_]+)\.avature\.net")),
    ("talentsoft", re.compile(r"([a-zA-Z0-9\-_]+)[-.]talent(?:-)?soft\.com")),
    ("workday", re.compile(r"([a-zA-Z0-9\-_]+)\.wd\d+\.myworkdaysite\.com")),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([a-zA-Z0-9\-_]+)")),
    ("teamtailor", re.compile(r"career\.([a-zA-Z0-9\-_]+)\.teamtailor\.com")),
    # ---------------------------------------------------------------
    # Brazil / LatAm  (a large share of the input dataset)
    # ---------------------------------------------------------------
    ("gupy", re.compile(r"([a-zA-Z0-9\-_]+)\.gupy\.io")),
    ("gupy", re.compile(r"portal\.gupy\.io/job-search/term=([a-zA-Z0-9\-_]+)")),
    ("kenoby", re.compile(r"([a-zA-Z0-9\-_]+)\.kenoby\.com")),
    ("solides", re.compile(r"([a-zA-Z0-9\-_]+)\.solides\.jobs")),
    ("solides", re.compile(r"jobs\.solides\.com/([a-zA-Z0-9\-_]+)")),
    ("inhire", re.compile(r"([a-zA-Z0-9\-_]+)\.inhire\.app")),
    ("abler", re.compile(r"([a-zA-Z0-9\-_]+)\.abler\.com\.br")),
    ("quickin", re.compile(r"([a-zA-Z0-9\-_]+)\.quickin\.io")),
    ("pandape", re.compile(r"([a-zA-Z0-9\-_]+)\.pandape\.com")),
    ("99jobs", re.compile(r"99jobs\.com/([a-zA-Z0-9\-_]+)")),
    ("compleo", re.compile(r"([a-zA-Z0-9\-_]+)\.compleo\.com\.br")),
    ("selecty", re.compile(r"([a-zA-Z0-9\-_]+)\.selecty\.com\.br")),
    # ---------------------------------------------------------------
    # Germany / DACH
    # ---------------------------------------------------------------
    ("dvinci", re.compile(r"([a-zA-Z0-9\-_]+)\.dvinci(?:-hr)?\.(?:com|de)")),
    ("rexx", re.compile(r"([a-zA-Z0-9\-_]+)\.rexx-systems\.com")),
    ("umantis", re.compile(r"([a-zA-Z0-9\-_]+)\.umantis\.com")),
    ("prescreen", re.compile(r"([a-zA-Z0-9\-_]+)\.prescreen\.io")),
    ("onlyfy", re.compile(r"(?:[a-zA-Z0-9\-_]+\.)?onlyfy\.jobs")),
    ("concludis", re.compile(r"([a-zA-Z0-9\-_]+)\.concludis\.de")),
    ("bite", re.compile(r"([a-zA-Z0-9\-_]+)\.bite-gmbh\.de")),
    ("talention", re.compile(r"([a-zA-Z0-9\-_]+)\.talention\.com")),
    ("workwise", re.compile(r"workwise\.io/(?:jobs|karriere)/([a-zA-Z0-9\-_]+)")),
    ("guidecom", re.compile(r"([a-zA-Z0-9\-_]+)\.guidecom\.de")),
    # German public sector -- the dataset is full of schools and municipalities
    ("interamt", re.compile(r"(?:www\.)?interamt\.de")),
    # ---------------------------------------------------------------
    # India
    # ---------------------------------------------------------------
    ("hrone", re.compile(r"([a-zA-Z0-9\-_]+)\.hrone\.cloud")),
    ("greythr", re.compile(r"([a-zA-Z0-9\-_]+)\.greythr\.com")),
    ("turbohire", re.compile(r"([a-zA-Z0-9\-_]+)\.turbohire\.co")),
    ("peoplestrong", re.compile(r"([a-zA-Z0-9\-_]+)\.peoplestrong\.com")),
    ("springrecruit", re.compile(r"([a-zA-Z0-9\-_]+)\.springrecruit\.com")),
    ("ceipal", re.compile(r"([a-zA-Z0-9\-_]+)\.ceipal\.com")),
    ("oorwin", re.compile(r"([a-zA-Z0-9\-_]+)\.oorwin\.com")),
    # ---------------------------------------------------------------
    # Australia / NZ
    # ---------------------------------------------------------------
    ("livehire", re.compile(r"([a-zA-Z0-9\-_]+)\.livehire\.com")),
    ("pageup", re.compile(r"([a-zA-Z0-9\-_]+)\.pageuppeople\.com")),
    ("pageup", re.compile(r"([a-zA-Z0-9\-_]+)\.pageup\.com")),
    ("elmo", re.compile(r"([a-zA-Z0-9\-_]+)\.elmotalent\.com\.au")),
    ("employmenthero", re.compile(r"([a-zA-Z0-9\-_]+)\.employmenthero\.com")),
    ("expr3ss", re.compile(r"([a-zA-Z0-9\-_]+)\.expr3ss\.com")),
    ("foundu", re.compile(r"([a-zA-Z0-9\-_]+)\.foundu\.com\.au")),
    ("scouterecruit", re.compile(r"([a-zA-Z0-9\-_]+)\.recruitwizard\.com")),
    # ---------------------------------------------------------------
    # Nordics / Benelux / France / Southern Europe
    # ---------------------------------------------------------------
    ("webcruiter", re.compile(r"([a-zA-Z0-9\-_]+)?\.?webcruiter\.(?:no|com)")),
    ("jobbnorge", re.compile(r"(?:www\.)?jobbnorge\.no")),
    ("varbi", re.compile(r"([a-zA-Z0-9\-_]+)?\.?varbi\.com")),
    ("talentech", re.compile(r"([a-zA-Z0-9\-_]+)\.talentech\.(?:com|io)")),
    ("flatchr", re.compile(r"([a-zA-Z0-9\-_]+)\.flatchr\.io")),
    ("taleez", re.compile(r"([a-zA-Z0-9\-_]+)\.taleez\.com")),
    ("digitalrecruiters", re.compile(r"([a-zA-Z0-9\-_]+)\.digitalrecruiters\.com")),
    ("softy", re.compile(r"([a-zA-Z0-9\-_]+)\.softy\.pro")),
    ("beetween", re.compile(r"([a-zA-Z0-9\-_]+)\.beetween\.com")),
    ("welcometothejungle", re.compile(r"welcometothejungle\.com/[a-z\-]+/companies/([a-zA-Z0-9\-_]+)")),
    ("carerix", re.compile(r"([a-zA-Z0-9\-_]+)\.carerix\.(?:net|com)")),
    ("recruitnow", re.compile(r"([a-zA-Z0-9\-_]+)\.recruitnow\.nl")),
    ("erecruiter", re.compile(r"([a-zA-Z0-9\-_]+)\.erecruiter\.pl")),
    ("traffit", re.compile(r"([a-zA-Z0-9\-_]+)\.traffit\.com")),
    ("hrappka", re.compile(r"([a-zA-Z0-9\-_]+)\.hrappka\.pl")),
    # ---------------------------------------------------------------
    # North America / global mid-market
    # ---------------------------------------------------------------
    ("applicantpro", re.compile(r"([a-zA-Z0-9\-_]+)\.applicantpro\.com")),
    ("applicantstack", re.compile(r"([a-zA-Z0-9\-_]+)\.applicantstack\.com")),
    ("clearcompany", re.compile(r"([a-zA-Z0-9\-_]+)\.clearcompany\.com")),
    ("trakstar", re.compile(r"([a-zA-Z0-9\-_]+)\.recruiterbox\.com")),
    ("trakstar", re.compile(r"([a-zA-Z0-9\-_]+)\.trakstar\.com")),
    ("newton", re.compile(r"([a-zA-Z0-9\-_]+)\.newtonsoftware\.com")),
    ("paycor", re.compile(r"([a-zA-Z0-9\-_]+)\.paycor\.com")),
    ("brassring", re.compile(r"(?:[a-zA-Z0-9\-_]+\.)?brassring\.com")),
    ("silkroad", re.compile(r"([a-zA-Z0-9\-_]+)\.(?:silkroad|openhire)\.com")),
    ("catsone", re.compile(r"([a-zA-Z0-9\-_]+)\.catsone\.com")),
    ("crelate", re.compile(r"([a-zA-Z0-9\-_]+)\.crelate\.com")),
    ("vincere", re.compile(r"([a-zA-Z0-9\-_]+)\.vincere\.io")),
    ("jobdiva", re.compile(r"([a-zA-Z0-9\-_]+)?\.?jobdiva\.com")),
    ("recruitcrm", re.compile(r"([a-zA-Z0-9\-_]+)\.recruitcrm\.io")),
    ("smartsearch", re.compile(r"([a-zA-Z0-9\-_]+)\.smartsearchonline\.com")),
    ("workstream", re.compile(r"([a-zA-Z0-9\-_]+)?\.?workstream\.us")),
    ("fountain", re.compile(r"([a-zA-Z0-9\-_]+)\.fountain\.com")),
    ("hireology", re.compile(r"([a-zA-Z0-9\-_]+)\.hireology\.com")),
    ("paradox", re.compile(r"([a-zA-Z0-9\-_]+)\.paradox\.ai")),
    ("avionte", re.compile(r"([a-zA-Z0-9\-_]+)\.avionte\.com")),
    ("polymer", re.compile(r"([a-zA-Z0-9\-_]+)\.polymer\.co")),
    ("infor", re.compile(r"([a-zA-Z0-9\-_]+)\.inforcloudsuite\.com")),
    # Education / public sector
    ("interfolio", re.compile(r"(?:apply\.)?interfolio\.com/([0-9]+)")),
    ("peopleadmin", re.compile(r"([a-zA-Z0-9\-_]+)\.peopleadmin\.com")),
    ("schooljobs", re.compile(r"(?:www\.)?schooljobs\.com/careers/([a-zA-Z0-9\-_]+)")),
    ("governmentjobs", re.compile(r"(?:www\.)?governmentjobs\.com/careers/([a-zA-Z0-9\-_]+)")),
    # ---------------------------------------------------------------
    # US K-12 / education -- Frontline/Applitrack hosts a very large share of
    # American school-district job boards, which matters for this dataset.
    # ---------------------------------------------------------------
    ("applitrack", re.compile(r"(?:www\.)?applitrack\.com/([a-zA-Z0-9\-_]+)")),
    ("schoolspring", re.compile(r"(?:www\.)?schoolspring\.com")),
    ("frontline", re.compile(r"([a-zA-Z0-9\-_]+)\.frontlineeducation\.com")),
    ("powerschool", re.compile(r"([a-zA-Z0-9\-_]+)\.powerschool\.com")),
    ("interviewexchange", re.compile(r"(?:main\.)?interviewexchange\.com")),
    ("symplr", re.compile(r"([a-zA-Z0-9\-_]+)\.symplr\.com")),
    # ---------------------------------------------------------------
    # Additional vendors with known public board hosts
    # ---------------------------------------------------------------
    ("100hires", re.compile(r"([a-zA-Z0-9\-_]+)?\.?100hires\.com")),
    ("peoplehr", re.compile(r"([a-zA-Z0-9\-_]+)?\.?peoplehr\.net")),
    ("altamira", re.compile(r"([a-zA-Z0-9\-_]+)\.altamira(?:web|recruiting)?\.(?:net|com)")),
    ("apploi", re.compile(r"([a-zA-Z0-9\-_]+)?\.?apploi\.com")),
    ("beapplied", re.compile(r"(?:app\.)?beapplied\.com")),
    ("arcoro", re.compile(r"([a-zA-Z0-9\-_]+)\.arcoro(?:hr)?\.com")),
    ("auzmor", re.compile(r"([a-zA-Z0-9\-_]+)\.auzmor\.com")),
    ("bernieportal", re.compile(r"([a-zA-Z0-9\-_]+)?\.?bernieportal\.com")),
    ("bizneo", re.compile(r"([a-zA-Z0-9\-_]+)\.bizneo\.com")),
    ("brightmove", re.compile(r"([a-zA-Z0-9\-_]+)?\.?brightmove\.com")),
    ("briohr", re.compile(r"([a-zA-Z0-9\-_]+)?\.?briohr\.com")),
    ("careerplug", re.compile(r"([a-zA-Z0-9\-_]+)?\.?careerplug\.com")),
    ("chameleoni", re.compile(r"([a-zA-Z0-9\-_]+)?\.?chameleoni\.com")),
    ("ciphr", re.compile(r"([a-zA-Z0-9\-_]+)\.ciphr(?:-irecruit)?\.com")),
    ("clayhr", re.compile(r"([a-zA-Z0-9\-_]+)?\.?clayhr\.com")),
    ("cleverstaff", re.compile(r"([a-zA-Z0-9\-_]+)?\.?cleverstaff\.net")),
    ("comeet", re.compile(r"([a-zA-Z0-9\-_]+)\.comeet\.(?:com|co)")),
    ("connexys", re.compile(r"([a-zA-Z0-9\-_]+)\.connexys\.(?:nl|com)")),
    ("coveto", re.compile(r"([a-zA-Z0-9\-_]+)?\.?coveto\.de")),
    ("cvminder", re.compile(r"([a-zA-Z0-9\-_]+)?\.?cvminder\.co\.uk")),
    ("datacruit", re.compile(r"([a-zA-Z0-9\-_]+)\.datacruit\.(?:com|eu)")),
    ("dover", re.compile(r"(?:app\.)?dover\.com/(?:jobs|careers)/([a-zA-Z0-9\-_]+)")),
    ("dualoo", re.compile(r"([a-zA-Z0-9\-_]+)?\.?dualoo\.com")),
    ("eddy", re.compile(r"([a-zA-Z0-9\-_]+)\.eddy\.com")),
    ("employwise", re.compile(r"([a-zA-Z0-9\-_]+)?\.?employwise\.com")),
    ("exelare", re.compile(r"([a-zA-Z0-9\-_]+)?\.?exelare\.com")),
    ("firefish", re.compile(r"([a-zA-Z0-9\-_]+)\.firefishsoftware\.com")),
    ("fitzii", re.compile(r"([a-zA-Z0-9\-_]+)?\.?fitzii\.com")),
    ("folkshr", re.compile(r"([a-zA-Z0-9\-_]+)?\.?folkshr\.com")),
    ("gestmax", re.compile(r"([a-zA-Z0-9\-_]+)\.gestmax\.(?:fr|eu)")),
    ("gohire", re.compile(r"([a-zA-Z0-9\-_]+)\.gohire\.io")),
    ("gr8people", re.compile(r"([a-zA-Z0-9\-_]+)?\.?gr8people\.com")),
    ("harri", re.compile(r"([a-zA-Z0-9\-_]+)?\.?harri\.com")),
    ("higherme", re.compile(r"([a-zA-Z0-9\-_]+)?\.?higherme\.com")),
    ("hirebridge", re.compile(r"([a-zA-Z0-9\-_]+)?\.?hirebridge\.com")),
    ("hireful", re.compile(r"([a-zA-Z0-9\-_]+)?\.?hireful\.co\.uk")),
    ("hireplanner", re.compile(r"([a-zA-Z0-9\-_]+)?\.?hireplanner\.com")),
    ("hiringthing", re.compile(r"([a-zA-Z0-9\-_]+)\.hiringthing\.com")),
    ("hrworks", re.compile(r"([a-zA-Z0-9\-_]+)?\.?hrworks\.de")),
    ("ismartrecruit", re.compile(r"([a-zA-Z0-9\-_]+)?\.?ismartrecruit\.com")),
    ("irecruit", re.compile(r"([a-zA-Z0-9\-_]+)?\.?irecruit-us\.com")),
    ("inrecruiting", re.compile(r"([a-zA-Z0-9\-_]+)\.(?:inrecruiting\.com|intervieweb\.it)")),
    ("jobboardio", re.compile(r"([a-zA-Z0-9\-_]+)\.jobboard\.io")),
    ("jobconvo", re.compile(r"([a-zA-Z0-9\-_]+)?\.?jobconvo\.com")),
    ("jobscore", re.compile(r"(?:careers\.)?jobscore\.com/careers/([a-zA-Z0-9\-_]+)")),
    ("jobtrain", re.compile(r"([a-zA-Z0-9\-_]+)?\.?jobtrain\.co\.uk")),
    ("keldair", re.compile(r"([a-zA-Z0-9\-_]+)\.(?:keldairhr|hyrell)\.com")),
    ("kula", re.compile(r"([a-zA-Z0-9\-_]+)?\.?kula\.ai")),
    ("lanteria", re.compile(r"([a-zA-Z0-9\-_]+)?\.?lanteria\.com")),
    ("loxo", re.compile(r"([a-zA-Z0-9\-_]+)?\.?loxo\.co")),
    ("njoyn", re.compile(r"([a-zA-Z0-9\-_]+)?\.?njoyn\.com")),
    ("oleeo", re.compile(r"([a-zA-Z0-9\-_]+)\.oleeo\.com")),
    ("onehcm", re.compile(r"([a-zA-Z0-9\-_]+)?\.?onehcm\.com")),
    ("pcrecruiter", re.compile(r"([a-zA-Z0-9\-_]+)?\.?pcrecruiter\.net")),
    ("peoplefluent", re.compile(r"([a-zA-Z0-9\-_]+)?\.?peoplefluent\.com")),
    ("peopleforce", re.compile(r"([a-zA-Z0-9\-_]+)\.peopleforce\.io")),
    ("pereless", re.compile(r"([a-zA-Z0-9\-_]+)?\.?pereless\.com")),
    ("pitchnhire", re.compile(r"([a-zA-Z0-9\-_]+)?\.?pitchnhire\.com")),
    ("porters", re.compile(r"([a-zA-Z0-9\-_]+)\.(?:porters-hr\.com|porters\.jp)")),
    ("pyjamahr", re.compile(r"([a-zA-Z0-9\-_]+)?\.?pyjamahr\.com")),
    ("qjumpers", re.compile(r"([a-zA-Z0-9\-_]+)?\.?qjumpers\.com")),
    ("recooty", re.compile(r"([a-zA-Z0-9\-_]+)?\.?recooty\.com")),
    ("recruitbpm", re.compile(r"([a-zA-Z0-9\-_]+)?\.?recruitbpm\.com")),
    ("recruiterflow", re.compile(r"([a-zA-Z0-9\-_]+)\.recruiterflow\.com")),
    ("recruiteze", re.compile(r"([a-zA-Z0-9\-_]+)?\.?recruiteze\.com")),
    ("sagehr", re.compile(r"([a-zA-Z0-9\-_]+)\.sage\.hr")),
    ("skeeled", re.compile(r"([a-zA-Z0-9\-_]+)?\.?skeeled\.com")),
    ("sloneek", re.compile(r"([a-zA-Z0-9\-_]+)?\.?sloneek\.com")),
    ("smartrecruitonline", re.compile(r"([a-zA-Z0-9\-_]+)?\.?smartrecruitonline\.com")),
    ("smartjobboard", re.compile(r"([a-zA-Z0-9\-_]+)\.smartjobboard\.com")),
    ("snaphire", re.compile(r"([a-zA-Z0-9\-_]+)\.snaphire\.com")),
    ("snaphunt", re.compile(r"([a-zA-Z0-9\-_]+)?\.?snaphunt\.com")),
    ("sparkhire", re.compile(r"([a-zA-Z0-9\-_]+)?\.?sparkhire\.com")),
    ("staffcv", re.compile(r"([a-zA-Z0-9\-_]+)\.staffcv\.com")),
    ("talentera", re.compile(r"([a-zA-Z0-9\-_]+)\.talentera\.com")),
    ("talentnest", re.compile(r"([a-zA-Z0-9\-_]+)\.talentnest\.com")),
    ("talentrecruit", re.compile(r"([a-zA-Z0-9\-_]+)?\.?talentrecruit\.com")),
    ("talexio", re.compile(r"([a-zA-Z0-9\-_]+)?\.?talexio\.com")),
    ("talos", re.compile(r"([a-zA-Z0-9\-_]+)?\.?talos(?:360)?\.(?:co\.uk|com)")),
    ("targetrecruit", re.compile(r"([a-zA-Z0-9\-_]+)?\.?targetrecruit\.net")),
    ("teamengine", re.compile(r"([a-zA-Z0-9\-_]+)?\.?teamengine\.io")),
    ("teamworkonline", re.compile(r"(?:www\.)?teamworkonline\.com")),
    ("teamdash", re.compile(r"([a-zA-Z0-9\-_]+)\.teamdash\.com")),
    ("tempworks", re.compile(r"([a-zA-Z0-9\-_]+)?\.?tempworks\.com")),
    ("applicantmanager", re.compile(r"([a-zA-Z0-9\-_]+)?\.?theapplicantmanager\.com")),
    ("tool2match", re.compile(r"([a-zA-Z0-9\-_]+)\.tool2match\.nl")),
    ("trackerrms", re.compile(r"([a-zA-Z0-9\-_]+)?\.?tracker-rms\.com")),
    ("trisys", re.compile(r"([a-zA-Z0-9\-_]+)?\.?trisys\.co\.uk")),
    ("truckright", re.compile(r"([a-zA-Z0-9\-_]+)?\.?truckright\.com")),
    ("viterbit", re.compile(r"([a-zA-Z0-9\-_]+)\.viterbit\.com")),
    ("vultus", re.compile(r"([a-zA-Z0-9\-_]+)?\.?vultus\.com")),
    ("winsearch", re.compile(r"([a-zA-Z0-9\-_]+)?\.?winsearch\.com")),
    ("wizehire", re.compile(r"([a-zA-Z0-9\-_]+)?\.?wizehire\.com")),
    ("workforcecom", re.compile(r"([a-zA-Z0-9\-_]+)?\.?workforce\.com")),
    ("workforcehub", re.compile(r"([a-zA-Z0-9\-_]+)?\.?workforcehub\.com")),
    ("workllama", re.compile(r"([a-zA-Z0-9\-_]+)?\.?workllama\.com")),
    ("vagas", re.compile(r"(?:www\.)?vagas\.com\.br/(?:cargo|empregos|v)/([a-zA-Z0-9\-_]+)")),
    ("jobteaser", re.compile(r"(?:www\.)?jobteaser\.com/[a-z]{2}/companies/([a-zA-Z0-9\-_]+)")),
]

# ATS fingerprints found in HTML page content.
ATS_HTML_MARKERS = [
    ("greenhouse", ["boards.greenhouse.io", "grnh.se", "greenhouse.io", "GHJob"]),
    ("lever", ["jobs.lever.co", "lever.co", "LeverJob", "lever-post"]),
    ("smartrecruiters", ["smartrecruiters.com", "SmartRecruiters"]),
    ("workable", ["apply.workable.com", "Workable"]),
    ("teamtailor", ["teamtailor.com", "Teamtailor", "teamtailor"]),
    ("recruitee", ["recruitee.com", "Recruitee"]),
    ("breezy", ["breezy.hr", "BreezyHR"]),
    ("jazzhr", ["applytojob.com", "jazzhr.com", "jazz.co", "JazzHR"]),
    ("bamboo", ["bamboohr.com", "BambooHR", "bamboohr"]),
    ("personio", ["jobs.personio", "Personio"]),
    ("softgarden", ["softgarden.io", "Softgarden", "softgarden"]),
    ("workday", ["myworkdayjobs.com", "Workday", "wd3.myworkdayjobs", "workday"]),
    ("icims", ["icims.com", "iCIMS", "icims"]),
    ("taleo", ["taleo.net", "Taleo", "taleo"]),
    ("successfactors", ["successfactors", "jobs.sap.com", "SuccessFactors"]),
    ("jobvite", ["jobvite.com", "Jobvite", "jvite"]),
    ("join", ["join.com", "JOIN.com"]),
    ("oracle", ["oraclecloud.com", "ce.hcm.od.taleo.net"]),
    ("pinpoint", ["pinpointhq.com", "Pinpoint"]),
    ("zoho", ["zohorecruit", "Zoho Recruit"]),
    ("freshteam", ["freshteam.com", "Freshteam"]),
    ("jobadder", ["jobadder.com", "JobAdder"]),
    ("bullhorn", ["bullhorn.com", "bulhorn.com"]),
    ("indeed", ["indeed.com"]),
    ("adzuna", ["adzuna"]),
    ("ashby", ["ashbyhq.com", "Ashby", "_ashby"]),
    ("rippling", ["ats.rippling.com", "rippling-ats", "Rippling"]),
    ("cornerstone", ["csod.com", "Cornerstone OnDemand", "cornerstone"]),
    ("dayforce", ["dayforcehcm.com", "Dayforce", "Ceridian"]),
    ("eightfold", ["eightfold.ai", "Eightfold"]),
    ("phenom", ["phenompeople.com", "Phenom People"]),
    ("paylocity", ["recruiting.paylocity.com", "Paylocity"]),
    ("paycom", ["paycomonline.net", "Paycom"]),
    ("ukg", ["ultipro.com", "UltiPro", "ukg.net", "UKG"]),
    ("adp", ["workforcenow.adp.com", "myjobs.adp.com"]),
    ("isolved", ["myisolved.com", "iSolved"]),
    ("hibob", ["hibob.com", "HiBob"]),
    ("homerun", ["homerun.co", "Homerun"]),
    ("occupop", ["occupop.com", "Occupop"]),
    ("tribepad", ["tribepad.com", "TribePad"]),
    ("lumesse", ["lumesse.com", "recruitmentplatform.com"]),
    ("cezanne", ["cezanneondemand.com", "Cezanne"]),
    ("hireserve", ["hireserve.com", "hireservelive.com"]),
    ("eploy", ["eploy.net", "Eploy"]),
    ("jobylon", ["jobylon.com", "Jobylon"]),
    ("reachmee", ["reachmee.com", "ReachMee"]),
    ("emply", ["emply.com", "Emply"]),
    ("talentlyft", ["talentlyft.com", "TalentLyft"]),
    ("factorial", ["factorialhr.com", "Factorial"]),
    ("otys", ["otys.com", "OTYS"]),
    ("keka", ["keka.com", "Keka"]),
    ("darwinbox", ["darwinbox.in", "darwinbox.com", "Darwinbox"]),
    ("zwayam", ["zwayam.com", "Zwayam"]),
    ("manatal", ["manatal.com", "Manatal"]),
    ("jobsoid", ["jobsoid.com", "Jobsoid"]),
    ("avature", ["avature.net", "Avature"]),
    ("talentsoft", ["talentsoft.com", "talent-soft.com", "TalentSoft"]),
    ("gupy", ["gupy.io", "Gupy"]),
    ("kenoby", ["kenoby.com", "Kenoby"]),
    ("solides", ["solides.jobs", "solides.com", "Sólides", "Solides"]),
    ("inhire", ["inhire.app", "inhire"]),
    ("abler", ["abler.com.br", "Abler"]),
    ("quickin", ["quickin.io", "Quickin"]),
    ("pandape", ["pandape.com", "Pandapé"]),
    ("99jobs", ["99jobs.com"]),
    ("dvinci", ["dvinci-hr.com", "dvinci.de", "d.vinci"]),
    ("rexx", ["rexx-systems.com", "rexx systems"]),
    ("umantis", ["umantis.com", "Haufe"]),
    ("prescreen", ["prescreen.io", "Prescreen"]),
    ("onlyfy", ["onlyfy.jobs", "onlyfy"]),
    ("concludis", ["concludis.de", "concludis"]),
    ("talention", ["talention.com", "Talention"]),
    ("interamt", ["interamt.de", "Interamt"]),
    ("hrone", ["hrone.cloud", "HROne"]),
    ("greythr", ["greythr.com", "greytHR"]),
    ("turbohire", ["turbohire.co", "TurboHire"]),
    ("peoplestrong", ["peoplestrong.com", "PeopleStrong"]),
    ("ceipal", ["ceipal.com", "CEIPAL"]),
    ("oorwin", ["oorwin.com", "Oorwin"]),
    ("livehire", ["livehire.com", "LiveHire"]),
    ("pageup", ["pageuppeople.com", "PageUp"]),
    ("elmo", ["elmotalent.com.au", "ELMO"]),
    ("employmenthero", ["employmenthero.com", "Employment Hero"]),
    ("expr3ss", ["expr3ss.com", "Expr3ss"]),
    ("webcruiter", ["webcruiter.no", "Webcruiter"]),
    ("jobbnorge", ["jobbnorge.no", "Jobbnorge"]),
    ("varbi", ["varbi.com", "Varbi"]),
    ("talentech", ["talentech.com", "Talentech"]),
    ("flatchr", ["flatchr.io", "Flatchr"]),
    ("taleez", ["taleez.com", "Taleez"]),
    ("digitalrecruiters", ["digitalrecruiters.com", "DigitalRecruiters"]),
    ("welcometothejungle", ["welcometothejungle.com", "Welcome to the Jungle"]),
    ("carerix", ["carerix.net", "Carerix"]),
    ("traffit", ["traffit.com", "Traffit"]),
    ("erecruiter", ["erecruiter.pl", "eRecruiter"]),
    ("applicantpro", ["applicantpro.com", "ApplicantPro"]),
    ("applicantstack", ["applicantstack.com", "ApplicantStack"]),
    ("clearcompany", ["clearcompany.com", "ClearCompany"]),
    ("trakstar", ["recruiterbox.com", "trakstar.com", "Trakstar", "Recruiterbox"]),
    ("newton", ["newtonsoftware.com", "Newton"]),
    ("paycor", ["paycor.com", "Paycor"]),
    ("brassring", ["brassring.com", "BrassRing", "Kenexa"]),
    ("silkroad", ["silkroad.com", "openhire.com", "SilkRoad"]),
    ("catsone", ["catsone.com", "CATS Applicant"]),
    ("crelate", ["crelate.com", "Crelate"]),
    ("vincere", ["vincere.io", "Vincere"]),
    ("jobdiva", ["jobdiva.com", "JobDiva"]),
    ("recruitcrm", ["recruitcrm.io", "Recruit CRM"]),
    ("smartsearch", ["smartsearchonline.com", "SmartSearch"]),
    ("workstream", ["workstream.us", "Workstream"]),
    ("fountain", ["fountain.com", "Fountain"]),
    ("hireology", ["hireology.com", "Hireology"]),
    ("paradox", ["paradox.ai", "Paradox"]),
    ("avionte", ["avionte.com", "Avionte"]),
    ("polymer", ["polymer.co", "Polymer"]),
    ("interfolio", ["interfolio.com", "Interfolio"]),
    ("peopleadmin", ["peopleadmin.com", "PeopleAdmin"]),
    ("schooljobs", ["schooljobs.com"]),
    ("governmentjobs", ["governmentjobs.com"]),
]

CAREER_KEYWORDS = [
    r"career",
    r"careers",
    r"jobs?",
    r"join[\s_-]*us",
    r"joinus",
    r"join[\s_-]*(?:our[\s_-]*)?team",
    r"vacancies?",
    r"job-openings",
    r"open-positions",
    r"employment",
    r"working[-\s_]*(with|for|at)",
    r"work[-\s_]*with[-\s_]*us",
    r"we[-\s_]*are[-\s_]*hiring",
    r"hiring",
    r"job[s]?\b",
    r"opportunit",
    r"stellenangebot",
    r"karriere",
    r"trabaj[aá]",
    r"empleo",
    r"offres[-\s]*d[’']emploi",
    r"emploi",
    r"recruit",
    r"recrut",
    r"praca",
    r"kariera",
    r"empleo",
    r"vagas",
    r"carreira",
    r"應聘|招聘",
    r"jobs\.?page",
]

EXCLUDE_PATH_RE = re.compile(
    r"(javascript:|mailto:|tel:|#|\.(jpg|jpeg|png|gif|svg|css|js|pdf|zip|mp4|webp)"
    r"|/wp-content/|/assets/|/static/|/img/|/images/|facebook|linkedin|twitter|instagram"
    r"|youtube|\.xml$|\.json$|/feed|/api/|/login|/signup|/admin)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Is this URL actually a job board?
#
# Vendors put "powered by <ATS>" links in page footers, so a careers page can
# reference e.g. https://www.icims.com/legal/privacy-notice-website/ .  That
# matched the iCIMS host pattern and was dispatched as the company's board:
#
#   ATS handoff .../careers -> https://www.icims.com/legal/privacy-notice-website/
#
# A vendor's own marketing or legal page is never a board.
# ---------------------------------------------------------------------------

_NON_BOARD_PATH_RE = re.compile(
    r"/(?:legal|privacy(?:-\w+)*|terms|tos|cookies?|gdpr|dpa|imprint|impressum|"
    r"about(?:-us)?|blog|news|newsroom|press|resources?|customers?|case-studies|"
    r"pricing|plans|contact(?:-us)?|support|help|docs?|documentation|why-[\w-]+|"
    r"products?|platform|solutions?|partners?|integrations?|events?|webinars?|"
    r"demo|request-demo|signup|sign-up|register|login|sign-in|security|status|"
    r"trust|accessibility|sitemap|glossary|templates?|ebooks?|guides?)(?:/|$|\?|#)",
    re.IGNORECASE,
)

# Asset, download and servlet endpoints.  They match an ATS host but never
# render a job list; one of them (an iCIMS AppInert download servlet) was
# rendered six times at a 90s timeout, twice, blocking both browser workers.
_ASSET_URL_RE = re.compile(
    r"/servlet/|/download|/getfile|/attachment|/asset|/media/|/binaries/|"
    r"/resource/|/export|/rss|/feed|"
    r"[?&](?:action=download|module=AppInert|download=|attachment=|export=)|"
    r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|csv|jpe?g|png|gif|svg|ico|css|js|"
    r"woff2?|ttf|mp4|mp3)(?:$|[?#])",
    re.IGNORECASE,
)

_BOARDISH_PATH_RE = re.compile(
    r"/(?:jobs?|careers?|vacanc\w*|openings?|positions?|opportunit\w*|companies|"
    r"company|apply|recruiting|recruitment|stellen|stellenangebote|karriere|"
    r"emplois?|empleos?|vagas|o|j|p|search)(?:/|$|\?|#|\d)",
    re.IGNORECASE,
)

# Hosts that are the vendor's own website rather than a tenant's board.
# Aggregator and social hosts: never one company's board, whatever the path.
_AGGREGATOR_HOST_RE = re.compile(
    r"(?:^|\.)(?:indeed|glassdoor|linkedin|monster|ziprecruiter|careerbuilder|"
    r"simplyhired|snagajob|dice|totaljobs|reed|cv-library|jobsite|adzuna|"
    r"stepstone|xing|naukri|hirist|shine|timesjobs|foundit|jobstreet|jobsdb|"
    r"seek|jora|wellfound|angel|welcometothejungle|jobteaser|vagas|catho|"
    r"infojobs|computrabajo|bumeran|zonajobs|governmentjobs|schooljobs|"
    r"teamworkonline|facebook|twitter|instagram|youtube|tiktok|pinterest)\.",
    re.IGNORECASE,
)

_VENDOR_CORPORATE_HOST_RE = re.compile(
    r"^(?:www\.)?(?:icims|greenhouse|lever|smartrecruiters|workable|teamtailor|"
    r"recruitee|breezy|jazzhr|jazz|bamboohr|personio|softgarden|workday|taleo|"
    r"successfactors|jobvite|oracle|pinpointhq|zohorecruit|freshteam|jobadder|"
    r"bullhorn|ashbyhq|rippling|csod|cornerstoneondemand|dayforcehcm|ceridian|"
    r"eightfold|phenompeople|paylocity|paycom|paycomonline|ultipro|ukg|adp|"
    r"myisolved|isolved|hibob|homerun|occupop|tribepad|lumesse|cezanneondemand|"
    r"hireserve|eploy|jobylon|reachmee|emply|talentlyft|factorialhr|otys|keka|"
    r"darwinbox|zwayam|manatal|jobsoid|avature|talentsoft|sap|gupy|kenoby|"
    r"solides|inhire|abler|quickin|pandape|dvinci|rexx-systems|umantis|"
    r"prescreen|onlyfy|concludis|talention|hrone|greythr|turbohire|"
    r"peoplestrong|ceipal|oorwin|livehire|pageup|pageuppeople|elmotalent|"
    r"employmenthero|expr3ss|webcruiter|jobbnorge|varbi|talentech|flatchr|"
    r"taleez|digitalrecruiters|softy|beetween|carerix|traffit|erecruiter|"
    r"applicantpro|applicantstack|clearcompany|trakstar|recruiterbox|"
    r"newtonsoftware|paycor|brassring|silkroad|openhire|catsone|crelate|"
    r"vincere|jobdiva|recruitcrm|smartsearchonline|workstream|fountain|"
    r"hireology|paradox|avionte|polymer|interfolio|peopleadmin)\."
    r"(?:com|io|co|hr|net|ai|de|eu|in|org|jobs|com\.au|co\.uk)$",
    re.IGNORECASE,
)


# Asset-delivery hosts.  A careers page loads its ATS widget from the vendor's
# CDN, and "https://cdn.phenompeople.com" was being dispatched as the board.
_CDN_HOST_RE = re.compile(
    r"^(?:cdn\d*|static\d*|assets?|media|img|images|js|css|scripts?|fonts?|"
    r"cdn-\w+|akamai\w*|cloudfront|edge)\.", re.IGNORECASE)


def is_probable_board_url(url):
    """Reject vendor marketing/legal pages that merely match an ATS host."""
    if not url:
        return False
    try:
        parsed = urlparse(ensure_https(url))
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if _ASSET_URL_RE.search(path + ("?" + parsed.query if parsed.query else "")):
        return False
    if _NON_BOARD_PATH_RE.search(path):
        return False
    if _CDN_HOST_RE.match(host):
        return False
    if _AGGREGATOR_HOST_RE.search(host):
        return False
    vendor, _captured = detect_ats_in_url(url)
    if vendor and not is_company_board_vendor(vendor):
        return False
    if _VENDOR_CORPORATE_HOST_RE.match(host):
        # The vendor's own domain only counts when the path names a board
        # (join.com/companies/acme, recruiting.paylocity.com/recruiting/jobs/...).
        return bool(_BOARDISH_PATH_RE.search(path))
    return True

# ---------------------------------------------------------------------------
# Vendor renames and aliases.
#
# The market consolidates constantly, so the same board shows up under several
# names.  Detection keys off the URL host, but the `source` column should read
# consistently, and it is the canonical name that gets reported.
# ---------------------------------------------------------------------------
ATS_ALIASES = {
    "oracle_taleo": "taleo",
    "sap_successfactors": "successfactors",
    "sap successfactors": "successfactors",
    "sap success factors": "successfactors",
    "success factors": "successfactors",
    "successfactors": "successfactors",
    "infinite talent": "brassring",       # Infinite Talent = Infinite BrassRing
    "infinite brassring": "brassring",
    "kenexa": "brassring",
    "keldairhr": "keldair",
    "hyrell": "keldair",                  # Hyrell -> KeldairHR
    "rival": "silkroad",                  # Rival = formerly SilkRoad Technology
    "silkroad technology": "silkroad",
    "openhire": "silkroad",
    "sprockets": "sprockets",
    "sentio": "sprockets",                # SENTIO -> Sprockets
    "greenhouse recruiting": "greenhouse",
    "monday.com": "jobflows",
    "recruitment crm": "recruitcrm",
    "recruiterbox": "trakstar",           # Recruiterbox -> Mitratech Trakstar
    "mitratech trakstar": "trakstar",
    "access vincere evo": "vincere",
    "access peoplehr": "peoplehr",
    "cegid digitalrecruiters": "digitalrecruiters",
    "dayforce hcm": "dayforce",
    "ceridian": "dayforce",
    "api healthcare": "symplr",
    "schoolspring": "applitrack",         # both are Frontline Education
    "frontline": "applitrack",
    "ultipro": "ukg",
    "kronos": "ukg",
    "haufe": "umantis",
    "xing e-recruiting": "onlyfy",
    "prescreen": "onlyfy",                # Prescreen is now part of onlyfy
    "cornerstone ondemand": "cornerstone",
    "csod": "cornerstone",
    "isolved talent acquisition": "isolved",
    "oracle cloud hcm": "oracle",
    "oracle recruiting cloud": "oracle",
    "freshworks": "freshteam",
    "employ": "jobvite",                  # Employ Inc owns Jobvite/JazzHR/Lever
    "powerschool applicant tracking": "powerschool",
}


def canonical_ats_name(name):
    """Normalise a vendor name to the one used in the `source` column.

    Tolerates the spellings these names appear in: "Oracle Taleo",
    "oracle_taleo", "SAP SuccessFactors", "sap-successfactors".
    """
    if not name:
        return ""
    raw = str(name).strip().lower()
    spaced = re.sub(r"[\s_\-]+", " ", raw).strip()
    underscored = spaced.replace(" ", "_")
    collapsed = spaced.replace(" ", "")
    for key in (raw, spaced, underscored, collapsed):
        if key in ATS_ALIASES:
            return ATS_ALIASES[key]
    return underscored


# ---------------------------------------------------------------------------
# Detected, but never a company's own job board.
#
# Job aggregators, sourcing tools, assessment vendors and payroll suites all
# appear on "ATS lists", but a link to one is not a company careers board --
# treating it as one attaches other companies' postings to this company.
# ---------------------------------------------------------------------------
NON_COMPANY_BOARD_VENDORS = {
    # aggregators / marketplaces
    "indeed", "adzuna", "careerbuilder", "wellfound", "monster", "ziprecruiter",
    "glassdoor", "linkedin", "stepstone", "xing", "naukri", "hirist",
    "jobteaser", "vagas", "teamworkonline", "governmentjobs", "schooljobs",
    # sourcing / CRM / outbound tools (no public board)
    "hireez", "gem", "fetcher", "findem", "signalhire", "visage", "loxo",
    "recruiterpm", "jobin_cloud",
    # assessment / interview / screening
    "criteria", "outmatch", "interviewer_ai", "sparkhire", "hirescore",
    "grayscale", "chattr", "paradox", "genfuse_ai",
    # payroll / HCM without a hosted careers board
    "paychex", "moorepay", "viventium", "epay", "insperity", "trinet",
    "paypro", "ascender", "buk",
}


def is_company_board_vendor(name):
    """True when a board from this vendor belongs to one company."""
    return canonical_ats_name(name) not in NON_COMPANY_BOARD_VENDORS

def detect_ats_in_url(url):
    url = ensure_https(url or "")
    for ats, pat in ATS_URL_PATTERNS:
        m = pat.search(url)
        if m:
            cap = m.group(1) if m.groups() else ""
            return ats, cap
    return None, None


def _detect_ats_in_text(text):
    if not text:
        return None
    low = text
    for ats, markers in ATS_HTML_MARKERS:
        for mk in markers:
            if mk in low:
                return ats
    return None


def ats_urls_in_html(html, base_url):
    """Every recognised ATS board URL referenced anywhere in a page.

    Boards are commonly embedded rather than linked -- an <iframe src>, a
    widget <script src>, or a <form action>.  Scanning only <a href> missed
    all of those, and no vendor pattern list can keep up with new hosts, so
    every URL-bearing attribute is checked.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()

    def consider(raw):
        if not raw:
            return
        full = url_join(base_url, raw) if not raw.startswith("http") else raw
        if not full or full in seen:
            return
        name, captured = detect_ats_in_url(full)
        if name and is_probable_board_url(full):
            seen.add(full)
            found.append((name, captured, full))

    for tag, attr in (("a", "href"), ("iframe", "src"), ("frame", "src"),
                      ("script", "src"), ("form", "action"), ("link", "href"),
                      ("embed", "src"), ("object", "data"), ("area", "href")):
        for element in soup.find_all(tag):
            consider(element.get(attr))

    # data-* attributes and inline JS often carry the board URL too.
    for element in soup.find_all(True):
        for key, value in (element.attrs or {}).items():
            if isinstance(value, str) and key.startswith("data-") and "http" in value:
                match = re.search(r"https?://[^\s\"'<>]+", value)
                if match:
                    consider(match.group(0))

    for match in re.finditer(r"https?://[^\s\"'<>()]{6,300}", html):
        consider(match.group(0))
    return found


def is_career_link(text, href):
    hay = " ".join([(text or "").lower(), (href or "").lower()])
    for kw in CAREER_KEYWORDS:
        if re.search(kw, hay):
            return True
    return False


def is_career_page(url):
    """Common direct career paths that are worth probing."""
    if not url:
        return False
    path = urlparse(url).path.lower().rstrip("/")
    for seg in ["/careers", "/career", "/jobs", "/join-us", "/joinus", "/jobs/careers",
                "/careers/jobs", "/job-openings", "/work-with-us", "/vacancies",
                "/career/jobs", "/karriere", "/stellenangebote", "/career-opportunities",
                "/join-our-team", "/careers-at", "/about/careers", "/recruiting",
                "/joblist", "/jobs2", "/job", "/stellesuche", "/trabaja", "/empleo"]:
        if path == seg or path.startswith(seg + "/"):
            return True
    return False


# Second-level labels that are effectively public suffixes: a domain under
# them needs three labels, not two.  "vortice.inf.br" was being reduced to
# "inf.br", which made every .inf.br site look like the same company and
# turned the discovery query into "site:inf.br careers jobs".
_MULTI_TLD = {
    # generic second levels used worldwide
    "co", "com", "org", "net", "gov", "gob", "ac", "edu", "mil", "gen",
    "info", "biz", "name", "int", "nom", "or", "ne", "go", "asn", "id",
    "sch", "plc", "ltd", "me", "web", "firm", "store", "rec",
    # Brazil (.br has many)
    "inf", "ind", "eng", "adv", "art", "esp", "etc", "far", "fot", "fst",
    "g12", "imb", "jor", "lel", "med", "mus", "not", "ntr", "odo", "ppg",
    "psi", "qsl", "slg", "srv", "teo", "tmp", "trd", "tur", "vet", "zlg",
    "agr", "am", "arq", "ato", "bio", "bmd", "cim", "cng", "cnt", "ecn",
    "emp", "eti", "fm", "fnd", "leg", "mat", "pro", "radio", "rec", "taxi",
    # other country-specific second levels
    "waw", "priv", "realestate", "com-br",
}


def registrable_domain(host):
    """Best-effort registrable domain (eTLD+1).

    Uses tldextract's bundled suffix list when available (no network access
    required), and falls back to the static table above otherwise.
    """
    host = (host or "").lower().strip().rstrip(".")
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]

    extractor = _get_tld_extractor()
    if extractor is not None:
        try:
            result = extractor(host)
            if result.domain and result.suffix:
                return "%s.%s" % (result.domain, result.suffix)
        except Exception:
            pass

    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _MULTI_TLD and len(parts[-1]) <= 3:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


# ---------------------------------------------------------------------------
# Vendor-agnostic board detection.
#
# There are several hundred ATS products and the market changes monthly, so a
# name list can never be complete.  This decides whether a page *behaves* like
# a job board, whoever built it -- which is what actually matters.
# ---------------------------------------------------------------------------

_BOARD_LINK_RE = re.compile(
    r"/job[s]?/[a-zA-Z0-9_\-]{2,}|/vacanc(?:y|ies)/[^/?#]+|/position[s]?/[^/?#]+|"
    r"/openings?/[^/?#]+|/stellenangebote?/[^/?#]+|/vagas?/[^/?#]+|"
    r"/offre[s]?-d-emploi/[^/?#]+|[?&](?:job|jobid|job_id|vacancy|posting|"
    r"requisition|reqid)(?:id)?=[^&]+",
    re.IGNORECASE,
)


def looks_like_job_board(html, url="", min_signals=2):
    """Does this page behave like a list of job postings?

    Signals, any two of which are enough:
      * two or more JobPosting records in JSON-LD or microdata
      * three or more links that look like individual postings
      * repeated posting-shaped containers
      * board vocabulary in the title or headings alongside apply language
    """
    if not html or len(html) < 400:
        return False

    signals = 0
    if len(re.findall(r'["\']?@type["\']?\s*:\s*["\']JobPosting', html, re.I)) >= 2:
        signals += 2
    if len(re.findall(r'itemtype=["\'][^"\']*schema\.org/JobPosting', html, re.I)) >= 2:
        signals += 2

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return signals >= min_signals

    posting_links = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if _BOARD_LINK_RE.search(href):
            posting_links.add(href.split("#")[0])
    if len(posting_links) >= 3:
        signals += 1
    if len(posting_links) >= 8:
        signals += 1

    # Repeated posting-shaped containers (a list, not a single page).
    containers = soup.find_all(
        ["li", "tr", "article", "div"],
        class_=re.compile(r"(?:job|vacanc|position|opening|posting|stelle|vaga)", re.I))
    if len(containers) >= 4:
        signals += 1

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    headings = " ".join(tag.get_text(" ", strip=True)
                        for tag in soup.find_all(["h1", "h2"], limit=8))
    heading_text = (title + " " + headings).lower()
    if re.search(r"job|caree?r|vacanc|position|opening|stelle|vaga|emploi|empleo",
                 heading_text):
        body = soup.get_text(" ", strip=True)[:20000].lower()
        if re.search(r"\bapply\b|\bapplication\b|bewerb|candidat|aplique|postul",
                     body):
            signals += 1

    return signals >= min_signals

def find_career_links(soup, base_url, limit=10):
    """Return official-domain or recognized ATS career links from a homepage."""
    found = []
    seen = set()
    if soup:
        base_host = urlparse(ensure_https(base_url)).hostname or ""
        base_dom = registrable_domain(base_host)
        anchors = soup.find_all("a", href=True)
        for a in anchors:
            text = " ".join(a.get_text(" ", strip=True).split())
            href = a["href"]
            full = url_join(base_url, href)
            if not full:
                continue
            external_ats, _ = detect_ats_in_url(full)
            if not is_career_link(text, href) and not external_ats:
                continue
            if EXCLUDE_PATH_RE.search(full):
                continue
            full_host = urlparse(full).hostname or ""
            same_domain = not base_dom or not full_host or registrable_domain(full_host) == base_dom
            if not same_domain and not external_ats:
                continue
            key = full.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            found.append((text, key))
    # Prioritize links whose text/href is strongly a careers/jobs page.
    def score(item):
        t, u = item
        s = 0
        if "career" in t or "career" in u:
            s += 3
        if "job" in t or "job" in u:
            s += 2
        if "join" in t or "work" in t:
            s += 1
        return -s

    found.sort(key=score)
    return [u for _, u in found[:limit]]


def validate_career_page(url, html_text, company_home_url=""):
    """Validate a career candidate using domain ownership and page evidence."""
    if not url or not html_text:
        return False
    candidate_host = urlparse(ensure_https(url)).hostname or ""
    company_host = urlparse(ensure_https(company_home_url)).hostname or ""
    same_domain = bool(candidate_host and company_host and
                       registrable_domain(candidate_host) == registrable_domain(company_host))
    ats, _ = detect_ats_in_url(url)
    soup = BeautifulSoup(html_text, "html.parser")
    visible = soup.get_text(" ", strip=True)
    if re.search(r"\b(page not found|404 not found|page does not exist|seite nicht gefunden|"
                 r"page introuvable|página no encontrada)\b", visible, re.I):
        return False
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    headings = " ".join(tag.get_text(" ", strip=True) for tag in soup.find_all(["h1", "h2", "h3"]))
    page_signal = is_career_link(title + " " + headings, "")
    jobposting_signal = bool(re.search(r'[@\"\']type[\"\']?\s*:\s*[\"\']JobPosting', html_text, re.I))
    main = soup.find("main") or soup.find("article")
    job_link_signal = bool(main and any(
        is_career_link(a.get_text(" ", strip=True), a.get("href", ""))
        for a in main.find_all("a", href=True)[:500]))
    return bool((same_domain or ats) and (ats or page_signal or jobposting_signal or job_link_signal))


def common_career_urls(base_url):
    """Probe common career paths directly."""
    host = urlparse(base_url).netloc or urlparse(base_url).path
    scheme = urlparse(base_url).scheme or "https"
    base = "%s://%s" % (scheme, host)
    paths = [
        "/careers", "/career", "/jobs", "/jobs/careers", "/careers/jobs",
        "/join-us", "/career-opportunities", "/work-with-us", "/vacancies",
        "/join-our-team", "/about/careers", "/career/jobs", "/karriere",
        "/stellenangebote", "/job-openings", "/careers-at", "/recruiting",
    ]
    out = []
    for p in paths:
        out.append((base + p, 0))
    return out
