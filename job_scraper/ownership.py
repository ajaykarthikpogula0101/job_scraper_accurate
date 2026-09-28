"""Does this website / job board / page actually belong to this company?

Every wrong row in the delivered file traced back to the same hole: a
fallback (web search, domain guess, ATS-board search) found *a* career page
and nothing checked that it was *this company's* career page.  One Eurofins
SmartRecruiters posting ended up attached to 124 unrelated companies, and
state.gov became the career site of "STATE PHARMACEUTICALS CORPORATION OF SRI
LANKA" because they share the word "state".

The rules here are deliberately conservative.  A company with no usable
website and a name made only of generic words ("City of Boston") cannot be
verified by name alone, and the correct answer for it is *no jobs* rather than
somebody else's jobs.
"""
import re

from bs4 import BeautifulSoup

from .fields import clean_text

# Legal-form and filler words: never evidence of anything.
NOISE_TOKENS = {
    "the", "and", "of", "for", "ltd", "llc", "gmbh", "inc", "corp", "co",
    "sa", "oy", "ab", "bv", "nv", "plc", "limited", "company", "group",
    "holding", "holdings", "srl", "ag", "kg", "sas", "spa", "pty", "pvt",
    "private", "sdn", "bhd", "ooo", "zao", "doo", "sro", "kft", "corporation",
    "ltda", "eirl", "cia", "sarl", "sl", "aps", "as", "oyj", "kk", "dba",
    "mbh", "gesellschaft", "societe", "sociedad", "societa", "compania",
    "companhia", "incorporated", "lp", "llp", "pc", "pa", "de", "del", "la",
    "le", "les", "das", "der", "die", "und", "et", "y", "e", "do", "da",
    "dos", "das", "van", "von", "di", "al", "el", "sp", "z", "o", "spolka",
    "akcyjna", "zoo", "ltd.", "s.a.", "s.r.l.", "b.v.", "n.v.", "a.s.",
    "branch", "office", "division", "subsidiary", "affiliate", "franchise",
}

# Words so common in organisation names that sharing one with a domain, a
# board slug or a page is not evidence of ownership.  A single one of these is
# never enough; only *distinctive* tokens count towards a match.
GENERIC_TOKENS = {
    # geography / civic
    "virginia", "carolina", "georgia", "california", "texas", "florida",
    "washington", "london", "paris", "berlin", "madrid", "dublin", "sydney",
    "toronto", "america", "american", "national", "international", "federal",
    "state", "states", "united", "central", "north", "south", "east", "west",
    "northern", "southern", "eastern", "western", "city", "county", "district",
    "regional", "region", "province", "provincial", "municipal", "municipality",
    "town", "township", "village", "borough", "parish", "metro", "metropolitan",
    "kingdom", "republic", "europe", "european", "asia", "asian", "africa",
    "african", "pacific", "atlantic", "global", "world", "worldwide", "usa",
    "canada", "canadian", "india", "indian", "brazil", "brasil", "brazilian",
    "germany", "german", "deutschland", "deutsche", "deutsches", "france",
    "french", "italia", "italy", "italian", "espana", "spain", "spanish",
    "japan", "japanese", "china", "chinese", "australia", "australian",
    "mexico", "mexican", "korea", "korean", "polska", "poland", "polish",
    "nederland", "netherlands", "dutch", "swiss", "schweiz", "suisse",
    "britain", "british", "england", "english", "scotland", "scottish",
    "wales", "welsh", "ireland", "irish", "sweden", "swedish", "sverige",
    "norway", "norwegian", "norge", "denmark", "danish", "danmark",
    "finland", "finnish", "suomi", "austria", "austrian", "belgium",
    "belgian", "portugal", "portuguese", "turkey", "turkish", "greece",
    "greek", "hellas", "israel", "israeli", "arabia", "saudi", "emirates",
    "singapore", "malaysia", "indonesia", "thailand", "vietnam",
    "philippines", "pakistan", "bangladesh", "lanka", "nigeria", "kenya",
    "egypt", "morocco", "chile", "colombia", "peru", "argentina",
    # cities
    "vancouver", "montreal", "calgary", "ottawa", "quebec", "chicago",
    "boston", "austin", "denver", "seattle", "portland", "phoenix", "atlanta",
    "dallas", "houston", "miami", "detroit", "richmond", "orlando",
    "york", "jersey", "angeles", "francisco", "diego", "vegas", "philadelphia",
    "baltimore", "cleveland", "pittsburgh", "minneapolis", "milwaukee",
    "louis", "kansas", "nashville", "memphis", "charlotte", "raleigh",
    "tampa", "jacksonville", "columbus", "cincinnati", "indianapolis",
    "mumbai", "delhi", "bangalore", "bengaluru", "chennai", "kolkata", "pune",
    "hyderabad", "ahmedabad", "dubai", "tokyo", "osaka", "shanghai",
    "beijing", "seoul", "jakarta", "manila", "bangkok", "munich", "muenchen",
    "hamburg", "frankfurt", "cologne", "koeln", "stuttgart", "vienna", "wien",
    "zurich", "geneva", "milan", "milano", "rome", "roma", "naples",
    "barcelona", "lisbon", "lisboa", "amsterdam", "rotterdam", "brussels",
    "antwerp", "copenhagen", "stockholm", "oslo", "helsinki", "warsaw",
    "krakow", "prague", "praha", "budapest", "bucharest", "bucuresti",
    "sofia", "athens", "istanbul", "ankara", "cairo", "lagos", "nairobi",
    "johannesburg", "pretoria", "durban", "limpopo", "gauteng", "melbourne",
    "brisbane", "perth", "adelaide", "auckland", "wellington", "brandenburg",
    "bavaria", "bayern", "saxony", "sachsen", "hessen", "ontario", "alberta",
    "yorkshire", "manchester", "birmingham", "glasgow", "edinburgh", "leeds",
    "liverpool", "bristol", "sao", "paulo", "rio", "janeiro", "minas",
    "gerais", "parana", "bahia", "santa", "san", "las", "los", "nova", "novo",
    # sector / organisation words
    "college", "university", "school", "schools", "institute", "institution",
    "academy", "kindergarten", "nursery", "primary", "secondary", "elementary",
    "high", "education", "educational", "training", "learning", "research",
    "hospital", "hospitals", "medical", "clinic", "clinics", "health",
    "healthcare", "care", "dental", "pharmacy", "pharmaceutical",
    "pharmaceuticals", "pharma", "physicians", "physician", "surgical",
    "medicine", "wellness", "nursing", "rehabilitation", "therapy",
    "behavioral", "behavioural", "mental", "senior", "seniors", "living",
    "center", "centre", "centers", "centres", "services", "service",
    "solutions", "systems", "system", "technologies", "technology", "tech",
    "technical", "industries", "industrial", "industry", "digital",
    "consulting", "consultants", "consultancy", "advisors", "advisory",
    "partners", "associates", "enterprises", "enterprise", "ventures",
    "trading", "commerce", "comercio", "industria", "bank", "banco",
    "banking", "energy", "power", "electric", "electrical", "electronics",
    "water", "environmental", "resources", "foundation", "trust", "council",
    "agency", "agencies", "authority", "department", "dept", "ministry",
    "government", "public", "general", "management", "development",
    "engineering", "engineers", "business", "commercial", "financial",
    "finance", "capital", "investment", "investments", "insurance",
    "assurance", "mutual", "properties", "property", "realty", "real",
    "estate", "construction", "builders", "building", "logistics",
    "transport", "transportation", "shipping", "marine", "products",
    "manufacturing", "marketing", "communications", "communication", "media",
    "software", "network", "networks", "data", "cloud", "law", "legal",
    "accounting", "home", "homes", "auto", "motors", "motor", "food", "foods",
    "hotel", "hotels", "restaurant", "market", "markets", "supply", "chain",
    "oil", "gas", "mining", "steel", "metal", "metals", "chemical",
    "chemicals", "plastics", "paper", "textile", "textiles", "apparel",
    "fashion", "retail", "wholesale", "distribution", "distributors",
    "import", "export", "trade", "traders", "universal", "premier", "first",
    "new", "one", "best", "top", "pro", "plus", "info", "net", "web",
    "online", "mobile", "smart", "green", "blue", "red", "gold", "silver",
    "star", "sun", "royal", "crown", "king", "union", "alliance", "family",
    "community", "children", "childrens", "youth", "women", "veterans",
    "board", "commission", "committee", "chamber", "federation", "league",
    "club", "society", "association", "organisation", "organization", "fund",
    "funds", "charity", "charitable", "mission", "church", "diocese",
    "catholic", "christian", "baptist", "lutheran", "methodist",
    "presbyterian", "jewish", "islamic", "temple", "ministries", "fellowship",
    "credit", "savings", "loan", "mortgage", "brokers", "brokerage",
    "staffing", "recruitment", "recruiting", "employment", "labor", "labour",
    "works", "utilities", "utility", "housing", "port", "airport", "transit",
    "railway", "rail", "roads", "highway", "bridge", "library", "museum",
    "arts", "art", "theatre", "theater", "music", "sports", "sport", "golf",
    "country", "parks", "park", "recreation", "fire", "police", "sheriff",
    "court", "courts", "justice", "corrections", "elections", "election",
    "revenue", "tax", "treasury", "treasurer", "secretary", "clerk",
    "office", "offices", "administration", "administrative", "executive",
    "human", "social", "welfare", "aid", "relief", "emergency", "safety",
    "security", "defense", "defence", "army", "navy", "air", "force",
    "guard", "national", "veteran", "memorial", "regional", "district",
    "unified", "independent", "consolidated", "joint", "cooperative", "coop",
    "co-op", "collective", "assembly", "ltd", "trust", "team", "teams",
    "lab", "labs", "laboratory", "laboratories", "sciences", "science",
    "scientific", "biotech", "bio", "life", "clinical", "diagnostics",
    "diagnostic", "imaging", "radiology", "oncology", "cardiology",
    "orthopedic", "orthopaedic", "pediatric", "paediatric", "veterinary",
    "animal", "pet", "farm", "farms", "agri", "agro", "agricultural",
    "agriculture", "seed", "seeds", "garden", "gardens", "forest", "forestry",
    "timber", "wood", "stone", "glass", "cement", "concrete", "machinery",
    "machine", "machines", "equipment", "tools", "tool", "parts", "components",
    "packaging", "printing", "print", "press", "publishing", "publishers",
    "books", "news", "radio", "television", "tv", "film", "studio", "studios",
    "design", "designs", "creative", "brand", "brands", "beauty", "cosmetics",
    "salon", "spa", "fitness", "gym", "travel", "tours", "tourism", "cruise",
    "airlines", "airline", "aviation", "aerospace", "automotive", "trucking",
    "freight", "cargo", "express", "courier", "post", "postal", "mail",
    "telecom", "telecommunications", "wireless", "cable", "satellite",
    "internet", "hosting", "computer", "computers", "computing", "it",
    "ai", "analytics", "intelligence", "automation", "robotics", "controls",
    "instruments", "instrument", "devices", "device", "medtech", "surgery",
    "optical", "vision", "eye", "hearing", "skin", "hair", "baby", "kids",
    "toys", "games", "gaming", "casino", "lottery", "bingo", "betting",
    "wine", "beer", "brewing", "brewery", "distillery", "spirits", "coffee",
    "tea", "bakery", "dairy", "meat", "seafood", "fish", "fruit", "fruits",
    "vegetables", "grocery", "supermarket", "store", "stores", "shop",
    "shops", "mall", "outlet", "outlets", "fashion", "shoes", "jewelry",
    "jewellery", "watches", "furniture", "interiors", "kitchen", "bath",
    "lighting", "lights", "solar", "wind", "nuclear", "petroleum", "fuel",
    "fuels", "coal", "hydro", "waste", "recycling", "cleaning", "facility",
    "facilities", "maintenance", "repair", "installation", "contractors",
    "contracting", "plumbing", "heating", "cooling", "hvac", "roofing",
    "painting", "flooring", "landscaping", "pest", "moving", "storage",
    "warehouse", "warehousing", "rental", "rentals", "leasing", "lease",
    "dealers", "dealer", "dealership", "sales", "trading", "exchange",
    "securities", "asset", "assets", "wealth", "equity", "partners", "venture",
    "holdings", "portfolio", "pension", "pensions", "benefits", "payroll",
    "hr", "talent", "people", "workforce", "career", "careers", "jobs", "job",
    "work", "works", "labor", "professional", "professionals", "experts",
    "expert", "specialists", "specialist", "quality", "premium", "select",
    "choice", "value", "prime", "elite", "superior", "advanced", "modern",
    "classic", "traditional", "heritage", "legacy", "pioneer", "frontier",
    "summit", "peak", "apex", "vertex", "core", "central", "main", "grand",
    "great", "big", "little", "small", "mini", "micro", "macro", "mega",
    "ultra", "super", "hyper", "max", "total", "complete", "full", "all",
    "every", "any", "true", "real", "pure", "simple", "easy", "fast", "quick",
    "direct", "instant", "express", "rapid", "swift", "speed", "flex",
    "flexible", "dynamic", "active", "action", "motion", "energy", "spark",
    "bright", "light", "clear", "crystal", "diamond", "pearl", "ruby",
    "emerald", "sapphire", "platinum", "titanium", "iron", "copper", "bronze",
    "oak", "pine", "maple", "cedar", "willow", "rose", "lily", "lotus",
    "eagle", "falcon", "hawk", "lion", "tiger", "bear", "wolf", "fox",
    "horse", "bull", "phoenix", "dragon", "river", "lake", "ocean", "sea",
    "bay", "harbor", "harbour", "island", "mountain", "valley", "hill",
    "hills", "ridge", "canyon", "desert", "prairie", "meadow", "field",
    "fields", "grove", "woods", "spring", "springs", "creek", "brook",
    "falls", "rapids", "point", "cape", "coast", "coastal", "shore",
    "beach", "sunrise", "sunset", "horizon", "sky", "skyline", "cloud",
    "storm", "thunder", "lightning", "rain", "snow", "ice", "frost", "fire",
    "flame", "blaze", "heat", "cool", "fresh", "clean", "safe", "secure",
    "strong", "solid", "steady", "stable", "reliable", "trusted", "honest",
    "fair", "just", "right", "good", "better", "excellent", "perfect",
    "ideal", "optimal", "optimum", "prime", "ace", "alpha", "beta", "delta",
    "omega", "sigma", "zeta", "nova", "stellar", "cosmic", "galaxy",
    "planet", "earth", "terra", "geo", "eco", "enviro", "green", "natural",
    "nature", "organic", "wild", "free", "liberty", "freedom", "victory",
    "triumph", "success", "progress", "advance", "forward", "future", "next",
    "modern", "innovative", "innovation", "innovations", "creative",
    "creations", "concepts", "concept", "ideas", "idea", "vision", "visions",
    "insight", "insights", "focus", "target", "goal", "aim", "quest",
    "journey", "path", "way", "road", "street", "avenue", "lane", "drive",
    "place", "plaza", "square", "circle", "court", "terrace", "gardens",
    "estates", "manor", "hall", "house", "lodge", "inn", "resort", "villa",
    "tower", "towers", "plaza", "gate", "gateway", "portal", "hub", "link",
    "links", "connect", "connection", "connections", "bridge", "bridges",
    "unity", "united", "union", "one", "first", "second", "third", "1st",
    "2nd", "3rd", "a", "b", "c", "i", "ii", "iii", "iv", "v", "x",
    "screen", "actors", "guild", "radio", "television", "artists",
    # "informatica" / "informatique" / "informatik" mean IT in Portuguese,
    # Spanish, Italian, French and German: not a brand.
    "informatica", "informatique", "informatik", "informatics", "informatica",
    "sistemas", "sistemi", "systemes", "tecnologias", "tecnologie",
    "telecomunicacoes", "telecomunicaciones", "telecomunicazioni", "comunicacoes",
    "comunicaciones", "comunicazioni", "digitale", "digitais", "digitales",
    "servicos", "servizi", "software", "hardware", "computacao", "computacion",
    "automacao", "automatizacion", "eletrica", "electrica", "elettrica",
    "eletronica", "electronica", "elettronica", "mecanica", "meccanica",
    "quimica", "chimica", "farmacia", "farmaceutica", "farmaceutico",
    "alimentar", "alimentares", "alimentacion", "textil", "tessile",
    "metalurgica", "siderurgica", "mineracao", "mineria", "petroleo", "energias",
    "ambiente", "ambientale", "agricola", "florestal", "forestal", "pecuaria",
    "consultores", "consultoria", "assessoria", "asesoria", "auditoria",
    "contabilidade", "contabilidad", "contabilita", "advogados", "abogados",
    "avvocati", "imobiliaria", "inmobiliaria", "immobiliare", "construtora",
    "constructora", "costruzioni", "incorporadora", "participacoes",
    "investimentos", "inversiones", "investimenti", "seguros", "seguridad",
    "seguranca", "sicurezza", "vigilancia", "limpeza", "limpieza", "pulizie",
    "transportes", "transporte", "trasporti", "logistica", "distribuicao",
    "distribucion", "distribuzione", "atacado", "varejo", "comercio",
    "commercio", "importacao", "exportacao", "importacion", "exportacion",
    "representacoes", "representaciones", "rappresentanze", "agencia",
    "agenzia", "editora", "editorial", "editrice", "grafica", "publicidade",
    "publicidad", "pubblicita", "marketing", "eventos", "eventi", "turismo",
    "viagens", "viajes", "viaggi", "hotelaria", "hoteleria", "restaurante",
    "ristorante", "padaria", "panaderia", "supermercado", "supermercados",
    "mercado", "mercados", "mercato", "loja", "lojas", "tienda", "tiendas",
    "negozio", "oficina", "officina", "taller", "fabrica", "fabbrica",
    "usina", "moinho", "molino", "mulino", "cooperativa", "associados",
    "asociados", "associati", "irmaos", "hermanos", "fratelli", "filhos",
    "hijos", "figli", "cia", "companhia",
    # Portuguese / Spanish
    "instituto", "universidade", "universidad", "escola", "escuela",
    "colegio", "clinica", "fundacao", "fundacion", "associacao", "asociacion",
    "sociedade", "empresa", "empresas", "servicos", "servicios", "comercial",
    "nacional", "internacional", "brasileiro", "brasileira", "governanca",
    "corporativa", "corporativo", "prefeitura", "municipio", "municipal",
    "secretaria", "ministerio", "departamento", "centro", "grupo",
    "tecnologia", "sistemas", "solucoes", "soluciones", "consultoria",
    "engenharia", "ingenieria", "construcao", "construccion", "transportes",
    "logistica", "alimentos", "farmaceutica", "laboratorio", "laboratorios",
    "medica", "medico", "saude", "salud", "energia", "ambiental", "agricola",
    "agropecuaria", "financeira", "seguros", "cooperativa", "sindicato",
    "camara", "conselho", "consejo", "instituicao", "institucion", "estado",
    "governo", "gobierno", "publica", "publico", "regional", "federal",
    "escritorio", "oficina", "productos", "produtos", "industrias",
    "industrial", "comercio", "distribuidora", "importadora", "exportadora",
    "participacoes", "administracao", "administracion", "gestao", "gestion",
    "educacao", "educacion", "ensino", "pesquisa", "investigacion", "hospital",
    "santa", "casa", "misericordia", "igreja", "iglesia", "paroquia",
    # German
    "gesellschaft", "stiftung", "verein", "verband", "klinik", "klinikum",
    "krankenhaus", "schule", "hochschule", "universitat", "universitaet",
    "fachhochschule", "gymnasium", "realschule", "grundschule", "berufsschule",
    "stadt", "gemeinde", "landkreis", "kreis", "amt", "ministerium",
    "behorde", "behoerde", "bundes", "landes", "deutsche", "deutscher",
    "technik", "technologie", "systeme", "losungen", "loesungen", "beratung",
    "dienstleistungen", "dienste", "industrie", "handel", "bau", "logistik",
    "energie", "immobilien", "versicherung", "sparkasse", "volksbank",
    "raiffeisenbank", "genossenschaft", "werke", "werk", "fabrik", "praxis",
    "zentrum", "institut", "akademie", "kirche", "evangelische",
    "katholische", "pflege", "gesundheit", "medizin", "medizinische",
    # French
    "societe", "universite", "ecole", "lycee", "hopital", "clinique",
    "fondation", "mairie", "ville", "conseil", "ministere", "departement",
    "groupe", "assurances", "banque", "caisse", "mutuelle", "cabinet",
    "laboratoire", "laboratoires", "pharmacie", "sante", "transports",
    "logistique", "immobilier", "gestion", "formation", "recherche",
    "nationale", "internationale", "generale", "commerciale", "municipale",
    "regionale", "provinciale", "federale", "agence", "bureau", "centre",
    "communaute", "communes", "commune", "syndicat", "chambre", "metiers",
    "industrie", "industries", "batiment", "travaux", "publics",
    # Italian
    "societa", "ospedale", "universita", "scuola", "comune", "provincia",
    "regione", "azienda", "aziende", "servizi", "laboratori", "fondazione",
    "istituto", "associazione", "consorzio", "cooperativa", "banca", "cassa",
    "assicurazioni", "costruzioni", "tecnologie", "sistemi", "soluzioni",
    "commerciale", "nazionale", "internazionale", "generale", "pubblica",
    # Dutch
    "stichting", "vereniging", "gemeente", "ziekenhuis", "universiteit",
    "hogeschool", "bedrijf", "bedrijven", "groep", "diensten", "zorg",
    "onderwijs", "scholen", "gemeenschap", "provincie", "waterschap",
    # Polish / Czech / other
    "spolka", "szkola", "szpital", "uniwersytet", "urzad", "miasto", "gmina",
    "powiat", "zaklad", "przedsiebiorstwo", "uslugi", "przemysl", "polskie",
    "polski", "polska", "spolecnost", "nemocnice", "univerzita", "mesto",
    "skola", "urad", "ministerstvo", "sluzby", "vyroba", "obchod",
    # Nordic
    "kommun", "kommune", "sykehus", "sjukhus", "universitet", "skole",
    "skola", "forening", "foreningen", "stiftelse", "stiftelsen", "aktiebolag",
    "aksjeselskap", "bank", "sparebank", "kommunale", "fylke", "region",
    # Japanese (romanised) / Korean / Chinese company words
    "kabushiki", "kaisha", "gakuen", "gakko", "byoin", "shiyakusho",
    "kyoiku", "iinkai", "daigaku", "kogyo", "seisakusho", "shoji", "sangyo",
    "kensetsu", "denki", "seiyaku", "shokai", "kyokai", "shi", "ku", "ken",
    "cho", "machi", "mura", "jusik", "hoesa", "youxian", "gongsi", "jituan",
    # Asia / Africa generic
    "sri", "lanka", "bharat", "hindustan", "maharashtra", "gujarat", "tamil",
    "nadu", "kerala", "karnataka", "punjab", "rajasthan", "bengal", "andhra",
    "telangana", "odisha", "assam", "bihar", "haryana", "jharkhand",
    "chhattisgarh", "uttar", "pradesh", "madhya", "himachal", "goa",
    "sabah", "sarawak", "selangor", "johor", "penang", "jawa", "jakarta",
    "bandung", "surabaya", "tbk", "persero", "perusahaan", "perseroan",
    "terbatas", "koperasi", "yayasan", "sekolah", "rumah", "sakit",
    "pemerintah", "kabupaten", "kota", "dinas", "kementerian", "universitas",
    "berhad", "sendirian", "syarikat", "sekolah", "kebangsaan", "malaysia",
    "kenya", "uganda", "tanzania", "ghana", "zambia", "zimbabwe", "namibia",
    "botswana", "ethiopia", "rwanda", "cape", "natal", "kwazulu", "free",
    "transvaal", "eastern", "western", "northern", "southern",
}

_SOCIAL_RE = re.compile(
    r"(?:linkedin|facebook|twitter|instagram|youtube|tiktok|pinterest|wikipedia|"
    r"indeed|glassdoor|monster|ziprecruiter|careerbuilder|xing|stepstone|naukri|"
    r"wellfound|crunchbase|zoominfo|trustpilot|yelp|yellowpages|dnb\.com|dun\.com|"
    r"bloomberg|reuters|britannica|google|bing|yahoo)\.", re.I)


def all_tokens(name):
    """Every meaningful word of the name, lowercase, legal forms removed."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return [w for w in words if len(w) > 1 and w not in NOISE_TOKENS]


def distinctive_tokens(name):
    """Words of the name that could only reasonably belong to this company."""
    return [w for w in all_tokens(name) if w not in GENERIC_TOKENS and len(w) >= 3]


def _squash(tokens):
    return "".join(tokens)


def slug_matches_company(slug, name, min_len=4):
    """Does an ATS board slug / Workday tenant carry this company's name?

    "Eurofins" never matches "County of Douglas"; "accenture" matches
    "Accenture GmbH"; "ngs" matches "National Geographic Society" only through
    its initials, which is accepted when the slug is exactly the initials of
    the distinctive+generic words (3+ letters).
    """
    slug = re.sub(r"[^a-z0-9]+", "", (slug or "").lower())
    if not slug or len(slug) < 2:
        return False
    distinct = distinctive_tokens(name)
    tokens = all_tokens(name)
    if not tokens:
        return False
    # 1. a distinctive token of useful length is inside the slug
    for token in distinct:
        if len(token) >= min_len and token in slug:
            return True
    # 2. the slug is (a prefix of) the squashed name -- covers "abbott" for
    #    "Abbott Laboratories de Mexico" only via rule 1, but "vfcorp" for
    #    "VF Services" via this rule when the slug repeats two name words
    squashed = _squash(tokens)
    if len(slug) >= 5 and (squashed.startswith(slug) or slug in squashed and
                           sum(1 for t in tokens if t in slug) >= 2):
        return True
    # 3. exact initials of a multi-word name ("ngs" == National Geographic Society)
    if len(tokens) >= 3 and len(slug) >= 3:
        initials = "".join(t[0] for t in tokens)
        if slug == initials:
            return True
    return False


def domain_matches_company(domain, name):
    """Does a registrable domain plausibly belong to this company?

    Requires a distinctive token (>= 4 chars) to be inside the domain label,
    or two or more name words that together explain most of the label.  A
    lone generic word ("state" in state.gov, "national" in
    nationalgeographic.com) is never enough.
    """
    domain = (domain or "").lower().strip()
    if not domain or _SOCIAL_RE.search(domain + "."):
        return False
    label = domain.split(".")[0]
    if label.startswith("www"):
        label = label[3:].lstrip("-.")
    if not label:
        return False
    distinct = distinctive_tokens(name)
    tokens = all_tokens(name)
    for token in distinct:
        # a distinctive word of useful length anywhere in the label
        # ("eurofins" in eurofins-scientific); shorter ones must lead it
        if len(token) >= 5 and token in label:
            return True
        if len(token) >= 4 and label.startswith(token):
            return True
        # short distinctive brand ("abb", "3m", "sap") must BE the label
        if len(token) >= 2 and label == token:
            return True
    present = [t for t in tokens if t in label]
    if len(present) >= 2 and any(t in distinct for t in present):
        coverage = sum(len(t) for t in present) / float(len(label))
        if coverage >= 0.6:
            return True
    if not distinct and len(present) >= 2 and len(present) == len(tokens):
        # every word of a generic-only name is in the label ("cityofboston")
        return True
    # "City of Boston" -> boston.gov: no distinctive token at all, so accept a
    # generic word only when the label IS that word and it is the longest word
    # of the name.
    if not distinct and len(tokens) >= 2:
        longest = max(tokens, key=len)
        if len(longest) >= 5 and label == longest:
            return True
    return False


def page_evidence(html, name, max_len=12000):
    """Fraction of the company's distinctive name words present on a page.

    Only the title, headings, site-name metadata and the first part of the
    visible text are considered.  When the name has no distinctive word at
    all, every word must be present for the page to count as evidence, and
    the score is capped so that it never looks strong.
    """
    if not html:
        return 0.0
    distinct = distinctive_tokens(name)
    tokens = distinct or all_tokens(name)
    if not tokens:
        return 0.0
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    if soup.title:
        parts.append(soup.title.get_text(" ", strip=True))
    for tag in soup.find_all(["h1", "h2"], limit=10):
        parts.append(tag.get_text(" ", strip=True))
    for prop in ("og:site_name", "og:title", "application-name", "description"):
        meta = soup.find("meta", attrs={"property": prop}) or \
            soup.find("meta", attrs={"name": prop})
        if meta and meta.get("content"):
            parts.append(meta["content"])
    body = clean_text(soup.get_text(" ", strip=True), max_len=max_len)
    haystack = re.sub(r"[^a-z0-9]+", " ", (" ".join(parts) + " " + body).lower())
    padded = " " + haystack + " "
    matched = 0
    for token in tokens:
        # whole-word for short tokens, substring for longer ones (plurals,
        # compounds such as "eurofins" in "eurofins-scientific")
        if len(token) <= 4:
            if (" " + token + " ") in padded:
                matched += 1
        elif token in haystack:
            matched += 1
    score = matched / float(len(tokens))
    if not distinct:
        # generic-only name: all words must match, and it is still weak
        return 0.5 if score >= 1.0 else 0.0
    return score


def title_evidence(html, name):
    """Share of ALL name words (generic ones included) in the page's own
    identity: <title>, og:site_name, og:title and the first <h1>.

    A site whose title reads "Technical University of Crete" or "City of
    Boston" identifies itself even though its domain (tuc.gr, boston.gov)
    carries no distinctive word.  Body text is deliberately excluded.
    """
    if not html:
        return 0.0, 0
    tokens = all_tokens(name)
    if not tokens:
        return 0.0, 0
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    if soup.title:
        parts.append(soup.title.get_text(" ", strip=True))
    for prop in ("og:site_name", "og:title", "application-name"):
        meta = soup.find("meta", attrs={"property": prop}) or \
            soup.find("meta", attrs={"name": prop})
        if meta and meta.get("content"):
            parts.append(meta["content"])
    h1 = soup.find("h1")
    if h1:
        parts.append(h1.get_text(" ", strip=True))
    haystack = " " + re.sub(r"[^a-z0-9]+", " ", " ".join(parts).lower()) + " "
    matched = sum(1 for t in tokens if (" " + t + " ") in haystack)
    return matched / float(len(tokens)), len(tokens)


def board_belongs_to_company(ats, captured, board_url, name, page_html=""):
    """Ownership check for an ATS board that was NOT reached from the
    company's own website (web search, name search, guessed link).

    Returns (accepted, reason).
    """
    from .ats import is_company_board_vendor
    slug = captured or ""
    if ats == "workday":
        m = re.match(r"^https?://([a-z0-9\-_]+)\.wd\d+\.myworkday(?:jobs|site)\.com",
                     (board_url or "").lower())
        if m:
            slug = m.group(1)
    if ats and slug and slug_matches_company(slug, name):
        return True, "slug:%s" % slug
    # Shared/multi-tenant vendors (job boards that host many employers) need
    # the page itself to name the company.
    evidence = page_evidence(page_html, name) if page_html else 0.0
    if evidence >= 0.5:
        return True, "page_evidence=%.2f" % evidence
    if ats and not is_company_board_vendor(ats):
        return False, "shared vendor board without company evidence"
    return False, "no ownership evidence (slug=%s evidence=%.2f)" % (slug, evidence)
