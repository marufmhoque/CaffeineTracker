"""Database schema (SQLModel). Core tables follow the PRD; a few bookkeeping columns/tables are added."""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Country(SQLModel, table=True):
    __tablename__ = "countries"

    iso_code: str = Field(primary_key=True, max_length=3)
    m49: int = Field(index=True)
    name: str
    region: str = Field(index=True)
    species_mix: str
    currency_code: str
    ico_group: str
    main_harvest_months: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    fly_harvest_months: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    default_bag_kg: float = 60.0
    typical_diff_low: Optional[float] = None
    typical_diff_high: Optional[float] = None


class MarketBenchmark(SQLModel, table=True):
    __tablename__ = "market_benchmarks"

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True, unique=True)
    ice_arabica_usd_lb: Optional[float] = None
    ice_robusta_usd_lb: Optional[float] = None
    ico_composite_usd_lb: Optional[float] = None
    # ICO group indicators in USD/lb, e.g. {"colombian_milds": 3.9, ...}; loaded from data/ico_prices.csv.
    ico_groups_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Units of currency per 1 USD, e.g. {"BRL": 5.43}.
    fx_rates_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class SpotOffer(SQLModel, table=True):
    __tablename__ = "spot_offers"

    id: Optional[int] = Field(default=None, primary_key=True)
    scraped_at: datetime = Field(default_factory=utcnow, index=True)
    batch_id: str = Field(index=True)  # one per scraper run / ingested file
    source: str  # scraper key or "dropzone"
    importer: str = Field(index=True)
    offer_date: Optional[dt.date] = None
    country_iso: Optional[str] = Field(default=None, foreign_key="countries.iso_code", index=True)
    lot_name: str
    origin_region: Optional[str] = None  # region / farm / co-op
    process: Optional[str] = Field(default=None, index=True)
    variety: Optional[str] = None
    species: Optional[str] = Field(default=None, index=True)  # Arabica | Robusta | Liberica
    sca_score: Optional[float] = None
    bag_weight_kg: Optional[float] = None
    warehouse_location: Optional[str] = None
    warehouse_tier: Optional[str] = None
    is_southeast: bool = False
    spot_price_usd_lb: float
    freight_usd_lb: Optional[float] = None
    atlanta_landed_usd_lb: Optional[float] = None
    bags_available: Optional[int] = None
    raw_url: Optional[str] = None
    offer_key: str = Field(index=True)  # stable identity across scrapes (importer + lot + warehouse)


class TradeValuation(SQLModel, table=True):
    __tablename__ = "trade_valuations"
    __table_args__ = (
        UniqueConstraint("period_month", "country_iso", "reporter", "hs_code", "freq"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    period_month: dt.date = Field(index=True)  # first day of month (Jan 1 for annual rows)
    freq: str = Field(default="M", index=True)  # M = monthly, A = annual
    country_iso: str = Field(foreign_key="countries.iso_code", index=True)
    reporter: str = "USA"  # importing market (ISO-3)
    hs_code: str = "090111"
    import_volume_kg: float
    import_value_usd: float
    unit_price_usd_lb: float
    source: str = "un_comtrade"


class FuturesQuote(SQLModel, table=True):
    """Daily settle of one futures contract month (the forward curve / term structure)."""

    __tablename__ = "futures_quotes"
    __table_args__ = (UniqueConstraint("date", "symbol"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)
    market: str = Field(default="arabica", index=True)  # arabica | robusta
    symbol: str  # e.g. KCH27.NYB
    contract_month: dt.date  # first day of the delivery month
    usd_lb: float


class CertifiedStock(SQLModel, table=True):
    """ICE-certified (exchange-graded, licensed warehouse) inventory, in 60 kg bags."""

    __tablename__ = "certified_stocks"
    __table_args__ = (UniqueConstraint("date", "market"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)
    market: str = Field(default="arabica", index=True)
    bags: int


class WeatherSignal(SQLModel, table=True):
    """Weather anomaly snapshot for one coffee growing belt (Open-Meteo)."""

    __tablename__ = "weather_signals"
    __table_args__ = (UniqueConstraint("as_of", "belt"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    as_of: dt.date = Field(index=True)
    country_iso: str = Field(foreign_key="countries.iso_code", index=True)
    belt: str
    species: str
    lat: float
    lon: float
    rain_90d_mm: Optional[float] = None
    rain_normal_mm: Optional[float] = None
    rain_pct_normal: Optional[float] = None
    hot_days_30d: int = 0
    frost_days_30d: int = 0
    frost_forecast_days: int = 0
    hot_forecast_days: int = 0
    risk: str = "Low"  # Low | Moderate | Critical
    reasons_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class CropEstimate(SQLModel, table=True):
    """USDA FAS PSD balance-sheet line, thousand 60 kg bags."""

    __tablename__ = "crop_estimates"
    __table_args__ = (UniqueConstraint("country_iso", "market_year", "attribute"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    country_iso: str = Field(index=True)  # ISO-3, or "WLD" for the world total
    market_year: int = Field(index=True)
    attribute: str  # production | arabica_production | robusta_production | domestic_consumption | ...
    value_k_bags: float


class Alert(SQLModel, table=True):
    __tablename__ = "alerts"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    country_iso: Optional[str] = Field(default=None, foreign_key="countries.iso_code", index=True)
    alert_type: str = Field(index=True)
    message: str
    is_read: bool = False
    dedupe_key: str = Field(index=True)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class IngestRun(SQLModel, table=True):
    __tablename__ = "ingest_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    job: str = Field(index=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    status: str = "running"  # running | ok | partial | error
    rows: int = 0
    message: str = ""


class IngestedFile(SQLModel, table=True):
    __tablename__ = "ingested_files"

    id: Optional[int] = Field(default=None, primary_key=True)
    sha256: str = Field(index=True, unique=True)
    filename: str
    ingested_at: datetime = Field(default_factory=utcnow)
    importer: Optional[str] = None
    rows: int = 0
    status: str = "ok"
    message: str = ""
