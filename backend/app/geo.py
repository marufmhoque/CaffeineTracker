"""Country-name resolution for free text (scraper titles, spot sheets, Census country names)."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .db import load_seed

# Extra names, spellings and well-known growing regions -> ISO-3. Region names let us place lots whose
# title only says e.g. "Yirgacheffe Konga" or "Sumatra Gayo".
ALIASES: dict[str, list[str]] = {
    "MEX": ["mexico", "chiapas", "oaxaca", "veracruz", "puebla", "guerrero"],
    "GTM": ["guatemala", "huehuetenango", "antigua guatemala", "atitlan", "acatenango", "fraijanes",
            "coban", "san marcos guatemala"],
    "HND": ["honduras", "marcala", "santa barbara honduras", "copan", "la paz honduras", "ocotepeque",
            "intibuca", "comayagua"],
    "SLV": ["el salvador", "salvador", "santa ana volcano", "apaneca", "chalatenango"],
    "NIC": ["nicaragua", "jinotega", "matagalpa", "nueva segovia", "dipilto"],
    "CRI": ["costa rica", "tarrazu", "tarrazú", "west valley", "central valley costa rica", "naranjo",
            "brunca", "tres rios", "chirripo"],
    "PAN": ["panama", "boquete", "volcan", "volcán", "chiriqui", "chiriquí"],
    "BLZ": ["belize"],
    "BRA": ["brazil", "brasil", "cerrado", "sul de minas", "mogiana", "minas gerais", "bahia",
            "chapada diamantina", "espirito santo", "matas de minas", "mantiqueira", "fazenda"],
    "COL": ["colombia", "huila", "nariño", "narino", "cauca", "tolima", "antioquia", "santander",
            "quindio", "risaralda", "caldas", "sierra nevada"],
    "PER": ["peru", "perú", "cajamarca", "amazonas peru", "san martin peru", "junin", "cusco", "puno",
            "jaen", "chanchamayo"],
    "ECU": ["ecuador", "loja", "pichincha", "zamora", "galapagos", "galápagos"],
    "BOL": ["bolivia", "caranavi", "yungas"],
    "VEN": ["venezuela"],
    "PRY": ["paraguay"],
    "GUY": ["guyana"],
    "SUR": ["suriname"],
    "JAM": ["jamaica", "blue mountain", "jamaican"],
    "DOM": ["dominican republic", "dominican", "barahona", "jarabacoa"],
    "HTI": ["haiti", "haitian"],
    "CUB": ["cuba", "cuban"],
    "PRI": ["puerto rico", "yauco"],
    "TTO": ["trinidad"],
    "ETH": ["ethiopia", "ethiopian", "yirgacheffe", "yirgachefe", "sidama", "sidamo", "guji",
            "harrar", "harar", "limu", "jimma", "djimmah", "kaffa", "bench maji", "gedeb", "kochere",
            "hambela", "uraga", "shakiso", "nensebo", "bensa", "west arsi", "arsi"],
    "KEN": ["kenya", "kenyan", "nyeri", "kirinyaga", "kiambu", "murang'a", "muranga", "embu",
            "meru kenya"],
    "UGA": ["uganda", "ugandan", "bugisu", "mount elgon", "mt. elgon", "rwenzori"],
    "TZA": ["tanzania", "kilimanjaro", "mbeya", "arusha", "mbinga", "ruvuma"],
    "RWA": ["rwanda", "rwandan", "nyamasheke", "rusizi", "huye", "gakenke"],
    "BDI": ["burundi", "kayanza", "ngozi", "muramvya"],
    "COD": ["democratic republic of the congo", "dr congo", "drc", "d.r. congo", "congo kinshasa",
            "kivu", "south kivu", "north kivu", "congo"],
    "MWI": ["malawi"],
    "ZMB": ["zambia"],
    "ZWE": ["zimbabwe", "chipinge"],
    "MDG": ["madagascar"],
    "AGO": ["angola"],
    "MOZ": ["mozambique"],
    "CMR": ["cameroon"],
    "CAF": ["central african republic"],
    "COG": ["republic of the congo", "congo brazzaville", "congo-brazzaville"],
    "GAB": ["gabon"],
    "GNQ": ["equatorial guinea"],
    "CIV": ["cote d'ivoire", "côte d'ivoire", "cote divoire", "ivory coast"],
    "GHA": ["ghana"],
    "GIN": ["guinea"],
    "LBR": ["liberia"],
    "SLE": ["sierra leone"],
    "NGA": ["nigeria"],
    "TGO": ["togo"],
    "BEN": ["benin"],
    "CPV": ["cabo verde", "cape verde", "fogo"],
    "STP": ["sao tome", "são tomé", "sao tome and principe"],
    "VNM": ["vietnam", "viet nam", "dalat", "da lat", "lam dong", "dak lak"],
    "IDN": ["indonesia", "sumatra", "sumatran", "java", "sulawesi", "toraja", "bali", "flores",
            "aceh", "gayo", "mandheling", "lintong", "kerinci", "papua indonesia"],
    "IND": ["india", "indian", "monsooned malabar", "malabar", "chikmagalur", "karnataka", "coorg",
            "araku"],
    "LAO": ["laos", "lao pdr", "bolaven"],
    "THA": ["thailand", "chiang mai", "chiang rai", "doi chaang"],
    "MMR": ["myanmar", "burma", "shan state"],
    "PHL": ["philippines", "mindanao", "benguet"],
    "CHN": ["china", "yunnan", "pu'er", "puer", "baoshan"],
    "TWN": ["taiwan", "alishan"],
    "NPL": ["nepal"],
    "LKA": ["sri lanka"],
    "TLS": ["timor-leste", "timor leste", "east timor", "timor", "ermera", "letefoho"],
    "PNG": ["papua new guinea", "png", "eastern highlands", "western highlands", "simbu"],
    "YEM": ["yemen", "yemeni", "haraz", "mocha yemen", "sana'a", "bani matar", "haymah"],
    "SAU": ["saudi arabia", "jazan", "khawlani"],
    "MYS": ["malaysia"],
    "KHM": ["cambodia", "mondulkiri"],
    "AUS": ["australia"],
    "FJI": ["fiji"],
    "VUT": ["vanuatu", "tanna"],
    "WSM": ["samoa"],
}

# Short/ambiguous aliases that should only match a whole dedicated country field, not prose.
_FIELD_ONLY = {"png", "drc", "java", "congo", "guinea", "timor", "salvador", "dominican", "fogo"}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = t.replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[int, str, re.Pattern[str], str]]:
    names: dict[str, set[str]] = {}
    for c in load_seed():
        names.setdefault(c["iso_code"], set()).add(_norm(c["name"]))
        # "China (Yunnan)" -> also "china"
        base = re.sub(r"\s*\(.*?\)", "", c["name"])
        names[c["iso_code"]].add(_norm(base))
    for iso, aliases in ALIASES.items():
        for a in aliases:
            names.setdefault(iso, set()).add(_norm(a))
    pats = []
    for iso, ns in names.items():
        for n in ns:
            pats.append((len(n), n, re.compile(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])"), iso))
    # Longest first: "papua new guinea" before "guinea", "democratic republic of the congo" before "congo".
    pats.sort(key=lambda p: -p[0])
    return pats


def resolve_country(text: str | None, *, field: bool = False) -> str | None:
    """Return the ISO-3 code of the coffee origin named in `text`, or None.

    `field=True` means the text is a dedicated country/origin column, so short or ambiguous aliases
    (e.g. 'PNG', 'DRC', 'Congo') are allowed.
    """
    if not text:
        return None
    t = _norm(str(text))
    if field and len(t) == 3 and t.upper() in {c["iso_code"] for c in load_seed()}:
        return t.upper()
    for _, name, pat, iso in _patterns():
        if not field and name in _FIELD_ONLY:
            continue
        if pat.search(t):
            return iso
    if not field:
        # Second pass allowing ambiguous aliases, but only as standalone words.
        for _, name, pat, iso in _patterns():
            if name in _FIELD_ONLY and pat.search(t):
                return iso
    return None


# US Census country names that don't match our seed names directly.
CENSUS_NAME_OVERRIDES = {
    "BURMA": "MMR",
    "CONGO, DEMOCRATIC REPUBLIC OF THE (CONGO KINSHASA)": "COD",
    "CONGO, REPUBLIC OF THE (CONGO BRAZZAVILLE)": "COG",
    "COTE D'IVOIRE": "CIV",
    "TIMOR-LESTE": "TLS",
    "TAIWAN": "TWN",
    "CABO VERDE": "CPV",
    "SAO TOME AND PRINCIPE": "STP",
}


def resolve_census_country(name: str) -> str | None:
    up = (name or "").strip().upper()
    if up in CENSUS_NAME_OVERRIDES:
        return CENSUS_NAME_OVERRIDES[up]
    return resolve_country(name, field=True)
