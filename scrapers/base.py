"""Common types and the scraper base class for Tier 2 importer spot offerings."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import httpx

from backend.app.http import make_client

log = logging.getLogger(__name__)


@dataclass
class OfferRecord:
    """One green coffee lot offered by an importer, normalized to USD/lb."""

    importer: str
    lot_name: str
    spot_price_usd_lb: float
    country_iso: str | None = None
    offer_date: date | None = None
    origin_region: str | None = None
    process: str | None = None
    variety: str | None = None
    sca_score: float | None = None
    bag_weight_kg: float | None = None
    warehouse_location: str | None = None
    bags_available: int | None = None
    raw_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseImporterScraper(ABC):
    """Subclass per importer. `fetch()` returns normalized OfferRecords.

    Scrapers must be polite: identify via User-Agent (shared client), make few requests, back off on 429.
    """

    key: str = "base"
    importer: str = "Unknown Importer"
    base_url: str = ""
    default_warehouse: str | None = None
    requires_credentials: bool = False

    def __init__(self, client: httpx.Client | None = None):
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = make_client()
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    @abstractmethod
    def fetch(self) -> list[OfferRecord]:
        ...

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "importer": self.importer,
            "base_url": self.base_url,
            "default_warehouse": self.default_warehouse,
            "requires_credentials": self.requires_credentials,
        }
