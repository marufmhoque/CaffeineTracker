"""Atlanta landed-cost engine: warehouse -> LTL freight tier, landed green cost, roasted cost.

Landed Green Cost/lb  = Spot/lb × (1 + Tariff %) + Ocean Surcharge/lb + LTL Freight to Atlanta/lb
                        + Warehouse/Financing Fee/lb
True Roasted Cost/lb  = Landed Green Cost/lb / (1 - Shrinkage)

Tariff and ocean surcharge default to 0 (importer spot prices are usually ex-warehouse, duty paid).
A per-container ocean surcharge is spread over the container's green weight.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config

DEFAULT_RULES: dict[str, Any] = {
    "destination": "Atlanta, GA (303xx metro)",
    "warehouse_fee_usd_lb": 0.05,
    "default_shrinkage": 0.15,
    "tariff_pct": 0.0,
    "ocean_surcharge_usd_lb": 0.0,
    "unknown_warehouse_freight_usd_lb": 0.45,
    "tiers": [
        {
            "id": "southeast",
            "label": "Southeast ports",
            "low": 0.12,
            "high": 0.18,
            "default": 0.15,
            "southeast": True,
            "warehouses": [
                {"name": "Savannah, GA", "aliases": ["savannah", "garden city", "sav"]},
                {"name": "Charleston, SC", "aliases": ["charleston", "north charleston", "chs"]},
                {"name": "Jacksonville, FL", "aliases": ["jacksonville", "jax"]},
            ],
        },
        {
            "id": "east",
            "label": "Northeast, Miami & New Orleans",
            "low": 0.22,
            "high": 0.30,
            "default": 0.26,
            "southeast": False,
            "warehouses": [
                {
                    "name": "New Jersey",
                    "aliases": ["new jersey", "newark", "elizabeth", "edison", "secaucus",
                                "kearny", "carteret", "port newark", "nj"],
                },
                {"name": "New York, NY", "aliases": ["new york", "brooklyn", "staten island", "ny", "nyc"]},
                {"name": "Miami, FL", "aliases": ["miami", "doral", "medley", "mia"]},
                {"name": "New Orleans, LA", "aliases": ["new orleans", "nola", "harahan"]},
            ],
        },
        {
            "id": "west",
            "label": "West Coast & Houston",
            "low": 0.38,
            "high": 0.55,
            "default": 0.45,
            "southeast": False,
            "warehouses": [
                {"name": "Oakland, CA", "aliases": ["oakland", "alameda", "san leandro", "hayward", "oak"]},
                {"name": "Seattle, WA", "aliases": ["seattle", "tacoma", "kent, wa", "sea"]},
                {"name": "Houston, TX", "aliases": ["houston", "hou"]},
                {"name": "Los Angeles, CA", "aliases": ["los angeles", "long beach", "fullerton",
                                                        "orange county", "orange, ca", "anaheim"]},
            ],
        },
    ],
}


@dataclass(frozen=True)
class WarehouseMatch:
    warehouse: str
    tier_id: str
    tier_label: str
    freight_usd_lb: float
    southeast: bool
    matched: bool


@dataclass(frozen=True)
class CostBreakdown:
    spot_usd_lb: float
    freight_usd_lb: float
    fee_usd_lb: float
    landed_usd_lb: float
    shrinkage: float
    roasted_usd_lb: float
    tariff_pct: float = 0.0
    tariff_usd_lb: float = 0.0
    ocean_usd_lb: float = 0.0


# A 20' container of green coffee: 320 bags x 60 kg (19.2 t).
CONTAINER_BAGS = 320
CONTAINER_BAG_KG = 60.0


_lock = threading.Lock()


def load_rules(path: Path | None = None) -> dict[str, Any]:
    """Load freight rules from JSON, falling back to defaults (and writing them on first use)."""
    path = path or config.FREIGHT_RULES_FILE
    with _lock:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                rules = json.load(f)
            validate_rules(rules)
            return rules
        rules = copy.deepcopy(DEFAULT_RULES)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        except OSError:
            pass
        return rules


def save_rules(rules: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    validate_rules(rules)
    path = path or config.FREIGHT_RULES_FILE
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    return rules


def validate_rules(rules: dict[str, Any]) -> None:
    for key in ("warehouse_fee_usd_lb", "default_shrinkage", "unknown_warehouse_freight_usd_lb"):
        if key not in rules:
            raise ValueError(f"freight rules missing {key!r}")
        if float(rules[key]) < 0:
            raise ValueError(f"{key} must be >= 0")
    if not 0 <= float(rules["default_shrinkage"]) < 1:
        raise ValueError("default_shrinkage must be in [0, 1)")
    if not 0 <= float(rules.get("tariff_pct", 0)) <= 2:
        raise ValueError("tariff_pct must be a fraction between 0 and 2 (0.10 = 10%)")
    if float(rules.get("ocean_surcharge_usd_lb", 0)) < 0:
        raise ValueError("ocean_surcharge_usd_lb must be >= 0")
    tiers = rules.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("freight rules need at least one tier")
    for t in tiers:
        for key in ("id", "label", "low", "high", "default", "warehouses"):
            if key not in t:
                raise ValueError(f"tier missing {key!r}")
        if not float(t["low"]) <= float(t["default"]) <= float(t["high"]):
            raise ValueError(f"tier {t['id']}: default must lie within [low, high]")


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z])" + re.escape(alias.lower()) + r"(?![a-z])")


def match_warehouse(
    location: str | None, rules: dict[str, Any] | None = None, *, allow_short: bool = True
) -> WarehouseMatch:
    """Map a free-text warehouse location to a freight tier.

    `allow_short=False` ignores aliases shorter than 4 chars (e.g. 'ny', 'oak'), which is safer when
    scanning long product descriptions rather than a dedicated warehouse field.
    """
    rules = rules or load_rules()
    text = (location or "").lower()
    if text:
        # Longest aliases first so "north charleston" beats "charleston", "new york" beats "ny".
        candidates = []
        for tier in rules["tiers"]:
            for wh in tier["warehouses"]:
                for alias in [wh["name"], *wh.get("aliases", [])]:
                    a = alias.lower()
                    if not allow_short and len(a) < 4:
                        continue
                    candidates.append((len(a), a, tier, wh))
        candidates.sort(key=lambda c: -c[0])
        for _, alias, tier, wh in candidates:
            if _alias_pattern(alias).search(text):
                return WarehouseMatch(
                    warehouse=wh["name"],
                    tier_id=tier["id"],
                    tier_label=tier["label"],
                    freight_usd_lb=float(tier["default"]),
                    southeast=bool(tier.get("southeast", False)),
                    matched=True,
                )
    return WarehouseMatch(
        warehouse=location.strip() if location and location.strip() else "Unknown",
        tier_id="unknown",
        tier_label="Unknown warehouse",
        freight_usd_lb=float(rules["unknown_warehouse_freight_usd_lb"]),
        southeast=False,
        matched=False,
    )


def landed_cost(
    spot_usd_lb: float, freight_usd_lb: float, fee_usd_lb: float, *, tariff_pct: float = 0.0,
    ocean_usd_lb: float = 0.0,
) -> float:
    if min(spot_usd_lb, freight_usd_lb, fee_usd_lb, tariff_pct, ocean_usd_lb) < 0:
        raise ValueError("cost components must be non-negative")
    return spot_usd_lb * (1 + tariff_pct) + ocean_usd_lb + freight_usd_lb + fee_usd_lb


def container_to_usd_lb(usd_per_container: float, bags: int = CONTAINER_BAGS, bag_kg: float = CONTAINER_BAG_KG) -> float:
    """Spread a per-container charge over the container's green weight."""
    if bags <= 0 or bag_kg <= 0:
        raise ValueError("bags and bag weight must be positive")
    from .units import bag_kg_to_lb

    return usd_per_container / (bags * bag_kg_to_lb(bag_kg))


def roasted_cost(landed_usd_lb: float, shrinkage: float) -> float:
    if not 0 <= shrinkage < 1:
        raise ValueError("shrinkage must be in [0, 1)")
    return landed_usd_lb / (1 - shrinkage)


def ltl_freight_per_lb(shipment_cost_usd: float, bags: int, bag_kg: float) -> float:
    """Per-lb freight for an LTL quote covering `bags` bags of `bag_kg` each."""
    if bags <= 0 or bag_kg <= 0:
        raise ValueError("bags and bag weight must be positive")
    from .units import bag_kg_to_lb

    return shipment_cost_usd / (bags * bag_kg_to_lb(bag_kg))


def cost_breakdown(
    spot_usd_lb: float,
    warehouse: str | None = None,
    *,
    rules: dict[str, Any] | None = None,
    freight_override: float | None = None,
    fee_override: float | None = None,
    shrinkage: float | None = None,
    tariff_pct: float | None = None,
    ocean_usd_lb: float | None = None,
) -> tuple[CostBreakdown, WarehouseMatch]:
    rules = rules or load_rules()
    match = match_warehouse(warehouse, rules)
    freight = match.freight_usd_lb if freight_override is None else float(freight_override)
    fee = float(rules["warehouse_fee_usd_lb"]) if fee_override is None else float(fee_override)
    shrink = float(rules["default_shrinkage"]) if shrinkage is None else float(shrinkage)
    tariff = float(rules.get("tariff_pct", 0)) if tariff_pct is None else float(tariff_pct)
    ocean = float(rules.get("ocean_surcharge_usd_lb", 0)) if ocean_usd_lb is None else float(ocean_usd_lb)
    landed = landed_cost(spot_usd_lb, freight, fee, tariff_pct=tariff, ocean_usd_lb=ocean)
    return (
        CostBreakdown(
            spot_usd_lb=spot_usd_lb,
            freight_usd_lb=freight,
            fee_usd_lb=fee,
            landed_usd_lb=landed,
            shrinkage=shrink,
            roasted_usd_lb=roasted_cost(landed, shrink),
            tariff_pct=tariff,
            tariff_usd_lb=spot_usd_lb * tariff,
            ocean_usd_lb=ocean,
        ),
        match,
    )
