"""Scraper for importers on NetSuite SuiteCommerce (public `/api/items` search endpoint).

Genuine Origin runs on SuiteCommerce. Field names on SuiteCommerce sites are partly site-specific, so
parsing is defensive: it scans every string field for weight/warehouse/score hints. Verify the first run's
output (`python -m backend.app.cli scrape --only genuine_origin --dry-run`) before trusting it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.app.costing import match_warehouse
from backend.app.geo import resolve_country
from backend.app.http import get_with_retry
from backend.app.units import LB_PER_KG, parse_weight_kg

from .base import BaseImporterScraper, OfferRecord
from .extract import clean_html, detect_process, detect_score, detect_variety, parse_int

log = logging.getLogger(__name__)

# Below this a listed price is assumed to be per lb; above it, per bag/box.
PER_LB_PRICE_CEILING = 40.0


def _price(item: dict) -> float | None:
    detail = item.get("onlinecustomerprice_detail") or {}
    for v in (
        detail.get("onlinecustomerprice"),
        item.get("onlinecustomerprice"),
        item.get("pricelevel1"),
        (item.get("pricelevel_detail") or {}).get("price") if isinstance(item.get("pricelevel_detail"), dict) else None,
    ):
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _all_text(item: dict) -> str:
    parts = []
    for k, v in item.items():
        if isinstance(v, str) and len(v) < 5000:
            parts.append(clean_html(v) if "<" in v else v)
    return " ".join(parts)


def parse_item(item: dict[str, Any], *, importer: str, base_url: str, default_warehouse: str | None) -> OfferRecord | None:
    name = (
        item.get("storedisplayname2") or item.get("displayname") or item.get("storedisplayname")
        or item.get("itemid") or ""
    ).strip()
    if not name:
        return None
    if item.get("isinstock") is False or item.get("isinactive") is True:
        return None
    text = _all_text(item)
    country = resolve_country(name) or resolve_country(text[:800])
    if not country:
        return None
    price = _price(item)
    if not price:
        return None
    kg = parse_weight_kg(name) or parse_weight_kg(item.get("saleunit")) or parse_weight_kg(text)
    if price <= PER_LB_PRICE_CEILING:
        price_lb = price
    elif kg:
        price_lb = price / (kg * LB_PER_KG)
    else:
        return None
    wm = match_warehouse(text, allow_short=False)
    url = item.get("urlcomponent")
    return OfferRecord(
        importer=importer,
        lot_name=name,
        spot_price_usd_lb=round(price_lb, 4),
        country_iso=country,
        process=detect_process(name, text),
        variety=detect_variety(name, text),
        sca_score=detect_score(text),
        bag_weight_kg=round(kg, 3) if kg else None,
        warehouse_location=wm.warehouse if wm.matched else default_warehouse,
        bags_available=parse_int(item.get("quantityavailable")),
        raw_url=f"{base_url.rstrip('/')}/{url}" if url else base_url,
    )


class SuiteCommerceScraper(BaseImporterScraper):
    search_query: str = ""
    page_size: int = 100
    max_pages: int = 20

    def fetch(self) -> list[OfferRecord]:
        url = self.base_url.rstrip("/") + "/api/items"
        offers: list[OfferRecord] = []
        offset = 0
        for _ in range(self.max_pages):
            params = {"fieldset": "search", "limit": self.page_size, "offset": offset}
            if self.search_query:
                params["q"] = self.search_query
            data = get_with_retry(self.client, url, params=params).json()
            items = data.get("items") or []
            for it in items:
                rec = parse_item(it, importer=self.importer, base_url=self.base_url,
                                 default_warehouse=self.default_warehouse)
                if rec:
                    offers.append(rec)
            total = data.get("total") or 0
            offset += self.page_size
            if not items or offset >= total:
                break
            time.sleep(1.0)
        return offers


class GenuineOriginScraper(SuiteCommerceScraper):
    key = "genuine_origin"
    importer = "Genuine Origin"
    base_url = "https://www.genuineorigin.com"
    default_warehouse = None  # multiple warehouses; taken from item text when present
    search_query = "coffee"
