"""Tier 2 importer scrapers and spot-sheet parsers."""

from __future__ import annotations

from .base import BaseImporterScraper, OfferRecord
from .shopify import BodhiLeafScraper, CoffeeShrubScraper, RoyalCrownJewelsScraper
from .suitecommerce import GenuineOriginScraper

SCRAPERS: dict[str, type[BaseImporterScraper]] = {
    cls.key: cls
    for cls in (RoyalCrownJewelsScraper, BodhiLeafScraper, CoffeeShrubScraper, GenuineOriginScraper)
}


def enabled_scrapers(setting: str = "all") -> list[type[BaseImporterScraper]]:
    if not setting or setting.strip().lower() == "all":
        return list(SCRAPERS.values())
    if setting.strip().lower() == "none":
        return []
    keys = [k.strip() for k in setting.split(",") if k.strip()]
    return [SCRAPERS[k] for k in keys if k in SCRAPERS]


__all__ = ["BaseImporterScraper", "OfferRecord", "SCRAPERS", "enabled_scrapers"]
