"""Coffee species tagging (Arabica / Robusta / Liberica) for offers, countries and customs rows."""

from __future__ import annotations

import re

ARABICA = "Arabica"
ROBUSTA = "Robusta"
LIBERICA = "Liberica"
MIXED = "Mixed"
SPECIES = (ARABICA, ROBUSTA)

_ROBUSTA = re.compile(r"\b(robustas?|canephora|conilon|kouillou)\b", re.I)
_LIBERICA = re.compile(r"\b(liberica|excelsa|barako)\b", re.I)
_ARABICA = re.compile(
    r"\b(arabica|geisha|gesha|bourbon|typica|caturra|catuai|catuaí|pacamara|sl[- ]?28|sl[- ]?34|"
    r"heirloom|74110|74112|castillo|colombia variety|pacas|maragogipe|mundo novo|java|ruiru|batian|"
    r"parainema|marsellesa|obata|tabi|sidra|wush wush|villa sarchi|sarchimor|catimor|lempira|ihcafe)\b",
    re.I,
)


def country_species(species_mix: str | None) -> str | None:
    """Single species for a country whose seed mix names only one; None when mixed/unknown."""
    if not species_mix:
        return None
    parts = [p.strip() for p in species_mix.split("/") if p.strip()]
    return parts[0] if len(parts) == 1 else None


def dominant_species(species_mix: str | None) -> str | None:
    """First-listed (dominant) species in a seed mix like 'Robusta/Arabica'."""
    if not species_mix:
        return None
    return species_mix.split("/")[0].strip() or None


def species_share(species_mix: str | None) -> dict[str, int]:
    """Indicative species split (%) from the seed mix ordering — used when USDA has no split."""
    if not species_mix:
        return {}
    parts = [p.strip() for p in species_mix.split("/") if p.strip()]
    if len(parts) == 1:
        return {parts[0]: 100}
    return {parts[0]: 70, parts[1]: 30}


def infer_species(*texts: str | None, species_mix: str | None = None) -> str | None:
    """Species of a lot from its name/variety/description, falling back to a single-species origin."""
    blob = " ".join(t for t in texts if t)
    if blob:
        if _LIBERICA.search(blob):
            return LIBERICA
        if _ROBUSTA.search(blob):
            return ROBUSTA
        if _ARABICA.search(blob):
            return ARABICA
    return country_species(species_mix)


def matches(species_filter: str | None, species: str | None, species_mix: str | None = None) -> bool:
    """Does a row with `species` (or a country with `species_mix`) belong under `species_filter`?"""
    if not species_filter or species_filter.lower() == "all":
        return True
    want = species_filter.capitalize()
    if species:
        return species == want
    return want in (species_mix or "")
