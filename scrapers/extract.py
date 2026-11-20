"""Field extraction helpers shared by scrapers and spot-sheet parsers."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

PROCESSES = ["Decaf", "Anaerobic", "Honey", "Natural", "Wet-Hulled", "Washed"]

_PROCESS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Decaf", re.compile(r"\bdecaf|swiss water|mountain water|\bewp\b|ethyl acetate|sugar ?cane process|\bCO2 process", re.I)),
    ("Anaerobic", re.compile(r"anaerobic|carbonic maceration|thermal shock|\binfused\b|co-?ferment", re.I)),
    ("Honey", re.compile(r"\bhoney\b|pulped natural|semi-?dry|miel\b|black honey|red honey|yellow honey", re.I)),
    ("Wet-Hulled", re.compile(r"wet[- ]?hulled|giling basah|semi-?washed", re.I)),
    ("Natural", re.compile(r"\bnatural\b|dry[- ]process|sun[- ]dried|\bunwashed\b|\bdp\b", re.I)),
    ("Washed", re.compile(r"\bwashed\b|wet[- ]process|fully washed|\bfw\b|lavado|\bwp\b", re.I)),
]

VARIETIES = [
    "Pink Bourbon", "Yellow Bourbon", "Red Bourbon", "Bourbon", "Typica", "Caturra", "Catuai", "Catuaí",
    "Gesha", "Geisha", "SL28", "SL-28", "SL34", "SL-34", "Pacamara", "Pacas", "Heirloom", "Landrace",
    "74110", "74112", "74158", "Castillo", "Colombia variety", "Catimor", "Sarchimor", "Sidra", "Maragogype",
    "Mundo Novo", "Batian", "Ruiru 11", "Parainema", "Lempira", "Marsellesa", "Villa Sarchi", "Obata",
    "Arara", "Tabi", "Wush Wush", "Laurina", "Mokka", "Java", "Ihcafe 90", "Anacafe 14", "Centroamericano",
    "Robusta", "Liberica", "Excelsa", "Abyssinia", "Tim Tim", "Ateng", "Sigararutang", "S795", "Kent",
    "Chiroso", "Tekisic", "Costa Rica 95", "Starmaya", "Mibirizi", "Jackson", "Blue Mountain",
]
_VARIETY_PATTERNS = [
    (v, re.compile(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", re.I)) for v in VARIETIES
]
_VARIETY_CANON = {"Geisha": "Gesha", "SL-28": "SL28", "SL-34": "SL34", "Catuaí": "Catuai"}

_SCORE_PATTERNS = [
    re.compile(r"(?:cup(?:ping)?\s*score|sca(?:\s*score)?|score|q\s*score)\s*[:=]?\s*(\d{2}(?:\.\d{1,2})?)", re.I),
    re.compile(r"(\d{2}(?:\.\d{1,2})?)\s*(?:pts|points|\+?\s*sca)\b", re.I),
]


def clean_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def detect_process(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    for name, pat in _PROCESS_PATTERNS:
        if pat.search(blob):
            return name
    return None


def normalize_process(value: str | None) -> str | None:
    """Map a process column value onto the canonical list (keeps unknown values title-cased)."""
    if not value or not str(value).strip():
        return None
    found = detect_process(str(value))
    return found or str(value).strip().title()


def detect_variety(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    found: list[str] = []
    for v, pat in _VARIETY_PATTERNS:
        if pat.search(blob):
            canon = _VARIETY_CANON.get(v, v)
            # Skip generic "Bourbon" when a colored bourbon already matched.
            if canon == "Bourbon" and any(f.endswith("Bourbon") for f in found):
                continue
            if canon not in found:
                found.append(canon)
    return ", ".join(found) if found else None


def detect_score(*texts: str | None) -> float | None:
    blob = " ".join(t for t in texts if t)
    for pat in _SCORE_PATTERNS:
        for m in pat.finditer(blob):
            v = float(m.group(1))
            if 78 <= v <= 96:
                return v
    return None


def parse_score(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(str(value).strip().rstrip("+").split()[0])
    except (ValueError, IndexError):
        return detect_score(str(value))
    return v if 70 <= v <= 100 else None


def parse_int(value) -> int | None:
    if value is None:
        return None
    m = re.search(r"-?\d[\d,]*", str(value))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None
