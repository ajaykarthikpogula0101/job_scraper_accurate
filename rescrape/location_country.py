"""Map a free-text job location ("Lancaster, PA, United States",
"München, Bayern, DE", "Bengaluru, India", "Remote - UK") to a country name
in the naming used by the company input file (USA, United Kingdom, ...).

Returns "" when nothing in the string is recognisable; the caller then falls
back to the company's own country and records that it did so.
"""
import re

# canonical names follow the input file's conventions
_COUNTRIES = {
    "usa": "USA", "united states": "USA", "united states of america": "USA",
    "u.s.": "USA", "u.s.a.": "USA", "us": "USA", "america": "USA", "estados unidos": "USA",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom",
    "great britain": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom", "northern ireland": "United Kingdom", "gb": "United Kingdom",
    "canada": "Canada", "ca": "Canada",
    "germany": "Germany", "deutschland": "Germany", "de": "Germany",
    "france": "France", "fr": "France",
    "india": "India", "in": "India", "bharat": "India",
    "brazil": "Brazil", "brasil": "Brazil", "br": "Brazil",
    "australia": "Australia", "au": "Australia",
    "japan": "Japan", "jp": "Japan", "日本": "Japan",
    "china": "China", "cn": "China", "people's republic of china": "China", "prc": "China", "中国": "China",
    "hong kong": "Hong Kong", "hk": "Hong Kong", "hong kong sar": "Hong Kong",
    "taiwan": "Taiwan", "tw": "Taiwan",
    "singapore": "Singapore", "sg": "Singapore",
    "malaysia": "Malaysia", "my": "Malaysia",
    "indonesia": "Indonesia", "id": "Indonesia",
    "thailand": "Thailand", "th": "Thailand",
    "vietnam": "Vietnam", "viet nam": "Vietnam", "vn": "Vietnam",
    "philippines": "Philippines", "ph": "Philippines",
    "south korea": "Korea", "korea": "Korea", "republic of korea": "Korea", "korea, republic of": "Korea", "kr": "Korea", "대한민국": "Korea",
    "mexico": "Mexico", "méxico": "Mexico", "mx": "Mexico",
    "argentina": "Argentina", "ar": "Argentina",
    "chile": "Chile", "cl": "Chile",
    "colombia": "Colombia", "co": "Colombia",
    "peru": "Peru", "perú": "Peru", "pe": "Peru",
    "italy": "Italy", "italia": "Italy", "it": "Italy",
    "spain": "Spain", "españa": "Spain", "espana": "Spain", "es": "Spain",
    "portugal": "Portugal", "pt": "Portugal",
    "netherlands": "Netherlands", "the netherlands": "Netherlands", "nederland": "Netherlands", "holland": "Netherlands", "nl": "Netherlands",
    "belgium": "Belgium", "belgië": "Belgium", "belgique": "Belgium", "be": "Belgium",
    "switzerland": "Switzerland", "schweiz": "Switzerland", "suisse": "Switzerland", "svizzera": "Switzerland", "ch": "Switzerland",
    "austria": "Austria", "österreich": "Austria", "at": "Austria",
    "sweden": "Sweden", "sverige": "Sweden", "se": "Sweden",
    "norway": "Norway", "norge": "Norway", "no": "Norway",
    "denmark": "Denmark", "danmark": "Denmark", "dk": "Denmark",
    "finland": "Finland", "suomi": "Finland", "fi": "Finland",
    "ireland": "Ireland", "ie": "Ireland", "republic of ireland": "Ireland",
    "poland": "Poland", "polska": "Poland", "pl": "Poland",
    "czech republic": "Czech Republic", "czechia": "Czech Republic", "cz": "Czech Republic",
    "slovakia": "Slovakia", "sk": "Slovakia", "slovak republic": "Slovakia",
    "hungary": "Hungary", "hu": "Hungary", "magyarország": "Hungary",
    "romania": "Romania", "ro": "Romania", "românia": "Romania",
    "bulgaria": "Bulgaria", "bg": "Bulgaria",
    "greece": "Greece", "gr": "Greece", "ελλάδα": "Greece",
    "turkey": "Turkey", "türkiye": "Turkey", "turkiye": "Turkey", "tr": "Turkey",
    "russia": "Russia", "russian federation": "Russia", "ru": "Russia",
    "ukraine": "Ukraine", "ua": "Ukraine",
    "israel": "Israel", "il": "Israel",
    "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates", "ae": "United Arab Emirates",
    "saudi arabia": "Saudi Arabia", "ksa": "Saudi Arabia", "sa": "Saudi Arabia", "kingdom of saudi arabia": "Saudi Arabia",
    "qatar": "Qatar", "qa": "Qatar", "kuwait": "Kuwait", "kw": "Kuwait", "bahrain": "Bahrain", "bh": "Bahrain", "oman": "Oman", "om": "Oman",
    "egypt": "Egypt", "eg": "Egypt", "morocco": "Morocco", "ma": "Morocco", "tunisia": "Tunisia", "algeria": "Algeria",
    "south africa": "South Africa", "za": "South Africa", "rsa": "South Africa",
    "nigeria": "Nigeria", "ng": "Nigeria", "kenya": "Kenya", "ke": "Kenya", "ghana": "Ghana", "gh": "Ghana",
    "ethiopia": "Ethiopia", "tanzania": "Tanzania", "uganda": "Uganda", "ug": "Uganda", "zambia": "Zambia", "zimbabwe": "Zimbabwe",
    "namibia": "Namibia", "botswana": "Botswana", "mozambique": "Mozambique", "angola": "Angola", "rwanda": "Rwanda", "senegal": "Senegal",
    "ivory coast": "Ivory Coast", "côte d'ivoire": "Ivory Coast", "cote d'ivoire": "Ivory Coast", "cameroon": "Cameroon",
    "new zealand": "New Zealand", "nz": "New Zealand",
    "pakistan": "Pakistan", "pk": "Pakistan", "bangladesh": "Bangladesh", "bd": "Bangladesh", "sri lanka": "Sri Lanka", "lk": "Sri Lanka",
    "nepal": "Nepal", "np": "Nepal",
    "bosnia and herzegovina": "Bosnia and Herzegovina", "bosnia": "Bosnia and Herzegovina", "ba": "Bosnia and Herzegovina", "bosna i hercegovina": "Bosnia and Herzegovina",
    "serbia": "Serbia", "rs": "Serbia", "croatia": "Croatia", "hr": "Croatia", "hrvatska": "Croatia", "slovenia": "Slovenia", "si": "Slovenia",
    "montenegro": "Montenegro", "north macedonia": "North Macedonia", "macedonia": "North Macedonia", "albania": "Albania", "kosovo": "Kosovo",
    "lithuania": "Lithuania", "lt": "Lithuania", "latvia": "Latvia", "lv": "Latvia", "estonia": "Estonia", "ee": "Estonia",
    "luxembourg": "Luxembourg", "lu": "Luxembourg", "iceland": "Iceland", "is": "Iceland", "malta": "Malta", "mt": "Malta", "cyprus": "Cyprus", "cy": "Cyprus",
    "belarus": "Belarus", "moldova": "Moldova", "georgia (country)": "Georgia", "armenia": "Armenia", "azerbaijan": "Azerbaijan",
    "kazakhstan": "Kazakhstan", "kz": "Kazakhstan", "uzbekistan": "Uzbekistan", "kyrgyzstan": "Kyrgyzstan", "mongolia": "Mongolia",
    "iran": "Iran", "iraq": "Iraq", "jordan": "Jordan", "jo": "Jordan", "lebanon": "Lebanon", "lb": "Lebanon",
    "myanmar": "Myanmar", "cambodia": "Cambodia", "laos": "Laos", "brunei": "Brunei", "macau": "Macau", "macao": "Macau",
    "puerto rico": "Puerto Rico", "pr": "Puerto Rico", "dominican republic": "Dominican Republic", "jamaica": "Jamaica",
    "costa rica": "Costa Rica", "cr": "Costa Rica", "panama": "Panama", "pa (panama)": "Panama", "guatemala": "Guatemala", "honduras": "Honduras",
    "el salvador": "El Salvador", "nicaragua": "Nicaragua", "cuba": "Cuba", "trinidad and tobago": "Trinidad and Tobago", "bahamas": "Bahamas",
    "bermuda": "Bermuda", "cayman islands": "Cayman Islands", "barbados": "Barbados",
    "uruguay": "Uruguay", "uy": "Uruguay", "paraguay": "Paraguay", "bolivia": "Bolivia", "ecuador": "Ecuador", "ec": "Ecuador", "venezuela": "Venezuela",
    "guyana": "Guyana", "suriname": "Suriname",
    "fiji": "Fiji", "papua new guinea": "Papua New Guinea", "mauritius": "Mauritius", "madagascar": "Madagascar", "seychelles": "Seychelles",
    "liechtenstein": "Liechtenstein", "monaco": "Monaco", "andorra": "Andorra", "san marino": "San Marino", "gibraltar": "Gibraltar",
    "isle of man": "Isle of Man", "jersey (channel islands)": "Jersey", "guernsey": "Guernsey", "greenland": "Greenland", "faroe islands": "Faroe Islands",
    "afghanistan": "Afghanistan", "maldives": "Maldives", "bhutan": "Bhutan", "timor-leste": "Timor-Leste",
}

_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "washington dc": "DC", "washington, d.c.": "DC",
}
_US_ABBR = set(_US_STATES.values())
_CA_PROVINCES = {"ontario", "quebec", "québec", "british columbia", "alberta", "manitoba", "saskatchewan",
                 "nova scotia", "new brunswick", "newfoundland and labrador", "newfoundland", "prince edward island",
                 "yukon", "nunavut", "northwest territories"}
_CA_ABBR = {"on", "qc", "bc", "ab", "mb", "sk", "ns", "nb", "nl", "pe", "yt", "nu", "nt"}
_AU_STATES = {"new south wales", "victoria", "queensland", "western australia", "south australia", "tasmania",
              "australian capital territory", "northern territory", "nsw", "vic", "qld", "wa (australia)", "tas", "act", "nt (australia)"}
_IN_STATES = {"maharashtra", "karnataka", "tamil nadu", "telangana", "andhra pradesh", "kerala", "gujarat", "rajasthan",
              "west bengal", "uttar pradesh", "madhya pradesh", "haryana", "punjab", "delhi", "new delhi", "bihar", "odisha",
              "assam", "jharkhand", "chhattisgarh", "goa", "himachal pradesh", "uttarakhand", "chandigarh", "ncr"}
_DE_STATES = {"bayern", "bavaria", "baden-württemberg", "baden-wurttemberg", "nordrhein-westfalen", "north rhine-westphalia",
              "hessen", "hesse", "niedersachsen", "lower saxony", "sachsen", "saxony", "rheinland-pfalz", "rhineland-palatinate",
              "berlin", "hamburg", "bremen", "brandenburg", "thüringen", "thuringia", "sachsen-anhalt", "saxony-anhalt",
              "schleswig-holstein", "mecklenburg-vorpommern", "saarland"}
_BR_STATES = {"são paulo", "sao paulo", "rio de janeiro", "minas gerais", "paraná", "parana", "rio grande do sul",
              "santa catarina", "bahia", "pernambuco", "ceará", "ceara", "goiás", "goias", "distrito federal",
              "espírito santo", "espirito santo", "amazonas", "pará", "para (brazil)", "mato grosso", "mato grosso do sul",
              "sp", "rj", "mg", "pr", "rs", "sc", "ba", "pe", "ce", "go", "df", "es"}
_UK_REGIONS = {"greater london", "west midlands", "greater manchester", "west yorkshire", "south yorkshire",
               "merseyside", "tyne and wear", "hampshire", "surrey", "kent", "essex", "hertfordshire", "berkshire",
               "oxfordshire", "cambridgeshire", "lancashire", "cheshire", "devon", "somerset", "bristol", "leeds",
               "manchester", "birmingham", "glasgow", "edinburgh", "cardiff", "belfast", "liverpool", "sheffield",
               "newcastle upon tyne", "nottingham", "leicester", "coventry", "reading", "milton keynes", "cambridge",
               "oxford", "london"}

_CITIES = {
    # USA
    "new york city": "USA", "nyc": "USA", "los angeles": "USA", "chicago": "USA", "houston": "USA", "phoenix": "USA",
    "philadelphia": "USA", "san antonio": "USA", "san diego": "USA", "dallas": "USA", "san jose": "USA", "austin": "USA",
    "jacksonville": "USA", "fort worth": "USA", "columbus": "USA", "charlotte": "USA", "san francisco": "USA",
    "indianapolis": "USA", "seattle": "USA", "denver": "USA", "boston": "USA", "nashville": "USA", "detroit": "USA",
    "portland": "USA", "las vegas": "USA", "memphis": "USA", "louisville": "USA", "baltimore": "USA", "milwaukee": "USA",
    "albuquerque": "USA", "tucson": "USA", "fresno": "USA", "sacramento": "USA", "kansas city": "USA", "atlanta": "USA",
    "miami": "USA", "raleigh": "USA", "omaha": "USA", "minneapolis": "USA", "tampa": "USA", "orlando": "USA",
    "pittsburgh": "USA", "cincinnati": "USA", "st. louis": "USA", "saint louis": "USA", "cleveland": "USA",
    "salt lake city": "USA", "richmond": "USA", "hartford": "USA", "boise": "USA", "anchorage": "USA", "honolulu": "USA",
    "remote - us": "USA", "remote us": "USA", "us remote": "USA", "remote, us": "USA", "united states - remote": "USA",
    # Canada
    "toronto": "Canada", "montreal": "Canada", "montréal": "Canada", "vancouver": "Canada", "calgary": "Canada",
    "edmonton": "Canada", "ottawa": "Canada", "winnipeg": "Canada", "mississauga": "Canada", "halifax": "Canada",
    # UK
    "london": "United Kingdom",
    # Europe
    "paris": "France", "lyon": "France", "marseille": "France", "toulouse": "France", "nantes": "France", "lille": "France",
    "berlin": "Germany", "munich": "Germany", "münchen": "Germany", "muenchen": "Germany", "hamburg": "Germany",
    "frankfurt": "Germany", "frankfurt am main": "Germany", "cologne": "Germany", "köln": "Germany", "stuttgart": "Germany",
    "düsseldorf": "Germany", "duesseldorf": "Germany", "dusseldorf": "Germany", "leipzig": "Germany", "nuremberg": "Germany",
    "nürnberg": "Germany", "hannover": "Germany", "hanover": "Germany", "dresden": "Germany", "bremen": "Germany", "essen": "Germany",
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain", "sevilla": "Spain", "seville": "Spain", "bilbao": "Spain",
    "rome": "Italy", "roma": "Italy", "milan": "Italy", "milano": "Italy", "turin": "Italy", "torino": "Italy", "naples": "Italy",
    "bologna": "Italy", "florence": "Italy", "firenze": "Italy",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands", "the hague": "Netherlands", "den haag": "Netherlands",
    "utrecht": "Netherlands", "eindhoven": "Netherlands",
    "brussels": "Belgium", "bruxelles": "Belgium", "brussel": "Belgium", "antwerp": "Belgium", "antwerpen": "Belgium", "ghent": "Belgium",
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland", "genève": "Switzerland", "basel": "Switzerland",
    "bern": "Switzerland", "lausanne": "Switzerland",
    "vienna": "Austria", "wien": "Austria", "graz": "Austria", "linz": "Austria", "salzburg": "Austria",
    "stockholm": "Sweden", "gothenburg": "Sweden", "göteborg": "Sweden", "malmö": "Sweden", "malmo": "Sweden",
    "oslo": "Norway", "bergen": "Norway", "trondheim": "Norway", "stavanger": "Norway",
    "copenhagen": "Denmark", "københavn": "Denmark", "aarhus": "Denmark", "odense": "Denmark",
    "helsinki": "Finland", "espoo": "Finland", "tampere": "Finland",
    "dublin": "Ireland", "cork": "Ireland", "galway": "Ireland", "limerick": "Ireland",
    "lisbon": "Portugal", "lisboa": "Portugal", "porto": "Portugal",
    "warsaw": "Poland", "warszawa": "Poland", "krakow": "Poland", "kraków": "Poland", "wroclaw": "Poland", "wrocław": "Poland",
    "poznan": "Poland", "poznań": "Poland", "gdansk": "Poland", "gdańsk": "Poland", "lodz": "Poland", "łódź": "Poland", "katowice": "Poland",
    "prague": "Czech Republic", "praha": "Czech Republic", "brno": "Czech Republic", "ostrava": "Czech Republic",
    "bratislava": "Slovakia", "košice": "Slovakia", "kosice": "Slovakia",
    "budapest": "Hungary", "debrecen": "Hungary", "bucharest": "Romania", "bucurești": "Romania", "cluj-napoca": "Romania", "cluj": "Romania",
    "timisoara": "Romania", "iasi": "Romania", "sofia": "Bulgaria", "athens": "Greece", "thessaloniki": "Greece",
    "istanbul": "Turkey", "ankara": "Turkey", "izmir": "Turkey", "moscow": "Russia", "saint petersburg": "Russia", "st. petersburg": "Russia",
    "kyiv": "Ukraine", "kiev": "Ukraine", "lviv": "Ukraine", "kharkiv": "Ukraine", "belgrade": "Serbia", "zagreb": "Croatia",
    "ljubljana": "Slovenia", "sarajevo": "Bosnia and Herzegovina", "vilnius": "Lithuania", "riga": "Latvia", "tallinn": "Estonia",
    "luxembourg city": "Luxembourg", "reykjavik": "Iceland", "valletta": "Malta", "nicosia": "Cyprus", "limassol": "Cyprus",
    # Middle East / Africa
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates", "sharjah": "United Arab Emirates",
    "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia", "dammam": "Saudi Arabia", "al khobar": "Saudi Arabia",
    "doha": "Qatar", "kuwait city": "Kuwait", "manama": "Bahrain", "muscat": "Oman", "tel aviv": "Israel", "jerusalem": "Israel",
    "haifa": "Israel", "amman": "Jordan", "beirut": "Lebanon", "cairo": "Egypt", "casablanca": "Morocco", "tunis": "Tunisia",
    "johannesburg": "South Africa", "cape town": "South Africa", "durban": "South Africa", "pretoria": "South Africa",
    "sandton": "South Africa", "centurion": "South Africa", "lagos": "Nigeria", "abuja": "Nigeria", "nairobi": "Kenya",
    "accra": "Ghana", "addis ababa": "Ethiopia", "dar es salaam": "Tanzania", "kampala": "Uganda", "lusaka": "Zambia",
    "harare": "Zimbabwe", "windhoek": "Namibia", "gaborone": "Botswana", "maputo": "Mozambique", "luanda": "Angola",
    "kigali": "Rwanda", "dakar": "Senegal", "abidjan": "Ivory Coast", "douala": "Cameroon",
    # Asia
    "mumbai": "India", "bombay": "India", "delhi": "India", "new delhi": "India", "bangalore": "India", "bengaluru": "India",
    "hyderabad": "India", "chennai": "India", "kolkata": "India", "pune": "India", "gurgaon": "India", "gurugram": "India",
    "noida": "India", "ahmedabad": "India", "jaipur": "India", "kochi": "India", "cochin": "India", "chandigarh": "India",
    "coimbatore": "India", "indore": "India", "lucknow": "India", "nagpur": "India", "thiruvananthapuram": "India", "trivandrum": "India",
    "vadodara": "India", "surat": "India", "bhubaneswar": "India", "visakhapatnam": "India", "mysore": "India", "mysuru": "India",
    "tokyo": "Japan", "osaka": "Japan", "nagoya": "Japan", "yokohama": "Japan", "kyoto": "Japan", "fukuoka": "Japan", "sapporo": "Japan", "kobe": "Japan",
    "東京": "Japan", "大阪": "Japan",
    "beijing": "China", "shanghai": "China", "shenzhen": "China", "guangzhou": "China", "chengdu": "China", "hangzhou": "China",
    "nanjing": "China", "wuhan": "China", "tianjin": "China", "suzhou": "China", "xi'an": "China", "xian": "China", "chongqing": "China",
    "上海": "China", "北京": "China", "taipei": "Taiwan", "hsinchu": "Taiwan", "kaohsiung": "Taiwan", "taichung": "Taiwan",
    "seoul": "Korea", "busan": "Korea", "incheon": "Korea", "seongnam": "Korea", "pangyo": "Korea", "서울": "Korea",
    "bangkok": "Thailand", "chiang mai": "Thailand", "kuala lumpur": "Malaysia", "petaling jaya": "Malaysia", "penang": "Malaysia",
    "johor bahru": "Malaysia", "cyberjaya": "Malaysia", "jakarta": "Indonesia", "surabaya": "Indonesia", "bandung": "Indonesia",
    "manila": "Philippines", "makati": "Philippines", "taguig": "Philippines", "cebu": "Philippines", "quezon city": "Philippines",
    "bonifacio global city": "Philippines", "pasig": "Philippines", "ho chi minh": "Vietnam", "ho chi minh city": "Vietnam",
    "hanoi": "Vietnam", "da nang": "Vietnam", "karachi": "Pakistan", "lahore": "Pakistan", "islamabad": "Pakistan",
    "dhaka": "Bangladesh", "colombo": "Sri Lanka", "kathmandu": "Nepal", "phnom penh": "Cambodia", "yangon": "Myanmar",
    "almaty": "Kazakhstan", "astana": "Kazakhstan", "tashkent": "Uzbekistan", "ulaanbaatar": "Mongolia",
    # Oceania
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia", "perth": "Australia", "adelaide": "Australia",
    "canberra": "Australia", "gold coast": "Australia", "hobart": "Australia", "darwin": "Australia", "newcastle (au)": "Australia",
    "auckland": "New Zealand", "wellington": "New Zealand", "christchurch": "New Zealand", "hamilton (nz)": "New Zealand",
    # Latin America
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil", "belo horizonte": "Brazil", "brasília": "Brazil",
    "brasilia": "Brazil", "curitiba": "Brazil", "porto alegre": "Brazil", "campinas": "Brazil", "salvador": "Brazil", "recife": "Brazil",
    "fortaleza": "Brazil", "manaus": "Brazil", "goiânia": "Brazil", "goiania": "Brazil", "florianópolis": "Brazil", "florianopolis": "Brazil",
    "joinville": "Brazil", "barueri": "Brazil", "osasco": "Brazil", "sorocaba": "Brazil", "ribeirão preto": "Brazil",
    "mexico city": "Mexico", "ciudad de méxico": "Mexico", "ciudad de mexico": "Mexico", "cdmx": "Mexico", "guadalajara": "Mexico",
    "monterrey": "Mexico", "querétaro": "Mexico", "queretaro": "Mexico", "tijuana": "Mexico", "puebla": "Mexico",
    "buenos aires": "Argentina", "córdoba": "Argentina", "cordoba": "Argentina", "rosario": "Argentina",
    "santiago": "Chile", "bogotá": "Colombia", "bogota": "Colombia", "medellín": "Colombia", "medellin": "Colombia",
    "cali": "Colombia", "lima": "Peru", "quito": "Ecuador", "guayaquil": "Ecuador", "montevideo": "Uruguay", "asunción": "Paraguay",
    "la paz": "Bolivia", "caracas": "Venezuela", "san josé": "Costa Rica", "san jose (cr)": "Costa Rica", "panama city": "Panama",
    "guatemala city": "Guatemala", "san juan": "Puerto Rico", "santo domingo": "Dominican Republic", "kingston": "Jamaica",
    "havana": "Cuba", "hamilton (bm)": "Bermuda", "george town": "Cayman Islands",
}

_SPLIT_RE = re.compile(r"\s*(?:,|;|/|\||–|—|\s-\s|\(|\)|\n)\s*")
_REMOTE_RE = re.compile(r"\b(remote|hybrid|virtual|work from home|wfh|home[- ]based|telecommute|anywhere)\b", re.I)


def _norm(part):
    return re.sub(r"\s+", " ", (part or "").strip().lower().strip(".")).strip()


def location_country(location):
    """Country name for a job-location string, or "" if unrecognisable."""
    text = (location or "").strip()
    if not text:
        return ""
    low = text.lower()
    # whole-string aliases first ("Remote - USA", "United States of America")
    whole = _norm(_REMOTE_RE.sub(" ", low))
    whole = re.sub(r"\s+", " ", whole).strip(" -,")
    if whole in _COUNTRIES:
        return _COUNTRIES[whole]
    parts = [_norm(p) for p in _SPLIT_RE.split(text) if _norm(p)]
    # also split on spaces for tokens such as "Lancaster PA US"
    tail_tokens = []
    for p in parts:
        tail_tokens.extend(p.split(" "))
    # 1. explicit country anywhere, last part first
    for p in reversed(parts):
        p2 = _REMOTE_RE.sub("", p).strip(" -,")
        if p2 in _COUNTRIES and not (len(p2) == 2 and p2 in ("in", "co", "de", "ca", "pa", "id", "no", "is", "it", "at", "be", "se", "sa", "ma", "my", "my")):
            return _COUNTRIES[p2]
        if re.match(r"^(?:united states|usa|u\.s\.)\b", p2):
            return "USA"
    # 2. two-letter ISO codes only as a trailing token (ambiguous otherwise)
    if parts:
        last = _REMOTE_RE.sub("", parts[-1]).strip(" -,")
        if len(last) == 2 and last in _COUNTRIES and last not in ("in", "co", "de", "ca", "pa", "id", "no", "is", "it", "at", "be", "se", "sa", "ma", "my"):
            return _COUNTRIES[last]
        if last == "de" and len(parts) >= 2 and (parts[-2] in _DE_STATES or parts[-2] in _CITIES and _CITIES[parts[-2]] == "Germany"):
            return "Germany"
        if last == "in" and len(parts) >= 2 and (parts[-2] in _IN_STATES or _CITIES.get(parts[-2]) == "India"):
            return "India"
        if last == "it" and len(parts) >= 2 and _CITIES.get(parts[-2]) == "Italy":
            return "Italy"
        if last == "ca" and len(parts) >= 2 and (parts[-2] in _CA_PROVINCES or _CITIES.get(parts[-2]) == "Canada"):
            return "Canada"
        if last == "ca":
            return "USA"  # "Los Angeles, CA"
        if last == "be" and len(parts) >= 2 and _CITIES.get(parts[-2]) == "Belgium":
            return "Belgium"
        if last == "at" and len(parts) >= 2 and _CITIES.get(parts[-2]) == "Austria":
            return "Austria"
        if last == "se" and len(parts) >= 2 and _CITIES.get(parts[-2]) == "Sweden":
            return "Sweden"
        if last == "no" and len(parts) >= 2 and _CITIES.get(parts[-2]) == "Norway":
            return "Norway"
    # 3. states / provinces / regions
    for p in reversed(parts):
        if p in _US_STATES or p.upper() in _US_ABBR and len(p) == 2:
            return "USA"
        if p in _CA_PROVINCES or (len(p) == 2 and p in _CA_ABBR and p not in ("on", "nl", "pe", "ab", "sk", "mb", "ns", "nb")):
            return "Canada"
        if p in _AU_STATES:
            return "Australia"
        if p in _IN_STATES:
            return "India"
        if p in _DE_STATES:
            return "Germany"
        if p in _BR_STATES and len(p) > 2:
            return "Brazil"
        if p in _UK_REGIONS:
            return "United Kingdom"
    # 4. cities
    for p in reversed(parts):
        if p in _CITIES:
            return _CITIES[p]
    # 5. Canadian two-letter provinces that clash with other codes, only after a known city
    for p in reversed(parts):
        if len(p) == 2 and p in _CA_ABBR:
            return "Canada"
        if p in _BR_STATES:
            return "Brazil"
    # 6. a country name embedded in a longer phrase ("Greater London Area, United Kingdom Remote")
    for key in sorted(_COUNTRIES, key=len, reverse=True):
        if len(key) >= 5 and re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", low):
            return _COUNTRIES[key]
    # 7. last resort: split on hyphens / spaces too ("CIO KPop-Dallas (US152527)",
    #    "Remote-Germany") and look for a state, city or US-style "City, ST" code
    loose = [t for t in re.split(r"[\s\-_/]+", re.sub(r"[(),;|]", " ", low)) if t]
    if loose and len(loose[-1]) == 2 and loose[-1].upper() in _US_ABBR and len(loose) >= 2:
        return "USA"   # "Minneapolis MN", "Plano TX"
    for t in reversed(loose):
        if t in _US_STATES or t in _CITIES and len(t) >= 5:
            return "USA" if t in _US_STATES else _CITIES[t]
        if t in _DE_STATES or t in _IN_STATES or t in _CA_PROVINCES or t in _AU_STATES:
            return ("Germany" if t in _DE_STATES else "India" if t in _IN_STATES
                    else "Canada" if t in _CA_PROVINCES else "Australia")
    m = re.search(r"\b([a-z]{2})\d{5,}\b", low)   # Workday location codes such as US152527
    if m and m.group(1) in ("us",):
        return "USA"
    return ""


if __name__ == "__main__":
    for s in ["Lancaster, PA, United States", "Ho Chi Minh, , Vietnam", "Bengaluru, India", "Remote - USA", "London, GB",
              "München, Bayern, DE", "Multiple Locations", "Sao Paulo, SP", "Toronto, ON, Canada", "Mississauga, ON",
              "Sydney, NSW", "Remote", "Greater London Area, United Kingdom", "Dublin, Ireland", "Singapore",
              "Warszawa, Poland", "Los Angeles, CA", "Bratislava", "Tokyo, Japan", "US - Remote", "Hyderabad, IN",
              "Chicago, IL, US", "Paris, France", "Frankfurt am Main", "Milan, Italy", "Mexico City, Mexico",
              "Seoul, Korea, Republic of", "Bosnia and Herzegovina - Sarajevo", "Zürich", "Melbourne, Victoria, Australia"]:
        print("%-45s -> %s" % (s, location_country(s)))
