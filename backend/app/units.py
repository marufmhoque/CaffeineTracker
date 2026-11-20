"""Unit normalization: USD/lb <-> USD/kg, bag weights, commodity quote units, price-string parsing."""

from __future__ import annotations

import re

LB_PER_KG = 2.2046226218487757
KG_PER_LB = 1 / LB_PER_KG
LB_PER_TONNE = 1000 * LB_PER_KG

# Standard export bag sizes in kg (jute / GrainPro). 60 kg = Brazil/Africa/Asia,
# 69 kg = Central America/Peru, 70 kg = Colombia.
STANDARD_BAG_KG = (60.0, 69.0, 70.0)


def usd_lb_to_usd_kg(price_usd_lb: float) -> float:
    return price_usd_lb * LB_PER_KG


def usd_kg_to_usd_lb(price_usd_kg: float) -> float:
    return price_usd_kg / LB_PER_KG


def convert_price(price: float | None, from_unit: str, to_unit: str) -> float | None:
    """Convert a USD price between 'lb' and 'kg' bases."""
    if price is None:
        return None
    f, t = from_unit.lower(), to_unit.lower()
    if f == t:
        return price
    if f == "lb" and t == "kg":
        return usd_lb_to_usd_kg(price)
    if f == "kg" and t == "lb":
        return usd_kg_to_usd_lb(price)
    raise ValueError(f"unsupported units {from_unit!r} -> {to_unit!r}")


def bag_kg_to_lb(bag_kg: float) -> float:
    return bag_kg * LB_PER_KG


def bag_price_to_usd_lb(bag_price_usd: float, bag_kg: float) -> float:
    """Price for a whole bag -> USD/lb."""
    if bag_kg <= 0:
        raise ValueError("bag weight must be positive")
    return bag_price_usd / bag_kg_to_lb(bag_kg)


def usd_lb_to_bag_price(price_usd_lb: float, bag_kg: float) -> float:
    return price_usd_lb * bag_kg_to_lb(bag_kg)


def cents_lb_to_usd_lb(cents: float) -> float:
    """ICE Arabica 'C' is quoted in US cents per lb."""
    return cents / 100.0


def usd_tonne_to_usd_lb(usd_per_tonne: float) -> float:
    """ICE Robusta is quoted in USD per metric tonne."""
    return usd_per_tonne / LB_PER_TONNE


_NUM = r"(\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def _to_float(num: str) -> float:
    return float(num.replace(",", "").replace(" ", ""))


def parse_weight_kg(text: str | None) -> float | None:
    """Parse a bag/box weight description into kg.

    Handles '69 kg', '70kg GrainPro', '152 lb', '22 lb box', '1 lb', '30 kilos', '60-kg bag'.
    Returns None if no weight is found.
    """
    if not text:
        return None
    t = str(text).lower()
    m = re.search(_NUM + r"\s*-?\s*(kgs?|kilos?|kilograms?)\b", t)
    if m:
        return _to_float(m.group(1))
    m = re.search(_NUM + r"\s*-?\s*(lbs?|pounds?|#)(?![a-z])", t)
    if m:
        return _to_float(m.group(1)) * KG_PER_LB
    return None


def parse_price(text: str | float | int | None) -> tuple[float, str] | None:
    """Parse a price string into (value, basis) where basis is 'lb', 'kg', 'bag' or 'unknown'.

    '$4.25' -> (4.25, 'unknown'); '4.25/lb' -> (4.25, 'lb'); '$9.37 / kg' -> (9.37, 'kg');
    'USD 562.00 per bag' -> (562.0, 'bag'); '425¢/lb' -> (4.25, 'lb').
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return (float(text), "unknown") if text == text else None  # NaN guard
    t = str(text).strip().lower()
    if not t or t in {"-", "n/a", "na", "tbd", "sold", "sold out"}:
        return None
    m = re.search(_NUM, t)
    if not m:
        return None
    value = _to_float(m.group(1))
    after = t[m.end():]
    if "¢" in t or re.search(r"\bcents?\b", t):
        value = value / 100.0
    if re.search(r"(/|\bper\b|\ba\b)\s*(kg|kilo)", t):
        basis = "kg"
    elif re.search(r"(/|\bper\b)\s*(lb|pound|#)", t) or re.match(r"\s*/\s*lb", after):
        basis = "lb"
    elif re.search(r"(/|\bper\b)\s*(bag|sack|box)", t):
        basis = "bag"
    else:
        basis = "unknown"
    return value, basis


def normalize_to_usd_lb(
    price: float, basis: str, bag_kg: float | None = None
) -> float | None:
    """Turn a parsed (price, basis) into USD/lb. 'unknown' basis is treated as per-lb (spot-sheet convention)."""
    if basis in ("lb", "unknown"):
        return price
    if basis == "kg":
        return usd_kg_to_usd_lb(price)
    if basis == "bag":
        if not bag_kg:
            return None
        return bag_price_to_usd_lb(price, bag_kg)
    raise ValueError(f"unknown basis {basis!r}")
