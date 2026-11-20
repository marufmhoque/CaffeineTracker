"""Scraper for importers whose public catalogs run on Shopify (standard `/products.json` endpoint).

Shopify exposes product + variant data (price, weight in grams, availability) as JSON on every store, so
these scrapers need no HTML parsing or headless browser. Prices are per variant; we pick the largest
available pack size (the bulk/roaster price) and normalize to USD/lb.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from backend.app.costing import match_warehouse
from backend.app.geo import resolve_country
from backend.app.http import get_with_retry
from backend.app.units import LB_PER_KG, parse_weight_kg

from .base import BaseImporterScraper, OfferRecord
from .extract import clean_html, detect_process, detect_score, detect_variety

log = logging.getLogger(__name__)

EXCLUDE = re.compile(
    r"gift ?card|roaster\b|roasting machine|grinder|brewer|kettle|filter|scale\b|subscription|"
    r"sample set|sampler|t-?shirt|mug\b|book\b|equipment|replacement|parts?\b|roasted coffee|"
    r"\broasted\b(?! green)|tea\b|chaff|cleaning|thermometer",
    re.I,
)
MIN_LB_FOR_BULK = 1.5  # ignore tiny sample variants when a bigger pack exists


def _tags(product: dict) -> list[str]:
    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return [t for t in tags if t]


def _tag_value(tags: list[str], *keys: str) -> str | None:
    for t in tags:
        m = re.match(r"\s*([A-Za-z ]+)\s*[:_]\s*(.+)$", t)
        if m and m.group(1).strip().lower() in keys:
            return m.group(2).strip()
    return None


def _variant_kg(variant: dict) -> float | None:
    grams = variant.get("grams")
    if grams and grams > 0:
        return grams / 1000.0
    for key in ("title", "option1", "option2", "option3"):
        kg = parse_weight_kg(variant.get(key))
        if kg:
            return kg
    return None


def choose_variant(variants: list[dict]) -> tuple[dict, float] | None:
    """Pick the largest in-stock pack (bulk price). Returns (variant, kg) or None."""
    sized = []
    for v in variants:
        kg = _variant_kg(v)
        try:
            price = float(v.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        if kg and kg > 0 and price > 0:
            sized.append((v, kg))
    if not sized:
        return None
    available = [s for s in sized if s[0].get("available", True)]
    pool = available or []
    if not pool:
        return None
    bulk = [s for s in pool if s[1] * LB_PER_KG >= MIN_LB_FOR_BULK]
    pool = bulk or pool
    return max(pool, key=lambda s: s[1])


def parse_product(
    product: dict[str, Any],
    *,
    importer: str,
    base_url: str,
    default_warehouse: str | None,
) -> OfferRecord | None:
    title = (product.get("title") or "").strip()
    tags = _tags(product)
    ptype = product.get("product_type") or ""
    body = clean_html(product.get("body_html"))
    head = " ".join([title, ptype, " ".join(tags)])
    if EXCLUDE.search(title) or EXCLUDE.search(ptype):
        return None

    country = (
        resolve_country(_tag_value(tags, "origin", "country"), field=True)
        or resolve_country(title)
        or resolve_country(" ".join(tags))
        or resolve_country(body[:600])
    )
    if not country:
        return None

    chosen = choose_variant(product.get("variants") or [])
    if not chosen:
        return None  # sold out or no usable size/price
    variant, kg = chosen
    price_lb = float(variant["price"]) / (kg * LB_PER_KG)

    wh_text = _tag_value(tags, "warehouse", "location", "ships from")
    if wh_text:
        warehouse = wh_text
    else:
        m = match_warehouse(body + " " + head, allow_short=False)
        warehouse = m.warehouse if m.matched else default_warehouse

    return OfferRecord(
        importer=importer,
        lot_name=title,
        spot_price_usd_lb=round(price_lb, 4),
        country_iso=country,
        origin_region=_tag_value(tags, "region", "farm", "producer", "coop", "co-op"),
        process=_tag_value(tags, "process", "processing") or detect_process(head, body),
        variety=_tag_value(tags, "variety", "varietal", "cultivar") or detect_variety(head, body),
        sca_score=detect_score(_tag_value(tags, "score", "cupping score") or "", head, body),
        bag_weight_kg=round(kg, 3),
        warehouse_location=warehouse,
        bags_available=None,  # Shopify's public JSON doesn't expose inventory counts
        raw_url=f"{base_url.rstrip('/')}/products/{product.get('handle')}",
        extra={"variant": variant.get("title"), "variant_price": float(variant["price"])},
    )


class ShopifyImporterScraper(BaseImporterScraper):
    # Catalog paths to try, in order; the first that returns a product list is used.
    catalog_paths: list[str] = ["/products.json"]
    max_pages: int = 20
    page_pause_s: float = 1.0

    def fetch_products(self) -> list[dict]:
        last_error: Exception | None = None
        for path in self.catalog_paths:
            url = self.base_url.rstrip("/") + path
            products: list[dict] = []
            try:
                for page in range(1, self.max_pages + 1):
                    resp = get_with_retry(self.client, url, params={"limit": 250, "page": page})
                    data = resp.json()
                    batch = data.get("products") if isinstance(data, dict) else None
                    if batch is None:
                        raise ValueError(f"{url} did not return a product list")
                    if not batch:
                        break
                    products.extend(batch)
                    if len(batch) < 250:
                        break
                    time.sleep(self.page_pause_s)
                return products
            except Exception as e:  # noqa: BLE001
                last_error = e
                log.info("%s: %s failed (%s), trying next path", self.key, path, e)
        raise RuntimeError(f"{self.importer}: no catalog endpoint worked ({last_error})")

    def fetch(self) -> list[OfferRecord]:
        offers = []
        for p in self.fetch_products():
            rec = parse_product(
                p, importer=self.importer, base_url=self.base_url, default_warehouse=self.default_warehouse
            )
            if rec:
                offers.append(rec)
        return offers


class RoyalCrownJewelsScraper(ShopifyImporterScraper):
    key = "royal_crown_jewels"
    importer = "Royal Coffee (Crown Jewels)"
    base_url = "https://royalcoffee.com"
    default_warehouse = "Oakland, CA"
    catalog_paths = ["/collections/crown-jewels/products.json", "/products.json"]


class BodhiLeafScraper(ShopifyImporterScraper):
    key = "bodhi_leaf"
    importer = "Bodhi Leaf"
    base_url = "https://bodhileafcoffee.com"
    default_warehouse = "Orange County, CA"
    catalog_paths = ["/collections/green-coffee/products.json", "/collections/all/products.json", "/products.json"]


class CoffeeShrubScraper(ShopifyImporterScraper):
    key = "coffee_shrub"
    importer = "Coffee Shrub"
    base_url = "https://www.coffeeshrub.com"
    default_warehouse = "Oakland, CA"
    catalog_paths = ["/collections/all/products.json", "/collections/green-coffee/products.json"]
